"""Web-search client — a third provider boundary alongside
`probe.llm.LLMClient` (text) and `probe.embeddings.EmbeddingClient`
(vectors), this one for evidence from the live web.

Same discipline as the other two, deliberately: a `Protocol` the
callers depend on, a deterministic `Stub*` implementation so the whole
test suite runs with no API key and no cost, one real provider
implementation behind it, and a `build_*` factory. Nothing in this
module knows what the evidence is *for* — see grounding.py for that.

The provider is Parallel Web Systems' Search API (POST
https://api.parallel.ai/v1/search, `x-api-key` auth). It is built to
return ranked URLs with pre-compressed, token-dense excerpts in one
synchronous round-trip, which is the whole reason it fits here: the
one caller (grounding.py) runs inside a live tutoring turn and needs
an excerpt it can drop straight into a prompt, not a page it would
have to fetch and summarize itself with a second model call.

Deliberately NOT wrapped: Parallel's Task, FindAll, or Monitor APIs.
Nothing in this codebase has a genuine use for continuous topic
monitoring or multi-step entity discovery, and adding them because
the provider offers them would be capability-first thinking.

No retry loop, unlike `GeminiLLMClient`. This client's single caller
is latency-critical and strictly optional — a failed search degrades
to an ungrounded answer (logged, never silent), which is a better
outcome than making a student wait through a backoff sequence for
context the turn does not require. `timeout_seconds` is the hard
ceiling on how long a turn can ever block on this.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.parallel.ai/v1/search"

# Parallel's latency presets: advanced ~3s, fast ~700ms, turbo ~200ms.
# `fast` is the default here because the caller runs inside a live turn
# the student is waiting on — the marginal excerpt quality `advanced`
# buys is not worth ~2.3s of added latency in a tutoring exchange.
DEFAULT_MODE = "fast"

# Hard ceiling on how long one turn may block on a search. Chosen to sit
# just above `fast`'s documented sub-5s worst case so a normally-slow
# response still lands, while a genuinely hung request cannot hold a
# turn open.
DEFAULT_TIMEOUT_SECONDS = 5.0


class SearchResult(BaseModel):
    """One ranked result. `excerpts` are Parallel's own compressed,
    markdown-formatted spans — already the "relevant part", so no
    additional extraction step is needed before prompting with one."""

    url: str
    title: str | None = None
    publish_date: str | None = None
    excerpts: list[str] = Field(default_factory=list)

    @property
    def top_excerpt(self) -> str | None:
        return self.excerpts[0] if self.excerpts else None


class WebSearchError(Exception):
    """Any failure to obtain search results — transport, non-2xx, or an
    unparseable body. One exception type so the caller has a single name
    to catch, same reasoning as `probe.llm.LLMTransportError`."""


class WebSearchClient(Protocol):
    """Async web-search interface. Callers depend on this, not on
    Parallel specifically — same split as LLMClient/GeminiLLMClient."""

    async def search(
        self,
        objective: str,
        queries: list[str],
        *,
        max_results: int = 3,
        max_chars_per_result: int | None = None,
    ) -> list[SearchResult]: ...


class StubWebSearchClient:
    """Deterministic stub — no API key, no network, no cost.

    `canned` maps an objective prefix -> the results to return, longest
    matching prefix wins (same convention as `StubLLMClient.canned`,
    keyed on the objective since that is this API's equivalent of a
    prompt). Anything unmatched returns `default`.

    Setting `error` makes every call raise it instead, so the
    fall-back-to-ungrounded path is directly testable without patching
    anything.

    Every call is appended to `self.calls` so tests can assert on what
    was actually searched for — and, just as importantly, assert that
    nothing was searched for on a turn that should not have fired.
    """

    def __init__(
        self,
        canned: dict[str, list[SearchResult]] | None = None,
        default: list[SearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.canned = canned or {}
        self.default = default if default is not None else []
        self.error = error
        self.calls: list[dict] = []

    async def search(
        self,
        objective: str,
        queries: list[str],
        *,
        max_results: int = 3,
        max_chars_per_result: int | None = None,
    ) -> list[SearchResult]:
        self.calls.append(
            {
                "objective": objective,
                "queries": list(queries),
                "max_results": max_results,
                "max_chars_per_result": max_chars_per_result,
            }
        )
        if self.error is not None:
            raise self.error
        for prefix in sorted(self.canned, key=len, reverse=True):
            if objective.startswith(prefix):
                return self.canned[prefix][:max_results]
        return self.default[:max_results]


class ParallelSearchClient:
    """Real WebSearchClient backed by Parallel's Search API.

    Owns its own `httpx.AsyncClient` so the connection is reused across
    turns rather than re-established per search. `aclose()` is provided
    for symmetry but is deliberately not required: the client holds no
    server-side state, and the one long-lived caller (a `SessionLoop`)
    outlives every search it makes.
    """

    def __init__(
        self,
        api_key: str,
        *,
        mode: str = DEFAULT_MODE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._mode = mode
        self._timeout = timeout_seconds
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def search(
        self,
        objective: str,
        queries: list[str],
        *,
        max_results: int = 3,
        max_chars_per_result: int | None = None,
    ) -> list[SearchResult]:
        advanced: dict = {"max_results": max_results}
        if max_chars_per_result is not None:
            advanced["excerpt_settings"] = {
                "max_chars_per_result": max_chars_per_result
            }
        payload = {
            "objective": objective,
            "search_queries": queries,
            "mode": self._mode,
            "advanced_settings": advanced,
        }
        try:
            response = await self._client.post(
                _SEARCH_URL,
                headers={
                    "x-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise WebSearchError(
                f"Parallel search returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WebSearchError(f"Parallel search failed: {exc}") from exc

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise WebSearchError(
                "Parallel search response had no 'results' list"
            )
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            excerpts = item.get("excerpts")
            results.append(
                SearchResult(
                    url=str(url),
                    title=item.get("title"),
                    publish_date=item.get("publish_date"),
                    excerpts=[
                        str(e) for e in excerpts if e
                    ] if isinstance(excerpts, list) else [],
                )
            )
        return results

    async def aclose(self) -> None:
        await self._client.aclose()


def build_web_search_client(
    api_key: str,
    *,
    mode: str = DEFAULT_MODE,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> WebSearchClient:
    """Mirrors `embeddings.build_embedding_client`'s construction
    pattern — one client, no pooling, since at most one search happens
    per turn and they never fan out concurrently."""
    return ParallelSearchClient(
        api_key, mode=mode, timeout_seconds=timeout_seconds
    )
