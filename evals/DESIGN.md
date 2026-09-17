# Evaluation design

## Status — read this first

The gold set that ships today is a **demonstration**: five hand-labelled cases
from one fictional manuscript whose defects were planted by this project's own
maintainer, checked against four real published sources.

It establishes that the harness runs end to end and that the planted defects
are caught. It does **not** estimate accuracy on real manuscripts, it does
**not** estimate a false-positive rate, and it does **not** establish clinical
or research validity. **No validity claim may be made from it** — not in the
README, not in a release note, not in a paper.

A formal evaluation dataset is still being built. This document specifies what
it has to be.

## Why software tests are not enough

`tests/` proves the plumbing: ingest, retrieval, coverage arithmetic, crop
placement, report rendering, JSON round-trips. All of it runs offline with the
model call held behind a seam.

None of it touches the only question a user actually cares about — *is the
verdict right?* That is an empirical property of a model, and it needs
measurement against human labels. The two are kept in separate directories,
separate runners and separate vocabulary on purpose, because conflating them is
how "47 tests pass" turns into an implied accuracy claim.

## The evaluation unit

One case is one **(manuscript claim, cited source)** pair carrying:

| Field | Why it is needed |
|---|---|
| `claim_text` | the manuscript sentence, verbatim — the aligner's anchor |
| `citation_labels` | which references the claim leans on |
| `gold_verdict` | the human answer |
| `gold_source_slug`, `gold_source_page` | where the answer lives |
| `decisive_passage` | the text that settles it — makes the label auditable |
| `gold_anchor_phrases` | substrings of that passage a search should find |
| `why` | one sentence: why this verdict and not the neighbouring one |

Schema: [`schemas/eval_gold.schema.json`](../schemas/eval_gold.schema.json).
Deliberately **no block id** — block ids depend on the ingest backend, so a
gold set pinned to them would break when the converter changes.

`unchecked` is **not a legal gold verdict**. It is a harness error state, never
a correct answer. A case whose labellers could not agree gets
`gold_verdict: null`, is kept in the set, and is excluded from every
denominator — deleting it would quietly make the set easier.

## Paired cases

A pair is the same fact from the same source in two versions: one stated
faithfully, one deliberately altered. Encoded inline on each case
(`pair.pair_id` + `pair.role`) so every case stays a self-contained row.

Pairing matters more than any other design element here, because it is the only
construction that separates **read the source** from **guessed a plausible
verdict**. Two metrics fall out of it:

- `pair_discrimination` — both members scored correctly.
- `pair_collapse_rate` — both members got the *same* verdict. That is the
  signature of a model pattern-matching plausibility rather than reading.

**A half-pair is not a pair.** Groups are built from *all* gold cases and
scored only when every member is eligible and matched. Building them from the
scoreable subset turned a pair whose sibling was excluded into a one-member
group scoring `pair_discrimination` 1/1 = 100% — and `pair_collapse_rate`
1/1 as well, since a single verdict is trivially one distinct verdict. A
comparison that never happened cannot be reported as a comparison that
succeeded. Incomplete groups are listed on the report with the members that
made them incomplete, and counted in `n_pairs_incomplete`, never in `n_pairs`.

### Open item — the demo cannot supply a single pair

Every planted defect in `examples/demo/make_manuscript.py` exists **only in its
altered form**; there is no faithful sibling sentence, and the set contains no
`partial` case either. Pairing therefore requires a *new* manuscript — a
generator emitting both "an AUC of 0.77" and "an AUC of 0.94" against the same
reference, in the same document.

`demo_v1` ships with **zero pairs**, and the report says so rather than leaving
the metric quietly blank. Writing `evals/gold/make_paired_manuscript.py` is the
next artefact, and the scaffold should not be described as finished without it.

## Metrics

Populations, defined once:

- **M** — every matched (gold case, ClaimResult) pair that is **eligible**: the
  gold verdict is resolved *and* no drifted source invalidates it.
- **J_gold** — pairs in M whose *gold* verdict is a judgement.
- **J** — pairs in J_gold whose *prediction* is also a judgement. The
  confusion-matrix population.

> **Amended.** M was previously specified as "every matched pair", and
> `retrieval` implemented that specification faithfully — which is why a case
> with `gold_verdict: null` scored as a *correct* retrieval: `(None !=
> "not_retrieved")` happened to agree with the prediction, and it sat in the
> numerator and the denominator both. The eligibility filter is the fix, and
> the specification moved with it rather than being left describing the bug.

**Eligibility is decided once, in `evals/eligibility.py`, for two reasons that
reach the same conclusion.** A case that must not be counted is a case that
must not be counted, whatever put it there:

| Reason | Trigger |
|---|---|
| `gold_verdict_unresolved` | `gold_verdict: null` — labellers did not agree |
| `refs_status_drift` | a cited source's observed status differs from the frozen expectation |

Each exclusion carries a `detail` computed from the data. Excluded cases stay
in `per_case` and get their own report section; deleting them would quietly
make the set easier.

`metrics.matched_population()` re-applies the resolved-verdict guard inside
each metric — defence in depth, so a future caller that forgets to filter
cannot reopen the leak.

**Every denominator is declared.** The record carries a top-level
`populations` block computed independently of the metrics, and every `Rate`
names the population it came from. Two rates with `n = 4`, one over M and one
over J_gold, are otherwise indistinguishable in JSON — which is the
neighbouring-bare-percentage hazard in miniature.
`test_every_rate_in_the_record_matches_its_declared_population` walks the whole
record and checks each `n` against its declared population, so the *next*
denominator that drifts away from what it claims fails a test rather than
printing a plausible number.

Judgement classes are `supported`, `partial`, `contradicted` and
`not_addressed` — **four**, since `not_addressed` was added to the verdict
vocabulary. It is a judgement like the others: the model read the source and
found it silent on the claim, which is a real finding about the citation, not a
failure. It therefore takes a row *and a column* in the confusion matrix, and
counts in `macro_f1` on the same terms as the rest.

`not_retrieved` is a retrieval fact; `unchecked` is a harness error. **Neither
is scored as a wrong verdict** — both get their own rates. This is the review
requirement to keep retrieval failures separate from model-judgement failures,
and the existing verdict enum already encodes the distinction.

| Metric | Numerator / denominator | Population |
|---|---|---|
| `judgment_accuracy` | correct verdicts / \|J\| | J |
| `binary_contradiction_detection` | correct contradicted-vs-not / \|J\| | J |
| `precision_c` | TP_c / all predicted c | J |
| `recall_c` | TP_c / gold c in J | J |
| `recall_c_pessimistic` | TP_c / **all** gold c | every gold case of that class |
| `macro_f1` | mean F1 over classes where F1 is defined | named exclusions, with a derived kind |
| `retrieval_outcome_accuracy` | declined exactly when it should / \|M\| | M |
| `false_gap_rate` | predicted not_retrieved / \|J_gold\| | J_gold |
| `missed_gap_rate` | judged anyway / gold not_retrieved | gold gaps |
| `source_page_accuracy` | exact page / pairs that answered | J with a gold page |
| `source_page_accuracy_pm1` | within one page / same | same |
| `page_missing_rate` | answered with no page / J with a gold page | same |
| `anchor_in_gold_passage` | ≥1 anchor inside the decisive passage / scoreable | J with passage + anchors |
| `anchor_empty_rate` | no anchor returned / \|J\| | J |
| `label_detection_accuracy` | tool labels ∩ gold labels / gold labels | grades the **regex**, per label |
| `claim_label_coverage` | covered / labels the tool found | grades the **extractor**, per label |
| `claim_occurrence_coverage` | covered occurrences / all occurrences | grades the **extractor**, per *place* a citation is made |
| `occurrence_uncertain_rate` | uncertain occurrences / all occurrences | printed beside the ratio, never folded into it |
| `occurrence_detection_accuracy` | tool occurrences ∩ gold occurrences / gold occurrences | grades the **regex**, per occurrence |
| `uncited_recall` | gold uncited statements matched / all of them | uncited register |
| `unchecked_rate`, `not_retrieved_rate` | over **all** predictions | the whole run, **including excluded cases** |
| `unchecked_rate_matched` | unchecked predictions / \|M\| | M |
| `pair_discrimination`, `pair_collapse_rate` | over **complete** pair groups only | pairs_complete |

Design decisions worth arguing with:

- **Precision and recall return `None`, not `0.0`, when the denominator is
  zero.** Every rate is an object carrying `k`, `n` and `value`, and the report
  renders undefined as `—`. "0% of 0" and "0% of 40" are different claims.
- **`macro_f1` excludes undefined classes and names them — but definedness is
  a count question, not a `precision + recall` question.** The common
  `zero_division=0` convention would report ~0.67 for a flawless run on a set
  with no `partial` example, so excluding a genuinely absent class and naming
  it is honest. The converse trap is the one this harness fell into: testing
  `(p + r) == 0` conflated **three** situations — present-and-always-wrong,
  in-gold-never-predicted, and predicted-never-in-gold — and made all three
  vanish from the mean, deleting exactly the classes the run failed. F1 is
  written as `2TP / (predicted_in_J + gold_in_J)` and is undefined only when
  that support is zero, i.e. when the class is in **neither column of J**. A
  class that is present and always wrong scores `0.0` and stays in.
- **Every exclusion carries a `kind` derived from the counts**
  (`absent_from_set` vs `eliminated_by_attrition`), plus the counts themselves.
  The former single `excluded_reason` string said *"no gold and/or no predicted
  instances in this set"* for every exclusion, so it printed a false sentence
  whenever a class was present and merely eliminated. A constant explanation
  cannot notice that it has stopped being true; a derivation can only be wrong
  if the counts are.
- **`missed_gap_rate` is the safety metric.** A non-zero value means a verdict
  was issued against a source that was never available.
- **`binary_contradiction_detection` is always reported next to the 3-class
  numbers**, because the supported/partial boundary is a judgement call that
  moves between runs and would otherwise dominate macro-F1 at small n.
- **No true precision is available.** The gold set is not exhaustive over the
  manuscript, so an over-extracting model is never penalised. Predictions with
  no gold case are reported as out-of-scope, explicitly **not** as errors.

### Repeated-run agreement

Each run is aligned to **gold** independently; runs are never aligned to each
other. A case that failed to align in one run contributes the sentinel
`__unmatched__` in that position rather than being dropped — dropping it would
bias agreement upward on exactly the cases the model handled inconsistently.

**Two sentinels, deliberately not one.** `__unmatched__` means the run *was*
asked and the aligner failed: a real measured outcome about the model, and it
belongs in the score. `__absent__` means the harness never asked, because the
case is missing from that run's record: an operator bookkeeping gap. Collapsing
them would charge the operator's gap to the model. Fleiss returns `None` with a
reason whenever anything is `__absent__`, because κ assumes every item is rated
by every rater.

**The run count is passed explicitly, never inferred from the first vector.**
Inference was safe only while the caller filtered to the complete-case set
first — the very filter that introduced the bias above. Removing the filter
without passing the count would have swapped a disclosed upward bias for an
undisclosed arithmetic error. Ragged input raises.

**Two populations are printed side by side, and only one of them is a bound.**
The *penalized* figure (`__absent__`-padded, every case seen in any run) is a
genuine **lower** bound: filling in any real vote where the harness never asked
can only raise the modal count. The *complete-case* figure (only cases present
in every run) was previously labelled the **upper** bound, and that was wrong —
it drops cases rather than penalising them, and a dropped case whose true
agreement is high pulls the reported mean *down*. With three or more runs the
omitted set can sit either side of the kept set, so complete-case is reported
as a different population ("how stable was the model where we actually asked
it") and explicitly not as a ceiling. The omitted cases are named per run.

**Only eligible cases vote.** `per_case` keeps excluded rows so they can be
rendered in their own section; they are filtered out before the agreement
vectors are built. A case that was never scoreable cannot be evidence of the
model disagreeing with itself.

**Runs that are not comparable are refused outright.** Agreement is defined
within one **(`set_id`, prompt fingerprint, ingest converter)** triple, and all
three are checked. Averaging across gold sets produces a number describing no
set; averaging across prompts or across ingest backends compares two different
systems and calls the difference instability. No caveat repairs either — a
category error, not a partial comparison.

- **`modal_agreement`** (headline) — mean over cases of (modal verdict count) / k.
- `unanimous_rate` — cases where all runs agree.
- `fleiss_kappa` (footnote) — reported with `p_bar`, `p_e_bar` and k.

Fleiss is the right family (k ≥ 2 interchangeable raters, categorical, no rater
identity — which rules out Cohen's κ). But it suffers the prevalence paradox: on
a small class-skewed set κ reads near zero despite high raw agreement, because
expected agreement approaches 1. So modal agreement leads and κ follows, with a
printed note that κ is uninformative below roughly 30 cases. Krippendorff's α
was rejected — it earns its extra machinery only with a distance metric.

Future work: the verdicts are actually **ordinal**
(supported > partial > contradicted), so a weighted κ would give partial credit
for supported↔partial disagreements. Worth doing once a set is large enough for
the ordering to matter.

## Alignment

Claim ids are model-assigned in reading order and the extraction prompt asks
for a *tight paraphrase*, not the verbatim sentence — so neither ids nor exact
text can be the key.

> **Amended.** This section previously claimed the outcome was "independent of
> the order of either list". It was not: stage 2 walked the gold list case by
> case and awarded each case its best free prediction, so the first-listed case
> won every contest. Reproduced — the same data with gold order `[hi, lo]`
> scored 1/2, and `[lo, hi]` scored 0/2.

Matching is a **global score-sorted assignment**. Every candidate (gold case,
prediction) pair is scored; the whole candidate set is sorted by
`(-score, case_id, pred_id)`, a total order that never mentions input order;
pairs are assigned best-first, skipping any whose case or prediction is already
consumed. Two passes run in sequence, so an exact match is never contested by a
similarity candidate that happens to score 1.0 on text while disagreeing on
labels:

1. **exact** — normalized text equal *and* identical label set.
2. **similarity** — shares a citation label, `difflib` ratio ≥ 0.60, and beats
   its best live competitor by ≥ 0.10. (`difflib` is stdlib; adding `rapidfuzz`
   to a tight dependency list was rejected.)
3. **discriminator** — a contest is broken only by human-authored `match_keys`.
   If that does not resolve it, the cases are reported **ambiguous**, not
   guessed.

**The margin has two directions.** A per-case aligner compares the best
prediction against the runner-up *for that case* — a **row** comparison. Under
global assignment the unit is the edge, which can be a coin flip two ways: a
row rival (same case, another prediction) and a **column** rival (same
prediction, another case). The reproduced failure was a column contest,
`lo→2 = 0.889` against `hi→2 = 0.84`. Sorting globally makes 0.889 always ask
first, which removes the order-dependence — but it would still be a guess at a
0.049 margin the harness already refuses to act on in the row direction, and
trading an order-dependent guess for a deterministic one is not the same as
trading it for an honest answer.

So the competitor is the highest-scoring **live** candidate sharing *either*
endpoint, recomputed at pop time (an earlier assignment may already have
removed the rival that made a pair look contested), and assignment requires
`score − competitor ≥ 0.10`. Sub-floor candidates stay in the rival set: a
near-tie below the floor is still a reason not to trust the winner. A contest
freezes **every case** in it and leaves its predictions free — freezing only
the popped case would let its column rival walk in unopposed on the next pop,
which is the guess just declined.

Order-independence is now asserted by
`test_alignment_is_identical_under_permutation_of_both_lists`, which permutes
both lists over a fixture carrying an exact match, a fuzzy match, a contested
pair and an unmatched case, and compares the whole `Alignment` object. The
previous claim was in a docstring and nothing checked it.

Nothing is dropped silently. Unmatched gold cases count as misses in
`recall_pessimistic` and are listed with a derived reason: `extraction_gap`
when the tool's own coverage audit already flagged that label, `ambiguous` when
a contest was refused, `attribution_undetermined` when the coverage audit could
not be read, otherwise `alignment_failure_or_paraphrase_drift`. That
distinction matters — the first is a tool failure, the last may be the
evaluator's.

**`labels_partially_covered` joins the gap set, and the direction is the
point.** A gold case sitting on the *second* occurrence of a label the tool
reached once used to read as `alignment_failure_or_paraphrase_drift` — label-level
coverage called the label covered, so the tool looked innocent and this
matcher looked broken. Occurrence coverage lets that be attributed to the tool,
which is where it belongs. Every future change here must move blame **toward**
the tool, never away: `labels_uncertain_only` is deliberately *not* in the gap
set, because an uncertain attribution is the harness declining to say who
failed, and adding it would move blame on a guess.

**`attribution_undetermined` exists to stop a silent transfer of blame.** The
reason is derived by intersecting the case's labels with
`coverage["missing"]` and `coverage["labels_partially_covered"]`. If either
collection changes shape under the aligner, a naive `set(...)` intersection becomes
quietly empty and *every* unmatched case is reattributed from the tool to the
harness grading it, with no error and no failing test. So `tool_coverage.missing_labels()`
decides the shape over the whole collection and refuses an unrecognised one
**wholesale**, returning an empty label set *and* an error string; a partially
parsed audit would produce confident, wrong attributions for exactly the
entries it failed to read. On refusal the reason says the attribution could not
be determined rather than defaulting in the direction that exonerates the tool.

An audit block that is absent entirely is not a shape error: it contributes no
missing labels, which is the pre-existing behaviour. "Coverage was never
audited" is a separate disclosure and is not smuggled in here.

**The aligner is itself fuzzy, and its errors are indistinguishable from model
errors in the score.** That cannot be engineered away at this scale. The
mitigations are visibility, not elimination: every match records its stage and
score, the report prints the alignment table *before* any metric, and a warning
fires above 25% fuzzy matches. All precision figures are conditional on
alignment.

## Run provenance

`RunResults` records none of this, so the harness captures it out of band into
`provenance` on the eval record:

| Field | Source |
|---|---|
| `model_reported` | `papertrace.check.last_model()`, else parsed off `results.checker` |
| `model_unidentified` | `true` when it cannot be determined — **never guessed** |
| `checker_string` | `RunResults.checker` (`cli.py` writes `claude -p · <model>`) |
| `claude_cli_version` | `claude --version` |
| `papertrace_version` | `importlib.metadata`, with `__version__` recorded alongside |
| `prompt_fingerprint` | sha256 of `EXTRACT_PROMPT` and `CHECK_PROMPT` |
| `git_commit`, `git_dirty` | the prompts live in the repo, not in package data |
| `converter` | docling vs pymupdf materially changes the input |
| `truncated` | inputs the character limits cut short |

**Prompt versioning is a content hash, not a constant.** A manual
`PROMPT_VERSION` is a promise a human has to keep, and the classic eval failure
is a prompt edit that does not bump it — silently making two runs incomparable
while the artefacts claim otherwise. A hash cannot be forgotten. The cost is
readability, paid off by the table below.

| Fingerprint | Meaning |
|---|---|
| `EXTRACT_PROMPT=sha256:f919e9c02674` | extraction prompt as of v0.3.1 |
| `CHECK_PROMPT=sha256:6fdce55bdfdc` | verification prompt as of v0.3.1 |

Append a row whenever a new hash first appears.

Scores are comparable only within one `(set_id, prompt fingerprint, converter)`
triple. All three are recorded so comparability is checkable rather than assumed.

### Two contamination risks the fingerprint does not cover

1. It covers only the two batch prompts. `prompts/review_core.md` drives the
   interactive skill and is not hashed.
2. **`claude -p` inherits project context from its working directory.** Running
   an eval inside this checkout may feed the model the repo's own
   `.claude/skills/`, settings or a `CLAUDE.md` — which would flatter the score
   relative to a user's run. Mitigation: the live runner defaults its case
   directory to a fresh path under the system temp dir, outside the repo, and
   records `ambient_context` (cwd, whether it is inside the repo, hashes of any
   `CLAUDE.md` found). Mitigated, not eliminated.

## Ambiguity and human gold

**Author-labelled gold is a conflict of interest.** The same person wrote the
prompts, planted the defects and assigned the verdicts. That is circular, and
the schema records it (`labelling.authored_the_prompts`). Any set intended to
support a validity claim must be labelled by **≥ 2 people who did not write the
prompts**, on manuscripts this project's authors did not construct.

**Adjudication protocol for real sets.** Two independent labellers per case,
blind to each other and to any model output; disagreements resolved by a third
adjudicator or by discussion; both the initial labels and the resolution
recorded per case. **Inter-labeller agreement is reported with the same
statistic as run agreement and framed as a ceiling on the achievable score** —
if humans agree 80% of the time, a model scoring 80% is at the ceiling, not 20%
wrong. That framing belongs in the report, not just here.

**Unresolvable cases are kept**, with `gold_verdict: null` and
`ambiguity: "unresolved"`, excluded from every denominator and counted.

### Named ambiguity kinds

Each needs a worked example in a real set: the supported↔partial boundary;
scope or population mismatch; verb strength; **multi-reference claims where the
retrieved source carries only half the claim** (batch judges against the first
available source only, so the tool structurally cannot resolve these — the gold
set must label them honestly rather than pretend); figure-only evidence;
second-hand attribution through the source's own citations.

### The supported / partial rubric

This boundary dominates 3-class macro-F1, the demo README already concedes it
varies between runs, and without a written rubric two labellers will not agree.

- **`supported`** — a reader of the decisive passage alone would write the
  manuscript's sentence.
- **`partial`** — the kernel proposition is in the source, but at least one of
  {population, magnitude, direction qualifier, time frame, outcome measured}
  differs, **or** the source states it with lower epistemic strength than the
  manuscript does.
- **`contradicted`** — the source states a value or direction incompatible with
  the claim.

## Gold sets are frozen

`set_id` carries the version. Editing a set means minting a new id. A
`not_retrieved` case depends on a DOI still being paywalled, so the runner
compares each source's `expected_ref_status` against the observed
`refs_manifest.json`. This is the least obvious failure mode in the whole
design and the one most likely to produce a wrong number nobody notices.

The check answers **three** ways, not two:

| `kind` | Meaning | Effect |
|---|---|---|
| *(no entry)* | observed status equals the frozen expectation | scored normally |
| `changed` | observed status differs | affected cases **invalidated and excluded**, bannered |
| `not_verified` | manifest missing, unreadable, or silent about this source | **disclosed, not excluded** |

Two silent holes were closed to make the third row possible. A missing manifest
returned `[]` — the same value as *checked, and nothing changed*, so an unasked
question rendered as a reassuring answer. And a source absent from the manifest
yielded no entry at all, rendering *unobservable* as *unchanged*.

`not_verified` does **not** exclude. This project's rule is that unverifiable
is not the same as wrong; excluding here would treat an unanswered question as
a failed one, and would empty every set scored without a reachable manifest —
including the documented offline invocation. It is disclosed instead, with a
banner, a caveat and its own report section, so the gap is visible rather than
assumed away in either direction.

The check also now **always runs**. It was previously computed only when
`--case-dir` was passed, and the documented invocation in `evals/README.md`
omitted it — so in practice the freeze check never ran at all, and its silence
was indistinguishable from a pass.

**A drifted case leaves every family that consumes the gold verdict or the gold
evidence** — judgement, retrieval, pages, anchors, pairs — and **stays** in
coverage and in the run-level `unchecked_rate` / `not_retrieved_rate`, which
are computed over every prediction the run produced. That has to be said aloud
on the report, because those percentages are printed beside the ones that
shrank. Excluded cases are kept in the record and rendered in their own
section: deleting them would quietly make the set easier.

## Occurrence-level coverage

The tool now audits coverage per **citation occurrence** — per place the body
text makes a citation — not per label. Three consequences for this harness.

**The gold key is `(label, page, ordinal)`, never an occurrence id.** An id
embeds a block id, and block ids depend on which ingest backend ran; the gold
set already refuses backend-dependent identifiers everywhere else.
`coverage_gold.occurrences_in_text` is **optional**: `demo_v1` omits it, so
`occurrence_detection_accuracy` is reported as **absent** and the report says
so in words. Absent is not zero, and the two must never render the same way.

**Reading-order zipping is rejected, and this is the record of the decision so
it is not "fixed" later.** `EXTRACT_PROMPT` asks for claims in reading order,
which makes zipping claim *n* to occurrence *n* tempting. The order is
unverified, and it degrades silently: one skipped claim shifts every later
pairing by one and the audit emits confident, wrong attributions that are
indistinguishable from correct ones.

Attribution is a **lookup, not a match**. `citation_occurrences()` builds the
inventory *before* the model call, `_render_inventory()` renders it into
`EXTRACT_PROMPT` as `ctx_NNNN`, and each claim comes back naming the ids it was
taken from, resolved through the map built in that same pass. A `ctx` absent
from the inventory is **dropped**, never repaired into "the first occurrence of
that label". Since `coverage/3`, `uncertain` has exactly one cause — a claim
cites a label and names none of that label's contexts, so a claim reached one of
them and nothing can say which — and it is never counted as covered.

The ids are not a licence to zip after all. The labels are *assigned* in reading
order, but they are resolved through the mapping built with them, never by
re-deriving position later; a consumer that pairs the *n*th ctx with the *n*th
occurrence of a freshly recomputed list has rebuilt the bug.

This section described a text-similarity mechanism until `coverage/3` deleted
it — `_attribute_label`, `_normalize_for_match`, `_ratio`, `_location_matches`,
`OCCURRENCE_MIN_RATIO`, `OCCURRENCE_MIN_MARGIN`, none of which survives in
`src/`. Do not reintroduce a similarity fallback for an unresolvable `ctx`:
that is the confident-wrong-pointer failure the redesign removed, and
`uncertain` is the honest answer instead.

**The occurrence figures are not `Rate`s.** Every `Rate` in an eval record
names a population declared in the record's `populations` block, and that block
is computed over gold cases and matched pairs — occurrences are neither. Rather
than declare a population the record cannot verify, these carry their
denominator inline (`numerator`, `denominator`, `counted_over`). The part of
the `Rate` contract that matters still holds: `value` is `None` when the
denominator is zero, never `0.0`.

## What is not aggregated

The uncited register and the citation coverage audit are **not model
judgements** and never roll into a headline accuracy figure. They measure an
extractor and a regex respectively.
