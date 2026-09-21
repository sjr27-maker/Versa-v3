"""`SessionLoop.handle_turn(on_delta=..., defer_tail=...)` and the per-turn
embedding cache -- built through `build_session_loop`, the same assembly the
server will use, so the streaming / deferred path is tested as it will run.

Two properties matter more than the mechanics:
  * streaming and deferral change WHEN things happen, never WHAT is
    persisted (the parity test);
  * a client that disconnects, or a slow background write, can never turn a
    good answer into a failed or half-recorded turn.
"""

import asyncio
import json
import time

import pytest

from versa.embeddings import TASK_QUERY
from versa.llm import ModelTierClients, StubLLMClient
from versa.session_builder import build_session_loop

_NOT_AMBIGUOUS = json.dumps({"needs_branches": False, "branches": []})
_FACT = json.dumps(
    {"situation": "asked a direct question", "resolution": "answered it directly"}
)
_ANSWER = "the direct answer to your question"


def _stub() -> StubLLMClient:
    return StubLLMClient(
        canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT}
    )


def _loop(pool, llm, embedding_client):
    return build_session_loop(
        pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client
    )


class _GatedLLM:
    """A StubLLMClient whose WRITE:FACT completion is held until released --
    stands in for a slow background write."""

    def __init__(self, inner: StubLLMClient) -> None:
        self._inner = inner
        self.gate = asyncio.Event()
        self.write_started = asyncio.Event()

    async def complete(self, prompt: str) -> str:
        if prompt.startswith("WRITE:FACT"):
            self.write_started.set()
            await self.gate.wait()
        return await self._inner.complete(prompt)

    async def stream(self, prompt: str):
        async for piece in self._inner.stream(prompt):
            yield piece


class _CountingEmbeddings:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str | None]] = []

    async def embed(self, text: str, *, task_type: str | None = None):
        self.calls.append((text, task_type))
        return await self._inner.embed(text, task_type=task_type)


@pytest.mark.asyncio(loop_scope="session")
async def test_on_delta_streams_the_answer_and_matches_the_returned_message(
    transcript, node_calls, clean_pool, learner_id, embedding_client,
):
    loop = _loop(clean_pool, _stub(), embedding_client)
    session_id = await transcript.create_session(learner_id)
    pieces: list[str] = []

    async def sink(piece: str) -> None:
        pieces.append(piece)

    message = await loop.handle_turn(session_id, 0, "what is a derivative?", on_delta=sink)

    assert message == _ANSWER
    assert len(pieces) > 1, "the answer should arrive in several pieces, not one blob"
    assert "".join(pieces) == message
    call = await node_calls.get_call_for_turn(session_id, 0, "FinalAnswer")
    assert call.output_json == _ANSWER
    # A callback must never leak into the audit trail (invariant 2).
    assert not any(callable(v) for v in call.input_json.values())


@pytest.mark.asyncio(loop_scope="session")
async def test_no_sink_means_no_streaming_and_the_same_message(
    transcript, clean_pool, learner_id, embedding_client,
):
    loop = _loop(clean_pool, _stub(), embedding_client)
    session_id = await transcript.create_session(learner_id)
    assert await loop.handle_turn(session_id, 0, "what is a derivative?") == _ANSWER


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failing_sink_does_not_fail_the_turn(
    transcript, clean_pool, learner_id, embedding_client, learner_fact_store,
):
    loop = _loop(clean_pool, _stub(), embedding_client)
    session_id = await transcript.create_session(learner_id)
    seen: list[str] = []

    async def flaky_sink(piece: str) -> None:
        seen.append(piece)
        raise ConnectionError("client went away")

    message = await loop.handle_turn(session_id, 0, "what is a derivative?", on_delta=flaky_sink)

    assert message == _ANSWER, "the answer must still be produced in full"
    assert len(seen) == 1, "a sink that raised must not be called again"
    assert len(await learner_fact_store.list_by_learner(learner_id)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_defer_tail_returns_before_the_background_writes_finish(
    transcript, clean_pool, learner_id, embedding_client, learner_fact_store,
    diagnostics_store,
):
    gated = _GatedLLM(_stub())
    loop = _loop(clean_pool, gated, embedding_client)
    session_id = await transcript.create_session(learner_id)

    # wait_for: if deferral were broken this would block on the gated write
    # forever -- fail fast and legibly instead of hanging the suite.
    message = await asyncio.wait_for(
        loop.handle_turn(session_id, 0, "what is a derivative?", defer_tail=True), timeout=10
    )

    assert message == _ANSWER
    await asyncio.wait_for(gated.write_started.wait(), timeout=5)
    # The answer is out, but the memory write is still in flight...
    assert await learner_fact_store.list_by_learner(learner_id) == []
    assert await diagnostics_store.list_for_session(session_id) == []

    hold_ms = 800
    await asyncio.sleep(hold_ms / 1000)
    gated.gate.set()
    await loop.wait_for_background_tasks()

    # ...and lands afterwards: the fact, and exactly one diagnostics row.
    assert len(await learner_fact_store.list_by_learner(learner_id)) == 1
    rows = await diagnostics_store.list_for_session(session_id)
    assert len(rows) == 1
    assert rows[0].node_call_counts.get("WriteLearnerFact") == 2  # LLM + embedding
    # duration_ms is what the user waited for, not the 800 ms background hold.
    assert rows[0].duration_ms < hold_ms * 0.75


@pytest.mark.asyncio(loop_scope="session")
async def test_the_next_turn_of_a_session_waits_for_the_previous_tail(
    transcript, clean_pool, learner_id, embedding_client, learner_fact_store,
):
    gated = _GatedLLM(_stub())
    loop = _loop(clean_pool, gated, embedding_client)
    session_id = await transcript.create_session(learner_id)
    await asyncio.wait_for(
        loop.handle_turn(session_id, 0, "what is a derivative?", defer_tail=True), timeout=10
    )
    await asyncio.wait_for(gated.write_started.wait(), timeout=5)

    second = asyncio.create_task(
        loop.handle_turn(session_id, 1, "and an integral?", defer_tail=True)
    )
    await asyncio.sleep(0.3)
    assert not second.done(), "turn 2 must not start while turn 1's tail is still writing"

    gated.gate.set()
    assert await asyncio.wait_for(second, timeout=10) == _ANSWER
    await loop.wait_for_background_tasks()
    assert len(await learner_fact_store.list_by_learner(learner_id)) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_deferring_the_tail_persists_exactly_what_running_it_inline_does(
    transcript, clean_pool, learner_store, embedding_client, learner_fact_store,
    diagnostics_store,
):
    async def run(defer: bool):
        learner = await learner_store.create(label=f"parity-{defer}")
        loop = _loop(clean_pool, _stub(), embedding_client)
        session_id = await transcript.create_session(learner.id)
        await loop.handle_turn(
            session_id, 0, "what is a derivative?", defer_tail=defer
        )
        await loop.wait_for_background_tasks()
        facts = await learner_fact_store.list_by_learner(learner.id)
        diag = (await diagnostics_store.list_for_session(session_id))[0]
        interactions = await clean_pool.fetchval(
            "SELECT count(*) FROM interactions WHERE learner_id = $1", learner.id
        )
        node_names = {
            r["node_name"]
            for r in await clean_pool.fetch(
                "SELECT node_name FROM node_calls WHERE session_id = $1", session_id
            )
        }
        return {
            "facts": [(f.fact_type, f.situation, f.resolution) for f in facts],
            "counts": diag.node_call_counts,
            "total": diag.total_call_count,
            "interactions": interactions,
            "node_names": node_names,
        }

    inline = await run(False)
    deferred = await run(True)
    assert inline == deferred
    assert inline["facts"] and inline["interactions"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_one_turn_embeds_the_students_message_once(
    transcript, clean_pool, learner_id, embedding_client,
):
    counting = _CountingEmbeddings(embedding_client)
    loop = _loop(clean_pool, _stub(), counting)
    session_id = await transcript.create_session(learner_id)
    message = "what is a derivative, exactly?"

    await loop.handle_turn(session_id, 0, message, defer_tail=True)
    await loop.wait_for_background_tasks()

    same_query = [c for c in counting.calls if c == (message, TASK_QUERY)]
    assert len(same_query) == 1, (
        f"memory search, history block and interaction record must share one "
        f"embedding, got {len(same_query)}: {counting.calls}"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_a_turn_that_raises_never_schedules_its_tail(
    transcript, clean_pool, learner_id, embedding_client, learner_fact_store,
):
    class _Boom(StubLLMClient):
        async def complete(self, prompt: str) -> str:
            if prompt.startswith("ASSESS:BRANCH"):
                return _NOT_AMBIGUOUS
            return await super().complete(prompt)

    loop = _loop(clean_pool, _Boom(canned={"WRITE:FACT": _FACT}), embedding_client)

    async def failing_record(*a, **k):
        raise RuntimeError("db down")

    loop._transcript.record_turn = failing_record  # the turn dies before any tail exists
    session_id = await transcript.create_session(learner_id)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        await loop.handle_turn(session_id, 0, "hello there", defer_tail=True)
    assert time.monotonic() - started < 5
    assert loop._session_tails == {}
    assert await learner_fact_store.list_by_learner(learner_id) == []
