-- versa: idempotency marker for session-end consolidation (thinking-style +
-- claim extraction, SessionLoop.consolidate_session). Set once, by UPDATE,
-- the moment a session is claimed for consolidation; never cleared. The app
-- server (versa serve) used to never consolidate at all, so thinking-style
-- and claims only ever ran from the CLI. Existing sessions stay NULL (they
-- may be swept the next time their learner starts a chat).

ALTER TABLE sessions ADD COLUMN consolidated_at TIMESTAMPTZ NULL;
