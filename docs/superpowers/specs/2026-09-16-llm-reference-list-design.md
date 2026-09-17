# LLM-proposed reference list as a reconcile candidate

Design, 2026-09-16. Status: approved, not implemented.

## Why

A real manuscript under review (22 printed references) parsed as 18, and every
claim citing `[6]` or above was judged against a different paper than the one
cited. Six substantive verdicts named papers the manuscript never cited.

Three root causes, all verified against the code and the on-disk artifacts:

1. **`refs.py:152`** — `items[-1] += " " + s` treats a non-bullet line as a
   wrapped continuation. Docling rendered part of the bibliography as a GFM
   table, so five reference rows were glued onto the previous entry's `raw`.
   Reproduced offline: `_entry` then scraped the *next* reference's DOI onto the
   previous entry, and `_title_check` **passed** on it, because the raw string
   contained both papers. A wrong-paper verdict with `identity confirmed`.
2. **`refs.py:185`** — `seen[0] != 1` refuses a partly-numbered list wholesale,
   and the caller then numbers by position anyway. Docling stripped the numerals
   from the bullets and kept them only inside the table, so no printed `1`
   existed and `_usable_printed_numerals` returned `False` about a document that
   *did* print a numbering. Measured: `[None]*5 + [6..10]` → `False`.
3. **`_covers` conflates two questions** — `body <= nums` (a genuine subset
   test) and `len(entries) == max(body)` (a proxy that assumes citation-order
   numbering and no supplement-only references). The correct 22-entry parse of
   this paper *fails* `_covers`, because `[20]`–`[22]` are cited only in the
   supplement: `22 == 19` is False.

`docs/adr/0002-no-grobid.md` already specifies the remedy and withholds
authorisation for it:

> If reference parsing ever becomes the dominant source of wrong audits, the
> cheap escalation is **a third candidate reading** wired into the existing
> `reconcile` arbitration behind an opt-in flag — not a replacement of the
> parser. [...] This ADR does not authorise it; it records it as the shape a
> future proposal should take.

That condition is now met with evidence. This design is that proposal.

## What is rejected, and why

- **A pairwise visual vote** ("does this bibliography crop match that title
  crop"). It verifies `entry → PDF`, which is already deterministically
  title-checked and which *passed* on all six wrong verdicts. The broken join is
  `label → entry`, and no crop carries a label, because the numeral is what the
  converter destroyed. It also has no deterministic verifier, so a false "match"
  would overwrite a correct `numbering_verified = False` with a plausible
  confirmation — the one thing CLAUDE.md forbids.
- **The LLM as arbiter.** The body's own `[N]` markers are the only reading
  definitionally right about what the paper cites.
- **Field-level splicing across candidates by position.** pymupdf loses two
  DOIs entirely, so from that point two lists are off by two and a `num` from
  one would be stapled to another's `doi`. That is reading-order zipping, which
  this codebase has rejected three times.
- **Images through `_ask`.** `claude -p` has no image channel (verified: no
  `--image`; `--file` needs an Anthropic Files-API id). Granting `Read` would
  dismantle `--tools ""` and `_scratch_cwd`, and would falsify README line 216.
  Also moot: the table block has `regions=1` for all five references, so no
  per-entry rectangle exists to crop.

## Architecture

`reconcile` goes from two candidates to four. Nothing replaces the parser.

```
                    ingest ──► refs ──► scout ──► check ──► highlight ──► report
                                 │
        ┌────────────────────────┴────────────────────────┐
        │ 1  crossref deposit    existing — absent with   │
        │                          no DOI (the main case) │
        │ 2  run-backend parse   existing                 │
        │ 3  pymupdf parse       NEW — free on a docling  │
        │                          run, skipped on a      │
        │                          pymupdf run (identical)│
        │ 4  llm structured list NEW — one call, default on│
        └────────────────────────┬────────────────────────┘
                                 ▼
                    reconcile(body_labels, candidates)
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
     a candidate covers              none covers:
     the body's labels          →    per-label agreement decides
     → numbering_verified            (agreed / single / disputed / absent)
```

### Units

| Unit | Does | Depends on |
|---|---|---|
| `ask.py` *(new, ~50 lines, extracted from `check.py`)* | The one hardened `claude -p` subprocess call: `--safe-mode`, `--tools ""`, `_scratch_cwd`, retries, timeout. Records the model **per call site** | nothing in-project |
| `reflist.py` *(new, ~180 lines)* | Builds the two-reading prompt, calls `ask`, parses the reply, field-verifies every value verbatim against the two input texts, returns `(list[RefEntry], ReflistProvenance)` | `ask`, `models` |
| `refs.reconcile()` *(extended)* | Takes N candidates; computes per-label agreement | `models` |
| `cli.py` *(wired)* | `--llm-refs/--no-llm-refs`, the pymupdf second reading, the interactive escalation | all of the above |

`reflist.py` is deterministic given a canned reply, so with `ask._ask`
monkeypatched every verification rule is testable offline. That is why
extracting `ask.py` is load-bearing rather than tidying.

### CLAUDE.md invariant change

`refs` needs a model call, because the numbering must be settled before
`refs_manifest.json` is written (both `scout` and `check` read it). The rule
"`check.py` is the only module that calls a model" becomes "every model call
goes through `ask.py`'s single seam; `check.py` and `refs.py` are its only
callers" — which is what the rule was protecting, and is now *enforceable*:

```python
def test_only_ask_py_shells_out_to_a_model():
    """greps src/ for `subprocess`, asserts exactly one file"""
```

It also fixes `check._LAST_MODEL` being clobbered: the model name moves to the
seam, which records per call site. The disclosure requirement depends on that.

## The model's output, and what Python does to it

```json
[{"num": "6",
  "authors": "Fujita S, Mori S, Onda K et al",
  "year": "2023",
  "title": "Characterization of Brain Volume Changes in Aging Individuals ...",
  "journal": "JAMA Netw Open. 6(6):e2318153",
  "doi": "10.1001/jamanetworkopen.2023.18153",
  "seen_in": "A"}]
```

Per field, in order:

1. **Verbatim check** against reading A or B (normalisation below). Fail ⇒ the
   **field** is discarded, not the entry.
2. **DOI shape** must match the existing `DOI_RE`. A line-broken
   `10.1038/s41591-` fails ⇒ `doi = None` ⇒ the entry resolves `no_doi`, a
   recorded gap.
3. **Numeral uniqueness.** A numeral proposed twice, or a gap in `1..max`, marks
   the candidate as not covering. It is never repaired.
4. **Whole-candidate discard** if any title fails verbatim. The run proceeds on
   the deterministic readings, with a disclosure saying the model's reading was
   unusable — never that it agreed.

A discarded field costs a gap; an accepted field is one you can grep for in the
source text. Nothing invented can survive to name a paper.

### Normalisation, per field

| Layer | Does | Applies to |
|---|---|---|
| **E — extraction damage** | de-hyphenate across line breaks, collapse whitespace, NBSP→space, ligatures `ﬁﬂﬀﬃﬄ`→`fi fl ff ffi ffl`, curly quotes→straight, en/em dash→hyphen | **all fields** |
| **O — orthography** | `_fold` (NFKD + strip combining) **plus** an explicit map for what NFKD misses — `ß→ss, ø→o, æ→ae, œ→oe, đ→d, ł→l, ı→i, þ→th` — then casefold | **authors and title only** |

Exact after Layer E, no folding: **DOI** (case-insensitive), **year**,
**journal**, **volume**, **issue**, **pages**.

Two rules that follow:

- **Layer E's de-hyphenation must not run inside a DOI.**
  `10.1038/s41591-\n019-0673-2` de-hyphenated gives `10.1038/s41591019-0673-2`
  — the hyphen was real. A DOI failing `DOI_RE` becomes `None`, never a repair.
- **A DOI that is a strict prefix of another does not settle identity.** Fall
  through to year + title tokens. A truncated DOI is evidence of extraction
  damage, not of a different paper. This prevents pymupdf's line-broken DOIs
  from causing false disputes.

### A latent bug this fixes on the way

`models._title_tokens` is `re.findall(r"[a-z]{5,}", raw.lower())` — ASCII only.
Measured:

```
Romero-Cristóbal  raw -> ['crist', 'romero']   folded -> ['cristobal', 'romero']
Küstner           raw -> ['stner']             folded -> ['kustner']
Späth             raw -> []                    folded -> ['spath']
Müller            raw -> []                    folded -> ['muller']
Bjørnsson         raw -> ['rnsson']            folded -> ['rnsson']   (ø needs the map)
Weiß              raw -> []                    folded -> []           (ß needs the map)
```

`Müller` and `Späth` contribute **no tokens at all**, and `_title_tokens` feeds
`titles_match`, `_same_work` and `_title_check_text`. `_fold` already exists at
`refs.py:957` and is not used by it. Fixing this improves reference identity for
every non-English author independently of this feature, so it ships first, as
its own PR.

## Per-label agreement

**The join key is `e.num` — the printed numeral, a recorded fact.**
`_first_divergence` cannot be reused: it zips by *position*, so it would compare
docling's 6th entry (Wachinger's tail) against pymupdf's 6th (Fujita) and report
divergence for the wrong reason.

```python
def label_agreement(
    candidates: dict[str, list[RefEntry]],   # {"parsed": [...], "pymupdf": [...], "llm": [...]}
    body: set[str],
) -> dict[str, str]:
    """Per cited label: agreed | disputed | single | absent."""
```

| State | Condition | Verdicts |
|---|---|---|
| `agreed` | ≥2 readings have it, all pairwise `_same_work` | **print** |
| `single` | exactly 1 reading has it | **print** with today's caveat |
| `disputed` | ≥2 readings have it, any pair fails `_same_work` | **withhold** ⇒ `unchecked` |
| `absent` | no reading has it | already `not_retrieved` |

**Any disagreement means `disputed` — no majority vote.** Ranking candidates
instead of refusing is rejected by name elsewhere in this codebase: *"A
non-unique match is refused, never ranked."*

**`single` deliberately prints.** If it withheld, a pymupdf run whose LLM
candidate was discarded would have one reading, every label would be `single`,
and the audit would die. Verdicts are subtracted only where readings *actively
contradict each other*.

### Withholding, concretely

At the existing availability filter in `check.py`:

```python
avail = [(r, e) for r, e in pairs if e and e.status in ("retrieved", "provided") and e.slug]
```

A `disputed` label is dropped from `avail`. If nothing survives, the claim's
verdict is **`unchecked`** — already in `PIPELINE_STATES`, already meaning "a
verdict nobody can be shown" — with a note naming the disputed labels.
Explicitly **not** `not_retrieved`, which would falsely claim the source could
not be obtained when it was fetched and read.

### The false alarm this also fixes

`numbering_corroborated` is added as a **second, independent axis**:

```python
numbering_corroborated: bool = False    # every cited label agreed by >= 2 readings
corroborating_readings: list[str] = []  # which ones, named
```

`numbering_verified` keeps its exact current meaning, so no published boolean is
redefined and no model path can flip it. Corroboration is reported beside it,
which lets the disclosure stop crying wolf on the 22-vs-19 case.

## Interactive escalation: show, then resolve

Fires only when **all three** hold: no candidate passed `_covers`, at least one
cited label is `disputed`, and the session is interactive.

```python
def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("CI")
```

### Step 1 — unskippable

Write `case/out/reference_disagreement.md` (per disputed label: every reading's
structured fields **and the verbatim source text each was read from**), print a
compact table and the path. Nobody is asked to choose blind.

### Step 2 — the targeted resolution call

```
1. Ask the model to resolve the N disputed labels     [default]
2. Withhold verdicts on all N
3. Use one reading whole: pymupdf / docling
4. Abort
```

Input: per disputed label, each reading's structured entry plus the verbatim raw
span it came from, from both texts. Output: a resolved entry, which reading it
matches (or corrected fields), **or an explicit abstention**.

Three rules on the reply:

1. **Field-verify verbatim** (Layers E and O). An unverifiable field is
   discarded; an unverifiable **title** leaves the label disputed.
2. **Abstention is a first-class answer.** `cannot tell` keeps the label
   disputed and withheld. A call that must always answer will confabulate on
   exactly the labels two readings disagree about.
3. **Partial resolution is normal** — 11 of 14 resolved and 3 still disputed is
   a real, representable, reported outcome.

### What no path can do

`numbering_verified` stays **False** through every model reply and every
interactive choice. Recorded instead:

```python
numbering_choice    = ""  # withheld | llm_resolved | parsed | pymupdf
numbering_chosen_by = ""  # default | user
labels_resolved     = []
labels_disputed     = []
```

`chosen_by: "user"` is what makes the escalation honest: a person consenting to
proceed is an input, not evidence. This codebase already ranks that signal — *"a
filename carries the user's assertion, content carries none."*

Lives in `cli._refs_pipeline`, not `wizard.py`: both `papertrace refs` and
`papertrace run` need it, and the wizard already delegates there.

## Disclosures, schema, parity

### PR 0 gates everything

There are 18 disclosure keys; 6 are claim-level (`anchor`, `sources`,
`unjudged_refs`, `no_quote`, `supplement_headline`, `claim_numbering`). The only
mechanical guard is:

```python
claim_level = {"anchor", "sources", "unjudged_refs", "judgement_anchor"}
missing = {k for k in keys - claim_level if f'"{k}"' not in terminal}
```

Two holes: the exclusion set is hand-written, contains one **dead** key
(`judgement_anchor` has zero occurrences in `disclosures.py`), and omits 3 of
the 6 real claim-level keys; and **no mechanical check exists for
`report.md.j2` or `report_editor.html.j2`**, both of which filter claim-level
disclosures by explicit `d.key == "..."`. A new claim-level key therefore
reaches the viewer and silently vanishes from the other three.

Fix: declare `CLAIM_KEYS` in `disclosures.py` where the producers live, drop the
dead key, and assert every claim-level key appears in all three Jinja templates.

### New keys — three run-level, one claim-level

| Key | Level | Fires when | Says |
|---|---|---|---|
| `reflist` | run | `--llm-refs` enabled | the required LLM disclosure: model, both readings, fields discarded |
| `labels_disputed` | run | any label disputed | which labels, and what happened to them |
| `numbering_corroboration` | run | ≥2 readings agree on every cited label | **not** gated on `numbering_verified` |
| `claim_pairing` | claim | the claim cites a non-agreed label | four texts, one per state |

```python
REFLIST_TOKEN = "the reference list was also read by a model"
```

Its text must carry the ceiling: *"A model agreeing with a parse is a second
reading, not confirmation: it read the same document, so a reference the layout
destroyed is one it may also have missed."*

`claim_pairing` has four texts — withheld, resolved-by-model-accepted-by-user,
a-reading-chosen-by-user, and corroborated (`level="info"`, and only on a claim
already carrying a warn-level disclosure, so it is not noise on a clean claim).

### Schema

`schemas/refs_manifest.schema.json` has **no `"schema"` version field** and
`additionalProperties` is never `false`, so the house convention applies:
additive fields, none in `required`, each with an explicit "absent means X" in
its `description`. No version bump.

Manifest level: `numbering_corroborated`, `corroborating_readings`,
`labels_disputed`, `labels_resolved`, `numbering_choice`, `numbering_chosen_by`,
`numbering_contested` (persists the flag that reached no reader),
`numbering_ledger` (`labels_detected`, `labels_absent`, `numerals_duplicated`,
`numerals_absent`, `labels_uncountable`), `reflist_model`,
`reflist_fields_discarded`.

Entry level, one field: `seen_in: list[str]` — which readings contributed this
entry. An entry seen only in the model's reading must be spottable.

New vocabulary, published as a tuple so there is something to compare against:

```python
LABEL_AGREEMENT = ("agreed", "single", "disputed", "absent")
```

`tests/test_coverage.py:384` asserts `set(VERDICTS) == set(schema_enum)`, but
**no equivalent test exists for `title_check`, `status` or `kind`** — which is
why a new state can fall through `disclosures.py`'s two-literal branch with
nothing red. PR 0 adds the parity test for `LABEL_AGREEMENT` and for those
three.

`labels_disputed` absent means **never computed**, not "none disputed" — the
same three-state discipline as `table_warnings`.

### Viewer

No change needed. `viewer_app.js:195` is a genuine run-level catch-all
(*"including any key this page has never heard of"*) and claim-level is
`sel.disclosures.filter(d => d.key !== 'anchor')`.

## Testing

Ten PRs, one behavioural change each. PR 0 and PR 5 carry no behaviour change.

| PR | Change | First failing test | Gates |
|---|---|---|---|
| 0 | Parity guard + vocabulary parity | `test_every_claim_disclosure_key_is_rendered_by_every_jinja_format` | — |
| 1 | `_fold` → `models.py` + map, used by `_title_tokens` | `test_a_surname_with_an_umlaut_still_contributes_tokens` | 4 |
| 2 | `_unwrap_table_rows` pre-pass | `test_a_wrapped_row_with_an_empty_numeral_cell_joins_the_row_above` | 4 |
| 3 | `_numerals_agree_with_position`, three-way outcome | `test_printed_numerals_contradicting_their_position_are_not_numbered_by_position` | 2, 4 |
| 4 | Ledger + `contested` persisted | `test_a_contested_but_verified_numbering_reaches_every_format` | 2 |
| 5 | Extract `ask.py` | `test_only_ask_py_shells_out_to_a_model` | — |
| 6 | pymupdf candidate + agreement + withholding | `test_a_disputed_label_withholds_the_verdict_and_names_it` | 2, 4 |
| 7 | `reflist.py` | `test_a_field_not_found_verbatim_in_either_reading_is_discarded` | 2, 4 |
| 8 | Interactive escalation | `test_the_disagreement_is_written_before_the_question_is_asked` | 2 |
| 9 | Eval task | offline `score_only.py` fixture | — |

No test calls a model or the network. `ask._ask` is monkeypatched everywhere;
readings are strings built in-test; PDFs come from `fitz.open()` +
`page.insert_text()`. New modules: `tests/test_reference_tables.py`,
`tests/test_reflist.py`, `tests/test_label_agreement.py`.

Gate 5 does not apply to PRs 0–8: no ingest, highlight or report *code*
changes. Templates gain branches, which the parity tests cover. State this
explicitly in each PR rather than skip it.

### The invariant test

```python
def test_no_model_path_can_set_numbering_verified(monkeypatch):
    """Parametrised over every model reply shape and every interactive choice.
    Most of this design's safety lives in one flag staying False."""
```

## Evaluation

A **second task**, never folded into `judgment_accuracy`. The unit is
`(citation label, printed reference entry)` — deliberately not
`(label, retrieved PDF)`, which conflates the two joins and is why
`title_check: verified` was printed on six wrong papers.

New `pairing_gold[]`, sibling of `coverage_gold`: `label`, `gold_entry_text`
(verbatim as printed, human-auditable), `gold_doi`, `gold_verdict`
(`same_work | different_work | null`), `why`, `page`, `ambiguity`. **No block id
and no entry index** — a gold set keyed on position breaks whenever parsing
changes. Plus set-level `gold_labels_in_text` (including labels appearing only
in tables) and `gold_entry_numerals`, so the deterministic ledger is graded
separately.

| Metric | Population |
|---|---|
| `wrong_numbering_confirmed_rate` | labels gold says the parse got wrong — **the safety metric** |
| `correct_numbering_refused_rate` | labels gold says the parse got right — the cost |
| `disputed_resolution_accuracy` | labels the resolution call answered |
| `abstention_rate` | labels it was asked about |
| `dropped_numeral_recall`, `duplicate_numeral_recall` | ledger vs `gold_entry_numerals` — deterministic, graded apart |

The two error directions are reported **separately**, never blended.

Plumbing that is not optional:

1. `metrics.confusion()` and `per_class` hard-code `JUDGMENT_VERDICTS` —
   parameterise `classes`, the gold key and the prediction accessor.
2. New populations must be declared in `scoring._populations`.
3. `eligibility.py:95` keys unscoreable on `gold_verdict is None`; pairing rows
   need their own pass, plus an exclusion reason for **superscript papers**,
   which have no arbiter and cannot be scored — excluded and counted, never
   deleted.
4. `provenance.prompt_fingerprint` hashes `EXTRACT_PROMPT` and `CHECK_PROMPT`
   only. Adding `REFLIST_PROMPT` and `RESOLVE_PROMPT` without extending it is
   the classic eval failure.

`align.py` is **not** reusable and must not be touched: its candidate scoring is
`difflib` over `(normalize_claim(text), set(labels))` with a 0.60 floor, and a
pairing task wants an exact join on the label. `missing` keeps its `list[str]`
meaning byte for byte — a `pairing_refused` set must never be unioned into it,
because a wrongly-paired label is a numbering fault, not an extraction gap.

**No accuracy figure may appear in the README** until measured on a gold set
this project's authors did not construct, labelled by ≥2 people who did not
write the prompts, on **both backends** — the converter is the cause here, not a
nuisance parameter.

## Docs

| File | Change |
|---|---|
| `docs/adr/0003-llm-reference-list.md` *(new)* | the decision ADR 0002 withheld: trigger met with evidence, the sanctioned shape, and the four rejected alternatives above |
| `CLAUDE.md` | the `ask.py` seam rule; the `refs.py` section gains the table-row rule and "do not restore positional numbering after a printed numeral contradicts it" |
| `CHANGELOG.md` | `## [0.7.0]` Added / Fixed / Changed, with the real numbers in Fixed |
| `README.md` | the numbering bullet; a new "does" bullet; a new **"does not"** bullet stating the ceiling; the bracketed-numeric bullet gains the table-skip caveat. **Line 216 stays true** — no images, nothing retracted |
| `evals/DESIGN.md` | the new task section |

## Open questions

None blocking. Two things to confirm during implementation:

- The regex shapes are now **observed**, not reconstructed: the real docling
  table block is `block_0054` of the case on disk — separator
  `|-----|---…---|` (variable dashes, no colons), empty cell `|     |`
  (spaces, not `||`), numeral cell `|  6. |`. **The separator is at line 1, not
  line 0**: docling promoted the table's first data row to the markdown header
  row, and that row is the continuation of the preceding bullet entry. Any
  implementation that skips a header row deletes reference 5's DOI-bearing tail.
- `max(body_labels)` is structurally a floor because `_body_citation_labels`
  skips table blocks. **Measured on this manuscript: no label appears only in a
  table and the label set is contiguous, so the hazard did not bite here.** It
  justifies a ledger field, not a finding.
