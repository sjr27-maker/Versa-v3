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
    stage_prompt,
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


def test_sanitize_drops_unanswerable_asks_unknowns_and_actions_missing_their_target():
    assert sanitize_action({"do": "ask", "question": "which?"}) is None  # no choices
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


def test_note_and_a_quick_check_ask_are_kept_and_bounded():
    assert sanitize_action({"do": "section", "n": 2}) is None, "no paragraph markers any more"
    note = sanitize_action({"do": "note", "text": "  A derivative is   a rate of change. " + "x" * 200})
    assert note["text"].startswith("A derivative is a rate of change.") and len(note["text"]) == 120
    assert sanitize_action({"do": "note", "text": "  "}) is None
    ask = sanitize_action({
        "do": "ask", "question": "What is the slope of x^2 at x=3?",
        "choices": [{"id": "a", "text": "6"}, {"id": "b", "text": "9"}, {"id": "a", "text": "dup"},
                    {"id": "c", "text": "3"}],
        "then": {"a": [{"do": "emote", "mood": "proud"}, {"do": "ask", "question": "nested?"},
                       {"do": "note", "text": "no notes in a reaction"}],
                 "b": [{"do": "say", "text": "Close! It's 2x, so 6."}],
                 "zzz": [{"do": "jump"}]},
    })
    assert [c["id"] for c in ask["choices"]] == ["a", "b", "c"]
    assert ask["then"] == {"a": [{"do": "emote", "mood": "proud"}],
                           "b": [{"do": "say", "text": "Close! It's 2x, so 6."}]}
    assert sanitize_action({"do": "ask", "question": "q", "choices": [{"id": "a", "text": "only one"}]}) is None
    nested = sanitize_action({"do": "together", "actions": [
        {"do": "jump"}, {"do": "ask", "question": "q", "choices": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}]}]})
    assert nested == {"do": "together", "actions": [{"do": "jump"}]}


def test_one_animation_from_the_answers_opening_carries_a_continuation_on():
    prompt = stage_prompt("what is a derivative?", opening="A derivative measures how fast")
    assert "RIGHT NOW" in prompt and "ONE continuous scene" in prompt
    assert "FIRST judge whether this exchange explains anything" in prompt, "a greeting gets a wave, not a show"
    assert "The answer begins:\n<<<A derivative measures how fast>>>" in prompt
    assert "Pin 2-4 notes" in prompt and "ONE ask" in prompt and "Never ask questions" not in prompt
    assert "section" not in prompt and "paragraph" not in prompt.lower()
    assert "CONTINUES the previous one" not in prompt
    cont = stage_prompt("Picture it as a snapshot", opening="A snapshot freezes the car.",
                        previous_answer="Speed at one instant...", continues="Picture it as a snapshot")
    assert "CONTINUES the previous one" in cont and "Speed at one instant..." in cont
    whole = stage_prompt("what is a derivative?", answer="A rate of change.")
    assert "Act out THIS answer" in whole and "<<<A rate of change.>>>" in whole
    legacy = stage_prompt("why do apples fall?")
    assert "Never ask questions" in legacy


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
async def test_one_animation_starts_the_moment_the_student_sends(clean_pool, embedding_client):
    answer = "Apples fall because of gravity. The Earth pulls on them.\n\nSo they drop."
    llm = StubLLMClient(canned={
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": answer, "WRITE:FACT": _FACT, "STAGE:DIRECT": _SKIT,
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
        assert types.index("stage_start") < types.index("delta"), "it opens before the answer does"
        actions = [e["action"] for e in events if e["type"] == "stage"]
        assert [a["do"] for a in actions] == ["emote", "spawn", "move", "say"], "one animation"
        assert not any(a["do"] == "section" for a in actions)

        calls = await NodeCallStore(clean_pool).list_calls_for_session(UUID(sid), "StageDirector")
        assert len(calls) == 1, "ONE call for the whole explanation, on record (invariant 2)"
        assert calls[0].input_json == {"message": "why do apples fall?", "live": True}, (
            "the question alone: it doesn't wait for the answer")
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_options_turn_shows_no_skit_and_the_pick_gets_the_whole_one(clean_pool, embedding_client):
    # 2026-09-28: the stage used to start at once, the options then stopped it
    # mid-skit, and the pick restarted it from scratch. Now it is written from
    # the start but only SHOWN once the turn answers.
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
            await asyncio.sleep(0.3)  # a held skit that leaked would arrive now
            assert "options" in [e["type"] for e in first]
            assert not any(e["type"].startswith("stage") for e in first), "the slime asks; no skit starts"

            option_id = next(e for e in first if e["type"] == "options")["options"][0]["id"]
            await ws.send(json.dumps({"type": "select_option", "option_id": option_id, "stage": True}))
            second = await _collect(ws, "done")
            if not any(e["type"] == "stage_end" for e in second):
                second += await _collect(ws, "stage_end")
            assert not any(e["type"] == "answering" for e in first + second), "the server's own signal"
            stage = [e for e in second if e["type"].startswith("stage")]
            assert {e["turn_index"] for e in stage} == {1}, "nothing from the discarded skit"
            assert stage[0]["type"] == "stage_start" and stage[-1]["type"] == "stage_end"
            assert [e["action"]["do"] for e in stage if e["type"] == "stage"] == ["emote", "spawn", "move", "say"]
        calls = await NodeCallStore(clean_pool).list_calls_for_session(UUID(sid), "StageDirector")
        assert [c.turn_index for c in calls] == [0, 1], "the discarded one is still on record (invariant 2)"
        # the pick's skit acts out the QUESTION as clarified, not the option's words alone
        # (2026-09-30: a truss question got "Awesome! Let's keep going!")
        assert "help with derivatives?" in calls[1].input_json["message"]
        assert "They clarified that they meant:" in calls[1].input_json["message"]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_no_flag_means_no_stage(clean_pool, embedding_client):
    llm = StubLLMClient(canned={
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": "Gravity.", "WRITE:FACT": _FACT, "STAGE:DIRECT": _SKIT,
    })
    live = await _start(clean_pool, llm, embedding_client)
    try:
        sid = await _chat(live)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await ws.send(json.dumps({"type": "message", "text": "why do apples fall?"}))
            events = await _collect(ws, "done")
            await asyncio.sleep(0.3)
        assert not any(e["type"].startswith("stage") for e in events)
        assert await NodeCallStore(clean_pool).list_calls_for_session(UUID(sid), "StageDirector") == []
    finally:
        await _stop(live)


def test_an_ask_keeps_its_right_answer_only_if_it_is_one_of_the_choices():
    base = {"do": "ask", "question": "Slope of x^2 at 3?",
            "choices": [{"id": "a", "text": "6"}, {"id": "b", "text": "9"}]}
    assert sanitize_action({**base, "answer": "a"})["answer"] == "a"
    assert "answer" not in sanitize_action({**base, "answer": "z"})
    assert "answer" not in sanitize_action(base)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_stage_check_pick_is_kept_right_or_wrong(clean_pool, embedding_client):
    from versa.stage import StageCheckStore

    live = await _start(clean_pool, StubLLMClient(canned={
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": "ok", "WRITE:FACT": _FACT}), embedding_client)
    try:
        sid = await _chat(live)
        choices = [{"id": "a", "text": "6"}, {"id": "b", "text": "9"}]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await ws.send(json.dumps({"type": "stage_check", "turn_index": 0, "question": "Slope at 3?",
                                      "choices": choices, "picked": "b", "answer": "a"}))
            await ws.send(json.dumps({"type": "stage_check", "turn_index": 0, "question": "Slope at 3?",
                                      "choices": choices, "picked": "a", "answer": "a"}))
            await ws.send(json.dumps({"type": "stage_check", "turn_index": 0, "question": "q",
                                      "choices": choices, "picked": "nope"}))
            error = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            assert error == {"type": "error", "message": "that answer isn't one of the choices"}
        rows = await StageCheckStore(clean_pool).list_for_session(UUID(sid))
        assert [(r["picked_id"], r["answer_id"], r["correct"]) for r in rows] == [("b", "a", False), ("a", "a", True)]
    finally:
        await _stop(live)



def test_a_live_start_carries_the_conversation_so_far():
    first = stage_prompt("why is it faster?", live=True, previous_answer="Binary search halves the list.")
    assert "RIGHT NOW" in first and "you haven't seen its answer" in first
    assert "Binary search halves the list." in first
    cont = stage_prompt("Picture it as a snapshot", live=True, previous_answer="Speed at one instant...",
                        continues="Picture it as a snapshot")
    assert "CONTINUES the previous one" in cont and "Speed at one instant..." in cont
    assert "Earlier in this chat" not in cont, "said once, as the story being continued"
