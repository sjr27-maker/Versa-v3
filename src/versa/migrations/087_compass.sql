-- versa: the compass (directions.py) -- a third way to show "where this could
-- go": four cards, one per family, placed around the answer (go deeper up,
-- make it simpler down, make it real left, go wider right). Presentation
-- changes what people pick, so every set records the one the learner saw.
--
-- Append-only (CLAUDE.md invariant 14): only the allowed values widen.

ALTER TABLE direction_sets DROP CONSTRAINT direction_sets_presentation_check;
ALTER TABLE direction_sets
    ADD CONSTRAINT direction_sets_presentation_check CHECK (presentation IN ('strip', 'fork', 'compass'));
