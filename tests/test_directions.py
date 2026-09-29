"""Where this could go (directions.py): a standard set after every answer,
shuffled and recorded; picks and passes; the order of approach reaching
session-end consolidation; and the learner model kept out of the generator."""

from __future__ import annotations

import asyncio
import json
import random
from uuid import UUID

import httpx
import pytest
import websockets

from tests.test_server import _ANSWER, _FACT, _NOT_AMBIGUOUS, _start, _stop, _turn
from versa.directions import (
    SLOTS,
    PathStep,
    parse_cards,
    render_path,
    shuffled_positions,
)
from versa.llm import StubLLMClient
from versa.memory import SummarizeSessionPath

_CARDS = {slot: f"card for {slot}" for slot in SLOTS}


def _llm(**extra) -> StubLLMClient:
    canned = {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
              "DIRECTIONS:SUGGEST": json.dumps({"cards": _CARDS})}
    canned.update(extra)
    return StubLLMClient(canned=canned)


# ------------------------------------------------------------------ pure rules


def test_only_a_complete_distinct_set_of_cards_counts():
    assert parse_cards(json.dumps({"cards": _CARDS})) == _CARDS
    missing = {k: v for k, v in _CARDS.items() if k != "why"}
    assert parse_cards(json.dumps({"cards": missing})) == {}
    assert parse_cards(json.dumps({"cards": {**_CARDS, "why": "  "}})) == {}
    assert parse_cards(json.dumps({"cards": {**_CARDS, "why": "CARD FOR USE"}})) == {}  # duplicate
    assert parse_cards("not json") == {}
    long = parse_cards(json.dumps({"cards": {**_CARDS, "deeper": "x" * 200 + "?"}}))
    assert len(long["deeper"]) == 70 and long["deeper"].endswith("…")
    assert parse_cards(json.dumps({"cards": {**_CARDS, "why": "Why does it work?"}}))["why"] == "Why does it work"


def test_positions_are_a_fresh_shuffle_of_every_slot():
    orders = {tuple(sorted(shuffled_positions(random.Random(seed)), key=shuffled_positions(
        random.Random(seed)).get)) for seed in range(30)}
    assert all(sorted(o) == sorted(SLOTS) for o in orders)
    assert len(orders) > 5  # not a fixed order
    assert sorted(shuffled_positions().values()) == list(range(len(SLOTS)))


def test_the_order_of_approach_renders_picks_and_passes_in_order():
    path = [
        PathStep(turn_index=0, kind="picked", slot="intuition", position=3, elapsed_ms=900),
        PathStep(turn_index=1, kind="passed", slot=None, position=None, elapsed_ms=4000),
        PathStep(turn_index=2, kind="picked", slot="example", position=0, elapsed_ms=1200),
        PathStep(turn_index=3, kind="picked", slot="why", position=5, elapsed_ms=700),
    ]
    assert render_path(path) == (
        "see it simply -> (asked their own) -> work through one concrete example -> why it works"
    )
    assert render_path([PathStep(turn_index=0, kind="passed", slot=None, position=None, elapsed_ms=1)]) == ""


@pytest.mark.asyncio(loop_scope="session")
async def test_path_summary_prompt_is_unchanged_without_picks_and_carries_the_order_with_them():
    llm = StubLLMClient(canned={"SUMMARIZE:PATH": json.dumps({"summary": "s"})})
    node = SummarizeSessionPath(llm)
    await node.run([])
    plain = llm.prompts[-1]
    await node.run([], direction_path="see it simply -> why it works")
    with_order = llm.prompts[-1]
    assert "directions they chose" not in plain
    assert "The directions they chose, in order:\nsee it simply -> why it works" in with_order
    assert "shuffled" in with_order


# ------------------------------------------------------------------ end to end


async def _directions_event(ws) -> dict:
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        if event["type"] == "directions":
            return event


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_then_directions_pick_pass_and_order_of_approach(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "explorer"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
            await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": 80, "breadth": 20})

        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            events = await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            assert events[-1]["type"] == "done" and events[-1]["kind"] == "answer"
            assert "directions" not in [e["type"] for e in events]  # it follows the answer
            offered = await _directions_event(ws)
            assert offered["turn_index"] == 0 and len(offered["cards"]) == 6
            assert {c["text"] for c in offered["cards"]} == set(_CARDS.values())
            prompt = next(p for p in llm.prompts if p.startswith("DIRECTIONS:SUGGEST"))
            assert "depth 80/100" in prompt and "breadth 20/100" in prompt

            # take the "why" card: it runs as the next turn, in its own words
            why = next(c for c in offered["cards"] if c["text"] == _CARDS["why"])
            events = await _turn(ws, {"type": "direction", "directions": True, "card_id": why["id"]})
            assert events[0] == {"type": "turn_start", "turn_index": 1}
            assert events[-1]["kind"] == "answer"
            second = await _directions_event(ws)
            again = await _turn(ws, {"type": "direction", "directions": True, "card_id": why["id"]})
            assert again[-1] == {"type": "error", "message": "that suggestion is no longer available"}

            # asking their own question passes the open set
            await _turn(ws, {"type": "message", "directions": True, "text": "ok, and integrals?"})
            await _directions_event(ws)
            stale = await _turn(ws, {"type": "direction", "directions": True, "card_id": second["cards"][0]["id"]})
            assert stale[-1]["type"] == "error"

        async with clean_pool.acquire() as conn:
            sets = await conn.fetch(
                "SELECT id, turn_index, depth_level, breadth_level FROM direction_sets "
                "WHERE session_id = $1 ORDER BY turn_index", sid)
            assert [s["turn_index"] for s in sets] == [0, 1, 2]
            assert (sets[0]["depth_level"], sets[0]["breadth_level"]) == (80, 20)
            positions = await conn.fetch(
                "SELECT slot, position FROM direction_cards WHERE set_id = $1", sets[0]["id"])
            assert sorted(p["position"] for p in positions) == list(range(6))
            shown_at = {p["slot"]: p["position"] for p in positions}
            assert [c["text"] for c in offered["cards"]] == [
                _CARDS[slot] for slot in sorted(shown_at, key=shown_at.get)]  # sent in shown order
            events = await conn.fetch(
                "SELECT e.kind, c.slot, e.next_turn_index, e.elapsed_ms FROM direction_events e "
                "JOIN direction_sets s ON s.id = e.set_id LEFT JOIN direction_cards c ON c.id = e.card_id "
                "WHERE s.session_id = $1 ORDER BY s.turn_index", sid)
            assert [(e["kind"], e["slot"], e["next_turn_index"]) for e in events] == [
                ("picked", "why", 1), ("passed", None, 2)]
            assert all(e["elapsed_ms"] >= 0 for e in events)
            # the generator never saw the learner model: only these inputs --
            # and, once they have taken one, the directions taken in this chat
            inputs = await conn.fetch(
                "SELECT input_json FROM node_calls WHERE session_id = $1 AND node_name = 'SuggestDirections' "
                "ORDER BY turn_index", sid)
            assert [set(r["input_json"]) - {"message", "answer", "depth", "breadth"} for r in inputs] == [
                set(), {"path_so_far"}, {"path_so_far"}]
            assert inputs[1]["input_json"]["path_so_far"] == [_CARDS["why"]]
            assert inputs[2]["input_json"]["path_so_far"] == [_CARDS["why"]]  # a pass adds nothing

        # the order of approach reaches session-end consolidation
        await live.loop.consolidate_session(UUID(sid))
        async with clean_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT input_json FROM node_calls WHERE session_id = $1 AND node_name = 'SummarizeSessionPath'",
                sid)
        assert row["input_json"]["direction_path"] == "why it works -> (asked their own)"
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_no_directions_are_offered_when_generation_fails(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(**{"DIRECTIONS:SUGGEST": "garbage"}), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "quiet"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": True, "text": "hello"})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_directions_event(ws), timeout=1.5)
        async with clean_pool.acquire() as conn:
            assert await conn.fetchval("SELECT COUNT(*) FROM direction_sets") == 0
            # the failed attempt is still on record (invariant 2), retried once
            calls = await conn.fetchval(
                "SELECT COUNT(*) FROM node_calls WHERE node_name = 'SuggestDirections'")
            assert calls == 1
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_client_that_does_not_show_the_strip_gets_none_and_nothing_is_recorded(
    clean_pool, embedding_client,
):
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "plain"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "text": "hello"})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_directions_event(ws), timeout=1.5)
        async with clean_pool.acquire() as conn:
            assert await conn.fetchval("SELECT COUNT(*) FROM direction_sets") == 0
            assert await conn.fetchval(
                "SELECT COUNT(*) FROM node_calls WHERE node_name = 'SuggestDirections'") == 0
    finally:
        await _stop(live)


# ------------------------------------------------------------ fork + the pad


@pytest.mark.asyncio(loop_scope="session")
async def test_final_answer_continues_only_when_told_to():
    from versa.disambiguate import FinalAnswer

    llm = StubLLMClient(canned={"FINAL:ANSWER": "ok"})
    node = FinalAnswer(llm)
    await node.run("what is a derivative?")
    plain = llm.prompts[-1]
    await node.run("Show me with a speedometer", continues="Show me with a speedometer")
    continued = llm.prompts[-1]
    assert "CONTINUES your previous answer" not in plain
    assert "CONTINUES your previous answer" in continued and "'Show me with a speedometer'" in continued
    assert "do not re-introduce the topic" in continued


@pytest.mark.asyncio(loop_scope="session")
async def test_a_fork_pick_continues_the_answer_and_a_card_pick_does_not(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "forker"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": "fork", "text": "what is a derivative?"})
            fork = await _directions_event(ws)
            assert fork["presentation"] == "fork"
            card = fork["cards"][0]
            await _turn(ws, {"type": "direction", "directions": "fork", "continue": True, "card_id": card["id"]})
            strip = await _directions_event(ws)
            await _turn(ws, {"type": "direction", "directions": True, "card_id": strip["cards"][0]["id"]})
            await _directions_event(ws)

        async with clean_pool.acquire() as conn:
            presentations = [r["presentation"] for r in await conn.fetch(
                "SELECT presentation FROM direction_sets WHERE session_id = $1 ORDER BY turn_index", sid)]
            assert presentations == ["fork", "fork", "strip"]
            inputs = {r["turn_index"]: r["input_json"] for r in await conn.fetch(
                "SELECT turn_index, input_json FROM node_calls WHERE session_id = $1 AND node_name = 'FinalAnswer'",
                sid)}
        assert "continues" not in inputs[0]
        assert inputs[1]["continues"] == card["text"]      # the fork link carried the answer on
        assert "continues" not in inputs[2]               # a card starts a fresh answer
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_moving_the_pad_rewrites_the_answer_and_re_pitches_the_directions(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "padder"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
            async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
                await _turn(ws, {"type": "message", "directions": "fork", "text": "what is a derivative?"})
                first = await _directions_event(ws)
                # the pad moves both levels at once, then asks for a rewrite
                await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": 90, "breadth": 10})
                await ws.send(json.dumps({"type": "regenerate", "request_id": 7, "directions": "fork"}))
                again = await _directions_event(ws)
                assert again["turn_index"] == first["turn_index"] == 0
                assert again["set_id"] != first["set_id"]
                stale = await _turn(ws, {"type": "direction", "directions": "fork", "continue": True,
                                         "card_id": first["cards"][0]["id"]})
                assert stale[-1]["type"] == "error"  # the old window's links are gone
        prompt = [p for p in llm.prompts if p.startswith("DIRECTIONS:SUGGEST")][-1]
        assert "depth 90/100" in prompt and "breadth 10/100" in prompt
        async with clean_pool.acquire() as conn:
            levels = await conn.fetch(
                "SELECT depth_level, breadth_level FROM direction_sets WHERE session_id = $1 ORDER BY created_at", sid)
        assert [(r["depth_level"], r["breadth_level"]) for r in levels] == [(50, 50), (90, 10)]
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_to_go_from_offers_no_strip_and_records_nothing(clean_pool, embedding_client):
    """ "hi" once got six cards about oxygen and nerve impulses: the model now
    judges first, and a deliberate {"cards": null} means no set -- not a retry."""
    live = await _start(clean_pool, _llm(**{"DIRECTIONS:SUGGEST": json.dumps({"cards": None})}), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "greeter"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": "fork", "text": "hi"})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(_directions_event(ws), timeout=1.5)
        prompt = next(p for p in live.app.state.loop.suggest_directions._llm.prompts
                      if p.startswith("DIRECTIONS:SUGGEST"))
        assert '{"cards": null}' in prompt and "a greeting" in prompt
        async with clean_pool.acquire() as conn:
            assert await conn.fetchval("SELECT COUNT(*) FROM direction_sets") == 0
            assert await conn.fetchval(
                "SELECT COUNT(*) FROM node_calls WHERE node_name = 'SuggestDirections'") == 1
            assert await conn.fetchval(
                "SELECT output_json FROM node_calls WHERE node_name = 'SuggestDirections'") == {}
    finally:
        await _stop(live)


def test_later_sets_build_on_the_path_taken_and_the_first_is_unchanged():
    from versa.directions import directions_prompt
    from versa.session_knobs import SessionKnobs

    first = directions_prompt("what is a derivative?", "a rate of change", SessionKnobs())
    assert "already taken" not in first
    assert directions_prompt("what is a derivative?", "a rate of change", SessionKnobs(), []) == first
    later = directions_prompt("what is a derivative?", "a rate of change", SessionKnobs(),
                              ["Show me with a speedometer", "Work one out: x^3"])
    assert '"Show me with a speedometer" -> "Work one out: x^3"' in later
    assert "never re-offer" in later and "same for every slot" in later
