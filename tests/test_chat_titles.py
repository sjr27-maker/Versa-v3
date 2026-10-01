"""What a chat is about, for the Home feed's cards (chat_titles.py): one
`DescribeChat` call after a Sandbox chat's first answer, recorded in
`node_calls` (invariant 2) and read back from there -- nothing else is stored.
The card's picture is the chat's latest stage performance, read the same way."""

from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest
import websockets

from versa.audit import NodeCallStore, TranscriptStore
from versa.chat_titles import (
    REDESCRIBE_AFTER_TURNS,
    DescribeChat,
    describe_chat_prompt,
    parse_description,
)
from versa.feed import FeedService
from versa.learner import LearnerStore
from versa.llm import StubLLMClient

from tests.test_server import _turn, live, new_chat  # noqa: F401  (fixtures)


def test_the_prompt_carries_the_chats_own_messages_and_nothing_about_the_learner():
    prompt = describe_chat_prompt(
        ["help with this\n\n[Attached picture -- what it shows: a circuit with a fuse]"],
        "A fuse is a thin wire that melts when too much current flows.",
    )
    assert prompt.startswith("CHAT:DESCRIBE")
    assert "a circuit with a fuse" in prompt and "A fuse is a thin wire" in prompt
    # a long chat: where it started and where it is now, not everything
    long = describe_chat_prompt([f"message {i}" for i in range(20)])
    assert "message 0" in long and "message 1" in long and "message 19" in long
    assert "message 7" not in long and long.count("\n  - ") == 6


def test_a_description_is_cleaned_and_anything_else_is_none():
    assert parse_description('```json\n{"title": "\\"Fuses and circuit symbols.\\"", "about": "How a  fuse\\nworks."}\n```') == {
        "title": "Fuses and circuit symbols", "about": "How a fuse works.",
    }
    assert parse_description('{"title": "Only a title"}') == {"title": "Only a title", "about": ""}
    assert len(parse_description(json.dumps({"title": "x" * 300, "about": "y" * 900}))["title"]) <= 70
    for bad in ["", "no json here", "[1, 2]", '{"title": ""}', '{"title": 3}', '{"about": "no title"}', "{broken"]:
        assert parse_description(bad) is None, bad


@pytest.mark.asyncio(loop_scope="session")
async def test_the_node_returns_nothing_usable_when_the_model_does_not_answer_in_json():
    assert await DescribeChat(StubLLMClient({"CHAT:DESCRIBE": "I'd call it physics"})).run(["what is force?"]) == {}
    named = await DescribeChat(StubLLMClient()).run(["what is a derivative?"])
    assert named["title"] == "What is a derivative"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_chat_is_described_after_its_first_answer_and_the_list_carries_it(live, new_chat, node_calls):  # noqa: F811
    learner_id, sid = await new_chat("titled")
    async with httpx.AsyncClient(base_url=live.http) as client:
        before = (await client.get(f"/api/learners/{learner_id}/sessions")).json()
    assert before[0]["title"] is None and before[0]["about"] is None

    async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
        await _turn(ws, {"type": "message", "text": "what is a derivative?"})
        while live.loop._background_tasks:
            await live.loop.wait_for_background_tasks()
        await _turn(ws, {"type": "message", "text": "and an integral?"})
        while live.loop._background_tasks:
            await live.loop.wait_for_background_tasks()

    calls = await node_calls.list_calls_for_session(UUID(sid), "DescribeChat")
    assert len(calls) == 1, "described once, not on every turn"
    assert calls[0].turn_index == 0
    assert calls[0].input_json["messages"] == ["what is a derivative?"]
    assert calls[0].input_json["answer"]

    async with httpx.AsyncClient(base_url=live.http) as client:
        row = (await client.get(f"/api/learners/{learner_id}/sessions")).json()[0]
        feed = (await client.get(f"/api/learners/{learner_id}/feed")).json()
    assert row["title"] == "What is a derivative" and row["about"]
    assert row["preview"] == "what is a derivative?", "the opening message is still there, untouched"
    assert row["scene"] is None, "only the feed carries the picture"
    assert feed["continue"][0]["title"] == "What is a derivative"


@pytest.mark.asyncio(loop_scope="session")
async def test_the_latest_description_wins_and_a_failed_one_is_ignored(clean_pool):
    learner = await LearnerStore(clean_pool).create("renamed")
    transcript, calls = TranscriptStore(clean_pool), NodeCallStore(clean_pool)
    sid = await transcript.create_session(learner.id)
    await transcript.record_turn(sid, 0, "what is force?")

    async def title():
        return (await transcript.list_session_summaries(learner.id, "sandbox"))[0].title

    assert await title() is None
    await calls.record("DescribeChat", sid, 0, {"messages": ["what is force?"]}, {"title": "Force", "about": "a"})
    assert await title() == "Force"
    await calls.record("DescribeChat", sid, REDESCRIBE_AFTER_TURNS, {"messages": []}, {})  # unreadable reply
    assert await title() == "Force"
    await calls.record("DescribeChat", sid, REDESCRIBE_AFTER_TURNS + 1, {"messages": []},
                       {"title": "Force and momentum", "about": "b"})
    assert await title() == "Force and momentum"
    # nothing was edited: every attempt is still on record
    assert len(await calls.list_calls_for_session(sid, "DescribeChat")) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_the_feeds_card_plays_the_chats_latest_performance_that_has_anything_in_it(clean_pool):
    learner = await LearnerStore(clean_pool).create("staged")
    other = await LearnerStore(clean_pool).create("someone-else")
    transcript, calls = TranscriptStore(clean_pool), NodeCallStore(clean_pool)
    plain = await transcript.create_session(learner.id)
    await transcript.record_turn(plain, 0, "no stage in this one")
    staged = await transcript.create_session(learner.id)
    await transcript.record_turn(staged, 0, "what is current?")
    theirs = await transcript.create_session(other.id)
    await transcript.record_turn(theirs, 0, "someone else's chat")

    first = [{"do": "spawn", "id": "wire", "kind": "cylinder"}]
    latest = [{"do": "spawn", "id": "cell", "kind": "cube"}, {"do": "say", "text": "A cell pushes charge."}]
    await calls.record("StageDirector", staged, 0, {"message": "what is current?"}, first)
    await calls.record("StageDirector", staged, 1, {"message": "and a cell?"}, latest)
    await calls.record("StageDirector", staged, 2, {"message": "ok"}, [])  # a turn the director wrote nothing for
    await calls.record("StageDirector", theirs, 0, {"message": "x"}, [{"do": "jump"}])

    feed = await FeedService(clean_pool, StubLLMClient()).get_feed(learner.id)
    by_session = {c.session_id: c for c in feed.continue_}
    assert set(by_session) == {plain, staged}, "only this learner's chats"
    assert by_session[staged].scene == latest
    assert by_session[plain].scene is None
