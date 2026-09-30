"""Revision notes of a chat (notes.py, migration 091, CLAUDE.md invariant 22):
made only when the learner asks, from the whole chat so far, pitched to them,
stored append-only, and drawn as a PDF on request."""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import websockets

import versa.notes as notes_module
from tests.test_rooms_append_only import _string_literals
from tests.test_server import (  # noqa: F401  (fixtures)
    _plain_llm,
    _start,
    _stop,
    _turn,
    live,
    new_chat,
)
from versa.llm import _DEFAULT_RESPONSES
from versa.models import HistoryTurn
from versa.notes import (
    Notes,
    NoteStore,
    notes_prompt,
    parse_notes,
    pdf_filename,
    render_chat,
    render_pdf,
)

# ------------------------------------------------------------- invariant 22


def test_notes_module_never_deletes_or_updates():
    path = Path(notes_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_notes_migration_has_no_delete_or_update():
    path = Path(notes_module.__file__).resolve().parent / "migrations" / "091_notes.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_note_store_has_no_removal_methods():
    for name in dir(NoteStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name


# ------------------------------------------------------------------ pure


def _t(i, kind="answer", student="q", tutor="a"):
    return HistoryTurn(turn_index=i, student_text=student, kind=kind, tutor_text=tutor)


def test_only_answered_turns_are_the_chat():
    turns = [
        _t(0, student="what is inertia?", tutor="It is resistance to a change in motion."),
        _t(1, kind="options", student="and the other one?", tutor=None),
        _t(2, student=None, tutor="The second law: $F = ma$."),  # a click on an offered reading
        _t(3, kind="pending", student="hmm", tutor=None),
    ]
    text, answers, last = render_chat(turns)
    assert answers == 2 and last == 2
    assert "Student: what is inertia?" in text and "$F = ma$" in text
    assert "picked one of the readings" in text and "and the other one?" not in text
    assert render_chat([_t(0, kind="options", tutor=None)]) == ("", 0, -1)


def test_a_long_chat_shortens_answers_never_questions():
    turns = [_t(i, student=f"question {i}", tutor="x" * 5000) for i in range(40)]
    text, answers, _ = render_chat(turns, limit=40_000)
    assert answers == 40 and len(text) < 45_000
    assert all(f"question {i}" in text for i in range(40)) and "[...]" in text


def test_the_prompt_is_the_whole_chat_pitched_to_the_learner():
    p = notes_prompt("Student: hi\nVersa: hello", background="\nWho this learner is: Class 9\n",
                     style=["starts from a concrete example"])
    assert p.startswith("NOTES:WRITE") and "Student: hi" in p and "Class 9" in p
    assert "starts from a concrete example" in p and "never WHAT is true" in p
    assert "nothing it didn't" in p  # no new material
    assert "confirmed patterns" not in notes_prompt("x")


def test_the_reply_is_read_and_tidied():
    n = parse_notes(_DEFAULT_RESPONSES["NOTES:WRITE"])
    assert n is not None and n.title and len(n.topics) == 2 and n.summary
    raw = json.dumps({
        "title": "", "summary": " all of it ",
        "topics": [
            {"name": "Forces", "points": ["push", " ", "pull"], "formulas": ["$$F = ma$$", "$p=mv$", ""],
             "example": ""},
            {"name": "Empty", "points": [], "formulas": []},
        ],
    })
    n = parse_notes(raw)
    assert n.title == "Forces" and n.summary == "all of it" and len(n.topics) == 1
    assert n.topics[0].points == ["push", "pull"] and n.topics[0].formulas == ["F = ma", "p=mv"]
    assert n.topics[0].example is None and n.stopped_at is None
    assert parse_notes("not json") is None
    assert parse_notes(json.dumps({"title": "t", "topics": [], "summary": "s"})) is None


def test_the_pdf_draws_the_notes_even_maths_it_cannot_typeset():
    n = parse_notes(_DEFAULT_RESPONSES["NOTES:WRITE"])
    n.topics[0].formulas.append("\\begin{pmatrix}1\\\\2\\end{pmatrix}")
    n.summary += "\n\n$$E = mc^2$$\n\nAnd **bold**, *italic*, `code` & <angles>."
    data = render_pdf(n, learner="Asha", mode="Sandbox")
    assert data.startswith(b"%PDF") and len(data) > 5000
    assert pdf_filename(n) == "versa-notes-solving-quadratic-equations.pdf"
    assert pdf_filename(Notes(title="∑", topics=n.topics, summary="s")) == "versa-notes-chat.pdf"


# --------------------------------------------------------------- the API


async def _chat_with_answers(live, new_chat, n=2):
    learner_id, session_id = await new_chat()
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        for i in range(n):
            events = await _turn(ws, {"type": "message", "text": f"explain part {i} of photosynthesis"})
            assert events[-1]["type"] == "done", events
    await live.loop.wait_for_background_tasks()
    return learner_id, session_id


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_is_made_until_asked_and_an_empty_chat_has_nothing(live, new_chat, clean_pool):
    _, session_id = await new_chat()
    async with httpx.AsyncClient(base_url=live.http) as client:
        status = (await client.get(f"/api/sessions/{session_id}/notes")).json()
        assert status["notes"] is None and status["answers"] == 0 and status["up_to_date"] is False
        assert status["cost"] == 2
        r = await client.post(f"/api/sessions/{session_id}/notes")
        assert r.status_code == 422 and "nothing to make notes from" in r.json()["detail"]
    assert await clean_pool.fetchval("SELECT count(*) FROM notes") == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_notes_are_written_once_kept_and_drawn_as_a_pdf(live, new_chat, clean_pool):
    learner_id, session_id = await _chat_with_answers(live, new_chat)
    async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
        before = (await client.get(f"/api/sessions/{session_id}/notes")).json()
        assert before["notes"] is None and before["answers"] == 2
        assert await clean_pool.fetchval("SELECT count(*) FROM notes") == 0  # opening the sheet makes nothing

        made = await client.post(f"/api/sessions/{session_id}/notes")
        assert made.status_code == 200, made.text
        body = made.json()
        assert body["title"] == "Solving quadratic equations" and len(body["topics"]) == 2
        assert body["answers"] == 2 and body["session_id"] == session_id

        # asking again with nothing new studied: the same notes, no new row, no new charge
        again = (await client.post(f"/api/sessions/{session_id}/notes")).json()
        assert again["id"] == body["id"]
        status = (await client.get(f"/api/sessions/{session_id}/notes")).json()
        assert status["up_to_date"] is True and status["notes"]["id"] == body["id"]

        pdf = await client.get(f"/api/sessions/{session_id}/notes/{body['id']}/pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        assert pdf.headers["content-type"] == "application/pdf"
        assert "versa-notes-solving-quadratic-equations.pdf" in pdf.headers["content-disposition"]

    rows = await clean_pool.fetch("SELECT prompt, raw_output, content, error, through_turn FROM notes")
    assert len(rows) == 1
    row = rows[0]
    assert row["prompt"].startswith("NOTES:WRITE") and "explain part 1 of photosynthesis" in row["prompt"]
    assert row["error"] is None and row["content"] is not None and row["through_turn"] == 1
    spent = await clean_pool.fetch(
        "SELECT amount FROM spark_events WHERE learner_id = $1 AND action = 'generate_notes' AND kind = 'spend'",
        UUID(learner_id),
    )
    assert [abs(r["amount"]) for r in spent] == [2]  # charged once, for the one time the notes were written


@pytest.mark.asyncio(loop_scope="session")
async def test_studying_more_makes_the_notes_out_of_date_until_asked_again(live, new_chat, clean_pool):
    _, session_id = await _chat_with_answers(live, new_chat, n=1)
    async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
        first = (await client.post(f"/api/sessions/{session_id}/notes")).json()
        async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
            assert (await _turn(ws, {"type": "message", "text": "and the dark reactions?"}))[-1]["type"] == "done"
        await live.loop.wait_for_background_tasks()
        status = (await client.get(f"/api/sessions/{session_id}/notes")).json()
        assert status["up_to_date"] is False and status["answers"] == 2 and status["notes"]["id"] == first["id"]
        second = (await client.post(f"/api/sessions/{session_id}/notes")).json()
        assert second["id"] != first["id"] and second["answers"] == 2
    assert await clean_pool.fetchval("SELECT count(*) FROM notes") == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_pdf_is_only_for_its_own_chat(live, new_chat):
    _, session_id = await _chat_with_answers(live, new_chat, n=1)
    _, other_session = await _chat_with_answers(live, new_chat, n=1)
    async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
        note_id = (await client.post(f"/api/sessions/{session_id}/notes")).json()["id"]
        assert (await client.get(f"/api/sessions/{other_session}/notes/{note_id}/pdf")).status_code == 404
        unknown = "00000000-0000-0000-0000-000000000000"
        assert (await client.get(f"/api/sessions/{unknown}/notes")).status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_notes_that_fail_are_recorded_and_refunded(clean_pool, embedding_client):
    llm = _plain_llm()
    llm.canned["NOTES:WRITE"] = "sorry, no JSON today"
    server = await _start(clean_pool, llm, embedding_client)
    try:
        async def make(label="tester"):
            async with httpx.AsyncClient(base_url=server.http) as client:
                learner = (await client.post("/api/learners", json={"label": label})).json()
                session = (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()
            return learner["id"], session["session_id"]

        learner_id, session_id = await _chat_with_answers(server, make, n=1)
        async with httpx.AsyncClient(base_url=server.http, timeout=30) as client:
            r = await client.post(f"/api/sessions/{session_id}/notes")
            assert r.status_code == 502 and "try again" in r.json()["detail"]
            status = (await client.get(f"/api/sessions/{session_id}/notes")).json()
            assert status["notes"] is None
    finally:
        await _stop(server)
    row = await clean_pool.fetchrow("SELECT content, error, raw_output FROM notes")
    assert row["content"] is None and row["error"] == "unreadable reply" and row["raw_output"] == "sorry, no JSON today"
    kinds = [r["kind"] for r in await clean_pool.fetch(
        "SELECT kind FROM spark_events WHERE learner_id = $1 AND action = 'generate_notes' ORDER BY seq",
        UUID(learner_id))]
    assert kinds == ["spend", "refund"]
