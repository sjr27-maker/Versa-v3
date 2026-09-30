-- versa: revision notes for a chat, made only when the learner asks
-- (notes.py, 2026-09-30).
--
-- The learner taps "Notes" in a chat and asks for them: one model call
-- (`WriteNotes`, NOTES:WRITE) reads the whole chat so far and writes
-- revision notes -- the topics studied, the key points of each, formulas
-- and an example where there are some, a short summary, and where they
-- stopped -- pitched to what the learner told Versa about themselves and
-- their confirmed way of thinking. The app shows them and can download them
-- as a PDF, which is rendered from this row on request (never stored).
--
--   notes  one row per request that ran the model, written once, AFTER the
--          call: which chat, how far into it the notes reach (through_turn,
--          and how many answers that was), the prompt, the raw reply, and
--          the parsed notes -- or the error. A failed attempt is still a
--          row (content NULL, error set). Asking again when nothing new was
--          studied returns the latest row and writes nothing.
--
-- The call's prompt and output are kept on this row -- invariant 2's
-- payload in the notes' own table, the same precedent as images /
-- profile_extractions: notes are not a turn of the chat.
--
-- Append-only (CLAUDE.md invariant 22): no UPDATE, no DELETE.

CREATE TABLE IF NOT EXISTS notes (
    id            UUID PRIMARY KEY,
    learner_id    UUID NOT NULL REFERENCES learners (id),
    session_id    UUID NOT NULL REFERENCES sessions (id),
    through_turn  INTEGER NOT NULL,
    answers       INTEGER NOT NULL,
    prompt        TEXT NOT NULL,
    raw_output    TEXT NULL,
    content       JSONB NULL,
    error         TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notes_session ON notes (session_id, created_at DESC);
