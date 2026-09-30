"""Layer 3 (style_patterns.py): a thinking style must mean what
docs/THINKING_STYLE.md defines -- each test below is one clause of that
definition, and shows the gate that stops something else from being called
a style. Every pattern is read from the learner's OWN data only, against
chance (2026-10-01): no function here takes another learner's anything."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from versa import style_patterns as sp
from versa.observations import from_knob_events

_T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _pick(i, slot, hand, *, prev=None, weight=1.0, topic=None, session=None):
    return sp.StylePick(session_id=session or uuid4(), at=_T0 + timedelta(days=i), slot=slot, prev_slot=prev,
                        weight=weight, topic=topic or f"t{i}", offered=tuple(hand))


def _way_in(picks):
    """The card-level way in (family-level ones have keys "family:...")."""
    found = [p for p in sp.pick_patterns(picks) if p.kind == "way_in" and not p.key.startswith("family:")]
    return found[0] if found else None


def _takes_example(n=14, miss_at=(2,), **kw):
    """Takes "work through one concrete example" whenever it is on offer."""
    return [_pick(i, "why" if i in miss_at else "example", ("example", "why", "next"), **kw) for i in range(n)]


def test_a_consistent_way_in_across_topics_and_time_is_confirmed_with_every_gate_shown():
    p = _way_in(_takes_example())
    assert p is not None and p.key == "example" and p.status == "confirmed", p
    assert set(p.gates) == {"evidence", "clear", "sessions", "topics", "over_time", "predicts"}
    assert all(g.ok for g in p.gates.values())
    assert p.baseline_rate == round(1 / 3, 4)  # chance, from the hands it was in -- never other learners
    assert p.trials >= 3 and p.hits / p.trials > p.baseline_rate  # proven out of sample
    assert "work through one concrete example" in p.statement
    assert "of the times offered" in p.gates["clear"].have  # read against the hands


def test_the_same_choice_on_one_topic_only_is_not_a_style():
    p = _way_in(_takes_example(topic="integrals"))
    assert p.status == "emerging" and not p.gates["topics"].ok  # stable ACROSS topics is required


def test_a_learner_with_no_style_almost_never_gets_one():
    # every pattern's proof is a binomial test corrected for how many
    # candidates were tried; without that, 10-22% of random learners were
    # told they had a style (2026-09-29)
    from versa.directions import deal_hand, draw_pool

    false_alarms = 0
    for seed in range(100):
        rng = random.Random(seed)
        picks = []
        for i in range(40):
            hand = deal_hand(draw_pool(rng), set(), rng=rng)
            picks.append(_pick(i, rng.choice(hand), hand))
        found = sp.pick_patterns(picks) + sp.lean_patterns(picks)
        false_alarms += any(p.status == "confirmed" for p in found)
    assert false_alarms <= 5


def test_a_broad_way_out_of_an_answer_is_confirmed_at_the_family_level():
    from versa.directions import FAMILY_OF, deal_hand, draw_pool

    rng = random.Random(4)
    picks = []
    for i in range(30):  # ten chats' worth of first moves
        hand = deal_hand(draw_pool(rng), set(), rng=rng)
        real = [c for c in hand if FAMILY_OF[c] == "real"]
        picks.append(_pick(i, real[0] if real and rng.random() < 0.85 else rng.choice(hand), hand))
    fam = [p for p in sp.pick_patterns(picks) if p.key == "family:real"]
    assert fam and fam[0].kind == "way_in" and fam[0].status == "confirmed", fam
    assert "making it real" in fam[0].statement


def test_a_card_shown_often_is_not_a_style_just_for_being_shown():
    # "story" is in every hand and taken a third of the time -- chance.
    picks = [_pick(i, ("story", "debate", "summary")[i % 3], ("story", "debate", "summary")) for i in range(15)]
    assert all(p.key != "story" or p.status != "confirmed" for p in sp.pick_patterns(picks))


def test_quick_and_rushed_taps_cannot_make_a_style():
    # layer 2's weights: a quick tap in a rushed session counts 0.3 x 0.6
    assert _way_in(_takes_example(weight=0.18, miss_at=())) is None


def test_a_way_in_that_changed_is_fading_not_confirmed():
    hand = ("example", "why", "next")
    picks = [_pick(i, "example", hand) for i in range(8)] + [_pick(8 + i, "why", hand) for i in range(5)]
    old = _way_in(picks)
    assert old is not None and old.key == "example" and old.status == "fading"


def test_what_follows_what_is_its_own_pattern():
    picks = []
    for i in range(12):
        s = uuid4()
        picks.append(_pick(2 * i, "example", ("example", "why", "next"), session=s, topic=f"t{i}"))
        picks.append(_pick(2 * i + 1, "use", ("use", "deeper", "compare"), prev="example", session=s, topic=f"t{i}"))
    then = [p for p in sp.pick_patterns(picks) if p.kind == "then" and not p.key.startswith("family:")]
    assert [(p.key, p.status) for p in then] == [("example>use", "confirmed")]
    assert "After" in then[0].statement and "where it is used" in then[0].statement


# ------------------------------------------------------------------- lean


def _lean_learner(axis_index, sign, n=24, seed=1):
    """Always takes the card furthest one way on one axis of the space."""
    from versa.directions import COORDS, deal_hand, draw_pool

    rng = random.Random(seed)
    picks = []
    for i in range(n):
        hand = deal_hand(draw_pool(rng), set(), rng=rng)
        chosen = max(hand, key=lambda s: (COORDS[s][axis_index] * sign, rng.random()))
        picks.append(_pick(i, chosen, hand))
    return picks


def test_a_lean_in_the_card_space_is_a_style_with_every_gate():
    leans = [p for p in sp.lean_patterns(_lean_learner(0, +1)) if p.key == "concrete"]
    assert leans and leans[0].status == "confirmed", leans
    assert leans[0].rate > 0.25 and "more concrete" in leans[0].statement
    assert leans[0].hits / leans[0].trials > 0.5  # it picks the more concrete card, out of sample


def test_a_lean_the_other_way_reads_the_other_way():
    leans = [p for p in sp.lean_patterns(_lean_learner(1, -1)) if p.key == "depth"]
    assert leans and leans[0].status == "confirmed" and "simpler" in leans[0].statement


# ------------------------------------------------------------------ range


def _moves(levels, knob="depth"):
    rows = []
    for i, level in enumerate(levels):
        before = {"answer_length": 50, "depth": 50, "breadth": 50}
        after = dict(before, **{knob: level})
        rows.append({"session_id": uuid4(), "turn_count": 1, "created_at": _T0 + timedelta(days=i),
                     **{f"from_{k}": v for k, v in before.items()}, **{f"to_{k}": v for k, v in after.items()}})
    return from_knob_events(uuid4(), rows)


def test_a_depth_they_keep_setting_is_their_range():
    [p] = sp.range_patterns(_moves([80, 85, 90, 85]))
    assert p.key == "depth" and p.status == "confirmed" and p.value == 85
    assert "leans rigorous" in p.statement


def test_a_range_needs_to_be_set_by_them_consistently():
    assert sp.range_patterns(_moves([85])) == []  # once is not a range
    scattered = sp.range_patterns(_moves([20, 90, 25, 85]))
    assert all(p.status != "confirmed" for p in scattered)
    # kept near where the slider starts: set, but not a range of their own
    near_default = sp.range_patterns(_moves([55, 45, 60, 50]))
    assert all(p.status != "confirmed" for p in near_default)


# ----------------------------------------------------------------- topics


def _vec(angle_deg):
    from versa.embeddings import EMBEDDING_DIM

    v = [0.0] * EMBEDDING_DIM
    v[0], v[1] = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return v


def test_topics_are_told_apart_without_drifting_into_each_other():
    s = uuid4()
    # each question ~53 degrees from the last (cos 0.6 > 0.545), but the third
    # is ~106 degrees from the first: it must not be chained into its topic
    rows = [{"session_id": s, "turn_number": i, "question_author": "learner", "question_embedding": _vec(a)}
            for i, a in enumerate([0, 53, 106])]
    rows.append({"session_id": s, "turn_number": 3, "question_author": "system_option", "question_embedding": None})
    from versa.embeddings import EMBEDDING_DIM

    topics = sp.assign_topics(rows, [0.0] * EMBEDDING_DIM)  # a zero centre: plain cosine vs TOPIC_THRESHOLD
    assert topics[(s, 0)] == topics[(s, 1)]
    assert topics[(s, 2)] != topics[(s, 0)]
    assert topics[(s, 3)] == topics[(s, 2)]  # a click belongs to the topic they were on

    # with no centre yet, topics can't be told apart: all one, so the
    # "different topics" gate can't pass on a guess
    blind = sp.assign_topics(rows, None)
    assert len({blind[(s, i)] for i in range(4)}) == 1


import pytest


async def _fresh_start_pick(pool, directions, session_id, slot, ms=6000):
    from uuid import uuid4 as _id

    from versa.directions import CLASSIC_SLOTS as _SLOTS
    from versa.directions import shuffled_positions
    from versa.session_knobs import SessionKnobs

    offered = await directions.add_set(session_id=session_id, turn_index=0, knobs=SessionKnobs(),
                                       cards={s: f"card {s}" for s in _SLOTS}, positions=shuffled_positions())
    card = next(c for c in offered.cards if c.slot == slot)
    await pool.execute(  # a deliberate pick: read for `ms` before tapping
        "INSERT INTO direction_events (id, set_id, kind, card_id, next_turn_index, elapsed_ms) "
        "VALUES ($1, $2, 'picked', $3, 1, $4)", _id(), offered.id, card.id, ms)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reader_finds_a_way_in_from_real_rows_and_the_api_shows_every_gate(clean_pool, embedding_client):
    import httpx

    from tests.test_directions import _llm
    from tests.test_server import _start, _stop
    from versa.directions import DirectionStore
    from versa.learner import LearnerStore

    learner = await LearnerStore(clean_pool).create(label="patterned")
    directions = DirectionStore(clean_pool)
    from versa.audit import TranscriptStore

    transcript = TranscriptStore(clean_pool)
    for _ in range(5):
        await _fresh_start_pick(clean_pool, directions, await transcript.create_session(learner.id), "use")

    patterns = await sp.StyleReader(clean_pool).patterns(learner.id)
    way_in = next(p for p in patterns if p.kind == "way_in" and p.key == "use")  # card level
    assert way_in.key == "use" and way_in.n == 5 and way_in.sessions == 5
    assert way_in.status in {"emerging", "confirmed"}

    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.get(f"/api/learners/{learner.id}/style-patterns")).json()
            missing = await client.get("/api/learners/00000000-0000-0000-0000-000000000000/style-patterns")
        assert body["version"] == sp.STYLE_VERSION
        shown = next(p for p in body["patterns"] if p["kind"] == "way_in")
        assert set(shown["gates"]) >= {"topics", "predicts"} and "above_cohort" not in shown["gates"]
        assert all({"ok", "have", "need"} <= set(g) for g in shown["gates"].values())
        # what the sky is drawn from: the picks it rests on, and where each card sits
        assert shown["evidence"] and {"card", "offered", "supports", "session"} <= set(shown["evidence"][0])
        assert body["space"]["example"]["family"] == "real" and len(body["space"]["example"]["coords"]) == 4
        assert missing.status_code == 404
    finally:
        await _stop(live)


def test_exploring_topic_trees_deeply_is_a_range_of_their_own():
    from versa.observations import from_topic_signals

    rows = []
    for i in range(4):  # four topics, each explored three levels down
        e = uuid4()
        for level in (1, 2, 3):
            rows.append({"exploration_id": e, "kind": "expand", "created_at": _T0 + timedelta(days=i, minutes=level),
                         "payload": {"depth": level, "title": f"branch {level}"}})
        rows.append({"exploration_id": e, "kind": "selection", "created_at": _T0 + timedelta(days=i, hours=1),
                     "payload": {"max_depth_selected": 3, "lessons_chosen": 9, "lessons_total": 10}})
    obs = from_topic_signals(uuid4(), rows)
    assert {o.key for o in obs} == {"explore_depth", "explore_breadth"} and all(o.lens == "range" for o in obs)
    found = {p.key: p for p in sp.range_patterns(obs)}
    assert found["explore_depth"].status == "confirmed" and "goes deep" in found["explore_depth"].statement
    assert found["explore_breadth"].status == "confirmed" and "most of what's offered" in found["explore_breadth"].statement


def test_steered_picks_are_left_out_of_the_style_evidence():
    from versa.pick_prediction import PastPick

    past = [PastPick(session_id=uuid4(), slot="use", position=1, elapsed_ms=5000, prev_slot=None,
                     first_in_session=True, at=_T0 + timedelta(days=i), offered=("use", "why", "story"),
                     steered=i % 2 == 0) for i in range(10)]
    assert len(sp.style_picks(past, {})) == 5


# ------------------------------------------------------- the second layer


def _situational(seed, n_chats, want, steps=4, topics=8):
    """A simulated learner: `want(familiar, stuck, step)` names the family
    they go to (85% of the time it's on offer), or None for no preference."""
    from versa.directions import FAMILY_OF, deal_hand, draw_pool

    rng = random.Random(seed)
    picks, seen, i = [], set(), 0
    for _ in range(n_chats):
        s, topic = uuid4(), f"t{rng.randrange(topics)}"
        familiar, prev = topic in seen, None
        seen.add(topic)
        for step in range(steps):
            hand = deal_hand(draw_pool(rng), set(), rng=rng)
            stuck = rng.random() < 0.3
            fam = want(familiar, stuck, step)
            mine = [c for c in hand if FAMILY_OF[c] == fam] if fam else []
            chosen = mine[0] if mine and rng.random() < 0.85 else rng.choice(hand)
            picks.append(sp.StylePick(session_id=s, at=_T0 + timedelta(hours=i), slot=chosen, prev_slot=prev,
                                      weight=1.0, topic=topic, offered=tuple(hand), stuck=stuck))
            i, prev = i + 1, chosen
    return picks


def test_a_choice_that_changes_with_the_situation_is_a_conditional_fact():
    picks = _situational(3, 30, lambda fam, stuck, step: "real" if step < 2 else "deeper")
    [c] = [p for p in sp.conditional_patterns(picks) if p.status == "confirmed"]
    assert c.key == "position:real|deeper"
    assert c.statement.startswith("Opening a chat, goes to making it real; further into a chat, to going deeper")
    assert c.gates["differs"].ok and all(g.ok for g in c.gates.values())


def test_being_stuck_can_change_the_way_they_go():
    picks = _situational(5, 30, lambda fam, stuck, step: "simpler" if stuck else "wider")
    found = {p.key for p in sp.conditional_patterns(picks) if p.status == "confirmed"}
    assert "state:simpler|wider" in found


def test_new_topics_against_familiar_ones_needs_enough_topics():
    want = lambda fam, stuck, step: ("deeper" if fam else "real") if step == 0 else None
    found = [p for p in sp.conditional_patterns(_situational(2, 80, want, topics=40)) if p.status == "confirmed"]
    assert any(p.key == "familiarity:real|deeper" for p in found)
    assert found[0].statement.startswith("On a topic that's new to them, goes to making it real")


def test_the_same_tendency_everywhere_is_not_conditional_and_neither_is_noise():
    for seed in range(20):
        always_real = _situational(seed, 30, lambda fam, stuck, step: "real")
        noise = _situational(seed, 30, lambda fam, stuck, step: None)
        assert not [p for p in sp.conditional_patterns(always_real) if p.status == "confirmed"]
        assert not [p for p in sp.conditional_patterns(noise) if p.status == "confirmed"]


def test_patterns_pointing_the_same_way_become_one_fact_with_facets():
    picks = _situational(1, 30, lambda fam, stuck, step: "real")
    found = sp.find_patterns(picks, [])
    heads = [p for p in found if p.facet_of is None and p.status == "confirmed" and p.kind != "range"]
    facets = [p for p in found if p.facet_of is not None]
    assert len(heads) == 1, [(p.id, p.statement) for p in heads]  # one tendency, one fact
    assert facets and all(f.facet_of == heads[0].id for f in facets)


# ------------------------------------ shape of a chat, passes over, speed

def _timed(picks, seed, fast_family=None):
    """Give picks a reading time: ~6s, a third of that for `fast_family`."""
    from dataclasses import replace

    from versa.directions import FAMILY_OF

    rng = random.Random(seed)
    return [replace(p, elapsed_ms=int(rng.lognormvariate(
        math.log(2000 if FAMILY_OF[p.slot] == fast_family else 6000), 0.5))) for p in picks]


def _confirmed(found, kind):
    return [p for p in found if p.kind == kind and p.status == "confirmed"]


def test_a_chat_that_gets_deeper_as_it_goes_is_a_shape():
    picks = _situational(4, 30, lambda fam, stuck, step: "simpler" if step < 2 else "deeper")
    [shape] = [p for p in _confirmed(sp.shape_patterns(picks), "shape") if p.key == "depth"]
    assert shape.statement == "Their picks get deeper as a chat goes on (they start simpler)."
    assert all(g.ok for g in shape.gates.values())


def test_a_card_they_skip_when_their_favourite_is_not_taken_is_passed_over():
    from versa.directions import FAMILY_OF

    def skip_summary(seed):
        # no favourite, but never "the one-line version" when there's another card
        rng = random.Random(seed)
        out = []
        for p in _situational(seed, 50, lambda fam, stuck, step: None):
            if p.slot == "summary" and rng.random() < 0.9:
                others = [c for c in p.offered if c != "summary"]
                p = sp.StylePick(**{**p.__dict__, "slot": rng.choice(others)})
            out.append(p)
        return out

    hits = sum(any(p.key == "summary" for p in _confirmed(sp.passes_over_patterns(skip_summary(s)),
                                                           "passes_over")) for s in range(10))
    assert hits >= 7
    assert FAMILY_OF["summary"] == "simpler"


def test_a_strong_way_in_is_not_also_passing_over_everything_else():
    for seed in range(10):
        picks = _situational(seed, 30, lambda fam, stuck, step: "real")
        assert not _confirmed(sp.passes_over_patterns(picks), "passes_over")


def test_recognising_one_way_fast_is_a_speed_fact():
    picks = _timed(_situational(6, 30, lambda fam, stuck, step: None), 6, fast_family="deeper")
    [speed] = _confirmed(sp.speed_patterns(picks), "speed")
    assert speed.key == "deeper" and "quicker" in speed.statement


def test_quick_taps_and_rushed_chats_say_nothing_about_speed():
    from dataclasses import replace

    picks = _timed(_situational(6, 30, lambda fam, stuck, step: None), 6, fast_family="deeper")
    rushed = [replace(p, rushed=True) for p in picks]
    assert not sp.speed_patterns(rushed)
    taps = [replace(p, elapsed_ms=800) for p in picks]
    assert not sp.speed_patterns(taps)


def test_no_preference_learners_rarely_get_a_shape_pass_or_speed_fact():
    false = 0
    for seed in range(40):
        picks = _timed(_situational(500 + seed, 30, lambda fam, stuck, step: None), seed)
        found = sp.shape_patterns(picks) + sp.passes_over_patterns(picks) + sp.speed_patterns(picks)
        false += any(p.status == "confirmed" for p in found)
    assert false <= 2  # <= 5%


# ------------------------------------------------ experimenting on a miss

def _missing(seed, n_chats, asks, topics=8, misses_per_chat=1):
    """A simulated learner's misses: `asks(rng)` names the type their own
    question is nearest to (or None: not clearly any)."""
    rng = random.Random(seed)
    out, i = [], 0
    for _ in range(n_chats):
        s, topic = uuid4(), f"t{rng.randrange(topics)}"
        for _ in range(misses_per_chat):
            out.append(sp.MissAsk(session_id=s, at=_T0 + timedelta(hours=i), topic=topic, asked=asks(rng)))
            i += 1
    return out


def _anything(rng):
    from versa.directions import SLOTS

    return rng.choice([*SLOTS, None])


def test_misses_that_keep_asking_the_same_way_are_a_fact():
    found = sp.asks_for_patterns(_missing(1, 12, lambda r: "why" if r.random() < 0.7 else _anything(r)))
    [why] = [p for p in found if p.key == "why" and p.status == "confirmed"]
    assert why.statement.startswith("When the cards miss, asks for")
    assert all(g.ok for g in why.gates.values())


def test_scattered_or_unread_misses_are_not_a_fact():
    false = 0
    for seed in range(40):
        found = sp.asks_for_patterns(_missing(300 + seed, 15, _anything))
        false += any(p.status == "confirmed" for p in found)
    assert false <= 2
    assert not sp.asks_for_patterns(_missing(5, 15, lambda r: None))


def test_follow_through_counts_a_match_found_after_a_miss_and_whether_it_held():
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    miss = sp.MissAsk(session_id=s1, at=_T0, topic="t", asked="why")

    def pick(s, h, slot, offered):
        return sp.StylePick(session_id=s, at=_T0 + timedelta(hours=h), slot=slot, prev_slot=None, weight=1.0,
                            topic="t", offered=offered)

    picks = [pick(s1, 1, "use", ("use", "summary", "compare")),  # not on offer: no test yet
             pick(s2, 2, "why", ("why", "summary", "compare")),  # offered, taken: a match
             pick(s3, 3, "why", ("why", "story", "debate"))]  # taken again in a later chat: it held
    assert sp.miss_follow_through([miss], picks) == {
        "misses": 1, "read": 1, "in_hand": 0, "offered_later": 1, "taken": 1, "held": 1}
    missed_again = [picks[0], pick(s2, 2, "summary", ("why", "summary", "compare"))]
    assert sp.miss_follow_through([miss], missed_again)["taken"] == 0


def test_after_a_miss_the_next_hand_is_widened_at_random():
    from versa.directions import with_extras

    hand = ["why", "summary", "compare"]
    got = {tuple(with_extras(hand, {"wild", "path"}, random.Random(s), widen=True)) for s in range(30)}
    assert got == {("why", "summary", "wild"), ("why", "summary", "path")}  # always an extra, either one
    assert with_extras(hand, set(), random.Random(1), widen=True) == hand  # nothing left to widen with


# ------------------------------------------- moves the cards don't offer

def _unit(i, dims=8, jitter=0.0, rng=None):
    v = [0.0] * dims
    v[i] = 1.0
    if rng:
        v = [x + rng.uniform(-jitter, jitter) for x in v]
    return tuple(v)


def test_a_learners_new_moves_group_among_their_own_readings():
    rng = random.Random(3)
    me = uuid4()
    readings = []
    for k in range(12):
        which = k % 3  # three different moves, each asked for four times
        readings.append(sp.MoveReading(
            set_id=uuid4(), learner_id=me, session_id=uuid4(), at=_T0 + timedelta(hours=k),
            move=["where the rule stops working", "what it costs to get wrong", "who decides this"][which],
            embedding=_unit(which, jitter=0.05, rng=rng)))
    of, groups = sp.discover_moves(readings)
    assert len(groups) == 3 and len(set(of.values())) == 3
    assert all(g["readings"] == 4 and g["sessions"] == 4 for g in groups)
    assert all("learners" not in g and "candidate_card" not in g for g in groups)  # never pooled across people
    more = readings + [sp.MoveReading(set_id=uuid4(), learner_id=me, session_id=uuid4(),
                                      at=_T0 + timedelta(hours=99), move="when does it break",
                                      embedding=_unit(0, jitter=0.05, rng=rng))]
    of2, groups2 = sp.discover_moves(more)
    assert groups2[0]["readings"] == 5 and groups2[0]["label"] == "where the rule stops working"
    assert {k: v for k, v in of2.items() if k in of} == of  # a group never changes once made


def test_misses_that_keep_asking_for_a_move_the_cards_lack_are_a_fact():
    def beyond(seed, share):
        rng = random.Random(seed)
        out = []
        for m in _missing(seed, 12, lambda r: None):
            move = "move:A" if rng.random() < share else f"move:{rng.randrange(8)}"
            out.append(sp.MissAsk(session_id=m.session_id, at=m.at, topic=m.topic, asked=None, move=move,
                                  move_label="where the rule stops working" if move == "move:A" else "other"))
        return out

    [fact] = [p for p in sp.asks_beyond_patterns(beyond(1, 0.7)) if p.status == "confirmed"]
    assert fact.key == "move:A" and "something they don't offer" in fact.statement
    assert "where the rule stops working" in fact.statement
    assert not [p for p in sp.asks_beyond_patterns(beyond(2, 0.0)) if p.status == "confirmed"]


# -------------------------------------------- only the learner's own data

@pytest.mark.asyncio(loop_scope="session")
async def test_another_learners_activity_never_changes_this_learners_style_or_guess(clean_pool):
    """The privacy promise (2026-10-01): a learner's patterns, misses, new
    moves and pick guesses are read from their own rows only -- so another
    learner doing anything at all leaves every one of them exactly as it was."""
    from versa.audit import TranscriptStore
    from versa.directions import DirectionStore
    from versa.learner import LearnerStore
    from versa.pick_prediction import PredictionStore

    learners, directions, transcript = LearnerStore(clean_pool), DirectionStore(clean_pool), TranscriptStore(clean_pool)
    me = await learners.create(label="me")
    for _ in range(5):
        await _fresh_start_pick(clean_pool, directions, await transcript.create_session(me.id), "use")
    reader = sp.StyleReader(clean_pool)

    async def everything():
        session = await transcript.create_session(me.id)
        from versa.directions import CLASSIC_SLOTS, shuffled_positions
        from versa.session_knobs import SessionKnobs

        probe = await directions.add_set(session_id=session, turn_index=0, knobs=SessionKnobs(),
                                         cards={s: f"card {s}" for s in CLASSIC_SLOTS},
                                         positions=shuffled_positions())
        guess = await PredictionStore(clean_pool).predict_for_set(learner_id=me.id, session_id=session,
                                                                 set_id=probe.id)
        patterns = [p.model_dump(exclude={"evidence"}) for p in await reader.patterns(me.id)]
        return patterns, guess.scores, await reader.misses(me.id), await reader.learner_moves(me.id)

    before = await everything()
    other = await learners.create(label="someone else")
    for _ in range(12):  # a very different chooser, lots of evidence
        await _fresh_start_pick(clean_pool, directions, await transcript.create_session(other.id), "why", ms=2500)
    after = await everything()
    assert after == before
