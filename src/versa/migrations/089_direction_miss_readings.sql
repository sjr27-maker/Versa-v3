-- versa: reading a miss the library can't place (docs/THINKING_STYLE.md,
-- build item 8, 2026-09-30).
--
-- When a missed question (migration 088) isn't clearly one of the library's
-- card types by embedding, one fast model call reads it: what KIND of move
-- it makes, in a short phrase with no topic words ("where the rule stops
-- working"), and whether that is one of the known types after all, or none.
-- The call is given the question and the one before it -- nothing about the
-- learner -- and recorded to node_calls like every model call (invariant 2).
--
--   direction_miss_readings  one row per missed set (set_id UNIQUE), written
--                            once. A reading that is none of the types is a
--                            NEW MOVE: grouped across learners by its
--                            embedding (style_patterns.discover_moves), a
--                            group asked for often enough, by enough
--                            learners, is a candidate new card type -- the
--                            way a thinking style outside the library is
--                            found. Nothing is added to the cards on its own:
--                            the library is the measuring instrument
--                            (invariant 14), changed only by a person.
--
-- Append-only (CLAUDE.md invariant 14): no UPDATE, no DELETE.

CREATE TABLE direction_miss_readings (
    id              UUID PRIMARY KEY,
    set_id          UUID NOT NULL UNIQUE REFERENCES direction_sets (id),
    same_subject    BOOLEAN NOT NULL,
    type            TEXT NULL,
    move            TEXT NOT NULL,
    move_embedding  halfvec(768) NULL,
    reader_version  TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
