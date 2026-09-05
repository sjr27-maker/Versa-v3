"""Time-sensitive grounding for `FinalAnswer` — the one place this
system is allowed to check the live web before answering a student.

THE BLIND SPOT THIS ADDRESSES

`FinalAnswer` (disambiguate.py) answers a question about a fast-moving
topic with exactly the same confidence it uses for calculus, and
nothing surfaces the difference. This module adds a signal, not a
layer: a message that plausibly concerns something time-sensitive gets
one web search whose top excerpt is threaded into the answer's prompt
and cited if used. Every other message is untouched.

WHY A LOCAL HEURISTIC AND NOT AN LLM PRE-CHECK

The originating spec allowed either, but its hard constraint decides
it: "Every other turn must be unchanged: zero extra calls, zero added
latency." A fast-tier LLM classifier would add one API call and its
round-trip to *every* turn, including the overwhelming majority that
are ordinary and stable — precisely the turns the constraint protects.
`detect_time_sensitivity` is therefore a pure lexical match over the
student's message: no network, no model, sub-millisecond, and — the
part that matters more than the speed — fully inspectable. Its exact
firing behaviour is readable from this file rather than being an
opaque judgment re-made per turn.

The honest cost of that choice is precision. A lexical rule cannot
tell "what's the current best model" from "what is current in a
circuit". Rather than tune that away invisibly, `matched_marker`
records *which* phrase fired on every turn, hit or miss, into
`node_calls` — so a miscalibration shows up as data in the audit trail
instead of as an unexplained search. If it fires on ordinary calculus
questions, that is a finding to report, not a threshold to quietly
nudge.

WHAT THIS IS NOT

Not a planner, and not an evidence-acquisition layer for one. There is
no model of competing hypotheses deciding what would be most
informative to search for; there is no knowledge store the results are
written into. One boolean decides whether to search, the student's own
message becomes the query, and the excerpt lives exactly as long as
the prompt it is pasted into. The previous architecture in this
project was deleted for failing to earn 20-40x the baseline's cost —
this addition is deliberately shaped so that the same question ("did
it earn it?") stays cheap to ask and cheap to answer no to.

Never blocks a turn: a slow or failed search degrades to the current
ungrounded behaviour, logged explicitly and recorded on
`GroundingResult.error`, never silently.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel

from probe.models import GroundingEvidence, GroundingResult
from probe.websearch import WebSearchClient

logger = logging.getLogger(__name__)


class GroundingConfig(BaseModel):
    """Independently toggleable, so the grounded/ungrounded comparison
    runs without a code change (`GroundingConfig(enabled=False)`).

    `enabled` gates the whole feature. Note that a `SessionLoop` also
    needs a `WebSearchClient` for grounding to be live at all — same
    two-part arrangement as the memory layer's `_memory_enabled`, so a
    deployment with no `PARALLEL_API_KEY` behaves exactly as it did
    before this module existed, with no config change required.
    """

    enabled: bool = True
    max_results: int = 3
    # One excerpt from each of the top `max_sources` results — source
    # diversity, not depth. See `_collect_evidence` for why this is
    # per-result rather than "the top N excerpts overall".
    max_sources: int = 3
    max_excerpt_chars: int = 1200
    # Hard ceiling on the whole grounding block, so a provider that
    # starts returning much longer excerpts can never quietly crowd out
    # the conversation history in FinalAnswer's prompt. Observed
    # excerpts run ~1000 chars, so 3 sources normally lands well under
    # this; it is a backstop, not the expected size.
    max_total_excerpt_chars: int = 3600


# --------------------------------------------------------------------
# The heuristic
# --------------------------------------------------------------------

# Phrases that assert recency directly — the message is asking about a
# state of the world that has a "now". Any single match fires.
#
# Deliberately excluded, despite being tempting: bare "current" (an
# electrical-current question is not a web-search question), bare
# "cost" (a cost function is not a price), and bare "score"/"api".
# Each would trade a real gain in recall for a false positive on an
# ordinary, stable tutoring question, which is the failure mode this
# check is least allowed to have.
_RECENCY_MARKERS = (
    "latest",
    "most recent",
    "newest",
    "up to date",
    "up-to-date",
    "currently",
    "right now",
    "as of today",
    "as of now",
    "nowadays",
    "these days",
    "today",
    "this week",
    "this month",
    "this year",
    "this quarter",
    "so far this year",
    "recently",
    "recent",
    "just released",
    "just announced",
    "current version",
    "current price",
    "current state",
    "state of the art",
)

# Topics whose answer changes underneath you regardless of how the
# question is phrased.
_VOLATILE_MARKERS = (
    "release date",
    "released",
    "launched",
    "deprecated",
    "discontinued",
    "pricing",
    "stock price",
    "exchange rate",
    "who won",
    "election",
    "weather forecast",
    "news",
)

_ALL_MARKERS = _RECENCY_MARKERS + _VOLATILE_MARKERS

# Longest-first alternation so the reported `matched_marker` is the most
# specific phrase present ("current version", not "current"-adjacent
# noise), matching the longest-prefix convention used by
# StubLLMClient.canned and _SCHEMA_BY_PREFIX.
_MARKER_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in sorted(_ALL_MARKERS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")


class TimeSensitivitySignal(BaseModel):
    """`detect_time_sensitivity`'s result. `matched_marker` is the
    literal phrase (or year) that fired, kept so a false positive is
    diagnosable from the audit trail rather than only reproducible by
    re-running the message."""

    is_time_sensitive: bool = False
    matched_marker: str | None = None


def detect_time_sensitivity(
    message: str, *, now: datetime | None = None
) -> TimeSensitivitySignal:
    """Pure, local, no I/O. A year at or after *last* year counts as a
    recency signal (asking about 2026 in 2026 is asking about now;
    asking about 1789 is history), which is why `now` is injectable —
    a test asserting on a specific year must not silently change
    meaning as the calendar moves.
    """
    if not message:
        return TimeSensitivitySignal()

    match = _MARKER_PATTERN.search(message)
    if match is not None:
        return TimeSensitivitySignal(
            is_time_sensitive=True, matched_marker=match.group(1).lower()
        )

    current_year = (now or datetime.now(timezone.utc)).year
    for year_match in _YEAR_PATTERN.finditer(message):
        if int(year_match.group(1)) >= current_year - 1:
            return TimeSensitivitySignal(
                is_time_sensitive=True, matched_marker=year_match.group(1)
            )
    return TimeSensitivitySignal()


# --------------------------------------------------------------------
# The node
# --------------------------------------------------------------------

_OBJECTIVE_TEMPLATE = (
    "A student asked a tutor this question, which appears to concern "
    "something that changes over time. Find the current, factual "
    "answer, preferring recent and authoritative sources: {message}"
)

# Parallel's own guidance is 2-3 queries of 3-6 words. One query built
# from the student's own words is used instead, deliberately: choosing
# *what else* to search for would be the first step toward a planner
# deciding which evidence best discriminates between competing
# hypotheses — the exact architecture this project deleted.
_MAX_QUERY_WORDS = 12


def _search_query(message: str) -> str:
    return " ".join(message.split()[:_MAX_QUERY_WORDS])


class GroundTimeSensitive:
    """Runs before `FinalAnswer` on every turn (when enabled), and in
    the overwhelming majority of them does nothing but record that it
    found nothing — see this module's docstring on why the negative
    case is written down too.

    A node in the full sense of CLAUDE.md invariant 2: routed through
    `SessionLoop._call_node`, so its input and output land in
    `node_calls` like every other node's. `last_call_count` counts
    *search* calls (0 or 1), never LLM calls, and is 0 on every turn
    that does not fire.

    This node has no fallback path of its own because it *is* the
    fallback: every failure mode returns a `GroundingResult` that
    leaves `FinalAnswer` exactly as ungrounded as it is today.
    """

    def __init__(
        self,
        search_client: WebSearchClient,
        config: GroundingConfig | None = None,
    ) -> None:
        self._search = search_client
        self._config = config or GroundingConfig()
        self.last_call_count: int = 0

    async def run(self, student_message: str) -> GroundingResult:
        self.last_call_count = 0
        if not self._config.enabled:
            return GroundingResult()

        signal = detect_time_sensitivity(student_message)
        if not signal.is_time_sensitive:
            return GroundingResult()

        try:
            results = await self._search.search(
                _OBJECTIVE_TEMPLATE.format(message=student_message),
                [_search_query(student_message)],
                max_results=self._config.max_results,
                max_chars_per_result=self._config.max_excerpt_chars,
            )
            self.last_call_count = 1
        except Exception as exc:  # noqa: BLE001 - deliberate, see below
            # Deliberately broad, not just WebSearchError: this node
            # must never be the reason a turn fails. WebSearchError is
            # the expected shape, but a provider client raising
            # anything at all still has to degrade to an ungrounded
            # answer rather than propagate into the turn.
            self.last_call_count = 1
            logger.warning(
                "GroundTimeSensitive: search failed (marker=%r) — answering "
                "UNGROUNDED this turn: %s",
                signal.matched_marker,
                exc,
            )
            return GroundingResult(
                time_sensitive=True,
                matched_marker=signal.matched_marker,
                searched=True,
                error=str(exc),
            )

        evidence = _collect_evidence(results, self._config)
        if not evidence:
            logger.info(
                "GroundTimeSensitive: fired on marker=%r but no usable "
                "excerpt returned — answering UNGROUNDED this turn",
                signal.matched_marker,
            )
            return GroundingResult(
                time_sensitive=True,
                matched_marker=signal.matched_marker,
                searched=True,
                error="no usable excerpt in search results",
            )

        logger.info(
            "GroundTimeSensitive: fired on marker=%r — grounding answer in %s",
            signal.matched_marker,
            ", ".join(e.url for e in evidence),
        )
        return GroundingResult(
            time_sensitive=True,
            matched_marker=signal.matched_marker,
            searched=True,
            evidence=evidence,
        )


def _collect_evidence(results, config: GroundingConfig) -> list[GroundingEvidence]:
    """One excerpt from each of the top `max_sources` results that
    carried one, in the provider's own relevance order.

    ONE PER RESULT, not the top N excerpts overall. That distinction is
    the entire fix: the failure this replaced discarded results 2 and 3
    and kept only result[0], whose excerpt happened to describe Python's
    release *phases* rather than name a version. Pulling more excerpts
    out of that same result would not have helped — the answer was on
    the other two domains. Diversity of source is what was missing.

    This module still does not re-rank: the provider's ordering is
    preserved and passed through as-is. Deciding which of these
    excerpts is actually the best evidence is left to the model reading
    them, with the prompt telling it they may disagree. Scoring the
    evidence here would be the first step toward exactly the kind of
    knowledge-quality machinery this project deleted.
    """
    collected: list[GroundingEvidence] = []
    total_chars = 0
    for result in results:
        if len(collected) >= config.max_sources:
            break
        excerpt = result.top_excerpt
        if not excerpt or not excerpt.strip():
            continue
        text = excerpt.strip()[: config.max_excerpt_chars]
        remaining = config.max_total_excerpt_chars - total_chars
        if remaining <= 0:
            break
        text = text[:remaining]
        collected.append(
            GroundingEvidence(
                url=result.url,
                title=result.title,
                publish_date=result.publish_date,
                excerpt=text,
            )
        )
        total_chars += len(text)
    return collected
