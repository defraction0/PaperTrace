"""Evidence crops: real page regions from a cited source, matched text boxed in red.

Boxes come from text search on the PDF — never hand-placed — so a box always
sits where the evidence actually is. The crop region defaults to the anchored
block's bbox (from the source's own source_map), padded for context.
"""

from __future__ import annotations

from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 module name (the bare `fitz` import is deprecated)
except ImportError:  # pragma: no cover - older PyMuPDF exposes only `fitz`
    import fitz
from PIL import Image, ImageDraw

RED = (214, 48, 42)
PAD_PT = 6.0  # context padding around the anchor region (PDF points)
BOX_PAD = 2.0  # breathing room around a matched phrase
TARGET_W = 2400  # rendered crop width in pixels (retina-ish)


def source_page_count(pdf_path: Path) -> int:
    """Pages in a resolved source — for bounds-checking a page the model named."""
    doc = fitz.open(pdf_path)
    n = doc.page_count
    doc.close()
    return n


def crop_evidence(
    pdf_path: Path,
    page_no: int,
    region: tuple[float, float, float, float],
    phrases: list[str],
    out_path: Path,
) -> int:
    """Render `region` of `page_no` (1-based) with red boxes on `phrases`.

    Returns the number of boxes drawn (0 if no phrase matched inside region —
    the crop is still written so the reader can judge the context).
    """
    doc = fitz.open(pdf_path)
    if not 1 <= page_no <= doc.page_count:
        # NOT `return 0`: the caller reads 0 as "crop written, nothing matched"
        # and would record anchor_located = False — asserting we searched a page
        # this document does not have. Callers bounds-check; reaching here is a
        # caller bug, and it says so instead of raising IndexError from a slice.
        n = doc.page_count
        doc.close()
        raise ValueError(f"page {page_no} is not in {pdf_path.name} ({n} pages)")
    page = doc[page_no - 1]

    rect = fitz.Rect(*region) + (-PAD_PT, -PAD_PT, PAD_PT, PAD_PT)
    rect &= page.rect  # clamp

    zoom = min(8.0, max(2.0, TARGET_W / max(rect.width, 1)))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)

    boxes = 0
    for phrase in phrases:
        for hit in page.search_for(phrase, clip=rect):
            x0 = (hit.x0 - BOX_PAD - rect.x0) * zoom
            y0 = (hit.y0 - BOX_PAD - rect.y0) * zoom
            x1 = (hit.x1 + BOX_PAD - rect.x0) * zoom
            y1 = (hit.y1 + BOX_PAD - rect.y0) * zoom
            draw.rectangle([x0, y0, x1, y1], outline=RED, width=max(2, round(zoom / 2)))
            boxes += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    doc.close()
    return boxes


def crop_for_claim(claim, sources_dir: Path, ingest_root: Path, out_dir: Path) -> str | None:
    """Produce the evidence crop for one ClaimResult, using its anchor.

    `sources_dir` holds the PDFs (named `<slug>.pdf`); `ingest_root/<slug>/`
    holds each source's source_map.json. Returns the crop's path relative to
    `out_dir`'s parent, or None when the claim has no usable anchor.

    Sets `claim.anchor_located` so the report can tell a boxed crop from an
    unboxed one.
    """
    from .models import SourceMap  # local import to avoid cycles

    if not (claim.source_slug and claim.source_page):
        return None
    pdf = sources_dir / f"{claim.source_slug}.pdf"
    if not pdf.exists():
        return None
    # a model can name a page the source does not have. Bail before any
    # doc[page - 1]: the IndexError used to abort the whole highlight step, and
    # anchor_located stays None because nothing was ever searched.
    if claim.source_page > source_page_count(pdf):
        return None

    region = None
    smap_path = ingest_root / claim.source_slug / "source_map.json"
    if claim.source_block and smap_path.exists():
        block = SourceMap.from_json(smap_path).find(claim.source_block)
        if block and block.page == claim.source_page:
            region = block.bbox
    if region is None:
        # fall back to the union of phrase hits on the page, padded
        doc = fitz.open(pdf)
        page = doc[claim.source_page - 1]
        hits = [h for p in claim.anchor_phrases for h in page.search_for(p)]
        doc.close()
        if not hits:
            # the phrases were searched against the real page and matched
            # nothing — that is "attempted and not located", not "unknown"
            if claim.anchor_phrases:
                claim.anchor_located = False
            return None
        u = hits[0]
        for h in hits[1:]:
            u |= h
        region = (u.x0 - 40, u.y0 - 14, u.x1 + 40, u.y1 + 14)

    out_path = out_dir / f"claim_{claim.id:02d}_{claim.source_slug}_p{claim.source_page}.png"
    boxes = crop_evidence(pdf, claim.source_page, region, claim.anchor_phrases, out_path)
    # a crop with no box is still useful context, but the report must not
    # caption it as matched text. No phrases to search for is a THIRD state:
    # `False` would assert we looked and missed, when nothing was ever looked for.
    claim.anchor_located = (boxes > 0) if claim.anchor_phrases else None
    return str(out_path)
