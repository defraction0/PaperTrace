"""Layout-aware ingest backend on docling (optional dependency).

Produces the full block taxonomy: real tables as GFM markdown, figures with
captions, lists as lists. The mapping core (`blocks_from_docling`) is
duck-typed and unit-tested against stubs, so the adapter's logic — label
mapping and the bottom-left → top-left bbox conversion — is verified even
where docling itself isn't installed.

Coordinate note: docling reports bounding boxes with a BOTTOMLEFT origin;
PyMuPDF (and our highlight step) use TOPLEFT. `_to_top_left` converts using
the page height — getting this wrong mirrors every red box vertically.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Block

_HEADING_LABELS = {"section_header", "title"}
_LIST_LABELS = {"list_item"}
_SKIP_LABELS = {"page_header", "page_footer", "footnote"}


def _to_top_left(bbox, page_height: float) -> tuple[float, float, float, float]:
    """Convert a docling BoundingBox to top-left-origin (x0, y0, x1, y1)."""
    left, top, right, bottom = bbox.l, bbox.t, bbox.r, bbox.b
    origin = str(getattr(bbox, "coord_origin", "")).upper()
    if "BOTTOM" in origin:
        # t/b measure up from the page bottom, with t > b
        y0, y1 = page_height - top, page_height - bottom
    else:
        y0, y1 = top, bottom
    if y0 > y1:
        y0, y1 = y1, y0
    return (round(left, 2), round(y0, 2), round(right, 2), round(y1, 2))


def _item_prov(item):
    prov = getattr(item, "prov", None) or []
    return prov[0] if prov else None


def _caption_of(item, doc) -> str:
    try:
        cap = item.caption_text(doc)
        return cap.strip() if cap else ""
    except Exception:  # noqa: BLE001 — caption access varies across docling versions
        return ""


def _table_markdown(item, doc) -> str:
    try:
        return item.export_to_markdown(doc)
    except TypeError:  # older docling-core signature
        return item.export_to_markdown()


def blocks_from_docling(doc, page_heights: dict[int, float]) -> list[Block]:
    """Map a docling document to our Block list. Duck-typed for testability.

    `doc` needs `iterate_items()` yielding (item, level); items need `.label`
    (str or enum), `.prov` (with `.page_no` and `.bbox`), and `.text` /
    `export_to_markdown` / `caption_text` per type. `page_heights` maps
    1-based page numbers to heights in PDF points.
    """
    blocks: list[Block] = []
    heading_stack: list[str] = []
    i = 0
    for item, _level in doc.iterate_items():
        label = str(getattr(item, "label", "text")).split(".")[-1].lower()
        if label in _SKIP_LABELS:
            continue
        prov = _item_prov(item)
        if prov is None:
            continue
        page = int(prov.page_no)
        bbox = _to_top_left(prov.bbox, page_heights.get(page, 842.0))

        kind = getattr(item, "__class__", type(item)).__name__.lower()
        if "table" in kind:
            btype = "table"
            text = _table_markdown(item, doc).strip()
            cap = _caption_of(item, doc)
            if cap:
                text = f"**{cap}**\n\n{text}"
        elif "picture" in kind:
            btype = "picture"
            cap = _caption_of(item, doc)
            text = f"[FIGURE: {cap}]" if cap else "[FIGURE]"
        elif label in _HEADING_LABELS:
            btype = "sectionheader"
            text = re.sub(r"\s+", " ", getattr(item, "text", "") or "").strip()
            heading_stack = [text]
        elif label in _LIST_LABELS:
            btype = "list"
            text = "- " + re.sub(r"\s+", " ", getattr(item, "text", "") or "").strip()
        else:
            btype = "text"
            text = re.sub(r"\s+", " ", getattr(item, "text", "") or "").strip()

        if not text:
            continue
        i += 1
        blocks.append(
            Block(
                id=f"block_{i:04d}",
                type=btype,
                page=page,
                bbox=bbox,
                heading_path=list(heading_stack),
                text=text,
            )
        )
    return blocks


def ingest_blocks_docling(pdf_path: Path) -> tuple[int, list[Block], str]:
    """Run docling on a PDF. Returns (page_count, blocks, docling_version)."""
    import docling
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(str(pdf_path))
    doc = result.document

    page_heights: dict[int, float] = {}
    for no, page in getattr(doc, "pages", {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            page_heights[int(no)] = float(size.height)
    pages = len(page_heights) or 1

    version = getattr(docling, "__version__", "?")
    return pages, blocks_from_docling(doc, page_heights), version
