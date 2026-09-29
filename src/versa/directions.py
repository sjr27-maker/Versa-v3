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
  - the generator is deliberately given NO thinking style, claims, profile
    or cross-session history -- a set shaped by what we already believe
    about someone would turn their pick into an echo of our own guess (the
    circularity risk in IDEAS.md). Two personal inputs only, both things the
    learner did rather than things we concluded, and both applied to every
    slot alike: the depth and breadth sliders (the window the cards are
    pitched in), and the directions they took earlier in THIS chat (so each
    set builds on where they are and never re-offers ground covered --
    "better paths each time", 2026-09-29);
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
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa.llm import LLMClient
from versa.session_knobs import SessionKnobs

Slot = str
# How a set is shown: cards under the answer, or inline links the answer ends
# with, where a pick continues the same explanation (migration 079).
Presentation = Literal["strip", "fork", "compass"]

# The card library (migration 086, docs/THINKING_STYLE.md "a bigger space").
# Every card type, in canonical order -- the first six are the original
# skeleton. Only a few are shown at a time: each answer generates a POOL
# drawn from the library, and a HAND is dealt from the pool (draw_pool,
# deal_hand). The draw is random and never depends on the learner
# (invariant 14); picks are read against the cards actually shown
# (choice.py), so a random subset is as comparable as a fixed six.
LIBRARY_VERSION = "lib-v2"
SLOTS: dict[str, str] = {
    "intuition": "see it simply -- an everyday picture or analogy of the idea",
    "example": "work through one concrete example",
    "why": "why it works -- the reason or mechanism underneath",
    "use": "where it is used -- a real application",
    "deeper": "go further -- a harder or more rigorous version",
    "next": "what comes next -- the related idea it leads to",
    "try_it": "try it yourself -- a small thing to do, build or calculate",
    "real_data": "real numbers -- the idea in actual data or measurements",
    "prove_it": "prove it -- a derivation or argument for why it must be so",
    "mistake": "a common mistake -- where people usually go wrong with it",
    "visualise": "picture it -- a diagram, shape or scene of how it looks",
    "story": "the story behind it -- who figured it out, and how",
    "summary": "the one-line version -- the whole idea in a sentence",
    "compare": "compare it -- how it differs from a similar idea",
    "connect": "connect it -- the same idea in another subject",
    "debate": "the debate -- where people disagree or it is still open",
}
CLASSIC_SLOTS: tuple[str, ...] = ("intuition", "example", "why", "use", "deeper", "next")

# Four families -- the four ways out of an answer (and the compass's four
# points). A hand takes at most one card per family, so every hand spans
# the space instead of being six shades of one thing.
FAMILIES: dict[str, tuple[str, ...]] = {
    "real": ("example", "use", "try_it", "real_data"),
    "deeper": ("why", "deeper", "prove_it", "mistake"),
    "simpler": ("intuition", "visualise", "story", "summary"),
    "wider": ("next", "compare", "connect", "debate"),
}
FAMILY_OF: dict[str, str] = {slot: fam for fam, slots in FAMILIES.items() for slot in slots}

# Where each card type sits in the space, -1..1 per axis (hand-set, lib-v2):
# concrete (+) vs abstract (-), deeper (+) vs simpler (-), wider (+) vs
# focused (-), practical (+) vs theoretical (-). A learner's style can then be
# read as a region -- which way their picks lean -- not just a favourite card.
AXES: tuple[str, ...] = ("concrete", "depth", "breadth", "practical")
COORDS: dict[str, tuple[float, float, float, float]] = {
    "example": (1.0, 0.0, -0.5, 0.5),
    "use": (0.5, 0.0, 0.5, 1.0),
    "try_it": (1.0, 0.0, -0.5, 1.0),
    "real_data": (1.0, 0.5, 0.0, 0.5),
    "why": (-0.5, 1.0, 0.0, -0.5),
    "deeper": (-0.5, 1.0, -0.5, -0.5),
    "prove_it": (-1.0, 1.0, -0.5, -1.0),
    "mistake": (0.5, 0.5, -0.5, 0.0),
    "intuition": (0.5, -1.0, 0.0, 0.0),
    "visualise": (0.5, -0.5, 0.0, 0.0),
    "story": (0.5, -0.5, 0.5, -0.5),
    "summary": (-0.5, -1.0, -0.5, 0.0),
    "next": (-0.5, 0.5, 0.5, -0.5),
    "compare": (0.0, 0.0, 1.0, -0.5),
    "connect": (0.0, 0.0, 1.0, 0.5),
    "debate": (-0.5, 0.5, 1.0, -0.5),
}

POOL_PER_FAMILY = 2  # a pool of 8: enough for the first hand and one "other directions"
HAND_SIZE = 3
# The compass shows one card for every family -- four ways out of the answer.
COMPASS_HAND_SIZE = 4
MAX_CARD_CHARS = 70
# How often a hand swaps one card for the pool's extras (idea 4 and 6 of "a
# bigger space"): a WILD card written with no type (tagged afterwards to the
# nearest one) and a PATH card that is two steps in one ("work one out, then
# see where it's used"). Random, logged on the card, never learner-driven.
PATH_CHANCE = 0.25
WILD_CHANCE = 0.25
EXTRAS = ("wild", "path")


def draw_pool(rng: random.Random | None = None) -> list[str]:
    """The card types one answer's cards are written for: POOL_PER_FAMILY
    from every family, at random. Never depends on the learner."""
    r = rng or random
    return [slot for fam in FAMILIES.values() for slot in r.sample(fam, POOL_PER_FAMILY)]


def draw_path(pool: list[str], rng: random.Random | None = None) -> tuple[str, str]:
    """A two-step route for the pool's path card: two of its card types from
    different families, in a random order."""
    r = rng or random
    first = r.choice(pool)
    second = r.choice([s for s in pool if FAMILY_OF[s] != FAMILY_OF[first]])
    return first, second


def with_extras(hand: list[str], available: set[str], rng: random.Random | None = None,
                *, widen: bool = False) -> list[str]:
    """Sometimes swap one card of a hand for an extra the pool still has
    ('path' or 'wild'), at random. The hand stays the same size.

    `widen` (right after a miss in this chat -- the learner passed every card
    by asking their own question): an extra is swapped in for certain, which
    one at random. The experiment is random: nothing about what Versa
    believes of the learner picks it (invariant 14)."""
    r = rng or random
    if widen:
        options = [e for e in EXTRAS if e in available]
        if options and hand:
            extra = r.choice(options)
            return [*hand[:-1], extra] if len(hand) > 1 else [*hand, extra]
        return hand
    for extra, chance in (("path", PATH_CHANCE), ("wild", WILD_CHANCE)):
        if extra in available and hand and r.random() < chance:
            return [*hand[:-1], extra] if len(hand) > 1 else [*hand, extra]
    return hand


def deal_hand(pool: list[str], dealt: set[str], size: int = HAND_SIZE,
              rng: random.Random | None = None) -> list[str]:
    """A hand from what is left of the pool: at most one card per family,
    families chosen at random (layered, so every hand spans the space).
    Fewer than `size` when the pool runs low; [] when it is spent."""
    r = rng or random
    left: dict[str, list[str]] = {}
    for slot in pool:
        if slot not in dealt:
            left.setdefault(FAMILY_OF[slot], []).append(slot)
    families = list(left)
    r.shuffle(families)
    return [r.choice(left[fam]) for fam in families[:size]]


class DirectionCard(BaseModel):
    id: UUID
    slot: Slot
    position: int
    text: str
    # a 'wild' card: the library type it was tagged as afterwards
    tagged_as: str | None = None
    # a 'path' card: its two steps, e.g. "example>use"
    path_slots: str | None = None


class DirectionSet(BaseModel):
    id: UUID
    session_id: UUID
    turn_index: int
    depth_level: int
    breadth_level: int
    presentation: Presentation = "strip"
    created_at: datetime
    cards: list[DirectionCard]
    # the pool this hand was dealt from (None: an original six-card set), and
    # which deal it was: 0 the first hand, 1+ after "other directions"
    pool_id: UUID | None = None
    deal_index: int = 0
    # 'after_miss': dealt right after a miss in this chat, widened at random
    experiment: str | None = None


class PathStep(BaseModel):
    """One step of a learner's order of approach within a session."""
    turn_index: int
    kind: Literal["picked", "passed", "more"]
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


def _path_line(path_so_far: list[str]) -> str:
    """Where this learner has already gone in THIS chat, or '' before their
    first pick (so the first set's prompt is unchanged)."""
    if not path_so_far:
        return ""
    taken = " -> ".join(f'"{text}"' for text in path_so_far)
    return (
        f"\nIn this chat they have already taken, in order: {taken}. Every card should "
        "build on where they are now and lead somewhere they have NOT been yet -- never "
        "re-offer a direction they already took. This is the same for every slot.\n"
    )


def directions_prompt(
    message: str, answer: str, knobs: SessionKnobs, path_so_far: list[str] | None = None,
    slots: list[str] | tuple[str, ...] = CLASSIC_SLOTS, path: tuple[str, str] | None = None,
    wild: bool = False,
) -> str:
    listed = "".join(f"- {slot}: {SLOTS[slot]}\n" for slot in slots)
    if path:
        listed += (f"- path: a two-step route in ONE card -- first {SLOTS[path[0]].split(' -- ')[0]}, "
                   f"then {SLOTS[path[1]].split(' -- ')[0]} (e.g. \"Work one out, then see where it's used\")\n")
    if wild:
        listed += ("- wild: a direction worth offering that NONE of the lines above covers -- surprising "
                   "but genuinely useful for this topic\n")
    extra_keys = [*(["path"] if path else []), *(["wild"] if wild else [])]
    shape = ", ".join(f'"{slot}": "..."' for slot in [*slots, *extra_keys])
    return (
        "DIRECTIONS:SUGGEST\n"
        "A learner just got an answer. Offer the directions they could take next, "
        "one card per slot below, so they can recognise what they want instead of "
        "having to put it into words.\n"
        f"Their message: {message}\n"
        f"The answer they got (may be cut off): <<<{answer[:1500]}>>>\n"
        f"{_window_line(knobs)}"
        f"{_path_line(path_so_far or [])}"
        f"\nSlots:\n{listed}"
        "\nFor EVERY slot write one card: what the learner would tap to go there, "
        "in their voice, specific to THIS topic (e.g. \"Show me with a speedometer\", "
        f"\"Work one out: x^3\"), at most {MAX_CARD_CHARS} characters, no numbering, "
        "no question marks. Different slots must lead to genuinely different places.\n"
        # 2026-09-27: "hi" got six cards about oxygen and nerve impulses.
        "\nBut FIRST judge whether the answer explains anything there is somewhere to go "
        "from. If it doesn't -- a greeting, small talk, thanks, a question about you or the "
        "app, the answer asking the learner what they want -- offer nothing: respond "
        '{"cards": null}.\n'
        f'Otherwise respond with JSON: {{"cards": {{{shape}}}}}'
    )


def declined(raw: str) -> bool:
    """The model judged there is nowhere to go from this answer ({"cards": null})."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        parsed = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and "cards" in parsed and parsed["cards"] is None


def parse_cards(raw: str, slots: list[str] | tuple[str, ...] = CLASSIC_SLOTS,
                optional: tuple[str, ...] = ()) -> dict[str, str]:
    """Only a complete set counts: every slot asked for, non-empty, all
    distinct. A card the draw chose but the model skipped would make the
    draw no longer random."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        parsed = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        parsed = None
    cards = parsed.get("cards") if isinstance(parsed, dict) else None
    if not isinstance(cards, dict):
        return {}
    out: dict[str, str] = {}
    for slot in slots:
        text = " ".join(str(cards.get(slot) or "").split()).rstrip("?").strip()
        if not text:
            return {}
        out[slot] = text if len(text) <= MAX_CARD_CHARS else text[: MAX_CARD_CHARS - 1].rstrip() + "…"
    for slot in optional:  # an extra the model skipped is just not offered
        text = " ".join(str(cards.get(slot) or "").split()).rstrip("?").strip()
        if text and text.lower() not in {t.lower() for t in out.values()}:
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

    async def run(
        self, message: str, answer: str, depth: int, breadth: int, path_so_far: list[str] | None = None,
        slots: list[str] | None = None, path: list[str] | None = None, wild: bool = False,
    ) -> dict[str, str]:
        """`path_so_far`: the directions this learner took earlier in THIS
        chat, in order -- so each set builds on where they are instead of
        re-offering ground they covered. Given to every slot alike; nothing
        learned about the learner (module docstring). `slots`: the card
        types the draw chose (draw_pool); the original six when not given."""
        self.last_call_count = 0
        knobs = SessionKnobs(depth=depth, breadth=breadth)
        wanted = tuple(slots) if slots else CLASSIC_SLOTS
        route = (path[0], path[1]) if path else None
        optional = (*(("path",) if route else ()), *(("wild",) if wild else ()))
        # With a drawn pool, the model writes a card for EVERY library type
        # (its response schema requires them all, llm.py) and the draw keeps
        # the ones it picked: asking for just the drawn types, Gemini's fixed
        # schema would not let it answer (found live 2026-09-30).
        written = tuple(SLOTS) if slots else CLASSIC_SLOTS
        prompt = directions_prompt(message, answer, knobs, path_so_far, written, route, wild)
        raw = await self._llm.complete(prompt)
        self.last_call_count += 1
        cards = parse_cards(raw, wanted, optional)
        if cards or declined(raw):
            return cards  # a deliberate "nothing to offer" is an answer, not a failure
        raw = await self._llm.complete(prompt)
        self.last_call_count += 1
        return parse_cards(raw, wanted, optional)


_TYPE_VECTORS: dict[str, list[float]] = {}


async def nearest_types(text: str, embedding_client) -> list[tuple[str, float]]:
    """Every library type by how close `text` is to its description (cosine,
    closest first) -- no model call, nothing about the learner. The type
    descriptions are embedded once per process."""
    from versa.embeddings import TASK_SIMILARITY
    from versa.vector_math import cosine_similarity

    for slot, desc in SLOTS.items():
        if slot not in _TYPE_VECTORS:
            _TYPE_VECTORS[slot] = await embedding_client.embed(desc, task_type=TASK_SIMILARITY)
    vec = await embedding_client.embed(text, task_type=TASK_SIMILARITY)
    return sorted(((slot, cosine_similarity(vec, _TYPE_VECTORS[slot])) for slot in SLOTS),
                  key=lambda x: -x[1])


async def tag_wild(text: str, embedding_client) -> str | None:
    """The library type a wild card is closest to."""
    return (await nearest_types(text, embedding_client))[0][0]


# A miss: the learner passed every card by asking their own question. How the
# question is read against the library (build item 8, migration 088).
MISS_TAGGER_VERSION = "miss-v1"
# The nearest type is kept only when it is this close and clearly closer than
# the next: a question that is none of the ways out is not forced onto one.
# Calibrated 2026-09-30 on real Gemini embeddings, 16 typical follow-ups (one
# per type) and 3 that are none: meant-as-a-type questions land at 0.85-0.95,
# "ok thanks" / a new subject at 0.71-0.80. At these bars 14 of 16 were kept,
# all 14 read right (the two dropped: one misread, one near-tie), and none of
# the 3 was kept. A small sample -- recalibrate from organic misses (both top
# similarities are kept on every row).
MISS_MIN_SIMILARITY = 0.84
MISS_MIN_MARGIN = 0.02


def read_miss(ranked: list[tuple[str, float]]) -> str | None:
    """The type a missed question asked for, or None when it isn't clearly one."""
    if not ranked:
        return None
    (best, sim), second = ranked[0], ranked[1][1] if len(ranked) > 1 else 0.0
    return best if sim >= MISS_MIN_SIMILARITY and sim - second >= MISS_MIN_MARGIN else None


MISS_READER_VERSION = "read-miss-v2"


def read_miss_prompt(earlier: str, question: str) -> str:
    kinds = "\n".join(f"- {slot}: {desc}" for slot, desc in SLOTS.items())
    return (
        "DIRECTIONS:READ_MISS\n"
        "A student was given an answer and some suggested ways to go on, took none of them, and typed "
        "their own follow-up instead. Read what KIND of move their follow-up makes -- not what it is about.\n\n"
        f"Their earlier question: {earlier or '(not known)'}\n"
        f"Their follow-up: {question}\n\n"
        f"The known kinds of move:\n{kinds}\n\n"
        "same_subject: false when the follow-up leaves the earlier subject for a new one (then it is not a "
        "way on from that answer at all).\n"
        "move: the move in at most 8 words, with no topic words at all -- the same phrase should fit any "
        "subject (e.g. \"where the rule stops working\", \"what it would cost to get wrong\").\n"
        "type: decide this last, and strictly. Give a known kind only when a card of exactly that kind, as "
        "described above, is what they asked for. A move that is merely NEAR a kind is \"none\": where a "
        "rule stops holding is not \"go further\", who decides or who is to be trusted is not \"the story "
        "behind it\", what is at stake is not \"a common mistake\". New kinds of move are what this is "
        "for -- do not force one onto the list.\n"
        'Respond with JSON: {"same_subject": true, "move": "...", "type": "..."}'
    )


class MissReading(BaseModel):
    same_subject: bool
    type: str | None = None
    move: str


class ReadMiss:
    """One fast-tier call: what kind of move a missed question makes, when
    the embedding couldn't place it. Given the question and the one before
    it only -- nothing about the learner (invariant 14's spirit: a reading,
    not a conclusion). Recorded through SessionLoop._call_node."""

    def __init__(self, llm) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, earlier: str, question: str) -> MissReading | None:
        self.last_call_count = 0
        raw = await self._llm.complete(read_miss_prompt(earlier, question))
        self.last_call_count += 1
        try:
            data = json.loads(raw)
            reading = MissReading(
                same_subject=bool(data["same_subject"]),
                type=data.get("type") if data.get("type") in SLOTS else None,
                move=" ".join(str(data["move"]).split())[:120],
            )
        except (ValueError, KeyError, TypeError):
            return None
        return reading if reading.move else None


class StoredMissReading(BaseModel):
    set_id: UUID
    learner_id: UUID
    session_id: UUID
    same_subject: bool
    type: str | None
    move: str
    move_embedding: list[float] | None
    created_at: datetime


class DirectionMiss(BaseModel):
    set_id: UUID
    session_id: UUID
    turn_index: int
    question: str
    follow_up: bool | None
    tagged_as: str | None
    similarity: float | None
    runner_up: str | None
    runner_up_similarity: float | None
    in_hand: bool
    created_at: datetime


def shuffled_positions(rng: random.Random | None = None,
                       slots: list[str] | tuple[str, ...] = CLASSIC_SLOTS) -> dict[str, int]:
    """slot -> display position, a fresh random order for every set."""
    order = list(slots)
    (rng or random).shuffle(order)
    return {slot: i for i, slot in enumerate(order)}


# ----------------------------------------------------------------- store


class DirectionStore:
    """Migration 078. Append-only: insert and read methods only -- no
    delete/remove/update method, no DELETE or UPDATE SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add_pool(self, *, session_id: UUID, turn_index: int, cards: dict[str, str]) -> UUID:
        """Every card one answer's generation wrote (migration 086): what
        could have been shown, kept whether or not it is ever dealt."""
        pool_id = uuid4()
        await self._pool.execute(
            "INSERT INTO direction_pools (id, session_id, turn_index, library_version, cards) "
            "VALUES ($1, $2, $3, $4, $5)",
            pool_id, session_id, turn_index, LIBRARY_VERSION, cards,
        )
        return pool_id

    async def get_pool(self, pool_id: UUID) -> dict[str, str]:
        cards = await self._pool.fetchval("SELECT cards FROM direction_pools WHERE id = $1", pool_id)
        return dict(cards or {})

    async def dealt_slots(self, pool_id: UUID) -> set[str]:
        """The card types already dealt from a pool (derived, never stored)."""
        rows = await self._pool.fetch(
            "SELECT c.slot, c.tagged_as FROM direction_cards c JOIN direction_sets s ON s.id = c.set_id "
            "WHERE s.pool_id = $1", pool_id,
        )
        return {r["slot"] for r in rows}

    async def add_set(
        self, *, session_id: UUID, turn_index: int, knobs: SessionKnobs,
        cards: dict[str, str], positions: dict[str, int], presentation: Presentation = "strip",
        pool_id: UUID | None = None, deal_index: int = 0, extras: dict[str, dict] | None = None,
        experiment: str | None = None,
    ) -> DirectionSet:
        """`extras`: per-card metadata by slot -- tagged_as for a wild card,
        path_slots for a path card."""
        set_id = uuid4()
        extras = extras or {}
        rows = [
            (uuid4(), set_id, slot, positions[slot], text,
             extras.get(slot, {}).get("tagged_as"), extras.get(slot, {}).get("path_slots"))
            for slot, text in cards.items()
        ]
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO direction_sets "
                "(id, session_id, turn_index, depth_level, breadth_level, presentation, pool_id, deal_index, "
                "experiment) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                set_id, session_id, turn_index, knobs.depth, knobs.breadth, presentation, pool_id, deal_index,
                experiment,
            )
            await conn.executemany(
                "INSERT INTO direction_cards (id, set_id, slot, position, text, tagged_as, path_slots) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                rows,
            )
        return await self.get_set(set_id)  # type: ignore[return-value]

    async def get_set(self, set_id: UUID) -> DirectionSet | None:
        row = await self._pool.fetchrow("SELECT * FROM direction_sets WHERE id = $1", set_id)
        if row is None:
            return None
        cards = await self._pool.fetch(
            "SELECT id, slot, position, text, tagged_as, path_slots FROM direction_cards "
            "WHERE set_id = $1 ORDER BY position",
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

    async def last_was_miss(self, session_id: UUID) -> bool:
        """Was the last settled set in this chat passed (a miss)? Something
        the learner did in THIS chat -- the only thing an experiment reads."""
        kind = await self._pool.fetchval(
            "SELECT e.kind FROM direction_events e JOIN direction_sets s ON s.id = e.set_id "
            "WHERE s.session_id = $1 ORDER BY e.created_at DESC LIMIT 1",
            session_id,
        )
        return kind == "passed"

    async def add_miss(self, *, set_id: UUID, question: str, follow_up: bool | None,
                       ranked: list[tuple[str, float]]) -> DirectionMiss | None:
        """Keep what a missed set's learner asked instead, read against the
        library. Once per set (set_id UNIQUE): None if already kept. A
        question on a new subject is kept untagged -- an interest, not a way
        out of the answer."""
        found = await self.get_set(set_id)
        if found is None:
            return None
        tagged = read_miss(ranked) if follow_up is not False else None
        hand = {c.tagged_as or c.slot for c in found.cards}
        best = ranked[0] if ranked else (None, None)
        second = ranked[1] if len(ranked) > 1 else (None, None)
        try:
            await self._pool.execute(
                "INSERT INTO direction_misses (id, set_id, session_id, turn_index, question, follow_up, tagged_as, "
                "similarity, runner_up, runner_up_similarity, in_hand, tagger_version) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                uuid4(), set_id, found.session_id, found.turn_index, question, follow_up, tagged,
                best[1], second[0], second[1], tagged in hand, MISS_TAGGER_VERSION,
            )
        except asyncpg.UniqueViolationError:
            return None
        row = await self._pool.fetchrow("SELECT * FROM direction_misses WHERE set_id = $1", set_id)
        return DirectionMiss(**{k: row[k] for k in DirectionMiss.model_fields})

    async def add_reading(self, *, set_id: UUID, reading: MissReading,
                          embedding: list[float] | None) -> bool:
        """Keep the model's reading of a missed set's question, once (set_id
        UNIQUE). False if it was already kept."""
        try:
            await self._pool.execute(
                "INSERT INTO direction_miss_readings (id, set_id, same_subject, type, move, move_embedding, "
                "reader_version) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                uuid4(), set_id, reading.same_subject, reading.type, reading.move, embedding, MISS_READER_VERSION,
            )
        except asyncpg.UniqueViolationError:
            return False
        return True

    async def miss_readings(self) -> list[StoredMissReading]:
        """Every learner's readings, oldest first (new moves are grouped
        across everyone: discover_moves)."""
        rows = await self._pool.fetch(
            "SELECT r.set_id, se.learner_id, s.session_id, r.same_subject, r.type, r.move, r.move_embedding, "
            "r.created_at FROM direction_miss_readings r JOIN direction_sets s ON s.id = r.set_id "
            "JOIN sessions se ON se.id = s.session_id ORDER BY r.created_at LIMIT 20000",
        )
        out = []
        for r in rows:
            row = dict(r)
            if row["move_embedding"] is not None:
                row["move_embedding"] = row["move_embedding"].to_list()
            out.append(StoredMissReading(**row))
        return out

    async def learner_misses(self, learner_id: UUID | None, *, others: bool = False) -> list[DirectionMiss]:
        """A learner's misses, oldest first -- or, with `others`, everyone
        else's (the cohort default)."""
        op = "IS DISTINCT FROM" if others else "="
        rows = await self._pool.fetch(
            "SELECT m.* FROM direction_misses m JOIN sessions se ON se.id = m.session_id "
            f"WHERE se.learner_id {op} $1 ORDER BY m.created_at LIMIT 5000",
            learner_id,
        )
        return [DirectionMiss(**{k: r[k] for k in DirectionMiss.model_fields}) for r in rows]

    async def is_settled(self, set_id: UUID) -> bool:
        return bool(await self._pool.fetchval(
            "SELECT 1 FROM direction_events WHERE set_id = $1", set_id))

    async def record_event(
        self, *, set_id: UUID, kind: str, next_turn_index: int, card_id: UUID | None = None,
    ) -> None:
        # How long they took, measured on the DATABASE's clock at both ends:
        # the set's created_at is the database's NOW(), so subtracting this
        # process's clock would add any skew between the two machines -- and
        # a skew of a few hundred ms is enough to turn a read-and-chosen pick
        # into a "too quick to have read" one (found 2026-09-29).
        try:
            await self._pool.execute(
                "INSERT INTO direction_events (id, set_id, kind, card_id, next_turn_index, elapsed_ms) "
                "SELECT $1, $2, $3, $4, $5, "
                "GREATEST(0, (EXTRACT(EPOCH FROM (clock_timestamp() - s.created_at)) * 1000)::int) "
                "FROM direction_sets s WHERE s.id = $2",
                uuid4(), set_id, kind, card_id, next_turn_index,
            )
        except asyncpg.UniqueViolationError:
            raise AlreadySettled from None

    async def taken_texts(self, session_id: UUID) -> list[str]:
        """The cards this learner took in this session, in order (their
        words on the cards they picked)."""
        rows = await self._pool.fetch(
            "SELECT c.text FROM direction_events e JOIN direction_sets s ON s.id = e.set_id "
            "JOIN direction_cards c ON c.id = e.card_id "
            "WHERE s.session_id = $1 AND e.kind = 'picked' ORDER BY s.turn_index, e.created_at",
            session_id,
        )
        return [r["text"] for r in rows]

    async def session_path(self, session_id: UUID) -> list[PathStep]:
        """This session's order of approach: every settled set, in order."""
        rows = await self._pool.fetch(
            "SELECT s.turn_index, e.kind, COALESCE(c.tagged_as, c.slot) AS slot, c.position, e.elapsed_ms "
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
    def step(s: PathStep) -> str:
        if s.kind == "more":
            return "(other directions)"
        if s.kind != "picked" or not s.slot:
            return "(asked their own)"
        return SLOTS[s.slot].split(" -- ")[0] if s.slot in SLOTS else s.slot

    steps = [step(s) for s in path]
    return " -> ".join(steps)
