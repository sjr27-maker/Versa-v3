-- versa: RevenueCat billing (billing.py).
--
-- Numbered 082 (it was 078 before the upstream 077-080 landed). IF NOT EXISTS
-- so a dev database that already ran it under the old name is not broken.
--
-- Append-only, like every store here (CLAUDE.md invariant 17).
--
-- billing_events: every purchase notice Versa received -- a RevenueCat
-- webhook, or a purchase found by an app-triggered sync -- kept verbatim,
-- once per event (event_key is RevenueCat's event id, or "sync:<transaction>").
-- Nothing is updated when a later event supersedes an earlier one.
--
-- exam_pass_grants: an Exam Pass bought through RevenueCat, turned into
-- Plus-level access that ends the day after the exam. One row per store
-- transaction; whether a pass is active is derived from ends_at, never
-- flagged.

CREATE TABLE IF NOT EXISTS billing_events (
    id           UUID PRIMARY KEY,
    event_key    TEXT NOT NULL UNIQUE,
    source       TEXT NOT NULL CHECK (source IN ('webhook', 'sync')),
    event_type   TEXT NOT NULL,
    -- the RevenueCat App User ID as sent (a learner id, or an anonymous id)
    app_user_id  TEXT NULL,
    learner_id   UUID NULL REFERENCES learners (id),
    product_id   TEXT NULL,
    payload      JSONB NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_billing_events_learner ON billing_events (learner_id, received_at DESC);

CREATE TABLE IF NOT EXISTS exam_pass_grants (
    id              UUID PRIMARY KEY,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    transaction_id  TEXT NOT NULL UNIQUE,
    product_id      TEXT NOT NULL,
    -- the exam the pass was bought for, when known
    exam_id         UUID NULL REFERENCES exams (id),
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);

CREATE INDEX IF NOT EXISTS idx_exam_pass_grants_learner ON exam_pass_grants (learner_id, ends_at DESC);
