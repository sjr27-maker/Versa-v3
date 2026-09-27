-- versa: a third session slider, breadth (0-100, narrow -> wide), next to
-- length and depth (migration 071). 50 is the untouched default and renders
-- no directive, so existing sessions' prompts are unchanged.
--
-- Today breadth shapes the answer (how far it reaches beyond the question
-- into related ideas and uses). It is also meant to set the outer limit of
-- the "space of possibilities" once that exists (IDEAS.md, 2026-09-27):
-- background sets the default window, depth and breadth move its edges.
--
-- answer_versions records the breadth each rewrite was made at, alongside
-- length and depth; rows written before this migration read as 50.

ALTER TABLE sessions
    ADD COLUMN breadth_level SMALLINT NOT NULL DEFAULT 50
        CHECK (breadth_level BETWEEN 0 AND 100);

ALTER TABLE answer_versions
    ADD COLUMN breadth_level SMALLINT NOT NULL DEFAULT 50
        CHECK (breadth_level BETWEEN 0 AND 100);
