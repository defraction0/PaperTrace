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
- A limit set on request (`--max-claims`, `--max-sources`) is recorded — every
  reference nobody tried is `skipped` with `skipped_by` and a reason, the
  manifest carries `limits`, `results.json` carries `scope` — and
  `disclosures._scope` states it at the **end** of every format as well as
  with the caveats at the top. A limited run must never read as a smaller
  paper; `tests/test_audit_limits.py` is the contract.

Two rules about the wire format follow from it, both learned the expensive way
on 0.7.0:

- **A published field's meaning is never widened to fix a misleading
  sentence.** When `numbering_verified` (extent: the chosen list accounts for
  exactly the cited labels) read as "confirmed" beside a line withholding
  verdicts for a disputed label (content), the fix was the *sentence*, not the
  flag — four report formats already read that boolean, and stretching it to
  cover content would have silently changed what every one of them asserts.
  Add a second axis (`numbering_corroborated`, `labels_uncomparable`) or
  reword; do not redefine.
- **A zero or an empty value must not mean both "measured zero" and "never
  measured".** `labels_uncomparable` is `None` when nothing computed it and
  `[]` when every dispute was measured and every one was a contradiction;
  `numbering_corroborated` is `bool | None` for the same reason, after its
  docstring claimed three states for a type that held two. The report picks a
  cause only where one was actually measured, and `evals/DESIGN.md` says the
  same thing about a rate: *"Absent is not zero, and the two must never render
  the same way."*

When you add a feature, ask what it does when it fails. If the answer is "falls
back to something reasonable", that is a bug in this codebase.

## Commands

```bash
pytest                            # whole suite: offline, no model calls, CI-safe
pytest tests/test_refs.py -q      # one module
pytest tests/test_refs.py::test_paywalled_is_honest -q   # one test
pytest -k coverage -q             # by name
pytest tests/test_report_viewer_js.py -q   # the viewer's browser logic, under node (skips without it)
pytest tests/test_mcp_server.py -q  # the MCP server, via the SDK's in-memory client + one real stdio run
papertrace mcp                    # serve over stdio for an MCP host ([mcp] extra); nothing on stdout
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
    --model claude-opus-5 --format terminal --format viewer --png
# expect: 1 supported · 2 contradicted · 1 not retrieved · 1 uncited assertion
# and out/report_viewer.html beside report.md — open it in a browser; the
# README's viewer screenshots come from scripts/make_viewer_shots.py on it
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
`--png`, with `playwright install chromium` once. The MCP SDK is the opposite
case: the `[mcp]` extra, and in `[dev]` as well, so CI runs
`tests/test_mcp_server.py` instead of skipping it.

## Architecture

Seven stages, each a CLI subcommand, chained by `run`. Every stage writes a
JSON/markdown artifact to the case folder and the next stage reads only that —
there is no in-memory pipeline object:

```
ingest → extract → refs → scout → check → highlight → report
```

- **`extract`** (`cli._extract_pipeline`, over `check.extract_claims`) —
  writes `out/claims.json`: every claim numbered in reading order, with its
  quote, location, `ctx_ids` and cited labels, and **no verdict field** — a
  default `not_retrieved` written there would be a finding nobody made. It
  runs BEFORE `refs` so `--max-claims` can retrieve only what the selected
  claims cite, and `check` reads the list back (`cli._extraction_for`, on a
  manuscript-hash match only) rather than extracting again, so the numbers a
  selection names hold still. `check.select_claims` takes an **array of ids**
  — `--max-claims 5` is `[1, 2, 3, 4, 5]`, and a cherry-pick is any other
  array on the same parameter; do not add a count-shaped API beside it.

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
  **A table row in a bibliography is a reference, not a continuation** (0.7.0).
  Docling reads a hanging-indent numeral column as a table, so entries arrive as
  `|  6. | Author A (2019) … |` and the marker regex cannot see them (`\s*` does
  not cross a `|`). `_unwrap_table_rows` runs first and restores the bullet form;
  a row with *no* numeral cell is emitted unmarked on purpose, because docling
  promotes the table's first data row to the header row and that row is the tail
  of the bullet above it. Treating the rows as wrapped continuations read 22
  references as 18 and judged every claim citing [6] or above against a different
  paper — and the title check passed, because the glued raw string held both
  papers' words.
  **Never restore positional numbering after a printed numeral contradicts it.**
  `_numerals_agree_with_position` is the whole distinction: where the surviving
  numerals sit at the positions they name, position *is* the printed reading and
  is used; where one does not, an entry above it was merged or split, so the
  printed reading is unavailable and the positional one is known wrong.
  Those entries set `boundary_ambiguous` and refuse to resolve. Position remains
  a legitimate reading in exactly one case — a list that printed no numeral at
  all — and `reconcile` marks even that unverified. Nor may an extent check
  confirm a refusal: a refused entry keeps its positional label, so `_covers`'
  subset test is satisfied by exactly the labels in doubt, and
  `_refusals_unconfirm` is what stops a matching count printing "numbering
  confirmed" over a numbering the parser declined to stand behind.
  **Agreement is per printed label, and disagreement is refused rather than
  ranked** (0.7.0). `reconcile` arbitrates between the Crossref deposit and the
  run backend's parse, unchanged; a flat-text pymupdf reading and a model
  reading (`reflist.py`, `--llm-refs`) are **voters only** — neither reaches
  `reconcile`'s arguments nor `resolve_all`, so on their own they cannot cause
  a source to be resolved, downloaded or judged: they withhold a verdict, and
  (through `stamp_seen_in`) name themselves on an entry they also carried.
  **Do not restate that as "no model output can": one path escapes it and is
  gated on a person.** `_escalate_disputed` menu option 1 makes a *second*
  model call (`reflist.resolve_disputed`) and substitutes each resolved entry
  into the chosen list, which `resolve_all` downloads and `check.py` judges.
  The substitution is mandatory, not optional: returning `entries` untouched
  un-disputes the label while leaving the entry the resolution ruled *against*
  as the paper judged, which is the original wrong-paper bug reached through
  the one path a user authorised. What keeps it honest is the gate, and the
  gate is what may not be relaxed — an interactive run, the full disagreement
  on disk before anything is asked, `numbering_chosen_by: "user"`, only the
  labels actually resolved substituted, and no answer setting
  `numbering_verified`.
  `label_agreement` joins on `e.num`, the printed numeral, never on position:
  `_first_divergence` zips, and would compare docling's 6th entry against
  pymupdf's 6th and report divergence for the wrong reason. Any pair
  `_comparably_same` answers `False` for makes the label `disputed` — no majority vote,
  because *"a non-unique match is refused, never ranked"* — and `single`
  deliberately **prints**, since a pymupdf-backend run whose model candidate
  was discarded has one reading, every label would be `single`, and the audit
  would report nothing at all. **`_same_work` is deliberately not reused here.**
  It returns True when either side has no comparable title — right for
  `_first_divergence`, where silence must not manufacture a divergence — and
  here it once reported two demonstrably unrelated papers as `agreed`, which is
  the one state that lets a verdict through. `_comparably_same` asks the mirror
  question and is three-valued: `None` is *cannot tell*, and a voter it answers
  `None` for **abstains** rather than dissenting. Collapsing that to `False`
  made a Crossref deposit of bare DOIs dispute every label it voted on while
  the two parses agreed perfectly — an audit with no verdicts in it. The one
  exception is a label where *no* voter said anything comparable at all
  (`_says_something_comparable`): that is `disputed`, because nothing
  establishes what the label names. So `disputed` has three causes — duplicate,
  contradiction, nothing comparable — `_label_state` returns which, and
  `labels_uncomparable` publishes the third as a subset of `labels_disputed`
  **by construction**, both read off the same call. Never compute the subset
  from a second pass, and re-intersect it after every mutation: the escalation
  can take a label out of `labels_disputed`, and a subset computed before it
  then names a label the manifest no longer disputes.
  A `boundary_ambiguous` entry does not speak for its label and neither does a
  reading carrying that label twice: the first has said it cannot stand behind
  the label, and for the second, which of the two it means is the question. The
  model's reading may not **corroborate** at all (`DERIVED_READINGS`):
  `reflist.propose` may only copy values out of the two extractions, both of
  which vote in their own right, so crediting it is one text counted twice.
  Every field of the model's reply must be found **verbatim** in one of the two
  texts it was shown or it is discarded, and one entry's unverifiable title
  discards the model's **whole reading**, never just that entry — nothing
  invented may name a paper. `numbering_corroborated` is a second, independent
  axis: `numbering_verified` keeps its exact meaning and stays `False` through
  every model reply and every interactive choice, which
  `tests/test_numbering_invariant.py` parametrises over and which is the one
  thing in this feature that may not be relaxed for convenience.
  `models._title_tokens` strips DOIs the way it strips URLs, and for the reason
  the URL strip's own docstring gives: an identifier's substrings are not words
  anybody wrote as a title. A DOI-only Crossref deposit (`_reference_raw`'s
  documented fallback, and the majority of the deposit on the paper that
  prompted this) was contributing `radiol` and `jamanetworkopen` as title
  words, which disputed every label it voted on and pushed `_title_check_text`
  toward `mismatch` on correct retrievals. Stripping it is global, not scoped
  to the comparator: `_same_work` and `_title_check_text` both move, both
  toward honesty, and both movements were measured before the strip landed.
- **`ask.py`** — the **only** file in `src/` that runs a subprocess, and the
  only place this codebase shells out to a model (`claude -p --safe-mode
  --tools ""` in a private scratch cwd; inherits the user's Claude Code login,
  no API key). "`check.py` is the only module that calls a model" was the rule
  until `refs` needed a reading of the bibliography too — the rule was
  protecting the seam, not the module, and `tests/test_ask.py` greps `src/` and
  asserts exactly one file, which makes it enforceable rather than
  conventional. Two callers, `check.py` and `reflist.py` (driven by the `refs`
  stage in `cli.py` — `refs.py` itself imports neither), and no third without
  that test going red. The model is recorded **per call
  site** (`for_site`, `model_for`, `SITE_CHECK`, `SITE_REFS`), not in one
  global: `_LAST_MODEL` was overwritten by every call, so a run whose judging
  made zero calls — every cited source `not_retrieved`, nothing to judge —
  printed the reference-list model as the `Checker:` of verdicts it never saw.
  The record assumes one command per process; `forget_models()` clears it for
  a process that outlives one run, which the MCP server calls per audit.
  `_ask`'s signature is frozen at `(prompt, model=None)`; every offline test
  that patches the seam does so with a two-argument lambda, which is why the
  site travels out of band in a context manager instead of as a third
  parameter. **No integer here**: the count moves with every test added or
  removed, and `ask.py` and `reflist.py` each still carry a stale one in a
  comment.
  **Limits** arrive as `resolve_all(only_labels=, limit=)`: a reference no
  selected claim cites, or one past the cap on sources *obtained* (successes,
  not attempts), is `_skip`ped — status `skipped`, `skipped_by`, a reason —
  and never resolved, provided file or not. The CLI passes those kwargs only
  when set, so every fake of the seam that predates them keeps working.
- **`check.py`** — every prompt and every verdict rule, and no subprocess of
  its own. Two prompts: `EXTRACT_PROMPT` then `CHECK_PROMPT`, one call per
  **document** so context stays small — an article, each of its supplements,
  and each of the audited paper's own are separate calls with separate
  verdicts. Call the bare name `_ask(...)`, imported `from .ask import _ask`, so
  `monkeypatch.setattr(check_mod, "_ask", …)` still intercepts; rewriting a
  call site as `ask._ask(...)` bypasses every patch and turns the offline suite
  into live paid calls. `_ask_with_retry` wraps the seam and reads
  `ASK_ATTEMPTS` rather than hardcoding one retry — the wizard prints a
  worst-case bill derived from that constant. Also holds `coverage_audit()`,
  which is deliberately **mechanical and prompt-independent** — a regex
  (`_LABEL_GROUP`) over bracketed numeric labels, so a citation the extractor
  missed still surfaces. **A `disputed` label is dropped from `avail` before any
  call is made**, so a source whose identity two readings contradict is never
  judged; if nothing survives the claim is `unchecked` with the labels named,
  never `not_retrieved` — that source was obtained, and `withheld_refs` is a
  different field from `unjudged_refs` for exactly that reason. `last_model()`
  reads the `check` site; truncation travels in a per-run `Truncations`.
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
  only draws. Disclosures are never re-derived in JS. The MCP server is a fifth
  reader: it serialises the same `Disclosure` objects with `_disclosure_dict`,
  generically, and `tests/test_mcp_server.py`'s parity loops assert every
  token reaches it.
- **`mcp_server.py`** — `papertrace mcp`, an MCP server over stdio
  (`mcp>=2.2,<3`, the `[mcp]` extra) with nine tools. Seven read a case folder
  and compute nothing: every verdict and count travels with its disclosures,
  the scope sentence first and again as the final `limited` key; a missing
  `results.json` is a `ToolError`, never empty counts; evidence is served only
  from inside `<case>/out/`, and no `pdf_path` leaves. `start_audit` runs
  `cli._run_pipeline` **in-process, on one daemon thread** — never a
  subprocess, which `tests/test_ask.py` would refuse — and `audit_status`
  long-polls for at most 50 s, inside the 60 s a TypeScript-SDK host allows a
  request. A server outlives many audits, which the CLI never did, so the job
  resets what a process used to reset by exiting: `cli.console` becomes the
  job's log (under stdio, stdout is the protocol's wire), `sys.stdin` becomes
  an empty stream (`_interactive()` is False, so a disputed label is withheld
  with `chosen_by: "default"` and the person-gated escalation is unreachable —
  do not offer MCP elicitation in its place: a host may be a model), and
  `ask.forget_models()` runs first. Those three are process-global, hence
  **one audit at a time**, and read tools refuse a case whose audit is still
  running. **Any new process-global state in the pipeline must be reset per
  audit here.** In SDK v2 only a `ToolError`'s message reaches the model — any
  other exception is a bare "Error executing tool" — so every refusal is one.
  Return types are `typing_extensions.TypedDict` with `structured_output=True`:
  `typing.TypedDict` silently cost every tool its output schema below 3.12.

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
