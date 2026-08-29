# Changelog

All notable changes to PaperTrace are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [Unreleased]

### Fixed

*An external review of the changes below found 14 further defects. All 14
reproduced, and all are fixed here. They shared one shape — a component that
could not answer honestly emitted a favourable answer instead of admitting the
gap — which is the shape this release exists to remove.*

- **The evaluation harness inflated its own grade.** `macro_f1` returned `None`
  for a class whose precision and recall were both defined and zero, so a class
  the run got *wrong* was dropped from the mean instead of scoring `0.0`. A run
  that got one class of three right reported **1.0**. It also printed
  *"no gold and/or no predicted instances in this set"* as the reason, which was
  false whenever the class was present. F1 is now derived from counts —
  `2·TP / (predicted + gold)`, undefined only when the class appears in neither
  column — and each exclusion carries a reason computed from those counts, so it
  cannot go stale. On the committed mini fixture this moves macro F1 from
  `0.833` to **`0.556`**; the old number was produced by deleting the class the
  run failed.
- **Claim alignment depended on the order of the gold list.** Two gold claims
  sharing a citation label, one prediction fitting both: whichever was listed
  first took it. Reversing the gold list alone turned 1/2 correct verdicts into
  0/2, while the module docstring claimed order-independence. Alignment is now a
  global score-sorted assignment, and the margin gained a second direction — a
  pair must beat the best live rival sharing **either** its case or its
  prediction, so a column contest is refused rather than settled by score noise.
- **A model could assign states only the pipeline may assign.** `VERDICTS` mixed
  three model judgements with two pipeline states, so a model answering
  `not_retrieved` for a source that *was* retrieved had it accepted. The
  vocabulary is split into `JUDGMENT_VERDICTS` and `PIPELINE_STATES`; the wire
  format is unchanged.
- **Malformed model responses were half-applied or fatal.** A verdict with no
  `source_page` was accepted and rendered as a literal `Page None` — a verdict
  the reader cannot open is not evidence, so it is now `unchecked`. An explicit
  `"anchor_phrases": null` raised an uncaught `TypeError` that killed the whole
  check stage, because the conversion sat outside the exception boundary.
  Validation is now atomic and total by construction: nothing is written to a
  claim until every field validates.
- **Two different papers could share one case folder** whenever their filenames
  matched. Case identity is now a content hash of the PDF; manifests written
  before the field existed fall back to name comparison and say so.
- **Reference-availability drift was warned about and scored anyway** — and in
  practice never even checked, since the drift scan only ran when `--case-dir`
  was passed and the documented invocation omitted it. Drifted and
  gold-unresolved cases now leave every metric they contaminate, are listed in
  their own report section rather than deleted, and every affected denominator
  is visible.
- **Null-gold cases inflated retrieval accuracy**, scoring as *correct* because
  neither side equalled `not_retrieved`. Two further leaks of the same kind:
  a pair whose sibling was excluded became a one-member "pair" reporting 100%
  discrimination, and `unchecked_rate_matched` drew on an unnamed population.
- **Repeated-run agreement silently dropped cases missing from a run** — the
  caller defeating its own module's documented contract, which names the exact
  upward bias it causes. Intersection and union are now reported side by side as
  upper and lower bounds, omissions are named, and aggregating two different
  gold sets is refused outright.
- **The three report formats disclosed different things.** The editor report —
  the one meant for a journal editor — carried **no** `unjudged_refs` disclosure
  at all. The terminal report had no per-crop anchor caption, and both its
  footer and the editor's asserted "red box = matched text" unconditionally. In
  the editor, the truncation warning was nested inside the coverage block, so a
  run with truncation and no coverage disclosed nothing. Every disclosure now
  has one definition in `src/papertrace/disclosures.py` carrying a `token` that
  must appear in all three formats, and a parity test asserts it as a loop
  rather than a checklist.
- **A crop with an unknown anchor was captioned as a match.** `anchor_located`
  is a tri-state and the templates tested only `== false`, so `null` — a result
  written before the field existed, where the box count was discarded — rendered
  as a located match. All three states now render distinctly. Separately,
  `highlight.py` recorded `False` ("we looked and it wasn't there") when there
  were no anchor phrases to look for; that is now `null`.
- **A cited page the source does not have took down the pipeline.** When a
  check named page 47 of a 12-page source, `doc[page - 1]` raised `IndexError`
  out of the highlight step — losing every crop already written, because
  `results.json` is saved after the loop, and under `run` stopping the report
  from being produced at all. Both indexing sites are bounded now. The claim
  keeps `anchor_located = null`, never `false`: nothing was searched, so
  "looked and missed" would be a fabrication, and the console names the page
  the check gave against the page count the source actually has.
- **Three tests passed only on the machine that produced them.** Two coverage
  tests copied their fixture out of `demo_case/`, and a drift test read
  `demo_case/refs_manifest.json` while calling it "the real committed
  manifest" — but `demo_case/` is gitignored, so a fresh clone had none of it.
  CI would have gone red on the first push. The coverage fixture is now built
  in-test from the same sentences, and the manifest ships as
  `evals/tests/fixtures/refs_manifest_demo_v1.json`. Verified by running the
  suite from a simulated fresh checkout with no ignored paths.
- **The flat-ingest warning told you to install what you already have.** rich
  reads `[docling]` as a style tag, so `pip install 'papertrace[docling]'`
  rendered as `pip install 'papertrace'` — the one line whose whole job is to
  say how to get the layout backend. The bracket is escaped, with a test.
- **`examples/demo/make_manuscript.py` crashed with a bare traceback** when
  playwright was absent, which is the case after `pip install -e ".[dev]"`. It
  now names the extra to install, the way `render.py` already did for the same
  import.
- **The Python 3.10 CI job could not run at all.** `tests/test_packaging.py`
  imported `tomllib` (stdlib only from 3.11) at module scope, so the newly
  added 3.10 cell died during *collection* — which aborts the whole pytest
  session, not just that file, and reports as a red job with no test results.
  The module now skips itself on 3.10; it asserts a static `pyproject.toml`
  table, which the three 3.11+ cells already cover. No shipped code was
  affected: `src/`, `evals/` and `scripts/` use no 3.11-only construct.
- **The sdist omitted the evaluation harness it advertises.** The archive held
  exactly one `evals` file — `evals/README.md`, swept in by an unanchored
  `README.md` glob that matched at any depth — while that same packaged README
  told you to run `python evals/runners/score_only.py`. The full harness now
  ships, run artefacts and machine-local paths are explicitly excluded, and the
  wheel is unchanged: `evals` is a source artefact, not an importable part of
  the package.
- **README statements that did not match the code:** only one of three formats
  stamped the converter; the retrieval manifest is written to the case root, not
  `case/out/`; CI runs on pull requests and pushes to `main`, not "every push";
  the title sanity check lets an empty or unreadable first page pass, which the
  README omitted; retrieval and the scout were called "deterministic" when they
  depend on live services and the date of the run; the demo's planted errors
  were promised as "always caught" when extraction and judgement are model
  behaviour nothing in the code constrains; and a cited source is flat-ingested
  only when not already ingested — `check` reuses an existing `annotated.md`.

- **Four places where the tool broke its own rules.** A model response missing
  its `verdict` key was defaulted to `partial` — an invented judgement from an
  unparseable answer; it is now `unchecked`, as is any value outside `VERDICTS`.
  A multi-reference claim is judged against the first available source only, and
  the co-cited sources it never opened are now recorded per claim
  (`unjudged_refs`) and named in every report. The manuscript was silently cut
  at 180k characters and each source at 150k; the cut is now recorded
  (`RunResults.truncated`) and disclosed. An evidence crop whose anchor phrase
  matched nothing was still captioned "red box = matched text"; the box count is
  no longer discarded and the caption tells the truth (`anchor_located`).
- **An installed wheel could not render a report.** `templates/` shipped only in
  the sdist while `report.py` located it by walking three parents up to a repo
  that isn't there. Templates moved to `src/papertrace/templates/` and load via
  `importlib.resources`, so they ship as package data.
- Version drift: `__version__` said `0.3.0` while `pyproject.toml` said `0.3.1`.
  There is now one source of truth — `__init__.py`, read by hatch — and the
  README release link and terminal-report footer no longer hard-code a number.

### Added

- **`evals/` — an offline evaluation harness and its design.**
  [`evals/DESIGN.md`](evals/DESIGN.md) specifies the evaluation unit, paired
  faithful/altered cases, the metric definitions (verdict accuracy, per-class
  precision/recall, macro F1, source-page accuracy, anchor localization,
  citation-label coverage, error rates, repeated-run agreement), the run
  provenance record and the human-labelling policy. Metrics keep retrieval
  failures (`not_retrieved`) and harness errors (`unchecked`) separate from
  model judgements, every rate carries its denominator, and an undefined rate
  renders as `—` rather than `0%`. Scoring is deterministic and offline; the
  live runner is a plain script that refuses to run in CI.
- `evals/gold/demo_v1.gold.json` — the demo's planted defects as machine-readable
  gold, labelled a **demonstration**, not a benchmark.
- `schemas/eval_gold.schema.json`, `schemas/eval_run.schema.json`, plus the
  first automated validation of the four pre-existing schemas.
- `docs/RELEASING.md` and `evals/PROPOSAL.md` (draft benchmark issue, unposted).
- CI badge, and a Python 3.10–3.14 matrix in place of 3.11 alone.
- `src/papertrace/disclosures.py` — the single place a report disclosure is
  defined, so the three formats differ in styling only.
- `evals/eligibility.py` (one mechanism deciding which cases are scored, and
  why not) and `evals/tool_coverage.py` (refuses an unreadable coverage audit
  wholesale rather than half-reading it into confident wrong attributions).
- `tests/test_disclosure_parity.py` and `tests/test_packaging.py`; the sdist
  verification steps in `docs/RELEASING.md`.
- The record gained a `populations` block and every rate names its population,
  so a percentage can no longer sit beside one drawn from a different
  denominator without saying so (`eval_run/2`).
- **A regression test for every finding above**, plus the disclosure parity
  loop and the sdist assertions. The suite more than doubled from 119; the
  CI badge is the live count, because a number written here goes stale.

### Fixed

- **`run` handed the stage commands Typer option objects instead of values.**
  The stages are Typer commands called as plain functions, where a declared
  default is an `OptionInfo`, not the string it displays — and `run` called them
  positionally. Adding `--case` to `ingest` shifted every later argument, so
  `backend` became an `OptionInfo`, matched neither `"auto"` nor `"docling"`,
  and every audit silently ingested as flat text while the wizard's preflight
  had just reported layout-aware ingest as available. All six calls are keyword
  arguments now, which makes a future insertion harmless.
- **An unrecognised ingest backend silently meant pymupdf.** That is what hid
  the bug above: `ingest_pdf` treated every value that was not `"docling"` as
  flat text, so a typo or a wrong type downgraded the run and the report then
  told the user to install a backend they already had. It raises now.
- **The flat-ingest warning said why.** It advised `pip install
  'papertrace[docling]'` unconditionally, including to people who had docling
  installed and had simply run with `--backend pymupdf`.
- **A References heading the ingest did not classify as one was invisible.**
  `references_section` only started collecting at a `sectionheader` block.
  Flat-text ingest guesses headings from font size, and on a real Elsevier paper
  it made three author lines into headings and left `References` as body text —
  so a paper with 34 references reported "No numbered references found" and the
  audit stopped. A block whose entire text is the word now counts, whatever the
  backend called it; a sentence merely *starting* with it still does not.
- **Reference entries running together were parsed as one.** The marker regex
  required `[N]` at a line start, but Elsevier PDFs extract with entries
  mid-line. All 34 references collapsed into entry `[1]`, which then took its
  DOI from reference `[2]` — a mis-attribution, not a shortfall: the resolver
  would have fetched the wrong paper and judged `[1]`'s claim against it. The
  bracketed form is now recognised anywhere in a line; the bare `12.` form
  still needs a line start, because mid-sentence it is prose.
- **A journal issue number in parentheses was read as a year.** `Br. J. Radiol.
  89 (1061) (2016)` slugged the entry `a-1061`. `YEAR_RE`'s parenthesised branch
  now requires a plausible century.

### Added

- **A guided audit: run `papertrace` with no arguments.** Simulating a
  first-time, non-technical user surfaced nine bumps, and none of them were
  bugs — every flag was correct and documented, but a newcomer had to assemble
  six decisions from a `--help` screen before anything happened, and three of
  the ways a run can fail only surfaced minutes in. The wizard checks the
  environment *first* (a missing `claude` CLI is now a sentence, not a
  traceback twenty minutes later), then asks one question at a time, and states
  the cost — counted from the paper — before spending it. It ends by printing
  the equivalent `papertrace run` line, because a wizard that hides the CLI
  leaves its user unable to repeat or script what they just did. Without an
  interactive terminal it prints help rather than waiting on stdin.
- **`--doi` no longer has to be explained.** It means the DOI of the paper being
  audited, not of anything it cites — and saying so did not stop it being
  misread, including once by this project's own documentation. The wizard reads
  the DOI off the paper's first page and asks "is that this paper's own DOI?",
  turning a definition into a yes/no. Only the front matter is read: a reference
  list is full of other papers' DOIs, and picking one up would anchor the
  literature scout to somebody else's work with no error to notice.
- **A saved contact email.** `~/.config/papertrace/config.json`, read after
  `--email` and the environment so an explicit value always wins. JSON, not
  TOML, on purpose: `requires-python` is `>=3.10` and `tomllib` is 3.11+.
  A missing, empty or corrupt file reads as `{}` — a convenience may not become
  a hard failure.
- **Python 3.14 in the CI matrix.** `requires-python` said `>=3.10` while CI
  tested 3.10–3.13, so anyone installing on 3.14 — which is what Homebrew's
  `python3` now is — ran on a version nothing verified. It passes, so the
  matrix says so rather than the metadata over-promising.
- **`papertrace ingest -c`.** `-c` meant the case folder in every subcommand
  except `ingest`, which failed with `No such option: -c`. `-o/--out` is
  unchanged.

### Changed

- **A co-cited claim is judged against every source it cites, not just the
  first.** Batch mode used to pick `avail[0]`, judge against that, and file
  every other co-citation as never opened. Co-citation is an offer of support,
  so each retrievable source now gets its own model call, its own note and its
  own evidence crop, with a count beside the claim — *"4 cited sources checked:
  2 fully support it; 1 partially supports it; 1 contradicts it"*. The claim's
  headline is the **most adverse** verdict any source gave, so one dissenting
  reference is never averaged away by two agreeing ones, and the per-source
  breakdown is always rendered so the headline cannot overstate the split.
  `unjudged_refs` consequently narrows to one meaning: the source could not be
  obtained. Cost note: a claim citing four retrievable sources now costs four
  model calls instead of one.
- **New verdict `not_addressed`** — the source was read and says nothing about
  the claim. Fanning a claim out to its co-citations makes this unavoidable: a
  source cited for another part of a compound claim is not `contradicted` (it
  does not say otherwise) and not `partial` (there is no true kernel), and
  forcing it into either would manufacture a finding. An inapt citation is a
  real result and now has a name. It is appended to `JUDGMENT_VERDICTS`, so
  every pre-existing verdict keeps its position; `counts()` gains a key and
  never reorders one. It is also the one verdict with no `source_page`, since
  there is no passage to point at — demanding one would force the model to cite
  an absence.
- **`examples/demo/output/` regenerated** against the current pipeline. The
  committed report predated the disclosure work and showed none of it: it
  named the checker only as `Claude`, reported `all 4 citation labels
  covered`, and carried neither the converter stamp nor the attribution
  caveat. It now reads `5/5 citation occurrences ... across 4 labels` and
  identifies the judging model. Same verdicts as documented: 2 supported,
  2 contradicted, 1 not retrieved, 1 uncited assertion.

- **Citation coverage is now per occurrence, not per label.** The audit tracked
  a *set of labels*: if two sentences cited `[3]` and only one became an
  extracted claim, label 3 counted as covered and the omitted sentence was
  invisible. This was the tool's single largest overstatement — and the demo
  manuscript contains exactly that shape, so it was live, not theoretical.
  Coverage now tracks each citation **occurrence** — the marker at its position,
  with its block, page and surrounding sentence — and lists the ones no
  extracted claim reached. `labels_in_text`, `covered` and `missing` keep their
  exact previous meaning and computation, so every existing consumer is
  unaffected; `occurrences`, `labels_partially_covered` and `attribution` are
  additive under `"schema": "coverage/2"`. A `results.json` from an older build
  still loads and still renders its label-level line.

  Occurrence coverage is **not** strictly better, and the report says so on its
  face: attributing a claim to a specific marker is a text match that can be
  wrong; an extractor that legitimately merges two adjacent sentences will show
  one as unaddressed; the denominator still inherits the bracketed-numeric-only
  regex, so an unseen style contributes zero occurrences and makes the ratio
  look *better* than reality; and the figure is not comparable between papers.
  An attribution the tool cannot make is reported as **uncertain** and counted
  as **not** covered.
- `evals/align.py` now treats a **partially** covered label as an
  `extraction_gap` too. Previously a gold case on a label's second occurrence
  was blamed on the evaluator's matcher rather than on the tool; the blame moves
  toward the tool, never away.
- **README rewritten for accuracy.** The "high-value claims" framing is gone —
  no value-based selection exists in the code, which asks for *every*
  citation-backed claim and relies on the coverage audit to disclose what
  extraction missed. New "Testing and evaluation" section separates software
  tests from model evaluation. Newly documented: batch judges multi-reference
  claims against the first available source; Claude proposes page/block/anchor
  phrases while Python locates them and draws the boxes; the model reads
  extracted text with page markers, not page images; the coverage audit reads
  bracketed numeric citations only. The single real-run example is framed as one
  illustrative run, not a measurement.
- Coverage wording throughout now says *labels reached by an extracted claim*
  rather than "covered" — the audit measures extraction reach, not that a source
  was read.
- `jsonschema` added to the `dev` extra; `testpaths` now includes `evals/tests`.

## [0.3.1] — 2026-08-16 (beta)

### Changed

- **Single license: MIT for everything** — code, prompts and skills alike.
  The CC BY-SA carve-out for prompts (`LICENSE-prompts`) is removed; bundled
  fonts remain third-party under SIL OFL 1.1. (The v0.3.0 source archive
  still contains the old dual-license file — this release supersedes it.)
- Honest "local" wording: the CLI and case files are local, and the README
  now says plainly that claim checking processes relevant text through
  Claude Code — confirm journal AI/confidentiality policies before auditing
  an unpublished manuscript.
- Removed stale `journal_packs` references (sdist include list,
  CONTRIBUTING) left over from the packs' removal; an `--exhaustive`
  checking mode joined the roadmap.

## [0.3.0] — 2026-08-16 (beta)

First public release under the PaperTrace name.

### Added

- Full batch pipeline: ingest → refs → scout → check → highlight → report
  (`papertrace run`), plus interactive `/review` and `/fact-check` skills for
  Claude Code.
- Open-access-only reference resolution (Crossref → Unpaywall → Europe PMC →
  arXiv) with an honest per-reference manifest: `retrieved / provided /
  paywalled / mismatch / no_doi / unpublished / error`, each with a reason.
- **Title sanity check on retrieved PDFs**: a downloaded copy whose first
  page doesn't look like the cited reference is rejected as `mismatch`
  instead of being judged against the wrong text (catches mistyped DOIs in
  reference lists).
- Page-level evidence: red-box crops placed by text search on the real
  source page — including table cells and in-figure numbers with the
  layout-aware (docling) ingest.
- Deterministic citation-coverage audit ("labels 7, 12 unaddressed"), an
  uncited-assertions register, and explicit disclosure when a citation style
  isn't recognized (bare superscripts) instead of a vacuous pass.
- A failed model check surfaces as `unchecked` with the reason — never
  disguised as a retrieval gap.
- Literature scout (Europe PMC): published-since and existed-but-uncited
  candidates, clearly framed as candidates.
- One case folder per paper — pointing a different paper at a used case is
  refused, so two audits can never mix.
- Reports as markdown, dark editor-window HTML, terminal-run HTML, and
  optional PNG export (`--png`, playwright).
- Committed demo: a fictional mini-review with planted citation errors, its
  pre-generated report in `examples/demo/output/`, and the four-step
  sequence to reproduce it.

### Known limitations

- Claim checking requires a local [Claude Code](https://claude.com/claude-code)
  login (`claude -p`); no API-key path yet.
- Bare-superscript citation styles (Nature-family layouts) are not parsed by
  the coverage audit — the report discloses this instead of auditing.
- The scout uses Europe PMC only; absence from its lists proves nothing.
- Verdict wording can vary slightly between runs of the model checker.

### Pre-history

Versions 0.1–0.2 were developed under the working name *ManuscriptAgent*
(manuscript-review focus). 0.3.0 reframes the tool to post-publication paper
auditing: published papers by design, retrieval gaps as first-class results.

[0.3.1]: https://github.com/defraction0/PaperTrace/releases/tag/v0.3.1
[0.3.0]: https://github.com/defraction0/PaperTrace/releases/tag/v0.3.0
