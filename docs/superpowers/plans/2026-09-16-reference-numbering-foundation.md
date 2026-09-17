# Reference Numbering Foundation — Implementation Plan (Plan A of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop PaperTrace judging claims against the wrong paper when a converter renders a bibliography as a table or strips its printed numerals.

**Architecture:** Five deterministic changes, no model involved. A normalising pre-pass teaches `parse_references` that a markdown table row is a reference, not a wrapped continuation. A position-vs-printed-numeral check replaces the silent sequential guess with a three-way outcome that can refuse. A numbering ledger reports *which* numerals were dropped or duplicated instead of a bare count difference. Unicode folding fixes a latent bug that silently discards any author name carrying a diacritic.

**Tech Stack:** Python 3.10+, pytest, pymupdf (`fitz`), Jinja2, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-16-llm-reference-list-design.md`

**Plan B** (`ask.py` extraction, per-label agreement, `reflist.py`, interactive escalation, eval task) depends on this plan and is written separately. Plan A alone fully closes the observed wrong-paper bug.

## Global Constraints

- **Tests stay offline.** No network, no model calls, ever. HTTP is faked with `httpx.MockTransport`. PDFs are generated in-test with pymupdf and never committed.
- **No `conftest.py` in `tests/`.** Every test module does `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))` then imports with `# noqa: E402`.
- **`ruff check src tests scripts evals` must be clean.** Line length 100, target py310, rules `E,F,W,I,UP,B`, `E501` ignored. **Do not run `ruff format`** — it is not enforced and reformatting files wholesale is forbidden.
- **Python 3.10+ syntax**: `X | None`, never `Optional[X]`.
- **Comments state constraints the code can't, not narration.**
- **Status words stay lowercase** in prose and reports.
- **Gate 2:** a new status, verdict or JSON field requires a `schemas/` update **and** a round-trip test.
- **Gate 4:** any task touching `refs.py` must prove the failure path still degrades honestly.
- **Gate 5 does not apply to any task in this plan** — no ingest, highlight or report *code* changes. Templates gain no branches in Plan A. State this in each PR description rather than skipping it silently.
- **Never commit or push unless the user explicitly asks.** The commit step in each task is written out for convenience; **stop and ask before running it.**
- Run tests with `/Users/danielgutmann/anaconda3/bin/python3.13 -m pytest` from the repo root.

---

### Task 1: Declare the claim-level disclosure keys

**Why:** There are 18 disclosure keys; 6 are claim-level. The only mechanical parity guard excludes claim-level keys via a hand-written set that contains one **dead** key (`judgement_anchor`, zero occurrences in `disclosures.py`) and omits 3 of the 6 real ones. And **no mechanical check exists for `report.md.j2` or `report_editor.html.j2`**, both of which filter claim-level disclosures by explicit `d.key == "..."`. A new claim-level key reaches the viewer and silently vanishes from the other three. Plan B adds such a key.

**Files:**
- Modify: `src/papertrace/disclosures.py` (add `CLAIM_KEYS` near the token constants, around line 48)
- Modify: `tests/test_disclosure_parity.py:227-233`
- Test: `tests/test_disclosure_parity.py`

**Interfaces:**
- Consumes: nothing
- Produces: `disclosures.CLAIM_KEYS: frozenset[str]` — the keys `claim_disclosures()` can emit. Plan B adds `"claim_pairing"` to it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_disclosure_parity.py`:

```python
TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "papertrace" / "templates"
JINJA_FORMATS = ("report.md.j2", "report_editor.html.j2", "report_terminal.html.j2")


def test_claim_keys_names_only_keys_a_producer_actually_emits():
    """A key in the set that no producer emits is dead weight that silently
    widens the exemption below. `judgement_anchor` was exactly that."""
    import re

    from papertrace import disclosures as mod

    src = Path(mod.__file__).read_text()
    real = set(re.findall(r'key="([a-z_]+)"', src))
    assert mod.CLAIM_KEYS <= real, f"not emitted by any producer: {sorted(mod.CLAIM_KEYS - real)}"


def test_every_claim_disclosure_key_is_rendered_by_every_jinja_format():
    """The viewer renders claim disclosures generically; the other three filter
    by explicit key. So a new claim-level disclosure reaches one reader in four
    and vanishes for the rest — the `source_identity` failure one layer down,
    and the layer with no guard on it.

    The six keys that exist today all pass. The point is that the seventh
    cannot be added without this going red first.
    """
    from papertrace import disclosures as mod

    for name in JINJA_FORMATS:
        src = (TEMPLATES / name).read_text()
        missing = {k for k in mod.CLAIM_KEYS if f'"{k}"' not in src}
        assert not missing, (
            f"{name} renders no branch for {sorted(missing)} — it filters claim "
            "disclosures by explicit key, so a new one is dropped, not surfaced"
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_disclosure_parity.py -k claim_keys -v`
Expected: both FAIL with `AttributeError: module 'papertrace.disclosures' has no attribute 'CLAIM_KEYS'`

- [ ] **Step 3: Declare the constant**

In `src/papertrace/disclosures.py`, immediately after the existing `CLAIM_NUMBERING_TOKEN` definition (around line 49):

```python
# The keys `claim_disclosures()` can emit. Declared here, where the producers
# live, because three of the four report formats filter claim disclosures by
# explicit key and so drop an unlisted one without erroring. The viewer is
# generic and would keep rendering it, which is what makes the loss silent.
CLAIM_KEYS = frozenset(
    {
        "anchor",
        "sources",
        "unjudged_refs",
        "no_quote",
        "supplement_headline",
        "claim_numbering",
    }
)
```

- [ ] **Step 4: Point the existing terminal guard at it**

In `tests/test_disclosure_parity.py`, replace lines 227-229:

```python
    claim_level = {"anchor", "sources", "unjudged_refs", "judgement_anchor"}

    missing = {k for k in keys - claim_level if f'"{k}"' not in terminal}
```

with:

```python
    # claim-level keys are covered by
    # test_every_claim_disclosure_key_is_rendered_by_every_jinja_format, which
    # checks all three Jinja formats rather than only this one
    missing = {k for k in keys - set(mod.CLAIM_KEYS) if f'"{k}"' not in terminal}
```

- [ ] **Step 5: Run the full parity module and the suite**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_disclosure_parity.py -v`
Expected: PASS, including the two new tests

Run: `~/anaconda3/bin/python3.13 -m pytest -q && ~/anaconda3/bin/python3.13 -m ruff check src tests scripts evals`
Expected: whole suite green, `All checks passed!`

- [ ] **Step 6: Commit (ask the user first)**

```bash
git add src/papertrace/disclosures.py tests/test_disclosure_parity.py
git commit -m "tests: claim-level disclosure keys are declared, and checked in every format

The parity guard exempted claim-level keys through a hand-written set that
held a dead key and omitted three real ones, and no check covered the
markdown or editor templates at all. A new claim-level disclosure would
have reached the viewer and silently vanished from the other three."
```

---

### Task 2: Fold diacritics before tokenising a title

**Why:** `models._title_tokens` is `re.findall(r"[a-z]{5,}", raw.lower())` — ASCII only. Measured:

```
Romero-Cristóbal  raw -> ['crist', 'romero']   folded -> ['cristobal', 'romero']
Küstner           raw -> ['stner']             folded -> ['kustner']
Späth             raw -> []                    folded -> ['spath']
Müller            raw -> []                    folded -> ['muller']
Bjørnsson         raw -> ['rnsson']            folded -> ['rnsson']   (ø needs the map)
Weiß              raw -> []                    folded -> []            (ß needs the map)
```

`Müller` and `Späth` contribute **no tokens at all**, and `_title_tokens` feeds `titles_match`, `_same_work` and `_title_check_text`. A `_fold` already exists at `refs.py:957` and is not used by it. NFKD alone does not handle `ß ø æ œ đ ð þ ł ı`, so an explicit map is required as well.

**Files:**
- Modify: `src/papertrace/models.py` (add `import unicodedata`; add `_TRANSLITERATE` and `_fold` above `_title_tokens` at line 77; change `_title_tokens`)
- Modify: `src/papertrace/refs.py:957-965` (re-export instead of redefining)
- Test: `tests/test_refs.py`

**Interfaces:**
- Consumes: nothing
- Produces: `models._fold(text: str) -> str` — lowercased, transliterated, diacritics decomposed away. `refs._fold` remains importable and is the same object.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_refs.py`:

```python
def test_a_surname_with_an_umlaut_still_contributes_tokens():
    """`_title_tokens` is `[a-z]{5,}`, so a diacritic splits a word or deletes
    it. `Späth` and `Müller` contributed NOTHING, and this set is what
    `titles_match`, `_same_work` and `_title_check_text` all compare — a
    surname that vanished cannot agree with its own paper.

    `_fold` existed in `refs.py` and was not used here. NFKD alone is not
    enough: it leaves ß, ø, æ, đ, ł undecomposed.
    """
    assert "spath" in models._title_tokens("Späth C, Makowski MR")
    assert "muller" in models._title_tokens("Müller H")
    assert "cristobal" in models._title_tokens("Romero-Cristóbal M")
    assert "kustner" in models._title_tokens("Küstner T")
    assert "bjornsson" in models._title_tokens("Bjørnsson B")


def test_folding_does_not_invent_or_merge_tokens():
    """Gate 4, the other direction: an ASCII title must tokenise exactly as
    before, and the stopword list must still bite after folding.

    Note the stopword list holds `commun`, not `communications` — so
    "Nature Communications" keeps a token. These four are all in the list.
    """
    assert models._title_tokens("Nature Science volume press") == set()
    assert models._title_tokens("Robust segmentation of anatomic structures") == {
        "robust",
        "segmentation",
        "anatomic",
        "structures",
    }
```

Ensure the module imports `models` — `tests/test_refs.py` already does `from papertrace import models  # noqa: E402`; if it imports only names, add `models` to that import.

- [ ] **Step 2: Run the test to verify it fails**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_refs.py -k umlaut -v`
Expected: FAIL — `assert 'spath' in set()`

- [ ] **Step 3: Add the fold to `models.py`**

Add `import unicodedata` to the import block at the top of `src/papertrace/models.py`, then insert above `_title_tokens` (line 77):

```python
# What NFKD cannot decompose, because these are distinct letters rather than a
# base plus a combining mark. Without them `Weiß` folds to `wei` and `Bjørnsson`
# to `bjrnsson` — a surname that changed, not one that normalised.
_TRANSLITERATE = str.maketrans(
    {
        "ß": "ss",
        "ø": "o",
        "æ": "ae",
        "œ": "oe",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "ł": "l",
        "ı": "i",
        "ħ": "h",
        "ŧ": "t",
    }
)


def _fold(text: str) -> str:
    """Lowercase, transliterated, diacritics decomposed away — `İnce` → `ince`.

    Lives here because three readers need it and none may import another, the
    same reason `titles_match` and `_title_tokens` do. `_slug` deletes non-ASCII
    instead (`[^A-Za-z\\-]`), which is why `İnce O` slugs `nce-2023` and `Müller`
    slugs `mller`. Folding is what a name comparison needs.
    """
    lowered = (text or "").lower().translate(_TRANSLITERATE)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in decomposed if not unicodedata.combining(c))
```

- [ ] **Step 4: Use it in `_title_tokens`**

Replace line 88 of `src/papertrace/models.py`:

```python
    return set(re.findall(r"[a-z]{5,}", _URL_RE.sub(" ", raw).lower())) - _TITLE_STOPWORDS
```

with:

```python
    # folded, not merely lowercased: `[a-z]{5,}` over raw text drops `Späth`
    # entirely and truncates `Cristóbal` to `crist`
    return set(re.findall(r"[a-z]{5,}", _fold(_URL_RE.sub(" ", raw)))) - _TITLE_STOPWORDS
```

- [ ] **Step 5: Re-export from `refs.py` instead of redefining**

Replace `src/papertrace/refs.py:957-965` (the whole local `_fold` definition, keeping its docstring's information in `models._fold`) with nothing, and add `_fold` to the existing `from .models import (...)` block in `refs.py`.

Verify no other definition survives:

Run: `grep -n "def _fold" src/papertrace/*.py`
Expected: exactly one hit, in `models.py`

- [ ] **Step 6: Run the tests**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_refs.py -v`
Expected: PASS

Run: `~/anaconda3/bin/python3.13 -m pytest -q`
Expected: whole suite green. **If `test_provided_identity.py` or `test_reference_reconciliation.py` change behaviour, do not weaken the new test** — folding makes token sets larger, so a comparison that previously returned `None` for too-few-tokens may now return a real verdict. That is the intended improvement; update the affected test's expectation and record why in its docstring.

Run: `~/anaconda3/bin/python3.13 -m ruff check src tests scripts evals`
Expected: `All checks passed!`

- [ ] **Step 7: Commit (ask the user first)**

```bash
git add src/papertrace/models.py src/papertrace/refs.py tests/test_refs.py
git commit -m "refs: a surname with a diacritic contributes tokens again

_title_tokens was [a-z]{5,} over lowercased text, so Späth and Müller
contributed nothing and Cristóbal became crist. That set is what
titles_match, _same_work and _title_check_text all compare. _fold existed
in refs.py and was not used here; NFKD alone leaves ß, ø, æ, đ and ł."
```

---

### Task 3: A table row in a bibliography is a reference, not a continuation

**Why:** Docling renders real tables as GFM markdown, and a hanging-indent bibliography's numeral column reads as a table. `references_span` appends every block's text regardless of type, so the pipe rows reach `parse_references`. The primary marker regex cannot see them (`\s*` cannot cross `|`), so parsing falls to `_parse_bulleted`, whose line 151-152 is:

```python
elif items and s:
    items[-1] += " " + s  # wrapped continuation of the previous entry
```

Five reference rows were glued onto the previous entry. Worse than a shortfall: `_entry` then scraped the **next** reference's DOI onto the previous entry, and `_title_check` **passed**, because the raw string contained both papers. A wrong-paper verdict with `identity confirmed`.

The real shape, observed in `block_0054` of a case on disk — **note the separator is at line 1, not line 0**, because docling promoted the table's first data row to the markdown header row, and that row is the continuation of the preceding bullet:

```
|     | human aging. Nat Aging. 4(11):1619-1634. https://doi.org/10.1038/s43587-024-00692-2       |
|-----|-------------------------------------------------------------------------------------------|
|  6. | Fujita S, Mori S, Onda K et al (2023) Characterization of Brain Volume Changes in Aging   |
|     | Individuals With Normal Cognition Using Serial Magnetic Resonance Imaging. JAMA Netw      |
```

**Files:**
- Modify: `src/papertrace/refs.py` (add three regexes and `_unwrap_table_rows` above `parse_references` at line 62; add one call as the first statement of `parse_references`)
- Create: `tests/test_reference_tables.py`

**Interfaces:**
- Consumes: nothing
- Produces: `refs._unwrap_table_rows(text: str) -> str` — rewrites GFM reference rows as bullets carrying their printed numeral. Task 4 relies on those numerals reaching `_leading_numeral`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reference_tables.py`:

```python
"""A bibliography the converter rendered as a markdown table.

Docling reads a hanging-indent numeral column as a table and emits GFM. The
rows then reach `parse_references` as text, where the marker regex cannot see
them (`\\s*` cannot cross a `|`) and `_parse_bulleted` treats them as wrapped
continuations — gluing five references onto one entry and scraping the wrong
DOI onto it. The observed shapes here are copied from a real `source_map.json`
table block, including the detail that the separator row is SECOND: docling
promotes the table's first data row to the markdown header row, and that row
is the tail of the bullet above it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.refs import parse_references  # noqa: E402

# 3 flat bullets, then a table holding references 4-7. The table's first row is
# Esteva's continuation, the separator is second, and reference 5 wraps across
# two rows with an empty numeral cell.
TABLE_BIBLIOGRAPHY = """\
- Shen D, Wu G, Suk H-I (2017) Deep learning in medical image analysis. Annu Rev Biomed Eng. 19:221-248. https://doi.org/10.1146/annurev-bioeng-071516-044442
- Litjens G, Kooi T, Bejnordi BE et al (2017) A survey on deep learning in medical image analysis. Med Image Anal. 42:60-88. https://doi.org/10.1016/j.media.2017.07.005
- Esteva A, Kuprel B, Novoa RA et al (2017) Dermatologist-level classification of skin

|     | cancer with deep neural networks. Nature. 542:115-118. https://doi.org/10.1038/nature21056 |
|-----|--------------------------------------------------------------------------------------------|
|  4. | Erickson BJ, Korfiatis P, Akkus Z et al (2017) Machine learning for medical imaging.        |
|     | Radiographics. 37:505-515. https://doi.org/10.1148/rg.2017160130                           |
|  5. | Weston AD, Korfiatis P, Kline TL et al (2019) Automated abdominal segmentation of CT        |
|     | scans for body composition analysis. Radiology. 290:669-679. https://doi.org/10.1148/radiol.2018181432 |
|  6. | Wasserthal J, Breit H-C, Meyer MT et al (2023) TotalSegmentator: robust segmentation of     |
|     | anatomic structures in CT images. Radiol Artif Intell. 5:e230024. https://doi.org/10.1148/ryai.230024 |
|  7. | Graf R, Platzek P, Riedel EO et al (2026) VIBESegmentator: full body MRI segmentation.      |
|     | Eur Radiol. 36:2548-2562. https://doi.org/10.1007/s00330-025-12035-9                        |
"""


def test_a_table_row_is_a_reference_not_a_continuation():
    """Seven printed references must parse as seven entries labelled 1-7."""
    entries = parse_references(TABLE_BIBLIOGRAPHY)
    assert [e.num for e in entries] == ["1", "2", "3", "4", "5", "6", "7"]


def test_a_wrapped_row_with_an_empty_numeral_cell_joins_the_row_above():
    """The wrong-paper route, and the most important assertion in this module.

    Erickson's DOI is printed in a continuation row of Erickson's own entry.
    Glued onto Esteva's entry instead, `_entry` scrapes it, the resolver
    downloads Erickson's paper, and `_title_check` PASSES because the raw
    string now contains both titles. Every claim citing [3] is then judged
    against [4]'s paper with `identity confirmed` printed beside it.
    """
    entries = {e.num: e for e in parse_references(TABLE_BIBLIOGRAPHY)}
    assert entries["3"].doi == "10.1038/nature21056"
    assert entries["4"].doi == "10.1148/rg.2017160130"
    assert "Erickson" not in entries["3"].raw


def test_the_table_separator_row_is_not_reference_text():
    """`|-----|` is layout. Reaching `_slug`, `_title_check` and the Crossref
    bibliographic search as part of a reference string is noise at best."""
    for e in parse_references(TABLE_BIBLIOGRAPHY):
        assert "|---" not in e.raw
        assert "|" not in e.raw


def test_the_tables_first_row_can_be_the_tail_of_the_bullet_above_it():
    """Observed, not hypothetical: docling promotes the first data row to the
    header row, and on the real manuscript that row was reference 5's tail. An
    implementation that skips a header row deletes it."""
    entries = {e.num: e for e in parse_references(TABLE_BIBLIOGRAPHY)}
    assert "Nature. 542:115-118" in entries["3"].raw


def test_a_volume_number_in_a_cell_is_not_a_reference_label():
    """Gate 4. The numeral must be the WHOLE cell: `2017;19` begins with digits
    and is a volume. This module already carries the `a-1061` scar from reading
    an issue number as a year."""
    text = (
        "- Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.1/a\n"
        "\n"
        "| 2017;19 | 221-248 |\n"
        "|---------|---------|\n"
    )
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1"]


def test_a_bibliography_rendered_entirely_as_a_table_is_parsed_with_its_printed_labels():
    """Today this returns [], which reaches the CLI's honest `Exit(1)`. Parsing
    it with the printed numerals is strictly better than refusing."""
    text = (
        "|  1. | Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.1/a |\n"
        "|-----|---------------------------------------------------------------------|\n"
        "|  2. | Litjens G, Kooi T (2017) A survey. Med Image Anal. https://doi.org/10.2/b |\n"
        "|  3. | Esteva A, Kuprel B (2017) Dermatologist-level. Nature. https://doi.org/10.3/c |\n"
    )
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1", "2", "3"]
    assert entries[1].doi == "10.2/b"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_reference_tables.py -v`
Expected: 5 of 6 FAIL. `test_a_volume_number_in_a_cell_is_not_a_reference_label` passes today (nothing parses the cell at all) — it is a gate-4 regression assertion, labelled as such, and must still pass after Step 3.

- [ ] **Step 3: Add the pre-pass**

Insert into `src/papertrace/refs.py` immediately above `parse_references` (line 62):

```python
# A bibliography docling rendered as a markdown table. These rows are CONTENT.
# The opposite rule is right in the body — `cli._body_citation_labels` skips
# table blocks because a results table's `[51, 77]` has the same syntax as a
# citation group — and the two do not conflict because `references_span` has
# already decided this text IS the reference list. Never call this on body text.
_TABLE_SEP_ROW = re.compile(r"^\|[\s:|-]*\|\s*$")
_TABLE_ROW = re.compile(r"^\|(.*)\|\s*$")
# the numeral must be the WHOLE cell: `2017;19` begins with digits and is a
# volume, and this module already has the `a-1061` scar from reading an issue
# number as a year
_NUMERAL_CELL = re.compile(r"^\(?(\d{1,3})\)?\s*[.):\]]?$")


def _unwrap_table_rows(text: str) -> str:
    """Rewrite a bibliography's table rows as bullets, so one reader sees one shape.

    A pre-pass rather than a branch inside `_parse_bulleted`, because that
    function runs only when the primary path found NOTHING: a list half flat
    text and half table would keep the flat half and drop the table half with
    nothing able to notice.

    Emitting a bullet — `- 7. Weston AD, …` — is deliberate. The primary marker
    regex cannot see it (`\\s*` cannot cross `-`), so flat-list behaviour stays
    byte-identical and the table case routes through `_parse_bulleted`, which is
    where the printed-numeral logic already lives.
    """
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if _TABLE_SEP_ROW.match(s):
            out.append("")  # layout, never content — and never glued to an entry
            continue
        m = _TABLE_ROW.match(s)
        if not m:
            out.append(line)
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if cells and (n := _NUMERAL_CELL.match(cells[0])):
            out.append(f"- {int(n.group(1))}. " + " ".join(c for c in cells[1:] if c))
        else:
            # No numeral cell. Two cases, one rule: a wrapped row (`|   | doi:… |`)
            # and a table whose first row is the tail of the bullet above it —
            # which is what docling's header row is. Emitted with no marker, so
            # the existing continuation rule joins it to whatever preceded.
            out.append("  " + " ".join(c for c in cells if c))
    return "\n".join(out)
```

- [ ] **Step 4: Call it once, at the top of `parse_references`**

Insert as the first statement of `parse_references` (before `marker = re.compile(...)` at line 80):

```python
    text = _unwrap_table_rows(text)  # a pipe row in a bibliography is a reference
```

- [ ] **Step 5: Run the tests**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_reference_tables.py -v`
Expected: all 6 PASS

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_refs.py tests/test_reference_list.py tests/test_reference_reconciliation.py -q`
Expected: green — flat bibliographies must be unaffected

Run: `~/anaconda3/bin/python3.13 -m pytest -q && ~/anaconda3/bin/python3.13 -m ruff check src tests scripts evals`
Expected: whole suite green, `All checks passed!`

- [ ] **Step 6: Verify against the real artifact**

Run:

```bash
~/anaconda3/bin/python3.13 - <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, "/Users/danielgutmann/Developer/PaperTrace/src")
from papertrace.models import SourceMap
from papertrace.ingest.pymupdf_ import references_span
from papertrace.refs import parse_references

p = Path("/Users/danielgutmann/Desktop/EuroRad_Review2/EURA-D-26-03681R1_Manuscript_ANNOTATED/ingest/manuscript/source_map.json")
text, _ = references_span(SourceMap.from_json(p))
entries = parse_references(text)
print(f"entries: {len(entries)}  labels: {[e.num for e in entries]}")
EOF
```

Expected: **22 entries labelled 1-22** (before this task: 18, with the labels wrong from [6] on). Paste the output into the PR description — evidence before assertions.

- [ ] **Step 7: Commit (ask the user first)**

```bash
git add src/papertrace/refs.py tests/test_reference_tables.py
git commit -m "refs: a table row in a bibliography is a reference, not a continuation

Docling reads a hanging-indent numeral column as a table and emits GFM.
_parse_bulleted treated the pipe rows as wrapped continuations, gluing five
references onto one entry — and _entry then scraped the NEXT reference's DOI
onto it, which _title_check passed because the raw string held both papers.
On the manuscript that surfaced this, 22 references parsed as 18 and every
claim citing [6] or above was judged against a different paper."
```

---

### Task 4: Printed numerals that contradict their position refuse to be numbered

**Why:** `_usable_printed_numerals` requires `seen[0] == 1`:

```python
seen = [n for n in numerals if n is not None]
if len(seen) < 2 or seen[0] != 1:
    return False
```

Docling stripped the numerals from the bullets and kept them only inside the table, so no printed `1` existed anywhere. Measured: `[None]*5 + [6,7,8,9,10]` → `False`. The caller then numbers by position:

```python
return [_entry(str(i), _strip_printed_numeral(raw)) for i, raw in enumerate(items, 1)]
```

Its docstring licenses that for "lists that genuinely carry no numerals". Both of that clause's factual claims were false here: the list *did* carry numerals, and position-vs-printed agreement *was* available and unread. Task 3 makes the numerals reach this function; this task makes the function ask the right question of them.

**Files:**
- Modify: `src/papertrace/refs.py:155-168` (the tail of `_parse_bulleted`); add `_numerals_agree_with_position` beside `_usable_printed_numerals` at line 176
- Modify: `tests/test_reference_tables.py`

**Interfaces:**
- Consumes: `refs._unwrap_table_rows` from Task 3
- Produces: `refs._numerals_agree_with_position(numerals: list[int | None]) -> bool`. `RefEntry.boundary_ambiguous` is set on entries from the first contradiction onward, so `resolve_entry` refuses them (existing behaviour at `refs.py:1467-1469`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reference_tables.py`:

```python
from papertrace.refs import _numerals_agree_with_position, _parse_bulleted  # noqa: E402


def test_numerals_surviving_on_only_some_items_still_fix_the_labels():
    """A list docling numbered half as bullets (numerals stripped) and half as
    a table (numerals kept) prints no [1], so `_usable_printed_numerals`
    refused it and the caller numbered by position — the guess it exists to
    avoid making unchecked. Where the numerals that ARE printed sit at the
    positions they name, position is the printed reading, corroborated.
    """
    assert _numerals_agree_with_position([None, None, 3]) is True
    assert _numerals_agree_with_position([1, None, 3, 4]) is True
    assert _numerals_agree_with_position([None, None, 9, 10, 11]) is False
    assert _numerals_agree_with_position([2, 3]) is False


def test_printed_numerals_contradicting_their_position_are_not_numbered_by_position():
    """Gate 4. Numerals 9-11 at positions 3-5 mean something above them was
    merged or split, so position is KNOWN wrong rather than merely unverified.
    Neither reading is available: refuse, and refuse to resolve."""
    text = (
        "- Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.1/a\n"
        "- Litjens G, Kooi T (2017) A survey. Med Image Anal. https://doi.org/10.2/b\n"
        "- 9. Esteva A (2017) Dermatologist-level. Nature. https://doi.org/10.3/c\n"
        "- 10. Erickson BJ (2017) Machine learning. Radiographics. https://doi.org/10.4/d\n"
        "- 11. Weston AD (2019) Automated abdominal. Radiology. https://doi.org/10.5/e\n"
    )
    entries = _parse_bulleted(text)
    # The nums stay positional — an entry needs some label to be a dict key and
    # a slug. What changes is that they are no longer *trusted*: every entry
    # from the first contradiction on refuses to resolve, so no claim is judged
    # against a paper this numbering picked.
    affected = [e for e in entries if e.boundary_ambiguous]
    assert affected, "a contradicted numbering must be refused, not relabelled"
    assert all(e.doi is None for e in affected)
    assert all("numbering" in e.reason for e in affected)


def test_a_list_printing_no_numeral_anywhere_is_still_numbered_by_position():
    """Gate 4, the other half, and an explicit regression assertion: the
    Nature-family case where the converter really did strip them is the one
    situation in which position is the only reading available. It keeps
    working, and `reconcile` still marks it unverified."""
    text = (
        "- Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.1/a\n"
        "- Litjens G, Kooi T (2017) A survey. Med Image Anal. https://doi.org/10.2/b\n"
    )
    entries = _parse_bulleted(text)
    assert [e.num for e in entries] == ["1", "2"]
    assert not any(e.boundary_ambiguous for e in entries)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_reference_tables.py -k "numerals or contradicting or no_numeral" -v`
Expected: first two FAIL (`ImportError` for `_numerals_agree_with_position`, then the relabelling assertion); the third PASSES today and must keep passing.

- [ ] **Step 3: Add the position check**

Insert into `src/papertrace/refs.py` immediately after `_usable_printed_numerals` (after line 189):

```python
def _numerals_agree_with_position(numerals: list[int | None]) -> bool:
    """Does every printed numeral equal its item's position in the list?

    The one question that can be asked of a PARTLY numbered list, and a
    generalisation of `_usable_printed_numerals`' `seen[0] == 1` rather than a
    weakening of it: when item 1 carries a numeral, its ordinal IS 1.

    Where the numerals that survived the converter sit at exactly the positions
    they name, the positional reading is not a guess — it is the printed
    reading, corroborated wherever the printing survived. Where they disagree,
    something above them was merged or split and neither reading is available.
    """
    return all(n is None or n == i for i, n in enumerate(numerals, 1))
```

- [ ] **Step 4: Replace the two-way tail with a three-way one**

Replace `src/papertrace/refs.py:168` (the single positional `return`) with:

```python
    printed = [n for n in numerals if n is not None]

    if printed and _numerals_agree_with_position(numerals):
        # positional, and corroborated by every numeral that survived
        return [_entry(str(i), _strip_printed_numeral(raw)) for i, raw in enumerate(items, 1)]

    if printed:
        # Printed numerals exist and CONTRADICT their positions, so an entry
        # above them was merged or split: the printed reading is unavailable and
        # the positional one is known wrong. Refuse from the first disagreement
        # on, reusing `boundary_ambiguous` — whose documented meaning is already
        # "nothing derived from this raw may be trusted to name a paper", and
        # which `resolve_entry` honours by returning `no_doi` without fetching.
        first = next(i for i, n in enumerate(numerals, 1) if n is not None and n != i)
        out: list[RefEntry] = []
        for i, raw in enumerate(items, 1):
            e = _entry(str(i), _strip_printed_numeral(raw))
            if i >= first:
                e.boundary_ambiguous = True
                e.doi = None
                e.reason = (
                    f"printed numbering contradicts position from entry {first} on — the "
                    f"converter kept a numeral reading [{numerals[first - 1]}] at position "
                    f"{first}, so an entry above it was merged or split; not resolved rather "
                    "than risk judging a claim against the wrong paper"
                )
            out.append(e)
        return out

    # No numeral anywhere: the Nature-family case the docstring describes, where
    # position really is the only reading available. `reconcile` marks it unverified.
    return [_entry(str(i), _strip_printed_numeral(raw)) for i, raw in enumerate(items, 1)]
```

- [ ] **Step 5: Run the tests**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_reference_tables.py -v`
Expected: all PASS

Run: `~/anaconda3/bin/python3.13 -m pytest -q && ~/anaconda3/bin/python3.13 -m ruff check src tests scripts evals`
Expected: whole suite green, `All checks passed!`

- [ ] **Step 6: Commit (ask the user first)**

```bash
git add src/papertrace/refs.py tests/test_reference_tables.py
git commit -m "refs: printed numerals that contradict their position refuse to be numbered

_usable_printed_numerals required seen[0] == 1, so a list whose converter
stripped the early numerals and kept the later ones was refused wholesale and
then numbered by position anyway. Its docstring licensed that for lists which
'genuinely carry no numerals'; here the list carried ten, and
position-vs-printed agreement was available and unread."
```

---

### Task 5: Report which numerals were dropped, and surface a contested numbering

**Why:** Two separate honesty defects.

First, the note reports a bare count. On the real run it said *"the manuscript cites [1]-[19], the parsed list has 18 references"* — a difference of one, where the truth was 5 references dropped and 1 numeral duplicated. `_covers` already computes the gap set (`body <= nums`) and throws it away.

Second, `Reconciliation.contested` is declared at `refs.py:496` with the comment *"burying it in a field no template renders was how a compensating parse error could pass unmentioned"* — and then `cli.py:707` does not persist it, no `RefManifest` field holds it, and `disclosures._numbering` is gated on `not numbering_verified`. So a **contested-but-verified** run discloses nothing in any format. `tests/test_reference_reconciliation.py:882` asserts the flag with the comment *"the deposit disagreed and nobody is told"* — it pins the flag, not the report.

**Files:**
- Modify: `src/papertrace/refs.py` (`Reconciliation` gains `ledger`; `reconcile` computes it and uses it in the note)
- Modify: `src/papertrace/models.py` (`RefManifest` gains `numbering_contested` and `numbering_ledger`; `to_json`/`from_json`)
- Modify: `src/papertrace/cli.py:707-711` (persist both)
- Modify: `src/papertrace/disclosures.py` (ungate a contested disclosure)
- Modify: `schemas/refs_manifest.schema.json`
- Modify: `src/papertrace/templates/report_terminal.html.j2` (one branch — the other three use run-level catch-alls)
- Test: `tests/test_reference_reconciliation.py`

**Interfaces:**
- Consumes: Tasks 3 and 4
- Produces: `RefManifest.numbering_contested: bool`, `RefManifest.numbering_ledger: dict`, and disclosure key `"numbering_contested"`. Plan B adds `numbering_corroborated` beside them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reference_reconciliation.py`:

```python
def test_the_note_names_the_dropped_and_duplicated_numerals():
    """A difference of two totals understated the real damage sixfold: the run
    that surfaced this said '[1]-[19] vs 18 references' where 5 references were
    dropped and 1 numeral duplicated. `_covers` computes the gap set already."""
    body = {str(i) for i in range(1, 8)}
    parsed = [
        _e("1"), _e("2"), _e("3"), _e("7"), _e("7"),
    ]
    _entries, rec = reconcile(body, None, parsed, crossref_absent="no DOI")
    assert rec.ledger["numerals_absent"] == ["4", "5", "6"]
    assert rec.ledger["numerals_duplicated"] == ["7"]
    assert "4" in rec.note and "5" in rec.note and "6" in rec.note
    assert "7" in rec.note


def test_a_contested_but_verified_numbering_reaches_every_format(tmp_path):
    """`contested` has never reached a reader. It is declared with a comment
    saying that burying it was how a compensating parse error passed
    unmentioned, and it is then buried: never persisted, and `_numbering` is
    gated on `not verified` so a verified-but-contested run says nothing."""
    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[_e("1"), _e("2")],
        numbering_verified=True,
        numbering_contested=True,
    )
    results = _results()
    reports = _render_with_manifest(results, manifest, tmp_path)
    for name, text in reports.items():
        assert "second reading of the reference list disagreed" in text, name


def test_the_ledger_round_trips_and_older_manifests_still_load(tmp_path):
    """Gate 2."""
    m = RefManifest(
        manuscript="m.pdf",
        entries=[_e("1")],
        numbering_contested=True,
        numbering_ledger={"numerals_absent": ["4"], "numerals_duplicated": []},
    )
    p = tmp_path / "refs_manifest.json"
    p.write_text(json.dumps(m.to_json()))
    back = RefManifest.from_json(p)
    assert back.numbering_contested is True
    assert back.numbering_ledger["numerals_absent"] == ["4"]

    old = {"manuscript": "m.pdf", "entries": [{"num": "1", "raw": "x", "status": "paywalled"}]}
    p.write_text(json.dumps(old))
    older = RefManifest.from_json(p)
    assert older.numbering_contested is False
    assert older.numbering_ledger == {}
```

Reuse the module's existing `_e`, `_results` and render helpers; add `_render_with_manifest` if the module has no helper that passes a manifest to `write_reports`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_reference_reconciliation.py -k "ledger or contested" -v`
Expected: FAIL — `TypeError: RefManifest.__init__() got an unexpected keyword argument 'numbering_contested'`

- [ ] **Step 3: Add the ledger to `Reconciliation` and compute it**

In `src/papertrace/refs.py`, add to `Reconciliation` (after `parsed_count`, line 501):

```python
    # Which numerals are missing and which are duplicated, by name. A difference
    # of two totals cannot reveal a duplicate at all, and a merge-plus-split
    # keeps the count — the case `_covers` documents itself as blind to.
    ledger: dict = field(default_factory=dict)
```

In `reconcile`, after `cited = max(...)` (line 604):

```python
    nums = [e.num for e in parsed]
    counts = Counter(nums)
    rec.ledger = {
        "labels_detected": len(body_labels),
        "labels_max": cited,
        "labels_absent": sorted(
            (str(i) for i in range(1, cited + 1) if str(i) not in body_labels), key=int
        ),
        "entries_parsed": len(parsed),
        "numerals_distinct": len(counts),
        "numerals_duplicated": sorted((n for n, c in counts.items() if c > 1), key=int),
        "numerals_absent": sorted(
            (str(i) for i in range(1, cited + 1) if str(i) not in counts), key=int
        ),
    }
```

Add `from collections import Counter` and `field` to the imports if absent.

- [ ] **Step 4: Use the ledger in the note**

Replace the `detail` assignment in `reconcile`'s failure branch (around line 681):

```python
    detail = f"the manuscript cites [1]-[{cited}], the parsed list has {len(parsed)} references"
```

with:

```python
    led = rec.ledger
    detail = (
        f"the body cites {led['labels_detected']} distinct labels up to [{cited}]; "
        f"the parsed list holds {led['entries_parsed']} entries carrying "
        f"{led['numerals_distinct']} distinct printed numerals"
    )
    if led["numerals_duplicated"]:
        detail += " — [" + "], [".join(led["numerals_duplicated"]) + "] appear more than once"
    if led["numerals_absent"]:
        detail += " — [" + "], [".join(led["numerals_absent"]) + "] are carried by no entry"
```

- [ ] **Step 5: Persist both on the manifest**

In `src/papertrace/models.py`, add to `RefManifest` after `unverified_from`:

```python
    # A second reading called the list wrong even though the chosen one matched
    # the body's labels. `_covers` tests extent, not content, so this is worth
    # the reader's eye even when the count checks out.
    numbering_contested: bool = False
    # Absent means never computed, NOT "nothing was dropped".
    numbering_ledger: dict = field(default_factory=dict)
```

Add both to `to_json` and read them in `from_json` with `.get("numbering_contested", False)` and `.get("numbering_ledger", {})`.

In `src/papertrace/cli.py:707-711`, add to the `RefManifest(...)` construction:

```python
        numbering_contested=rec.contested,
        numbering_ledger=rec.ledger,
```

- [ ] **Step 6: Disclose it**

In `src/papertrace/disclosures.py`, add the token beside the others:

```python
NUMBERING_CONTESTED_TOKEN = "second reading of the reference list disagreed"
```

and a producer that is **not** gated on `numbering_verified`:

```python
def _numbering_contested(manifest) -> Disclosure | None:
    """Fires whether or not the numbering was verified.

    `_numbering` is gated on `not verified`, so a contested-but-verified run
    disclosed nothing in any format — which is the compensating-parse-error case
    `Reconciliation.contested` was added to catch.
    """
    if not getattr(manifest, "numbering_contested", False):
        return None
    return Disclosure(
        key="numbering_contested",
        level="warn",
        token=NUMBERING_CONTESTED_TOKEN,
        text=(
            f"A {NUMBERING_CONTESTED_TOKEN} — the reading used here accounts for exactly "
            "the labels the body cites, but another reading of the same bibliography named "
            "different papers. An extent check cannot see a parse that merges one pair of "
            "references and splits another, so check the retrieval manifest against the "
            "paper's own reference list before relying on a verdict."
        ),
        short=f"{NUMBERING_CONTESTED_TOKEN} — extent matched, content did not",
    )
```

Call it from `run_disclosures` beside `_numbering`.

- [ ] **Step 7: Add the terminal branch**

`report.md.j2` and `report_editor.html.j2` render run-level disclosures through a catch-all and need no edit. `report_terminal.html.j2` is an allow-list — add after the `numbering` branch (line 100-102), matching its markup exactly:

```jinja
{% for d in disclosures if d.key == "numbering_contested" %}
    <div class="row"><span class="step">▸ resolve</span><span class="amber">⚠ {{ d.short }}</span></div>
{% endfor %}
```

- [ ] **Step 8: Update the schema (gate 2)**

In `schemas/refs_manifest.schema.json`, add to the manifest-level `properties` — **not** to `required`:

```json
"numbering_contested": {
  "type": "boolean",
  "description": "A second reading disagreed about which papers the list names, even though the chosen reading matched the body's labels. Absent on manifests written before 0.7."
},
"numbering_ledger": {
  "type": "object",
  "description": "Which numerals were dropped or duplicated, by name. Absent means never computed, not that nothing was dropped."
}
```

- [ ] **Step 9: Run everything**

Run: `~/anaconda3/bin/python3.13 -m pytest tests/test_reference_reconciliation.py tests/test_disclosure_parity.py -v`
Expected: PASS, including the parity loop picking up the new key

Run: `~/anaconda3/bin/python3.13 -m pytest -q && ~/anaconda3/bin/python3.13 -m ruff check src tests scripts evals`
Expected: whole suite green, `All checks passed!`

- [ ] **Step 10: Commit (ask the user first)**

```bash
git add src/papertrace/refs.py src/papertrace/models.py src/papertrace/cli.py \
        src/papertrace/disclosures.py src/papertrace/templates/report_terminal.html.j2 \
        schemas/refs_manifest.schema.json tests/test_reference_reconciliation.py
git commit -m "refs: name the dropped numerals, and tell the reader a numbering is contested

The note reported a difference of two totals, which understated a real run
sixfold — '[1]-[19] vs 18 references' where five references were dropped and
one numeral duplicated. _covers already computed the gap set and discarded it.

Reconciliation.contested was declared with a comment saying that burying it
was how a compensating parse error passed unmentioned, and was then never
persisted and never rendered."
```

---

## Self-Review

**Spec coverage.** Plan A covers the spec's §"Why" causes 1-3, the `_title_tokens` latent bug, the ledger, `contested`, and PR 0's parity guard. Deferred to Plan B, deliberately and by name: `ask.py` extraction, the pymupdf candidate, `label_agreement`, withholding in `check.py`, `reflist.py`, the interactive escalation, `numbering_corroborated`, the `claim_pairing` disclosure, the `LABEL_AGREEMENT` vocabulary, and the eval task.

**Placeholder scan.** No TBD, no "handle edge cases", no "similar to Task N". Every code step carries the actual code.

**Type consistency.** `_unwrap_table_rows(text: str) -> str` (Task 3) is consumed by `parse_references` only. `_numerals_agree_with_position(list[int | None]) -> bool` (Task 4) is consumed by `_parse_bulleted` only. `Reconciliation.ledger: dict` (Task 5) flows to `RefManifest.numbering_ledger: dict` — same type, different name, deliberately: one is per-reconciliation, the other is persisted. `boundary_ambiguous`, `doi` and `reason` in Task 4 are existing `RefEntry` fields.

**One gap found and accepted.** Task 5's ledger uses `cited = max(body_labels)`, which is structurally a floor because `cli._body_citation_labels` skips table blocks. Measured on the manuscript that surfaced this bug: no label appears only in a table and the label set is contiguous, so the hazard did not bite. A `labels_uncountable` flag is specified in the design but deferred to Plan B, where `numbering_corroboration` gives it somewhere to be reported.
