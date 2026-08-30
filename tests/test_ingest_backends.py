"""Backend dispatch + docling adapter mapping, tested against stubs.

docling itself is not required: `blocks_from_docling` is duck-typed, so the
label mapping, the GFM table passthrough, the figure captions and — most
importantly — the bottom-left → top-left bbox conversion are verified here
without the heavyweight dependency installed.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.highlight import crop_evidence  # noqa: E402
from papertrace.ingest import ingest_pdf, write_outputs  # noqa: E402
from papertrace.ingest.docling_ import (  # noqa: E402
    _to_top_left,
    blocks_from_docling,
    hyphen_joins,
    restore_hyphen_joins,
)
from papertrace.models import Block, SourceMap  # noqa: E402

try:
    import pymupdf as fitz  # noqa: E402
except ImportError:  # pragma: no cover — older PyMuPDF exposes only `fitz`
    import fitz  # noqa: E402

# ---------------------------------------------------------------------------
# stub docling objects
# ---------------------------------------------------------------------------


@dataclass
class StubBBox:
    l: float  # noqa: E741 — mirrors docling's field names
    t: float
    r: float
    b: float
    coord_origin: str = "CoordOrigin.BOTTOMLEFT"


@dataclass
class StubProv:
    page_no: int
    bbox: StubBBox


@dataclass
class StubText:
    label: str
    text: str
    prov: list = field(default_factory=list)


@dataclass
class StubTable:
    label: str = "table"
    prov: list = field(default_factory=list)
    md: str = "| a | b |\n|---|---|\n| 1 | 2 |"

    def export_to_markdown(self, doc=None):
        return self.md

    def caption_text(self, doc):
        return "Table 2. Subgroup C-index."


@dataclass
class StubPicture:
    label: str = "picture"
    prov: list = field(default_factory=list)

    def caption_text(self, doc):
        return "Figure 3. Forest plot."


class StubDoc:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        for it in self._items:
            yield it, 0


# ---------------------------------------------------------------------------
# bbox conversion — the mirror-image bug this guards against is real
# ---------------------------------------------------------------------------


def test_bbox_bottomleft_converts_to_topleft():
    # page 842pt tall; a block whose TOP edge is 800pt above the page bottom
    bb = StubBBox(l=50, t=800, r=300, b=760)
    x0, y0, x1, y1 = _to_top_left(bb, 842.0)
    assert (x0, x1) == (50, 300)
    assert y0 == 42.0 and y1 == 82.0  # near the top of the page, not the bottom
    assert y0 < y1


def test_bbox_topleft_passthrough():
    bb = StubBBox(l=10, t=100, r=200, b=140, coord_origin="CoordOrigin.TOPLEFT")
    assert _to_top_left(bb, 842.0) == (10, 100, 200, 140)


# ---------------------------------------------------------------------------
# adapter mapping
# ---------------------------------------------------------------------------


def _prov(page=1, t=700, b=650):
    return [StubProv(page_no=page, bbox=StubBBox(l=40, t=t, r=550, b=b))]


def test_blocks_from_docling_full_taxonomy():
    doc = StubDoc(
        [
            StubText("DocItemLabel.SECTION_HEADER", "Results", _prov(t=800, b=780)),
            StubText("DocItemLabel.TEXT", "C-index differed by subgroup.", _prov(t=770, b=740)),
            StubTable(prov=_prov(t=730, b=600)),
            StubPicture(prov=_prov(t=590, b=400)),
            StubText("DocItemLabel.LIST_ITEM", "first item", _prov(t=390, b=370)),
            StubText("DocItemLabel.PAGE_FOOTER", "page 1 of 9", _prov(t=30, b=10)),
        ]
    )
    blocks = blocks_from_docling(doc, {1: 842.0})
    types = [b.type for b in blocks]
    assert types == ["sectionheader", "text", "table", "picture", "list"]  # footer skipped

    table = blocks[2]
    assert "| a | b |" in table.text and "Table 2." in table.text  # GFM + caption
    picture = blocks[3]
    assert picture.text == "[FIGURE: Figure 3. Forest plot.]"
    lst = blocks[4]
    assert lst.text == "- first item"
    # heading path propagates
    assert blocks[1].heading_path == ["Results"]
    # ids sequential
    assert [b.id for b in blocks] == [f"block_{i:04d}" for i in range(1, 6)]


def test_a_picture_reaches_the_model_as_its_caption_only():
    """Characterization, not a fix: a `picture` item's own text is not read.

    docling carries in-figure text — when its layout model finds a text region
    inside the figure — as separate nested text items, never on the picture
    item, so the adapter has nothing to lose here. Pinned because the README
    now states what a figure region delivers to the judge.
    """
    pic = StubPicture(prov=_prov(t=590, b=400))
    pic.text = "97% completed follow-up"  # docling's PictureItem has no such field
    blocks = blocks_from_docling(StubDoc([pic]), {1: 842.0})
    assert [b.text for b in blocks] == ["[FIGURE: Figure 3. Forest plot.]"]


def test_in_figure_text_survives_when_docling_emits_it_as_a_text_item():
    """The flat adapter keeps a text item that sits inside a figure's region.

    Whether docling emits one is docling's decision, not ours — on the paper
    this was measured against it emitted none for any of nine figures.
    """
    doc = StubDoc(
        [
            StubPicture(prov=_prov(t=590, b=400)),
            StubText("DocItemLabel.TEXT", "97% completed follow-up", _prov(t=520, b=500)),
        ]
    )
    blocks = blocks_from_docling(doc, {1: 842.0})
    assert [b.type for b in blocks] == ["picture", "text"]
    assert blocks[1].text == "97% completed follow-up"


# ---------------------------------------------------------------------------
# hyphenated line breaks — docling deletes the hyphen, the page still has one
# ---------------------------------------------------------------------------


def _hyphen_pdf(tmp_path):
    """A page that breaks words at a hyphen across lines, as typeset pages do.

    Line 1 writes "Non-Hispanic" out unbroken; lines 2-3 break it. That pairing
    is the whole evidence rule.
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Regarding race, white Non-Hispanic patients were", fontsize=11)
    page.insert_text((72, 114), "prevalent in each subgroup, followed by Asian, Non-", fontsize=11)
    page.insert_text((72, 128), "Hispanic; and HbA1c was measured in PaperTrace.", fontsize=11)
    page.insert_text((72, 170), "The cohort was assessed approxi-", fontsize=11)
    page.insert_text((72, 184), "mately once a year at https://x.org/Data-Science-and-", fontsize=11)
    page.insert_text((72, 198), "Informatics/report as agreed.", fontsize=11)
    # page 2: the joined form is also a real word here, so the break is ambiguous
    page2 = doc.new_page()
    page2.insert_text((72, 100), "The column NonHispanic holds the Non-Hispanic flag, and", fontsize=11)
    page2.insert_text((72, 114), "white Non-", fontsize=11)
    page2.insert_text((72, 128), "Hispanic is its label.", fontsize=11)
    pdf = tmp_path / "hyphen.pdf"
    doc.save(pdf)
    doc.close()
    return pdf


def test_hyphen_joins_takes_the_compound_from_the_document(tmp_path):
    joins = hyphen_joins(_hyphen_pdf(tmp_path))
    # the paper writes "Non-Hispanic" out on line 1, so the break on line 2 is
    # a hyphen docling should not have eaten
    assert joins[1]["NonHispanic"] == "Non-Hispanic"
    # a syllabic break: docling's "approximately" IS the author's word, and
    # "approxi-mately" appears nowhere — no entry, nothing rewritten
    assert "approximately" not in joins[1]
    # the URL wrap breaks at a hyphen that belongs to the URL, but the document
    # never writes it out unbroken — unproven, so not claimed
    assert "andInformatics" not in joins[1]
    # mid-line hyphens and camel-cased words are not breaks at all
    assert "HbA1c" not in joins[1] and "PaperTrace" not in joins[1]
    # page 2 carries "NonHispanic" as a word of its own: which of the two a
    # rewrite would hit is unknowable, so nothing is claimed there
    assert "NonHispanic" not in joins.get(2, {})


def test_restore_hyphen_joins_rewrites_only_the_proven_join(tmp_path):
    joins = {1: {"NonHispanic": "Non-Hispanic"}}
    blocks = [
        Block("block_0001", "text", 1, (0, 0, 1, 1), [],
              "Asian, NonHispanic; white Non-Hispanic; HbA1c in PaperTrace"),
        Block("block_0002", "text", 2, (0, 0, 1, 1), [], "Asian, NonHispanic follow"),
    ]
    out = restore_hyphen_joins(blocks, joins)
    assert out[0].text == "Asian, Non-Hispanic; white Non-Hispanic; HbA1c in PaperTrace"
    # page 2 has no proven join — a page's joins never leak onto another page
    assert out[1].text == "Asian, NonHispanic follow"


def test_the_repaired_block_reads_as_the_author_wrote_it(tmp_path):
    pdf = _hyphen_pdf(tmp_path)
    blocks = restore_hyphen_joins(
        [Block("block_0001", "text", 1, (0, 0, 1, 1), [], "followed by Asian, NonHispanic; and")],
        hyphen_joins(pdf),
    )
    assert "Asian, Non-Hispanic" in blocks[0].text


# ---------------------------------------------------------------------------
# the anchor half of the same defect: what `search_for` can actually find
#
# These belong beside a highlight test, but the pairing is the point — the join
# decides what the model can quote, and the page decides what can be boxed.
# `search_for` reads a line break as a space, so neither the compound
# ("Non-Hispanic") nor docling's join ("NonHispanic") is on the page: only
# "Non- Hispanic" is.
# ---------------------------------------------------------------------------


def test_a_compound_quote_boxes_the_broken_line_on_the_page(tmp_path):
    pdf = _hyphen_pdf(tmp_path)
    page = fitz.open(pdf)[0]
    assert page.search_for("Asian, Non-Hispanic") == []  # not on the page verbatim
    boxes = crop_evidence(pdf, 1, (60, 90, 560, 210), ["Asian, Non-Hispanic"],
                          tmp_path / "crop.png")
    assert boxes >= 1


def test_a_dehyphenated_quote_boxes_the_broken_line_on_the_page(tmp_path):
    """The syllabic case, which no phrase-only rule can repair: the page is asked."""
    pdf = _hyphen_pdf(tmp_path)
    page = fitz.open(pdf)[0]
    assert page.search_for("assessed approximately once") == []
    boxes = crop_evidence(pdf, 1, (60, 90, 560, 210), ["assessed approximately once"],
                          tmp_path / "crop2.png")
    assert boxes >= 1


def test_the_retry_never_invents_a_box(tmp_path):
    pdf = _hyphen_pdf(tmp_path)
    boxes = crop_evidence(pdf, 1, (60, 90, 560, 210), ["Pacific-Islander patients were"],
                          tmp_path / "crop3.png")
    assert boxes == 0


# ---------------------------------------------------------------------------
# writers render tables/figures usefully for the LLM
# ---------------------------------------------------------------------------


def test_write_outputs_renders_table_and_figure(tmp_path):
    smap = SourceMap(
        doc="x.pdf",
        pages=1,
        converter="docling 2.x",
        blocks=[
            Block("block_0001", "sectionheader", 1, (0, 0, 1, 1), [], "Results"),
            Block("block_0002", "table", 1, (0, 0, 1, 1), ["Results"],
                  "| grp | c |\n|---|---|\n| Asian | 0.668 |"),
            Block("block_0003", "picture", 1, (0, 0, 1, 1), ["Results"],
                  "[FIGURE: Figure 3. Forest plot.]"),
        ],
    )
    write_outputs(smap, tmp_path)
    annotated = (tmp_path / "annotated.md").read_text()
    assert "| Asian | 0.668 |" in annotated  # table survives as a table
    assert "block_0002, page 1, table" in annotated  # provenance marker present
    assert "[FIGURE: Figure 3. Forest plot.]" in annotated
    reloaded = SourceMap.from_json(tmp_path / "source_map.json")
    assert reloaded.converter == "docling 2.x"
    assert reloaded.find("block_0002").type == "table"


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_auto_falls_back_to_pymupdf_and_discloses(tmp_path, monkeypatch):
    # simulate a docling-less install, whatever this environment actually has
    import papertrace.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "_docling_available", lambda: False)

    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Tiny Fixture", fontsize=16)
    page.insert_text((72, 120), "Body text here that is long enough to count as body prose.",
                     fontsize=10)
    pdf = tmp_path / "t.pdf"
    doc.save(pdf)
    doc.close()

    smap = ingest_pdf(pdf, tmp_path / "out", backend="auto")
    assert smap.converter == "pymupdf"

    import pytest

    with pytest.raises(RuntimeError, match="docling backend requested"):
        ingest_pdf(pdf, tmp_path / "out2", backend="docling")
