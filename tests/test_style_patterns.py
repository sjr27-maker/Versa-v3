"""Layer 3 (style_patterns.py): a thinking style must mean what
docs/THINKING_STYLE.md defines -- each test below is one clause of that
definition, and shows the gate that stops something else from being called
a style."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from versa import style_patterns as sp
from versa.observations import from_knob_events

_T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _picks(seq, *, weight=1.0, topics=None, sessions=None):
    """seq: slots taken as the FIRST pick after a question of their own,
    one per (session, topic), a day apart."""
    out = []
    for i, slot in enumerate(seq):
        out.append(sp.StylePick(
            session_id=(sessions[i] if sessions else uuid4()), at=_T0 + timedelta(days=i), slot=slot,
            prev_slot=None, weight=weight, topic=(topics[i] if topics else f"t{i}"),
        ))
    return out


_EVERYONE = {"intuition": 20, "example": 20, "why": 20, "use": 20, "deeper": 20, "next": 20}


def _way_in(picks, cohort=_EVERYONE):
    found = [p for p in sp.pick_patterns(picks, cohort, {}) if p.kind == "way_in"]
    return found[0] if found else None


def test_a_consistent_way_in_across_topics_and_time_is_confirmed_with_every_gate_shown():
    p = _way_in(_picks(["example", "example", "use", "example", "example", "example", "example", "example"]))
    assert p is not None and p.key == "example" and p.status == "confirmed", p
    assert set(p.gates) == {"evidence", "clear", "sessions", "topics", "above_cohort", "over_time", "predicts"}
    assert all(g.ok for g in p.gates.values())
    assert p.trials >= 3 and p.hits / p.trials > p.cohort_rate  # proven out of sample
    assert "work through one concrete example" in p.statement


def test_the_same_choice_on_one_topic_only_is_not_a_style():
    p = _way_in(_picks(["example"] * 8, topics=["integrals"] * 8))
    assert p.status == "emerging" and not p.gates["topics"].ok  # stable ACROSS topics is required


def test_what_everyone_does_is_being_new_not_a_style():
    everyone_example = {"example": 90, "why": 5, "use": 5}
    p = _way_in(_picks(["example"] * 8), cohort=everyone_example)
    assert p.status == "emerging" and not p.gates["above_cohort"].ok  # measured against the cohort


def test_a_learner_with_no_style_gets_no_confirmed_one():
    rng = random.Random(7)
    for _ in range(50):
        picks = _picks([rng.choice(list(sp.SLOTS)) for _ in range(12)])
        assert not any(p.status == "confirmed" for p in sp.pick_patterns(picks, _EVERYONE, {}))


def test_quick_and_rushed_taps_cannot_make_a_style():
    # layer 2's weights: a quick tap in a rushed session counts 0.3 x 0.6
    assert _way_in(_picks(["deeper"] * 8, weight=0.18)) is None


def test_a_way_in_that_changed_is_fading_not_confirmed():
    p = _way_in(_picks(["example"] * 6 + ["why"] * 6))
    assert p is None or p.status != "confirmed"
    old = [p for p in sp.pick_patterns(_picks(["example"] * 8 + ["why"] * 5), _EVERYONE, {})
           if p.kind == "way_in"]
    assert old and old[0].key == "example" and old[0].status == "fading"


def test_what_follows_what_is_its_own_pattern():
    picks = []
    for i in range(6):
        s = uuid4()
        picks.append(sp.StylePick(s, _T0 + timedelta(days=i), "example", None, 1.0, f"t{i}"))
        picks.append(sp.StylePick(s, _T0 + timedelta(days=i, hours=1), "use", "example", 1.0, f"t{i}"))
    then = [p for p in sp.pick_patterns(picks, _EVERYONE, {}) if p.kind == "then"]
    assert [(p.key, p.status) for p in then] == [("example>use", "confirmed")]
    assert "After" in then[0].statement and "where it is used" in then[0].statement


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
    [p] = sp.range_patterns(_moves([80, 85, 90, 85]), {})
    assert p.key == "depth" and p.status == "confirmed" and p.value == 85
    assert "leans rigorous" in p.statement


def test_a_range_needs_to_be_set_by_them_consistently():
    assert sp.range_patterns(_moves([85]), {}) == []  # once is not a range
    scattered = sp.range_patterns(_moves([20, 90, 25, 85]), {})
    assert all(p.status != "confirmed" for p in scattered)
    everyone_deep = sp.range_patterns(_moves([80, 85, 90, 85]), {"depth": [85, 80, 90, 85]})
    assert all(p.status != "confirmed" for p in everyone_deep)  # the cohort goes as deep


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

    from versa.directions import SLOTS as _SLOTS
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
    way_in = next(p for p in patterns if p.kind == "way_in")
    assert way_in.key == "use" and way_in.n == 5 and way_in.sessions == 5
    assert way_in.status in {"emerging", "confirmed"}

    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            body = (await client.get(f"/api/learners/{learner.id}/style-patterns")).json()
            missing = await client.get("/api/learners/00000000-0000-0000-0000-000000000000/style-patterns")
        assert body["version"] == sp.STYLE_VERSION
        shown = next(p for p in body["patterns"] if p["kind"] == "way_in")
        assert set(shown["gates"]) >= {"topics", "above_cohort", "predicts"}
        assert all({"ok", "have", "need"} <= set(g) for g in shown["gates"].values())
        assert missing.status_code == 404
    finally:
        await _stop(live)
