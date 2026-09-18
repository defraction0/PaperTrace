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
against a different paper than the one cited — the citation label is the join
key, so the verdicts were confident and wrong.

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
and still takes exactly two arguments — the deposit and the parse — because
only those two can be *adopted* as the list a source is resolved and downloaded
against. The flat-text and model readings never reach `reconcile`'s arguments
or `resolve_all`; they are folded into a second, orthogonal computation,
`label_agreement`, whose only power is to withhold a verdict. Five constraints
define the shape, and each is the answer to a way this could have gone wrong:

1. **The model's reading can only subtract verdicts.** `reconcile` still
   chooses between the deposit and the parse. The flat-text and model readings
   are voters only, so no model output can cause a source to be resolved,
   downloaded or judged — it can only cause a verdict to be withheld. This is
   the safety property the design rests on and
   `tests/test_numbering_invariant.py` tests it directly.
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
   disputed label is dropped from the sources `check.py` judges, and the claim
   lands `unchecked` with a note — explicitly not `not_retrieved`, which would
   claim a retrieval failure that did not happen.
5. **`numbering_verified` keeps its exact current meaning and stays `False`
   through every model reply and every interactive choice.** Corroboration is
   a **second, independent axis** (`numbering_corroborated`), reported beside
   it. No published boolean is redefined and no model path can flip one.

Four alternatives were weighed and rejected:

- **A pairwise visual vote** — "does this bibliography crop match that title
  crop." It verifies `entry → PDF`, which is already deterministically
  title-checked and which *passed* on every one of the wrong verdicts above.
  The broken join is `label → entry`, and no crop carries a label, because the
  numeral is what the converter destroyed. It also has no deterministic
  verifier, so a false "match" would overwrite a correct
  `numbering_verified = False` with a plausible confirmation — the one thing
  CLAUDE.md forbids. It is moot besides: the real docling table block has
  `regions=1` for all five glued references, so no per-entry rectangle exists
  to crop.
- **The LLM as arbiter.** The body's own `[N]` markers are the only reading
  definitionally right about what the paper cites. Replacing a free, offline,
  definitionally-correct arbiter with a probabilistic one is a strict loss.
- **Field-level splicing across candidates by position.** pymupdf loses DOIs
  entirely on some entries, so the moment one list is short by even one field,
  a `num` from one reading would be stapled to another reading's `doi` at the
  wrong position.
- **Images through the seam.** `claude -p` has no image channel. Granting it
  one would dismantle `--tools ""` and the private scratch cwd, and would
  falsify the README's statement that the judge never receives a page image.

`ask.py` is extracted as part of this decision. CLAUDE.md's rule *"`check.py`
is the only module that calls a model"* was protecting the seam, not the
module; with `refs` (through `reflist.py`) calling it too, the rule becomes
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
- Where nothing accounted for the body's labels and labels are in dispute, an
  interactive run writes the full disagreement to
  `case/out/reference_disagreement.md` **before** it asks anything, and
  records `numbering_chosen_by: "user"` for whatever the user answers. A
  person consenting to proceed is an input, not evidence. A non-interactive
  run withholds the disputed labels and asks nothing.
- ADR 0002's decision is unchanged. GROBID is still not adopted and still not
  benchmarked; this ADR takes the escalation ADR 0002 named, not the one it
  refused.
