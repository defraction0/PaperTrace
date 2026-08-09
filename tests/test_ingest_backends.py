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

from papertrace.ingest import ingest_pdf, write_outputs  # noqa: E402
from papertrace.ingest.docling_ import _to_top_left, blocks_from_docling  # noqa: E402
from papertrace.models import Block, SourceMap  # noqa: E402

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


def test_auto_falls_back_to_pymupdf_and_discloses(tmp_path):
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
    assert smap.converter == "pymupdf"  # docling not installed in CI

    import pytest

    with pytest.raises(RuntimeError, match="docling backend requested"):
        ingest_pdf(pdf, tmp_path / "out2", backend="docling")
