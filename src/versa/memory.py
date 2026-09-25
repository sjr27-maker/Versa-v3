"""The memory layer — a derived, searchable record built on top of
minimal_branch (ReasoningMode.DISAMBIGUATE), NOT a replacement for
anything DisambiguationStore already does: migration 029's tables stay
exactly as they are, untouched by this module.

WHY THIS EXISTS: every disambiguation-mode turn today is answered
fresh, with no memory of how past uncertainty in THIS student was
actually resolved. Two paths converge on the same underlying store,
`learner_facts` (migration 030):

- WITHIN a session: `EmbedAndSearchFacts` + `ConfirmFactMatch` run
  before `AssessAndBranch` on every fresh message (see loop.py's
  `_handle_disambiguation_turn`). If a past fact — possibly written
  just one turn ago, in this same session — strongly matches and a
  confirmation call agrees it actually resolves the current message,
  branching is skipped entirely and `FinalAnswer` runs directly with
  that fact as context. This is deliberately visible and auditable,
  never a silent shortcut: `turn_diagnostics.branching_skipped_by_memory`
  (migration 031) is set whenever it happens, alongside which fact
  caused it.
- ACROSS sessions: `SummarizeSessionPath` labels the abstract
  order-structure of one session's facts (concrete-before-abstract,
  or the reverse — never the specific topic), and
  `ConfirmThinkingStyleMatch` decides whether that matches an existing
  `thinking_style_candidates` row closely enough to count as one more
  independent confirmation. This ONLY ever runs as a background,
  session-end step (`SessionLoop.consolidate_session`) — never live,
  mid-turn (see that method's own docstring for why).

A THIRD, additive element (migration 056, see IDEAS.md's "Store the
reason" entry): `WriteLearnerFact` may also record `reason` — why a
resolution fit THIS student, when this specific exchange actually
suggests one — with its own, independently-searched embedding.
`EmbedAndSearchFacts` runs both searches and reports which one
produced the winning candidate, so real usage can show whether a
reason-based match earns any more trust than a situation/resolution
one before it is ever treated as primary — see that class's own
docstring for the mechanics, and `MemoryConfig.reason_similarity_
threshold` for why it gets its own, independently-tunable bar.

THE ONE RULE EVERYTHING HERE SERVES: nothing below may assert a
pattern before it's actually been earned across independent evidence.
Concretely:
  - `EmbedAndSearchFacts`' vector search only ever FINDS a candidate to
    ask about; only `ConfirmFactMatch`'s structured yes/no is allowed
    to actually skip branching. Cosine similarity alone never decides
    anything live.
  - Likewise, `thinking_style_candidates.confirmation_count`/
    `session_ids` only ever grow via `ConfirmThinkingStyleMatch` saying
    yes, never from a candidate merely coming back as the nearest
    vector match.
  - `ThinkingStyleCandidate` only gets fed into any live session's
    prompt once `confirmation_count` crosses
    `MemoryConfig.thinking_style_promotion_threshold` — a config value
    deliberately set well above "2-3 sessions could be coincidence"
    (see MemoryConfig's own docstring for the exact number and why).

Append-only (CLAUDE.md invariant 10): no delete method on either
store, no DELETE SQL anywhere in this module or migration 030.
`learner_facts` rows are never mutated after insert; a
`thinking_style_candidates` row's `status` moves candidate ->
confirmed/retired via UPDATE only, same resurrection-over-deletion
principle as HypothesisStore's tiers.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa import embeddings
from versa.embeddings import EmbeddingClient
from versa.llm import LLMClient
from versa.models import (
    ExtractedFact,
    FactMatchConfirmation,
    FactSearchResult,
    LearnerFact,
    LearnerFactType,
    PathSummary,
    ReasonRelevance,
    ThinkingStyleCandidate,
    ThinkingStyleConfirmation,
    ThinkingStyleStatus,
)
from versa.row_mapping import assert_row_consumed


class MemoryConfig(BaseModel):
    """Every threshold the memory layer uses, named and justified here
    rather than hardcoded at each call site — same "config value, not
    a magic number" discipline as ValueFunctionConfig/
    ReasoningBudgetConfig elsewhere in this codebase.
    """

    # Cosine similarity a learner_facts match must clear before
    # ConfirmFactMatch is even asked about it (step 3-4).
    #
    # Was 0.85 — a hand-picked "deliberately strict" placeholder. Now
    # measured against real gemini-embedding-001 output (768-dim,
    # asymmetric RETRIEVAL_QUERY/DOCUMENT — see embeddings.TASK_*):
    #   * a genuine paraphrase match ("which rule for x^5?" vs a stored
    #     fact about the power rule for x^5)      -> ~0.80
    #   * topically adjacent but a different question
    #     ("which rule for x^5?" vs a fact about *disambiguating the
    #      word 'derivatives'")                    -> ~0.57
    #   * different topic entirely
    #     (a limits question vs a derivatives fact) -> ~0.55
    # 0.85 sat ABOVE the paraphrase band, so the pre-check could
    # essentially never fire on real usage. 0.72 clears real matches
    # while still rejecting the ~0.57 "related but different" band with
    # margin — and it is only the FIRST of two gates (ConfirmFactMatch's
    # structured yes/no still has to agree before any branching is
    # skipped), so a slightly looser vector bar just means "ask the
    # confirming LLM a bit more often", never "skip on similarity alone".
    fact_similarity_threshold: float = 0.72
    # The reason-embedding search's own bar (migration 056) -- a
    # SEPARATE knob from fact_similarity_threshold above on purpose,
    # not reused: reason text ("this student needs the concrete
    # example first") is shorter and more abstract than a situation+
    # resolution pair, so there is no reason to assume the same
    # embedding-behaviour measurements that picked 0.72 for that pair
    # transfer here. Starts equal to fact_similarity_threshold only
    # because nothing has been measured yet for this path specifically
    # (see IDEAS.md's "Store the reason" entry) -- it exists as its own
    # field precisely so it CAN move independently once real reason
    # matches accumulate to measure against.
    reason_similarity_threshold: float = 0.72
    # Same reasoning / same measured embedding behaviour, for step 7's
    # search over thinking_style_candidates' path_summary embeddings —
    # an existing candidate is only worth ASKING ConfirmThinkingStyleMatch
    # about above this bar (and that call is again a second gate).
    thinking_style_similarity_threshold: float = 0.72
    # How many INDEPENDENT sessions must confirm the same order-
    # structure before it becomes a durable, LLM-facing claim (step 8).
    # Deliberately well above "2-3, could be coincidence" per this
    # feature's own instructions. 5 is chosen as a middle ground: high
    # enough that five separate sessions agreeing is a real, repeated
    # pattern rather than noise, low enough to be reachable without
    # requiring the full 12-then-13 horizon the original framing used
    # as an illustration (that framing was about how far payoff can
    # extend, not a literal minimum session count). Not measured
    # against real data yet — a config value specifically so it can
    # move once real cross-session data exists to tune it against.
    thinking_style_promotion_threshold: int = 5
    # CLI-only: a session below this many turns is not eligible for
    # automatic consolidation on interactive-loop exit (see cli.py's
    # run_interactive) — a 2-turn session that got interrupted
    # shouldn't feed the thinking-style detector at all. 6 is half of
    # this codebase's own reference 12-turn scripted comparison script
    # (early-signal, shaped-material, pivot, callback phases) — enough
    # turns to plausibly contain a real order, not just an opening
    # exchange. The standalone `versa consolidate-session` command is
    # unaffected by this — an explicit trigger is not a guess.
    min_turns_for_cli_auto_consolidation: int = 6
    # How many nearest candidates search_similar_facts/
    # search_similar_thinking_styles return — only the top one is ever
    # acted on (see EmbedAndSearchFacts), the rest exist purely so a
    # caller inspecting the raw search can see what else was close.
    search_limit: int = 5


def _fact_prompt(
    fact_type: LearnerFactType,
    student_message: str,
    tutor_message: str,
    branch_statements: list[str] | None,
) -> str:
    # The third field below (reason) is deliberately optional in both
    # prompt variants and in the schema that backs WRITE:FACT
    # (llm.py's _SCHEMA_BY_PREFIX) -- asking the model to abstain
    # rather than always fill it in is what keeps this from becoming
    # the confabulation-on-every-episode failure named in IDEAS.md's
    # "Store the reason" entry. situation/resolution stay mandatory:
    # only reason gets the "leave it out" instruction.
    _REASON_INSTRUCTION = (
        '"reason" (OPTIONAL): only if THIS specific exchange actually '
        "shows something about how this student thinks or what they "
        "needed — e.g. they asked for the concrete example before the "
        "rule, or named their own approach explicitly. Leave it out "
        "entirely if the resolution above already says everything "
        "there is to say; do not invent a reason just to fill this in. "
        "This may be shown directly to the student later as \"it looks "
        'like this is because {reason}\" -- phrase it in the second '
        'person, present tense, so it reads naturally spliced in there '
        '(e.g. "you tend to want the concrete example before the '
        'rule", not "wanted the concrete example" or "the student '
        'wants").\n'
    )
    if fact_type is LearnerFactType.BRANCH_RESOLUTION:
        listing = "\n".join(f"- {s}" for s in (branch_statements or []))
        return (
            "WRITE:FACT\n"
            "An earlier message from this student was ambiguous. Here "
            f"were the distinct readings that were on offer:\n{listing}\n\n"
            f"The student's message that resolved it: {student_message}\n"
            f"How the tutor responded once resolved: {tutor_message}\n\n"
            "Write short, plain-English notes, in the student's own "
            "terms where possible, for a future tutor to read back "
            "before ever asking something similar again:\n"
            '"situation": what was actually unclear or ambiguous\n'
            '"resolution": which reading was correct and what was chosen\n'
            f"{_REASON_INSTRUCTION}\n"
            'Respond with JSON: {"situation": "...", "resolution": "...", '
            '"reason": "..." or omit it entirely}'
        )
    return (
        "WRITE:FACT\n"
        f"The student asked or did this: {student_message}\n"
        f"The tutor answered: {tutor_message}\n\n"
        "Write short, plain-English notes, in the student's own "
        "terms where possible, for a future tutor to read back before "
        "answering something similar again:\n"
        '"situation": what the student was actually asking or doing\n'
        '"resolution": what was answered and how\n'
        f"{_REASON_INSTRUCTION}\n"
        'Respond with JSON: {"situation": "...", "resolution": "...", '
        '"reason": "..." or omit it entirely}'
    )


def _parse_extracted_fact(raw: str, fallback_situation: str, fallback_resolution: str) -> ExtractedFact:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        return ExtractedFact(situation=fallback_situation, resolution=fallback_resolution)
    situation = parsed.get("situation")
    resolution = parsed.get("resolution")
    # No fallback for reason, unlike situation/resolution above: a
    # missing or blank reason is a valid "nothing more to say" answer,
    # not a parse failure to paper over with raw turn text.
    reason = parsed.get("reason")
    return ExtractedFact(
        situation=str(situation) if situation else fallback_situation,
        resolution=str(resolution) if resolution else fallback_resolution,
        reason=str(reason) if reason else None,
    )


def _confirm_fact_match_prompt(
    matched_situation: str, matched_resolution: str, current_message: str
) -> str:
    return (
        "CONFIRM:FACT_MATCH\n"
        f"Earlier, this exact student faced this situation: {matched_situation}\n"
        f"It was resolved like this: {matched_resolution}\n\n"
        f"The student's current message is: {current_message}\n\n"
        "Does the earlier resolution directly resolve the current "
        "message, well enough that asking the student to clarify again "
        "would be redundant? Say no if the current message is only "
        "related in topic but is actually a new or different question — "
        "do not force a match. A real \"no\" is a correct, useful "
        "answer here, not a failure to avoid.\n"
        'Respond with JSON: {"resolves": true or false}'
    )


def _parse_fact_match_confirmation(raw: str) -> FactMatchConfirmation:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return FactMatchConfirmation(resolves=False)
    if not isinstance(parsed, dict):
        return FactMatchConfirmation(resolves=False)
    return FactMatchConfirmation(resolves=bool(parsed.get("resolves", False)))


class EmbedAndSearchFacts:
    """Step 3: the semantic pre-check's retrieval half. Embeds the
    current message and searches this learner's `learner_facts` by
    cosine similarity — a pure retrieval step, never itself a
    live-affecting judgment (see module docstring). Only ever returns
    a populated match when similarity clears the relevant threshold;
    below that, this is correctly reported as "nothing worth using,"
    not a weak match.

    Runs TWO independent searches (migration 056) — the existing
    situation+resolution `embedding`, and the additive `reason_embedding`
    — and returns whichever single candidate is the best real match
    (`FactSearchResult.matched_via` names which one). This is the
    "add it alongside, don't replace" mitigation plan from IDEAS.md's
    "Store the reason" entry: both paths feed the SAME downstream gate
    (`ConfirmFactMatch` still has to agree either way), so this never
    makes a skip more likely on an unproven signal — it only changes
    WHICH stored fact gets offered to that gate.

    Fast in spirit (an embedding call, not a generation call), but
    still a real API call — `last_call_count` counts it toward
    MAX_CALLS_PER_TURN like everything else. Both searches below reuse
    the ONE query embedding computed here; running two SQL searches on
    one existing embedding costs no extra API call.
    """

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        fact_store: LearnerFactStore,
        config: MemoryConfig,
    ) -> None:
        self._embed = embedding_client
        self._facts = fact_store
        self._config = config
        self.last_call_count: int = 0

    async def run(self, learner_id: UUID, message: str) -> FactSearchResult:
        self.last_call_count = 0
        # The retrieval QUERY side of an asymmetric pair — the stored
        # facts are embedded as DOCUMENTs (see WriteLearnerFact).
        embedding = await self._embed.embed(message, task_type=embeddings.TASK_QUERY)
        self.last_call_count += 1

        situation_matches = await self._facts.search_similar(
            learner_id, embedding, limit=self._config.search_limit
        )
        situation_hit = situation_matches[0] if situation_matches else None

        # Naturally empty until this learner has at least one fact with
        # a recorded reason — search_similar_by_reason filters to
        # reason_embedding IS NOT NULL at the SQL level, not a special
        # case handled here.
        reason_matches = await self._facts.search_similar_by_reason(
            learner_id, embedding, limit=self._config.search_limit
        )
        reason_hit = reason_matches[0] if reason_matches else None

        situation_clears = (
            situation_hit is not None
            and situation_hit[1] >= self._config.fact_similarity_threshold
        )
        reason_clears = (
            reason_hit is not None
            and reason_hit[1] >= self._config.reason_similarity_threshold
        )

        if situation_clears and reason_clears:
            # Both cleared their own bar -- higher similarity wins; an
            # exact tie goes to situation/resolution, the proven path,
            # not reason, which is still being measured (see the
            # mitigation plan referenced above: reason must earn
            # primacy, never default to it).
            if reason_hit[1] > situation_hit[1]:
                fact, similarity, matched_via = *reason_hit, "reason"
            else:
                fact, similarity, matched_via = *situation_hit, "situation_resolution"
        elif reason_clears:
            fact, similarity, matched_via = *reason_hit, "reason"
        elif situation_clears:
            fact, similarity, matched_via = *situation_hit, "situation_resolution"
        else:
            # Neither cleared its bar -- report whichever near-miss was
            # closer, purely for node_calls visibility (no
            # matched_fact_id: this is not a real match), same spirit
            # as before this search was dual.
            candidates = [h for h in (situation_hit, reason_hit) if h is not None]
            if not candidates:
                return FactSearchResult()
            _, best_similarity = max(candidates, key=lambda h: h[1])
            return FactSearchResult(similarity=best_similarity)

        return FactSearchResult(
            matched_fact_id=fact.id,
            situation=fact.situation,
            resolution=fact.resolution,
            reason=fact.reason,
            similarity=similarity,
            matched_via=matched_via,
        )


class ConfirmFactMatch:
    """Step 4's structured judgment call — the ONLY thing allowed to
    turn a strong vector-similarity hit into an actual skip-branching
    decision (see module docstring)."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count: int = 0

    async def run(
        self, matched_situation: str, matched_resolution: str, current_message: str
    ) -> FactMatchConfirmation:
        self.last_call_count = 0
        raw = await self._llm.complete(
            _confirm_fact_match_prompt(matched_situation, matched_resolution, current_message)
        )
        self.last_call_count += 1
        return _parse_fact_match_confirmation(raw)


def _confirm_reason_relevance_prompt(reason: str, current_message: str) -> str:
    return (
        "CONFIRM:REASON_RELEVANT\n"
        f"A pattern was previously noticed about this student: {reason}\n\n"
        f"Their current message is: {current_message}\n\n"
        "Does the current message still fit wanting that, or does it "
        "already say the opposite -- e.g. asking for the reverse, "
        "saying to stop doing it, or explicitly changing their mind? "
        "Say true if the message is neutral or still consistent with "
        "the pattern (an ordinary question, unrelated topic, or a "
        "genuine repeat of it) -- only say false when the message "
        "itself already, unambiguously contradicts the pattern.\n"
        'Respond with JSON: {"still_applies": true or false}'
    )


def _parse_reason_relevance(raw: str) -> ReasonRelevance:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ReasonRelevance(still_applies=True)
    if not isinstance(parsed, dict):
        return ReasonRelevance(still_applies=True)
    return ReasonRelevance(still_applies=bool(parsed.get("still_applies", True)))


class ConfirmReasonRelevance:
    """A cheap gate in front of the reason-confirmation offer
    (loop.py's `matched_via == "reason"` branch) — see IDEAS.md's "ask
    for confirmation directly" entry and its own follow-up finding.

    That entry deliberately skipped `ConfirmFactMatch`-style judgment
    for a reason match, on the reasoning that a direct yes/no from the
    student is stronger evidence than another LLM's guess. A live run
    found the gap that leaves open: `EmbedAndSearchFacts`' reason
    search is pure cosine similarity, and similarity cannot tell "the
    student still wants X" apart from "the student just said to stop
    doing X" — the two are topically almost identical text. Confirmed
    concretely: an explicit reversal of a stated preference ("forget
    examples, just give me the abstract definition") still matched the
    stored reason and got offered back as "is that right?", as if it
    were being reconfirmed rather than rejected.

    This does not reintroduce `ConfirmFactMatch` for reason matches in
    general — a genuine match still goes straight to the student, no
    LLM standing in for their answer. It only screens out the one
    failure mode where the CURRENT message has already, by itself,
    answered the question the confirmation was about to ask."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count: int = 0

    async def run(self, reason: str, current_message: str) -> ReasonRelevance:
        self.last_call_count = 0
        raw = await self._llm.complete(
            _confirm_reason_relevance_prompt(reason, current_message)
        )
        self.last_call_count += 1
        return _parse_reason_relevance(raw)


class WriteLearnerFact:
    """Step 5: writes exactly one fact per turn that actually resolved
    something (a click, or a direct answer — never a turn that only
    raised options with nothing decided yet). One LLM call extracts
    `situation`/`resolution` (and optionally `reason`) in plain
    English; one embedding call embeds situation+resolution as before,
    and a SECOND embedding call runs ONLY when a reason was actually
    extracted (migration 056) — no reason, no wasted call. The
    resulting `LearnerFact` is persisted directly (same "a node
    performs its own store write" as `Update`'s `HypothesisStore.add()`
    call) — see `ExtractedFact` for why the node's *return value*
    deliberately excludes the embeddings and ids the persisted row
    actually has.

    A parse failure degrades to the raw student/tutor text as-is for
    situation/resolution (still a usable, if less polished, fact)
    rather than losing the turn's memory-writing entirely — same
    "never crash on malformed model output" discipline as every other
    parse-and-validate node. `reason` has no such fallback: a missing
    reason just means none was written this turn, never a degraded
    stand-in (see `_parse_extracted_fact`).
    """

    def __init__(
        self,
        llm: LLMClient,
        embedding_client: EmbeddingClient,
        fact_store: LearnerFactStore,
    ) -> None:
        self._llm = llm
        self._embed = embedding_client
        self._facts = fact_store
        self.last_call_count: int = 0

    async def run(
        self,
        fact_type: LearnerFactType,
        learner_id: UUID,
        session_id: UUID,
        turn_index: int,
        source_turn_id: UUID,
        student_message: str,
        tutor_message: str,
        branch_statements: list[str] | None = None,
    ) -> ExtractedFact:
        self.last_call_count = 0
        raw = await self._llm.complete(
            _fact_prompt(fact_type, student_message, tutor_message, branch_statements)
        )
        self.last_call_count += 1
        extracted = _parse_extracted_fact(raw, student_message, tutor_message)

        # The retrieval DOCUMENT side of an asymmetric pair — searched
        # by EmbedAndSearchFacts' TASK_QUERY embedding of a later
        # message. situation+resolution so a query phrased like either
        # the original question or the answer can surface it.
        embedding = await self._embed.embed(
            f"{extracted.situation}\n{extracted.resolution}",
            task_type=embeddings.TASK_DOCUMENT,
        )
        self.last_call_count += 1

        # Second, INDEPENDENT document embedding (migration 056) --
        # only when a reason was actually extracted. No reason means no
        # embedding, no extra call, and reason_embedding stays NULL, so
        # search_similar_by_reason never surfaces this row (see that
        # method's own comment).
        reason_embedding: list[float] | None = None
        if extracted.reason:
            reason_embedding = await self._embed.embed(
                extracted.reason, task_type=embeddings.TASK_DOCUMENT
            )
            self.last_call_count += 1

        await self._facts.add(
            LearnerFact(
                learner_id=learner_id,
                session_id=session_id,
                turn_index=turn_index,
                fact_type=fact_type,
                situation=extracted.situation,
                resolution=extracted.resolution,
                embedding=embedding,
                reason=extracted.reason,
                reason_embedding=reason_embedding,
                source_turn_id=source_turn_id,
            )
        )
        return extracted


def _summarize_path_prompt(facts: list[LearnerFact]) -> str:
    listing = "\n".join(
        f"{i + 1}. situation: {f.situation}\n   resolution: {f.resolution}"
        for i, f in enumerate(facts)
    )
    return (
        "SUMMARIZE:PATH\n"
        "Below is one student's resolutions this session, in the exact "
        f"order they occurred:\n{listing}\n\n"
        "Label the STRUCTURE of this order — the abstract shape of how "
        "this student moved through material, not the specific topic "
        "or content (e.g. \"concrete example requested before abstract "
        "definition, repeatedly\" or \"answers accepted directly, only "
        "asks for the underlying reason afterward\"). It must read as "
        "true regardless of what subject a future session is about.\n"
        'Respond with JSON: {"summary": "..."}'
    )


def _parse_path_summary(raw: str) -> PathSummary:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    summary = parsed.get("summary") if isinstance(parsed, dict) else None
    return PathSummary(summary=str(summary) if summary else "")


class SummarizeSessionPath:
    """Step 6 — background-only (see `SessionLoop.consolidate_session`):
    labels one session's ordered facts by the abstract STRUCTURE of
    their order, never their specific content. This alone asserts
    nothing about the learner; it is only ever a candidate's proposed
    label until independently confirmed (see `ThinkingStyleCandidate`).
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count: int = 0

    async def run(self, facts: list[LearnerFact]) -> PathSummary:
        self.last_call_count = 0
        raw = await self._llm.complete(_summarize_path_prompt(facts))
        self.last_call_count += 1
        return _parse_path_summary(raw)


def _confirm_thinking_style_prompt(existing_path_summary: str, new_path_summary: str) -> str:
    return (
        "CONFIRM:THINKING_STYLE\n"
        f"An existing hypothesized pattern for this learner: {existing_path_summary}\n"
        f"This session's own labeled order-structure: {new_path_summary}\n\n"
        "Do these genuinely share the same underlying order-structure, "
        "or is the resemblance only superficial (similar wording, "
        "different actual structure)? Do not force it — a real \"no\" "
        "is a correct, useful answer, not a failure to avoid.\n"
        'Respond with JSON: {"confirms": true or false}'
    )


def _parse_thinking_style_confirmation(raw: str) -> ThinkingStyleConfirmation:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ThinkingStyleConfirmation(confirms=False)
    if not isinstance(parsed, dict):
        return ThinkingStyleConfirmation(confirms=False)
    return ThinkingStyleConfirmation(confirms=bool(parsed.get("confirms", False)))


class ConfirmThinkingStyleMatch:
    """Step 7's structured judgment call — the ONLY thing allowed to
    grow an existing ThinkingStyleCandidate's confirmation_count/
    session_ids (see module docstring). Background-only, same as
    SummarizeSessionPath."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count: int = 0

    async def run(
        self, existing_path_summary: str, new_path_summary: str
    ) -> ThinkingStyleConfirmation:
        self.last_call_count = 0
        raw = await self._llm.complete(
            _confirm_thinking_style_prompt(existing_path_summary, new_path_summary)
        )
        self.last_call_count += 1
        return _parse_thinking_style_confirmation(raw)


class LearnerFactStore:
    """Append-only store for `learner_facts` (migration 030). See
    CLAUDE.md invariant 10: no delete method, no DELETE SQL.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(self, fact: LearnerFact) -> LearnerFact:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO learner_facts (
                    id, learner_id, session_id, turn_index, fact_type,
                    situation, resolution, embedding, reason, reason_embedding,
                    source_turn_id, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                fact.id,
                fact.learner_id,
                fact.session_id,
                fact.turn_index,
                fact.fact_type.value,
                fact.situation,
                fact.resolution,
                fact.embedding,
                fact.reason,
                fact.reason_embedding,
                fact.source_turn_id,
                fact.created_at,
            )
        return fact

    async def search_similar(
        self, learner_id: UUID, embedding: list[float], limit: int = 5
    ) -> list[tuple[LearnerFact, float]]:
        """Nearest `limit` facts for this learner by cosine similarity
        over `embedding` (situation+resolution; pgvector's `<=>`
        cosine-distance operator, similarity = 1 - distance), nearest
        first. Empty for a learner with no facts yet — the expected
        shape before any fact has ever been written for them, not an
        error."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *, 1 - (embedding <=> $2) AS similarity
                FROM learner_facts
                WHERE learner_id = $1
                ORDER BY embedding <=> $2
                LIMIT $3
                """,
                learner_id,
                embedding,
                limit,
            )
        return [self._row_to_fact_with_similarity(row) for row in rows]

    async def search_similar_by_reason(
        self, learner_id: UUID, embedding: list[float], limit: int = 5
    ) -> list[tuple[LearnerFact, float]]:
        """The same nearest-neighbour search as `search_similar` above,
        but over `reason_embedding` (migration 056) instead of
        `embedding` — the second, independently-searched path
        `memory.EmbedAndSearchFacts` compares against the first (see
        that class's own docstring). `WHERE reason_embedding IS NOT
        NULL` excludes every fact with no recorded reason at the SQL
        level, not in Python: most facts will have none (see
        `memory._fact_prompt`'s abstain-by-default instruction), and
        there is no similarity score to compute against a NULL vector
        anyway."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *, 1 - (reason_embedding <=> $2) AS similarity
                FROM learner_facts
                WHERE learner_id = $1 AND reason_embedding IS NOT NULL
                ORDER BY reason_embedding <=> $2
                LIMIT $3
                """,
                learner_id,
                embedding,
                limit,
            )
        return [self._row_to_fact_with_similarity(row) for row in rows]

    async def list_by_learner(self, learner_id: UUID) -> list[LearnerFact]:
        """Every fact for one learner, chronological — the raw
        material for the story view (webui) and for
        `list_by_session`'s session-scoped counterpart below."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM learner_facts WHERE learner_id = $1 "
                "ORDER BY created_at",
                learner_id,
            )
        return [self._row_to_fact(row) for row in rows]

    async def list_by_session(self, session_id: UUID) -> list[LearnerFact]:
        """One session's facts, in the order they occurred — the input
        to `SummarizeSessionPath` (step 6)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM learner_facts WHERE session_id = $1 "
                "ORDER BY turn_index",
                session_id,
            )
        return [self._row_to_fact(row) for row in rows]

    def _row_to_fact(self, row) -> LearnerFact:
        mapped = dict(row)
        mapped["embedding"] = mapped["embedding"].to_list()
        # Unlike embedding (always NOT NULL), reason_embedding is
        # nullable (migration 056) -- a row written before that
        # migration, or any fact with no recorded reason, has no
        # `.to_list()` to call.
        if mapped["reason_embedding"] is not None:
            mapped["reason_embedding"] = mapped["reason_embedding"].to_list()
        assert_row_consumed(LearnerFact, mapped)
        return LearnerFact(**mapped)

    def _row_to_fact_with_similarity(self, row) -> tuple[LearnerFact, float]:
        mapped = dict(row)
        similarity = mapped.pop("similarity")
        mapped["embedding"] = mapped["embedding"].to_list()
        if mapped["reason_embedding"] is not None:
            mapped["reason_embedding"] = mapped["reason_embedding"].to_list()
        assert_row_consumed(LearnerFact, mapped)
        return LearnerFact(**mapped), similarity


class ThinkingStyleStore:
    """Append-only store for `thinking_style_candidates` (migration
    030). See CLAUDE.md invariant 10: no delete method, no DELETE SQL
    — retirement is a status transition (candidate -> confirmed/
    retired) via UPDATE, same resurrection-over-deletion principle as
    HypothesisStore's tiers.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_candidate(
        self,
        learner_id: UUID,
        session_id: UUID,
        path_summary: str,
        path_summary_embedding: list[float],
    ) -> ThinkingStyleCandidate:
        candidate_id = uuid4()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO thinking_style_candidates (
                    id, learner_id, session_ids, path_summary,
                    path_summary_embedding, confirmation_count, status
                ) VALUES ($1, $2, $3, $4, $5, 1, 'candidate')
                RETURNING *
                """,
                candidate_id,
                learner_id,
                [session_id],
                path_summary,
                path_summary_embedding,
            )
        return self._row_to_candidate(row)

    async def get(self, candidate_id: UUID) -> ThinkingStyleCandidate | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM thinking_style_candidates WHERE id = $1", candidate_id
            )
        return self._row_to_candidate(row) if row is not None else None

    async def list_by_learner(
        self, learner_id: UUID, status: ThinkingStyleStatus | None = None
    ) -> list[ThinkingStyleCandidate]:
        async with self._pool.acquire() as conn:
            if status is None:
                rows = await conn.fetch(
                    "SELECT * FROM thinking_style_candidates WHERE learner_id = $1 "
                    "ORDER BY created_at",
                    learner_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM thinking_style_candidates "
                    "WHERE learner_id = $1 AND status = $2 ORDER BY created_at",
                    learner_id,
                    status.value,
                )
        return [self._row_to_candidate(row) for row in rows]

    async def list_confirmed_for_prompt(self, learner_id: UUID) -> list[ThinkingStyleCandidate]:
        """Step 8's "only once promoted does it get fed into future
        sessions' prompts" — the exact, single read method any prompt-
        building code is allowed to use for this. Deliberately narrower
        than `list_by_learner`: a `candidate`-status row must be
        structurally unreachable from here, not merely convention."""
        return await self.list_by_learner(learner_id, status=ThinkingStyleStatus.CONFIRMED)

    async def search_similar(
        self, learner_id: UUID, embedding: list[float], limit: int = 5
    ) -> list[tuple[ThinkingStyleCandidate, float]]:
        """Nearest non-retired candidates for this learner by cosine
        similarity on path_summary_embedding — retired candidates are
        excluded from being re-suggested as a match (they stopped
        matching once; re-litigating them isn't this search's job),
        but are never deleted (see CLAUDE.md invariant 10)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *, 1 - (path_summary_embedding <=> $2) AS similarity
                FROM thinking_style_candidates
                WHERE learner_id = $1 AND status != 'retired'
                ORDER BY path_summary_embedding <=> $2
                LIMIT $3
                """,
                learner_id,
                embedding,
                limit,
            )
        return [self._row_to_candidate_with_similarity(row) for row in rows]

    async def confirm(
        self, candidate_id: UUID, session_id: UUID, promotion_threshold: int
    ) -> ThinkingStyleCandidate:
        """The ONLY way confirmation_count/session_ids ever grow (see
        module docstring) — called exactly once per session a
        `ConfirmThinkingStyleMatch` call actually said yes to. Promotes
        candidate -> confirmed via the same UPDATE the moment the new
        count crosses `promotion_threshold`; never demotes a
        `confirmed` row back to `candidate` if called again."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE thinking_style_candidates
                SET session_ids = array_append(session_ids, $2::uuid),
                    confirmation_count = confirmation_count + 1,
                    status = CASE
                        WHEN status = 'candidate'
                            AND confirmation_count + 1 >= $3
                        THEN 'confirmed'
                        ELSE status
                    END,
                    updated_at = NOW()
                WHERE id = $1 AND NOT ($2::uuid = ANY(session_ids))
                RETURNING *
                """,
                candidate_id,
                session_id,
                promotion_threshold,
            )
        if row is None:
            # Either the candidate doesn't exist, or this session was already
            # counted for it — a session is one independent confirmation,
            # never two, so a repeat consolidation is a no-op.
            existing = await self.get(candidate_id)
            if existing is None:
                raise KeyError(f"thinking_style_candidate {candidate_id} not found")
            return existing
        return self._row_to_candidate(row)

    async def retire(self, candidate_id: UUID) -> ThinkingStyleCandidate:
        async with self._pool.acquire() as conn:
            updated = await conn.fetchval(
                "UPDATE thinking_style_candidates SET status = 'retired', "
                "updated_at = NOW() WHERE id = $1 RETURNING id",
                candidate_id,
            )
        if updated is None:
            raise KeyError(f"thinking_style_candidate {candidate_id} not found")
        return await self.get(candidate_id)

    def _row_to_candidate(self, row) -> ThinkingStyleCandidate:
        mapped = dict(row)
        mapped["path_summary_embedding"] = mapped["path_summary_embedding"].to_list()
        assert_row_consumed(ThinkingStyleCandidate, mapped)
        return ThinkingStyleCandidate(**mapped)

    def _row_to_candidate_with_similarity(self, row) -> tuple[ThinkingStyleCandidate, float]:
        mapped = dict(row)
        similarity = mapped.pop("similarity")
        mapped["path_summary_embedding"] = mapped["path_summary_embedding"].to_list()
        assert_row_consumed(ThinkingStyleCandidate, mapped)
        return ThinkingStyleCandidate(**mapped), similarity
