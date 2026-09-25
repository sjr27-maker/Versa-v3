"""Live answer regeneration at new slider levels (migration 071).

The original answer is never overwritten: it stays in `node_calls` as that
turn's `FinalAnswer` output (version 0). Each regeneration runs through
`RegenerateAnswer` -- its own node name, so every regeneration is its own
`node_calls` row (CLAUDE.md invariant 2) while every existing reader of a
turn's `FinalAnswer` row keeps seeing the original -- and its text is
appended here as version 1, 2, ...

Append-only, like every other store: no UPDATE, no DELETE, no removal
methods."""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg

from versa.session_knobs import SessionKnobs


class RegenerateAnswer:
    """Re-runs `FinalAnswer` with the inputs a turn originally had, except
    the knob directive. A wrapper rather than a second `FinalAnswer`
    instance so the recorded node name tells a regeneration apart from the
    turn's original answer."""

    def __init__(self, final_answer) -> None:
        self._final_answer = final_answer
        self.last_call_count: int = 0

    async def run(self, **kwargs) -> str:
        text = await self._final_answer.run(**kwargs)
        self.last_call_count = self._final_answer.last_call_count
        return text


class AnswerVersionStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self, session_id: UUID, turn_index: int, text: str, knobs: SessionKnobs
    ) -> int:
        """Appends the next version for this turn and returns its number."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                INSERT INTO answer_versions
                    (id, session_id, turn_index, version, text, answer_length_level, depth_level)
                SELECT $1, $2, $3, COALESCE(MAX(version), 0) + 1, $4, $5, $6
                FROM answer_versions WHERE session_id = $2 AND turn_index = $3
                RETURNING version
                """,
                uuid4(),
                session_id,
                turn_index,
                text,
                knobs.answer_length,
                knobs.depth,
            )

    async def latest_by_turn(self, session_id: UUID) -> dict[int, tuple[int, str]]:
        """{turn_index: (version, text)} for every regenerated turn of a
        session -- the version a resumed chat shows."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (turn_index) turn_index, version, text
                FROM answer_versions WHERE session_id = $1
                ORDER BY turn_index, version DESC
                """,
                session_id,
            )
        return {r["turn_index"]: (r["version"], r["text"]) for r in rows}
