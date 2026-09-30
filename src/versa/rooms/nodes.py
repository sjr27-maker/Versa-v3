"""RoomDirector: after something happens in a room, decide whether Versa
says anything, to whom, and what -- or stays quiet.

One call per burst of activity (the hub coalesces messages that arrive while
a call is running into the next one). Its output is a short list of actions:

    say            a chat line, a teaching explanation ('content') or a
                   question -- to everyone, or to one person (optionally
                   private, so only they see it)
    options        clickable choices for one person or for everyone
    task           a task for one person (or one each) -- ALWAYS a quiz or
                   a puzzle answered with one tap (2-4 options, one right),
                   never something to type (2026-10-01); the tap itself is
                   graded (hub.py), so a right answer completes it
    race           one question for EVERYONE, answered with one tap: the
                   first right tap wins (graded by the hub, which closes the
                   race and keeps score) -- the competitive beat
    complete_task  that person's current task is done, judged from what
                   THEY wrote (they chose to type)
    part_done      the group has covered one part of the topic's outline

The flow the director follows: explain a piece of the current part (acted
out on the stage), quiz everyone on it, and carry on piece by piece until
the part's content is covered, then the next part -- how long a part takes
follows how much it holds.

No actions means Versa stays quiet, which is the default while the people
are talking to each other. Every action is validated here (`parse_actions`):
an unknown member, kind or shape is dropped, so a model that drifts can make
Versa quieter, never make it write something malformed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel

from versa.formatting import MATH_STYLE_JSON, repair_latex_escapes
from versa.llm import LLMClient
from versa.rooms.store import TASK_KINDS

logger = logging.getLogger(__name__)

MAX_ACTIONS = 4
MAX_TASKS = 8
MAX_OPTIONS = 4
_SAY_KINDS = ("chat", "content", "question")
_EVERYONE = {"all", "everyone", "everybody", "group", "room", ""}

# "@Versa", "Versa, ...", "hey versa" -- somebody is talking to Versa itself.
_MENTION = re.compile(r"(?<![\w@])@?versa\b", re.IGNORECASE)


def mentions_versa(text: str) -> bool:
    return bool(_MENTION.search(text or ""))


class RoomAction(BaseModel):
    type: str
    to: str | None = None  # a member NAME, or None = everyone
    private: bool = False
    kind: str | None = None  # say: chat|content|question; task: a TASK_KINDS value
    text: str = ""
    prompt: str = ""
    options: list[str] = []
    evidence: str = ""
    answer: str = ""  # task: the right option's text
    part: int | None = None  # part_done: which outline part (1-based)


class RoomDecision(BaseModel):
    reason: str = ""
    actions: list[RoomAction] = []
    # what the model asked for that couldn't be used, and why -- kept in the
    # audit (room_node_calls) so a quiet Versa can be explained
    dropped: list[dict] = []


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_object(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(repair_latex_escapes(match.group(0)))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _quiz_options(item: dict) -> tuple[list[str], str] | None:
    """(options, the right one) of a task, or None when it isn't a quiz a
    tap can answer: 2-4 distinct options, and the answer one of them."""
    seen: set[str] = set()
    options: list[str] = []
    # "choices" is what the prompt asks for (2026-10-01, live: with "options"
    # the model kept leaving them out of tasks -- "options" is also an action)
    raw = item.get("choices") if isinstance(item.get("choices"), list) else item.get("options")
    for o in raw if isinstance(raw, list) else []:
        text = _clip(o.get("text") if isinstance(o, dict) else o, 140)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            options.append(text)
    options = options[:MAX_OPTIONS]
    if len(options) < 2:
        return None
    right = _right_option(item.get("answer"), options)
    if right is None:
        return None
    return options, right


def _loose(text: str) -> str:
    """Text compared the forgiving way: no $...$, no LaTeX commands, no
    punctuation or case -- '$0\\text{ m}$' and '0 m' are the same answer."""
    t = re.sub(r"\\[a-zA-Z]+", " ", str(text))
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", t).lower().split())


def _right_option(answer: object, options: list[str]) -> str | None:
    """Which option `answer` names: its exact text, the same text written a
    little differently, its letter ("B", "b)") or its number (1-based, or a
    0-based index when given as an int)."""
    if isinstance(answer, int) and not isinstance(answer, bool):
        return options[answer] if 0 <= answer < len(options) else None
    raw = str(answer or "").strip()
    if not raw:
        return None
    for o in options:
        if o.lower() == raw.lower():
            return o
    loose = _loose(raw)
    matches = [o for o in options if _loose(o) == loose]
    if len(matches) == 1:
        return matches[0]
    letter = re.fullmatch(r"\(?([a-dA-D])[).:]?", raw)
    if letter:
        i = "abcd".index(letter.group(1).lower())
        return options[i] if i < len(options) else None
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return None


def parse_actions(raw: str, member_names: list[str], part_count: int = 0) -> RoomDecision:
    """The model's JSON -> validated actions. `to` is resolved to a member's
    exact name (case-insensitive) or None for everyone; anything addressed
    to someone who isn't in the room is dropped. A task that isn't a
    tap-to-answer quiz is dropped too: nobody is set something to type."""
    parsed = _json_object(raw) or {}
    by_key = {n.lower(): n for n in member_names}

    def audience(value: Any) -> tuple[bool, str | None]:
        key = str(value or "").strip().lstrip("@").lower()
        if key in _EVERYONE:
            return True, None
        name = by_key.get(key)
        return name is not None, name

    actions: list[RoomAction] = []
    dropped: list[dict] = []
    raw_actions = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
    for item in raw_actions:
        if len(actions) >= MAX_ACTIONS:
            break
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "")).strip().lower()
        ok, to = audience(item.get("to"))
        if kind == "part_done":
            try:
                part = int(item.get("part"))
            except (TypeError, ValueError):
                continue
            if 1 <= part <= part_count:
                actions.append(RoomAction(type="part_done", part=part, evidence=_clip(item.get("evidence"), 300)))
            continue
        if not ok:
            continue
        if kind == "say":
            text = _clip(item.get("text"), 2400)
            say_kind = str(item.get("kind") or "chat").strip().lower()
            if not text:
                continue
            actions.append(RoomAction(
                type="say", to=to, kind=say_kind if say_kind in _SAY_KINDS else "chat", text=text,
                private=bool(item.get("private")) and to is not None,
            ))
        elif kind == "options":
            prompt = _clip(item.get("prompt") or item.get("text"), 400)
            seen: set[str] = set()
            options: list[str] = []
            for o in item.get("options") if isinstance(item.get("options"), list) else []:
                text = _clip(o.get("text") if isinstance(o, dict) else o, 140)
                if text and text.lower() not in seen:
                    seen.add(text.lower())
                    options.append(text)
            if not prompt or len(options) < 2:
                continue
            actions.append(RoomAction(type="options", to=to, prompt=prompt, options=options[:MAX_OPTIONS]))
        elif kind == "task":
            description = _clip(
                item.get("description") or item.get("text") or item.get("prompt") or item.get("question"), 400,
            )
            quiz = _quiz_options(item)
            if not description or quiz is None:
                dropped.append({"action": item, "why": "a task must be a quiz: 2-4 options and the right one"})
                continue
            task_kind = str(item.get("task_kind") or item.get("kind") or "check").strip().lower()
            actions.append(RoomAction(
                type="task", to=to, text=description, options=quiz[0], answer=quiz[1],
                kind=task_kind if task_kind in TASK_KINDS else "check",
            ))
        elif kind == "race":
            prompt = _clip(
                item.get("prompt") or item.get("text") or item.get("description") or item.get("question"), 400,
            )
            quiz = _quiz_options(item)
            if not prompt or quiz is None:
                dropped.append({"action": item, "why": "a race needs 2-4 options and the right one"})
                continue
            actions.append(RoomAction(type="race", to=None, prompt=prompt, options=quiz[0], answer=quiz[1]))
        elif kind == "complete_task":
            if to is None:
                continue
            actions.append(RoomAction(type="complete_task", to=to, evidence=_clip(item.get("evidence"), 300)))
    # Tasks and a race have their own place in the reply, where the schema
    # makes their choices and answer required (2026-10-01, live: as flat
    # actions the model kept leaving the choices out of every task). The
    # older in-`actions` form above is still read.
    for item in (parsed.get("tasks") if isinstance(parsed.get("tasks"), list) else [])[:MAX_TASKS]:
        if not isinstance(item, dict):
            continue
        ok, to = audience(item.get("to"))
        description = _clip(item.get("question") or item.get("description") or item.get("text"), 400)
        quiz = _quiz_options(item)
        if not ok or not description or quiz is None:
            dropped.append({"action": item, "why": "a task needs someone in the room, a question, 2-4 choices and the right one"})
            continue
        task_kind = str(item.get("task_kind") or "check").strip().lower()
        actions.append(RoomAction(
            type="task", to=to, text=description, options=quiz[0], answer=quiz[1],
            kind=task_kind if task_kind in TASK_KINDS else "check",
        ))
    race = parsed.get("race")
    if isinstance(race, dict) and any(race.values()):
        prompt = _clip(race.get("question") or race.get("prompt") or race.get("text"), 400)
        quiz = _quiz_options(race)
        if prompt and quiz is not None:
            actions.append(RoomAction(type="race", to=None, prompt=prompt, options=quiz[0], answer=quiz[1]))
        else:
            dropped.append({"action": race, "why": "a race needs a question, 2-4 choices and the right one"})
    if dropped:
        logger.warning("room director: dropped %d action(s): %s", len(dropped), dropped)
    return RoomDecision(reason=_clip(parsed.get("reason"), 400), actions=actions, dropped=dropped)


def _member_block(members: list[dict]) -> str:
    lines = []
    for m in members:
        current = m.get("current_task")
        options = f' [tap: {" | ".join(current["options"])}]' if current and current.get("options") else ""
        task = (
            f'current task ({current["kind"]}): "{current["description"]}"{options}'
            if current else "NO open task"
        )
        online = "online" if m.get("online") else "offline"
        score = f"; score {m.get('points', 0)}" if m.get("points") else ""
        queued = f"; {m['queued_count']} more task(s) queued after it" if m.get("queued_count") else ""
        lines.append(f"- {m['name']} ({online}) -- {task}{queued}; tasks finished: {m.get('done_count', 0)}{score}")
    return "\n".join(lines) or "- (nobody yet)"


def room_prompt(
    *,
    title: str,
    outline: list[dict],
    resource_excerpt: str,
    members: list[dict],
    open_options: list[str],
    transcript: list[str],
    events: list[str],
    must_reply: bool,
    done_parts: list[int] | None = None,
) -> str:
    done = set(done_parts or [])
    current_part = next((i + 1 for i in range(len(outline)) if i + 1 not in done), None)

    def mark(n: int) -> str:
        return " [COVERED]" if n in done else (" [CURRENT]" if n == current_part else "")

    parts = "".join(
        f"{i + 1}. {p['title']} -- {p.get('summary', '')}{mark(i + 1)}\n" for i, p in enumerate(outline)
    )
    resource = (
        f"\nThe group is learning from this resource (excerpt, may be cut off):\n<<<\n{resource_excerpt}\n>>>\n"
        if resource_excerpt else ""
    )
    options_block = "".join(f"- {o}\n" for o in open_options) or "- (none)\n"
    chat = "\n".join(transcript) or "(no messages yet)"
    happened = "\n".join(f"EVENT: {e}" for e in events)
    reply_rule = (
        "\nSomeone addressed you directly (or picked one of your options): you MUST "
        "respond this time, to them.\n"
        if must_reply else ""
    )
    return (
        "ROOM:DIRECT\n"
        "You are Versa, a tutor who is ONE MEMBER of a group chat (like a WhatsApp "
        "group) where a few people are learning a topic together. You are not "
        "giving a lecture: you are a sharp, friendly study partner who watches "
        "everything and steps in only when it helps.\n\n"
        f"TOPIC: {title}\nIts parts:\n{parts}{resource}\n"
        f"PEOPLE IN THE ROOM:\n{_member_block(members)}\n\n"
        f"OPTIONS CURRENTLY WAITING FOR A CLICK:\n{options_block}\n"
        f"CHAT SO FAR (oldest first; [n] is the message number):\n{chat}\n\n"
        f"WHAT JUST HAPPENED:\n{happened}\n{reply_rule}\n"
        "WHEN TO SPEAK. Staying quiet is the default while people are talking to "
        "each other productively -- a real person in a group doesn't answer every "
        "message. Speak when:\n"
        "- someone talks to you, or asks a question the others haven't answered;\n"
        "- someone states something wrong about the topic (correct it kindly);\n"
        "- someone is stuck, confused or frustrated;\n"
        "- the chat has drifted off the topic for a while;\n"
        "- someone finished their task (acknowledge it, give them the next one);\n"
        "- someone new joined, or the room was just created (welcome them, give a task);\n"
        "- two people could learn from each other (connect them: ask one to explain to the other).\n\n"
        "HOW THE GROUP LEARNS. Work through the parts in order, starting at the [CURRENT] one: "
        "explain one piece of it (kind \"content\" -- it is acted out on the stage), then give "
        "each person a task on that piece, and when they've answered, explain the next piece. "
        "Keep going until everything the part holds is covered (the resource's text for it, when "
        "there is one), then mark it with part_done, say so in one line, and move on to the next "
        "part. A big part takes many pieces, a small one few.\n\n"
        "TASKS. A task (in `tasks`) is ALWAYS a quick quiz or a small puzzle answered with ONE TAP: give its "
        "`choices` (2-4 short ones) and the `answer` (exactly one of the choices, copied exactly). "
        "A task or race without its `choices` is thrown away, so never leave them out. "
        "Never set anything to type, write or explain -- nobody has to type; people type only when "
        "they choose to. Vary the form (a quiz question, spot the mistake, what happens next, which "
        "comes first, odd one out), fit it to the piece just explained, and make wrong options "
        "believable mistakes. Every person should always have exactly ONE open task (don't give a "
        "new one while their current one is open, and don't send them other options meanwhile -- "
        "that would replace their task's choices). Taps are graded for you: a right one completes "
        "the task; after a wrong one, give a short kind correction, then a fresh task. Use "
        "complete_task only when someone TYPED something that does their task -- never because "
        "someone else did the work.\n\n"
        "COMPETITION. Keep it lively. After explaining a piece, choose how to check it: usually "
        "individual tasks (one each, fitted to each person), but SOMETIMES -- roughly one piece in "
        "three, and only when two or more people are online -- a RACE (in `race`) instead: one question for "
        "everyone, first right tap wins. Races suit quick recall and spot-the-answer questions. The "
        "hub closes a race the moment someone wins and keeps score (a race win is worth 3, a task "
        "answered right 1). Announce a winner warmly in one line, cheer a close call, mention the "
        "scoreboard now and then -- friendly rivalry, never mocking anyone. Don't start a race while "
        "one is still open.\n\n"
        "WHO TO TALK TO. Message everyone when it concerns the group. Message one "
        "person (to: their name) when it's about them. Make it private only when "
        "saying it in front of the group would be awkward or would spoil it for "
        "others (a hint on their own task, a gentle personal correction).\n\n"
        "HOW TO WRITE. Chat lines: 1-3 short sentences, casual, like a friend in a "
        "group chat, no headings. When you actually teach something, use kind "
        "\"content\": a clear explanation of at most ~180 words, plain text (simple "
        "'- ' bullets are fine). Use \"question\" when you ask the group or someone "
        "something. Use options when a quick click helps (choose what to explore "
        "next, a multiple-choice check question): 2-4 short options. Address people "
        "by their exact names. At most 3 actions; an empty list means you stay quiet.\n"
        f"{MATH_STYLE_JSON}\n\n"
        "Respond with JSON:\n"
        '{"reason": "one sentence: why you act or stay quiet", "actions": ['
        '{"type": "say", "to": "all" or a name, "private": false, "kind": "chat|content|question", "text": "..."}, '
        '{"type": "options", "to": "all" or a name, "prompt": "...", "options": ["...", "..."]}, '
        '{"type": "complete_task", "to": a name, "evidence": "what they wrote that shows it"}, '
        '{"type": "part_done", "to": "all", "part": the part number, "evidence": "what the group covered"}'
        "], "
        '"tasks": [{"to": a name or "all" (one each), "question": "the quiz question or puzzle", '
        '"choices": ["...", "..."], "answer": "the right choice, exactly"}] -- or [] when you set none, '
        '"race": {"question": "...", "choices": ["...", "..."], "answer": "the right choice, exactly"} -- or null}'
    )


class RoomDirector:
    """One call: the room's state + what just happened -> RoomDecision."""

    name = "RoomDirector"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(self, **kwargs) -> str:
        return room_prompt(**kwargs)

    async def run(
        self,
        *,
        title: str,
        outline: list[dict],
        resource_excerpt: str,
        members: list[dict],
        open_options: list[str],
        transcript: list[str],
        events: list[str],
        must_reply: bool,
        done_parts: list[int] | None = None,
    ) -> RoomDecision:
        self.last_call_count = 0
        raw = await self._llm.complete(room_prompt(
            title=title, outline=outline, resource_excerpt=resource_excerpt, members=members,
            open_options=open_options, transcript=transcript, events=events, must_reply=must_reply,
            done_parts=done_parts,
        ))
        self.last_call_count += 1
        return parse_actions(raw, [m["name"] for m in members], part_count=len(outline))
