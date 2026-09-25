"""The Home feed: what to pick up again, what's related to what you've asked
about, and what's new to explore (wireframe 1: "recommendations, topics to
learn just to learn, similar topic from past, just as youtube feed").

    GET /api/learners/{id}/feed[?refresh=true]

Three sections, each read from or generated from what the learner actually
did:

- continue -- the learner's most recent Sandbox chats that have at least one
  turn, read live on every request (never cached, so a chat you just had
  shows up at once).
- related  -- topics next to what they have asked about, each with a reason
  naming the thing it came from ("because you asked about recursion").
- explore  -- topics outside anything in their history.

`related` and `explore` come from ONE fast-tier call (`GenerateFeed`) over a
bounded history block: the opening and a few later student messages from
recent chats, plus recent `learner_facts` resolutions. A learner with no
history gets `explore` only; `related` is forced empty, never invented.

Generations are cached in `feed_generations` (migration 070) and reused for 6
hours, or regenerated sooner on an explicit refresh, or when a learner who
had no history at generation time now has some. That table is also this
node's audit record: `node_calls` needs a session (see the migration's header
for why a learner-level feed can't use it), so each row keeps the node name,
full input and parsed output instead. Append-only like every other store --
a refresh adds a row, nothing is updated or removed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from versa.audit import TranscriptStore
from versa.learner import LearnerStore
from versa.llm import LLMClient
from versa.models import ChatSummary

logger = logging.getLogger(__name__)

FEED_MAX_AGE = timedelta(hours=6)
_CONTINUE_LIMIT = 6
_HISTORY_SESSIONS = 8
_MESSAGES_PER_SESSION = 3
_HISTORY_FACTS = 12
_MAX_ITEMS_PER_SECTION = 6
_NODE_NAME = "GenerateFeed"

Trigger = Literal["initial", "stale", "refresh", "history_arrived"]


class FeedItem(BaseModel):
    title: str
    hook: str
    reason: str
    starter: str


class FeedSections(BaseModel):
    related: list[FeedItem] = []
    explore: list[FeedItem] = []


class LearnerHistory(BaseModel):
    """What the feed is allowed to know about the learner: their own words
    and recorded resolutions, nothing inferred."""

    sessions: list[list[str]] = []  # per recent chat, its student messages in order
    facts: list[str] = []

    @property
    def message_count(self) -> int:
        return sum(len(s) for s in self.sessions)

    @property
    def is_empty(self) -> bool:
        return not self.sessions and not self.facts

    def render(self) -> str:
        lines: list[str] = []
        for i, messages in enumerate(self.sessions, 1):
            lines.append(f"Chat {i}:")
            lines.extend(f"  - {m}" for m in messages)
        if self.facts:
            lines.append("Things resolved in past chats:")
            lines.extend(f"  - {f}" for f in self.facts)
        return "\n".join(lines)


class FeedGeneration(BaseModel):
    id: UUID
    learner_id: UUID
    trigger: str
    items: FeedSections
    item_count: int
    session_count: int
    error: str | None
    created_at: datetime


class FeedResponse(BaseModel):
    has_history: bool
    generated_at: datetime | None
    cached: bool
    continue_: list[ChatSummary]
    related: list[FeedItem]
    explore: list[FeedItem]

    def to_wire(self) -> dict:
        data = self.model_dump(mode="json")
        data["continue"] = data.pop("continue_")
        return data


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def collect_history(pool: asyncpg.Pool, learner_id: UUID) -> LearnerHistory:
    async with pool.acquire() as conn:
        session_rows = await conn.fetch(
            """
            SELECT s.id
            FROM sessions s
            JOIN turns t ON t.session_id = s.id
            WHERE s.learner_id = $1
            GROUP BY s.id
            ORDER BY max(t.created_at) DESC
            LIMIT $2
            """,
            learner_id,
            _HISTORY_SESSIONS,
        )
        sessions: list[list[str]] = []
        for row in session_rows:
            turns = await conn.fetch(
                "SELECT text FROM turns WHERE session_id = $1 ORDER BY turn_index LIMIT $2",
                row["id"],
                _MESSAGES_PER_SESSION,
            )
            messages = [_clip(t["text"], 200) for t in turns if t["text"].strip()]
            if messages:
                sessions.append(messages)
        fact_rows = await conn.fetch(
            """
            SELECT situation, resolution FROM learner_facts
            WHERE learner_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            learner_id,
            _HISTORY_FACTS,
        )
    facts = [_clip(f"{r['situation']} -> {r['resolution']}", 220) for r in fact_rows]
    return LearnerHistory(sessions=sessions, facts=facts)


def _feed_prompt(history: LearnerHistory) -> str:
    if history.is_empty:
        body = (
            "This learner is new: there is no history yet. Return an EMPTY "
            "`related` list and 6 `explore` topics spanning clearly different "
            "fields (science, maths, history, arts, technology, everyday life).\n"
        )
    else:
        body = (
            "Here is what this learner has asked about, in their own words, most "
            "recent chat first:\n"
            f"{history.render()}\n\n"
            "Return up to 6 `related` topics: each one a natural next step from, "
            "or a close neighbour of, something above. Its `reason` must name the "
            "specific thing it came from, e.g. \"Because you asked about "
            "recursion\". Never suggest a topic they have already covered.\n"
            "Return 6 `explore` topics that are OUTSIDE everything above, spanning "
            "different fields. Their `reason` says why it's worth a look, without "
            "claiming any link to their history.\n"
        )
    return (
        "FEED:RECOMMEND\n"
        "You are choosing learning topics for a student's home feed.\n"
        f"{body}"
        "Each item: `title` (2-6 words), `hook` (one inviting sentence, max 18 "
        "words), `reason` (max 12 words), `starter` (the first message the "
        "student could send to start learning it, written as the student, "
        "max 25 words).\n"
        'Respond with JSON: {"related": [{"title", "hook", "reason", "starter"}], '
        '"explore": [...]}'
    )


def _parse_items(raw_items: object) -> list[FeedItem]:
    items: list[FeedItem] = []
    seen: set[str] = set()
    if not isinstance(raw_items, list):
        return items
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        fields = {k: raw.get(k) for k in ("title", "hook", "reason", "starter")}
        if not all(isinstance(v, str) and v.strip() for v in fields.values()):
            continue
        key = fields["title"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(
            FeedItem(
                title=_clip(fields["title"], 60),
                hook=_clip(fields["hook"], 160),
                reason=_clip(fields["reason"], 90),
                starter=_clip(fields["starter"], 240),
            )
        )
        if len(items) >= _MAX_ITEMS_PER_SECTION:
            break
    return items


def parse_feed_response(raw: str, *, has_history: bool) -> FeedSections:
    """Lenient: a response that isn't the expected JSON yields empty
    sections rather than an exception -- the caller records the attempt and
    serves nothing from it."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match is None:
        return FeedSections()
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return FeedSections()
    if not isinstance(parsed, dict):
        return FeedSections()
    related = _parse_items(parsed.get("related")) if has_history else []
    explore = _parse_items(parsed.get("explore"))
    return FeedSections(related=related, explore=explore)


class GenerateFeed:
    """One fast-tier call: history block in, related + explore topics out."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(self, history: LearnerHistory) -> str:
        return _feed_prompt(history)

    async def run(self, history: LearnerHistory) -> FeedSections:
        self.last_call_count = 0
        raw = await self._llm.complete(self.prompt(history))
        self.last_call_count += 1
        return parse_feed_response(raw, has_history=not history.is_empty)


class FeedGenerationStore:
    """Append-only: `record` inserts, the rest only read."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self,
        *,
        learner_id: UUID,
        trigger: Trigger,
        input_json: dict,
        items: FeedSections,
        history: LearnerHistory,
        error: str | None = None,
    ) -> FeedGeneration:
        row_id = uuid4()
        item_count = len(items.related) + len(items.explore)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO feed_generations (
                    id, learner_id, node_name, trigger, input_json, items,
                    item_count, session_count, fact_count, message_count, error
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING *
                """,
                row_id,
                learner_id,
                _NODE_NAME,
                trigger,
                input_json,
                items.model_dump(),
                item_count,
                len(history.sessions),
                len(history.facts),
                history.message_count,
                error,
            )
        return _row_to_generation(row)

    async def latest_usable(self, learner_id: UUID) -> FeedGeneration | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM feed_generations
                WHERE learner_id = $1 AND item_count > 0 AND error IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                learner_id,
            )
        return _row_to_generation(row) if row is not None else None

    async def list_for_learner(self, learner_id: UUID) -> list[FeedGeneration]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM feed_generations WHERE learner_id = $1 ORDER BY created_at",
                learner_id,
            )
        return [_row_to_generation(r) for r in rows]


def _row_to_generation(row: asyncpg.Record) -> FeedGeneration:
    return FeedGeneration(
        id=row["id"],
        learner_id=row["learner_id"],
        trigger=row["trigger"],
        items=FeedSections(**row["items"]),
        item_count=row["item_count"],
        session_count=row["session_count"],
        error=row["error"],
        created_at=row["created_at"],
    )


class FeedService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        llm: LLMClient,
        *,
        max_age: timedelta = FEED_MAX_AGE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pool = pool
        self._transcript = TranscriptStore(pool)
        self.store = FeedGenerationStore(pool)
        self.node = GenerateFeed(llm)
        self._max_age = max_age
        self._clock = clock
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def _continue(self, learner_id: UUID) -> list[ChatSummary]:
        chats = await self._transcript.list_session_summaries(learner_id, "sandbox")
        return [c for c in chats if c.turn_count > 0][:_CONTINUE_LIMIT]

    def _needs_generation(
        self, cached: FeedGeneration | None, history: LearnerHistory, refresh: bool
    ) -> Trigger | None:
        if refresh:
            return "refresh"
        if cached is None:
            return "initial"
        if cached.session_count == 0 and history.sessions:
            return "history_arrived"
        if self._clock() - cached.created_at >= self._max_age:
            return "stale"
        return None

    async def get_feed(self, learner_id: UUID, *, refresh: bool = False) -> FeedResponse:
        continue_ = await self._continue(learner_id)
        lock = self._locks.setdefault(learner_id, asyncio.Lock())
        async with lock:
            history = await collect_history(self._pool, learner_id)
            cached = await self.store.latest_usable(learner_id)
            trigger = self._needs_generation(cached, history, refresh)
            generation, was_cached = cached, True
            if trigger is not None:
                fresh = await self._generate(learner_id, history, trigger)
                if fresh.item_count > 0 and fresh.error is None:
                    generation, was_cached = fresh, False
        sections = generation.items if generation is not None else FeedSections()
        return FeedResponse(
            has_history=not history.is_empty,
            generated_at=generation.created_at if generation is not None else None,
            cached=was_cached and generation is not None,
            continue_=continue_,
            related=sections.related,
            explore=sections.explore,
        )

    async def _generate(
        self, learner_id: UUID, history: LearnerHistory, trigger: Trigger
    ) -> FeedGeneration:
        input_json = {"history": history.model_dump(), "prompt": self.node.prompt(history)}
        try:
            items = await self.node.run(history)
            error = None if (items.related or items.explore) else "response had no usable items"
        except Exception as exc:  # noqa: BLE001 -- recorded, not raised: the feed falls back to the last good one
            logger.warning("feed generation failed for learner %s: %s", learner_id, exc)
            items, error = FeedSections(), f"{type(exc).__name__}: {exc}"
        return await self.store.record(
            learner_id=learner_id,
            trigger=trigger,
            input_json=input_json,
            items=items,
            history=history,
            error=error,
        )


def build_feed_router(pool: asyncpg.Pool, llm: LLMClient) -> APIRouter:
    router = APIRouter(prefix="/api")
    learners = LearnerStore(pool)
    service = FeedService(pool, llm)

    @router.get("/learners/{learner_id}/feed")
    async def get_feed(learner_id: UUID, refresh: bool = False) -> dict:
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        feed = await service.get_feed(learner_id, refresh=refresh)
        return feed.to_wire()

    router.feed_service = service  # type: ignore[attr-defined]  # exposed for tests
    return router
