"""Billing (billing.py): RevenueCat decides what a learner paid for, Versa
decides what it means -- Plus refills, Spark packs, Exam Passes that end the
day after the exam -- through a sync the app calls and RevenueCat's webhook,
each purchase applied exactly once, and a RevenueCat outage never blocking
anyone."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

import versa.billing as billing_module
from tests.test_rooms_append_only import _string_literals
from tests.test_server import _ANSWER, _FACT, _NOT_AMBIGUOUS, _stop
from tests.test_sparks import _start_with
from versa.billing import (
    EXAM_PASS_FALLBACK_DAYS,
    Billing,
    RevenueCatClient,
    RevenueCatError,
    StorePurchase,
)
from versa.learner import LearnerStore
from versa.llm import StubLLMClient
from versa.sparks import TIERS, WELCOME_SPARKS, SparkEngine

_T0 = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)


class FakeRevenueCat:
    def __init__(self) -> None:
        self.entitlements: dict[str, dict[str, datetime | None]] = {}
        self.bought: dict[str, list[StorePurchase]] = {}
        self.down = False
        self.lookups = 0

    async def active_entitlements(self, customer_id: str):
        self.lookups += 1
        if self.down:
            raise RevenueCatError("down")
        return dict(self.entitlements.get(customer_id, {}))

    async def purchases(self, customer_id: str):
        if self.down:
            raise RevenueCatError("down")
        return list(self.bought.get(customer_id, []))


class _Clock:
    def __init__(self) -> None:
        self.now, self.mono = _T0, 0.0

    def __call__(self) -> datetime:
        return self.now


async def _setup(pool, *, webhook_auth="hook-secret"):
    fake, clock = FakeRevenueCat(), _Clock()
    billing = Billing(pool, fake, webhook_auth=webhook_auth, now=clock, clock=lambda: clock.mono)
    sparks = SparkEngine(pool, billing.tiers, now=clock)
    billing.sparks = sparks
    return billing, sparks, fake, clock


async def _learner(pool, label="billing"):
    return (await LearnerStore(pool).create(label=label)).id


async def _exam(pool, learner_id, exam_date):
    exam_id = uuid4()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO exams (id, learner_id, title, exam_date, source_kind, query) "
            "VALUES ($1, $2, 'Physics finals', $3, 'search', 'physics')",
            exam_id, learner_id, exam_date,
        )
    return exam_id


# -------------------------------------------------------------- the plan


@pytest.mark.asyncio(loop_scope="session")
async def test_plus_from_revenuecat_changes_the_refill(clean_pool):
    billing, sparks, fake, _ = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    assert await billing.tiers.tier_for(lid) == "free"
    fake.entitlements[str(lid)] = {"versa_plus": _T0 + timedelta(days=30)}
    billing.tiers.forget(lid)
    assert await billing.tiers.tier_for(lid) == "plus"
    status = await sparks.status(lid)
    assert status.tier == "plus" and status.cap == TIERS["plus"].cap
    # an expired entitlement RevenueCat still reports is not Plus
    fake.entitlements[str(lid)] = {"versa_plus": _T0 - timedelta(seconds=1)}
    billing.tiers.forget(lid)
    assert await billing.tiers.tier_for(lid) == "free"


@pytest.mark.asyncio(loop_scope="session")
async def test_revenuecat_answers_are_cached_and_an_outage_keeps_the_last_plan(clean_pool):
    billing, _, fake, clock = await _setup(clean_pool)
    assert clock.now == _T0
    lid = await _learner(clean_pool)
    fake.entitlements[str(lid)] = {"versa_plus": None}
    assert await billing.tiers.tier_for(lid) == "plus"
    assert await billing.tiers.tier_for(lid) == "plus"
    assert fake.lookups == 1, "the second answer came from the cache"
    clock.mono += 10_000
    fake.down = True
    assert await billing.tiers.tier_for(lid) == "plus", "last known plan while RevenueCat is down"
    other = await _learner(clean_pool, "never-seen")
    assert await billing.tiers.tier_for(other) == "free"


# -------------------------------------------------------------- purchases


@pytest.mark.asyncio(loop_scope="session")
async def test_a_spark_pack_adds_fifty_once(clean_pool):
    billing, sparks, fake, _ = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    fake.bought[str(lid)] = [StorePurchase("txn-1", "versa_sparks_50", _T0)]
    first = await billing.sync(lid)
    assert first == [{"kind": "sparks", "amount": 50}]
    assert await billing.sync(lid) == [], "the same transaction never counts twice"
    status = await sparks.status(lid)
    assert status.balance == WELCOME_SPARKS + 50, "bought Sparks may pass the refill cap"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_exam_pass_lasts_until_the_day_after_the_exam(clean_pool):
    billing, sparks, fake, clock = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    exam_day = date(2030, 1, 16)
    exam_id = await _exam(clean_pool, lid, exam_day)
    fake.bought[str(lid)] = [StorePurchase("txn-pass", "versa_exam_pass", _T0)]
    granted = await billing.sync(lid, exam_id)
    assert granted[0]["kind"] == "exam_pass" and granted[0]["exam_id"] == exam_id
    ends = granted[0]["ends_at"]
    assert ends.astimezone().date() == exam_day + timedelta(days=1)
    assert await billing.tiers.tier_for(lid) == "exam_pass"
    assert (await sparks.status(lid)).tier == "exam_pass"
    out = await billing.billing_out(lid)
    assert out.exam_pass_ends_at == ends and out.exam_pass_exam_id == exam_id

    clock.now = ends + timedelta(seconds=1)
    assert await billing.tiers.tier_for(lid) == "free", "the pass ends on its own"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_exam_pass_finds_the_nearest_exam_or_falls_back(clean_pool):
    billing, _, fake, _ = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    await _exam(clean_pool, lid, date(2030, 3, 1))
    near = await _exam(clean_pool, lid, date(2030, 1, 20))
    await _exam(clean_pool, lid, date(2029, 12, 1))  # already past
    fake.bought[str(lid)] = [StorePurchase("t1", "versa_exam_pass", _T0)]
    assert (await billing.sync(lid))[0]["exam_id"] == near

    nobody = await _learner(clean_pool, "no-exams")
    fake.bought[str(nobody)] = [StorePurchase("t2", "versa_exam_pass", _T0)]
    granted = (await billing.sync(nobody))[0]
    assert granted["exam_id"] is None
    assert granted["ends_at"] == _T0 + timedelta(days=EXAM_PASS_FALLBACK_DAYS)


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_products_are_recorded_but_grant_nothing(clean_pool):
    billing, sparks, fake, _ = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    fake.bought[str(lid)] = [StorePurchase("t3", "some_other_thing", _T0)]
    assert await billing.sync(lid) == []
    assert (await sparks.status(lid)).balance == WELCOME_SPARKS
    async with clean_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM billing_events WHERE learner_id = $1", lid) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_sync_without_revenuecat_is_a_503(clean_pool):
    from fastapi import HTTPException

    billing = Billing(clean_pool, None)
    with pytest.raises(HTTPException) as exc:
        await billing.sync(await _learner(clean_pool))
    assert exc.value.status_code == 503
    assert await billing.tiers.tier_for(uuid4()) == "free"


# -------------------------------------------------------------- webhooks


def _event(lid, **kw):
    event = {"id": str(uuid4()), "type": "NON_RENEWING_PURCHASE", "app_user_id": str(lid),
             "product_id": "versa_sparks_50", "transaction_id": "wh-txn",
             "purchased_at_ms": int(_T0.timestamp() * 1000), "environment": "SANDBOX"}
    event.update(kw)
    return {"api_version": "1.0", "event": event}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_webhook_and_a_sync_for_the_same_purchase_count_once(clean_pool):
    billing, sparks, fake, _ = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    body = _event(lid)
    first = await billing.handle_webhook(body)
    assert first["granted"] == {"kind": "sparks", "amount": 50}
    replay = await billing.handle_webhook(body)
    assert replay["duplicate"] is True and replay["granted"] is None
    fake.bought[str(lid)] = [StorePurchase("wh-txn", "versa_sparks_50", _T0)]
    assert await billing.sync(lid) == []
    assert (await sparks.status(lid)).balance == WELCOME_SPARKS + 50


@pytest.mark.asyncio(loop_scope="session")
async def test_other_webhooks_refresh_the_plan_and_anonymous_ids_are_kept_but_ignored(clean_pool):
    billing, _, fake, _ = await _setup(clean_pool)
    lid = await _learner(clean_pool)
    assert await billing.tiers.tier_for(lid) == "free"
    fake.entitlements[str(lid)] = {"versa_plus": None}
    out = await billing.handle_webhook(_event(lid, type="INITIAL_PURCHASE", product_id="versa_plus_yearly"))
    assert out["granted"] is None
    assert await billing.tiers.tier_for(lid) == "plus", "the webhook dropped the cached plan"
    anon = await billing.handle_webhook(_event("$RCAnonymousID:abc"))
    assert anon == {"received": True, "duplicate": False, "granted": None}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_webhook_endpoint_checks_its_password(clean_pool, embedding_client):
    llm = StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT})
    billing, sparks, fake, _ = await _setup(clean_pool)
    live = await _start_with(clean_pool, llm, embedding_client, sparks, billing=billing)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            lid = (await client.post("/api/learners", json={"label": "hook"})).json()["id"]
            url = "/api/billing/revenuecat/webhook"
            assert (await client.post(url, json=_event(lid))).status_code == 401
            assert (await client.post(url, json=_event(lid), headers={"Authorization": "nope"})).status_code == 401
            r = await client.post(url, json=_event(lid), headers={"Authorization": "hook-secret"})
            assert r.status_code == 200 and r.json()["granted"] == {"kind": "sparks", "amount": 50}

            fake.bought[lid] = [StorePurchase("app-txn", "versa_sparks_50", _T0)]
            synced = (await client.post(f"/api/learners/{lid}/billing/sync", json={})).json()
            assert synced["granted"] == [{"kind": "sparks", "amount": 50}]
            assert synced["sparks"]["balance"] == WELCOME_SPARKS + 100
            assert synced["billing"]["tier"] == "free" and synced["billing"]["enabled"] is True
            assert (await client.get(f"/api/learners/{lid}/billing")).json()["tier"] == "free"
    finally:
        await _stop(live)


# -------------------------------------------------------------- the REST client


def _mock_revenuecat(handler_log: list):
    def handler(request: httpx.Request) -> httpx.Response:
        handler_log.append((request.url.path, dict(request.url.params), request.headers["authorization"]))
        path = request.url.path
        if path.endswith("/entitlements"):
            return httpx.Response(200, json={"object": "list", "items": [
                {"object": "entitlement", "id": "entl123", "lookup_key": "versa_plus"}], "next_page": None})
        if path.endswith("/products"):
            return httpx.Response(200, json={"object": "list", "items": [
                {"object": "product", "id": "prod1", "store_identifier": "versa_sparks_50"}], "next_page": None})
        if path.endswith("/active_entitlements"):
            if "nobody" in path:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json={"object": "list", "items": [
                {"object": "customer.active_entitlement", "entitlement_id": "entl123",
                 "expires_at": 1893456000000}], "next_page": None})
        if path.endswith("/purchases"):
            if request.url.params.get("starting_after") == "p1":
                return httpx.Response(200, json={"object": "list", "items": [
                    {"id": "p2", "product_id": "prod1", "status": "refunded",
                     "store_purchase_identifier": "GPA.2", "purchased_at": 1893456000000}], "next_page": None})
            return httpx.Response(200, json={"object": "list", "items": [
                {"id": "p1", "product_id": "prod1", "status": "owned",
                 "store_purchase_identifier": "GPA.1", "purchased_at": 1893456000000}],
                "next_page": "/v2/projects/proj/customers/c1/purchases?starting_after=p1"})
        if "boom" in path:
            return httpx.Response(500, text="oops")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio(loop_scope="session")
async def test_the_client_translates_revenuecat_ids_and_follows_pages():
    log: list = []
    client = RevenueCatClient("sk_test", "proj", transport=_mock_revenuecat(log))
    try:
        ents = await client.active_entitlements("c1")
        assert ents == {"versa_plus": datetime.fromtimestamp(1893456000, UTC)}
        assert await client.active_entitlements("nobody") == {}
        purchases = await client.purchases("c1")
        assert purchases == [StorePurchase("GPA.1", "versa_sparks_50", datetime.fromtimestamp(1893456000, UTC))]
        assert all(auth == "Bearer sk_test" for _, _, auth in log)
        assert any(params.get("starting_after") == "p1" for _, params, _ in log), "second page read"
    finally:
        await client.aclose()


# -------------------------------------------------------------- append-only


def test_billing_module_never_deletes_or_updates():
    path = Path(billing_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_billing_migration_has_no_delete_or_update():
    path = Path(billing_module.__file__).resolve().parent / "migrations" / "082_billing.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_billing_store_has_no_removal_methods():
    for name in dir(billing_module.BillingStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name


def test_from_env_stays_off_with_placeholders(monkeypatch):
    monkeypatch.setenv("REVENUECAT_SECRET_KEY", "sk_your-key-here")
    monkeypatch.setenv("REVENUECAT_PROJECT_ID", "d526484b")
    monkeypatch.setenv("REVENUECAT_WEBHOOK_AUTH", "change-me")
    billing = Billing.from_env(None)
    assert billing.enabled is False and billing.webhook_auth is None
    assert json.dumps({"ok": UUID(int=0)}, default=str)
