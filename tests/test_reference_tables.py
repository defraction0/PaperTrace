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
        "|  1. | Shen D, Wu G (2017) Deep learning. Annu Rev. https://doi.org/10.0001/a |\n"
        "|-----|-----------------------------------------------------------------------|\n"
        "|  2. | Litjens G, Kooi T (2017) A survey. Med Image Anal. https://doi.org/10.0002/b |\n"
        "|  3. | Esteva A, Kuprel B (2017) Dermatologist-level. Nature. https://doi.org/10.0003/c |\n"
    )
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1", "2", "3"]
    assert entries[1].doi == "10.0002/b"
