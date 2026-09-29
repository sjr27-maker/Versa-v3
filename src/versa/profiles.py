"""The sign-up profile: what a learner tells Versa about themselves.

Asked once, right after the first sign-in (and editable from Settings):
their name, age, what they're doing now (school / university / working /
other), where (country and state or region -- anywhere in the world), and
the details that pin down their level: board or curriculum and class for
school, institution, course and year for university, field for work. Plus
optional subjects and goals, and consent (their own, and a parent's or
guardian's under 18).

One model call (`ReadSignupProfile`, PROFILE:EXTRACT) reads those answers
into a structured picture -- stage, education system, level, subjects, what
they're working towards, and a likely starting point to build up from. It
also judges whether the stated age fits the stage: people type 4 or 99 to
see what happens, so age never sets the level on its own. When it doesn't
fit, the stage they described wins and the age is left out of every prompt.
The call is recorded to `profile_extractions` (invariant 2's payload in
accounts' own table: there is no session yet). If it fails, the answers are
still kept and rendered as they were given.

`render_background` turns the latest profile into the short "who this
learner is" block the answer, the ambiguity check, the clarifying options,
Learn-a-topic generation and exam syllabus search are given. It is what they
SAID, not something inferred: it doesn't write into claims, facts or
thinking styles, and it is kept out of "where this could go" (invariant 14).

Append-only (CLAUDE.md invariant 18): editing the profile writes a new row;
the latest row is the profile.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from versa.audit import to_jsonable
from versa.llm import LLMClient

logger = logging.getLogger(__name__)

CONSENT_VERSION = "2026-09-29"
MIN_AGE = 5
MAX_AGE = 100
ADULT_AGE = 18
EXTRACT_TIMEOUT_S = 30

Occupation = Literal["school", "university", "working", "other"]
Stage = Literal[
    "primary_school", "middle_school", "secondary_school", "higher_secondary",
    "undergraduate", "postgraduate", "working", "other",
]
_STAGES = set(Stage.__args__)  # type: ignore[attr-defined]


class ConsentIn(BaseModel):
    # "I agree to Versa storing what I type and learn to personalise it"
    data: bool = False
    # under 18: "my parent or guardian knows and agrees"
    guardian: bool = False


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    age: int
    occupation: Occupation
    country: str = Field(min_length=1, max_length=60)
    region: str | None = Field(None, max_length=80)  # state / province / region
    institution: str | None = Field(None, max_length=160)  # school, college or university
    curriculum: str | None = Field(None, max_length=100)  # board / curriculum (CBSE, IB, A-levels...)
    level: str | None = Field(None, max_length=60)  # class / grade / year
    course: str | None = Field(None, max_length=160)  # degree & branch, or field / role at work
    subjects: str | None = Field(None, max_length=400)
    goals: str | None = Field(None, max_length=600)  # exams, projects, why they're here
    consent: ConsentIn

    @model_validator(mode="after")
    def _check(self) -> ProfileIn:
        if not MIN_AGE <= self.age <= MAX_AGE:
            raise ValueError(f"age must be between {MIN_AGE} and {MAX_AGE}")
        if not self.consent.data:
            raise ValueError("consent is needed to create a profile")
        if self.age < ADULT_AGE and not self.consent.guardian:
            raise ValueError("under 18, a parent or guardian needs to agree too")
        for name in ("name", "country", "region", "institution", "curriculum", "level", "course",
                     "subjects", "goals"):
            value = getattr(self, name)
            if isinstance(value, str):
                cleaned = " ".join(value.split())
                setattr(self, name, cleaned or None)
        if not self.name or not self.country:
            raise ValueError("name and country are needed")
        return self


class ExtractedProfile(BaseModel):
    stage: str = "other"
    education_system: str | None = None
    level: str | None = None
    location: str | None = None
    institution: str | None = None
    field: str | None = None
    subjects: list[str] = []
    working_towards: list[str] = []
    starting_point: str | None = None
    age_fits_stage: bool = True
    note: str | None = None


class ProfileOut(BaseModel):
    id: UUID
    answers: dict
    consent: dict
    extracted: ExtractedProfile | None
    created_at: datetime


class ProfileStatusOut(BaseModel):
    complete: bool
    profile: ProfileOut | None
    min_age: int = MIN_AGE
    max_age: int = MAX_AGE
    adult_age: int = ADULT_AGE


def age_warning(answers: ProfileIn) -> str | None:
    """A cheap, deterministic "are you sure?" the app shows before saving
    (never blocks): ages that can't match what they said they're doing."""
    if answers.occupation == "school" and answers.age > 22:
        return f"You said you're {answers.age} and at school."
    if answers.occupation == "university" and answers.age < 15:
        return f"You said you're {answers.age} and at university."
    if answers.occupation == "working" and answers.age < 14:
        return f"You said you're {answers.age} and working."
    return None


# ------------------------------------------------------------------ the model call


def _extract_prompt(answers: dict) -> str:
    lines = "\n".join(f"- {k}: {v}" for k, v in answers.items() if v not in (None, "") and k != "consent")
    return (
        "PROFILE:EXTRACT\n"
        "A new learner signed up to Versa, a learning app, and answered a few questions about "
        "themselves. Read their answers into a short structured picture that lets every later "
        "explanation start at the right level and build upward from there.\n\n"
        f"Their answers (typed by them, may be informal, misspelled or in any language):\n{lines}\n\n"
        "Work out:\n"
        f"- stage: one of {', '.join(sorted(_STAGES))} -- from what they say they're doing "
        "(and their class, year or course), NOT from their age.\n"
        "- education_system: the board, curriculum or university system as it is usually named "
        "(e.g. 'CBSE (India)', 'Kerala State Board', 'IB Diploma', 'GCSE (England)', "
        "'US high school', 'Anna University (India)'), or null if unknown.\n"
        "- level: their class, grade or year in plain words (e.g. 'Class 11', 'Year 2 of a "
        "B.Tech in Computer Science', 'Grade 8'), or null.\n"
        "- location: 'State or region, Country', tidied.\n"
        "- institution: the school, college or university named, tidied, or null.\n"
        "- field: their stream, degree branch, or line of work, or null.\n"
        "- subjects: the subjects they study or care about now (their own, plus the standard "
        "core subjects of their level and system if they named none), at most 8.\n"
        "- working_towards: exams, projects or goals they mentioned (e.g. 'JEE Main 2027', "
        "'semester exams'), at most 4.\n"
        "- starting_point: 2-3 sentences on what a learner at this level and in this system "
        "has most likely already covered, and where explanations should start for them -- "
        "concrete (name topics), never a judgement of the person.\n"
        "- age_fits_stage: false if the age they gave can't plausibly match what they said "
        "they're doing (e.g. 4 and at university, 99 and in Class 7) -- people sometimes type "
        "a silly age; then trust what they said they're doing over the age.\n"
        "- note: one short line on anything else that will matter when teaching them, or null.\n\n"
        "Respond with JSON only: {\"stage\": \"...\", \"education_system\": ..., \"level\": ..., "
        "\"location\": ..., \"institution\": ..., \"field\": ..., \"subjects\": [...], "
        "\"working_towards\": [...], \"starting_point\": ..., \"age_fits_stage\": true|false, "
        "\"note\": ...}"
    )


def _parse_extraction(raw: str) -> ExtractedProfile | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        out = ExtractedProfile.model_validate(data)
    except ValueError:
        return None
    if out.stage not in _STAGES:
        out.stage = "other"
    out.subjects = [s for s in out.subjects if isinstance(s, str) and s.strip()][:8]
    out.working_towards = [s for s in out.working_towards if isinstance(s, str) and s.strip()][:4]
    return out


class ReadSignupProfile:
    """PROFILE:EXTRACT -- fast tier, one call, one corrective retry."""

    node_name = "ReadSignupProfile"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def run(self, answers: dict) -> tuple[ExtractedProfile | None, str, str]:
        """(the extraction or None, the prompt, the raw reply)."""
        prompt = _extract_prompt(answers)
        raw = ""
        for _ in range(2):
            raw = await self._llm.complete(prompt)
            parsed = _parse_extraction(raw)
            if parsed is not None:
                return parsed, prompt, raw
        return None, prompt, raw


# ------------------------------------------------------------------ store


@dataclass
class ProfileRow:
    id: UUID
    learner_id: UUID
    answers: dict
    consent: dict
    extracted: ExtractedProfile | None
    created_at: datetime

    def out(self) -> ProfileOut:
        return ProfileOut(id=self.id, answers=self.answers, consent=self.consent,
                          extracted=self.extracted, created_at=self.created_at)


class ProfileStore:
    """Append-only (invariant 18): an edit is a new row, the latest wins."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record_extraction(self, *, learner_id: UUID, input_json: dict,
                                output_json: dict | None, error: str | None) -> UUID:
        extraction_id = uuid4()
        await self._pool.execute(
            "INSERT INTO profile_extractions (id, learner_id, node_name, input_json, output_json, error) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            extraction_id, learner_id, ReadSignupProfile.node_name, to_jsonable(input_json),
            to_jsonable(output_json) if output_json is not None else None, error,
        )
        return extraction_id

    async def add(self, *, learner_id: UUID, answers: dict, consent: dict,
                  extracted: ExtractedProfile | None, extraction_id: UUID | None) -> ProfileRow:
        profile_id = uuid4()
        await self._pool.execute(
            "INSERT INTO learner_profiles (id, learner_id, answers, consent, extracted, extraction_id) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            profile_id, learner_id, answers, consent,
            extracted.model_dump() if extracted is not None else None, extraction_id,
        )
        latest = await self.latest(learner_id)
        assert latest is not None
        return latest

    @staticmethod
    def _row(row: asyncpg.Record) -> ProfileRow:
        extracted = row["extracted"]
        return ProfileRow(
            id=row["id"], learner_id=row["learner_id"], answers=row["answers"], consent=row["consent"],
            extracted=ExtractedProfile.model_validate(extracted) if extracted else None,
            created_at=row["created_at"],
        )

    async def latest(self, learner_id: UUID) -> ProfileRow | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM learner_profiles WHERE learner_id = $1 ORDER BY created_at DESC, id LIMIT 1",
            learner_id,
        )
        return self._row(row) if row is not None else None

    async def history(self, learner_id: UUID) -> list[ProfileRow]:
        rows = await self._pool.fetch(
            "SELECT * FROM learner_profiles WHERE learner_id = $1 ORDER BY created_at", learner_id
        )
        return [self._row(r) for r in rows]

    async def has_profile(self, learner_id: UUID) -> bool:
        return bool(await self._pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM learner_profiles WHERE learner_id = $1)", learner_id
        ))

    async def display_name(self, learner_id: UUID) -> str | None:
        profile = await self.latest(learner_id)
        if profile is not None and profile.answers.get("name"):
            return str(profile.answers["name"])
        return await self._pool.fetchval(
            "SELECT coalesce(display_name, label) FROM learners WHERE id = $1", learner_id
        )


# ------------------------------------------------------------------ rendering


def _stage_words(extracted: ExtractedProfile | None, answers: dict) -> str:
    if extracted is not None and (extracted.level or extracted.education_system):
        parts = [extracted.level, f"({extracted.education_system})" if extracted.education_system else None]
        text = " ".join(p for p in parts if p)
    else:
        bits = [answers.get("level"), answers.get("curriculum"), answers.get("course")]
        text = ", ".join(b for b in bits if b) or str(answers.get("occupation", ""))
    return text


def profile_lines(profile: ProfileRow | None) -> list[str]:
    """What the learner said at sign-up, one fact per line (no header)."""
    if profile is None:
        return []
    a, x = profile.answers, profile.extracted
    lines: list[str] = []
    stage = _stage_words(x, a)
    location = (x.location if x and x.location else
                ", ".join(p for p in (a.get("region"), a.get("country")) if p))
    institution = (x.institution if x and x.institution else a.get("institution"))
    where = " -- ".join(p for p in (institution, location) if p)
    if stage:
        lines.append(f"Where they are: {stage}" + (f"; {where}" if where else ""))
    elif where:
        lines.append(f"Where they are: {where}")
    field_name = x.field if x and x.field else a.get("course")
    if field_name and a.get("occupation") != "school":
        lines.append(f"Field: {field_name}")
    subjects = ", ".join(x.subjects) if x and x.subjects else a.get("subjects")
    if subjects:
        lines.append(f"Subjects: {subjects}")
    towards = ", ".join(x.working_towards) if x and x.working_towards else a.get("goals")
    if towards:
        lines.append(f"Working towards: {towards}")
    if x and x.starting_point:
        lines.append(f"Likely starting point: {x.starting_point}")
    age = a.get("age")
    if age is not None:
        if x is None or x.age_fits_stage:
            lines.append(f"Age: {age}")
        else:
            lines.append("(Their stated age doesn't fit what they said they're doing -- go by that, not the age.)")
    if x and x.note:
        lines.append(f"Also: {x.note}")
    return lines


def render_background(profile: ProfileRow | None, *, noun: str = "learner") -> str:
    """The "who this learner is" block, or "" when there is no profile."""
    lines = profile_lines(profile)
    if not lines:
        return ""
    return (
        f"\nWho this {noun} is, from what they told Versa when they signed up -- pitch the level, "
        "examples and terminology to it and build up from where they are; never mention it to them, "
        "and let what they actually ask come first:\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n"
    )


def render_exam_hint(profile: ProfileRow | None) -> str:
    """One line for exam syllabus search: the system and level whose syllabus
    to follow. Stated, not inferred -- see exams.py."""
    if profile is None:
        return ""
    x, a = profile.extracted, profile.answers
    system = (x.education_system if x else None) or a.get("curriculum")
    level = (x.level if x else None) or a.get("level")
    location = (x.location if x else None) or ", ".join(
        p for p in (a.get("region"), a.get("country")) if p)
    bits = [b for b in (level, system, location) if b]
    if not bits:
        return ""
    return (
        "\nThe student said they are studying: " + "; ".join(bits)
        + ". If the exam is on their own course, follow that syllabus; otherwise ignore this.\n"
    )


async def load_background(pool: asyncpg.Pool, learner_id: UUID, *, noun: str = "learner") -> str:
    """render_background for a learner, degrading to "" on any failure."""
    try:
        return render_background(await ProfileStore(pool).latest(learner_id), noun=noun)
    except Exception as exc:  # noqa: BLE001 -- a profile must never fail a turn
        logger.warning("profile background failed for %s: %s", learner_id, exc)
        return ""


# ------------------------------------------------------------------ routes


class ProfileSaveOut(BaseModel):
    profile: ProfileOut
    label: str


def build_profiles_router(pool: asyncpg.Pool, llm: LLMClient) -> APIRouter:
    router = APIRouter(prefix="/api")
    store = ProfileStore(pool)
    reader = ReadSignupProfile(llm)

    async def require_learner(learner_id: UUID) -> None:
        if await pool.fetchval("SELECT 1 FROM learners WHERE id = $1", learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")

    @router.get("/learners/{learner_id}/profile", response_model=ProfileStatusOut)
    async def get_profile(learner_id: UUID) -> ProfileStatusOut:
        await require_learner(learner_id)
        latest = await store.latest(learner_id)
        return ProfileStatusOut(complete=latest is not None,
                                profile=latest.out() if latest is not None else None)

    @router.post("/learners/{learner_id}/profile/check")
    async def check_profile(learner_id: UUID, body: ProfileIn) -> dict:
        """Validation plus the "are you sure?" line, without saving."""
        await require_learner(learner_id)
        return {"ok": True, "warning": age_warning(body)}

    @router.post("/learners/{learner_id}/profile", response_model=ProfileSaveOut)
    async def save_profile(learner_id: UUID, body: ProfileIn) -> ProfileSaveOut:
        await require_learner(learner_id)
        answers = body.model_dump(exclude={"consent"})
        consent = {**body.consent.model_dump(), "version": CONSENT_VERSION,
                   "guardian_required": body.age < ADULT_AGE}
        extracted: ExtractedProfile | None = None
        output: dict | None = None
        error: str | None = None
        prompt = _extract_prompt(answers)
        try:
            extracted, _prompt, raw = await asyncio.wait_for(reader.run(answers), EXTRACT_TIMEOUT_S)
            output = {"raw": raw, "parsed": extracted.model_dump() if extracted else None}
            if extracted is None:
                error = "unparseable reply"
        except Exception as exc:  # noqa: BLE001 -- the answers are kept regardless
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("profile extraction failed for %s: %s", learner_id, error)
        extraction_id = await store.record_extraction(
            learner_id=learner_id, input_json={"answers": answers, "prompt": prompt},
            output_json=output, error=error,
        )
        saved = await store.add(learner_id=learner_id, answers=answers, consent=consent,
                                extracted=extracted, extraction_id=extraction_id)
        return ProfileSaveOut(profile=saved.out(), label=body.name)

    return router
