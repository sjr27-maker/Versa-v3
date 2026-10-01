"""Learn a topic: explore a topic as a tree, build a course from the chosen
branches, and learn it lesson by lesson with progress tracked.

Flow
    1. Explore. A keyword search (`POST /api/topic-explorations`) produces
       first-level branches -- at least four concepts -- and any branch can
       be expanded into more (`POST /api/topic-nodes/{id}/expand`).
       A PDF or web link (resources.py) is mapped IMMEDIATELY and in full:
       one call reads the whole resource (evenly sampled when it is long)
       and returns every chapter, section and sub-section it actually
       teaches -- front and back matter and anything it doesn't cover are
       left out -- and each branch is located in the resource's text
       (`topic_nodes.source_start/end`, migration 092). Branching further on
       a resource branch reads THAT section of the resource; when the
       section has no parts of its own, a few closely related "beyond the
       resource" branches are offered once, and those branch no further.
    2. Build. The student ticks branches. A ticked branch with no ticked
       ancestor becomes a CHAPTER; ticked branches under it become its
       LESSONS, in tree order. A resource chapter with none ticked takes its
       own sub-sections as lessons; any other chapter gets lessons planned.
       Every lesson is a list of POINTS -- the content it covers, as many as
       there is (a short section two or three, a dense one up to twelve),
       taken from the resource's own text when there is one.
    3. Learn. A lesson chat is an ordinary session (app_mode 'topic',
       sessions.lesson_id) running through the normal SessionLoop, so memory,
       options, stated preferences, claims and consolidation all apply to it
       unchanged. `LessonHooks` adds the lesson's context to AssessAndBranch
       and FinalAnswer: the tutor explains the CURRENT point (from the
       resource's text when there is one) and never asks the student to type
       an answer -- the app then asks for a tap-to-answer quiz or puzzle on
       that point (`POST /api/lessons/{id}/activity`, `LessonQuiz`), and a
       right answer (`/activity-result`) completes the point. The student
       types only when they choose to. A lesson is done when every point is,
       a chapter when every lesson is. The stage acts out the point being
       explained (`LessonHooks.stage_note`), whatever the student typed.
       Courses built before 2026-10-01 keep their learn/practice/apply/check
       tasks, judged after each answer by `JudgeLessonProgress` (through
       `_call_node`, so it lands in node_calls).

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
import contextlib
import itertools
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
from versa import images as _images
from versa import profiles as _profiles
from versa import resources as _resources
from versa import stage as _stage
from versa.audit import TranscriptStore, to_jsonable
from versa.formatting import repair_latex_escapes
from versa.claims import ClaimStore
from versa.embeddings import EmbeddingClient
from versa.interactions import StatedPreferenceStore
from versa.learner import LearnerStore
from versa.llm import LLMClient
from versa.memory import LearnerFactStore
from versa.models import ClaimStatus
from versa.reviews import ReviewStore, apply_reviews_overlay
from versa.session_knobs import SessionKnobs, render_knob_directive

logger = logging.getLogger(__name__)

TaskKind = Literal["learn", "practice", "apply", "check", "point"]
_TASK_KINDS = ("learn", "practice", "apply", "check")
_ROOT_BRANCHES = (5, 8)
_EXPAND_BRANCHES = (3, 6)
_PLANNED_LESSONS = (3, 6)
_TASKS = (3, 5)
# A lesson's points: how much content it covers decides how many.
_POINTS = (2, 12)
# A resource outline: branches per level, top level first.
_OUTLINE_LIMITS = (25, 15, 10)
# How much of a resource one call reads: a section to branch, a chapter to
# plan, a lesson's text for the tutor.
_SECTION_CHARS = 40_000
_LESSON_SOURCE_CHARS = 8_000
# "Beyond the resource" branches offered once a section has no parts of its own.
_EXTRA_BRANCHES = (2, 3)
_PROFILE_FACT_MIN_SIMILARITY = 0.55


# ================================================================ wire models


class NodeOut(BaseModel):
    id: UUID
    parent_id: UUID | None
    title: str
    summary: str
    depth: int
    expanded: bool
    # Whether asking for (more) branches here can still bring any: false for
    # a "beyond the resource" branch, and for a resource branch whose
    # section has run out (it already got its few extras).
    can_branch: bool = True
    beyond_resource: bool = False
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


class ImageIn(BaseModel):
    learner_id: UUID
    image_id: UUID


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
    # where in the resource's text this branch is (migration 092), when it is
    source_start: int | None = None
    source_end: int | None = None
    beyond_resource: bool = False


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


_NODE_COLUMNS = (
    "id, exploration_id, parent_id, title, summary, depth, position, "
    "source_start, source_end, beyond_resource"
)


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

    async def get_resource_slice(self, resource_id: UUID, start: int, end: int) -> str:
        """Characters [start, end) of a resource's text."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT substr(text, $2 + 1, $3) FROM topic_resources WHERE id = $1",
                resource_id, max(start, 0), max(end - start, 0),
            ) or ""

    async def lesson_source(self, lesson_id: UUID) -> tuple[str, str] | None:
        """(resource kind, text) for a lesson of a course built from a
        resource: its own section, or -- for a planned lesson -- its
        chapter's section when that is short enough to hand over whole.
        None for a search course, or when the section wasn't located."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT r.id AS resource_id, r.kind,
                       ln.source_start AS l_start, ln.source_end AS l_end,
                       cn.source_start AS c_start, cn.source_end AS c_end
                FROM topic_lessons l
                JOIN topic_chapters c ON c.id = l.chapter_id
                JOIN topics t ON t.id = c.topic_id
                JOIN topic_explorations e ON e.id = t.exploration_id
                JOIN topic_resources r ON r.id = e.resource_id
                LEFT JOIN topic_nodes ln ON ln.id = l.node_id
                LEFT JOIN topic_nodes cn ON cn.id = c.node_id
                WHERE l.id = $1
                """,
                lesson_id,
            )
        if row is None:
            return None
        if row["l_start"] is not None and row["l_end"] is not None:
            start, end = row["l_start"], min(row["l_end"], row["l_start"] + _LESSON_SOURCE_CHARS)
        elif (row["c_start"] is not None and row["c_end"] is not None
              and row["c_end"] - row["c_start"] <= _LESSON_SOURCE_CHARS):
            start, end = row["c_start"], row["c_end"]
        else:
            return None
        text = await self.get_resource_slice(row["resource_id"], start, end)
        return (row["kind"], text) if text.strip() else None

    async def latest_answer(self, session_id: UUID) -> str:
        """The tutor's latest answer in a chat ("" when there is none yet)."""
        async with self._pool.acquire() as conn:
            out = await conn.fetchval(
                "SELECT output_json FROM node_calls WHERE session_id = $1 AND node_name = 'FinalAnswer' "
                "ORDER BY turn_index DESC, seq DESC LIMIT 1",
                session_id,
            )
        if isinstance(out, str):
            with contextlib.suppress(ValueError):
                out = json.loads(out)
        return out if isinstance(out, str) else ""

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
                source_start, source_end = item.get("source_start"), item.get("source_end")
                beyond = bool(item.get("beyond_resource"))
                await conn.execute(
                    """
                    INSERT INTO topic_nodes
                        (id, exploration_id, parent_id, title, summary, depth, position, generation_id,
                         source_start, source_end, beyond_resource)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    nid, exploration_id, parent.id if parent else None, item["title"],
                    item["summary"], depth, start_position + i, generation_id,
                    source_start, source_end, beyond,
                )
                out.append(NodeRow(
                    id=nid, exploration_id=exploration_id,
                    parent_id=parent.id if parent else None, title=item["title"],
                    summary=item["summary"], depth=depth, position=start_position + i,
                    source_start=source_start, source_end=source_end, beyond_resource=beyond,
                ))
        return out

    async def add_tree(
        self, exploration_id: UUID, parent: NodeRow | None, items: list[dict], generation_id: UUID | None,
        start_position: int = 0,
    ) -> list[NodeRow]:
        """`items` and every level of their `children`, parents first."""
        rows = await self.add_nodes(exploration_id, parent, items, generation_id, start_position)
        for row, item in zip(rows, items, strict=True):
            if item.get("children"):
                await self.add_tree(exploration_id, row, item["children"], generation_id)
        return rows

    async def list_nodes(self, exploration_id: UUID) -> list[NodeRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_NODE_COLUMNS} FROM topic_nodes WHERE exploration_id = $1 "
                "ORDER BY depth, position, created_at",
                exploration_id,
            )
        return [NodeRow(**dict(r)) for r in rows]

    async def get_node(self, node_id: UUID) -> NodeRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_NODE_COLUMNS} FROM topic_nodes WHERE id = $1",
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
        # What they told Versa at sign-up (profiles.py): the level to start
        # a course at and build up from.
        signup = _profiles.profile_lines(await _profiles.ProfileStore(pool).latest(learner_id))
        if signup:
            lines.append("They told Versa at sign-up: " + "; ".join(signup))
            used["signup_profile"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: sign-up profile failed for %s: %s", learner_id, exc)

    try:
        # The thinking style as layer 3 confirms it (style_patterns.py).
        from versa.style_patterns import confirmed_statements

        confirmed = await confirmed_statements(pool, learner_id)
        if confirmed:
            lines.append("How they move through ideas, from their own choices: " + "; ".join(confirmed))
            used["thinking_styles"] = len(confirmed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("profile: style patterns failed for %s: %s", learner_id, exc)
        confirmed = []

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
                "SELECT answer_length_level, depth_level, breadth_level FROM sessions WHERE learner_id = $1 "
                "ORDER BY created_at DESC LIMIT 1",
                learner_id,
            )
        if row is not None:
            knobs = SessionKnobs(
                answer_length=row["answer_length_level"], depth=row["depth_level"],
                breadth=row["breadth_level"],
            )
            if knobs != SessionKnobs():
                lines.append(
                    f"Their latest style sliders: length {knobs.answer_length}/100, "
                    f"depth {knobs.depth}/100, breadth {knobs.breadth}/100 "
                    "(0 = short/gist/focused, 100 = long/rigorous/wide)"
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
        parsed = json.loads(repair_latex_escapes(match.group(0)))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clip(text: object, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse_branch_items(
    raw_items: object, limit: int, avoid: set[str] | None = None, deeper: tuple[int, ...] = (6,),
) -> list[dict]:
    """Validated branches. `deeper`: how many children each level below may
    keep (and so how many levels are read at all). An `anchor` -- where the
    branch starts in a resource's text -- is kept when given."""
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
        item: dict = {"title": title, "summary": summary}
        anchor = _clip(raw.get("anchor"), 160)
        if anchor:
            item["anchor"] = anchor
        if "children" in raw and deeper:
            item["children"] = _parse_branch_items(
                raw.get("children"), deeper[0], avoid={title}, deeper=deeper[1:],
            )
        items.append(item)
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------- finding a branch in a resource


def _anchor_pattern(anchor: str) -> re.Pattern | None:
    words = re.findall(r"\w+", anchor.lower())[:12]
    if not words:
        return None
    return re.compile(r"\W+".join(re.escape(w) for w in words), re.IGNORECASE)


def _find_anchor(text: str, item: dict, start: int, end: int) -> int | None:
    for key in ("anchor", "title"):
        pattern = _anchor_pattern(item.get(key) or "")
        if pattern is None:
            continue
        match = pattern.search(text, start, end)
        if match is not None:
            return match.start()
    return None


def _flatten(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in items:
        out.append(item)
        out += _flatten(item.get("children") or [])
    return out


def _after_contents(text: str, items: list[dict], end: int) -> int:
    """Where the body starts, past a table of contents: the contents list
    the headings close together, and each of them appears AGAIN later, in
    the body. So: the longest run of first appearances (three or more, gaps
    under ~250 characters) counts as contents only when most of those
    headings turn up again -- a short text whose sections really are close
    together is not mistaken for one -- and the body starts where the first
    of them reappears. Found live 2026-10-01: a
    two-chapter book's sections were matched in its contents page.
    No contents: from the start."""
    firsts = sorted(
        (p, i) for i in _flatten(items) if (p := _find_anchor(text, i, 0, end)) is not None
    )
    best: list[tuple[int, dict]] = []
    run: list[tuple[int, dict]] = []
    for hit in firsts:
        if run and hit[0] - run[-1][0] >= 250:
            run = []
        run.append(hit)
        if len(run) > len(best):
            best = list(run)
    if len(best) < 3:
        return 0
    # the body starts where the first of them turns up again
    seconds = [s for p, item in best if (s := _find_anchor(text, item, p + 1, end)) is not None]
    return min(seconds) if len(seconds) * 2 >= len(best) else 0


def locate_sections(text: str, items: list[dict], start: int = 0, end: int | None = None) -> None:
    """Give each branch (and its children, recursively) the part of the
    resource's text it covers: `source_start` where its heading is found,
    `source_end` where the next sibling's is (or the parent's end). A branch
    whose heading isn't found gets no range -- it then reads its parent's.

    A table of contents lists every heading close together near the start;
    when the first pass lands there (most gaps under a few hundred
    characters), the search runs again from after it, to the body."""
    end = len(text) if end is None else end
    if start == 0:
        start = _after_contents(text, items, end)

    def search(from_: int) -> list[int | None]:
        found: list[int | None] = []
        cursor = from_
        for item in items:
            pos = _find_anchor(text, item, cursor, end)
            found.append(pos)
            if pos is not None:
                cursor = pos + 1
        return found

    found = search(start)
    hits = [f for f in found if f is not None]
    if len(hits) >= 3:
        gaps = sorted(b - a for a, b in itertools.pairwise(hits))
        if gaps[len(gaps) // 2] < 250:
            again = search(hits[-1] + 1)
            if sum(f is not None for f in again) >= len(hits) // 2:
                found = again
    for i, item in enumerate(items):
        pos = found[i]
        if pos is None:
            continue
        stop = next((f for f in found[i + 1:] if f is not None), end)
        item["source_start"], item["source_end"] = pos, max(stop, pos + 1)
        if item.get("children"):
            locate_sections(text, item["children"], pos, item["source_end"])


def sample_text(text: str, limit: int) -> str:
    """The whole resource in `limit` characters: all of it when it fits,
    otherwise evenly spaced windows from start to end, each marked with
    where it comes from -- so a long book's last chapters are seen too."""
    if len(text) <= limit:
        return text
    windows = 12
    size = limit // windows
    step = (len(text) - size) / (windows - 1)
    parts = []
    for i in range(windows):
        at = int(i * step)
        parts.append(f"[... from character {at} of {len(text)} ...]\n{text[at:at + size]}")
    return "\n\n".join(parts)


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
    """A resource's text + headings -> a topic title and EVERY branch it
    teaches, up to three levels deep (chapters, sections, sub-sections), in
    its own order, each with the heading it starts at so it can be found in
    the text (`locate_sections`). Nothing the resource doesn't teach."""

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
            "A student brought this resource to learn from. Map EVERYTHING it teaches as a "
            "tree they can choose from -- all of it, not a sample.\n"
            f"Resource title: {title}\n"
            f"{heading_block}"
            f"Text (a long resource is shown as evenly spaced windows from start to end):\n"
            f"<<<\n{excerpt}\n>>>\n"
            f"{profile}"
            "\nReturn a short `title` for the topic and its `branches`, in the resource's own "
            "order:\n"
            f"- top level: every chapter or main part it teaches (at most {_OUTLINE_LIMITS[0]});\n"
            f"- `children`: every section of that part (at most {_OUTLINE_LIMITS[1]} each), and "
            f"their `children`: every sub-section (at most {_OUTLINE_LIMITS[2]} each) -- as deep as "
            "the resource itself goes, and no deeper. A part with no sections has no children.\n"
            "- ONLY what the resource actually teaches. Leave out the cover, contents, preface, "
            "foreword, acknowledgements, about the author, index, glossary lists, bibliography "
            "and references, end-of-book exercises, question banks and answer keys, and any topic "
            "the resource doesn't cover itself -- never add a chapter it doesn't have.\n"
            "- Each branch: `title` (2-6 words), `summary` (one sentence, at most 25 words, "
            "saying what that part of THIS resource covers) and `anchor`: its heading copied "
            "exactly as it appears in the text (so it can be found there).\n"
            'Respond with JSON: {"title": "...", "branches": [{"title": "...", "summary": "...", '
            '"anchor": "...", "children": [{"title": "...", "summary": "...", "anchor": "...", '
            '"children": [...]}]}]}'
        )

    async def run(self, title: str, headings: list[str], excerpt: str, profile: str) -> dict:
        self.last_call_count = 0
        raw = await self._llm.complete(self.prompt(title, headings, excerpt, profile))
        self.last_call_count += 1
        parsed = _json_object(raw) or {}
        return {
            "title": _clip(parsed.get("title") or title, 120),
            "branches": _parse_branch_items(
                parsed.get("branches"), _OUTLINE_LIMITS[0], deeper=_OUTLINE_LIMITS[1:],
            ),
        }


class ExpandSection:
    """One branch of a resource -> its parts, read from THAT section of the
    resource's text. When the section has no parts of its own, a few
    closely related branches just beyond the resource instead ("a little
    extra"), which branch no further."""

    name = "ExpandSection"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    def prompt(self, path: list[str], section: str, existing: list[str], profile: str,
               allow_extra: bool = True) -> str:
        avoid = ""
        if existing:
            avoid = (
                "\nAlready shown under this branch -- do not repeat, rephrase or overlap these:\n"
                + "".join(f"- {e}\n" for e in existing)
            )
        extra_rule = (
            f"- If the section has NO further parts beyond those already shown, return "
            f"`branches: []` and instead {_EXTRA_BRANCHES[0]}-{_EXTRA_BRANCHES[1]} `extra` branches: "
            "ideas just beyond the resource that help understand THIS section (a prerequisite it "
            "assumes, a closely related idea, a real application). Nothing loosely related.\n"
            if allow_extra else "- `extra`: always [] here.\n"
        )
        return (
            "TOPIC:SECTION\n"
            "A student is mapping a resource they brought, to choose what to learn.\n"
            "Path (each step is a part of the one before): " + " > ".join(path) + "\n"
            f"The text of the LAST step, {path[-1]!r}, from the resource:\n<<<\n{section}\n>>>\n"
            f"{avoid}{profile}"
            "\nRules:\n"
            f"- `branches`: the parts this text actually contains, one level deeper, in its own "
            f"order ({_EXPAND_BRANCHES[0]}-{_EXPAND_BRANCHES[1] + 4}). Only what the text covers -- "
            "never a part it doesn't have, never an unrelated chapter. Each: `title` (2-6 words), "
            "`summary` (one sentence, at most 25 words), `anchor`: where it starts, copied exactly "
            "from the text.\n"
            f"{extra_rule}"
            'Respond with JSON: {"branches": [{"title": "...", "summary": "...", "anchor": "..."}], '
            '"extra": [{"title": "...", "summary": "..."}]}'
        )

    async def run(self, path: list[str], section: str, existing: list[str], profile: str,
                  allow_extra: bool = True) -> dict:
        self.last_call_count = 0
        raw = await self._llm.complete(self.prompt(path, section, existing, profile, allow_extra))
        self.last_call_count += 1
        parsed = _json_object(raw) or {}
        branches = _parse_branch_items(
            parsed.get("branches"), _EXPAND_BRANCHES[1] + 4, avoid=set(existing), deeper=(),
        )
        extra = [] if branches or not allow_extra else _parse_branch_items(
            parsed.get("extra"), _EXTRA_BRANCHES[1], avoid=set(existing), deeper=(),
        )
        for item in extra:
            item.pop("anchor", None)
            item["beyond_resource"] = True
        return {"branches": branches, "extra": extra}


_DEFAULT_TASKS = [
    {"kind": "learn", "description": "Work through the core idea with the tutor and explain it back in your own words."},
    {"kind": "practice", "description": "Try a short practice question on it."},
]


def _default_check(title: str) -> dict:
    return {"kind": "check", "description": f"Answer 2-3 short end-of-lesson questions on {title}."}


def normalize_points(raw_points: object, lesson_title: str) -> list[dict]:
    """A lesson's points as tasks of kind 'point': the content it covers, in
    order, at most `_POINTS[1]`, never none."""
    points: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw_points, list):
        for raw in raw_points:
            text = _clip(raw.get("point") if isinstance(raw, dict) else raw, 300)
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            points.append({"kind": "point", "description": text})
    points = points[: _POINTS[1]]
    if not points:
        points.append({"kind": "point", "description": f"The main idea of {lesson_title}."})
    return points


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
    with an objective and the POINTS each covers -- as many as its content
    needs, from the resource's own text when there is one."""

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
        has_source = bool(chapter.get("source")) or any(le.get("source") for le in chosen_lessons)
        if chosen_lessons:
            lesson_rule = (
                "The lessons of this chapter are fixed. Keep exactly these, in this order, with "
                "these titles:\n"
                + "".join(
                    f"{i + 1}. {le['title']} -- {le['summary']}\n"
                    + (f"   Its text in the resource:\n   <<<\n{le['source']}\n   >>>\n" if le.get("source") else "")
                    for i, le in enumerate(chosen_lessons)
                )
            )
        elif chapter.get("source"):
            lesson_rule = (
                "Split this chapter into lessons that follow the resource's own sections, in its "
                f"order ({_PLANNED_LESSONS[0] - 2}-{_PLANNED_LESSONS[1]}; a short chapter may be one lesson).\n"
            )
        else:
            lesson_rule = (
                f"Split this chapter into {_PLANNED_LESSONS[0]}-{_PLANNED_LESSONS[1]} "
                "lessons in a sensible learning order for this student.\n"
            )
        source_block = (
            f"This chapter's text in the student's resource:\n<<<\n{chapter['source']}\n>>>\n"
            if chapter.get("source") else ""
        )
        source_rule = (
            "- The points come from the resource's text: cover EVERYTHING it teaches in that "
            "lesson's part, in its order, and nothing it doesn't (skip exercises' answers, "
            "references and anything off the chapter).\n"
            if has_source else ""
        )
        return (
            "TOPIC:LESSONS\n"
            f"You are planning one chapter of a course called {course_title!r}.\n"
            f"This chapter: {chapter['title']} -- {chapter['summary']}\n"
            f"The course's other chapters (for context only, don't plan them):\n{others}"
            f"{source_block}"
            f"{lesson_rule}"
            f"{profile}"
            "\nA lesson is taught point by point in a chat with a tutor: each point is explained, "
            "then checked with a quick tap-to-answer quiz or puzzle, and the lesson is done when "
            "every point is. For each lesson give a `title`, an `objective` (one sentence: what "
            "the student can do after it) and its `points`:\n"
            "- each point is ONE idea, fact, rule or method to explain, as one short sentence "
            "that states it (\"Acceleration is the rate of change of velocity: a = dv/dt\"), "
            "in teaching order;\n"
            f"- as many as the content needs, {_POINTS[0]}-{_POINTS[1]}: a light lesson two or "
            "three, a dense one more -- never padding, never skipping something it teaches;\n"
            f"{source_rule}"
            "- keep every lesson inside this chapter.\n"
            'Respond with JSON: {"lessons": [{"title": "...", "objective": "...", '
            '"points": ["...", "..."]}]}'
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
                    "tasks": normalize_points(raw_l.get("points"), chosen["title"]),
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
                "tasks": normalize_points(raw_l.get("points"), title),
            })
        if not lessons:
            lessons.append({
                "node_id": None,
                "title": chapter["title"],
                "objective": _clip(chapter["summary"], 300),
                "tasks": normalize_points([], chapter["title"]),
            })
        return lessons


class ActivityChoice(BaseModel):
    id: str
    text: str


class ActivityOut(BaseModel):
    """A lesson task set by the stage: the scene that leads into it (ending
    in the challenge, an `ask`), and facts about the next lesson for the
    slime to tell while the learner is idle. The same challenge is given
    flat (`question`, `choices`, `form`) for the chat, which shows it when
    the stage is off -- one tap answers it wherever it is shown."""
    activity_id: UUID
    task_id: UUID
    task_description: str
    script: list[dict]
    facts: list[str]
    next_lesson: str | None = None
    question: str = ""
    choices: list[ActivityChoice] = []
    form: str = "quiz"


class ActivityResultIn(BaseModel):
    activity_id: UUID
    picked: str


class ActivityResultOut(BaseModel):
    correct: bool
    answer: str
    # why the right answer is right (a quiz on a point), for the chat
    explain: str = ""
    # the same payload a `progress` frame carries, when the task got done
    progress: dict | None = None


class LessonActivity:
    """One fast-tier call: the lesson's CURRENT task turned into something the
    learner does on the stage (stage.task_stage_prompt), plus idle facts
    about the next lesson. Recorded to topic_generations with its prompt and
    output (the precedent every topic-mode call follows)."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def run(self, prompt: str) -> tuple[str, tuple | None]:
        raw = await self._llm.complete(prompt)
        return raw, _stage.parse_task_activity(raw)


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


# What a lesson calls the thing it is taught from (a picture's "text" is the
# reading made of it on upload, images.py).
_SOURCE_WORD = {"pdf": "PDF", "image": "picture"}


def _point_goal(lp: LessonProgress, current: TaskRow, source_kind: str | None) -> str:
    number = next(i for i, t in enumerate(lp.tasks, 1) if t.id == current.id)
    faithful = (
        f"- Teach it from the {_SOURCE_WORD.get(source_kind, 'resource')}'s text below, "
        "faithfully: what it says, in its terms -- add only what is needed to understand it.\n"
        if source_kind else ""
    )
    return (
        f"- The lesson is taught point by point. The CURRENT point is {number} of {len(lp.tasks)}: "
        f"{current.description!r}. Write every answer exactly as you would in a free Sandbox chat -- "
        "your usual voice and formatting, at the length and depth their sliders set.\n"
        "- The lesson decides the main track: when their message is 'start', 'continue', 'next' or asks "
        "to explain it again, explain the CURRENT point (and nothing from later points). A quick "
        "tap-to-answer quiz or puzzle on it follows your answer, so never ask them to type anything and "
        "don't end with a question; you may end with one short line handing over to it (\"Let's check "
        "that with a quick one.\").\n"
        f"{faithful}"
        "- The student decides where to wander: any other message -- a question they typed, or one of "
        "the directions under your last answer they tapped (an example, the why, a picture, a real "
        "use, something deeper) -- is theirs. Answer THAT, fully, as the Sandbox would, even when it "
        "goes beyond the current point; tie it to what the lesson is about where it genuinely helps. "
        "Don't drag it back to the current point, and don't quiz them: end with one short line saying "
        "the lesson carries on whenever they're ready.\n"
    )


def render_lesson_context(
    course: CourseProgress, lesson_id: UUID, profile: str = "", source: tuple[str, str] | None = None,
) -> tuple[str, str]:
    """(answer_context, assess_context) for one lesson turn. Both start with a
    newline so they drop into the prompts like the other blocks do.
    `source`: (resource kind, the lesson's text in it), for a course built
    from a PDF or link (`TopicStore.lesson_source`)."""
    lp = course.lessons[lesson_id]
    chapter = next(c for c in course.chapters if c.id == lp.lesson.chapter_id)
    current = lp.current_task
    task_lines = []
    for i, t in enumerate(lp.tasks, 1):
        mark = "done" if t.id in lp.done_task_ids else ("CURRENT" if current and t.id == current.id else "to do")
        task_lines.append(f"  [{mark}] {i}. ({t.kind}) {t.description}\n")
    points = bool(lp.tasks) and all(t.kind == "point" for t in lp.tasks)
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
    elif current.kind == "point":
        goal = _point_goal(lp, current, source[0] if source else None)
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
        f"{'Points' if points else 'Tasks'} in this lesson ({lp.percent}% complete):\n{''.join(task_lines)}"
        + (f"Other chapters of this course, for connections only:\n{''.join(others)}" if others else "")
        + (f"This lesson's text in the student's {source[0]} (teach from it):\n<<<\n{source[1]}\n>>>\n"
           if source else "")
        + f"{profile}"
        + "How to teach this lesson:\n"
        + goal
        + "- Stay inside this chapter. If the student asks about something outside it, "
        "answer briefly, then steer back -- and name the chapter that covers it if one does.\n"
        "- Make a connection to another chapter only when it genuinely helps them "
        "understand: at most one per answer, naming that chapter. Never force one.\n"
        + ("- If the conversation is just starting, open by stating the lesson's objective in "
           "one sentence, then explain the first point.\n" if points else
           "- If the conversation is just starting, open by stating the lesson's objective in "
           "one sentence, then set the first task.\n")
    )
    assess_context = (
        f"\nThis message is part of a lesson in the student's course {course.topic.title!r}: "
        f"chapter {chapter.title!r}, lesson {lp.lesson.title!r}"
        + (f", current {'point' if current.kind == 'point' else 'task'}: {current.description!r}"
           if current else "")
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
        return render_lesson_context(
            course, lesson_id, _tutoring_profile(profile), await self.store.lesson_source(lesson_id),
        )

    async def stage_note(self, session_id: UUID) -> str | None:
        """What the stage should act out on a lesson turn: the lesson and the
        point being explained now. None for any non-lesson session."""
        lesson_id = await self.store.get_session_lesson(session_id)
        if lesson_id is None:
            return None
        topic_id = await self.store.get_lesson_topic_id(lesson_id)
        course = await load_course(self.store, topic_id) if topic_id else None
        if course is None or lesson_id not in course.lessons:
            return None
        lp = course.lessons[lesson_id]
        chapter = next(c for c in course.chapters if c.id == lp.lesson.chapter_id)
        current = lp.current_task
        where = (
            f"The student is studying their course {course.topic.title!r}: chapter {chapter.title!r}, "
            f"lesson {lp.lesson.title!r} ({lp.lesson.objective})."
        )
        if current is None:
            return where + " Every point of this lesson is done: celebrate it and sum the lesson up."
        return where + f" The lesson's current point: {current.description!r}."

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
        if current is None or current.kind == "point":
            # a point is completed by its tap-to-answer quiz (activity-result),
            # never judged from the chat
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


async def tutoring_sources(
    pool: asyncpg.Pool, learner_id: UUID, *, session_id: UUID | None = None
) -> list[str]:
    """What shapes a lesson chat for this learner, in plain words: the
    tutoring profile (thinking style, confirmed traits) plus what
    FinalAnswer's own personalization blocks read (stated preference,
    past chats through the history block, the session's sliders).
    "Past chats" means another chat than this lesson's own `session_id`:
    a learner's first chat has none, whatever they typed in it."""
    profile = await build_learner_profile(pool, learner_id, include_courses=False)
    keep = ("thinking_styles", "stated_preference", "knobs")
    used = {k: v for k, v in profile.used.items() if k in keep}
    if (profile.used.get("claims") or {}).get("confirmed"):
        used["claims"] = {"confirmed": profile.used["claims"]["confirmed"]}
    async with pool.acquire() as conn:
        if await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM interactions WHERE learner_id = $1 "
            "AND session_id IS DISTINCT FROM $2)",
            learner_id, session_id,
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


def can_branch(node: NodeRow, nodes: list[NodeRow], from_resource: bool) -> bool:
    """Whether asking for (more) branches under `node` can bring anything.
    A search branch always can. A resource branch can until its section has
    run out -- shown by the "beyond the resource" extras it got then -- and
    an extra itself never does."""
    if not from_resource:
        return True
    if node.beyond_resource:
        return False
    return not any(n.parent_id == node.id and n.beyond_resource for n in nodes)


def _tree(nodes: list[NodeRow], from_resource: bool = False) -> list[NodeOut]:
    children: dict[UUID | None, list[NodeRow]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)

    def build(n: NodeRow) -> NodeOut:
        kids = sorted(children.get(n.id, []), key=lambda k: k.position)
        return NodeOut(
            id=n.id, parent_id=n.parent_id, title=n.title, summary=n.summary,
            depth=n.depth, expanded=bool(kids), children=[build(k) for k in kids],
            can_branch=can_branch(n, nodes, from_resource), beyond_resource=n.beyond_resource,
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
        self.section = ExpandSection(llm)
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
            resource=resource,
            root_nodes=_tree(await self.store.list_nodes(exploration.id), exploration.resource_id is not None),
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
            excerpt=sample_text(resource.text, _resources.OUTLINE_TEXT_CHARS), profile=profile.text,
        )
        if not outline["branches"]:
            raise HTTPException(status_code=502, detail="could not outline that resource")
        # the whole tree at once, each branch found in the text
        locate_sections(resource.text, outline["branches"])
        await self.store.add_tree(exploration.id, None, outline["branches"], gid)
        return await self.exploration_out(exploration)

    async def _section_of(self, node: NodeRow, nodes: list[NodeRow], resource_id: UUID) -> tuple[int, int, str]:
        """(start, end, text) of the resource a branch covers: its own range,
        or the nearest ancestor's that has one, or the whole resource."""
        by_id = {n.id: n for n in nodes}
        cursor: NodeRow | None = node
        while cursor is not None and (cursor.source_start is None or cursor.source_end is None):
            cursor = by_id.get(cursor.parent_id) if cursor.parent_id else None
        if cursor is None:
            text, _, _ = await self.store.get_resource_text(resource_id)
            return 0, len(text), text
        start, end = cursor.source_start, cursor.source_end
        return start, end, await self.store.get_resource_slice(resource_id, start, end)

    async def would_generate(self, node: NodeRow, more: bool) -> bool:
        """Whether expanding `node` runs a model call (and so is priced)."""
        exploration = await self.store.get_exploration(node.exploration_id)
        nodes = await self.store.list_nodes(node.exploration_id)
        has_children = any(n.parent_id == node.id for n in nodes)
        if has_children and not more:
            return False
        return can_branch(node, nodes, exploration.resource_id is not None)

    async def expand(self, node_id: UUID, more: bool) -> list[NodeOut]:
        node = await self.store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="unknown branch")
        lock = self._node_locks.setdefault(node_id, asyncio.Lock())
        async with lock:
            exploration = await self.store.get_exploration(node.exploration_id)
            nodes = await self.store.list_nodes(node.exploration_id)
            existing = sorted((n for n in nodes if n.parent_id == node.id), key=lambda n: n.position)
            from_resource = exploration.resource_id is not None
            if (existing and not more) or not can_branch(node, nodes, from_resource):
                return _subtree(nodes, node.id, from_resource)
            by_id = {n.id: n for n in nodes}
            path, cursor = [], node
            while cursor is not None:
                path.append(cursor.title)
                cursor = by_id.get(cursor.parent_id) if cursor.parent_id else None
            path.append(exploration.query)
            path.reverse()
            profile = await build_learner_profile(
                self._pool, exploration.learner_id, query_text=" ".join(path[-2:]),
                embedding_client=self._embeddings,
            )
            if from_resource:
                # branch on what THIS section of the resource contains; when it
                # has no more parts, a few extras just beyond it, once
                start, end, section = await self._section_of(node, nodes, exploration.resource_id)
                result, gid = await self._generate(
                    self.section, learner_id=exploration.learner_id, exploration_id=exploration.id,
                    profile_used=profile.used, path=path, section=section[:_SECTION_CHARS],
                    existing=[e.title for e in existing], profile=profile.text,
                    allow_extra=not node.beyond_resource,
                )
                items = result["branches"]
                if items:
                    locate_sections(section, items)
                    for item in items:  # section-relative -> resource offsets
                        if item.get("source_start") is not None:
                            item["source_start"] += start
                            item["source_end"] = min(item["source_end"] + start, end)
                else:
                    items = result["extra"]
            else:
                items, gid = await self._generate(
                    self.branches, learner_id=exploration.learner_id, exploration_id=exploration.id,
                    profile_used=profile.used, path=path, existing=[e.title for e in existing],
                    profile=profile.text,
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
            return _subtree(await self.store.list_nodes(node.exploration_id), node.id, from_resource)

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
        if exploration.resource_id is not None:
            # a resource chapter with no lessons ticked takes its own
            # sections as lessons (2026-10-01: PDF courses came out as bare
            # chapters, their sub-sections dropped)
            for ch in selection:
                if not ch["lessons"]:
                    ch["lessons"] = sorted(
                        (n for n in nodes if n.parent_id == ch["node"].id and not n.beyond_resource),
                        key=lambda n: n.position,
                    )
        title = (body.title or "").strip() or exploration.query
        profile = await build_learner_profile(
            self._pool, body.learner_id, query_text=title, embedding_client=self._embeddings,
        )
        chapter_labels = [f"{c['node'].title}: {c['node'].summary}" for c in selection]

        async def source_of(node: NodeRow, budget: int) -> str:
            if exploration.resource_id is None or node.source_start is None or node.source_end is None:
                return ""
            end = min(node.source_end, node.source_start + budget)
            return await self.store.get_resource_slice(exploration.resource_id, node.source_start, end)

        async def plan(i: int, ch: dict) -> list[dict]:
            others = [label for j, label in enumerate(chapter_labels) if j != i]
            chosen = [{"node_id": n.id, "title": n.title, "summary": n.summary} for n in ch["lessons"]]
            chapter = {"title": ch["node"].title, "summary": ch["node"].summary}
            if chosen:
                per_lesson = _SECTION_CHARS // len(chosen)
                for le, node in zip(chosen, ch["lessons"], strict=True):
                    if text := await source_of(node, per_lesson):
                        le["source"] = text
            elif text := await source_of(ch["node"], _SECTION_CHARS):
                chapter["source"] = text
            lessons, _ = await self._generate(
                self.plan, learner_id=body.learner_id, exploration_id=exploration.id,
                profile_used=profile.used, course_title=title, chapter=chapter,
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


def _subtree(nodes: list[NodeRow], parent_id: UUID, from_resource: bool = False) -> list[NodeOut]:
    def find(tree: list[NodeOut]) -> list[NodeOut] | None:
        for n in tree:
            if n.id == parent_id:
                return n.children
            found = find(n.children)
            if found is not None:
                return found
        return None

    return find(_tree(nodes, from_resource)) or []


def build_topics_router(
    pool: asyncpg.Pool,
    llm: LLMClient,
    embedding_client: EmbeddingClient | None,
    *,
    ablation_config=None,
    link_fetcher: Callable | None = None,
    sparks=None,
    on_progress: Callable[[UUID, dict], None] | None = None,
) -> APIRouter:
    """`sparks` (a sparks.SparkEngine) prices the generating endpoints --
    exploring, expanding a branch, building a course -- and answers 402 when
    the balance is too low. None: nothing is charged."""
    router = APIRouter(prefix="/api")
    learners = LearnerStore(pool)
    transcript = TranscriptStore(pool)
    service = TopicService(pool, llm, embedding_client)
    store = service.store
    fetch_link = link_fetcher or _resources.fetch_link

    async def require_learner(learner_id: UUID) -> None:
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")

    def priced(learner_id: UUID, action: str, **ref):
        """Charge for generated work, refunded if it fails (sparks.py)."""
        if sparks is None:
            return contextlib.nullcontext()
        return sparks.charged(learner_id, action, ref=ref)

    @router.post("/topic-explorations", response_model=ExplorationOut)
    async def explore(body: ExplorationIn) -> ExplorationOut:
        await require_learner(body.learner_id)
        if not body.query.strip():
            raise HTTPException(status_code=422, detail="query must not be blank")
        async with priced(body.learner_id, "explore_topic", source="search"):
            return await service.explore_keyword(body.learner_id, body.query)

    @router.post("/topic-explorations/from-link", response_model=ExplorationOut)
    async def explore_link(body: LinkIn) -> ExplorationOut:
        await require_learner(body.learner_id)
        try:
            resource = await fetch_link(body.url)
        except _resources.ResourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        async with priced(body.learner_id, "explore_topic", source="link"):
            return await service.explore_resource(body.learner_id, resource)

    @router.post("/topic-explorations/from-pdf", response_model=ExplorationOut)
    async def explore_pdf(learner_id: UUID = Form(...), file: UploadFile = File(...)) -> ExplorationOut:
        await require_learner(learner_id)
        data = await file.read(_resources.MAX_PDF_BYTES + 1)
        try:
            resource = await asyncio.to_thread(_resources.extract_pdf, data, file.filename)
        except _resources.ResourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        async with priced(learner_id, "explore_topic", source="pdf"):
            return await service.explore_resource(learner_id, resource)

    @router.post("/topic-explorations/from-image", response_model=ExplorationOut)
    async def explore_image(body: ImageIn) -> ExplorationOut:
        """A picture the learner took (a page, a syllabus, their notes),
        uploaded and read once by images.py: its reading is the resource."""
        await require_learner(body.learner_id)
        resource = await _images.picture_resource(pool, body.learner_id, body.image_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="unknown picture")
        async with priced(body.learner_id, "explore_topic", source="image"):
            return await service.explore_resource(body.learner_id, resource)

    @router.get("/topic-explorations/{exploration_id}", response_model=ExplorationOut)
    async def get_exploration(exploration_id: UUID) -> ExplorationOut:
        exploration = await store.get_exploration(exploration_id)
        if exploration is None:
            raise HTTPException(status_code=404, detail="unknown exploration")
        return await service.exploration_out(exploration)

    @router.post("/topic-nodes/{node_id}/expand", response_model=list[NodeOut])
    async def expand(node_id: UUID, body: ExpandIn | None = None) -> list[NodeOut]:
        more = bool(body and body.more)
        node = await store.get_node(node_id) if sparks is not None else None
        if node is None:  # unknown (the service answers 404) or nothing to price
            return await service.expand(node_id, more)
        if not await service.would_generate(node, more):  # read back, or nothing left: free
            return await service.expand(node_id, more)
        exploration = await store.get_exploration(node.exploration_id)
        async with priced(exploration.learner_id, "expand_topic", node_id=node_id):
            return await service.expand(node_id, more)

    @router.post("/topics", response_model=TopicOut)
    async def create_topic(body: TopicIn) -> TopicOut:
        await require_learner(body.learner_id)
        async with priced(body.learner_id, "build_course"):
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
            personalized_by=await tutoring_sources(
                pool, course.topic.learner_id, session_id=lp.session_id
            ),
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

    activity = LessonActivity(llm)

    def _next_lesson_title(course: CourseProgress, lesson_id: UUID) -> str | None:
        order = list(course.lessons)
        i = order.index(lesson_id)
        return course.lessons[order[i + 1]].lesson.title if i + 1 < len(order) else None

    @router.post("/lessons/{lesson_id}/activity", response_model=ActivityOut)
    async def lesson_activity(lesson_id: UUID) -> ActivityOut:
        """The lesson's current task, set by the stage: the slime acts out a
        scene and ends by asking the learner to do the task (an on-stage
        question with one right answer). Answer it with /activity-result."""
        course, _ = await lesson_view(lesson_id)
        lp = course.lessons[lesson_id]
        current = lp.current_task
        if current is None:
            raise HTTPException(status_code=409, detail="every task in this lesson is done")
        context, _ = render_lesson_context(course, lesson_id)
        upcoming = _next_lesson_title(course, lesson_id)
        explanation, asked = "", []
        if current.kind == "point" and lp.session_id is not None:
            explanation = await store.latest_answer(lp.session_id)
            async with pool.acquire() as conn:  # "try another": not the same question again
                asked = [r["q"] for r in await conn.fetch(
                    "SELECT output_json->>'question' AS q FROM topic_generations "
                    "WHERE node_name = 'LessonActivity' AND output_json IS NOT NULL "
                    "AND input_json->>'task_id' = $1 ORDER BY created_at",
                    str(current.id),
                ) if r["q"]]
        prompt = _stage.task_stage_prompt(context, current.kind, current.description, upcoming or "",
                                          explanation=explanation, avoid=asked)
        learner_id = course.topic.learner_id
        inputs = {"lesson_id": lesson_id, "task_id": current.id, "prompt": prompt}
        try:
            raw, parsed = await activity.run(prompt)
        except Exception as exc:  # noqa: BLE001 -- recorded, then reported
            await store.record_generation(learner_id=learner_id, exploration_id=None, node_name="LessonActivity",
                                          input_json=inputs, output_json=None, error=repr(exc))
            raise HTTPException(status_code=502, detail="could not set this task on the stage") from None
        if parsed is None:
            await store.record_generation(learner_id=learner_id, exploration_id=None, node_name="LessonActivity",
                                          input_json=inputs, output_json={"raw": raw}, error="no answerable ask")
            raise HTTPException(status_code=502, detail="could not set this task on the stage")
        script, ask, facts = parsed
        gid = await store.record_generation(
            learner_id=learner_id, exploration_id=None, node_name="LessonActivity", input_json=inputs,
            output_json={"raw": raw, "script": script, "facts": facts, "question": ask["question"],
                         "answer": ask["answer"], "explain": ask.get("explain", "")},
            error=None,
        )
        return ActivityOut(
            activity_id=gid, task_id=current.id, task_description=current.description,
            script=script, facts=facts, next_lesson=upcoming, question=ask["question"],
            choices=[ActivityChoice(id=c["id"], text=c["text"]) for c in ask["choices"]],
            form=ask.get("form", "quiz"),
        )

    @router.post("/lessons/{lesson_id}/activity-result", response_model=ActivityResultOut)
    async def lesson_activity_result(lesson_id: UUID, body: ActivityResultIn) -> ActivityResultOut:
        """The learner answered a stage-set task. Right: the task is done --
        what they did on the stage is the evidence -- and the bars move.
        The right answer is the one kept with the activity, never the
        client's word for it."""
        row = await pool.fetchrow(
            "SELECT learner_id, node_name, input_json, output_json FROM topic_generations WHERE id = $1",
            body.activity_id,
        )
        if (row is None or row["node_name"] != "LessonActivity" or row["output_json"] is None
                or str(row["input_json"].get("lesson_id")) != str(lesson_id)):
            raise HTTPException(status_code=404, detail="unknown activity")
        out = row["output_json"]
        correct = body.picked == out.get("answer")
        course, lesson = await lesson_view(lesson_id)
        task_id = UUID(str(row["input_json"]["task_id"]))
        lp = course.lessons[lesson_id]
        explain = str(out.get("explain") or "")
        await store.add_signal(
            learner_id=course.topic.learner_id, kind="quiz_answered", topic_id=course.topic.id,
            payload={"lesson_id": lesson_id, "task_id": task_id, "activity_id": body.activity_id,
                     "picked": body.picked, "correct": correct},
        )
        if not correct or task_id in lp.done_task_ids:
            return ActivityResultOut(correct=correct, answer=str(out.get("answer")), explain=explain)
        choice = next((c.get("text") for c in (out.get("script") or [{}])[-1].get("choices", [])
                       if c.get("id") == body.picked), body.picked)
        await store.add_task_event(
            lesson_id=lesson_id, task_id=task_id, session_id=lesson.session_id, turn_index=None,
            event="completed", evidence=_clip(f"did it on the stage: {out.get('question')} -> {choice}", 300),
        )
        task = next(t for t in lp.tasks if t.id == task_id)
        await store.add_signal(
            learner_id=course.topic.learner_id, kind="task_completed", topic_id=course.topic.id,
            payload={"lesson_id": lesson_id, "task_id": task_id, "task_kind": task.kind, "on_stage": True,
                     "evidence": f"answered the stage's challenge: {choice}"},
        )
        updated = await load_course(store, course.topic.id)
        ulp = updated.lessons[lesson_id]
        progress = {
            "lesson_id": str(lesson_id), "task_id": str(task_id), "lesson_percent": ulp.percent,
            "chapter_percent": updated.chapter_percent(ulp.lesson.chapter_id),
            "topic_percent": updated.percent, "lesson_status": ulp.status,
        }
        if on_progress is not None and lesson.session_id is not None:
            on_progress(lesson.session_id, progress)  # a finished lesson earns its reward
        return ActivityResultOut(correct=True, answer=str(out.get("answer")), explain=explain,
                                 progress=progress)

    router.topic_service = service  # type: ignore[attr-defined]  # exposed for tests
    return router
