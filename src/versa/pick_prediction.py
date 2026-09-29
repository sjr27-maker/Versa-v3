"""Guessing the learner's next direction, before they pick it (migration 085).

The proof clock for the core claim (docs/THINKING_STYLE.md): if Versa is
learning how someone thinks, it should get better at telling, before a set
of "where this could go" cards is shown, which one they will take. So every
set gets a prediction, written BEFORE the cards are sent -- it can never see
the pick -- and after a pick the learner is told whether Versa guessed it,
with what the guess rested on. They see it learn them.

Deliberately arithmetic, not a model call: it adds milliseconds, not
seconds, and every contribution is an exact, named number that can be read
back -- "what contributes to what must be seen". Nothing here changes the
cards: the set is generated and shuffled exactly as before (invariant 14),
so a pick stays clean evidence; the guess is only revealed after it.

v1 blends, per slot:
  - everyone else's picks (the default for "people like them" -- for now,
    all other learners);
  - this learner's own picks, recent ones counting more;
  - their order of approach: what they took after the slot they just took;
  - how they open a chat: their first pick in a session.
Each of their own sources is trusted in proportion to how much of it there
is. Picks that say less about how they think count for less, by name: a tap
too quick to have read the cards, a tap on the first card shown, a pick
right after a question they were stuck on (ability), and picks from a
session they were rushing through (mood) -- the last two read from the
observation ledger (observations.py).

Append-only (CLAUDE.md invariant 20).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa.directions import SLOTS
from versa.observations import QUICK_TAP_MS, ObservationReader

PREDICTOR_VERSION = "v1"
_SLOTS: tuple[str, ...] = tuple(SLOTS)  # canonical order breaks ties, so a guess is reproducible

# QUICK_TAP_MS (observations.py): a pick this soon was probably not read.
QUICK_TAP_WEIGHT = 0.3
# The card shown first gets tapped for being first, some of the time.
FIRST_CARD_WEIGHT = 0.75
# Right after a question they were stuck on, a pick is partly about the
# struggle, not their way of thinking (ability).
STUCK_WEIGHT = 0.5
# A session they were rushing through says less about them (mood).
RUSHED_WEIGHT = 0.6
# A pick 20 picks ago counts half as much as the latest one.
RECENCY_HALF_LIFE = 20
# Everyone else's picks count as this many of the learner's own (a prior):
# at K picks their own weigh as much as the crowd, and more after.
OWN_PICKS_K = 5.0
CONTEXT_K = 3.0
# The running record the learner sees: over their last N guessed picks.
RECORD_WINDOW = 10


def slot_label(slot: str) -> str:
    return SLOTS[slot].split(" -- ")[0]


@dataclass(frozen=True)
class PastPick:
    """One of the learner's earlier picks, oldest first."""

    session_id: UUID
    slot: str
    position: int
    elapsed_ms: int
    # the slot they picked from the set just before this one in the same
    # session, when that set was picked too (else None)
    prev_slot: str | None
    first_in_session: bool
    # the answered turn the set followed, and the ledger's flags for it
    turn_index: int = -1
    stuck: bool = False
    rushed: bool = False
    # when they took it (layer 3 reads patterns over time)
    at: datetime | None = None


class Prediction(BaseModel):
    predicted_slot: str
    scores: dict[str, float]
    contributions: dict
    evidence_count: int
    predictor_version: str = PREDICTOR_VERSION


def _normalise(counts: dict[str, float]) -> dict[str, float]:
    """Counts -> a distribution over every slot, with one pseudo-pick each so
    an unseen slot is unlikely, never impossible."""
    total = sum(counts.values()) + len(_SLOTS)
    return {s: (counts.get(s, 0.0) + 1.0) / total for s in _SLOTS}


def _pick_weight(pick: PastPick) -> tuple[float, list[str]]:
    weight, why = 1.0, []
    if pick.elapsed_ms < QUICK_TAP_MS:
        weight *= QUICK_TAP_WEIGHT
        why.append("quick_tap")
    if pick.position == 0:
        weight *= FIRST_CARD_WEIGHT
        why.append("first_card")
    if pick.stuck:
        weight *= STUCK_WEIGHT
        why.append("stuck")
    if pick.rushed:
        weight *= RUSHED_WEIGHT
        why.append("rushed")
    return weight, why


def predict(
    past: list[PastPick], population: dict[str, int], *, prev_slot: str | None, first_in_session: bool,
) -> Prediction:
    """Pure: the same inputs always give the same guess and the same
    breakdown. `past` is this learner's picks, oldest first; `population`
    is everyone else's pick counts per slot."""
    n = len(past)
    own: dict[str, float] = {}
    after: dict[str, float] = {}
    opening: dict[str, float] = {}
    set_aside = {"quick_tap": 0, "first_card": 0, "stuck": 0, "rushed": 0}
    raw_own: dict[str, int] = {}
    raw_after: dict[str, int] = {}
    raw_opening: dict[str, int] = {}
    for i, pick in enumerate(past):
        weight, why = _pick_weight(pick)
        for reason in why:
            set_aside[reason] += 1
        weight *= 0.5 ** ((n - 1 - i) / RECENCY_HALF_LIFE)
        own[pick.slot] = own.get(pick.slot, 0.0) + weight
        raw_own[pick.slot] = raw_own.get(pick.slot, 0) + 1
        if prev_slot is not None and pick.prev_slot == prev_slot:
            after[pick.slot] = after.get(pick.slot, 0.0) + weight
            raw_after[pick.slot] = raw_after.get(pick.slot, 0) + 1
        if first_in_session and pick.first_in_session:
            opening[pick.slot] = opening.get(pick.slot, 0.0) + weight
            raw_opening[pick.slot] = raw_opening.get(pick.slot, 0) + 1

    # Everyone else's picks are the prior, worth OWN_PICKS_K picks; the
    # learner's own (weighted) picks are added on top. So after K picks
    # their own choices outweigh the crowd, however large the crowd is.
    everyone = _normalise({s: float(c) for s, c in population.items()})
    own_total = sum(own.values())
    blend = {s: (own.get(s, 0.0) + OWN_PICKS_K * everyone[s]) / (own_total + OWN_PICKS_K) for s in _SLOTS}
    w_own = own_total / (own_total + OWN_PICKS_K)

    # Then the situation they're in -- right after a slot, or opening a
    # chat -- the same way, with the blend above as its prior.
    context_name, context, context_raw = None, {}, {}
    if prev_slot is not None and raw_after:
        context_name, context, context_raw = "order", after, raw_after
    elif first_in_session and raw_opening:
        context_name, context, context_raw = "opening", opening, raw_opening
    w_ctx = 0.0
    if context_name is not None:
        ctx_total = sum(context.values())
        w_ctx = ctx_total / (ctx_total + CONTEXT_K)
        blend = {s: (context.get(s, 0.0) + CONTEXT_K * blend[s]) / (ctx_total + CONTEXT_K) for s in _SLOTS}

    predicted = max(_SLOTS, key=lambda s: (blend[s], -_SLOTS.index(s)))
    contributions = {
        "everyone": {
            "weight": round((1 - w_own) * (1 - w_ctx), 4),
            "counts": {s: population.get(s, 0) for s in _SLOTS},
        },
        "your_picks": {
            "weight": round(w_own * (1 - w_ctx), 4),
            "counts": {s: raw_own.get(s, 0) for s in _SLOTS},
            "picks": n,
        },
        "context": None if context_name is None else {
            "kind": context_name,
            "after_slot": prev_slot if context_name == "order" else None,
            "weight": round(w_ctx, 4),
            "counts": {s: context_raw.get(s, 0) for s in _SLOTS},
        },
        "set_aside": set_aside,
    }
    return Prediction(
        predicted_slot=predicted,
        scores={s: round(blend[s], 4) for s in _SLOTS},
        contributions=contributions,
        evidence_count=n,
    )


def explain(prediction: Prediction) -> list[str]:
    """What the guess rested on, in plain words, strongest first -- read
    straight off the stored numbers, so it can never say more than they do."""
    c = prediction.contributions
    slot = prediction.predicted_slot
    label = slot_label(slot)
    lines: list[tuple[float, str]] = []
    own = c["your_picks"]
    if own["picks"]:
        lines.append((own["weight"],
                      f"You took “{label}” {own['counts'][slot]} of your {own['picks']} picks so far."))
    else:
        lines.append((0.0, "Versa hasn't seen you pick yet, so this is its starting guess."))
    ctx = c.get("context")
    if ctx:
        total = sum(ctx["counts"].values())
        if ctx["kind"] == "order":
            lines.append((ctx["weight"], (
                f"Right after “{slot_label(ctx['after_slot'])}”, you went to "
                f"“{label}” {ctx['counts'][slot]} of {total} times."
            )))
        else:
            lines.append((ctx["weight"],
                          f"You opened a chat with “{label}” {ctx['counts'][slot]} of {total} times."))
    everyone = c["everyone"]
    total_everyone = sum(everyone["counts"].values())
    if total_everyone:
        share = round(100 * everyone["counts"][slot] / total_everyone)
        lines.append((everyone["weight"], f"Other learners take “{label}” {share}% of the time."))
    lines.sort(key=lambda pair: -pair[0])
    out = [text for _, text in lines]
    if not own["picks"]:  # say first that this is a starting guess
        out.sort(key=lambda line: not line.startswith("Versa hasn't"))
    aside = c["set_aside"]
    parts = []
    if aside["quick_tap"]:
        parts.append(f"{aside['quick_tap']} tap{'s' if aside['quick_tap'] != 1 else ''} too quick to have read the cards")
    if aside["first_card"]:
        parts.append(f"{aside['first_card']} tap{'s' if aside['first_card'] != 1 else ''} on the first card shown")
    if aside.get("stuck"):
        parts.append(f"{aside['stuck']} pick{'s' if aside['stuck'] != 1 else ''} right after a question "
                     "you were stuck on")
    if aside.get("rushed"):
        parts.append(f"{aside['rushed']} pick{'s' if aside['rushed'] != 1 else ''} from a session you "
                     "were rushing through")
    if parts:
        out.append("Counted for less: " + "; ".join(parts) + ".")
    return out


# ----------------------------------------------- shaping the answer (layer 4)

# Answers lean toward a learner's usual way into an idea only once it is
# clear: enough fresh starts (the first card they took after a question of
# their own), and one way in well above chance (1 in 6) among them.
PROFILE_MIN_STARTS = 4
PROFILE_MIN_SHARE = 0.35
# ... and a second step only when they took it after the first most times.
PROFILE_MIN_FOLLOWS = 2
PROFILE_MIN_FOLLOW_SHARE = 0.5

# What each slot asks of an answer, as a part of it.
_ANSWER_PART = {
    "intuition": "a simple everyday picture or analogy of the idea",
    "example": "one small, concrete worked example",
    "why": "why it works -- the mechanism underneath",
    "use": "where it is actually used",
    "deeper": "a more rigorous version",
    "next": "the related idea it leads to",
}


class ApproachProfile(BaseModel):
    """How a learner usually moves through an idea, read off their own picks:
    where they go first, and (when clear) where they go after that."""

    path: list[str]
    # fresh starts it rests on, and how many took each way in
    starts: int
    first_share: float
    counts: dict[str, int]
    # what they took right after path[0]
    follows: dict[str, int]


def approach_profile(past: list[PastPick]) -> ApproachProfile | None:
    """Pure. None until the learner's way in is clear enough to act on -- an
    answer is never shaped from a hunch. The way in is read only from fresh
    starts (a pick with no pick just before it: the first move after a
    question of their own), since that is what an answer to a new question
    should open with; the second step from what they took right after it.
    Same weighting as the guess (quick taps and first-card taps count for
    less, recent picks more), so the answer and the guess rest on the same
    reading."""
    starts = [(i, p) for i, p in enumerate(past) if p.prev_slot is None]
    if len(starts) < PROFILE_MIN_STARTS:
        return None
    n = len(past)
    weighted: dict[str, float] = {}
    counts: dict[str, int] = {}
    for i, pick in starts:
        weight, _ = _pick_weight(pick)
        weight *= 0.5 ** ((n - 1 - i) / RECENCY_HALF_LIFE)
        weighted[pick.slot] = weighted.get(pick.slot, 0.0) + weight
        counts[pick.slot] = counts.get(pick.slot, 0) + 1
    shares = _normalise(weighted)
    first = max(_SLOTS, key=lambda s: (shares[s], -_SLOTS.index(s)))
    if shares[first] < PROFILE_MIN_SHARE:
        return None
    follows: dict[str, int] = {}
    for pick in past:
        if pick.prev_slot == first:
            follows[pick.slot] = follows.get(pick.slot, 0) + 1
    path = [first]
    if follows:
        second = max(follows, key=lambda s: (follows[s], -_SLOTS.index(s)))
        total = sum(follows.values())
        if (second != first and follows[second] >= PROFILE_MIN_FOLLOWS
                and follows[second] / total >= PROFILE_MIN_FOLLOW_SHARE):
            path.append(second)
    return ApproachProfile(
        path=path, starts=len(starts), first_share=round(shares[first], 4),
        counts={s: counts.get(s, 0) for s in _SLOTS}, follows={s: follows.get(s, 0) for s in _SLOTS},
    )


def render_approach_directive(profile: ApproachProfile | None, noun: str = "student") -> str:
    """The FinalAnswer prompt block, or '' (so the prompt is unchanged) when
    there is no clear profile."""
    if profile is None:
        return ""
    parts = [_ANSWER_PART[s] for s in profile.path]
    order = parts[0] if len(parts) == 1 else f"{parts[0]}, then {parts[1]}"
    return (
        f"\nHow this {noun} likes to move through an idea -- learned from {profile.starts} of their "
        "own choices of where to go after an answer, not from anything they said: shape this "
        f"answer so it starts with {order}. Keep it natural and still answer exactly what they asked; do not "
        "mention that you are doing this.\n"
    )


def explain_profile(profile: ApproachProfile) -> list[str]:
    """Why the answer was shaped this way, in plain words, from the numbers."""
    first = profile.path[0]
    lines = [(
        f"After an answer to your own question, you went to “{slot_label(first)}” first "
        f"{profile.counts[first]} of {profile.starts} times -- more than any other way in."
    )]
    if len(profile.path) > 1:
        second = profile.path[1]
        lines.append(
            f"After “{slot_label(first)}”, you went to “{slot_label(second)}” "
            f"{profile.follows[second]} of {sum(profile.follows.values())} times."
        )
    return lines


# ----------------------------------------------------------------- store


class PredictionStore:
    """Migration 085. Append-only (CLAUDE.md invariant 20): insert and read
    only -- no delete/remove/update method, no DELETE or UPDATE SQL. Whether
    a guess was right is never stored: it is derived from the set's event."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def past_picks(self, learner_id: UUID) -> list[PastPick]:
        rows = await self._pool.fetch(
            "WITH sets AS ("
            "  SELECT s.id, s.session_id, s.turn_index, s.created_at, e.kind, e.elapsed_ms, e.created_at AS picked_at, "
            "         c.slot, c.position, "
            "         ROW_NUMBER() OVER (PARTITION BY s.session_id ORDER BY s.turn_index, s.created_at) AS n, "
            "         LAG(c.slot) OVER (PARTITION BY s.session_id ORDER BY s.turn_index, s.created_at) AS prev_slot "
            "  FROM direction_sets s JOIN sessions se ON se.id = s.session_id "
            "  JOIN direction_events e ON e.set_id = s.id "
            "  LEFT JOIN direction_cards c ON c.id = e.card_id "
            "  WHERE se.learner_id = $1"
            ") SELECT * FROM sets WHERE kind = 'picked' ORDER BY created_at, id",
            learner_id,
        )
        flags = await ObservationReader(self._pool).turn_flags(learner_id) if rows else None
        return [
            PastPick(
                session_id=r["session_id"], slot=r["slot"], position=r["position"],
                elapsed_ms=r["elapsed_ms"], prev_slot=r["prev_slot"], first_in_session=r["n"] == 1,
                turn_index=r["turn_index"],
                stuck=(r["session_id"], r["turn_index"]) in flags.stuck,
                rushed=r["session_id"] in flags.rushed,
                at=r["picked_at"],
            )
            for r in rows
        ]

    async def approach_profile(self, learner_id: UUID) -> ApproachProfile | None:
        """How this learner usually moves through an idea, or None while it
        isn't clear yet. One query, no model call."""
        return approach_profile(await self.past_picks(learner_id))

    async def population(self, exclude_learner: UUID) -> dict[str, int]:
        rows = await self._pool.fetch(
            "SELECT c.slot, count(*) AS n FROM direction_events e "
            "JOIN direction_cards c ON c.id = e.card_id "
            "JOIN direction_sets s ON s.id = e.set_id JOIN sessions se ON se.id = s.session_id "
            "WHERE e.kind = 'picked' AND se.learner_id IS DISTINCT FROM $1 GROUP BY c.slot",
            exclude_learner,
        )
        return {r["slot"]: r["n"] for r in rows}

    async def context(self, session_id: UUID, set_id: UUID) -> tuple[str | None, bool]:
        """(the slot picked from the settled set just before `set_id` in this
        session if it was picked, whether `set_id` is the session's first
        set). Sets never settled -- replaced when a slider re-pitched the
        answer -- are skipped, the same as `past_picks` never sees them."""
        rows = await self._pool.fetch(
            "SELECT s.id, e.kind, c.slot FROM direction_sets s "
            "LEFT JOIN direction_events e ON e.set_id = s.id "
            "LEFT JOIN direction_cards c ON c.id = e.card_id "
            "WHERE s.session_id = $1 ORDER BY s.turn_index, s.created_at",
            session_id,
        )
        ids = [r["id"] for r in rows]
        if set_id not in ids:
            return None, False
        earlier = [r for r in rows[: ids.index(set_id)] if r["kind"] is not None]
        if not earlier:
            return None, True
        before = earlier[-1]
        return (before["slot"] if before["kind"] == "picked" else None), False

    async def predict_for_set(self, *, learner_id: UUID, session_id: UUID, set_id: UUID) -> Prediction:
        """Guess the pick for a set that has just been written and not yet
        sent, and record the guess."""
        past = await self.past_picks(learner_id)
        population = await self.population(learner_id)
        prev_slot, first = await self.context(session_id, set_id)
        prediction = predict(past, population, prev_slot=prev_slot, first_in_session=first)
        await self._pool.execute(
            "INSERT INTO direction_predictions (id, set_id, learner_id, predicted_slot, scores, "
            "contributions, evidence_count, predictor_version) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            uuid4(), set_id, learner_id, prediction.predicted_slot, prediction.scores,
            prediction.contributions, prediction.evidence_count, prediction.predictor_version,
        )
        return prediction

    async def get(self, set_id: UUID) -> Prediction | None:
        row = await self._pool.fetchrow(
            "SELECT predicted_slot, scores, contributions, evidence_count, predictor_version "
            "FROM direction_predictions WHERE set_id = $1",
            set_id,
        )
        if row is None:
            return None
        return Prediction(
            predicted_slot=row["predicted_slot"], scores=row["scores"],
            contributions=row["contributions"], evidence_count=row["evidence_count"],
            predictor_version=row["predictor_version"],
        )

    async def record(self, learner_id: UUID, window: int = RECORD_WINDOW) -> tuple[int, int]:
        """(hits, guesses) over the learner's last `window` picked sets that
        had a guess -- derived from the events, never stored."""
        rows = await self._pool.fetch(
            "SELECT p.predicted_slot = c.slot AS hit FROM direction_predictions p "
            "JOIN direction_events e ON e.set_id = p.set_id AND e.kind = 'picked' "
            "JOIN direction_cards c ON c.id = e.card_id "
            "WHERE p.learner_id = $1 ORDER BY e.created_at DESC LIMIT $2",
            learner_id, window,
        )
        return sum(1 for r in rows if r["hit"]), len(rows)

