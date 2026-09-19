# ADR 0002 — No GROBID; Crossref stays the independent second reading

- **Status:** accepted
- **Date:** 2026-09-05
- **Related:** ADR [0001](0001-no-gold-benchmark.md),
  ADR [0003](0003-llm-reference-list.md) — which is the authorisation this ADR
  withheld, granted once the trigger below was met with evidence —
  `src/papertrace/refs.py`, README §"What it does — and what it does not"
  (this pointed at §"Two independent readings" until 0.7.0 took the count from
  two readings to four and the section was renamed; the link did not break, it
  simply stopped naming anything)

## Context

A full-stack review observed that the custom bibliography parsing and
reference-number reconciliation carry substantial complexity, and suggested
benchmarking a specialist parser before extending those rules further. GROBID
was named specifically: it supports citation-context recognition, links
citation markers to bibliography entries, and reports PDF coordinates. The
review was explicit that it is a candidate replacement, not a guaranteed
improvement.

The complexity is real. Measured on the current tree:

| Region of `refs.py` | Lines |
|---|---|
| printed-bibliography parsing (`parse_references`, `_parse_bulleted`, numeral handling) | ~161 |
| Crossref deposit as a second reading | ~217 |
| corroboration (identity when the title is unreadable) | ~45 |
| reconciliation (`_covers`, `_same_work`, `_first_divergence`, `reconcile`) | ~208 |
| entry construction, slugs, slug uniqueness | ~50 |
| **contiguous total** | **~707** |

Plus ~100 lines in `ingest/pymupdf_.py` locating and resuming the reference
span, ~145 lines of shared rules in `models.py`, and ~133 lines of
orchestration in `cli.py` — roughly 1,140 lines of source, pinned by a
1,205-line test module.

Two facts decided this.

**GROBID would displace far less of that than the total suggests.** Only the
~261 lines of *parsing* (bibliography text → numbered entries, including
locating the reference span) are work a parser does. The other ~470 lines —
the Crossref deposit, `deposit_corroborates`, and `reconcile` — are not parser
code. They exist because **any** reading of a reference list can be wrong and
the tool must be able to say so. `reconcile` takes candidate readings and lets
the manuscript's own `[N]` markers arbitrate between them; that machinery is
needed whether the candidate came from a regex or from a CRF. A better parser
would reduce how often `contested` and `unverified_from` fire. It would not
remove the need for them, because a parser cannot certify itself.

**GROBID is not an oracle, and it is a heavy dependency.** Its own published
figures put reference parsing at ~0.87–0.90 F1 and citation-context linking at
0.76–0.91 F1 depending on the collection. It is written in Java with JNI calls
to native CRF and/or deep-learning libraries, distributed as a Docker image or
a Java service, and wants 2–4 GB of memory for full PDF processing. PaperTrace
is a `pip install` command-line tool whose central promise is that it degrades
honestly rather than guessing; requiring users to stand up a JVM service to
audit a PDF is disproportionate to a parsing gain that is itself probabilistic.

Set against that, the thing GROBID would replace is not the weak link. The
three-way reconciliation was built for, and catches, exactly the failures that
motivated this concern: a 43-vs-41 parse inflation and a 106-vs-101, both
caught on real papers; 7 of 7 test-spread papers had a usable Crossref
deposit. Crossref supplies the *publisher's own* reference list at zero
deployment cost — an independent reading authored by someone other than this
tool, which is the property that makes reconciliation possible at all.

Two arguments in GROBID's favour were weighed and found insufficient:

- **Superscript citations.** Three of seven papers in the measured spread have
  their numbering unconfirmed because the coverage audit reads bracketed
  numeric labels only, and GROBID reads superscripts. This is a genuine gap —
  but it is a gap in *citation-marker detection*, addressable directly
  (`pymupdf` span flags already expose superscript formatting; see the
  `papertrace-superscript-citations` note) without adopting a JVM service.
- **PDF coordinates.** GROBID reports them. So does the existing pipeline:
  `highlight.py` locates anchor phrases with PyMuPDF `page.search_for` and
  draws every box in Python. Boxes are never model-placed, and that division
  of labour is not improved by a second coordinate source.

## Decision

**GROBID will not be adopted, and will not be benchmarked.**

- The roadmap item `- [ ] GROBID-grade reference parsing` is removed from
  `README.md`. Leaving it implies a plan that does not exist.
- `refs.py`'s parsing stays. Where it needs to improve, it improves in place —
  and the honest-degradation states it already carries (`boundary_ambiguous`
  with the DOI nulled, `reference_source`, `numbering_verified`,
  `numbering_note`, `unverified_from`, `label_is_doubtful`,
  `references_resumed`, `Reconciliation.contested`) are what keep a wrong
  parse visible rather than confident.
- Crossref remains a **candidate, not an oracle**. That distinction is
  load-bearing and is not softened by this decision: one publisher in the
  measured spread deposited 3 of its own declared 52 references, and that
  deposit is discarded by `_covers`.

Not benchmarking is part of the decision, not an omission from it. A benchmark
is only worth running if a favourable result would change the outcome, and it
would not: the deployment cost is disqualifying independent of the F1.

## Consequences

- The ~707 lines stay, and stay tested. Anyone proposing to extend them should
  read `_covers`, `_same_work` and `_first_divergence` first — several past
  attempts to simplify these have been reverted for cause, and
  `looks_like_reference`'s author-list clause in particular must keep it (a
  year-only test passes every unit test and drops real references that arrive
  truncated mid-title).
- If reference parsing ever becomes the dominant source of wrong audits, the
  cheap escalation is **a third candidate reading** wired into the existing
  `reconcile` arbitration behind an opt-in flag — not a replacement of the
  parser. That path is strictly additive and leaves the two-reading behaviour
  unchanged when the third is absent. This ADR does not authorise it; it
  records it as the shape a future proposal should take.
- Superscript-citation support remains open and is the higher-value work on
  this surface. It is unaffected by this decision.
