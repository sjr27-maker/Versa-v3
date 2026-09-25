-- versa: student review actions on thinking-style candidates and claims
-- (IDEAS.md Thinking-style page: "show everything with edit, view, delete
-- options"). Append-only (CLAUDE.md invariants 1/4/6-11's established
-- pattern): a student's "delete" is a status OVERLAY computed from the
-- latest review row, never a removed row -- 'archive' hides an item from
-- the default view, 'restore' brings it back, exactly the resurrection-
-- over-deletion convention every other store in this codebase already
-- uses. 'edit' never overwrites the claim/candidate's own statement column;
-- it appends a revision here, and readers resolve "the current text" to
-- the latest 'edit' row's `revised_statement`, falling back to the
-- original when there is none -- same pattern as `claim_statements`.
-- 'approve' is the direct, non-circular confirmation IDEAS.md's "Ask for
-- confirmation directly" entry wanted for the memory layer, extended to
-- claims/thinking-style: it is recorded as a fact, but deliberately does
-- NOT touch `claims.confidence` or `thinking_style_candidates.
-- confirmation_count` -- those numbers stay governed only by the real
-- evidence rules in claims.py/memory.py, so a click here can never
-- inflate a number a live session earned.
--
-- `previous_statement` is set only on 'edit' rows: the text this edit
-- replaced, captured at write time so an Undo (reviews.py) can construct
-- the exact inverse edit without re-deriving history.
--
-- `source`/`qna_id` distinguish a review the student made directly (the
-- View/Edit/Approve/Delete buttons) from one the ask-the-chat flow applied
-- on their behalf (migration 067's `item_qna`, added after this table --
-- `qna_id` is stored as a plain UUID with no FK to avoid a forward
-- reference; the application enforces it points at a real row).

CREATE TABLE student_reviews (
    id                  UUID PRIMARY KEY,
    claim_id            UUID NULL REFERENCES claims (id),
    thinking_style_candidate_id UUID NULL REFERENCES thinking_style_candidates (id),
    review_type         TEXT NOT NULL CHECK (review_type IN ('approve', 'edit', 'archive', 'restore')),
    revised_statement   TEXT NULL,
    previous_statement  TEXT NULL,
    source              TEXT NOT NULL DEFAULT 'direct' CHECK (source IN ('direct', 'chat')),
    qna_id              UUID NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (claim_id IS NOT NULL AND thinking_style_candidate_id IS NULL) OR
        (claim_id IS NULL AND thinking_style_candidate_id IS NOT NULL)
    )
);

CREATE INDEX student_reviews_claim_idx ON student_reviews (claim_id, created_at);
CREATE INDEX student_reviews_thinking_style_idx
    ON student_reviews (thinking_style_candidate_id, created_at);
