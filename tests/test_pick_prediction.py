"""Versa's guess at the next direction (pick_prediction.py, migration 085):
the arithmetic, what it sets aside, the plain-words breakdown, the store,
the live flow (guessed before the set is sent, revealed after the pick) and
append-only (CLAUDE.md invariant 20)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import websockets

import versa.pick_prediction as pick_prediction_module
from tests.test_directions import (  # noqa: F401  (fixture)
    _directions_event,
    _llm,
    fixed_hands,
)
from tests.test_rooms_append_only import _string_literals
from tests.test_server import _start, _stop, _turn
from versa.pick_prediction import (
    QUICK_TAP_MS,
    PastPick,
    PredictionStore,
    explain,
    predict,
)

_S = uuid4()
_HAND = ("use", "why", "example")


def _pick(slot, *, offered=_HAND, prev=None, first=False, ms=5000, position=3, session=_S,
          stuck=False, rushed=False) -> PastPick:
    return PastPick(session_id=session, slot=slot, position=position, elapsed_ms=ms,
                    prev_slot=prev, first_in_session=first, stuck=stuck, rushed=rushed, offered=tuple(offered))


def _others(n, chosen, offered=_HAND):
    from versa.choice import Shown

    return [Shown(tuple(offered), chosen) for _ in range(n)]


# ------------------------------------------------------------ the arithmetic


def test_with_nothing_seen_it_guesses_what_everyone_else_takes():
    p = predict([], _others(30, "example") + _others(10, "why"), offered=_HAND, prev_slot=None,
                first_in_session=True)
    assert p.predicted_slot == "example"
    assert p.evidence_count == 0
    assert p.contributions["your_picks"]["weight"] == 0
    assert "starting guess" in explain(p)[0]


def test_the_guess_is_always_one_of_the_cards_on_offer():
    everyone_loves_example = _others(50, "example")
    hand = ("story", "debate", "summary")
    p = predict([], everyone_loves_example, offered=hand, prev_slot=None, first_in_session=False)
    assert p.predicted_slot in hand
    assert set(p.scores) == set(hand) and sum(p.scores.values()) == pytest.approx(1, abs=1e-3)


def test_with_nothing_at_all_the_tie_breaks_the_same_way_every_time():
    hand = ("why", "intuition", "next")
    first = predict([], [], offered=hand, prev_slot=None, first_in_session=False)
    assert first.predicted_slot == "intuition"  # canonical library order
    assert first == predict([], [], offered=hand, prev_slot=None, first_in_session=False)


def test_their_own_picks_outweigh_everyone_once_there_are_enough():
    past = [_pick("use") for _ in range(8)]
    p = predict(past, _others(100, "example"), offered=_HAND, prev_slot=None, first_in_session=False)
    assert p.predicted_slot == "use"
    assert p.contributions["your_picks"]["tally"]["use"] == {"offered": 8, "taken": 8}


def test_a_card_always_taken_when_offered_beats_one_merely_shown_more():
    # "story" was taken 5 times, but out of 15 hands; "use" only 3 -- every
    # time it was offered. Raw counts say story; read against the hands, use.
    past = [_pick(s, offered=("story", "summary", "debate")) for s in ["story", "summary", "debate"] * 5]
    past += [_pick("use", offered=("use", "story", "summary")) for _ in range(3)]
    p = predict(past, [], offered=("use", "story", "debate"), prev_slot=None, first_in_session=False)
    assert p.predicted_slot == "use"
    assert "you took it 3 of 3 times" in explain(p)[0]


def test_quick_taps_and_first_card_taps_count_for_less_and_are_named():
    past = [_pick("example", ms=QUICK_TAP_MS - 1) for _ in range(5)] + [_pick("use") for _ in range(3)]
    p = predict(past, [], offered=_HAND, prev_slot=None, first_in_session=False)
    assert p.predicted_slot == "use"
    assert p.contributions["set_aside"] == {"quick_tap": 5, "first_card": 0, "stuck": 0, "rushed": 0}
    assert "5 taps too quick to have read the cards" in explain(p)[-1]

    past = [_pick("example", position=0) for _ in range(2)]
    aside = predict(past, [], offered=_HAND, prev_slot=None, first_in_session=False).contributions["set_aside"]
    assert aside == {"quick_tap": 0, "first_card": 2, "stuck": 0, "rushed": 0}


def test_recent_picks_count_more_than_old_ones():
    past = [_pick("why") for _ in range(6)] + [_pick("use") for _ in range(6)]
    p = predict(past, [], offered=_HAND, prev_slot=None, first_in_session=False)
    assert p.predicted_slot == "use"


def test_their_order_of_approach_wins_right_after_the_card_it_follows():
    # overall they take "example" most, but after an example they go to "use"
    past = []
    for _ in range(4):
        s = uuid4()
        past += [_pick("example", first=True, session=s), _pick("use", prev="example", session=s)]
    past += [_pick("example") for _ in range(4)]
    after_example = predict(past, [], offered=_HAND, prev_slot="example", first_in_session=False)
    assert after_example.predicted_slot == "use"
    ctx = after_example.contributions["context"]
    assert ctx["kind"] == "order" and ctx["after_slot"] == "example"
    assert ctx["tally"]["use"] == {"offered": 4, "taken": 4}
    assert any("Right after" in line and "4 of 4" in line for line in explain(after_example))

    opening = predict(past, [], offered=_HAND, prev_slot=None, first_in_session=True)
    assert opening.predicted_slot == "example"
    assert opening.contributions["context"]["kind"] == "opening"


def test_the_breakdown_puts_the_strongest_reason_first():
    p = predict([_pick("why") for _ in range(20)], _others(5, "why") + _others(5, "use"), offered=_HAND,
                prev_slot=None, first_in_session=False)
    lines = explain(p)
    assert lines[0].startswith("When") and "20 of 20" in lines[0]
    assert any("Other learners" in line for line in lines)


# ------------------------------------------------------------- invariant 20


def test_pick_prediction_module_never_deletes_or_updates():
    path = Path(pick_prediction_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_direction_predictions_migration_has_no_delete_or_update():
    path = Path(pick_prediction_module.__file__).resolve().parent / "migrations" / "085_direction_predictions.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_prediction_store_has_no_removal_methods():
    for name in dir(PredictionStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name


# ------------------------------------------------------------- the live flow


async def _chat(client) -> tuple[str, str]:
    lid = (await client.post("/api/learners", json={"label": f"guess-{uuid4().hex[:6]}"})).json()["id"]
    sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
    return lid, sid


@pytest.mark.asyncio(loop_scope="session")
async def test_guessed_before_the_set_goes_out_and_revealed_after_the_pick(clean_pool, embedding_client, fixed_hands):  # noqa: F811
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            _, sid = await _chat(client)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            picked_slots = []
            await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            for _ in range(3):
                offered = await _directions_event(ws)
                async with clean_pool.acquire() as conn:
                    guess_row = await conn.fetchrow(
                        "SELECT predicted_slot FROM direction_predictions WHERE set_id = $1",
                        UUID(offered["set_id"]))
                assert guess_row is not None  # written before the learner could see the cards
                use = next(c for c in offered["cards"] if c["text"] == "card for use")
                events = await _turn(ws, {"type": "direction", "directions": True, "card_id": use["id"]})
                picked_slots.append("use")
                assert events[0]["type"] == "turn_start"
                guess = events[1]
                assert guess["type"] == "guess" and guess["turn_index"] == events[0]["turn_index"]
                assert guess["picked"] == "where it is used"
                assert guess["hit"] == (guess_row["predicted_slot"] == "use")
                assert guess["because"] and all(isinstance(line, str) for line in guess["because"])
            # three picks of "use": by the third it has learned them
            assert guess["hit"] is True and guess["picks_seen"] == 2
            assert guess["guesses"] == 3 and 1 <= guess["hits"] <= 3

            # asking their own question is a pass: no guess is revealed
            events = await _turn(ws, {"type": "message", "directions": True, "text": "and integrals?"})
            assert "guess" not in [e["type"] for e in events]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_store_derives_the_record_and_never_stores_a_hit(clean_pool, embedding_client, fixed_hands):  # noqa: F811
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid, sid = await _chat(client)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            offered = await _directions_event(ws)
        store = PredictionStore(clean_pool)
        stored = await store.get(UUID(offered["set_id"]))
        assert stored is not None and stored.evidence_count == 0
        assert set(stored.scores) == {"why", "use", "intuition"}  # only the hand on offer
        assert await store.record(UUID(lid)) == (0, 0)  # nothing picked yet
        async with clean_pool.acquire() as conn:
            cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'direction_predictions'")}
        assert not {"hit", "correct", "picked_slot"} & cols
        assert json.dumps(stored.contributions)  # the breakdown round-trips
    finally:
        await _stop(live)


# ---------------------------------------------------- shaping the answer


def test_no_profile_until_their_way_in_is_clear():
    from versa.pick_prediction import approach_profile, render_approach_directive

    assert approach_profile([_pick("use") for _ in range(3)]) is None  # too few fresh starts
    assert approach_profile([_pick("use", prev="why") for _ in range(9)]) is None  # none fresh
    scattered = [_pick(s) for s in ("use", "why", "example", "deeper", "next", "intuition")]
    assert approach_profile(scattered) is None  # no way in stands out
    assert render_approach_directive(None) == ""


def test_a_clear_way_in_and_what_follows_it_become_the_answer_order():
    from versa.pick_prediction import (
        approach_profile,
        explain_profile,
        render_approach_directive,
    )

    past = []
    for _ in range(4):
        s = uuid4()
        past += [_pick("example", first=True, session=s), _pick("use", prev="example", session=s)]
    profile = approach_profile(past)
    assert profile is not None and profile.path == ["example", "use"]
    directive = render_approach_directive(profile)
    assert "learned from 4 of their own choices" in directive
    assert "starts with one small, concrete worked example, then where it is actually used" in directive
    lines = explain_profile(profile)
    assert "first 4 of 4 times" in lines[0] and "4 of 4 times" in lines[1]


def test_quick_taps_cannot_make_a_way_in_on_their_own():
    from versa.pick_prediction import approach_profile

    past = [_pick("deeper", ms=100) for _ in range(6)] + [_pick("why") for _ in range(4)]
    profile = approach_profile(past)
    assert profile is not None and profile.path[0] == "why"


@pytest.mark.asyncio(loop_scope="session")
async def test_final_answer_carries_the_order_only_when_given():
    from versa.disambiguate import FinalAnswer
    from versa.llm import StubLLMClient

    llm = StubLLMClient(canned={"FINAL:ANSWER": "ok"})
    node = FinalAnswer(llm)
    await node.run("what is a derivative?")
    plain = llm.prompts[-1]
    await node.run("what is a derivative?", approach_directive="\nSTART WITH AN EXAMPLE\n")
    shaped = llm.prompts[-1]
    assert "START WITH AN EXAMPLE" in shaped and shaped.replace("\nSTART WITH AN EXAMPLE\n", "") == plain


@pytest.mark.asyncio(loop_scope="session")
async def test_a_new_question_is_answered_normally_and_a_follow_up_their_way(clean_pool, monkeypatch, fixed_hands):  # noqa: F811
    import versa.interactions as interactions_module
    from versa.audit import NodeCallStore
    from versa.embeddings import EMBEDDING_DIM, StubEmbeddingClient

    # a fresh test database has no real "average question": give it a neutral one
    async def neutral_centre(self):
        return [0.0] * EMBEDDING_DIM

    monkeypatch.setattr(interactions_module.InteractionStore, "question_centre", neutral_centre)
    # two wordings of one question embed together; everything else apart
    integrals = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    embeddings = StubEmbeddingClient(canned={
        "what are integrals?": integrals, "so how do you compute an integral?": integrals,
    })
    live = await _start(clean_pool, _llm(), embeddings)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid, sid = await _chat(client)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            # four questions of their own, each followed by taking "where it is used"
            for n in range(4):
                events = await _turn(ws, {"type": "message", "directions": True, "text": f"question {n}?"})
                assert "adapted" not in [e["type"] for e in events]  # not clear yet
                offered = await _directions_event(ws)
                use = next(c for c in offered["cards"] if c["text"] == "card for use")
                # read the cards first: an instant tap rightly counts for little
                await asyncio.sleep(QUICK_TAP_MS / 1000 + 0.1)
                events = await _turn(ws, {"type": "direction", "directions": True, "card_id": use["id"]})
                assert "adapted" not in [e["type"] for e in events]  # a pick already chose the direction
                await _directions_event(ws)
            # something new: the first answer is always a normal one
            new_topic = await _turn(ws, {"type": "message", "directions": True, "text": "what are integrals?"})
            await _directions_event(ws)
            # a follow-up on it: now answered the way they go
            follow_up = await _turn(
                ws, {"type": "message", "directions": True, "text": "so how do you compute an integral?"})
            # a pick under the shaped answer is steered: no evidence of style
            under_shaped = await _directions_event(ws)
            use = next(c for c in under_shaped["cards"] if c["text"] == "card for use")
            await asyncio.sleep(QUICK_TAP_MS / 1000 + 0.1)
            await _turn(ws, {"type": "direction", "directions": True, "card_id": use["id"]})

        assert "adapted" not in [e["type"] for e in new_topic]
        adapted = next(e for e in follow_up if e["type"] == "adapted")
        assert adapted["path"] == ["where it is used"] and adapted["because"]
        kinds = [e["type"] for e in follow_up]
        assert kinds.index("adapted") < kinds.index("done")

        calls = NodeCallStore(clean_pool)
        assert "approach_directive" not in (await calls.get_call_for_turn(UUID(sid), 8, "FinalAnswer")).input_json
        shaped = await calls.get_call_for_turn(UUID(sid), 9, "FinalAnswer")
        assert "where it is actually used" in shaped.input_json["approach_directive"]  # on record
        picks = await PredictionStore(clean_pool).past_picks(UUID(lid))
        assert [p.steered for p in picks] == [False] * 4 + [True]
    finally:
        await _stop(live)


class _Recent:
    """Just enough of InteractionStore for is_follow_up."""

    def __init__(self, rows, centre="zero"):
        from versa.embeddings import EMBEDDING_DIM

        self.rows = rows
        self.centre = [0.0] * EMBEDDING_DIM if centre == "zero" else centre

    async def get_recent_in_session(self, session_id, turn_number, limit):
        return self.rows[:limit]

    async def question_centre(self):
        return self.centre


def _row(vec, did_branch=False):
    from types import SimpleNamespace

    return SimpleNamespace(question_embedding=vec, did_branch=did_branch)


def _unit(i):
    from versa.embeddings import EMBEDDING_DIM

    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


def _recorder(rows, canned, centre="zero"):
    from versa.embeddings import StubEmbeddingClient
    from versa.interactions import InteractionRecorder

    return InteractionRecorder(_Recent(rows, centre), None, StubEmbeddingClient(canned=canned),
                               same_subject_threshold=0.545)


@pytest.mark.asyncio(loop_scope="session")
async def test_follow_up_means_related_to_what_they_just_asked_in_this_chat():
    canned = {"how do you compute an integral?": _unit(0), "what is photosynthesis?": _unit(1)}
    rec = _recorder([_row(_unit(5)), _row(_unit(0))], canned)  # asked about integrals two turns ago
    assert await rec.is_follow_up(uuid4(), 3, "how do you compute an integral?") is True
    assert await rec.is_follow_up(uuid4(), 3, "what is photosynthesis?") is False  # something new
    assert await _recorder([], canned).is_follow_up(uuid4(), 0, "how do you compute an integral?") is False


@pytest.mark.asyncio(loop_scope="session")
async def test_resolving_options_is_the_first_answer_to_that_question_not_a_follow_up():
    canned = {"derivatives?": _unit(2)}
    # the previous turn offered options for this very question: skipped
    rec = _recorder([_row(_unit(2), did_branch=True), _row(_unit(7))], canned)
    assert await rec.is_follow_up(uuid4(), 2, "derivatives?") is False
    # ... but it still counts if the question itself follows on from earlier
    rec = _recorder([_row(_unit(2), did_branch=True), _row(_unit(2))], canned)
    assert await rec.is_follow_up(uuid4(), 2, "derivatives?") is True


def test_picks_while_stuck_or_rushing_count_for_less_and_are_named():
    past = [_pick("example", stuck=True) for _ in range(3)] + [_pick("use", rushed=True) for _ in range(3)]
    past += [_pick("why") for _ in range(2)]
    p = predict(past, [], offered=_HAND, prev_slot=None, first_in_session=False)
    assert p.predicted_slot == "why"  # 2 clean picks beat 3 x 0.5 and 3 x 0.6
    assert p.contributions["set_aside"] == {"quick_tap": 0, "first_card": 0, "stuck": 3, "rushed": 3}
    last = explain(p)[-1]
    assert "3 picks right after a question you were stuck on" in last
    assert "3 picks from a session you were rushing through" in last


@pytest.mark.asyncio(loop_scope="session")
async def test_with_no_centre_yet_nothing_is_a_follow_up_so_every_answer_stays_normal():
    canned = {"how do you compute an integral?": _unit(0)}
    rec = _recorder([_row(_unit(0))], canned, centre=None)
    assert await rec.is_follow_up(uuid4(), 1, "how do you compute an integral?") is False


def test_centring_removes_what_every_question_shares():
    from versa.interactions import CENTRED_FOLLOW_UP_THRESHOLD, centred_cosine
    from versa.vector_math import cosine_similarity

    common = _unit(9)  # the "hey, can you show me..." every question has
    a = [c * 0.9 + x * 0.3 for c, x in zip(common, _unit(1))]  # recursion
    b = [c * 0.9 + x * 0.3 for c, x in zip(common, _unit(2))]  # photosynthesis
    centre = [c * 0.9 for c in common]
    assert cosine_similarity(a, b) > 0.8  # raw: looks like the same thing
    assert centred_cosine(a, b, centre) < CENTRED_FOLLOW_UP_THRESHOLD  # centred: two subjects
    assert centred_cosine(a, a, centre) > 0.99


def test_a_way_in_clear_only_as_a_family_still_shapes_the_answer():
    # a different concrete card each time: no single type is clear, the family is
    from versa.pick_prediction import (
        approach_profile,
        explain_profile,
        render_approach_directive,
    )

    hands = [("example", "why", "story"), ("use", "deeper", "compare"), ("try_it", "summary", "next"),
             ("real_data", "prove_it", "visualise"), ("example", "mistake", "connect"), ("use", "intuition", "debate")]
    past = [_pick(h[0], offered=h) for h in hands]
    profile = approach_profile(past)
    assert profile is not None and profile.path == ["family:real"]
    assert "making it real" in explain_profile(profile)[0] and "6 of 6" in explain_profile(profile)[0]
    assert "something concrete" in render_approach_directive(profile)


def test_picks_under_a_shaped_answer_never_confirm_the_shaping():
    from versa.pick_prediction import approach_profile

    steered = [PastPick(session_id=uuid4(), slot="use", position=1, elapsed_ms=5000, prev_slot=None,
                        first_in_session=True, offered=_HAND, steered=True) for _ in range(8)]
    assert approach_profile(steered) is None  # eight steered "use" picks prove nothing
    assert approach_profile([_pick("use") for _ in range(8)]) is not None  # eight free ones do
