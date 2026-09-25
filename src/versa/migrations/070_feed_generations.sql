-- versa: the Home feed's generated recommendations (feed.py). One row per
-- GenerateFeed call: what the model was shown (input_json), what it
-- returned after parsing (items), and how much history backed it
-- (source counts). The newest usable row younger than 6h is served as the
-- learner's feed; older rows stay on record.
--
-- Why a table of its own instead of node_calls (CLAUDE.md invariant 2):
-- node_calls.session_id is NOT NULL REFERENCES sessions, and a feed is
-- generated per learner, outside any chat -- a brand-new learner has no
-- session at all. This table therefore carries the same audit payload
-- node_calls would (node name, full input, full output, timestamp) keyed by
-- learner instead of by session/turn.
--
-- Append-only, same rule as every other store: no row is ever updated or
-- removed. A refresh writes a new row; a failed generation is recorded
-- with `error` set and item_count = 0 and is never served.

CREATE TABLE feed_generations (
    id              UUID PRIMARY KEY,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    node_name       TEXT NOT NULL,
    trigger         TEXT NOT NULL CHECK (trigger IN ('initial', 'stale', 'refresh', 'history_arrived')),
    input_json      JSONB NOT NULL,
    items           JSONB NOT NULL,
    item_count      INT NOT NULL,
    session_count   INT NOT NULL,
    fact_count      INT NOT NULL,
    message_count   INT NOT NULL,
    error           TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_feed_generations_learner ON feed_generations (learner_id, created_at DESC);
