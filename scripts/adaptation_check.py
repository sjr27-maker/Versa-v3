"""Does Versa learn how someone moves through ideas -- live, through the real
server? (docs/THINKING_STYLE.md: the pick guess, answers shaped only on a
follow-up, cards that build on the path, the observation ledger.)

Signs in as a tester by name (POST /api/auth/dev, the laptop sign-in) against
a running `versa serve`, and drives chats over the WebSocket exactly as the
app does. A simulated student (Gemini's `best` tier, role-playing a hidden
persona) writes the first question of each chat, reads each answer and the
"where this could go" cards (text only -- never slot names), and either takes
a card, asks its own follow-up, or ends the chat.

Records, per turn: whether Versa guessed the card taken (the `guess` frame),
whether the answer was shaped (`adapted`) and whether that turn was a new
topic or a follow-up, and whether a later card set re-offered a card already
taken in that chat.

    VERSA_SPARKS=off uv run versa serve --host 127.0.0.1 --port 8765
    uv run python scripts/adaptation_check.py --server http://127.0.0.1:8765 --as sooraj

Staged verification: the student is a model following a persona, so this can
show the mechanisms work end to end on real models -- not that Versa learned
a real person.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter

import httpx
import websockets
from dotenv import load_dotenv

from versa.llm import build_tier_clients

TOPICS = [
    "how compound interest works (you just opened a savings account)",
    "photosynthesis (biology homework)",
    "recursion in programming (you're learning Python)",
    "supply and demand (economics class)",
    "how vaccines train the immune system (you're curious)",
    "the Pythagorean theorem (maths class)",
    "how binary numbers work (curious about computers)",
    "Newton's third law (physics class)",
]

PERSONA = (
    "You learn by doing. On any new topic, the FIRST thing you want is one concrete worked "
    "case -- actual numbers or a specific instance. Once you've seen that, you want to know "
    "where it is actually used in real life. You rarely care for analogies or 'imagine "
    "that' pictures, and harder or rigorous versions put you off. Every so often, instead of "
    "tapping a card, you ask your own short follow-up question about the SAME topic, in your "
    "own words. You write casually and briefly."
)


def _parse_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        return json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}


def opener_prompt(topic: str) -> str:
    return (
        "You are role-playing a student using a tutoring app.\n"
        f"Your hidden way of learning: {PERSONA}\n"
        f"You want to learn about: {topic}.\n"
        "Write your FIRST message to the tutor, one or two short casual sentences. "
        'Respond with JSON: {"text": "..."}'
    )


def choice_prompt(topic: str, history: list[str], answer: str, cards: list[str], turns_left: int) -> str:
    listed = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(cards)) or "(none)"
    return (
        "You are role-playing a student using a tutoring app.\n"
        f"Your hidden way of learning: {PERSONA}\n"
        f"Topic: {topic}. What you've done so far: {' | '.join(history[-6:])}\n"
        f"The tutor just answered (may be cut): <<<{answer[:1200]}>>>\n"
        f"Under the answer the app offers these next steps:\n{listed}\n"
        f"You have about {turns_left} more actions in this chat.\n"
        "Choose what YOU would do now, true to your way of learning: tap one card, ask your own "
        "short follow-up about this topic, or stop if you're satisfied. Respond with JSON only: "
        '{"action": "pick", "card": <number>} or {"action": "ask", "text": "..."} or {"action": "stop"}'
    )


class StudentUnavailable(Exception):
    """Gemini stayed unreachable for the simulated student: end this chat."""


async def ask_student(student, prompt: str) -> str:
    """The simulated student's call. Fail fast: on a network failure the whole
    run stops (and writes what it has) rather than spending calls retrying."""
    try:
        return await student.complete(prompt)
    except Exception as exc:  # noqa: BLE001 -- any transport failure
        print(f"  (student call failed: {exc!r}; stopping the run)")
        raise StudentUnavailable from exc


async def recv_until(ws, types: set[str], timeout: float) -> list[dict]:
    """Collect frames until one of `types` arrives (inclusive)."""
    out = []
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return out
        try:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=left))
        except TimeoutError:
            return out
        out.append(frame)
        if frame.get("type") in types:
            return out


async def turn(ws, payload: dict) -> tuple[list[dict], list[dict] | None]:
    """Send one frame; return (turn frames through done/error, the directions
    frame that follows an answer, if any)."""
    await ws.send(json.dumps(payload))
    frames = await recv_until(ws, {"done", "error", "paywall"}, 120)
    directions = None
    done = frames[-1] if frames else {}
    if done.get("type") == "done" and done.get("kind") == "answer":
        more = await recv_until(ws, {"directions"}, 40)
        directions = next((f for f in more if f.get("type") == "directions"), None)
    return frames, directions


def follow_up_prompt(topic: str, history: list[str], answer: str) -> str:
    return (
        "You are role-playing a student using a tutoring app.\n"
        f"Your hidden way of learning: {PERSONA}\n"
        f"Topic: {topic}. What you've done so far: {' | '.join(history[-6:])}\n"
        f"The tutor just answered (may be cut): <<<{answer[:1200]}>>>\n"
        "Instead of tapping one of the app's suggested next steps, ask your OWN short follow-up "
        "question about this same topic, in your own words, true to your way of learning. "
        'Respond with JSON: {"text": "..."}'
    )


async def play(client, ws_base, token, learner_id, student, topic, max_actions, log, switch_to=None):
    sid = (await client.post("/api/sessions", json={"learner_id": learner_id})).json()["session_id"]
    history: list[str] = []
    record = {"topic": topic, "session_id": sid, "turns": []}
    try:
        async with websockets.connect(f"{ws_base}/api/sessions/{sid}/chat", subprotocols=["versa", token],
                                      max_size=None) as ws:
            opener = _parse_json(await ask_student(student, opener_prompt(topic))).get("text") or f"explain {topic}"
            frames, directions = await turn(ws, {"type": "message", "text": opener, "directions": True})
            taken_texts: list[str] = []
            kind, text = "new_topic", opener
            for step in range(max_actions):
                done = frames[-1] if frames else {}
                answer = done.get("text", "")
                # an options turn: take the first reading, as a student would click one
                if done.get("kind") == "options" or any(f.get("type") == "options" for f in frames):
                    opts = next((f for f in reversed(frames) if f.get("type") == "options"), {})
                    options = opts.get("options") or []
                    entry = {"kind": kind, "text": text, "options": [o.get("text") for o in options]}
                    record["turns"].append(entry)
                    if not options:
                        break
                    frames, directions = await turn(
                        ws, {"type": "select_option", "option_id": options[0]["id"], "directions": True})
                    kind, text = "clicked_option", options[0].get("text")
                    continue
                guess = next((f for f in frames if f.get("type") == "guess"), None)
                adapted = next((f for f in frames if f.get("type") == "adapted"), None)
                cards = (directions or {}).get("cards") or []
                repeats = [c["text"] for c in cards if c["text"] in taken_texts]
                record["turns"].append({
                    "kind": kind, "text": text, "answer_words": len(answer.split()),
                    "guess": guess and {k: guess[k] for k in ("hit", "predicted", "picked", "hits", "guesses",
                                                               "picks_seen", "because")},
                    "adapted": adapted and {"path": adapted["path"], "because": adapted["because"]},
                    "cards": [c["text"] for c in cards], "repeats_taken": repeats,
                    "error": done.get("message") if done.get("type") == "error" else None,
                })
                log(f"  [{topic[:24]}] {kind:14s} guess={'-' if not guess else ('HIT' if guess['hit'] else 'miss')} "
                    f"adapted={'yes ' + '->'.join(adapted['path']) if adapted else 'no'} cards={len(cards)}")
                if done.get("type") != "done":
                    if done.get("type") == "error":
                        log(f"  (Versa turn failed: {done.get('message')}; stopping the run)")
                        raise StudentUnavailable
                    break
                # every chat: one follow-up of their own (step 1), and -- when
                # given -- a switch to a brand-new subject part-way (step 3), so
                # shaping and "the first answer to something new is normal" are
                # both exercised
                if step == 1:
                    own = _parse_json(await ask_student(student, follow_up_prompt(topic, history, answer))).get("text")
                    choice = {"action": "ask", "text": own} if own else {"action": "stop"}
                elif step == 3 and switch_to:
                    opener = _parse_json(await ask_student(student, opener_prompt(switch_to))).get("text") or f"can you explain {switch_to}?"
                    history.append(f"switched to: {switch_to}")
                    frames, directions = await turn(ws, {"type": "message", "text": opener, "directions": True})
                    kind, text, topic, taken_texts = "new_topic_midchat", opener, switch_to, []
                    continue
                else:
                    choice = _parse_json(await ask_student(student, 
                        choice_prompt(topic, history, answer, [c["text"] for c in cards], max_actions - step)))
                action = choice.get("action")
                if action == "pick" and cards:
                    n = int(choice.get("card", 1)) - 1
                    card = cards[max(0, min(n, len(cards) - 1))]
                    await asyncio.sleep(2.5)  # reading the cards, as a person would
                    history.append(f"tapped: {card['text']}")
                    taken_texts.append(card["text"])
                    frames, directions = await turn(ws, {"type": "direction", "card_id": card["id"], "directions": True})
                    kind, text = "picked_card", card["text"]
                elif action == "ask" and choice.get("text"):
                    history.append(f"asked: {choice['text']}")
                    frames, directions = await turn(ws, {"type": "message", "text": choice["text"], "directions": True})
                    kind, text = "own_follow_up", choice["text"]
                else:
                    break
    except StudentUnavailable:
        record["ended_early"] = True  # kept in the report; main() stops the run
        return record
    await client.post(f"/api/sessions/{sid}/end")
    return record


def render(learner: str, sessions: list[dict], observations: str) -> str:
    lines = [f"# Adaptation check -- learner {learner} ({time.strftime('%Y-%m-%d %H:%M')})", "",
             "Real Gemini, real server (`versa serve`), signed in with the tester name sign-in. A simulated "
             "student with a hidden persona drove every chat. **Staged verification**: shows the mechanisms "
             "working end to end on real models, not that Versa learned a real person.", "",
             f"Persona: _{PERSONA}_", ""]
    guesses = [t["guess"] for s in sessions for t in s["turns"] if t.get("guess")]
    hits = [g["hit"] for g in guesses]
    lines.append(f"- guesses revealed: {len(hits)}, right: {sum(hits)}")
    third = max(1, len(hits) // 3)
    if hits:
        lines.append(f"- first third right: {sum(hits[:third])}/{len(hits[:third])}; "
                     f"last third right: {sum(hits[-third:])}/{len(hits[-third:])}")
    kinds = Counter((t["kind"], bool(t.get("adapted"))) for s in sessions for t in s["turns"])
    lines.append("- answers shaped, by kind of turn: " + ", ".join(
        f"{k} {'shaped' if a else 'normal'} x{n}" for (k, a), n in sorted(kinds.items())))
    repeats = sum(len(t.get("repeats_taken") or []) for s in sessions for t in s["turns"])
    lines.append(f"- cards re-offering one already taken in that chat: {repeats}")
    picked = Counter(g["picked"] for g in guesses)
    lines.append(f"- cards taken (by kind): {dict(picked)}")
    lines += ["", "## Ledger after the run (`versa observations`)", "", "```", observations.strip(), "```", ""]
    for i, s in enumerate(sessions):
        lines.append(f"## Chat {i} -- {s['topic']}")
        for t in s["turns"]:
            g, a = t.get("guess"), t.get("adapted")
            bits = [t["kind"]]
            if g:
                bits.append(f"guess {'HIT' if g['hit'] else 'miss'} (expected {g['predicted']}, took {g['picked']}, "
                            f"{g['hits']}/{g['guesses']})")
            if a:
                bits.append(f"SHAPED {' -> '.join(a['path'])}")
            if t.get("options"):
                bits.append(f"options offered: {t['options']}")
            if t.get("repeats_taken"):
                bits.append(f"REPEATED: {t['repeats_taken']}")
            if t.get("error"):
                bits.append(f"ERROR {t['error']}")
            lines.append(f"- {' · '.join(bits)} -- _{(t.get('text') or '')[:90]}_")
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--as", dest="name", default="sooraj")
    parser.add_argument("--chats", type=int, default=6)
    parser.add_argument("--actions", type=int, default=6)
    parser.add_argument("--out", default="adaptation_check.md")
    args = parser.parse_args()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY is not set in .env")
    student = build_tier_clients(api_key).best
    ws_base = args.server.replace("http", "ws", 1)

    async with httpx.AsyncClient(base_url=args.server, timeout=120) as client:
        signed = await client.post("/api/auth/dev", json={"name": args.name})
        signed.raise_for_status()
        token, learner_id = signed.json()["token"], signed.json()["learner"]["id"]
        client.headers["Authorization"] = f"Bearer {token}"
        print(f"signed in as {args.name} ({learner_id})")
        sessions = []
        for i in range(args.chats):
            topic = TOPICS[i % len(TOPICS)]
            print(f"chat {i}: {topic}")
            switch_to = TOPICS[(i + 3) % len(TOPICS)]
            try:
                sessions.append(await asyncio.wait_for(
                    play(client, ws_base, token, learner_id, student, topic, args.actions, print,
                         switch_to=switch_to),
                    timeout=600))
                if sessions[-1].get("ended_early"):
                    raise StudentUnavailable
            except (TimeoutError, StudentUnavailable):
                print("  run stopped early: network or model failure -- report covers what finished")
                break

    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "versa", "observations", "--learner", learner_id,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    observations = (await proc.communicate())[0].decode("utf-8", "replace")
    report = render(args.name, sessions, observations)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    with open(args.out.replace(".md", ".json"), "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=1)
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
