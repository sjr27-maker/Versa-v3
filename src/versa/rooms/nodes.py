"""RoomDirector: after something happens in a room, decide whether Versa
says anything, to whom, and what -- or stays quiet.

One call per burst of activity (the hub coalesces messages that arrive while
a call is running into the next one). Its output is a short list of actions:

    say            a chat line, a teaching explanation ('content') or a
                   question -- to everyone, or to one person (optionally
                   private, so only they see it)
    options        clickable choices for one person or for everyone
    task           a task in the topic for one person (or one each)
    complete_task  that person's current task is done, judged from what
                   THEY wrote

No actions means Versa stays quiet, which is the default while the people
are talking to each other. Every action is validated here (`parse_actions`):
an unknown member, kind or shape is dropped, so a model that drifts can make
Versa quieter, never make it write something malformed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

from versa.llm import LLMClient
from versa.rooms.store import TASK_KINDS

MAX_ACTIONS = 4
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


class RoomDecision(BaseModel):
    reason: str = ""
    actions: list[RoomAction] = []


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_object(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_actions(raw: str, member_names: list[str]) -> RoomDecision:
    """The model's JSON -> validated actions. `to` is resolved to a member's
    exact name (case-insensitive) or None for everyone; anything addressed
    to someone who isn't in the room is dropped."""
    parsed = _json_object(raw) or {}
    by_key = {n.lower(): n for n in member_names}

    def audience(value: Any) -> tuple[bool, str | None]:
        key = str(value or "").strip().lstrip("@").lower()
        if key in _EVERYONE:
            return True, None
        name = by_key.get(key)
        return name is not None, name

    actions: list[RoomAction] = []
    raw_actions = parsed.get("actions") if isinstance(parsed.get("actions"), list) else []
    for item in raw_actions:
        if len(actions) >= MAX_ACTIONS:
            break
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "")).strip().lower()
        ok, to = audience(item.get("to"))
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
            description = _clip(item.get("description") or item.get("text"), 400)
            task_kind = str(item.get("task_kind") or item.get("kind") or "learn").strip().lower()
            if not description:
                continue
            actions.append(RoomAction(
                type="task", to=to, text=description,
                kind=task_kind if task_kind in TASK_KINDS else "learn",
            ))
        elif kind == "complete_task":
            if to is None:
                continue
            actions.append(RoomAction(type="complete_task", to=to, evidence=_clip(item.get("evidence"), 300)))
    return RoomDecision(reason=_clip(parsed.get("reason"), 400), actions=actions)


def _member_block(members: list[dict]) -> str:
    lines = []
    for m in members:
        current = m.get("current_task")
        task = (
            f'current task ({current["kind"]}): "{current["description"]}"'
            if current else "NO open task"
        )
        online = "online" if m.get("online") else "offline"
        queued = f"; {m['queued_count']} more task(s) queued after it" if m.get("queued_count") else ""
        lines.append(f"- {m['name']} ({online}) -- {task}{queued}; tasks finished: {m.get('done_count', 0)}")
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
) -> str:
    parts = "".join(f"{i + 1}. {p['title']} -- {p.get('summary', '')}\n" for i, p in enumerate(outline))
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
        "TASKS. Every person should always have exactly ONE open task on this topic "
        "(don't give a new one while their current one is still open). "
        "Give different people different tasks that fit together (one explains a part, "
        "another tests it with a question, another applies it to an example), each "
        "doable in a few minutes of chatting, and move through the topic's parts in "
        "order. Only mark a task complete from what THAT person actually wrote -- "
        "never because someone else did the work.\n\n"
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
        "by their exact names. At most 3 actions; an empty list means you stay quiet.\n\n"
        "Respond with JSON:\n"
        '{"reason": "one sentence: why you act or stay quiet", "actions": ['
        '{"type": "say", "to": "all" or a name, "private": false, "kind": "chat|content|question", "text": "..."}, '
        '{"type": "options", "to": "all" or a name, "prompt": "...", "options": ["...", "..."]}, '
        '{"type": "task", "to": a name or "all" (one each), "task_kind": "learn|practice|apply|check|discuss", "description": "..."}, '
        '{"type": "complete_task", "to": a name, "evidence": "what they wrote that shows it"}'
        "]}"
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
    ) -> RoomDecision:
        self.last_call_count = 0
        raw = await self._llm.complete(room_prompt(
            title=title, outline=outline, resource_excerpt=resource_excerpt, members=members,
            open_options=open_options, transcript=transcript, events=events, must_reply=must_reply,
        ))
        self.last_call_count += 1
        return parse_actions(raw, [m["name"] for m in members])
