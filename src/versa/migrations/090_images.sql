-- versa: pictures a learner attaches to a message (images.py, 2026-09-30).
--
-- A picture is read ONCE, when it is uploaded: one fast model call that can
-- see it writes down what it shows -- any text or maths in it transcribed
-- (maths as LaTeX), a diagram or graph described -- and that reading is the
-- context every later step gets (the answer, the ambiguity check, the stage).
-- Nothing downstream is given the pixels themselves: the reading is the
-- picture's record, so what a turn was told is always readable later.
--
--   images  one row per upload, written once, AFTER the reading call: the
--           bytes, their type and hash, the prompt the reader was given, and
--           what it said (or the error). A failed reading is still a row
--           (reading NULL, error set) -- the upload happened.
--
-- The model call has no session (a picture can be picked before a chat has
-- one, or for an exam answer), so its prompt and output are kept on this
-- row -- invariant 2's payload in images' own table, the same precedent as
-- topic_generations / profile_extractions.
--
-- Append-only (CLAUDE.md invariant 21): no UPDATE, no DELETE.

CREATE TABLE images (
    id          UUID PRIMARY KEY,
    learner_id  UUID NOT NULL REFERENCES learners (id),
    mime_type   TEXT NOT NULL,
    byte_count  INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    data        BYTEA NOT NULL,
    filename    TEXT NULL,
    prompt      TEXT NOT NULL,
    reading     TEXT NULL,
    error       TEXT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_images_learner ON images (learner_id, created_at DESC);
