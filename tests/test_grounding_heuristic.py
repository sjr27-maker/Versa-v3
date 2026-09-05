"""The time-sensitivity heuristic (grounding.py) in isolation — no
loop, no store, no network.

The negative cases carry as much weight as the positive ones here. The
feature's whole cost/benefit rests on it NOT firing on ordinary
tutoring questions, so a regression that starts grounding calculus is
supposed to fail loudly in this file rather than show up as a bill.
"""

from datetime import datetime, timezone

import pytest

from probe.grounding import detect_time_sensitivity

# A deliberately fixed "now" so a test asserting on a specific year
# cannot change meaning as the calendar moves (see
# detect_time_sensitivity's `now` parameter).
_NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


# Ordinary, stable tutoring questions. None of these may fire.
STABLE_QUESTIONS = [
    "what is a derivative?",
    "explain the chain rule",
    "why does integration by parts work?",
    "can you sketch that?",
    "what is the difference between a limit and a derivative",
    "I don't understand eigenvalues",
    "prove that the square root of 2 is irrational",
    "how do I balance a redox equation",
    "what caused the fall of the Roman Empire",
    "walk me through a worked example",
    # The collocations the marker list deliberately excludes: bare
    # "current" (physics), bare "cost" (optimization), bare "score".
    "what is current in a circuit?",
    "how do I minimise the cost function",
    "how is a z-score calculated",
]

# Genuinely time-sensitive questions. All of these must fire.
TIME_SENSITIVE_QUESTIONS = [
    "what is the latest version of Python?",
    "who is currently the CEO of OpenAI?",
    "what is the most recent Mars mission?",
    "what happened in the news today",
    "what is the current price of bitcoin",
    "when is the release date for the next Elder Scrolls",
    "has that library been deprecated yet",
    "what is the state of the art in protein folding",
    "what did they announce recently",
]


@pytest.mark.parametrize("message", STABLE_QUESTIONS)
def test_stable_questions_do_not_fire(message):
    signal = detect_time_sensitivity(message, now=_NOW)
    assert not signal.is_time_sensitive, (
        f"heuristic fired on a stable question via {signal.matched_marker!r} "
        f"-- this is the miscalibration the feature is least allowed to have"
    )
    assert signal.matched_marker is None


@pytest.mark.parametrize("message", TIME_SENSITIVE_QUESTIONS)
def test_time_sensitive_questions_fire(message):
    signal = detect_time_sensitivity(message, now=_NOW)
    assert signal.is_time_sensitive
    assert signal.matched_marker


def test_matched_marker_is_the_specific_phrase_not_a_substring():
    # Longest-first alternation: "most recent" must win over "recent",
    # so a false positive is diagnosable from the trail.
    signal = detect_time_sensitivity("what is the most recent release", now=_NOW)
    assert signal.matched_marker == "most recent"


def test_a_current_or_recent_year_fires():
    assert detect_time_sensitivity("what changed in 2026", now=_NOW).is_time_sensitive
    # Last year still counts as "now"-adjacent.
    assert detect_time_sensitivity("the 2025 standard", now=_NOW).is_time_sensitive


def test_a_historical_year_does_not_fire():
    signal = detect_time_sensitivity("what happened in 1789", now=_NOW)
    assert not signal.is_time_sensitive
    signal = detect_time_sensitivity("the 2011 paper on transformers", now=_NOW)
    assert not signal.is_time_sensitive


def test_year_detection_moves_with_now():
    # 2026 is current at _NOW; from 2030 it is history. Nothing about
    # the rule is pinned to a hardcoded year.
    later = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert detect_time_sensitivity("what changed in 2026", now=later).is_time_sensitive is False


def test_matching_is_case_insensitive_and_word_bounded():
    assert detect_time_sensitivity("What is the LATEST spec", now=_NOW).is_time_sensitive
    # "latest" inside another word must not fire.
    assert not detect_time_sensitivity("translatestring helper", now=_NOW).is_time_sensitive


def test_empty_message_does_not_fire():
    assert not detect_time_sensitivity("", now=_NOW).is_time_sensitive


def test_known_limitation_time_sensitive_without_a_lexical_marker_is_missed():
    """A documented false NEGATIVE, asserted so it stays visible.

    "Has the EU AI Act's obligations taken effect yet?" is genuinely
    time-sensitive but contains no recency word — a lexical rule cannot
    see it. This is the cost of the precision-first design, and it is
    the right direction to fail in: a miss produces today's ungrounded
    answer (the status quo, no harm added), whereas a false positive
    spends a real search on a calculus question.

    If this ever starts passing, the marker list grew — re-run
    `compare_grounding.py --calibration` and check what it cost in
    false positives before keeping the change.
    """
    signal = detect_time_sensitivity(
        "Has the EU AI Act's general-purpose model obligations taken effect yet?",
        now=_NOW,
    )
    assert signal.is_time_sensitive is False
