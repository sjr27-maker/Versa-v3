"""Where this could go: after every answer, a standard set of directions the
learner could take next (IDEAS.md "A space of possibilities", 2026-09-27).

Why. A learner often can't say what they want -- they may not know yet. So
instead of asking, Versa shows the space: six short cards under the answer,
one per SLOT of a fixed skeleton (see the intuition, work an example, why
it works, where it's used, go deeper, what comes next). Recognising what you
want beats describing it, and a card can spark a want that wasn't there.

Evidence. What a learner picks, and above all the ORDER they explore in
(intuition -> example -> why ...), is how they think, shown rather than
stated. So:
  - the skeleton is the same for everyone, so picks compare across people
    and across sessions; only the card wording is generated;
  - the generator is deliberately given NO thinking style, claims or
    history -- a set shaped by what we already believe about someone would
    turn their pick into an echo of our own guess (the circularity risk in
    IDEAS.md). The depth and breadth sliders are the only personal input:
    the learner set them, and they bound the window the cards are pitched in;
  - card positions are shuffled for every set and stored, so a preference
    can be told apart from tapping whatever came first;
  - every set, card, pick and pass (with how long it took) is kept, and
    `session_path` hands the ordered picks to session-end consolidation.

Append-only (CLAUDE.md invariant 14): a set is written once with its cards;
what happened to it is a single event row. The model call goes through
`SessionLoop._call_node`, so it is in node_calls (invariant 2).
"""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa.llm import LLMClient
from versa.session_knobs import SessionKnobs

Slot = Literal["intuition", "example", "why", "use", "deeper", "next"]
# How a set is shown: cards under the answer, or inline links the answer ends
# with, where a pick continues the same explanation (migration 079).
Presentation = Literal["strip", "fork"]

# The standard skeleton, in canonical order (display order is shuffled).
SLOTS: dict[str, str] = {
    "intuition": "see it simply -- an everyday picture or analogy of the idea",
    "example": "work through one concrete example",
    "why": "why it works -- the reason or mechanism underneath",
    "use": "where it is used -- a real application",
    "deeper": "go further -- a harder or more rigorous version",
    "next": "what comes next -- the related idea it leads to",
}
MAX_CARD_CHARS = 70


class DirectionCard(BaseModel):
    id: UUID
    slot: Slot
    position: int
    text: str


class DirectionSet(BaseModel):
    id: UUID
    session_id: UUID
    turn_index: int
    depth_level: int
    breadth_level: int
    presentation: Presentation = "strip"
    created_at: datetime
    cards: list[DirectionCard]


class PathStep(BaseModel):
    """One step of a learner's order of approach within a session."""
    turn_index: int
    kind: Literal["picked", "passed"]
    slot: Slot | None
    position: int | None
    elapsed_ms: int


class AlreadySettled(Exception):
    """The set was already picked from or passed."""


# ------------------------------------------------------------------ node


def _window_line(knobs: SessionKnobs) -> str:
    """The learner's own sliders as hard limits on the cards. A live check
    (2026-09-27) showed a soft "keep them near the basics" barely moved the
    cards, so each end now says what it means for the slots it bounds."""

    def band(level: int, low: str, mid: str, high: str) -> str:
        return low if level < 35 else high if level > 65 else mid

    depth = band(
        knobs.depth,
        "LOW: every card stays beginner-level and intuitive -- even 'go further' is one small "
        "step up, with no formal definitions, proofs or notation",
        "MIDDLE: a typical student's level; 'go further' may add some precision",
        "HIGH: pitch cards at an advanced level -- 'why it works' and 'go further' should reach "
        "the formal definition, proof or underlying mechanism",
    )
    breadth = band(
        knobs.breadth,
        "LOW: 'where it is used' and 'what comes next' stay INSIDE this same subject -- a use "
        "within the subject itself and the very next idea in the same chapter, never another field",
        "MIDDLE: 'where it is used' may be a familiar real-world use; 'what comes next' stays "
        "in the same subject",
        "HIGH: 'where it is used' and 'what comes next' should cross into other subjects and "
        "the wider world",
    )
    return (
        "The learner set these limits themselves -- follow them strictly:\n"
        f"- depth {knobs.depth}/100, {depth}\n"
        f"- breadth {knobs.breadth}/100, {breadth}\n"
    )


def directions_prompt(message: str, answer: str, knobs: SessionKnobs) -> str:
    slots = "".join(f"- {slot}: {desc}\n" for slot, desc in SLOTS.items())
    return (
        "DIRECTIONS:SUGGEST\n"
        "A learner just got an answer. Offer the directions they could take next, "
        "one card per slot below, so they can recognise what they want instead of "
        "having to put it into words.\n"
        f"Their message: {message}\n"
        f"The answer they got (may be cut off): <<<{answer[:1500]}>>>\n"
        f"{_window_line(knobs)}"
        f"\nSlots:\n{slots}"
        "\nFor EVERY slot write one card: what the learner would tap to go there, "
        "in their voice, specific to THIS topic (e.g. \"Show me with a speedometer\", "
        f"\"Work one out: x^3\"), at most {MAX_CARD_CHARS} characters, no numbering, "
        "no question marks. Different slots must lead to genuinely different places.\n"
        # 2026-09-27: "hi" got six cards about oxygen and nerve impulses.
        "\nBut FIRST judge whether the answer explains anything there is somewhere to go "
        "from. If it doesn't -- a greeting, small talk, thanks, a question about you or the "
        "app, the answer asking the learner what they want -- offer nothing: respond "
        '{"cards": null}.\n'
        'Otherwise respond with JSON: {"cards": {"intuition": "...", "example": "...", "why": "...", '
        '"use": "...", "deeper": "...", "next": "..."}}'
    )


def declined(raw: str) -> bool:
    """The model judged there is nowhere to go from this answer ({"cards": null})."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        parsed = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and "cards" in parsed and parsed["cards"] is None


def parse_cards(raw: str) -> dict[str, str]:
    """Only a complete set counts: every slot, non-empty, all distinct. A
    partial set would break the one thing that makes picks comparable."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        parsed = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        parsed = None
    cards = parsed.get("cards") if isinstance(parsed, dict) else None
    if not isinstance(cards, dict):
        return {}
    out: dict[str, str] = {}
    for slot in SLOTS:
        text = " ".join(str(cards.get(slot) or "").split()).rstrip("?").strip()
        if not text:
            return {}
        out[slot] = text if len(text) <= MAX_CARD_CHARS else text[: MAX_CARD_CHARS - 1].rstrip() + "…"
    if len({t.lower() for t in out.values()}) != len(out):
        return {}
    return out


class SuggestDirections:
    """One fast-tier call after an answered turn -> text for every slot.
    Takes no learner model on purpose (module docstring)."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, message: str, answer: str, depth: int, breadth: int) -> dict[str, str]:
        self.last_call_count = 0
        knobs = SessionKnobs(depth=depth, breadth=breadth)
        raw = await self._llm.complete(directions_prompt(message, answer, knobs))
        self.last_call_count += 1
        cards = parse_cards(raw)
        if cards or declined(raw):
            return cards  # a deliberate "nothing to offer" is an answer, not a failure
        raw = await self._llm.complete(directions_prompt(message, answer, knobs))
        self.last_call_count += 1
        return parse_cards(raw)


def shuffled_positions(rng: random.Random | None = None) -> dict[str, int]:
    """slot -> display position, a fresh random order for every set."""
    order = list(SLOTS)
    (rng or random).shuffle(order)
    return {slot: i for i, slot in enumerate(order)}


# ----------------------------------------------------------------- store


class DirectionStore:
    """Migration 078. Append-only: insert and read methods only -- no
    delete/remove/update method, no DELETE or UPDATE SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add_set(
        self, *, session_id: UUID, turn_index: int, knobs: SessionKnobs,
        cards: dict[str, str], positions: dict[str, int], presentation: Presentation = "strip",
    ) -> DirectionSet:
        set_id = uuid4()
        rows = [(uuid4(), set_id, slot, positions[slot], text) for slot, text in cards.items()]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO direction_sets "
                "(id, session_id, turn_index, depth_level, breadth_level, presentation) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                set_id, session_id, turn_index, knobs.depth, knobs.breadth, presentation,
            )
            await conn.executemany(
                "INSERT INTO direction_cards (id, set_id, slot, position, text) VALUES ($1, $2, $3, $4, $5)",
                rows,
            )
        return await self.get_set(set_id)  # type: ignore[return-value]

    async def get_set(self, set_id: UUID) -> DirectionSet | None:
        row = await self._pool.fetchrow("SELECT * FROM direction_sets WHERE id = $1", set_id)
        if row is None:
            return None
        cards = await self._pool.fetch(
            "SELECT id, slot, position, text FROM direction_cards WHERE set_id = $1 ORDER BY position",
            set_id,
        )
        return DirectionSet(**dict(row), cards=[DirectionCard(**dict(c)) for c in cards])

    async def latest_set(self, session_id: UUID) -> DirectionSet | None:
        set_id = await self._pool.fetchval(
            "SELECT id FROM direction_sets WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        return await self.get_set(set_id) if set_id else None

    async def open_set_for_card(self, card_id: UUID) -> tuple[DirectionSet, DirectionCard] | None:
        set_id = await self._pool.fetchval("SELECT set_id FROM direction_cards WHERE id = $1", card_id)
        if set_id is None:
            return None
        found = await self.get_set(set_id)
        card = next(c for c in found.cards if c.id == card_id)
        return found, card

    async def is_settled(self, set_id: UUID) -> bool:
        return bool(await self._pool.fetchval(
            "SELECT 1 FROM direction_events WHERE set_id = $1", set_id))

    async def record_event(
        self, *, set_id: UUID, kind: str, next_turn_index: int, card_id: UUID | None = None,
    ) -> None:
        created = await self._pool.fetchval("SELECT created_at FROM direction_sets WHERE id = $1", set_id)
        elapsed = max(0, int((datetime.now(UTC) - created).total_seconds() * 1000))
        try:
            await self._pool.execute(
                "INSERT INTO direction_events (id, set_id, kind, card_id, next_turn_index, elapsed_ms) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid4(), set_id, kind, card_id, next_turn_index, elapsed,
            )
        except asyncpg.UniqueViolationError:
            raise AlreadySettled from None

    async def session_path(self, session_id: UUID) -> list[PathStep]:
        """This session's order of approach: every settled set, in order."""
        rows = await self._pool.fetch(
            "SELECT s.turn_index, e.kind, c.slot, c.position, e.elapsed_ms "
            "FROM direction_events e JOIN direction_sets s ON s.id = e.set_id "
            "LEFT JOIN direction_cards c ON c.id = e.card_id "
            "WHERE s.session_id = $1 ORDER BY s.turn_index, e.created_at",
            session_id,
        )
        return [PathStep(**dict(r)) for r in rows]


def render_path(path: list[PathStep]) -> str:
    """The order of approach as a line for the path-summary prompt, or ''
    when the learner never picked a direction (so that prompt is unchanged)."""
    if not any(step.kind == "picked" for step in path):
        return ""
    steps = [SLOTS[s.slot].split(" -- ")[0] if s.kind == "picked" and s.slot else "(asked their own)"
             for s in path]
    return " -> ".join(steps)
