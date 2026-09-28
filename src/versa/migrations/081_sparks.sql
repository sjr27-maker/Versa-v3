-- versa: Sparks, the learning credits (sparks.py).
--
-- Numbered 081 (it was 077 before the upstream 077-080 landed). IF NOT EXISTS
-- so a dev database that already ran it under the old name is not broken.
--
-- One append-only ledger (CLAUDE.md invariant 16). A learner's balance is
-- never stored: it is SUM(amount) over their events. Spends are negative;
-- welcome grants, refills, refunds, rewards and purchases are positive. A
-- refund is a new event pointing at the spend it reverses (ref.spend_key),
-- never an edit of the spend.
--
-- idempotency_key makes every write safe to retry: a double-tapped button,
-- a retried request or a replayed RevenueCat webhook lands at most once.
-- A refill window that added nothing is still recorded (amount 0), so a
-- window can only ever refill once.

CREATE TABLE IF NOT EXISTS spark_events (
    id              UUID PRIMARY KEY,
    -- write order, to break ties between events written in the same instant
    seq             BIGSERIAL NOT NULL,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'welcome', 'refill', 'spend', 'refund', 'reward', 'purchase', 'adjust')),
    amount          INT NOT NULL,
    -- the priced action a spend or refund is for (sparks.ACTION_COSTS)
    action          TEXT NULL,
    -- why a grant happened: 'refill', 'unit_quiz_passed', 'study_streak', ...
    reason          TEXT NULL,
    -- the learner's plan when the event was written ('free' / 'plus')
    tier            TEXT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    ref             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (kind = 'spend' AND amount <= 0)
        OR (kind IN ('welcome', 'refill', 'refund', 'reward', 'purchase') AND amount >= 0)
        OR kind = 'adjust'
    )
);

CREATE INDEX IF NOT EXISTS idx_spark_events_learner ON spark_events (learner_id, created_at DESC, seq DESC);
