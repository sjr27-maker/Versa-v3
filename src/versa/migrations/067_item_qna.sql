-- versa: "ask about this" threads on a thinking-style candidate or claim
-- (IDEAS.md Thinking-style page). One row per question+answer pair,
-- append-only, so a thread reappears exactly as it happened when the
-- detail view is reopened. `intent`/`applied_review_id` record when the
-- student's question was actually a correction the ask flow applied on
-- their behalf (student_reviews.source='chat', linked here and there) --
-- never overwritten after the fact, so what changed and why stays visible
-- next to the conversation that caused it.

CREATE TABLE item_qna (
    id                  UUID PRIMARY KEY,
    claim_id            UUID NULL REFERENCES claims (id),
    thinking_style_candidate_id UUID NULL REFERENCES thinking_style_candidates (id),
    question            TEXT NOT NULL,
    answer              TEXT NOT NULL,
    intent              TEXT NOT NULL DEFAULT 'none'
        CHECK (intent IN ('none', 'edit', 'approve', 'archive')),
    applied_review_id   UUID NULL REFERENCES student_reviews (id),
    node_call_session_id UUID NOT NULL REFERENCES sessions (id),
    node_call_turn_index INT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (claim_id IS NOT NULL AND thinking_style_candidate_id IS NULL) OR
        (claim_id IS NULL AND thinking_style_candidate_id IS NOT NULL)
    )
);

CREATE INDEX item_qna_claim_idx ON item_qna (claim_id, created_at);
CREATE INDEX item_qna_thinking_style_idx ON item_qna (thinking_style_candidate_id, created_at);
