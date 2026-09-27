-- versa: the stage's quick-check questions and what the student picked
-- (stage.py StageCheckStore). A skit may end with one question; until now
-- the pick lived only in the app. Right or wrong is evidence of what landed,
-- so it is kept -- walled off like the other episodic stores: nothing here
-- is written into claims or thinking styles.
--
-- Append-only (CLAUDE.md invariant 15): one row per pick, never edited.

CREATE TABLE stage_checks (
    id          UUID PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions (id),
    turn_index  INT NOT NULL,
    question    TEXT NOT NULL,
    choices     JSONB NOT NULL,
    picked_id   TEXT NOT NULL,
    -- the id the director marked right; NULL if it marked none
    answer_id   TEXT NULL,
    -- NULL when there was no marked answer to compare with
    correct     BOOLEAN NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_stage_checks_session ON stage_checks (session_id, created_at);
