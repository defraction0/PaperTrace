# Changelog

All notable changes to PaperTrace are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [0.7.0] — unreleased

### Fixed — a bibliography rendered as a table was read as four fewer references

Docling reads a hanging-indent numeral column as a table and emits GFM, so the
entries arrive as `|  6. | Author A (2019) … |` rows. The marker regex cannot
see them (`\s*` does not cross a `|`) and `_parse_bulleted` treated each row as
a wrapped continuation of the bullet above it. **On the manuscript that
surfaced this, 22 printed references parsed as 18, and every claim citing [6]
or above was judged against a different paper** — the citation label is the
join key, so the verdicts were confident and about the wrong papers.

The mis-attribution was invisible to the one check that should have caught it:
gluing five references onto one entry left `_entry` scraping the *next*
reference's DOI onto it, and the existing title check **passed** on that DOI,
because the glued raw string contained the words of both papers. A stronger
check would not have helped either — the reference string really did name the
paper that was downloaded, alongside the one the label meant.

A pipe row in a bibliography is now a reference: `_unwrap_table_rows` runs
before the marker regex, a numeral cell restores the bullet form, and a row
with no numeral cell is emitted unmarked so the existing continuation rule
joins it to whatever preceded it — which is what docling's promoted header row
actually is.

### Fixed — a diacritic in a reference deleted its own correct download

`_title_tokens` folds a reference's words (`Küstner` → `kustner`) so that a
surname with a diacritic contributes tokens at all. `_title_check_text` scored
those folded tokens against a page that was only lowercased, and the score is a
substring test — so none of them matched. Measured against a first page
carrying the reference's own title verbatim, `7/7 verified` became `2/9
mismatch`, and a mismatch is not a shrug: `_accept` unlinks the downloaded PDF,
records `mismatch`, and tells the reader the reference has *"a wrong or
mistyped DOI"*. A correct retrieval destroyed and the manuscript blamed for it,
on German, Scandinavian, Polish, Turkish, Spanish and Portuguese references —
the cases folding was introduced for. Both sides of the comparison are folded
now, and `_fold`'s docstring says so.

### Fixed — printed numerals that contradict their position are refused, not renumbered

`_usable_printed_numerals` required the first numeral to be `1`, so a list
whose converter stripped the early numerals and kept the later ones was refused
wholesale — and then numbered by position anyway, which is the guess the rule
exists to avoid. Position is now checked against every numeral that survived:
where they agree, the positional reading *is* the printed reading, corroborated
wherever the printing survived. Where they contradict it, something above them
was merged or split, so neither reading is available and the entries from the
first contradiction on refuse to resolve rather than risk a wrong paper.

A refused entry keeps a positional label — it needs one to be a slug and a
download path — so the extent check `_covers` performs was satisfied by exactly
the labels in doubt. A five-item list refused from entry 4, against a body
citing [1]-[5], reported *"numbering confirmed"*, fired no numbering
disclosure and caveated no claim. `reconcile` no longer confirms a numbering
any entry in the chosen reading refuses, which closes the same hole on the
pre-existing duplicate-label refusal.

### Added — the numbering ledger, and a second reading that disagrees is disclosed

`numbering_ledger` (schema: `refs_manifest`) names which labels are duplicated
and which are carried by no entry, instead of reporting a difference of two
totals — which understated a real run sixfold: *"[1]-[19] vs 18 references"*
where five references were dropped and one label duplicated. A difference of
totals cannot reveal a duplicate at all.

`numbering_contested` is now persisted and rendered in all four formats. It was
declared with a comment saying that burying it was how a compensating parse
error could pass unmentioned, and was then never written to the manifest and
never shown. It fires when the two readings of the bibliography stop describing
the same paper **at or below a label the body cites** — not merely when the
other reading failed the extent check, which is satisfied by a deposit
identical for every cited label and longer by two references nobody cites.
Publishers routinely deposit those.

### Changed — the unconfirmed-numbering note reads as sentences

The note reaches markdown, editor, terminal and viewer verbatim, and it had
four em-dashes in one sentence with the Crossref clause spliced into the middle
of it — where `CROSSREF_NO_DOI` carries an em-dash *and* a full stop of its
own, so the sentence appeared to end at *"Pass --doi if the paper does have
one"* and then resumed at *"that does not add up"*. The ledger is its own
sentence now, the Crossref clause is last and stands on its own, and a single
duplicated label *appears* rather than *appear*.

### Fixed — the guided wizard wrote the audit somewhere the user had not named

The case-folder prompt was the one path answer that never went through
`clean_path`. Finder's drag-and-drop quotes any path containing a space, so the
answer began with a literal `'` — which made it a *relative* name, and the
audit was written to a directory called `'` under wherever the user happened to
be standing, while every line the run printed named an absolute folder that did
not exist. The other three path prompts were immune by accident: they check
`.exists()`, and a quoted path fails that. A case folder is *created*, so
nothing could contradict it.

All four prompts share `clean_path` now. A relative answer is still accepted —
`-c demo_case` is in the README — but it is resolved and the resolution is
printed on one unwrapped line, which is where a mangled path becomes visible
before the first paid model call rather than after all of them. An answer of
whitespace arrives as `.` and is refused, for the reason `default_case` already
refuses the working directory: an audit needs a folder of its own.

### Added — a third and fourth reading of the reference list, and a per-label verdict on the numbering

`reconcile` arbitrated between two readings of the bibliography: the tool's own
parse and the reference list the publisher deposited with Crossref. Both can be
absent — an unpublished manuscript has no DOI to look up — and where the parse
is the only reading, nothing can contradict it.

Two more candidates now stand beside them. A flat-text (pymupdf) parse of the
same PDF, free on a docling run and skipped on a pymupdf one because it would
be identical. And a structured reference list proposed by a model, which is
shown both texts and given no authority over either: **every field of its
reply must be found verbatim in one of them or it is discarded**, one entry's
unverifiable title discards the model's **whole reading** — a title is the
one field that names the paper, so a reply that got one wrong cannot be
trusted about the rest — and the model's list never becomes `reconcile`'s
chosen reading. It is a voter. On by default, `--no-llm-refs` to turn it off,
on both `refs` and `run`.

Agreement is then computed **per printed citation label** — joined on the
numeral the page carries, never on position — and reported as `agreed`,
`single`, `disputed` or `absent`. Where two readings name different papers at a
label, verdicts on claims citing it are **withheld**: the claim is reported
`unchecked` with the label named, not `not_retrieved`, because the source was
obtained and read. `single` deliberately prints, with the caveat it already
carried. `numbering_corroborated` is a new, independent axis beside
`numbering_verified`, which keeps its exact meaning.

Where nothing accounted for the body's labels and labels are in dispute, an
interactive run writes the full disagreement to
`case/out/reference_disagreement.md` — every reading's fields and the verbatim
text each was read from — **prints the path, and only then asks** what to do
about it. Four options: resolve the disputed labels with a model, withhold
verdicts on all of them, adopt one reading whole, or abort. Whatever is
answered is recorded as `numbering_chosen_by: "user"`; a person consenting to
proceed is an input, not evidence, and nothing a user answers can set
`numbering_verified`. A non-interactive run withholds the disputed labels and
asks nothing.

Partial resolution is a normal, representable, reported outcome:
`labels_resolved` and `labels_disputed` are both non-empty on a run where some
labels were settled and some were not, and "cannot tell" from the resolution
call is a correct answer that keeps a label withheld.

### Added — `ask.py`, the one seam, with the model recorded per call site

Every model call went through one `_ask` in `check.py`, which was a convention
stated in `CLAUDE.md` with nothing enforcing it. It is now a file —
`src/papertrace/ask.py`, the only file in `src/` that runs a subprocess — and
`tests/test_ask.py` greps `src/` and asserts exactly that. Same flags, same
timeout, same sandbox: `--safe-mode`, `--tools ""`, a private 0700 scratch cwd.

### Fixed — a run that judged nothing could name a judge

The model that judged the claims was a single module global overwritten by
every `_ask` call, and the report's `Checker:` line rendered it. With `refs`
also calling the seam, a run whose judging made zero calls — every cited
source not retrieved, so nothing to judge — would have printed the
**reference-list** model as the judge of verdicts it never saw. The model is
now recorded per call site, and `last_model()` reads the judging site only:
`None` where no judging happened, never the other site's answer.

### Fixed — claim extraction had no retry, and the wizard's cost ceiling assumed it did

`ASK_ATTEMPTS` exists so the wizard's advertised worst-case bill cannot drift
from the real retry policy. The retry loop did not read it — it hardcoded one
retry and matched the constant only because the constant happens to be 2 — and
the single extraction call, whose failure fails the whole run, had no retry at
all. Extraction now retries like everything else, and the wizard's ceiling moved
with it.

### Changed — the coverage of `unjudged_refs`, and a second axis on the numbering

`ClaimResult.withheld_refs` is a new field and is **not** `unjudged_refs`. An
entry in `unjudged_refs` means the source could not be obtained — nobody read
it. A withheld reference **was** obtained; what is in doubt is whether it is the
paper the label names. Putting it in `unjudged_refs` would report a retrieval
gap that does not exist.

`numbering_verified` is unchanged in meaning and unchanged in what can set it.
`numbering_corroborated` is reported beside it, which lets the numbering
disclosure stop crying wolf on the case it was firing on wrongly: a list two
readings agree about entry for entry, longer than the highest cited label
because three of its references are cited only in the supplement.

### Known staleness — `examples/demo/output/` predates this feature

`--llm-refs` defaults on, so a fresh demo run now makes one extra model call
and, on the docling backend the demo uses, emits the `reflist` disclosure in
all four formats. The committed showcase in `examples/demo/output/` was
generated before this feature existed and shows none of it. That is staleness
in the committed artefact, not a defect in the feature — the showcase needs a
fresh end-to-end run (network, a logged-in `claude` CLI) to pick it up, and
that run is a branch-level step, not part of this change.

## [0.6.0] — 2026-09-13 (beta)

Carries 0.4.1 and 0.5.0 with it. Neither was ever published, so neither has a
tag; their sections below are the record of what went out in this release.

### Added — an interactive report viewer: `--format viewer`

`report_viewer.html` is a fourth look on the same `results.json`, and the
first one built for *reviewing* rather than reading: the processed manuscript
on the left with every audited sentence underlined in its verdict's colour,
and on the right — for whichever sentence is selected — the cropped source page
with its red boxes, the checker's rationale and the caveats. Summary cards, a
claim map (one box per audited claim in reading order, grouped by section),
per-source and gap registers, the scout's candidates, verdict/section/text
filters, keyboard navigation (`j`/`k`/`r`/`/`), a reviewed checkbox per claim
that persists in the browser, and export (markdown regenerated with the
reviewed marks, print, a review checklist as JSON) live in the right column.

It works offline. `results.json`, `annotated.md`, `scout.json`, the retrieval
manifest and every disclosure are embedded in the page — escaped for a script
context, so a cited source's text cannot end the element it sits in — the two
scripts are inlined, and the fonts ride in `assets/` beside it. Nothing is
fetched when it is opened: a report on an unpublished manuscript must not phone
a font host.

The manuscript is `ingest/manuscript/annotated.md`, whose block markers let
each audited sentence be placed by text search. A claim's `ctx_ids` name the
block the extractor read it from, and that block is searched first, so a
sentence the paper repeats lands where it was cited and not on the first
paragraph that happens to carry it. What cannot be placed is counted as such —
*"125 of 137 audited sentences located in the text"* — and the claim's panel
says *quote not located in the processed manuscript text*; there is no
nearest-sentence fallback. A case folder with no `annotated.md` still opens:
the page builds a skeleton from the audited sentences and says on its face
that the full manuscript is not shown.

Disclosures are decided in Python and travel with the data, never re-derived
in the browser. The page renders every one — run-level in the Summary tab,
claim-level in the claim's caveats, the anchor state as each crop set's single
caption (the red box may sit in any crop of a passage that crosses a break, so
no crop is captioned on its own) — and the parity suite now asserts each token
in four formats rather than three. The manifest is embedded without
`pdf_path`: the viewer is the one report meant to be sent to someone else, and
the absolute paths of the auditor's PDFs are not part of the audit.

`--png` does not screenshot it — a sticky header over a panel that scrolls on
its own records nothing as a static image — and does not imply it. The
browser-side logic is a DOM-free module, `templates/viewer_logic.js`, run
under node by `tests/test_report_viewer_js.py` (which skips, visibly, when
node is not on PATH); `templates/viewer_app.js` only draws.

Reachable from every way in. Batch: `-f viewer` on `run` or `report`. The
guided wizard asks *"Also write the interactive viewer beside report.md?"*,
default yes, and the one-line command it prints for next time carries the
answer. The `/review` skill writes it in its outputs step and hands over the
path. `run` closes by naming the page to open. The README has a section on it
with two screenshots, produced by `scripts/make_viewer_shots.py` from a real
case rather than drawn — the demo's own, since the showcase has now been
re-run and `examples/demo/output/report_viewer.html` is committed beside
`report.md`. Its `assets/` is not: the fonts are already package data, and the
page falls through to Georgia without them.

### Changed — the mark: a trace from the claim into the boxed evidence

PaperTrace has a mark. A dot sits on a line of the paper, routes like a
circuit trace, and lands in a red box around the evidence — the same red as
the evidence boxes in every report. It replaces the pixel-art page and
magnifier in three places: the viewer's header and favicon (inlined, so the
page still touches no network), the README lockup (a light and a dark variant,
chosen by the reader's colour scheme), and the terminal banner that
`papertrace`, `run`, `init` and the guided wizard print — the wizard opened
with a bare title before. The banner lives in `brand.py` now, one module for
the CLI and the wizard.

The vectors ship as package data in `templates/brand/`, verbatim from the
design hand-off with their content-credentials manifests intact; the viewer
strips the manifest when inlining, since a signed block inside an HTML page
verifies nothing and would add 8 KB to every report. `papertrace-logo-dark.svg`
is derived here from the bare dark mark and the lockup's wordmark, because the
ink wordmark of the light lockup is unreadable on a dark page.

Not touched: `docs/social_preview.png` and `docs/hero.png` still show the old
logo; the social preview is regenerated by hand from `assets/logo.png`
(`scripts/make_social_preview.py`), which stays for that reason.

### Added — a provided PDF is identified by what is in it

`--provided` matched on the **filename** and nothing said so. It needs the
reference's surname and year in the name, which a reference-manager export has
and a publisher download never does:

```
pyrros-2023.pdf                              matches
Pyrros et al. - 2023 - Opportunistic....pdf  matches
s41467-023-39631-x.pdf                       no
1-s2.0-S0140673623001234-main.pdf            no
41467_2023_39631_MOESM1_ESM.pdf              no   (the standard Nature supplement name)
```

So a user who dragged in a folder of downloads got an audit that looked
entirely normal and used none of it — and the failure was **asymmetric**: an
unmatched supplement was reported, an unmatched *article* was skipped in
silence.

Each unrecognised PDF is now identified from **its own DOI**, else from **its
own title** compared against the reference list. Filename matching still runs
first and still wins: that is the user's own assertion about the file, and
content only fills the gap it leaves. Supplements are identified the same way,
which matters more than it sounds — the publisher forms carry no filename
marker at all (`\besm\b` cannot match inside `MOESM1_ESM`, and `mmc1` and
`media-1` say nothing) while their first page states plainly what they are.

**Nothing in the folder goes unremarked.** `unused_provided` lists every PDF
that ended up attached to nothing, with the reason kept apart: unrecognisable,
ambiguous, a spare copy of a paper already matched, or a supplement whose
article is missing.

⚠️ **Two refusals, both deliberate.** A title matching **two** references is
used for neither — a corrigendum shares nearly every distinctive word with its
original, and picking the better score would judge a claim against the wrong
paper with nothing downstream able to notice. And a title with too few
distinctive words to tell papers apart is not a match: a filename match may be
accepted as `unverifiable` because the user named the file, but nobody asserted
anything about a file identified by content.

Not reused for this: `_title_check_text`, the rule that already vets a
filename-matched file. Measured on the demo's real sources it verifies
`pyrros-2023.pdf` against an unrelated NEJM review as well, because it counts a
reference's words anywhere on a whole page and both are about AI in medical
imaging. It is a forgiving veto for a file already chosen, and it stays that.

`Supplement.verified` and `SourceJudgement.verified` record which supplements
were established to belong to their work. The 0.6.0 disclosure said
*"supplements carry no identity check"*; that was true of all of them then and
is true of only some now, so the report states the split per file instead of
warning about both equally.

Also fixed: a surname under four characters is dropped by the filename token
filter, so `liu-2019` matched on the **year alone** and `smith-2019-appendix.pdf`
would attach to Liu 2019 — with no title check to catch it, since supplements
had none. Such a match now requires the slug itself in the filename.

The guided wizard asks *whether* you have cited PDFs before asking *where*,
defaulting to yes when `<case>/sources` already holds some, and asks the same
about the paper's own supplementary material. A user with neither now answers
two questions instead of reading two explainers and two path prompts.

### Added — supplementary material, read as its own document

A subgroup table in Supplementary Table S2, a sensitivity analysis in Appendix
B, a protocol in an ESM: real papers put the decisive evidence outside the
article, and a user holding that file had no way to hand it over. Worse,
`refs.py` recognised supplement filenames **only in order to discard them**,
because judging a claim against an appendix while calling it the cited source
is the laundering this tool exists to prevent.

The organising idea is that **a judgement target is a document, not a
reference**. Multi-source checking already judged one claim against N documents
— one model call each, per-document verdicts and crops, most-adverse headline,
`not_addressed` unranked so a silent document taints nothing. A supplement
enters as one more document, which is why this needs no new verdict, no new
headline rule and no change to `coverage/3`.

- **A cited work's supplements need no flag.** Drop
  `pyrros-2023-supplement.pdf` beside `pyrros-2023.pdf` in the sources folder.
  Several per reference is fine. Each is judged separately, and a claim citing
  `[14]` is read against every document `[14]` has.
- **`--supplement` (repeatable) for the audited paper**, which has no reference
  slug for a filename to key on. A claim pointing at its own `Table S3` is read
  against those; with none supplied it is `not retrieved` and names the flag,
  rather than sitting in the uncited register — the paper said where its
  evidence was and nobody opened it.
- **A supplement never stands in for the article.** It attaches only to a
  reference that was actually obtained; an orphan is named in the ticker with
  the reason, because a file the user supplied that then did nothing is the
  quietest possible failure.
- **The wizard now asks for the sources folder**, which it never did:
  `run_wizard` hardcoded `provided=None`, so the guided path could not reach a
  flag the CLI has had all along.

⚠️ **Two disclosures you should expect to see.** Supplements carry **no
identity check** — a supplement's own title is not its parent's, so the check
that guards every cited source cannot apply, and it is not faked. And a
citation appearing *only* inside a supplement is **not counted** by the
coverage audit, which reads the manuscript alone. Both are stated in all three
reports whenever supplements were read, and a claim whose headline came from a
supplement rather than the article body says so on the claim.

Slugs come from the file stem, never an ordinal: `-suppl1`/`-suppl2` numbered
in folder order is the shifting-id defect this codebase already rejects for
citation occurrences, where deleting one file re-points another document's
stored verdicts and crops.

Wire format: `RefEntry.supplements`, `RefManifest.manuscript_supplements`,
`SourceJudgement.kind`, `ClaimResult.own_supplement`, all schema-declared and
absent-safe, so 0.5.x files still load. `RefManifest.from_json` also stops
raising `TypeError` on a key it does not know — a manifest from a newer
papertrace used to kill an older one outright.

Both prompts changed, so `evals/provenance.prompt_fingerprint()` moves and
`agreement.py` will refuse to compare a 0.6.0 run against an earlier one. That
is the guard working, not a regression.

Judgement quality here is **unmeasured**, like everything since ADR 0001.

### Fixed — a dropped table cell is a disclosure, not console noise

A real audit printed roughly two hundred lines of this during ingest:

```
MatchingPostProcessor WARNING  Orphan pdf_cell 186 recovered to col=6 by
                               nearest-column fallback (row=11, x=620.5)
```

They come from docling's TableFormer post-processor via
`logging.getLogger("MatchingPostProcessor")` — a bare top-level name whose
**parent is `root`**, which the library levels itself and to which it attaches
its own `StreamHandler(sys.stdout)`. `_quiet_third_party_loggers` levels
`"docling"`, so it could never reach it.

Those are repairs and they are noise. Buried among them was one that is not:

```
5 of 65 pdf cells matched neither a row nor a column band of the 24x4 grid
and were dropped from the table
```

That is text missing from a cited source's table, reaching the user only as a
stray line from a third-party library. **Silencing the logger without surfacing
that would have hidden a fidelity loss**, so the same filter does both: it
swallows the logger's output and captures what is not a repair.

The classification **defaults to surfacing**. Only the self-describing recovery
messages count as noise; anything else that logger emits is kept and reported.
The wording belongs to `docling-ibm-models` and changes between versions — the
message above does not exist in the version resolved by `docling>=2.0` at the
time of writing — so matching the loss literally would under-report silently on
a version nobody has seen. Matching the *repair* fails the safe way.

A `logging.Filter` rather than a level, because the library calls `setLevel` on
that logger itself while the models load, overwriting anything set beforehand;
filters are consulted after the level check. It is installed for one conversion
and removed even when the conversion raises.

Wire format: `SourceMap.table_warnings` and `RunResults.source_table_warnings`,
both schema-declared. `table_warnings` has **three** answers —
messages (something was lost), `[]` (watched, nothing lost), and `null` (nobody
watched: a map written before this, or a backend with no table model). `null`
must never be read as "nothing was lost". The new `table_loss` disclosure
carries the converter's own count verbatim, because "5 of 65" is the finding and
"cells were dropped" is not.

### Fixed — a passage crossing a column or page break is shown in full

An evidence crop was one rectangle on one page, so a passage continuing into the
next column or overleaf was shown only as far as its opening. The reader saw
where the evidence began and never where it was.

The cause was ingest throwing away data it had been given. `prov` is a list and
`_item_prov` returned `prov[0]`, so only the rectangle a block's opening sat in
survived — while docling states `page_no`, `bbox` **and** `charspan` for every
rectangle. Measured on one 14-page cited source: **14,367 characters sat outside
the rectangle their block claimed**, and five blocks were on two pages at once
while `Block.page` named one of them.

```
block_0012  3 rectangles, text length 2554
  page 2  chars    0- 295   ← the only one kept
  page 2  chars  296-2152   the right column
  page 3  chars 2153-2554   overleaf
```

`Block.regions` now records all of them and `crop_for_anchor` returns one image
per rectangle, in reading order, each with its own matched text boxed.
`crop_evidence` is untouched — it already boxed exactly the hits intersecting
the region it was handed.

Measured on a real 101-reference audit before the fix: **17 of 31 anchored
judgements had located anchor text outside the crop region**, 4 of them on the
next page. On a fresh end-to-end run of one of those claims, the verdict's
evidence turned out to be on page 3 of the source — a page the report had no way
to show, so the crop came back unboxed and captioned "no anchor phrase could be
boxed" while the phrase sat two rectangles away.

`anchor_located` is now true when **any** image carries a box, and
`_downgrade_unshowable` no longer discards a verdict whose boxes are in a
continuation. The continuation filename carries the region ordinal, not the
page: two rectangles can share a page, and the unconditional save would have
left one image holding the other's picture.

Wire format: `Block.regions` (source map) and `continuation_images` on both the
claim and the judgement (results). Both schema-declared and absent-safe —
**empty means not recorded, never "no continuation"**, so an older source map
keeps the single-rectangle behaviour it always had. Continuations appear only
for sources ingested after this change.

Two caption defects went with it, both found by rendering the report and reading
it. The first image carried the judgement's anchor caption, so a crop where the
passage merely opens — no box in it at all — was captioned "red box = matched
text"; the caption describes the set and now sits below the images. And
"the anchor phrase was located on this page" was false whenever the box is in a
continuation on another page.

### Fixed — a quote crossing a column break was boxed nowhere at all

A two-column page splits a sentence at the column break, so a decisive passage
often continues in the next column. `crop_evidence` bounded its text search
with `search_for(phrase, clip=region)` — and PyMuPDF discards the **whole**
match when any part of it falls outside the clip, not just the outside part.
One phrase on one real page:

```
unclipped         [[268, 206, 278, 219], [40, 220, 154, 232], [305, 60, 407, 72]]
clip = the block  []
```

So no box was drawn, and the crop was captioned as unboxed — the tool
admitting a failure it had not suffered, on a quote that was verbatim on the
page. The search is now unclipped and the hits are filtered by intersection
with the region, so the crop region bounds what is **rendered** and no longer
decides what counts as a match. The invariant that makes a box trustworthy is
unchanged: a box is only ever drawn where `search_for` found the text, and only
inside the region on screen.

Still open, and unaffected by this: the region is one block's bbox on one page,
so the continuation itself is not shown.

### Fixed — an unboxed crop no longer says the phrase is absent from the page

`anchor_located = False` records that nothing could be boxed **inside the
cropped region** — and the region is one block's bbox. A passage continuing
into the next column is on the page and outside the region at once, so
"no anchor phrase was found on this page" asserted an absence nobody
established. It is live in the demo: claim 4's three anchor phrases have five
hits on page 3 of the cited source, under that caption.

```
- no anchor phrase was found on this page — the crop is shown for context and nothing is boxed.
+ no anchor phrase could be boxed — none was found inside the region this crop shows, so the crop is shown for context only.
```

⚠️ `ANCHOR_NOT_LOCATED_TOKEN` changes value. It is a published literal that
`tests/test_pipeline.py` and `tests/test_multisource.py` assert appears in all
three formats; anything outside this repo matching the old string will stop
matching. The `located` wording is untouched — a boxed phrase really was found
on the page.

`examples/demo/output/report.md` still carries the old sentence: it is the
output of a live audit and is regenerated by a demo run, not edited.

### Fixed — one provided PDF answered for four different references

Found by auditing a real 101-reference paper with 39 provided PDFs: **six files
were each attributed to 2–4 references, and every one was reported as
`identity confirmed`.**

```
li-2023.pdf → [75] nce-2023   7/16 tokens   İnce O… prediction of treatment response after TACE
              [76] li-2023   19/19 tokens   Li J… nomogram for hepatocellular carcinoma   ← the real one
              [78] ma-2023    7/12 tokens   Ma J… response to lenvatinib combined with TACE
              [81] ren-2023   7/14 tokens   Ren H… local tumor progression after microwave ablation
```

Every claim citing the wrong ones would have been judged against a different
paper, and shown as a correctly red-boxed crop of it.

Attributing provided files is a **matching** — one file to at most one
reference — and it was solved as one independent decision per reference, so
nothing could ever say *"this file is already [76], so it is not [75]."* Two
lossy steps then made collisions the norm rather than the exception: `_slug`
strips non-ASCII, so `İnce` becomes `nce`, and `_named_for` keeps only slug
tokens over three characters, so `nce-2023`, `ma-2023` and `ren-2023` all reduce
to the key `{"2023"}` — matched by every 2023 filename. The title check that was
meant to catch this passed all of them, because a bag-of-words containment ratio
has no precision left on a bibliography whose references are uniformly about one
subject.

**A file now answers for one reference only.** Ownership rests on an exact
`<slug>.pdf` stem — your own assertion — or on the file's own DOI or unique
title, and a file owned elsewhere is not a candidate however well its name
matches. Token containment proposes; it no longer decides. The gap names the
owner rather than reporting a bare failure:

```
[75] … Set aside: 14 files matched its name and every one belongs elsewhere
     (li-2023.pdf → [76], goto-2023.pdf → [12], sato-2023.pdf → [80], and 11 more)
     — a file answers for one reference only
```

Measured on that corpus: 39/39 files attributed, one-to-one, all six
misattributions gone, no file lost. `identify_by_content` needed no change — it
was already correct and was simply never consulted, because every
token-matchable file was withheld from it.

Not done here, deliberately: `_slug` still strips non-ASCII (`Müller`→`mller`,
`Gençtürk`→`gentrk`), so a user's `ince-2023.pdf` cannot match slug `nce-2023`.
That is a real bug, it is independent of this one, and fixing it renames
`ingest/<slug>/` and every report source id — so it gets its own change.

### Added — the first author and year are reported beside an attribution

A bibliography always prints at least a first author and a year, so the manifest
now says whether they agree with the file it accepted:

```
[1] matched yanagawa-2023.pdf … identity confirmed: 14/14 reference tokens on its
    first page · first author Yanagawa matches the file and the year 2023 appears
    on its first page
```

**It gates nothing, and that is measured rather than cautious.** Requiring the
surname on the first page would have vetoed 7 of the 8 misattributions above —
but it also refuses `yin-2024.pdf`, the *correct* source for its reference,
because journal PDFs glue affiliation superscripts to surnames and `Yin1` has no
word boundary before the digit. And it still cannot separate the two different
Zhang 2024 papers in that same bibliography. A false gap is no better than a
false verdict, so this informs the reader instead of deciding.

Three answers, not two: `unknown` prints nothing, because 10 of those 39 files
carry no `/Author` metadata and silence about identity is not evidence against
it. Across all 39 attributions: 29 agree, 10 unknown, **0 false disagreements**.

Surnames fold rather than vanish here (`İnce`→`ince`, `Müller`→`muller`) — the
first use of `unicodedata` in the codebase — and a leading run of initials is
skipped, so `F.P. Rivara` reads `rivara` and not `fp`.

### Fixed — the uncited register was one paragraph, not a list

`report.md` is rendered with `trim_blocks=True`, which strips the newline after
**any** block tag. The uncited-assertion line ended in `{% endif %}`, so every
item was emitted with no line ending:

```
- **[U1]** … *(Abstract, Background)*- **[U2]** Although MRI is highly sensitive…
```

Nine assertions on a real paper rendered as **one** list item. Both HTML looks
were correct throughout — `<li>` does not depend on newlines — so only
`report.md` was affected. Fixed with Jinja2's `+%}`, which the template already
uses on the two lines where this had been noticed.

The committed demo has exactly one uncited assertion, so a single-item fixture
had been testing the separator between items vacuously for as long as the
section has existed.

## [0.5.0] — shipped in 0.6.0

Never published on its own, so there is no `v0.5.0` tag. 0.4.1 was not
published either, so its entries below ship together with these — all of it
went out in 0.6.0.

### Added — `papertrace --version`

The first thing anyone types after installing, and it answered *"No such
option: --version"*. Found by installing this branch from GitHub into a clean
virtualenv and typing it. The only way to check was
`python -c "import papertrace; print(papertrace.__version__)"`, which nobody
guesses — so a user who had just installed from a branch had no way to confirm
which one they were running.

`--version` / `-V`, eager so it answers before the callback body runs: a bare
`papertrace` on a terminal opens the guided wizard, and a version flag resolved
after that would have interviewed the user about their manuscript before
telling them the number. It reads `papertrace.__version__`, the one home
`docs/RELEASING.md` names, rather than restating it where it could drift.

### Changed — extraction is told where the citations are ⚠️ **`coverage/3`**

The old flow discarded the location and then worked to reconstruct it. The
model returned a paraphrase plus a free-text `location` ("Methods ¶2"), and
Python guessed which of several `[3]` markers that paraphrase had come from:
normalise both sides, score with `SequenceMatcher`, accept only on
`ratio ≥ 0.45` **and** `margin ≥ 0.10`, assign globally best-first, and report
everything it could not decide as `uncertain`. The counts were right and the
*pointer* could be wrong.

The inventory it was matching against had been there all along — built
deterministically from `source_map.json`, just *after* the model call instead
of before it.

- **The inventory goes into the prompt.** `_render_inventory()` renders each
  citation occurrence as `ctx_NNNN` with its page, section, labels and
  sentence; `EXTRACT_PROMPT` asks the model to work through that list and
  return, per claim, the ids it was taken from. One sentence citing [2] and [3]
  is **one** claim carrying **both** ids.
- **Attribution becomes a set lookup.** An occurrence is covered when some
  claim's `ctx_ids` names it. `results.json` gains `ctx_ids` per claim and the
  audit is `"schema": "coverage/3"`; `coverage/2` files still validate, and
  `labels_in_text`/`covered`/`missing` keep their label-level meaning byte for
  byte, because `evals/align.py` reads `missing` to apportion blame.
- **Six symbols deleted** — `_attribute_label`, `_normalize_for_match`,
  `_ratio`, `_location_matches`, `OCCURRENCE_MIN_RATIO`,
  `OCCURRENCE_MIN_MARGIN` — and the `unicodedata`/`SequenceMatcher` imports
  with them. **This is not a net line saving and should not be sold as one:**
  `check.py` loses 110 lines and gains 115, roughly a third of the new ones
  being prompt text and comments. What goes is a *mechanism* — a scoring
  function, two tuned thresholds and a global assignment pass — replaced by a
  dictionary lookup. The audit no longer
  publishes `min_ratio`/`min_margin` because there is nothing to tune. The
  deliberate ~10-line duplication with `evals/align.py` is gone too — the
  reason it existed (papertrace cannot import `evals`, `evals` must not import
  a matcher from the thing it grades) no longer applies, since there is no
  matcher on this side.
- **A `ctx` the inventory does not contain is dropped, never repaired.**
  A hallucinated `ctx_9999` and an honest `"ctx": []` carry the same amount of
  information about which sentence was meant, and both are treated as such.
  Falling back to "the first occurrence of that label" would manufacture
  exactly the confident wrong pointer this removes.
- **`uncertain` survives, with one cause instead of several.** A claim cites a
  label and names none of that label's contexts ⇒ a claim reached one of those
  places and nothing can say which, so they are `uncertain` and counted as
  **not** covered. Previously it also absorbed close calls the matcher refused;
  that category no longer exists.
- The report's attribution self-caveat is correspondingly shorter, and its
  token changes: attribution is no longer "a text match that can be wrong" but
  "the context the extractor named" — still a model step, so still capable of
  naming the wrong place, and the report keeps saying so.

**Also unmeasured**, per ADR 0001. The argument for it is structural — it
deletes a guess and a whole class of silent wrong pointer — not a score.

### Changed — the committed demo report is regenerated, and its judge is pinned

`examples/demo/output/` is the only committed output and the artefact the README
links as *"See a completed report"*. It was produced on 2026-08-30 by 0.4.1, so
it showed none of what this release changed.

- **Regenerated under 0.5.0**, and the demo command now pins
  `--model claude-opus-5`. Without it `claude -p` takes the account default,
  which had silently moved from opus to haiku between two regenerations — so
  the committed showcase's judge depended on the day it was rebuilt.
- **The pinned expectation moves to `1 supported · 2 contradicted · 1 not
  retrieved · 1 uncited assertion`, over 4 claims rather than 5.** All four
  planted defects are still found; what changed is that the sentence citing
  both [2] and [3] now arrives as **one multi-source claim** instead of two
  single-source ones, because extraction is asked for the verbatim sentence.
  **Reproduced on `claude-opus-5` and `claude-haiku-4-5` alike**, so it is the
  prompt and not the model — which is worth stating, because the first
  regeneration changed both at once and the cause was ambiguous until the
  second run isolated it.
- Two README claims corrected as a consequence: the counts, and the line
  asserting that no claim in the demo cites more than one reference. That is
  now false, and the demo consequently exercises the per-source breakdown and
  the new `most adverse of 2 cited sources` qualifier — which the old one
  never did.

### Changed — cited sources are read with the layout backend ⚠️ **breaking**

`check.py` hard-coded `backend="pymupdf"` for every cited source, and said why:
*"Layout fidelity (tables/figures) is spent on the audited paper, not its
sources."* That had the asymmetry backwards. The manuscript's claim is the
question; the **source** is the evidence — and the evidence for a subgroup
claim is usually a table row. Read flat, the row is gone.

- **Sources now get the same backend as the paper.** `check` gains
  `--backend`, `run` forwards its own, and `check_claims` takes it as a
  **required** keyword — no default, like `_clip`'s truncation accumulator in
  the same module and for the same reason. Neither possible default is honest:
  `auto` drags docling into an offline test run, `pymupdf` silently downgrades
  a caller who asked for layout.
- **`docling` moves from an extra to a base dependency.** It cannot be optional
  once the sources depend on it. The `[docling]` and `[full]` extras are kept as
  aliases so 0.4.x install commands still resolve. **Measured in a clean
  virtualenv: 1.4 GB installed** (torch 591 MB, then opencv, transformers,
  scipy), plus the ~500 MB layout-model download on first *use*. That number is
  in the README install table rather than left as "pulls torch", because it is
  the kind of cost a user should meet before typing the command and not after.
  `--backend pymupdf` remains the escape hatch.
- **CI installs it and never runs it.** The models download on use, not on
  install, and every test pins `backend="pymupdf"` — which the required
  argument now makes impossible to forget. The suite stays offline and no
  slower: measured back to back on one machine, 632 tests in 22.6 s before this
  change and 645 tests in 17.1 s after. `import docling` is itself only ~0.2 s,
  because it does not pull torch until something converts a PDF.

**Two defects this would otherwise have introduced, both found by looking:**

- **`_stale_ingest` compared only the PDF hash**, so re-running an existing
  case folder would have reused its 0.4.x **pymupdf** source maps while the run
  reported layout-aware source ingest — a silent wrong-fidelity judgement,
  which is the exact failure class this project exists to refuse. It now
  compares the recorded `converter` too, resolving `auto` and ignoring
  docling's version suffix through a shared `ingest.resolve_backend()`.
- **The reports never said how the sources were read.** `RunResults.converter`
  is the *manuscript's*, and the only mention of the sources was one dim line
  in the terminal — the markdown and both HTML looks said nothing. Each
  source's converter now travels in `RunResults.source_converters`, and any
  source read as flat text is **named by slug** in all three formats. An empty
  dict means the run never recorded it (every 0.4.x file) and is deliberately
  not read as "all of them were flat".

**Measured cost**, since this is a real slowdown and not an unpriced one: on
this machine the first docling ingest in a process costs ~41 s (loading the
layout models) and each subsequent source ~3 s. Under `papertrace run` the
models are already loaded from the manuscript, so a 20-source paper pays
roughly a minute more in total; `papertrace check` on its own pays the load
once. `--backend pymupdf` remains a deliberate choice for a constrained
machine, and now says so per source in the report instead of being the
unstated default.

A source-ingest failure — docling can run out of memory or fail to fetch its
models, which flat text never could — unchecks that one source with the reason
in its note, and is never laundered into `not_retrieved`.

### Changed — the judge reads the paper's own sentence, not a summary of it

`EXTRACT_PROMPT` asked for each claim "tightly paraphrased, ≤160 chars", and
`CHECK_PROMPT` was handed `{id, claim, location}`. So the population, the
effect size, the confidence interval and the hedging — the things that actually
decide whether a citation supports a statement — had to survive a compression
the judge could not undo. *"Mortality fell by 12% in the subgroup over 65 (HR
0.88, 95% CI 0.79-0.98)"* and *"mortality fell by 12%"* are different claims,
and only one of them is checkable.

- **Extraction returns a verbatim `quote`** — the manuscript's own sentence,
  ≤500 chars — alongside the paraphrase, whose cap rises to 300. The paraphrase
  stays because it is what a report headline reads well; the quote is what gets
  judged, and `CHECK_PROMPT` says so explicitly.
- **The quote appears in the report** above each verdict, in all three formats,
  so what was judged is visible rather than taken on trust.
- **A claim judged without one says so.** An empty quote means the model did
  not return a sentence, so the verdict rests on the paraphrase — weaker
  evidence, and now a warn-level disclosure in every format rather than
  something a reader has to infer from a missing blockquote. It fires only
  where a judgement actually happened: nothing read an unretrieved source, so
  the notice would otherwise land on every row of the gap register.
- **The quote is never back-filled from the paraphrase.** That would reinstate
  the exact compression this change removes while looking like it had been
  fixed.
- Coverage attribution briefly took its similarity ratio on the quote rather
  than the paraphrase, which was a free improvement to the matcher — and then
  the matcher was deleted outright by the change below. Nothing of it remains;
  the note is kept only so the two entries do not appear to contradict each
  other.
- `results.json` gains `quote` on both cited claims and the uncited register;
  `schemas/results.schema.json` is updated and `from_json` still loads a 0.4.x
  file, where the field is simply absent.

**One consequence for `evals/`:** `prompt_fingerprint()` is a content hash of
the prompts, so this invalidates comparison against any pre-0.5.0 run.
`agreement.py` already refuses to compare runs that do not share the
`(set_id, prompt fingerprint, converter)` triple — that is the correct
behaviour, not a regression. And per ADR 0001 there is no benchmark to
compare against anyway: **this change is unmeasured.** It removes a known
information loss; that is not the same as evidence that verdicts improved.

### Changed — `report.md` by default; the HTML looks on request ⚠️ **breaking**

Every run wrote three report files and a ~1 MB font bundle, whether or not
anyone wanted three. `report.md` is what almost every run is read through; the
editor and terminal looks exist for sharing and for screenshots.

- **`papertrace run` and `papertrace report` now write `report.md` alone.**
  Add `--format editor`, `--format terminal`, or both — `-f` for short, and
  repeatable. The fonts are copied only when an HTML look is actually written.
- **`report.md` is always written**, whatever `--format` says. It is the record
  of the audit, not one presentation of it among three; a request for only a
  screenshot look must not leave the case folder without the report itself.
- **`--png` pulls in the HTML it screenshots.** `--png --format md` cannot mean
  "photograph a file I told you not to write", so the HTML looks are rendered
  regardless. Honouring it literally would have produced no PNG and said
  nothing about why.
- **A mistyped format is refused** — `unknown --format pdf — expected any of
  md, editor, terminal`, exit 2, checked before `results.json` is even loaded
  so a bad flag cannot half-write a report folder. Silently ignoring it would
  answer `--format pdf` with a folder containing no PDF and no complaint, which
  is the same shape as the unknown-backend bug `ingest_pdf` already refuses.
- `write_reports()` itself still defaults to every format. It is the seam the
  disclosure-parity suite drives, and that suite has to render all three or it
  stops comparing anything; the narrower default belongs to the CLI, where the
  user's intent actually is.

**To restore the old behaviour:** `papertrace run paper.pdf -f editor -f
terminal`.

### Fixed — a wizard-driven audit would have crashed at the report stage

Found while adding `--format`, and the third appearance of a bug class this
codebase has now met three times. `run_wizard()` calls `cli.run` as a plain
Python function, and Typer's declared defaults are `OptionInfo` sentinels
rather than the values `--help` displays — so the new parameter the wizard did
not name would have arrived as a sentinel, reached `write_reports`, and raised
on not being iterable. After every paid model call had already been made.

- `report` is now split into the Typer command and `_report_pipeline()`, which
  is keyword-only with ordinary Python defaults — the same treatment `ingest`
  and `refs` already had, and for the same reason. `run()` calls the pipeline
  function.
- The wizard now names **every** parameter `run` declares, and a new test
  asserts that against `inspect.signature(cli.run)` rather than against a list
  of names — so the next parameter added to `run` is caught without anyone
  remembering to come back and update the test.

### Changed — the headline no longer reads as a verdict on the whole claim

`❌ CONTRADICTED` is one source's verdict. On a claim citing four references it
reads as a statement about the claim, and a compound sentence may legitimately
draw different parts from different references — so one dissenting source of
four overstates by exactly the amount the status line cannot show.

- **A multi-source headline now names what it ranged over**: *"❌ CONTRADICTED
  — most adverse of 4 cited sources"*, in all three report formats. The rule
  itself is unchanged and deliberately so: the most adverse verdict is the
  right triage signal, and one dissenter must never be averaged away. What
  changes is that it stops being stated unqualified.
- **Single-source claims are not qualified**, and neither is a claim with no
  judgements. With one source the headline *is* the claim's verdict, and
  "most adverse of 1" would be noise that teaches readers to skip the line; a
  `not_retrieved` claim ranked nothing at all, so naming a comparison that
  never happened would be its own small invention.
- No new verdict value, no schema change. A `disputed`/`mixed` state was
  considered and declined: it would have meant a `VERDICTS` entry, a schema
  update, a gold-verdict enum change and six render sites, to express something
  the existing per-source breakdown already shows.

**The limitation this leaves, stated rather than glossed:** the run's summary
counts and `results.json` still tally each claim once, under its headline. A
claim splitting 2 support / 1 partial / 1 contradict appears in the
`contradicted` total and nowhere else. That total means *"claims with at least
one contradicting source"*, not *"claims that are wrong"*, and the README's
does-not list now says so. Fixing the totals properly needs the per-source
population counted separately, which is a larger change than this one.

### Decided against — two proposals declined in writing, with reasons on file

A full-stack review raised seven items. Five became changes; two are declined,
and `docs/adr/` now exists to record why so that a future review does not
re-derive them. Choosing not to build something is user-visible scope, which
is why it is here and not only in a commit message.

- **No gold benchmark, and therefore still no accuracy figure**
  ([ADR 0001](docs/adr/0001-no-gold-benchmark.md)). The evaluation harness is
  not the thing that was missing: `evals/` already holds ten modules, 22 metric
  functions, a JSON-schema'd gold contract and twelve CI-green test modules,
  and `evals/PROPOSAL.md` already specifies the ≥40-case paired set down to its
  acceptance criteria. What is missing is data, and one precondition for it —
  `evals/DESIGN.md` requires ≥ 2 labellers who did not write the prompts.
  There is one maintainer, who wrote them. Building the set self-labelled would
  produce a number the harness itself prints a conflict-of-interest caveat
  against, and a number nobody may cite is worse than no number, because the
  number gets cited. `evals/PROPOSAL.md` is kept, with its status updated: it
  is the plan if that precondition ever changes.

  The consequence is stated rather than glossed: the other changes in this
  release **ship unmeasured**. They remove mechanisms that could only degrade
  judgment quality; that is not the same as evidence it improved, and the two
  are not blurred anywhere in this file or the README.

- **No GROBID** ([ADR 0002](docs/adr/0002-no-grobid.md)). The reference
  parsing and reconciliation really is ~707 contiguous lines of `refs.py`, but
  only ~261 of those are *parsing* a specialist parser would displace. The
  other ~470 — the Crossref deposit, corroboration and `reconcile` — exist
  because any reading of a reference list can be wrong and the tool must be
  able to say so, and they survive a parser swap: a parser cannot certify
  itself. Against that, GROBID wants Java, Docker and 2–4 GB of memory, and
  its own citation-context linking is 0.76–0.91 F1 — a probabilistic gain for
  a disqualifying deployment cost in a `pip install` tool. Not benchmarking it
  is part of the decision: a benchmark is only worth running if a favourable
  result would change the outcome. The roadmap item is removed rather than left
  implying a plan that does not exist.

  Superscript-citation support, which shares this surface and would fix three
  of seven papers with unconfirmed numbering, is unaffected and remains the
  higher-value work here.

## [0.4.1] — shipped in 0.6.0

Never published on its own, so there is no `v0.4.1` tag.

### Added — the reference list is now checked against what the paper cites

`parse_references` was the only stage in the pipeline with no way to report its
own failure. Every other stage has one — `not_retrieved`, `unchecked`, the
anchor tri-state, `unverifiable`, coverage `uncertain` — but the reference
parser always returned a confident list, and nothing ever compared it to
anything. The citation label is the **join key** between a claim and the source
it is judged against, so a list off by one does not produce a worse audit; it
produces a confident audit of the wrong papers. One live run misnumbered 27 of
41 references and said so nowhere.

- **Three-way reconciliation.** Two independent readings of the reference list
  are taken — the tool's parse of the printed text, and the list the publisher
  deposited with Crossref — and the manuscript's own `[N]` markers arbitrate
  between them. A reading is used only if it accounts for exactly the labels
  the body cites, which under citation-order numbering is a structural test
  rather than a heuristic: reference *N* is by definition the *N*th first-cited
  work. `refs` gains `--doi`, defaulting to the DOI printed on page 1.
- **Crossref is a candidate, not an oracle.** A short deposit is more dangerous
  than a bad parse because it looks authoritative: mapped onto `[1]`, `[2]` it
  would silently discard the rest. One record in the test spread carries 2
  references for a paper citing about 40, and the payload cannot reveal it —
  Crossref's `references-count` counts what was *deposited*, so it always equals
  the array length. The body's labels are the only thing that catches it.
- **A reference deposited as a bare DOI is kept, and a shortfall is named as
  this tool's.** Some publishers deposit references as a DOI and nothing else;
  those rendered to an empty string and were dropped, and the run then reported
  that the publisher had deposited a fraction of its own list — a false
  accusation, and a plausible-looking number in place of an admission. They are
  now kept and named after the DOI, which is the best case for retrieval: the
  DOI is already resolved, so the title search is skipped entirely. Where this
  tool still cannot render part of a deposit, the deposit is set aside rather
  than used to renumber, and the disclosure says whose limitation it is.
  Reference numbering is read from **array order**, never from the `key` field — keys are
  publisher-specific (`_b0005`, `_bib1`, `3400_CR1`, `bibr1-…`,
  `R10-45-20210317`), and two schemes turned up inside a single deposit.
- **The DOI is checked against the paper before its record is trusted.** The
  DOI is typed by hand or read off page 1, and the deposit is the one retrieval
  route that can replace the *entire* reference list — a companion paper, an
  erratum or an earlier version can carry exactly as many references as the body
  cites, so the count test passes and the run would print "numbering confirmed"
  over another paper's bibliography. The record's title is now compared with the
  paper's own, tri-state like every other title check here: a mismatch sets the
  deposit aside, and too little title to compare leaves the list in use with the
  identity disclosed as unconfirmed rather than assumed either way. The DOI used
  and where it came from are printed and recorded.
- **The paper's title comes from the paper, not from its layout.** Source maps
  record `declared_title`, the title the PDF states in its own metadata.
  Measured on the seven-paper spread, the first heading is the article-type
  banner whenever the layout heuristic was wrong — `CLINICAL GUIDELINE`,
  `RESEARCH ARTICLE`, `Journal Pre-proofs`, `Editorial` — while the metadata
  carried the exact title for six of the seven. Docling does not help here: on
  the seventh it emits no `title` item at all. A declaration that is not
  title-shaped (too few words, a producer's filename, a `Microsoft Word -`
  prefix) is passed over for the layout, because an author's PDF declares the
  name of the file it was exported from, and this tool's main case is an
  author's PDF. `scout` uses the same title to identify the paper, so its
  Europe PMC lookup stops searching for "RESEARCH ARTICLE".
- **A paper's bibliography identifies it when its title cannot.** Where the
  title comparison is unverifiable, the deposit is checked against the reference
  list printed in the paper: 38 of 41 deposited works appear in the printed list
  for the audited paper, against 0 of 41 for a different paper's list. Compared
  as a set, never positionally — the same pair scores 34% in order, because that
  paper's parse is the misnumbered one this feature exists to catch, so the
  numbering cannot be an input to the identity test. The asymmetry is
  deliberate: agreement is evidence of identity, disagreement is not evidence of
  difference, since two lists that disagree may be one paper read badly. Across
  the spread this settles all seven papers — six by title, one by bibliography,
  where before it settled three.
- **Failure is disclosed, not fatal.** When neither reading can be confirmed the
  audit continues, a run-level disclosure states that the numbering is
  unconfirmed, and every claim citing a doubtful label carries the caveat beside
  its verdict — in all three report formats. Where the two readings corroborate
  each other the doubt starts at their first divergence, so a list that is right
  for its first 30 entries is not tainted wholesale.
- **Three absences read differently.** No DOI, no deposit, and Crossref
  unreachable are three different facts asking the reader for three different
  things, and are never collapsed into one message.
- `RefManifest` gains `reference_source`, `numbering_verified`,
  `numbering_note` and `unverified_from`; all additive, and an older manifest
  still loads — as a parse whose numbering was never checked, which is what it
  is. `scripts/reference_audit.py` reports the three counts per PDF, offline of
  the model and free.

Measured on seven papers across four publishers: all seven deposit a reference
list, and the check catches both known parse failures (43 parsed vs 41 real;
106 parsed vs 101 real). Three of the seven cite by **superscript numeral**,
which flattens to indistinguishable prose when the PDF is converted to text —
those papers have no arbiter, and are reported as unconfirmed rather than
presented as checked.

### Fixed — eleven cited sources were downloading to one file

Found by a live run on a JAMA editorial while verifying the above, and worse
than the `TypeError` that revealed it. `_slug` took the *first* token of the
reference, stripped non-letters, and fell back to the literal `ref` when nothing
survived. `_parse_bulleted` leaves the printed list numeral at the front of the
reference text, so the first token was `1`, `2`, `3`… and **23 of 28 references
slugged `ref-2024`**. The slug is also the download's filename, so all eleven
retrieved sources wrote to one path, each overwriting the last — every claim
citing any of them would have been judged against whichever paper downloaded
last, with no error.

- `_slug` now takes the first token that actually contains letters.
- `_unique_slugs` guarantees no two entries in a manifest share a slug, applied
  to both producers. A genuine collision needs no parser bug — the same first
  author and year cited twice does it — so uniqueness is enforced rather than
  assumed to follow from a better slug. The first entry keeps the natural slug,
  so a `--provided` file named `<author>-<year>.pdf` still matches.
- `_parse_bulleted` strips the leading numeral, which also kept it out of the
  Crossref bibliographic search and the title check.

### Fixed — `--parse-only` and the offline test suite reached the network

`refs` is also called as a plain Python function, by `run` and by the tests, and
Typer's declared default for an option is an `OptionInfo` object rather than the
value the help screen shows. `OptionInfo` is truthy, so the new `doi or
detect_doi(...)` took it for a real DOI and built a request URL out of its repr.
The offline test suite began making live Crossref calls — and passed, because
the machine running it had network. Same shape as the bug that made `ingest`'s
backend an `OptionInfo` and read every paper as flat text while reporting
layout-aware ingest.

### Fixed — the scout's wrong-paper warning stopped firing when the DOI became a guess

`_resolve_paper` records `via: doi` whenever a DOI is supplied, and the console
warned "wrong paper? pass --doi" only on `via: title` — so when `run` began
reading the DOI off page 1 and handing it down, a funder, data-availability or
erratum DOI could anchor the whole literature scan to somebody else's paper
*and* suppress the only signal that it had. The provenance is not recoverable
inside `scout`, and it is the wrong question: the record's own title is
comparable with the paper's.

`ScoutResults` gains `identity` — `confirmed` / `unverified` / `mismatch`,
additive, and `""` on an older `scout.json` means not recorded rather than
confirmed. A mismatch stops the scan and says so instead of filling both
registers from another paper, since the registers *are* the finding. Too little
title to compare leaves the scan in place and discloses the unknown, the same
tri-state used for a deposit and for a downloaded source. The title comparison
itself moved to `models.titles_match`: three readers now need it, and a copy in
`scout` is the defect the other shared rules in that module exist to prevent.

### Fixed — a `--provided` file could be judged as two different references

`_unique_slugs` renames the second of two colliding entries to
`smith-2019-r7`, and `_provided_candidates` drops slug tokens of three
characters or fewer — so `r7`, the only thing telling the two apart, was
invisible and `sources/smith-2019.pdf` matched both. Measured: entry [7] came
back `status=provided`, `title_check=mismatch`, pointing at entry [2]'s paper,
and its claims would have been judged against it. Worse than before slugs were
made unique, when both entries shared a slug and were grouped into one source.

"Disclosed, not fatal" still holds for a file the user *named* for a reference —
they chose it, there is nothing to fall back to, and a scanned PDF yields no
text to check. It does not hold for a file a token match found: nobody chose it
for that reference, so a title check that says "different paper" is now a reason
to keep looking. Candidates are read in rank order until one is usable, and
where the retrieval chain then finds nothing, the reason names the file that was
set aside and why — a gap that withholds what the tool already knows is the
failure this project exists to avoid.

### Fixed — the coverage audit had its own idea of where the bibliography begins

`coverage_audit` cut the body at `^##\s+(references|bibliography|literature)`,
a second boundary rule beside `models.is_references_heading` — which carries a
comment saying two readers need one rule because two is a defect this project
already shipped. The regex needs ingest to have *typed* the block as a heading,
and flat-text ingest guesses headings from font size, so a `References` line at
body size reaches `clean.md` with no `##`. Reproduced on a generated paper:
`labels_in_text` came back `['1','2','3']` where `[3]` appears only inside the
reference list, so the audit reported a gap that does not exist — in the one
figure it computes mechanically so that it cannot. Both the label reading and
the `clean.md` occurrence fallback now cut on the shared rule.

### Added — `init --for <paper>` names the case folder the way `run` would

`init` then `run paper.pdf` used to orphan `case/sources/`: `run`/`refs` name
their own folder after the paper, so a hand-made `./case/` is only reused if
`-c case` is remembered every time. `init --for paper.pdf` now names the
folder exactly as `default_case` would, so a plain follow-up
`papertrace run paper.pdf` finds it automatically. An explicit folder name
still wins over `--for`; omitting `--for` keeps the previous `./case/`
default and its `-c` reminder.

### Fixed — the judging call ran with the wrong repo's rules and a full toolset

`_ask`, the only seam that calls a model, passed no `cwd` to `claude -p` and no
tool restriction. Running an audit from inside a repo silently fed that repo's
own `CLAUDE.md` into every verdict, undisclosed anywhere in the report, and the
judge held the CLI's default toolset — Bash, Edit, WebFetch — while it is only
ever supposed to read the prompt it is given and answer. `_ask` now runs with
`--safe-mode`, `--tools ""` and `cwd` set to a private, per-process scratch
directory — not the shared, world-writable system temp root, which another
local user could otherwise plant config into.

### Fixed — the "no case folder" hint implied a search it never ran

`check`, `highlight`, `report` and `scout` take no manuscript path, so when
`-c` is omitted and no case folder is found, the hint had nothing to look
beside and only ever checked the current working directory — but it said "no
case folder found here," which reads as an exhaustive search. Reworded to "no
case folder found in the current directory," naming the one thing that was
actually checked.

### Fixed — a table's own numbers were read as citations

`_LABEL_GROUP` matches `[N]` and `[N, M]` alike, and a results table's 95% CI
column is written exactly that way — `[100, 100]`, `[51, 85]`. Reproduced on a
real radiology paper: two table blocks holding CI columns supplied every
square-bracket match in the manuscript, none from prose, and pushed the highest
cited label the reconciler saw from the paper's real count to 100 — a confident,
wrong numbering read for a paper whose actual in-text citation style
(round-bracket numeric) this tool does not yet recognise at all, so the honest
answer was "unconfirmable," not "[1]-[100]." `_body_citation_labels`,
`citation_occurrences`, and `citation_labels_in_text` now skip table content —
by block type where a source map is available, by each row's own GFM `| ... |`
shape in the `clean.md` fallback, since flat text carries no block type.

### Fixed — the numbering banner and the per-claim caveat contradicted each other

When the doubt could not be narrowed, the run-level disclosure rendered "every
entry is affected" while `label_is_doubtful` returned False for every label for
the same reason — `unverified_from is None`. The report asserted that every
entry was suspect and marked no claim suspect, so a reader acting on a single
verdict was told nothing. An unconfirmed numbering with no recorded scope now
puts every label in doubt. Two shapes reach that state: a manifest written
before the list was reconciled at all, and two readings that agree entry for
entry with no arbiter to confirm either — the superscript-citation case, which
is about half of real papers, so those reports now carry the caveat on every
claim rather than on none.

### Fixed — a reused source directory could hold a different paper

`check` re-ingests a cited source only when `annotated.md` is missing, and the
directory it reuses is named after the reference's slug. A slug is not an
identity that holds still: fixing a slug collision renames one of the two
colliding entries, and the reconciler can hand `refs` the publisher's list on
one run and the parsed list on the next. Re-running an existing case could
therefore hand the model the directory's previous occupant and judge a claim,
confidently, against a different paper. `SourceMap.doc` could not catch it —
every cited source is stored as `<slug>.pdf`, so it reads the same either way.

Source maps now record `source_sha256`, the hash of the bytes they were built
from, and a directory whose hash does not match the file now at `pdf_path` is
re-ingested. An unhashed map — written before this — counts as stale:
re-ingesting is local, free and quick, while trusting it is a guess about which
paper is in a file. The field is additive and older maps still load, where
absent means unknown and never "matches".

### Fixed — the tool could invent a reference

Found by the first real audit: a 43-reference Elsevier paper was reported as
having 46, and the three extra "references" were the paper's own table
captions, published in the retrieval manifest as `paywalled` works with real
DOIs attached.

- **A resumed reference list must look like references.** `references_span`
  scanned to the end of the document for any run of blocks sharing the
  bibliography's block *type*, with no test on the text — so three `list`
  blocks under a `TABLE TITLES` heading became references 44–46. A candidate
  run now has to be at least half reference-shaped. Half rather than all,
  because a genuine continuation can carry a bare-URL entry with no year. The
  docstring claimed this was already the case; it was not.

- **A non-reference is never title-searched, and a component DOI is never
  accepted.** Crossref answered a title search for "Table 1. Dataset
  characteristics" with `10.7717/peerj.7892/table-1` — a *table* belonging to
  an unrelated paper — and nothing caught it, because the title sanity check
  only runs on the download path and no copy was ever downloaded. Entries that
  read as nothing citable are refused before the search, mirroring the existing
  web-page gate, and any DOI naming a table, figure or supplement is rejected
  wherever it came from.

  `looks_like_reference` accepts a year, a DOI, an arXiv id **or an author
  list**. The author clause is not decoration: two real references in the same
  paper reached the resolver truncated mid-title with no year at all, and
  Crossref found both correct DOIs from the author string. A year-only test
  turned them into gaps.

- **The retrieval manifest keeps the evidence for a title check that passed.**
  `title_check: verified` and `title_check: unverifiable` both arrived as bare
  assurances; the detail was recorded only on mismatch. Accepted downloads now
  carry it too — `title check: 18/19 reference tokens on its first page`.

- **The scout says which failure it was.** With `--doi` supplied and no record
  found, it reported "paper not identified in Europe PMC — pass `--doi` to pin
  it", advising the operator to do what they had just done, and wrote
  `"doi": ""` into `scout.json` so the artifact could not show what was tried.
  A DOI that returns nothing means the paper is not indexed — usual for an
  in-press pre-proof, and a stronger fact than a failed title heuristic. Both
  registers being empty is absence of data, not a clean literature search.

### Fixed — the literature scout, and the escaping hole it uncovered

Found by a second live audit, of a pancreatic-cancer paper.

- **The keyword query is about the subject now.** `_keywords` took the first
  four content words of the title, so *"Image registration improves inter-reader
  agreement of objective response in CT assessment of pancreas adenocarcinoma"*
  searched for `image AND registration AND improves AND inter-reader` — a method
  phrase containing a verb, never reaching the disease. It matched a stroke
  conference abstract on the word IMPROVES. Words are now ranked by length as a
  proxy for topical specificity rather than by position, and a short list of
  words that state what a paper *claims* rather than what it is *about*
  (`improves`, `reduces`, `assessment`, …) joins the stop list. The same title
  now yields `adenocarcinoma AND registration AND inter-reader AND agreement`.

- **A paper from the manuscript's own year is no longer "existed but uncited".**
  That register invites the reader to ask what the authors missed, and a
  same-year paper may have appeared after submission — on the audited paper all
  fifteen candidates were from its own year. `same_year` is a third register,
  rendered apart and labelled, because folding it into either neighbour states
  something false and dropping it would lose a finding a reviewer might
  legitimately raise. Additive in `schemas/scout.schema.json`; an older
  `scout.json` still loads.

- **Europe PMC's escaped markup is decoded.** Titles arrived as
  `CTV&lt;sub&gt;boost&lt;/sub&gt;` and were rendered verbatim.

- **The HTML reports actually escape their interpolations.** `report.py` passed
  `select_autoescape(["html"])`, which matches a name ending in `.html` — the
  templates are `report_editor.html.j2` and `report_terminal.html.j2`, so
  nothing ever matched and **autoescape was off for all three formats**. It
  stayed invisible because the one field carrying angle brackets, a Europe PMC
  title, arrived pre-escaped from the API; decoding those entities above is what
  made it reachable. Cited source PDFs are downloaded from third parties and
  their text reaches the report, so this was not hypothetical. Matched on
  `.html.j2` now. Markdown is not HTML and is left verbatim.

### Changed

- **A substantive verdict must now name a page and a block the source actually
  has, and must be showable.** `check` validates every `supported`, `partial`
  and `contradicted` judgement against the cited source's own
  `source_map.json`: the page must exist, `source_block` is now **required**,
  and it must sit on the page the verdict names. Anything else is
  `unchecked` with a note, never a verdict. `highlight` enforces the same rule
  against reality — a substantive judgement that produced no evidence image is
  downgraded there too, because the PDF can be missing from
  `sources_resolved/` and a source map can disagree with the PDF it came from.

  The block requirement is what makes the picture unconditional: the crop
  region is the block's bbox, so a valid block always yields an image and the
  anchor phrases only decide whether a red box is drawn on it. `CHECK_PROMPT`
  already asked for `source_block` and already told the model to omit it only
  for `not_addressed`, so no prompt text changed and eval runs stay comparable
  across this release.

  **This changes counts.** A run that previously reported a verdict resting on
  page-only provenance, an impossible page or a nonexistent block now reports a
  gap. `not_addressed` is unaffected — it never claimed a passage.

- **A source with no `source_map.json` can no longer produce a verdict.** Its
  judgements are `unchecked`, with a note naming the re-ingest that fixes it.
  Previously the location it named could not be checked against anything.

### Fixed

- **Two ways around the one-case-one-paper guard.** `papertrace ingest` never
  consulted `_guard_case`, so a different paper could overwrite
  `<case>/ingest/manuscript` — the slot `refs` fills and the coverage audit
  reads — while the manifest still described the first paper. The guard now
  runs whenever the output *is* that slot, recognised by shape so `--out`
  cannot walk in behind `-c`'s back; a cited source ingested into
  `<case>/ingest/<slug>` is untouched. And `refs --parse-only` on a pre-hash
  case re-ingested the manuscript slot and then returned before writing the
  manifest; an inspection command now reads the paper into a temporary
  directory and mutates nothing.

- **Claims whose headline is `not_retrieved` or `unchecked` now show their full
  per-source state.** The gap sections printed the claim text alone, so a claim
  citing [1,2] where source 1's check failed and source 2 was never obtainable
  said neither thing, and a `not_addressed` from a source that *was* read
  vanished behind the `unchecked` headline that outranks it. All three formats
  now render the co-citation breakdown, the unretrieved co-citations and one
  row per judgement with its note. The editor look also labelled a whole
  section row with `items[0].verdict`, calling a mixed section whichever
  verdict came first; it is one row per claim now.

- **The anchor tri-state is no longer flattened.** `anchor_located` is `True`
  (searched and located), `False` (searched, not located) or `None` (never
  searched) — three facts. The disclosure was gated on `evidence_image`, so a
  verdict with a page and no crop disclosed nothing; it is gated on provenance
  now, with wording that does not describe a picture that was not written. The
  `highlight` console branched on truthiness and described `None` as "no anchor
  phrase found on the page", asserting a search that never happened.

### Evaluation harness

Developer tooling; none of this affects an ordinary audit.

- Gold-case eligibility is decided **before** alignment, not after. An
  unresolved or drift-invalidated case used to compete for predictions and
  consume the one an eligible case needed — which then reported as the tool's
  extraction gap, moving blame off the tool silently.
- Cases that were never eligible no longer vote in repeated-run agreement.
- Duplicate prediction ids are refused with an error naming them, instead of a
  dict comprehension keeping whichever came last — the one place alignment's
  documented order-independence did not hold.
- Repeated-run agreement enforces the whole **(`set_id`, prompt fingerprint,
  ingest converter)** triple. The error message already claimed the triple
  while only `set_id` was checked.
- The two agreement figures are renamed for what they are: **penalized**
  (a genuine lower bound) and **complete-case** (a different population, not a
  bound in either direction). `intersection` was labelled the upper bound,
  which is false — dropping a case whose true agreement is high pulls the mean
  down.
- `not_addressed` is a rendered confusion-matrix **column**, not only a row.
  The arithmetic always had four classes; the table printed three, so a
  mistake was counted and then hidden.
- `evals/DESIGN.md` describes all four judgement classes.

### Documentation

`README.md` corrections, each a statement that did not match the code: page
provenance is not universal (`not_addressed` has none by design) and is now
page *and* block; an unboxed crop needs a valid block to exist at all;
`not_addressed` is deliberately unranked in the headline rule; the default case
folder is the paper's stem, not `case/`; text drawn inside a raster figure has
no text layer to box; and both Quick Starts need `git clone` because PaperTrace
is not on PyPI.

## [0.4.0] — 2026-08-30 (beta)

### Added

- **A resumed reference list says so, in all three formats.** Crossing a section
  boundary to finish a bibliography is a judgement, and the numbering of the
  later entries rests on it — so `RefManifest.references_resumed` records it
  (optional, in `schemas/refs_manifest.schema.json`) and a run-level disclosure
  carries it into markdown, editor and terminal; `refs` also says so on the
  console in amber, so the crossed boundary is visible before the report is
  read. The wording points both ways on
  purpose: the guess could be wrong, but not making it was the previous
  behaviour and that failed silently. The terminal template needed an explicit
  branch, and the mechanical guard added in 0.4.0 caught the omission before any
  parity test did — its first live catch.

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


- **The ingest backend is stated where it can be seen.** It was printed once,
  during `ingest`, above that wall of noise and never repeated across a
  four-minute run — and `papertrace report` alone printed no provenance at all.
  `report` now names the backend that read the manuscript, `run` repeats it on
  its closing line, and both say that **cited sources are always read as flat
  text** — deliberate behaviour (`check.py` ingests sources with
  `backend="pymupdf"`) that no reader could infer from a line naming docling.
  The `/review` skill now names the backend unconditionally rather than only
  when it is bad news.


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

- **The README claimed a figure's contents get checked, which it could not
  support.** "A claim that lives in a table cell **or inside a figure** is found,
  checked, and shown like any other" was true of the red box — a figure's numbers
  are in the PDF text layer, so text search finds them on the real page — and
  unsupported for the judging half: under the layout backend a figure region
  reaches the model as `[FIGURE: <caption>]`, and in-figure text arrives only
  where docling's layout model found a text region inside the figure. On the one
  paper measured it found none: of 9 figures, 5 carried text in the text layer
  and no docling text block landed inside any figure region, while the flat-text
  backend did carry that text. The section now names which backend each half
  holds for, states the measurement as one paper rather than a rate, and says the
  two illustrating crops come from **cited sources** — which batch mode always
  reads as flat text. The same section's claim that a flat-text source delivers
  "its figures not at all" was wrong in the other direction and now says what it
  does deliver: loose words with no figure attached.

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
  real result and now has a name. It is appended to `JUDGMENT_VERDICTS`, which keeps
  the judgement group's own order stable but **does shift the concatenated
  `VERDICTS` tuple**: `not_retrieved` moves from index 3 to 4 and `unchecked`
  from 4 to 5. Nothing in this repository indexes `VERDICTS` positionally, and
  `counts()` gains a key without reordering one — but an external consumer that
  does index it will read the wrong name, so this is a breaking change for
  anyone who does. It is also the one verdict with no `source_page`, since
  there is no passage to point at — demanding one would force the model to cite
  an absence.
- **`examples/demo/output/` regenerated** against the current pipeline
  (2026-08-30, docling 2.118.1). The committed report predated the disclosure
  work and showed none of it: it named the checker only as `Claude`, reported
  `all 4 citation labels covered`, and carried neither the converter stamp nor
  the attribution caveat. It now reads `5/5 citation occurrences ... across 4
  labels`, identifies the judging model, and renders through the per-source
  judgement path. Same verdicts as documented: 2 supported, 2 contradicted,
  1 not retrieved, 1 uncited assertion — the evidence crop for claim 4 moved
  from page 1 to page 2, which is anchor-location variation between runs, not a
  different verdict. No demo claim is co-cited, so the per-source summary count
  still has no committed example; the README says so rather than leaving it to
  be assumed.

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
  tests from model evaluation. Newly documented: Claude proposes page/block/anchor
  phrases while Python locates them and draws the boxes; the model reads
  extracted text with page markers, not page images; the coverage audit reads
  bracketed numeric citations only. The single real-run example is framed as one
  illustrative run, not a measurement.
- Coverage wording throughout now says *labels reached by an extracted claim*
  rather than "covered" — the audit measures extraction reach, not that a source
  was read.
- `jsonschema` added to the `dev` extra; `testpaths` now includes `evals/tests`.

### Fixed

- **A URL-only reference was resolved to an unrelated paper.** Reference [8] of a
  real audited manuscript is an ACR news page with no DOI. With no DOI to look
  up, `resolve_entry` fell through to a Crossref *bibliographic title search* —
  which always returns something — and that something was `10.1002/acr2.11538`:
  ACR Open Rheumatology, American College of *Rheumatology*, not Radiology.
  Unpaywall served Solomon et al.'s editorial on authorship and ChatGPT, the
  title check passed it at 6/15, and two claims were reported `not_addressed`
  against a rheumatology editorial. Two of those six matches were `chatgpt` and
  `source`, harvested from the `?utm_source=chatgpt.com` tracking parameter in
  the reference's own URL — the tracking parameter is what made a ChatGPT
  editorial look like a title match. A reference whose identity is carried by a
  URL — no DOI, no volume, no page range, no identifier — now terminates at
  `no_doi` with no request sent: a news page was never retrievable as a PDF, so
  the honest gap costs nothing that was ever on offer. The gate keys on the
  absence of article structure rather than the presence of a link, because
  publishers' own reference styles print a URL beside the volume and those
  references resolve well. Separately the title check no longer takes tokens
  from a URL, and `verified` now needs four distinct matched words rather than a
  ratio a three-word reference clears on generic domain vocabulary — falling
  short reads `unverifiable`, never `mismatch`, since too few words to tell is
  not evidence of a different paper.
- **Every audit defaulted into one folder called `case`, in whatever directory the
  user was standing in.** A first-time user ran a batch audit from the root of a
  git clone and the output landed in `PaperTrace/case/` — not named for the paper,
  and the same folder every subsequent paper would have used. `run` and `refs` now
  default to a folder named after the paper, beside the paper: the one location
  stable across invocations, so a re-run finds its own case without a flag. An
  explicit `-c` still wins, unconditionally. `check`, `highlight`, `report` and
  `scout` have no paper to take a name from, so they are given no default at all —
  `./case` is still used when it exists, and otherwise they refuse, listing the
  folders in this directory that look like audits rather than picking one. Because
  a case folder is no longer named `case`, `.gitignore`'s name-based guardrail no
  longer covers it, so the folder is created carrying a `.gitignore` of its own.
- **Re-running a paper reused its case folder silently, including when that was not
  what the user meant.** Adding source PDFs and re-running is the intended flow, but
  so is auditing a revised draft, and nothing distinguished them: `_guard_case`
  refuses a *different* paper and permits the same one without a word. A derived
  case folder already holding an audit of this paper now asks — amend it
  (references are resolved again, so sources added since are picked up) or start a
  numbered sibling, leaving the first untouched. Only for a folder the tool named
  itself; `-c` is an instruction, not a suggestion. With nobody at a terminal it
  amends and says so, never blocking on stdin: amend is what a re-run did before,
  it deletes nothing, and it keeps the report's path predictable, where a fresh
  folder would move a scripted caller's output somewhere it never named. A
  different paper in the folder is still exit 2, unchanged.
- **The skills documented four commands that do not exist.** `papertrace refs …
  -o case/` (`refs` has no `-o`), `papertrace report case/` and `papertrace
  highlight case/ --claim <id>` (neither takes a positional argument), and a prose
  `papertrace refs --parse-only` with no manuscript — each a usage error, in files
  an agent executes verbatim. Every `papertrace` line in every skill is now parsed
  against the real Typer commands by a test. Appending `--help` would not have
  done: `--help` is eager and fires before click reports an unexpected extra
  argument, so `report case/ --help` exits 0 and the check would have passed a
  broken line. It parses each documented line into a click context instead, which
  validates arguments without invoking anything. Scanning one skill is how the
  fourth command survived while the other three were fixed, so the test walks
  `.claude/skills/**/*.md`.
- **docling deleted a hyphen that belonged to the word.** It joins a word split
  across two lines and drops the hyphen, which is *right* far more often than it
  is wrong — `approxi-` / `mately` is the single word "approximately", and 87 of
  94 breaks on the paper measured were of that kind — and wrong when the hyphen
  is the word's own: `Non-` / `Hispanic` arrived as `NonHispanic`, `thin-fat` as
  `thinfat`. Latent: it corrupts the text the model reads and the phrases it can
  quote. The adapter now repairs a join **only where the paper writes that
  compound out unbroken somewhere else** — the document's own evidence, never a
  lower→upper junction, which proves nothing (`HbA1c` and `PaperTrace` have one;
  `Timedependent` has one and is broken). Unproven joins are left exactly as
  docling produced them rather than repaired by guesswork: on the measured paper
  that is 4 blocks rewritten, not the 46 a blanket fix touched.
- **An anchor phrase could be unboxable for typesetting reasons alone, and this
  one was observed.** `page.search_for` reads a hyphenated line break as a
  space, so a page printing `Non-` / `Hispanic` carries only `Non- Hispanic` —
  neither the compound the paper means nor docling's join is on it, and a model
  tidying a quoted `develop- ing` to `developing` was searching for a string no
  page has. In a real audit one of two quoted phrases (`sohn-2022` p2) matched
  nothing, and that claim kept its box only because its second phrase matched.
  `highlight` now retries a missed phrase in forms the **page** dictates:
  whitespace beside a dash the phrase already carries, then the phrase rewritten
  with the page's own line-break hyphenation. Still exact text search, still no
  similarity matching — a phrase the page does not carry returns no box and
  `anchor_located = False`, as before. Measured over that audit's 20 anchor
  phrases: 18 located verbatim, 19 with the retry.
- **A reference list split by an intervening section was parsed short.** A real
  pre-proof put refs 1-9 on page 7, a `Declaration of interests` section next,
  then refs 10-15 on page 8. `references_section` stops at the following section
  header — the guard that keeps the reference list from swallowing the rest of
  the paper — so six references were never parsed, never retrieved and never
  mentioned: the audit reported 9 references on a paper citing 15. The list is
  now picked up again after an interruption, gated on two independent signals
  because neither alone separates a split bibliography from an appendix: the
  entries must be `list`-typed, and the resumed run must be at least two blocks.
  Restricted to `list` and never `text` on purpose — under the flat backend
  reference entries are `text`, the same type as every paragraph, so resuming
  there would swallow the Discussion of any paper whose references are not last.
  The cost of that asymmetry is that a flat-ingested split list is still parsed
  short. Only reference-shaped runs are collected, which matters more than it
  sounds: `_parse_bulleted` appends a non-bullet line to the *previous* entry, so
  a stray paragraph corrupts a reference rather than merely adding noise.

- **A bracketed label inside a reference could steal the next entry, and its
  DOI.** The mid-line marker rule exists because Elsevier PDFs run entries
  together — requiring a line start once collapsed 34 references into one. But
  the ascending-run filter accepted the *first* occurrence of the expected
  number and never reconsidered, so `[2]` printed inside reference 1's title
  became the start of reference 2, which then carried reference 1's DOI. A claim
  citing `[2]` would have been judged against paper 1: the same wrong-paper
  hazard the mid-line rule was added to fix, arriving from the other direction.
  Neither "first" nor "last" occurrence is safe — the mirror case, a real entry
  whose own text repeats its label, breaks the opposite guess — so a duplicated
  label is now **disclosed, never guessed**. Such an entry carries no DOI and
  `resolve_entry` refuses it before the provided-file match, the Crossref title
  lookup and every download, because all three derive from a `raw` string that
  is two references spliced together. New optional field
  `RefEntry.boundary_ambiguous`, in `schemas/refs_manifest.schema.json`.
- **Re-running `refs` on a legacy case could stamp a new paper's hash onto the
  old paper's references.** `refs` parsed the *cached* `source_map.json` before
  `_guard_case` ran, and the pre-hash branch of that guard only warns — so a
  different PDF with the same file name produced a manifest describing the
  previous paper while carrying the new one's `manuscript_sha256`. Every later
  run then trusted a case that mixed two papers, and the warning's own promise
  that "re-running `papertrace refs` fixes it" was exactly backwards: it
  laundered the identity instead. The guard now runs before any cached artifact
  is read, and a name-only case re-ingests the manuscript it was given so the
  manifest and its hash describe the same file. `_guard_case` returns the basis
  its answer rested on, which is what lets the caller act on it.
- **The offline test suite made a network call whenever docling was
  installed.** `tests/test_pipeline.py` called `ingest_pdf` without a backend,
  so `auto` chose docling and its first use fetched layout models from Hugging
  Face. CI installs `[dev]` only, so CI never saw it, and local runs passed on a
  warm cache — the suite's offline property was never tested, only its
  offline-with-warm-cache property. Every deterministic pipeline test now names
  `backend="pymupdf"`; the docling adapter is covered against stubs in
  `tests/test_ingest_backends.py`. Verified with empty caches and
  `HF_HUB_OFFLINE=1`.
- **The paid evaluation runner crashed before reaching a model.**
  `evals/runners/run_eval.py` called `cli.ingest(manuscript, out, backend)`
  positionally, but `ingest`'s third parameter is `case` — so `backend` bound to
  `case` and the real `backend` stayed Typer's `OptionInfo`, which the dispatcher
  rejects. The keyword-only fix had been applied in `cli.py` and missed here, in
  the one module no CI job executes. All stage calls are keyword-only now, with a
  forwarding test that fails if any argument shifts again.
- **The coverage audit counted the bibliography as body citations.**
  `references_section` was taught to accept a body-typed `References` block —
  necessary, because flat ingest guesses headings from font size and on a real
  Elsevier paper typed author lines as headings and left `References` as body
  text. `citation_occurrences` was not taught the same rule and still stopped
  only at a `sectionheader`, so on exactly that paper every `[N]` in the
  reference list counted as a manuscript citation and the audit reported gaps
  that do not exist. Both now call one `models.is_references_heading`, and the
  second, competing regex in `check.py` is gone rather than left to be reused.
- **A source nobody could identify was reported as a match.**
  `_title_check_text` returned the same `None` for "the title matches" and "there
  is no readable text to compare", so a scanned PDF — common for exactly the
  papers people supply by hand — got status `provided` and the reason `matched
  smith-2020.pdf in your sources folder`. The check is tri-state now
  (`verified` / `unverifiable` / `mismatch`, recorded in `RefEntry.title_check`
  and in the schema); the file is still used, because the user named it and
  there is nothing to fall back to, but the reason says which of the three
  happened. A test asserting the old silence — written on the true premise that
  unverifiable is not the same as wrong — had locked the defect in; not-wrong
  does not license saying nothing. The fact also reached only markdown readers,
  since the retrieval manifest is rendered in `report.md.j2` alone, so it is now
  a run-level disclosure carried into all three formats.
- **`not_addressed` + `unchecked` produced the headline "does not address the
  claim".** `not_addressed` asserts that every available source *was read* and
  none spoke to the claim — an inapt citation, a real finding about the paper. A
  source whose check failed was not read, so that assertion is unavailable.
  Ranking `not_addressed` above `unchecked` turned a run failure into a finding
  about the manuscript, in the one field a reader looks at first. `unchecked`
  now outranks it.
- **The contact email went to every host the resolver touched.** One
  `httpx.Client` carried a `mailto:` User-Agent, so Europe PMC, arXiv and
  whichever third party serves a PDF all received the address, while the wizard
  disclosed Unpaywall and Crossref. Unpaywall requires a contact and Crossref's
  polite pool uses one; nothing else does. The address is now a per-request
  header on those two calls only, so the code matches what the wizard already
  promised rather than the promise being widened.
- **`_judgement_from` was not total after all.** A 5,000-digit ASCII
  `source_page` passed both `isascii()` and `isdigit()`, then hit CPython's
  4300-digit integer conversion limit — so a `ValueError` escaped a function
  documented as unable to raise, and could abort a whole claim group instead of
  yielding `unchecked` with a note. Page strings are length-bounded before
  conversion, with a narrow `ValueError` catch around `int()` only; there is
  still deliberately no blanket `except Exception`, which would relabel our own
  bugs as the model's.
- **The wizard offered a cited paper's DOI as the manuscript's own.**
  `detect_doi`'s comment says only the front matter is read, "because a
  reference list is full of other papers' DOIs and picking one up would anchor
  the literature scout to somebody else's work without any error to notice" —
  but the code read the whole first page, which on a short paper reaches the
  reference list. The scan now stops at the references heading, using the same
  rule as ingest, and the confirmation no longer defaults to yes: pressing
  return used to accept whatever was found.
- **The wizard's "equivalent command" did not run and did not match.** Paths
  were interpolated unquoted, so `/tmp/My Paper.pdf` split into two arguments
  and Typer rejected `/tmp/My`; and the email was absent entirely, so the
  printed replay either failed or silently used a different saved address. It is
  built as argv and rendered with `shlex.join`, and carries `--email`.
- **"Up to N model calls" was a ceiling the retry could exceed.** `_ask` retries
  once, so a single-source run advertised as 2 calls could issue 3. The estimate
  now shows a base and a worst case derived from `check.ASK_ATTEMPTS`, so the
  quoted cost cannot drift from the policy that governs it.
- **A `--help` assertion passed locally and failed on all five Python versions
  in CI.** rich styles single words inside a sentence, so with colour enabled the
  help screen renders `Run \x1b[1;2mpapertrace\x1b[0m\x1b[2m with no arguments`
  and a plain substring test for that phrase cannot match. CI has colour on and a
  developer terminal usually does not. The test now forces colour on for the
  render and strips it before asserting, so it exercises the styled path
  everywhere instead of depending on the terminal it runs in.
- **The live progress marks reported every source as unretrieved while judging
  it.** `tick` read `ClaimResult.verdict`, but multi-source checking only assigns
  that field in `apply_headline()`, which runs after *every* source group — so
  each group printed the field's default, `not_retrieved`, whose glyph `○` is
  exactly what the final tally uses for a source that was never obtained. The
  demo run judged 2 supported and 2 contradicted while the console showed
  `○ ○ ○ ○`. Marks now come from that source's own `SourceJudgement`, and
  `partial`, `not_addressed` and `unchecked` each get their own glyph instead of
  sharing the fallback.
- **`--provided` could hand a supplement to the judge instead of the paper.**
  `_match_provided` returned the first filename containing every slug token over
  an *unsorted* `Path.glob`, so a sources folder holding both an article and its
  supplement produced an undefined choice — the same folder could yield different
  audits on different machines — and a folder holding only a supplement supplied
  it as the source. Candidates are ranked now (exact `<slug>.pdf`, else shortest
  name), supplement-looking filenames are excluded rather than ranked last, and a
  supplement-only match falls through to the online resolver. The marker list
  contains nothing shorter than five characters: `si` would have rejected the
  real slug `si-mohamed-2021`.
- **A provided file was accepted with no verification at all.** The title check
  runs inside `_accept`, which only ever sees downloaded candidates, so
  `--provided` bypassed it entirely. Provided files are checked now, and a
  mismatch is recorded in the manifest rather than refused — the user named the
  file and there is nothing to fall back to. An unreadable or scanned PDF still
  passes, per the existing rule that unverifiable is not the same as wrong.

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
  A multi-reference claim was judged against the first available source only;
  the sources it never opened are recorded per claim (`unjudged_refs`) and named
  in every report — and later in this same release the claim came to be judged
  against *every* retrievable cited source, so `unjudged_refs` now holds only
  those that could not be obtained. The manuscript was silently cut
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


- **The console verdict tally did not count `not_addressed`.** A real audit
  printed `0 supported · 19 partial · 0 contradicted · 12 not retrieved` for 34
  claims; `results.json` for the same run had `not_addressed: 3`. The verdict was
  added to all three report templates and missed on the console — the surface a
  user reads first. The buckets now sum to the number of claims, and a test
  asserts that arithmetic rather than the wording.
- **Thirteen lines of third-party log noise buried the ingest backend.** The
  torch warning was self-inflicted: `_quiet_third_party_loggers` set `TORCH_LOGS`
  *and* called `set_logs`, so torch printed "Using TORCH_LOGS environment
  variable …, ignoring call to set_logs" — a warning that existed only because we
  did both. RapidOCR could not be silenced the way it was attempted at all: it
  creates its logger, calls `setLevel` on it itself and attaches its own console
  handler, all inside the single `convert()` call, so a level set beforehand is
  overwritten and a private handler emits regardless. The conversion now runs
  inside a scoped `logging.disable(INFO)`, restored in `finally`. Only
  third-party model chatter is affected: every disclosure PaperTrace makes
  travels by `rich`, not `logging`.


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

[0.6.0]: https://github.com/defraction0/PaperTrace/releases/tag/v0.6.0
[0.4.0]: https://github.com/defraction0/PaperTrace/releases/tag/v0.4.0
[0.3.1]: https://github.com/defraction0/PaperTrace/releases/tag/v0.3.1
[0.3.0]: https://github.com/defraction0/PaperTrace/releases/tag/v0.3.0
