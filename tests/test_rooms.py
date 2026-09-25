"""Rooms (rooms/): group study chats with Versa as one more member.

Pure rules first (parsing Versa's actions, deriving open options and done
tasks), then the real HTTP + WebSocket routes end to end on the stub."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import websockets

from tests.test_resources import make_pdf
from tests.test_server import _start, _stop
from versa.llm import StubLLMClient
from versa.rooms.nodes import mentions_versa, parse_actions
from versa.rooms.store import (
    OptionRow,
    OptionSetRow,
    PickRow,
    TaskEventRow,
    done_task_ids,
    open_option_sets,
)

# ------------------------------------------------------------------ pure rules


def test_parse_actions_resolves_names_and_drops_what_it_cannot_use():
    raw = json.dumps({
        "reason": "welcome",
        "actions": [
            {"type": "say", "to": "all", "kind": "chat", "text": "hi all"},
            {"type": "say", "to": "asha", "private": True, "kind": "content", "text": "a hint"},
            {"type": "say", "to": "all", "private": True, "text": "private to nobody"},
            {"type": "say", "to": "Zed", "text": "not in the room"},
            {"type": "options", "to": "@Ben", "prompt": "pick", "options": ["a", "A", "b"]},
        ],
    })
    decision = parse_actions(raw, ["Asha", "Ben"])
    assert decision.reason == "welcome"
    kinds = [(a.type, a.to, a.private) for a in decision.actions]
    assert kinds == [
        ("say", None, False),
        ("say", "Asha", True),
        ("say", None, False),  # private needs a single recipient
        ("options", "Ben", False),
    ]
    assert decision.actions[3].options == ["a", "b"]  # duplicates dropped


def test_parse_actions_rejects_bad_shapes():
    raw = json.dumps({"actions": [
        {"type": "options", "to": "all", "prompt": "only one", "options": ["x"]},
        {"type": "task", "to": "Asha", "task_kind": "juggle", "description": "explain it"},
        {"type": "complete_task", "to": "all"},
        {"type": "dance", "to": "all"},
        {"type": "say", "to": "all", "text": ""},
    ]})
    decision = parse_actions(raw, ["Asha"])
    assert [(a.type, a.kind) for a in decision.actions] == [("task", "learn")]
    assert parse_actions("not json at all", ["Asha"]).actions == []


def test_mentions_versa():
    assert mentions_versa("@Versa what's a derivative?")
    assert mentions_versa("versa, help")
    assert mentions_versa("hey VERSA")
    assert not mentions_versa("universal truths")
    assert not mentions_versa("versatile")


def _set(member_id, at, n=2):
    return OptionSetRow(
        id=uuid4(), room_id=uuid4(), member_id=member_id, prompt="p", message_id=None,
        created_at=at, options=[OptionRow(id=uuid4(), position=i, text=f"o{i}") for i in range(n)],
    )


def test_open_option_sets_newest_per_audience_and_unpicked():
    me, other = uuid4(), uuid4()
    t0 = datetime.now(UTC)
    old_mine = _set(me, t0)
    everyone = _set(None, t0 + timedelta(seconds=1))
    new_mine = _set(me, t0 + timedelta(seconds=2))
    theirs = _set(other, t0 + timedelta(seconds=3))
    sets = [old_mine, everyone, new_mine, theirs]
    assert [s.id for s in open_option_sets(me, sets, [])] == [new_mine.id, everyone.id]
    picks = [PickRow(set_id=everyone.id, option_id=everyone.options[0].id, member_id=me, created_at=t0)]
    assert [s.id for s in open_option_sets(me, sets, picks)] == [new_mine.id]
    # someone else's pick doesn't close it for me
    assert everyone.id in {s.id for s in open_option_sets(other, sets, picks)}


def test_done_is_the_latest_event_per_task():
    a, b = uuid4(), uuid4()
    now = datetime.now(UTC)
    events = [
        TaskEventRow(task_id=a, event="completed", evidence="", created_at=now),
        TaskEventRow(task_id=b, event="completed", evidence="", created_at=now),
        TaskEventRow(task_id=b, event="reopened", evidence="", created_at=now),
    ]
    assert done_task_ids(events) == {a}


# ------------------------------------------------------------------ end to end


def _director(prompt: str) -> str:
    """A scripted RoomDirector: welcome + a task on join/create, a private
    hint and options when someone talks to Versa, a completion when Ben
    says 'done', and quiet otherwise."""
    actions = []
    for line in prompt.splitlines():
        if not line.startswith("EVENT: "):
            continue
        event = line[7:]
        name = event.split(" ", 1)[0]
        if "just created" in event or "just joined" in event:
            actions += [
                {"type": "say", "to": "all", "kind": "chat", "text": f"Welcome {name}"},
                {"type": "task", "to": name, "task_kind": "learn", "description": f"Explain part 1, {name}"},
            ]
        elif "talking to YOU directly" in event:
            actions += [
                {"type": "say", "to": name, "private": True, "kind": "chat", "text": f"psst {name}"},
                {"type": "options", "to": "all", "prompt": "What next?", "options": ["Example", "Quiz"]},
            ]
        elif "clicked the option" in event:
            actions.append({"type": "say", "to": name, "kind": "content", "text": "Here is an example."})
    if "Ben: done" in prompt and "task done" not in prompt:
        actions.append({"type": "complete_task", "to": "Ben", "evidence": "said done"})
    return json.dumps({"reason": "scripted", "actions": actions})


@pytest_asyncio.fixture(loop_scope="session")
async def live(clean_pool, embedding_client):
    llm = StubLLMClient(canned={"ROOM:DIRECT": _director})
    server = await _start(clean_pool, llm, embedding_client)
    server.llm = llm
    yield server
    await server.app.state.room_hub.wait_idle()
    await _stop(server)


class Socket:
    """A room WebSocket that remembers every frame it received."""

    def __init__(self, ws):
        self.ws = ws
        self.frames: list[dict] = []

    async def pump(self, until, timeout=5.0):
        async def run():
            while not until(self):
                self.frames.append(json.loads(await self.ws.recv()))
        await asyncio.wait_for(run(), timeout)

    def messages(self):
        out = []
        for f in self.frames:
            if f["type"] == "state":
                out += f["messages"]
            elif f["type"] == "message":
                out.append(f["message"])
        seen, unique = set(), []
        for m in out:
            if m["id"] not in seen:
                seen.add(m["id"])
                unique.append(m)
        return unique

    def texts(self):
        return [m["text"] for m in self.messages()]

    def board(self):
        boards = [f["board"] for f in self.frames if f["type"] in ("board", "state")]
        return boards[-1] if boards else None

    def send(self, **frame):
        return self.ws.send(json.dumps(frame))


@pytest.mark.asyncio(loop_scope="session")
async def test_a_room_end_to_end(live):
    hub = live.app.state.room_hub
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.post("/api/rooms", json={"code": "Calc-101", "name": "Asha", "topic": "derivatives"})
        assert r.status_code == 200, r.text
        asha = r.json()["member"]
        assert r.json()["room"]["title"] == "derivatives"
        assert len(r.json()["room"]["outline"]) >= 3

        # codes are unique, case-insensitively; the same name rejoins as the same member
        taken = await client.post("/api/rooms", json={"code": "calc-101", "name": "X", "topic": "t"})
        assert taken.status_code == 409
        again = await client.post("/api/rooms/CALC-101/join", json={"name": "asha"})
        assert again.json()["member"]["id"] == asha["id"]
        assert (await client.post("/api/rooms/nope-00/join", json={"name": "Ben"})).status_code == 404
        assert (await client.post("/api/rooms/calc-101/join", json={"name": "Versa"})).status_code == 422

        await hub.wait_idle()
        async with websockets.connect(f"{live.ws}/api/rooms/calc-101/ws?member_id={asha['id']}") as a_ws:
            a = Socket(a_ws)
            await a.pump(lambda s: any(f["type"] == "state" for f in s.frames))
            assert "Welcome Asha" in a.texts()
            assert [t["description"] for t in a.board()["tasks"]] == ["Explain part 1, Asha"]

            ben = (await client.post("/api/rooms/calc-101/join", json={"name": "Ben"})).json()["member"]
            async with websockets.connect(f"{live.ws}/api/rooms/calc-101/ws?member_id={ben['id']}") as b_ws:
                b = Socket(b_ws)
                await b.pump(lambda s: any(f["type"] == "state" for f in s.frames))
                await a.pump(lambda s: "Welcome Ben" in s.texts())
                await hub.wait_idle()

                # people talk to each other: everyone sees it, Versa stays quiet
                await a.send(type="message", text="hi Ben, shall we start?")
                await b.pump(lambda s: "hi Ben, shall we start?" in s.texts())
                await hub.wait_idle()

                # talking to Versa: a PRIVATE line only Ben sees, options for everyone
                await b.send(type="message", text="@Versa where do we start?")
                await b.pump(lambda s: "psst Ben" in s.texts() and s.board()["options"])
                await a.pump(lambda s: "What next?" in s.texts())
                await hub.wait_idle()
                assert "psst Ben" not in a.texts()
                private = next(m for m in b.messages() if m["text"] == "psst Ben")
                assert private["private"] and private["to_name"] == "Ben" and private["sender_name"] == "Versa"
                await a.pump(lambda s: s.board()["options"])
                options = a.board()["options"][0]
                assert options["for_everyone"] and [o["text"] for o in options["options"]] == ["Example", "Quiz"]

                # a click becomes Asha's message; it closes the set for her only
                await a.send(type="pick", option_id=options["options"][0]["id"])
                await b.pump(lambda s: any(m["kind"] == "pick" and m["sender_name"] == "Asha" for m in s.messages()))
                await a.pump(lambda s: not s.board()["options"])
                await hub.wait_idle()
                await a.send(type="pick", option_id=options["options"][1]["id"])
                await a.pump(lambda s: any(f["type"] == "error" for f in s.frames))
                assert b.board()["options"], "Ben hasn't answered yet"

                # the answer to the click is teaching content, acted out on the stage for everyone
                await b.pump(lambda s: "Here is an example." in s.texts()
                             and any(f["type"] == "stage_end" for f in s.frames))
                assert any(f["type"] == "stage" for f in b.frames)

                # a task marked done from what that person wrote
                await b.send(type="message", text="done")
                await a.pump(lambda s: any(t["member_name"] == "Ben" and t["done"] for t in s.board()["tasks"]))
                await hub.wait_idle()
                assert any(m["kind"] == "progress" for m in a.messages())

                # typing reaches the others, not the typist
                await b.send(type="typing")
                await a.pump(lambda s: any(f["type"] == "typing" and f["name"] == "Ben" for f in s.frames))

                # online presence
                assert {m["name"]: m["online"] for m in a.board()["members"]} == {"Asha": True, "Ben": True}

        # the rooms list: last visible message and unread counts
        summaries = (await client.post("/api/rooms/summaries", json={"memberships": [
            {"member_id": asha["id"], "seen_seq": 0}, {"member_id": str(uuid4()), "seen_seq": 0},
        ]})).json()
        assert len(summaries) == 1 and summaries[0]["code"] == "Calc-101"
        assert summaries[0]["unread"] > 0 and summaries[0]["member_names"] == ["Asha", "Ben"]
        assert summaries[0]["last_message"]["text"] != "psst Ben"

        # history over REST hides other people's private messages too
        state = (await client.get(f"/api/rooms/calc-101/state?member_id={asha['id']}")).json()
        assert "psst Ben" not in [m["text"] for m in state["messages"]]
        seqs = [m["seq"] for m in state["messages"]]
        assert seqs == sorted(seqs)
        assert (await client.get(f"/api/rooms/calc-101/state?member_id={uuid4()}")).status_code == 404

    # every model call is on record, with its input and output
    room = await hub.store.get_room_by_code("calc-101")
    calls = await hub.store.list_node_calls(room.id)
    names = [c["node_name"] for c in calls]
    assert names[0] == "GenerateBranches" and "RoomDirector" in names and "StageDirector" in names
    director = next(c for c in calls if c["node_name"] == "RoomDirector")
    assert director["input_json"]["prompt"].startswith("ROOM:DIRECT") and director["output_json"]["actions"]


@pytest.mark.asyncio(loop_scope="session")
async def test_create_from_a_pdf_keeps_the_text_for_versa(live):
    hub = live.app.state.room_hub
    pdf = make_pdf(["Photosynthesis", "Light reactions happen in the thylakoid membranes."])
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.post(
            "/api/rooms/from-pdf", data={"code": "bio-lab", "name": "Kim"},
            files={"file": ("notes.pdf", pdf, "application/pdf")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["room"]["source_kind"] == "pdf"
        assert r.json()["room"]["resource_filename"] == "notes.pdf"
        bad = await client.post("/api/rooms", json={"code": "x", "name": "Kim", "topic": "t"})
        assert bad.status_code == 422
        empty = await client.post("/api/rooms", json={"code": "abc", "name": "Kim"})
        assert empty.status_code == 422
    await hub.wait_idle()
    director_prompts = [p for p in live.llm.prompts if p.startswith("ROOM:DIRECT")]
    assert "thylakoid" in director_prompts[-1]
