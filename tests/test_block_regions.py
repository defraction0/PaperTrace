"""A block's text lives in every rectangle docling gives it, not just the first.

`Block.bbox` was `prov[0].bbox` and `Block.page` was `prov[0].page_no`, so for a
paragraph that continues into the next column or onto the next page the stored
rectangle bounded only its opening. Measured against the live converter on a
real paper (`fujima-2023.pdf`, the block behind a real audited claim):

    block_0012  n_prov 3   (text length 2554)
      page 2  charspan (   0,  295)   <- the only one kept
      page 2  charspan ( 296, 2152)   <- the right column
      page 3  charspan (2153, 2554)   <- onto page 3

14,367 characters in that one 14-page paper sat outside the rectangle their
block claimed, and five items crossed a page boundary — so `page` was wrong for
part of the text too, not just `bbox`.

docling records `page_no`, `bbox` AND `charspan` per provenance entry
(`docling_core/types/doc/common/reference.py`), so nothing here is derived: the
charspans are why regions can be put in reading order, which y-coordinates
cannot do when two rectangles share a page.

`page` and `bbox` keep their meaning — the FIRST region — so every existing
consumer and the schema's `maxItems: 4` stay valid.

Offline: `blocks_from_docling` is duck-typed (see its docstring), so these tests
hand it fake items and docling is never imported.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.ingest.docling_ import blocks_from_docling  # noqa: E402
from papertrace.models import Block, Region, SourceMap  # noqa: E402


class _BBox:
    """A docling BoundingBox, bottom-left origin like the real converter emits."""

    def __init__(self, left, top, right, bottom):
        self.l, self.t, self.r, self.b = left, top, right, bottom  # noqa: E741
        self.coord_origin = "CoordOrigin.BOTTOMLEFT"


class _Prov:
    def __init__(self, page_no, bbox, charspan):
        self.page_no, self.bbox, self.charspan = page_no, bbox, charspan


class _Item:
    """A docling TextItem, as much of one as `blocks_from_docling` reads."""

    def __init__(self, text, prov):
        self.label, self.text, self.prov = "DocItemLabel.TEXT", text, prov


class _Doc:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        return ((it, 0) for it in self._items)


# The real prov triple from fujima-2023 block_0012, page heights as measured.
_FUJIMA = _Item(
    "Over the past decade, advanced analytical methods " + "x" * 2500,
    [
        _Prov(2, _BBox(42.01, 115.98, 284.02, 59.29), (0, 295)),
        _Prov(2, _BBox(299.0, 416.0, 541.09, 59.29), (296, 2152)),
        _Prov(3, _BBox(54.03, 139.96, 296.04, 59.29), (2153, 2554)),
    ],
)
_HEIGHTS = {1: 782.36, 2: 782.36, 3: 782.36}


def test_every_provenance_rectangle_is_recorded():
    """The defect itself: two of three rectangles were thrown away."""
    blocks = blocks_from_docling(_Doc([_FUJIMA]), _HEIGHTS)
    assert len(blocks) == 1
    assert len(blocks[0].regions) == 3, blocks[0].regions


def test_the_regions_carry_their_own_page():
    """`page` alone cannot describe a passage that crosses a page break."""
    b = blocks_from_docling(_Doc([_FUJIMA]), _HEIGHTS)[0]
    assert [r.page for r in b.regions] == [2, 2, 3]


def test_the_regions_are_in_reading_order_by_charspan():
    """Not by y: regions 1 and 2 share page 2, and the continuation is HIGHER on
    the page than the opening (right column, y 366 vs left column, y 666)."""
    b = blocks_from_docling(_Doc([_FUJIMA]), _HEIGHTS)[0]
    assert [r.char_start for r in b.regions] == [0, 296, 2153]
    assert [r.char_end for r in b.regions] == [295, 2152, 2554]
    assert b.regions[1].bbox[1] < b.regions[0].bbox[1], "region 2 is higher up the page"


def test_page_and_bbox_still_mean_the_first_region():
    """Every existing consumer reads these two, and the schema pins their shape."""
    b = blocks_from_docling(_Doc([_FUJIMA]), _HEIGHTS)[0]
    assert b.page == 2
    assert b.bbox == (42.01, 666.38, 284.02, 723.07)  # measured against the real map
    assert b.regions[0].page == b.page
    assert b.regions[0].bbox == b.bbox


def test_an_item_with_one_rectangle_gets_exactly_one_region():
    """The common case — 193 of 205 items in that paper — must not grow."""
    one = _Item("A single-column paragraph.",
                [_Prov(1, _BBox(50.0, 700.0, 300.0, 650.0), (0, 25))])
    b = blocks_from_docling(_Doc([one]), _HEIGHTS)[0]
    assert len(b.regions) == 1
    assert b.regions[0].page == 1


def test_an_item_with_no_provenance_is_still_skipped():
    """Unchanged behaviour: nothing locatable, nothing recorded."""
    assert blocks_from_docling(_Doc([_Item("orphan", [])]), _HEIGHTS) == []


# --- wire format ----------------------------------------------------------


def test_regions_round_trip_through_the_source_map(tmp_path):
    smap = SourceMap(doc="x.pdf", pages=3, blocks=[
        Block("block_0001", "text", 2, (42.0, 666.0, 284.0, 723.0), [], "text",
              regions=[Region(2, (42.0, 666.0, 284.0, 723.0), 0, 295),
                       Region(2, (299.0, 366.0, 541.0, 723.0), 296, 2152),
                       Region(3, (54.0, 642.0, 296.0, 723.0), 2153, 2554)]),
    ])
    smap.to_json(tmp_path / "m.json")
    back = SourceMap.from_json(tmp_path / "m.json")
    assert [(r.page, r.char_start, r.char_end) for r in back.blocks[0].regions] == [
        (2, 0, 295), (2, 296, 2152), (3, 2153, 2554)
    ]
    assert back.blocks[0].regions[0].bbox == (42.0, 666.0, 284.0, 723.0)


def test_a_source_map_written_before_regions_still_loads(tmp_path):
    """Absent means NOT RECORDED, never "this block has no continuation" —
    consumers fall back to the single rectangle, which is today's behaviour."""
    import json

    (tmp_path / "old.json").write_text(json.dumps({
        "doc": "x.pdf", "pages": 1,
        "blocks": [{"id": "block_0001", "type": "text", "page": 1,
                    "bbox": [10.0, 20.0, 30.0, 40.0], "text": "old"}],
    }))
    b = SourceMap.from_json(tmp_path / "old.json").blocks[0]
    assert b.regions == []
    assert b.bbox == (10.0, 20.0, 30.0, 40.0)


def test_the_flat_backend_records_one_region_per_block(tmp_path):
    """Both backends populate it, so nothing downstream branches on backend.
    PyMuPDF never merges across a column or page, so one region is the truth."""
    import pymupdf

    from papertrace.ingest.pymupdf_ import ingest_blocks_pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((60, 90), "A paragraph of body text here.", fontsize=10)
    doc.save(tmp_path / "f.pdf")
    doc.close()

    _pages, blocks = ingest_blocks_pymupdf(tmp_path / "f.pdf")
    assert blocks, "fixture produced no blocks"
    for b in blocks:
        assert len(b.regions) == 1, b.id
        assert b.regions[0].page == b.page
        assert b.regions[0].bbox == b.bbox
        assert (b.regions[0].char_start, b.regions[0].char_end) == (0, len(b.text))
