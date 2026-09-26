-- versa: Exam preparation (exams.py). An exam is a title, an optional date
-- and a syllabus of units -- from a search, a PDF or link (the same
-- topic_resources Learn a topic reads), or an existing course's chapters.
-- Students take a quiz per unit and timed mock tests across all units.
--
-- Append-only (CLAUDE.md invariant 13): every table here is only ever
-- inserted into. A quiz is one sitting: a retake is a NEW quiz, never an
-- edit of the old one. Scores are not columns -- they are derived from
-- exam_answers. Walled off from the personal learner model: nothing here is
-- read from or written to learner facts, claims or thinking styles.

CREATE TABLE exams (
    id           UUID PRIMARY KEY,
    learner_id   UUID NOT NULL REFERENCES learners (id),
    title        TEXT NOT NULL,
    exam_date    DATE NULL,
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('search', 'pdf', 'link', 'course')),
    query        TEXT NULL,
    topic_id     UUID NULL REFERENCES topics (id),
    resource_id  UUID NULL REFERENCES topic_resources (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_exams_learner ON exams (learner_id, created_at DESC);

CREATE TABLE exam_units (
    id        UUID PRIMARY KEY,
    exam_id   UUID NOT NULL REFERENCES exams (id),
    position  INT NOT NULL,
    title     TEXT NOT NULL,
    summary   TEXT NOT NULL,
    -- extra context for question writing, e.g. a course chapter's lessons
    detail    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_exam_units_exam ON exam_units (exam_id, position);

-- Every model call exam prep makes (syllabus, questions, grading): node
-- name, full input incl. the prompt, parsed output or the error. There is
-- no session, so this is invariant 2's record in exam prep's own table
-- (same precedent as topic_generations / feed_generations).
CREATE TABLE exam_generations (
    id           UUID PRIMARY KEY,
    learner_id   UUID NOT NULL REFERENCES learners (id),
    exam_id      UUID NULL REFERENCES exams (id),
    node_name    TEXT NOT NULL,
    input_json   JSONB NOT NULL,
    output_json  JSONB NULL,
    error        TEXT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_exam_generations_exam ON exam_generations (exam_id, created_at);

-- One sitting. unit_id is set for a unit quiz, NULL for a mock test.
-- created_at is when the clock started.
CREATE TABLE exam_quizzes (
    id                  UUID PRIMARY KEY,
    exam_id             UUID NOT NULL REFERENCES exams (id),
    unit_id             UUID NULL REFERENCES exam_units (id),
    kind                TEXT NOT NULL CHECK (kind IN ('unit', 'mock')),
    time_limit_seconds  INT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((kind = 'unit') = (unit_id IS NOT NULL))
);

CREATE INDEX idx_exam_quizzes_exam ON exam_quizzes (exam_id, created_at);

CREATE TABLE exam_questions (
    id             UUID PRIMARY KEY,
    quiz_id        UUID NOT NULL REFERENCES exam_quizzes (id),
    unit_id        UUID NOT NULL REFERENCES exam_units (id),
    position       INT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('choice', 'short')),
    prompt         TEXT NOT NULL,
    choices        JSONB NOT NULL DEFAULT '[]'::jsonb,
    correct_index  INT NULL,
    model_answer   TEXT NOT NULL,
    explanation    TEXT NOT NULL,
    CHECK ((kind = 'choice') = (correct_index IS NOT NULL))
);

CREATE INDEX idx_exam_questions_quiz ON exam_questions (quiz_id, position);

-- Handing a quiz in. At most one per quiz (a retake is a new quiz).
CREATE TABLE exam_submissions (
    id               UUID PRIMARY KEY,
    quiz_id          UUID NOT NULL UNIQUE REFERENCES exam_quizzes (id),
    elapsed_seconds  INT NOT NULL,
    over_time        BOOLEAN NOT NULL,
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE exam_answers (
    id             UUID PRIMARY KEY,
    submission_id  UUID NOT NULL REFERENCES exam_submissions (id),
    question_id    UUID NOT NULL REFERENCES exam_questions (id),
    response       TEXT NOT NULL,
    -- NULL when a short answer could not be graded (the grading call failed)
    correct        BOOLEAN NULL,
    feedback       TEXT NOT NULL DEFAULT '',
    graded_by      TEXT NOT NULL CHECK (graded_by IN ('choice', 'model', 'ungraded')),
    UNIQUE (submission_id, question_id)
);
