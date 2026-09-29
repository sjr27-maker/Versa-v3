"""Layer 3 of docs/THINKING_STYLE.md: which ways of thinking hold across
sessions -- the thinking style, as it was defined:

  > how a person, given free choice, moves through ideas -- where they
  > start, in what order, in which direction, and within what depth and
  > breadth limits -- stable across topics and over time, measured against
  > their cohort's default, and proven when it predicts their next
  > unsteered choice. Interests are tracked separately.

So a pattern is structured, not a sentence, and it must pass a gate for
every clause of that definition before it is called confirmed:

  free choice     built only from unsteered evidence: "where this could go"
                  picks (the cards are never shaped by beliefs, invariant
                  14) and slider moves the learner made themselves
  where / order   kinds: `way_in` (the first card after a question of their
                  own), `then` (what they take right after a given card),
                  `range` (the depth / breadth level they set)
  mood, ability   every pick weighed by its session's reading (layer 2):
                  stuck, rushed, quick taps and first-card taps count less
  across topics   seen in >= MIN_SESSIONS sessions and >= MIN_TOPICS
                  different topics (questions told apart by the same
                  similarity entry_state uses)
  over time       clear in both the earlier and the later half of the
                  evidence; clear before but not lately = `fading`
  vs the cohort   at least MIN_LIFT times as common as for other learners
  proven          out of sample: judged only on picks made AFTER the
                  pattern had shown itself, it must beat the cohort
  interests       not a pattern here -- what pulls them is not how they think

`emerging` means clear in their picks but not yet through every gate --
enough to shape a follow-up answer (pick_prediction.approach_profile), not
enough to be called their style.

Nothing is stored: patterns are derived on every read from append-only rows,
stamped STYLE_VERSION, and every gate is returned with its numbers so what
contributed can be seen.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from versa.directions import SLOTS
from versa.interactions import InteractionStore, centred_cosine
from versa.observations import Observation, ObservationReader
from versa.pick_prediction import PastPick, PredictionStore, slot_label
from versa.pick_prediction import _pick_weight as pick_weight

STYLE_VERSION = "style-v1"
_SLOTS: tuple[str, ...] = tuple(SLOTS)

MIN_CONTEXTS = 4
MIN_RATE = 0.35
MIN_SESSIONS = 3
MIN_TOPICS = 3
MIN_LIFT = 1.5
MIN_TRIALS = 3
MIN_PRIOR = 3  # picks seen before a pick is used as an out-of-sample trial

# Topics are told apart on centred embeddings (interactions.py's calibration
# note). The bar is LOWER than the follow-up one on purpose: wrongly
# splitting one topic in two would let a pattern pass the "different topics"
# gate too easily, so grouping errs toward merging.
TOPIC_THRESHOLD = 0.30

RANGE_MIN_SESSIONS = 3
RANGE_MIN_OFFSET = 15
RANGE_MAX_SPREAD = 30
RANGE_MAX_DRIFT = 20

Status = Literal["confirmed", "emerging", "fading"]


class Gate(BaseModel):
    ok: bool
    have: str
    need: str


class StylePattern(BaseModel):
    kind: Literal["way_in", "then", "range"]
    key: str
    statement: str
    status: Status
    gates: dict[str, Gate]
    n: int
    sessions: int
    topics: int = 0
    rate: float | None = None
    cohort_rate: float | None = None
    lift: float | None = None
    early_rate: float | None = None
    late_rate: float | None = None
    hits: int = 0
    trials: int = 0
    expected_hits: float = 0.0
    value: int | None = None
    version: str = STYLE_VERSION


@dataclass(frozen=True)
class StylePick:
    session_id: UUID
    at: datetime
    slot: str
    prev_slot: str | None
    weight: float
    topic: str


# --------------------------------------------------------------- topics


def assign_topics(
    rows: Iterable[dict], centre: list[float] | None, threshold: float = TOPIC_THRESHOLD,
) -> dict[tuple[UUID, int], str]:
    """(session, turn) -> topic id. Questions the learner typed are grouped
    in time order by centred similarity to each topic's FIRST question.
    With no centre yet (too few questions on record) topics can't be told
    apart, so everything is one topic -- nothing can pass the "different
    topics" gate on a guess. Deliberately not a running
    centre: an online running mean drifts and swallows unrelated topics (the
    cascading-merge failure vector_math.running_mean documents). A turn that isn't
    a question of their own (an option click, a card) belongs to the topic
    they were on in that session (so does a taken card: its text is ours,
    not their question -- the reader marks those "card"). Row keys: session_id, turn_number,
    question_author, question_embedding -- ordered by time."""
    seeds: list[list[float]] = []
    current: dict[UUID, str] = {}
    out: dict[tuple[UUID, int], str] = {}
    for r in rows:
        sid, turn = r["session_id"], r["turn_number"]
        vec = r["question_embedding"]
        if r["question_author"] == "learner" and vec is not None:
            vec = vec.to_list() if hasattr(vec, "to_list") else list(vec)  # pgvector halfvec
            best, best_sim = None, threshold
            if centre is None:
                seeds = seeds or [vec]
            for i, seed in enumerate(seeds):
                sim = 1.0 if centre is None else centred_cosine(vec, seed, centre)
                if sim >= best_sim:
                    best, best_sim = i, sim
            if best is None:
                seeds.append(vec)
                best = len(seeds) - 1
            current[sid] = f"t{best}"
        out[(sid, turn)] = current.get(sid, f"s:{sid}")
    return out


def style_picks(past: list[PastPick], topics: dict[tuple[UUID, int], str]) -> list[StylePick]:
    """Layer 2's weighing applied (stuck, rushed, quick tap, first card);
    no recency decay here -- time is judged by the early/late gate."""
    return [
        StylePick(session_id=p.session_id, at=p.at, slot=p.slot, prev_slot=p.prev_slot,
                  weight=pick_weight(p)[0], topic=topics.get((p.session_id, p.turn_index), f"s:{p.session_id}"))
        for p in past if p.at is not None
    ]


# ------------------------------------------------------- the pick patterns


def _share(picks: list[StylePick], slot: str) -> float:
    """Weighted share of `slot`, with one pseudo-pick per slot (an unseen
    slot is unlikely, never impossible) -- the same smoothing the guess uses."""
    total = sum(p.weight for p in picks)
    hit = sum(p.weight for p in picks if p.slot == slot)
    return (hit + 1.0) / (total + len(_SLOTS))


def _top(picks: list[StylePick]) -> str:
    return max(_SLOTS, key=lambda s: (_share(picks, s), -_SLOTS.index(s)))


def _cohort_rate(counts: dict[str, int], slot: str) -> float:
    return (counts.get(slot, 0) + 1.0) / (sum(counts.values()) + len(_SLOTS))


def evaluate(kind: str, key: str, context: list[StylePick], slot: str, cohort: dict[str, int],
             statement: str) -> StylePattern | None:
    """Every gate for one candidate pattern: `context` is every pick made in
    the situation the pattern is about (e.g. every first pick after a
    question of their own), `slot` the one it says they go to."""
    context = sorted(context, key=lambda p: p.at)
    n = len(context)
    if n == 0:
        return None
    rate = _share(context, slot)
    support = [p for p in context if p.slot == slot]
    sessions = len({p.session_id for p in support})
    topics = len({p.topic for p in support})
    cohort_rate = _cohort_rate(cohort, slot)
    lift = rate / cohort_rate
    half = n // 2
    early = _share(context[:half], slot) if half else None
    late = _share(context[half:], slot) if half else None
    hits = trials = 0
    expected = 0.0
    for i in range(MIN_PRIOR, n):
        prior = context[:i]
        if _top(prior) == slot and _share(prior, slot) >= MIN_RATE:
            trials += 1
            hits += context[i].slot == slot
            expected += cohort_rate
    bar = max(MIN_RATE, cohort_rate * MIN_LIFT)
    gates = {
        "evidence": Gate(ok=n >= MIN_CONTEXTS, have=f"{n} picks in this situation", need=f">= {MIN_CONTEXTS}"),
        "clear": Gate(ok=rate >= MIN_RATE, have=f"{rate:.0%} go there", need=f">= {MIN_RATE:.0%}"),
        "sessions": Gate(ok=sessions >= MIN_SESSIONS, have=f"{sessions} sessions", need=f">= {MIN_SESSIONS}"),
        "topics": Gate(ok=topics >= MIN_TOPICS, have=f"{topics} different topics", need=f">= {MIN_TOPICS}"),
        "above_cohort": Gate(ok=lift >= MIN_LIFT, have=f"{lift:.1f}x other learners ({cohort_rate:.0%})",
                             need=f">= {MIN_LIFT}x"),
        "over_time": Gate(ok=early is not None and early >= MIN_RATE and late >= MIN_RATE,
                          have="-" if early is None else f"earlier {early:.0%}, later {late:.0%}",
                          need=f"both >= {MIN_RATE:.0%}"),
        "predicts": Gate(ok=trials >= MIN_TRIALS and hits / trials >= bar,
                         have=f"{hits} of {trials} later picks" if trials else "no later picks yet",
                         need=f">= {MIN_TRIALS} and >= {bar:.0%} right"),
    }
    if all(g.ok for g in gates.values()):
        status: Status = "confirmed"
    elif n >= 2 * MIN_CONTEXTS and early is not None and early >= MIN_RATE and late < MIN_RATE:
        status = "fading"
    elif gates["evidence"].ok and gates["clear"].ok:
        status = "emerging"
    else:
        return None
    return StylePattern(
        kind=kind, key=key, statement=statement, status=status, gates=gates, n=n, sessions=sessions,
        topics=topics, rate=round(rate, 4), cohort_rate=round(cohort_rate, 4), lift=round(lift, 3),
        early_rate=None if early is None else round(early, 4), late_rate=None if late is None else round(late, 4),
        hits=hits, trials=trials, expected_hits=round(expected, 3),
    )


def pick_patterns(picks: list[StylePick], cohort_way_in: dict[str, int],
                  cohort_then: dict[str, dict[str, int]]) -> list[StylePattern]:
    out = []
    starts = [p for p in picks if p.prev_slot is None]
    if starts:
        slot = _top(starts)
        found = evaluate("way_in", slot, starts, slot, cohort_way_in,
                         f"On a question of their own, goes first to “{slot_label(slot)}”.")
        if found:
            out.append(found)
    for a in _SLOTS:
        after = [p for p in picks if p.prev_slot == a]
        if not after:
            continue
        b = _top(after)
        found = evaluate("then", f"{a}>{b}", after, b, cohort_then.get(a, {}),
                         f"After “{slot_label(a)}”, goes to “{slot_label(b)}”.")
        if found:
            out.append(found)
    return out


# ------------------------------------------------------- the range patterns


def _median(values: list[int]) -> float:
    v = sorted(values)
    m = len(v) // 2
    return float(v[m]) if len(v) % 2 else (v[m - 1] + v[m]) / 2


def range_patterns(knob_obs: Iterable[Observation], cohort_levels: dict[str, list[int]]) -> list[StylePattern]:
    """Where they set their own depth / breadth. Only sessions where they
    moved that slider count -- a default was never a choice. A range is set,
    not picked, so it is proven by being kept (consistent, stable over time)
    rather than by predicting a pick."""
    per_session: dict[str, dict[UUID, tuple[datetime, int]]] = {"depth": {}, "breadth": {}}
    for o in knob_obs:
        if o.source == "knob" and o.key in per_session:
            prev = per_session[o.key].get(o.session_id)
            if prev is None or o.at >= prev[0]:
                per_session[o.key][o.session_id] = (o.at, int(o.value))  # the level they left it at
    out = []
    for knob, sessions in per_session.items():
        if not sessions:
            continue
        ordered = [v for _, v in sorted(sessions.values())]
        n = len(ordered)
        value = _median(ordered)
        others = cohort_levels.get(knob) or []
        cohort = _median(others) if len(others) >= 3 else 50.0
        offset = value - cohort
        spread = max(ordered) - min(ordered)
        half = n // 2
        drift = abs(_median(ordered[:half]) - _median(ordered[half:])) if half else None
        gates = {
            "sessions": Gate(ok=n >= RANGE_MIN_SESSIONS, have=f"set it in {n} sessions",
                             need=f">= {RANGE_MIN_SESSIONS}"),
            "above_cohort": Gate(ok=abs(offset) >= RANGE_MIN_OFFSET,
                                 have=f"{value:.0f} vs {cohort:.0f} for other learners",
                                 need=f"differs by >= {RANGE_MIN_OFFSET}"),
            "consistent": Gate(ok=spread <= RANGE_MAX_SPREAD, have=f"{min(ordered)}-{max(ordered)}",
                               need=f"within {RANGE_MAX_SPREAD}"),
            "over_time": Gate(ok=drift is not None and drift <= RANGE_MAX_DRIFT,
                              have="-" if drift is None else f"earlier vs later differ by {drift:.0f}",
                              need=f"<= {RANGE_MAX_DRIFT}"),
        }
        if all(g.ok for g in gates.values()):
            status: Status = "confirmed"
        elif n >= 2 and abs(offset) >= RANGE_MIN_OFFSET:
            status = "emerging"
        else:
            continue
        word = {"depth": ("rigorous", "a gist"), "breadth": ("wide", "focused")}[knob]
        lean = word[0] if offset > 0 else word[1]
        out.append(StylePattern(
            kind="range", key=knob, status=status, gates=gates, n=n, sessions=n, value=round(value),
            statement=f"Sets {knob} around {value:.0f}/100 ({min(ordered)}-{max(ordered)}) -- leans {lean}.",
        ))
    return out


_ORDER = {"confirmed": 0, "fading": 1, "emerging": 2}


def find_patterns(picks: list[StylePick], knob_obs: Iterable[Observation], cohort_way_in: dict[str, int],
                  cohort_then: dict[str, dict[str, int]], cohort_levels: dict[str, list[int]]) -> list[StylePattern]:
    """Pure: everything layer 3 concludes, strongest first."""
    found = pick_patterns(picks, cohort_way_in, cohort_then) + range_patterns(knob_obs, cohort_levels)
    return sorted(found, key=lambda p: (_ORDER[p.status], {"way_in": 0, "then": 1, "range": 2}[p.kind]))


# ------------------------------------------------------------------ reading


class StyleReader:
    """Read-only: fetches, then `find_patterns`. No table, nothing written."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def patterns(self, learner_id: UUID) -> list[StylePattern]:
        past = await PredictionStore(self._pool).past_picks(learner_id)
        rows = await self._pool.fetch(
            "SELECT session_id, turn_number, question_author::text AS question_author, question_embedding "
            "FROM interactions WHERE learner_id = $1 ORDER BY created_at, turn_number",
            learner_id,
        )
        # A turn started by taking a card carries the card's text, not a
        # question of their own: it belongs to the topic they were on.
        picked = {(r["session_id"], r["next_turn_index"]) for r in await self._pool.fetch(
            "SELECT s.session_id, e.next_turn_index FROM direction_events e "
            "JOIN direction_sets s ON s.id = e.set_id JOIN sessions se ON se.id = s.session_id "
            "WHERE se.learner_id = $1 AND e.kind = 'picked'", learner_id)}
        rows = [dict(r, question_author="card") if (r["session_id"], r["turn_number"]) in picked else dict(r)
                for r in rows]
        topics = assign_topics(rows, await InteractionStore(self._pool).question_centre())
        knob_obs = await ObservationReader(self._pool).for_learner(learner_id, ("knob",))
        way_in, then = await self._cohort_picks(learner_id)
        levels = await self._cohort_levels(learner_id)
        return find_patterns(style_picks(past, topics), knob_obs, way_in, then, levels)

    async def _cohort_picks(self, learner_id: UUID) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
        """Everyone else's first-pick and next-pick counts (the default for
        "people like them" -- for now, all other learners)."""
        rows = await self._pool.fetch(
            "WITH sets AS ("
            "  SELECT se.learner_id, e.kind, c.slot, "
            "         LAG(c.slot) OVER (PARTITION BY s.session_id ORDER BY s.turn_index, s.created_at) AS prev_slot "
            "  FROM direction_sets s JOIN sessions se ON se.id = s.session_id "
            "  JOIN direction_events e ON e.set_id = s.id LEFT JOIN direction_cards c ON c.id = e.card_id"
            ") SELECT prev_slot, slot, count(*) AS n FROM sets "
            "WHERE kind = 'picked' AND learner_id IS DISTINCT FROM $1 GROUP BY prev_slot, slot",
            learner_id,
        )
        way_in: dict[str, int] = {}
        then: dict[str, dict[str, int]] = {}
        for r in rows:
            if r["prev_slot"] is None:
                way_in[r["slot"]] = way_in.get(r["slot"], 0) + r["n"]
            else:
                then.setdefault(r["prev_slot"], {})[r["slot"]] = r["n"]
        return way_in, then

    async def _cohort_levels(self, learner_id: UUID) -> dict[str, list[int]]:
        """The level every other learner left each slider at, per session
        they moved it in."""
        rows = await self._pool.fetch(
            "SELECT DISTINCT ON (k.session_id) k.session_id, k.from_depth, k.to_depth, k.from_breadth, "
            "k.to_breadth FROM knob_events k JOIN sessions se ON se.id = k.session_id "
            "WHERE se.learner_id IS DISTINCT FROM $1 ORDER BY k.session_id, k.created_at DESC",
            learner_id,
        )
        out: dict[str, list[int]] = {"depth": [], "breadth": []}
        for r in rows:
            for knob, levels in out.items():
                if r[f"to_{knob}"] != 50 or r[f"from_{knob}"] != 50:
                    levels.append(r[f"to_{knob}"])
        return out
