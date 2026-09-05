"""GroundTimeSensitive (grounding.py) and the Parallel search client
boundary (websearch.py), against StubWebSearchClient — no key, no
network, no cost.

The failure paths matter most here: the node's entire contract is that
it can never be the reason a turn fails, and that it never degrades
silently.
"""

import json

import httpx
import pytest

from probe.grounding import GroundingConfig, GroundTimeSensitive
from probe.websearch import (
    ParallelSearchClient,
    SearchResult,
    StubWebSearchClient,
    WebSearchError,
)

_HIT = SearchResult(
    url="https://example.org/versions",
    title="Python releases",
    publish_date="2026-08-01",
    excerpts=["Python 3.14 is the current stable release."],
)


@pytest.mark.asyncio
async def test_does_not_search_on_a_stable_question():
    search = StubWebSearchClient(default=[_HIT])
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="what is a derivative?")

    assert search.calls == [], "an ordinary question must cost zero searches"
    assert result.time_sensitive is False
    assert result.searched is False
    assert result.evidence == []
    assert node.last_call_count == 0


@pytest.mark.asyncio
async def test_searches_and_returns_the_top_excerpt_on_a_hit():
    search = StubWebSearchClient(default=[_HIT])
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="what is the latest version of Python?")

    assert len(search.calls) == 1
    assert result.time_sensitive is True
    assert result.matched_marker == "latest"
    assert result.searched is True
    assert len(result.evidence) == 1
    assert result.evidence[0].url == "https://example.org/versions"
    assert result.evidence[0].excerpt == "Python 3.14 is the current stable release."
    assert result.evidence[0].publish_date == "2026-08-01"
    assert result.error is None
    assert node.last_call_count == 1


@pytest.mark.asyncio
async def test_the_student_message_becomes_the_query():
    search = StubWebSearchClient(default=[_HIT])
    node = GroundTimeSensitive(search)

    await node.run(student_message="what is the latest version of Python?")

    call = search.calls[0]
    assert "latest version of Python" in call["objective"]
    # One query, from the student's own words — not a set of
    # planner-chosen discriminating queries.
    assert len(call["queries"]) == 1


@pytest.mark.asyncio
async def test_a_failed_search_degrades_to_ungrounded_and_records_why():
    search = StubWebSearchClient(error=WebSearchError("boom"))
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="what is the latest version of Python?")

    assert result.evidence == [], "a failed search must not fabricate evidence"
    assert result.time_sensitive is True
    assert result.searched is True
    assert result.error == "boom", "the degradation must be on the record"


@pytest.mark.asyncio
async def test_an_unexpected_exception_also_degrades_rather_than_propagating():
    # Not a WebSearchError — the node must still swallow it.
    search = StubWebSearchClient(error=RuntimeError("provider client blew up"))
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="who is currently the CEO of OpenAI?")

    assert result.evidence == []
    assert "provider client blew up" in result.error


@pytest.mark.asyncio
async def test_results_with_no_excerpts_are_skipped_for_the_first_usable_one():
    search = StubWebSearchClient(
        default=[
            SearchResult(url="https://example.org/empty", excerpts=[]),
            SearchResult(url="https://example.org/blank", excerpts=["   "]),
            _HIT,
        ]
    )
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="what is the latest version of Python?")

    assert [e.url for e in result.evidence] == ["https://example.org/versions"]


@pytest.mark.asyncio
async def test_zero_usable_results_is_an_explicit_error_not_silent():
    search = StubWebSearchClient(default=[])
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="what is the latest version of Python?")

    assert result.evidence == []
    assert result.searched is True
    assert result.error == "no usable excerpt in search results"


@pytest.mark.asyncio
async def test_disabled_config_never_searches_even_on_a_firing_message():
    search = StubWebSearchClient(default=[_HIT])
    node = GroundTimeSensitive(search, GroundingConfig(enabled=False))

    result = await node.run(student_message="what is the latest version of Python?")

    assert search.calls == []
    assert result.time_sensitive is False
    assert result.evidence == []


@pytest.mark.asyncio
async def test_excerpt_is_truncated_to_the_configured_ceiling():
    long = SearchResult(url="https://example.org/x", excerpts=["y" * 5000])
    search = StubWebSearchClient(default=[long])
    node = GroundTimeSensitive(search, GroundingConfig(max_excerpt_chars=100))

    result = await node.run(student_message="what is the latest news")

    assert len(result.evidence[0].excerpt) == 100


@pytest.mark.asyncio
async def test_lower_ranked_results_are_not_discarded():
    """Regression test for the measured Q1 failure.

    Live run, 2026-09-05: for "latest stable version of Python", the
    top-ranked result was the authoritative domain but its excerpt was
    a glossary of release phases naming no version, while results 2 and
    3 both carried the answer. Keeping only result[0] produced a
    grounded answer LESS accurate than the ungrounded one, with a
    citation attached. All three must reach the prompt.
    """
    search = StubWebSearchClient(
        default=[
            SearchResult(
                url="https://devguide.python.org/versions/",
                excerpts=["bugfix: once a version has been fully released..."],
            ),
            SearchResult(
                url="https://example.org/blog",
                excerpts=["Python 3.14 is the latest stable release."],
            ),
            SearchResult(
                url="https://docs.python.org/3/whatsnew/3.14.html",
                excerpts=["What's New In Python 3.14"],
            ),
        ]
    )
    node = GroundTimeSensitive(search)

    result = await node.run(student_message="what is the latest version of Python?")

    assert len(result.evidence) == 3
    assert "3.14" in result.evidence[1].excerpt
    # Provider relevance order is preserved, not re-ranked here.
    assert result.evidence[0].url == "https://devguide.python.org/versions/"


@pytest.mark.asyncio
async def test_sources_are_capped_by_max_sources():
    search = StubWebSearchClient(
        default=[
            SearchResult(url=f"https://example.org/{i}", excerpts=[f"e{i}"])
            for i in range(6)
        ]
    )
    node = GroundTimeSensitive(search, GroundingConfig(max_sources=2))

    result = await node.run(student_message="what is the latest news")

    assert len(result.evidence) == 2


@pytest.mark.asyncio
async def test_the_total_excerpt_budget_is_a_hard_ceiling():
    """A provider that starts returning much longer excerpts must not
    be able to crowd out conversation history in FinalAnswer's prompt."""
    search = StubWebSearchClient(
        default=[
            SearchResult(url=f"https://example.org/{i}", excerpts=["z" * 1000])
            for i in range(3)
        ]
    )
    node = GroundTimeSensitive(
        search, GroundingConfig(max_excerpt_chars=1000, max_total_excerpt_chars=1500)
    )

    result = await node.run(student_message="what is the latest news")

    assert sum(len(e.excerpt) for e in result.evidence) == 1500


# ───────────────────── the real client's wire contract ─────────────────


def _transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_parallel_client_sends_the_documented_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("x-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "search_id": "s1",
                "results": [
                    {
                        "url": "https://example.org/a",
                        "title": "A",
                        "publish_date": "2026-01-01",
                        "excerpts": ["an excerpt"],
                    }
                ],
            },
        )

    client = ParallelSearchClient("test-key", client=_transport(handler))
    results = await client.search("find the thing", ["the thing"], max_results=2)

    assert seen["url"] == "https://api.parallel.ai/v1/search"
    assert seen["api_key"] == "test-key"
    assert seen["body"]["objective"] == "find the thing"
    assert seen["body"]["search_queries"] == ["the thing"]
    assert seen["body"]["advanced_settings"]["max_results"] == 2
    assert len(results) == 1
    assert results[0].url == "https://example.org/a"
    assert results[0].top_excerpt == "an excerpt"


@pytest.mark.asyncio
async def test_parallel_client_wraps_a_non_2xx_in_websearcherror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    client = ParallelSearchClient("bad-key", client=_transport(handler))
    with pytest.raises(WebSearchError, match="401"):
        await client.search("o", ["q"])


@pytest.mark.asyncio
async def test_parallel_client_wraps_a_transport_failure_in_websearcherror():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = ParallelSearchClient("k", client=_transport(handler))
    with pytest.raises(WebSearchError):
        await client.search("o", ["q"])


@pytest.mark.asyncio
async def test_parallel_client_rejects_a_body_with_no_results_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"search_id": "s1"})

    client = ParallelSearchClient("k", client=_transport(handler))
    with pytest.raises(WebSearchError, match="results"):
        await client.search("o", ["q"])
