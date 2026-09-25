"""The rooms tables (migration rooms_001_rooms.sql), insert-only.

Nothing here updates or removes a row (CLAUDE.md invariant 12). The parts of
a room that change over time are derived from rows that were only ever
added: a task is done if its latest event says so (`done_task_ids`), and an
option set is open for someone until they pick from it or a newer set for
the same audience arrives (`open_option_sets`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa.audit import to_jsonable

Sender = Literal["member", "versa", "system"]
MessageKind = Literal["text", "content", "question", "task", "progress", "pick", "event"]
TaskKind = Literal["learn", "practice", "apply", "check", "discuss"]
TASK_KINDS: tuple[str, ...] = ("learn", "practice", "apply", "check", "discuss")


class RoomCodeTaken(Exception):
    pass


class RoomRow(BaseModel):
    id: UUID
    code: str
    title: str
    source_kind: Literal["search", "pdf", "link"]
    query: str
    resource_url: str | None = None
    resource_filename: str | None = None
    resource_excerpt: str | None = None
    outline: list[dict]
    created_by: str
    created_at: datetime


class MemberRow(BaseModel):
    id: UUID
    room_id: UUID
    name: str
    joined_at: datetime


class MessageRow(BaseModel):
    id: UUID
    room_id: UUID
    seq: int
    sender: Sender
    member_id: UUID | None
    kind: MessageKind
    text: str
    to_member_id: UUID | None
    private: bool
    meta: dict
    created_at: datetime

    def visible_to(self, member_id: UUID) -> bool:
        return not self.private or member_id in (self.to_member_id, self.member_id)


class TaskRow(BaseModel):
    id: UUID
    room_id: UUID
    member_id: UUID
    kind: TaskKind
    description: str
    message_id: UUID | None
    created_at: datetime


class TaskEventRow(BaseModel):
    task_id: UUID
    event: Literal["completed", "reopened"]
    evidence: str
    created_at: datetime


class OptionRow(BaseModel):
    id: UUID
    position: int
    text: str


class OptionSetRow(BaseModel):
    id: UUID
    room_id: UUID
    member_id: UUID | None  # None: offered to everyone
    prompt: str
    message_id: UUID | None
    created_at: datetime
    options: list[OptionRow] = []


class PickRow(BaseModel):
    set_id: UUID
    option_id: UUID
    member_id: UUID
    created_at: datetime


def code_key(code: str) -> str:
    return code.strip().lower()


def name_key(name: str) -> str:
    return " ".join(name.split()).lower()


def done_task_ids(events: list[TaskEventRow]) -> set[UUID]:
    """A task is done when its LATEST event is 'completed' (events arrive in
    creation order)."""
    latest: dict[UUID, str] = {}
    for e in events:
        latest[e.task_id] = e.event
    return {task_id for task_id, event in latest.items() if event == "completed"}


def open_option_sets(
    member_id: UUID, sets: list[OptionSetRow], picks: list[PickRow]
) -> list[OptionSetRow]:
    """The option sets `member_id` can still click: for each audience (just
    them, or everyone) only the newest set counts, and only if they haven't
    already picked from it. Newest first."""
    picked = {p.set_id for p in picks if p.member_id == member_id}
    newest: dict[UUID | None, OptionSetRow] = {}
    for s in sets:  # creation order
        if s.member_id in (None, member_id):
            newest[s.member_id] = s
    live = [s for s in newest.values() if s.id not in picked]
    return sorted(live, key=lambda s: s.created_at, reverse=True)


class RoomStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------ rooms

    async def add_room(
        self,
        *,
        code: str,
        title: str,
        source_kind: str,
        query: str,
        outline: list[dict],
        created_by: str,
        resource_url: str | None = None,
        resource_filename: str | None = None,
        resource_excerpt: str | None = None,
    ) -> RoomRow:
        room_id = uuid4()
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO rooms (id, code, code_key, title, source_kind, query, resource_url, "
                    "resource_filename, resource_excerpt, outline, created_by) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) RETURNING *",
                    room_id, code.strip(), code_key(code), title, source_kind, query, resource_url,
                    resource_filename, resource_excerpt, to_jsonable(outline), created_by,
                )
        except asyncpg.UniqueViolationError:
            raise RoomCodeTaken(code) from None
        return _room(row)

    async def get_room(self, room_id: UUID) -> RoomRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM rooms WHERE id = $1", room_id)
        return None if row is None else _room(row)

    async def get_room_by_code(self, code: str) -> RoomRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM rooms WHERE code_key = $1", code_key(code))
        return None if row is None else _room(row)

    # ---------------------------------------------------------- members

    async def get_or_add_member(self, room_id: UUID, name: str) -> tuple[MemberRow, bool]:
        """The member called `name` in this room, created if new. Returns
        (member, created). Names are matched case-insensitively."""
        clean = " ".join(name.split())
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO room_members (id, room_id, name, name_key) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (room_id, name_key) DO NOTHING RETURNING *",
                uuid4(), room_id, clean, name_key(clean),
            )
            if row is not None:
                return MemberRow(**dict(row)), True
            row = await conn.fetchrow(
                "SELECT * FROM room_members WHERE room_id = $1 AND name_key = $2",
                room_id, name_key(clean),
            )
        return MemberRow(**dict(row)), False

    async def get_member(self, member_id: UUID) -> MemberRow | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM room_members WHERE id = $1", member_id)
        return None if row is None else MemberRow(**dict(row))

    async def list_members(self, room_id: UUID) -> list[MemberRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM room_members WHERE room_id = $1 ORDER BY joined_at, name_key", room_id
            )
        return [MemberRow(**dict(r)) for r in rows]

    # --------------------------------------------------------- messages

    async def add_message(
        self,
        room_id: UUID,
        *,
        sender: Sender,
        kind: MessageKind,
        text: str,
        member_id: UUID | None = None,
        to_member_id: UUID | None = None,
        private: bool = False,
        meta: dict | None = None,
    ) -> MessageRow:
        """Appends the room's next message. The per-room advisory lock makes
        `seq` gapless and unique even with several writers at once."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))", str(room_id)
            )
            row = await conn.fetchrow(
                "INSERT INTO room_messages (id, room_id, seq, sender, member_id, kind, text, "
                "to_member_id, private, meta) VALUES ($1, $2, "
                "(SELECT COALESCE(MAX(seq), 0) + 1 FROM room_messages WHERE room_id = $2), "
                "$3, $4, $5, $6, $7, $8, $9) RETURNING *",
                uuid4(), room_id, sender, member_id, kind, text, to_member_id, private,
                to_jsonable(meta or {}),
            )
        return MessageRow(**dict(row))

    async def list_messages(self, room_id: UUID, *, limit: int | None = None) -> list[MessageRow]:
        """Every message, oldest first (or only the newest `limit`)."""
        async with self._pool.acquire() as conn:
            if limit is None:
                rows = await conn.fetch(
                    "SELECT * FROM room_messages WHERE room_id = $1 ORDER BY seq", room_id
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM (SELECT * FROM room_messages WHERE room_id = $1 "
                    "ORDER BY seq DESC LIMIT $2) newest ORDER BY seq",
                    room_id, limit,
                )
        return [MessageRow(**dict(r)) for r in rows]

    async def list_visible_messages(
        self, room_id: UUID, member_id: UUID, *, limit: int = 300
    ) -> list[MessageRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM (SELECT * FROM room_messages WHERE room_id = $1 AND "
                "(NOT private OR to_member_id = $2 OR member_id = $2) "
                "ORDER BY seq DESC LIMIT $3) newest ORDER BY seq",
                room_id, member_id, limit,
            )
        return [MessageRow(**dict(r)) for r in rows]

    async def count_visible_after(self, room_id: UUID, member_id: UUID, after_seq: int) -> int:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM room_messages WHERE room_id = $1 AND seq > $3 AND "
                "(member_id IS DISTINCT FROM $2) AND "
                "(NOT private OR to_member_id = $2)",
                room_id, member_id, after_seq,
            )

    # ------------------------------------------------------------ tasks

    async def add_task(
        self, room_id: UUID, member_id: UUID, kind: str, description: str, message_id: UUID | None
    ) -> TaskRow:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO room_tasks (id, room_id, member_id, kind, description, message_id) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                uuid4(), room_id, member_id, kind, description, message_id,
            )
        return TaskRow(**dict(row))

    async def list_tasks(self, room_id: UUID) -> list[TaskRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM room_tasks WHERE room_id = $1 ORDER BY created_at, id", room_id
            )
        return [TaskRow(**dict(r)) for r in rows]

    async def add_task_event(
        self, task_id: UUID, event: str, evidence: str, message_id: UUID | None
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO room_task_events (id, task_id, event, evidence, message_id) "
                "VALUES ($1, $2, $3, $4, $5)",
                uuid4(), task_id, event, evidence, message_id,
            )

    async def list_task_events(self, room_id: UUID) -> list[TaskEventRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT e.task_id, e.event, e.evidence, e.created_at FROM room_task_events e "
                "JOIN room_tasks t ON t.id = e.task_id WHERE t.room_id = $1 "
                "ORDER BY e.created_at, e.id",
                room_id,
            )
        return [TaskEventRow(**dict(r)) for r in rows]

    # ---------------------------------------------------------- options

    async def add_option_set(
        self,
        set_id: UUID,
        room_id: UUID,
        member_id: UUID | None,
        prompt: str,
        options: list[str],
        message_id: UUID | None,
    ) -> OptionSetRow:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO room_option_sets (id, room_id, member_id, prompt, message_id) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                set_id, room_id, member_id, prompt, message_id,
            )
            out = OptionSetRow(**dict(row))
            for position, text in enumerate(options):
                option_id = uuid4()
                await conn.execute(
                    "INSERT INTO room_options (id, set_id, position, text) VALUES ($1, $2, $3, $4)",
                    option_id, set_id, position, text,
                )
                out.options.append(OptionRow(id=option_id, position=position, text=text))
        return out

    async def list_option_sets(self, room_id: UUID) -> list[OptionSetRow]:
        async with self._pool.acquire() as conn:
            set_rows = await conn.fetch(
                "SELECT * FROM room_option_sets WHERE room_id = $1 ORDER BY created_at, id", room_id
            )
            option_rows = await conn.fetch(
                "SELECT o.* FROM room_options o JOIN room_option_sets s ON s.id = o.set_id "
                "WHERE s.room_id = $1 ORDER BY o.position",
                room_id,
            )
        by_set: dict[UUID, list[OptionRow]] = {}
        for r in option_rows:
            by_set.setdefault(r["set_id"], []).append(
                OptionRow(id=r["id"], position=r["position"], text=r["text"])
            )
        return [OptionSetRow(**dict(r), options=by_set.get(r["id"], [])) for r in set_rows]

    async def add_pick(
        self, set_id: UUID, option_id: UUID, member_id: UUID, message_id: UUID | None
    ) -> bool:
        """False if this member already picked from this set."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO room_option_picks (id, set_id, option_id, member_id, message_id) "
                "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (set_id, member_id) DO NOTHING RETURNING id",
                uuid4(), set_id, option_id, member_id, message_id,
            )
        return row is not None

    async def list_picks(self, room_id: UUID) -> list[PickRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT p.set_id, p.option_id, p.member_id, p.created_at FROM room_option_picks p "
                "JOIN room_option_sets s ON s.id = p.set_id WHERE s.room_id = $1 "
                "ORDER BY p.created_at, p.id",
                room_id,
            )
        return [PickRow(**dict(r)) for r in rows]

    # ------------------------------------------------------------ audit

    async def record_node_call(
        self,
        *,
        room_id: UUID | None,
        seq: int,
        node_name: str,
        input_json: dict,
        output_json: object,
        error: str | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO room_node_calls (id, room_id, seq, node_name, input_json, output_json, error) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                uuid4(), room_id, seq, node_name, to_jsonable(input_json),
                None if output_json is None else to_jsonable(output_json), error,
            )

    async def list_node_calls(self, room_id: UUID) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM room_node_calls WHERE room_id = $1 ORDER BY created_at, id", room_id
            )
        return [dict(r) for r in rows]


def _room(row: asyncpg.Record) -> RoomRow:
    return RoomRow(**dict(row))
