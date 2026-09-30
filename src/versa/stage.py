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
import logging
import math
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from versa.llm import LLMClient

logger = logging.getLogger(__name__)

StageSink = Callable[[dict], Awaitable[None]]

stage_sink: ContextVar[StageSink | None] = ContextVar("versa_stage_sink", default=None)

GROUND_Y = 0.62
MAX_ACTIONS = 40

MOODS = {"neutral", "happy", "excited", "confused", "strain", "surprised", "sad", "proud", "thinking"}
PROP_KINDS = {
    "box", "ball", "arrow", "text", "star", "heart", "cloud",
    "emoji", "circle", "rect", "triangle", "line", "path", "wave",
    # the graph kit and typeset formulas (2026-09-27: "real visuals like graphs")
    "axes", "plot", "dot", "tangent", "math",
    # a flowing connection between two things (2026-09-28: "more dynamicity")
    "link",
    # instruments: things that SHOW a quantity changing, animated by the app
    # (2026-09-28: "it speeds up one thing but there is no reference of time")
    "clock", "stopwatch", "counter", "gauge", "bar", "thermometer",
    # 3D solids, placed in the stage's world with depth (2026-09-27: "make the
    # space around it 3d and new objects to spawn")
    "cube", "sphere", "cylinder", "cone", "pyramid", "prism", "torus", "planet", "atom",
}
GRAPH_KINDS = {"axes", "plot", "dot", "tangent"}

# What a plot's formula may contain -- the app's formula reader accepts the
# same, and refuses anything else: numbers, x, + - * / ^, parentheses and
# these names.
_FORMULA_NAMES = {"x", "pi", "e", "sin", "cos", "tan", "exp", "log", "ln", "sqrt", "abs"}
_FORMULA_CHARS = re.compile(r"^[0-9a-z+\-*/^(). ]{1,80}$")


def valid_formula(fn: str) -> bool:
    s = fn.strip().lower()
    if not _FORMULA_CHARS.match(s) or s.count("(") != s.count(")"):
        return False
    for word in re.findall(r"[a-z]+", s):
        rest = word
        while rest:  # a run like "sinx" or "2pix" is names written together
            head = next((n for n in sorted(_FORMULA_NAMES, key=len, reverse=True) if rest.startswith(n)), None)
            if head is None:
                return False
            rest = rest[len(head):]
    return True
EFFECTS = {"confetti", "sparks", "smoke", "fire", "rain", "snow", "bubbles", "hearts", "zzz", "splash", "stars"}
COLORS = {"accent", "olive", "warn", "yellow", "ink", "blue", "pink", "red", "green"}
MOVE_STYLES = {"hop", "slide", "leap", "fall"}

# Which fields each action may carry. Anything else is dropped.
_FIELDS: dict[str, set[str]] = {
    "spawn": {"id", "kind", "x", "y", "x2", "y2", "label", "caption", "size", "color", "w", "h", "points", "amp",
              "cycles", "to", "rate", "value", "track",
              "fn", "on", "at", "xmin", "xmax", "ymin", "ymax", "tex",
              "z", "lift", "spin", "yaw", "pitch"},
    # the 3D world: switch it on or off; turn a solid; send one around another
    "world": {"mode"},
    "turn": {"target", "yaw", "pitch", "ms"},
    "orbit": {"target", "around", "radius", "turns", "ms"},
    # move a graph dot along its curve to x = at
    "slide": {"target", "at", "ms"},
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
    # the story moves to its next idea: what was on stage steps back into the
    # room, dimmed but still there (2026-09-28: "smooth ... acknowledgement
    # and moving on")
    "scene": set(),
    # an instrument's reading or rate eases to a new value
    "set": {"target", "value", "rate", "ms"},
    # a thing travels at a steady speed (0..0.99 of the fastest) for ms, in place
    "cruise": {"target", "speed", "ms"},
    # two lanes, A on the left and B on the right, for a side-by-side
    # comparison; with neither label it goes away
    "compare": {"left", "right"},
    # the first line: what each thing stands for and how the idea is seen
    "plan": {"cast", "shows"},
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
    # A takeaway worth keeping: pinned in "Keep in mind" below the stage,
    # where it stays after the skit moves on.
    "note": {"text"},
    # One quick check with 2-3 answers; "then" is the slime's reaction to
    # each (kept apart from the chat's own ambiguity options, which take
    # the stage over while they are open).
    "ask": {"question", "choices", "then", "answer"},
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
    "at": (-1e4, 1e4),
    "z": (0, 1),
    "lift": (0, 0.5),
    "spin": (-2, 2),
    "yaw": (-4, 4),
    "pitch": (-4, 4),
    "radius": (0.03, 0.4),
    "rate": (0, 5),
    "value": (0, 1),
    "speed": (0, 0.99),
    "xmin": (-1e4, 1e4),
    "xmax": (-1e4, 1e4),
    "ymin": (-1e4, 1e4),
    "ymax": (-1e4, 1e4),
}

_STRING_LIMITS = {"text": 90, "label": 40, "caption": 40, "to": 24, "track": 24, "left": 30, "right": 30,
                  "shows": 140, "id": 24, "target": 24, "question": 90, "on": 24, "tex": 120, "fn": 80,
                  "around": 24}
NOTE_CHARS = 120
MAX_NOTES = 4


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


_JSON_ESCAPES_IN_LATEX = {"\f": "\\f", "\t": "\\t", "\b": "\\b", "\r": "\\r", "\n": "\\n"}


def repair_tex(tex: str) -> str:
    """A model that writes "\\frac" in JSON with ONE backslash sends a form
    feed and "rac" (likewise \\theta/\\text -> tab, \\beta -> backspace,
    \\neq -> newline, \\rho -> return). Put the backslash back."""
    for char, escaped in _JSON_ESCAPES_IN_LATEX.items():
        tex = tex.replace(char, escaped)
    return tex


# how a spawned thing arrives (the app picks one per thing when not given)
ENTRANCES = {"pop", "drop", "rise", "swoop"}


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
    if isinstance(raw.get("tex"), str):
        raw = {**raw, "tex": repair_tex(raw["tex"])}
    out: dict[str, Any] = {"do": do}
    for key in _FIELDS[do]:
        if key not in raw:
            continue
        value = raw[key]
        if key in _RANGES and not (do == "spawn" and key == "to"):  # a link's "to" is an id
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
                glyph = value.strip()
                if not any(c.isascii() and c.isalnum() for c in glyph) and len(glyph) <= 8:
                    # 2026-09-28: the model wrote the emoji itself as the kind
                    # ("kind":"⌛","label":"t = 12:00") -- it meant an emoji
                    # with a caption
                    out[key] = "emoji"
                    caption = raw.get("caption") or raw.get("label")
                    raw = {**raw, "label": glyph, "caption": caption}
                    if isinstance(caption, str) and caption.strip():
                        out["caption"] = caption.strip()[:40]
                    out["label"] = glyph
                else:
                    # The app shows an unknown kind as its name; keep it short.
                    out[key] = glyph[:24]
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
        elif key == "choices":
            if isinstance(value, list):
                choices, seen = [], set()
                for c in value[:8]:
                    if not isinstance(c, dict):
                        continue
                    cid = str(c.get("id", "")).strip()[:24]
                    text = str(c.get("text", "")).strip()[:60]
                    if cid and text and cid not in seen:
                        seen.add(cid)
                        choices.append({"id": cid, "text": text})
                    if len(choices) == 3:
                        break
                if len(choices) >= 2:
                    out[key] = choices
        elif key == "then":
            # the reaction to each answer: a few plain actions, no nesting
            if depth == 0 and isinstance(value, dict):
                branches = {}
                for cid, acts in list(value.items())[:3]:
                    if isinstance(acts, list):
                        branch = [a for a in (sanitize_action(v, depth=1) for v in acts[:6])
                                  if a is not None and a["do"] not in ("ask", "note")]
                        branches[str(cid)[:24]] = branch
                out[key] = branches
        elif key == "cast":
            if depth == 0 and isinstance(value, dict):
                cast = {}
                for cid, name in list(value.items())[:10]:
                    if isinstance(name, str) and name.strip():
                        cast[str(cid).strip()[:24]] = name.strip()[:30]
                if cast:
                    out[key] = cast
        elif key == "actions":
            if depth == 0 and isinstance(value, list):
                inner = [a for a in (sanitize_action(v, depth=1) for v in value[:6]) if a is not None]
                if inner:
                    out[key] = inner
    # An action missing what it can't do without is noise.
    if do == "spawn" and "id" not in out:
        return None
    if do == "spawn" and raw.get("enter") in ENTRANCES:
        out["enter"] = raw["enter"]
    if do == "spawn" and out.get("kind") == "link" and (
        "on" not in out or "to" not in out or out["on"] == out["to"]
    ):
        return None
    if do == "spawn" and out.get("kind") in GRAPH_KINDS | {"math"}:
        kind = out["kind"]
        if kind == "axes":
            # a graph needs room: wider limits than a prop's
            for key, hi in (("w", 0.85), ("h", 0.58)):
                v = _num(raw.get(key))
                if v is not None:
                    out[key] = min(hi, max(0.15, v))
            if out.get("xmin", -5) >= out.get("xmax", 5) or out.get("ymin", -5) >= out.get("ymax", 5):
                return None
        elif kind == "plot":
            if "on" not in out or not valid_formula(out.get("fn", "")):
                return None
        elif kind in ("dot", "tangent"):
            if "on" not in out:
                return None
        elif kind == "math" and "tex" not in out:
            return None
    if do == "slide" and ("target" not in out or "at" not in out):
        return None
    if do in ("set", "cruise") and "target" not in out:
        return None
    if do == "set" and "value" not in out and "rate" not in out:
        return None
    if do == "plan" and (depth > 0 or "cast" not in out):
        return None
    if do == "world":
        mode = raw.get("mode")
        if mode not in ("3d", "flat"):
            return None
        out["mode"] = mode
    if do == "turn" and "target" not in out:
        return None
    if do == "orbit" and ("target" not in out or "around" not in out or out["target"] == out["around"]):
        return None
    if do in ("approach", "push", "scale", "spin", "recolor", "relabel", "carry", "drop", "throw")             and "target" not in out:
        return None
    if do == "remove" and "id" not in out:
        return None
    if do == "say" and "text" not in out:
        return None
    if do == "together" and "actions" not in out:
        return None
    if do == "note":
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        out["text"] = " ".join(text.split())[:NOTE_CHARS]
    if do == "ask":
        # a question only inside a skit, never nested, and answerable
        if depth > 0 or "question" not in out or "choices" not in out:
            return None
        ids = {c["id"] for c in out["choices"]}
        out["then"] = {k: v for k, v in out.get("then", {}).items() if k in ids}
        # which choice is right, so the app can show it after a wrong pick
        answer = raw.get("answer")
        if answer is not None and str(answer) in ids:
            out["answer"] = str(answer)
        else:
            out.pop("answer", None)
    return out


INSTRUMENTS = {"clock", "stopwatch", "counter", "gauge", "bar", "thermometer"}
_QUANTITY_WORDS = re.compile(
    r"\b(time|clock|speed|fast|slow|temperature|heat|hot|cold|pressure|mass|weight|distance|energy|"
    r"count|money|price|rate|level|amount|growth|population)\b", re.IGNORECASE)


def check_script(actions: list[dict]) -> list[str]:
    """What a script promises but doesn't show -- logged, never blocking (the
    script is already on the student's screen by the time it is complete).
    A plan that casts things it never makes; a changing quantity with no
    instrument or graph to see it by; a label attached to nothing."""
    problems: list[str] = []
    plan = next((a for a in actions if a["do"] == "plan"), None)
    spawned = {a["id"] for a in actions if a["do"] == "spawn"}
    kinds = {a.get("kind") for a in actions if a["do"] == "spawn"}
    if plan is None:
        if any(a["do"] == "spawn" for a in actions):
            problems.append("no plan line")
    else:
        missing = [cid for cid in plan.get("cast", {}) if cid not in spawned]
        if missing:
            problems.append(f"plan casts {missing} but never spawns them")
        if _QUANTITY_WORDS.search(plan.get("shows", "")) and not (kinds & (INSTRUMENTS | GRAPH_KINDS)):
            problems.append(f"shows a changing quantity with nothing measuring it: {plan.get('shows')!r}")
    for a in actions:
        if a["do"] == "spawn" and a.get("on") and a.get("kind") in ("text", "math") and a["on"] not in spawned:
            problems.append(f"{a['id']!r} is attached to {a['on']!r}, which never appears")
    return problems


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
The stage is a 3D ROOM: a floor running back to a horizon. The front row of the floor is at
y = {GROUND_Y}; things standing on it use y = {GROUND_Y} (the default). Keep everything inside
x 0.05..0.95 and y 0.05..{GROUND_Y}. ANY spawn may take "z" (0 front .. 1 far): it stands that deep
in the room, smaller and nearer the horizon -- build SCENES with depth (things behind things, a row
receding into the distance), not a flat row. Graphs and formulas stay at the front (z 0).
The slime starts near x = 0.3 at the front. "blob" is the slime's own id; it hops out of the way
of anything that appears on top of it, but plan the scene so it doesn't have to.

ACTIONS (one JSON object per line):
{{"do":"say","text":"..."}}                    speech bubble, <= 10 words, witty
{{"do":"emote","mood":"M"}}                    M: neutral happy excited confused strain surprised sad proud thinking
{{"do":"spawn","id":"ID","kind":"K", ...}}     make something appear (with a pop)
   K = emoji  + "label":"🍎"  (ANY object: 🌍 ⚡ 🦠 🚗 🏛️ 💧 🧲 ... -- prefer this for real things)
               + optional "caption":"t = 0"  words under it (relabel changes the caption)
   K = text   + "label":"F = m×a"        (labels, equations, numbers)
   K = arrow | line  + "x2","y2"         (from x,y to x2,y2; optional "label")
   K = wave   + "x2","amp","cycles"      (a moving sine wave from x to x2 at height y)
   K = path   + "points":[[x,y],...]     (a zig-zag / graph line from x,y through the points)
   K = circle | rect | triangle  + optional "w","h" (fractions of stage height), "label"
   K = box | ball | star | heart | cloud  (hand-drawn props)
   K = link + "on":"A","to":"B"  a FLOWING connection from A to B that follows them as they move (optional
               "label", "color") -- heat, force, a signal, money, cause -> effect. Remove it when the flow stops.
INSTRUMENTS (they SHOW a quantity changing -- use one whenever the idea is about time, speed, amount,
   temperature, a level, a count; never leave a quantity as a bare letter):
   K = clock | stopwatch  "rate":1 (ticks per second; a slower clock ticks slower), shows its elapsed time
   K = counter  "rate":N (counts up N per second; 0 = still), "label":"km" its unit
   K = gauge | bar | thermometer  "value":0..1 (needle / fill level), "label":"speed" what it measures
   + "track":"ID" binds it to a moving thing: a clock riding with it SLOWS as it goes faster (relativity), a
     gauge/bar/thermometer READS its speed, a counter adds up the distance it travels.
{{"do":"set","target":"ID","value":0.8,"rate":0.5,"ms":800}}     an instrument's reading / rate eases to new values
{{"do":"cruise","target":"ID","speed":0.9,"ms":4000}}   it travels at that speed (0..0.99 of the fastest) in place --
   speed lines stream past it -- while anything tracking it responds
{{"do":"compare","left":"At home","right":"On the ship"}}   two lanes to compare side by side: put the left things
   at x ~0.25 and the right ones at x ~0.75, the SAME kinds of thing in each, and change only ONE difference.
   {{"do":"compare"}} removes the lanes.
LABELS ON THINGS: a text or math spawn with "on":"ID" sits on that thing and moves with it -- put every symbol
   (v, t, m, F) ON the thing it names, never floating on its own.
   optional "enter": pop | drop | rise | swoop  how it arrives (drop = falls in and bounces, rise = grows up
               out of the floor, swoop = flies in from the side); vary it.
   K = math  + "tex":"\\\\frac{{dy}}{{dx}} = 2x"   a TYPESET formula (LaTeX): fractions, powers, roots,
               Greek letters, \\\\int, \\\\sum, \\\\lim, subscripts. "x","y" = its centre, "size" scales it.
               In JSON every LaTeX backslash is written twice: "\\\\frac", "\\\\theta", "\\\\int".
GRAPH KIT (for anything mathematical or quantitative -- plot it, don't draw a picture of it):
{{"do":"spawn","id":"ax","kind":"axes","x":0.4,"y":0.58,"w":0.52,"h":0.5,"xmin":-1,"xmax":4,"ymin":-1,"ymax":10}}
   axes: "x","y" = bottom-left corner; "w" = fraction of stage WIDTH, "h" of height; the number ranges.
   Keep the slime clear of it (e.g. axes on the right, slime at x 0.2) and ranges that fit the curve.
{{"do":"spawn","id":"f","kind":"plot","on":"ax","fn":"x^2","tex":"y = x^2","color":"accent"}}
   a curve that draws itself. "fn" uses only x, numbers, + - * / ^, ( ), sin cos tan exp log ln sqrt abs pi e.
{{"do":"spawn","id":"p","kind":"dot","on":"f","at":1,"tex":"P"}}          a point on the curve at x = at
{{"do":"spawn","id":"t","kind":"tangent","on":"p","tex":"\\\\text{{slope}} = {{slope}}"}}
   the tangent at the point; it follows the point, and {{slope}} in its tex shows the live slope
{{"do":"slide","target":"p","at":3,"ms":1500}}                          move the point along its curve
   Removing the axes removes everything on them.
3D SOLIDS (real lit shapes -- use them freely: blocks for bits and bytes, towers for sizes,
   spheres for particles and planets, cylinders for pipes and batteries, stacks for layers):
{{"do":"spawn","id":"S","kind":"cube","x":0.6,"z":0.3,"lift":0.1,"spin":0.2}}
   K = cube | sphere | cylinder | cone | pyramid | prism | torus | planet (with rings) | atom (electrons orbit)
   "z" = depth into the stage (0 front .. 1 far: smaller, nearer the horizon), "lift" = height above the
   floor (0..0.5), "spin" = steady turns per second, "size", "color". Real lit 3D -- they shade as they turn.
{{"do":"turn","target":"S","yaw":1,"pitch":0.25,"ms":1500}}          rotate it (in full turns)
{{"do":"orbit","target":"moon","around":"earth","radius":0.2,"turns":1,"ms":4000}}
   circle one solid around another; it passes behind and in front
   {{"do":"world","mode":"flat"}} flattens the room to a plain board (rarely needed); "3d" brings it back.
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
{{"do":"scene"}}   move on to the next idea: everything on stage glides back into the room and dims (still
   visible, so the link stays); anything you use again (move, relabel, scale...) comes forward again
{{"do":"together","actions":[ ...up to 4 actions... ]}}   run at once
{{"do":"note","text":"..."}}                  pin a takeaway to keep in mind (<= 20 words, a full sentence)
{{"do":"ask","question":"...","choices":[{{"id":"a","text":"..."}},{{"id":"b","text":"..."}}],"answer":"a",
  "then":{{"a":[ ...reaction... ],"b":[ ...reaction... ]}}}}   a quick check: "answer" is the right id; the slime reacts to the pick
"""


def stage_prompt(
    message: str, answer: str = "", previous_answer: str = "", continues: str = "", opening: str = "",
    live: bool = False,
) -> str:
    """One continuous animation that explains the whole thing.

    `opening`: the chat is writing its answer right now and this is how it
    begins -- the stage starts at once (not after the answer), acting out the
    explanation the answer is setting out to give, consistent with it.
    `answer`: the complete answer, when it is already there.
    `live`: the stage starts the moment the student sends (2026-09-27: "make
    them load in like 2-3 sec"), alongside the answer, so it has the question
    and the conversation so far -- the previous answer, and for a fork
    continuation the direction tapped -- but not the new answer.
    With none of these (older callers), the question alone."""
    if answer or opening or live:
        carry_on = ""
        if continues:
            carry_on = (
                f"\nThis answer CONTINUES the previous one: the student tapped {continues!r} "
                "to take it further. Keep the same story and characters going -- it is the "
                "next scene, not a new show.\n"
                f"The previous answer, for context:\n<<<{previous_answer[:1500]}>>>\n"
            )
        if live:
            earlier = ""
            if previous_answer and not continues:
                earlier = (
                    "\nEarlier in this chat (continuity only -- if the question follows on "
                    "from it, carry that story on):\n"
                    f"<<<{previous_answer[:1200]}>>>\n"
                )
            source = (
                "The chat is answering the student RIGHT NOW and you start at the same moment, so "
                "you haven't seen its answer: act out the explanation this question calls for -- "
                "the standard, correct one -- as ONE continuous scene from start to finish.\n"
                f"{carry_on}{earlier}"
                f"\nThe student asked: {message!r}\n"
            )
        elif answer:
            source = (
                "The chat shows the student the answer below. Act out THIS answer -- the same "
                "ideas, in the same order, nothing it doesn't say -- so the stage and the chat "
                "tell one story.\n"
                f"{carry_on}"
                f"\nThe student asked: {message!r}\n"
                f"The answer:\n<<<{answer[:3000]}>>>\n"
            )
        else:
            source = (
                "The chat is writing its answer to the student RIGHT NOW; below is how it "
                "begins. Act out the whole explanation it is setting out to give -- the same "
                "ideas, in the same direction, consistent with how it begins -- as ONE "
                "continuous scene from start to finish.\n"
                f"{carry_on}"
                f"\nThe student asked: {message!r}\n"
                f"The answer begins:\n<<<{opening[:1500]}>>>\n"
            )
        task = source
        rules_extra = (
            # 2026-09-28: relativity came out as disconnected pictures -- a fast
            # thing and a still one the slime ignored, then a formula, then a
            # bare claim and a quiz. Tell it as one story.
            "- TELL ONE STORY, not a slideshow. Pick 2-4 objects that stand for the idea "
            "(e.g. for relativity: a rocket with a clock, and a clock left at home) and keep "
            "them on stage the whole time -- move, relabel, recolor and scale THE SAME ids "
            "to show what changes, instead of spawning new unrelated things.\n"
            "- Every beat runs: SET UP -> SHOW -> REACT -> LINK. Introduce each new thing "
            "(the slime looks at it or walks to it, and a say names it and ties it to what is "
            "already there); when something changes, the slime WATCHES it (look) and reacts "
            "(emote + a say about what just happened -- AFTER it happened, never before); "
            "then one say LEADS INTO the next beat "
            "(\"But what about its clock?\"). Never jump to the next thing unacknowledged.\n"
            "- Show first, then say it. Never state a conclusion before the scene has shown "
            "it. A formula comes AFTER the picture, as the summary of what was just seen, and "
            "its symbols are put on the objects they mean (label v on the moving thing, t on "
            "the clock) so the maths and the picture are visibly the same thing.\n"
            "- Keep it ALIVE but easy to follow: something is always gently in motion -- things "
            "arrive (vary \"enter\"), move, spin, orbit, flow along a link -- ONE change at a time, "
            "each watched. Generate new things when the story needs them, arriving next to what "
            "they relate to, often linked to it.\n"
            "- When something CHANGES, show it against something that doesn't: use compare (two lanes, "
            "one difference) -- a fast clock only means something next to a normal one.\n"
            "- One idea shown well beats three stated: at most TWO ideas (one scene change), "
            "the second growing out of the first.\n"
            "- Moving to a new idea: use {\"do\":\"scene\"} so the last one steps back "
            "instead of vanishing, and open the new beat with a say that connects it to the "
            "last (\"So if moving slows time...\").\n"
            "- Nothing new after the last note: no extra formula or object tacked on at the "
            "end -- only a one-line wrap-up and the ask. Open with the scene, not a claim.\n"
            "- The ask comes last, after a one-line wrap-up, and is about what the scene "
            "showed -- never a surprise.\n"
            # 2026-09-27: "hi" got a full show with notes and a quiz. Judge first.
            "- For maths and anything quantitative, SHOW THE REAL THING: plot it with the graph "
            "kit and write formulas as typeset math (kind math), never as emoji or plain text. "
            "Emoji are for real-world objects only.\n"
            "- FIRST judge whether this exchange explains anything worth making visible. If it "
            "doesn't -- a greeting, small talk, thanks, a question about you or the app, a "
            "one-line reply, the answer asking the student what they want -- output ONLY 1-3 "
            "actions: a small friendly reaction (an emote, a jump, at most one short say). No "
            "props, no notes, no ask. Everything below applies only to a real explanation.\n"
            f"- Pin 2-{MAX_NOTES} notes: the points the student must keep in mind, each a short "
            "full sentence taken from the explanation itself (never generic facts, never "
            "the answer's own questions). Put each right after the beat that SHOWED it, "
            "never before.\n"
            "- You MAY end with ONE ask: a quick check on the main idea with 2-3 short "
            "answers, exactly one right, named in \"answer\"; in \"then\", react to each "
            "(right: proud + a small effect; wrong: a kind one-line correction that ends "
            "happy -- the app shows the right answer first).\n"
        )
        count = "18-34"
    else:
        task = f"The student just asked: {message!r}\n"
        rules_extra = "- Never ask questions (no 'ask' action).\n"
        count = "12-24"
    return (
        "STAGE:DIRECT\n"
        "You direct a tiny animated stage beside a tutoring chat. The star is a "
        "cute, expressive green slime (think the slime from 'That Time I Got "
        "Reincarnated as a Slime'): funny, a bit dramatic, endearing.\n\n"
        "Make the idea VISIBLE: conjure the objects involved, show the cause and "
        "effect, the process, the before/after -- so someone watching gets it at "
        "a glance. Humor matters: a physical gag, a mishap, a smug pose, a "
        "surprised face. Speech bubbles are short reactions, never the "
        "explanation itself.\n\n"
        f"{_VOCABULARY}\n"
        "RULES:\n"
        f"- Output {count} actions, ONE JSON object per line, nothing else: no "
        "prose, no code fences, no array brackets.\n"
        "- The FIRST line is the PLAN: {\"do\":\"plan\",\"cast\":{\"ship\":\"the spaceship\","
        "\"home\":\"the clock at home\"},\"shows\":\"time: the ship's clock ticks slower than home's\"} "
        "-- every thing you will spawn, BY THE EXACT ID you will spawn it with, and what it stands for, and "
        "how the key idea will be SEEN (which "
        "instrument, graph or motion shows it). Then play exactly that plan. The SECOND line must be "
        "something visible (an emote or a spawn).\n"
        "- Real things look like themselves: an emoji for a ship, a person, the Earth, a cup; an instrument "
        "for a clock or a reading. Plain shapes (cube, cylinder, sphere...) only for geometry and abstract "
        "blocks (bits, layers, particles).\n"
        "- Give every spawned thing a short unique id; only target ids you spawned.\n"
        "- Don't clutter: remove things you're done with; at most ~6 props at once.\n"
        "- Timing: leave a 'wait' of 400-900 ms after an important beat so it lands.\n"
        "- End on a clear, happy takeaway beat (a proud emote, a label, a small effect).\n"
        f"{rules_extra}\n"
        f"{task}"
    )


class StageDirector:
    """One streamed fast-tier call per answered turn -> a validated stage
    script (see module docstring). Returns the full validated script; if a
    `stage_sink` is installed, each action is also handed to it the moment
    its line is complete."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0
        self.last_problems: list[str] = []

    def _checked(self, actions: list[dict]) -> list[dict]:
        self.last_problems = check_script(actions)
        if self.last_problems:
            logger.warning("stage script: %s", "; ".join(self.last_problems))
        return actions

    async def run(
        self, message: str, answer: str = "", previous_answer: str = "", continues: str = "",
        opening: str = "", live: bool = False,
    ) -> list[dict]:
        self.last_call_count = 0
        prompt = stage_prompt(message, answer, previous_answer, continues, opening, live)
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
            return self._checked(actions)

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
        return self._checked(actions)


class StageCheckStore:
    """The stage's quick-check picks (migration 080). Append-only (CLAUDE.md
    invariant 15): insert and read only, no delete/remove/update method and
    no DELETE or UPDATE SQL. Walled off: nothing here reaches claims or
    thinking styles."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self, *, session_id: UUID, turn_index: int, question: str, choices: list[dict],
        picked_id: str, answer_id: str | None,
    ) -> UUID:
        cid = uuid4()
        correct = None if answer_id is None else picked_id == answer_id
        await self._pool.execute(
            "INSERT INTO stage_checks (id, session_id, turn_index, question, choices, picked_id, "
            "answer_id, correct) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            cid, session_id, turn_index, question[:200], choices, picked_id[:24],
            answer_id[:24] if answer_id else None, correct,
        )
        return cid

    async def list_for_session(self, session_id: UUID) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT * FROM stage_checks WHERE session_id = $1 ORDER BY created_at", session_id,
        )
        return [dict(r) for r in rows]


# ------------------------------------------------------- a lesson task, set on stage

TASK_FACTS = 3


def task_stage_prompt(lesson_context: str, task_kind: str, task_description: str, next_lesson: str) -> str:
    """A lesson's current task, SET by the stage instead of listed: the slime
    acts out a short scene that leads into a challenge, and the learner does
    it by answering on the stage. Plus a few true facts about the next lesson
    for the slime to tell while the learner is idle."""
    upcoming = (
        f"The NEXT lesson is {next_lesson!r}. Write {TASK_FACTS} short, true, surprising facts that make "
        "someone curious about it (one sentence each, under 110 characters, no questions).\n"
        if next_lesson else "There is no next lesson: facts may be about this lesson's idea instead.\n"
    )
    return (
        "STAGE:TASK\n"
        "You direct a tiny animated stage beside a lesson. The star is a cute, expressive green slime: "
        "funny, a bit dramatic, endearing.\n\n"
        f"{_VOCABULARY}\n"
        f"{lesson_context}\n"
        f"The student's CURRENT task ({task_kind}): {task_description}\n\n"
        "Turn this task into something the student DOES on the stage, instead of reading it as an "
        "instruction. Act out a short scene (8-16 actions) that sets up the situation the task is about "
        "-- the slime runs into a problem it needs help with -- then end with ONE ask: the challenge "
        "itself, phrased as the slime asking the student for help, with 2-4 short choices and exactly "
        "one right (named in \"answer\"). Answering it right must show the student did what the task "
        "asks (understood it, applied it, or worked it out). In \"then\", react to each choice (right: "
        "proud + a small effect; wrong: a kind one-line hint that ends happy).\n"
        "- Show first, then ask: the scene must give everything needed to answer, and the ask comes "
        "last. Keep 2-4 things on stage, the same ids throughout.\n"
        "- For maths, show the real thing (graph kit, typeset math), never emoji for numbers.\n"
        f"{upcoming}\n"
        'Respond with ONE JSON object: {"script": [ ...stage actions, the ask last... ], '
        '"facts": ["...", "..."]}'
    )


def parse_task_activity(raw: str) -> tuple[list[dict], dict, list[str]] | None:
    """The model's reply -> (the script, its final ask, the idle facts), or
    None when there is no answerable ask at the end (then there is no task to
    do on stage, and the lesson falls back to the chat)."""
    text = _strip_fences(raw or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("script"), list):
        return None
    actions = [a for a in (sanitize_action(r) for r in data["script"][:MAX_ACTIONS]) if a is not None]
    asks = [a for a in actions if a["do"] == "ask"]
    if not asks or "answer" not in asks[-1]:
        return None
    ask = asks[-1]
    # only the last ask counts, and nothing after it
    actions = [a for a in actions[:actions.index(ask)] if a["do"] != "ask"] + [ask]
    facts = [" ".join(str(f).split())[:140] for f in (data.get("facts") or []) if isinstance(f, str) and f.strip()]
    return actions, ask, facts[:TASK_FACTS + 2]
