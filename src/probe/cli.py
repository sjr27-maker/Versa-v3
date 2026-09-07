from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

from dotenv import load_dotenv

from probe.audit import NodeCallStore, TranscriptStore
from probe.db import create_pool
from probe.diagnostics import TurnDiagnosticsStore
from probe.disambiguate import DisambiguationStore
from probe.domain_config import load_domain_config
from probe.embeddings import (
    EmbeddingClient,
    StubEmbeddingClient,
    build_embedding_client,
)
from probe.learner import LearnerStore
from probe.llm import ModelTierClients, StubLLMClient, build_tier_clients
from probe.loop import SessionLoop
from probe.interactions import InteractionAbstractStore
from probe.memory import LearnerFactStore, ThinkingStyleStore
from probe.models import Learner
from probe.population_patterns import (
    PopulationAggregationConfig,
    PopulationPatternStore,
    aggregate_population_patterns,
)
from probe import migrate as _migrate


def _database_url() -> str:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("error: DATABASE_URL not set (check .env)", file=sys.stderr)
        sys.exit(2)
    return url


def _require_gemini_api_key() -> str:
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        print(
            "error: GEMINI_API_KEY not set (check .env) — pass --stub to "
            "run against StubLLMClient instead of the real Gemini API",
            file=sys.stderr,
        )
        sys.exit(2)
    return key


def _build_tier_clients(use_stub: bool) -> ModelTierClients:
    if use_stub:
        stub = StubLLMClient()
        return ModelTierClients(fast=stub, capable=stub, best=stub)
    return build_tier_clients(_require_gemini_api_key())


def _build_embedding_client(use_stub: bool) -> EmbeddingClient:
    if use_stub:
        return StubEmbeddingClient()
    return build_embedding_client(_require_gemini_api_key())


async def _resolve_learner(store: LearnerStore, spec: str) -> Learner:
    """--learner accepts either an existing learner's UUID or a label.

    A UUID must already exist (there's no "create by guessing an id").
    A label resumes the matching learner if one exists, else creates a
    new one — this is the session's identity, resolved once here, not
    per-turn state.
    """
    try:
        learner_id = UUID(spec)
    except ValueError:
        learner_id = None
    if learner_id is not None:
        learner = await store.get(learner_id)
        if learner is None:
            print(f"error: no learner with id {spec}", file=sys.stderr)
            sys.exit(2)
        return learner

    learner = await store.get_by_label(spec)
    if learner is not None:
        return learner
    return await store.create(label=spec)


def _build_interaction_pipeline(pool, embedding_client: EmbeddingClient) -> dict:
    """The interaction/retrieval pipeline (interactions.py, retrieval.py,
    history_block.py) -- wired into `probe chat` (and, since _build_loop
    is shared, `probe consolidate-session`'s session-end deferred-marking
    hook) but deliberately NOT into `probe serve` (webserver.py has its
    own separate _build_loop, untouched) until the hand-read described in
    this feature's own review has actually happened there too.

    No longer a dry run as of history_block.py: FinalAnswer's own prompt
    now reads retrieval's output directly (learner_history_block) and,
    when this learner has explicitly stated one, a structural requirement
    built from `stated_preferences` -- see history_block.py's and
    disambiguate.FinalAnswer's own docstrings for the read side, and
    StatedPreference's docstring for the write side. `reference_bindings`
    (reference_bindings.py) adds a third, independently-gated read: an
    exact-match lookup of this learner's known recurring-phrase meanings,
    fed into both AssessAndBranch (to suppress branching on something
    already known) and FinalAnswer (as a short background section, above
    learner_history_block). Predictions (interaction_nodes.
    LLMSelectionPredictor) still feed nothing back into what the learner
    sees. The critical-path additions are: the entry_state similarity
    comparison, the history-block assembly's own embedding call, the
    stated-preference lookup, the reference-binding lookup, and the
    option shuffle; classification/abstraction/stated-preference/
    reference-resolution classification itself all still run off the
    critical path.
    """
    from probe.interactions import (
        InteractionAbstractStore,
        InteractionOptionStore,
        InteractionRecorder,
        InteractionStore,
        PredictionStore,
        ReferenceBindingStore,
        StatedPreferenceStore,
        TurnOutcomeStore,
    )
    from probe.retrieval_config import RetrievalConfig

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
    }


def _build_loop(
    pool, tiers: ModelTierClients, embedding_client: EmbeddingClient, domain_config
) -> SessionLoop:
    return SessionLoop(
        transcript=TranscriptStore(pool),
        node_calls=NodeCallStore(pool),
        llm=tiers.fast,
        model_tier_clients=tiers,
        diagnostics_store=TurnDiagnosticsStore(pool),
        disambiguation_store=DisambiguationStore(pool),
        learner_fact_store=LearnerFactStore(pool),
        thinking_style_store=ThinkingStyleStore(pool),
        embedding_client=embedding_client,
        domain_config=domain_config,
        **_build_interaction_pipeline(pool, embedding_client),
    )


async def _chat(learner_spec: str, use_stub: bool) -> None:
    tiers = _build_tier_clients(use_stub)
    embedding_client = _build_embedding_client(use_stub)
    # The ONE place PROBE_DOMAIN is read for the CLI path (see
    # domain_config.load_domain_config's own docstring) -- resolved
    # once here, passed down as a plain DomainConfig object; nothing
    # past this point reads the environment variable again.
    domain_config = load_domain_config()
    pool = await create_pool(_database_url(), min_size=1, max_size=4)
    try:
        learner = await _resolve_learner(LearnerStore(pool), learner_spec)
        label_suffix = f" (label={learner.label!r})" if learner.label else ""
        print(f"probe: learner {learner.id}{label_suffix}")
        print("probe: minimal_branch mode — no concept graph")
        print(f"probe: domain = {domain_config.domain.value}")
        print(
            "probe: interaction/retrieval pipeline ON (dry run -- writes "
            "interactions/topics/outcomes/abstracts/predictions; nothing "
            "it produces reaches this session's own responses yet)"
        )
        loop = _build_loop(pool, tiers, embedding_client, domain_config)
        await loop.run_interactive(learner.id)
    finally:
        await pool.close()


async def _consolidate_session(session_id_str: str, use_stub: bool) -> None:
    try:
        session_id = UUID(session_id_str)
    except ValueError:
        print(f"error: {session_id_str!r} is not a valid session id", file=sys.stderr)
        sys.exit(2)

    tiers = _build_tier_clients(use_stub)
    embedding_client = _build_embedding_client(use_stub)
    domain_config = load_domain_config()
    pool = await create_pool(_database_url(), min_size=1, max_size=4)
    try:
        loop = _build_loop(pool, tiers, embedding_client, domain_config)
        # Deliberate, unambiguous trigger — no turn-count gate (unlike
        # run_interactive's own auto-consolidate on exit): this command
        # exists specifically to consolidate a session on demand,
        # regardless of how many turns it has.
        result = await loop.consolidate_session(session_id)
        if result is None:
            print(
                "probe: nothing to consolidate — this session wrote no "
                "learner_facts (a BASELINE session, or it never resolved "
                "anything)"
            )
            return
        print(
            f"probe: thinking-style candidate {result.id}\n"
            f"  path_summary: {result.path_summary}\n"
            f"  confirmation_count={result.confirmation_count} "
            f"status={result.status.value}\n"
            f"  session_ids: {[str(s) for s in result.session_ids]}"
        )
    finally:
        await pool.close()


async def _aggregate_patterns() -> None:
    """`probe aggregate-patterns` — the on-demand batch step of
    population_patterns.py's pipeline (raw interaction -> abstract form
    -> aggregation -> multi-learner support -> retrieval). Deliberately
    explicit and on-demand, same "no automatic per-turn trigger"
    precedent as `probe consolidate-session`: clustering every
    learner's abstracts against every other learner's is a global
    operation, not something to redo on every turn.
    """
    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        abstract_store = InteractionAbstractStore(pool)
        pattern_store = PopulationPatternStore(pool)
        written = await aggregate_population_patterns(
            abstract_store, pattern_store, PopulationAggregationConfig()
        )
        if not written:
            print(
                "probe: no readable population patterns this run -- every "
                "cluster fell short of >=20 distinct learners or exceeded "
                "the 25% single-learner share cap"
            )
            return
        print(f"probe: wrote {len(written)} readable population pattern(s):")
        for pattern in written:
            print(
                f"  {pattern.id}: {pattern.abstract_form!r} "
                f"(support={pattern.support_count}, "
                f"distinct_learners={pattern.distinct_learner_count}, "
                f"max_share={pattern.max_per_learner_share:.2f})"
            )
    finally:
        await pool.close()


async def _run_migrations(status_only: bool, do_baseline: bool) -> None:
    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if status_only:
                applied, pending = await _migrate.status(conn)
                print(f"migrations: {len(applied)} applied, {len(pending)} pending")
                for name in applied:
                    print(f"  [x] {name}")
                for name in pending:
                    print(f"  [ ] {name}")
                return
            if do_baseline:
                stamped = await _migrate.baseline(conn)
                if stamped:
                    print(
                        f"probe migrate: recorded {len(stamped)} migration(s) as "
                        f"already-applied without running them "
                        f"({stamped[0]} .. {stamped[-1]})"
                    )
                else:
                    print("probe migrate: nothing to baseline - ledger already complete")
                return
            applied = await _migrate.apply_all(
                conn, on_apply=lambda name: print(f"  applied {name}")
            )
            if applied:
                print(f"probe migrate: applied {len(applied)} migration(s)")
            else:
                print("probe migrate: database already up to date")
    finally:
        await pool.close()


def _serve(host: str, port: int) -> None:
    """`probe serve` — the calm single-page UI (probe/static/) backed by
    the Starlette API in webserver.py, over the same SessionLoop the CLI
    drives. Imported lazily so `probe chat` never pulls in Starlette.
    This is the only web UI; the old Streamlit `probe web` was removed."""
    from probe.webserver import serve

    serve(host=host, port=port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="probe")
    subparsers = parser.add_subparsers(dest="command")
    chat_parser = subparsers.add_parser(
        "chat", help="start an interactive minimal_branch session loop"
    )
    chat_parser.add_argument(
        "--learner",
        required=True,
        help="learner label (resumes if it exists, creates if not) "
        "or an existing learner's UUID",
    )
    chat_parser.add_argument(
        "--stub",
        action="store_true",
        help="use StubLLMClient/StubEmbeddingClient instead of the "
        "real Gemini API (no GEMINI_API_KEY needed, no cost)",
    )
    consolidate_parser = subparsers.add_parser(
        "consolidate-session",
        help="background step 6-8 of the memory layer (memory.py) for "
        "one session on demand: label its facts' order-structure and "
        "compare against this learner's thinking_style_candidates",
    )
    consolidate_parser.add_argument("session_id", help="session id (UUID) to consolidate")
    consolidate_parser.add_argument(
        "--stub",
        action="store_true",
        help="use StubLLMClient/StubEmbeddingClient instead of the "
        "real Gemini API (no GEMINI_API_KEY needed, no cost)",
    )
    subparsers.add_parser(
        "aggregate-patterns",
        help="cluster every learner's latest interaction_abstracts row "
        "and write readable (>=20 distinct learners, <=25% single-"
        "learner share) clusters to population_patterns -- the "
        "on-demand aggregation step retrieval reads from",
    )
    migrate_parser = subparsers.add_parser(
        "migrate",
        help="apply pending SQL migrations to DATABASE_URL, in order, "
        "once each (idempotent; tracked in a schema_migrations table)",
    )
    migrate_parser.add_argument(
        "--status",
        action="store_true",
        help="show applied/pending migrations and exit without changing anything",
    )
    migrate_parser.add_argument(
        "--baseline",
        action="store_true",
        help="record every migration as already-applied WITHOUT running "
        "it - for a database that already has the full schema but no "
        "schema_migrations ledger (e.g. a hand-migrated dev DB)",
    )
    serve_parser = subparsers.add_parser(
        "serve",
        help="launch the calm single-page web UI (Starlette API + probe/static/)",
    )
    # Defaults are deployment-first: bind 0.0.0.0 and take the port from
    # $PORT (Cloud Run / any PaaS injects it) so the container works with
    # no extra flags. Locally it's still reachable at localhost:8000;
    # pass --host 127.0.0.1 to restrict it.
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument(
        "--port", type=int, default=int(os.environ.get("PORT", "8000"))
    )
    args = parser.parse_args()

    if args.command == "chat":
        asyncio.run(_chat(args.learner, args.stub))
    elif args.command == "consolidate-session":
        asyncio.run(_consolidate_session(args.session_id, args.stub))
    elif args.command == "aggregate-patterns":
        asyncio.run(_aggregate_patterns())
    elif args.command == "migrate":
        asyncio.run(_run_migrations(args.status, args.baseline))
    elif args.command == "serve":
        _serve(args.host, args.port)
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
