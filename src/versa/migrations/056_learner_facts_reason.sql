-- versa: learner_facts gains `reason` and its own embedding (see
-- IDEAS.md's "Store the reason, make it the primary retrieval key"
-- entry for the full design discussion this implements).
--
-- ADDITIVE, alongside the existing situation+resolution `embedding`,
-- not a replacement for it -- the mitigation plan that entry settled
-- on was explicit: don't switch retrieval over to reason-matching
-- outright, add it as a second, independently-searchable path and
-- MEASURE whether a reason-based match gets confirmed by
-- ConfirmFactMatch more often than a situation/resolution-based one
-- before ever trusting it as primary. `memory.EmbedAndSearchFacts`
-- runs both searches and reports which one produced the winning
-- candidate (`FactSearchResult.matched_via`, `turn_diagnostics.
-- memory_match_via` -- migration 057) precisely so that comparison is
-- possible once real usage accumulates.
--
-- Both columns are NULLABLE, unlike `situation`/`resolution`/
-- `embedding`: a reason is written only when THIS specific exchange
-- actually suggests one (see memory.py's _fact_prompt) -- extracting a
-- confabulated reason on every single episode, the way situation/
-- resolution always are, is exactly the failure mode named in that
-- IDEAS.md entry (claims.py's own "a fluent model produces a
-- plausible-sounding claim for any input" risk). Nullable also means
-- every row written before this migration reads back correctly as
-- "no reason recorded" rather than needing a backfill this migration
-- cannot honestly perform (a real reason takes an LLM call over the
-- original exchange, not a default string).
--
-- Append-only (CLAUDE.md invariant 10), unchanged: this ALTERs the
-- table's shape, it does not touch a single existing row's
-- situation/resolution/embedding, and nothing here is a DELETE.

ALTER TABLE learner_facts ADD COLUMN reason TEXT;
ALTER TABLE learner_facts ADD COLUMN reason_embedding vector(768);

-- Partial index: only rows with a reason actually get embedded, and
-- only those are ever searched (LearnerFactStore.search_similar_by_reason
-- filters WHERE reason_embedding IS NOT NULL) -- indexing the NULLs
-- too would just be dead weight in an HNSW graph nothing ever queries.
CREATE INDEX idx_learner_facts_reason_embedding_hnsw
    ON learner_facts USING hnsw (reason_embedding vector_cosine_ops)
    WHERE reason_embedding IS NOT NULL;
