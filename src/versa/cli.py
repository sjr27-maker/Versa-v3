from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

from versa import migrate as _migrate
from versa.db import create_pool
from versa.domain_config import load_domain_config
from versa.embeddings import (
    EmbeddingClient,
    StubEmbeddingClient,
    build_embedding_client,
)
from versa.interactions import InteractionAbstractStore
from versa.learner import LearnerStore
from versa.llm import ModelTierClients, StubLLMClient, build_tier_clients
from versa.loop import SessionLoop
from versa.models import Learner
from versa.population_patterns import (
    PopulationAggregationConfig,
    PopulationPatternStore,
    aggregate_population_patterns,
)
from versa.session_builder import build_session_loop


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


def _build_loop(
    pool, tiers: ModelTierClients, embedding_client: EmbeddingClient, domain_config
) -> SessionLoop:
    """Thin wrapper over `session_builder.build_session_loop` -- the one
    shared assembly point every entry point calls, so no two of them can
    silently diverge in which optional stores they wire in (see
    session_builder.py's own docstring for the incident that made this
    a single function)."""
    return build_session_loop(pool, tiers, embedding_client, domain_config=domain_config)


async def _chat(learner_spec: str, use_stub: bool) -> None:
    tiers = _build_tier_clients(use_stub)
    embedding_client = _build_embedding_client(use_stub)
    # The ONE place VERSA_DOMAIN is read for the CLI path (see
    # domain_config.load_domain_config's own docstring) -- resolved
    # once here, passed down as a plain DomainConfig object; nothing
    # past this point reads the environment variable again.
    domain_config = load_domain_config()
    pool = await create_pool(_database_url(), min_size=1, max_size=4)
    try:
        learner = await _resolve_learner(LearnerStore(pool), learner_spec)
        label_suffix = f" (label={learner.label!r})" if learner.label else ""
        print(f"versa: learner {learner.id}{label_suffix}")
        print("versa: minimal_branch mode — no concept graph")
        print(f"versa: domain = {domain_config.domain.value}")
        print(
            "versa: interaction/retrieval pipeline ON (dry run -- writes "
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
                "versa: nothing to consolidate — this session wrote no "
                "learner_facts (a BASELINE session, or it never resolved "
                "anything)"
            )
            return
        print(
            f"versa: thinking-style candidate {result.id}\n"
            f"  path_summary: {result.path_summary}\n"
            f"  confirmation_count={result.confirmation_count} "
            f"status={result.status.value}\n"
            f"  session_ids: {[str(s) for s in result.session_ids]}"
        )
    finally:
        await pool.close()


async def _aggregate_patterns() -> None:
    """`versa aggregate-patterns` — the on-demand batch step of
    population_patterns.py's pipeline (raw interaction -> abstract form
    -> aggregation -> multi-learner support -> retrieval). Deliberately
    explicit and on-demand, same "no automatic per-turn trigger"
    precedent as `versa consolidate-session`: clustering every
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
                "versa: no readable population patterns this run -- every "
                "cluster fell short of >=20 distinct learners or exceeded "
                "the 25% single-learner share cap"
            )
            return
        print(f"versa: wrote {len(written)} readable population pattern(s):")
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
                        f"versa migrate: recorded {len(stamped)} migration(s) as "
                        f"already-applied without running them "
                        f"({stamped[0]} .. {stamped[-1]})"
                    )
                else:
                    print("versa migrate: nothing to baseline - ledger already complete")
                return
            applied = await _migrate.apply_all(
                conn, on_apply=lambda name: print(f"  applied {name}")
            )
            if applied:
                print(f"versa migrate: applied {len(applied)} migration(s)")
            else:
                print("versa migrate: database already up to date")
    finally:
        await pool.close()


async def _discovered_moves(learner_spec: str | None = None) -> None:
    """`versa discovered-moves` -- the moves learners asked for when none of
    the cards matched and none of the library's types fit (migration 089),
    grouped across learners (style_patterns.discover_moves). A group marked
    CANDIDATE was asked for often enough, by enough learners, to consider as
    a new card type -- a person decides; nothing changes the library on its
    own. Read-only; no model call, writes nothing."""
    from versa.style_patterns import (
        DISCOVER_MIN_LEARNERS,
        DISCOVER_MIN_READINGS,
        StyleReader,
    )

    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        if learner_spec:
            learner = await _resolve_learner(LearnerStore(pool), learner_spec)
            moves = await StyleReader(pool).learner_moves(learner.id)
            if not moves:
                print(f"learner {learner.id}: no new moves yet")
                return
            print(f"learner {learner.id}: {len(moves)} new move(s) -- asked for, not offered by any card")
            for m in moves:
                shared = f", also asked by {m['others']} other learner(s)" if m["others"] else ""
                print(f"  \u201c{m['label']}\u201d -- {m['times']} time(s) in {m['chats']} chat(s){shared}")
            return
        # the instrument's own check: a learner typed the very move a card
        # on screen offered -- that card's wording didn't land
        missed_wording = await pool.fetch(
            "SELECT tagged_as, count(*) AS n FROM direction_misses WHERE in_hand GROUP BY tagged_as ORDER BY n DESC")
        if missed_wording:
            print("cards whose wording missed (typed what a card on screen already offered):")
            for r in missed_wording:
                print(f"  {r['tagged_as']}: {r['n']} time(s)")
        groups = await StyleReader(pool).discovered_moves()
        if not groups:
            print("no new moves yet -- every missed question so far fitted a card type, or none was read")
            return
        print(f"{len(groups)} group(s) of new moves (a candidate needs >= {DISCOVER_MIN_READINGS} readings "
              f"from >= {DISCOVER_MIN_LEARNERS} learners)")
        for g in groups:
            mark = "CANDIDATE " if g["candidate_card"] else ""
            print(f"  {mark}“{g['label']}” -- {g['readings']} reading(s), {g['learners']} learner(s), "
                  f"{g['sessions']} chat(s)")
            for example in g["examples"][1:]:
                print(f"      also: {example}")
    finally:
        await pool.close()


async def _observations(learner_spec: str) -> None:
    """`versa observations` -- the observation ledger for one learner
    (observations.py, docs/THINKING_STYLE.md layer 1): every raw event split
    by lens, then what the pick guesser currently reads off it -- the
    moments it counts for less, its hit record, and the learner's way in
    if it is clear yet. Read-only; no model call, writes nothing."""
    from collections import Counter

    from versa import pick_prediction as _pick_prediction
    from versa.observations import DERIVATION_VERSION, ObservationReader, turn_flags

    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        learner = await _resolve_learner(LearnerStore(pool), learner_spec)
        ledger = await ObservationReader(pool).for_learner(learner.id)
        print(f"learner {learner.id} -- {len(ledger)} observations ({DERIVATION_VERSION})")
        by_lens = Counter((o.lens, o.key) for o in ledger)
        for lens in ("style", "range", "interest", "ability", "mood", "said"):
            keys = sorted((k, n) for (lz, k), n in by_lens.items() if lz == lens)
            if keys:
                print(f"  {lens:9s} " + ", ".join(f"{k} x{n}" for k, n in keys))
        flags = turn_flags(ledger)
        print(f"  counted for less: {len(flags.stuck)} stuck turn(s), {len(flags.rushed)} rushed session(s)")
        store = _pick_prediction.PredictionStore(pool)
        hits, guesses = await store.record(learner.id, window=1000)
        print(f"  guesses: {hits} of {guesses} picks guessed right")
        from versa.style_patterns import StyleReader

        patterns = await StyleReader(pool).patterns(learner.id)
        print(f"  thinking style ({len(patterns)} pattern(s)):" if patterns else "  thinking style: nothing clear yet")
        # one fact per tendency; patterns pointing the same way are its facets
        for pattern in [p for p in patterns if p.facet_of is None]:
            print(f"    [{pattern.status}] {pattern.statement}")
            for name, gate in pattern.gates.items():
                print(f"        {'ok ' if gate.ok else 'no '} {name:12s} {gate.have}  (needs {gate.need})")
            for facet in [p for p in patterns if p.facet_of == pattern.id]:
                print(f"        also seen as [{facet.status}]: {facet.statement}")
        through = await StyleReader(pool).follow_through(learner.id)
        print(f"  misses (passed every card by asking their own): {through['misses']}, "
              f"{through['read']} read as a way out ({through['in_hand']} of those were on a card); "
              f"later offered {through['offered_later']}, taken {through['taken']}, held {through['held']}")
        profile = await store.approach_profile(learner.id)
        if profile is None:
            print("  way in: not clear yet -- answers are not shaped")
        else:
            print("  way in: " + " -> ".join(_pick_prediction.slot_label(s) for s in profile.path))
            for line in _pick_prediction.explain_profile(profile):
                print(f"    {line}")
    finally:
        await pool.close()


async def _review_claims(learner_spec: str) -> None:
    """`versa review-claims` -- the claim layer's review surface (per
    its own spec: "list every claim with its statement, test, status,
    confidence, evidence count, topic spread, and links to the
    interactions behind it"). Read-only; makes no LLM call and writes
    nothing.

    Shows the CURRENT (latest `claim_statements` row) statement, not
    `Claim.statement` directly -- see `claims.maybe_restate_claims`'s
    own docstring for why those can differ. When they differ, both are
    printed (current, then the original founding wording) so the
    drift is visible without a separate query. Any evidence row
    carrying a `provenance_note` (a human-diagnosed harness bug) shows
    it inline, so a reader doesn't have to remember which row was
    flagged."""
    from versa.claims import ClaimStore

    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        learner = await _resolve_learner(LearnerStore(pool), learner_spec)
        store = ClaimStore(pool)
        claims = await store.list_for_learner(learner.id)
        if not claims:
            print(f"versa: no claims for learner {learner.id}")
            return
        for claim in claims:
            evidence = await store.list_evidence(claim.id)
            topics = sorted({e.topic for e in evidence})
            current_statement = await store.get_current_statement(claim.id)
            print(f"\n[{claim.status.value}] {claim.id}")
            if current_statement is not None and current_statement.statement != claim.statement:
                print(f"  statement (current): {current_statement.statement}")
                print(f"  statement (founding): {claim.statement}")
            else:
                print(f"  statement: {claim.statement}")
            print(f"  test: {claim.test}")
            print(f"  value: {claim.value.value}   confidence: {claim.confidence:.3f}")
            print(f"  source: {claim.source.value}   write_policy: {claim.write_policy.value}")
            print(f"  evidence: {len(evidence)} row(s) across {len(topics)} topic(s): {topics}")
            for e in evidence:
                note = f"  [FLAGGED: {e.provenance_note}]" if e.provenance_note else ""
                print(
                    f"    - {e.direction.value} | topic={e.topic!r} | "
                    f"interaction={e.interaction_id} | session={e.session_id} | "
                    f"test_fired={e.test_fired} contradiction_possible="
                    f"{e.contradiction_was_possible}{note}"
                )
    finally:
        await pool.close()


async def _score_predictions(exclude_contaminated: bool) -> None:
    """`versa score-predictions` -- the reliability-diagram check
    score_predictions.py exists for: scores every claim's evidence
    history against itself (see that module's own docstring for why
    that's not circular) and prints observed-vs-predicted hit rate per
    confidence bucket, plus an overall Brier score. Read-only, no LLM
    call, pools across every learner (a calibration question about the
    confidence formula, not about any one learner).

    `--exclude-contaminated` prints BOTH pictures -- the full one
    (identical to production confidence, contaminated rows included)
    and one with every human-flagged (`provenance_note`) row dropped
    entirely -- so a real miscalibration can be told apart from a known
    harness bug's effect on the curve, without ever hiding the
    contaminated rows from production itself."""
    from versa.claims import ClaimStore
    from versa.score_predictions import (
        format_reliability_diagram,
        score_predictions_for_all_learners,
    )

    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        store = ClaimStore(pool)
        bins, overall_brier, total_trials = await score_predictions_for_all_learners(store)
        print("ALL EVIDENCE (production picture):")
        print(format_reliability_diagram(bins, overall_brier, total_trials))
        if exclude_contaminated:
            clean_bins, clean_brier, clean_total = await score_predictions_for_all_learners(
                store, exclude_contaminated=True
            )
            print("\nEXCLUDING FLAGGED (provenance_note) ROWS:")
            print(format_reliability_diagram(clean_bins, clean_brier, clean_total))
    finally:
        await pool.close()


async def _seed_demo_fixture() -> None:
    """`versa seed-demo-fixture` -- (re)applies demo_fixture.py's two
    hand-authored, opposite-portrait learners. Idempotent; no LLM call,
    no embedding call. See that module's own docstring for what it
    does and does not build."""
    from versa.demo_fixture import DEMO_QUESTION_SET, seed_demo_fixture

    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        learners = await seed_demo_fixture(pool)
        print("versa: demo fixture seeded (idempotent -- safe to re-run):")
        for role, learner in learners.items():
            print(f"  {role}: {learner.label} ({learner.id})")
        print(f"\nfixed question set ({len(DEMO_QUESTION_SET)} questions):")
        for q in DEMO_QUESTION_SET:
            print(f"  - {q}")
    finally:
        await pool.close()


async def _compare_portraits(question: str | None, use_stub: bool) -> None:
    """`versa compare-portraits` -- comparison.py's three-column wrong-
    portrait control: same question, the two fixture portraits plus a
    zero-claims control, side by side. Seeds/reuses the fixture and the
    control learner (idempotent). Without --stub this makes a real
    Gemini call per column (3 calls per question) -- the only way to
    see whether the answers actually differ, not just whether the
    claim-derived prompt does (see comparison.py's own docstring on
    what a stub run can and cannot prove)."""
    from versa.comparison import format_comparison, run_comparison
    from versa.demo_fixture import DEMO_QUESTION_SET

    tiers = _build_tier_clients(use_stub)
    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        questions = [question] if question else DEMO_QUESTION_SET
        for q in questions:
            columns = await run_comparison(pool, q, llm=tiers.best)
            print(format_comparison(q, columns))
            print()
    finally:
        await pool.close()


def _lan_address() -> str | None:
    """This machine's address on its local network (the interface outbound
    traffic would use; nothing is sent), for `serve --host 0.0.0.0`."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


async def _serve(host: str, port: int, use_stub: bool, web_dir: str | None) -> None:
    """`versa serve` -- the HTTP/WebSocket API the Versa app talks to (server.py),
    plus the built Flutter web app at "/" if `app/build/web` exists."""
    import uvicorn

    from versa.accounts import Auth
    from versa.server import LOCAL_ORIGIN_REGEX, create_app

    # Sign-in (accounts.py) is on unless VERSA_AUTH=off, and a server other
    # devices can reach must have it: that is the whole point of it.
    local = host in ("127.0.0.1", "localhost", "::1")
    auth = Auth.from_env(local=local)
    if auth is None and not local:
        print("error: VERSA_AUTH=off is only allowed with --host 127.0.0.1", file=sys.stderr)
        sys.exit(2)
    # The phone app doesn't need CORS; a separately hosted web build does.
    cors = os.environ.get("VERSA_CORS_ORIGIN_REGEX") or (LOCAL_ORIGIN_REGEX if local else r"^$")

    tiers = _build_tier_clients(use_stub)
    embedding_client = _build_embedding_client(use_stub)
    domain_config = load_domain_config()
    pool = await create_pool(_database_url(), min_size=1, max_size=8)
    try:
        async with pool.acquire() as conn:
            _applied, pending = await _migrate.status(conn)
        if pending:
            print(
                f"error: {len(pending)} pending migration(s) -- run `versa migrate` first",
                file=sys.stderr,
            )
            sys.exit(2)
        resolved_web = Path(web_dir) if web_dir else Path("app") / "build" / "web"
        app = create_app(
            pool, tiers, embedding_client,
            domain_config=domain_config,
            web_dir=resolved_web,
            llm_mode="stub" if use_stub else "live",
            cors_origin_regex=cors,
            auth=auth,
            android_download_url=os.environ.get("VERSA_ANDROID_URL") or None,
        )
        if auth is None:
            print("versa: sign-in OFF (VERSA_AUTH=off) -- name-only, this machine only")
        else:
            ways = [w for w, on in (("Google/email", auth.google),
                                    (f"dev names {sorted(auth.dev_logins)}", bool(auth.dev_logins))) if on]
            print(f"versa: sign-in ON ({', '.join(ways) or 'nothing configured!'}; "
                  f"invites {'required' if auth.invites_required else 'off'})")
            for note in auth.notes:
                print(f"versa:   note: {note}")
        shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
        print(f"versa: API      http://{shown_host}:{port}/api/health  "
              f"({'stub LLM' if use_stub else 'live Gemini'})")
        if resolved_web.is_dir():
            print(f"versa: web app  http://{shown_host}:{port}/   (serving {resolved_web})")
            lan = _lan_address() if host == "0.0.0.0" else None
            if lan:
                # Study rooms (rooms/): other devices on this network join here.
                print(f"versa: on your network  http://{lan}:{port}/")
        else:
            print(f"versa: no web build at {resolved_web} -- build it with "
                  "`cd app && flutter build web`, or run the app with `flutter run -d edge`")
        server = uvicorn.Server(uvicorn.Config(
            app, host=host, port=port, log_level="warning",
            # behind Cloud Run's proxy: trust its X-Forwarded-* (https, client ip)
            proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        ))
        await server.serve()
    finally:
        await pool.close()


async def _invite(args) -> None:
    """`versa invite create|list|revoke` -- against DATABASE_URL, so point it
    at the deployed database (e.g. through the Cloud SQL proxy) to make codes
    for the demo."""
    from datetime import UTC, datetime

    from versa.accounts import AccountStore, create_invites

    base = os.environ.get("VERSA_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        store = AccountStore(pool)
        if args.invite_command == "create":
            invites = await create_invites(
                pool, count=max(1, args.count), max_uses=args.uses or None, note=args.note,
                days=args.days, created_by=os.environ.get("USERNAME") or os.environ.get("USER"),
            )
            for invite in invites:
                uses = "unlimited" if invite.max_uses is None else f"{invite.max_uses} use(s)"
                print(f"{invite.code}  {base}/invite/{invite.code}  ({uses})")
        elif args.invite_command == "list":
            now = datetime.now(UTC)
            for invite in await store.list_invites():
                problem = store.invite_problem(invite, now)
                limit = "unlimited" if invite.max_uses is None else str(invite.max_uses)
                state = "ok" if problem is None else problem
                print(f"{invite.code}  used {invite.uses}/{limit}  {state}  {invite.note or ''}")
        elif args.invite_command == "revoke":
            found = await store.revoke_invite(args.code, args.reason)
            print("revoked" if found else "no such code")
            if not found:
                sys.exit(1)
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="versa")
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
    serve_parser = subparsers.add_parser(
        "serve",
        help="run the HTTP/WebSocket API the Versa app talks to (and serve the "
        "built web app at / if app/build/web exists)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="bind address (default 127.0.0.1 -- this machine only; "
                              "0.0.0.0 lets other devices in, which needs sign-in on)")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--stub", action="store_true",
                              help="StubLLMClient/StubEmbeddingClient: no key, no cost")
    serve_parser.add_argument("--web-dir", default=None,
                              help="built Flutter web app to serve at / (default app/build/web)")
    invite_parser = subparsers.add_parser(
        "invite", help="invite codes for new accounts (Versa is invite-only; accounts.py)"
    )
    invite_sub = invite_parser.add_subparsers(dest="invite_command")
    invite_create = invite_sub.add_parser("create", help="make invite code(s) and print their links")
    invite_create.add_argument("--count", type=int, default=1, help="how many codes (default 1)")
    invite_create.add_argument("--uses", type=int, default=1,
                               help="accounts each code can let in (default 1; 0 = unlimited)")
    invite_create.add_argument("--days", type=int, default=None, help="expire after this many days")
    invite_create.add_argument("--note", default=None, help="who it's for, for your own records")
    invite_sub.add_parser("list", help="every invite code, how often it was used, and whether it still works")
    invite_revoke = invite_sub.add_parser("revoke", help="withdraw a code (accounts it already let in stay)")
    invite_revoke.add_argument("code")
    invite_revoke.add_argument("--reason", default=None)
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
    subparsers.add_parser(
        "seed-demo-fixture",
        help="(re)apply demo_fixture.py's two hand-authored, opposite-"
        "portrait learners plus a fixed question set -- idempotent, "
        "no LLM/embedding call, for a reproducible comparison demo",
    )
    compare_parser = subparsers.add_parser(
        "compare-portraits",
        help="three-column wrong-portrait comparison (comparison.py): "
        "same question run for the concrete portrait, the abstract "
        "portrait, and a zero-claims control, showing which claims "
        "fired, the frozen claim_constraints_block each one produced, "
        "and the resulting answer -- seeds the demo fixture if needed",
    )
    compare_parser.add_argument(
        "--question", default=None,
        help="one question to run (default: every question in "
        "demo_fixture.DEMO_QUESTION_SET)",
    )
    compare_parser.add_argument(
        "--stub",
        action="store_true",
        help="use StubLLMClient instead of the real Gemini API -- proves "
        "the claims/prediction wiring is correct, but under a stub all "
        "three answers are identical by construction (a stub cannot "
        "read the prompt), so this cannot show whether the answers "
        "actually differ",
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
    review_claims_parser = subparsers.add_parser(
        "review-claims",
        help="read-only listing of one learner's claims (claims.py): "
        "statement, test, status, confidence, evidence count, topic "
        "spread, and the interactions each evidence row points at",
    )
    review_claims_parser.add_argument(
        "--learner", required=True,
        help="learner label or an existing learner's UUID",
    )
    observations_parser = subparsers.add_parser(
        "observations",
        help="read-only: one learner's observation ledger (observations.py) "
        "split by lens, what counts for less, the pick guesser's record and "
        "their way in",
    )
    observations_parser.add_argument(
        "--learner", required=True,
        help="learner label or an existing learner's UUID",
    )
    discovered_parser = subparsers.add_parser(
        "discovered-moves",
        help="read-only: moves learners asked for that no card type covers, grouped across "
        "learners -- candidates for new card types (style_patterns.discover_moves)",
    )
    discovered_parser.add_argument(
        "--learner", default=None,
        help="only this learner's own new moves (label or UUID)",
    )
    score_predictions_parser = subparsers.add_parser(
        "score-predictions",
        help="read-only reliability-diagram check (score_predictions.py): "
        "scores every claim's evidence history against itself and "
        "prints observed-vs-predicted hit rate per confidence bucket "
        "plus an overall Brier score -- the check clamp_confidence_for_"
        "decisions' [0.3, 0.7] clamp is waiting on before it can move",
    )
    score_predictions_parser.add_argument(
        "--exclude-contaminated", action="store_true",
        help="also print the reliability diagram with every human-flagged "
        "(provenance_note) evidence row excluded, alongside the normal "
        "(production) picture",
    )
    args = parser.parse_args()

    if args.command == "chat":
        asyncio.run(_chat(args.learner, args.stub))
    elif args.command == "serve":
        asyncio.run(_serve(args.host, args.port, args.stub, args.web_dir))
    elif args.command == "consolidate-session":
        asyncio.run(_consolidate_session(args.session_id, args.stub))
    elif args.command == "aggregate-patterns":
        asyncio.run(_aggregate_patterns())
    elif args.command == "seed-demo-fixture":
        asyncio.run(_seed_demo_fixture())
    elif args.command == "compare-portraits":
        asyncio.run(_compare_portraits(args.question, args.stub))
    elif args.command == "review-claims":
        asyncio.run(_review_claims(args.learner))
    elif args.command == "observations":
        asyncio.run(_observations(args.learner))
    elif args.command == "discovered-moves":
        asyncio.run(_discovered_moves(args.learner))
    elif args.command == "score-predictions":
        asyncio.run(_score_predictions(args.exclude_contaminated))
    elif args.command == "invite":
        if args.invite_command is None:
            invite_parser.print_help()
            sys.exit(2)
        asyncio.run(_invite(args))
    elif args.command == "migrate":
        asyncio.run(_run_migrations(args.status, args.baseline))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
