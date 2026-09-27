"""Does a thinking style get detected across many sessions -- and ONLY when
there is one? (IDEAS.md, thinking-style open checks from
docs/verification-runs/thinking_style_rerun_20260924.md: the negative
control, and whether a confirmed style reaches later prompts.)

Runs real Gemini + embeddings against the dev database (DATABASE_URL), with
a simulated student (the `best` tier, role-playing) driving every session the
way the app would: it writes its own messages, clicks options, and takes or
passes the "Continue with ->" direction links (shuffled per set, as the
server does). It never sees slot names -- only the link text.

    learner "FF"          ONE consistent hidden style across every session
    learner "FF-control"  the same topics, a DIFFERENT style each session

After each session it runs session-end consolidation (what the app's "end
chat" does) and records what the detector did: the path summary, the nearest
existing candidate and its similarity, the confirmation call's verdict, and
the candidate's resulting status.

    uv run python scripts/thinking_style_check.py [--sessions 9] [--out report.md]

Staged verification: the student is a model following a persona, so this can
show the mechanism separates a consistent style from a varied one -- not that
Versa detected a real student's style.
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
from uuid import UUID

from dotenv import load_dotenv

from versa import directions as _directions
from versa.db import create_pool
from versa.embeddings import build_embedding_client
from versa.learner import LearnerStore
from versa.llm import build_tier_clients
from versa.session_builder import build_session_loop

TOPICS = [
    "conditional probability (you have a stats class)",
    "photosynthesis (biology homework)",
    "how compound interest works (you just opened a savings account)",
    "recursion in programming (you're learning Python)",
    "Newton's third law (physics class)",
    "supply and demand (economics class)",
    "how vaccines train the immune system (you're just curious)",
    "the Pythagorean theorem (maths class)",
    "how binary numbers work (you're curious about computers)",
]

FF_PERSONA = (
    "You learn by doing. On any new topic, the FIRST thing you want is one concrete, "
    "worked case -- actual numbers or a specific instance -- before any general "
    "explanation. Once you've seen an example, you want to know where it's actually "
    "used in real life. Only after that, if at all, do you ask why it works. You find "
    "analogies and 'imagine that...' pictures unhelpful, and formal/rigorous or "
    "harder versions put you off. You write casually and briefly."
)

CONTROL_PERSONAS = [
    "You want theory first: the precise definition and the general principle before "
    "any example, and you like formal, rigorous treatments and harder versions.",
    "You think in pictures: you want an everyday analogy first, then you like to move "
    "straight on to the next related idea. Worked numbers bore you.",
    "You're in a hurry: you want the bottom-line answer only, then you ask what the "
    "bigger picture or next topic is. You never ask for examples.",
    "You're a skeptic: you challenge claims, ask why it's true and what the mechanism "
    "is, then push for a harder version.",
    "You're a historian at heart: you ask how the idea came about, who figured it out, "
    "and how it connects to other ideas.",
    "You wander: after every answer you jump to some loosely related idea you're "
    "curious about, rarely staying on one thing.",
    "You want to be tested: you ask the tutor to quiz you with a question, you answer "
    "it (sometimes wrongly), and ask to be checked.",
    "You like recipes: you want the steps as a numbered procedure you can follow, and "
    "you ask about edge cases where the steps break.",
    "You're application-driven: the first thing you ask is what it's used for in "
    "practice; then you want to go deeper into the rigorous version.",
]

MAX_ACTIONS = 6
MIN_ACTIONS = 3


def _j(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _parse_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def student_prompt(persona: str, topic: str, history: list[tuple[str, str]], reply: str | None,
                   options: list[str], links: list[str], n_actions: int) -> str:
    lines = [
        "You are role-playing a student using a tutoring app. Stay in character. Do not "
        "mention that you are role-playing, and do not describe your learning style to "
        "the tutor -- just act the way this person acts.",
        f"WHO YOU ARE: {persona}",
        f"TODAY'S TOPIC: {topic}",
    ]
    if history:
        lines.append("\nThe conversation so far (oldest first):")
        for who, text in history[-8:]:
            lines.append(f"{who}: {text[:700]}")
    if reply is None:
        lines.append(
            "\nYou have just opened a new chat. Write your first message about today's "
            "topic, in your own words -- it can be short or vague, the way a real "
            "student would type it."
        )
        lines.append('Respond with JSON only: {"action": "type", "text": "...", "why": "..."}')
        return "\n".join(lines)
    lines.append(f"\nThe tutor's latest reply:\n{reply[:2500]}")
    if options:
        lines.append("\nThe tutor asked which you meant, with clickable options:")
        lines += [f"  option {i + 1}. {o}" for i, o in enumerate(options)]
    if links:
        lines.append("\nUnder the answer the app shows 'Continue with ->' links:")
        lines += [f"  link {i + 1}. {t}" for i, t in enumerate(links)]
    choices = []
    if options:
        choices.append('click an option ("option", with its number)')
    if links:
        choices.append('tap a link ("link", with its number)')
    choices.append('type your own message ("type", with the text)')
    if n_actions >= MIN_ACTIONS:
        choices.append('end the chat ("end") if you are done with today\'s topic')
    lines.append("\nWhat do you do next? You can: " + "; ".join(choices) + ".")
    lines.append(
        'Respond with JSON only: {"action": "option"|"link"|"type"|"end", "number": <int '
        'or null>, "text": "<your message if typing, else empty>", "why": "<one short line, '
        'in character>"}'
    )
    return "\n".join(lines)


class Run:
    """One learner's sessions, and everything recorded about them."""

    def __init__(self, name: str, learner_id: UUID, personas: list[str]):
        self.name = name
        self.learner_id = learner_id
        self.personas = personas
        self.sessions: list[dict] = []


async def play_session(loop, pool, student_llm, direction_store, run: Run, idx: int,
                       nearest_log: dict) -> dict:
    persona, topic = run.personas[idx], TOPICS[idx]
    sid = await loop._transcript.create_session(run.learner_id, ablation_config=loop.ablation_config)
    rec = {"index": idx, "topic": topic, "persona": persona, "session_id": str(sid), "actions": []}
    history: list[tuple[str, str]] = []
    reply: str | None = None
    options = []
    open_set = None
    turn = 0
    for n in range(MAX_ACTIONS + 1):
        links = [c.text for c in open_set.cards] if open_set else []
        prompt = student_prompt(persona, topic, history, reply, [o.text for o in options], links, n)
        decision = {}
        for _ in range(3):
            decision = _parse_json(await student_llm.complete(prompt))
            if decision.get("action") in {"option", "link", "type", "end"}:
                break
        action = decision.get("action") or "type"
        number = decision.get("number")
        if action == "end" or n == MAX_ACTIONS:
            rec["actions"].append({"kind": "end", "why": decision.get("why", "")})
            break
        selected_option_id = None
        continues = None
        picked_card = None
        if action == "option" and options and isinstance(number, int) and 1 <= number <= len(options):
            option = options[number - 1]
            text, selected_option_id = option.text, option.id
            label = f"[clicked option] {text}"
        elif action == "link" and open_set and isinstance(number, int) and 1 <= number <= len(open_set.cards):
            picked_card = open_set.cards[number - 1]
            text = continues = picked_card.text
            label = f"[took link: {picked_card.slot}, shown #{number}] {text}"
        else:
            text = (decision.get("text") or "").strip() or "ok, go on"
            label = text
        # What the learner did with the open directions (server.py _run_turn).
        if open_set is not None:
            try:
                if picked_card is not None:
                    await direction_store.record_event(
                        set_id=open_set.id, kind="picked", card_id=picked_card.id, next_turn_index=turn)
                elif selected_option_id is None:
                    await direction_store.record_event(
                        set_id=open_set.id, kind="passed", next_turn_index=turn)
            except _directions.AlreadySettled:
                pass
        open_set = None
        start = time.monotonic()
        try:
            reply = await loop.handle_turn(sid, turn, text, selected_option_id, continues=continues)
            await loop.wait_for_background_tasks()
            options = await loop.pending_options(sid)
            error = None
        except Exception as exc:  # a failed turn is reported, not hidden
            reply, options, error = f"(turn failed: {exc})", [], repr(exc)
        act = {"turn": turn, "sent": label, "why": decision.get("why", ""), "error": error,
               "options": [o.text for o in options], "secs": round(time.monotonic() - start, 1),
               "reply_words": len((reply or "").split())}
        history.append(("student", label))
        history.append(("tutor", reply or ""))
        if not options and error is None:
            knobs = await loop._transcript.get_knobs(sid)
            try:
                cards = await loop._call_node(
                    loop.suggest_directions, sid, turn, message=text, answer=reply,
                    depth=knobs.depth, breadth=knobs.breadth)
                if cards:
                    open_set = await direction_store.add_set(
                        session_id=sid, turn_index=turn, knobs=knobs, cards=cards,
                        positions=_directions.shuffled_positions(), presentation="fork")
                    act["links"] = [f"{c.slot}: {c.text}" for c in open_set.cards]
            except Exception as exc:
                act["directions_error"] = repr(exc)
        rec["actions"].append(act)
        turn += 1

    # Session end, as the app's "end chat" does (server.py end_session).
    nearest_log.pop(str(sid), None)
    before = {str(c.id): (c.status.value, c.confirmation_count)
              for c in await loop._thinking_styles.list_by_learner(run.learner_id)}
    result = None
    if await loop._transcript.claim_for_consolidation(sid):
        try:
            result = await loop.consolidate_session(sid)
            await loop.wait_for_background_tasks()
        except Exception as exc:
            rec["consolidation_error"] = repr(exc)
    rec["nearest"] = nearest_log.get(str(sid))
    async with pool.acquire() as conn:
        rec["facts"] = [dict(r) for r in await conn.fetch(
            "SELECT fact_type, situation, resolution, reason FROM learner_facts "
            "WHERE session_id=$1 ORDER BY turn_index", sid)]
        s = await conn.fetchrow(
            "SELECT output_json FROM node_calls WHERE session_id=$1 AND node_name='SummarizeSessionPath' "
            "ORDER BY seq DESC LIMIT 1", sid)
        rec["path_summary"] = (_j(s["output_json"]) or {}).get("summary") if s else None
        c = await conn.fetchrow(
            "SELECT input_json, output_json FROM node_calls WHERE session_id=$1 "
            "AND node_name='ConfirmThinkingStyleMatch' ORDER BY seq DESC LIMIT 1", sid)
        rec["confirm_call"] = _j(c["output_json"]) if c else None
        s_in = await conn.fetchrow(
            "SELECT input_json FROM node_calls WHERE session_id=$1 AND node_name='SummarizeSessionPath' "
            "ORDER BY seq DESC LIMIT 1", sid)
        rec["direction_path"] = (_j(s_in["input_json"]) or {}).get("direction_path") if s_in else None
        hints = await conn.fetch(
            "SELECT node_name, input_json->>'thinking_style_hint' AS hint FROM node_calls "
            "WHERE session_id=$1 AND input_json ? 'thinking_style_hint'", sid)
        rec["hint_calls"] = sum(1 for h in hints if h["hint"])
        rec["hint_calls_total"] = len(hints)
        rec["hint_text"] = next((h["hint"] for h in hints if h["hint"]), None)
        rec["slot_picks"] = [r["slot"] for r in await conn.fetch(
            "SELECT c.slot FROM direction_events e JOIN direction_sets s ON s.id=e.set_id "
            "JOIN direction_cards c ON c.id=e.card_id WHERE s.session_id=$1 AND e.kind='picked' "
            "ORDER BY s.turn_index", sid)]
        rec["passes"] = await conn.fetchval(
            "SELECT count(*) FROM direction_events e JOIN direction_sets s ON s.id=e.set_id "
            "WHERE s.session_id=$1 AND e.kind='passed'", sid)
        rec["node_calls"] = await conn.fetchval("SELECT count(*) FROM node_calls WHERE session_id=$1", sid)
    if result is not None:
        was = before.get(str(result.id))
        rec["outcome"] = {
            "candidate_id": str(result.id), "status": result.status.value,
            "count": result.confirmation_count,
            "event": "created" if was is None else ("promoted" if was[0] != result.status.value
                                                     else "confirmed"),
        }
    else:
        rec["outcome"] = None
    run.sessions.append(rec)
    o = rec["outcome"] or {}
    print(f"[{run.name}] session {idx} done: {len(rec['actions'])} actions, picks={rec['slot_picks']}, "
          f"facts={len(rec['facts'])}, outcome={o.get('event')} {o.get('status')} x{o.get('count')}",
          flush=True)
    return rec


async def drive(loop, pool, student_llm, direction_store, run: Run, n_sessions: int, nearest_log,
                dump_path: str):
    for i in range(n_sessions):
        await play_session(loop, pool, student_llm, direction_store, run, i, nearest_log)
        with open(dump_path, "w", encoding="utf-8") as fh:
            json.dump([r.__dict__ | {"learner_id": str(r.learner_id)} for r in RUNS], fh,
                      indent=1, default=str)


RUNS: list[Run] = []


def render(runs: list[Run], final_candidates: dict[str, list]) -> str:
    out: list[str] = []
    e = out.append
    e(f"# Thinking-style detection over many sessions -- learner FF ({time.strftime('%Y-%m-%d %H:%M')})\n")
    e("Real Gemini (billed key), real dev database, full `build_session_loop`. A simulated "
      "student (the `best` tier, role-playing a hidden persona) wrote every message, clicked "
      "options and took or passed the shuffled \"Continue with ->\" links; `consolidate_session` "
      "ran after every session. **Staged verification**: this can show whether the detector "
      "separates a consistent style from a varied one, not that it detected a real student.\n")
    for run in runs:
        e(f"- `{run.name}` = {run.learner_id}")
    for run in runs:
        e(f"\n## {run.name}\n")
        if run.name == "FF":
            e(f"Hidden persona (every session): _{FF_PERSONA}_\n")
        else:
            e("Hidden persona: a different one each session (listed per session below).\n")
        e("| # | topic | actions | options clicked | links taken (slot, in order) | passes | facts | "
          "nearest candidate sim | confirm verdict | outcome | style hint in prompts |")
        e("|---|---|---|---|---|---|---|---|---|---|---|")
        for s in run.sessions:
            acts = [a for a in s["actions"] if a.get("kind") != "end"]
            clicked = sum(1 for a in acts if a["sent"].startswith("[clicked option]"))
            near = s.get("nearest")
            near_txt = f"{near['similarity']:.3f}" if near else "-"
            verdict = s["confirm_call"].get("confirms") if s.get("confirm_call") else "-"
            o = s.get("outcome") or {}
            out_txt = f"{o.get('event')} -> {o.get('status')} x{o.get('count')}" if o else "no candidate"
            e(f"| {s['index']} | {s['topic'].split(' (')[0]} | {len(acts)} | {clicked} | "
              f"{' -> '.join(s['slot_picks']) or '-'} | {s['passes']} | {len(s['facts'])} | {near_txt} | "
              f"{verdict} | {out_txt} | {s['hint_calls']}/{s['hint_calls_total']} |")
        picks = Counter(p for s in run.sessions for p in s["slot_picks"])
        first = Counter(s["slot_picks"][0] for s in run.sessions if s["slot_picks"])
        e(f"\nLinks taken overall: {dict(picks.most_common())}; first link taken per session: "
          f"{dict(first.most_common())}\n")
        e("### Candidates at the end\n")
        for c in final_candidates[run.name]:
            e(f"- **[{c['status']}, {c['confirmation_count']} sessions]** {c['path_summary']}")
        e("\n### Per session\n")
        for s in run.sessions:
            e(f"#### {run.name} session {s['index']} -- {s['topic']}\n")
            if run.name != "FF":
                e(f"Persona: _{s['persona']}_\n")
            e(f"session `{s['session_id']}`, {s['node_calls']} node_calls rows")
            for a in s["actions"]:
                if a.get("kind") == "end":
                    e(f"- (ended the chat) _why: {a['why']}_")
                    continue
                err = f" **TURN FAILED: {a['error']}**" if a.get("error") else ""
                e(f"- turn {a['turn']} ({a['secs']}s): {a['sent']}{err}  \n  _why: {a['why']}_")
                if a["options"]:
                    e(f"  - options offered: {a['options']}")
                else:
                    e(f"  - answer: {a['reply_words']} words")
                if a.get("links"):
                    e(f"  - links shown (slot: text, display order): {a['links']}")
                if a.get("directions_error"):
                    e(f"  - directions failed: {a['directions_error']}")
            e(f"\n- facts written: {len(s['facts'])}")
            for f in s["facts"]:
                e(f"  - [{f['fact_type']}] {f['situation']} -> {f['resolution']}"
                  + (f" (reason: {f['reason']})" if f["reason"] else ""))
            e(f"- direction path given to the summarizer: {s['direction_path'] or '(none)'}")
            e(f"- path summary: {s['path_summary'] or '(none -- no facts, consolidation skipped)'}")
            if s.get("nearest"):
                e(f"- nearest candidate (sim {s['nearest']['similarity']:.3f}): {s['nearest']['summary']}")
            if s.get("confirm_call"):
                e(f"- ConfirmThinkingStyleMatch: {s['confirm_call']}")
            e(f"- outcome: {s.get('outcome')}")
            if s.get("hint_text"):
                e(f"- thinking-style hint reaching prompts: {s['hint_text']}")
            if s.get("consolidation_error"):
                e(f"- **consolidation failed: {s['consolidation_error']}**")
            e("")
    return "\n".join(out)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=len(TOPICS))
    parser.add_argument("--out", default=None)
    parser.add_argument("--dump", default="thinking_style_check.json")
    parser.add_argument("--ff-label", default="FF")
    args = parser.parse_args()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "your-key-here":
        sys.exit("GEMINI_API_KEY is not set in .env")

    pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=8)
    try:
        tiers = build_tier_clients(api_key)
        loop = build_session_loop(pool, tiers, build_embedding_client(api_key))
        direction_store = _directions.DirectionStore(pool)
        learners = LearnerStore(pool)
        control_label = f"{args.ff_label}-control"
        made = []
        for label in (args.ff_label, control_label):
            existing = await learners.get_by_label(label)
            if existing is not None:
                # reuse only a learner that never had a session (e.g. a run
                # that crashed on startup); never mix into a used one
                if await pool.fetchval("SELECT count(*) FROM sessions WHERE learner_id=$1", existing.id):
                    sys.exit(f"learner {label!r} already has sessions -- pick another --ff-label")
                made.append(existing)
            else:
                made.append(await learners.create(label=label))
        ff, ctrl = made

        # Record what the detector's nearest-candidate search saw (the
        # similarity is not stored anywhere), keyed by the session being
        # consolidated. Read-only wrapper; the search itself is unchanged.
        nearest_log: dict[str, dict] = {}
        store = loop._thinking_styles
        original_search = store.search_similar
        current: dict[UUID, str] = {}

        async def logged_search(learner_id, embedding, limit=5):
            found = await original_search(learner_id, embedding, limit)
            sid = current.get(learner_id)
            if sid and found:
                nearest_log[sid] = {"summary": found[0][0].path_summary, "similarity": found[0][1]}
            return found

        store.search_similar = logged_search
        original_consolidate = loop.consolidate_session

        async def tracked_consolidate(session_id):
            learner_id = await loop._transcript.get_learner_id(session_id)
            current[learner_id] = str(session_id)
            return await original_consolidate(session_id)

        loop.consolidate_session = tracked_consolidate

        RUNS.extend([Run(ff.label, ff.id, [FF_PERSONA] * len(TOPICS)),
                     Run(ctrl.label, ctrl.id, CONTROL_PERSONAS)])
        print(f"FF={ff.id} control={ctrl.id}", flush=True)
        await asyncio.gather(*(drive(loop, pool, tiers.best, direction_store, r, args.sessions,
                                     nearest_log, args.dump) for r in RUNS))
        final = {}
        for r in RUNS:
            final[r.name] = [
                {"status": c.status.value, "confirmation_count": c.confirmation_count,
                 "path_summary": c.path_summary}
                for c in await store.list_by_learner(r.learner_id)
            ]
        report = render(RUNS, final)
        print(report)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
