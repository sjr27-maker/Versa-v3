from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from collections.abc import AsyncIterator, Callable
from typing import NamedTuple, Protocol

import httpx
from google import genai
from google.genai import errors, types

from versa.model_config import ModelTierConfig

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Async LLM interface. Nodes depend on this, not on a specific provider."""

    async def complete(self, prompt: str) -> str: ...


# A canned entry is either a fixed string or a function of the prompt, so
# tests can stub prompt-sensitive behaviour (e.g. a proposer that reacts
# to which hypotheses appear in the listing) without a real LLM.
CannedResponse = str | Callable[[str], str]


# Two generic openers, so a width-3 plan still needs one backfill and the
# backfill path stays exercised by the default configuration.
_DEFAULT_PROPOSALS = json.dumps(
    [
        {
            "action": "explain",
            "target_concept": None,
            "rationale": "stub proposer: lead with a direct explanation",
        },
        {
            "action": "ask",
            "target_concept": None,
            "rationale": "stub proposer: check what the student already has",
        },
    ]
)


_DEFAULT_INTENT_BRANCHES = json.dumps(
    [
        {
            "statement": "wants to relate the new idea to something familiar",
            "plausibility": 0.6,
            "predicted_next_turn": "will ask for a real-world analogy",
        },
        {
            "statement": "is missing a prerequisite concept",
            "plausibility": 0.5,
            "predicted_next_turn": "will ask a clarifying question about an earlier concept",
        },
        {
            "statement": "is testing the tutor's explanation for correctness",
            "plausibility": 0.3,
            "predicted_next_turn": "will point out a perceived inconsistency",
        },
    ]
)


_DEFAULT_EXPAND_BRANCHES = json.dumps(
    {
        "layer_label": "knowledge_gap",
        "children": [
            {
                "statement": "may be missing the underlying definition",
                "plausibility": 0.5,
                "predicted_next_turn": "will ask what the term actually means",
            },
            {
                "statement": "may be conflating this with a related but different concept",
                "plausibility": 0.4,
                "predicted_next_turn": "will describe the related concept instead",
            },
        ],
    }
)


_DEFAULT_PATH_REQUIREMENT = json.dumps(
    {
        "current_belief": "the student's current understanding is not yet clear",
        "needed": "a direct explanation of the target concept",
        "must_not_assume": [],
        "scope": "one concrete idea, not a full syllabus",
    }
)


def _stub_describe_chat(prompt: str) -> str:
    first = next((line[4:] for line in prompt.splitlines() if line.startswith("  - ")), "this chat")
    words = first.rstrip("?.! ").split()
    title = " ".join(words[:6]) or "This chat"
    return json.dumps({"title": title[:1].upper() + title[1:], "about": f"A chat about: {' '.join(words[:14])}."})


def _stub_room_director(prompt: str) -> str:
    """rooms.RoomDirector on the stub: welcome and give a task to whoever
    created or joined the room, answer whoever talks to Versa or clicks an
    option, and otherwise stay quiet -- so `versa serve --stub` shows a room
    behaving roughly like the real thing (see rooms/nodes.py for the events)."""
    actions: list[dict] = []
    for line in prompt.splitlines():
        if not line.startswith("EVENT: "):
            continue
        event = line[len("EVENT: "):]
        name = event.split(" ", 1)[0]
        if " just created this room" in event or " just joined the room" in event:
            actions.append({"type": "say", "to": "all", "kind": "chat",
                            "text": f"Welcome, {name}! (stub) Let's learn this together."})
            actions.append({"type": "task", "to": name, "task_kind": "check",
                            "description": "(stub) Quick one: which of these is part of the topic?",
                            "options": ["The first part", "Something unrelated"],
                            "answer": "The first part"})
        elif " clicked the option " in event:
            actions.append({"type": "say", "to": name, "kind": "chat",
                            "text": f"Good pick, {name}. (stub)"})
        elif "talking to YOU directly" in event:
            actions.append({"type": "say", "to": "all", "kind": "content",
                            "text": f"(stub) Here's a short explanation for {name}. A real model "
                                    "would teach the topic here."})
            actions.append({"type": "options", "to": name, "prompt": "Where next?",
                            "options": ["An example", "A quick quiz", "The next part"]})
    return json.dumps({"reason": "stub", "actions": actions[:3]})


DIRECTION_CARD_KEYS: tuple[str, ...] = (
    "intuition", "example", "why", "use", "deeper", "next", "try_it", "real_data", "prove_it", "mistake",
    "visualise", "story", "summary", "compare", "connect", "debate", "path", "wild",
)

_DEFAULT_RESPONSES: dict[str, CannedResponse] = {
    # rooms.RoomDirector: reacts to the events in the prompt (see above).
    "ROOM:DIRECT": _stub_room_director,
    # StageDirector (stage.py): a tiny generic skit in the JSON Lines format,
    # so `versa serve --stub` still shows the stage performing.
    "STAGE:DIRECT": "\n".join(
        json.dumps(a, ensure_ascii=False)
        for a in [
            {"do": "emote", "mood": "thinking"},
            {"do": "spawn", "id": "idea", "kind": "emoji", "label": "💡", "x": 0.55, "y": 0.3},
            {"do": "say", "text": "Ooh, a good one. Let me show you."},
            {"do": "spawn", "id": "q", "kind": "text", "label": "?", "x": 0.75, "y": 0.2, "size": 1.6},
            {"do": "approach", "target": "idea"},
            {"do": "jump", "times": 1},
            {"do": "effect", "kind": "sparks", "x": 0.55, "y": 0.3},
            {"do": "relabel", "target": "q", "label": "!"},
            {"do": "emote", "mood": "excited"},
            {"do": "say", "text": "The chat has the details. I have the drama."},
            {"do": "wait", "ms": 600},
            {"do": "emote", "mood": "proud"},
        ]
    ),
    # Existing loop nodes. "[]" is a valid response under both the old
    # reweight-only contract and the current reweight-or-create one —
    # no test that doesn't opt in proposes anything either way.
    "INFER:": "[]",
    "TEACH:": "[stub teach]",
    "BASELINE:TEACH": "[stub baseline teach]",
    # Conservative default: no explicit request detected, so Plan/
    # DerivePath/Teach's precedence logic doesn't activate unless a
    # test opts in — same convention as MISMATCH/GROUND/RESOLVE.
    "EXTRACT:REQUEST": json.dumps({"present": False, "what": None}),
    # Conservative default: nothing extracted, so a test that doesn't
    # opt in doesn't need to fabricate a fake example/analogy for
    # ExtractTeachingArtifact's "already used" list to stay accurate.
    "EXTRACT:ARTIFACT": json.dumps({"example": None, "analogy": None}),
    "PROPOSE:ACTIONS": _DEFAULT_PROPOSALS,
    # Conservative default: no mismatch, so MismatchDetector doesn't
    # propose revisions or reweight hypotheses unless a test opts in.
    "MISMATCH:DETECT": json.dumps({"mismatch": False}),
    # Conservative default: no grounding, so GroundConcept doesn't act
    # on a fabricated concept unless a test opts in.
    "GROUND:CONCEPT": json.dumps({"concept_id": None, "confidence": 0.0}),
    # ValueFunction stubs — conservative middle values so score() is well-
    # defined even without canned test overrides. Individual tests provide
    # explicit canned responses when they want specific values.
    "SCORE:LEARNING_VALUE": "0.5",
    "SCORE:COGNITIVE_COST": "0.3",
    "SCORE:FRUSTRATION_RISK": "0.2",
    "SCORE:INFO_RESPONSES": "[]",
    "SCORE:INFO_UPDATE": "{}",
    # HypothesisGenerator's speculative prediction tree.
    "GENERATE:INTENT": _DEFAULT_INTENT_BRANCHES,
    "GENERATE:EXPAND": _DEFAULT_EXPAND_BRANCHES,
    # Conservative default: no match, so resolve() doesn't fabricate a
    # match unless a test opts in — same convention as MISMATCH/GROUND.
    "RESOLVE:MATCH": json.dumps({"matched_branch_id": None, "confidence": 0.0}),
    # Conservative default: no selection, so SelectBranch falls back to
    # its own deterministic highest-plausibility choice rather than a
    # stub-fabricated id — same convention as RESOLVE:MATCH.
    "SELECT:BRANCH": json.dumps({"selected_branch_id": None, "rationale": ""}),
    "DERIVE:PATH": _DEFAULT_PATH_REQUIREMENT,
    # Conservative default: no options, so a test that doesn't opt in
    # doesn't need to fabricate real branch ids for GenerateOptions to
    # map against — an empty list is a valid outcome, not rejected.
    "GENERATE:OPTIONS": "[]",
    # Conservative default: nothing satisfied, so CheckEvidence doesn't
    # fabricate a match — same convention as RESOLVE:MATCH/SELECT:BRANCH.
    "CHECK:EVIDENCE": json.dumps({"satisfied_branch_id": None, "confidence": 0.0}),
    # AttachTopic's topic-extraction call.
    "TOPIC:INFER": json.dumps({"topic": "stub topic"}),
    # disambiguate.py's minimal three-call mode. Conservative default:
    # not ambiguous, so a test that doesn't opt in goes straight to the
    # 1-call direct-answer path rather than needing to fabricate
    # branches.
    "ASSESS:BRANCH": json.dumps({"needs_branches": False, "branches": []}),
    # Conservative default: no options, same convention as
    # GENERATE:OPTIONS — an empty list is valid, not rejected.
    "DISAMBIGUATE:OPTIONS": "[]",
    "FINAL:ANSWER": "[stub final answer]",
    # memory.py's memory layer. Conservative default: a matched fact
    # does NOT resolve the current message, so a test that doesn't opt
    # in never accidentally skips branching.
    "CONFIRM:FACT_MATCH": json.dumps({"resolves": False}),
    # Conservative default: still applies, so a test that doesn't opt
    # in keeps the existing reason-confirmation-offered behavior
    # rather than silently suppressing it.
    "CONFIRM:REASON_RELEVANT": json.dumps({"still_applies": True}),
    "WRITE:FACT": json.dumps(
        {"situation": "stub situation", "resolution": "stub resolution"}
    ),
    "SUMMARIZE:PATH": json.dumps({"summary": "stub path summary"}),
    # Conservative default: does NOT confirm, so a test that doesn't
    # opt in never accidentally grows a thinking_style_candidates row.
    "CONFIRM:THINKING_STYLE": json.dumps({"confirms": False}),
    # interactions.py's async pipeline. Conservative default: abstains,
    # so a test that doesn't opt in never accidentally appends a
    # guessed turn_outcomes row (see ClassifyTurnOutcome's own
    # docstring on abstention).
    "CLASSIFY:TURN_OUTCOME": json.dumps(
        {"outcome": "deferred", "confidence": 0.0, "abstains": True}
    ),
    "ABSTRACT:FORM": json.dumps({"abstract_form": "stub abstract form"}),
    # Keys are option ids decided per-call (same reasoning as
    # SCORE:INFO_UPDATE) -- a static schema can't name them in advance.
    "PREDICT:SELECTION": "{}",
    # reviews.py's "why Versa thinks this" node -- plain text, not JSON.
    "EXPLAIN:PATTERN": "stub explanation of the pattern, from the evidence given.",
    # reviews.py's ask-about-it node. Conservative default: intent=none, so
    # a test that doesn't opt in never accidentally applies an edit.
    "ASK:PATTERN": json.dumps(
        {"answer": "stub answer, from the evidence given.", "intent": "none", "new_statement": None}
    ),
    # reviews.MatchStatedPreferenceToClaim. Conservative default: no match,
    # so a test that doesn't opt in never accidentally revises a claim.
    "MATCH:STATED_PREFERENCE": json.dumps(
        {"matched_index": None, "action": None, "new_statement": None}
    ),
    # topics.py (Learn a topic). Static but well-formed, so the whole flow
    # -- explore, expand, build, learn -- runs end to end on the stub.
    "TOPIC:BRANCHES": json.dumps(
        {
            "branches": [
                {"title": "Core ideas", "summary": "The basic concepts everything else builds on."},
                {"title": "How it works", "summary": "The mechanism behind it, step by step."},
                {"title": "Worked examples", "summary": "Seeing the ideas applied to real problems."},
                {"title": "Common mistakes", "summary": "Where learners usually go wrong, and why."},
                {"title": "Real-world uses", "summary": "Where this shows up outside the classroom."},
            ]
        }
    ),
    "TOPIC:OUTLINE": json.dumps(
        {
            "title": "Stub resource topic",
            "branches": [
                {"title": "Introduction", "summary": "What the resource sets out to teach.",
                 "anchor": "Introduction",
                 "children": [{"title": "Key terms", "summary": "The vocabulary used throughout.",
                               "anchor": "Key terms", "children": []}]},
                {"title": "Main section", "summary": "The central material of the resource.",
                 "anchor": "Main section", "children": []},
                {"title": "Summary", "summary": "How the pieces fit together.", "anchor": "Summary",
                 "children": []},
            ],
        }
    ),
    # topics.ExpandSection: a resource branch's own parts (none here, so the
    # stub offers the "beyond the resource" extras).
    "TOPIC:SECTION": json.dumps(
        {
            "branches": [],
            "extra": [
                {"title": "A related idea", "summary": "Something just beyond the resource that helps."},
                {"title": "Where it is used", "summary": "A real application of this section."},
            ],
        }
    ),
    "TOPIC:LESSONS": json.dumps(
        {
            "lessons": [
                {
                    "title": f"Stub lesson {i}",
                    "objective": "Explain the idea in your own words and use it once.",
                    "points": [
                        "The idea itself, stated plainly.",
                        "Why it works.",
                        "One place it shows up.",
                    ],
                }
                for i in (1, 2, 3)
            ]
        }
    ),
    # exams.py (Exam preparation). Well-formed, so set up -> quiz -> mock ->
    # hand in runs end to end on the stub. The first choice is always right.
    "EXAM:SYLLABUS": json.dumps(
        {
            "units": [
                {"title": "Foundations", "summary": "The core definitions an exam will expect you to know."},
                {"title": "Key mechanisms", "summary": "How the main processes work, step by step."},
                {"title": "Applications", "summary": "Using the ideas on unfamiliar problems."},
                {"title": "Common pitfalls", "summary": "The mistakes examiners look for."},
            ]
        }
    ),
    "EXAM:QUESTIONS": json.dumps(
        {
            "questions": [
                *(
                    {"kind": "choice", "prompt": f"Stub question {i}: which statement is correct?",
                     "skill": skill, "form": "puzzle" if i == 4 else "quiz",
                     "choices": ["The right one", "A wrong one", "Another wrong one", "Not this"],
                     "correct_index": 0, "explanation": "Because the first statement is the right one."}
                    for i, skill in ((1, "recall"), (2, "understand"), (3, "apply"), (4, "analyse"))
                ),
                {"kind": "short", "prompt": "Stub question 5: explain the idea in one sentence.",
                 "skill": "understand", "form": "quiz",
                 "answer": "The idea, stated in one sentence.",
                 "explanation": "A good answer states the idea itself."},
            ]
        }
    ),
    # exams.ExamWarmUp: a chapter's warm-up before its questions.
    "EXAM:WARMUP": json.dumps({
        "points": ["The chapter's main rule, stated plainly.", "What it is used for.", "The common mistake to avoid."],
        "formula": "v = u + at",
        "example": "A car starting at 2 m/s speeding up at 3 m/s^2 for 4 s reaches 2 + 3 x 4 = 14 m/s.",
        "script": [
            {"do": "plan", "cast": {"car": "the car"}, "shows": "speed growing over time"},
            {"do": "spawn", "id": "car", "kind": "emoji", "label": "🚗", "x": 0.3},
            {"do": "say", "text": "Watch the speed grow."},
            {"do": "note", "text": "The chapter's main rule, stated plainly."},
        ],
    }),
    # exams.ExamScene: a question set up on the stage (its ask is put back
    # by the parser, with the question's own choices).
    "EXAM:SCENE": json.dumps({"script": [
        {"do": "plan", "cast": {"q": "the question's situation"}, "shows": "the setup"},
        {"do": "spawn", "id": "q", "kind": "box", "label": "?", "x": 0.6},
        {"do": "say", "text": "Here's the situation."},
    ]}),
    # Grades every short answer correct (the stub can't read them).
    "EXAM:GRADE": lambda prompt: json.dumps(
        {"grades": [{"index": i, "correct": True, "feedback": "Stub: looks right."}
                    for i in range(prompt.count("Student's answer:"))]}
    ),
    # profiles.py: a sign-up profile read into a structured picture.
    "PROFILE:EXTRACT": json.dumps(
        {
            "stage": "higher_secondary", "education_system": "CBSE (India)", "level": "Class 11",
            "location": "Kerala, India", "institution": None, "field": "Science (PCM)",
            "subjects": ["Physics", "Chemistry", "Mathematics"], "working_towards": ["JEE Main"],
            "starting_point": "Has covered Class 10 algebra and basic mechanics; start new ideas "
                              "from those and build up.",
            "age_fits_stage": True, "note": None,
        }
    ),
    # topics.LessonActivity: a lesson task set on the stage (stage.task_stage_prompt).
    "STAGE:TASK": json.dumps({
        "kind": "quiz",
        "explain": "2 and 3 more make 5.",
        "script": [
            {"do": "plan", "cast": {"box": "a box of apples"}, "shows": "counting the apples"},
            {"do": "spawn", "id": "box", "kind": "emoji", "label": "\U0001F34E", "x": 0.6},
            {"do": "say", "text": "How many do I have now?"},
            {"do": "ask", "question": "Help me count: 2 apples plus 3 more?",
             "choices": [{"id": "a", "text": "5"}, {"id": "b", "text": "6"}], "answer": "a",
             "then": {"a": [{"do": "emote", "mood": "proud"}], "b": [{"do": "say", "text": "Count again!"}]}},
        ],
        "facts": ["The next lesson has a trick that saves half the work.",
                  "People used this idea long before it had a name."],
    }),
    # notes.py WriteNotes: revision notes of a chat.
    "NOTES:WRITE": json.dumps({
        "title": "Solving quadratic equations",
        "topics": [
            {"name": "Factorising a quadratic",
             "points": ["A quadratic $ax^2 + bx + c = 0$ can often be split into two brackets.",
                        "If a product is **zero**, one of the factors must be zero."],
             "formulas": ["x^2 - 5x + 6 = (x-2)(x-3)"],
             "example": "$x^2 - 5x + 6 = 0$ gives $x = 2$ or $x = 3$."},
            {"name": "The quadratic formula",
             "points": ["It solves any quadratic, even ones that don't factorise."],
             "formulas": ["x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}"],
             "example": None},
        ],
        "summary": "You solved quadratics by factorising, then met the formula that always works.",
        "stopped_at": "You were about to try the formula on $2x^2 + 3x - 2 = 0$.",
    }),
    # images.py ReadImage: what an uploaded picture shows.
    "IMAGE:READ": (
        "A photo of a handwritten maths problem. It reads: Solve $x^2 - 5x + 6 = 0$. "
        "Below it, the student's working: $(x-2)(x-3)=0$, then $x = 2$ and $x = 3$."
    ),
    # directions.py ReadMiss: a missed question the library can't place.
    "DIRECTIONS:READ_MISS": json.dumps(
        {"same_subject": True, "type": "none", "move": "where the rule stops working"}
    ),
    # directions.py: a card for every library type (the pool takes the ones
    # it drew), plus the path and wild extras.
    "DIRECTIONS:SUGGEST": json.dumps(
        {
            "cards": {
                "intuition": "Show me with an everyday picture",
                "example": "Work one example through with me",
                "why": "Why does it work like that",
                "use": "Where is this used for real",
                "deeper": "Take it one level deeper",
                "next": "What should I learn after this",
                "try_it": "Let me try one myself",
                "real_data": "Show me real numbers for this",
                "prove_it": "Prove that it has to be true",
                "mistake": "What do people usually get wrong",
                "visualise": "Draw me a picture of it",
                "story": "Who figured this out first",
                "summary": "Give me the one-line version",
                "compare": "How is it different from similar ideas",
                "connect": "Show the same idea in another subject",
                "debate": "Where do people still disagree",
                "path": "Work one out, then see where it is used",
                "wild": "Surprise me with an unexpected angle",
            }
        }
    ),
    # Conservative: never completes a task unless a test opts in.
    "LESSON:JUDGE": json.dumps(
        {"completed": False, "evidence": "", "drifted": False, "check_passed": None}
    ),
    # chat_titles.DescribeChat: named after the chat's own first message.
    "CHAT:DESCRIBE": lambda prompt: _stub_describe_chat(prompt),
    # feed.GenerateFeed. The parser drops `related` for a learner with no
    # history, so a static answer is right for both cases.
    "FEED:RECOMMEND": json.dumps(
        {
            "related": [
                {
                    "title": "Going one step deeper",
                    "hook": "Pick up where your last chat left off and push a little further.",
                    "reason": "Because of your recent chats",
                    "starter": "Can we go one step deeper on what we talked about last time?",
                },
            ],
            "explore": [
                {
                    "title": "How bridges stay up",
                    "hook": "Forces, arches and why some shapes carry far more weight.",
                    "reason": "Everyday engineering, no maths needed",
                    "starter": "How do bridges hold so much weight without falling down?",
                },
                {
                    "title": "Why the sky is blue",
                    "hook": "A question children ask that physicists love answering.",
                    "reason": "A short, satisfying bit of physics",
                    "starter": "Why is the sky blue and not violet?",
                },
                {
                    "title": "The printing press",
                    "hook": "How one machine changed who got to read and think.",
                    "reason": "A turning point in history",
                    "starter": "How did the printing press change the world?",
                },
            ],
        }
    ),
}


class StubLLMClient:
    """Deterministic stub used until the real client lands.

    Callers may pass a `canned` dict of {prompt_prefix: response}; the
    longest matching prefix wins. A canned value may be a callable taking
    the full prompt, for stubs that need to vary with prompt content.
    Anything unmatched falls through to `_DEFAULT_RESPONSES` and finally
    to a generic placeholder.

    Every prompt is appended to `self.prompts` so tests can assert on
    what a node actually asked for.
    """

    def __init__(self, canned: dict[str, CannedResponse] | None = None) -> None:
        self.canned = canned or {}
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        for prefix in sorted(self.canned, key=len, reverse=True):
            if prompt.startswith(prefix):
                response = self.canned[prefix]
                return response(prompt) if callable(response) else response
        for prefix, response in _DEFAULT_RESPONSES.items():
            if prompt.startswith(prefix):
                return response(prompt) if callable(response) else response
        return "[stub llm response]"

    async def complete_with_image(self, prompt: str, data: bytes, mime_type: str) -> str:
        """`complete`, with a picture beside the prompt (images.py). The stub
        can't see it: it answers from the prompt like any other call, and
        records what it was shown in `self.images`."""
        self.images = [*getattr(self, "images", []), (len(data), mime_type)]
        return await self.complete(prompt)

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """Same text `complete` would return, yielded a word at a time -- so
        the streaming path is exercised by tests exactly as the real client
        would drive it."""
        text = await self.complete(prompt)
        for piece in re.findall(r"\S+\s*|\s+", text) or [text]:
            yield piece


class LLMTransportError(Exception):
    """Raised when GeminiLLMClient exhausts its retries against a
    transport-level failure (timeout, rate limit, 5xx).

    A malformed or empty response body is deliberately NOT this: that's
    a content issue, and content-level recovery already exists per-node
    (Plan's rejected-value re-ask, GroundConcept/MismatchDetector's
    graceful None on a JSONDecodeError, Infer's per-item skip). This
    client returns whatever text a successful HTTP round-trip produced
    — including "" for a blocked/empty candidate — and lets that
    existing logic reject it exactly as it already rejects a canned
    garbage StubLLMClient response. Client-level and node-level retries
    must not compound: this client only ever retries the HTTP
    round-trip itself, never the model's content.
    """


# Prompt-prefix -> Gemini structured-output config, mirroring the same
# longest-prefix dispatch StubLLMClient uses to decide what each node's
# prompt expects back. Every node either json.loads()s the response or
# float()s it — TEACH: is the one exception (its output is the message
# shown to the student, not something downstream parses), so it alone
# gets no JSON mode. SCORE:INFO_UPDATE gets JSON mode without a fixed
# schema: its keys are hypothesis UUIDs decided per-call, which a static
# schema can't name in advance.
_NUMBER_SCHEMA = {"type": "NUMBER"}
_JSON_ONLY = object()  # sentinel: JSON mode, no fixed schema
_FREE_TEXT = object()  # sentinel: no JSON mode at all

_SCHEMA_BY_PREFIX: dict[str, object] = {
    # Best-effort schema for the current INFER: prompt. Note the prompt
    # itself (nodes.py, Infer.run) never actually asks the model for an
    # evidence_ref/turn_id — it's a still-stub prompt (see its
    # docstring) — so a real model's replies validate against
    # ProposedEvidence about as often as StubLLMClient's "[]" default
    # does today: rarely to never. That's a pre-existing gap in the
    # prompt, not something this client should paper over by inventing
    # a turn_id the model was never told.
    "INFER:": {
        # Flat, not oneOf: a "reweight" item uses hypothesis_id/
        # new_probability/new_confidence/polarity; a "create" item uses
        # layer/statement/initial_probability/initial_confidence. Every
        # field but `kind` is nullable so both shapes fit one schema —
        # nodes.py's Infer._parse_reweight/_parse_create pick which
        # fields actually apply based on `kind` and ignore the rest.
        # `evidence_ref`/`turn_id` are deliberately absent: the model
        # never supplies those (it doesn't know the turn's UUID) —
        # Infer builds every EvidenceRef itself. See Infer's docstring:
        # the pre-fix schema had no `kind` at all (reweight-only) and
        # asked for `evidence_ref` here, which no real response could
        # ever actually populate.
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "kind": {"type": "STRING", "enum": ["reweight", "create"]},
                "hypothesis_id": {"type": "STRING", "nullable": True},
                "new_probability": {"type": "NUMBER", "nullable": True},
                "new_confidence": {"type": "NUMBER", "nullable": True},
                "polarity": {
                    "type": "STRING",
                    "enum": ["supporting", "contradicting"],
                    "nullable": True,
                },
                "layer": {
                    "type": "STRING",
                    "enum": ["goal", "knowledge", "mental_model", "cognitive_state", "teaching"],
                    "nullable": True,
                },
                "statement": {"type": "STRING", "nullable": True},
                "initial_probability": {"type": "NUMBER", "nullable": True},
                "initial_confidence": {"type": "NUMBER", "nullable": True},
            },
            "required": ["kind"],
        },
    },
    "PROPOSE:ACTIONS": {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING"},
                "target_concept": {"type": "STRING", "nullable": True},
                "rationale": {"type": "STRING"},
            },
            "required": ["action"],
        },
    },
    "MISMATCH:DETECT": {
        "type": "OBJECT",
        "properties": {
            "mismatch": {"type": "BOOLEAN"},
            "learner_claim": {"type": "STRING"},
            "world_claim": {"type": "STRING"},
            "confidence": {"type": "NUMBER"},
            "suggested_cause": {
                "type": "STRING",
                "enum": ["learner_misconception", "possible_world_model_error"],
            },
        },
        "required": ["mismatch"],
    },
    "GROUND:CONCEPT": {
        "type": "OBJECT",
        "properties": {
            "concept_id": {"type": "STRING", "nullable": True},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["confidence"],
    },
    "EXTRACT:REQUEST": {
        "type": "OBJECT",
        "properties": {
            "present": {"type": "BOOLEAN"},
            "what": {"type": "STRING", "nullable": True},
        },
        "required": ["present"],
    },
    "EXTRACT:ARTIFACT": {
        "type": "OBJECT",
        "properties": {
            "example": {"type": "STRING", "nullable": True},
            "analogy": {"type": "STRING", "nullable": True},
        },
        "required": [],
    },
    "SCORE:LEARNING_VALUE": _NUMBER_SCHEMA,
    "SCORE:COGNITIVE_COST": _NUMBER_SCHEMA,
    "SCORE:FRUSTRATION_RISK": _NUMBER_SCHEMA,
    "SCORE:INFO_RESPONSES": {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "response": {"type": "STRING"},
                "probability": {"type": "NUMBER"},
            },
            "required": ["response", "probability"],
        },
    },
    "SCORE:INFO_UPDATE": _JSON_ONLY,
    "TEACH:": _FREE_TEXT,
    # BaselineTeach's output is likewise shown straight to the student,
    # not parsed downstream — same reasoning as TEACH:. Without this
    # entry, an unrecognized prefix falls through to this function's own
    # JSON-mode default (see its docstring), which forces
    # response_mime_type=application/json at the API level regardless
    # of what the prompt itself asks for — no amount of "respond with
    # plain prose" prompt text can override that. Confirmed live: this
    # was the actual cause of BASELINE wrapping its answer in
    # `{"response": "..."}` even after that instruction was added.
    "BASELINE:TEACH": _FREE_TEXT,
    "GENERATE:INTENT": {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "statement": {"type": "STRING"},
                "plausibility": {"type": "NUMBER"},
                "predicted_next_turn": {"type": "STRING"},
                "requires_evidence": {"type": "STRING", "nullable": True},
            },
            "required": ["statement", "plausibility", "predicted_next_turn"],
        },
    },
    "GENERATE:EXPAND": {
        "type": "OBJECT",
        "properties": {
            "layer_label": {"type": "STRING"},
            "children": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "statement": {"type": "STRING"},
                        "plausibility": {"type": "NUMBER"},
                        "predicted_next_turn": {"type": "STRING"},
                        "requires_evidence": {"type": "STRING", "nullable": True},
                    },
                    "required": ["statement", "plausibility", "predicted_next_turn"],
                },
            },
        },
        "required": ["layer_label", "children"],
    },
    "RESOLVE:MATCH": {
        "type": "OBJECT",
        "properties": {
            "matched_branch_id": {"type": "STRING", "nullable": True},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["confidence"],
    },
    "SELECT:BRANCH": {
        "type": "OBJECT",
        "properties": {
            "selected_branch_id": {"type": "STRING", "nullable": True},
            "rationale": {"type": "STRING"},
        },
        "required": ["rationale"],
    },
    "DERIVE:PATH": {
        "type": "OBJECT",
        "properties": {
            "current_belief": {"type": "STRING"},
            "needed": {"type": "STRING"},
            "must_not_assume": {"type": "ARRAY", "items": {"type": "STRING"}},
            "scope": {"type": "STRING"},
        },
        "required": ["current_belief", "needed", "must_not_assume", "scope"],
    },
    "GENERATE:OPTIONS": {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "branch_id": {"type": "STRING"},
                "text": {"type": "STRING"},
            },
            "required": ["branch_id", "text"],
        },
    },
    "CHECK:EVIDENCE": {
        "type": "OBJECT",
        "properties": {
            "satisfied_branch_id": {"type": "STRING", "nullable": True},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["confidence"],
    },
    "TOPIC:INFER": {
        "type": "OBJECT",
        "properties": {"topic": {"type": "STRING"}},
        "required": ["topic"],
    },
    "ASSESS:BRANCH": {
        "type": "OBJECT",
        "properties": {
            "needs_branches": {"type": "BOOLEAN"},
            "branches": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"statement": {"type": "STRING"}},
                    "required": ["statement"],
                },
            },
        },
        "required": ["needs_branches"],
    },
    # Updated for the kind/axis decision disambiguate.DisambiguationOptions
    # now makes (see that class's own docstring for the audit that
    # required it) -- previously a bare ARRAY of {branch_id, text}. A
    # live check confirmed the schema is what actually governs the
    # shape: with the OLD array schema still in force, the model kept
    # returning a bare array regardless of what the prompt text asked
    # for, exactly the same failure mode BASELINE:TEACH/FINAL:ANSWER's
    # own comments already document for response_mime_type -- the
    # schema constrains structured output at the API level and
    # overrides prompt instructions, not the other way around.
    "DISAMBIGUATE:OPTIONS": {
        "type": "OBJECT",
        "properties": {
            "kind": {"type": "STRING", "enum": ["subject", "approach"]},
            "axis": {
                "type": "STRING",
                "nullable": True,
                "enum": [
                    "concrete_general", "scope_narrow_broad", "rigor_intuition",
                    "mechanism_procedure", "worked_steps_result", "analogy_formal",
                    "single_example_pattern", "forward_derivation_backward_verification",
                    "brevity_depth", "structured_narrative",
                ],
            },
            "options": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "branch_id": {"type": "STRING"},
                        "text": {"type": "STRING"},
                        # nullable, not required: a subject-kind option
                        # carries no side (there's no axis to be a pole
                        # of) -- see OptionProposal's own docstring for
                        # why this is decided and persisted here rather
                        # than inferred downstream from option text.
                        "side": {
                            "type": "STRING", "nullable": True,
                            "enum": ["first", "second"],
                        },
                    },
                    "required": ["branch_id", "text"],
                },
            },
        },
        "required": ["kind", "options"],
    },
    # FinalAnswer's output is shown straight to the student, same
    # reasoning as TEACH:/BASELINE:TEACH — no JSON mode, or Gemini
    # would force response_mime_type=application/json regardless of
    # what the prompt itself asks for (see BASELINE:TEACH's own
    # comment for the live failure this was confirmed to cause).
    "FINAL:ANSWER": _FREE_TEXT,
    # StageDirector (stage.py) streams JSON *Lines* -- one action per line so
    # each can be forwarded as soon as it is complete. JSON mode would force
    # a single JSON value, so this must be free text.
    "STAGE:DIRECT": _FREE_TEXT,
    # rooms.RoomDirector. One flat action shape: which fields matter depends
    # on `type` (rooms/nodes.py parse_actions validates each).
    "ROOM:DIRECT": {
        "type": "OBJECT",
        "properties": {
            "reason": {"type": "STRING"},
            "actions": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "type": {"type": "STRING", "enum": ["say", "options", "complete_task", "part_done"]},
                        "to": {"type": "STRING"},
                        "private": {"type": "BOOLEAN", "nullable": True},
                        "kind": {"type": "STRING", "enum": ["chat", "content", "question"], "nullable": True},
                        "text": {"type": "STRING", "nullable": True},
                        "prompt": {"type": "STRING", "nullable": True},
                        "options": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True},
                        # a task's or race's tap-to-answer choices
                        "choices": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True},
                        "task_kind": {
                            "type": "STRING",
                            "enum": ["learn", "practice", "apply", "check", "discuss"],
                            "nullable": True,
                        },
                        "description": {"type": "STRING", "nullable": True},
                        "answer": {"type": "STRING", "nullable": True},
                        "part": {"type": "INTEGER", "nullable": True},
                        "evidence": {"type": "STRING", "nullable": True},
                    },
                    "required": ["type", "to"],
                },
            },
            # quiz tasks and a race: their own place, so choices and answer
            # can be required (the flat actions left them out, live 2026-10-01)
            "tasks": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "to": {"type": "STRING"},
                        "question": {"type": "STRING"},
                        "choices": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "answer": {"type": "STRING"},
                    },
                    "required": ["to", "question", "choices", "answer"],
                },
            },
            "race": {
                "type": "OBJECT",
                "nullable": True,
                "properties": {
                    "question": {"type": "STRING"},
                    "choices": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "answer": {"type": "STRING"},
                },
                "required": ["question", "choices", "answer"],
            },
        },
        "required": ["reason", "actions", "tasks"],
    },
    "CONFIRM:FACT_MATCH": {
        "type": "OBJECT",
        "properties": {"resolves": {"type": "BOOLEAN"}},
        "required": ["resolves"],
    },
    "CONFIRM:REASON_RELEVANT": {
        "type": "OBJECT",
        "properties": {"still_applies": {"type": "BOOLEAN"}},
        "required": ["still_applies"],
    },
    "WRITE:FACT": {
        "type": "OBJECT",
        "properties": {
            "situation": {"type": "STRING"},
            "resolution": {"type": "STRING"},
            # Nullable and NOT required, unlike situation/resolution --
            # a reason is written only when this specific exchange
            # actually suggests one, never invented to fill the field
            # (see memory.py's _fact_prompt and IDEAS.md's "Store the
            # reason" entry for why abstaining must be a valid,
            # expected output here, not a parse failure).
            "reason": {"type": "STRING", "nullable": True},
        },
        "required": ["situation", "resolution"],
    },
    "SUMMARIZE:PATH": {
        "type": "OBJECT",
        "properties": {"summary": {"type": "STRING"}},
        "required": ["summary"],
    },
    "CONFIRM:THINKING_STYLE": {
        "type": "OBJECT",
        "properties": {"confirms": {"type": "BOOLEAN"}},
        "required": ["confirms"],
    },
    "CLASSIFY:TURN_OUTCOME": {
        "type": "OBJECT",
        "properties": {
            "outcome": {
                "type": "STRING",
                "enum": ["matched", "contradicted_intent", "moved_on", "deferred"],
            },
            "confidence": {"type": "NUMBER"},
            "abstains": {"type": "BOOLEAN"},
        },
        "required": ["outcome", "confidence"],
    },
    "ABSTRACT:FORM": {
        "type": "OBJECT",
        "properties": {"abstract_form": {"type": "STRING"}},
        "required": ["abstract_form"],
    },
    # Keys are option ids decided per-call -- same reasoning as
    # SCORE:INFO_UPDATE, no fixed schema possible.
    "PREDICT:SELECTION": _JSON_ONLY,
    # claims.ClaimExtractor -- a JSON ARRAY (zero, one, or several
    # competing candidates from one episode), each carrying the SAME
    # closed-vocabulary "value" stated_preferences already uses (see
    # claims.py's own module docstring for why). Explicitly registered
    # rather than left to fall through to _JSON_ONLY's schema-less
    # default: a live check during DisambiguationOptions' own redesign
    # confirmed the response schema, not the prompt text, is what
    # actually governs shape -- worth pinning here from the start
    # rather than discovering the same failure mode again.
    "EXTRACT:CLAIM": {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "statement": {"type": "STRING"},
                "test": {"type": "STRING"},
                "value": {
                    "type": "STRING",
                    "enum": [
                        "concrete_before_abstract", "rule_before_example",
                        "wants_steps_shown", "prefers_brevity", "wants_analogies",
                        "no_analogies", "other",
                    ],
                },
                "topic": {"type": "STRING"},
            },
            "required": ["statement", "test", "value", "topic"],
        },
    },
    # Explicitly registered for the same reason EXTRACT:CLAIM is --
    # pinning a schema for a single-field object is cheap insurance
    # against the same "schema silently overrides prompt text" failure
    # mode, not left to fall through to _JSON_ONLY's schema-less default.
    "RESTATE:CLAIM": {
        "type": "OBJECT",
        "properties": {"statement": {"type": "STRING"}},
        "required": ["statement"],
    },
    # reviews.ExplainItem -- shown straight to the student, same reasoning
    # as FINAL:ANSWER: no JSON mode.
    "EXPLAIN:PATTERN": _FREE_TEXT,
    "ASK:PATTERN": {
        "type": "OBJECT",
        "properties": {
            "answer": {"type": "STRING"},
            "intent": {"type": "STRING", "enum": ["none", "edit", "approve", "archive"]},
            "new_statement": {"type": "STRING", "nullable": True},
        },
        "required": ["answer", "intent"],
    },
    "MATCH:STATED_PREFERENCE": {
        "type": "OBJECT",
        "properties": {
            "matched_index": {"type": "INTEGER", "nullable": True},
            "action": {"type": "STRING", "nullable": True, "enum": ["approve", "edit", "archive"]},
            "new_statement": {"type": "STRING", "nullable": True},
        },
        "required": ["matched_index"],
    },
    "TOPIC:BRANCHES": {
        "type": "OBJECT",
        "properties": {"branches": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"title": {"type": "STRING"}, "summary": {"type": "STRING"}},
            "required": ["title", "summary"],
        }}},
        "required": ["branches"],
    },
    "TOPIC:SECTION": {
        "type": "OBJECT",
        "properties": {
            "branches": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"}, "summary": {"type": "STRING"}, "anchor": {"type": "STRING"},
                },
                "required": ["title", "summary"],
            }},
            "extra": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {"title": {"type": "STRING"}, "summary": {"type": "STRING"}},
                "required": ["title", "summary"],
            }},
        },
        "required": ["branches", "extra"],
    },
    "TOPIC:OUTLINE": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "branches": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "summary": {"type": "STRING"},
                        "anchor": {"type": "STRING"},
                        "children": {"type": "ARRAY", "items": {
                            "type": "OBJECT",
                            "properties": {
                                "title": {"type": "STRING"},
                                "summary": {"type": "STRING"},
                                "anchor": {"type": "STRING"},
                                "children": {"type": "ARRAY", "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "title": {"type": "STRING"},
                                        "summary": {"type": "STRING"},
                                        "anchor": {"type": "STRING"},
                                    },
                                    "required": ["title", "summary"],
                                }},
                            },
                            "required": ["title", "summary"],
                        }},
                    },
                    "required": ["title", "summary"],
                },
            },
        },
        "required": ["title", "branches"],
    },
    "TOPIC:LESSONS": {
        "type": "OBJECT",
        "properties": {
            "lessons": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "objective": {"type": "STRING"},
                        "points": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["title", "objective", "points"],
                },
            },
        },
        "required": ["lessons"],
    },
    # A card for EVERY library type is required (directions.py lib-v2); the
    # random draw then picks which of them make the pool. The schema is fixed
    # per prompt, so it can't require just the drawn ones -- and with none
    # required Gemini wrote only two or three cards, while requiring the
    # original six forced exactly those six whatever the prompt asked (both
    # found live, 2026-09-30). path / wild stay optional (asked for when
    # drawn). A test keeps this list in step with directions.SLOTS.
    "DIRECTIONS:SUGGEST": {
        "type": "OBJECT",
        "properties": {"cards": {
            "type": "OBJECT",
            "nullable": True,
            "properties": {slot: {"type": "STRING"} for slot in DIRECTION_CARD_KEYS},
            "required": [slot for slot in DIRECTION_CARD_KEYS if slot not in ("path", "wild")],
        }},
        "required": ["cards"],
    },
    "DIRECTIONS:READ_MISS": {
        "type": "OBJECT",
        "properties": {
            "same_subject": {"type": "BOOLEAN"},
            "type": {"type": "STRING", "enum": [*[k for k in DIRECTION_CARD_KEYS if k not in ("path", "wild")],
                                                "none"]},
            "move": {"type": "STRING"},
        },
        "required": ["same_subject", "type", "move"],
    },
    "NOTES:WRITE": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "topics": {"type": "ARRAY", "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING"},
                    "points": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "formulas": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "example": {"type": "STRING", "nullable": True},
                },
                "required": ["name", "points", "formulas"],
            }},
            "summary": {"type": "STRING"},
            "stopped_at": {"type": "STRING", "nullable": True},
        },
        "required": ["title", "topics", "summary"],
    },
    "PROFILE:EXTRACT": {
        "type": "OBJECT",
        "properties": {
            "stage": {"type": "STRING", "enum": [
                "primary_school", "middle_school", "secondary_school", "higher_secondary",
                "undergraduate", "postgraduate", "working", "other"]},
            "education_system": {"type": "STRING", "nullable": True},
            "level": {"type": "STRING", "nullable": True},
            "location": {"type": "STRING", "nullable": True},
            "institution": {"type": "STRING", "nullable": True},
            "field": {"type": "STRING", "nullable": True},
            "subjects": {"type": "ARRAY", "items": {"type": "STRING"}},
            "working_towards": {"type": "ARRAY", "items": {"type": "STRING"}},
            "starting_point": {"type": "STRING", "nullable": True},
            "age_fits_stage": {"type": "BOOLEAN"},
            "note": {"type": "STRING", "nullable": True},
        },
        "required": ["stage", "subjects", "working_towards", "age_fits_stage"],
    },
    "EXAM:SYLLABUS": {
        "type": "OBJECT",
        "properties": {"units": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"title": {"type": "STRING"}, "summary": {"type": "STRING"}},
            "required": ["title", "summary"],
        }}},
        "required": ["units"],
    },
    "EXAM:QUESTIONS": {
        "type": "OBJECT",
        "properties": {"questions": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "kind": {"type": "STRING", "enum": ["choice", "short"]},
                "skill": {"type": "STRING", "enum": ["recall", "understand", "apply", "analyse"]},
                "form": {"type": "STRING", "enum": ["quiz", "puzzle"]},
                "prompt": {"type": "STRING"},
                "choices": {"type": "ARRAY", "items": {"type": "STRING"}},
                "correct_index": {"type": "INTEGER", "nullable": True},
                "answer": {"type": "STRING", "nullable": True},
                "explanation": {"type": "STRING"},
            },
            "required": ["kind", "skill", "form", "prompt", "explanation"],
        }}},
        "required": ["questions"],
    },
    "EXAM:GRADE": {
        "type": "OBJECT",
        "properties": {"grades": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "index": {"type": "INTEGER"},
                "correct": {"type": "BOOLEAN"},
                "feedback": {"type": "STRING"},
            },
            "required": ["index", "correct", "feedback"],
        }}},
        "required": ["grades"],
    },
    "LESSON:JUDGE": {
        "type": "OBJECT",
        "properties": {
            "completed": {"type": "BOOLEAN"},
            "evidence": {"type": "STRING"},
            "drifted": {"type": "BOOLEAN"},
            "check_passed": {"type": "BOOLEAN", "nullable": True},
        },
        "required": ["completed", "evidence", "drifted"],
    },
    "FEED:RECOMMEND": {
        "type": "OBJECT",
        "properties": {
            section: {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING"},
                        "hook": {"type": "STRING"},
                        "reason": {"type": "STRING"},
                        "starter": {"type": "STRING"},
                    },
                    "required": ["title", "hook", "reason", "starter"],
                },
            }
            for section in ("related", "explore")
        },
        "required": ["related", "explore"],
    },
}


def _response_config_kwargs(prompt: str) -> dict:
    """GenerateContentConfig kwargs for one prompt, by longest matching
    known prefix. An unrecognized prefix defaults to plain JSON mode
    (no fixed schema) rather than free text: every node in this
    codebase except Teach either json.loads()s or float()s its
    response, so JSON-shaped is the safer default for anything new."""
    for prefix in sorted(_SCHEMA_BY_PREFIX, key=len, reverse=True):
        if prompt.startswith(prefix):
            schema = _SCHEMA_BY_PREFIX[prefix]
            if schema is _FREE_TEXT:
                return {}
            if schema is _JSON_ONLY:
                return {"response_mime_type": "application/json"}
            return {
                "response_mime_type": "application/json",
                "response_json_schema": schema,
            }
    return {"response_mime_type": "application/json"}


# Exceptions the SDK itself would have retried transparently (timeout,
# connection failure) that don't carry an HTTP status code the way
# errors.APIError does — always retryable. NetworkError (not just
# ConnectError) because a connection that drops MID-response surfaces as
# httpx.ReadError -- observed live, and previously it escaped the retry loop
# and crashed the call. Programming errors (LocalProtocolError,
# UnsupportedProtocol) are deliberately NOT included.
_TRANSIENT_HTTPX_EXC: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)

# How much of a prompt to log on a retry — enough to identify which
# node/call this was (every prompt starts with a fixed prefix like
# "TEACH:", "SCORE:LEARNING_VALUE", "GENERATE:EXPAND") without dumping
# the whole prompt (which can include full hypothesis listings) into logs.
_RETRY_LOG_PROMPT_CHARS = 80


_THINKING_LEVELS = ("minimal", "low", "medium", "high")


def _thinking_config(setting: str | None) -> types.ThinkingConfig | None:
    """ModelTierConfig's `*_thinking` string -> an SDK ThinkingConfig.
    None / "" / "default" sends no config at all (the model's own dynamic
    thinking); a level name uses `thinking_level`; an integer string uses
    `thinking_budget` (0 = off). Anything else is a config mistake and
    fails loudly at construction, not silently on the first live call."""
    if setting is None:
        return None
    setting = setting.strip().lower()
    if setting in ("", "default"):
        return None
    if setting in _THINKING_LEVELS:
        return types.ThinkingConfig(thinking_level=setting)
    if setting.isdigit():
        return types.ThinkingConfig(thinking_budget=int(setting))
    raise ValueError(
        f"invalid thinking setting {setting!r}: expected 'default', one of "
        f"{_THINKING_LEVELS}, or an integer token budget"
    )


class GeminiLLMClient:
    """Real LLMClient backed by the Gemini API, implementing the exact
    same Protocol as StubLLMClient — no node changes needed to use it.

    Structured-output mode (responseSchema) is used for every call
    whose result a node will JSON-parse (see `_SCHEMA_BY_PREFIX`
    above); TEACH: is exempted since its output is free-text shown
    directly to the student.

    Retry/timeout/rate-limit handling lives entirely at this boundary —
    but as its own explicit loop, not the SDK's built-in
    `HttpRetryOptions` (build_tier_clients disables that at the
    genai.Client level, attempts=1). The SDK's retry is opaque tenacity
    machinery with no callback hook: it logs a generic "retrying in N
    seconds" line to its own internal logger with no way to attach
    which node/model/prompt the retry belongs to. Owning the loop here
    means a throttled call and a genuinely slow call are distinguishable
    — every attempt logs model, prompt prefix, attempt number, the HTTP
    status that triggered it, and the backoff chosen — and `retry_count`
    gives SessionLoop a running total it can snapshot per turn for
    turn_diagnostics (see loop.py's _total_retry_count).

    Retries only transport-level failure modes (timeout, 429, 5xx) and
    never touches a 200 response whose body a node's own validation
    later rejects. See `LLMTransportError` for why those two layers are
    kept separate.
    """

    def __init__(
        self,
        client: genai.Client,
        model: str,
        *,
        max_attempts: int = 3,
        retryable_status_codes: tuple[int, ...] = (408, 429, 500, 502, 503, 504),
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        exp_base: float = 2.0,
        jitter: float = 1.0,
        thinking: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._thinking_config = _thinking_config(thinking)
        self._max_attempts = max_attempts
        self._retryable_status_codes = retryable_status_codes
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._exp_base = exp_base
        self._jitter = jitter
        # Cumulative for this client's whole lifetime, not reset per
        # call — SessionLoop reads the delta between a snapshot taken
        # at the start and end of a turn to get that turn's retry
        # count, the same before/after pattern duration_ms already uses
        # with time.monotonic().
        self.retry_count: int = 0

    async def complete(self, prompt: str) -> str:
        config_kwargs = _response_config_kwargs(prompt)
        if self._thinking_config is not None:
            config_kwargs["thinking_config"] = self._thinking_config
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        prompt_prefix = prompt[:_RETRY_LOG_PROMPT_CHARS]
        delay = self._initial_delay

        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=config,
                )
                text = getattr(response, "text", None)
                # A blocked/empty candidate (safety filter, no valid
                # part) is a content issue, not a transport one —
                # return "" and let the calling node's existing
                # parse-failure path handle it, same as it already
                # handles a StubLLMClient canned garbage response.
                return text if text is not None else ""
            except (errors.ServerError, errors.ClientError, *_TRANSIENT_HTTPX_EXC) as exc:
                delay = await self._backoff_or_raise(exc, attempt, prompt_prefix, delay)
        # Unreachable: the loop above always returns on success or
        # raises once attempts are exhausted.
        raise AssertionError("unreachable")

    async def complete_with_image(self, prompt: str, data: bytes, mime_type: str) -> str:
        """`complete` with an image part before the prompt (images.py reads
        uploads with it). Same retry policy; free text out."""
        config = (
            types.GenerateContentConfig(thinking_config=self._thinking_config)
            if self._thinking_config is not None else None
        )
        contents = [types.Part.from_bytes(data=data, mime_type=mime_type), prompt]
        prompt_prefix = prompt[:_RETRY_LOG_PROMPT_CHARS]
        delay = self._initial_delay
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model, contents=contents, config=config,
                )
                text = getattr(response, "text", None)
                return text if text is not None else ""
            except (errors.ServerError, errors.ClientError, *_TRANSIENT_HTTPX_EXC) as exc:
                delay = await self._backoff_or_raise(exc, attempt, prompt_prefix, delay)
        raise AssertionError("unreachable")

    async def _backoff_or_raise(
        self, exc: Exception, attempt: int, prompt_prefix: str, delay: float
    ) -> float:
        """The retry decision shared by `complete` and `stream`: raise
        `LLMTransportError` if `exc` is not retryable or attempts are
        exhausted, else log, back off, and return the next delay."""
        status = getattr(exc, "code", None)
        retryable = (
            status in self._retryable_status_codes
            if status is not None
            else isinstance(exc, _TRANSIENT_HTTPX_EXC)
        )
        if not retryable or attempt >= self._max_attempts:
            # Wrapped in one exception type so anything
            # upstream that wants to catch this has a single
            # name to catch, not the SDK's internal error
            # hierarchy.
            raise LLMTransportError(
                f"Gemini request failed after {attempt} attempt(s) "
                f"(model={self._model}): {exc}"
            ) from exc
        self.retry_count += 1
        backoff = min(self._max_delay, delay) + random.uniform(0, self._jitter)
        logger.warning(
            "GeminiLLMClient retry: model=%s prompt_prefix=%r "
            "attempt=%d/%d http_status=%s backoff=%.2fs",
            self._model,
            prompt_prefix,
            attempt,
            self._max_attempts,
            status,
            backoff,
        )
        await asyncio.sleep(backoff)
        return min(self._max_delay, delay * self._exp_base)

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """Yield the completion's text as the model produces it (same
        request config as `complete`, incl. thinking). Retries a transport
        failure only BEFORE the first piece is yielded: once text has
        reached the caller it cannot be taken back, so a mid-stream failure
        raises `LLMTransportError` and the caller decides what to do."""
        config_kwargs = _response_config_kwargs(prompt)
        if self._thinking_config is not None:
            config_kwargs["thinking_config"] = self._thinking_config
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        prompt_prefix = prompt[:_RETRY_LOG_PROMPT_CHARS]
        delay = self._initial_delay

        for attempt in range(1, self._max_attempts + 1):
            yielded = False
            try:
                async for chunk in await self._client.aio.models.generate_content_stream(
                    model=self._model,
                    contents=prompt,
                    config=config,
                ):
                    text = getattr(chunk, "text", None)
                    if text:
                        yielded = True
                        yield text
                return
            except (errors.ServerError, errors.ClientError, *_TRANSIENT_HTTPX_EXC) as exc:
                if yielded:
                    raise LLMTransportError(
                        f"Gemini stream failed mid-response "
                        f"(model={self._model}): {exc}"
                    ) from exc
                delay = await self._backoff_or_raise(exc, attempt, prompt_prefix, delay)


class _PooledGeminiLLMClient:
    """Round-robins `complete()` calls across a small pool of independent
    `genai.Client` connections for one tier, so N concurrent calls to the
    same tier don't serialize behind a single HTTP connection. Each call
    still goes through the exact same GeminiLLMClient.complete() path
    (same retry policy, same structured-output config) — only which
    underlying connection carries it differs, so behavior/content is
    identical to the unpooled client.

    The round-robin counter is a plain int mutated between awaits, which
    is safe under asyncio's single-threaded cooperative scheduling (no
    `await` occurs between reading and incrementing it), not because of
    any lock.
    """

    def __init__(
        self,
        clients: list[genai.Client],
        model: str,
        *,
        max_attempts: int = 3,
        thinking: str | None = None,
    ) -> None:
        self._delegates = [
            GeminiLLMClient(c, model, max_attempts=max_attempts, thinking=thinking)
            for c in clients
        ]
        self._next = 0

    async def complete(self, prompt: str) -> str:
        delegate = self._delegates[self._next % len(self._delegates)]
        self._next += 1
        return await delegate.complete(prompt)

    async def complete_with_image(self, prompt: str, data: bytes, mime_type: str) -> str:
        delegate = self._delegates[self._next % len(self._delegates)]
        self._next += 1
        return await delegate.complete_with_image(prompt, data, mime_type)

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        delegate = self._delegates[self._next % len(self._delegates)]
        self._next += 1
        async for piece in delegate.stream(prompt):
            yield piece

    @property
    def retry_count(self) -> int:
        # Sum across every connection in the pool — each GeminiLLMClient
        # delegate tracks its own retries; this is the pool-wide total
        # SessionLoop reads for turn_diagnostics.
        return sum(d.retry_count for d in self._delegates)


class ModelTierClients(NamedTuple):
    """One LLMClient per tier. Passed into SessionLoop so each node is
    constructed with the tier its agreed assignment calls for (see
    model_config.py). Never constructed with mixed provider types in
    practice, but nothing here requires that — a test can hand
    SessionLoop three StubLLMClients, or three of the same instance for
    the current default (fully backward-compatible) behavior."""

    fast: LLMClient
    capable: LLMClient
    best: LLMClient
    # The fast model with its own (lighter) thinking, for the stage director;
    # None = use `fast` (stubs and tests).
    stage: LLMClient | None = None


def build_tier_clients(
    api_key: str,
    tier_config: ModelTierConfig | None = None,
    *,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
    pool_size: int | None = None,
) -> ModelTierClients:
    """Construct the three tiered LLMClients used by a real (non-stub)
    run. Each tier gets its own pool of `pool_size` independent
    genai.Client connections (each with the same retry/timeout policy),
    round-robinned by _PooledGeminiLLMClient — so a turn's fan-out of
    concurrent calls to the same tier (e.g. Plan scoring several
    candidates at once) doesn't queue behind one shared HTTP connection.
    pool_size=1 skips the round-robin wrapper entirely and returns the
    plain unpooled GeminiLLMClient, unchanged from before this existed.

    Defaults to 4, overridable via GEMINI_CLIENT_POOL_SIZE — same
    env-override escape hatch as ModelTierConfig.from_env(), here
    because the right pool size depends on the API key's actual rate
    limit, not something this codebase can pin correctly in advance.
    """
    cfg = tier_config or ModelTierConfig.from_env()
    if pool_size is None:
        pool_size = int(os.getenv("GEMINI_CLIENT_POOL_SIZE", "4"))

    def _make_connection() -> genai.Client:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                # SDK-level retry disabled (attempts=1) — GeminiLLMClient
                # now runs its own retry loop instead, so every attempt
                # can be logged with the model/prompt/status/backoff
                # this boundary needs (see GeminiLLMClient's docstring
                # for why the SDK's own tenacity retry can't do this).
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def _tier_client(model: str, thinking: str) -> LLMClient:
        if pool_size <= 1:
            return GeminiLLMClient(
                _make_connection(), model, max_attempts=max_attempts, thinking=thinking
            )
        connections = [_make_connection() for _ in range(pool_size)]
        return _PooledGeminiLLMClient(
            connections, model, max_attempts=max_attempts, thinking=thinking
        )

    return ModelTierClients(
        fast=_tier_client(cfg.fast, cfg.fast_thinking),
        capable=_tier_client(cfg.capable, cfg.capable_thinking),
        best=_tier_client(cfg.best, cfg.best_thinking),
        stage=_tier_client(cfg.fast, cfg.stage_thinking),
    )
