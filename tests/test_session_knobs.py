"""Per-session length/depth sliders (migrations 063/071, session_knobs.py) and
live answer regeneration (answer_versions.py): the level -> directive mapping,
the store, the PATCH endpoint, and the WebSocket `regenerate` flow, which
must record every rewrite (invariant 2) without touching the original."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
import websockets

from versa.answer_versions import AnswerVersionStore
from versa.disambiguate import FinalAnswer
from versa.llm import StubLLMClient
from versa.session_knobs import SessionKnobs, render_knob_directive, target_words

from tests.test_server import (  # noqa: F401  (fixtures)
    _ANSWER,
    _FACT,
    _NOT_AMBIGUOUS,
    _GatedLLM,
    _start,
    _stop,
    _turn,
    live,
    new_chat,
)

# ------------------------------------------------------------ the directive


def test_the_default_levels_render_to_the_empty_string():
    assert render_knob_directive(SessionKnobs()) == ""
    assert render_knob_directive(SessionKnobs(answer_length=50, depth=50)) == ""


def test_each_moved_slider_adds_its_own_line():
    only_length = render_knob_directive(SessionKnobs(answer_length=10))
    assert "Length: 10/100" in only_length and "Depth" not in only_length
    only_depth = render_knob_directive(SessionKnobs(depth=90))
    assert "Depth: 90/100" in only_depth and "rigorous" in only_depth and "Length" not in only_depth
    both = render_knob_directive(SessionKnobs(answer_length=0, depth=0))
    assert both.count("\n- ") == 2


def test_length_grows_with_the_level_and_neighbours_differ():
    words = [target_words(level) for level in range(0, 101, 10)]
    assert words == sorted(words) and words[0] < 30 and words[-1] > 1000
    assert target_words(50) == 167
    assert render_knob_directive(SessionKnobs(depth=61)) != render_knob_directive(SessionKnobs(depth=62))


def test_levels_outside_0_to_100_are_rejected():
    with pytest.raises(ValueError):
        SessionKnobs(answer_length=101)
    with pytest.raises(ValueError):
        SessionKnobs(depth=-1)


# ----------------------------------------------------------------- the store


@pytest.mark.asyncio(loop_scope="session")
async def test_knobs_round_trip_and_default_for_new_sessions(transcript, clean_pool, learner_id):
    session_id = await transcript.create_session(learner_id)
    assert await transcript.get_knobs(session_id) == SessionKnobs()
    await transcript.set_knobs(session_id, SessionKnobs(answer_length=12, depth=88))
    assert await transcript.get_knobs(session_id) == SessionKnobs(answer_length=12, depth=88)


@pytest.mark.asyncio(loop_scope="session")
async def test_knob_store_raises_for_an_unknown_session(transcript, clean_pool):
    with pytest.raises(KeyError):
        await transcript.get_knobs(uuid4())
    with pytest.raises(KeyError):
        await transcript.set_knobs(uuid4(), SessionKnobs())


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_versions_number_upward_per_turn(transcript, clean_pool, learner_id):
    store = AnswerVersionStore(clean_pool)
    session_id = await transcript.create_session(learner_id)
    assert await store.record(session_id, 0, "short", SessionKnobs(answer_length=5)) == 1
    assert await store.record(session_id, 0, "long", SessionKnobs(answer_length=95)) == 2
    assert await store.record(session_id, 1, "other turn", SessionKnobs()) == 1
    assert await store.latest_by_turn(session_id) == {0: (2, "long"), 1: (1, "other turn")}


@pytest.mark.asyncio(loop_scope="session")
async def test_final_answer_prompt_carries_the_directive_only_when_given():
    llm = StubLLMClient(canned={"FINAL:ANSWER": "ok"})
    node = FinalAnswer(llm)
    await node.run("what is a derivative?")
    plain = llm.prompts[-1]
    directive = render_knob_directive(SessionKnobs(answer_length=10))
    await node.run("what is a derivative?", knob_directive=directive)
    styled = llm.prompts[-1]

    assert "style controls" not in plain
    assert "style controls" in styled and "Length: 10/100" in styled
    assert styled.replace(directive, "") == plain


# ------------------------------------------------------------------ the API


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_updates_only_the_given_level_and_tone_is_gone(live, new_chat):  # noqa: F811
    _, sid = await new_chat("knobber")
    async with httpx.AsyncClient(base_url=live.http) as client:
        first = await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": 20})
        second = await client.patch(f"/api/sessions/{sid}/knobs", json={"answer_length": 80})
        fetched = await client.get(f"/api/sessions/{sid}/knobs")
    assert first.json() == {"answer_length": 50, "depth": 20}
    assert second.json() == {"answer_length": 80, "depth": 20}
    assert fetched.json() == second.json()
    assert "tone" not in fetched.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_rejects_out_of_range_levels_and_unknown_sessions(live, new_chat):  # noqa: F811
    _, sid = await new_chat("strict")
    async with httpx.AsyncClient(base_url=live.http) as client:
        bad = await client.patch(f"/api/sessions/{sid}/knobs", json={"answer_length": 101})
        word = await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": "rigorous"})
        missing = await client.patch(
            "/api/sessions/00000000-0000-0000-0000-000000000000/knobs", json={"depth": 10}
        )
    assert bad.status_code == 422 and word.status_code == 422
    assert missing.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_a_moved_slider_reaches_the_next_final_answer_and_is_recorded(live, new_chat, node_calls):  # noqa: F811
    _, sid = await new_chat("recorded")
    async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})
        while live.loop._background_tasks:
            await live.loop.wait_for_background_tasks()
        async with httpx.AsyncClient(base_url=live.http) as client:
            await client.patch(f"/api/sessions/{sid}/knobs", json={"answer_length": 10})
        await _turn(ws, {"type": "message", "text": "and an integral?"})

    first = await node_calls.get_call_for_turn(UUID(sid), 0, "FinalAnswer")
    second = await node_calls.get_call_for_turn(UUID(sid), 1, "FinalAnswer")
    assert first.input_json["knob_directive"] == ""
    assert "Length: 10/100" in second.input_json["knob_directive"]


# ------------------------------------------------------------ regeneration


async def _regen(ws, request_id) -> list[dict]:
    await ws.send(json.dumps({"type": "regenerate", "request_id": request_id}))
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        events.append(event)
        if event["type"] in ("regen_done", "regen_skipped", "regen_error", "error"):
            return events


@pytest.mark.asyncio(loop_scope="session")
async def test_regenerate_rewrites_the_latest_answer_without_touching_the_original(
    live, new_chat, node_calls, clean_pool  # noqa: F811
):
    _, sid = await new_chat("regen")
    async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})
        async with httpx.AsyncClient(base_url=live.http) as client:
            await client.patch(f"/api/sessions/{sid}/knobs", json={"answer_length": 90, "depth": 90})
        events = await _regen(ws, 7)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "regen_start" and "regen_delta" in kinds and kinds[-1] == "regen_done"
    assert all(e["request_id"] == 7 for e in events)
    done = events[-1]
    assert done["turn_index"] == 0 and done["version"] == 1 and done["text"] == _ANSWER
    assert "".join(e["text"] for e in events if e["type"] == "regen_delta") == _ANSWER

    original = await node_calls.get_call_for_turn(UUID(sid), 0, "FinalAnswer")
    rewrite = await node_calls.get_call_for_turn(UUID(sid), 0, "RegenerateAnswer")
    assert original.input_json["knob_directive"] == "" and original.output_json == _ANSWER
    assert "Length: 90/100" in rewrite.input_json["knob_directive"]
    same = {k: v for k, v in rewrite.input_json.items() if k != "knob_directive"}
    assert same == {k: v for k, v in original.input_json.items() if k != "knob_directive"}
    assert await AnswerVersionStore(clean_pool).latest_by_turn(UUID(sid)) == {0: (1, _ANSWER)}


@pytest.mark.asyncio(loop_scope="session")
async def test_history_shows_the_latest_version_of_a_rewritten_answer(live, new_chat, clean_pool):  # noqa: F811
    _, sid = await new_chat("resumer")
    async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})
    while live.loop._background_tasks:
        await live.loop.wait_for_background_tasks()
    await AnswerVersionStore(clean_pool).record(UUID(sid), 0, "the rewritten answer", SessionKnobs(depth=5))
    async with httpx.AsyncClient(base_url=live.http) as client:
        history = (await client.get(f"/api/sessions/{sid}/history")).json()
    assert history[0]["kind"] == "answer" and history[0]["tutor_text"] == "the rewritten answer"


@pytest.mark.asyncio(loop_scope="session")
async def test_regenerate_with_no_answer_yet_is_skipped(live, new_chat):  # noqa: F811
    _, sid = await new_chat("nothing-yet")
    async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
        events = await _regen(ws, 1)
    assert [e["type"] for e in events] == ["regen_start", "regen_skipped"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_newer_regenerate_cancels_the_one_in_flight(clean_pool, embedding_client, node_calls):
    gated = _GatedLLM({"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    live_server = await _start(clean_pool, gated, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live_server.http) as client:
            learner = (await client.post("/api/learners", json={"label": "slider"})).json()
            sid = (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()["session_id"]
        async with websockets.connect(f"{live_server.ws}/api/sessions/{sid}/chat") as ws:
            gated.gate.set()
            await _turn(ws, {"type": "message", "text": "what is a derivative?"})
            while live_server.loop._background_tasks:
                await live_server.loop.wait_for_background_tasks()
            gated.gate.clear()
            gated.answer_started.clear()

            await ws.send(json.dumps({"type": "regenerate", "request_id": 1}))
            await asyncio.wait_for(gated.answer_started.wait(), timeout=10)
            gated.answer_started.clear()
            await ws.send(json.dumps({"type": "regenerate", "request_id": 2}))
            await asyncio.wait_for(gated.answer_started.wait(), timeout=10)
            gated.gate.set()

            events = []
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                events.append(event)
                if event["type"] == "regen_done":
                    break
        assert events[-1]["request_id"] == 2 and events[-1]["version"] == 1
        assert not [e for e in events if e["type"] == "regen_done" and e["request_id"] == 1]
        rewrites = await node_calls.list_calls_for_session(UUID(sid), "RegenerateAnswer")
        assert len(rewrites) == 1
        assert await AnswerVersionStore(clean_pool).latest_by_turn(UUID(sid)) == {0: (1, _ANSWER)}
    finally:
        gated.gate.set()
        await _stop(live_server)
