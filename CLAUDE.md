# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The one rule that overrides convenience

**An unread source never receives a verdict.** Honest degradation over silent
failure: if a step cannot do its job, it says so in the report — it never
guesses, and it never emits a plausible-looking value in place of an admission
of ignorance.

Every subsystem encodes this, and most past bugs have been breaches of it:

- A source that couldn't be retrieved yields `not_retrieved` locally, with **no
  model call** (`check.py`).
- A malformed model response yields `unchecked` with a note — never a default
  verdict.
- Input truncated at `MANUSCRIPT_CHAR_LIMIT` / `SOURCE_CHAR_LIMIT` is recorded
  in `RunResults.truncated` and disclosed in the report.
- A co-cited claim is judged against **every** retrievable cited source, one
  call each, and `ClaimResult.unjudged_refs` holds only those that could not be
  obtained — so an entry there means nobody read it, not that it lost a
  tie-break. `not_addressed` exists so a source that says nothing about the
  claim is not laundered into `partial` or `contradicted`.
- An evidence crop whose anchor phrase matched nothing sets
  `anchor_located = False` and is captioned as unboxed.
- A flat-text ingest fallback stamps `converter: pymupdf` into the source map so
  the fidelity loss appears in the report.

When you add a feature, ask what it does when it fails. If the answer is "falls
back to something reasonable", that is a bug in this codebase.

## Commands

```bash
pytest                            # whole suite: offline, no model calls, CI-safe
pytest tests/test_refs.py -q      # one module
pytest tests/test_refs.py::test_paywalled_is_honest -q   # one test
pytest -k coverage -q             # by name
pytest tests/test_report_viewer_js.py -q   # the viewer's browser logic, under node (skips without it)
ruff check src tests scripts evals
uv build                          # sdist + wheel (python -m build is NOT installed)
```

Evaluation harness (separate from the test suite — see below):

```bash
python evals/runners/score_only.py --gold evals/gold/demo_v1.gold.json \
    --results evals/gold/demo_v1.observed.json --out /tmp/evalout   # offline
python evals/runners/run_eval.py ...    # live, paid model calls; refuses under CI=true
```

End-to-end smoke test — needs network and a logged-in `claude` CLI, ~5 min:

```bash
python examples/demo/make_manuscript.py
# --model is pinned so the committed showcase is reproducible: without it
# `claude -p` takes the account default, which silently changed the judge
# from opus to haiku between two regenerations of examples/demo/output/
papertrace run examples/demo/demo_manuscript.pdf -c demo_case \
    --model claude-opus-5 --format terminal --png
# expect: 1 supported · 2 contradicted · 1 not retrieved · 1 uncited assertion
# 4 claims, not 5: the sentence citing [2] and [3] comes back as ONE
# multi-source claim, because 0.5.0 asks extraction for the verbatim sentence.
# Reproduced on both claude-opus-5 and claude-haiku-4-5, so it is the prompt
# and not the model. Both planted contradictions, the paywalled [4] and the
# uncited assertion are what actually matter and are unaffected.
```

Install: `pip install -e ".[dev]"` for development (this is also exactly what
CI installs). **`docling` is a base dependency as of 0.5.0**, so CI installs it
and torch with it — but never *runs* it: the ~500 MB layout models download on
first use, not on install, and every test pins `backend="pymupdf"`
(`check_claims` makes `backend` a required argument so none can forget).
`playwright` must still stay out of CI; it is the `png` extra, needed only for
`--png`, with `playwright install chromium` once.

## Architecture

Six stages, each a CLI subcommand, chained by `run`. Every stage writes a
JSON/markdown artifact to the case folder and the next stage reads only that —
there is no in-memory pipeline object:

```
ingest → refs → scout → check → highlight → report
```

- **`ingest/`** — two backends behind one contract: `pymupdf_.py` (flat text,
  tables linearized) and `docling_.py` (layout-aware, ~500 MB model download on
  first run). Both are installed; `--backend pymupdf` is a deliberate choice,
  not a fallback for a missing package. `backend="auto"` resolves through the
  shared `resolve_backend()` and falls back loudly. Everything downstream reads
  only `source_map.json` and does not know which backend ran — except
  `check._stale_ingest`, which compares the recorded `converter` so a source
  map from an earlier run with the other backend is rebuilt rather than reused.
  **Cited sources are ingested with the same backend as the paper** (0.5.0);
  each source's converter travels in `RunResults.source_converters` and a
  flat-read source is named in all four reports.
- **`refs.py`** — resolves citations through legal open-access routes only
  (Crossref → Unpaywall → Europe PMC → arXiv), with a title sanity check that
  rejects a mismatched download rather than judging against the wrong paper.
  Per-ref status from `REF_STATUSES`. Also attaches **supplements** (0.6.0):
  `_named_for` is the one token-match rule, `_provided_candidates` and
  `_supplement_candidates` are that rule with `_SUPPLEMENT_RE` inverted, and a
  supplement attaches only to an already-available reference — the orphan is
  reported by `unused_provided`, never silently dropped.
  **`identify_by_content` decides ownership**: DOI first, then `titles_match`
  against a *short* title string. Deliberately **not** `_title_check_text` —
  that counts a reference's words across a whole page, which is right for
  vetoing a file the user already named and measurably wrong for discovery (it
  verified one demo source against two unrelated references). A non-unique match
  is refused, never ranked, and `titles_match` returning `None` is not an
  accept: a filename carries the user's assertion, content carries none.
  **Attributing provided files is a matching, not N independent lookups.** One
  file answers for one reference: `_exact_stem_claims` (the stem *is* the slug)
  and `identify_by_content` establish ownership, `resolve_all` builds the
  `owners` map, and `resolve_entry` drops any candidate owned elsewhere. Before
  that constraint existed, one `li-2023.pdf` answered for four references with
  `identity confirmed` on each, because `_named_for` keeps only slug tokens over
  three characters so `nce-2023`/`ma-2023`/`ren-2023` all collapse to the year.
  Do not restore token containment as an acceptance route: it is a proposal, and
  the veto behind it has no precision on a single-subject bibliography.
- **`check.py`** — the **only** module that calls a model, and only through the
  `_ask()` seam (`claude -p` subprocess; inherits the user's Claude Code login,
  no API key). Two prompts: `EXTRACT_PROMPT` then `CHECK_PROMPT`, one call per
  **document** so context stays small — an article, each of its supplements,
  and each of the audited paper's own are separate calls with separate verdicts. Also holds `coverage_audit()`, which is
  deliberately **mechanical and prompt-independent** — a regex
  (`_LABEL_GROUP`) over bracketed numeric labels, so a citation the extractor
  missed still surfaces. The module global `_LAST_MODEL` carries the judging
  model out to the report; truncation travels in a per-run `Truncations`.
- **`highlight.py`** — the division of labour that keeps evidence trustworthy:
  the model proposes page, block and verbatim anchor phrases; **Python** locates
  them with PyMuPDF `page.search_for` and draws the boxes. Boxes are never
  model-placed or hand-placed. `crop_for_anchor` returns **one image per
  `Block.region`** — a passage crossing a column or page break is several
  rectangles and cropping only the first showed where the evidence began and not
  where it was. The rectangles come from the converter's own provenance, never
  from clustering the search hits: a derived rule would be guessing at a recorded
  fact, and both candidate rules were measured and rejected. `crop_evidence`
  bounds the boxes by intersection with the region it is given, so one call per
  region needs no new box logic; the continuation filename carries the region
  **ordinal**, because two regions can share a page and the save is
  unconditional.
- **`models.py`** — the dataclasses *are* the wire format. `VERDICTS`,
  `REF_STATUSES`, `BLOCK_TYPES` and `DOCUMENT_KINDS` are the vocabularies;
  `to_json`/`from_json` pairs must stay symmetric, and `from_json` uses
  `.get(...)` defaults so older `results.json` files still load. **A judgement
  target is a document, not a reference**: `RefManifest.document(slug)` /
  `.documents()` resolve an article, a cited work's supplement or the audited
  paper's own behind one interface, so no consumer hand-rolls
  `next(e for e in entries if e.slug == slug)` — that shape can only ever find
  an article, and every supplement would be invisible to it.
- **`report.py`** — Jinja2 over `src/papertrace/templates/` (four templates:
  markdown, editor HTML, terminal HTML, and the interactive viewer). Templates
  are **package data** loaded via `importlib.resources`, not a repo-relative
  path — an installed wheel has no repo. Any new disclosure field must be
  surfaced in all four templates; the viewer renders the embedded disclosure
  list generically (run-level in its Summary tab, claim-level in the claim's
  caveats, the anchor state as the crop set's caption), so a new key reaches it
  without a template branch. The viewer embeds the case data as JSON escaped
  for a `<script>` context (`_script_json` — `<`, never `&lt;`, and the
  manifest without `pdf_path`) and inlines two scripts: `viewer_logic.js`, a
  DOM-free module (parsing `annotated.md`, anchoring quotes with the claim's
  `ctx_ids` block searched first, crop sets, filters, the claim map) that
  `tests/test_report_viewer_js.py` runs under node, and `viewer_app.js`, which
  only draws. Disclosures are never re-derived in JS.

The `case/` folder is the unit of work: one case per paper, guarded by
`_guard_case`. It is gitignored by design — manuscripts stay local.

## Citation coverage is per occurrence — and the rejected alternative

`coverage_audit()` counts every **place** the manuscript makes a citation, not
every distinct label. Two sentences citing `[3]` with one extracted claim used
to report `[3]` as covered: pure set arithmetic, and the tool's single largest
overstatement. Occurrences come from `source_map.json` (never `annotated.md`,
whose inline provenance markers shift every offset), with `clean.md` as the
fallback when a case folder has no map.

Three decisions not to re-litigate:

- **`labels_in_text`, `covered` and `missing` keep their label-level meaning
  byte for byte.** `evals/align.py` reads `missing` as a list of label strings
  to decide whether an unmatched gold case is the tool's failure or the
  evaluator's; reshaping it would move that blame silently, with no test going
  red. Everything occurrence-level is additive; `"schema"` is `coverage/3`
  since 0.5.0 and `coverage/2` files still validate. In particular `covered` is
  *not* "labels with ≥1 covered occurrence" — that would push an all-uncertain
  label into `missing`.
- **`uncertain` is a third status, never folded into either.** An attribution
  the tool cannot make counts as *not covered*, and the uncertain count is
  always printed beside the ratio: when it is large the ratio is close to
  meaningless, and a percentage alone hides that. Since 0.5.0 it has exactly
  one cause: a claim cites a label and names none of that label's contexts, so
  a claim reached one of them and nothing can say which.
- **Attribution is a lookup, not a match** (`coverage/3`). `citation_occurrences()`
  builds the inventory **before** the model call, `_render_inventory()` renders
  it as `ctx_NNNN` into `EXTRACT_PROMPT`, and each claim comes back carrying
  the ids it was taken from — resolved through the map built in that same pass,
  in `extract_claims`, and stored in `ClaimResult.ctx_ids`. A `ctx` not in the
  inventory is **dropped**, never repaired into "the first occurrence of that
  label".

  This replaced ~130 lines of similarity matching (`_attribute_label`,
  `_normalize_for_match`, `_ratio`, `_location_matches`, `OCCURRENCE_MIN_RATIO`,
  `OCCURRENCE_MIN_MARGIN`). Do not reintroduce a text-similarity fallback for
  an unresolvable `ctx`: that is the confident-wrong-pointer failure the
  redesign removed, and `uncertain` is the honest answer instead.
- **Reading-order zipping is still rejected**, and `ctx_NNNN` is not a licence
  to reintroduce it. The labels are *assigned* in reading order, but they are
  resolved through the mapping built with them — never by re-deriving position
  later. Any consumer that pairs the *n*th ctx with the *n*th occurrence of a
  freshly recomputed list has rebuilt the bug: one dropped occurrence shifts
  every id after it, silently.

## Non-negotiable gates

1. **Tests stay offline.** No network, no model calls, ever. HTTP is faked with
   `httpx.MockTransport` (`tests/test_refs.py`); PDFs are generated in-test with
   pymupdf and never committed. Tests prepend `src/` to `sys.path` and import
   with `# noqa: E402` — there is no `conftest.py` in `tests/`.
2. **New status, verdict or JSON field ⇒ schema update + round-trip test.** The
   files in `schemas/` are the published contract, not documentation.
3. **Never commit** a `*.pdf`, `*.docx`, or a `case/` folder. `.gitignore` blocks
   them as a confidentiality guardrail. The only committed output is
   `examples/demo/output/`.
4. **Touched `refs.py`, `check.py` or `scout.py`?** Prove the failure path still
   degrades honestly.
5. **Touched ingest, highlight or report?** Re-run the demo end to end.

## TDD

Write the failing test first, and one behavioral change per PR. A test that
passes before your change is not evidence of your change. Paste the command
output rather than asserting the result — "evidence before assertions" is the
project's own standard, applied to its own development.

Because `check.py` is the only model-calling module and it calls out through a
single seam, everything else is deterministically testable. Put the
deterministic part under test and `monkeypatch` the seam; if a change seems to
need a live model call to test, the logic is in the wrong module.

## Software tests ≠ model evaluation

Keep these separate, in the repo and in what you claim:

- `tests/` — conventional software tests of deterministic behaviour.
- `evals/` — measures *model judgement quality* against human gold labels.
  Specified in `evals/DESIGN.md`. Live runs cost money and never run in pytest
  or CI.

**No accuracy figure is claimed anywhere in the README, because none has been
measured.** Do not invent benchmark results, test counts or performance claims.
`examples/demo/` is a controlled demonstration with deliberately planted
citation errors — not a benchmark. Hard-coded test counts go stale; the CI badge
is the live signal.

## Lint and style

`ruff check src tests scripts evals` must be clean. Line length 100, target
py310, rules `E,F,W,I,UP,B`, `E501` ignored (the limit is a guide, not an error).
`cli.py` ignores `B008` for Typer's option-in-default idiom. `ruff format` is
**not** enforced — do not reformat files wholesale.

Python 3.10+ syntax is expected (`X | None`, not `Optional[X]`).

**Comments state constraints the code can't, not narration:**

```python
email = _email(email)          # fail fast — before the ingest models load, not after
_guard_case(case, manuscript)  # one case folder per paper — never mix two audits
```

Every module opens with a one-line docstring naming its job. Status words stay
lowercase in prose and reports (`not retrieved`, `supported`, `partial`,
`contradicted`).

## Documentation

The README is a correctness surface, not marketing: **every technical statement
must match the implementation.** Its "does / does not" list is the project's
honest scope — when behaviour changes, that list changes with it. Specific
current constraints documented there, worth not re-breaking: the coverage audit
reads bracketed numeric labels only; batch mode judges a co-cited claim against
every retrievable source and reports the most adverse verdict as the claim's
headline — where `not_addressed` is deliberately unranked and becomes the
headline only when no source addressed the claim at all; a substantive verdict
must name a page and a block that exist in the source's own map, so a verdict
nobody can be shown is `unchecked`; the model reads extracted text with page
markers, not page images.

Update `CHANGELOG.md` for any user-visible change, and `README.md` when flags,
the install matrix or the does/does-not list change.

## Handover

Long or multi-session work is handed over in writing, because the working tree
is often the only place the work exists:

- State the git reality first: branch, HEAD, how many commits vs. staged vs.
  uncommitted paths, and whether anything was pushed.
- Give the verification actually run, with real output — and label what was
  *not* verified.
- List deliberate omissions explicitly, so the next session doesn't "fix" a
  considered decision. Silently scaling work down is worse than reporting it.
- Flag anything stored outside the repo (`/tmp`, plan files) as ephemeral.

**Never commit or push unless explicitly asked.** No PyPI publish, no release,
no PR without explicit permission.

## Serena

Serena (semantic code search / symbol tools, via the official plugin MCP server)
is onboarded for this project. `.serena/` is **gitignored** — its `project.yml`,
`memories/` and pickled symbol cache are machine-local.

Prefer Serena's symbol tools over grep-and-read for "where is X defined / what
calls X" questions. But treat `.serena/memories/*.md` as **hand-written prose
that nothing recomputes**: it drifts from the code silently. Verify a memory's
claim against the actual file before acting on it, and correct the memory when
you find it stale.
