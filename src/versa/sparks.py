"""Sparks: Versa's learning credits.

Versa's promise is "pay for learning, never for confusion". Sparks make that
concrete:

- Real work costs a few Sparks: an answer, exploring a topic, building a
  course, writing a quiz or a mock test (`ACTION_COSTS`).
- Versa's own uncertainty is free. A turn that ends in clarifying options
  costs nothing; the student pays once, for the answer, whichever way it
  was reached. Rewriting an answer with the length/depth sliders is free too.
- Learning pays some back: passing a unit quiz or a mock test, finishing a
  lesson, and keeping a study streak (`REWARDS`).
- Sparks refill on their own, every `refill_hours`, up to a cap. How much and
  how high depends on the learner's plan (`TIERS`): Free refills slowly, Plus
  quickly. Rewards and purchases may take a balance above the cap; refills
  never do.

Storage is one append-only ledger, `spark_events` (migration 081, CLAUDE.md
invariant 16). A balance is never stored: it is the sum of the learner's
events, the same way exam scores and lesson progress are derived rather than
kept. Every event carries a unique `idempotency_key`, so a retried request,
a double-tap or a replayed webhook can never charge or reward twice. A
refund is a new, positive event pointing at the spend it reverses.

Which plan a learner is on comes from a `TierResolver`: billing.py's
`RevenueCatTierResolver` (Plus from RevenueCat, Exam Pass from Versa's own
grants) when RevenueCat is configured, otherwise everyone is Free. Bought
Spark packs arrive through `purchase` (billing.py), once per store
transaction.

Spark balances are billing state, not part of the learner model: nothing
here reads or writes beliefs about the learner, and exam results only ever
enter as pass/fail for a reward (invariant 13 stays intact).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# What each piece of real work costs. Anything not listed is free -- most
# importantly a clarifying-options turn, which never reaches `charge`.
ACTION_COSTS: dict[str, int] = {
    "answer": 1,  # a Sandbox or lesson-chat answer
    "explore_topic": 2,  # a keyword / link / PDF turned into a branch tree
    "expand_topic": 1,  # new sub-branches under one branch
    "build_course": 5,  # ticked branches turned into chapters and lessons
    "create_exam": 3,  # an exam's syllabus units from a search, PDF, link or course
    "unit_quiz": 3,  # a 5-question unit quiz (marking included)
    "mock_test": 8,  # a timed mock across every unit (marking included)
    "generate_notes": 2,  # revision notes of a chat, made when the learner asks (notes.py)
}

# What learning earns back.
REWARDS: dict[str, int] = {
    "unit_quiz_passed": 2,
    "mock_test_passed": 4,
    "lesson_completed": 3,
    "study_streak": 5,
}
PASS_PERCENT = 70  # a quiz or mock at or above this counts as passed
STREAK_DAYS = 5  # a streak reward every this many consecutive study days

WELCOME_SPARKS = 20  # a brand-new learner's first balance

TierName = Literal["free", "plus", "exam_pass"]


@dataclass(frozen=True)
class TierPolicy:
    name: TierName
    refill_amount: int  # added at the start of each refill window...
    refill_hours: int  # ...every this many hours...
    cap: int  # ...but never taking the balance above this


TIERS: dict[str, TierPolicy] = {
    "free": TierPolicy("free", refill_amount=10, refill_hours=12, cap=20),
    "plus": TierPolicy("plus", refill_amount=50, refill_hours=12, cap=100),
    # an Exam Pass is Plus-level until the day after the exam (billing.py)
    "exam_pass": TierPolicy("exam_pass", refill_amount=50, refill_hours=12, cap=100),
}

EventKind = Literal["welcome", "refill", "spend", "refund", "reward", "purchase", "adjust"]


@dataclass(frozen=True)
class Charge:
    balance: int  # after the spend (and any streak reward it triggered)
    spent: int  # Sparks actually taken (0 when Sparks are off or already charged)
    streak_reward: int = 0


class InsufficientSparks(Exception):
    """Not enough Sparks for an action. `detail()` is what the client gets
    (HTTP 402 body, or the `paywall` websocket event)."""

    def __init__(self, *, action: str, needed: int, balance: int, tier: str,
                 next_refill_at: datetime) -> None:
        super().__init__(f"{action} needs {needed} Sparks, balance is {balance}")
        self.action, self.needed, self.balance = action, needed, balance
        self.tier, self.next_refill_at = tier, next_refill_at

    def detail(self) -> dict:
        return {
            "reason": "sparks",
            "action": self.action,
            "needed": self.needed,
            "balance": self.balance,
            "tier": self.tier,
            "next_refill_at": self.next_refill_at.isoformat(),
        }


class TierResolver(Protocol):
    async def tier_for(self, learner_id: UUID) -> str: ...


class FreeTierResolver:
    """Everyone is on Free. The default until RevenueCat is connected."""

    async def tier_for(self, learner_id: UUID) -> str:
        return "free"


class FixedTierResolver:
    """A fixed learner -> tier map, Free for anyone not listed (tests, demos)."""

    def __init__(self, tiers: dict[UUID, str] | None = None) -> None:
        self.tiers = dict(tiers or {})

    async def tier_for(self, learner_id: UUID) -> str:
        return self.tiers.get(learner_id, "free")


class SparkEventOut(BaseModel):
    kind: str
    amount: int
    action: str | None
    reason: str | None
    created_at: datetime


class SparkStatusOut(BaseModel):
    learner_id: UUID
    enabled: bool
    tier: str
    balance: int
    cap: int
    refill_amount: int
    refill_hours: int
    next_refill_at: datetime
    costs: dict[str, int]
    rewards: dict[str, int]
    recent: list[SparkEventOut]
    # The usage view (the app's Sparks meter): what today cost, and the study
    # streak -- consecutive days with at least one Spark spent on learning.
    # A streak still counts until a whole day is missed (studying yesterday
    # but not yet today keeps it alive).
    spent_today: int = 0
    streak_days: int = 0
    studied_today: bool = False
    streak_goal: int = STREAK_DAYS


class SparkStore:
    """The append-only `spark_events` ledger. There is deliberately no way to
    change or remove an event (invariant 16)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @staticmethod
    async def append(
        conn: asyncpg.Connection,
        *,
        learner_id: UUID,
        kind: EventKind,
        amount: int,
        key: str,
        action: str | None = None,
        reason: str | None = None,
        tier: str | None = None,
        ref: dict | None = None,
        at: datetime | None = None,
    ) -> bool:
        """Write one event; False (and nothing written) if its key exists."""
        row = await conn.fetchrow(
            """
            INSERT INTO spark_events (id, learner_id, kind, amount, action, reason,
                                      tier, idempotency_key, ref, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            uuid4(), learner_id, kind, amount, action, reason, tier, key,
            json.dumps(ref or {}, default=str), at or datetime.now(UTC),
        )
        return row is not None

    @staticmethod
    async def balance(conn: asyncpg.Connection, learner_id: UUID) -> int:
        return await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM spark_events WHERE learner_id = $1",
            learner_id,
        )

    @staticmethod
    async def has_key(conn: asyncpg.Connection, key: str) -> bool:
        return await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM spark_events WHERE idempotency_key = $1)", key
        )

    @staticmethod
    async def spend_of(conn: asyncpg.Connection, key: str) -> asyncpg.Record | None:
        return await conn.fetchrow(
            "SELECT learner_id, amount, action FROM spark_events "
            "WHERE idempotency_key = $1 AND kind = 'spend'",
            key,
        )

    @staticmethod
    async def spend_times(conn: asyncpg.Connection, learner_id: UUID) -> list[datetime]:
        rows = await conn.fetch(
            "SELECT created_at FROM spark_events WHERE learner_id = $1 AND kind = 'spend'",
            learner_id,
        )
        return [r["created_at"] for r in rows]

    @staticmethod
    async def spent_between(conn: asyncpg.Connection, learner_id: UUID,
                            start: datetime, end: datetime) -> int:
        """Sparks spent in [start, end), net of refunds."""
        total = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM spark_events "
            "WHERE learner_id = $1 AND kind IN ('spend', 'refund') "
            "AND created_at >= $2 AND created_at < $3",
            learner_id, start, end,
        )
        return max(0, -int(total))

    async def list_events(self, learner_id: UUID, limit: int = 20) -> list[SparkEventOut]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT kind, amount, action, reason, created_at FROM spark_events "
                "WHERE learner_id = $1 AND NOT (kind = 'refill' AND amount = 0) "
                "ORDER BY created_at DESC, seq DESC LIMIT $2",
                learner_id, limit,
            )
        return [SparkEventOut(**dict(r)) for r in rows]


def _local_date(moment: datetime) -> date:
    """The server's own calendar day, the same notion exams.local_today uses."""
    return moment.astimezone().date()


def streak_run(days: set[date], today: date) -> int:
    """Consecutive study days ending today -- or ending yesterday, when today
    has no study yet (the streak is still alive until a whole day is missed)."""
    end = today if today in days else today - timedelta(days=1)
    run = 0
    while end - timedelta(days=run) in days:
        run += 1
    return run


class SparkEngine:
    """Charges, refunds, rewards and refills, each an atomic step under a
    per-learner lock so two requests can never spend the same Spark."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        tiers: TierResolver | None = None,
        *,
        enabled: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._pool = pool
        self.store = SparkStore(pool)
        self.tiers = tiers or FreeTierResolver()
        self.enabled = enabled
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_env(cls, pool: asyncpg.Pool, tiers: TierResolver | None = None) -> SparkEngine:
        """`VERSA_SPARKS=off` turns enforcement off (nothing is charged)."""
        enabled = os.getenv("VERSA_SPARKS", "on").strip().lower() not in {"off", "0", "false", "no"}
        return cls(pool, tiers, enabled=enabled)

    # -- plan and refill windows ---------------------------------------------

    async def _policy(self, learner_id: UUID) -> TierPolicy:
        try:
            name = await self.tiers.tier_for(learner_id)
        except Exception:  # a billing outage must never block learning
            logger.exception("tier lookup failed for learner %s; using free", learner_id)
            name = "free"
        return TIERS.get(name, TIERS["free"])

    @staticmethod
    def _window(policy: TierPolicy, moment: datetime) -> tuple[int, datetime]:
        """-> (index of the refill window containing `moment`, when the next
        one starts). Windows are fixed slices of time, not per-learner."""
        span = policy.refill_hours * 3600
        index = int(moment.timestamp()) // span
        return index, datetime.fromtimestamp((index + 1) * span, UTC)

    @contextlib.asynccontextmanager
    async def _locked(self, learner_id: UUID) -> AsyncIterator[asyncpg.Connection]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 77))", str(learner_id))
            yield conn

    async def _settle(self, conn: asyncpg.Connection, learner_id: UUID,
                      policy: TierPolicy, now: datetime) -> int:
        """Apply the welcome grant and this window's refill if due; -> balance."""
        index, _ = self._window(policy, now)
        window_key = f"refill:{learner_id}:{policy.name}:{policy.refill_hours}h:{index}"
        if await SparkStore.append(
            conn, learner_id=learner_id, kind="welcome", amount=WELCOME_SPARKS,
            key=f"welcome:{learner_id}", reason="welcome", tier=policy.name, at=now,
        ):
            # the welcome grant stands in for this window's refill
            await SparkStore.append(
                conn, learner_id=learner_id, kind="refill", amount=0, key=window_key,
                reason="covered by welcome", tier=policy.name, at=now,
            )
        balance = await SparkStore.balance(conn, learner_id)
        if not await SparkStore.has_key(conn, window_key):
            amount = max(0, min(policy.refill_amount, policy.cap - balance))
            # recorded even at 0 so the window counts as used: spending down
            # later in the same window must not unlock a second refill
            await SparkStore.append(
                conn, learner_id=learner_id, kind="refill", amount=amount, key=window_key,
                reason="refill", tier=policy.name, at=now,
            )
            balance += amount
        return balance

    # -- reading -------------------------------------------------------------

    async def status(self, learner_id: UUID) -> SparkStatusOut:
        policy = await self._policy(learner_id)
        now = self._now()
        async with self._locked(learner_id) as conn:
            balance = await self._settle(conn, learner_id, policy, now)
            days = {_local_date(t) for t in await SparkStore.spend_times(conn, learner_id)}
            local_now = now.astimezone()
            day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            spent_today = await SparkStore.spent_between(
                conn, learner_id, day_start, day_start + timedelta(days=1),
            )
        _, next_refill = self._window(policy, now)
        today = _local_date(now)
        return SparkStatusOut(
            learner_id=learner_id, enabled=self.enabled, tier=policy.name, balance=balance,
            cap=policy.cap, refill_amount=policy.refill_amount, refill_hours=policy.refill_hours,
            next_refill_at=next_refill, costs=dict(ACTION_COSTS), rewards=dict(REWARDS),
            recent=await self.store.list_events(learner_id),
            spent_today=spent_today, streak_days=streak_run(days, today),
            studied_today=today in days, streak_goal=STREAK_DAYS,
        )

    async def require(self, learner_id: UUID, action: str) -> int:
        """Raise InsufficientSparks unless `action` is affordable right now.
        Nothing is spent. -> the current balance."""
        cost = ACTION_COSTS[action]
        policy = await self._policy(learner_id)
        now = self._now()
        async with self._locked(learner_id) as conn:
            balance = await self._settle(conn, learner_id, policy, now)
        if self.enabled and balance < cost:
            raise InsufficientSparks(action=action, needed=cost, balance=balance,
                                     tier=policy.name, next_refill_at=self._window(policy, now)[1])
        return balance

    # -- writing -------------------------------------------------------------

    async def charge(
        self,
        learner_id: UUID,
        action: str,
        key: str,
        *,
        ref: dict | None = None,
        after_the_fact: bool = False,
    ) -> Charge:
        """Spend the cost of `action`, once per `key`.

        Raises InsufficientSparks if the balance is too low, unless
        `after_the_fact` (the work was already delivered, e.g. an answer that
        streamed while another tab spent the last Spark): then it takes what
        is there and never goes below zero."""
        cost = ACTION_COSTS[action]
        policy = await self._policy(learner_id)
        now = self._now()
        async with self._locked(learner_id) as conn:
            balance = await self._settle(conn, learner_id, policy, now)
            if not self.enabled:
                return Charge(balance=balance, spent=0)
            if balance < cost and not after_the_fact:
                raise InsufficientSparks(action=action, needed=cost, balance=balance,
                                         tier=policy.name, next_refill_at=self._window(policy, now)[1])
            amount = min(cost, max(balance, 0))
            if not await SparkStore.append(
                conn, learner_id=learner_id, kind="spend", amount=-amount, key=f"spend:{key}",
                action=action, tier=policy.name, ref=ref, at=now,
            ):
                return Charge(balance=balance, spent=0)
            streak = await self._streak(conn, learner_id, policy, now)
            return Charge(balance=balance - amount + streak, spent=amount, streak_reward=streak)

    async def refund(self, key: str, reason: str) -> int:
        """Give back a spend (the work it paid for failed). Once per spend;
        0 if there is nothing to refund. -> Sparks returned."""
        async with self._pool.acquire() as conn:
            spent = await SparkStore.spend_of(conn, f"spend:{key}")
        if spent is None or spent["amount"] == 0:
            return 0
        async with self._locked(spent["learner_id"]) as conn:
            if await SparkStore.append(
                conn, learner_id=spent["learner_id"], kind="refund", amount=-spent["amount"],
                key=f"refund:{key}", action=spent["action"], reason=reason,
                ref={"spend_key": f"spend:{key}"}, at=self._now(),
            ):
                return -spent["amount"]
        return 0

    async def reward(self, learner_id: UUID, reason: str, key: str, *, ref: dict | None = None) -> int:
        """Grant a learning reward once per `key`. -> Sparks granted (0 if
        already given or Sparks are off)."""
        if not self.enabled:
            return 0
        amount = REWARDS[reason]
        policy = await self._policy(learner_id)
        now = self._now()
        async with self._locked(learner_id) as conn:
            await self._settle(conn, learner_id, policy, now)
            granted = await SparkStore.append(
                conn, learner_id=learner_id, kind="reward", amount=amount, key=f"reward:{key}",
                reason=reason, tier=policy.name, ref=ref, at=now,
            )
        return amount if granted else 0

    async def purchase(self, learner_id: UUID, amount: int, key: str, *, ref: dict | None = None) -> int:
        """Add bought Sparks once per `key` (a store transaction). Counted
        even when Sparks are off: it was paid for. -> Sparks added."""
        policy = await self._policy(learner_id)
        now = self._now()
        async with self._locked(learner_id) as conn:
            await self._settle(conn, learner_id, policy, now)
            added = await SparkStore.append(
                conn, learner_id=learner_id, kind="purchase", amount=amount, key=f"purchase:{key}",
                reason="spark_pack", tier=policy.name, ref=ref, at=now,
            )
        return amount if added else 0

    async def _streak(self, conn: asyncpg.Connection, learner_id: UUID,
                      policy: TierPolicy, now: datetime) -> int:
        """After a spend: if today completes a run of STREAK_DAYS (or a
        multiple) consecutive study days, grant the streak reward, once per
        day. -> Sparks granted."""
        days = {_local_date(t) for t in await SparkStore.spend_times(conn, learner_id)}
        today = _local_date(now)
        run = 0
        while today - timedelta(days=run) in days:
            run += 1
        if run == 0 or run % STREAK_DAYS:
            return 0
        amount = REWARDS["study_streak"]
        granted = await SparkStore.append(
            conn, learner_id=learner_id, kind="reward", amount=amount,
            key=f"reward:streak:{learner_id}:{today.isoformat()}", reason="study_streak",
            tier=policy.name, ref={"days": run}, at=now,
        )
        return amount if granted else 0

    @contextlib.asynccontextmanager
    async def charged(self, learner_id: UUID, action: str, *, ref: dict | None = None) -> AsyncIterator[str]:
        """Charge up front for a piece of generated work, refunding if the
        work then fails. Raises HTTP 402 when the balance is too low.

            async with sparks.charged(learner_id, "build_course"):
                return await service.build_topic(body)
        """
        key = f"op:{action}:{uuid4()}"
        try:
            await self.charge(learner_id, action, key, ref=ref)
        except InsufficientSparks as exc:
            raise HTTPException(status_code=402, detail=exc.detail()) from None
        try:
            yield key
        except BaseException:
            await self.refund(key, "the work failed")
            raise


def passed(percent: int | None) -> bool:
    return percent is not None and percent >= PASS_PERCENT


def build_sparks_router(engine: SparkEngine, pool: asyncpg.Pool) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/learners/{learner_id}/sparks", response_model=SparkStatusOut)
    async def get_sparks(learner_id: UUID) -> SparkStatusOut:
        async with pool.acquire() as conn:
            if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM learners WHERE id = $1)", learner_id):
                raise HTTPException(status_code=404, detail="unknown learner")
        return await engine.status(learner_id)

    return router
