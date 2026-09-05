<p align="center">
  <img src="https://raw.githubusercontent.com/defraction0/PaperTrace/main/assets/logo.png" width="180" alt="PaperTrace — pixel-art logo: a paper page under a magnifying glass, one line boxed in red">
</p>

<h1 align="center">PaperTrace</h1>

<p align="center">
  <a href="https://github.com/defraction0/PaperTrace/actions/workflows/ci.yml"><img src="https://github.com/defraction0/PaperTrace/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
</p>

<p align="center"><b>Check what a scientific paper claims against what its cited sources actually say.</b></p>

<p align="center">PaperTrace retrieves legally available cited PDFs, checks the paper's<br>
citation-backed claims against the text of the cited pages, and shows the evidence —<br>
the matched text boxed in red on the real page.</p>

<p align="center"><b>An unread source never receives a verdict.<br>
A missed citation is reported, not silently skipped.</b></p>

<p align="center">
  <a href="examples/demo/output/report.md">See a completed report</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#ethics--scope">Ethics &amp; scope</a> ·
  <a href="https://github.com/defraction0/PaperTrace/releases/latest">Latest release (beta)</a>
</p>

<p align="center"><sub>Open source · Python 3.10+ · local CLI and case files · claim checking currently uses <a href="https://claude.com/claude-code">Claude Code</a> · built for published papers</sub></p>

<p align="center">
  <a href="https://github.com/defraction0/PaperTrace/blob/main/examples/demo/output/report_terminal.png"><img src="https://raw.githubusercontent.com/defraction0/PaperTrace/main/docs/hero.png" width="80%" alt="One checked claim from the demo report: the paper claims an external validation AUC of 0.94, the verdict is CONTRADICTED, and the cited source's real page shows the matched text — an AUC of 0.77 — boxed in red. Click for the full report."></a>
</p>

---

> **See the result first — no install needed.** The demo report committed at
> [`examples/demo/output/report.md`](examples/demo/output/report.md) audits a
> fictional mini-review with planted citation errors and real, published
> references: **2 supported · 2 contradicted · 1 not retrieved · 1 uncited
> assertion** — the planted errors, and exactly them, *in that run*. Extraction
> and judgement are model steps, so the committed report is an inspectable
> artefact, not a guaranteed re-run.

Pick a paper that matters to you — the landmark your project builds on, the
method paper you are about to adopt, your own published work. PaperTrace
answers three questions about it: **do its citations say what it claims they
say?** — **what has been published since?** — **what existed at the time but
went uncited?**

## What it does — and what it does not

**PaperTrace does**

- Retrieve what the paper cites through legal open-access routes only
  (Crossref → Unpaywall → Europe PMC → arXiv) — and reject a downloaded PDF
  that doesn't look like the cited paper (title sanity check), rather than
  judge claims against the wrong text. The check needs 35% of the reference's
  distinctive words on the retrieved first page; a first page that is **empty
  or unreadable** (scanned, image-only) **passes** — unverifiable is not the
  same as wrong, so a scanned source is checked rather than silently discarded.
- Attempt to extract **every** citation-backed claim, then judge each against
  the text of its cited source. Every `supported`, `partial` or `contradicted`
  verdict carries a page **and** the source block it rests on, both checked
  against that source's own ingest — a verdict naming a page or block the
  source does not have is reported `⚠ not checked`, not published. `◌ does not
  address the claim` carries no page by design: the source was read and says
  nothing, so there is no passage to point at.
  Extraction is a model step, so it is an attempt, not a guarantee — which is
  why the coverage audit below exists.
- Show the evidence: real page crops with the matched text boxed in red.
  Claude proposes the page, the block and verbatim anchor phrases; Python then
  finds those phrases in the PDF and draws the boxes — placed by text search,
  never by hand, and never by the model. The crop region comes from the source
  block the verdict names, so a crop whose anchor phrase matched nothing is
  still shown — unboxed, and captioned as unboxed. Where no anchor phrase was
  offered at all, the caption says that instead: "searched and not found" and
  "never searched for" are different facts and are never merged.
- Preserve unavailable sources as explicit gaps: a claim whose source
  couldn't be retrieved is `⊘ not retrieved` — recorded, never guessed.
- **Check its own reference numbering before trusting it.** The citation label
  is the join key between a claim and the source it is judged against, so a
  list off by one produces a confident audit of the *wrong papers*. Two
  independent readings are taken — the tool's parse of the printed list, and
  the reference list the publisher deposited with Crossref (`refs --doi`,
  defaulting to the DOI printed on page 1) — and the manuscript's own `[N]`
  markers arbitrate between them. A reading is used only if it accounts for
  exactly the labels the body cites. When neither does, the audit continues,
  the report says the numbering is unconfirmed, and every verdict on a claim
  citing a doubtful label carries that caveat beside it. Crossref is a second
  reading, **not** an oracle. A deposit this tool can only partly read is set
  aside rather than used to renumber a longer list, and the shortfall is
  reported as the tool's own, not the publisher's. A deposit can also be
  genuinely short — one record in this project's spread carries 2 references
  for a paper citing about 40 — and nothing in the payload gives that away,
  because Crossref's own count field counts what was deposited. The
  manuscript's labels are what catch it. And the DOI is checked against the
  paper before its record is trusted: a deposit whose Crossref record is titled
  as some other paper is set aside. The paper's title is taken from the PDF's
  own metadata where it states one, since the largest heading on a first page
  is often the article-type banner rather than the title. Where the titles
  cannot be compared, the paper's own bibliography settles it — the works the
  publisher deposited are looked for in the reference list printed in the
  paper — and where neither can, the list is used and the manifest says the
  identity behind it was never confirmed.
- Report every citation **occurrence** — each bracketed marker at its own place
  in the text — that no extracted claim reached, so a second sentence citing an
  already-checked reference is not silently counted as covered. It also
  registers assertions carrying no citation at all. **Detection** is mechanical
  and prompt-independent (a regex over bracketed numeric labels): if extraction
  skipped a citation, it shows up here. **Attribution** of a claim to a specific
  occurrence is a text match the tool can get wrong; an attribution it cannot
  make is reported as *uncertain* and counted as **not** covered, never as
  covered.
- Judge a co-cited claim against **every** cited source it could retrieve, one
  model call each, and show the passage behind each verdict. Co-citation is an
  offer of support, so each source is checked on its own text: a claim citing
  four references gets four verdicts, four notes and four evidence crops, with a
  count beside it (*"4 cited sources checked: 2 fully support it; 1 partially
  supports it; 1 contradicts it"*). The claim's headline is the **most adverse**
  verdict any of them gave, so one dissenting source is never averaged away —
  and on a multi-source claim the headline says so on its own line
  (*"❌ contradicted — most adverse of 4 cited sources"*), because a compound
  sentence may legitimately draw different parts from different references, and
  one citation conflicting is not the same finding as the statement being
  wrong. A source that turns out to say nothing about the claim is `◌ does not address
  the claim` — an inapt citation, distinct from a contradiction and from a
  retrieval gap. It is deliberately **not ranked** among the three: while any
  source actually spoke to the claim, that source decides the headline, and
  `◌` becomes the headline only when no available source addressed the claim
  at all. The per-source breakdown beside the headline is where an inapt
  citation stays visible.
- Disclose its ingest fidelity: every report — markdown, editor and terminal —
  names the converter that read the **audited paper**, and a flat-text fallback
  says so loudly. Cited sources are ingested separately (see *Tables and
  figures are evidence too*).
- Keep the human responsible for interpretation — it prepares evidence and
  drafts; the conclusions are yours.

**PaperTrace does not**

- Bypass paywalls — what it can't get legally, it reports as not obtainable.
- Treat model memory as evidence — verdicts come only from retrieved or
  user-provided pages.
- **Guarantee** that every citation-bearing sentence was checked. The
  extraction prompt asks for all of them, but a model's recall is not a
  guarantee — so the coverage audit reports which labels no claim reached.
  That is a disclosure mechanism, not proof of exhaustive sentence-level
  coverage (an `--exhaustive` mode is on the roadmap).
- Audit every citation style. The coverage audit reads **bracketed numeric**
  labels only — `[12]`, `[7,8]`, `[9-11]`. Author-year, parenthetical and
  bare-superscript styles are not audited, and the report says
  *"coverage not audited"* rather than quietly reporting zero gaps.
  This is not a rare corner: superscript numerals lose their superscript when a
  PDF is flattened to text, so `burnout.<sup>1</sup>` arrives as `burnout. 1`
  and is indistinguishable from prose. Three of the seven papers in this
  project's test spread — Wiley, AMA and one Elsevier journal — cite that way.
  For those papers the numbering has no arbiter either, so the reference list
  is reported as unconfirmed rather than presented as checked.
- Count a mixed claim as mixed in the **totals**. The per-claim headline is
  qualified and the per-source breakdown sits beside it, but the run's summary
  counts (and `results.json`) tally each claim once, under its headline — so a
  claim whose four sources split 2 support / 1 partial / 1 contradict appears in
  the `contradicted` total and nowhere else. Read the totals as *"claims with at
  least one contradicting source"*, not as *"claims that are wrong"*.
- Read the source pages as images. In batch mode the model receives the cited
  source as extracted text with `page / block` provenance markers — the page
  picture is for you, in the evidence crop, not for the judge.
- Tell you *why* a co-cited source was never opened, beyond its retrieval
  status. Every cited source that could be obtained is judged; one listed as
  unopened could not be retrieved, and the manifest carries the reason.
- **Read the audited paper's supplemental material.** Only the single PDF you
  pass is ingested. A claim living in an eTable, appendix or supporting-
  information file is not extracted, its citation labels are not counted in the
  coverage audit, and it is **not** reported as unread — it is simply absent.
  This is the one gap in the tool that is silent rather than disclosed, so it is
  named here. Merge the supplement into the main PDF before running if you need
  it audited.
- **Fetch the supplements of cited sources.** The resolver chain returns the
  article. A claim resting on a source's supplementary figure or table is judged
  against its main text alone, which can come back `partial` or `does not
  address the claim` for a reason that belongs to retrieval rather than to the
  source.
- Prove that an uncited article *should* have been cited — scout hits are
  candidates for your judgement, never accusations.
- Guarantee an exhaustive literature search — the scout is search-based, and
  absence from its lists proves nothing.
- Replace peer review or your research judgement.

## Quick start

### Guided — `papertrace`, and answer the questions

```bash
git clone https://github.com/defraction0/PaperTrace && cd PaperTrace
pip install -e ".[full]"     # standard install — layout-aware ingest
papertrace                   # asks for the paper, the DOI and your email
```

*(PaperTrace is not on PyPI yet, so the clone is not optional — `pip install -e .`
installs the checkout you are standing in.)*

Nothing to memorise. It checks your setup first — so a missing `claude` CLI is
a sentence before you type anything, not a traceback twenty minutes in — then
asks one question at a time: the paper (drag the file in; quotes and escaped
spaces are fine), where to keep the audit, whether the paper is published, and
a contact email it offers to remember. Before spending anything it tells you
how many model calls the run will make and asks you to confirm, and when you
say yes it prints the equivalent one-line command so you can repeat or script
it next time.

`papertrace start` does the same thing explicitly. Without an interactive
terminal — a pipe, a CI job — bare `papertrace` prints help instead of waiting
on stdin.

<p align="center">
  <img src="https://raw.githubusercontent.com/defraction0/PaperTrace/main/docs/wizard.png" width="85%" alt="The guided audit in a terminal: a setup check listing the claude CLI and layout-aware ingest as present and PNG export as missing with its one-line fix, then the questions one at a time — the paper's path, the case folder, a DOI found on the first page offered for confirmation, a contact email it offers to remember — and finally the cost stated as up to 25 model calls before asking permission to start.">
</p>

### Interactive — the `/review` skill (deepest mode)

```bash
git clone https://github.com/defraction0/PaperTrace && cd PaperTrace
pip install -e ".[full]"     # standard install — layout-aware ingest
claude                       # start Claude Code here
> /review
```

*(First run of the layout backend downloads docling's models — ~500 MB, once.
On a constrained machine, `pip install -e .` gives the light flat-text core.)*

The interactive audit interviews you: the paper's PDF, any reference PDFs you
already have — and, if you are using it for peer review, screenshots of your
journal's reviewer form, which it reads and answers question by question.
Then it retrieves, checks, crops, and drafts, showing you evidence as it goes
and batching its questions.

### Batch — one command, scriptable

```bash
git clone https://github.com/defraction0/PaperTrace && cd PaperTrace
pip install -e ".[full]"        # standard install (see matrix below)
export PAPERTRACE_EMAIL="you@example.org"     # Unpaywall asks for a contact
papertrace run paper.pdf --provided ./my_pdfs      # case folder: ./paper/ beside the PDF
```

Install options:

| Command | What you get |
|---|---|
| `pip install -e ".[full]"` | ⭐ **standard install** — layout-aware ingest (real tables, figures, lists) + PNG rendering. Pulls torch; first run downloads docling's layout models (~500 MB, once) |
| `pip install -e ".[docling]"` | layout-aware ingest only |
| `pip install -e ".[png]"` | PNG report rendering only |
| `pip install -e ".[dev]"` | the test and lint tooling — `pytest`, `ruff`, `jsonschema`. This is what CI installs |
| `pip install -e ".[dev,full]"` | everything: run audits **and** run the suite |
| `pip install -e .` | minimal core — flat-text ingest. For CI and constrained machines; every report will carry a "tables linearized" warning |

> **`[full]` does not include the test tooling.** The extras are independent:
> `full` is user features, `dev` is `pytest` + `ruff`. Installing `[full]` and
> then running `pytest` finds whatever `pytest` happens to be on your `PATH` —
> usually a system one, with none of this project's dependencies — and fails
> with `ModuleNotFoundError: No module named 'pymupdf'`. If you intend to run
> the suite, install `".[dev,full]"` and invoke it as `python -m pytest`, which
> fails loudly instead of silently using the wrong interpreter.

`--backend auto` (default) uses docling when installed and falls back to flat
text otherwise — and the report always says which one ran, because a
linearized table is a degradation worth disclosing.

**`--provided` matches by filename**, so the name decides which file stands for
a reference. Files must contain the reference's author and year (`pyrros-2023`
matches `pyrros-2023.pdf` and `pyrros-et-al-2023-chest-radiographs.pdf`), and
where several match, an exact `<author>-<year>.pdf` wins, else the shortest
name. A filename that reads as supplemental material — `supplement`, `appendix`,
`supporting information`, `ESM`, `online only` — is **not** used as the source,
and if it is the only match the reference is left to the online resolver
instead: a supplement is not the paper it accompanies. Rename it to the plain
`<author>-<year>.pdf` if you do mean it to stand in. Provided files are
title-checked like downloaded ones, but a mismatch is recorded in the manifest
rather than refused — you named the file, so it is used and the doubt is
disclosed.

Output in `<case>/out/` — where `<case>` defaults to a folder named after the
paper, beside the paper (`paper.pdf` → `./paper/`), and `-c` chooses another.
It holds `report.md` with inline evidence images, the same
report as a dark **editor-window** page and as a **terminal-run** page
(`report_editor.html`, `report_terminal.html`), plus machine-readable
`results.json` and `scout.json`. The retrieval manifest is written one level
up, at `<case>/refs_manifest.json`. Want shareable PNG images of the report
looks? Add `--png` (one-time setup: `playwright install chromium`).

**`--doi` is the DOI of the paper you are auditing** — not of anything it
cites. It is optional, it defaults to the DOI printed on the paper's own first
page, and it feeds two steps:

- **The reference-numbering check** (`refs`, and so `run`). It fetches the
  reference list the publisher deposited with Crossref, as a second reading to
  measure the tool's own parse against. The record's own title is compared with
  the paper's first, so a mistyped or mis-scraped DOI cannot substitute another
  paper's bibliography; a comparison too thin to settle it leaves the list in
  use and the identity disclosed as unconfirmed. Without a DOI there is only
  one reading, and the manifest says the numbering is unconfirmed rather than
  implying it was checked.
- **The literature scout**, which has to identify your paper in Europe PMC
  before it can look for work published since, or work in the field you did
  not cite.

Verdicts, evidence crops and coverage figures still come only from the
retrieved sources — but *which* source a claim is judged against depends on the
reference numbering, so a `--doi` that confirms the numbering can change the
audit's answers.

- **Published paper** → pass it, or let it be detected. Either way the record
  the scout finds is checked against the paper's own title: a record that is
  some other paper stops the scan and is reported, rather than anchoring both
  registers to it, and a comparison too thin to settle leaves the scan in place
  with the identity disclosed as unverified. `resolved_via` says which query
  answered — `doi` or `title` — and, since a detected DOI answers `doi` too, it
  is not the thing to read for reliability; `identity` is.
- **Unpublished manuscript** → there is no DOI to pass. The scout can never
  identify it, so use `--no-scout` to skip that step rather than reading an
  empty result as "nothing to find"; the guided flow does this for you. The
  numbering check has nothing to compare against either, and says so.

**One case folder per paper.** `case` is only the default name — give each
paper its own (`papertrace run zhang2025.pdf -c zhang2025`). Re-running the
same paper into its case is fine; pointing a *different* paper at a used
case is refused, so two audits can never mix.

Batch checking runs on headless Claude Code (`claude -p`) — it inherits your
existing login, **no API key to configure**. Each call runs with `--safe-mode`
and no tool access, from a neutral working directory: the judge only ever
reads the prompt it is given and returns a verdict, regardless of which
project's `CLAUDE.md` or `.claude/` config happens to sit above the directory
you ran `papertrace` from. It is the **only step that calls a
model**; every other step is plain Python. Ingest, crops and reports are also
**deterministic** — same input, same output. Retrieval and the scout are
**not**: they query Crossref, Unpaywall, Europe PMC and arXiv live, so their
results depend on those services, on their index state and on the date of the
run. Re-running the same paper a month later can legitimately produce a
different manifest.

## What a real run looks like

> **One run, not a benchmark.** What follows is a single unscripted audit,
> reported to show the *shape* of the output. It is not an accuracy
> measurement, the numbers would move between runs, and the artefact is not
> shareable — the source PDFs are publisher-controlled, so the case folder
> cannot be committed. For what is measurable and inspectable, see
> [Testing and evaluation](#testing-and-evaluation).

A real, unscripted audit of a published paper (Zhang et al., *Nature Mental
Health* 3, 1168–1180, 2025 —
[doi:10.1038/s44220-025-00501-8](https://doi.org/10.1038/s44220-025-00501-8)):
86 cited references, of which 22 had legal open-access copies — the other 64
are recorded as not obtainable, never guessed. 15 extracted claims were read
against their cited pages: **8 supported, 7 partial, 0 contradicted**. The
partials are the interesting part — a Methods sentence calling tests
"well-established" whose own cited source describes them as "brief and
bespoke, non-standard", and multi-reference claims where the retrieved source
carries one half of the claim while the unretrieved co-citation is *named* as
the possible carrier of the other half. Candidates for your judgement, not
accusations.

<p align="center">
  <a href="https://github.com/defraction0/PaperTrace/blob/main/docs/real_audit_terminal.png"><img src="https://raw.githubusercontent.com/defraction0/PaperTrace/main/docs/real_audit_terminal.png" width="80%" alt="Excerpt of a real audit of a published paper: three checked claims, each shown with the actual page of its cited source and the matched text boxed in red — a Methods claim its own cited source describes differently, a supported claim, and a two-reference claim split into its checked and unretrieved halves"></a>
</p>

## Tables and figures are evidence too

A number in a table cell is in the PDF's text layer, and so is text drawn
inside a figure **when the figure carries a text layer at all** — a vector
chart usually does, a scanned or raster-exported one does not, and nothing can
box text that is only pixels. Where the text is there, the red box lands on it
whichever backend read the document, because `highlight` searches the real
page, never the extracted text:

<p align="center">
  <img src="https://raw.githubusercontent.com/defraction0/PaperTrace/main/docs/table_figure_evidence.png" width="85%" alt="Two evidence crops: a table cell (N = 8382, 84.3%) and a number inside a flow-chart figure (97%), each boxed in red">
</p>

Both crops above come from **cited sources** whose block types (`table block`,
`picture block`) come from ingesting those sources with the layout backend by
hand — in batch mode `check` reads a cited source as flat text.

Whether such a number can be *claimed and checked* in the first place is a
different question, decided by what the backend hands the model:

| | a table cell | text drawn inside a figure |
|---|---|---|
| **flat text** (`pymupdf`) | reaches the model linearised — the row and column it belongs to are lost | reaches the model as loose words, with no figure to belong to |
| **layout-aware** (`docling`; standard install, audited paper only) | reaches the model as a GFM table | the figure arrives as `[FIGURE: <caption>]`; in-figure text arrives only where docling's layout model found a text region inside the figure |

On the one paper measured for this, it found none: of 9 figures, 5 carried text
in the PDF's text layer, and docling emitted no text block anywhere inside a
figure region — so that text reached the model nowhere, while the flat-text
backend did carry it. One paper is not a rate and none is claimed; what is
claimed is only that in-figure text is **not guaranteed** on the layout path.

So the box is equally trustworthy either way, and the claim behind it is not:
a table-cell claim is strongest under the layout backend, and an in-figure claim
is the weakest evidence this tool produces — under the layout backend the judge
may never have seen the number, and under flat text it saw the number without
the figure that gives it meaning.

That layout fidelity is spent on the **audited paper**. In batch mode a cited
source that has **not yet been ingested** is ingested with the fast flat-text
backend, so its tables reach the judge linearised and its figures only as
whatever loose words sat inside them.
`check` reuses an existing `case/ingest/<slug>/annotated.md` if one is already
there — so a source you ingested yourself with `papertrace ingest --backend
docling` keeps its layout, and the report does **not** currently distinguish
the two cases.

## Try the demo yourself

The committed report above was produced by this exact sequence — a fictional
mini-review with **planted citation errors** and real, published references.
The resolver fetches the open-access sources live, then the checker catches
the plants: an AUC quoted as 0.94 where the cited paper says 0.77, an
attendance figure that contradicts the cited flow chart, a headline resting
on a paywalled source that is honestly reported as unverifiable, and one
assertive sentence with no citation at all.

One run of this demo took 95 seconds end to end with the layout models already
downloaded — one run, not a benchmark. The first time adds two one-time downloads
(chromium ~150 MB, docling's layout models ~500 MB). **Prerequisite:**
[Claude Code](https://claude.com/claude-code) installed and logged in — the
claim checker runs on `claude -p`.

```bash
pip install -e ".[full]" && playwright install chromium   # 1 · install
export PAPERTRACE_EMAIL="you@example.org"                 # 2 · Unpaywall contact
python examples/demo/make_manuscript.py                   # 3 · build the demo paper
papertrace run examples/demo/demo_manuscript.pdf -c demo_case   # 4 · audit it
```

When it finishes, open `demo_case/out/report.md`. Expected result:
**2 supported · 2 contradicted · 1 not retrieved**, one uncited assertion
flagged, and all 5 citation occurrences — spread across the 4 labels — reached
by an extracted claim, 0 uncertain. (The scout step reports the fictional paper
as *not identified* in Europe PMC — the tool would rather say so than invent
neighbours. Verdict wording varies run to run, and
extraction and judgement are live model behaviour that nothing in the code
constrains — so treat these numbers as what the demo has produced, not as a
promise. If a re-run misses a plant, that is a real result about the model, and
`examples/demo/` documents each plant so you can see exactly what was missed.)
Details per plant:
[`examples/demo/`](examples/demo/).

> **Note:** the committed `examples/demo/output/` is the output of a real run
> (2026-08-30, docling 2.118.1) and matches what the current code produces. It
> stays an inspectable artefact, not a byte-exact expected output: judgement
> wording differs between runs, and so can the page an anchor is found on — the
> crop for claim 4 moved from page 1 to page 2 across two runs that reached the
> same verdict. No claim in the demo cites more than one reference, so the
> per-source breakdown and its summary count do not appear in it.

## How it works

```
paper.pdf ─────ingest──▶ clean.md + source_map.json       (page + bbox for every block)
      │
      └─refs──▶ refs_manifest.json                        (per-ref: retrieved / provided /
                + sources_resolved/*.pdf                    paywalled / mismatch / no_doi /
      │                                                     error + reason)
      └─scout─▶ out/scout.json                            (europe pmc: published-since +
                                                            existed-but-uncited candidates)
      │
      └─check─▶ results.json                              (per-claim verdict + page anchor;
                                                            unavailable source ⇒ not_retrieved)
      │
      └─highlight─▶ out/evidence/claim_NN.png             (red box on the matched text)
      │
      └─report──▶ report.md · report_editor.html/png · report_terminal.html/png
```

The JSON contracts are versioned in [`schemas/`](schemas/). The two skills in
[`.claude/skills/`](.claude/skills/) drive the same tools interactively; the
audit craft lives in [`prompts/review_core.md`](prompts/review_core.md).

## Ethics & scope

- **Built for published papers.** Everything PaperTrace reads is already
  public, so no confidentiality question arises in its primary use.
- **A note on peer review.** The interactive workflow also handles a full
  reviewer's job — reading the journal's form, checking the claims, drafting
  comments — and remains fully supported. But many journals prohibit sharing
  unpublished manuscripts with AI tools: if you use it on a manuscript under
  review, confirm your journal's policy first and disclose AI assistance
  where required. PDFs and case folders remain outside Git (the repo's
  `.gitignore` refuses `*.pdf`/`*.docx` outright), but relevant text is
  processed through Claude Code during checking — so confirm the journal's
  AI and confidentiality policies before using PaperTrace on an unpublished
  manuscript.
- **You are the judge.** The tool prepares evidence and drafts; the
  conclusions are yours. Scout hits are candidates, not accusations — verify
  before you act on any finding.
- **No paywall bypassing.** The resolver uses legal open-access routes only;
  what it can't get, it reports as not obtainable.
- **No verdict laundering.** An unread source can never yield a supported
  claim — `not_retrieved` is a first-class result and appears in every
  report. The scout is search-based and says so: absence from its lists
  proves nothing.

## Testing and evaluation

These are two different things, and the project keeps them apart on purpose.

**Software tests — what CI runs.** `tests/` holds conventional unit and
integration tests. They make **no network calls and no model calls**: the
reference resolver runs against `httpx.MockTransport`, PDFs are generated
in-test with pymupdf, and the single `claude -p` touchpoint sits behind a seam
that tests replace. They run in CI on every pull request and on every push to
`main`, across Python 3.10–3.14 (see the badge above). A push to a feature
branch with no open PR triggers nothing — run `pytest -q` locally. They tell you the *plumbing* is correct — ingest, retrieval,
coverage arithmetic, crop placement, report rendering, JSON round-trips.

**They tell you nothing about whether the model's verdicts are right.**

**Model evaluation — under development.** Judging claims is a model step, so
its quality is an empirical question that unit tests cannot answer. The
protocol for answering it — evaluation unit, paired cases, metric definitions,
labelling policy — is specified in [`evals/DESIGN.md`](evals/DESIGN.md), with
an offline scoring harness in [`evals/`](evals/). A formal evaluation dataset
is **still being built**; what ships today is the design, the scaffold, and a
demonstration fixture.

**The demonstration is not a benchmark.** [`examples/demo/`](examples/demo/) is
a small controlled example: one fictional manuscript with **deliberately
planted citation errors**, citing four real published papers. It shows the
pipeline catching defects that were put there on purpose. It is five cases,
authored and labelled by this project's own maintainer, and it measures
nothing about performance on real manuscripts.

No accuracy figure is claimed anywhere in this README, because none has been
measured.

## Development

```bash
pip install -e ".[dev,png]"
pytest          # fixtures only — no network, no LLM
ruff check src tests scripts evals
```

Scoring a run against a gold set is offline and separate from the test suite:

```bash
python evals/runners/score_only.py \
    --gold evals/gold/demo_v1.gold.json \
    --results evals/gold/demo_v1.observed.json \
    --out evals/runs
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for how to report paper-format
failures — the feedback that improves the tool fastest. The pixel logo is
generated: `python scripts/make_logo.py`. Changes are tracked in
[`CHANGELOG.md`](CHANGELOG.md); cite the tool via [`CITATION.cff`](CITATION.cff).

## Roadmap

- [ ] Retraction & correction flags on cited references
- [ ] More citation styles in the coverage audit — author-year, parenthetical
      numerics and bare superscripts (as in Nature-family journals). The
      report currently says "not audited" instead of silently passing
- [ ] `--exhaustive` mode — close the gap between what extraction is asked
      for and what it returns: labels the coverage audit reports as unreached
      get a focused second extraction pass, and a citation with no checkable
      assertion ("see [12]") is classified as a pointer rather than forced
      into a verdict
- [ ] More scout backends (OpenAlex, Semantic Scholar)
- [ ] Support for other LLM backends (local and API models alongside
      headless Claude Code)
- [ ] Claude Desktop integration
- [ ] MCP server — drive PaperTrace as a tool from any MCP-capable client
- [ ] DOCX ingest
- [ ] Revision (R1) mode polish
- [ ] Figure-vs-text consistency pass (batch)
- [ ] PyPI release
- [ ] Journal review packs — may be added in the future

## License

MIT — code, prompts and skills alike (see [`LICENSE`](LICENSE)). The bundled
fonts in `src/papertrace/templates/assets/` are third-party, under the SIL Open Font
License 1.1 (see the `*-OFL.txt` files there).
