"""Tier-to-model mapping for the real (Gemini) LLM client.

Three tiers exist purely as a cost/quality knob over which concrete
Gemini model answers a node's calls — not something nodes know or care
about. Every node still just calls `llm.complete(prompt)`; which tier's
client it was constructed with is decided once, in SessionLoop, never
per-call.

Tier -> node assignment (fixed by agreement, not derived from anything
in this file):
    fast:     AssessAndBranch, DisambiguationOptions, the memory-layer
              judgment nodes (ConfirmFactMatch, WriteLearnerFact,
              SummarizeSessionPath, ConfirmThinkingStyleMatch)
    capable:  (unused since the full reasoning path was removed)
    best:     FinalAnswer, BaselineTeach

Model ids below are pinned to flash-class models across all three
tiers as of 2026-08, verified live against a real key (see the
scratch probe that produced this commit): `gemini-3.1-pro-preview`
(the previous `capable`/`best` pin) is blocked on this key by a
free-tier quota of 0 for Pro-class models — not a stale id, an
entitlement gap. `capable` and `best` are kept as separate fields
rather than collapsed to one flash id specifically so upgrading either
back to a Pro model later, once one is confirmed available on this
key, is a `GEMINI_MODEL_CAPABLE`/`GEMINI_MODEL_BEST` env override, not
a code change. Preview/dated model ids are still the least stable
constant in this codebase — re-verify against
https://ai.google.dev/gemini-api/docs/models (or the same live-probe
approach) before trusting any of this without checking first.
"""

from __future__ import annotations

import os

from pydantic import BaseModel


class ModelTierConfig(BaseModel):
    fast: str = "gemini-3.6-flash"
    capable: str = "gemini-3.5-flash"
    best: str = "gemini-3.5-flash"
    # The memory layer's embedding model (versa.embeddings) — verified
    # live against this key: `text-embedding-004`/`models/embedding-001`
    # both 404 (not available on the v1beta API this SDK version
    # targets), `gemini-embedding-001` works. Not one of the three
    # reasoning tiers above (it never generates text, only vectors), so
    # kept as its own field rather than folded into fast/capable/best.
    embedding: str = "gemini-embedding-001"
    # Model "thinking" effort per tier. Measured live (2026-09-21, streaming,
    # a ~120-word answer, median of 3): with no thinking config the models
    # think dynamically -- first text at ~5.5-6.9s; with "minimal" (or a 0
    # budget) first text lands at ~1.0-1.3s and the whole answer in ~2s. So
    # this is the single biggest per-turn latency lever. Values: "default"
    # (send no config -- the model's own dynamic thinking), a level
    # ("minimal" | "low" | "medium" | "high"), or an integer token budget
    # ("0" = off). Overridable per tier via GEMINI_THINKING_FAST/CAPABLE/BEST.
    #
    # Why fast/capable are a 256-token budget and best is "minimal": the fast
    # tier runs AssessAndBranch, whose judgment depends on resolving a
    # reference across the conversation ("why is IT faster?" after a
    # binary-search answer AND a derivatives detour). Measured (6 runs each):
    # "minimal" wrongly asked for clarification 5/6 times; a 128-256 budget
    # got 6/6 right with the same 0/18 false alarms and 0/18 misses on
    # standalone messages, for +0.3s (assess 1.5s -> 1.8-1.9s; the model's
    # default thinking took 3.3s and still got 1/6 wrong). The best tier only
    # writes the answer, which streams: its thinking delays the FIRST word, so
    # it stays minimal until answer quality under it has been evaluated.
    fast_thinking: str = "256"
    capable_thinking: str = "256"
    best_thinking: str = "minimal"

    @classmethod
    def from_env(cls) -> ModelTierConfig:
        """Env vars override the defaults without a code change — same
        escape hatch as every other config block in this codebase, here
        specifically because preview model ids drift on their own
        schedule, independent of when this file was last edited."""
        defaults = cls()
        return cls(
            fast=os.getenv("GEMINI_MODEL_FAST", defaults.fast),
            capable=os.getenv("GEMINI_MODEL_CAPABLE", defaults.capable),
            best=os.getenv("GEMINI_MODEL_BEST", defaults.best),
            embedding=os.getenv("GEMINI_MODEL_EMBEDDING", defaults.embedding),
            fast_thinking=os.getenv("GEMINI_THINKING_FAST", defaults.fast_thinking),
            capable_thinking=os.getenv("GEMINI_THINKING_CAPABLE", defaults.capable_thinking),
            best_thinking=os.getenv("GEMINI_THINKING_BEST", defaults.best_thinking),
        )
