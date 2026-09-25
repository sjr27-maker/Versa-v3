# Reason-confirmation personalization: two fault-finding sessions, then a fix

UTC 2026-09-22 | real Gemini, real dev database, one throwaway learner
(`personalization-eval-181605`) across all three sessions below | scripts
were scratch, not committed — this file is the durable record.

Session 1 and 2 were run to evaluate personalization, the reason/feedback
loop, and cross-session adaptability, and then to deliberately try to break
it. Two real bugs were found (see the end of session 2). Session 3 was run
after fixing them, against the SAME learner, to prove the fix live.

---

## Session 1 — personalization & adaptability (baseline)

Session 1 id: `8ce90eac-8824-4e40-abed-7b7032ef4d24`

### Turn 0 — states a preference
> **Sent:** "Before explaining anything new, I always want you to start with
> one concrete worked number example, then the general rule. Can you
> explain the power rule for derivatives?"

- First words at **4.95s**, returned **6.39s**
- Reply (start): *"To differentiate $x^3$, we bring the exponent $3$ to the
  front and decrease the power by $1$..."*
- `learner_facts` written this turn:
  - situation: *"Asked for an explanation of the power rule for
    derivatives, explicitly requesting a concrete numerical example before
    the general rule."*
  - resolution: *"Provided a concrete example using x^3 first, followed by
    the general mathematical formula and explanation for the power rule."*
  - **reason** (written unprompted): *"you preferred seeing a concrete
    numerical example before learning the general rule"*

### Turn 1 — ambiguous, different topic
> **Sent:** "How should I approach understanding integrals?"

- First words at **7.33s**, returned **9.70s**
- Did **not** branch this run (real model variability) — answered
  directly. `history_block_used=True` on this turn: the answer already
  drew on turn 0's history even though nothing was ambiguous.

### Turn 2 — echoes the reason, different words
> **Sent:** "Actually, could you show me a real numeric example of
> computing an integral before you explain the general method?"

- Shown at **0.89s** — no LLM call, pure vector search + template
- `memory_match_via=reason`, `reason_confirmation_offered=True`
- Reply: *"Before I answer — it looks like this is because you preferred
  seeing a concrete numerical example before learning the general rule. Is
  that right?"*

### Turn 3 — confirms the reason (Yes)
> **Sent:** "Yes, that's right"

- First words at **2.80s**, returned **7.22s**
- `FinalAnswer`'s `memory_context`: *"you preferred seeing a concrete
  numerical example before learning the general rule"*
- Reply led with a worked numeric integral example before the general
  method — the confirmed reason visibly shaped the answer.

### Session 2, turn 0 — cross-session, new topic, preference never restated
Session 2 id: `e2bb30e9-ab07-4a3d-a4b0-6877c5e0bbca`

> **Sent:** "Can you explain what a limit is in calculus?"

- First words at **4.76s**, returned **8.08s**
- `FinalAnswer` received:
  - `structural_requirement`: *"Open with a concrete worked example using
    specific numbers or a real scenario, and state the general rule or
    formal definition only afterward. Do not begin with the abstract
    rule..."*
  - `learner_history_block`: a correct cross-session recap of both prior
    turns ("Earlier today, they asked...")
- Reply **led with a concrete driving-speed analogy** before the formal
  definition, unprompted — the clearest evidence in this run: behavior
  changed, not just context.

### End-of-run audit (learner: `eb51b98c-1150-4d3f-99e2-cc0ca6a61d48`)

`learner_facts`:
| fact_type | situation | reason |
|---|---|---|
| direct_answer | power rule request | "you preferred seeing a concrete numerical example before learning the general rule" |
| direct_answer | "what is a limit" | *(none)* |

`turn_diagnostics`, session 1:
| turn | memory_match_via | reason_confirmation_offered | reason_confirmed_by_student | history_block_used |
|---|---|---|---|---|
| 0 | — | — | — | False |
| 1 | — | — | — | **True** |
| 2 | **reason** | **True** | — | False |
| 3 | — | — | **True** | True |

`thinking_style_candidates`: **one candidate created** after
`consolidate_session`:
> `status=candidate, confirmation_count=1, path_summary='concrete example requested before abstract definition'`

Never promoted (by design — needs many independent sessions), but the
detection mechanism demonstrably fired on real behavior after a single
session.

**Caveat, honestly reported:** several background enrichment calls
(`GenerateAbstractForm`, reference-binding classification) failed on real
Gemini free-tier 429s during this run — non-critical to what's measured
above, but see session 2's bug findings, where the same constraint turned
out to matter more.

---

## Session 2 — adversarial fault-finding (same learner)

Session id: `ceeb078c-fca5-4f55-9840-63c7f71ca78d`. Learner already had 2
facts on record coming in.

### Turn A — echoes the reason again (logarithms)
> **Sent:** "Could you walk me through an example with real numbers before
> diving into the theory — for logarithms this time?"

- First words **6.99s**, returned **8.83s**
- `memory_match_via=None` — did not match this time (real variability);
  answered directly with a worked log example.

### Turn B — echoes the reason AGAIN (chain rule)
> **Sent:** "Same as before — a real-numbers example before the theory,
> please. Can you explain the chain rule?"

- Shown at **0.77s**
- `memory_match_via=reason`, `reason_confirmation_offered=True`
- Reply: *"Before I answer — it looks like this is because you prefer to
  see a concrete example with real numbers before diving into the
  theoretical definition. Is that right?"*

### Turn B2 — declines
> **Sent:** "No, something else"

- First words **2.61s**, returned **5.92s**
- Correctly fell back and answered the **original** chain-rule question
  (turn B's real content), not the confirmation text.

### Turn C — explicitly contradicts the earlier preference — 🐛 BUG
> **Sent:** "Actually, forget examples — from now on just give me the
> abstract definition directly, no numeric examples, no stories."

- Shown at **0.81s**
- **The entire reply was the confirmation question again:**
  *"Before I answer — it looks like this is because you prefer to see a
  concrete example with real numbers before diving into the theoretical
  definition. Is that right?"*
- Turn C's real content (the reversal) was never answered and never
  reached `CLASSIFY:STATED_PREFERENCE`.

### Turn D — fresh topic, checks which preference wins — 🐛 BUG (consequence of C)
> **Sent:** "What is a Taylor series?"

- `structural_requirement` was **still** the original concrete-first
  imperative: *"Open with a concrete worked example... Do not begin with
  the abstract rule..."* — the explicit reversal in turn C had zero effect.
- Confirmed against the DB directly: `stated_preferences.get_latest_for_learner`
  still returned the session-1 text, verbatim.

### Turn E — forces branching again (step-1 options personalization)
> **Sent:** "What's the deal with eigenvalues — how should I even start
> with this topic?"

- Did not branch this run either (`options: []`) — a repeated
  observation, not a bug: this domain/phrasing rarely reads as ambiguous
  to the model.

### Turn F — skipped (no options were live to type past)

### Turn G — near-empty message
> **Sent:** "ok"

- Handled gracefully via recent-history continuity, no crash.

### Faults confirmed this session
1. **[High]** Turn C: an explicit reversal got intercepted by the
   reason-match and mistaken for something to reconfirm, instead of being
   answered. Root cause: cosine similarity cannot tell affirmation from
   negation of the same topic.
2. **[High, consequence of 1]** Turn D: the stated preference never
   updated, because turn C's content never reached classification.
3. **[Medium]** Turn B → B2 → turn C's own match: the SAME already-declined
   fact was offered again shortly after being declined — nothing recorded
   the decline against that specific fact.
4. **[Medium]** Confirmed by code read (`loop.py: _call_node`): a failed
   background node call (this run hit real 429s — free-tier daily quota,
   20 requests/day on the fast-tier model) leaves **zero** row in
   `node_calls` and no `turn_diagnostics` flag — only a transient log line.
   Only 1 of an expected ~5 `learner_facts` this session actually got
   written.
5. **Ruled out:** the `�` seen in terminal output is a Git-Bash/Windows
   codepage artifact rendering the real em dash (`—`) in
   `render_reason_confirmation_message` — verified against source, not a
   product bug.

---

## The fix (between session 2 and session 3)

- **`memory.ConfirmReasonRelevance`** (new node, fast tier,
  `CONFIRM:REASON_RELEVANT` schema): a cheap gate in front of the
  reason-confirmation offer. Asks whether the CURRENT message already
  contradicts the matched reason; if so, the match is treated as if
  nothing had matched, and the turn proceeds through the normal
  `AssessAndBranch`/`FinalAnswer` path — so a reversal finally gets
  answered and classified. Fixes bug 1/2.
- **`diagnostics.TurnDiagnosticsStore.declined_fact_ids_for_learner`**: a
  JOIN against the *existing* `reason_confirmed_by_student`/
  `matched_fact_id` columns (no new schema) — checked before offering a
  confirmation. Fixes bug 3.
- Along the way, found and fixed a smaller bug this surfaced: the
  click-resolution turn that records a "no" never actually stored *which*
  fact was declined (`matched_fact_id` was never threaded through that
  diagnostics call) — bug 3's fix could not have worked without also
  fixing this.
- Bug 4 (silent background-failure gaps) is **not** fixed — flagged in
  `docs/IDEAS.md` section 5 as a real, separate finding needing more than
  a same-day fix.
- 9 new/changed tests, full suite green before this run.

---

## Session 3 — same learner, after the fix

Session id: `2885840f-4ebe-4170-8502-b59e4297cc1b`. Run against the SAME
learner as sessions 1-2, immediately after applying the fix and confirming
459/459 tests green.

**Complication, reported honestly:** this run hit the real Gemini
free-tier's **daily** quota (20 requests/day on the fast-tier model,
`gemini-3.6-flash`, exhausted by the combined weight of sessions 1+2+3 in
one day) partway through — this is exactly bug 4 from session 2, now
observed a second time from a different angle. Some turns below show a
"the tutor failed to respond" fallback message where a real API call
failed; those are transport failures, not logic failures, and are called
out explicitly rather than hidden.

### Turn 0 — the exact contradiction that broke session 2's turn C
> **Sent:** "Actually, forget examples — from now on just give me the
> abstract definition directly, no numeric examples, no stories. What is a
> derivative, formally?"

- `memory_match_via=None` — no reason match fired this time (real
  phrasing/embedding variance; the embeddings API is separate from the
  rate-limited model and was not itself failing).
- `FinalAnswer` failed on this turn — but from a real `504 DEADLINE_EXCEEDED`
  / 429 upstream, not from being skipped by the confirmation branch.
- **Options offered: `[]`** — no confirmation was wrongly offered either
  way.
- **Verdict: inconclusive for bug 1/2's live proof** (no match ever fired
  to exercise the new gate against) — but the earlier committed test,
  `test_a_message_that_contradicts_the_reason_is_not_offered_as_a_confirmation`,
  proves the gate itself deterministically, with `ConfirmReasonRelevance`
  actually invoked and returning `still_applies=False`.

### Turn 1 — fresh topic, checks whether the reversal took
> **Sent:** "What is the fundamental theorem of calculus?"

- Answered normally. `structural_requirement` was still the original
  concrete-first imperative, and `stated_preferences.latest` was still the
  session-1 text — expected, since turn 0 never actually completed (it
  failed on the real API error above, so there was nothing new to
  classify).

### Turn 2 (C1) — echoes the original reason (the exact shape that broke in session 2)
> **Sent:** "Could you show me a worked example with real numbers before
> the theory — for series convergence?"

- `memory_match_via=reason`, `reason_confirmation_offered=True` — the
  underlying match still works correctly when not rate-limited.

### Turn 3 (C2) — declines
> **Sent:** "No, something else"

- The fallback answer itself failed on a real API error (same quota
  exhaustion) — but critically, **the decline was still correctly
  recorded**: `turn_diagnostics` for this turn shows
  `matched_fact_id=e0b1c04e-7467-4a33-85bd-c769c02ecd65`,
  `reason_confirmed_by_student=False`. That recording happens in the
  Python/DB layer before any answer-generation call, so it's unaffected by
  the API failure. `declined_fact_ids_for_learner` immediately afterward
  correctly contains this fact id.

### Turn 4 (C3) — echoes the SAME reason again, immediately — ✅ FIX PROVEN LIVE
> **Sent:** "Same idea — a real numeric example before the theory, for
> limits at infinity."

- `memory_match_via=reason` — **matched the identical fact again**
  (`e0b1c04e-7467-4a33-85bd-c769c02ecd65`)
- **`reason_confirmation_offered=False`** — correctly **not** re-offered.

This is the clean, conclusive result: the exact fact that was just
declined matched again on a genuinely different message, and the fix
suppressed the re-offer — live, against the real database, with no stub
involved in the decision itself (only the fallback answer text was
affected by the unrelated API outage).

### Full audit, session 3

`turn_diagnostics`:
| turn | memory_match_via | reason_confirmation_offered | reason_confirmed_by_student | matched_fact_id |
|---|---|---|---|---|
| 0 | None | False | — | None |
| 1 | None | False | — | None |
| 2 | reason | **True** | — | e0b1c04e… |
| 3 | reason | False | **False** | e0b1c04e… |
| 4 | reason | **False** ✅ | — | e0b1c04e… (same fact, correctly not re-offered) |

### Summary of what session 3 actually proved
- **Bug 3 (re-offering a declined reason): fixed, proven live**, turn 4
  above — the cleanest result of the whole exercise.
- **Bug 1/2 (contradiction swallowed): fixed at the code/test level**
  (`test_a_message_that_contradicts_the_reason_is_not_offered_as_a_confirmation`,
  deterministic, passing) — the live attempt was inconclusive only because
  no reason match fired on that specific message this time, not because
  the gate failed.
- **Bug 4 (silent gaps on failed background calls): not fixed, and
  observed again this session** — a second, independent live confirmation
  that the free tier's daily cap is a real, recurring constraint worth
  addressing before treating multi-session live evaluation as routine.
