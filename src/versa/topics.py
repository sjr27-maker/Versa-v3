"""Learn a topic: explore a topic as a tree, build a course from the chosen
branches, and learn it lesson by lesson with progress tracked.

Flow
    1. Explore. A keyword search (`POST /api/topic-explorations`), a web link
       or an uploaded PDF (resources.py) produces first-level branches. Any
       branch can be expanded into more (`POST /api/topic-nodes/{id}/expand`).
    2. Build. The student ticks branches. A ticked branch with no ticked
       ancestor becomes a CHAPTER; ticked branches under it become its
       LESSONS, in tree order; a chapter with none gets 3-6 lessons planned.
       Every lesson gets 3-5 tasks ending in a 'check' task (the
       end-of-lesson questions).
    3. Learn. A lesson chat is an ordinary session (app_mode 'topic',
       sessions.lesson_id) running through the normal SessionLoop, so memory,
       options, stated preferences, claims and consolidation all apply to it
       unchanged. `LessonHooks` adds the lesson's context to AssessAndBranch
       and FinalAnswer, and after every answer runs `JudgeLessonProgress`
       (through `_call_node`, so it lands in node_calls) to decide whether the
       current task is done; a completion is appended to lesson_task_events
       and pushed to the client as a `progress` event.

Personalization flows both ways.
    In:  branch generation, lesson planning and lesson tutoring get a profile
         built from what the system already knows (`build_learner_profile`):
         confirmed and student-approved thinking styles, non-archived claims
         (with the student's own edits), the latest stated preference,
         relevant past resolutions, the learner's slider levels, and their
         other courses. Every generation records which inputs it used.
    Out: every meaningful action is appended to `topic_signals` (search,
         resource, expand, selection incl. what was shown but NOT chosen,
         lesson open order, turns per task, check results, drifting off the
         chapter). These are episodic, the same wall as CLAUDE.md invariants
         6/8: they are never written into claims or thinking styles directly.
         Lesson chats themselves still feed the existing evidence-gated paths.

Audit. Calls inside a lesson chat go through `SessionLoop._call_node`
(node_calls). Calls outside any chat (branches, outlines, lesson plans) have
no session, so -- as feed.py does -- `topic_generations` keeps the node name,
full input and parsed output.

Append-only. No DELETE and no UPDATE anywhere in this module: explorations,
nodes, courses, tasks, events and signals are only ever inserted. Progress is
derived from the latest event per task, never stored.

This is a course OUTLINE, not a learner model (CLAUDE.md invariant 4): no
prerequisite edges, no mastery estimate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from versa import embeddings as _embeddings
from versa import resources as _resources
from versa.audit import TranscriptStore, to_jsonable
from versa.claims import ClaimStore
from versa.embeddings import EmbeddingClient
from versa.interactions import StatedPreferenceStore
from versa.learner import LearnerStore
from versa.llm import LLMClient
from versa.memory import LearnerFactStore, ThinkingStyleStore
from versa.models import ClaimStatus, ThinkingStyleStatus
from versa.reviews import ReviewStore, apply_reviews_overlay
from versa.session_knobs import SessionKnobs, render_knob_directive

logger = logging.getLogger(__name__)

TaskKind = Literal["learn", "practice", "apply", "check"]
_TASK_KINDS = ("learn", "practice", "apply", "check")
_ROOT_BRANCHES = (5, 8)
_EXPAND_BRANCHES = (3, 6)
_PLANNED_LESSONS = (3, 6)
_TASKS = (3, 5)
_PROFILE_FACT_MIN_SIMILARITY = 0.55


# ================================================================ wire models


class NodeOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    title: str
    summary: str
    depth: int
    expanded: bool
    children: list[NodeOut] = []


class ResourceOut(BaseModel):
    id: UUID
    kind: str
    title: str
    url: str | None
    filename: str | None
    char_count: int


class ExplorationOut(BaseModel):
    id: UUID
    query: str
    source_kind: str
    resource: ResourceOut | None
    root_nodes: list[NodeOut]
    personalized_by: list[str] = []


class ExplorationIn(BaseModel):
    learner_id: UUID
    query: str = Field(min_length=1, max_length=200)


class LinkIn(BaseModel):
    learner_id: UUID
    url: str = Field(min_length=1, max_length=2000)


class ExpandIn(BaseModel):
    more: bool = False


class TopicIn(BaseModel):
    learner_id: UUID
    exploration_id: UUID
    title: str | None = Field(None, max_length=120)
    selected_node_ids: list[UUID] = Field(min_length=1)


class TaskOut(BaseModel):
    id: UUID
    position: int
    kind: str
    description: str
    done: bool


class LessonSummaryOut(BaseModel):
    id: UUID
    title: str
    objective: str
    position: int
    status: Literal["not_started", "in_progress", "done"]
    percent: int
    tasks_total: int
    tasks_done: int


class LessonOut(LessonSummaryOut):
    chapter_id: UUID
    chapter_title: str
    topic_id: UUID
    topic_title: str
    tasks: list[TaskOut]
    session_id: UUID | None
    personalized_by: list[str] = []


class ChapterOut(BaseModel):
    id: UUID
    title: str
    summary: str
    position: int
    percent: int
    lessons: list[LessonSummaryOut]


class TopicOut(BaseModel):
    id: UUID
    title: str
    percent: int
    source_kind: str
    chapters: list[ChapterOut]
    personalized_by: list[str] = []


class TopicSummaryOut(BaseModel):
    id: UUID
    title: str
    percent: int
    chapter_count: int
    lesson_count: int
    lessons_done: int
    updated_at: datetime


class LessonStartOut(BaseModel):
    session_id: UUID


# ================================================================ store rows


class NodeRow(BaseModel):
    id: UUID
    exploration_id: UUID
    parent_id: UUID | None
    title: str
    summary: str
    depth: int
    position: int


class ExplorationRow(BaseModel):
    id: UUID
    learner_id: UUID
    query: str
    source_kind: str
    resource_id: UUID | None
    personalization_used: dict = {}


class TaskRow(BaseModel):
    id: UUID
    lesson_id: UUID
    position: int
    kind: str
    description: str


class LessonRow(BaseModel):
    id: UUID
    chapter_id: UUID
    position: int
    title: str
    objective: str


class ChapterRow(BaseModel):
    id: UUID
    topic_id: UUID
    position: int
    title: str
    summary: str


class TopicRow(BaseModel):
    id: UUID
    learner_id: UUID
    exploration_id: UUID | None
    title: str
    source_kind: str
    created_at: datetime
    personalization_used: dict = {}


class TopicStore:
    """Every table in migrations 072-074. Append-only: insert and read
    methods only -- no delete/remove method, no DELETE or UPDATE SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ---------------------------------------------------------- discovery

    async def add_resource(self, learner_id: UUID, res: _resources.ExtractedResource) -> ResourceOut:
        rid = uuid4()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO topic_resources
                    (id, learner_id, kind, title, url, filename, text, headings, char_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                rid, learner_id, res.kind,
                res.title, res.url, res.filename, res.text, res.headings, len(res.text),
            )
        return await self.get_resource(rid)  # type: ignore[return-value]

    async def get_resource(self, resource_id: UUID) -> ResourceOut | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, kind, title, url, filename, char_count FROM topic_resources WHERE id = $1",
                resource_id,
            )
        return ResourceOut(**dict(row)) if row else None

    async def get_resource_text(self, resource_id: UUID) -> tuple[str, list[str], str]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT text, headings, title FROM topic_resources WHERE id = $1", resource_id
            )
        return row["text"], list(row["headings"] or []), row["title"]

    async def add_exploration(
        self,
        learner_id: UUID,
        query: str,
        source_kind: str,
        resource_id: UUID | None = None,
        personalization_used: dict | None = None,
    ) -> ExplorationRow:
        eid = uuid4()
        used = to_jsonable(personalization_used or {})
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO topic_explorations "
                "(id, learner_id, query, source_kind, resource_id, personalization_used) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                eid, learner_id, query, source_kind, resource_id, used,
            )
        return ExplorationRow(
            id=eid, learner_id=learner_id, query=query, source_kind=source_kind,
            resource_id=resource_id, personalization_used=used,
        )

    async def get_exploration(self, exploration_id: UUID) -> ExplorationRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, learner_id, query, source_kind, resource_id, personalization_used "
                "FROM topic_explorations WHERE id = $1",
                exploration_id,
            )
        return ExplorationRow(**dict(row)) if row else None

    async def record_generation(
        self,
        *,
        learner_id: UUID,
        exploration_id: UUID | None,
        node_name: str,
        input_json: dict,
        output_json: object | None,
        error: str | None,
    ) -> UUID:
        gid = uuid4()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO topic_generations
                    (id, learner_id, exploration_id, node_name, input_json, output_json, error)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                gid, learner_id, exploration_id, node_name, to_jsonable(input_json),
                to_jsonable(output_json) if output_json is not None else None, error,
            )
        return gid

    async def list_generations(self, learner_id: UUID) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM topic_generations WHERE learner_id = $1 ORDER BY created_at",
                learner_id,
            )
        return [dict(r) for r in rows]

    async def add_nodes(
        self,
        exploration_id: UUID,
        parent: NodeRow | None,
        items: list[dict],
        generation_id: UUID | None,
        start_position: int = 0,
    ) -> list[NodeRow]:
        depth = 0 if parent is None else parent.depth + 1
        out: list[NodeRow] = []
        async with self._pool.acquire() as conn:
            for i, item in enumerate(items):
                nid = uuid4()
                await conn.execute(
                    """
                    INSERT INTO topic_nodes
                        (id, exploration_id, parent_id, title, summary, depth, position, generation_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    nid, exploration_id, parent.id if parent else None, item["title"],
                    item["summary"], depth, start_position + i, generation_id,
                )
                out.append(NodeRow(
                    id=nid, exploration_id=exploration_id,
                    parent_id=parent.id if parent else None, title=item["title"],
                    summary=item["summary"], depth=depth, position=start_position + i,
                ))
        return out

    async def list_nodes(self, exploration_id: UUID) -> list[NodeRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, exploration_id, parent_id, title, summary, depth, position "
                "FROM topic_nodes WHERE exploration_id = $1 ORDER BY depth, position, created_at",
                exploration_id,
            )
        return [NodeRow(**dict(r)) for r in rows]

    async def get_node(self, node_id: UUID) -> NodeRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, exploration_id, parent_id, title, summary, depth, position "
                "FROM topic_nodes WHERE id = $1",
                node_id,
            )
        return NodeRow(**dict(row)) if row else None

    # ------------------------------------------------------------- courses

    async def add_topic(
        self,
        *,
        learner_id: UUID,
        exploration_id: UUID | None,
        title: str,
        source_kind: str,
        chapters: list[dict],
        personalization_used: dict | None = None,
    ) -> UUID:
        """`chapters` = [{node_id, title, summary, lessons: [{node_id, title,
        objective, tasks: [{kind, description}]}]}], written in one
        transaction so a course is never half-built."""
        tid = uuid4()
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO topics "
                    "(id, learner_id, exploration_id, title, source_kind, personalization_used) "
                    "VALUES ($1, $2, $3, $4, $5, $6)",
                    tid, learner_id, exploration_id, title, source_kind,
                    to_jsonable(personalization_used or {}),
                )
                for ci, ch in enumerate(chapters):
                    cid = uuid4()
                    await conn.execute(
                        "INSERT INTO topic_chapters (id, topic_id, node_id, position, title, summary) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        cid, tid, ch.get("node_id"), ci, ch["title"], ch["summary"],
                    )
                    for li, le in enumerate(ch["lessons"]):
                        lid = uuid4()
                        await conn.execute(
                            "INSERT INTO topic_lessons (id, chapter_id, node_id, position, title, objective) "
                            "VALUES ($1, $2, $3, $4, $5, $6)",
                            lid, cid, le.get("node_id"), li, le["title"], le["objective"],
                        )
                        for ti, task in enumerate(le["tasks"]):
                            await conn.execute(
                                "INSERT INTO lesson_tasks (id, lesson_id, position, kind, description) "
                                "VALUES ($1, $2, $3, $4, $5)",
                                uuid4(), lid, ti, task["kind"], task["description"],
                            )
        return tid

    async def get_topic(self, topic_id: UUID) -> TopicRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, learner_id, exploration_id, title, source_kind, created_at, "
                "personalization_used FROM topics WHERE id = $1",
                topic_id,
            )
        return TopicRow(**dict(row)) if row else None

    async def list_topics(self, learner_id: UUID) -> list[TopicRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, learner_id, exploration_id, title, source_kind, created_at, "
                "personalization_used FROM topics WHERE learner_id = $1 ORDER BY created_at DESC",
                learner_id,
            )
        return [TopicRow(**dict(r)) for r in rows]

    async def list_chapters(self, topic_id: UUID) -> list[ChapterRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, topic_id, position, title, summary FROM topic_chapters "
                "WHERE topic_id = $1 ORDER BY position",
                topic_id,
            )
        return [ChapterRow(**dict(r)) for r in rows]

    async def list_lessons(self, topic_id: UUID) -> list[LessonRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT l.id, l.chapter_id, l.position, l.title, l.objective
                FROM topic_lessons l JOIN topic_chapters c ON c.id = l.chapter_id
                WHERE c.topic_id = $1 ORDER BY c.position, l.position
                """,
                topic_id,
            )
        return [LessonRow(**dict(r)) for r in rows]

    async def list_tasks(self, topic_id: UUID) -> list[TaskRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.id, t.lesson_id, t.position, t.kind, t.description
                FROM lesson_tasks t
                JOIN topic_lessons l ON l.id = t.lesson_id
                JOIN topic_chapters c ON c.id = l.chapter_id
                WHERE c.topic_id = $1 ORDER BY t.position
                """,
                topic_id,
            )
        return [TaskRow(**dict(r)) for r in rows]

    async def get_lesson_topic_id(self, lesson_id: UUID) -> UUID | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT c.topic_id FROM topic_lessons l JOIN topic_chapters c ON c.id = l.chapter_id "
                "WHERE l.id = $1",
                lesson_id,
            )

    # ------------------------------------------------------------ progress

    async def add_task_event(
        self,
        *,
        lesson_id: UUID,
        task_id: UUID,
        session_id: UUID | None,
        turn_index: int | None,
        event: Literal["completed", "reopened"],
        evidence: str,
    ) -> UUID:
        eid = uuid4()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO lesson_task_events
                    (id, lesson_id, task_id, session_id, turn_index, event, evidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                eid, lesson_id, task_id, session_id, turn_index, event, evidence,
            )
        return eid

    async def list_task_events(self, topic_id: UUID) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.* FROM lesson_task_events e
                JOIN topic_lessons l ON l.id = e.lesson_id
                JOIN topic_chapters c ON c.id = l.chapter_id
                WHERE c.topic_id = $1 ORDER BY e.created_at, e.id
                """,
                topic_id,
            )
        return [dict(r) for r in rows]

    async def lesson_sessions(self, topic_id: UUID) -> list[dict]:
        """(lesson_id, session_id, created_at, last_activity) for every lesson
        chat in this course, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.lesson_id, s.id AS session_id, s.created_at,
                       coalesce(max(t.created_at), s.created_at) AS last_activity
                FROM sessions s
                JOIN topic_lessons l ON l.id = s.lesson_id
                JOIN topic_chapters c ON c.id = l.chapter_id
                LEFT JOIN turns t ON t.session_id = s.id
                WHERE c.topic_id = $1
                GROUP BY s.id
                ORDER BY s.created_at DESC
                """,
                topic_id,
            )
        return [dict(r) for r in rows]

    async def get_session_lesson(self, session_id: UUID) -> UUID | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchval("SELECT lesson_id FROM sessions WHERE id = $1", session_id)

    # ------------------------------------------------------------- signals

    async def add_signal(
        self,
        *,
        learner_id: UUID,
        kind: str,
        payload: dict,
        topic_id: UUID | None = None,
        exploration_id: UUID | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO topic_signals (id, learner_id, topic_id, exploration_id, kind, payload)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid4(), learner_id, topic_id, exploration_id, kind, to_jsonable(payload),
            )

    async def list_signals(self, learner_id: UUID, kind: str | None = None) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM topic_signals WHERE learner_id = $1 "
                "AND ($2::text IS NULL OR kind = $2) ORDER BY created_at, id",
                learner_id, kind,
            )
        return [dict(r) for r in rows]


# ================================================================ progress


class LessonProgress(BaseModel):
    lesson: LessonRow
    tasks: list[TaskRow]
    done_task_ids: set[UUID]
    has_session: bool
    session_id: UUID | None

    @property
    def tasks_done(self) -> int:
        return sum(1 for t in self.tasks if t.id in self.done_task_ids)

    @property
    def percent(self) -> int:
        return round(100 * self.tasks_done / len(self.tasks)) if self.tasks else 0

    @property
    def status(self) -> Literal["not_started", "in_progress", "done"]:
        if self.tasks and self.tasks_done == len(self.tasks):
            return "done"
        if self.tasks_done or self.has_session:
            return "in_progress"
        return "not_started"

    @property
    def current_task(self) -> TaskRow | None:
        return next((t for t in self.tasks if t.id not in self.done_task_ids), None)

    def summary(self) -> LessonSummaryOut:
        return LessonSummaryOut(
            id=self.lesson.id, title=self.lesson.title, objective=self.lesson.objective,
            position=self.lesson.position, status=self.status, percent=self.percent,
            tasks_total=len(self.tasks), tasks_done=self.tasks_done,
        )


class CourseProgress(BaseModel):
    topic: TopicRow
    chapters: list[ChapterRow]
    lessons: dict[UUID, LessonProgress]  # by lesson id, in course order
    updated_at: datetime

    def chapter_lessons(self, chapter_id: UUID) -> list[LessonProgress]:
        return [lp for lp in self.lessons.values() if lp.lesson.chapter_id == chapter_id]

    def chapter_percent(self, chapter_id: UUID) -> int:
        lps = self.chapter_lessons(chapter_id)
        return round(sum(lp.percent for lp in lps) / len(lps)) if lps else 0

    @property
    def percent(self) -> int:
        if not self.chapters:
            return 0
        return round(sum(self.chapter_percent(c.id) for c in self.chapters) / len(self.chapters))

    def order_index(self, lesson_id: UUID) -> int:
        return list(self.lessons).index(lesson_id)

    def to_topic_out(self) -> TopicOut:
        return TopicOut(
            id=self.topic.id, title=self.topic.title, percent=self.percent,
            source_kind=self.topic.source_kind,
            personalized_by=describe_personalization(self.topic.personalization_used),
            chapters=[
                ChapterOut(
                    id=c.id, title=c.title, summary=c.summary, position=c.position,
                    percent=self.chapter_percent(c.id),
                    lessons=[lp.summary() for lp in self.chapter_lessons(c.id)],
                )
                for c in self.chapters
            ],
        )

    def to_summary(self) -> TopicSummaryOut:
        return TopicSummaryOut(
            id=self.topic.id, title=self.topic.title, percent=self.percent,
            chapter_count=len(self.chapters), lesson_count=len(self.lessons),
            lessons_done=sum(1 for lp in self.lessons.values() if lp.status == "done"),
            updated_at=self.updated_at,
        )


def derive_done_task_ids(events: list[dict]) -> set[UUID]:
    """The latest event per task decides: 'completed' -> done, 'reopened' ->
    not done. Events arrive in creation order."""
    latest: dict[UUID, str] = {}
    for e in events:
        latest[e["task_id"]] = e["event"]
    return {tid for tid, ev in latest.items() if ev == "completed"}


async def load_course(store: TopicStore, topic_id: UUID) -> CourseProgress | None:
    topic = await store.get_topic(topic_id)
    if topic is None:
        return None
    chapters = await store.list_chapters(topic_id)
    lessons = await store.list_lessons(topic_id)
    tasks = await store.list_tasks(topic_id)
    events = await store.list_task_events(topic_id)
    sessions = await store.lesson_sessions(topic_id)
    done = derive_done_task_ids(events)
    latest_session: dict[UUID, UUID] = {}
    for s in sessions:  # newest first
        latest_session.setdefault(s["lesson_id"], s["session_id"])
    chapter_pos = {c.id: c.position for c in chapters}
    lessons.sort(key=lambda le: (chapter_pos.get(le.chapter_id, 0), le.position))
    by_lesson: dict[UUID, LessonProgress] = {}
    for le in lessons:
        lesson_tasks = sorted((t for t in tasks if t.lesson_id == le.id), key=lambda t: t.position)
        by_lesson[le.id] = LessonProgress(
            lesson=le, tasks=lesson_tasks,
            done_task_ids={t.id for t in lesson_tasks if t.id in done},
            has_session=le.id in latest_session, session_id=latest_session.get(le.id),
        )
    stamps = [topic.created_at]
    stamps += [e["created_at"] for e in events]
    stamps += [s["last_activity"] for s in sessions]
    return CourseProgress(
        topic=topic, chapters=chapters, lessons=by_lesson, updated_at=max(stamps)
    )


# ================================================================ personalization in


def describe_personalization(used: dict) -> list[str]:
    """`LearnerProfile.used` as plain-language sources for the app's
    "Shaped by: ..." note. Only names what actually went in."""
    out: list[str] = []
    if used.get("thinking_styles"):
        out.append("your thinking style")
    claims = used.get("claims") or {}
    if claims.get("confirmed"):
        out.append("what you've confirmed about how you learn")
    if claims.get("observed"):
        out.append("patterns noticed in your past chats")
    if used.get("stated_preference"):
        out.append("a preference you stated")
    if used.get("learner_facts") or used.get("history"):
        out.append("your past chats")
    if used.get("knobs"):
        out.append("your length and depth sliders")
    if used.get("courses"):
        out.append("your other courses")
    return out


class LearnerProfile(BaseModel):
    text: str
    used: dict


async def build_learner_profile(
    pool: asyncpg.Pool,
    learner_id: UUID,
    *,
    query_text: str | None = None,
    embedding_client: EmbeddingClient | None = None,
    include_courses: bool = True,
    exclude_topic_id: UUID | None = None,
) -> LearnerProfile:
    """What the system already knows about this learner, rendered for a
    generation prompt. Read-only over existing stores; every source degrades
    to nothing on its own failure. `used` records what went in (for audit)."""
    lines: list[str] = []
    used: dict = {}
    reviews = ReviewStore(pool)

    try:
        styles = await ThinkingStyleStore(pool).list_by_learner(learner_id)
        style_lines = []
        for c in styles:
            rs = await reviews.list_for_thinking_style(c.id)
            overlay = apply_reviews_overlay(c.path_summary, rs)
            approved = any(r.review_type == "approve" for r in rs)
            if overlay.archived:
                continue
            if c.status is ThinkingStyleStatus.CONFIRMED or approved:
                why = "student confirmed" if approved else f"seen in {len(set(c.session_ids))} sessions"
                style_lines.append(f"{overlay.statement} ({why})")
        if style_lines:
            lines.append("How they tend to move through material: " + "; ".join(style_lines))
            used["thinking_styles"] = len(style_lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: thinking styles failed for %s: %s", learner_id, exc)

    try:
        claim_store = ClaimStore(pool)
        confirmed, observed = [], []
        for claim in await claim_store.list_for_learner(learner_id):
            if claim.status not in (ClaimStatus.CANDIDATE, ClaimStatus.PROMOTED):
                continue
            current = await claim_store.get_current_statement(claim.id)
            rs = await reviews.list_for_claim(claim.id)
            overlay = apply_reviews_overlay(current.statement if current else claim.statement, rs)
            if overlay.archived:
                continue
            if any(r.review_type in ("approve", "edit") for r in rs) or claim.status is ClaimStatus.PROMOTED:
                confirmed.append(overlay.statement)
            else:
                observed.append(overlay.statement)
        if confirmed:
            lines.append("They confirmed about themselves: " + "; ".join(confirmed[:6]))
        if observed:
            lines.append("Observed, not yet proven (weigh lightly): " + "; ".join(observed[:4]))
        if confirmed or observed:
            used["claims"] = {"confirmed": len(confirmed[:6]), "observed": len(observed[:4])}
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: claims failed for %s: %s", learner_id, exc)

    try:
        pref = await StatedPreferenceStore(pool).get_latest_for_learner(learner_id)
        if pref is not None and pref.stated_preference:
            lines.append(f'They said: "{pref.stated_preference}"')
            used["stated_preference"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: stated preference failed for %s: %s", learner_id, exc)

    if query_text and embedding_client is not None:
        try:
            vec = await embedding_client.embed(query_text, task_type=_embeddings.TASK_QUERY)
            hits = await LearnerFactStore(pool).search_similar(learner_id, vec, limit=4)
            facts = [f for f, sim in hits if sim >= _PROFILE_FACT_MIN_SIMILARITY][:3]
            if facts:
                lines.append(
                    "Related things from their past chats: "
                    + "; ".join(f"{f.situation} -> {f.resolution}" for f in facts)
                )
                used["learner_facts"] = [str(f.id) for f in facts]
        except Exception as exc:  # noqa: BLE001
            logger.warning("profile: fact search failed for %s: %s", learner_id, exc)

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT answer_length_level, depth_level FROM sessions WHERE learner_id = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                learner_id,
            )
        if row is not None:
            knobs = SessionKnobs(answer_length=row["answer_length_level"], depth=row["depth_level"])
            if knobs != SessionKnobs():
                lines.append(
                    f"Their latest style sliders: length {knobs.answer_length}/100, "
                    f"depth {knobs.depth}/100 (0 = short/gist, 100 = long/rigorous)"
                )
                used["knobs"] = knobs.model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: knobs failed for %s: %s", learner_id, exc)

    if include_courses:
        try:
            store = TopicStore(pool)
            courses = []
            for t in (await store.list_topics(learner_id))[:6]:
                if t.id == exclude_topic_id:
                    continue
                course = await load_course(store, t.id)
                if course is not None:
                    courses.append(f"{t.title} ({course.percent}% done)")
            if courses:
                lines.append("Their other courses: " + ", ".join(courses))
                used["courses"] = len(courses)
        except Exception as exc:  # noqa: BLE001
            logger.warning("profile: courses failed for %s: %s", learner_id, exc)

    if not lines:
        return LearnerProfile(text="", used={})
    text = (
        "\nAbout this student -- use it to shape order, emphasis and wording, "
        "never mention it to them, and let the topic itself come first:\n"
        + "".join(f"- {line}\n" for line in lines)
    )
    return LearnerProfile(text=text, used=used)


# ================================================================ generation nodes


def _json_object(raw: str) -> dict | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clip(text: object, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse_branch_items(raw_items: object, limit: int, avoid: set[str] | None = None) -> list[dict]:
    items: list[dict] = []
    seen = {a.strip().lower() for a in (avoid or set())}
    if not isinstance(raw_items, list):
        return items
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title, summary = _clip(raw.get("title"), 80), _clip(raw.get("summary"), 240)
        if not title or not summary or title.lower() in seen:
            continue
        seen.add(title.lower())
        item = {"title": title, "summary": summary}
        if "children" in raw:
            item["children"] = _parse_branch_items(raw.get("children"), 6, avoid={title})
        items.append(item)
        if len(items) >= limit:
            break
    return items


class GenerateBranches:
    """Keyword -> first-level branches, or one branch -> its sub-branches."""

    name = "GenerateBranches"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(
        self,
        path: list[str],
        existing: list[str],
        profile: str,
        resource_note: str = "",
    ) -> str:
        low, high = _ROOT_BRANCHES if len(path) == 1 and not existing else _EXPAND_BRANCHES
        if len(path) == 1:
            where = f"Topic the student searched for: {path[0]}\nMap its main parts."
        else:
            where = (
                "Topic path (each step is a part of the one before): "
                + " > ".join(path)
                + f"\nMap the parts of the LAST step, {path[-1]!r}, one level deeper."
            )
        avoid = ""
        if existing:
            avoid = (
                "\nAlready shown at this level -- do not repeat, rephrase or overlap these; "
                "find genuinely different parts:\n" + "".join(f"- {e}\n" for e in existing)
            )
        return (
            "TOPIC:BRANCHES\n"
            "You are mapping out what a topic contains, so a student can see how it "
            "branches and choose what to learn.\n"
            f"{where}\n"
            f"{resource_note}"
            f"{avoid}"
            f"{profile}"
            f"\nReturn between {low} and {high} branches. Together they should cover "
            "this level of the topic without overlapping each other, ordered in a "
            "sensible learning order for this student. Each branch: `title` (2-6 words) "
            "and `summary` (one sentence, at most 25 words: what this part covers and "
            "why it matters).\n"
            'Respond with JSON: {"branches": [{"title": "...", "summary": "..."}]}'
        )

    async def run(
        self, path: list[str], existing: list[str], profile: str, resource_note: str = ""
    ) -> list[dict]:
        self.last_call_count = 0
        raw = await self._llm.complete(self.prompt(path, existing, profile, resource_note))
        self.last_call_count += 1
        parsed = _json_object(raw) or {}
        high = (_ROOT_BRANCHES if len(path) == 1 and not existing else _EXPAND_BRANCHES)[1]
        return _parse_branch_items(parsed.get("branches"), high, avoid=set(existing))


class OutlineResource:
    """A resource's text + headings -> a topic title and 1-2 levels of
    branches covering only what the resource teaches."""

    name = "OutlineResource"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(self, title: str, headings: list[str], excerpt: str, profile: str) -> str:
        heading_block = (
            "Its headings, in order:\n" + "".join(f"- {h}\n" for h in headings[:120])
            if headings else "It has no headings; work from the text.\n"
        )
        return (
            "TOPIC:OUTLINE\n"
            "A student brought this resource to learn from. Map what it teaches as a "
            "tree they can choose from.\n"
            f"Resource title: {title}\n"
            f"{heading_block}"
            f"Text (may be cut off):\n<<<\n{excerpt}\n>>>\n"
            f"{profile}"
            "\nReturn a short `title` for the topic, and 4-10 top-level `branches` in "
            "the resource's own order, each with 0-6 `children`. Only include what the "
            "resource actually covers. Each branch/child: `title` (2-6 words) and "
            "`summary` (one sentence, at most 25 words).\n"
            'Respond with JSON: {"title": "...", "branches": [{"title": "...", '
            '"summary": "...", "children": [{"title": "...", "summary": "..."}]}]}'
        )

    async def run(self, title: str, headings: list[str], excerpt: str, profile: str) -> dict:
        self.last_call_count = 0
        raw = await self._llm.complete(self.prompt(title, headings, excerpt, profile))
        self.last_call_count += 1
        parsed = _json_object(raw) or {}
        return {
            "title": _clip(parsed.get("title") or title, 120),
            "branches": _parse_branch_items(parsed.get("branches"), 10),
        }


_DEFAULT_TASKS = [
    {"kind": "learn", "description": "Work through the core idea with the tutor and explain it back in your own words."},
    {"kind": "practice", "description": "Try a short practice question on it."},
]


def _default_check(title: str) -> dict:
    return {"kind": "check", "description": f"Answer 2-3 short end-of-lesson questions on {title}."}


def normalize_tasks(raw_tasks: object, lesson_title: str) -> list[dict]:
    """3-5 tasks, valid kinds only, exactly one 'check', and it is last."""
    tasks: list[dict] = []
    check: dict | None = None
    if isinstance(raw_tasks, list):
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind", "")).strip().lower()
            description = _clip(raw.get("description"), 300)
            if kind not in _TASK_KINDS or not description:
                continue
            if kind == "check":
                check = check or {"kind": "check", "description": description}
            else:
                tasks.append({"kind": kind, "description": description})
    tasks = tasks[: _TASKS[1] - 1]
    for default in _DEFAULT_TASKS:
        if len(tasks) >= _TASKS[0] - 1:
            break
        tasks.append(dict(default))
    tasks.append(check or _default_check(lesson_title))
    return tasks


class PlanLessons:
    """One chapter -> its lessons (kept as the student chose them, or planned)
    with an objective and 3-5 tasks each."""

    name = "PlanLessons"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(
        self,
        course_title: str,
        chapter: dict,
        other_chapters: list[str],
        chosen_lessons: list[dict],
        profile: str,
    ) -> str:
        others = "".join(f"- {c}\n" for c in other_chapters) or "- (none)\n"
        if chosen_lessons:
            lesson_rule = (
                "The student chose these lessons for this chapter. Keep exactly these, "
                "in this order, with these titles:\n"
                + "".join(f"{i + 1}. {le['title']} -- {le['summary']}\n" for i, le in enumerate(chosen_lessons))
            )
        else:
            lesson_rule = (
                f"Split this chapter into {_PLANNED_LESSONS[0]}-{_PLANNED_LESSONS[1]} "
                "lessons in a sensible learning order for this student.\n"
            )
        return (
            "TOPIC:LESSONS\n"
            f"You are planning one chapter of a course called {course_title!r}.\n"
            f"This chapter: {chapter['title']} -- {chapter['summary']}\n"
            f"The course's other chapters (for context only, don't plan them):\n{others}"
            f"{lesson_rule}"
            f"{profile}"
            "\nFor each lesson give a `title`, an `objective` (one sentence: what the "
            f"student can do after it) and {_TASKS[0]}-{_TASKS[1]} `tasks` done in a chat "
            "with a tutor, in order. Each task has a `kind` -- learn (understand an "
            "idea and explain it back), practice (a short exercise), apply (use it on a "
            "real case) or check -- and a concrete `description`. The LAST task must be "
            "kind \"check\": 2-4 short end-of-lesson questions the student answers. "
            "Keep every lesson inside this chapter.\n"
            'Respond with JSON: {"lessons": [{"title": "...", "objective": "...", '
            '"tasks": [{"kind": "learn", "description": "..."}]}]}'
        )

    async def run(
        self,
        course_title: str,
        chapter: dict,
        other_chapters: list[str],
        chosen_lessons: list[dict],
        profile: str,
    ) -> list[dict]:
        self.last_call_count = 0
        raw = await self._llm.complete(
            self.prompt(course_title, chapter, other_chapters, chosen_lessons, profile)
        )
        self.last_call_count += 1
        parsed = _json_object(raw) or {}
        raw_lessons = parsed.get("lessons") if isinstance(parsed.get("lessons"), list) else []
        lessons: list[dict] = []
        if chosen_lessons:
            for i, chosen in enumerate(chosen_lessons):
                raw_l = raw_lessons[i] if i < len(raw_lessons) and isinstance(raw_lessons[i], dict) else {}
                lessons.append({
                    "node_id": chosen.get("node_id"),
                    "title": chosen["title"],
                    "objective": _clip(raw_l.get("objective") or chosen["summary"], 300),
                    "tasks": normalize_tasks(raw_l.get("tasks"), chosen["title"]),
                })
            return lessons
        for raw_l in raw_lessons[: _PLANNED_LESSONS[1]]:
            if not isinstance(raw_l, dict):
                continue
            title = _clip(raw_l.get("title"), 100)
            if not title:
                continue
            lessons.append({
                "node_id": None,
                "title": title,
                "objective": _clip(raw_l.get("objective") or title, 300),
                "tasks": normalize_tasks(raw_l.get("tasks"), title),
            })
        if not lessons:
            lessons.append({
                "node_id": None,
                "title": chapter["title"],
                "objective": _clip(chapter["summary"], 300),
                "tasks": normalize_tasks([], chapter["title"]),
            })
        return lessons


class LessonJudgement(BaseModel):
    completed: bool = False
    evidence: str = ""
    drifted: bool = False
    check_passed: bool | None = None


class JudgeLessonProgress:
    """After a lesson turn: is the CURRENT task now done, judged from what
    the student did (not from the tutor explaining)? Also flags a drift off
    the chapter. Fast tier; runs through SessionLoop._call_node."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(self, lesson_context: str, task_kind: str, task_description: str,
               recent_history: str, student_message: str, tutor_message: str) -> str:
        history = f"Earlier in this lesson:\n{recent_history}\n" if recent_history else ""
        return (
            "LESSON:JUDGE\n"
            "A tutor is teaching one lesson of a student's course.\n"
            f"{lesson_context}\n"
            f"The CURRENT task ({task_kind}): {task_description}\n"
            f"{history}"
            f"Latest exchange:\nStudent: {student_message}\nTutor: {tutor_message}\n\n"
            "Is the CURRENT task now complete, judged from what the STUDENT has done? "
            "A tutor explaining something does not complete a task by itself. "
            "learn: the student engaged and showed they understood (explained it back, "
            "asked a follow-up that shows grasp, or used the idea correctly). "
            "practice/apply: the student attempted it and got it essentially right. "
            "check: the student answered the end-of-lesson questions and most answers "
            "were right; set `check_passed` accordingly (null for other kinds). "
            "Also set `drifted` if the student's message was about something outside "
            "this chapter. `evidence`: one sentence citing what the student did.\n"
            'Respond with JSON: {"completed": true or false, "evidence": "...", '
            '"drifted": true or false, "check_passed": true, false or null}'
        )

    async def run(self, lesson_context: str, task_kind: str, task_description: str,
                  recent_history: str, student_message: str, tutor_message: str) -> LessonJudgement:
        self.last_call_count = 0
        raw = await self._llm.complete(self.prompt(
            lesson_context, task_kind, task_description, recent_history,
            student_message, tutor_message,
        ))
        self.last_call_count += 1
        parsed = _json_object(raw) or {}
        check_passed = parsed.get("check_passed")
        return LessonJudgement(
            completed=parsed.get("completed") is True,
            evidence=_clip(parsed.get("evidence"), 300),
            drifted=parsed.get("drifted") is True,
            check_passed=check_passed if isinstance(check_passed, bool) else None,
        )


# ================================================================ course building


def build_selection(nodes: list[NodeRow], selected: set[UUID]) -> list[dict]:
    """The selection rule. Returns chapters in tree (depth-first) order:
    [{node, lessons: [node, ...]}]. A selected node with no selected
    ancestor is a chapter; selected nodes under it are its lessons, in
    depth-first order."""
    children: dict[UUID | None, list[NodeRow]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: n.position)

    chapters: list[dict] = []

    def walk(node: NodeRow, chapter: dict | None) -> None:
        if node.id in selected:
            if chapter is None:
                chapter = {"node": node, "lessons": []}
                chapters.append(chapter)
            else:
                chapter["lessons"].append(node)
        for kid in children.get(node.id, []):
            walk(kid, chapter)

    for root in children.get(None, []):
        walk(root, None)
    return chapters


# ================================================================ lesson context (tutoring)


def render_lesson_context(course: CourseProgress, lesson_id: UUID, profile: str = "") -> tuple[str, str]:
    """(answer_context, assess_context) for one lesson turn. Both start with a
    newline so they drop into the prompts like the other blocks do."""
    lp = course.lessons[lesson_id]
    chapter = next(c for c in course.chapters if c.id == lp.lesson.chapter_id)
    current = lp.current_task
    task_lines = []
    for i, t in enumerate(lp.tasks, 1):
        mark = "done" if t.id in lp.done_task_ids else ("CURRENT" if current and t.id == current.id else "to do")
        task_lines.append(f"  [{mark}] {i}. ({t.kind}) {t.description}\n")
    others = [
        f"  - {c.title}: {c.summary} ({course.chapter_percent(c.id)}% done)\n"
        for c in course.chapters if c.id != chapter.id
    ]
    order = list(course.lessons)
    idx = order.index(lesson_id)
    next_lesson = course.lessons[order[idx + 1]].lesson.title if idx + 1 < len(order) else None
    if current is None:
        goal = (
            "- Every task in this lesson is done. Tell the student the lesson is complete"
            + (f" and suggest the next lesson, {next_lesson!r}" if next_lesson else " -- and the whole course is complete")
            + "; answer anything else they ask briefly.\n"
        )
    elif current.kind == "check":
        goal = (
            "- All the teaching tasks are done: run the end-of-lesson questions now. Ask 2-4 "
            "short questions in one message; when they answer, tell them which were right, "
            "correct any that weren't, and say whether the lesson is complete.\n"
        )
    else:
        goal = (
            f"- Your objective in this conversation is to get the CURRENT task done: "
            f"{current.description!r}. Say plainly what the task is, teach what it needs, "
            "and give the student something to do so they can complete it. Don't move to "
            "the next task until this one is done.\n"
        )
    answer_context = (
        "\nThis conversation is a lesson in the student's own course. Teach it as follows.\n"
        f"Course: {course.topic.title} ({course.percent}% complete)\n"
        f"Chapter {chapter.position + 1} of {len(course.chapters)}: {chapter.title} -- "
        f"{chapter.summary} ({course.chapter_percent(chapter.id)}% complete)\n"
        f"Lesson: {lp.lesson.title}. Objective: {lp.lesson.objective}\n"
        f"Tasks in this lesson ({lp.percent}% complete):\n{''.join(task_lines)}"
        + (f"Other chapters of this course, for connections only:\n{''.join(others)}" if others else "")
        + f"{profile}"
        + "How to teach this lesson:\n"
        + goal
        + "- Stay inside this chapter. If the student asks about something outside it, "
        "answer briefly, then steer back -- and name the chapter that covers it if one does.\n"
        "- Make a connection to another chapter only when it genuinely helps them "
        "understand: at most one per answer, naming that chapter. Never force one.\n"
        "- If the conversation is just starting, open by stating the lesson's objective in "
        "one sentence, then set the first task.\n"
    )
    assess_context = (
        f"\nThis message is part of a lesson in the student's course {course.topic.title!r}: "
        f"chapter {chapter.title!r}, lesson {lp.lesson.title!r}"
        + (f", current task: {current.description!r}" if current else "")
        + ". Anything the lesson already settles is NOT ambiguous; only flag a real fork "
        "the lesson doesn't answer.\n"
    )
    return answer_context, assess_context


# ================================================================ loop hooks


class LessonHooks:
    """What SessionLoop calls for a lesson session. `contexts_for` is read
    once per turn (None for any non-lesson session, which keeps Sandbox
    prompts byte-identical); `after_answer` runs in the turn's background
    tail and judges progress."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm: LLMClient,
        on_progress: Callable[[UUID, dict], None] | None = None,
    ) -> None:
        self._pool = pool
        self.store = TopicStore(pool)
        self.judge = JudgeLessonProgress(llm)
        self.on_progress = on_progress

    async def contexts_for(self, session_id: UUID) -> tuple[str, str] | None:
        lesson_id = await self.store.get_session_lesson(session_id)
        if lesson_id is None:
            return None
        topic_id = await self.store.get_lesson_topic_id(lesson_id)
        course = await load_course(self.store, topic_id) if topic_id else None
        if course is None or lesson_id not in course.lessons:
            return None
        profile = await build_learner_profile(
            self._pool, course.topic.learner_id, include_courses=False,
        )
        # thinking styles + confirmed claims only: FinalAnswer's own
        # personalization blocks already carry history, stated preference
        # and promoted claims.
        return render_lesson_context(course, lesson_id, _tutoring_profile(profile))

    async def after_answer(
        self,
        loop,
        session_id: UUID,
        turn_index: int,
        student_message: str,
        tutor_message: str,
    ) -> None:
        lesson_id = await self.store.get_session_lesson(session_id)
        if lesson_id is None:
            return
        topic_id = await self.store.get_lesson_topic_id(lesson_id)
        course = await load_course(self.store, topic_id)
        lp = course.lessons[lesson_id]
        current = lp.current_task
        if current is None:
            return
        answer_context, _ = render_lesson_context(course, lesson_id)
        recent_history = await loop._build_disambiguation_history(session_id, turn_index)
        judgement: LessonJudgement = await loop._call_node(
            self.judge, session_id, turn_index,
            lesson_context=answer_context, task_kind=current.kind,
            task_description=current.description, recent_history=recent_history,
            student_message=student_message, tutor_message=tutor_message,
        )
        learner_id = course.topic.learner_id
        if judgement.drifted:
            await self.store.add_signal(
                learner_id=learner_id, kind="chapter_drift", topic_id=topic_id,
                payload={"lesson_id": lesson_id, "session_id": session_id,
                         "turn_index": turn_index, "message": _clip(student_message, 300)},
            )
        if not judgement.completed:
            return
        await self.store.add_task_event(
            lesson_id=lesson_id, task_id=current.id, session_id=session_id,
            turn_index=turn_index, event="completed",
            evidence=judgement.evidence or "judged complete",
        )
        prior = [
            e for e in await self.store.list_task_events(topic_id)
            if e["lesson_id"] == lesson_id and e["event"] == "completed" and e["task_id"] != current.id
            and e["session_id"] == session_id
        ]
        since = max((e["turn_index"] for e in prior if e["turn_index"] is not None), default=-1)
        await self.store.add_signal(
            learner_id=learner_id, kind="task_completed", topic_id=topic_id,
            payload={"lesson_id": lesson_id, "task_id": current.id, "task_kind": current.kind,
                     "turns_taken": turn_index - since, "evidence": judgement.evidence},
        )
        if current.kind == "check":
            await self.store.add_signal(
                learner_id=learner_id, kind="check_result", topic_id=topic_id,
                payload={"lesson_id": lesson_id, "passed": judgement.check_passed,
                         "evidence": judgement.evidence},
            )
        updated = await load_course(self.store, topic_id)
        ulp = updated.lessons[lesson_id]
        payload = {
            "lesson_id": str(lesson_id),
            "task_id": str(current.id),
            "lesson_percent": ulp.percent,
            "chapter_percent": updated.chapter_percent(ulp.lesson.chapter_id),
            "topic_percent": updated.percent,
            "lesson_status": ulp.status,
        }
        if self.on_progress is not None:
            self.on_progress(session_id, payload)


async def tutoring_sources(pool: asyncpg.Pool, learner_id: UUID) -> list[str]:
    """What shapes a lesson chat for this learner, in plain words: the
    tutoring profile (thinking style, confirmed traits) plus what
    FinalAnswer's own personalization blocks read (stated preference,
    past chats through the history block, the session's sliders)."""
    profile = await build_learner_profile(pool, learner_id, include_courses=False)
    keep = ("thinking_styles", "stated_preference", "knobs")
    used = {k: v for k, v in profile.used.items() if k in keep}
    if (profile.used.get("claims") or {}).get("confirmed"):
        used["claims"] = {"confirmed": profile.used["claims"]["confirmed"]}
    async with pool.acquire() as conn:
        if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM interactions WHERE learner_id = $1)", learner_id
        ):
            used["history"] = True
    return describe_personalization(used)


def _tutoring_profile(profile: LearnerProfile) -> str:
    if not profile.text:
        return ""
    keep = [
        line for line in profile.text.splitlines()
        if line.startswith("- How they tend") or line.startswith("- They confirmed")
    ]
    if not keep:
        return ""
    return (
        "What the student has confirmed about how they learn (shape your teaching "
        "by it, never mention it):\n" + "\n".join(keep) + "\n"
    )


# ================================================================ service + router


def _tree(nodes: list[NodeRow]) -> list[NodeOut]:
    children: dict[UUID | None, list[NodeRow]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)

    def build(n: NodeRow) -> NodeOut:
        kids = sorted(children.get(n.id, []), key=lambda k: k.position)
        return NodeOut(
            id=n.id, parent_id=n.parent_id, title=n.title, summary=n.summary,
            depth=n.depth, expanded=bool(kids), children=[build(k) for k in kids],
        )

    return [build(n) for n in sorted(children.get(None, []), key=lambda k: k.position)]


class TopicService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        llm: LLMClient,
        embedding_client: EmbeddingClient | None,
    ) -> None:
        self._pool = pool
        self.store = TopicStore(pool)
        self.branches = GenerateBranches(llm)
        self.outline = OutlineResource(llm)
        self.plan = PlanLessons(llm)
        self._embeddings = embedding_client
        self._node_locks: dict[UUID, asyncio.Lock] = {}

    async def _generate(self, node, *, learner_id: UUID, exploration_id: UUID | None,
                        profile_used: dict, **kwargs):
        input_json = {"kwargs": kwargs, "prompt": node.prompt(**kwargs), "personalization_used": profile_used}
        try:
            result = await node.run(**kwargs)
        except Exception as exc:
            await self.store.record_generation(
                learner_id=learner_id, exploration_id=exploration_id, node_name=node.name,
                input_json=input_json, output_json=None, error=f"{type(exc).__name__}: {exc}",
            )
            raise
        gid = await self.store.record_generation(
            learner_id=learner_id, exploration_id=exploration_id, node_name=node.name,
            input_json=input_json, output_json=result, error=None,
        )
        return result, gid

    async def exploration_out(self, exploration: ExplorationRow) -> ExplorationOut:
        resource = (
            await self.store.get_resource(exploration.resource_id) if exploration.resource_id else None
        )
        return ExplorationOut(
            id=exploration.id, query=exploration.query, source_kind=exploration.source_kind,
            resource=resource, root_nodes=_tree(await self.store.list_nodes(exploration.id)),
            personalized_by=describe_personalization(exploration.personalization_used),
        )

    async def explore_keyword(self, learner_id: UUID, query: str) -> ExplorationOut:
        query = " ".join(query.split())
        profile = await build_learner_profile(
            self._pool, learner_id, query_text=query, embedding_client=self._embeddings,
        )
        exploration = await self.store.add_exploration(
            learner_id, query, "search", personalization_used=profile.used
        )
        await self.store.add_signal(
            learner_id=learner_id, kind="search", exploration_id=exploration.id,
            payload={"query": query},
        )
        items, gid = await self._generate(
            self.branches, learner_id=learner_id, exploration_id=exploration.id,
            profile_used=profile.used, path=[query], existing=[], profile=profile.text,
        )
        if not items:
            raise HTTPException(status_code=502, detail="could not map that topic, try rephrasing it")
        await self.store.add_nodes(exploration.id, None, items, gid)
        return await self.exploration_out(exploration)

    async def explore_resource(
        self, learner_id: UUID, resource: _resources.ExtractedResource
    ) -> ExplorationOut:
        saved = await self.store.add_resource(learner_id, resource)
        profile = await build_learner_profile(
            self._pool, learner_id, query_text=resource.title, embedding_client=self._embeddings,
        )
        exploration = await self.store.add_exploration(
            learner_id, resource.title, saved.kind, resource_id=saved.id,
            personalization_used=profile.used,
        )
        await self.store.add_signal(
            learner_id=learner_id, kind="resource", exploration_id=exploration.id,
            payload={"kind": saved.kind, "title": resource.title, "chars": saved.char_count,
                     "url": resource.url, "filename": resource.filename},
        )
        outline, gid = await self._generate(
            self.outline, learner_id=learner_id, exploration_id=exploration.id,
            profile_used=profile.used, title=resource.title, headings=resource.headings,
            excerpt=resource.outline_excerpt(), profile=profile.text,
        )
        if not outline["branches"]:
            raise HTTPException(status_code=502, detail="could not outline that resource")
        roots = await self.store.add_nodes(exploration.id, None, outline["branches"], gid)
        for root, item in zip(roots, outline["branches"], strict=True):
            if item.get("children"):
                await self.store.add_nodes(exploration.id, root, item["children"], gid)
        return await self.exploration_out(exploration)

    async def expand(self, node_id: UUID, more: bool) -> list[NodeOut]:
        node = await self.store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="unknown branch")
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            exploration = await self.store.get_exploration(node.exploration_id)
            nodes = await self.store.list_nodes(node.exploration_id)
            existing = sorted((n for n in nodes if n.parent_id == node.id), key=lambda n: n.position)
            if existing and not more:
                return _subtree(nodes, node.id)
            by_id = {n.id: n for n in nodes}
            path, cursor = [], node
            while cursor is not None:
                path.append(cursor.title)
                cursor = by_id.get(cursor.parent_id) if cursor.parent_id else None
            path.append(exploration.query)
            path.reverse()
            resource_note = ""
            if exploration.resource_id:
                _, _, title = await self.store.get_resource_text(exploration.resource_id)
                resource_note = f"The student is learning this from a resource titled {title!r}.\n"
            profile = await build_learner_profile(
                self._pool, exploration.learner_id, query_text=" ".join(path[-2:]),
                embedding_client=self._embeddings,
            )
            items, gid = await self._generate(
                self.branches, learner_id=exploration.learner_id, exploration_id=exploration.id,
                profile_used=profile.used, path=path, existing=[e.title for e in existing],
                profile=profile.text, resource_note=resource_note,
            )
            await self.store.add_nodes(
                exploration.id, node, items, gid,
                start_position=(max((e.position for e in existing), default=-1) + 1),
            )
            await self.store.add_signal(
                learner_id=exploration.learner_id, kind="expand", exploration_id=exploration.id,
                payload={"node_id": node.id, "title": node.title, "depth": node.depth,
                         "more": more, "new_children": len(items)},
            )
            return _subtree(await self.store.list_nodes(node.exploration_id), node.id)

    async def build_topic(self, body: TopicIn) -> TopicOut:
        exploration = await self.store.get_exploration(body.exploration_id)
        if exploration is None or exploration.learner_id != body.learner_id:
            raise HTTPException(status_code=404, detail="unknown exploration")
        nodes = await self.store.list_nodes(exploration.id)
        node_ids = {n.id for n in nodes}
        selected = set(body.selected_node_ids)
        if not selected <= node_ids:
            raise HTTPException(status_code=422, detail="selected branches must come from this exploration")
        selection = build_selection(nodes, selected)
        title = (body.title or "").strip() or exploration.query
        profile = await build_learner_profile(
            self._pool, body.learner_id, query_text=title, embedding_client=self._embeddings,
        )
        chapter_labels = [f"{c['node'].title}: {c['node'].summary}" for c in selection]

        async def plan(i: int, ch: dict) -> list[dict]:
            others = [label for j, label in enumerate(chapter_labels) if j != i]
            chosen = [{"node_id": n.id, "title": n.title, "summary": n.summary} for n in ch["lessons"]]
            lessons, _ = await self._generate(
                self.plan, learner_id=body.learner_id, exploration_id=exploration.id,
                profile_used=profile.used, course_title=title,
                chapter={"title": ch["node"].title, "summary": ch["node"].summary},
                other_chapters=others, chosen_lessons=chosen, profile=profile.text,
            )
            return lessons

        planned = await asyncio.gather(*(plan(i, ch) for i, ch in enumerate(selection)))
        chapters = [
            {"node_id": ch["node"].id, "title": ch["node"].title, "summary": ch["node"].summary,
             "lessons": lessons}
            for ch, lessons in zip(selection, planned, strict=True)
        ]
        topic_id = await self.store.add_topic(
            learner_id=body.learner_id, exploration_id=exploration.id, title=title,
            source_kind=exploration.source_kind, chapters=chapters,
            personalization_used=profile.used,
        )
        max_depth_shown = max((n.depth for n in nodes), default=0)
        await self.store.add_signal(
            learner_id=body.learner_id, kind="selection", topic_id=topic_id,
            exploration_id=exploration.id,
            payload={
                "selected": [{"id": n.id, "title": n.title, "depth": n.depth}
                             for n in nodes if n.id in selected],
                "shown_not_selected": [{"id": n.id, "title": n.title, "depth": n.depth}
                                       for n in nodes if n.id not in selected],
                "max_depth_shown": max_depth_shown,
                "max_depth_selected": max((n.depth for n in nodes if n.id in selected), default=0),
                "chapters": len(chapters),
                "lessons_chosen": sum(len(c["lessons"]) for c in selection),
                "lessons_total": sum(len(c["lessons"]) for c in chapters),
            },
        )
        course = await load_course(self.store, topic_id)
        return course.to_topic_out()


def _subtree(nodes: list[NodeRow], parent_id: UUID) -> list[NodeOut]:
    def find(tree: list[NodeOut]) -> list[NodeOut] | None:
        for n in tree:
            if n.id == parent_id:
                return n.children
            found = find(n.children)
            if found is not None:
                return found
        return None

    return find(_tree(nodes)) or []


def build_topics_router(
    pool: asyncpg.Pool,
    llm: LLMClient,
    embedding_client: EmbeddingClient | None,
    *,
    ablation_config=None,
    link_fetcher: Callable | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    learners = LearnerStore(pool)
    transcript = TranscriptStore(pool)
    service = TopicService(pool, llm, embedding_client)
    store = service.store
    fetch_link = link_fetcher or _resources.fetch_link

    async def require_learner(learner_id: UUID) -> None:
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")

    @router.post("/topic-explorations", response_model=ExplorationOut)
    async def explore(body: ExplorationIn) -> ExplorationOut:
        await require_learner(body.learner_id)
        if not body.query.strip():
            raise HTTPException(status_code=422, detail="query must not be blank")
        return await service.explore_keyword(body.learner_id, body.query)

    @router.post("/topic-explorations/from-link", response_model=ExplorationOut)
    async def explore_link(body: LinkIn) -> ExplorationOut:
        await require_learner(body.learner_id)
        try:
            resource = await fetch_link(body.url)
        except _resources.ResourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return await service.explore_resource(body.learner_id, resource)

    @router.post("/topic-explorations/from-pdf", response_model=ExplorationOut)
    async def explore_pdf(learner_id: UUID = Form(...), file: UploadFile = File(...)) -> ExplorationOut:
        await require_learner(learner_id)
        data = await file.read(_resources.MAX_PDF_BYTES + 1)
        try:
            resource = await asyncio.to_thread(_resources.extract_pdf, data, file.filename)
        except _resources.ResourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return await service.explore_resource(learner_id, resource)

    @router.get("/topic-explorations/{exploration_id}", response_model=ExplorationOut)
    async def get_exploration(exploration_id: UUID) -> ExplorationOut:
        exploration = await store.get_exploration(exploration_id)
        if exploration is None:
            raise HTTPException(status_code=404, detail="unknown exploration")
        return await service.exploration_out(exploration)

    @router.post("/topic-nodes/{node_id}/expand", response_model=list[NodeOut])
    async def expand(node_id: UUID, body: ExpandIn | None = None) -> list[NodeOut]:
        return await service.expand(node_id, bool(body and body.more))

    @router.post("/topics", response_model=TopicOut)
    async def create_topic(body: TopicIn) -> TopicOut:
        await require_learner(body.learner_id)
        return await service.build_topic(body)

    @router.get("/learners/{learner_id}/topics", response_model=list[TopicSummaryOut])
    async def list_topics(learner_id: UUID) -> list[TopicSummaryOut]:
        await require_learner(learner_id)
        out = []
        for t in await store.list_topics(learner_id):
            course = await load_course(store, t.id)
            if course is not None:
                out.append(course.to_summary())
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out

    @router.get("/topics/{topic_id}", response_model=TopicOut)
    async def get_topic(topic_id: UUID) -> TopicOut:
        course = await load_course(store, topic_id)
        if course is None:
            raise HTTPException(status_code=404, detail="unknown topic")
        return course.to_topic_out()

    async def lesson_view(lesson_id: UUID) -> tuple[CourseProgress, LessonOut]:
        topic_id = await store.get_lesson_topic_id(lesson_id)
        course = await load_course(store, topic_id) if topic_id else None
        if course is None or lesson_id not in course.lessons:
            raise HTTPException(status_code=404, detail="unknown lesson")
        lp = course.lessons[lesson_id]
        chapter = next(c for c in course.chapters if c.id == lp.lesson.chapter_id)
        return course, LessonOut(
            **lp.summary().model_dump(), chapter_id=chapter.id, chapter_title=chapter.title,
            topic_id=course.topic.id, topic_title=course.topic.title,
            tasks=[TaskOut(id=t.id, position=t.position, kind=t.kind, description=t.description,
                           done=t.id in lp.done_task_ids) for t in lp.tasks],
            session_id=lp.session_id,
            personalized_by=await tutoring_sources(pool, course.topic.learner_id),
        )

    @router.get("/lessons/{lesson_id}", response_model=LessonOut)
    async def get_lesson(lesson_id: UUID) -> LessonOut:
        _, out = await lesson_view(lesson_id)
        return out

    @router.post("/lessons/{lesson_id}/start", response_model=LessonStartOut)
    async def start_lesson(lesson_id: UUID) -> LessonStartOut:
        course, lesson = await lesson_view(lesson_id)
        session_id = lesson.session_id
        if session_id is None:
            session_id = await transcript.create_session(
                course.topic.learner_id, ablation_config=ablation_config,
                app_mode="topic", lesson_id=lesson_id,
            )
        opens = await store.list_signals(course.topic.learner_id, "lesson_open")
        previous = next(
            (s["payload"] for s in reversed(opens) if s["topic_id"] == course.topic.id), None
        )
        index = course.order_index(lesson_id)
        prev_index = previous.get("order_index") if previous else None
        await store.add_signal(
            learner_id=course.topic.learner_id, kind="lesson_open", topic_id=course.topic.id,
            payload={
                "lesson_id": lesson_id, "order_index": index,
                "previous_lesson_id": previous.get("lesson_id") if previous else None,
                "sequential": (index == 0) if prev_index is None
                else index in (prev_index, prev_index + 1),
                "resumed": lesson.session_id is not None,
                "lesson_status": lesson.status,
            },
        )
        return LessonStartOut(session_id=session_id)

    router.topic_service = service  # type: ignore[attr-defined]  # exposed for tests
    return router
