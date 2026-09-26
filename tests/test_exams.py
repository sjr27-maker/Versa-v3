"""Exam preparation (exams.py): set up from a search, a PDF or a course;
unit quizzes and timed mocks; grading; results; the study plan; and the
rules that keep it append-only and walled off from the personal learner
model."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from tests.test_resources import make_pdf
from tests.test_server import _ANSWER, _FACT, _NOT_AMBIGUOUS, _start, _stop
from versa.exams import (
    OVER_TIME_GRACE_SECONDS,
    SECONDS_PER_CHOICE,
    SECONDS_PER_SHORT,
    AnswerRow,
    PlanItemRow,
    Sitting,
    UnitOut,
    build_schedule,
    days_left,
    match_sittings,
    mock_time_limit,
    parse_questions,
    score_of,
    seconds_elapsed,
    weakest_unit,
)
from versa.llm import StubLLMClient


def _llm(**extra) -> StubLLMClient:
    canned = {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT}
    canned.update(extra)
    return StubLLMClient(canned=canned)


# ------------------------------------------------------------------ pure rules


def test_malformed_questions_are_dropped():
    good_choice = {"kind": "choice", "prompt": "Q1?", "choices": ["a", "b", "c", "d"],
                   "correct_index": 2, "explanation": "c is right"}
    questions = parse_questions([
        good_choice,
        {**good_choice, "prompt": "q1?"},                          # duplicate prompt
        {**good_choice, "prompt": "Q2?", "correct_index": 4},      # index out of range
        {**good_choice, "prompt": "Q3?", "correct_index": True},   # a bool is not an index
        {**good_choice, "prompt": "Q4?", "choices": ["a", "a", "b", "c"]},  # repeated choice
        {**good_choice, "prompt": "Q5?", "choices": ["a", "b"]},   # too few choices
        {"kind": "short", "prompt": "Q6?", "answer": "", "explanation": ""},  # no model answer
        {"kind": "essay", "prompt": "Q7?"},                        # unknown kind
        {"kind": "short", "prompt": "Q8?", "answer": "because", "explanation": "x"},
    ], limit=10)
    assert [q["prompt"] for q in questions] == ["Q1?", "Q8?"]
    assert questions[0]["model_answer"] == "c" and questions[1]["correct_index"] is None


def test_score_counts_only_graded_answers():
    answers = [
        AnswerRow(question_id=f"00000000-0000-0000-0000-00000000000{i}", response="x",
                  correct=c, feedback="", graded_by="model")
        for i, c in enumerate([True, False, None, True])
    ]
    score = score_of(answers, total=4)
    assert (score.correct, score.graded, score.total, score.percent) == (2, 3, 4, 67)
    assert score_of([], total=3).percent is None


def test_mock_clock_and_exam_countdown():
    assert mock_time_limit([{"kind": "choice"}, {"kind": "short"}]) == SECONDS_PER_CHOICE + SECONDS_PER_SHORT
    assert days_left(date(2026, 10, 1), today=date(2026, 9, 26)) == 5
    assert days_left(None) is None
    start = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    assert seconds_elapsed(start, now=start + timedelta(seconds=90)) == 90
    assert seconds_elapsed(start, now=start - timedelta(seconds=5)) == 0


# ------------------------------------------------------------------ end to end


@pytest.mark.asyncio(loop_scope="session")
async def test_search_exam_unit_quiz_hand_in_and_results(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "exam-flow"})).json()["id"]
            r = await client.post("/api/exams", json={
                "learner_id": lid, "query": "cell biology", "exam_date": "2030-06-01",
            })
            assert r.status_code == 200, r.text
            exam = r.json()
            assert exam["title"] == "cell biology" and exam["source_kind"] == "search"
            assert exam["exam_date"] == "2030-06-01" and exam["days_left"] > 0
            assert [u["title"] for u in exam["units"]][:2] == ["Foundations", "Key mechanisms"]
            assert all(u["quizzes_taken"] == 0 and u["last_percent"] is None for u in exam["units"])

            unit = exam["units"][0]
            r = await client.post(f"/api/exam-units/{unit['id']}/quiz")
            assert r.status_code == 200, r.text
            quiz = r.json()
            assert quiz["kind"] == "unit" and quiz["unit_title"] == "Foundations"
            assert quiz["time_limit_seconds"] is None and quiz["submitted"] is False
            assert len(quiz["questions"]) == 5
            # the answers never reach the client before hand-in
            raw = json.dumps(quiz)
            assert "correct_index" not in raw and "The idea, stated in one sentence." not in raw

            qs = quiz["questions"]
            answers = [
                {"question_id": qs[0]["id"], "response": "0"},   # right
                {"question_id": qs[1]["id"], "response": "2"},   # wrong
                {"question_id": qs[2]["id"], "response": "0"},   # right
                # qs[3] left blank -> wrong
                {"question_id": qs[4]["id"], "response": "It is the idea in a sentence."},
            ]
            r = await client.post(f"/api/exam-quizzes/{quiz['id']}/submit", json={"answers": answers})
            assert r.status_code == 200, r.text
            done = r.json()
            assert done["submitted"] is True and done["over_time"] is False
            assert done["score"] == {"correct": 3, "graded": 5, "total": 5, "percent": 60}
            by_id = {res["id"]: res for res in done["results"]}
            assert by_id[qs[1]["id"]]["correct"] is False
            assert by_id[qs[1]["id"]]["correct_answer"] == "The right one"
            assert by_id[qs[3]["id"]]["response"] == "" and by_id[qs[3]["id"]]["correct"] is False
            assert by_id[qs[4]["id"]]["feedback"] == "Stub: looks right."

            # handed in once only; re-reading shows the same result
            again = await client.post(f"/api/exam-quizzes/{quiz['id']}/submit", json={"answers": answers})
            assert again.status_code == 409
            reread = (await client.get(f"/api/exam-quizzes/{quiz['id']}")).json()
            assert reread["score"] == done["score"]

            # a retake is a new quiz, told what was already asked
            retake = (await client.post(f"/api/exam-units/{unit['id']}/quiz")).json()
            assert retake["id"] != quiz["id"]
            assert "already seen these questions" in llm.prompts[-1]
            assert qs[0]["prompt"] in llm.prompts[-1]

            exam = (await client.get(f"/api/exams/{exam['id']}")).json()
            assert exam["units"][0]["quizzes_taken"] == 1
            assert exam["units"][0]["last_percent"] == 60 == exam["units"][0]["best_percent"]
            summaries = (await client.get(f"/api/learners/{lid}/exams")).json()
            assert summaries[0]["id"] == exam["id"] and summaries[0]["quizzes_taken"] == 1

        # every model call is on record (invariant 13 / 2)
        async with clean_pool.acquire() as conn:
            names = [r["node_name"] for r in await conn.fetch(
                "SELECT node_name FROM exam_generations ORDER BY created_at")]
            assert names == ["ExamSyllabus", "WriteQuestions", "GradeAnswers", "WriteQuestions"]
            # walled off: nothing about the learner was written to the personal model
            for table in ("learner_facts", "claims", "thinking_style_candidates"):
                assert await conn.fetchval(f"SELECT COUNT(*) FROM {table}") == 0, table
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_mock_is_timed_and_covers_every_unit(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "exam-mock"})).json()["id"]
            exam = (await client.post("/api/exams", json={"learner_id": lid, "query": "optics"})).json()
            assert exam["exam_date"] is None and exam["days_left"] is None
            r = await client.post(f"/api/exams/{exam['id']}/mock")
            assert r.status_code == 200, r.text
            mock = r.json()
            units = {u["title"] for u in exam["units"]}
            assert mock["kind"] == "mock" and len(mock["questions"]) == 2 * len(units)
            assert {q["unit_title"] for q in mock["questions"]} == units
            assert mock["time_limit_seconds"] == 2 * len(units) * SECONDS_PER_CHOICE
            assert 0 < mock["seconds_left"] <= mock["time_limit_seconds"]

            r = await client.post(f"/api/exam-quizzes/{mock['id']}/submit", json={"answers": [
                {"question_id": q["id"], "response": "0"} for q in mock["questions"]
            ]})
            done = r.json()
            assert done["score"]["percent"] == 100 and done["over_time"] is False
            assert done["seconds_left"] is None  # the clock stops at hand-in
            exam = (await client.get(f"/api/exams/{exam['id']}")).json()
            assert [m["score"]["percent"] for m in exam["mocks"]] == [100]
            assert all(u["quizzes_taken"] == 0 for u in exam["units"])  # a mock isn't a unit quiz
            assert OVER_TIME_GRACE_SECONDS > 0
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_grading_leaves_short_answers_ungraded_not_wrong(clean_pool, embedding_client):
    def _boom(_prompt):
        raise RuntimeError("quota")

    live = await _start(clean_pool, _llm(**{"EXAM:GRADE": _boom}), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "exam-grade"})).json()["id"]
            exam = (await client.post("/api/exams", json={"learner_id": lid, "query": "x"})).json()
            quiz = (await client.post(f"/api/exam-units/{exam['units'][0]['id']}/quiz")).json()
            short = next(q for q in quiz["questions"] if not q["choices"])
            done = (await client.post(f"/api/exam-quizzes/{quiz['id']}/submit", json={"answers": [
                {"question_id": short["id"], "response": "my answer"},
            ]})).json()
            result = next(r for r in done["results"] if r["id"] == short["id"])
            assert result["correct"] is None and "couldn't be graded" in result["feedback"].lower()
            assert done["score"]["graded"] == 4 and done["score"]["total"] == 5

            bad = await client.post(f"/api/exam-quizzes/{quiz['id']}/submit", json={"answers": [
                {"question_id": exam["id"], "response": "0"}]})
            assert bad.status_code in (409, 422)
        async with clean_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT error FROM exam_generations WHERE node_name = 'GradeAnswers'")
            assert "quota" in row["error"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_exam_from_a_pdf_and_from_a_course(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "exam-sources"})).json()["id"]

            pdf = make_pdf(["Photosynthesis notes", "Light reactions split water."])
            r = await client.post(
                "/api/exams/from-pdf",
                data={"learner_id": lid, "title": "Bio mock paper", "exam_date": "2030-01-02"},
                files={"file": ("notes.pdf", pdf, "application/pdf")},
            )
            assert r.status_code == 200, r.text
            exam = r.json()
            assert exam["source_kind"] == "pdf" and exam["title"] == "Bio mock paper"
            assert "Light reactions split water." in llm.prompts[-1]  # the syllabus read the PDF
            await client.post(f"/api/exam-units/{exam['units'][0]['id']}/quiz")
            assert "Light reactions split water." in llm.prompts[-1]  # and so do the questions

            # a course built in Learn a topic: its chapters become the units, no model call
            exploration = (await client.post("/api/topic-explorations",
                                             json={"learner_id": lid, "query": "optics"})).json()
            roots = exploration["root_nodes"]
            topic = (await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": exploration["id"],
                "selected_node_ids": [roots[0]["id"], roots[1]["id"]],
            })).json()
            calls_before = len(llm.prompts)
            r = await client.post("/api/exams/from-course",
                                  json={"learner_id": lid, "topic_id": topic["id"]})
            assert r.status_code == 200, r.text
            course_exam = r.json()
            assert len(llm.prompts) == calls_before
            assert course_exam["source_kind"] == "course" and course_exam["title"] == "optics"
            assert [u["title"] for u in course_exam["units"]] == [c["title"] for c in topic["chapters"]]
            await client.post(f"/api/exam-units/{course_exam['units'][0]['id']}/quiz")
            assert "Stub lesson 1" in llm.prompts[-1]  # the chapter's lessons shape the questions

            other = (await client.post("/api/learners", json={"label": "someone-else"})).json()["id"]
            r = await client.post("/api/exams/from-course",
                                  json={"learner_id": other, "topic_id": topic["id"]})
            assert r.status_code == 404  # only your own courses
    finally:
        await _stop(live)


# ------------------------------------------------------------------ study plan


def _by_day(items):
    out = {}
    for it in items:
        out.setdefault(it["day"], []).append((it["kind"], it["unit_id"]))
    return out


def test_schedule_spreads_units_then_reviews_with_a_mock_the_day_before():
    units = [f"u{i}" for i in range(7)]
    start = date(2030, 1, 1)
    days = _by_day(build_schedule(units, start, date(2030, 1, 11)))  # 10 days, exam on the 11th
    assert sorted(days) == [start + timedelta(days=k) for k in range(10)]  # never the exam day
    for i, u in enumerate(units):  # learning: 7 days, one unit each, revise then quiz
        assert days[start + timedelta(days=i)] == [("revise", u), ("quiz", u)]
    assert days[date(2030, 1, 8)] == [("weakest", None)]
    assert days[date(2030, 1, 9)] == [("weakest", None)]
    assert days[date(2030, 1, 10)] == [("mock", None)]


def test_schedule_compresses_when_the_exam_is_close():
    units = ["a", "b", "c", "d"]
    days = _by_day(build_schedule(units, date(2030, 1, 1), date(2030, 1, 3)))  # 2 days
    assert days[date(2030, 1, 1)] == [("revise", "a"), ("revise", "b"), ("quiz", "a"), ("quiz", "b")]
    assert days[date(2030, 1, 2)][-1] == ("mock", None)
    tomorrow = _by_day(build_schedule(units, date(2030, 1, 1), date(2030, 1, 2)))
    assert len(tomorrow) == 1 and tomorrow[date(2030, 1, 1)][-1] == ("mock", None)
    with pytest.raises(ValueError):
        build_schedule(units, date(2030, 1, 1), date(2030, 1, 1))


def test_long_schedule_fills_gaps_with_spaced_practice_and_weekly_mocks():
    items = build_schedule(["a", "b", "c"], date(2030, 1, 1), date(2030, 1, 31))  # 30 days
    days = _by_day(items)
    assert len(days) == 30 and all(days.values())  # every day has something
    mocks = sorted(it["day"] for it in items if it["kind"] == "mock")
    assert mocks == [date(2030, 1, 23), date(2030, 1, 30)]  # weekly, back from the day before
    assert days[date(2030, 1, 1)] == [("revise", "a"), ("quiz", "a")]
    assert [it["position"] for it in items if it["day"] == date(2030, 1, 1)] == [0, 1]


def test_sittings_tick_matching_items_in_order_and_early_work_counts():
    plan, a, b = uuid4(), uuid4(), uuid4()

    def item(day, pos, kind, unit=None):
        return PlanItemRow(id=uuid4(), plan_id=plan, day=date(2030, 1, day), position=pos,
                           kind=kind, unit_id=unit)

    rev_a, quiz_a, quiz_b = item(1, 0, "revise", a), item(1, 1, "quiz", a), item(2, 0, "quiz", b)
    weak, mock = item(3, 0, "weakest"), item(4, 0, "mock")
    at = datetime(2030, 1, 1, 9, tzinfo=UTC)
    done = match_sittings([rev_a, quiz_a, quiz_b, weak, mock], [
        Sitting(submitted_at=at, kind="unit", unit_id=b),                       # b, early: counts
        Sitting(submitted_at=at + timedelta(hours=1), kind="unit", unit_id=b),  # b again -> weakest
        Sitting(submitted_at=at + timedelta(hours=2), kind="mock", unit_id=None),
    ])
    assert done == {quiz_b.id, weak.id, mock.id}  # revise is never auto-ticked; a wasn't quizzed


def test_weakest_is_untested_first_then_lowest_score():
    def unit(pos, last):
        return UnitOut(id=uuid4(), position=pos, title=f"u{pos}", summary="",
                       quizzes_taken=0 if last is None else 1, last_percent=last, best_percent=last)

    assert weakest_unit([unit(0, 80), unit(1, None), unit(2, 20)]).title == "u1"
    assert weakest_unit([unit(0, 80), unit(1, 40), unit(2, 20)]).title == "u2"
    assert weakest_unit([]) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_from_today_to_the_exam_ticks_itself_and_by_hand(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client)
    service = live.app.state.exam_service
    service.today = lambda: date(2030, 1, 1)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "exam-plan"})).json()["id"]
            exam = (await client.post("/api/exams", json={
                "learner_id": lid, "query": "chemistry", "exam_date": "2030-01-09"})).json()
            eid = exam["id"]
            assert (await client.get(f"/api/exams/{eid}/plan")).json() is None

            r = await client.post(f"/api/exams/{eid}/plan")
            assert r.status_code == 200, r.text
            plan = r.json()
            assert plan["start_date"] == "2030-01-01" and plan["end_date"] == "2030-01-09"
            assert plan["days_left"] == 8 and len(plan["days"]) == 8 and plan["percent"] == 0
            assert [i["text"] for i in plan["today_items"]] == ["Revise Foundations", "Quiz: Foundations"]
            assert plan["days"][-1]["items"][-1]["text"] == "Mock test"
            weak = next(i for d in plan["days"] for i in d["items"] if i["kind"] == "weakest")
            assert weak["day"] == "2030-01-03"
            assert weak["text"] == "Quiz your weakest unit" and weak["unit_id"] is None  # decided on the day

            # handing in a Foundations quiz ticks its item by itself
            unit_id = exam["units"][0]["id"]
            quiz = (await client.post(f"/api/exam-units/{unit_id}/quiz")).json()
            await client.post(f"/api/exam-quizzes/{quiz['id']}/submit", json={"answers": [
                {"question_id": q["id"], "response": "0"} for q in quiz["questions"] if q["choices"]]})
            plan = (await client.get(f"/api/exams/{eid}/plan")).json()
            revise, quiz_item = plan["today_items"]
            assert quiz_item["done"] and quiz_item["auto_done"] and not revise["done"]

            # revising is ticked by hand, and can be unticked
            plan = (await client.post(f"/api/exam-plan-items/{revise['id']}/done", json={"done": True})).json()
            assert plan["today_items"][0]["done"] and not plan["today_items"][0]["auto_done"]
            assert plan["done"] == 2 and plan["percent"] == round(200 / plan["total"])
            plan = (await client.post(f"/api/exam-plan-items/{revise['id']}/done", json={"done": False})).json()
            assert not plan["today_items"][0]["done"]

            # two days later, what wasn't done shows as overdue
            service.today = lambda: date(2030, 1, 3)
            plan = (await client.get(f"/api/exams/{eid}/plan")).json()
            assert plan["days_left"] == 6
            assert {i["text"] for i in plan["overdue"]} >= {"Revise Foundations", "Revise Key mechanisms"}
            assert all(i["day"] < "2030-01-03" for i in plan["overdue"])
            # today's "weakest" is picked from units covered by today (Foundations, quizzed; Key
            # mechanisms, not yet) -- never Applications, which the plan doesn't reach until the 4th
            weak = next(i for i in plan["today_items"] if i["kind"] == "weakest")
            assert weak["text"] == "Quiz your weakest unit: Key mechanisms" and weak["unit_id"]

            # re-planning starts a new plan from today; the old one's items can't be ticked any more
            new = (await client.post(f"/api/exams/{eid}/plan")).json()
            assert new["id"] != plan["id"] and new["start_date"] == "2030-01-03" and new["overdue"] == []
            stale = await client.post(f"/api/exam-plan-items/{revise['id']}/done", json={"done": True})
            assert stale.status_code == 409

            # an exam with no date needs one; a date that isn't after today is refused
            undated = (await client.post("/api/exams", json={"learner_id": lid, "query": "optics"})).json()
            assert (await client.post(f"/api/exams/{undated['id']}/plan")).status_code == 422
            assert (await client.post(f"/api/exams/{undated['id']}/plan",
                                      json={"end_date": "2030-01-03"})).status_code == 422
            ok = await client.post(f"/api/exams/{undated['id']}/plan", json={"end_date": "2030-01-05"})
            assert ok.status_code == 200 and ok.json()["end_date"] == "2030-01-05"
    finally:
        await _stop(live)
