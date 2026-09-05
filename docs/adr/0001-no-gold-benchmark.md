# ADR 0001 — No gold benchmark, and therefore no accuracy figure

- **Status:** accepted
- **Date:** 2026-09-05
- **Supersedes:** nothing
- **Related:** [`evals/DESIGN.md`](../../evals/DESIGN.md),
  [`evals/PROPOSAL.md`](../../evals/PROPOSAL.md), ADR
  [0002](0002-no-grobid.md)

## Context

A full-stack review of PaperTrace raised the point that judgment quality has
never been established: the test suite demonstrates that the plumbing works,
but says nothing about how often a `contradicted` verdict is real or an
important discrepancy is missed. The review proposed a modest, independently
reviewed evaluation set as more valuable than another layer of defensive
logic.

That diagnosis is correct, and the project already agrees with it in writing.
What the review assumed was missing — a design — is not what is missing.

**The harness exists.** `evals/` holds ten modules (`align.py`,
`agreement.py`, `metrics.py` with 22 metric functions, `eligibility.py`,
`scoring.py`, `provenance.py`, `tool_coverage.py`, `eval_report.py` and two
runners) plus twelve deterministic test modules that run in CI.
`schemas/eval_gold.schema.json` is a published contract with conditional shape
enforcement per verdict. `evals/DESIGN.md` specifies the evaluation unit,
declared denominators, the population algebra, the alignment procedure and its
failure modes, run provenance, and a gold-set freeze policy that answers three
ways rather than two.

**The benchmark is already specified too.** `evals/PROPOSAL.md` is a written
proposal for exactly the set the review asked for: ≥40 cases, ≥15 of them in
faithful/altered pairs, all four gold verdicts represented including `partial`,
`not_retrieved` cases resting on genuinely paywalled DOIs, and
`pair_collapse_rate` as the signal that separates a checker which reads the
source from one which pattern-matches plausibility.

**What is missing is data, and one specific precondition for it.**
`evals/DESIGN.md` requires that any set supporting a validity claim be
labelled by **≥ 2 people who did not write the prompts**, on manuscripts this
project's authors did not construct, with two independent blind labels per
case, third-party adjudication, both labels recorded, and inter-labeller
agreement reported as a *ceiling* on the achievable score. That precondition
cannot currently be met: there is one maintainer, who wrote the prompts.

The only gold set that exists is `evals/gold/demo_v1.gold.json` — five cases,
four sources, zero pairs, `labellers: ["maintainer"]`, `independent: false`,
`authored_the_prompts: true`. `evals/scoring.py` already emits its own caveat
for that flag: *"conflict of interest: the same party authored the prompts and
assigned the gold verdicts — no validity claim can rest on this set."*

Three options were considered:

1. **Build the set anyway, self-labelled.** Rejected. It would satisfy the
   letter of "a gold set exists" while failing the condition that makes a gold
   set mean anything, and the harness would print a conflict-of-interest
   caveat on every report it produced. A number nobody may cite is worse than
   no number, because the number gets cited.
2. **Treat the benchmark as blocked rather than declined,** parking it until a
   second labeller appears. Rejected as a status: an indefinite block that
   nothing is scheduled to unblock is a decision wearing a delay's clothes,
   and it would leave every future architecture review re-proposing it.
3. **Decline it, and say what the cost is.** Accepted.

## Decision

**No gold benchmark will be built, and no accuracy figure will be claimed.**

- `evals/` stays as it is — the harness is not deleted. It is the artefact that
  would make the benchmark cheap if the labelling precondition ever changes,
  and its twelve test modules are conventional software tests of deterministic
  arithmetic, which belong in CI regardless.
- `evals/gold/demo_v1.gold.json` stays a `demonstration`, never a `benchmark`.
  Its `kind` enum already forbids the promotion without the labelling policy
  being satisfied.
- `evals/PROPOSAL.md` stays in the repository, with its status updated to
  record that it was considered and declined here. It is the design that would
  be executed if the precondition changes; deleting it would mean re-deriving
  it.
- The standing README rule is unchanged and now has a reason on file: **no
  accuracy figure is claimed anywhere, because none has been measured.**

## Consequences

**The cost, stated plainly.** The changes made in v0.5.0 in response to the
same review — passing the judge a verbatim manuscript quotation instead of a
compressed paraphrase, reading cited sources with the layout-aware backend,
and qualifying the most-adverse headline — **ship unmeasured**. Each is
justified structurally rather than by a score:

- the judge reading the author's actual sentence rather than a ≤160-character
  compression of it removes a known information loss, whether or not the loss
  was changing verdicts;
- a layout-aware source read cannot be worse than a flat one for evidence that
  lives in a table;
- and the headline change alters wording only, so it cannot move a verdict at
  all.

None of that is evidence that judgment quality improved. It is evidence that
three specific mechanisms which could only degrade it were removed. The
difference matters and must not be blurred in the CHANGELOG or the README.

**What stays unanswerable.** "How often is a `contradicted` verdict real?" and
"how often is a real discrepancy missed?" have no answer for this tool, will
not acquire one under this decision, and must not be answered by inference,
anecdote, or a live run that happened to look good. `missed_gap_rate` — the
safety metric `evals/DESIGN.md` names as the one that matters most — is
uncomputable without gold data.

**What still catches regressions.** The deterministic suite, the honest-
degradation states (`not_retrieved`, `unchecked`, `not_addressed`, the anchor
tri-state, coverage `uncertain`, the numbering disclosures), and the demo
end-to-end run with its pinned result. These catch *breakage*. They do not
measure *quality*, and no combination of them may be presented as if it did.

**Prompt changes are no longer comparable across versions, and that is
correct.** `evals/provenance.prompt_fingerprint()` is a content hash, so the
v0.5.0 prompt change invalidates comparison with any earlier run.
`evals/agreement.py` refuses outright to compare runs that do not share the
`(set_id, prompt fingerprint, converter)` triple. With no benchmark this costs
nothing, and the refusal remains the right behaviour.

**Reopening this.** The single fact that would reverse it is the availability
of two labellers who did not write the prompts. If that changes,
`evals/PROPOSAL.md` is the plan, and nothing in this decision needs
re-litigating first.
