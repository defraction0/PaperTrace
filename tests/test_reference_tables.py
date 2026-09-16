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

from papertrace.refs import (  # noqa: E402
    _covers,
    _entry,
    _numerals_agree_with_position,
    _parse_bulleted,
    parse_references,
    reconcile,
)

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
        "|  1. | Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.0001/a |\n"
        "|-----|-----------------------------------------------------------------------|\n"
        "|  2. | Litjens G, Kooi T (2017) A survey. Med Image Anal. https://doi.org/10.0002/b |\n"
        "|  3. | Esteva A, Kuprel B (2017) Dermatologist-level. Nature. https://doi.org/10.0003/c |\n"
    )
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1", "2", "3"]
    assert entries[1].doi == "10.0002/b"


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


def test_a_refused_numbering_is_never_confirmed_by_a_matching_count():
    """The refuse branch and `_covers` compose into a lie.

    A refused entry keeps a positional label — it needs one to be a dict key and
    a slug — so the parsed list still carries `{1..N}`, `_covers`' subset test
    cannot bite and only its length test can. A five-item list refused from
    entry 4 on, against a body citing [1]-[5], therefore reported `verified` and
    fired neither the run-level `numbering` disclosure nor a single
    `claim_numbering` caveat, while `refs.py` wrote "printed numbering
    contradicts position from entry 4 on" onto two of the entries in the same
    run. An extent check cannot confirm a numbering the parser refused.
    """
    text = (
        "- Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.1/a\n"
        "- Litjens G, Kooi T (2017) A survey. Med Image Anal. https://doi.org/10.2/b\n"
        "- Esteva A (2017) Dermatologist-level. Nature. https://doi.org/10.3/c\n"
        "- 9. Erickson BJ (2017) Machine learning. Radiographics. https://doi.org/10.4/d\n"
        "- 10. Weston AD (2019) Automated abdominal. Radiology. https://doi.org/10.5/e\n"
    )
    entries = _parse_bulleted(text)
    assert [e.num for e in entries] == ["1", "2", "3", "4", "5"]
    assert [e.num for e in entries if e.boundary_ambiguous] == ["4", "5"]

    body = {"1", "2", "3", "4", "5"}
    assert _covers(body, entries), "the count agrees — which is the whole trap"
    _chosen, rec = reconcile(body, None, entries)

    assert rec.verified is False, "the parser refused these labels; a count cannot confirm them"
    assert rec.unverified_from == 4, "the first refused entry is where the doubt starts"
    assert "[4], [5]" in rec.note


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


def test_a_trailing_run_of_stripped_numerals_is_accepted_but_never_verified():
    """The shape the real manuscript actually produces, pinned.

    Docling keeps the numerals it found inside a table and strips them from
    every bullet after it, so the printed run stops partway:
    [None x5, 6..11, None x13]. A None agrees with any position, so the labels
    are accepted rather than refused — the contradiction branch never fires.

    That is the honest answer only because nothing calls it verified. Two
    split-title fragments are still counted as entries, so the list is longer
    than the highest label the body cites and `_covers` fails. This test exists
    so a later change cannot quietly promote acceptance into confirmation.
    """
    numerals = [None] * 5 + [6, 7, 8, 9, 10, 11] + [None] * 13
    assert _numerals_agree_with_position(numerals) is True

    body = {str(i) for i in range(1, 20)}  # the body cites [1]-[19]
    entries = [
        _entry(str(i), f"Author {i} A (2020) A paper title. J Test. https://doi.org/10.1000/x{i}")
        for i in range(1, 25)  # the parse holds 24, two of them fragments
    ]
    assert not _covers(body, entries)
    _chosen, rec = reconcile(body, None, entries, crossref_absent="no DOI for the paper")
    assert rec.verified is False
