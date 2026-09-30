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
    assert decision.actions == []  # a task to type is never set
    assert parse_actions("not json at all", ["Asha"]).actions == []


def test_a_race_is_a_quiz_for_everyone():
    raw = json.dumps({"actions": [
        {"type": "race", "to": "Asha", "prompt": "3 x 3?", "options": ["9", "6"], "answer": "9"},
        {"type": "race", "to": "all", "prompt": "no answer", "options": ["9", "6"]},
    ]})
    (race,) = parse_actions(raw, ["Asha"]).actions
    assert (race.type, race.to, race.prompt, race.options, race.answer) == ("race", None, "3 x 3?", ["9", "6"], "9")


def test_a_task_is_always_a_quiz_answered_with_one_tap():
    raw = json.dumps({"actions": [
        {"type": "task", "to": "Asha", "description": "Which is a derivative?",
         "options": ["dy/dx", "x + y", "DY/DX"], "answer": "dy/dx"},
        {"type": "task", "to": "Asha", "description": "no right answer given", "options": ["a", "b"]},
        {"type": "task", "to": "Asha", "description": "answer not an option", "options": ["a", "b"], "answer": "c"},
        {"type": "task", "to": "Asha", "description": "one option", "options": ["a"], "answer": "a"},
        {"type": "part_done", "to": "all", "part": 2, "evidence": "covered"},
        {"type": "part_done", "to": "all", "part": 9},
    ]})
    decision = parse_actions(raw, ["Asha"], part_count=3)
    task, part = decision.actions
    assert (task.type, task.kind, task.options, task.answer) == ("task", "check", ["dy/dx", "x + y"], "dy/dx")
    assert (part.type, part.part) == ("part_done", 2)


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


def _asked_for_race(prompt: str, event: str) -> bool:
    """The message this event is about asked for a race."""
    import re

    seq = re.search(r"message \[(\d+)\]", event)
    line = next((ln for ln in prompt.splitlines() if seq and ln.startswith(f"[{seq.group(1)}] ")), "")
    return "race" in line.lower()


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
                {"type": "task", "to": name, "task_kind": "check", "description": f"Quick one, {name}: 2 + 2?",
                 "options": ["4", "5"], "answer": "4"},
            ]
        elif "answered their task by tapping" in event:
            actions.append({"type": "say", "to": name, "kind": "chat",
                            "text": "Nice one!" if "RIGHT" in event else "Close -- it's 4."})
        elif "talking to YOU directly" in event and _asked_for_race(prompt, event):
            actions.append({"type": "race", "to": "all", "prompt": "Race! 3 x 3?", "options": ["9", "6"], "answer": "9"})
        elif "WON the race" in event or "nobody got it" in event:
            actions.append({"type": "say", "to": "all", "kind": "chat", "text": f"Race over: {event[:40]}"})
        elif "talking to YOU directly" in event:
            actions += [
                {"type": "say", "to": name, "private": True, "kind": "chat", "text": f"psst {name}"},
                {"type": "options", "to": "all", "prompt": "What next?", "options": ["Example", "Quiz"]},
            ]
        elif "clicked the option" in event:
            actions.append({"type": "say", "to": name, "kind": "content", "text": "Here is an example."})
    if "Ben: done" in prompt and "task done" not in prompt:
        actions.append({"type": "complete_task", "to": "Ben", "evidence": "said done"})
    if "Ben: done" in prompt and "part 1 covered" not in prompt:
        actions.append({"type": "part_done", "to": "all", "part": 1, "evidence": "part 1 covered"})
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
            assert [t["description"] for t in a.board()["tasks"]] == ["Quick one, Asha: 2 + 2?"]
            # the task is a quiz: its choices are hers to tap; the answer stays on the server
            quiz = a.board()["options"][0]
            assert quiz["task_id"] and [o["text"] for o in quiz["options"]] == ["4", "5"]
            task_msg = next(m for m in a.messages() if m["kind"] == "task")
            assert task_msg["meta"]["options"] == ["4", "5"] and "answer" not in task_msg["meta"]
            assert [p["title"] for p in a.board()["parts"]] and a.board()["parts"][0]["current"]

            # a wrong tap: graded, kept, answered kindly -- the task stays open
            await a.send(type="pick", option_id=quiz["options"][1]["id"])
            await a.pump(lambda s: "Close -- it's 4." in s.texts())
            tap = next(m for m in a.messages() if m["kind"] == "pick")
            assert tap["meta"]["quiz"] and tap["meta"]["correct"] is False and tap["meta"]["right_answer"] == "4"
            assert not a.board()["tasks"][0]["done"]
            await hub.wait_idle()

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
                await a.pump(lambda s: any(o["for_everyone"] for o in s.board()["options"]))
                options = next(o for o in a.board()["options"] if o["for_everyone"])
                assert not options["task_id"] and [o["text"] for o in options["options"]] == ["Example", "Quiz"]

                # a click becomes Asha's message; it closes the set for her only
                await a.send(type="pick", option_id=options["options"][0]["id"])
                await b.pump(lambda s: any(m["kind"] == "pick" and m["text"] == "Example" for m in s.messages()))
                await a.pump(lambda s: not any(o["for_everyone"] for o in s.board()["options"]))
                await hub.wait_idle()
                await a.send(type="pick", option_id=options["options"][1]["id"])
                await a.pump(lambda s: any(f["type"] == "error" for f in s.frames))
                assert any(o["for_everyone"] for o in b.board()["options"]), "Ben hasn't answered yet"

                # the answer to the click is teaching content, acted out on the stage for everyone
                await b.pump(lambda s: "Here is an example." in s.texts()
                             and any(f["type"] == "stage_end" for f in s.frames))
                assert any(f["type"] == "stage" for f in b.frames)

                # a right tap completes the task by itself
                ben_quiz = next(o for o in b.board()["options"] if o["task_id"])
                await b.send(type="pick", option_id=ben_quiz["options"][0]["id"])
                await a.pump(lambda s: any(t["member_name"] == "Ben" and t["done"] for t in s.board()["tasks"]))
                await b.pump(lambda s: "Nice one!" in s.texts())
                await hub.wait_idle()
                done_msg = next(m for m in a.messages() if m["kind"] == "progress")
                assert done_msg["meta"]["evidence"] == 'tapped the right answer: "4"'

                # typing is still welcome (Versa reads it), and a part gets covered
                await b.send(type="message", text="done")
                await a.pump(lambda s: s.board()["parts"][0]["done"])
                await hub.wait_idle()
                assert a.board()["parts"][1]["current"]
                assert any(m["kind"] == "progress" and m["meta"].get("part") == 1 for m in a.messages())

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
async def test_a_race_first_right_tap_wins_and_scores(live):
    """One question for everyone: a wrong tap keeps it open (and gives the
    answer away to nobody), the first right tap wins it, closes it for the
    rest, and scores 3; a race nobody gets reveals the answer."""
    hub = live.app.state.room_hub
    async with httpx.AsyncClient(base_url=live.http) as client:
        asha = (await client.post("/api/rooms", json={"code": "race-01", "name": "Asha", "topic": "times tables"})).json()["member"]
        ben = (await client.post("/api/rooms/race-01/join", json={"name": "Ben"})).json()["member"]
        cai = (await client.post("/api/rooms/race-01/join", json={"name": "Cai"})).json()["member"]
    await hub.wait_idle()
    async with (websockets.connect(f"{live.ws}/api/rooms/race-01/ws?member_id={asha['id']}") as a_ws,
                websockets.connect(f"{live.ws}/api/rooms/race-01/ws?member_id={ben['id']}") as b_ws,
                websockets.connect(f"{live.ws}/api/rooms/race-01/ws?member_id={cai['id']}") as c_ws):
        a, b, c = Socket(a_ws), Socket(b_ws), Socket(c_ws)
        for s in (a, b, c):
            await s.pump(lambda s: any(f["type"] == "state" for f in s.frames))

        seen: set[str] = set()

        async def start_race():
            def new_race(s):
                return [o for o in s.board()["options"] if o.get("race") and o["set_id"] not in seen]

            await a.send(type="message", text="@Versa give us a race")
            await b.pump(lambda s: new_race(s))
            await hub.wait_idle()
            race = new_race(b)[0]
            seen.add(race["set_id"])
            return race

        race = await start_race()
        question = next(m for m in b.messages() if m["kind"] == "question" and m["meta"].get("race"))
        assert "answer" not in question["meta"]  # the answer never leaves the server
        right = next(o for o in race["options"] if o["text"] == "9")
        wrong = next(o for o in race["options"] if o["text"] == "6")

        await b.send(type="pick", option_id=wrong["id"])
        await a.pump(lambda s: any(m["kind"] == "pick" and m["text"] == "6" for m in s.messages()))
        tap = next(m for m in a.messages() if m["kind"] == "pick" and m["text"] == "6")
        assert tap["meta"]["race"] and tap["meta"]["correct"] is False and "right_answer" not in tap["meta"]
        assert any(o["race"] for o in a.board()["options"]), "still open for the others"

        await c.send(type="pick", option_id=right["id"])
        await a.pump(lambda s: any(m["kind"] == "progress" and m["meta"].get("winner") == "Cai" for m in s.messages()))
        await a.pump(lambda s: not any(o["race"] for o in s.board()["options"]))  # closed for Asha too
        await hub.wait_idle()
        await a.pump(lambda s: any(t.startswith("Race over") for t in s.texts()))
        scores = {s["name"]: (s["points"], s["wins"]) for s in a.board()["scores"]}
        assert scores == {"Cai": (3, 1), "Asha": (0, 0), "Ben": (0, 0)}
        assert a.board()["scores"][0]["name"] == "Cai"

        # everyone wrong: nobody wins and the answer comes out
        race = await start_race()
        wrong = next(o for o in race["options"] if o["text"] == "6")
        for s in (a, b, c):
            await s.send(type="pick", option_id=wrong["id"])
        await a.pump(lambda s: any(m["kind"] == "progress" and "race_set_id" in m["meta"]
                                   and m["meta"]["winner"] is None for m in s.messages()))
        nobody = next(m for m in a.messages() if m["kind"] == "progress" and m["meta"].get("race_set_id")
                      and m["meta"]["winner"] is None)
        assert nobody["meta"]["right_answer"] == "9"
        await hub.wait_idle()


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
