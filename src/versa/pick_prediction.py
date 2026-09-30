"""Guessing the learner's next direction, before they pick it (migration 085).

How the thinking style will be checked on real learners
(docs/THINKING_STYLE.md) -- nothing is claimed from it yet: if Versa is
picking up how someone approaches things, it should get better at telling, before a set
of "where this could go" cards is shown, which one they will take. So every
set gets a prediction, written BEFORE the cards are sent -- it can never see
the pick -- and after a pick the learner is told whether Versa guessed it,
with what the guess rested on. They see it learn them.

Deliberately arithmetic, not a model call: it adds milliseconds, not
seconds, and every contribution is an exact, named number that can be read
back -- "what contributes to what must be seen". Nothing here changes the
cards: the set is generated and shuffled exactly as before (invariant 14),
so a pick stays clean evidence; the guess is only revealed after it.

v2 (the card library, lib-v2): every pick is read against the hand it was
taken from, and the guess is always one of the cards about to be shown
(choice.py's preference weights). It blends:
  - an even start: before they have picked, every card is equally likely
    (v3, 2026-10-01: this used to be every other learner's choices -- no
    learner's data feeds another's any more, so only their own picks move
    the guess);
  - this learner's own choices, recent ones counting more;
  - their order of approach: what they took after the card they just took;
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

from versa.choice import Shown, among, luce_fit, win_rate, win_stats
from versa.directions import CLASSIC_SLOTS, FAMILIES, FAMILY_OF, SLOTS
from versa.observations import QUICK_TAP_MS, ObservationReader

PREDICTOR_VERSION = "v3"  # v3: an even start instead of other learners' picks
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
# The even start counts as this many of the learner's own picks (a prior):
# at K picks their own weigh as much as it, and more after.
OWN_PICKS_K = 5.0
CONTEXT_K = 3.0
# The running record the learner sees: over their last N guessed picks.
RECORD_WINDOW = 10


_FAMILY_WORDS = {
    "real": "making it real",
    "deeper": "going deeper",
    "simpler": "making it simpler",
    "wider": "going wider",
}


def slot_label(slot: str) -> str:
    if slot.startswith("family:"):
        return _FAMILY_WORDS.get(slot.removeprefix("family:"), slot)
    if slot in SLOTS:
        return SLOTS[slot].split(" -- ")[0]
    return {"wild": "a surprise direction", "path": "a two-step path"}.get(slot, slot)


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
    # the hand it was taken from (the original six for sets before lib-v2)
    offered: tuple[str, ...] = CLASSIC_SLOTS
    # the answer these cards followed was shaped to the learner's way in:
    # the pick is partly Versa's steering, so it is no evidence of style
    # (it still helps the guess, which only predicts)
    steered: bool = False


class Prediction(BaseModel):
    predicted_slot: str
    scores: dict[str, float]
    contributions: dict
    evidence_count: int
    predictor_version: str = PREDICTOR_VERSION


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


def _shown(pick: PastPick, weight: float) -> Shown:
    return Shown(offered=tuple(pick.offered), chosen=pick.slot, weight=weight)


def predict(
    past: list[PastPick], *, offered: list[str] | tuple[str, ...],
    prev_slot: str | None, first_in_session: bool,
) -> Prediction:
    """Pure: the same inputs always give the same guess and the same
    breakdown. `past` is this learner's picks, oldest first, each with the
    hand it was taken from -- the only picks it reads; `offered` the hand
    about to be shown. The guess is always one of the offered cards
    (choice.py)."""
    offered = tuple(offered)
    n = len(past)
    set_aside = {"quick_tap": 0, "first_card": 0, "stuck": 0, "rushed": 0}
    own: list[Shown] = []
    after: list[Shown] = []
    opening: list[Shown] = []
    for i, pick in enumerate(past):
        weight, why = _pick_weight(pick)
        for reason in why:
            set_aside[reason] += 1
        weight *= 0.5 ** ((n - 1 - i) / RECENCY_HALF_LIFE)
        shown = _shown(pick, weight)
        own.append(shown)
        if prev_slot is not None and pick.prev_slot == prev_slot:
            after.append(shown)
        if first_in_session and pick.first_in_session:
            opening.append(shown)

    # An even start is the prior, worth OWN_PICKS_K choices; the learner's
    # own (weighted) choices are fitted on top. So after K picks their own
    # choices outweigh it.
    own_total = sum(c.weight for c in own)
    weights = luce_fit(own, _SLOTS, prior_strength=OWN_PICKS_K)
    w_own = own_total / (own_total + OWN_PICKS_K)

    # Then the situation they are in -- right after a card, or opening a
    # chat -- the same way, with the weights above as its prior.
    context_name, context = None, []
    if prev_slot is not None and after:
        context_name, context = "order", after
    elif first_in_session and opening:
        context_name, context = "opening", opening
    w_ctx = 0.0
    if context_name is not None:
        ctx_total = sum(c.weight for c in context)
        w_ctx = ctx_total / (ctx_total + CONTEXT_K)
        weights = luce_fit(context, _SLOTS, prior=weights, prior_strength=CONTEXT_K)

    probs = among(weights, offered)
    predicted = max(offered, key=lambda s: (probs[s], -_SLOTS.index(s) if s in _SLOTS else 0))

    def tally(choices: list[Shown]) -> dict[str, dict[str, int]]:
        return {slot: {"offered": sum(1 for c in choices if slot in c.offered),
                       "taken": sum(1 for c in choices if c.chosen == slot)} for slot in offered}

    contributions = {
        "offered": list(offered),
        "even_start": {"weight": round((1 - w_own) * (1 - w_ctx), 4)},
        "your_picks": {"weight": round(w_own * (1 - w_ctx), 4), "tally": tally(own), "picks": n},
        "context": None if context_name is None else {
            "kind": context_name,
            "after_slot": prev_slot if context_name == "order" else None,
            "weight": round(w_ctx, 4),
            "tally": tally(context),
        },
        "set_aside": set_aside,
    }
    return Prediction(
        predicted_slot=predicted,
        scores={s: round(probs[s], 4) for s in offered},
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
    mine = own["tally"].get(slot, {"offered": 0, "taken": 0})
    if not own["picks"]:
        lines.append((0.0, "Versa hasn't seen you pick yet, so this is its starting guess."))
    elif mine["offered"]:
        lines.append((own["weight"], (f"When \u201c{label}\u201d was on offer, you took it "
                                     f"{mine['taken']} of {mine['offered']} times.")))
    else:
        lines.append((own["weight"], (f"You haven't had \u201c{label}\u201d on offer before -- "
                                     "this rests on the rest of your picks.")))
    ctx = c.get("context")
    if ctx:
        t = ctx["tally"].get(slot, {"offered": 0, "taken": 0})
        if t["offered"]:
            where = (f"Right after \u201c{slot_label(ctx['after_slot'])}\u201d" if ctx["kind"] == "order"
                     else "Opening a chat")
            lines.append((ctx["weight"], (f"{where}, with \u201c{label}\u201d on offer, you took it "
                                         f"{t['taken']} of {t['offered']} times.")))
    # (a guess made before v3 kept other learners' picks under "everyone";
    # it is shown as it was stored, never re-derived)
    every = (c.get("everyone") or {}).get("tally", {}).get(slot, {"offered": 0, "taken": 0})
    if every["offered"]:
        share = round(100 * every["taken"] / every["offered"])
        lines.append((c["everyone"]["weight"],
                      f"Other learners take \u201c{label}\u201d {share}% of the times it's offered."))
    if c.get("even_start") and own["picks"] and c["even_start"]["weight"] >= 0.3:
        lines.append((c["even_start"]["weight"],
                      "Every card starts even -- only your own picks move the guess."))
    lines.sort(key=lambda pair: -pair[0])
    out = [text for _, text in lines]
    if not own["picks"]:
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
# their own), and one way in taken well above chance whenever it was offered.
PROFILE_MIN_STARTS = 4
PROFILE_MIN_OFFERED = 2
PROFILE_MIN_SHARE = 0.35
PROFILE_CHANCE_LIFT = 1.5
# ... and a second step only when they took it after the first most times.
PROFILE_MIN_FOLLOWS = 2
PROFILE_MIN_FOLLOW_SHARE = 0.5

# What each card type asks of an answer, as a part of it.
_ANSWER_PART = {
    "intuition": "a simple everyday picture or analogy of the idea",
    "example": "one small, concrete worked example",
    "why": "why it works -- the mechanism underneath",
    "use": "where it is actually used",
    "deeper": "a more rigorous version",
    "next": "the related idea it leads to",
    "try_it": "a small thing to try or calculate themselves",
    "real_data": "the idea in real numbers or data",
    "prove_it": "a short argument for why it must be so",
    "mistake": "the common mistake people make with it",
    "visualise": "a picture of how it looks",
    "story": "the story of who figured it out",
    "summary": "the whole idea in one sentence",
    "compare": "how it differs from a similar idea",
    "connect": "the same idea in another subject",
    "debate": "where people disagree about it",
    # the family level: used when their way in is clear as a direction but
    # not yet as one card type
    "family:real": "something concrete -- a small worked example or where it is actually used",
    "family:deeper": "why it works -- the mechanism or reasoning underneath",
    "family:simpler": "the simple picture first -- an everyday analogy or the idea in one line",
    "family:wider": "how it connects -- what it leads to or where else the same idea shows up",
}


class ApproachProfile(BaseModel):
    """How a learner usually moves through an idea, read off their own picks
    against what was offered: where they go first, and (when clear) where
    they go after that."""

    path: list[str]
    # fresh starts it rests on; for the way in, how often it was on offer
    # and taken then, and its rate (pulled toward chance)
    starts: int
    first_share: float
    first_offered: int
    first_taken: int
    # after path[0]: how often the second step was on offer and taken
    follows_offered: int = 0
    follows_taken: int = 0


def _best(choices: list[Shown], min_offered: int,
          universe: tuple[str, ...] = _SLOTS) -> tuple[str, float, float, float, float] | None:
    """The card (or family) taken most above chance when offered: (slot,
    rate, chance, taken, offered), or None."""
    best = None
    for slot in universe:
        taken, offered_n, chance = win_stats(choices, slot)
        if offered_n < min_offered:
            continue
        rate = win_rate(taken, offered_n, chance)
        key = (rate / chance if chance else 0.0, -universe.index(slot))
        if best is None or key > best[0]:
            best = (key, (slot, rate, chance, taken, offered_n))
    return best[1] if best else None


_FAMILY_KEYS: tuple[str, ...] = tuple(f"family:{f}" for f in FAMILIES)


def _as_family(c: Shown) -> Shown | None:
    """The same choice one level up: the family taken, from the families on
    offer -- None for a card with no family."""
    if c.chosen not in FAMILY_OF or any(s not in FAMILY_OF for s in c.offered):
        return None
    offered = tuple(dict.fromkeys(f"family:{FAMILY_OF[s]}" for s in c.offered))
    return Shown(offered=offered, chosen=f"family:{FAMILY_OF[c.chosen]}", weight=c.weight)


def approach_profile(past: list[PastPick]) -> ApproachProfile | None:
    """Pure. None until the learner's way in is clear enough to act on -- an
    answer is never shaped from a hunch. The way in is read only from fresh
    starts (a pick with no pick just before it: the first move after a
    question of their own), against the cards that were on offer each time
    (choice.py); the second step from what they took right after it. Same
    weighting as the guess (quick taps and first-card taps count for less,
    recent picks more), so the answer and the guess rest on the same
    reading."""
    # a pick after a shaped answer would only confirm the shaping
    starts = [(i, p) for i, p in enumerate(past) if p.prev_slot is None and not p.steered]
    if len(starts) < PROFILE_MIN_STARTS:
        return None
    n = len(past)
    start_choices = [
        _shown(p, _pick_weight(p)[0] * 0.5 ** ((n - 1 - i) / RECENCY_HALF_LIFE)) for i, p in starts
    ]
    found = _best(start_choices, PROFILE_MIN_OFFERED)
    if found is not None and found[1] < max(PROFILE_MIN_SHARE, PROFILE_CHANCE_LIFT * found[2]):
        found = None
    if found is None:
        # With random hands from 16 card types, one type is on offer rarely;
        # the family (three of four are in every hand) is clear much sooner.
        return _family_profile(starts, start_choices)
    first, rate, _chance, _, _ = found
    raw_offered = sum(1 for _, p in starts if first in p.offered)
    raw_taken = sum(1 for _, p in starts if p.slot == first)
    path = [first]
    after = [_shown(p, 1.0) for p in past if p.prev_slot == first and not p.steered]
    f_offered = f_taken = 0
    second = _best(after, PROFILE_MIN_FOLLOWS)
    if second is not None:
        slot2, rate2, chance2, taken2, offered2 = second
        if (slot2 != first and taken2 >= PROFILE_MIN_FOLLOWS
                and rate2 >= max(PROFILE_MIN_FOLLOW_SHARE, PROFILE_CHANCE_LIFT * chance2)):
            path.append(slot2)
            f_offered, f_taken = int(offered2), int(taken2)
    return ApproachProfile(
        path=path, starts=len(starts), first_share=round(rate, 4),
        first_offered=raw_offered, first_taken=raw_taken,
        follows_offered=f_offered, follows_taken=f_taken,
    )


def _family_profile(starts: list[tuple[int, PastPick]], start_choices: list[Shown]) -> ApproachProfile | None:
    """The way in as a family ("making it real"), when no single card type
    is clear yet -- same gates, one level up."""
    fam_choices = [f for c in start_choices if (f := _as_family(c)) is not None]
    found = _best(fam_choices, PROFILE_MIN_OFFERED, _FAMILY_KEYS)
    if found is None:
        return None
    first, rate, chance, _, _ = found
    if rate < max(PROFILE_MIN_SHARE, PROFILE_CHANCE_LIFT * chance):
        return None
    fam = first.removeprefix("family:")
    raw = [p for _, p in starts if p.slot in FAMILY_OF and all(s in FAMILY_OF for s in p.offered)]
    return ApproachProfile(
        path=[first], starts=len(starts), first_share=round(rate, 4),
        first_offered=sum(1 for p in raw if any(FAMILY_OF[s] == fam for s in p.offered)),
        first_taken=sum(1 for p in raw if FAMILY_OF[p.slot] == fam),
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
        f"answer so it starts with {order}. Keep it natural and still answer exactly what they asked; "
        "do not mention that you are doing this.\n"
    )


def explain_profile(profile: ApproachProfile) -> list[str]:
    """Why the answer was shaped this way, in plain words, from the numbers."""
    first = profile.path[0]
    lines = [(
        f"After an answer to your own question, when \u201c{slot_label(first)}\u201d was on offer "
        f"you went there first {profile.first_taken} of {profile.first_offered} times."
    )]
    if len(profile.path) > 1:
        second = profile.path[1]
        lines.append(
            f"After \u201c{slot_label(first)}\u201d, with \u201c{slot_label(second)}\u201d on offer, "
            f"you went there {profile.follows_taken} of {profile.follows_offered} times."
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
            # a hand passed over with "other directions" (kind 'more') is not a
            # step on the path: the next hand's pick follows what came before it
            "WITH sets AS ("
            "  SELECT s.id, s.session_id, s.turn_index, s.created_at, e.kind, e.elapsed_ms, e.created_at AS picked_at, "
            "         COALESCE(c.tagged_as, c.slot) AS slot, c.position, "
            "         ARRAY(SELECT COALESCE(o.tagged_as, o.slot) FROM direction_cards o "
            "               WHERE o.set_id = s.id ORDER BY o.position) AS offered, "
            "         EXISTS (SELECT 1 FROM node_calls nc WHERE nc.session_id = s.session_id AND nc.turn_index = s.turn_index AND nc.node_name IN ('FinalAnswer', 'RegenerateAnswer') AND nc.input_json ? 'approach_directive') AS steered, "
            "         ROW_NUMBER() OVER (PARTITION BY s.session_id ORDER BY s.turn_index, s.created_at) AS n, "
            "         LAG(COALESCE(c.tagged_as, c.slot)) OVER (PARTITION BY s.session_id "
            "             ORDER BY s.turn_index, s.created_at) AS prev_slot "
            "  FROM direction_sets s JOIN sessions se ON se.id = s.session_id "
            "  JOIN direction_events e ON e.set_id = s.id AND e.kind <> 'more' "
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
                offered=tuple(r["offered"]),
                steered=r["steered"],
            )
            for r in rows
        ]

    async def approach_profile(self, learner_id: UUID) -> ApproachProfile | None:
        """How this learner usually moves through an idea, or None while it
        isn't clear yet. One query, no model call."""
        return approach_profile(await self.past_picks(learner_id))

    async def context(self, session_id: UUID, set_id: UUID) -> tuple[str | None, bool]:
        """(the slot picked from the settled set just before `set_id` in this
        session if it was picked, whether `set_id` is the session's first
        set). Sets never settled -- replaced when a slider re-pitched the
        answer -- are skipped, the same as `past_picks` never sees them."""
        rows = await self._pool.fetch(
            "SELECT s.id, e.kind, COALESCE(c.tagged_as, c.slot) AS slot FROM direction_sets s "
            "LEFT JOIN direction_events e ON e.set_id = s.id "
            "LEFT JOIN direction_cards c ON c.id = e.card_id "
            "WHERE s.session_id = $1 ORDER BY s.turn_index, s.created_at",
            session_id,
        )
        ids = [r["id"] for r in rows]
        if set_id not in ids:
            return None, False
        earlier = [r for r in rows[: ids.index(set_id)] if r["kind"] not in (None, "more")]
        if not earlier:
            return None, True
        before = earlier[-1]
        return (before["slot"] if before["kind"] == "picked" else None), False

    async def predict_for_set(self, *, learner_id: UUID, session_id: UUID, set_id: UUID) -> Prediction:
        """Guess the pick for a set that has just been written and not yet
        sent, and record the guess."""
        past = await self.past_picks(learner_id)
        prev_slot, first = await self.context(session_id, set_id)
        offered = [r["slot"] for r in await self._pool.fetch(
            "SELECT COALESCE(tagged_as, slot) AS slot FROM direction_cards WHERE set_id = $1 ORDER BY position",
            set_id)]
        prediction = predict(past, offered=offered, prev_slot=prev_slot, first_in_session=first)
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

