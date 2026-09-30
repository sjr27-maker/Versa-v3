-- Exam preparation, focused (exams.py, 2026-10-01):
--
-- 1. Every question says what it tests -- `skill`: recall, understand, apply
--    or analyse -- and whether it is a straight quiz question or a puzzle
--    (`form`: spot the mistake, what happens next, which comes first...).
--    A result is then read per skill, not only as one number.
-- 2. A unit quiz is practice taken one question at a time: each tap is
--    checked there and then (right answer and why). `exam_checks` keeps that
--    first tap, once per question (question_id UNIQUE); handing the quiz in
--    uses it, so a checked answer can't be changed afterwards. Mock tests are
--    never checked early -- they stay exam-like.
--
-- Append-only (CLAUDE.md invariant 13): columns and a table are added; no row
-- is ever changed or removed. Older questions keep NULL skill/form.

ALTER TABLE exam_questions
    ADD COLUMN skill TEXT NULL CHECK (skill IN ('recall', 'understand', 'apply', 'analyse')),
    ADD COLUMN form  TEXT NULL CHECK (form IN ('quiz', 'puzzle'));

CREATE TABLE exam_checks (
    id           UUID PRIMARY KEY,
    quiz_id      UUID NOT NULL REFERENCES exam_quizzes (id),
    question_id  UUID NOT NULL UNIQUE REFERENCES exam_questions (id),
    response     TEXT NOT NULL,
    correct      BOOLEAN NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_exam_checks_quiz ON exam_checks (quiz_id, created_at);
