"""Billing: connects RevenueCat to Versa's Sparks (sparks.py).

RevenueCat knows what a student has paid for; Versa decides what that
means. The RevenueCat customer id is the Versa learner id (the app calls
`Purchases.logIn(learner.id)`), so no mapping table is needed.

- Plan. `RevenueCatTierResolver` answers "which plan is this learner on"
  for the Spark engine: "exam_pass" while one of Versa's own Exam Pass
  grants is running, else "plus" if RevenueCat reports the `versa_plus`
  entitlement active, else "free". RevenueCat answers are cached for a few
  minutes; if RevenueCat can't be reached the last known answer (or Free)
  is used -- a billing outage never blocks learning.
- Spark packs (`SPARK_PACKS`) become a `purchase` event in the Spark ledger,
  once per store transaction.
- An Exam Pass (`EXAM_PASS_PRODUCTS`) becomes a row in `exam_pass_grants`:
  Plus-level access from the purchase until the day after the exam. The exam
  is the one the app names when it syncs, else the learner's nearest upcoming
  dated exam, else `EXAM_PASS_FALLBACK_DAYS`.

Purchases reach the server two ways, through the same code so one purchase
counts once whichever arrives first:

    POST /api/learners/{id}/billing/sync  {exam_id?}   the app, right after a
         purchase: re-reads the learner from RevenueCat and applies anything new
    POST /api/billing/revenuecat/webhook                RevenueCat itself, once
         the server has a public URL (Authorization header must equal
         REVENUECAT_WEBHOOK_AUTH)
    GET  /api/learners/{id}/billing                     plan, Plus expiry,
         Exam Pass window

Both tables are append-only (migration 082, CLAUDE.md invariant 17).

Configuration (.env): REVENUECAT_SECRET_KEY (a v2 secret key with customer
read access and project-configuration read access), REVENUECAT_PROJECT_ID,
REVENUECAT_WEBHOOK_AUTH. Without the key, billing is off: everyone is Free
and the sync endpoint answers 503.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from versa.sparks import SparkEngine, SparkStatusOut

logger = logging.getLogger(__name__)

PLUS_ENTITLEMENT = "versa_plus"
SPARK_PACKS: dict[str, int] = {"versa_sparks_50": 50}  # store product id -> Sparks
EXAM_PASS_PRODUCTS = {"versa_exam_pass"}
EXAM_PASS_FALLBACK_DAYS = 30  # a pass bought with no dated exam to tie it to
TIER_CACHE_SECONDS = 300

API_BASE = "https://api.revenuecat.com/v2"


# ------------------------------------------------------------------ RevenueCat


@dataclass(frozen=True)
class StorePurchase:
    """One one-time purchase as RevenueCat reports it."""

    transaction_id: str
    product_id: str  # the store product id, e.g. "versa_sparks_50"
    purchased_at: datetime


class RevenueCatAPI(Protocol):
    async def active_entitlements(self, customer_id: str) -> dict[str, datetime | None]:
        """lookup key -> expiry (None: no expiry). {} for an unknown customer."""
        ...

    async def purchases(self, customer_id: str) -> list[StorePurchase]: ...


class RevenueCatError(Exception):
    pass


def _ms(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, UTC)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class RevenueCatClient:
    """RevenueCat REST API v2, read-only. Entitlement and product ids in v2
    responses are RevenueCat's internal ids, so the project's entitlements
    and products are read once (and re-read on a miss) to translate them to
    the lookup keys / store product ids Versa uses."""

    def __init__(self, secret_key: str, project_id: str, *, base_url: str = API_BASE,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._project = project_id
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=8.0, transport=transport,
            headers={"Authorization": f"Bearer {secret_key}", "Accept": "application/json"},
        )
        self._entitlement_keys: dict[str, str] = {}
        self._product_ids: dict[str, str] = {}

    async def _get(self, path: str, params: dict | None = None) -> dict | None:
        try:
            response = await self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise RevenueCatError(f"RevenueCat unreachable: {exc}") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise RevenueCatError(f"RevenueCat {response.status_code}: {response.text[:300]}")
        return response.json()

    async def _list(self, path: str) -> list[dict]:
        items: list[dict] = []
        params: dict | None = {"limit": 100}
        next_path: str | None = path
        for _ in range(50):  # a hard stop against a pagination loop
            page = await self._get(next_path, params)
            if page is None:
                break
            items.extend(page.get("items") or [])
            nxt = page.get("next_page")
            if not nxt:
                break
            # next_page is a full URL or a path from the API root ("/v2/...")
            next_path = nxt.split("/v2", 1)[1] if "/v2/" in nxt else nxt
            params = None
        return items

    async def _entitlement_key(self, entitlement_id: str) -> str:
        if entitlement_id not in self._entitlement_keys:
            for item in await self._list(f"/projects/{self._project}/entitlements"):
                self._entitlement_keys[item["id"]] = item.get("lookup_key") or item["id"]
        return self._entitlement_keys.get(entitlement_id, entitlement_id)

    async def _store_product(self, product_id: str) -> str:
        if product_id not in self._product_ids:
            for item in await self._list(f"/projects/{self._project}/products"):
                self._product_ids[item["id"]] = item.get("store_identifier") or item["id"]
        return self._product_ids.get(product_id, product_id)

    async def active_entitlements(self, customer_id: str) -> dict[str, datetime | None]:
        out: dict[str, datetime | None] = {}
        for item in await self._list(f"/projects/{self._project}/customers/{customer_id}/active_entitlements"):
            key = await self._entitlement_key(item["entitlement_id"])
            out[key] = _ms(item.get("expires_at"))
        return out

    async def purchases(self, customer_id: str) -> list[StorePurchase]:
        out = []
        for item in await self._list(f"/projects/{self._project}/customers/{customer_id}/purchases"):
            if str(item.get("status", "owned")).lower() not in {"owned", "active", "completed"}:
                continue  # refunded / revoked
            product = item.get("product_id") or item.get("product", {}).get("id")
            if not product:
                continue
            out.append(StorePurchase(
                transaction_id=str(item.get("store_purchase_identifier") or item["id"]),
                product_id=await self._store_product(product),
                purchased_at=_ms(item.get("purchased_at")) or datetime.now(UTC),
            ))
        return out

    async def aclose(self) -> None:
        await self._http.aclose()


# ------------------------------------------------------------------ storage


class BillingStore:
    """`billing_events` and `exam_pass_grants`: append-only (invariant 17)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_event(self, *, event_key: str, source: str, event_type: str,
                           app_user_id: str | None, learner_id: UUID | None,
                           product_id: str | None, payload: dict) -> bool:
        """-> False if this event was already recorded."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO billing_events (id, event_key, source, event_type, app_user_id,
                                            learner_id, product_id, payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
                ON CONFLICT (event_key) DO NOTHING RETURNING id
                """,
                uuid4(), event_key, source, event_type, app_user_id, learner_id, product_id,
                json.dumps(payload, default=str),
            )
        return row is not None

    async def add_exam_pass(self, *, learner_id: UUID, transaction_id: str, product_id: str,
                            exam_id: UUID | None, starts_at: datetime, ends_at: datetime) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO exam_pass_grants (id, learner_id, transaction_id, product_id,
                                              exam_id, starts_at, ends_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (transaction_id) DO NOTHING RETURNING id
                """,
                uuid4(), learner_id, transaction_id, product_id, exam_id, starts_at, ends_at,
            )
        return row is not None

    async def active_exam_pass(self, learner_id: UUID, now: datetime) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT exam_id, starts_at, ends_at FROM exam_pass_grants "
                "WHERE learner_id = $1 AND starts_at <= $2 AND ends_at > $2 "
                "ORDER BY ends_at DESC LIMIT 1",
                learner_id, now,
            )

    async def exam_for_pass(self, learner_id: UUID, exam_id: UUID | None,
                            today: date) -> tuple[UUID | None, date | None]:
        """The exam a pass is for: the named one if it is this learner's,
        else the nearest upcoming dated exam. -> (exam id, exam date)."""
        async with self._pool.acquire() as conn:
            if exam_id is not None:
                row = await conn.fetchrow(
                    "SELECT id, exam_date FROM exams WHERE id = $1 AND learner_id = $2",
                    exam_id, learner_id,
                )
                if row is not None:
                    return row["id"], row["exam_date"]
            row = await conn.fetchrow(
                "SELECT id, exam_date FROM exams WHERE learner_id = $1 AND exam_date >= $2 "
                "ORDER BY exam_date ASC, created_at DESC LIMIT 1",
                learner_id, today,
            )
        return (row["id"], row["exam_date"]) if row else (None, None)

    async def learner_exists(self, learner_id: UUID) -> bool:
        async with self._pool.acquire() as conn:
            return await conn.fetchval("SELECT EXISTS (SELECT 1 FROM learners WHERE id = $1)", learner_id)


# ------------------------------------------------------------------ the plan


class RevenueCatTierResolver:
    """sparks.TierResolver backed by Versa's Exam Pass grants and RevenueCat."""

    def __init__(self, store: BillingStore, api: RevenueCatAPI | None, *,
                 now: Callable[[], datetime] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store, self.api = store, api
        self._now = now or (lambda: datetime.now(UTC))
        self._clock = clock
        # learner -> (fetched at, active entitlements); kept after expiry as
        # the fallback when RevenueCat is unreachable
        self._cache: dict[UUID, tuple[float, dict[str, datetime | None]]] = {}

    def forget(self, learner_id: UUID) -> None:
        """Drop the cached RevenueCat answer (a purchase or webhook just arrived)."""
        self._cache.pop(learner_id, None)

    async def entitlements(self, learner_id: UUID) -> dict[str, datetime | None]:
        if self.api is None:
            return {}
        cached = self._cache.get(learner_id)
        if cached is not None and self._clock() - cached[0] < TIER_CACHE_SECONDS:
            return cached[1]
        try:
            fresh = await self.api.active_entitlements(str(learner_id))
        except Exception:
            logger.warning("RevenueCat lookup failed for %s; using last known plan", learner_id, exc_info=True)
            return cached[1] if cached is not None else {}
        self._cache[learner_id] = (self._clock(), fresh)
        return fresh

    async def tier_for(self, learner_id: UUID) -> str:
        if await self.store.active_exam_pass(learner_id, self._now()) is not None:
            return "exam_pass"
        entitlements = await self.entitlements(learner_id)
        expiry = entitlements.get(PLUS_ENTITLEMENT, "absent")
        if expiry != "absent" and (expiry is None or expiry > self._now()):
            return "plus"
        return "free"


# ------------------------------------------------------------------ applying purchases


class BillingOut(BaseModel):
    learner_id: UUID
    enabled: bool
    tier: str
    plus_expires_at: datetime | None = None
    exam_pass_ends_at: datetime | None = None
    exam_pass_exam_id: UUID | None = None


class Billing:
    def __init__(self, pool: asyncpg.Pool, api: RevenueCatAPI | None, sparks: SparkEngine | None = None, *,
                 webhook_auth: str | None = None, now: Callable[[], datetime] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = BillingStore(pool)
        self.api = api
        self.webhook_auth = webhook_auth or None
        self._now = now or (lambda: datetime.now(UTC))
        self.tiers = RevenueCatTierResolver(self.store, api, now=self._now, clock=clock)
        self.sparks = sparks

    @property
    def enabled(self) -> bool:
        return self.api is not None

    @classmethod
    def from_env(cls, pool: asyncpg.Pool) -> Billing:
        key = os.getenv("REVENUECAT_SECRET_KEY", "").strip()
        project = os.getenv("REVENUECAT_PROJECT_ID", "").strip()
        auth = os.getenv("REVENUECAT_WEBHOOK_AUTH", "").strip()
        placeholder = not key or key.startswith("sk_your") or not project
        api = None if placeholder else RevenueCatClient(key, project)
        return cls(pool, api, webhook_auth=None if auth in ("", "change-me") else auth)

    async def apply_purchase(self, learner_id: UUID, purchase: StorePurchase, *,
                             exam_id: UUID | None = None) -> dict | None:
        """Turn one store purchase into Sparks or an Exam Pass, once per
        transaction. -> what it granted, or None (unknown product / already applied)."""
        if purchase.product_id in SPARK_PACKS:
            amount = SPARK_PACKS[purchase.product_id]
            added = await self.sparks.purchase(
                learner_id, amount, purchase.transaction_id,
                ref={"product_id": purchase.product_id, "transaction_id": purchase.transaction_id},
            )
            return {"kind": "sparks", "amount": added} if added else None
        if purchase.product_id in EXAM_PASS_PRODUCTS:
            today = purchase.purchased_at.astimezone().date()
            exam, exam_date = await self.store.exam_for_pass(learner_id, exam_id, today)
            starts = purchase.purchased_at
            if exam_date is not None:
                # through the whole of exam day, ending at the start of the next
                ends = datetime.combine(exam_date + timedelta(days=1), dtime.min).astimezone()
            else:
                ends = starts + timedelta(days=EXAM_PASS_FALLBACK_DAYS)
            if ends <= starts:  # an exam already past: a day's access, not nothing
                ends = starts + timedelta(days=1)
            if await self.store.add_exam_pass(
                learner_id=learner_id, transaction_id=purchase.transaction_id,
                product_id=purchase.product_id, exam_id=exam, starts_at=starts, ends_at=ends,
            ):
                self.tiers.forget(learner_id)
                return {"kind": "exam_pass", "exam_id": exam, "ends_at": ends}
            return None
        return None

    async def sync(self, learner_id: UUID, exam_id: UUID | None = None) -> list[dict]:
        """Re-read a learner from RevenueCat; apply any purchase not yet seen."""
        if self.api is None:
            raise HTTPException(status_code=503, detail="billing is not configured")
        self.tiers.forget(learner_id)
        try:
            purchases = await self.api.purchases(str(learner_id))
        except (RevenueCatError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=f"RevenueCat: {exc}") from None
        granted = []
        for p in sorted(purchases, key=lambda p: p.purchased_at):
            await self.store.record_event(
                event_key=f"sync:{p.transaction_id}", source="sync", event_type="PURCHASE_SEEN",
                app_user_id=str(learner_id), learner_id=learner_id, product_id=p.product_id,
                payload={"transaction_id": p.transaction_id, "product_id": p.product_id,
                         "purchased_at": p.purchased_at.isoformat()},
            )
            result = await self.apply_purchase(learner_id, p, exam_id=exam_id)
            if result is not None:
                granted.append(result)
        await self.tiers.entitlements(learner_id)  # warm the cache with the new plan
        return granted

    async def handle_webhook(self, body: dict) -> dict:
        event = body.get("event") or {}
        event_id = event.get("id")
        if not event_id:
            raise HTTPException(status_code=422, detail="no event id")
        ids = [event.get("app_user_id"), event.get("original_app_user_id"),
               *(event.get("aliases") or []), *(event.get("transferred_to") or []),
               *(event.get("transferred_from") or [])]
        learners = []
        for raw in ids:
            try:
                lid = UUID(str(raw))
            except (TypeError, ValueError):
                continue  # an anonymous RevenueCat id
            if lid not in learners and await self.store.learner_exists(lid):
                learners.append(lid)
        learner_id = learners[0] if learners else None
        fresh = await self.store.record_event(
            event_key=str(event_id), source="webhook", event_type=str(event.get("type", "UNKNOWN")),
            app_user_id=event.get("app_user_id"), learner_id=learner_id,
            product_id=event.get("product_id"), payload=body,
        )
        for lid in learners:
            self.tiers.forget(lid)
        granted = None
        if (fresh and learner_id is not None and event.get("type") == "NON_RENEWING_PURCHASE"
                and event.get("product_id")):
            granted = await self.apply_purchase(learner_id, StorePurchase(
                transaction_id=str(event.get("transaction_id") or event_id),
                product_id=str(event["product_id"]),
                purchased_at=_ms(event.get("purchased_at_ms")) or self._now(),
            ))
        return {"received": True, "duplicate": not fresh, "granted": granted}

    async def billing_out(self, learner_id: UUID) -> BillingOut:
        now = self._now()
        grant = await self.store.active_exam_pass(learner_id, now)
        entitlements = await self.tiers.entitlements(learner_id)
        return BillingOut(
            learner_id=learner_id, enabled=self.enabled, tier=await self.tiers.tier_for(learner_id),
            plus_expires_at=entitlements.get(PLUS_ENTITLEMENT),
            exam_pass_ends_at=grant["ends_at"] if grant else None,
            exam_pass_exam_id=grant["exam_id"] if grant else None,
        )


class SyncIn(BaseModel):
    # the exam an Exam Pass was just bought for (from the exam's paywall)
    exam_id: UUID | None = None


class SyncOut(BaseModel):
    granted: list[dict]
    billing: BillingOut
    sparks: SparkStatusOut


def build_billing_router(billing: Billing) -> APIRouter:
    router = APIRouter(prefix="/api")

    async def require_learner(learner_id: UUID) -> None:
        if not await billing.store.learner_exists(learner_id):
            raise HTTPException(status_code=404, detail="unknown learner")

    @router.get("/learners/{learner_id}/billing", response_model=BillingOut)
    async def get_billing(learner_id: UUID) -> BillingOut:
        await require_learner(learner_id)
        return await billing.billing_out(learner_id)

    @router.post("/learners/{learner_id}/billing/sync", response_model=SyncOut)
    async def sync(learner_id: UUID, body: SyncIn | None = None) -> SyncOut:
        await require_learner(learner_id)
        granted = await billing.sync(learner_id, body.exam_id if body else None)
        return SyncOut(granted=granted, billing=await billing.billing_out(learner_id),
                       sparks=await billing.sparks.status(learner_id))

    @router.post("/billing/revenuecat/webhook")
    async def webhook(request: Request) -> dict:
        if billing.webhook_auth is None:
            raise HTTPException(status_code=503, detail="webhook is not configured")
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), billing.webhook_auth.encode()) and not hmac.compare_digest(
            supplied.encode(), f"Bearer {billing.webhook_auth}".encode()
        ):
            raise HTTPException(status_code=401, detail="bad authorization")
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=422, detail="not JSON") from None
        return await billing.handle_webhook(body)

    return router
