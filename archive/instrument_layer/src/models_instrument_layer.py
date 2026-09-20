"""PARKED: the instrument-layer row shapes, extracted verbatim from
`probe/models.py` (everything from the "instrument layer" marker to the
end of that file). To restore, append this file's body below the
imports to `probe/models.py` (it needs `model_validator` imported from
pydantic there alongside `field_validator`). See README.md in this
directory."""
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from probe.models import ApproachAxis, StatedPreferenceLabel, _utcnow


# ─────────────────────────── instrument layer (migrations 050-052) ────
#
# See instruments.py's own module docstring for the full design record
# (the calibration gate this foundation exists to be checked against
# before a second instrument gets built). These are the DB row shapes
# only — stores, the pure interpretation function, and the one
# hand-authored contract live in instruments.py, the same split
# claims.py/models.py already use.


class InstrumentPrimitive(str, Enum):
    """The closed set of instrument shapes. Only LOCATE is implemented
    in this build — the other five are defined now so the schema
    (InteractionContract/Instrument/instrument_events' event_type) is
    proven against all six from the start rather than reshaped per
    instrument later, which is how event schemas rot."""

    CHOOSE = "choose"
    ORDER = "order"
    LOCATE = "locate"
    ADJUST = "adjust"
    PREDICT = "predict"
    CONSTRUCT = "construct"


class InstrumentEventType(str, Enum):
    """Shared across every primitive. `start`/`submit`/`abandon` are
    what let ANY primitive distinguish completion from abandonment and
    carry timing (`elapsed_ms`, on every row); `move`/`revise` are what
    let any primitive with a revision trail record one — not every
    primitive uses every event type, but the enum itself is fixed
    across all six so a future primitive never needs a schema change
    to fit."""

    START = "start"
    MOVE = "move"
    REVISE = "revise"
    CLICK = "click"
    VALUE_CHANGE = "value_change"
    SUBMIT = "submit"
    ABANDON = "abandon"
    HINT_REQUEST = "hint_request"


class MeasurementKind(str, Enum):
    """Whether a method's evidence answers "which framing they want"
    (PREFERENCE) or "whether they can do the thing" (PERFORMANCE) —
    see method_capabilities.py's own module docstring for the category
    error conflating the two produces. A PERFORMANCE contract must
    target a `CapabilityClaim` (by `skill`), never a preference `Claim`
    (by `axis`+`value`) — `InteractionContract`'s own validator below
    enforces this shape."""

    PREFERENCE = "preference"
    PERFORMANCE = "performance"


class CapabilityLabel(str, Enum):
    """Closed vocabulary for capability/mastery claims — what a
    learner CAN DO, observed directly, never a preference-vocabulary
    label standing in for it. This is the fix for a real incident:
    `locate`/`predict` originally targeted `StatedPreferenceLabel`
    values (WANTS_STEPS_SHOWN, RULE_BEFORE_EXAMPLE) despite measuring
    performance, which meant capability observations were silently
    accumulating into a preference claim's confidence — "can trace a
    swap correctly" is not a preference, and the two can diverge
    (someone can prefer worked examples and still be perfectly able to
    derive forward without them)."""

    TRACES_WORKED_STEPS = "traces_worked_steps"  # locate: catches a seeded error by following each step
    DERIVES_FORWARD = "derives_forward"  # predict: predicts a mechanism's outcome before it's revealed
    OTHER = "other"


class CapabilityStatus(str, Enum):
    CANDIDATE = "candidate"
    PROMOTED = "promoted"


class CapabilityClaim(BaseModel):
    """The capability-side counterpart to `Claim` — same append-only
    discipline (statement/test/skill fixed at creation; confidence/
    status/updated_at the only mutable fields, via `capability.
    CapabilityClaimStore.refresh`) but a SEPARATE table (migration 054)
    and a separate closed vocabulary (`CapabilityLabel`, never
    `StatedPreferenceLabel`).

    No axis: capability isn't bidirectional — there's no "opposite"
    pole to a skill the way each `ApproachAxis` has two named poles,
    so nothing here plays the role axis/`find_by_axis` play for
    preference claims. Matching is exact-skill
    (`CapabilityClaimStore.find_by_skill`), not axis-sharing-then-
    similarity: at most one live claim per (learner, skill) should
    exist. No `statement_embedding` either — with no similarity-based
    fallback matching path (skill is always known, never None the way
    axis can be), there is nothing for an embedding to disambiguate."""

    id: UUID = Field(default_factory=uuid4)
    learner_id: UUID
    statement: str
    test: str
    skill: CapabilityLabel
    confidence: float
    write_policy: ClaimWritePolicy
    status: CapabilityStatus = CapabilityStatus.CANDIDATE
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CapabilityEvidence(BaseModel):
    """The capability-side counterpart to `ClaimEvidence` — same
    fields, same eligibility gate (`test_fired AND
    contradiction_was_possible`), `skill` where `ClaimEvidence` has
    `axis`+`topic` (capability evidence is always exactly one skill,
    no topic-fallback branch needed since there's no axis-less case).
    Fully append-only, no mutable field except `provenance_note` (same
    one sanctioned exception `ClaimEvidence` has, for the identical
    reason)."""

    id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    learner_id: UUID
    interaction_id: UUID
    direction: EvidenceDirection
    skill: CapabilityLabel
    session_id: UUID
    test_fired: bool
    contradiction_was_possible: bool
    created_at: datetime = Field(default_factory=_utcnow)
    provenance_note: str | None = None
    source: EvidenceSource = EvidenceSource.INSTRUMENT


class InteractionContract(BaseModel):
    """One deterministic recipe for turning an instrument's event
    stream into supports/contradicts/uninformative — see
    `instruments.interpret_instrument`'s own docstring for how the
    three predicate lists are read. Append-only: no field here is ever
    edited after creation (migration 050's own header).

    `measures` decides which of two shapes this contract has: a
    PREFERENCE contract sets `target_axis`+`target_value` (and, when
    `target_claim_id` is set, that id names a `claims` row); a
    PERFORMANCE contract sets `target_skill` instead (`target_claim_id`,
    when set, names a `capability_claims` row instead). Never both —
    the validator below enforces this. `target_claim_id` carries no
    foreign key at the database level for exactly this reason: it is
    POLYMORPHIC across two tables, and which one it points into is the
    caller's responsibility to get right from `measures` — see
    `MeasurementKind`'s own docstring for the incident this shape
    change closes.

    `target_claim_id` is nullable — a contract may target an existing
    claim (evidence attaches directly, no matching needed: the path
    this build implements) or, in a future not built here, a candidate
    trait with no claim row yet ("no instrument generation yet").

    `uninformative_when` must be non-empty — enforced here (fail fast,
    before ever reaching the database's own CHECK constraint) AND at
    the database (the authoritative backstop): every contract must
    name at least one outcome it cannot read, or post-hoc
    interpretation fills the gap, which is where confabulation lives.
    """

    id: UUID = Field(default_factory=uuid4)
    target_claim_id: UUID | None = None
    measures: MeasurementKind
    target_axis: ApproachAxis | None = None
    target_value: StatedPreferenceLabel | None = None
    target_skill: CapabilityLabel | None = None
    primitive: InstrumentPrimitive
    supports_when: list[dict]
    contradicts_when: list[dict]
    uninformative_when: list[dict]
    generator_version: str
    created_at: datetime = Field(default_factory=_utcnow)

    @field_validator("uninformative_when")
    @classmethod
    def _uninformative_when_must_be_nonempty(cls, v: list[dict]) -> list[dict]:
        if not v:
            raise ValueError(
                "uninformative_when must name at least one outcome the contract "
                "cannot read (abandonment, timeout, an unanticipated response) — "
                "an empty list lets post-hoc interpretation fill the gap"
            )
        return v

    @model_validator(mode="after")
    def _target_shape_matches_measures(self) -> "InteractionContract":
        if self.measures is MeasurementKind.PREFERENCE:
            if self.target_axis is None or self.target_value is None:
                raise ValueError("a PREFERENCE contract must set target_axis and target_value")
            if self.target_skill is not None:
                raise ValueError("a PREFERENCE contract must not set target_skill")
        else:
            if self.target_skill is None:
                raise ValueError("a PERFORMANCE contract must set target_skill")
            if self.target_axis is not None or self.target_value is not None:
                raise ValueError("a PERFORMANCE contract must not set target_axis/target_value")
        return self


class Instrument(BaseModel):
    """One PRESENTATION of a contract to a learner. Append-only in this
    codebase's established sense: `completed_at`/`abandoned` resolve
    via UPDATE once (same status-transition-via-UPDATE convention as
    claims.status), every other field is fixed at creation.

    `interaction_id`/`session_id` correlate this presentation with the
    rest of the learner's history, but `interaction_id` is NOT foreign-
    keyed to `interactions` (migration 051's own header) — an
    instrument is deliberately outside the turn-based chat flow this
    build leaves unchanged."""

    id: UUID = Field(default_factory=uuid4)
    interaction_id: UUID
    learner_id: UUID
    session_id: UUID
    contract_id: UUID
    primitive: InstrumentPrimitive
    spec: dict
    presented_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    abandoned: bool = False


class InstrumentEvent(BaseModel):
    """One raw event from an instrument's presentation — fully append-
    only, no mutable field at all, not even a status (migration 052's
    own header): nothing ever writes an interpretation back onto a row
    here. `interpret_instrument` reads this table; nothing writes to it
    except the original capture.

    `seq` is caller-supplied (app.js assigns it as events fire), not a
    DB identity column — interpretation needs a deterministic,
    client-known ordering, and the database rejects two events
    claiming the same position rather than silently reordering them.
    `elapsed_ms` is measured from the instrument's `presented_at`."""

    id: UUID = Field(default_factory=uuid4)
    instrument_id: UUID
    seq: int
    event_type: InstrumentEventType
    payload: dict
    elapsed_ms: int
    created_at: datetime = Field(default_factory=_utcnow)
