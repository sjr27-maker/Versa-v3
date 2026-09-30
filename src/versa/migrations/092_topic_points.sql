-- Learn a topic, reworked (topics.py, 2026-10-01):
--
-- 1. A lesson is a list of POINTS of content, not learn/practice/apply/check
--    tasks. Each point is explained, then checked with a tap-to-answer quiz
--    (a quiz, a puzzle -- never something to type). The lesson is done when
--    every point is, so how long it takes follows how much there is to cover.
--    Older courses keep their task kinds; nothing is rewritten.
-- 2. A branch mapped from a PDF or a link remembers WHERE in the resource it
--    comes from (source_start/source_end, character offsets into
--    topic_resources.text), so branching further and planning lessons read
--    that part of the resource instead of guessing. A branch the resource
--    doesn't cover -- the "little extra" offered once a section has no parts
--    of its own -- is marked beyond_resource and branches no further.
-- 3. Every quiz tap is kept as a signal, right or wrong.
--
-- Append-only like 072-074: columns and allowed values are added, no row is
-- ever changed or removed.

ALTER TABLE lesson_tasks DROP CONSTRAINT lesson_tasks_kind_check;
ALTER TABLE lesson_tasks ADD CONSTRAINT lesson_tasks_kind_check
    CHECK (kind IN ('learn', 'practice', 'apply', 'check', 'point'));

ALTER TABLE topic_nodes
    ADD COLUMN source_start     INT NULL,
    ADD COLUMN source_end       INT NULL,
    ADD COLUMN beyond_resource  BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE topic_signals DROP CONSTRAINT topic_signals_kind_check;
ALTER TABLE topic_signals ADD CONSTRAINT topic_signals_kind_check
    CHECK (kind IN (
        'search', 'resource', 'expand', 'selection', 'lesson_open',
        'task_completed', 'check_result', 'chapter_drift', 'quiz_answered'));
