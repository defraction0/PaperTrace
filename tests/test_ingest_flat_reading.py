"""A second, independent reading of the bibliography — no ingest, no write.

`references_span_flat` exists to give `reconcile` a plain pymupdf reading of
the References section, cheap enough to run on every docling run without
asking permission. Neither existing ingest test module fits what this needs
checked: `test_ingest_backends.py` stubs docling and never touches a real PDF
or pymupdf, and `test_reference_list.py` hand-builds `SourceMap`s in memory.
This module's central claim — a real file read, and NOTHING written back to
disk — needs a real path and a real `cwd` to check at all.
"""

import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 module name (the bare `fitz` import is deprecated)
except ImportError:  # pragma: no cover - older PyMuPDF exposes only `fitz`
    import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.ingest import references_span_flat  # noqa: E402
from papertrace.refs import parse_references  # noqa: E402


def _paper(path: Path, *, with_references: bool) -> Path:
    """A two-page PDF: page 1 is body text, page 2 is a References section
    with three numbered entries, or an unrelated heading when
    `with_references` is False."""
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 100), "A Study of Something", fontsize=16)
    page1.insert_text((72, 140), "Body text citing [1], [2] and [3] here.", fontsize=11)
    page2 = doc.new_page()
    if with_references:
        page2.insert_text((72, 100), "References", fontsize=14)
        page2.insert_text((72, 130), "[1] Alpha A. A first paper. 2019.", fontsize=11)
        page2.insert_text((72, 150), "[2] Beta B. A second paper. 2020.", fontsize=11)
        page2.insert_text((72, 170), "[3] Gamma C. A third paper. 2021.", fontsize=11)
    else:
        page2.insert_text((72, 100), "Acknowledgements", fontsize=14)
        page2.insert_text((72, 130), "We thank nobody in particular.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_the_flat_reading_holds_the_printed_entries_and_parses_to_the_same_count(tmp_path):
    """The whole point of a second reading: it must actually see the
    bibliography, not merely avoid crashing on one."""
    pdf = _paper(tmp_path / "paper.pdf", with_references=True)

    text, resumed = references_span_flat(pdf)

    assert resumed is False
    for surname in ("Alpha", "Beta", "Gamma"):
        assert surname in text, f"{surname} missing from the flat reading — {text!r}"
    assert len(parse_references(text)) == 3


def test_a_paper_with_no_references_heading_degrades_honestly(tmp_path):
    """No heading found means `(\"\", False)` — never an exception, and never
    a guess at where the list might be."""
    pdf = _paper(tmp_path / "paper.pdf", with_references=False)

    assert references_span_flat(pdf) == ("", False)


def test_the_flat_reading_writes_nothing_to_disk(tmp_path, monkeypatch):
    """This is a READING, not an ingest. `--parse-only` already carries the
    scar of a second read touching the case's real `source_map.json`
    (`cli.py:543-549`) — a reading taken purely to vote on a label must not
    risk doing that even by accident, so it never calls `write_outputs`, never
    takes an `out_dir`, and never touches the cwd it happens to run in."""
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "sources" / "paper.pdf", with_references=True)

    references_span_flat(pdf)

    assert list(tmp_path.rglob("source_map.json")) == []
    assert list(tmp_path.rglob("clean.md")) == []
    assert list(tmp_path.rglob("annotated.md")) == []
