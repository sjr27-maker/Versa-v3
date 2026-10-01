"""What a chat is about, in a clean title and one sentence -- for the Home
feed's "Continue" cards, which used to show the chat's first message
verbatim ("help with this [Attached picture -- ...").

One fast-tier call (`DescribeChat`) after a Sandbox chat's first answer, and
again once the chat has grown by `REDESCRIBE_AFTER_TURNS` turns, so a chat
that moved on is not named after its opening forever. Given only the
learner's own messages in THIS chat and the start of the latest answer --
nothing about the learner, nothing from another chat.

Nothing new is stored: the call goes through `SessionLoop._call_node`
(invariant 2), and the title is read back from its `node_calls` row (the
latest one wins) by `TranscriptStore.list_session_summaries`. A chat with no
such row -- every chat from before this existed, or one whose call failed --
shows its opening message, as before.

It is a label, not evidence: nothing in claims, thinking styles,
observations or the direction cards reads it.
"""

from __future__ import annotations

import json
import re

from versa.llm import LLMClient

NODE_NAME = "DescribeChat"
# A chat is described again once it has this many more turns than when it
# was last described.
REDESCRIBE_AFTER_TURNS = 6
_MAX_MESSAGES = 6
_MESSAGE_CHARS = 300
_ANSWER_CHARS = 400
_TITLE_CHARS = 70
_ABOUT_CHARS = 160


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_chat_prompt(messages: list[str], answer: str = "") -> str:
    # the opening and the latest messages: where it started, where it is now
    kept = messages if len(messages) <= _MAX_MESSAGES else [*messages[:2], *messages[-(_MAX_MESSAGES - 2):]]
    lines = "\n".join(f"  - {_clip(m, _MESSAGE_CHARS)}" for m in kept if m.strip())
    began = f"The tutor's latest answer began:\n  {_clip(answer, _ANSWER_CHARS)}\n" if answer.strip() else ""
    return (
        "CHAT:DESCRIBE\n"
        "A student is chatting with a tutor. Name what this chat is about, for the card that "
        "lets them pick it up again later.\n"
        "The student's messages so far, in order:\n"
        f"{lines}\n"
        f"{began}"
        "`title`: 3 to 7 words naming the subject, like a chapter heading (\"How cells turn "
        "glucose into ATP\").\n"
        "`about`: one clean, specific sentence (max 20 words) saying what the chat covers -- the "
        "subject and the angle taken.\n"
        "Describe the subject, not the student (\"Ohm's law, and why a wire heats up\", never "
        "\"The student asks about...\"). Do not quote the messages. Plain words only: no question "
        "marks, no LaTeX, no markdown.\n"
        'Respond with JSON: {"title": "...", "about": "..."}'
    )


def parse_description(raw: str) -> dict[str, str] | None:
    """Lenient: anything that isn't the expected JSON with a usable title is
    None -- the caller records the attempt, and the card keeps the chat's
    opening message."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    title, about = data.get("title"), data.get("about")
    if not isinstance(title, str) or not title.strip():
        return None
    title = _clip(title.strip().strip("\"'“”").rstrip("."), _TITLE_CHARS)
    about = _clip(about, _ABOUT_CHARS) if isinstance(about, str) else ""
    return {"title": title, "about": about}


class DescribeChat:
    """One fast-tier call: a chat's own messages in, {"title", "about"} out
    -- or {} for a reply that couldn't be read (`node_calls.output_json` is
    NOT NULL, and the attempt should be on record either way). Recorded
    through SessionLoop._call_node."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, messages: list[str], answer: str = "") -> dict[str, str]:
        self.last_call_count = 0
        raw = await self._llm.complete(describe_chat_prompt(messages, answer))
        self.last_call_count += 1
        return parse_description(raw) or {}
