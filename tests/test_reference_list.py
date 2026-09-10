"""Where the reference list starts, and where it really ends.

A real Elsevier pre-proof put refs 1-9 on page 7, then a `Declaration of
interests` section, then refs 10-15 on page 8. `references_section` stops at the
next section header — the guard that stops the reference list swallowing the
rest of the paper — so six references were never parsed and never retrieved. The
audit reported 9 references on a paper that cites 15, with nothing saying so.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.ingest.pymupdf_ import references_section, references_span  # noqa: E402
from papertrace.models import Block, SourceMap  # noqa: E402


def _map(*blocks: tuple[str, int, str]) -> SourceMap:
    """(type, page, text) triples → a SourceMap, ids in order."""
    return SourceMap(
        doc="p.pdf", pages=max(p for _, p, _ in blocks), converter="docling 2.123.1",
        blocks=[
            Block(id=f"block_{i:04d}", type=t, page=p, bbox=(0, 0, 10, 10),
                  heading_path=[], text=x)
            for i, (t, p, x) in enumerate(blocks, start=1)
        ],
    )


# the real shape, reduced: nine entries, an interrupting section, six more
_SPLIT = _map(
    ("sectionheader", 7, "Conclusion"),
    ("text", 7, "The narrative surrounding AI in radiology has often centered on replacement."),
    ("sectionheader", 7, "References"),
    ("list", 7, "- Jing, A. B., Garg, N. & Brown, J. J. AI solutions to the radiology gap. 2025."),
    ("list", 7, "- Tejani, A. S., Cook, T. S. & Hussain, M. Integrating AI. Radiology 2024."),
    ("list", 7, "- Korfiatis, P. et al. Implementing artificial intelligence algorithms. 2025."),
    ("sectionheader", 8, "Declaration of interests"),
    ("text", 8, "☒ The authors declare that they have no known competing interests."),
    ("sectionheader", 8, "Journal Pre-proofs"),
    ("list", 8, "- Dean, G. et al. Real-world monitoring of AI in radiology. 2025."),
    ("list", 8, "- Assess-AI algorithm performance monitoring. https://www.acr.org/Data-Science"),
    ("list", 8, "- Kitamura, F. et al. Teaching AI for Radiology applications. 2025."),
    ("text", 8, "_"),
)


def test_a_reference_list_split_by_a_section_is_parsed_whole():
    """The reported defect. Six of nine entries here sit after an interrupting
    `Declaration of interests` section, and stopping at the first header lost
    them — six sources never even attempted for retrieval."""
    text = references_section(_SPLIT)

    for surname in ("Jing", "Tejani", "Korfiatis", "Dean", "Assess-AI", "Kitamura"):
        assert surname in text, f"{surname} missing — the list was cut short"
    assert text.count("- ") == 6, text


def test_the_interrupting_section_is_not_pulled_into_the_list():
    """`_parse_bulleted` glues a non-bullet line onto the previous entry, so a
    stray prose block does not merely add noise — it corrupts a reference."""
    text = references_section(_SPLIT)

    assert "competing interests" not in text
    assert "Declaration" not in text
    assert "Journal Pre-proofs" not in text
    assert "narrative surrounding" not in text


def test_resuming_across_a_section_break_is_reported():
    """Crossing a section boundary to continue a list is a guess the reader
    should be able to check, so the fact travels with the text."""
    _, resumed = references_span(_SPLIT)
    assert resumed is True

    plain = _map(
        ("sectionheader", 7, "References"),
        ("list", 7, "- Jing, A. B. AI solutions to the radiology gap. 2025."),
        ("list", 7, "- Tejani, A. S. Integrating AI. Radiology 2024."),
    )
    _, resumed = references_span(plain)
    assert resumed is False, "an ordinary list must not claim it was resumed"


def test_prose_after_the_references_is_still_never_swallowed():
    """The guard this defect sits behind. Text following the reference list must
    stay out, whatever its position — that is what stops "References were
    checked by hand" taking the rest of the paper with it."""
    m = _map(
        ("sectionheader", 7, "References"),
        ("list", 7, "- Jing, A. B. AI solutions to the radiology gap. 2025."),
        ("sectionheader", 8, "Appendix A"),
        ("text", 8, "References were checked by hand against the originals."),
        ("text", 8, "Further discussion of the cohort follows in three paragraphs."),
    )
    text = references_section(m)
    assert "Jing" in text
    assert "checked by hand" not in text
    assert "Further discussion" not in text


def test_a_single_stray_item_does_not_resume_the_list():
    """One list block after an unrelated heading is far more likely to be a
    bulleted sentence than the tail of a bibliography, so it takes a run."""
    m = _map(
        ("sectionheader", 7, "References"),
        ("list", 7, "- Jing, A. B. AI solutions to the radiology gap. 2025."),
        ("list", 7, "- Tejani, A. S. Integrating AI. Radiology 2024."),
        ("sectionheader", 8, "Acknowledgements"),
        ("list", 8, "- Supported by a grant from the institute, 2019."),
    )
    text, resumed = references_span(m)
    assert "Supported by a grant" not in text, text
    assert resumed is False


def test_a_differently_typed_run_does_not_resume_the_list():
    """The reference blocks were `list`; a following run of `text` blocks is a
    different structure and must not be appended to the bibliography."""
    m = _map(
        ("sectionheader", 7, "References"),
        ("list", 7, "- Jing, A. B. AI solutions to the radiology gap. 2025."),
        ("list", 7, "- Tejani, A. S. Integrating AI. Radiology 2024."),
        ("sectionheader", 8, "Appendix"),
        ("text", 8, "The first supplementary consideration, discussed in 2020."),
        ("text", 8, "The second supplementary consideration, discussed in 2021."),
    )
    text, resumed = references_span(m)
    assert "supplementary consideration" not in text, text
    assert resumed is False


# --- back matter is not a continued bibliography ----------------------------
#
# The first real audit of an Elsevier paper reported 46 references on a paper
# citing 43. Blocks 120-162 were the references; block 163 was a `TABLE TITLES`
# heading; blocks 164-166 were three `list` blocks holding the paper's own table
# captions. The resume scan runs to the END of the document and accepted them,
# because the only test it applied was the block *type*. Refs 44-46 were then
# title-searched against Crossref, which answered with table-component DOIs from
# unrelated papers, and the report published three works that do not exist.

_TABLE_TITLES = _map(
    ("sectionheader", 22, "REFERENCES"),
    ("list", 22, "- G.C. Feuerriegel, R.P. Marcus, S. Sommer, Rotator cuff. Eur Radiol 2023."),
    ("list", 22, "- D.A. Lansdown, S. Lee, C. Sam, A prospective quantitative study. 2017."),
    ("list", 22, "- W.T. Dixon, Simple proton spectroscopic imaging, Radiology 153 (1984) 189-194."),
    ("sectionheader", 26, "TABLE TITLES"),
    ("list", 26, "- Table 1. Dataset characteristics"),
    ("list", 26, "- Table 2. Accuracy and reliability of automated thresholding models"),
    ("list", 26, "- Table 3. Diagnostic accuracy for clinical cutoffs of Goutallier"),
)


def test_table_captions_after_the_references_are_not_references():
    """Three list blocks under `TABLE TITLES` share the reference list's block
    type and nothing else. None carries a year, a DOI or an arXiv id."""
    text, resumed = references_span(_TABLE_TITLES)

    assert "Table 1." not in text, "the paper's own table captions became references"
    assert "Table 2." not in text
    assert "Table 3." not in text
    assert resumed is False, "nothing was resumed, so nothing should be reported as resumed"
    assert text.count("- ") == 3, text


def test_the_real_references_survive_the_shape_test():
    """The other half of the same assertion: rejecting back matter must not
    reject the bibliography it follows."""
    text, _ = references_span(_TABLE_TITLES)
    for surname in ("Feuerriegel", "Lansdown", "Dixon"):
        assert surname in text, f"{surname} was lost to the shape test"


def test_a_genuine_continuation_still_resumes_when_one_entry_lacks_a_year():
    """The shape test is applied to the RUN, not to each entry. `_SPLIT`'s
    resumed run holds two dated references and one URL-only entry; requiring
    every entry to be reference-shaped would undo the 0.4.0 fix over the one
    entry that is a bare link."""
    text, resumed = references_span(_SPLIT)

    assert resumed is True
    assert "Dean" in text and "Kitamura" in text
    assert "Assess-AI" in text, "the year-less entry in a real run was dropped"
