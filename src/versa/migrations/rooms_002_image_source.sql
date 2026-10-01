-- versa rooms: a room's topic can come from a PICTURE (a page, a syllabus,
-- someone's notes), next to a search, a PDF and a web link. The room is given
-- the words read out of the picture (rooms/router.py CreateIn.picture) -- a
-- room never handles the picture itself.
--
-- Only the allowed values of source_kind widen: every room already there
-- satisfies the new check, and none is touched (invariant 12).

ALTER TABLE rooms
    DROP CONSTRAINT rooms_source_kind_check,
    ADD CONSTRAINT rooms_source_kind_check CHECK (source_kind IN ('search', 'pdf', 'link', 'image'));
