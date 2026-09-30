"""Sparks (sparks.py): the ledger rules -- welcome grant, refills up to a
cap, charges that never double-spend or go negative, refunds, learning
rewards and streaks -- and how the server applies them: an options turn is
free, an answer costs one, an empty balance gets a paywall event / HTTP 402,
and a passed quiz pays back."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import websockets
from fastapi import HTTPException

from tests.test_server import (
    _ANSWER,
    _FACT,
    _NOT_AMBIGUOUS,
    _TWO_BRANCHES,
    _options_for_branches,
    _stop,
    _turn,
)
from versa.learner import LearnerStore
from versa.llm import ModelTierClients, StubLLMClient
from versa.sparks import (
    ACTION_COSTS,
    REWARDS,
    STREAK_DAYS,
    TIERS,
    WELCOME_SPARKS,
    FixedTierResolver,
    InsufficientSparks,
    SparkEngine,
    passed,
)


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


# a moment exactly at the start of a 12-hour refill window
_T0 = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)


async def _learner(pool, label="sparks"):
    return (await LearnerStore(pool).create(label=label)).id


async def _engine(pool, *, tiers=None, enabled=True):
    clock = _Clock(_T0)
    return SparkEngine(pool, tiers, enabled=enabled, now=clock), clock


# -------------------------------------------------------------- the ledger


@pytest.mark.asyncio(loop_scope="session")
async def test_a_new_learner_starts_with_the_welcome_grant(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    status = await engine.status(lid)
    assert status.balance == WELCOME_SPARKS and status.tier == "free"
    assert status.next_refill_at == _T0 + timedelta(hours=TIERS["free"].refill_hours)
    assert [e.kind for e in status.recent] == ["welcome"], "an empty refill marker is not shown"
    # reading again, even much later in the same window, adds nothing
    assert (await engine.status(lid)).balance == WELCOME_SPARKS


@pytest.mark.asyncio(loop_scope="session")
async def test_charges_spend_once_per_key_and_refuse_when_short(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    first = await engine.charge(lid, "build_course", "k1")
    assert (first.balance, first.spent) == (WELCOME_SPARKS - ACTION_COSTS["build_course"], ACTION_COSTS["build_course"])
    again = await engine.charge(lid, "build_course", "k1")  # a retry / double-tap
    assert (again.balance, again.spent) == (first.balance, 0)

    assert (await engine.status(lid)).balance == first.balance


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unaffordable_action_is_refused_and_costs_nothing(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    await engine.charge(lid, "mock_test", "a")  # 12 left
    await engine.charge(lid, "mock_test", "b")  # 4 left
    with pytest.raises(InsufficientSparks) as exc:
        await engine.charge(lid, "build_course", "c")
    detail = exc.value.detail()
    assert detail["reason"] == "sparks" and detail["needed"] == 5 and detail["balance"] == 4
    assert (await engine.status(lid)).balance == 4
    with pytest.raises(InsufficientSparks):
        await engine.require(lid, "build_course")
    assert await engine.require(lid, "unit_quiz") == 4


@pytest.mark.asyncio(loop_scope="session")
async def test_after_the_fact_takes_what_is_there_and_never_goes_negative(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    await engine.charge(lid, "mock_test", "a")
    await engine.charge(lid, "mock_test", "b")
    await engine.charge(lid, "unit_quiz", "c")  # 1 left
    await engine.charge(lid, "answer", "d")  # 0 left
    late = await engine.charge(lid, "answer", "e", after_the_fact=True)
    assert (late.balance, late.spent) == (0, 0)


@pytest.mark.asyncio(loop_scope="session")
async def test_refills_top_up_once_per_window_and_stop_at_the_cap(clean_pool):
    engine, clock = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    policy = TIERS["free"]
    await engine.charge(lid, "mock_test", "a")
    await engine.charge(lid, "mock_test", "b")  # 4 left
    clock.advance(hours=policy.refill_hours)
    assert (await engine.status(lid)).balance == 4 + policy.refill_amount
    # spending down inside the same window never unlocks a second refill
    await engine.charge(lid, "mock_test", "c")
    assert (await engine.status(lid)).balance == 4 + policy.refill_amount - 8
    # a full balance refills only up to the cap
    for _ in range(4):
        clock.advance(hours=policy.refill_hours)
        await engine.status(lid)
    assert (await engine.status(lid)).balance == policy.cap


@pytest.mark.asyncio(loop_scope="session")
async def test_plus_refills_faster_and_higher(clean_pool):
    lid = await _learner(clean_pool)
    engine, clock = await _engine(clean_pool, tiers=FixedTierResolver({lid: "plus"}))
    status = await engine.status(lid)
    assert status.tier == "plus" and status.cap == TIERS["plus"].cap
    clock.advance(hours=TIERS["plus"].refill_hours)
    assert (await engine.status(lid)).balance == WELCOME_SPARKS + TIERS["plus"].refill_amount


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failing_tier_lookup_falls_back_to_free(clean_pool):
    class _Broken:
        async def tier_for(self, learner_id):
            raise RuntimeError("billing is down")

    engine, _ = await _engine(clean_pool, tiers=_Broken())
    lid = await _learner(clean_pool)
    assert (await engine.status(lid)).tier == "free"


@pytest.mark.asyncio(loop_scope="session")
async def test_refunds_return_a_spend_exactly_once(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    await engine.charge(lid, "build_course", "job")
    assert await engine.refund("job", "the work failed") == ACTION_COSTS["build_course"]
    assert await engine.refund("job", "again") == 0
    assert await engine.refund("never-charged", "x") == 0
    assert (await engine.status(lid)).balance == WELCOME_SPARKS


@pytest.mark.asyncio(loop_scope="session")
async def test_charged_refunds_when_the_work_fails_and_402s_when_short(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    with pytest.raises(RuntimeError):
        async with engine.charged(lid, "build_course"):
            raise RuntimeError("the model fell over")
    assert (await engine.status(lid)).balance == WELCOME_SPARKS

    await engine.charge(lid, "mock_test", "a")
    await engine.charge(lid, "mock_test", "b")
    with pytest.raises(HTTPException) as exc:
        async with engine.charged(lid, "build_course"):
            pytest.fail("the work must not run")
    assert exc.value.status_code == 402 and exc.value.detail["action"] == "build_course"


@pytest.mark.asyncio(loop_scope="session")
async def test_rewards_are_granted_once_and_may_pass_the_cap(clean_pool):
    engine, _ = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    assert await engine.reward(lid, "mock_test_passed", "quiz:1") == REWARDS["mock_test_passed"]
    assert await engine.reward(lid, "mock_test_passed", "quiz:1") == 0
    assert (await engine.status(lid)).balance == WELCOME_SPARKS + REWARDS["mock_test_passed"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_study_streak_pays_on_its_fifth_day(clean_pool):
    engine, clock = await _engine(clean_pool)
    lid = await _learner(clean_pool)
    rewards = []
    for day in range(STREAK_DAYS):
        charge = await engine.charge(lid, "answer", f"day{day}")
        rewards.append(charge.streak_reward)
        charge = await engine.charge(lid, "answer", f"day{day}-again")  # same day: no second reward
        rewards.append(charge.streak_reward)
        clock.advance(days=1)
    assert rewards == [0] * (2 * STREAK_DAYS - 2) + [REWARDS["study_streak"], 0]


@pytest.mark.asyncio(loop_scope="session")
async def test_status_shows_todays_use_and_the_live_streak(clean_pool):
    engine, clock = await _engine(clean_pool)
    clock.advance(hours=12)  # midday, well inside one local day
    lid = await _learner(clean_pool)
    status = await engine.status(lid)
    assert (status.spent_today, status.streak_days, status.studied_today) == (0, 0, False)
    assert status.streak_goal == STREAK_DAYS

    for day in range(3):
        await engine.charge(lid, "answer", f"d{day}")
        if day == 2:
            await engine.charge(lid, "answer", f"d{day}-b")
        clock.advance(days=1)
    clock.advance(days=-1)  # back to the third study day
    status = await engine.status(lid)
    assert (status.spent_today, status.streak_days, status.studied_today) == (2, 3, True)

    # the next day, before studying: still a 3-day streak, nothing spent yet
    clock.advance(days=1)
    status = await engine.status(lid)
    assert (status.spent_today, status.streak_days, status.studied_today) == (0, 3, False)

    # a whole day missed: the streak is gone
    clock.advance(days=1)
    assert (await engine.status(lid)).streak_days == 0


def test_streak_run():
    from datetime import date

    from versa.sparks import streak_run
    today = date(2030, 1, 10)
    days = {date(2030, 1, 7), date(2030, 1, 8), date(2030, 1, 9)}
    assert streak_run(days, today) == 3            # alive: studied up to yesterday
    assert streak_run(days | {today}, today) == 4
    assert streak_run({date(2030, 1, 8)}, today) == 0
    assert streak_run(set(), today) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_turned_off_nothing_is_charged_or_rewarded(clean_pool):
    engine, _ = await _engine(clean_pool, enabled=False)
    lid = await _learner(clean_pool)
    for i in range(10):
        await engine.charge(lid, "mock_test", f"k{i}")
    assert await engine.reward(lid, "lesson_completed", "l") == 0
    status = await engine.status(lid)
    assert status.balance == WELCOME_SPARKS and status.enabled is False


def test_pass_mark():
    assert passed(70) and passed(100)
    assert not passed(69) and not passed(None)


# ---------------------------------------------------------- through the API


async def _start_with(pool, llm, embedding_client, engine, billing=None):
    """tests.test_server._start, but with a given SparkEngine."""
    import asyncio
    import socket

    import uvicorn

    from tests.test_server import _Live
    from versa.server import create_app

    app = create_app(pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client,
                     llm_mode="stub", sparks=engine, billing=billing)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    return _Live(port, app, server, task)


@pytest.mark.asyncio(loop_scope="session")
async def test_options_are_free_the_answer_costs_one(clean_pool, embedding_client):
    llm = StubLLMClient(canned={
        "ASSESS:BRANCH": _TWO_BRANCHES, "DISAMBIGUATE:OPTIONS": _options_for_branches,
        "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
    })
    engine, _ = await _engine(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, engine)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "spk-opts"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            first = await _turn(ws, {"type": "message", "text": "help with derivatives?"})
            assert [e["type"] for e in first] == ["turn_start", "options", "done"]
            second = await _turn(ws, {"type": "select_option", "option_id": first[1]["options"][0]["id"]})
            sparks = [e for e in second if e["type"] == "sparks"]
            assert sparks == [{"type": "sparks", "balance": WELCOME_SPARKS - 1, "spent": 1}]
            assert second[-1]["type"] == "done", "the charge lands before done"
        async with httpx.AsyncClient(base_url=live.http) as client:
            status = (await client.get(f"/api/learners/{lid}/sparks")).json()
        assert status["balance"] == WELCOME_SPARKS - 1
        assert status["costs"]["answer"] == 1 and status["tier"] == "free"
        assert [e["kind"] for e in status["recent"]] == ["spend", "welcome"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_empty_balance_gets_a_paywall_instead_of_a_turn(clean_pool, embedding_client):
    llm = StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    engine, _ = await _engine(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, engine)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "spk-empty"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        from uuid import UUID
        for i in range(WELCOME_SPARKS):
            await engine.charge(UUID(lid), "answer", f"drain{i}")
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await ws.send(json.dumps({"type": "message", "text": "what is a derivative?"}))
            event = json.loads(await ws.recv())
            assert event["type"] == "paywall" and event["reason"] == "sparks"
            assert event["needed"] == 1 and event["balance"] == 0 and event["tier"] == "free"
            assert "next_refill_at" in event
        async with httpx.AsyncClient(base_url=live.http) as client:
            history = (await client.get(f"/api/sessions/{sid}/history")).json()
        assert history == [], "a refused turn never ran"
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_exam_actions_are_priced_and_a_passed_quiz_pays_back(clean_pool, embedding_client):
    llm = StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    engine, _ = await _engine(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, engine)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "spk-exam"})).json()["id"]

            async def balance():
                return (await client.get(f"/api/learners/{lid}/sparks")).json()["balance"]

            exam = (await client.post("/api/exams", json={"learner_id": lid, "query": "cells"})).json()
            assert await balance() == WELCOME_SPARKS - ACTION_COSTS["create_exam"]

            quiz = (await client.post(f"/api/exam-units/{exam['units'][0]['id']}/quiz")).json()
            assert await balance() == WELCOME_SPARKS - ACTION_COSTS["create_exam"] - ACTION_COSTS["unit_quiz"]
            answers = [
                {"question_id": q["id"], "response": "0" if q["kind"] == "choice" else "It is the idea in a sentence."}
                for q in quiz["questions"]
            ]
            done = (await client.post(f"/api/exam-quizzes/{quiz['id']}/submit", json={"answers": answers})).json()
            assert passed(done["score"]["percent"])
            after_quiz = WELCOME_SPARKS - 3 - 3 + REWARDS["unit_quiz_passed"]
            assert await balance() == after_quiz

            # 16 left: a mock (8) is fine, a second one (8) too, a third is refused
            assert (await client.post(f"/api/exams/{exam['id']}/mock")).status_code == 200
            assert (await client.post(f"/api/exams/{exam['id']}/mock")).status_code == 200
            refused = await client.post(f"/api/exams/{exam['id']}/mock")
            assert refused.status_code == 402
            assert refused.json()["detail"]["action"] == "mock_test"
            assert await balance() == after_quiz - 16
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_generation_is_refunded(clean_pool, embedding_client):
    def _boom(prompt):
        raise RuntimeError("model down")

    llm = StubLLMClient(canned={
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
        "EXAM:SYLLABUS": _boom,
    })
    engine, _ = await _engine(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, engine)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "spk-refund"})).json()["id"]
            r = await client.post("/api/exams", json={"learner_id": lid, "query": "cells"})
            assert r.status_code >= 400
            assert (await client.get(f"/api/learners/{lid}/sparks")).json()["balance"] == WELCOME_SPARKS
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_topic_actions_are_priced_and_reading_back_is_free(clean_pool, embedding_client):
    llm = StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    engine, _ = await _engine(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, engine)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "spk-topic"})).json()["id"]

            async def balance():
                return (await client.get(f"/api/learners/{lid}/sparks")).json()["balance"]

            ex = (await client.post("/api/topic-explorations", json={"learner_id": lid, "query": "optics"})).json()
            expected = WELCOME_SPARKS - ACTION_COSTS["explore_topic"]
            assert await balance() == expected
            root = ex["root_nodes"][0]["id"]
            await client.post(f"/api/topic-nodes/{root}/expand")
            expected -= ACTION_COSTS["expand_topic"]
            assert await balance() == expected
            await client.post(f"/api/topic-nodes/{root}/expand")  # already expanded: read back
            assert await balance() == expected
            await client.post(f"/api/topic-nodes/{root}/expand", json={"more": True})
            expected -= ACTION_COSTS["expand_topic"]
            assert await balance() == expected
            r = await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": ex["id"], "selected_node_ids": [root],
            })
            assert r.status_code == 200, r.text
            assert await balance() == expected - ACTION_COSTS["build_course"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_finishing_a_lesson_pays_back(clean_pool, embedding_client):
    import asyncio

    judge_done = json.dumps({"completed": True, "evidence": "explained it back", "drifted": False,
                             "check_passed": True})
    llm = StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER,
                                "WRITE:FACT": _FACT, "LESSON:JUDGE": judge_done})
    engine, _ = await _engine(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, engine)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "spk-lesson"})).json()["id"]
            ex = (await client.post("/api/topic-explorations", json={"learner_id": lid, "query": "waves"})).json()
            topic = (await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": ex["id"], "selected_node_ids": [ex["root_nodes"][0]["id"]],
            })).json()
            lesson_id = topic["chapters"][0]["lessons"][0]["id"]
            sid = (await client.post(f"/api/lessons/{lesson_id}/start")).json()["session_id"]
        rewards, statuses = [], []
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "text": "I'm ready, let's start this lesson."})
            for _ in range(6):
                # each point is finished by its tap-to-answer quiz (topics.py)
                async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
                    quiz = (await client.post(f"/api/lessons/{lesson_id}/activity")).json()
                    if "activity_id" not in quiz:
                        break
                    await client.post(f"/api/lessons/{lesson_id}/activity-result",
                                      json={"activity_id": quiz["activity_id"], "picked": "a"})
                await live.loop.wait_for_background_tasks()
                while True:
                    try:
                        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=1.5))
                    except TimeoutError:
                        break
                    if event["type"] == "progress":
                        statuses.append(event["lesson_status"])
                    elif event["type"] == "sparks_reward":
                        rewards.append(event)
                if "done" in statuses:
                    break
        assert statuses[-1] == "done"
        assert rewards == [{"type": "sparks_reward", "reason": "lesson_completed",
                            "amount": REWARDS["lesson_completed"]}]
        async with httpx.AsyncClient(base_url=live.http) as client:
            recent = (await client.get(f"/api/learners/{lid}/sparks")).json()["recent"]
        assert sum(1 for e in recent if e["reason"] == "lesson_completed") == 1
    finally:
        await _stop(live)
