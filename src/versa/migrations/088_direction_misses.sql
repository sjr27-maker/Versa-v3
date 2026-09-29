-- versa: experimenting on a miss (docs/THINKING_STYLE.md, build item 8).
--
-- A miss is a directions set the learner passed by asking their own question
-- instead. That question is the one time Versa sees what was in their mind
-- when none of its cards matched it -- so it is kept, with the card type it
-- is nearest to (by embedding against the library's descriptions: no model
-- call, nothing about the learner), and whether it stayed on the same
-- subject (a new subject is an interest, not a way out of the answer).
--
--   direction_misses   one row per passed set (set_id UNIQUE), written once
--                      in the background after the pass is recorded. The top
--                      two types and their similarities are kept, so the
--                      tagging can be recalibrated later without re-embedding.
--   direction_sets     + experiment: 'after_miss' when the hand dealt right
--                      after a miss in the same chat was widened at random
--                      (an extra card swapped in for certain). Random, never
--                      chosen from what Versa believes (invariant 14).
--
-- Append-only (CLAUDE.md invariant 14): no UPDATE, no DELETE.

CREATE TABLE direction_misses (
    id                    UUID PRIMARY KEY,
    set_id                UUID NOT NULL UNIQUE REFERENCES direction_sets (id),
    session_id            UUID NOT NULL REFERENCES sessions (id),
    turn_index            INT NOT NULL,
    question              TEXT NOT NULL,
    follow_up             BOOLEAN NULL,
    tagged_as             TEXT NULL,
    similarity            REAL NULL,
    runner_up             TEXT NULL,
    runner_up_similarity  REAL NULL,
    in_hand               BOOLEAN NOT NULL,
    tagger_version        TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_direction_misses_session ON direction_misses (session_id, created_at);

ALTER TABLE direction_sets
    ADD COLUMN experiment TEXT NULL CHECK (experiment IN ('after_miss'));
