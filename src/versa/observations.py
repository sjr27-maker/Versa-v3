"""Layer 1 of docs/THINKING_STYLE.md: every raw event, split by lens.

A pick, a slider move, a quick check, a stated preference -- each is raw
evidence, but a single event can mean different things: "took the example
card" is style, how fast they took it is mood, and a quick check they got
wrong right before it is ability. This module reads the append-only raw
tables and turns each event into one or more tagged observations:

  lens     style | range | interest | ability | mood | said
  key      what it is about (a slot, a slider, "stuck", a preference label)
  value    what was seen
  steered  whether Versa shaped the choice it came from (a clean pick is
           the only kind that can prove a style -- invariant 14)

Nothing is stored. The ledger is derived on every read from rows that are
never edited, and stamped with DERIVATION_VERSION, so improving the split
means changing this file -- never rewriting history. The functions that
turn rows into observations are pure; ObservationReader only fetches.

First use (layer 2's start): `turn_flags` marks the moments a pick should
count for less as evidence of style -- right after a question the learner
was stuck on, or from a session they were rushing through -- and the
pick guesser (pick_prediction.py) discounts those picks, by name.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

DERIVATION_VERSION = "obs-v1"

Lens = Literal["style", "range", "interest", "ability", "mood", "said"]
Source = Literal["direction", "knob", "stage", "interaction", "stated"]
ALL_SOURCES: tuple[Source, ...] = ("direction", "knob", "stage", "interaction", "stated")

# A pick this soon after the cards appeared was probably not read.
QUICK_TAP_MS = 1500
# A session is "rushed" when at least this many picks were made in it and
# at least this share of them were quick taps.
RUSHED_MIN_PICKS = 3
RUSHED_QUICK_SHARE = 0.5


class Observation(BaseModel):
    learner_id: UUID
    session_id: UUID
    # the answered turn the evidence is about (None: not tied to one turn)
    turn_index: int | None
    at: datetime
    source: Source
    lens: Lens
    key: str
    value: Any
    steered: bool = False
    detail: dict = {}
    version: str = DERIVATION_VERSION


# ------------------------------------------------------------- pure splits


def from_direction_events(learner_id: UUID, rows: Iterable[dict]) -> list[Observation]:
    """A directions set and what happened to it. The cards are never shaped
    by what Versa believes (invariant 14), so every pick is unsteered.
    Row keys: session_id, turn_index (the answered turn the set followed),
    kind, slot, position, elapsed_ms, created_at."""
    out = []
    for r in rows:
        base = {"learner_id": learner_id, "session_id": r["session_id"], "turn_index": r["turn_index"],
                    "at": r["created_at"], "source": "direction"}
        if r["kind"] == "picked":
            out.append(Observation(**base, lens="style", key=r["slot"], value=1,
                                   detail={"position": r["position"], "elapsed_ms": r["elapsed_ms"]}))
        else:
            out.append(Observation(**base, lens="style", key="passed", value=1))
        out.append(Observation(**base, lens="mood", key="decision_ms", value=r["elapsed_ms"],
                               detail={"kind": r["kind"]}))
    return out


def from_knob_events(learner_id: UUID, rows: Iterable[dict]) -> list[Observation]:
    """A settled slider move: the learner setting their own range. Row keys:
    session_id, turn_count, from_*/to_* levels, created_at."""
    out = []
    for r in rows:
        for knob in ("answer_length", "depth", "breadth"):
            before, after = r[f"from_{knob}"], r[f"to_{knob}"]
            if before != after:
                out.append(Observation(
                    learner_id=learner_id, session_id=r["session_id"], turn_index=None, at=r["created_at"],
                    source="knob", lens="range", key=knob, value=after,
                    detail={"from": before, "turn_count": r["turn_count"]},
                ))
    return out


def from_stage_checks(learner_id: UUID, rows: Iterable[dict]) -> list[Observation]:
    """A quick check at the end of a stage skit. Only checks with a marked
    answer say anything about what landed. Row keys: session_id,
    turn_index, correct, created_at."""
    return [
        Observation(learner_id=learner_id, session_id=r["session_id"], turn_index=r["turn_index"],
                    at=r["created_at"], source="stage", lens="ability", key="stage_check", value=r["correct"])
        for r in rows if r["correct"] is not None
    ]


def from_interactions(learner_id: UUID, rows: Iterable[dict]) -> list[Observation]:
    """How each question came in (interactions.py's entry_state) and how much
    help the answer gave. Row keys: session_id, turn_number, entry_state,
    help_level, created_at."""
    out = []
    for r in rows:
        base = {"learner_id": learner_id, "session_id": r["session_id"], "turn_index": r["turn_number"],
                    "at": r["created_at"], "source": "interaction"}
        state = r["entry_state"]
        if state == "stuck_repeat":
            out.append(Observation(**base, lens="ability", key="stuck", value=True))
        elif state == "topic_switch":
            out.append(Observation(**base, lens="interest", key="topic_switch", value=True))
        elif state == "returning_after_gap":
            out.append(Observation(**base, lens="interest", key="returned_to_topic", value=True))
        if r["help_level"] and r["help_level"] != "none":
            out.append(Observation(**base, lens="ability", key="help_level", value=r["help_level"]))
    return out


def from_stated_preferences(learner_id: UUID, rows: Iterable[dict]) -> list[Observation]:
    """What the learner said about how they like to learn, in their words.
    Row keys: session_id, turn_number, label, stated_preference, created_at."""
    return [
        Observation(learner_id=learner_id, session_id=r["session_id"], turn_index=r["turn_number"],
                    at=r["created_at"], source="stated", lens="said", key=r["label"] or "other",
                    value=r["stated_preference"])
        for r in rows
    ]


# ------------------------------------------ layer 2: reading each session


@dataclass(frozen=True)
class SessionReading:
    """What one session says about the learner's STATE, before anything in it
    is read as style (docs/THINKING_STYLE.md layer 2). Mood and ability vary
    from session to session; the style is what is left once they are set
    aside, so a pick is weighed by the reading of the session it came from.

    `stuck_turns`: answered turns whose question they were stuck on (a
    repeat after the answer missed), or whose quick check they got wrong --
    a pick right after one is partly about the struggle (ability).
    `rushed`: they tapped most cards too fast to have read them (mood)."""

    session_id: UUID
    picks: int = 0
    passes: int = 0
    quick_share: float = 0.0
    median_decision_ms: int | None = None
    rushed: bool = False
    stuck_turns: frozenset[int] = field(default_factory=frozenset)
    wrong_checks: int = 0
    right_checks: int = 0
    help_levels: tuple[str, ...] = ()
    slider_moves: int = 0


def read_sessions(observations: Iterable[Observation]) -> dict[UUID, SessionReading]:
    """Pure: the ledger -> one reading per session it touches."""
    pick_ms: dict[UUID, list[int]] = {}
    passes: dict[UUID, int] = {}
    stuck: dict[UUID, set[int]] = {}
    wrong: dict[UUID, int] = {}
    right: dict[UUID, int] = {}
    helps: dict[UUID, list[str]] = {}
    moves: dict[UUID, int] = {}
    seen: set[UUID] = set()
    for o in observations:
        sid = o.session_id
        seen.add(sid)
        if o.source == "direction" and o.lens == "mood":
            if o.detail.get("kind") == "picked":
                pick_ms.setdefault(sid, []).append(o.value)
            else:
                passes[sid] = passes.get(sid, 0) + 1
        elif o.key == "stuck" and o.value is True and o.turn_index is not None:
            stuck.setdefault(sid, set()).add(o.turn_index)
        elif o.key == "stage_check":
            if o.value is False:
                wrong[sid] = wrong.get(sid, 0) + 1
                if o.turn_index is not None:
                    stuck.setdefault(sid, set()).add(o.turn_index)
            else:
                right[sid] = right.get(sid, 0) + 1
        elif o.key == "help_level":
            helps.setdefault(sid, []).append(o.value)
        elif o.source == "knob":
            moves[sid] = moves.get(sid, 0) + 1
    out = {}
    for sid in seen:
        ms = pick_ms.get(sid, [])
        quick = sum(1 for m in ms if m < QUICK_TAP_MS) / len(ms) if ms else 0.0
        out[sid] = SessionReading(
            session_id=sid, picks=len(ms), passes=passes.get(sid, 0), quick_share=round(quick, 3),
            median_decision_ms=sorted(ms)[len(ms) // 2] if ms else None,
            rushed=len(ms) >= RUSHED_MIN_PICKS and quick >= RUSHED_QUICK_SHARE,
            stuck_turns=frozenset(stuck.get(sid, set())), wrong_checks=wrong.get(sid, 0),
            right_checks=right.get(sid, 0), help_levels=tuple(helps.get(sid, [])),
            slider_moves=moves.get(sid, 0),
        )
    return out


@dataclass(frozen=True)
class TurnFlags:
    """The moments a pick counts for less, read off the session readings:
    `stuck` -- (session, turn) pairs (ability); `rushed` -- sessions (mood)."""

    stuck: frozenset[tuple[UUID, int]] = field(default_factory=frozenset)
    rushed: frozenset[UUID] = field(default_factory=frozenset)


def turn_flags(observations: Iterable[Observation]) -> TurnFlags:
    readings = read_sessions(observations)
    return TurnFlags(
        stuck=frozenset((sid, t) for sid, r in readings.items() for t in r.stuck_turns),
        rushed=frozenset(sid for sid, r in readings.items() if r.rushed),
    )


# ------------------------------------------------------------------ reading


class ObservationReader:
    """Fetches a learner's raw rows and splits them. Read-only: no table of
    its own, nothing written."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def for_learner(
        self, learner_id: UUID, sources: Iterable[Source] = ALL_SOURCES,
    ) -> list[Observation]:
        wanted = set(sources)
        out: list[Observation] = []
        async with self._pool.acquire() as conn:
            if "direction" in wanted:
                out += from_direction_events(learner_id, map(dict, await conn.fetch(
                    "SELECT s.session_id, s.turn_index, e.kind, c.slot, c.position, e.elapsed_ms, e.created_at "
                    "FROM direction_events e JOIN direction_sets s ON s.id = e.set_id "
                    "JOIN sessions se ON se.id = s.session_id "
                    "LEFT JOIN direction_cards c ON c.id = e.card_id WHERE se.learner_id = $1",
                    learner_id)))
            if "knob" in wanted:
                out += from_knob_events(learner_id, map(dict, await conn.fetch(
                    "SELECT k.* FROM knob_events k JOIN sessions se ON se.id = k.session_id "
                    "WHERE se.learner_id = $1", learner_id)))
            if "stage" in wanted:
                out += from_stage_checks(learner_id, map(dict, await conn.fetch(
                    "SELECT c.session_id, c.turn_index, c.correct, c.created_at FROM stage_checks c "
                    "JOIN sessions se ON se.id = c.session_id WHERE se.learner_id = $1", learner_id)))
            if "interaction" in wanted:
                out += from_interactions(learner_id, map(dict, await conn.fetch(
                    "SELECT session_id, turn_number, entry_state::text AS entry_state, "
                    "help_level::text AS help_level, created_at FROM interactions WHERE learner_id = $1",
                    learner_id)))
            if "stated" in wanted:
                out += from_stated_preferences(learner_id, map(dict, await conn.fetch(
                    "SELECT i.session_id, i.turn_number, p.label::text AS label, p.stated_preference, "
                    "p.created_at FROM stated_preferences p JOIN interactions i ON i.id = p.interaction_id "
                    "WHERE p.learner_id = $1 AND p.has_preference", learner_id)))
        out.sort(key=lambda o: (o.at, o.source, o.key))
        return out

    async def turn_flags(self, learner_id: UUID) -> TurnFlags:
        return turn_flags(await self.for_learner(learner_id, ("direction", "stage", "interaction")))
