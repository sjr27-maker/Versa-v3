"""Per-session style knobs -- answer length, depth and breadth as 0-100
slider levels -- and the one pure function that turns them into a directive
for `FinalAnswer`.

A slider at 50 adds nothing to the prompt (all three at 50 renders to '').
Every other value renders something that differs from its neighbours, so each
slider move really changes the prompt the regeneration runs with.

A new chat's length starts at 40, not 50 (2026-10-01: untouched answers ran
long on a phone), so an untouched chat's prompt carries the length line --
about 110 words instead of the ~167 an unprompted answer measured. Depth and
breadth still start at 50. The column default is migration 094; chats made
before it keep the level they had."""

from __future__ import annotations

from pydantic import BaseModel, Field

# The level at which a slider adds nothing to the prompt.
DEFAULT_LEVEL = 50
# Where a new chat's length slider starts.
DEFAULT_ANSWER_LENGTH = 40


class SessionKnobs(BaseModel):
    answer_length: int = Field(DEFAULT_ANSWER_LENGTH, ge=0, le=100)
    depth: int = Field(DEFAULT_LEVEL, ge=0, le=100)
    # narrow -> wide: how far an answer reaches beyond the question itself
    breadth: int = Field(DEFAULT_LEVEL, ge=0, le=100)


def target_words(level: int) -> int:
    """Log scale: 0 -> ~20 words, 50 -> ~167 (what an untouched answer
    measured live on 2026-09-24), 100 -> ~1400."""
    return round(20 * 70 ** (level / 100))


def _length_line(level: int) -> str:
    words = target_words(level)
    low, high = round(words * 0.8), round(words * 1.2)
    if level <= 15:
        extra = " No preamble, no recap, no examples unless essential."
    elif level < 50:
        extra = " Keep it compact; cut anything that isn't needed."
    elif level < 85:
        extra = " Use the extra room for explanation and an example."
    else:
        extra = " Use the room for explanation, worked examples and context."
    return (
        f"Length: {level}/100 on a short-to-long scale. Aim for about {words} words "
        f"(between {low} and {high}).{extra}"
    )


_DEPTH_BANDS = (
    (19, ("give only the core idea in plain everyday language; no technical terms, "
          "no mechanism, no edge cases")),
    (39, "focus on intuition; keep technical detail light and explain any term you use"),
    (60, "balance intuition with the main technical detail"),
    (80, "go into the underlying mechanism and reasoning, using precise terms"),
    (100, ("be fully rigorous: precise definitions, the underlying mechanism, "
           "assumptions, edge cases and exceptions, and formal notation where it helps")),
)


def _depth_line(level: int) -> str:
    descriptor = next(text for top, text in _DEPTH_BANDS if level <= top)
    return f"Depth: {level}/100 on a gist-to-rigorous scale -- {descriptor}."


_BREADTH_BANDS = (
    (19, "answer exactly what was asked and nothing around it; no side topics or connections"),
    (39, "stay close to the question; mention at most one closely related idea, and only if it helps"),
    (60, "answer the question and briefly connect it to one or two related ideas"),
    (80, "connect it to neighbouring ideas, related topics and where it is used"),
    (100, ("range widely: link it to related topics, other subjects, real-world uses "
           "and where it leads next")),
)


def _breadth_line(level: int) -> str:
    descriptor = next(text for top, text in _BREADTH_BANDS if level <= top)
    return f"Breadth: {level}/100 on a focused-to-wide scale -- {descriptor}."


def render_knob_directive(knobs: SessionKnobs) -> str:
    lines = []
    if knobs.answer_length != DEFAULT_LEVEL:
        lines.append(_length_line(knobs.answer_length))
    if knobs.depth != DEFAULT_LEVEL:
        lines.append(_depth_line(knobs.depth))
    if knobs.breadth != DEFAULT_LEVEL:
        lines.append(_breadth_line(knobs.breadth))
    if not lines:
        return ""
    return (
        "\nThese style controls are set for this conversation. They are "
        "requirements on how you write, and they override any default "
        "preference for length or structure elsewhere in this prompt:\n"
        + "".join(f"- {line}\n" for line in lines)
    )
