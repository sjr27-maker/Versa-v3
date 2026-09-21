"""The building blocks under streaming: LLM `stream()` (stub, Gemini, pool)
and the per-turn embedding cache. No database, no network."""

import asyncio

import pytest
from google.genai import errors

from versa.embeddings import StubEmbeddingClient, TurnCachedEmbeddings
from versa.llm import (
    GeminiLLMClient,
    LLMTransportError,
    StubLLMClient,
    _PooledGeminiLLMClient,
)


async def _collect(aiter):
    return [piece async for piece in aiter]


# ------------------------------------------------------------------ stub


@pytest.mark.asyncio
async def test_stub_stream_reassembles_to_the_completion_text():
    llm = StubLLMClient(canned={"FINAL:ANSWER": "the quick brown fox"})
    pieces = await _collect(llm.stream("FINAL:ANSWER x"))
    assert len(pieces) > 1
    assert "".join(pieces) == "the quick brown fox"
    assert llm.prompts == ["FINAL:ANSWER x"]


# ---------------------------------------------------------------- gemini


class _Chunk:
    def __init__(self, text):
        self.text = text


class _FakeStreamModels:
    """scripts: one list per call; an Exception entry is raised at that
    point in the stream, a string entry is yielded as a chunk."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = 0
        self.last_config = None

    async def generate_content_stream(self, model, contents, config):
        self.calls += 1
        self.last_config = config
        script = self._scripts.pop(0)

        async def gen():
            for item in script:
                if isinstance(item, Exception):
                    raise item
                yield _Chunk(item)

        return gen()


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, scripts):
        self.models = _FakeStreamModels(scripts)
        self.aio = _FakeAio(self.models)


def _client(scripts, **kw):
    fake = _FakeClient(scripts)
    return GeminiLLMClient(fake, "m", initial_delay=0, jitter=0, **kw), fake


def _rate_limited():
    return errors.ClientError(429, {"error": {"message": "rate limited"}})


@pytest.mark.asyncio
async def test_gemini_stream_yields_pieces_in_order_and_skips_empty_chunks():
    client, fake = _client([["Hel", "", "lo ", None, "world"]])
    assert await _collect(client.stream("FINAL:ANSWER x")) == ["Hel", "lo ", "world"]
    assert fake.models.calls == 1


@pytest.mark.asyncio
async def test_gemini_stream_retries_a_failure_before_any_output():
    client, fake = _client([[_rate_limited()], ["ok"]])
    assert "".join(await _collect(client.stream("FINAL:ANSWER x"))) == "ok"
    assert fake.models.calls == 2
    assert client.retry_count == 1


@pytest.mark.asyncio
async def test_gemini_stream_never_retries_after_output_reached_the_caller():
    client, fake = _client([["partial ", _rate_limited()], ["would-be retry"]])
    got = []
    with pytest.raises(LLMTransportError, match="mid-response"):
        async for piece in client.stream("FINAL:ANSWER x"):
            got.append(piece)
    assert got == ["partial "], "what was already streamed stays streamed"
    assert fake.models.calls == 1, "no retry once text has been delivered"


@pytest.mark.asyncio
async def test_gemini_stream_sends_the_thinking_config():
    client, fake = _client([["ok"]], thinking="minimal")
    await _collect(client.stream("FINAL:ANSWER x"))
    assert fake.models.last_config.thinking_config.thinking_level.value.lower() == "minimal"


@pytest.mark.asyncio
async def test_pooled_stream_round_robins_across_connections():
    a, b = _FakeClient([["a1"], ["a2"]]), _FakeClient([["b1"]])
    pooled = _PooledGeminiLLMClient([a, b], "m")
    out = [
        "".join(await _collect(pooled.stream("FINAL:ANSWER x"))) for _ in range(3)
    ]
    assert out == ["a1", "b1", "a2"]


# ------------------------------------------------------- embedding cache


class _CountingInner:
    def __init__(self, delay=0.0, fail_first=False):
        self.calls: list[tuple[str, str | None]] = []
        self._delay = delay
        self._fail_first = fail_first
        self._stub = StubEmbeddingClient()

    async def embed(self, text, *, task_type=None):
        self.calls.append((text, task_type))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._fail_first and len(self.calls) == 1:
            raise RuntimeError("boom")
        return await self._stub.embed(text, task_type=task_type)


@pytest.mark.asyncio
async def test_outside_a_turn_every_call_passes_through():
    inner = _CountingInner()
    cached = TurnCachedEmbeddings(inner)
    await cached.embed("hello", task_type="Q")
    await cached.embed("hello", task_type="Q")
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_inside_a_turn_identical_embeds_share_one_call():
    inner = _CountingInner()
    cached = TurnCachedEmbeddings(inner)
    token = cached.begin_turn()
    try:
        v1 = await cached.embed("hello", task_type="Q")
        v2 = await cached.embed("hello", task_type="Q")
        other_task = await cached.embed("hello", task_type="D")
        other_text = await cached.embed("bye", task_type="Q")
    finally:
        cached.end_turn(token)
    assert v1 == v2
    assert inner.calls == [("hello", "Q"), ("hello", "D"), ("bye", "Q")]
    assert other_task is not None and other_text is not None


@pytest.mark.asyncio
async def test_concurrent_identical_requests_share_one_in_flight_call():
    inner = _CountingInner(delay=0.05)
    cached = TurnCachedEmbeddings(inner)
    token = cached.begin_turn()
    try:
        results = await asyncio.gather(*(cached.embed("same", task_type="Q") for _ in range(4)))
    finally:
        cached.end_turn(token)
    assert len(inner.calls) == 1
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_a_failed_embed_is_evicted_so_the_next_request_retries():
    inner = _CountingInner(fail_first=True)
    cached = TurnCachedEmbeddings(inner)
    token = cached.begin_turn()
    try:
        with pytest.raises(RuntimeError):
            await cached.embed("x", task_type="Q")
        await asyncio.sleep(0)  # let the done-callback evict
        assert await cached.embed("x", task_type="Q") is not None
    finally:
        cached.end_turn(token)
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_two_turns_never_share_a_cache():
    inner = _CountingInner()
    cached = TurnCachedEmbeddings(inner)
    for _ in range(2):
        token = cached.begin_turn()
        try:
            await cached.embed("hello", task_type="Q")
        finally:
            cached.end_turn(token)
    assert len(inner.calls) == 2
