"""The API the Versa app talks to (server.py), exercised over a REAL uvicorn
server and a REAL WebSocket inside the test's event loop -- so streaming,
disconnects and concurrent messages are tested as the app will hit them.
The LLM is a stub; the database is the real test database.
"""

import asyncio
import json
import re
import socket
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import uvicorn
import websockets

from versa.llm import ModelTierClients, StubLLMClient
from versa.server import create_app

_NOT_AMBIGUOUS = json.dumps({"needs_branches": False, "branches": []})
_TWO_BRANCHES = json.dumps(
    {
        "needs_branches": True,
        "branches": [
            {"statement": "wants the power rule explained"},
            {"statement": "wants a worked numeric example"},
        ],
    }
)
_FACT = json.dumps({"situation": "asked something", "resolution": "answered it"})
_ANSWER = "the direct answer to your question"


def _options_for_branches(prompt: str) -> str:
    ids = re.findall(r"id=([0-9a-f-]{36})", prompt)
    return json.dumps(
        {
            "kind": "subject",
            "axis": None,
            "options": [{"branch_id": bid, "text": f"reading {i}"} for i, bid in enumerate(ids)],
        }
    )


class _GatedLLM:
    """StubLLMClient whose streamed FINAL:ANSWER waits for `release()` --
    holds a turn open so tests can act while it is in flight."""

    def __init__(self, canned: dict) -> None:
        self._inner = StubLLMClient(canned=canned)
        self.gate = asyncio.Event()
        self.answer_started = asyncio.Event()

    async def complete(self, prompt: str) -> str:
        return await self._inner.complete(prompt)

    async def stream(self, prompt: str):
        if prompt.startswith("FINAL:ANSWER"):
            self.answer_started.set()
            await self.gate.wait()
        async for piece in self._inner.stream(prompt):
            yield piece


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Live:
    def __init__(self, port, app, server, task):
        self.port, self.app, self.server, self.task = port, app, server, task
        self.http = f"http://127.0.0.1:{port}"
        self.ws = f"ws://127.0.0.1:{port}"

    @property
    def loop(self):
        return self.app.state.loop


async def _start(pool, llm, embedding_client, web_dir=None) -> _Live:
    app = create_app(
        pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client,
        llm_mode="stub", web_dir=web_dir,
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn did not start"
    return _Live(port, app, server, task)


async def _stop(live: _Live) -> None:
    # let deferred post-response writes land before the next test wipes the DB
    while live.loop._background_tasks:
        await live.loop.wait_for_background_tasks()
    live.server.should_exit = True
    await live.task


def _plain_llm() -> StubLLMClient:
    return StubLLMClient(
        canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT}
    )


@pytest_asyncio.fixture(loop_scope="session")
async def live(clean_pool, embedding_client):
    server = await _start(clean_pool, _plain_llm(), embedding_client)
    yield server
    await _stop(server)


@pytest_asyncio.fixture(loop_scope="session")
async def new_chat(live):
    """-> (learner_id, session_id) for a brand-new learner and chat."""

    async def _make(label="tester"):
        async with httpx.AsyncClient(base_url=live.http) as client:
            learner = (await client.post("/api/learners", json={"label": label})).json()
            session = (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()
        return learner["id"], session["session_id"]

    return _make


async def _turn(ws, payload: dict) -> list[dict]:
    """Send one client frame; collect server events through `done`/`error`."""
    await ws.send(json.dumps(payload))
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        events.append(event)
        if event["type"] in ("done", "error"):
            return events


# ------------------------------------------------------------------- REST


@pytest.mark.asyncio(loop_scope="session")
async def test_health_reports_ok_and_the_llm_mode(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        assert (await client.get("/api/health")).json() == {"status": "ok", "llm": "stub"}


@pytest.mark.asyncio(loop_scope="session")
async def test_learner_by_name_is_get_or_create(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        first = (await client.post("/api/learners", json={"label": "  Asha  "})).json()
        again = (await client.post("/api/learners", json={"label": "Asha"})).json()
        other = (await client.post("/api/learners", json={"label": "Ben"})).json()
        blank = await client.post("/api/learners", json={"label": "   "})
    assert first["id"] == again["id"] and first["label"] == "Asha"
    assert other["id"] != first["id"]
    assert blank.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_session_for_an_unknown_learner_is_404(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.post(
            "/api/sessions", json={"learner_id": "00000000-0000-0000-0000-000000000000"}
        )
    assert r.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_the_browser_may_call_from_a_localhost_dev_origin(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.get("/api/health", headers={"Origin": "http://localhost:51234"})
        stranger = await client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:51234"
    assert "access-control-allow-origin" not in stranger.headers


@pytest.mark.asyncio(loop_scope="session")
async def test_list_sessions_shows_a_turnless_chat_as_new_and_orders_by_activity(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        learner = (await client.post("/api/learners", json={"label": "sidebar"})).json()
        lid = learner["id"]
        first = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        second = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        rows = (await client.get(f"/api/learners/{lid}/sessions", params={"mode": "sandbox"})).json()

    assert [r["session_id"] for r in rows] == [second, first], "newest chat first, none used yet"
    assert all(r["preview"] is None and r["turn_count"] == 0 for r in rows)
    assert all(r["app_mode"] == "sandbox" for r in rows)


@pytest.mark.asyncio(loop_scope="session")
async def test_list_sessions_for_an_unknown_learner_is_404(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.get(
            "/api/learners/00000000-0000-0000-0000-000000000000/sessions",
            params={"mode": "sandbox"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_session_history_for_an_unknown_session_is_404(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.get("/api/sessions/00000000-0000-0000-0000-000000000000/history")
    assert r.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_session_history_reconstructs_a_real_conversation_and_appears_in_the_sidebar(
    live, new_chat
):
    learner_id, session_id = await new_chat("resume")
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})

    async with httpx.AsyncClient(base_url=live.http) as client:
        history = (await client.get(f"/api/sessions/{session_id}/history")).json()
        rows = (
            await client.get(f"/api/learners/{learner_id}/sessions", params={"mode": "sandbox"})
        ).json()

    (turn,) = history
    assert turn == {
        "turn_index": 0,
        "student_text": "what is a derivative?",
        "kind": "answer",
        "tutor_text": _ANSWER,
        "options_message": None,
        "options": [],
    }
    (row,) = [r for r in rows if r["session_id"] == session_id]
    assert row["turn_count"] == 1
    assert row["preview"] == "what is a derivative?"


# ------------------------------------------------------------------- chat


@pytest.mark.asyncio(loop_scope="session")
async def test_a_direct_answer_streams_then_finishes(live, new_chat):
    _, session_id = await new_chat()
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        events = await _turn(ws, {"type": "message", "text": "what is a derivative?"})

    types = [e["type"] for e in events]
    assert types[0] == "turn_start" and types[-1] == "done"
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert len(deltas) > 1, "the answer must arrive in pieces"
    done = events[-1]
    assert "".join(deltas) == done["text"] == _ANSWER
    assert done["kind"] == "answer" and done["turn_index"] == 0
    assert 0 < done["timing"]["first_output_ms"] <= done["timing"]["total_ms"]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_ambiguous_message_offers_options_and_a_click_answers(clean_pool, embedding_client):
    llm = StubLLMClient(
        canned={
            "ASSESS:BRANCH": _TWO_BRANCHES,
            "DISAMBIGUATE:OPTIONS": _options_for_branches,
            "FINAL:ANSWER": "the power rule says ...",
            "WRITE:FACT": _FACT,
        }
    )
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            learner = (await client.post("/api/learners", json={"label": "opts"})).json()
            sid = (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()[
                "session_id"
            ]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            first = await _turn(ws, {"type": "message", "text": "can you help me with derivatives?"})
            kinds = [e["type"] for e in first]
            assert kinds == ["turn_start", "options", "done"], kinds
            options = first[1]["options"]
            assert len(options) == 2 and all({"id", "text"} <= set(o) for o in options)
            assert first[-1]["kind"] == "options"

            second = await _turn(ws, {"type": "select_option", "option_id": options[0]["id"]})
            assert second[-1]["kind"] == "answer" and second[-1]["turn_index"] == 1
            assert "".join(e["text"] for e in second if e["type"] == "delta") == "the power rule says ..."

            # a double-tap / stale button is refused, never replayed as a new turn
            again = await _turn(ws, {"type": "select_option", "option_id": options[0]["id"]})
            assert again[-1]["type"] == "error"
            assert "no longer available" in again[-1]["message"]
            other = await _turn(ws, {"type": "select_option", "option_id": options[1]["id"]})
            assert other[-1]["type"] == "error", "the sibling reading was superseded by the click"
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_bad_frames_get_an_error_and_the_socket_stays_usable(live, new_chat):
    _, session_id = await new_chat()
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        assert (await _turn(ws, {"type": "message", "text": "   "}))[-1]["type"] == "error"
        assert (await _turn(ws, {"type": "wat"}))[-1]["type"] == "error"
        assert (await _turn(ws, {"type": "select_option", "option_id": "not-a-uuid"}))[-1][
            "type"
        ] == "error"
        assert (await _turn(ws, {"type": "message", "text": "hello"}))[-1]["type"] == "done"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unknown_session_is_rejected(live):
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(
            f"{live.ws}/api/sessions/00000000-0000-0000-0000-000000000000/chat"
        ):
            pass


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_connection_messaging_a_busy_session_is_refused(clean_pool, embedding_client):
    gated = _GatedLLM({"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    live = await _start(clean_pool, gated, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            learner = (await client.post("/api/learners", json={"label": "busy"})).json()
            sid = (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()[
                "session_id"
            ]
        url = f"{live.ws}/api/sessions/{sid}/chat"
        async with websockets.connect(url) as first, websockets.connect(url) as other_tab:
            await first.send(json.dumps({"type": "message", "text": "first"}))
            await asyncio.wait_for(gated.answer_started.wait(), timeout=10)

            refused = await _turn(other_tab, {"type": "message", "text": "from another tab"})
            assert refused[-1]["type"] == "error"
            assert "already running" in refused[-1]["message"]

            gated.gate.set()
            events = []
            while not events or events[-1]["type"] != "done":
                events.append(json.loads(await asyncio.wait_for(first.recv(), timeout=10)))
            assert events[-1]["turn_index"] == 0
    finally:
        gated.gate.set()
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_two_messages_on_one_socket_are_handled_in_order(live, new_chat):
    _, session_id = await new_chat("queue")
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        await ws.send(json.dumps({"type": "message", "text": "first question"}))
        await ws.send(json.dumps({"type": "message", "text": "second question"}))
        dones = []
        while len(dones) < 2:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if event["type"] == "done":
                dones.append(event["turn_index"])
    assert dones == [0, 1]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_client_that_disconnects_mid_answer_does_not_lose_the_turn(
    clean_pool, embedding_client, node_calls, learner_fact_store,
):
    gated = _GatedLLM({"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    live = await _start(clean_pool, gated, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            learner = (await client.post("/api/learners", json={"label": "vanish"})).json()
            sid = (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()[
                "session_id"
            ]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await ws.send(json.dumps({"type": "message", "text": "will you still answer?"}))
            await asyncio.wait_for(gated.answer_started.wait(), timeout=10)
        # socket closed while the answer is still being written
        gated.gate.set()
        await asyncio.sleep(0.5)
        while live.loop._background_tasks:
            await live.loop.wait_for_background_tasks()

        call = await node_calls.get_call_for_turn(UUID(sid), 0, "FinalAnswer")
        assert call is not None and call.output_json == _ANSWER, "the answer must still be persisted"
        assert len(await learner_fact_store.list_by_learner(UUID(learner["id"]))) == 1
    finally:
        gated.gate.set()
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_next_message_sees_state_from_the_previous_turns_background_writes(live, new_chat):
    _, session_id = await new_chat("memory")
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})
        # immediately again: the loop waits for turn 0's background tail first
        second = await _turn(ws, {"type": "message", "text": "and an integral?"})
    assert second[-1]["type"] == "done" and second[-1]["turn_index"] == 1


# ------------------------------------------------------------- web app


@pytest.mark.asyncio(loop_scope="session")
async def test_a_built_web_app_is_served_at_root_without_shadowing_the_api(
    clean_pool, embedding_client, tmp_path,
):
    (tmp_path / "index.html").write_text("<html><body>versa app</body></html>", encoding="utf-8")
    live = await _start(clean_pool, _plain_llm(), embedding_client, web_dir=tmp_path)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            root = await client.get("/")
            health = await client.get("/api/health")
        assert "versa app" in root.text
        assert health.json()["status"] == "ok"
    finally:
        await _stop(live)
