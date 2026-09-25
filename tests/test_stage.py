"""The stage director (stage.py): validation of model-written actions, the
JSON Lines streaming, and the server's `stage_*` events running alongside
an answer."""
import asyncio
import json
from uuid import UUID

import httpx
import pytest
import websockets

from tests.test_server import (
    _FACT,
    _NOT_AMBIGUOUS,
    _TWO_BRANCHES,
    _options_for_branches,
    _start,
    _stop,
)
from versa.audit import NodeCallStore
from versa.llm import StubLLMClient
from versa.stage import (
    MAX_ACTIONS,
    StageDirector,
    parse_script_lines,
    sanitize_action,
    stage_sink,
)

_SKIT = "\n".join(
    json.dumps(a)
    for a in [
        {"do": "emote", "mood": "excited"},
        {"do": "spawn", "id": "apple", "kind": "emoji", "label": "A", "x": 0.6, "y": 0.3},
        {"do": "move", "target": "apple", "y": 0.62, "style": "fall", "ms": 400},
        {"do": "say", "text": "Gravity!"},
    ]
)

# ------------------------------------------------------------- validation


def test_sanitize_clamps_trims_and_drops_unknown_fields():
    a = sanitize_action({"do": "spawn", "id": "x" * 50, "kind": "emoji", "label": "A",
                         "x": 7, "y": -3, "size": 99, "bogus": 1, "color": "#ff00ff"})
    assert a == {"do": "spawn", "id": "x" * 24, "kind": "emoji", "label": "A", "x": 0.97, "y": 0.04, "size": 3.0}


def test_sanitize_drops_asks_unknowns_and_actions_missing_their_target():
    assert sanitize_action({"do": "ask", "question": "which?"}) is None
    assert sanitize_action({"do": "teleport"}) is None
    assert sanitize_action({"do": "push", "dx": 0.2}) is None
    assert sanitize_action({"do": "say"}) is None
    assert sanitize_action("not an object") is None
    assert sanitize_action({"do": "emote", "mood": "furious"}) == {"do": "emote"}


def test_sanitize_nested_together_is_one_level_deep_only():
    a = sanitize_action({"do": "together", "actions": [
        {"do": "jump"},
        {"do": "together", "actions": [{"do": "jump"}]},
        {"do": "ask"},
    ]})
    assert a == {"do": "together", "actions": [{"do": "jump"}]}


def test_unknown_spawn_kind_is_kept_so_the_app_can_show_its_name():
    assert sanitize_action({"do": "spawn", "id": "v", "kind": "volcano"})["kind"] == "volcano"


def test_parse_accepts_lines_fences_trailing_commas_or_a_whole_array():
    lines = '```json\n{"do":"jump"},\nnot json\n{"do":"emote","mood":"happy"}\n```'
    assert parse_script_lines(lines) == [{"do": "jump"}, {"do": "emote", "mood": "happy"}]
    array = json.dumps([{"do": "jump"}, {"do": "wait", "ms": 100}])
    assert parse_script_lines(array) == [{"do": "jump"}, {"do": "wait", "ms": 100}]
    assert parse_script_lines("no actions here") == []


# ---------------------------------------------------------------- the node


@pytest.mark.asyncio
async def test_each_action_reaches_the_sink_as_its_line_completes():
    director = StageDirector(StubLLMClient(canned={"STAGE:DIRECT": _SKIT}))
    seen: list[dict] = []

    async def sink(action: dict) -> None:
        seen.append(action)

    token = stage_sink.set(sink)
    try:
        script = await director.run(message="why do apples fall?")
    finally:
        stage_sink.reset(token)
    assert [a["do"] for a in script] == ["emote", "spawn", "move", "say"]
    assert seen == script
    assert director.last_call_count == 1


@pytest.mark.asyncio
async def test_without_a_sink_it_still_returns_the_script():
    director = StageDirector(StubLLMClient(canned={"STAGE:DIRECT": _SKIT}))
    assert len(await director.run(message="x")) == 4


@pytest.mark.asyncio
async def test_a_runaway_script_is_capped():
    endless = "\n".join(json.dumps({"do": "jump"}) for _ in range(MAX_ACTIONS * 2))
    director = StageDirector(StubLLMClient(canned={"STAGE:DIRECT": endless}))
    assert len(await director.run(message="x")) == MAX_ACTIONS


# ------------------------------------------------------------------ server


async def _collect(ws, until: str, timeout: float = 15) -> list[dict]:
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        events.append(event)
        if event["type"] in (until, "error"):
            return events


async def _chat(live) -> str:
    async with httpx.AsyncClient(base_url=live.http) as client:
        learner = (await client.post("/api/learners", json={"label": "stage"})).json()
        return (await client.post("/api/sessions", json={"learner_id": learner["id"]})).json()["session_id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_answer_turn_with_stage_on_streams_a_performance_alongside(clean_pool, embedding_client):
    llm = StubLLMClient(canned={
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": "apples fall because of gravity",
        "WRITE:FACT": _FACT, "STAGE:DIRECT": _SKIT,
    })
    live = await _start(clean_pool, llm, embedding_client)
    try:
        sid = await _chat(live)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await ws.send(json.dumps({"type": "message", "text": "why do apples fall?", "stage": True}))
            events = await _collect(ws, "done")
            if not any(e["type"] == "stage_end" for e in events):
                events += await _collect(ws, "stage_end")
        types = [e["type"] for e in events]
        start, first_delta = types.index("stage_start"), types.index("delta")
        assert start < first_delta and set(types[start + 1:first_delta]) <= {"stage", "stage_end"}, (
            "held until the answer's first word, then released (with anything already "
            "generated) right before that word"
        )
        actions = [e["action"] for e in events if e["type"] == "stage"]
        assert [a["do"] for a in actions] == ["emote", "spawn", "move", "say"]
        assert all(e["turn_index"] == 0 for e in events if e["type"].startswith("stage"))

        calls = await NodeCallStore(clean_pool).list_calls_for_session(UUID(sid), "StageDirector")
        assert len(calls) == 1, "recorded to node_calls like every node (invariant 2)"
        assert calls[0].input_json == {"message": "why do apples fall?"}
        assert calls[0].output_json == actions
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_no_stage_flag_or_an_options_turn_means_no_performance(clean_pool, embedding_client):
    llm = StubLLMClient(canned={
        "ASSESS:BRANCH": _TWO_BRANCHES, "DISAMBIGUATE:OPTIONS": _options_for_branches,
        "FINAL:ANSWER": "the power rule says ...", "WRITE:FACT": _FACT, "STAGE:DIRECT": _SKIT,
    })
    live = await _start(clean_pool, llm, embedding_client)
    try:
        sid = await _chat(live)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await ws.send(json.dumps({"type": "message", "text": "help with derivatives?", "stage": True}))
            first = await _collect(ws, "done")
            assert [e["type"] for e in first] == ["turn_start", "options", "done"], "the slime asks instead"

            option_id = first[1]["options"][0]["id"]
            await ws.send(json.dumps({"type": "select_option", "option_id": option_id}))
            second = await _collect(ws, "done")
            assert not any(e["type"].startswith("stage") for e in second), "stage wasn't asked for"
            await asyncio.sleep(0.3)
        calls = await NodeCallStore(clean_pool).list_calls_for_session(UUID(sid), "StageDirector")
        assert [c.turn_index for c in calls] == [0], (
            "the options turn's performance was generated (and recorded) but never shown; "
            "the unflagged turn generated none"
        )
    finally:
        await _stop(live)
