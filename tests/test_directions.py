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
    CLASSIC_SLOTS,
    FAMILIES,
    FAMILY_OF,
    SLOTS,
    PathStep,
    deal_hand,
    draw_pool,
    parse_cards,
    render_path,
    shuffled_positions,
)
from versa.llm import StubLLMClient
from versa.memory import SummarizeSessionPath

_CARDS = {slot: f"card for {slot}" for slot in SLOTS}  # every library type, so any drawn pool parses
_CLASSIC = {slot: _CARDS[slot] for slot in CLASSIC_SLOTS}

# A fixed draw for flow tests that need to take a known card: every pool is
# these eight, and hands come in this order (the first is why, use, intuition).
_FIXED_POOL = ["why", "use", "intuition", "example", "deeper", "next", "visualise", "compare"]


@pytest.fixture
def fixed_hands(monkeypatch):
    import versa.directions as directions_module

    monkeypatch.setattr(directions_module, "draw_pool", lambda rng=None: list(_FIXED_POOL))
    monkeypatch.setattr(directions_module, "deal_hand", lambda pool, dealt, size=3, rng=None: [
        s for s in _FIXED_POOL if s in pool and s not in dealt][:size])
    monkeypatch.setattr(directions_module, "with_extras", lambda hand, available, rng=None, widen=False: hand)


def _llm(**extra) -> StubLLMClient:
    canned = {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
              "DIRECTIONS:SUGGEST": json.dumps({"cards": _CARDS})}
    canned.update(extra)
    return StubLLMClient(canned=canned)


# ------------------------------------------------------------------ pure rules


def test_only_a_complete_distinct_set_of_cards_counts():
    assert parse_cards(json.dumps({"cards": _CARDS})) == _CLASSIC  # only what was asked for
    drawn = ["try_it", "story", "debate"]
    assert parse_cards(json.dumps({"cards": _CARDS}), drawn) == {s: _CARDS[s] for s in drawn}
    assert parse_cards(json.dumps({"cards": _CLASSIC}), drawn) == {}  # a drawn card was skipped
    missing = {k: v for k, v in _CARDS.items() if k != "why"}
    assert parse_cards(json.dumps({"cards": missing})) == {}
    assert parse_cards(json.dumps({"cards": {**_CARDS, "why": "  "}})) == {}
    assert parse_cards(json.dumps({"cards": {**_CARDS, "why": "CARD FOR USE"}})) == {}  # duplicate
    assert parse_cards("not json") == {}
    long = parse_cards(json.dumps({"cards": {**_CARDS, "deeper": "x" * 200 + "?"}}))
    assert len(long["deeper"]) == 70 and long["deeper"].endswith("…")
    assert parse_cards(json.dumps({"cards": {**_CARDS, "why": "Why does it work?"}}))["why"] == "Why does it work"


def test_positions_are_a_fresh_shuffle_of_every_card():
    orders = {tuple(sorted(shuffled_positions(random.Random(seed)), key=shuffled_positions(
        random.Random(seed)).get)) for seed in range(30)}
    assert all(sorted(o) == sorted(CLASSIC_SLOTS) for o in orders)
    assert len(orders) > 5  # not a fixed order
    hand = ["story", "use", "debate"]
    assert sorted(shuffled_positions(slots=hand).values()) == [0, 1, 2]


def test_the_library_keeps_the_original_six_and_every_card_has_a_family_and_a_place():
    from versa.directions import COORDS

    assert set(CLASSIC_SLOTS) <= set(SLOTS) and len(SLOTS) == 16
    assert set(FAMILY_OF) == set(SLOTS) == set(COORDS)
    assert all(len(v) == 4 for v in FAMILIES.values())


def test_a_pool_takes_two_from_every_family_and_a_hand_one_per_family():
    for seed in range(40):
        rng = random.Random(seed)
        pool = draw_pool(rng)
        assert len(pool) == 8 and all(sum(FAMILY_OF[s] == f for s in pool) == 2 for f in FAMILIES)
        first = deal_hand(pool, set(), rng=rng)
        assert len(first) == 3 and len({FAMILY_OF[s] for s in first}) == 3  # spans the space
        second = deal_hand(pool, set(first), rng=rng)
        assert len(second) == 3 and not set(first) & set(second)
    pools = {tuple(draw_pool(random.Random(seed))) for seed in range(40)}
    assert len(pools) > 20  # random, not a fixed skeleton


def test_the_draw_takes_nothing_about_the_learner():
    import inspect

    assert list(inspect.signature(draw_pool).parameters) == ["rng"]
    assert list(inspect.signature(deal_hand).parameters) == ["pool", "dealt", "size", "rng"]


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
async def test_answer_then_directions_pick_pass_and_order_of_approach(clean_pool, embedding_client, fixed_hands):
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
            assert offered["turn_index"] == 0 and len(offered["cards"]) == 3  # a hand, not the pool
            assert {c["text"] for c in offered["cards"]} == {_CARDS[s] for s in ("why", "use", "intuition")}
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
            assert sorted(p["position"] for p in positions) == list(range(3))
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
                {"slots", "path", "wild"}, {"slots", "path", "wild", "path_so_far"},
                {"slots", "path", "wild", "path_so_far"}]  # the random draw, path and wild ask are on record
            assert inputs[0]["input_json"]["slots"] == _FIXED_POOL  # the draw is on record
            pools = await conn.fetch("SELECT cards FROM direction_pools WHERE session_id = $1", sid)
            assert len(pools) == 3 and set(pools[0]["cards"]) == set(_FIXED_POOL)  # the whole pool is kept
            assert inputs[1]["input_json"]["path_so_far"] == [_CARDS["why"]]
            assert inputs[2]["input_json"]["path_so_far"] == [_CARDS["why"]]  # a pass adds nothing

        # the order of approach is read from these rows directly
        # (style_patterns.py); session end no longer summarises it in free text
        await live.loop.consolidate_session(UUID(sid))
        async with clean_pool.acquire() as conn:
            assert not await conn.fetchval(
                "SELECT count(*) FROM node_calls WHERE session_id = $1 AND node_name = 'SummarizeSessionPath'",
                sid)
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


# ------------------------------------------------ other directions, extras


def test_a_path_is_two_steps_from_different_families_and_extras_are_optional():
    from versa.directions import directions_prompt, draw_path, with_extras
    from versa.session_knobs import SessionKnobs

    for seed in range(30):
        pool = draw_pool(random.Random(seed))
        a, b = draw_path(pool, random.Random(seed))
        assert a in pool and b in pool and FAMILY_OF[a] != FAMILY_OF[b]
    prompt = directions_prompt("m", "a", SessionKnobs(), None, ["why", "use"], ("example", "use"), True)
    assert "- path: a two-step route" in prompt and "- wild:" in prompt and '"path": "..."' in prompt
    got = parse_cards(json.dumps({"cards": {"why": "w", "use": "u", "path": "Work one, then use it"}}),
                      ["why", "use"], ("path", "wild"))
    assert got == {"why": "w", "use": "u", "path": "Work one, then use it"}  # no wild: just not offered
    hand = ["why", "use", "story"]
    seen = {tuple(with_extras(hand, {"path", "wild"}, random.Random(seed))) for seed in range(60)}
    assert ("why", "use", "story") in seen and any("path" in h for h in seen) and any("wild" in h for h in seen)
    assert all(len(h) == 3 for h in seen)


async def _collect(ws, types, timeout=15):
    out = []
    while True:
        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        out.append(event)
        if event["type"] in types:
            return out


@pytest.mark.asyncio(loop_scope="session")
async def test_other_directions_deals_the_rest_of_the_pool_then_writes_a_fresh_one(
    clean_pool, embedding_client, fixed_hands,
):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "more"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            first = await _directions_event(ws)
            calls = sum(1 for p in llm.prompts if p.startswith("DIRECTIONS:SUGGEST"))
            hands = [first]
            for _ in range(3):
                await ws.send(json.dumps({"type": "more_directions", "set_id": hands[-1]["set_id"]}))
                hands.append((await _collect(ws, {"directions"}))[-1])
            stale = await _turn(ws, {"type": "more_directions", "set_id": first["set_id"]})
            assert stale[-1] == {"type": "error", "message": "those directions are no longer open"}
            # take a card from the latest hand: it runs as the next turn
            events = await _turn(ws, {"type": "direction", "directions": True, "card_id": hands[-1]["cards"][0]["id"]})
            assert events[0]["type"] == "turn_start"

        texts = [[c["text"] for c in h["cards"]] for h in hands]
        assert texts[0] == [_CARDS[s] for s in ("why", "use", "intuition")] or len(texts[0]) == 3
        assert {c for h in texts[:3] for c in h} == {_CARDS[s] for s in _FIXED_POOL}  # the whole pool, no repeats
        assert len(texts[2]) == 2  # what was left of it
        assert [h["deal_index"] for h in hands] == [0, 1, 2, 3] and all(h["turn_index"] == 0 for h in hands)
        # the first three hands came from one model call; the fourth needed a fresh pool
        assert sum(1 for p in llm.prompts if p.startswith("DIRECTIONS:SUGGEST")) == calls + 1
        async with clean_pool.acquire() as conn:
            kinds = [r["kind"] for r in await conn.fetch(
                "SELECT e.kind FROM direction_events e JOIN direction_sets s ON s.id = e.set_id "
                "WHERE s.session_id = $1 ORDER BY s.created_at", UUID(sid))]
            pools = await conn.fetchval("SELECT count(*) FROM direction_pools WHERE session_id = $1", UUID(sid))
            guesses = await conn.fetchval(
                "SELECT count(*) FROM direction_predictions p JOIN direction_sets s ON s.id = p.set_id "
                "WHERE s.session_id = $1", UUID(sid))
        assert kinds[:4] == ["more", "more", "more", "picked"]
        assert pools >= 2  # the first answer's pool and its fresh one (the pick's own may still be writing)
        assert guesses >= 4  # every hand was guessed before it went out
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_path_and_wild_cards_are_dealt_tagged_and_read_by_their_type(clean_pool, embedding_client, monkeypatch):
    import versa.directions as directions_module
    from versa.pick_prediction import PredictionStore

    cards = {**_CARDS, "path": "Work one out, then see where it's used", "wild": "Why do bees care about this"}
    llm = _llm(**{"DIRECTIONS:SUGGEST": json.dumps({"cards": cards})})
    monkeypatch.setattr(directions_module, "PATH_CHANCE", 1.0)  # the first hand carries the path
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "extras"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            offered = await _directions_event(ws)
            path = next(c for c in offered["cards"] if c["text"] == cards["path"])
            await asyncio.sleep(0.05)
            await _turn(ws, {"type": "direction", "directions": True, "card_id": path["id"]})
        async with clean_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT slot, tagged_as, path_slots FROM direction_cards WHERE id = $1", UUID(path["id"]))
            pool = await conn.fetchval(
                "SELECT cards FROM direction_pools WHERE session_id = $1 ORDER BY created_at LIMIT 1", UUID(sid))
        assert row["slot"] == "path"
        first, second = row["path_slots"].split(">")
        assert row["tagged_as"] == first and FAMILY_OF[first] != FAMILY_OF[second]
        assert pool["wild"] == cards["wild"] and pool["~wild_tag"] in SLOTS  # tagged blind, kept with the pool
        [pick] = await PredictionStore(clean_pool).past_picks(UUID(lid))
        assert pick.slot == first  # a path pick is read as choosing its first step
    finally:
        await _stop(live)


def test_the_live_schema_lets_every_drawn_card_through():
    # found live 2026-09-30: a schema requiring the original six forced Gemini
    # to write those six whatever was drawn, so no pool ever parsed
    from versa.directions import EXTRAS
    from versa.llm import _SCHEMA_BY_PREFIX, DIRECTION_CARD_KEYS

    assert set(DIRECTION_CARD_KEYS) == set(SLOTS) | set(EXTRAS)
    cards = _SCHEMA_BY_PREFIX["DIRECTIONS:SUGGEST"]["properties"]["cards"]
    assert set(cards["required"]) == set(SLOTS)  # every type written; the draw keeps its own


@pytest.mark.asyncio(loop_scope="session")
async def test_a_drawn_pool_asks_for_every_type_and_keeps_only_the_drawn():
    from versa.directions import SuggestDirections

    llm = StubLLMClient(canned={"DIRECTIONS:SUGGEST": json.dumps({"cards": _CARDS})})
    drawn = ["story", "debate", "try_it", "why", "use", "summary", "compare", "prove_it"]
    got = await SuggestDirections(llm).run("m", "a", 50, 50, slots=drawn)
    assert set(got) == set(drawn)
    assert all(f"- {slot}:" in llm.prompts[-1] for slot in SLOTS)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_compass_deals_one_card_per_family_and_says_which(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "compass"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": "compass", "text": "what is a derivative?"})
            offered = await _directions_event(ws)
        assert offered["presentation"] == "compass" and len(offered["cards"]) == 4
        assert sorted(c["family"] for c in offered["cards"]) == sorted(FAMILIES)  # one per way out
        async with clean_pool.acquire() as conn:
            shown = await conn.fetchval("SELECT presentation FROM direction_sets WHERE id = $1",
                                        UUID(offered["set_id"]))
        assert shown == "compass"
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_miss_keeps_what_they_asked_and_widens_the_next_hand(clean_pool, embedding_client, monkeypatch):
    """Experimenting on a miss (migration 088): passing every card by asking
    their own question keeps that question, read against the library; the
    next answer's first hand is widened at random."""
    import versa.directions as directions_module
    from versa.embeddings import StubEmbeddingClient
    from versa.style_patterns import StyleReader

    cards = {**_CARDS, "path": "Work one out, then see where it's used", "wild": "Why do bees care about this"}
    llm = _llm(**{"DIRECTIONS:SUGGEST": json.dumps({"cards": cards})})
    monkeypatch.setattr(directions_module, "PATH_CHANCE", 0.0)
    monkeypatch.setattr(directions_module, "WILD_CHANCE", 0.0)  # only a miss brings an extra in
    question = "but why does that actually work?"
    # their question lands exactly on the "why it works" type
    embedding_client.canned[question] = await StubEmbeddingClient().embed(SLOTS["why"])
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "misser"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            first = await _directions_event(ws)
            await _turn(ws, {"type": "message", "directions": True, "text": question})  # a miss
            second = await _directions_event(ws)
        await live.loop.wait_for_background_tasks()
        async with clean_pool.acquire() as conn:
            miss = await conn.fetchrow("SELECT * FROM direction_misses WHERE set_id = $1", UUID(first["set_id"]))
            sets = await conn.fetch(
                "SELECT id, experiment FROM direction_sets WHERE session_id = $1 ORDER BY turn_index", UUID(sid))
            second_slots = {r["slot"] for r in await conn.fetch(
                "SELECT slot FROM direction_cards WHERE set_id = $1", UUID(second["set_id"]))}
            first_slots = {r["slot"] for r in await conn.fetch(
                "SELECT slot FROM direction_cards WHERE set_id = $1", UUID(first["set_id"]))}
        assert miss["question"] == question and miss["tagged_as"] == "why"
        assert miss["follow_up"] is None  # no reliable centre yet: unknown, not "new subject"
        assert miss["in_hand"] == ("why" in first_slots)  # was the way they asked for on a card?
        assert [s["experiment"] for s in sets] == [None, "after_miss"]
        assert second_slots & {"path", "wild"}  # widened: an extra for certain
        misses, _ = await StyleReader(clean_pool).misses(UUID(lid))
        assert [m.asked for m in misses] == ["why"]
    finally:
        await _stop(live)


def test_a_missed_question_is_read_as_a_way_out_only_when_clearly_one():
    from versa.directions import read_miss

    assert read_miss([("why", 0.91), ("story", 0.82)]) == "why"
    assert read_miss([("connect", 0.85), ("next", 0.84)]) is None  # a near-tie is not forced
    assert read_miss([("next", 0.77), ("example", 0.76)]) is None  # "ok thanks" is none of them
    assert read_miss([]) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_a_miss_the_library_cannot_place_is_read_as_a_new_move(clean_pool, embedding_client):
    """When the embedding can't place a missed question, one fast model call
    reads the kind of move it makes -- recorded like every node call -- and
    a move none of the types covers is kept with its embedding (migration 089)."""
    from versa.style_patterns import StyleReader

    import time

    from versa import style_patterns

    reading = {"same_subject": True, "type": "none", "move": "where the rule stops working"}
    llm = _llm(**{"DIRECTIONS:READ_MISS": json.dumps(reading)})
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "beyond"})).json()["id"]
            sid = (await client.post("/api/sessions", json={"learner_id": lid})).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            await _turn(ws, {"type": "message", "directions": True, "text": "what is a derivative?"})
            first = await _directions_event(ws)
            # a style read before this turn must not outlive it: the turn's own
            # reading counts what they just did (analysed turn by turn)
            style_patterns._confirmed_cache[UUID(lid)] = (time.monotonic(), ["a stale reading"])
            await _turn(ws, {"type": "message", "directions": True, "text": "when does that rule stop working?"})
            await _directions_event(ws)
        await live.loop.wait_for_background_tasks()
        assert style_patterns._confirmed_cache.get(UUID(lid), (0, []))[1] != ["a stale reading"]
        async with clean_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT same_subject, type, move, move_embedding FROM direction_miss_readings WHERE set_id = $1",
                UUID(first["set_id"]))
            call = await conn.fetchrow(
                "SELECT input_json FROM node_calls WHERE session_id = $1 AND node_name = 'ReadMiss'", UUID(sid))
        assert row["move"] == "where the rule stops working" and row["type"] is None and row["same_subject"]
        assert row["move_embedding"] is not None
        assert call["input_json"] == {"earlier": "what is a derivative?",
                                      "question": "when does that rule stop working?"}  # nothing about them
        prompt = next(p for p in llm.prompts if p.startswith("DIRECTIONS:READ_MISS"))
        assert "beyond" not in prompt  # not even their name
        [miss], _ = await StyleReader(clean_pool).misses(UUID(lid))
        assert miss.asked is None and miss.move == f"move:{first['set_id']}"
        assert miss.move_label == "where the rule stops working"
        assert (await StyleReader(clean_pool).discovered_moves())[0]["label"] == "where the rule stops working"
        async with httpx.AsyncClient(base_url=live.http) as client:  # their own new moves, one learner
            out = (await client.get(f"/api/learners/{lid}/style-patterns")).json()
        assert [(m["label"], m["times"], m["others"]) for m in out["new_moves"]] == [
            ("where the rule stops working", 1, 0)]
    finally:
        await _stop(live)
