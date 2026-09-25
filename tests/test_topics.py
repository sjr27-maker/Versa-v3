"""Learn a topic (topics.py): explore -> expand -> build -> learn, progress
derived from task events, personalization in and out, and Sandbox untouched."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
import websockets

from tests.test_resources import make_pdf
from tests.test_server import _ANSWER, _FACT, _NOT_AMBIGUOUS, _start, _stop, _turn
from versa import resources
from versa.llm import StubLLMClient
from versa.memory import ThinkingStyleStore
from versa.reviews import ReviewStore
from versa.topics import (
    NodeRow,
    build_selection,
    derive_done_task_ids,
    describe_personalization,
    normalize_tasks,
)

_JUDGE_DONE = json.dumps(
    {"completed": True, "evidence": "explained it back correctly", "drifted": False, "check_passed": None}
)
_JUDGE_NOT_DONE = json.dumps(
    {"completed": False, "evidence": "", "drifted": True, "check_passed": None}
)


def _llm(**extra) -> StubLLMClient:
    canned = {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT}
    canned.update(extra)
    return StubLLMClient(canned=canned)


# ------------------------------------------------------------------ pure rules


def _node(title, parent=None, depth=0, position=0):
    return NodeRow(
        id=uuid4(), exploration_id=uuid4(), parent_id=parent.id if parent else None,
        title=title, summary=f"{title} summary", depth=depth, position=position,
    )


def test_selection_rule_chapters_and_lessons_follow_the_tree():
    a, b, c = _node("A", position=0), _node("B", position=1), _node("C", position=2)
    a1 = _node("A1", a, 1, 0)
    a1x = _node("A1x", a1, 2, 0)
    a2 = _node("A2", a, 1, 1)
    b1 = _node("B1", b, 1, 0)
    b2 = _node("B2", b, 1, 1)
    nodes = [c, b2, a1x, a, b, a1, a2, b1]
    chapters = build_selection(nodes, {a.id, a1x.id, a2.id, b2.id, c.id})
    assert [ch["node"].title for ch in chapters] == ["A", "B2", "C"]
    assert [n.title for n in chapters[0]["lessons"]] == ["A1x", "A2"]
    assert chapters[1]["lessons"] == [] and chapters[2]["lessons"] == []


def test_progress_is_the_latest_event_per_task():
    t1, t2, t3 = uuid4(), uuid4(), uuid4()
    events = [
        {"task_id": t1, "event": "completed"},
        {"task_id": t2, "event": "completed"},
        {"task_id": t2, "event": "reopened"},
        {"task_id": t3, "event": "reopened"},
        {"task_id": t3, "event": "completed"},
    ]
    assert derive_done_task_ids(events) == {t1, t3}


def test_tasks_are_normalized_to_end_in_exactly_one_check():
    tasks = normalize_tasks(
        [{"kind": "check", "description": "q1"}, {"kind": "learn", "description": "a"},
         {"kind": "bogus", "description": "x"}, {"kind": "check", "description": "q2"}],
        "Entropy",
    )
    assert [t["kind"] for t in tasks][-1] == "check"
    assert sum(t["kind"] == "check" for t in tasks) == 1
    assert 3 <= len(tasks) <= 5
    many = normalize_tasks([{"kind": "learn", "description": str(i)} for i in range(9)], "X")
    assert len(many) == 5 and many[-1]["kind"] == "check"
    assert normalize_tasks(None, "Heat")[-1]["description"].endswith("Heat.")


def test_personalization_is_described_only_from_what_was_used():
    assert describe_personalization({}) == []
    assert describe_personalization({"thinking_styles": 1, "knobs": {"depth": 80}}) == [
        "your thinking style", "your length and depth sliders",
    ]


# ------------------------------------------------------------------ full flow


async def _signals(pool, learner_id, kind=None):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT kind, payload FROM topic_signals WHERE learner_id = $1 "
            "AND ($2::text IS NULL OR kind = $2) ORDER BY created_at",
            __import__("uuid").UUID(learner_id), kind,
        )
    return rows


@pytest.mark.asyncio(loop_scope="session")
async def test_explore_expand_build_learn_and_progress(clean_pool, embedding_client):
    llm = _llm(**{"LESSON:JUDGE": _JUDGE_DONE})
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            learner = (await client.post("/api/learners", json={"label": "topic-flow"})).json()
            lid = learner["id"]

            r = await client.post("/api/topic-explorations", json={"learner_id": lid, "query": "thermodynamics"})
            assert r.status_code == 200, r.text
            exploration = r.json()
            roots = exploration["root_nodes"]
            assert exploration["source_kind"] == "search" and len(roots) == 5
            assert all(not n["expanded"] for n in roots)
            assert exploration["personalized_by"] == []  # a brand-new learner: nothing known

            kids = (await client.post(f"/api/topic-nodes/{roots[0]['id']}/expand", json={})).json()
            assert len(kids) == 5 and all(k["parent_id"] == roots[0]["id"] for k in kids)
            again = (await client.post(f"/api/topic-nodes/{roots[0]['id']}/expand")).json()
            assert [k["id"] for k in again] == [k["id"] for k in kids]  # idempotent

            tree = (await client.get(f"/api/topic-explorations/{exploration['id']}")).json()
            assert tree["root_nodes"][0]["expanded"] is True
            assert len(tree["root_nodes"][0]["children"]) == 5

            r = await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": exploration["id"],
                "selected_node_ids": [roots[0]["id"], kids[0]["id"], kids[1]["id"], roots[1]["id"]],
            })
            assert r.status_code == 200, r.text
            topic = r.json()
            assert topic["title"] == "thermodynamics" and topic["percent"] == 0
            ch0, ch1 = topic["chapters"]
            assert ch0["title"] == roots[0]["title"]
            assert [le["title"] for le in ch0["lessons"]] == [kids[0]["title"], kids[1]["title"]]
            assert [le["title"] for le in ch1["lessons"]] == ["Stub lesson 1", "Stub lesson 2", "Stub lesson 3"]
            assert all(le["status"] == "not_started" and le["tasks_total"] == 3
                       for ch in topic["chapters"] for le in ch["lessons"])

            summaries = (await client.get(f"/api/learners/{lid}/topics")).json()
            assert summaries[0]["id"] == topic["id"] and summaries[0]["lesson_count"] == 5

            lesson_id = ch0["lessons"][0]["id"]
            lesson = (await client.get(f"/api/lessons/{lesson_id}")).json()
            assert lesson["tasks"][-1]["kind"] == "check" and lesson["session_id"] is None
            sid = (await client.post(f"/api/lessons/{lesson_id}/start")).json()["session_id"]
            assert (await client.post(f"/api/lessons/{lesson_id}/start")).json()["session_id"] == sid

        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            events = await _turn(ws, {"type": "message", "text": "I'm ready, let's start this lesson."})
            assert events[-1]["type"] == "done"
            await live.loop.wait_for_background_tasks()
            progress = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert progress["type"] == "progress"
        assert progress["lesson_id"] == lesson_id
        assert progress["lesson_percent"] == 33 and progress["lesson_status"] == "in_progress"
        assert progress["chapter_percent"] == 16 or progress["chapter_percent"] == 17
        assert progress["topic_percent"] > 0

        answer_prompt = next(p for p in llm.prompts if p.startswith("FINAL:ANSWER"))
        assert "This conversation is a lesson in the student's own course" in answer_prompt
        assert "[CURRENT] 1." in answer_prompt
        assert roots[1]["title"] in answer_prompt  # the other chapter, for connections
        assess_prompt = next(p for p in llm.prompts if p.startswith("ASSESS:BRANCH"))
        assert "part of a lesson" in assess_prompt

        async with httpx.AsyncClient(base_url=live.http) as client:
            lesson = (await client.get(f"/api/lessons/{lesson_id}")).json()
            assert lesson["tasks"][0]["done"] is True and lesson["status"] == "in_progress"
            assert lesson["session_id"] == sid
            chats = (await client.get(f"/api/learners/{lid}/sessions/all")).json()
            assert chats[0]["lesson_id"] == lesson_id and chats[0]["app_mode"] == "topic"

        async with clean_pool.acquire() as conn:
            judged = await conn.fetchval(
                "SELECT count(*) FROM node_calls WHERE session_id = $1 AND node_name = 'JudgeLessonProgress'",
                __import__("uuid").UUID(sid),
            )
            generations = await conn.fetch(
                "SELECT node_name FROM topic_generations WHERE learner_id = $1",
                __import__("uuid").UUID(lid),
            )
        assert judged == 1
        names = [g["node_name"] for g in generations]
        assert names.count("GenerateBranches") == 2 and names.count("PlanLessons") == 2

        kinds = [s["kind"] for s in await _signals(clean_pool, lid)]
        for kind in ("search", "expand", "selection", "lesson_open", "task_completed"):
            assert kind in kinds, kind
        selection = (await _signals(clean_pool, lid, "selection"))[0]["payload"]
        not_chosen = {n["title"] for n in selection["shown_not_selected"]}
        assert roots[2]["title"] in not_chosen and selection["chapters"] == 2
        opens = await _signals(clean_pool, lid, "lesson_open")
        assert opens[0]["payload"]["sequential"] is True and opens[1]["payload"]["resumed"] is True
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_expand_more_appends_new_children_only(clean_pool, embedding_client):
    calls = {"n": 0}

    def branches(prompt: str) -> str:
        calls["n"] += 1
        return json.dumps({"branches": [
            {"title": f"Part {calls['n']}-{i}", "summary": "s"} for i in range(4)
        ]})

    live = await _start(clean_pool, _llm(**{"TOPIC:BRANCHES": branches}), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "more"})).json()["id"]
            root = (await client.post("/api/topic-explorations",
                                      json={"learner_id": lid, "query": "optics"})).json()["root_nodes"][0]
            first = (await client.post(f"/api/topic-nodes/{root['id']}/expand")).json()
            more = (await client.post(f"/api/topic-nodes/{root['id']}/expand", json={"more": True})).json()
        assert len(first) == 4 and len(more) == 8
        assert [m["id"] for m in more[:4]] == [f["id"] for f in first]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_task_not_done_sends_no_progress_but_records_drift(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(**{"LESSON:JUDGE": _JUDGE_NOT_DONE}), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "drift"})).json()["id"]
            ex = (await client.post("/api/topic-explorations", json={"learner_id": lid, "query": "waves"})).json()
            topic = (await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": ex["id"], "selected_node_ids": [ex["root_nodes"][0]["id"]],
            })).json()
            lesson_id = topic["chapters"][0]["lessons"][0]["id"]
            sid = (await client.post(f"/api/lessons/{lesson_id}/start")).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "text": "what's the capital of France?"})
            await live.loop.wait_for_background_tasks()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.recv(), timeout=1.5)
        drift = await _signals(clean_pool, lid, "chapter_drift")
        assert len(drift) == 1 and "France" in drift[0]["payload"]["message"]
        async with httpx.AsyncClient(base_url=live.http) as client:
            assert (await client.get(f"/api/topics/{topic['id']}")).json()["percent"] == 0
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_sandbox_prompts_and_node_inputs_carry_no_lesson_context(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "sandbox-plain"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "text": "what is entropy?"})
        await live.loop.wait_for_background_tasks()
        assert not any("lesson" in p.lower() for p in llm.prompts
                       if p.startswith(("FINAL:ANSWER", "ASSESS:BRANCH")))
        assert not any(p.startswith("LESSON:JUDGE") for p in llm.prompts)
        async with clean_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT node_name, input_json FROM node_calls WHERE session_id = $1",
                __import__("uuid").UUID(sid),
            )
        for row in rows:
            if row["node_name"] in ("FinalAnswer", "AssessAndBranch"):
                assert "lesson_context" not in row["input_json"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_what_is_known_about_the_learner_shapes_generation(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "known"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
            await client.patch(f"/api/sessions/{sid}/knobs", json={"answer_length": 20, "depth": 85})
        from uuid import UUID
        style = await ThinkingStyleStore(clean_pool).create_candidate(
            UUID(lid), UUID(sid), "wants the big picture before any detail", [0.1] * 768,
        )
        await ReviewStore(clean_pool).add(thinking_style_candidate_id=style.id, review_type="approve")

        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            ex = (await client.post("/api/topic-explorations", json={"learner_id": lid, "query": "genetics"})).json()
            topic = (await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": ex["id"], "selected_node_ids": [ex["root_nodes"][0]["id"]],
            })).json()
            lesson = (await client.get(f"/api/lessons/{topic['chapters'][0]['lessons'][0]['id']}")).json()
        branches_prompt = next(p for p in llm.prompts if p.startswith("TOPIC:BRANCHES"))
        assert "About this student" in branches_prompt
        assert "wants the big picture before any detail (student confirmed)" in branches_prompt
        assert "length 20/100, depth 85/100" in branches_prompt
        plan_prompt = next(p for p in llm.prompts if p.startswith("TOPIC:LESSONS"))
        assert "wants the big picture" in plan_prompt
        assert "your thinking style" in ex["personalized_by"]
        assert "your length and depth sliders" in ex["personalized_by"]
        assert "your thinking style" in topic["personalized_by"]
        assert "your thinking style" in lesson["personalized_by"]
        async with clean_pool.acquire() as conn:
            used = await conn.fetchval(
                "SELECT input_json->'personalization_used' FROM topic_generations "
                "WHERE learner_id = $1 AND node_name = 'GenerateBranches'", UUID(lid),
            )
        assert used["thinking_styles"] == 1 and used["knobs"]["depth"] == 85
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_from_link_and_from_pdf_build_a_tree_from_the_resource(clean_pool, embedding_client, monkeypatch):
    async def fake_fetch(url: str) -> resources.ExtractedResource:
        return resources.extract_html(
            "<title>Photosynthesis</title><h2>Light reactions</h2><p>ATP.</p>", url=url
        )

    monkeypatch.setattr(resources, "fetch_link", fake_fetch)
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "resources"})).json()["id"]
            ex = (await client.post("/api/topic-explorations/from-link",
                                    json={"learner_id": lid, "url": "https://example.test/p"})).json()
            assert ex["source_kind"] == "link" and ex["resource"]["title"] == "Photosynthesis"
            assert ex["root_nodes"][0]["children"][0]["title"] == "Key terms"  # 2 levels from the outline
            outline_prompt = next(p for p in llm.prompts if p.startswith("TOPIC:OUTLINE"))
            assert "- Light reactions" in outline_prompt and "ATP." in outline_prompt

            r = await client.post(
                "/api/topic-explorations/from-pdf", data={"learner_id": lid},
                files={"file": ("notes.pdf", make_pdf(["Entropy notes", "Second law"]), "application/pdf")},
            )
            assert r.status_code == 200, r.text
            assert r.json()["source_kind"] == "pdf" and r.json()["resource"]["filename"] == "notes.pdf"

            bad = await client.post(
                "/api/topic-explorations/from-pdf", data={"learner_id": lid},
                files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
            )
            assert bad.status_code == 422 and "not a PDF" in bad.json()["detail"]
        kinds = [s["kind"] for s in await _signals(clean_pool, lid, "resource")]
        assert kinds == ["resource", "resource"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unsafe_link_is_refused_before_any_request(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "ssrf"})).json()["id"]
            r = await client.post("/api/topic-explorations/from-link",
                                  json={"learner_id": lid, "url": "http://127.0.0.1:5435/"})
        assert r.status_code == 422 and "private or local" in r.json()["detail"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_lesson_chats_are_consolidated_like_any_other_chat(clean_pool, embedding_client, transcript):
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "consolidate"})).json()["id"]
            ex = (await client.post("/api/topic-explorations", json={"learner_id": lid, "query": "stats"})).json()
            topic = (await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": ex["id"], "selected_node_ids": [ex["root_nodes"][0]["id"]],
            })).json()
            sid = (await client.post(
                f"/api/lessons/{topic['chapters'][0]['lessons'][0]['id']}/start")).json()["session_id"]
            from uuid import UUID
            for i in range(live.loop.memory_config.min_turns_for_cli_auto_consolidation):
                await transcript.record_turn(UUID(sid), i, f"turn {i}")
            assert (await client.post(f"/api/sessions/{sid}/end")).json()["status"] == "scheduled"
    finally:
        await _stop(live)
