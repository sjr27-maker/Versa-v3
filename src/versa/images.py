"""Pictures a learner attaches to a message (migration 090, CLAUDE.md
invariant 21).

A picture is read once, when it is uploaded: one fast model call that can see
it (`ReadImage`) writes down what it shows -- the text and maths in it
transcribed (maths as LaTeX), a diagram, graph or scene described. That
READING is what the rest of Versa gets: the turn's message carries it
(`with_image`), so the ambiguity check, the answer, memory and the stage all
see the picture's content without any of them handling pixels. The app keeps
the picture itself to show it (in the chat, and held up by the slime).

Where the reading goes, per mode:
  * Sandbox and a lesson's chat: the app sends `image_id` with its message and
    the server adds the reading (server.py `_run_turn`), after checking the
    picture is the session learner's own.
  * Study with others and exam answers: the app puts the reading into the
    text it sends -- rooms and exams are walled off (invariants 12/13), and
    the reading is no more than what the learner could have typed.

Append-only: a row is written once, after the reading call, with its prompt
and output or error (invariant 2's payload in this table's own columns --
there may be no session yet).
"""

from __future__ import annotations

import hashlib
import logging
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024
READING_LIMIT = 2500
# A picture a course is built from is transcribed in full (read_resource_prompt).
RESOURCE_READING_LIMIT = 12000

# Sniffed from the bytes, never taken from the client's word for it.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class ImageError(ValueError):
    pass


def sniff_mime(data: bytes) -> str:
    """The picture's real type, or ImageError for anything that isn't one we
    read (PNG, JPEG, GIF, WebP, HEIC -- what phones and screenshots make)."""
    for magic, mime in _SIGNATURES:
        if data.startswith(magic):
            return mime
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) > 12 and data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heif"):
        return "image/heic"
    raise ImageError("that isn't a picture Versa can read (PNG, JPEG, GIF, WebP or HEIC)")


def read_prompt(filename: str | None = None) -> str:
    return (
        "IMAGE:READ\n"
        "A student attached this picture to a message to their tutor. Write down what it "
        "shows, so a tutor who cannot see it knows exactly what is in it:\n"
        "- Transcribe ALL text in it, word for word. Write every formula, equation and "
        "symbol as LaTeX: inline $...$, on its own line $$...$$.\n"
        "- A problem or exercise: give it in full, then any working the student wrote, "
        "step by step, as written -- mistakes included, never corrected.\n"
        "- Handwritten notes (a notebook page, a revision sheet): say that they are "
        "handwritten notes, then transcribe them in reading order, keeping their headings, "
        "bullets, numbering, boxes, underlining and arrows (what each arrow joins); write a "
        "word you cannot make out as [unclear], never a guess.\n"
        "- A diagram, graph, table or figure: what it is, its labels, axes, values and "
        "shape.\n"
        "- A photo of a thing or scene: what it is and the details that matter.\n"
        "Describe only what is there -- no solving, no advice, no guessing what the "
        "student wants. Plain sentences, at most about 250 words.\n"
        + (f"(Its file name: {filename[:80]!r}.)\n" if filename else "")
    )


def read_resource_prompt(filename: str | None = None) -> str:
    """The reading asked for when the picture is what a course, an exam or a
    room is to be BUILT from (a page of a textbook, a syllabus, a sheet of
    notes) rather than a question about it: all of it, in full, with its
    structure kept -- the outline is made from this text."""
    return (
        "IMAGE:READ\n"
        "A student photographed this to learn from it: a page of a book or of notes, a "
        "syllabus, a list of topics, a worksheet or a diagram. A course will be built from "
        "what you write, by someone who cannot see the picture. Write down everything in it:\n"
        "- Transcribe ALL text, word for word, in reading order. Keep its structure: put each "
        "heading, unit or chapter title on its own line, and keep lists, numbering and "
        "sub-points under the heading they belong to.\n"
        "- Write every formula, equation and symbol as LaTeX: inline $...$, on its own line "
        "$$...$$.\n"
        "- A diagram, graph, table or figure: what it is, its labels, axes, values and what "
        "it shows, in plain sentences.\n"
        "- Handwriting: transcribe it the same way; write a word you cannot make out as "
        "[unclear], never a guess.\n"
        "Begin with one line naming what this is (\"Syllabus: Class 10 Physics, Electricity\"). "
        "Only what is there -- no summary, no teaching, no advice. Up to about 1200 words.\n"
        + (f"(Its file name: {filename[:80]!r}.)\n" if filename else "")
    )


def with_image(text: str, reading: str) -> str:
    """The message a turn is given for words + a picture: the words, then what
    the picture shows. A picture sent alone asks about the picture."""
    words = text.strip() or "(They sent only this picture: help with what it shows.)"
    return f"{words}\n\n[Attached picture -- what it shows: {reading.strip()}]"


class ReadImage:
    """One fast-tier call: a picture -> a written reading of it."""

    name = "ReadImage"

    def __init__(self, llm) -> None:
        self._llm = llm

    async def run(
        self, data: bytes, mime_type: str, filename: str | None = None, purpose: str = "message",
    ) -> tuple[str, str]:
        resource = purpose == "resource"
        prompt = read_resource_prompt(filename) if resource else read_prompt(filename)
        raw = await self._llm.complete_with_image(prompt, data, mime_type)
        return prompt, (raw or "").strip()[: RESOURCE_READING_LIMIT if resource else READING_LIMIT]


class StoredImage(BaseModel):
    id: UUID
    learner_id: UUID
    mime_type: str
    byte_count: int
    reading: str | None
    error: str | None
    filename: str | None = None


class ImageStore:
    """Append-only (invariant 21): `add` writes a picture and its reading
    once; nothing edits or removes one."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(
        self, *, learner_id: UUID, data: bytes, mime_type: str, filename: str | None,
        prompt: str, reading: str | None, error: str | None,
    ) -> UUID:
        image_id = uuid4()
        await self._pool.execute(
            "INSERT INTO images (id, learner_id, mime_type, byte_count, sha256, data, filename, "
            "prompt, reading, error) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            image_id, learner_id, mime_type, len(data), hashlib.sha256(data).hexdigest(), data,
            (filename or None) and filename[:200], prompt, reading, error,
        )
        return image_id

    async def get(self, image_id: UUID) -> StoredImage | None:
        row = await self._pool.fetchrow(
            "SELECT id, learner_id, mime_type, byte_count, reading, error, filename FROM images WHERE id = $1",
            image_id,
        )
        return StoredImage(**dict(row)) if row else None

    async def data(self, image_id: UUID) -> tuple[bytes, str] | None:
        row = await self._pool.fetchrow("SELECT data, mime_type FROM images WHERE id = $1", image_id)
        return (bytes(row["data"]), row["mime_type"]) if row else None


class ImageOut(BaseModel):
    id: UUID
    mime_type: str
    reading: str


def build_images_router(pool: asyncpg.Pool, llm) -> APIRouter:
    router = APIRouter(prefix="/api")
    store = ImageStore(pool)
    reader = ReadImage(llm)

    @router.post("/images", response_model=ImageOut)
    async def upload(
        learner_id: UUID = Form(...), file: UploadFile = File(...), purpose: str = Form("message"),
    ) -> ImageOut:
        """A picture for a message: kept, read once, and the reading returned
        (the app shows it nowhere; it sends `id` with the message, or -- in a
        room or an exam answer -- the reading itself). `purpose=resource`: the
        picture is what a course, an exam or a room will be built from, so it
        is transcribed in full (read_resource_prompt)."""
        if purpose not in ("message", "resource"):
            raise HTTPException(status_code=422, detail="purpose: message or resource")
        if await pool.fetchval("SELECT 1 FROM learners WHERE id = $1", learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        data = await file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="that picture is too big (8 MB at most)")
        try:
            mime = sniff_mime(data)
        except ImageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        prompt = read_resource_prompt(file.filename) if purpose == "resource" else read_prompt(file.filename)
        try:
            prompt, reading = await reader.run(data, mime, file.filename, purpose)
        except Exception as exc:  # noqa: BLE001 -- recorded, then reported
            logger.warning("reading an image failed", exc_info=True)
            await store.add(learner_id=learner_id, data=data, mime_type=mime, filename=file.filename,
                            prompt=prompt, reading=None, error=repr(exc))
            raise HTTPException(status_code=502, detail="could not read that picture -- try again") from None
        if not reading:
            await store.add(learner_id=learner_id, data=data, mime_type=mime, filename=file.filename,
                            prompt=prompt, reading=None, error="empty reading")
            raise HTTPException(status_code=502, detail="could not read that picture -- try again")
        image_id = await store.add(learner_id=learner_id, data=data, mime_type=mime,
                                   filename=file.filename, prompt=prompt, reading=reading, error=None)
        return ImageOut(id=image_id, mime_type=mime, reading=reading)

    @router.get("/images/{image_id}")
    async def image_file(image_id: UUID) -> Response:
        found = await store.data(image_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown image")
        data, mime = found
        return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})

    router.image_store = store  # type: ignore[attr-defined]  # exposed for server.py and tests
    return router


async def picture_resource(pool: asyncpg.Pool, learner_id: UUID, image_id: UUID):
    """The learner's own picture as something to build a course or an exam
    from (resources.ExtractedResource, kind 'image'): its READING is the text,
    exactly as it was written down on upload -- nothing sees the pixels
    again. None when the picture isn't theirs or was never read."""
    from versa import resources as _resources

    picture = await ImageStore(pool).get(image_id)
    if picture is None or picture.learner_id != learner_id or not picture.reading:
        return None
    return _resources.resource_from_reading(picture.reading, picture.filename)
