"""Revision notes for a chat, made only when the learner asks (migration 091,
CLAUDE.md invariant 22).

The learner taps "Notes" in a chat and then "Generate notes". Nothing is made
before that tap, and nothing is made on a schedule. One model call
(`WriteNotes`, NOTES:WRITE) reads the WHOLE chat so far -- every question and
every answer, as `session_history` replays it -- and writes revision notes of
what was studied, up to where they stopped:

  title       what the chat was about, in a few words
  topics      each topic studied, in the order it came up, with its key
              points (short, revision-sized), any formulas, and one example
              when the chat had one
  summary     a short wrap-up of everything covered
  stopped_at  where the chat got to -- the last thing they were on

Pitched to the learner: the sign-up profile (their level, board, course --
`profiles.load_background`) and their CONFIRMED thinking style
(`style_patterns.confirmed_statements`) shape how the notes are written --
wording, order within a topic, examples first or rules first -- never what
is true, and never anything the chat didn't cover. Notes are not evidence:
nothing here is read by claims, thinking styles or the direction cards.

Any chat that is a session can have notes -- a Sandbox chat, and a Learn-a-
topic lesson chat (the same `sessions` row, app_mode 'topic'). Exam prep and
rooms keep their own tables and are not sessions; they are not covered here.

Priced (`generate_notes` in sparks.ACTION_COSTS), charged only when the
model actually runs and refunded if it fails. Asking again when nothing new
was studied since the last notes returns those notes and costs nothing.

The PDF is drawn from a stored row on request (`render_pdf`, reportlab, with
maths typeset by matplotlib's mathtext) and never stored itself: the row is
the record, the PDF is a view of it.

Append-only: a row is written once, after the call, with its prompt, the
raw reply and the parsed notes or the error (invariant 2's payload in this
table's own columns -- notes are not a turn of the chat).
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from versa.formatting import MATH_STYLE_JSON, repair_latex_escapes
from versa.models import HistoryTurn

logger = logging.getLogger(__name__)

# The whole chat goes in; past this many characters each answer is shortened
# (the student's own words never are) so a very long chat still fits.
MAX_CHAT_CHARS = 120_000
MIN_ANSWER_CHARS = 400
MAX_TOPICS = 10
MAX_POINTS = 7
MAX_FORMULAS = 4


class NotesError(Exception):
    """The chat has nothing to make notes from yet."""


# ------------------------------------------------------------------ the notes


class NoteTopic(BaseModel):
    name: str
    points: list[str] = Field(default_factory=list)
    formulas: list[str] = Field(default_factory=list)  # LaTeX, no $ delimiters
    example: str | None = None


class Notes(BaseModel):
    title: str
    topics: list[NoteTopic]
    summary: str
    stopped_at: str | None = None


# ------------------------------------------------------------------ the chat


def render_chat(turns: list[HistoryTurn], *, limit: int = MAX_CHAT_CHARS) -> tuple[str, int, int]:
    """(the chat as text, how many answers it has, the last turn index in it).

    Only turns that were ANSWERED count: a "which did you mean?" turn is the
    tutor asking, not teaching, and a turn with no recorded answer taught
    nothing. A click on one of the offered readings has no words of the
    student's own (`student_text` None) -- it reads as picking a reading."""
    pairs: list[tuple[str, str, int]] = []
    for t in turns:
        if t.kind != "answer" or not (t.tutor_text or "").strip():
            continue
        asked = (t.student_text or "").strip() or "(picked one of the readings Versa offered)"
        pairs.append((asked, t.tutor_text.strip(), t.turn_index))
    if not pairs:
        return "", 0, -1
    total = sum(len(a) + len(b) for a, b, _ in pairs)
    if total > limit:
        asked_total = sum(len(a) for a, _, _ in pairs)
        per_answer = max(MIN_ANSWER_CHARS, (limit - asked_total) // len(pairs))
        pairs = [(a, b if len(b) <= per_answer else b[:per_answer].rstrip() + " [...]", i) for a, b, i in pairs]
    text = "\n\n".join(f"Student: {a}\nVersa: {b}" for a, b, _ in pairs)
    return text, len(pairs), pairs[-1][2]


# ------------------------------------------------------------------ the model call


def notes_prompt(chat: str, *, background: str = "", style: list[str] | None = None) -> str:
    style_block = ""
    if style:
        style_block = (
            "\nHow this learner moves through ideas, from their own choices (confirmed patterns). "
            "Shape HOW the notes are written to it -- the order within a topic, whether an example "
            "or the rule comes first, how much 'why' goes with each point -- never WHAT is true, "
            "and never mention it to them:\n" + "\n".join(f"- {s}" for s in style) + "\n"
        )
    return (
        "NOTES:WRITE\n"
        "A student has been studying with their tutor, Versa, and now asked for revision notes of "
        "this chat -- something to read back later to remember what they learned. Write them.\n"
        f"{background}{style_block}\n"
        "The chat so far (every question and answer, in order):\n"
        "<chat>\n"
        f"{chat}\n"
        "</chat>\n\n"
        "Rules:\n"
        "- Cover everything they studied, up to where the chat stopped -- and nothing it didn't "
        "cover. No new material, no extra topics, no advice.\n"
        f"- topics: each topic studied, in the order it came up (merge repeats), at most {MAX_TOPICS}. "
        "name: a few words.\n"
        f"- points: the key points of that topic for revision, at most {MAX_POINTS} -- short, one idea "
        "each, in the student's level of language; the most important first. Keep the facts exactly "
        "as the tutor explained them.\n"
        f"- formulas: the formulas or equations of that topic, if any, at most {MAX_FORMULAS}; each "
        "one bare LaTeX with no dollar signs (e.g. \"v = u + at\", \"\\\\frac{a}{b}\"). [] if none.\n"
        "- example: one short worked or real example from the chat for that topic, or null.\n"
        "- summary: 3-5 sentences wrapping up the whole chat -- what it covered and how the ideas "
        "connect.\n"
        "- stopped_at: one short line on where the chat got to (the last thing they were on), or "
        "null if it plainly finished.\n"
        "- title: what the chat was about, in 2-6 words.\n"
        "- Write to the student as 'you' where it reads naturally; never mention the tutor, the "
        "chat or these rules.\n"
        f"{MATH_STYLE_JSON}\n\n"
        "Respond with JSON only: {\"title\": \"...\", \"topics\": [{\"name\": \"...\", \"points\": "
        "[\"...\"], \"formulas\": [\"...\"], \"example\": \"...\"|null}], \"summary\": \"...\", "
        "\"stopped_at\": \"...\"|null}"
    )


def _clean(text: object) -> str:
    return str(text).strip() if text is not None else ""


def _bare_formula(text: str) -> str:
    """A formula as bare LaTeX: the model is asked for no delimiters, but a
    stray $...$ or $$...$$ must not show as dollar signs."""
    t = text.strip()
    for left, right in (("$$", "$$"), ("\\[", "\\]"), ("$", "$"), ("\\(", "\\)")):
        if t.startswith(left) and t.endswith(right) and len(t) > len(left) + len(right):
            return t[len(left):-len(right)].strip()
    return t


def parse_notes(raw: str) -> Notes | None:
    try:
        data = json.loads(repair_latex_escapes(raw))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        notes = Notes.model_validate(data)
    except ValidationError:
        return None
    topics = []
    for t in notes.topics:
        name = _clean(t.name)
        points = [p for p in (_clean(p) for p in t.points) if p][:MAX_POINTS]
        if not name or not points:
            continue
        formulas = [f for f in (_bare_formula(_clean(f)) for f in t.formulas) if f][:MAX_FORMULAS]
        topics.append(NoteTopic(name=name, points=points, formulas=formulas, example=_clean(t.example) or None))
    if not topics or not _clean(notes.summary):
        return None
    return Notes(
        title=_clean(notes.title) or topics[0].name,
        topics=topics[:MAX_TOPICS],
        summary=_clean(notes.summary),
        stopped_at=_clean(notes.stopped_at) or None,
    )


class WriteNotes:
    """NOTES:WRITE -- one call, one corrective retry on an unreadable reply."""

    node_name = "WriteNotes"

    def __init__(self, llm) -> None:
        self._llm = llm

    async def run(self, prompt: str) -> tuple[Notes | None, str]:
        """(the notes or None, the raw reply)."""
        raw = ""
        for _ in range(2):
            raw = await self._llm.complete(prompt)
            parsed = parse_notes(raw)
            if parsed is not None:
                return parsed, raw
        return None, raw


# ------------------------------------------------------------------ store


class StoredNotes(BaseModel):
    id: UUID
    learner_id: UUID
    session_id: UUID
    through_turn: int
    answers: int
    notes: Notes
    created_at: datetime


class NoteStore:
    """Append-only (invariant 22): `add` writes one attempt once; nothing
    edits or removes one."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(
        self, *, learner_id: UUID, session_id: UUID, through_turn: int, answers: int,
        prompt: str, raw_output: str | None, notes: Notes | None, error: str | None,
    ) -> UUID:
        note_id = uuid4()
        await self._pool.execute(
            "INSERT INTO notes (id, learner_id, session_id, through_turn, answers, prompt, raw_output, "
            "content, error) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)",
            note_id, learner_id, session_id, through_turn, answers, prompt, raw_output,
            notes.model_dump_json() if notes is not None else None, error,
        )
        return note_id

    @staticmethod
    def _row(row: asyncpg.Record) -> StoredNotes:
        content = row["content"]
        if isinstance(content, str):
            content = json.loads(content)
        return StoredNotes(
            id=row["id"], learner_id=row["learner_id"], session_id=row["session_id"],
            through_turn=row["through_turn"], answers=row["answers"],
            notes=Notes.model_validate(content), created_at=row["created_at"],
        )

    async def latest(self, session_id: UUID) -> StoredNotes | None:
        """The newest notes that came out right for this chat."""
        row = await self._pool.fetchrow(
            "SELECT id, learner_id, session_id, through_turn, answers, content, created_at FROM notes "
            "WHERE session_id = $1 AND content IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        return self._row(row) if row else None

    async def get(self, note_id: UUID) -> StoredNotes | None:
        row = await self._pool.fetchrow(
            "SELECT id, learner_id, session_id, through_turn, answers, content, created_at FROM notes "
            "WHERE id = $1 AND content IS NOT NULL",
            note_id,
        )
        return self._row(row) if row else None


# ------------------------------------------------------------------ the PDF

_INK = "#2B2823"
_BODY = "#4A443B"
_FAINT = "#A89B7F"
_ACCENT = "#C85A2E"
_ACCENT_SOFT = "#FDF2EA"
_ACCENT_LINE = "#F4DCC6"
_SLIVER = "#FAF5EA"
_BORDER = "#E8DCC4"

_fonts_ready = False


def _register_fonts() -> None:
    """DejaVu, shipped inside matplotlib: covers every script and symbol a
    note is likely to hold (the PDF core fonts are Latin-1 only)."""
    global _fonts_ready
    if _fonts_ready:
        return
    import matplotlib
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
    for name, file in (
        ("Versa", "DejaVuSans.ttf"), ("Versa-Bold", "DejaVuSans-Bold.ttf"),
        ("Versa-Italic", "DejaVuSans-Oblique.ttf"), ("Versa-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
        ("VersaMono", "DejaVuSansMono.ttf"),
    ):
        pdfmetrics.registerFont(TTFont(name, str(ttf / file)))
    addMapping("Versa", 0, 0, "Versa")
    addMapping("Versa", 1, 0, "Versa-Bold")
    addMapping("Versa", 0, 1, "Versa-Italic")
    addMapping("Versa", 1, 1, "Versa-BoldItalic")
    _fonts_ready = True


def _tex_for_mathtext(tex: str, *, display: bool = False) -> str:
    """The few everyday LaTeX spellings mathtext doesn't know, rewritten. A
    displayed formula gets full-size fractions, as LaTeX would set them."""
    t = tex.strip()
    t = re.sub(r"\\[td]?frac\b", r"\\dfrac" if display else r"\\frac", t)
    t = t.replace("\\displaystyle", "").replace("\\limits", "")
    t = re.sub(r"\\(?:text|textrm|textnormal|mbox)\{", r"\\mathrm{", t)
    t = t.replace("\\ ", "\\,")
    return t


class _Math:
    """Typesets LaTeX to PNGs in a scratch folder, for one PDF."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        self._n = 0

    def image(self, tex: str, size: float, *, display: bool = False) -> tuple[str, float, float, float] | None:
        """(png path, width pt, height pt, depth pt), or None if mathtext
        can't read it (the caller then shows the LaTeX as written)."""
        from matplotlib import mathtext
        from matplotlib.font_manager import FontProperties

        self._n += 1
        path = self._folder / f"m{self._n}.png"
        dpi = 300
        try:
            raw = io.BytesIO()
            # depth: how far the baseline sits above the image's bottom, in points
            depth = mathtext.math_to_image(
                f"${_tex_for_mathtext(tex, display=display)}$", raw, prop=FontProperties(size=size), dpi=dpi, format="png",
            )
            from PIL import Image

            raw.seek(0)
            with Image.open(raw) as im:
                # Black on white -> ink on transparent, so it sits on any box.
                alpha = Image.eval(im.convert("L"), lambda v: 255 - v)
                ink = Image.new("RGBA", im.size, _INK)
                ink.putalpha(alpha)
                ink.save(path, format="PNG")
                w, h = im.size
        except Exception:  # noqa: BLE001 -- anything it can't typeset is shown as written
            return None
        scale = 72 / dpi
        return str(path), w * scale, h * scale, float(depth or 0)


_INLINE = re.compile(r"(\$\$.+?\$\$|\$[^$\n]+?\$|\*\*.+?\*\*|`[^`\n]+?`|(?<![\w*])\*(?!\s)[^*\n]+?(?<!\s)\*(?![\w*]))")


def _markup(text: str, math: _Math, size: float) -> str:
    """The app's small formatting (formatting.py) as reportlab paragraph
    markup: **bold**, *italic*, `code`, and $maths$ as inline images."""
    out: list[str] = []
    pos = 0
    for m in _INLINE.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        token = m.group(0)
        if token.startswith("$"):
            tex = token.strip("$")
            img = math.image(tex, size)
            if img is None:
                out.append(f'<font name="VersaMono">{html.escape(tex, quote=False)}</font>')
            else:
                path, w, h, depth = img
                out.append(f'<img src="{path}" width="{w:.2f}" height="{h:.2f}" valign="{-depth:.2f}"/>')
        elif token.startswith("**"):
            out.append(f"<b>{html.escape(token[2:-2], quote=False)}</b>")
        elif token.startswith("`"):
            out.append(f'<font name="VersaMono">{html.escape(token[1:-1], quote=False)}</font>')
        else:
            out.append(f"<i>{html.escape(token[1:-1], quote=False)}</i>")
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out).replace("\n", "<br/>")


def _blocks(text: str) -> list[tuple[str, str]]:
    """Split prose into ("text", ...) paragraphs and ("math", tex) displays."""
    parts: list[tuple[str, str]] = []
    for piece in re.split(r"(\$\$.+?\$\$)", text, flags=re.DOTALL):
        if piece.startswith("$$") and piece.endswith("$$") and len(piece) > 4:
            parts.append(("math", piece[2:-2].strip()))
            continue
        for para in re.split(r"\n\s*\n", piece):
            para = para.strip()
            if para:
                parts.append(("text", para))
    return parts


def render_pdf(notes: Notes, *, learner: str | None = None, made: datetime | None = None,
               mode: str = "Sandbox") -> bytes:
    """The notes as an A4 PDF: title, topics covered, each topic's key points,
    formulas and example, the summary, and where they stopped."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        CondPageBreak,
        Image,
        KeepTogether,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    _register_fonts()
    made = made or datetime.now(UTC)
    ink, body, faint, accent = (colors.HexColor(c) for c in (_INK, _BODY, _FAINT, _ACCENT))

    def style(name: str, **kw) -> ParagraphStyle:
        base = {"fontName": "Versa", "fontSize": 10.5, "leading": 15.5, "textColor": body}
        base.update(kw)
        return ParagraphStyle(name, **base)

    kicker = style("kicker", fontName="VersaMono", fontSize=8, leading=10, textColor=accent)
    title = style("title", fontName="Versa-Bold", fontSize=22, leading=27, textColor=ink, spaceBefore=4)
    meta = style("meta", fontSize=9, leading=12, textColor=faint)
    h2 = style("h2", fontName="Versa-Bold", fontSize=13.5, leading=18, textColor=ink, spaceBefore=4, spaceAfter=4)
    label = style("label", fontName="VersaMono", fontSize=8, leading=10, textColor=accent, spaceAfter=3)
    text = style("text")
    small = style("small", fontSize=9.5, leading=14)
    centred = style("centred", alignment=TA_CENTER)

    with tempfile.TemporaryDirectory(prefix="versa-notes-") as scratch:
        math = _Math(Path(scratch))

        def prose(value: str, st: ParagraphStyle = text) -> list:
            flow: list = []
            for kind, part in _blocks(value):
                if kind == "math":
                    img = math.image(part, st.fontSize + 1.5, display=True)
                    if img is None:
                        flow.append(Paragraph(f'<font name="VersaMono">{html.escape(part, quote=False)}</font>', centred))
                    else:
                        flow += [Spacer(1, 3), Image(img[0], width=img[1], height=img[2], hAlign="CENTER"), Spacer(1, 3)]
                else:
                    flow.append(Paragraph(_markup(part, math, st.fontSize), st))
            return flow

        def boxed(flow: list, fill: str, line: str, align: str = "LEFT") -> Table:
            t = Table([[flow]], colWidths=[None])
            t.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), align),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(fill)),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(line)),
                ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]))
            return t

        story: list = [
            Paragraph("VERSA · REVISION NOTES", kicker),
            Paragraph(html.escape(notes.title, quote=False), title),
            Paragraph(html.escape(" · ".join(b for b in (
                f"For {learner}" if learner else None, f"{mode} chat", made.strftime("%d %b %Y"),
            ) if b), quote=False), meta),
            Spacer(1, 10),
        ]

        # What this covers, at a glance.
        covered = [Paragraph("TOPICS COVERED", label)]
        covered.append(ListFlowable(
            [ListItem(Paragraph(_markup(t.name, math, 10.5), text), leftIndent=14) for t in notes.topics],
            bulletType="1", bulletFontName="Versa-Bold", bulletColor=accent, bulletFontSize=10, leftIndent=14,
        ))
        story += [boxed(covered, _SLIVER, _BORDER), Spacer(1, 14)]

        for i, topic in enumerate(notes.topics, start=1):
            head = Paragraph(f'<font color="{_ACCENT}">{i}.</font>  {_markup(topic.name, math, 13.5)}', h2)
            points = ListFlowable(
                [ListItem(prose(p), leftIndent=14) for p in topic.points],
                bulletType="bullet", start="•", bulletColor=accent, leftIndent=14, bulletFontSize=9,
            )
            story.append(CondPageBreak(60 * mm))
            story.append(KeepTogether([head, points]))
            if topic.formulas:
                rows: list = []
                for f in topic.formulas:
                    img = math.image(f, 12.5, display=True)
                    rows.append(Image(img[0], width=img[1], height=img[2], hAlign="CENTER") if img
                                else Paragraph(f'<font name="VersaMono">{html.escape(f, quote=False)}</font>', centred))
                    rows.append(Spacer(1, 5))
                story += [Spacer(1, 6), boxed([Paragraph("KEY FORMULAS", label), *rows], _ACCENT_SOFT, _ACCENT_LINE, "CENTER")]
            if topic.example:
                story += [Spacer(1, 6), boxed([Paragraph("EXAMPLE", label), *prose(topic.example, small)],
                                               "#FFFFFF", _BORDER)]
            story.append(Spacer(1, 14))

        story.append(CondPageBreak(40 * mm))
        summary = [Paragraph("SUMMARY", label), *prose(notes.summary)]
        story.append(boxed(summary, _SLIVER, _BORDER, "CENTER"))
        if notes.stopped_at:
            story += [Spacer(1, 10), Paragraph("WHERE YOU STOPPED", label), *prose(notes.stopped_at)]

        def footer(canvas, doc) -> None:
            canvas.saveState()
            canvas.setFont("VersaMono", 7.5)
            canvas.setFillColor(faint)
            canvas.drawString(18 * mm, 10 * mm, "Made by Versa from your chat")
            canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"{doc.page}")
            canvas.setStrokeColor(accent)
            canvas.setLineWidth(2)
            canvas.line(18 * mm, A4[1] - 12 * mm, 34 * mm, A4[1] - 12 * mm)
            canvas.restoreState()

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
            title=f"{notes.title} -- Versa notes", author="Versa",
        )
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return buf.getvalue()


def pdf_filename(notes: Notes) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", notes.title).strip("-").lower()[:50] or "chat"
    return f"versa-notes-{slug}.pdf"


# ------------------------------------------------------------------ routes


class NotesOut(BaseModel):
    id: UUID
    session_id: UUID
    title: str
    topics: list[NoteTopic]
    summary: str
    stopped_at: str | None
    answers: int
    created_at: datetime


class NotesStatusOut(BaseModel):
    """What the Notes sheet opens on: the latest notes (if any), how many
    answers the chat has now, and whether the notes cover all of them."""

    notes: NotesOut | None
    answers: int
    up_to_date: bool
    cost: int


def _out(stored: StoredNotes) -> NotesOut:
    n = stored.notes
    return NotesOut(
        id=stored.id, session_id=stored.session_id, title=n.title, topics=n.topics, summary=n.summary,
        stopped_at=n.stopped_at, answers=stored.answers, created_at=stored.created_at,
    )


LoadHistory = Callable[[UUID], Awaitable[list[HistoryTurn]]]


def build_notes_router(pool: asyncpg.Pool, llm, load_history: LoadHistory, *, sparks=None) -> APIRouter:
    """`load_history(session_id)` is the chat as the app would replay it
    (server.py passes `reconstruct_session_history`). `sparks`: the
    SparkEngine, or None -- nothing is charged."""
    import contextlib

    from versa.profiles import ProfileStore, load_background
    from versa.sparks import ACTION_COSTS

    router = APIRouter(prefix="/api")
    store = NoteStore(pool)
    writer = WriteNotes(llm)

    async def owner(session_id: UUID) -> tuple[UUID, str]:
        row = await pool.fetchrow("SELECT learner_id, app_mode FROM sessions WHERE id = $1", session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown session")
        return row["learner_id"], row["app_mode"]

    async def current(session_id: UUID) -> tuple[str, int, int]:
        return render_chat(await load_history(session_id))

    @router.get("/sessions/{session_id}/notes", response_model=NotesStatusOut)
    async def notes_status(session_id: UUID) -> NotesStatusOut:
        await owner(session_id)
        _, answers, last = await current(session_id)
        latest = await store.latest(session_id)
        return NotesStatusOut(
            notes=_out(latest) if latest else None, answers=answers,
            up_to_date=latest is not None and latest.through_turn >= last,
            cost=ACTION_COSTS.get("generate_notes", 0),
        )

    @router.post("/sessions/{session_id}/notes", response_model=NotesOut)
    async def generate(session_id: UUID) -> NotesOut:
        """Make the notes -- only ever because the learner asked. If nothing
        was studied since the last notes, those come back, free."""
        learner_id, _ = await owner(session_id)
        chat, answers, last = await current(session_id)
        if answers == 0:
            raise HTTPException(status_code=422, detail="Ask Versa something first -- there's nothing to make notes from yet.")
        latest = await store.latest(session_id)
        if latest is not None and latest.through_turn >= last:
            return _out(latest)
        background = await load_background(pool, learner_id)
        try:
            from versa.style_patterns import confirmed_statements

            style = await confirmed_statements(pool, learner_id)
        except Exception:  # notes never fail for want of a style
            logger.warning("notes: style patterns failed for %s", learner_id, exc_info=True)
            style = []
        prompt = notes_prompt(chat, background=background, style=style)
        priced = (sparks.charged(learner_id, "generate_notes", ref={"session_id": str(session_id)})
                  if sparks is not None else contextlib.nullcontext())
        async with priced:
            try:
                notes, raw = await writer.run(prompt)
            except Exception as exc:
                logger.warning("writing notes failed", exc_info=True)
                await store.add(learner_id=learner_id, session_id=session_id, through_turn=last,
                                answers=answers, prompt=prompt, raw_output=None, notes=None, error=repr(exc))
                raise HTTPException(status_code=502, detail="Couldn't write the notes -- try again.") from None
            if notes is None:
                await store.add(learner_id=learner_id, session_id=session_id, through_turn=last,
                                answers=answers, prompt=prompt, raw_output=raw, notes=None,
                                error="unreadable reply")
                raise HTTPException(status_code=502, detail="Couldn't write the notes -- try again.")
            note_id = await store.add(learner_id=learner_id, session_id=session_id, through_turn=last,
                                      answers=answers, prompt=prompt, raw_output=raw, notes=notes, error=None)
        stored = await store.get(note_id)
        assert stored is not None
        return _out(stored)

    @router.get("/sessions/{session_id}/notes/{note_id}/pdf")
    async def notes_pdf(session_id: UUID, note_id: UUID) -> Response:
        learner_id, app_mode = await owner(session_id)
        stored = await store.get(note_id)
        if stored is None or stored.session_id != session_id:
            raise HTTPException(status_code=404, detail="unknown notes")
        name = await ProfileStore(pool).display_name(learner_id)
        mode = {"sandbox": "Sandbox", "topic": "Lesson"}.get(app_mode, "Versa")
        import asyncio

        data = await asyncio.to_thread(render_pdf, stored.notes, learner=name, made=stored.created_at, mode=mode)
        return Response(
            content=data, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{pdf_filename(stored.notes)}"'},
        )

    router.note_store = store  # type: ignore[attr-defined]  # exposed for tests
    return router
