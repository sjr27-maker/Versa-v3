-- versa: continuous length/depth sliders and live answer regeneration
-- (IDEAS.md "Session knobs", reworked 2026-09-24).
--
-- 1. The knobs become 0-100 levels instead of three-way choices. 50/50 is
--    the untouched default and renders no directive, so an existing
--    session's prompt is unchanged. The text columns from migration 063
--    (answer_length, depth, tone) stay on the table, dormant: tone was
--    removed from the product, and length/depth now live in the *_level
--    columns.
--
-- 2. answer_versions: every regeneration of an answer at new slider levels.
--    The original answer is never overwritten -- it stays in node_calls as
--    that turn's FinalAnswer output (version 0). Each regeneration is a new
--    row here (version 1, 2, ...), and a resumed chat shows the highest
--    version. Append-only like every other store: no UPDATE, no DELETE.

ALTER TABLE sessions
    ADD COLUMN answer_length_level SMALLINT NOT NULL DEFAULT 50
        CHECK (answer_length_level BETWEEN 0 AND 100),
    ADD COLUMN depth_level SMALLINT NOT NULL DEFAULT 50
        CHECK (depth_level BETWEEN 0 AND 100);

CREATE TABLE answer_versions (
    id                   UUID PRIMARY KEY,
    session_id           UUID NOT NULL REFERENCES sessions (id),
    turn_index           INT NOT NULL,
    version              INT NOT NULL CHECK (version >= 1),
    text                 TEXT NOT NULL,
    answer_length_level  SMALLINT NOT NULL CHECK (answer_length_level BETWEEN 0 AND 100),
    depth_level          SMALLINT NOT NULL CHECK (depth_level BETWEEN 0 AND 100),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, turn_index, version)
);

CREATE INDEX idx_answer_versions_session ON answer_versions (session_id, turn_index, version DESC);
