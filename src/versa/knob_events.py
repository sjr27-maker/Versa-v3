"""The history of a session's length/depth/breadth sliders (migration 084).

`sessions` keeps only the current levels -- what the next answer is written
at. This keeps every settled move: where the learner put their own range,
from where, and how far into the session. It is Range-lens evidence for the
thinking style (docs/THINKING_STYLE.md) and learner-initiated, so clean.

Append-only (CLAUDE.md invariant 19): insert and read only.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa.session_knobs import SessionKnobs


class KnobEvent(BaseModel):
    id: UUID
    session_id: UUID
    turn_count: int
    before: SessionKnobs
    after: SessionKnobs
    created_at: datetime


class KnobEventStore:
    """Slider moves (migration 084). Append-only (CLAUDE.md invariant 19): no
    delete/remove/update method and no DELETE or UPDATE SQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self, *, session_id: UUID, turn_count: int, before: SessionKnobs, after: SessionKnobs,
    ) -> UUID | None:
        """One row for a move that changed at least one level; None (nothing
        written) when `after` equals `before` -- a no-op PATCH moved nothing."""
        if before == after:
            return None
        event_id = uuid4()
        await self._pool.execute(
            "INSERT INTO knob_events (id, session_id, turn_count, "
            "from_answer_length, from_depth, from_breadth, to_answer_length, to_depth, to_breadth) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            event_id, session_id, turn_count,
            before.answer_length, before.depth, before.breadth,
            after.answer_length, after.depth, after.breadth,
        )
        return event_id

    async def list_for_session(self, session_id: UUID) -> list[KnobEvent]:
        rows = await self._pool.fetch(
            "SELECT * FROM knob_events WHERE session_id = $1 ORDER BY created_at, id", session_id,
        )
        return [
            KnobEvent(
                id=r["id"], session_id=r["session_id"], turn_count=r["turn_count"],
                before=SessionKnobs(
                    answer_length=r["from_answer_length"], depth=r["from_depth"], breadth=r["from_breadth"],
                ),
                after=SessionKnobs(
                    answer_length=r["to_answer_length"], depth=r["to_depth"], breadth=r["to_breadth"],
                ),
                created_at=r["created_at"],
            )
            for r in rows
        ]
