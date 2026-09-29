"""Slider moves (knob_events.py KnobEventStore, migration 084): every settled
move is kept -- `sessions` only holds the current levels -- and the log is
append-only (CLAUDE.md invariant 19), same AST-based scan as every other
store."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import httpx
import pytest

import versa.knob_events as knob_events_module
from tests.test_rooms_append_only import _string_literals
from tests.test_server import _turn, live, new_chat  # noqa: F401  (fixtures)
from versa.knob_events import KnobEventStore
from versa.session_knobs import SessionKnobs

# ------------------------------------------------------------- invariant 19


def test_knob_events_module_never_deletes_or_updates():
    path = Path(knob_events_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_knob_events_migration_has_no_delete_or_update():
    path = Path(knob_events_module.__file__).resolve().parent / "migrations" / "084_knob_events.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_knob_event_store_has_no_removal_methods():
    for name in dir(KnobEventStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name


# ----------------------------------------------------------------- the store


@pytest.mark.asyncio(loop_scope="session")
async def test_moves_are_kept_in_order_and_a_no_op_writes_nothing(transcript, clean_pool, learner_id):
    store = KnobEventStore(clean_pool)
    session_id = await transcript.create_session(learner_id)
    start = SessionKnobs()
    deeper = SessionKnobs(depth=80)
    wider = SessionKnobs(depth=80, breadth=10, answer_length=30)

    assert await store.record(session_id=session_id, turn_count=0, before=start, after=deeper) is not None
    assert await store.record(session_id=session_id, turn_count=3, before=deeper, after=deeper) is None
    assert await store.record(session_id=session_id, turn_count=3, before=deeper, after=wider) is not None

    events = await store.list_for_session(session_id)
    assert [(e.turn_count, e.before, e.after) for e in events] == [(0, start, deeper), (3, deeper, wider)]


# ------------------------------------------------------------------ the API


@pytest.mark.asyncio(loop_scope="session")
async def test_every_settled_move_survives_though_the_session_keeps_only_the_last(
    live, new_chat, clean_pool,  # noqa: F811
):
    _, sid = await new_chat("mover")
    async with httpx.AsyncClient(base_url=live.http) as client:
        await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": 20})
        await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": 20})  # no change
        await client.patch(f"/api/sessions/{sid}/knobs", json={"depth": 90, "breadth": 70})
        current = (await client.get(f"/api/sessions/{sid}/knobs")).json()
        missing = await client.patch(
            "/api/sessions/00000000-0000-0000-0000-000000000000/knobs", json={"depth": 10}
        )

    assert current == {"answer_length": 50, "depth": 90, "breadth": 70}
    assert missing.status_code == 404
    events = await KnobEventStore(clean_pool).list_for_session(UUID(sid))
    assert [(e.before.depth, e.after.depth, e.after.breadth) for e in events] == [(50, 20, 50), (20, 90, 70)]
    assert all(e.turn_count == 0 for e in events)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_move_records_how_far_into_the_session_it_came(live, new_chat, clean_pool):  # noqa: F811
    import websockets

    _, sid = await new_chat("later")
    async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})
        while live.loop._background_tasks:
            await live.loop.wait_for_background_tasks()
    async with httpx.AsyncClient(base_url=live.http) as client:
        await client.patch(f"/api/sessions/{sid}/knobs", json={"answer_length": 10})

    [event] = await KnobEventStore(clean_pool).list_for_session(UUID(sid))
    assert event.turn_count == 1
    assert (event.before.answer_length, event.after.answer_length) == (50, 10)
