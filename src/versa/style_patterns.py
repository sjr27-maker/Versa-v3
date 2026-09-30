"""Layer 3 of docs/THINKING_STYLE.md: the order and pattern in how a learner
approaches topics -- which directions they choose, in what order -- read
across sessions from their own chats. That order and pattern is what Versa
calls their thinking style, and nothing more is claimed for it (2026-10-01):
the gates below filter out noise and one-offs (ability, mood, one topic,
chance) before a pattern is shown; whether it holds for real learners is
still to be seen -- every check so far ran on simulated students.

ONLY THIS LEARNER'S OWN DATA (decided 2026-10-01, for privacy): nothing
here reads another learner's picks, misses, sliders, timings or questions.
Every baseline that used to be "other learners" is now what chance would
give from the same hands (a card's share of the hand it was in), zero (no
lean, no shape, no speed difference) or the slider's default (50).

So a pattern is structured, not a sentence, and it must pass a gate for
every clause of that definition before it is called confirmed:

  free choice     built only from unsteered evidence: "where this could go"
                  picks (the cards are never shaped by beliefs, invariant
                  14) and slider moves the learner made themselves -- and
                  not picks made under an answer that was itself shaped to
                  their way in (they would only confirm the shaping)
  where / order   kinds: `way_in` (the first card after a question of their
                  own), `then` (what they take right after a given card) --
                  each read twice: per FAMILY (make it real / go deeper /
                  make it simpler / go wider: the way out of an answer; three
                  of the four are in every hand, so this firms up fast) and
                  per card type (finer, but a type is in few random hands, so
                  it needs many more picks)
  which way       `lean` (which way their picks lean in the card space --
                  concrete/abstract, deeper/simpler, wider/focused,
                  practical/theoretical -- against what each hand offered)
  limits          `range` (the depth / breadth level they set)
  mood, ability   every pick weighed by its session's reading (layer 2):
                  stuck, rushed, quick taps and first-card taps count less
  across topics   seen in >= MIN_SESSIONS sessions and >= MIN_TOPICS
                  different topics (questions told apart by the same
                  similarity entry_state uses)
  over time       clear in both the earlier and the later half of the
                  evidence; clear before but not lately = `fading`
  vs chance       every rate is judged against what a random chooser would
                  do with the same hands (the "clear" bar is CHANCE_LIFT x
                  that chance, and the out-of-sample test is against it)
  (hands vary)    every rate is "taken when it was offered", against the
                  chance of that from the hands it was in (choice.py) --
                  the card library deals random hands, so raw counts would
                  mostly measure how often a card happened to be shown
  out of sample   at each pick, the guess is made from the
                  EARLIER picks only (so choosing the best-looking card can't
                  leak into its own test), and the hits must beat chance
                  by more than luck -- an exact binomial test at
                  ALPHA, corrected for every candidate that was tried
                  (Bonferroni: 16 card types, or 4 axes). Without that
                  correction a learner choosing at random was told they had
                  a style 10-22% of the time (measured 2026-09-29).
  interests       not a pattern here -- what pulls them is not how they think

The second layer (2026-09-30: "we still haven't found anything that is
unique, it's all the top layer stuff"):

  one fact, not four   patterns that point the same way in the card space
                       ("goes first to making it real", "leans concrete",
                       "leans practical") are one tendency: the strongest
                       is the fact, the rest are its facets (`facet_of`)
  conditional          the same choice split by situation -- a topic new to
                       them vs one they've met before, stuck vs going fine,
                       opening a chat vs further in -- confirmed only when
                       BOTH sides pass every gate on their own and the
                       choice really differs between them. This is what
                       tells one person from another who shares the average.
  the shape of a chat  `shape`: how their picks move as a chat goes on --
                       deeper or simpler, more concrete or more abstract --
                       the later picks against the opening ones
  what they pass over  `passes_over`: a card type or family they rarely take
                       when it IS offered -- far below chance
  speed                `speed`: which way out they recognise fastest -- how
                       long they take to choose it against their other picks
                       (quick taps and rushed sessions left out). Speed is not
                       a choice, so it is checked by a significance test and
                       holding over time rather than by predicting a pick.

Experimenting on a miss (build item 8, migration 088):

  asks for             `asks_for`: when none of the cards matched and they
                       asked their own question on the same subject, the way
                       out that question was nearest to -- the one time Versa
                       sees what was in their mind unprompted. A fact when
                       their misses keep asking for the same way, well above
                       an even spread, and it predicts their later misses.
  follow-through       `miss_follow_through`: after a miss asked for a way,
                       was it taken when a later (random) hand offered it, and
                       did it hold in a later chat -- "keep experimenting until
                       a match is found, and see if it persists".

`emerging` means clear in their picks but not yet through every gate --
enough to shape a follow-up answer (pick_prediction.approach_profile), not
enough to be called their style.

Nothing is stored: patterns are derived on every read from append-only rows,
stamped STYLE_VERSION, and every gate is returned with its numbers so what
contributed can be seen.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from versa.choice import Shown, win_rate, win_stats
from versa.directions import AXES, COORDS, FAMILIES, FAMILY_OF, SLOTS
from versa.interactions import InteractionStore, centred_cosine
from versa.observations import Observation, ObservationReader
from versa.pick_prediction import PastPick, PredictionStore, slot_label
from versa.pick_prediction import _pick_weight as pick_weight

STYLE_VERSION = "style-v3"
_SLOTS: tuple[str, ...] = tuple(SLOTS)
_FAMILIES: tuple[str, ...] = tuple(FAMILIES)
FAMILY_LABEL = {
    "real": "making it real (an example, where it's used, trying it)",
    "deeper": "going deeper (why it works, a harder version, the proof)",
    "simpler": "making it simpler (a picture, an analogy, the one-liner)",
    "wider": "going wider (what's next, a comparison, another subject)",
}

MIN_CONTEXTS = 4
MIN_RATE = 0.35
MIN_SESSIONS = 3
MIN_TOPICS = 3
MIN_TRIALS = 5
MIN_PRIOR = 3  # picks seen before a pick is used as an out-of-sample trial
ALPHA = 0.05
# "clear" also means well above the chance of taking it from its hands
CHANCE_LIFT = 1.5

# A lean in the card space (idea 2 of "a bigger space"): the average of
# (the chosen card's coordinate - the hand's mean coordinate) on one axis.
LEAN_MIN_PICKS = 8
LEAN_MIN = 0.25
LEAN_HALF_MIN = 0.15
LEAN_MIN_PRIOR = 6
_LEAN_WORDS = {
    "concrete": ("more concrete", "more abstract"),
    "depth": ("deeper", "simpler"),
    "breadth": ("wider", "more focused"),
    "practical": ("more practical", "more theoretical"),
}

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
    kind: Literal["way_in", "then", "lean", "range", "conditional", "shape", "passes_over", "speed", "asks_for",
                  "asks_beyond"]
    key: str
    statement: str
    status: Status
    gates: dict[str, Gate]
    n: int
    sessions: int
    topics: int = 0
    rate: float | None = None
    # what the rate is judged against: chance, zero, or the default (never
    # other learners -- only this learner's own data is read)
    baseline_rate: float | None = None
    lift: float | None = None
    early_rate: float | None = None
    late_rate: float | None = None
    hits: int = 0
    trials: int = 0
    expected_hits: float = 0.0
    value: int | None = None
    version: str = STYLE_VERSION
    # What the pattern rests on, oldest first (the last EVIDENCE_LIMIT): for a
    # pick pattern, every pick in its situation -- the card taken, the hand,
    # whether it supports the pattern, which session; for a range, each
    # session's level. The Thinking-style page draws this as a sky.
    evidence: list[dict] = []
    # set when this pattern is a facet of a stronger one pointing the same
    # way ("<kind>:<key>" of that fact); None for a fact of its own
    facet_of: str | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass(frozen=True)
class StylePick:
    session_id: UUID
    at: datetime
    slot: str
    prev_slot: str | None
    weight: float
    topic: str
    # the hand it was taken from
    offered: tuple[str, ...] = ()
    # the card actually taken, when `slot` is its family (family patterns)
    card: str = ""
    # layer 2: right after a question they were stuck on (for conditionals)
    stuck: bool = False
    # how long they took to choose, and whether their session was rushed
    elapsed_ms: int = 0
    rushed: bool = False


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
    cascading-merge failure interactions.py documents). A turn that isn't
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
                  weight=pick_weight(p)[0], topic=topics.get((p.session_id, p.turn_index), f"s:{p.session_id}"),
                  offered=tuple(p.offered), stuck=p.stuck, elapsed_ms=p.elapsed_ms, rushed=p.rushed)
        for p in past if p.at is not None and not p.steered
    ]


# ------------------------------------------------------- the pick patterns


def beats_luck(hits: int, trials: int, rate: float, candidates: int) -> float:
    """P(at least `hits` of `trials` right at `rate`) -- the exact binomial
    tail -- times the number of candidates tried (Bonferroni). Below ALPHA:
    this many hits is very unlikely to be luck."""
    if trials == 0:
        return 1.0
    rate = min(max(rate, 1e-9), 1 - 1e-9)
    tail = sum(math.comb(trials, k) * rate**k * (1 - rate) ** (trials - k) for k in range(hits, trials + 1))
    return min(1.0, tail * candidates)


def _shown(picks: Iterable[StylePick]) -> list[Shown]:
    return [Shown(offered=p.offered, chosen=p.slot, weight=p.weight) for p in picks]


def _rate(picks: list[StylePick], slot: str) -> tuple[float, float, float]:
    """(rate taken when offered -- pulled toward chance --, chance, weighted
    times offered)."""
    taken, offered, chance = win_stats(_shown(picks), slot)
    return win_rate(taken, offered, chance), chance, offered


def _best_slot(picks: list[StylePick], universe: tuple[str, ...] = _SLOTS) -> str | None:
    """The card (or family) taken most above chance when it was offered."""
    best = None
    for slot in universe:
        rate, chance, offered = _rate(picks, slot)
        if offered < 2 or not chance:
            continue
        key = (rate / chance, -universe.index(slot))
        if best is None or key > best[0]:
            best = (key, slot)
    return best[1] if best else None


def evaluate(kind: str, key: str, context: list[StylePick], slot: str,
             statement: str, universe: tuple[str, ...] = _SLOTS, tests: int = 1) -> StylePattern | None:
    """Every gate for one candidate pattern: `context` is every pick made in
    the situation the pattern is about (e.g. every first pick after a
    question of their own), `slot` the card it says they go to -- judged
    only on the hands it was offered in, against chance from those hands."""
    context = sorted(context, key=lambda p: p.at)
    exposures = [p for p in context if slot in p.offered]
    n = len(exposures)
    if n == 0:
        return None
    rate, chance, weight = _rate(exposures, slot)
    bar = max(MIN_RATE, CHANCE_LIFT * chance)
    support = [p for p in exposures if p.slot == slot]
    sessions = len({p.session_id for p in support})
    topics = len({p.topic for p in support})
    lift = rate / chance if chance else 0.0
    half = n // 2
    early = _rate(exposures[:half], slot)[0] if half else None
    late = _rate(exposures[half:], slot)[0] if half else None
    # Out of sample: walk the situation in time; at each pick, the card
    # picked out from the EARLIER picks alone is the guess. A trial counts for
    # this pattern when that guess is this card and it is in the hand.
    hits = trials = 0
    expected = 0.0
    for i, pick in enumerate(context):
        if slot not in pick.offered:
            continue
        prior = context[:i]
        prior_exposed = [q for q in prior if slot in q.offered]
        if len(prior_exposed) < MIN_PRIOR or _best_slot(prior, universe) != slot:
            continue
        if _rate(prior_exposed, slot)[0] < bar:
            continue
        trials += 1
        hits += pick.slot == slot
        expected += 1 / len(pick.offered)  # a random chooser, from this hand
    luck = beats_luck(hits, trials, expected / trials if trials else chance, len(universe) * tests)
    gates = {
        # weighted: quick taps, rushed or stuck picks count as less evidence
        "evidence": Gate(ok=weight >= MIN_CONTEXTS,
                         have=f"offered {n} times in this situation (counting as {weight:.1f})",
                         need=f">= {MIN_CONTEXTS}"),
        "clear": Gate(ok=rate >= bar, have=f"taken {rate:.0%} of the times offered (chance {chance:.0%})",
                      need=f">= {bar:.0%}"),
        "sessions": Gate(ok=sessions >= MIN_SESSIONS, have=f"{sessions} sessions", need=f">= {MIN_SESSIONS}"),
        "topics": Gate(ok=topics >= MIN_TOPICS, have=f"{topics} different topics", need=f">= {MIN_TOPICS}"),
        "over_time": Gate(ok=early is not None and early >= bar and late >= bar,
                          have="-" if early is None else f"earlier {early:.0%}, later {late:.0%}",
                          need=f"both >= {bar:.0%}"),
        "predicts": Gate(ok=trials >= MIN_TRIALS and luck < ALPHA,
                         have=(f"{hits} of {trials} later picks guessed from earlier ones "
                               f"(chance: {expected:.1f}; luck p={luck:.3f})")
                         if trials else "no later picks yet",
                         need=f">= {MIN_TRIALS} guesses, better than luck (p < {ALPHA})"),
    }
    if all(g.ok for g in gates.values()):
        status: Status = "confirmed"
    elif weight >= 2 * MIN_CONTEXTS and early is not None and early >= bar and late < bar:
        status = "fading"
    elif gates["evidence"].ok and gates["clear"].ok:
        status = "emerging"
    else:
        return None
    return StylePattern(
        kind=kind, key=key, statement=statement, status=status, gates=gates, n=n, sessions=sessions,
        topics=topics, rate=round(rate, 4), baseline_rate=round(chance, 4), lift=round(lift, 3),
        early_rate=None if early is None else round(early, 4), late_rate=None if late is None else round(late, 4),
        hits=hits, trials=trials, expected_hits=round(expected, 3),
        evidence=[_evidence(p, p.slot == slot) for p in exposures[-EVIDENCE_LIMIT:]],
    )


EVIDENCE_LIMIT = 80


def _evidence(p: StylePick, supports: bool) -> dict:
    """One pick as the constellation draws it -- the real card (not its
    family), the hand, whether it supports the pattern."""
    return {"card": p.card or p.slot, "offered": list(p.offered), "supports": supports,
            "session": str(p.session_id), "at": p.at.isoformat() if p.at else None}


def _as_families(picks: list[StylePick]) -> list[StylePick]:
    """The same picks one level up: the family taken, from the families
    offered."""
    return [
        StylePick(session_id=p.session_id, at=p.at, slot=FAMILY_OF[p.slot],
                  prev_slot=FAMILY_OF.get(p.prev_slot) if p.prev_slot else None, weight=p.weight,
                  topic=p.topic, offered=tuple(dict.fromkeys(FAMILY_OF[s] for s in p.offered)), card=p.slot,
                  stuck=p.stuck, elapsed_ms=p.elapsed_ms, rushed=p.rushed)
        for p in picks if p.slot in FAMILY_OF and all(s in FAMILY_OF for s in p.offered)
    ]


def family_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """Which way out of an answer they go -- the family level of way_in and
    then (keys "family:<name>" and "family:<a>><b>")."""
    out = []
    fam_picks = _as_families(picks)
    starts = [p for p in fam_picks if p.prev_slot is None]
    fam = _best_slot(starts, _FAMILIES)
    if fam:
        found = evaluate("way_in", f"family:{fam}", starts, fam,
                         f"On a question of their own, goes first to {FAMILY_LABEL[fam]}.", _FAMILIES)
        if found:
            out.append(found)
    for a in _FAMILIES:
        after = [p for p in fam_picks if p.prev_slot == a]
        b = _best_slot(after, _FAMILIES)
        if not b:
            continue
        found = evaluate("then", f"family:{a}>{b}", after, b,
                         f"After {FAMILY_LABEL[a].split(' (')[0]}, goes on to {FAMILY_LABEL[b]}.", _FAMILIES)
        if found:
            out.append(found)
    return out


def pick_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """Where they start and in what order: family level, then card level."""
    out = family_patterns(picks)
    starts = [p for p in picks if p.prev_slot is None]
    slot = _best_slot(starts)
    if slot:
        found = evaluate("way_in", slot, starts, slot,
                         f"On a question of their own, goes first to \u201c{slot_label(slot)}\u201d.")
        if found:
            out.append(found)
    for a in _SLOTS:
        after = [p for p in picks if p.prev_slot == a]
        b = _best_slot(after)
        if not b:
            continue
        found = evaluate("then", f"{a}>{b}", after, b,
                         f"After \u201c{slot_label(a)}\u201d, goes to \u201c{slot_label(b)}\u201d.")
        if found:
            out.append(found)
    return out


# -------------------------------------------------------- the lean patterns


def _delta(chosen: str, offered: tuple[str, ...], axis: int) -> float | None:
    """How far the chosen card sits from the hand's average on one axis --
    None when a card has no place in the space (e.g. an untagged card)."""
    if chosen not in COORDS or any(s not in COORDS for s in offered) or not offered:
        return None
    return COORDS[chosen][axis] - sum(COORDS[s][axis] for s in offered) / len(offered)


def _lean(items: Iterable[tuple[float, float]]) -> float | None:
    items = list(items)
    total = sum(w for _, w in items)
    return sum(d * w for d, w in items) / total if total else None


def lean_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """Which way their picks lean in the card space, one axis at a time --
    the style as a region, not a favourite card. Read against each hand
    (chosen minus the hand's average), so a random hand can't fake a lean."""
    out = []
    ordered = sorted(picks, key=lambda p: p.at)
    for i, axis in enumerate(AXES):
        rows = [(p, d) for p in ordered if (d := _delta(p.slot, p.offered, i)) is not None]
        n = len(rows)
        if n == 0:
            continue
        lean = _lean((d, p.weight) for p, d in rows)
        weight = sum(p.weight for p, _ in rows)
        if lean is None or lean == 0:
            continue
        sign = 1 if lean > 0 else -1
        support = [p for p, d in rows if d * sign > 0]
        sessions = len({p.session_id for p in support})
        topics = len({p.topic for p in support})
        half = n // 2
        early = _lean((d, p.weight) for p, d in rows[:half]) if half else None
        late = _lean((d, p.weight) for p, d in rows[half:]) if half else None
        hits = trials = 0
        expected = 0.0
        for j in range(LEAN_MIN_PRIOR, n):
            prior = _lean((d, p.weight) for p, d in rows[:j])
            # the guess's direction comes from the earlier picks alone
            if prior is None or abs(prior) < LEAN_MIN or (prior > 0) != (sign > 0):
                continue
            pick = rows[j][0]
            scores = sorted(((COORDS[s][i] * sign, s) for s in pick.offered), reverse=True)
            if len(scores) > 1 and scores[0][0] == scores[1][0]:
                continue  # the hand doesn't separate on this axis: no trial
            trials += 1
            hits += pick.slot == scores[0][1]
            expected += 1 / len(pick.offered)
        chance = expected / trials if trials else 0.0
        luck = beats_luck(hits, trials, chance, len(AXES) * 2)
        word = _LEAN_WORDS[axis][0 if sign > 0 else 1]
        gates = {
            "evidence": Gate(ok=weight >= LEAN_MIN_PICKS, have=f"{n} picks (counting as {weight:.1f})",
                             need=f">= {LEAN_MIN_PICKS}"),
            "clear": Gate(ok=abs(lean) >= LEAN_MIN, have=f"lean {lean:+.2f}", need=f"at least {LEAN_MIN} either way"),
            "sessions": Gate(ok=sessions >= MIN_SESSIONS, have=f"{sessions} sessions", need=f">= {MIN_SESSIONS}"),
            "topics": Gate(ok=topics >= MIN_TOPICS, have=f"{topics} different topics", need=f">= {MIN_TOPICS}"),
            "over_time": Gate(ok=early is not None and early * sign >= LEAN_HALF_MIN and late * sign >= LEAN_HALF_MIN,
                              have="-" if early is None else f"earlier {early:+.2f}, later {late:+.2f}",
                              need=f"both >= {LEAN_HALF_MIN} their way"),
            "predicts": Gate(ok=trials >= MIN_TRIALS and luck < ALPHA,
                             have=(f"{hits} of {trials} later picks guessed from earlier ones "
                                   f"(chance: {expected:.1f}; luck p={luck:.3f})") if trials else "no later picks yet",
                             need=f">= {MIN_TRIALS} guesses, better than luck (p < {ALPHA})"),
        }
        if all(g.ok for g in gates.values()):
            status: Status = "confirmed"
        elif weight >= 2 * LEAN_MIN_PICKS and early is not None and early * sign >= LEAN_MIN and late * sign < LEAN_HALF_MIN:
            status = "fading"
        elif gates["evidence"].ok and gates["clear"].ok:
            status = "emerging"
        else:
            continue
        out.append(StylePattern(
            kind="lean", key=axis, status=status, gates=gates, n=n, sessions=sessions, topics=topics,
            rate=round(lean, 4), baseline_rate=0.0,
            early_rate=None if early is None else round(early, 4), late_rate=None if late is None else round(late, 4),
            hits=hits, trials=trials, expected_hits=round(expected, 3),
            statement=f"Takes the {word} card on offer (lean {lean:+.2f}).",
            evidence=[_evidence(p, d * sign > 0) for p, d in rows[-EVIDENCE_LIMIT:]],
        ))
    return out


# ------------------------------------------------------- the range patterns


def _median(values: list[int]) -> float:
    v = sorted(values)
    m = len(v) // 2
    return float(v[m]) if len(v) % 2 else (v[m - 1] + v[m]) / 2


# range keys: (word when above the default, word when below, statement)
_RANGE_KEYS: dict[str, tuple[str, str, str]] = {
    "depth": ("rigorous", "a gist", "Sets depth around {value:.0f}/100 ({low}-{high}) -- leans {lean}."),
    "breadth": ("wide", "focused", "Sets breadth around {value:.0f}/100 ({low}-{high}) -- leans {lean}."),
    "explore_depth": ("deep", "near the top", ("Exploring a topic, goes {lean} into its tree "
                      "(around level {value:.0f}/100, {low}-{high}).")),
    "explore_breadth": ("most of what's offered", "a narrow few", ("Building a course, keeps {lean} "
                        "(around {value:.0f}% of the lessons, {low}-{high}%).")),
}


RANGE_DEFAULT = 50.0  # where every slider starts: the baseline a range is read against


def range_patterns(knob_obs: Iterable[Observation]) -> list[StylePattern]:
    """Where they set their own depth / breadth. Only sessions where they
    moved that slider count -- a default was never a choice. A range is set,
    not picked, so it is checked by being kept (consistent, stable over time)
    rather than by predicting a pick."""
    per_session: dict[str, dict[UUID, tuple[datetime, int]]] = {k: {} for k in _RANGE_KEYS}
    for o in knob_obs:
        if (o.source, o.key) in {("knob", "depth"), ("knob", "breadth"), ("topic", "explore_depth"),
                                 ("topic", "explore_breadth")}:
            prev = per_session[o.key].get(o.session_id)
            if o.source == "topic" and o.key == "explore_depth":
                # the deepest they went in that tree
                if prev is None or int(o.value) >= prev[1]:
                    per_session[o.key][o.session_id] = (o.at, int(o.value))
            elif prev is None or o.at >= prev[0]:
                per_session[o.key][o.session_id] = (o.at, int(o.value))  # the level they left it at
    out = []
    for knob, sessions in per_session.items():
        if not sessions:
            continue
        ordered = [v for _, v in sorted(sessions.values())]
        n = len(ordered)
        value = _median(ordered)
        offset = value - RANGE_DEFAULT
        spread = max(ordered) - min(ordered)
        half = n // 2
        drift = abs(_median(ordered[:half]) - _median(ordered[half:])) if half else None
        gates = {
            "sessions": Gate(ok=n >= RANGE_MIN_SESSIONS, have=f"set it in {n} sessions",
                             need=f">= {RANGE_MIN_SESSIONS}"),
            "away_from_default": Gate(ok=abs(offset) >= RANGE_MIN_OFFSET,
                                      have=f"{value:.0f} vs {RANGE_DEFAULT:.0f} (where it starts)",
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
        lean = _RANGE_KEYS[knob][0 if offset > 0 else 1]
        out.append(StylePattern(
            kind="range", key=knob, status=status, gates=gates, n=n, sessions=n, value=round(value),
            baseline_rate=RANGE_DEFAULT,
            statement=_RANGE_KEYS[knob][2].format(value=value, low=min(ordered), high=max(ordered), lean=lean),
            evidence=[{"value": v, "at": at.isoformat()} for at, v in sorted(sessions.values())[-EVIDENCE_LIMIT:]],
        ))
    return out


# ---------------------------------------------------- the second layer

# The situations a choice is split by: (the two sides, in words)
CONDITIONS: dict[str, tuple[str, str]] = {
    "familiarity": ("On a topic that's new to them", "on one they've met before"),
    "state": ("When they're stuck", "when it's going fine"),
    "position": ("Opening a chat", "further into a chat"),
}
# how much more often the way taken on one side is taken there than on the other
COND_MIN_DIFF = 0.25
POSITION_OPENING = 2  # the first picks of a chat count as its opening


def _sides(picks: list[StylePick], condition: str) -> list[str]:
    """For each pick (in time order), which side of `condition` it is on."""
    out = []
    if condition == "familiarity":
        # familiar: they picked on this topic in an EARLIER chat
        seen: dict[str, set[UUID]] = {}
        for p in picks:
            earlier = seen.get(p.topic, set()) - {p.session_id}
            out.append("b" if earlier else "a")
            seen.setdefault(p.topic, set()).add(p.session_id)
    elif condition == "state":
        out = ["a" if p.stuck else "b" for p in picks]
    else:
        count: dict[UUID, int] = {}
        for p in picks:
            n = count.get(p.session_id, 0)
            out.append("a" if n < POSITION_OPENING else "b")
            count[p.session_id] = n + 1
    return out


def _family_words(family: str) -> str:
    return FAMILY_LABEL[family].split(" (")[0]


def conditional_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """The same way out of an answer, split by situation. A conditional is a
    pattern only when each side holds up on its own (every gate, including
    the out-of-sample test, corrected for all the splits tried) and the
    family taken differs between the sides by at least COND_MIN_DIFF --
    otherwise it is just the plain tendency, seen twice."""
    fam = sorted(_as_families(picks), key=lambda p: p.at)
    out = []
    for condition, (word_a, word_b) in CONDITIONS.items():
        # the way in (first moves) for familiarity; every move otherwise
        pool = [p for p in fam if p.prev_slot is None] if condition == "familiarity" else fam
        sides = _sides(pool, condition)
        side_a = [p for p, v in zip(pool, sides, strict=True) if v == "a"]
        side_b = [p for p, v in zip(pool, sides, strict=True) if v == "b"]
        fa, fb = _best_slot(side_a, _FAMILIES), _best_slot(side_b, _FAMILIES)
        if not fa or not fb or fa == fb:
            continue
        tests = len(CONDITIONS) * 2
        statement = (f"{word_a}, goes to {_family_words(fa)}; {word_b}, to {_family_words(fb)}.")
        pa = evaluate("conditional", fa, side_a, fa, statement, _FAMILIES, tests)
        pb = evaluate("conditional", fb, side_b, fb, statement, _FAMILIES, tests)
        if pa is None or pb is None:
            continue
        # does the choice really change with the situation?
        a_there = _rate([p for p in side_a if fa in p.offered], fa)[0]
        a_elsewhere = _rate([p for p in side_b if fa in p.offered], fa)[0]
        b_there = _rate([p for p in side_b if fb in p.offered], fb)[0]
        b_elsewhere = _rate([p for p in side_a if fb in p.offered], fb)[0]
        differs = min(a_there - a_elsewhere, b_there - b_elsewhere)
        gates = {f"{word_a.lower()}: {name}": g for name, g in pa.gates.items()}
        gates |= {f"{word_b}: {name}": g for name, g in pb.gates.items()}
        gates["differs"] = Gate(
            ok=differs >= COND_MIN_DIFF,
            have=(f"{_family_words(fa)} {a_there:.0%} vs {a_elsewhere:.0%}; "
                  f"{_family_words(fb)} {b_there:.0%} vs {b_elsewhere:.0%}"),
            need=f"each at least {COND_MIN_DIFF:.0%} more on its own side")
        if not gates["differs"].ok:
            continue
        if pa.status == "confirmed" and pb.status == "confirmed":
            status: Status = "confirmed"
        elif "fading" in (pa.status, pb.status):
            status = "fading"
        else:
            status = "emerging"
        out.append(StylePattern(
            kind="conditional", key=f"{condition}:{fa}|{fb}", statement=statement, status=status, gates=gates,
            n=pa.n + pb.n, sessions=len({p.session_id for p in side_a + side_b}),
            topics=len({p.topic for p in side_a + side_b}), hits=pa.hits + pb.hits, trials=pa.trials + pb.trials,
            expected_hits=round(pa.expected_hits + pb.expected_hits, 3),
            evidence=(pa.evidence + pb.evidence)[-EVIDENCE_LIMIT:],
        ))
    return out


# which way in the card space a pattern points (None: it doesn't point one way)
def _direction(p: StylePattern) -> tuple[float, ...] | None:
    def of(target: str) -> tuple[float, ...] | None:
        if target in COORDS:
            return COORDS[target]
        members = [COORDS[t] for t, f in FAMILY_OF.items() if f == target]
        return tuple(sum(c[i] for c in members) / len(members) for i in range(len(AXES))) if members else None

    key = p.key.removeprefix("family:")
    if p.kind in ("way_in", "asks_for"):
        return of(key)
    if p.kind == "then":
        # an order points where it leads: "after going deeper, goes on to
        # making it real" is news only if making it real isn't already their
        # way -- when it is, it is a facet of that fact, not a fact of its own
        return of(key.partition(">")[2])
    if p.kind == "passes_over":
        d = of(key)
        return tuple(-x for x in d) if d else None
    if p.kind == "lean" and p.rate:
        return tuple((1.0 if p.rate > 0 else -1.0) if axis == p.key else 0.0 for axis in AXES)
    return None


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    na, nb = sum(x * x for x in a) ** .5, sum(x * x for x in b) ** .5
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


SAME_WAY = 0.6


def merge_facts(patterns: list[StylePattern]) -> list[StylePattern]:
    """Patterns pointing the same way are one fact: in strongest-first order,
    each joins the first fact within SAME_WAY (cosine) of it as a facet."""
    heads: list[tuple[StylePattern, tuple[float, ...]]] = []
    for p in patterns:
        d = _direction(p)
        if d is None:
            continue
        head = next((h for h, hd in heads if _cosine(d, hd) >= SAME_WAY), None)
        if head is None:
            heads.append((p, d))
        else:
            p.facet_of = head.id
    return patterns


# ---------------------------------------- the shape of a chat, passes, speed

SHAPE_MIN_CHATS = 5
SHAPE_MIN = 0.3
SHAPE_WORDS = {
    "depth": ("get deeper as a chat goes on (they start simpler)", "get simpler as a chat goes on (they start deeper)"),
    "concrete": ("get more concrete as a chat goes on", "get more abstract as a chat goes on (they start concrete)"),
}


def _chats(picks: list[StylePick]) -> list[list[StylePick]]:
    """Each chat's picks in order, oldest chat first."""
    by: dict[UUID, list[StylePick]] = {}
    for p in sorted(picks, key=lambda q: q.at):
        by.setdefault(p.session_id, []).append(p)
    return list(by.values())


def _chat_shape(chat: list[StylePick], axis: int) -> float | None:
    """Later picks against the opening ones, on one axis (each read against
    its own hand); None when the chat has no later picks."""
    early = [d for p in chat[:POSITION_OPENING] if (d := _delta(p.slot, p.offered, axis)) is not None]
    late = [d for p in chat[POSITION_OPENING:] if (d := _delta(p.slot, p.offered, axis)) is not None]
    if not early or not late:
        return None
    return sum(late) / len(late) - sum(early) / len(early)


def shape_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """How their picks move as a chat goes on; a random chooser's chats have
    no shape (0), which is the baseline."""
    out = []
    chats = _chats(picks)
    for axis_name in SHAPE_WORDS:
        axis = AXES.index(axis_name)
        rows = [(c, v) for c in chats if (v := _chat_shape(c, axis)) is not None]
        n = len(rows)
        if n == 0:
            continue
        shape = sum(v for _, v in rows) / n
        if shape == 0:
            continue
        sign = 1 if shape > 0 else -1
        support = [c for c, v in rows if v * sign > 0]
        half = n // 2
        early = sum(v for _, v in rows[:half]) / half if half else None
        late = sum(v for _, v in rows[half:]) / (n - half) if half else None
        # out of sample: once the earlier chats show the shape, guess each
        # later chat's opening as the card furthest the other way and its
        # later picks as the card furthest this way
        hits = trials = 0
        expected = 0.0
        for j in range(4, n):
            prior = sum(v for _, v in rows[:j]) / j
            if prior * sign < SHAPE_MIN:
                continue
            chat = rows[j][0]
            for k, pick in enumerate(chat):
                want = -sign if k < POSITION_OPENING else sign
                if any(s not in COORDS for s in pick.offered):
                    continue
                scores = sorted(((COORDS[s][axis] * want, s) for s in pick.offered), reverse=True)
                if len(scores) > 1 and scores[0][0] == scores[1][0]:
                    continue
                trials += 1
                hits += pick.slot == scores[0][1]
                expected += 1 / len(pick.offered)
        chance = expected / trials if trials else 0.0
        luck = beats_luck(hits, trials, chance, len(SHAPE_WORDS) * 2)
        gates = {
            "evidence": Gate(ok=n >= SHAPE_MIN_CHATS, have=f"{n} chats with an opening and later picks",
                             need=f">= {SHAPE_MIN_CHATS}"),
            "clear": Gate(ok=abs(shape) >= SHAPE_MIN, have=f"later picks {shape:+.2f} against the opening",
                          need=f"at least {SHAPE_MIN} either way"),
            "sessions": Gate(ok=len(support) >= MIN_SESSIONS, have=f"{len(support)} chats show it",
                             need=f">= {MIN_SESSIONS}"),
            "topics": Gate(ok=len({c[0].topic for c in support}) >= MIN_TOPICS,
                           have=f"{len({c[0].topic for c in support})} different topics", need=f">= {MIN_TOPICS}"),
            "over_time": Gate(ok=early is not None and early * sign >= SHAPE_MIN / 2 and late * sign >= SHAPE_MIN / 2,
                              have="-" if early is None else f"earlier chats {early:+.2f}, later chats {late:+.2f}",
                              need=f"both >= {SHAPE_MIN / 2} their way"),
            "predicts": Gate(ok=trials >= MIN_TRIALS and luck < ALPHA,
                             have=(f"{hits} of {trials} picks in later chats guessed from earlier ones "
                                   f"(chance: {expected:.1f}; luck p={luck:.3f})") if trials else "no later chats yet",
                             need=f">= {MIN_TRIALS} guesses, better than luck (p < {ALPHA})"),
        }
        status = _status(gates, n, early, late, sign, SHAPE_MIN, SHAPE_MIN_CHATS)
        if status is None:
            continue
        words = SHAPE_WORDS[axis_name][0 if sign > 0 else 1]
        out.append(StylePattern(
            kind="shape", key=axis_name, statement=f"Their picks {words}.", status=status, gates=gates, n=n,
            sessions=len(support), topics=len({c[0].topic for c in support}), rate=round(shape, 4),
            baseline_rate=0.0, hits=hits, trials=trials, expected_hits=round(expected, 3),
            early_rate=None if early is None else round(early, 4), late_rate=None if late is None else round(late, 4),
            evidence=[_evidence(p, (_delta(p.slot, p.offered, axis) or 0) * (sign if k >= POSITION_OPENING else -sign) > 0)
                      for c, _ in rows[-12:] for k, p in enumerate(c)][-EVIDENCE_LIMIT:],
        ))
    return out


def _status(gates: dict[str, Gate], n: float, early, late, sign: int, bar: float, min_n: float) -> Status | None:
    if all(g.ok for g in gates.values()):
        return "confirmed"
    if n >= 2 * min_n and early is not None and early * sign >= bar and late * sign < bar / 2:
        return "fading"
    if gates["evidence"].ok and gates["clear"].ok:
        return "emerging"
    return None


PASS_MIN_OFFERED = 8
PASS_MAX_SHARE_OF_CHANCE = 0.5  # taken at most half as often as chance would


def _takes_luck(takes: int, trials: int, rate: float, candidates: int) -> float:
    """P(at most `takes` of `trials` at `rate`) x candidates: how unlikely it
    is that they took it this rarely by chance."""
    if trials == 0:
        return 1.0
    rate = min(max(rate, 1e-9), 1 - 1e-9)
    tail = sum(math.comb(trials, k) * rate**k * (1 - rate) ** (trials - k) for k in range(takes + 1))
    return min(1.0, tail * candidates)


def passes_over_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """A card type or family they rarely take when it is on offer -- at most
    half as often as chance would. Read at both levels; one fact per target.

    Someone who nearly always takes their favourite way passes over every
    other one -- that is their way in again, not a second fact. So this is
    read with the favourite set aside: among the picks that were NOT their
    favourite, with it taken out of the hand."""
    out = []
    fams = _as_families(picks)
    fav_family = _clear_favourite(fams, _FAMILIES)
    # at card level the whole favourite family is set aside (its cards share
    # the favourite's pull), and the favourite card if it is elsewhere
    card_fav = _clear_favourite(picks, _SLOTS)
    card_set = {c for c, f in FAMILY_OF.items() if f == fav_family} | ({card_fav} if card_fav else set())
    levels = [(picks, _SLOTS, False, card_fav, card_set),
              (fams, _FAMILIES, True, fav_family, {fav_family} - {None})]
    for all_mine, universe, family, fav, aside in levels:
        mine = _without(all_mine, aside)
        for target in universe:
            if target in aside:
                continue
            exposures = sorted((p for p in mine if target in p.offered), key=lambda p: p.at)
            n = len(exposures)
            if n < PASS_MIN_OFFERED:
                continue
            taken, offered_w, chance = win_stats(_shown(exposures), target)
            rate = taken / offered_w if offered_w else 0.0
            bar = chance * PASS_MAX_SHARE_OF_CHANCE
            half = n // 2
            early = _shares(exposures[:half], target)
            late = _shares(exposures[half:], target)
            takes = trials = 0
            expected = 0.0
            for i in range(4, n):
                prior = exposures[:i]
                if _shares(prior, target) > bar:
                    continue
                trials += 1
                takes += exposures[i].slot == target
                expected += 1 / len(exposures[i].offered)  # a random chooser, from this hand
            luck = _takes_luck(takes, trials, expected / trials if trials else chance,
                               len(_SLOTS) + len(_FAMILIES))
            label = FAMILY_LABEL[target].split(" (")[0] if family else f"\u201c{slot_label(target)}\u201d"
            gates = {
                "evidence": Gate(ok=offered_w >= PASS_MIN_OFFERED, have=f"offered {n} times",
                                 need=f">= {PASS_MIN_OFFERED}"),
                "clear": Gate(ok=rate <= bar, have=f"taken {rate:.0%} of the times offered (chance {chance:.0%})",
                              need=f"at most {bar:.0%}"),
                "sessions": Gate(ok=len({p.session_id for p in exposures}) >= MIN_SESSIONS,
                                 have=f"offered in {len({p.session_id for p in exposures})} chats",
                                 need=f">= {MIN_SESSIONS}"),
                "topics": Gate(ok=len({p.topic for p in exposures}) >= MIN_TOPICS,
                               have=f"{len({p.topic for p in exposures})} different topics", need=f">= {MIN_TOPICS}"),
                "over_time": Gate(ok=half > 0 and early <= bar and late <= bar,
                                  have=f"earlier {early:.0%}, later {late:.0%}" if half else "-",
                                  need=f"both at most {bar:.0%}"),
                "predicts": Gate(ok=trials >= MIN_TRIALS and luck < ALPHA,
                                 have=(f"took it {takes} of the {trials} later times it was offered "
                                       f"(chance would have {expected:.1f}; luck p={luck:.3f})")
                                 if trials else "not offered again yet",
                                 need=f">= {MIN_TRIALS} later offers, rarer than luck (p < {ALPHA})"),
            }
            if all(g.ok for g in gates.values()):
                status: Status = "confirmed"
            elif gates["evidence"].ok and gates["clear"].ok:
                status = "emerging"
            else:
                continue
            out.append(StylePattern(
                kind="passes_over", key=f"family:{target}" if family else target, status=status, gates=gates,
                statement=(f"Passes over {label}: took it {round(taken)} of {n} times it was offered "
                           + (f"when not {_fav_label(fav_family, True)} " if fav_family else "")
                           + f"(chance would be {chance:.0%})."),
                n=n, sessions=len({p.session_id for p in exposures}), topics=len({p.topic for p in exposures}),
                rate=round(rate, 4), baseline_rate=round(chance, 4), hits=trials - takes, trials=trials,
                expected_hits=round(expected, 3),
                evidence=[_evidence(p, p.slot != target) for p in exposures[-EVIDENCE_LIMIT:]],
            ))
    return out


def _clear_favourite(picks: list[StylePick], universe: tuple[str, ...]) -> str | None:
    """Their favourite, only if it clearly is one (taken >= MIN_RATE and
    CHANCE_LIFT x chance when offered) -- a noise favourite is not set aside."""
    fav = _best_slot(picks, universe)
    if fav is None:
        return None
    rate, chance, _ = _rate(picks, fav)
    return fav if rate >= max(MIN_RATE, CHANCE_LIFT * chance) else None


def _without(picks: list[StylePick], aside: set[str]) -> list[StylePick]:
    """The picks that weren't one of `aside`, with those taken out of the hand."""
    if not aside:
        return picks
    return [replace(p, offered=tuple(o for o in p.offered if o not in aside))
            for p in picks if p.slot not in aside and sum(o not in aside for o in p.offered) >= 2]


def _shares(picks: list[StylePick], target: str) -> float:
    taken, offered, _ = win_stats(_shown(picks), target)
    return taken / offered if offered else 0.0


def _fav_label(fav: str, family: bool) -> str:
    return FAMILY_LABEL[fav].split(" (")[0] if family else f"“{slot_label(fav)}”"


SPEED_MIN_EACH = 6
SPEED_MIN_RATIO = 1.5  # at least 1.5x faster (or slower) than their other picks


def _speed_rows(picks: list[StylePick]) -> list[StylePick]:
    """Picks whose time says something: read (not a quick tap), not rushed."""
    from versa.observations import QUICK_TAP_MS

    return [p for p in picks if p.elapsed_ms >= QUICK_TAP_MS and not p.rushed and p.slot in FAMILY_OF]


def _log_gap(rows: list[StylePick], family: str) -> tuple[float, float, int, int] | None:
    """(mean log-time gap: this family minus the rest, its z-score, n, m)."""
    this = [math.log(p.elapsed_ms) for p in rows if FAMILY_OF[p.slot] == family]
    rest = [math.log(p.elapsed_ms) for p in rows if FAMILY_OF[p.slot] != family]
    if len(this) < 2 or len(rest) < 2:
        return None

    def mv(xs):
        m = sum(xs) / len(xs)
        return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    (m1, v1), (m2, v2) = mv(this), mv(rest)
    se = math.sqrt(v1 / len(this) + v2 / len(rest)) or 1e-9
    return m1 - m2, (m1 - m2) / se, len(this), len(rest)


def speed_patterns(picks: list[StylePick]) -> list[StylePattern]:
    """Which way out they recognise fastest (or slowest): the time to choose
    a family against the time for their OWN other picks, on a log scale
    (baseline: no difference)."""
    out = []
    rows = sorted(_speed_rows(picks), key=lambda p: p.at)
    for family in _FAMILIES:
        found = _log_gap(rows, family)
        if found is None:
            continue
        gap, z, n_this, n_rest = found
        ratio = math.exp(abs(gap))
        sign = -1 if gap < 0 else 1  # -1: faster
        # two-sided normal p, Bonferroni over the four families
        p_value = min(1.0, math.erfc(abs(z) / math.sqrt(2)) * len(_FAMILIES))
        half = len(rows) // 2
        e, l = _log_gap(rows[:half], family), _log_gap(rows[half:], family)
        gates = {
            "evidence": Gate(ok=n_this >= SPEED_MIN_EACH and n_rest >= SPEED_MIN_EACH,
                             have=f"{n_this} such picks, {n_rest} others (quick taps left out)",
                             need=f">= {SPEED_MIN_EACH} each"),
            "clear": Gate(ok=ratio >= SPEED_MIN_RATIO, have=f"{ratio:.1f}x {'faster' if sign < 0 else 'slower'}",
                          need=f">= {SPEED_MIN_RATIO}x"),
            "sessions": Gate(ok=len({p.session_id for p in rows if FAMILY_OF[p.slot] == family}) >= MIN_SESSIONS,
                             have=f"{len({p.session_id for p in rows if FAMILY_OF[p.slot] == family})} chats",
                             need=f">= {MIN_SESSIONS}"),
            "over_time": Gate(ok=bool(e and l and e[0] * sign > 0 and l[0] * sign > 0),
                              have=(f"earlier {math.exp(abs(e[0])):.1f}x, later {math.exp(abs(l[0])):.1f}x"
                                    if e and l else "-"), need="the same way in both halves"),
            "not_luck": Gate(ok=p_value < ALPHA, have=f"p={p_value:.3f}", need=f"< {ALPHA} (all four families tried)"),
        }
        if all(g.ok for g in gates.values()):
            status: Status = "confirmed"
        elif gates["evidence"].ok and gates["clear"].ok:
            status = "emerging"
        else:
            continue
        words = _family_words(family)
        out.append(StylePattern(
            kind="speed", key=family, status=status, gates=gates, n=n_this + n_rest,
            sessions=len({p.session_id for p in rows}),
            statement=(f"Recognises {words} {'fast' if sign < 0 else 'slowly'} -- "
                       f"about {ratio:.1f}x {'quicker' if sign < 0 else 'slower'} than their other picks."),
            rate=round(gap, 4), baseline_rate=0.0,
            evidence=[_evidence(p, FAMILY_OF[p.slot] == family) for p in rows[-EVIDENCE_LIMIT:]],
        ))
    return out


_ORDER = {"confirmed": 0, "fading": 1, "emerging": 2}


# ---------------------------------------------------- experimenting on a miss

@dataclass(frozen=True)
class MissAsk:
    """One miss: a hand they passed by asking their own question, and the
    way out that question was nearest to (None: a new subject, or not
    clearly any of the ways). `move`: when it was none of the library's
    ways, the group of new moves it belongs to (discover_moves) -- a way of
    thinking the cards don't offer yet."""
    session_id: UUID
    at: datetime
    topic: str
    asked: str | None
    in_hand: bool = False  # the type WAS on a card: its wording missed, not the hand
    move: str | None = None
    move_label: str | None = None


ASKS_MIN = 3


@dataclass
class _Ask:
    target: str
    count: int
    n: int
    share: float
    even: float
    mine: list
    hits: int
    trials: int
    early: float | None
    late: float | None
    gates: dict
    status: Status


def _ask_facts(tagged: list[MissAsk], key_of, universe: tuple[str, ...], tests: int,
               spread: int | None = None) -> list[_Ask]:
    """The checks shared by `asks_for` and `asks_beyond`: one target (a way
    out, a family, or a new move) their read misses keep asking for -- the
    share of their misses against an even spread over `spread` possible ways
    (default: `universe`), over chats and topics, in both halves, and
    guessing their later misses from earlier ones."""
    n = len(tagged)
    if n == 0 or not universe:
        return []
    counts: dict[str, int] = {}
    for m in tagged:
        counts[key_of(m)] = counts.get(key_of(m), 0) + 1
    even = 1 / (spread or len(universe))
    out = []
    for target, count in counts.items():
        share = count / n
        bar = max(MIN_RATE, CHANCE_LIFT * even)
        mine = [m for m in tagged if key_of(m) == target]
        half = n // 2
        early = sum(key_of(m) == target for m in tagged[:half]) / half if half else None
        late = sum(key_of(m) == target for m in tagged[half:]) / (n - half) if half else None
        hits = trials = 0
        for j in range(MIN_PRIOR, n):
            prior = tagged[:j]
            top = max(universe, key=lambda u: sum(key_of(m) == u for m in prior))
            if top != target or sum(key_of(m) == target for m in prior) / j < bar:
                continue
            trials += 1
            hits += key_of(tagged[j]) == target
        luck = beats_luck(hits, trials, even, tests)
        gates = {
            "evidence": Gate(ok=count >= ASKS_MIN, have=f"asked for it {count} times", need=f">= {ASKS_MIN}"),
            "clear": Gate(ok=share >= bar, have=f"{count} of their {n} read misses ({share:.0%})",
                          need=f">= {bar:.0%}"),
            "sessions": Gate(ok=len({m.session_id for m in mine}) >= MIN_SESSIONS,
                             have=f"in {len({m.session_id for m in mine})} chats", need=f">= {MIN_SESSIONS}"),
            "topics": Gate(ok=len({m.topic for m in mine}) >= MIN_TOPICS,
                           have=f"{len({m.topic for m in mine})} different topics", need=f">= {MIN_TOPICS}"),
            "over_time": Gate(ok=early is not None and early >= bar / 2 and late >= bar / 2,
                              have=f"earlier {early:.0%}, later {late:.0%}" if half else "-",
                              need=f"both >= {bar / 2:.0%}"),
            "predicts": Gate(ok=trials >= MIN_TRIALS and luck < ALPHA,
                             have=(f"{hits} of {trials} later misses asked for it, guessed from earlier ones "
                                   f"(an even spread: {even:.0%}; luck p={luck:.3f})") if trials else "no later misses yet",
                             need=f">= {MIN_TRIALS} guesses, better than luck (p < {ALPHA})"),
        }
        if all(g.ok for g in gates.values()):
            status: Status = "confirmed"
        elif gates["evidence"].ok and gates["clear"].ok:
            status = "emerging"
        else:
            continue
        out.append(_Ask(target=target, count=count, n=n, share=share, even=even, mine=mine, hits=hits,
                        trials=trials, early=early, late=late, gates=gates, status=status))
    return out


def _ask_pattern(kind: str, key: str, a: _Ask, statement: str) -> StylePattern:
    return StylePattern(
        kind=kind, key=key, status=a.status, gates=a.gates, statement=statement,
        n=a.n, sessions=len({m.session_id for m in a.mine}), topics=len({m.topic for m in a.mine}),
        rate=round(a.share, 4), baseline_rate=round(a.even, 4), hits=a.hits, trials=a.trials,
        expected_hits=round(a.even * a.trials, 3),
        early_rate=None if a.early is None else round(a.early, 4),
        late_rate=None if a.late is None else round(a.late, 4),
    )


def asks_for_patterns(misses: list[MissAsk], picks: list[StylePick] | None = None) -> list[StylePattern]:
    """The way out their own questions keep asking for when the cards miss.
    Read at card and family level; the share of their read misses against an
    even spread over the ways."""
    out = []
    tagged = sorted((m for m in misses if m.asked in FAMILY_OF), key=lambda m: m.at)
    tests = len(_SLOTS) + len(_FAMILIES)
    for family in (False, True):
        universe = _FAMILIES if family else _SLOTS

        def key_of(m: MissAsk) -> str:
            return FAMILY_OF[m.asked] if family else m.asked  # noqa: B023

        for a in _ask_facts(tagged, key_of, universe, tests):
            label = _family_words(a.target) if family else f"\u201c{slot_label(a.target)}\u201d"
            through = miss_follow_through(a.mine, picks or [], family=family) if picks is not None else None
            tail = ""
            if through and through["offered_later"]:
                tail = (f" When a later hand offered it, they took it {through['taken']} of "
                        f"{through['offered_later']} times.")
            out.append(_ask_pattern(
                "asks_for", f"family:{a.target}" if family else a.target, a,
                f"When the cards miss, asks for {label} ({a.count} of {a.n} times).{tail}"))
    return out


def asks_beyond_patterns(misses: list[MissAsk]) -> list[StylePattern]:
    """A way of thinking the cards don't offer: their misses that were none
    of the library's ways keep landing in the same group of their own new
    moves (discover_moves over their readings only). Same checks as
    `asks_for`; the even spread is over every way a question could go -- the
    library's card types and the new moves they have asked for."""
    tagged = sorted((m for m in misses if m.move), key=lambda m: m.at)
    universe = tuple(sorted({m.move for m in tagged}))
    labels = {m.move: m.move_label for m in tagged}
    return [
        _ask_pattern("asks_beyond", a.target, a,
                     f"When the cards miss, keeps asking for something they don't offer: "
                     f"\u201c{labels[a.target]}\u201d ({a.count} of {a.n} times).")
        for a in _ask_facts(tagged, lambda m: m.move, universe, max(1, len(universe)),
                            spread=len(_SLOTS) + len(universe))
    ]


# A miss read as none of the library's ways is a NEW MOVE, kept as a short
# topic-free phrase with its embedding (migration 089). A learner's moves are
# grouped among THEIR OWN readings only (2026-10-01: no learner's data feeds
# another's), each joining the earliest move within MOVE_SAME of it (cosine)
# -- so a group's id never changes once made.
# Calibrated 2026-09-30, live (read-miss-v2 on real Gemini, 7 new-move
# phrases in 3 intended groups -- limits, stakes, authority): same group
# >= 0.808, different groups <= 0.795. A thin margin on a small sample --
# recalibrate from organic readings (every phrase and embedding is kept).
MOVE_SAME = 0.80


@dataclass(frozen=True)
class MoveReading:
    set_id: UUID
    learner_id: UUID
    session_id: UUID
    at: datetime
    move: str
    embedding: tuple[float, ...]


def _cos(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def discover_moves(readings: list[MoveReading], same: float = MOVE_SAME) -> tuple[dict[UUID, str], list[dict]]:
    """Group one learner's new moves: (set_id -> group id, the groups). A
    group's id is its first reading's set; its label is that reading's
    phrase. Each group says how often it was asked for, in how many chats,
    with examples. Callers pass one learner's readings only."""
    leaders: list[tuple[str, tuple[float, ...], str]] = []
    of: dict[UUID, str] = {}
    members: dict[str, list[MoveReading]] = {}
    for r in sorted(readings, key=lambda r: r.at):
        best = max(((_cos(r.embedding, e), gid) for gid, e, _ in leaders), default=(0.0, None))
        if best[1] is not None and best[0] >= same:
            gid = best[1]
        else:
            gid = f"move:{r.set_id}"
            leaders.append((gid, r.embedding, r.move))
        of[r.set_id] = gid
        members.setdefault(gid, []).append(r)
    groups = []
    for gid, _, label in leaders:
        rs = members[gid]
        groups.append({
            "id": gid, "label": label, "readings": len(rs),
            "sessions": len({r.session_id for r in rs}), "examples": [r.move for r in rs[:5]],
        })
    groups.sort(key=lambda g: (-g["readings"], -g["sessions"]))
    return of, groups


def miss_follow_through(misses: list[MissAsk], picks: list[StylePick], *, family: bool = False) -> dict:
    """For each miss that asked for a way: the first later hand that offered
    it -- was it taken (a match found)? And once taken, was it taken again in
    a later chat (it held)? Counts, plus the misses in all."""
    fam = _as_families(picks) if family else picks
    ordered = sorted(fam, key=lambda q: q.at)
    read = [m for m in misses if m.asked in FAMILY_OF]
    offered_later = taken = held = 0
    for m in read:
        want = FAMILY_OF[m.asked] if family else m.asked
        later = [q for q in ordered if q.at > m.at and want in q.offered]
        if not later:
            continue
        offered_later += 1
        if later[0].slot != want:
            continue
        taken += 1
        if any(q.slot == want and q.session_id != later[0].session_id for q in later[1:]):
            held += 1
    return {"misses": len(misses), "read": len(read), "in_hand": sum(m.in_hand for m in read),
            "offered_later": offered_later, "taken": taken, "held": held}


def find_patterns(picks: list[StylePick], knob_obs: Iterable[Observation],
                  misses: list[MissAsk] | None = None) -> list[StylePattern]:
    """Pure: everything layer 3 concludes, strongest first -- from this
    learner's own picks, slider moves and misses only."""
    found = (pick_patterns(picks) + lean_patterns(picks) + conditional_patterns(picks)
             + shape_patterns(picks) + passes_over_patterns(picks) + speed_patterns(picks)
             + asks_for_patterns(misses or [], picks) + asks_beyond_patterns(misses or [])
             + range_patterns(knob_obs))
    order = {"conditional": 0, "shape": 1, "speed": 2, "way_in": 3, "asks_for": 4, "asks_beyond": 5, "then": 6,
             "passes_over": 7, "lean": 8, "range": 9}
    return merge_facts(sorted(found, key=lambda p: (_ORDER[p.status], order[p.kind])))


# ------------------------------------------------------------------ reading

# What the rest of Versa is told about a learner's thinking style: only
# CONFIRMED patterns (every gate passed), as their plain statements. It is
# re-read on the turn after anything the learner does (a pick, a pass,
# "other directions", a slider move, a miss read): `forget` drops their
# entry the moment it happens, so the style is analysed turn by turn, not at
# session end. The short TTL only covers another server process having seen
# the event (Cloud Run runs several).
_CONFIRMED_TTL_SECONDS = 120.0
_confirmed_cache: dict[UUID, tuple[float, list[str]]] = {}


def forget(learner_id: UUID | None) -> None:
    """Something new happened for this learner: read their style afresh."""
    if learner_id is not None:
        _confirmed_cache.pop(learner_id, None)


async def confirmed_statements(pool: asyncpg.Pool, learner_id: UUID) -> list[str]:
    """The learner's confirmed thinking-style patterns, in words -- what the
    ambiguity check, the options and Learn-a-topic are given (never the
    direction cards: invariant 14)."""
    import time

    now = time.monotonic()
    hit = _confirmed_cache.get(learner_id)
    if hit is not None and now - hit[0] < _CONFIRMED_TTL_SECONDS:
        return hit[1]
    # one statement per fact: a facet only repeats its fact
    statements = [p.statement for p in await StyleReader(pool).patterns(learner_id)
                  if p.status == "confirmed" and p.facet_of is None]
    _confirmed_cache[learner_id] = (now, statements)
    return statements



class StyleReader:
    """Read-only: fetches, then `find_patterns`. No table, nothing written."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def patterns(self, learner_id: UUID) -> list[StylePattern]:
        """Every query below is scoped to this learner: their picks, their
        questions (topics are told apart against the average of their OWN
        questions), their slider moves and their misses. Nothing about any
        other learner is read (2026-10-01)."""
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
        topics = assign_topics(rows, await InteractionStore(self._pool).question_centre(learner_id))
        knob_obs = await ObservationReader(self._pool).for_learner(learner_id, ("knob", "topic"))
        misses = await self.misses(learner_id, topics)
        return find_patterns(style_picks(past, topics), knob_obs, misses)

    async def _own_moves(self, learner_id: UUID) -> tuple[list, dict[UUID, str], list[dict]]:
        """This learner's new-move readings, grouped among themselves only:
        (readings, set_id -> group id, groups)."""
        from versa.directions import DirectionStore

        readings = [r for r in await DirectionStore(self._pool).miss_readings(learner_id)
                    if r.type is None and r.same_subject and r.move_embedding is not None]
        of, groups = discover_moves([
            MoveReading(set_id=r.set_id, learner_id=r.learner_id, session_id=r.session_id, at=r.created_at,
                        move=r.move, embedding=tuple(r.move_embedding)) for r in readings])
        return readings, of, groups

    async def misses(self, learner_id: UUID, topics: dict | None = None) -> list[MissAsk]:
        """Their misses, on the topics their chats were on. Only a question on
        the same subject is read as asking for a way out (migration 088's
        tagging)."""
        from versa.directions import DirectionStore

        store = DirectionStore(self._pool)
        topics = topics or {}
        readings = {r.set_id: r for r in await store.miss_readings(learner_id)}
        _, groups, _ = await self._own_moves(learner_id)
        labels = {r.set_id: r.move for r in readings.values()}

        def ask(m) -> MissAsk:
            reading = readings.get(m.set_id)
            asked = m.tagged_as
            if asked is None and reading is not None and reading.same_subject:
                asked = reading.type  # the model's reading, when the embedding couldn't place it
            move = groups.get(m.set_id)
            return MissAsk(session_id=m.session_id, at=m.created_at,
                           topic=topics.get((m.session_id, m.turn_index), f"s:{m.session_id}"), asked=asked,
                           in_hand=m.in_hand, move=move,
                           move_label=labels.get(UUID(move.removeprefix("move:"))) if move else None)

        return [ask(m) for m in await store.learner_misses(learner_id)]

    async def learner_moves(self, learner_id: UUID) -> list[dict]:
        """One learner's own new moves -- what they asked for that the cards
        don't offer -- grouped among their own readings, strongest first."""
        readings, of, groups = await self._own_moves(learner_id)
        mine: dict[str, list] = {}
        for r in readings:
            mine.setdefault(of[r.set_id], []).append(r)
        by_id = {g["id"]: g for g in groups}
        out = [{"id": gid, "label": by_id[gid]["label"], "times": len(rs),
                "chats": len({r.session_id for r in rs}), "examples": [r.move for r in rs[:5]]}
               for gid, rs in mine.items()]
        return sorted(out, key=lambda g: (-g["times"], -g["chats"]))

    async def follow_through(self, learner_id: UUID) -> dict:
        """Experimenting on a miss, in numbers (miss_follow_through)."""
        misses = await self.misses(learner_id)
        past = await PredictionStore(self._pool).past_picks(learner_id)
        return miss_follow_through(misses, style_picks(past, {}))
