-- versa: cached "why Versa thinks this" explanations for thinking-style
-- candidates and claims (IDEAS.md Thinking-style page). One real LLM call
-- (reviews.ExplainItem, routed through SessionLoop._call_node per CLAUDE.md
-- invariant 2 -- node_call_session_id/node_call_turn_index name the turn it
-- was recorded against) per distinct evidence state; a repeat view with the
-- same evidence_fingerprint (the item's confirmation/evidence count at
-- generation time) is served from here with no new LLM call. Append-only:
-- more evidence arriving later writes a NEW row under a NEW fingerprint,
-- never overwrites this one -- the old explanation stays inspectable, same
-- as every other historical record in this codebase.

CREATE TABLE explanation_cache (
    id                  UUID PRIMARY KEY,
    claim_id            UUID NULL REFERENCES claims (id),
    thinking_style_candidate_id UUID NULL REFERENCES thinking_style_candidates (id),
    evidence_fingerprint TEXT NOT NULL,
    explanation         TEXT NOT NULL,
    node_call_session_id UUID NOT NULL REFERENCES sessions (id),
    node_call_turn_index INT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (claim_id IS NOT NULL AND thinking_style_candidate_id IS NULL) OR
        (claim_id IS NULL AND thinking_style_candidate_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX explanation_cache_claim_fingerprint_idx
    ON explanation_cache (claim_id, evidence_fingerprint) WHERE claim_id IS NOT NULL;
CREATE UNIQUE INDEX explanation_cache_thinking_style_fingerprint_idx
    ON explanation_cache (thinking_style_candidate_id, evidence_fingerprint)
    WHERE thinking_style_candidate_id IS NOT NULL;
