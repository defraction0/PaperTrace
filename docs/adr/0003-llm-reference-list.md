# ADR 0003 — An LLM reading of the reference list, as a fourth candidate

- **Status:** accepted
- **Date:** 2026-09-17
- **Related:** ADR [0002](0002-no-grobid.md), ADR [0001](0001-no-gold-benchmark.md),
  `src/papertrace/reflist.py`, `src/papertrace/ask.py`,
  `docs/superpowers/specs/2026-09-16-llm-reference-list-design.md`

## Context

ADR 0002 declined to adopt GROBID and, in the same breath, specified the
escalation it *would* accept and refused to authorise it:

> If reference parsing ever becomes the dominant source of wrong audits, the
> cheap escalation is **a third candidate reading** wired into the existing
> `reconcile` arbitration behind an opt-in flag — not a replacement of the
> parser. That path is strictly additive and leaves the two-reading behaviour
> unchanged when the third is absent. This ADR does not authorise it; it
> records it as the shape a future proposal should take.

This ADR is that authorisation. The trigger is now met with evidence.

A manuscript under review had 22 printed references parse as 18. Docling
renders a hanging-indent numeral column as a GFM table, so five reference rows
arrived as `|  6. | Author A (2019) … |` and were glued onto the entry above
them as wrapped continuations. Every claim citing `[6]` or above was judged
against a different paper than the one cited — six substantive verdicts naming
papers the manuscript never cited.

The verdicts were not merely wrong, they were **confident**. Gluing five
references onto one entry left `_entry` scraping the next reference's DOI onto
it, and the title check *passed* on that DOI, because the glued raw string held
the words of both papers. `title_check: verified` was printed on every one of
them. A stronger title check would not have helped: the reference string
really did name the paper that was downloaded, alongside the one the label
meant. That is the distinction this ADR turns on — the broken join is
`label → entry`, and every check the tool had verified `entry → PDF`.

Three root causes, all reproduced offline and all fixed deterministically
first (0.7.0: `_unwrap_table_rows`, `_numerals_agree_with_position`, and the
`_covers` conflation that let a refused entry's positional label still satisfy
the extent check). Those fixes are not the subject of this ADR and needed no
authorisation — they are parser bugs. What they cannot do is certify
themselves, which is the sentence ADR 0002 already wrote about GROBID: *"a
parser cannot certify itself."* Two readings of one bibliography, both
produced by this tool from the same converter's output, share a failure mode.
A third and fourth reading that do not are the only thing that can say so.

## Decision

**A model reading of the reference list is adopted as a candidate — a voter
with no vote of its own — behind `--llm-refs/--no-llm-refs`, default on.**

The reconciliation now weighs four readings of one bibliography: the Crossref
deposit, the run backend's parse, a flat-text (pymupdf) parse of the same PDF,
and a structured list proposed by a model. `reconcile()` itself is unchanged
and still weighs exactly two candidate readings — the deposit and the parse —
because only those two can be *adopted* as the list a source is resolved and
downloaded against. The flat-text and model readings never reach `reconcile`'s
arguments or `resolve_all`; they are folded into a second, orthogonal
computation, `label_agreement`, whose only power is to withhold a verdict. One
gated path does more than that, and constraint 1 states it rather than leaving
it to be discovered. Five constraints define the shape, and each is the answer
to a way this could have gone wrong:

1. **The model's reading can only subtract verdicts — unless a person is shown
   the disagreement and asks for more.** `reconcile` still chooses between the
   deposit and the parse. The `--llm-refs` reading and the flat-text reading
   are voters only: neither reaches `reconcile`'s arguments or `resolve_all`,
   so on their own they cannot cause a source to be resolved, downloaded or
   judged — they can withhold a verdict, and (through `stamp_seen_in`) name
   themselves on an entry they also carried. That is the safety property the
   design rests on, and
   `tests/test_reference_readings.py::test_a_model_naming_different_papers_changes_no_resolved_entry`
   tests it directly, as an equality plus a capturing stub over `resolve_all`.

   **One path escapes it, and it is the path this feature spends money to
   reach.** Menu option 1 of the escalation asks a *second* model call
   (`reflist.resolve_disputed`) to settle the disputed labels, and
   `cli._escalate_disputed` substitutes each resolved entry into the chosen
   list, which `resolve_all` then downloads and `check.py` then judges. The
   substitution is mandatory rather than optional, and the reason sits in a
   comment at the substitution itself: returning the list untouched would take
   the label out of `labels_disputed` — so `check` judges it again — against the very entry
   the resolution ruled *against*, which is the original wrong-paper bug
   reached through the one path a person authorised. The gate on it is the
   person: the run is interactive, the full disagreement is on disk before
   anything is asked, the choice is recorded as `numbering_chosen_by: "user"`,
   only the labels actually resolved are substituted (the rest stay disputed
   and withheld), and no answer sets `numbering_verified`.
2. **Every field of the model's reply is found verbatim in one of the two
   texts it was shown, or discarded.** A discarded field costs a recorded gap;
   an accepted field is one a reader can grep out of the source text. Nothing
   invented survives to name a paper. A title is the one field that names the
   paper, so one entry's unverifiable title discards the model's **whole
   reading** — never just that entry — and the run says the model's reading
   was unusable, never that it agreed.
3. **Agreement is computed per printed citation label, never by position.**
   `_first_divergence` zips two readings by position and is right for its own
   job — deciding whether Crossref's deposit and the parse still describe the
   same paper — but reused here it would compare docling's 6th entry against
   pymupdf's 6th and report a divergence for the wrong reason on any list a
   reading split or merged. `label_agreement` joins on `e.num`, the printed
   numeral, instead. Reading-order zipping has been rejected repeatedly in
   this codebase and is rejected again here.
4. **Any disagreement is `disputed`. No majority vote, no ranking.** The
   codebase's own rule: *"a non-unique match is refused, never ranked."* A
   disputed label is dropped from the sources `check.py` judges before any
   model call is made, and it is named in `ClaimResult.withheld_refs`. A claim
   left with *no other* source then lands `unchecked` with the label named —
   explicitly not `not_retrieved`, which would claim a retrieval failure that
   did not happen. A claim that also cites a source nobody disputes is still
   judged on that source, and the withheld label is named beside the verdict:
   the *source* is always withheld, the *claim* is `unchecked` only when
   nothing else answered for it.

   The comparison behind `disputed` is three-valued
   (`refs._comparably_same → True | False | None`), so *"cannot compare"* is
   not *"the readings disagree"*: a voter whose comparison is `None` abstains
   and is excluded rather than counted as dissent, which can leave one reading
   standing and print `single`. The one exception is a label where **no** voter
   said anything comparable at all — that is `disputed`, because nothing
   establishes what the label names. `disputed` therefore carries three causes
   — a duplicate, a contradiction, and nothing comparable — and
   `RefManifest.labels_uncomparable` publishes the third as a documented
   subset of `labels_disputed`, so a report can say *which* cause withheld a
   label instead of naming a disjunction. It is a subset by construction: both
   lists are read off the same `refs._label_state` call.
5. **`numbering_verified` keeps its exact current meaning and stays `False`
   through every model reply and every interactive choice.** Corroboration is
   a **second, independent axis** (`numbering_corroborated`), reported beside
   it. No published boolean is redefined and no model path can flip one.
   `tests/test_numbering_invariant.py` is the module that tests this one, over
   every reply shape and every interactive choice, from both starting values.
   The model's own reading may not *corroborate* at all (`refs.DERIVED_READINGS`):
   `reflist.propose` may only copy values out of the two extractions, and both
   of those vote in their own right, so the model agreeing with one of them is
   one text read twice. It still votes, and a disagreement from it is real.

Four alternatives were weighed and rejected:

- **A pairwise visual vote** — "does this bibliography crop match that title
  crop." It verifies `entry → PDF`, which is already deterministically
  title-checked and which *passed* on every one of the wrong verdicts above.
  The broken join is `label → entry`, and no crop carries a label, because the
  numeral is what the converter destroyed. It also has no deterministic
  verifier, so a false "match" would overwrite a correct
  `numbering_verified = False` with a plausible confirmation — the one thing
  CLAUDE.md forbids. It is moot besides: on the manuscript that prompted this,
  the docling table block was measured at `regions=1` for all five glued
  references (recorded in the design spec named above, not reproducible from
  this repository — gate 3 forbids committing the manuscript), so no per-entry
  rectangle exists to crop.
- **The LLM as arbiter.** The body's own `[N]` markers are the only reading
  definitionally right about what the paper cites. Replacing a free, offline,
  definitionally-correct arbiter with a probabilistic one is a strict loss.
- **Field-level splicing across candidates by position.** pymupdf loses DOIs
  entirely on some entries, so the moment one list is short by even one field,
  a `num` from one reading would be stapled to another reading's `doi` at the
  wrong position.
- **Images through the seam.** Nothing but the prompt crosses it: `ask._ask`
  runs `claude -p --output-format json --safe-mode --tools ""` with an optional
  `--model`, the prompt on stdin, and no file, path or attachment argument
  anywhere in `src/`. Opening an image channel would mean dismantling
  `--tools ""` and the private scratch cwd, and would falsify the README's
  statement that the judge never receives a page image.

`ask.py` is extracted as part of this decision. CLAUDE.md's rule *"`check.py`
is the only module that calls a model"* was protecting the seam, not the
module; with the `refs` stage (through `reflist.py`) calling it too, the rule
becomes
*"one file in `src/` shells out, and a test says which"* — enforceable rather
than conventional (`tests/test_ask.py::test_only_ask_py_shells_out_to_a_model`).
It also fixes a live misattribution: the model that answered was one module
global overwritten by every call, so a run whose judging made zero calls (every
cited source `not_retrieved`, nothing to judge) would have printed the
reference-list model as the `Checker:` of verdicts it never saw. The model is
now recorded per call site.

**No accuracy figure is claimed for any of this, and none may be.** ADR 0001
governs: a figure needs a gold set this project's authors did not construct,
labelled by at least two people who did not write the prompts, on **both**
backends — the converter is the cause here, not a nuisance parameter. A
pairing evaluation for the `(citation label, printed reference entry)` join is
specified as a sibling task in `evals/DESIGN.md` and is not part of this ADR.

## Consequences

- Crossref remains a **candidate, not an oracle**, and so does the model.
  Adding a fourth reading does not soften that; it is the same rule applied to
  a fourth voter.
- Two stages now call the model seam, so a per-call-site model record is
  load-bearing rather than tidy. Anything that reads "which model did this"
  must name a site.
- A disputed label costs verdicts. That direction is deliberate and it is the
  expensive one: the cost and the safety of this join are two different
  metrics in `evals/DESIGN.md`'s sibling pairing task, reported separately and
  never blended.
- Where the readings are in dispute at any label, an interactive run writes the
  full disagreement to `case/out/reference_disagreement.md` **before** it asks
  anything, and records `numbering_chosen_by: "user"` for whatever the user
  answers. There is no second condition: `_escalate_disputed` returns early on
  an empty `labels_disputed` and on nothing else, and a run whose numbering
  passed the extent check can still have a disputed label — extent and content
  are different questions and both can be true at once. A person consenting to
  proceed is an input, not evidence. A non-interactive run withholds the
  disputed labels and asks nothing.
- Choosing *resolve* changes which paper is fetched. That is the one place a
  model's answer reaches retrieval, it is gated on a person, and it is
  disclosed as such: the run records `resolution_outcome`,
  `resolution_model`, `resolution_fields_discarded` and `resolution_readings`
  — the extractions the call was shown, named, never "both texts", since a
  pymupdf-backend run has one printed span and the reading being ruled against
  may have none. A resolution is a reading, not a confirmation.
- ADR 0002's decision is unchanged. GROBID is still not adopted and still not
  benchmarked; this ADR takes the escalation ADR 0002 named, not the one it
  refused. One housekeeping note for whoever follows the link: 0002's Related
  list points at a README section called *"Two independent readings"*, which
  this change rewrote — the numbering bullet under **PaperTrace does** is where
  that material now lives.
