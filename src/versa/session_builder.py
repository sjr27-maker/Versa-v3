"""The single place a fully-wired `SessionLoop` is assembled — every
entry point (today `cli.py`'s `versa chat` / `versa consolidate-session`)
builds its loop through `build_session_loop` below and nowhere else.

WHY THIS MODULE EXISTS: it didn't, until 2026-09-15. Two entry points
(the CLI and a since-removed server) each built their own
`SessionLoop`, and drifted: the CLI wired `claim_store`,
`stated_preference_store`, `reference_binding_store`, and the whole
interaction/retrieval pipeline (`interaction_recorder`, `retrieval_pool`,
`history_block`'s dependencies); the server wired none of it. Every
server session ran un-personalized, silently, with no error and no test
catching it — confirmed by running a real session through the real HTTP
routes and reading `node_calls`/`turn_diagnostics` back: every one of
those blocks reached `FinalAnswer`'s prompt once wired in.

That was the fifth instance of one failure class in this codebase's
history (see git log / CLAUDE.md's incident-driven invariants for the
others — the hypothesis store running empty, `load_dotenv` never being
called, the Gemini schema silently overriding the prompt): something
built, verified in isolation, and never wired into the path that
actually runs. The fix is structural, not a one-time sync: nothing
constructs a `SessionLoop` any other way, so a future optional layer
added here reaches every entry point by construction. Any future server
must build its loop through this function too.
"""

from __future__ import annotations

from typing import Callable

import asyncpg

from versa.audit import NodeCallStore, TranscriptStore
from versa.claims import ClaimStore
from versa.diagnostics import TurnDiagnosticsStore
from versa.disambiguate import DisambiguationStore
from versa.domain_config import DomainConfig
from versa.embeddings import EmbeddingClient, TurnCachedEmbeddings
from versa.interactions import (
    InteractionAbstractStore,
    InteractionOptionStore,
    InteractionRecorder,
    InteractionStore,
    PredictionStore,
    ReferenceBindingStore,
    StatedPreferenceStore,
    TurnOutcomeStore,
)
from versa.llm import ModelTierClients
from versa.loop import SessionLoop
from versa.memory import LearnerFactStore, ThinkingStyleStore
from versa.retrieval_config import RetrievalConfig


def build_interaction_pipeline_stores(pool: asyncpg.Pool, embedding_client: EmbeddingClient) -> dict:
    """The interaction/retrieval pipeline (interactions.py, retrieval.py,
    history_block.py, claims.py, reference_bindings.py) — every
    `SessionLoop` kwarg that is additive on top of minimal_branch and
    gated on a store being present. `FinalAnswer`'s prompt reads
    `learner_history_block` (retrieval), `structural_requirement`
    (stated_preferences, when this learner has explicitly stated one),
    `reference_bindings_block` (an exact-match lookup of known recurring-
    phrase meanings), and `claim_constraints_block` (promoted claims) —
    see loop.py's own comments at each read site for exactly how each is
    built. `AssessAndBranch` also reads reference_bindings, to suppress
    branching on something already known. Predictions
    (interaction_nodes.LLMSelectionPredictor) still feed nothing back
    into what the learner sees."""
    interaction_store = InteractionStore(pool)
    turn_outcome_store = TurnOutcomeStore(pool)
    recorder = InteractionRecorder(
        interaction_store,
        turn_outcome_store,
        embedding_client,
        same_subject_threshold=RetrievalConfig().same_subject_threshold,
    )
    return {
        "interaction_recorder": recorder,
        "interaction_option_store": InteractionOptionStore(pool),
        "interaction_abstract_store": InteractionAbstractStore(pool),
        "turn_outcome_store": turn_outcome_store,
        "prediction_store": PredictionStore(pool),
        "retrieval_pool": pool,
        "stated_preference_store": StatedPreferenceStore(pool),
        "reference_binding_store": ReferenceBindingStore(pool),
        "claim_store": ClaimStore(pool),
    }


def build_session_loop(
    pool: asyncpg.Pool,
    tiers: ModelTierClients,
    embedding_client: EmbeddingClient,
    domain_config: DomainConfig | None = None,
    on_node_start: Callable[[str], None] | None = None,
) -> SessionLoop:
    """The one and only place a `SessionLoop` gets constructed — every
    optional store wired in, unconditionally. `on_node_start` is the
    one param that may genuinely differ by caller (a server can forward
    node-progress to a client; the CLI has nothing to forward to), so it
    stays a parameter rather than being hidden away too.

    The embedding client is wrapped ONCE here in `TurnCachedEmbeddings` and
    the wrapper is what both the loop and the interaction recorder receive,
    so the several identical embeds one turn used to make (memory search,
    history block, interaction record) share a single API call."""
    if not isinstance(embedding_client, TurnCachedEmbeddings):
        embedding_client = TurnCachedEmbeddings(embedding_client)
    return SessionLoop(
        transcript=TranscriptStore(pool),
        node_calls=NodeCallStore(pool),
        llm=tiers.fast,
        model_tier_clients=tiers,
        diagnostics_store=TurnDiagnosticsStore(pool),
        on_node_start=on_node_start,
        disambiguation_store=DisambiguationStore(pool),
        learner_fact_store=LearnerFactStore(pool),
        thinking_style_store=ThinkingStyleStore(pool),
        embedding_client=embedding_client,
        domain_config=domain_config,
        **build_interaction_pipeline_stores(pool, embedding_client),
    )
