-- versa: lets a sandbox-chat-triggered claim update (loop.py's stated-
-- preference matching, background, off the critical path) record itself
-- through the SAME append-only review mechanism migration 065 built for
-- the Thinking-style page's manual actions -- 'sandbox_chat' joins
-- 'direct' (a button click) and 'chat' (the per-item ask-about-it flow)
-- as a third, distinct source. `source_session_id`/`source_turn_index`
-- name the live turn that caused it (nullable: only ever set for
-- source='sandbox_chat'), so session_history.py can replay the inline
-- "Noted: ..." note on resume without a second lookup table.

ALTER TABLE student_reviews
    DROP CONSTRAINT student_reviews_source_check,
    ADD CONSTRAINT student_reviews_source_check
        CHECK (source IN ('direct', 'chat', 'sandbox_chat')),
    ADD COLUMN source_session_id UUID NULL REFERENCES sessions (id),
    ADD COLUMN source_turn_index INT NULL;
