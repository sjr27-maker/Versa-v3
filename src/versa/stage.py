"""The stage director: turns what the student asked into a live performance
for the app's slime character (app/lib/stage/), acted out WHILE the chat
answer is being written.

Division of labour (the user's own framing): the chat carries the long
explanation and anything worth reading; the slime acts out what the topic
IS -- short lines, props, motion, jokes -- and asks the ambiguity options
(those come from the chat's own options turn, never from here, so this
node never emits an `ask`).

One fast-tier call per answered turn, streamed. The prompt asks for JSON
Lines -- one stage action per line -- so each action can be forwarded the
moment its line is complete and the slime starts moving within about a
second of the first token, rather than waiting for a whole script. The
vocabulary is the app's `script.dart` one; every action is validated and
clamped here (`sanitize_action`) before it goes anywhere, so a model that
drifts can produce a dull skit but never a broken stage.

Routed through `SessionLoop._call_node` like every other node (CLAUDE.md
invariant 2): `node_calls` records the student's message in and the full
validated script out, so a performance is as auditable as an answer.
Streaming reaches the caller through the `stage_sink` ContextVar, for the
same reasons `streaming.delta_sink` is one (a callback has no business in
`input_json`; one node object serves every session).
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from versa.llm import LLMClient

StageSink = Callable[[dict], Awaitable[None]]

stage_sink: ContextVar[StageSink | None] = ContextVar("versa_stage_sink", default=None)

GROUND_Y = 0.62
MAX_ACTIONS = 40

MOODS = {"neutral", "happy", "excited", "confused", "strain", "surprised", "sad", "proud", "thinking"}
PROP_KINDS = {
    "box", "ball", "arrow", "text", "star", "heart", "cloud",
    "emoji", "circle", "rect", "triangle", "line", "path", "wave",
}
EFFECTS = {"confetti", "sparks", "smoke", "fire", "rain", "snow", "bubbles", "hearts", "zzz", "splash", "stars"}
COLORS = {"accent", "olive", "warn", "yellow", "ink", "blue", "pink", "red", "green"}
MOVE_STYLES = {"hop", "slide", "leap", "fall"}

# Which fields each action may carry. Anything else is dropped.
_FIELDS: dict[str, set[str]] = {
    "spawn": {"id", "kind", "x", "y", "x2", "y2", "label", "size", "color", "w", "h", "points", "amp", "cycles"},
    "move": {"target", "x", "y", "ms", "style"},
    "approach": {"target"},
    "push": {"target", "dx", "ms"},
    "emote": {"mood"},
    "say": {"text", "ms"},
    "look": {"target", "x"},
    "jump": {"times"},
    "shake": {"target", "ms"},
    "remove": {"id"},
    "wait": {"ms"},
    "together": {"actions"},
    "scale": {"target", "to", "ms"},
    "spin": {"target", "turns", "ms"},
    "recolor": {"target", "color"},
    "relabel": {"target", "label"},
    "carry": {"target"},
    "drop": {"target"},
    "throw": {"target", "x", "ms"},
    "effect": {"kind", "x", "y", "ms"},
    "wear": {"label"},
}

# Numeric fields and the range each is clamped to.
_RANGES: dict[str, tuple[float, float]] = {
    "x": (0.03, 0.97),
    "y": (0.04, GROUND_Y),
    "x2": (0.03, 0.97),
    "y2": (0.04, GROUND_Y),
    "size": (0.3, 3.0),
    "w": (0.03, 0.4),
    "h": (0.03, 0.5),
    "amp": (0.005, 0.12),
    "cycles": (0.5, 8),
    "ms": (0, 6000),
    "dx": (-0.6, 0.6),
    "times": (1, 4),
    "to": (0.2, 3.0),
    "turns": (-4, 4),
}

_STRING_LIMITS = {"text": 90, "label": 40, "id": 24, "target": 24}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def sanitize_action(raw: Any, *, depth: int = 0) -> dict | None:
    """One model-written action -> a safe one, or None to drop it.

    Unknown actions, `ask` (the slime's questions come from the chat's own
    options turn) and malformed values are dropped; numbers are clamped to
    the stage; strings are trimmed; enum-like fields fall back to None
    (the app then uses its default) rather than passing through."""
    if not isinstance(raw, dict):
        return None
    do = raw.get("do")
    if do not in _FIELDS:
        return None
    out: dict[str, Any] = {"do": do}
    for key in _FIELDS[do]:
        if key not in raw:
            continue
        value = raw[key]
        if key in _RANGES:
            n = _num(value)
            if n is None:
                continue
            lo, hi = _RANGES[key]
            out[key] = min(hi, max(lo, n))
        elif key in _STRING_LIMITS:
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                text = str(value).strip()[: _STRING_LIMITS[key]]
                if text:
                    out[key] = text
        elif key == "kind":
            allowed = EFFECTS if do == "effect" else PROP_KINDS
            if value in allowed:
                out[key] = value
            elif do == "spawn" and isinstance(value, str) and value.strip():
                # The app shows an unknown kind as its name; keep it short.
                out[key] = value.strip()[:24]
        elif key == "mood":
            if value in MOODS:
                out[key] = value
        elif key == "color":
            if value in COLORS:
                out[key] = value
        elif key == "style":
            if value in MOVE_STYLES:
                out[key] = value
        elif key == "points":
            if isinstance(value, list):
                pts = []
                for pt in value[:12]:
                    if isinstance(pt, list) and len(pt) >= 2:
                        px, py = _num(pt[0]), _num(pt[1])
                        if px is not None and py is not None:
                            pts.append([min(0.97, max(0.03, px)), min(GROUND_Y, max(0.04, py))])
                if pts:
                    out[key] = pts
        elif key == "actions":
            if depth == 0 and isinstance(value, list):
                inner = [a for a in (sanitize_action(v, depth=1) for v in value[:6]) if a is not None]
                if inner:
                    out[key] = inner
    # An action missing what it can't do without is noise.
    if do == "spawn" and "id" not in out:
        return None
    if do in ("approach", "push", "scale", "spin", "recolor", "relabel", "carry", "drop", "throw")             and "target" not in out:
        return None
    if do == "remove" and "id" not in out:
        return None
    if do == "say" and "text" not in out:
        return None
    if do == "together" and "actions" not in out:
        return None
    return out


def parse_script_lines(text: str) -> list[dict]:
    """Every valid action in a whole response: JSON Lines, or (a model
    ignoring the format) one JSON array."""
    actions = [a for a in (_parse_line(line) for line in text.splitlines()) if a is not None]
    if actions:
        return actions[:MAX_ACTIONS]
    try:
        whole = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(whole, list):
        return [a for a in (sanitize_action(v) for v in whole) if a is not None][:MAX_ACTIONS]
    return []


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
    t = t.removesuffix("```")
    return t.strip()


def _parse_line(line: str) -> dict | None:
    s = line.strip().rstrip(",")
    if not s.startswith("{"):
        return None
    try:
        return sanitize_action(json.loads(s))
    except json.JSONDecodeError:
        return None


_VOCABULARY = f"""\
STAGE (coordinates are fractions: x 0 = left .. 1 = right, y 0 = top .. 1 = bottom).
The ground line is at y = {GROUND_Y}; things standing on the ground use y = {GROUND_Y}
(the default). Keep everything inside x 0.05..0.95 and y 0.05..{GROUND_Y}.
The slime starts near x = 0.3 on the ground. "blob" is the slime's own id.

ACTIONS (one JSON object per line):
{{"do":"say","text":"..."}}                    speech bubble, <= 10 words, witty
{{"do":"emote","mood":"M"}}                    M: neutral happy excited confused strain surprised sad proud thinking
{{"do":"spawn","id":"ID","kind":"K", ...}}     make something appear (with a pop)
   K = emoji  + "label":"🍎"  (ANY object: 🌍 ⚡ 🦠 🚗 🏛️ 💧 🧲 ... -- prefer this for real things)
   K = text   + "label":"F = m×a"        (labels, equations, numbers)
   K = arrow | line  + "x2","y2"         (from x,y to x2,y2; optional "label")
   K = wave   + "x2","amp","cycles"      (a moving sine wave from x to x2 at height y)
   K = path   + "points":[[x,y],...]     (a zig-zag / graph line from x,y through the points)
   K = circle | rect | triangle  + optional "w","h" (fractions of stage height), "label"
   K = box | ball | star | heart | cloud  (hand-drawn props)
   optional on any spawn: "x","y","size" (0.3..3), "color" (accent olive yellow ink blue pink red green)
{{"do":"move","target":"blob|ID","x":X,"y":Y,"style":"hop|slide|leap|fall","ms":N}}
{{"do":"approach","target":"ID"}}              slime walks up beside it
{{"do":"push","target":"ID","dx":0.2}}         slime strains and shoves it along the ground
{{"do":"carry","target":"ID"}}  {{"do":"drop","target":"ID"}}  {{"do":"throw","target":"ID","x":X}}
{{"do":"scale","target":"ID","to":1.8}}  {{"do":"spin","target":"ID","turns":1}}
{{"do":"recolor","target":"ID","color":"C"}}  {{"do":"relabel","target":"ID","label":"..."}}
{{"do":"effect","kind":"E","x":X,"y":Y,"ms":N}}   E: confetti sparks smoke fire rain snow bubbles hearts zzz splash stars
   (no x = on the slime; ms > 0 keeps it going that long)
{{"do":"wear","label":"🎓"}}                   a hat on the slime (null label takes it off)
{{"do":"look","target":"ID"}}  {{"do":"jump","times":2}}  {{"do":"shake","target":"blob|ID"}}
{{"do":"remove","id":"ID"}}  {{"do":"wait","ms":600}}
{{"do":"together","actions":[ ...up to 4 actions... ]}}   run at once
"""


def stage_prompt(message: str) -> str:
    return (
        "STAGE:DIRECT\n"
        "You direct a tiny animated stage beside a tutoring chat. The star is a "
        "cute, expressive green slime (think the slime from 'That Time I Got "
        "Reincarnated as a Slime'): funny, a bit dramatic, endearing.\n\n"
        "The chat panel is writing the full explanation right now. YOUR job is "
        "different: make the idea VISIBLE. Act out what the topic is about -- "
        "conjure the objects involved, show the cause and effect, the process, "
        "the before/after -- so that someone watching gets the gist at a "
        "glance. Humor matters: a physical gag, a mishap, a smug pose, a "
        "surprised face. The speech bubbles are short reactions and "
        "one-line takeaways, never the explanation itself.\n\n"
        f"{_VOCABULARY}\n"
        "RULES:\n"
        "- Output 12-24 actions, ONE JSON object per line, nothing else: no "
        "prose, no code fences, no array brackets.\n"
        "- The FIRST line must be something visible immediately (an emote or a "
        "spawn), so the stage reacts the moment the student asks.\n"
        "- Give every spawned thing a short unique id; only target ids you spawned.\n"
        "- Don't clutter: remove things you're done with; at most ~6 props at once.\n"
        "- Timing: leave a 'wait' of 400-900 ms after an important beat so it lands.\n"
        "- End on a clear, happy takeaway beat (a proud emote, a label, a small effect).\n"
        "- Never ask questions (no 'ask' action).\n\n"
        f"The student just asked: {message!r}\n"
    )


class StageDirector:
    """One streamed fast-tier call per answered turn -> a validated stage
    script (see module docstring). Returns the full validated script; if a
    `stage_sink` is installed, each action is also handed to it the moment
    its line is complete."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, message: str) -> list[dict]:
        self.last_call_count = 0
        prompt = stage_prompt(message)
        sink = stage_sink.get()
        stream = getattr(self._llm, "stream", None)
        actions: list[dict] = []

        async def emit(action: dict) -> None:
            actions.append(action)
            if sink is not None:
                await sink(action)

        if sink is None or stream is None:
            text = await self._llm.complete(prompt)
            self.last_call_count += 1
            for action in parse_script_lines(text):
                await emit(action)
            return actions

        buffer = ""
        full: list[str] = []
        async for piece in stream(prompt):
            full.append(piece)
            buffer += piece
            while "\n" in buffer and len(actions) < MAX_ACTIONS:
                line, buffer = buffer.split("\n", 1)
                action = _parse_line(line)
                if action is not None:
                    await emit(action)
        self.last_call_count += 1
        tail = _parse_line(buffer)
        if tail is not None and len(actions) < MAX_ACTIONS:
            await emit(tail)
        if not actions:
            # The model ignored the line format (e.g. one JSON array).
            for action in parse_script_lines("".join(full)):
                await emit(action)
        return actions
