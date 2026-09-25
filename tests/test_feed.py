"""The Home feed (feed.py, migration 070): history collection, the cached
generation and its 6-hour rule, the no-history case, failures, and the
endpoint."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from versa.audit import TranscriptStore
from versa.feed import (
    FeedService,
    build_feed_router,
    collect_history,
    parse_feed_response,
)
from versa.learner import LearnerStore
from versa.llm import StubLLMClient

ITEM = {"title": "Tail recursion", "hook": "A loop in disguise.", "reason": "Because you asked about recursion",
        "starter": "What is tail recursion?"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


class FailingLLM:
    async def complete(self, prompt: str) -> str:
        raise RuntimeError("quota")


def feed_prompts(llm: StubLLMClient) -> list[str]:
    return [p for p in llm.prompts if p.startswith("FEED:RECOMMEND")]


async def _learner(pool):
    return await LearnerStore(pool).create(label=f"feed-{uuid4().hex[:8]}")


async def _chat(pool, learner_id, *messages):
    transcript = TranscriptStore(pool)
    sid = await transcript.create_session(learner_id)
    for i, m in enumerate(messages):
        await transcript.record_turn(sid, i, m)
    return sid


def test_parse_drops_related_without_history_and_skips_bad_items():
    raw = json.dumps({
        "related": [ITEM],
        "explore": [ITEM, ITEM, {"title": "x"}, "junk", {**ITEM, "title": "Stars"}],
    })
    no_history = parse_feed_response(raw, has_history=False)
    assert no_history.related == []
    assert [i.title for i in no_history.explore] == ["Tail recursion", "Stars"]
    with_history = parse_feed_response("Here you go: " + raw, has_history=True)
    assert [i.title for i in with_history.related] == ["Tail recursion"]


def test_parse_non_json_is_empty_not_an_error():
    sections = parse_feed_response("sorry, no", has_history=True)
    assert sections.related == [] and sections.explore == []


@pytest.mark.asyncio(loop_scope="session")
async def test_new_learner_gets_explore_only(clean_pool):
    learner = await _learner(clean_pool)
    llm = StubLLMClient()
    feed = await FeedService(clean_pool, llm).get_feed(learner.id)
    assert feed.has_history is False
    assert feed.continue_ == [] and feed.related == []
    assert len(feed.explore) == 3
    assert feed.cached is False
    assert "no history yet" in feed_prompts(llm)[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_generation_is_cached_and_regenerated_after_six_hours(clean_pool):
    learner = await _learner(clean_pool)
    llm, clock = StubLLMClient(), Clock()
    service = FeedService(clean_pool, llm, clock=clock)
    first = await service.get_feed(learner.id)

    clock.now += timedelta(hours=5, minutes=59)
    second = await service.get_feed(learner.id)
    assert second.cached is True
    assert second.generated_at == first.generated_at
    assert len(feed_prompts(llm)) == 1

    clock.now += timedelta(minutes=2)  # created_at is the DB's clock, a few ms after clock.now started
    third = await service.get_feed(learner.id)
    assert third.cached is False
    assert len(feed_prompts(llm)) == 2
    rows = await service.store.list_for_learner(learner.id)
    assert [r.trigger for r in rows] == ["initial", "stale"]


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_adds_a_row_and_keeps_the_old_one(clean_pool):
    learner = await _learner(clean_pool)
    llm = StubLLMClient()
    service = FeedService(clean_pool, llm)
    await service.get_feed(learner.id)
    await service.get_feed(learner.id, refresh=True)
    rows = await service.store.list_for_learner(learner.id)
    assert [r.trigger for r in rows] == ["initial", "refresh"]


@pytest.mark.asyncio(loop_scope="session")
async def test_history_uses_the_learners_own_words_and_continue_skips_empty_chats(clean_pool):
    learner = await _learner(clean_pool)
    older = await _chat(clean_pool, learner.id, "how does recursion work?", "what about the base case?")
    await _chat(clean_pool, learner.id)  # a chat opened but never used
    newer = await _chat(clean_pool, learner.id, "explain photosynthesis")
    llm = StubLLMClient({"FEED:RECOMMEND": json.dumps({"related": [ITEM], "explore": [ITEM]})})
    feed = await FeedService(clean_pool, llm).get_feed(learner.id)

    assert feed.has_history is True
    assert [c.session_id for c in feed.continue_] == [newer, older]
    assert feed.related[0].reason == "Because you asked about recursion"
    prompt = feed_prompts(llm)[0]
    assert "how does recursion work?" in prompt and "explain photosynthesis" in prompt
    assert prompt.index("explain photosynthesis") < prompt.index("how does recursion work?")

    history = await collect_history(clean_pool, learner.id)
    assert history.sessions == [["explain photosynthesis"], ["how does recursion work?", "what about the base case?"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_first_history_triggers_regeneration_inside_six_hours(clean_pool):
    learner = await _learner(clean_pool)
    llm = StubLLMClient()
    service = FeedService(clean_pool, llm)
    empty = await service.get_feed(learner.id)
    assert empty.related == []
    await _chat(clean_pool, learner.id, "what is a black hole?")
    after = await service.get_feed(learner.id)
    assert after.cached is False and len(after.related) == 1
    rows = await service.store.list_for_learner(learner.id)
    assert [r.trigger for r in rows] == ["initial", "history_arrived"]


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_generation_is_recorded_and_falls_back_to_the_last_good_feed(clean_pool):
    learner = await _learner(clean_pool)
    good = FeedService(clean_pool, StubLLMClient())
    before = await good.get_feed(learner.id)

    failing = FeedService(clean_pool, FailingLLM())
    after = await failing.get_feed(learner.id, refresh=True)
    assert [i.title for i in after.explore] == [i.title for i in before.explore]
    assert after.cached is True
    rows = await failing.store.list_for_learner(learner.id)
    assert rows[-1].error == "RuntimeError: quota" and rows[-1].item_count == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_failure_with_nothing_cached_returns_empty_sections(clean_pool):
    learner = await _learner(clean_pool)
    feed = await FeedService(clean_pool, FailingLLM()).get_feed(learner.id)
    assert feed.explore == [] and feed.generated_at is None


@pytest.mark.asyncio(loop_scope="session")
async def test_endpoint_shape_and_unknown_learner(clean_pool):
    learner = await _learner(clean_pool)
    await _chat(clean_pool, learner.id, "how do vaccines work?")
    app = FastAPI()
    app.include_router(build_feed_router(clean_pool, StubLLMClient()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        r = await client.get(f"/api/learners/{learner.id}/feed")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"has_history", "generated_at", "cached", "continue", "related", "explore"}
        assert body["continue"][0]["preview"] == "how do vaccines work?"
        assert body["explore"][0]["starter"]
        missing = await client.get(f"/api/learners/{uuid4()}/feed")
        assert missing.status_code == 404
