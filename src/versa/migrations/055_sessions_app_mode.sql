-- versa: sessions gain app_mode -- which PRODUCT mode this chat belongs
-- to (Sandbox, Learn a topic, ...; server.py's SessionIn.mode), so a
-- learner's chat-history sidebar can list only the current mode's own
-- chats, per mode, the way the app is built to separate them. This is
-- deliberately a SEPARATE column from ablation_config (migration 025):
-- ablation_config is the reasoning ARCHITECTURE a session runs under
-- (minimal_branch vs. baseline), decided once by the backend; app_mode
-- is which product surface the learner picked, decided by the client.
-- The two happen to both default to a single live value today (every
-- session is minimal_branch AND every session is "sandbox") but they
-- answer different questions and must not be conflated when more modes
-- exist.
--
-- NOT NULL DEFAULT, not nullable: every session created before this
-- migration was, by construction, a Sandbox chat (it was the only mode
-- that existed), so backfilling existing rows to 'sandbox' is exactly
-- correct, not a guess -- unlike ablation_config's NULL-means-default
-- pattern, there is no "unknown mode" case to preserve here.

ALTER TABLE sessions ADD COLUMN app_mode TEXT NOT NULL DEFAULT 'sandbox';

-- The sidebar's own read pattern: this learner's sessions, filtered to
-- one app_mode, newest first.
CREATE INDEX idx_sessions_learner_app_mode ON sessions (learner_id, app_mode, created_at DESC);
