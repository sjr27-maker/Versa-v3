"""The observation ledger (observations.py, docs/THINKING_STYLE.md layer 1):
each raw event split by lens, derived and never stored, and the moments a
pick should count for less (stuck, rushed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from versa import observations as obs

_L = uuid4()
_T0 = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)


def _at(i: int) -> datetime:
    return _T0 + timedelta(seconds=i)


def test_a_pick_is_style_and_its_speed_is_mood_and_a_pass_is_a_miss():
    s = uuid4()
    out = obs.from_direction_events(_L, [
        {"session_id": s, "turn_index": 0, "kind": "picked", "slot": "use", "position": 2,
         "elapsed_ms": 4200, "created_at": _at(1)},
        {"session_id": s, "turn_index": 1, "kind": "passed", "slot": None, "position": None,
         "elapsed_ms": 9000, "created_at": _at(2)},
    ])
    assert [(o.lens, o.key, o.value) for o in out] == [
        ("style", "use", 1), ("mood", "decision_ms", 4200), ("style", "passed", 1), ("mood", "decision_ms", 9000)]
    assert not any(o.steered for o in out)  # the cards are never shaped by beliefs (invariant 14)
    assert all(o.version == obs.DERIVATION_VERSION for o in out)


def test_a_slider_move_is_range_one_observation_per_slider_that_moved():
    s = uuid4()
    out = obs.from_knob_events(_L, [{
        "session_id": s, "turn_count": 2, "from_answer_length": 50, "from_depth": 50, "from_breadth": 50,
        "to_answer_length": 50, "to_depth": 85, "to_breadth": 20, "created_at": _at(1)}])
    assert [(o.lens, o.key, o.value, o.detail["from"]) for o in out] == [
        ("range", "depth", 85, 50), ("range", "breadth", 20, 50)]


def test_ability_interest_and_said_come_from_their_own_sources():
    s = uuid4()
    checks = obs.from_stage_checks(_L, [
        {"session_id": s, "turn_index": 3, "correct": False, "created_at": _at(1)},
        {"session_id": s, "turn_index": 4, "correct": None, "created_at": _at(2)},  # nothing marked
    ])
    assert [(o.lens, o.key, o.value) for o in checks] == [("ability", "stage_check", False)]
    turns = obs.from_interactions(_L, [
        {"session_id": s, "turn_number": 1, "entry_state": "stuck_repeat", "help_level": "worked_example",
         "created_at": _at(3)},
        {"session_id": s, "turn_number": 2, "entry_state": "topic_switch", "help_level": "none",
         "created_at": _at(4)},
    ])
    assert [(o.lens, o.key) for o in turns] == [
        ("ability", "stuck"), ("ability", "help_level"), ("interest", "topic_switch")]
    said = obs.from_stated_preferences(_L, [
        {"session_id": s, "turn_number": 0, "label": "prefers_brevity", "stated_preference": "keep it short",
         "created_at": _at(5)}])
    assert [(o.lens, o.key, o.value) for o in said] == [("said", "prefers_brevity", "keep it short")]


def test_turn_flags_mark_stuck_turns_and_rushed_sessions():
    calm, rushed = uuid4(), uuid4()
    ledger = obs.from_interactions(_L, [
        {"session_id": calm, "turn_number": 2, "entry_state": "stuck_repeat", "help_level": None,
         "created_at": _at(1)}])
    ledger += obs.from_stage_checks(_L, [{"session_id": calm, "turn_index": 5, "correct": False,
                                          "created_at": _at(2)}])
    picks = [(calm, 6000), (calm, 7000), (calm, 900), (rushed, 800), (rushed, 700), (rushed, 5000)]
    ledger += obs.from_direction_events(_L, [
        {"session_id": sid, "turn_index": i, "kind": "picked", "slot": "why", "position": 1,
         "elapsed_ms": ms, "created_at": _at(10 + i)} for i, (sid, ms) in enumerate(picks)])
    flags = obs.turn_flags(ledger)
    assert flags.stuck == {(calm, 2), (calm, 5)}
    assert flags.rushed == {rushed}  # 2 of 3 quick; the calm session had 1 of 3


def test_the_ledger_module_writes_nothing():
    import re
    from pathlib import Path

    from tests.test_rooms_append_only import _string_literals

    path = Path(obs.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", node.value), f"{path.name}:{node.lineno}"


import pytest


@pytest.mark.asyncio(loop_scope="session")
async def test_the_reader_splits_real_rows_and_flags_a_pick_after_a_wrong_check(transcript, clean_pool, learner_id):
    from versa.directions import CLASSIC_SLOTS as SLOTS
    from versa.directions import DirectionStore, shuffled_positions
    from versa.knob_events import KnobEventStore
    from versa.pick_prediction import PredictionStore
    from versa.session_knobs import SessionKnobs
    from versa.stage import StageCheckStore

    session_id = await transcript.create_session(learner_id)
    directions = DirectionStore(clean_pool)
    cards = {slot: f"card {slot}" for slot in SLOTS}
    offered = await directions.add_set(session_id=session_id, turn_index=0, knobs=SessionKnobs(),
                                       cards=cards, positions=shuffled_positions())
    why = next(c for c in offered.cards if c.slot == "why")
    await directions.record_event(set_id=offered.id, kind="picked", card_id=why.id, next_turn_index=1)
    await StageCheckStore(clean_pool).record(session_id=session_id, turn_index=0, question="q?",
                                              choices=[{"id": "a"}, {"id": "b"}], picked_id="a", answer_id="b")
    await KnobEventStore(clean_pool).record(session_id=session_id, turn_count=1, before=SessionKnobs(),
                                            after=SessionKnobs(depth=90))

    reader = obs.ObservationReader(clean_pool)
    ledger = await reader.for_learner(learner_id)
    assert {(o.lens, o.key) for o in ledger} == {
        ("style", "why"), ("mood", "decision_ms"), ("ability", "stage_check"), ("range", "depth")}
    assert (await reader.turn_flags(learner_id)).stuck == {(session_id, 0)}

    [pick] = await PredictionStore(clean_pool).past_picks(learner_id)
    assert pick.slot == "why" and pick.stuck is True  # the guesser counts it for less
