"""Evidence crops: real page regions from a cited source, matched text boxed in red.

Boxes come from text search on the PDF — never hand-placed — so a box always
sits where the evidence actually is. The crop region defaults to the anchored
block's bbox (from the source's own source_map), padded for context; it bounds
what is rendered and which hits are kept, and is never handed to `search_for`,
whose own clip discards a column-spanning match whole.

A quote can miss the page for typesetting reasons alone: a hyphenated line break
reads as "Non- Hispanic" to `search_for`, and the text the model read says
"Non-Hispanic" or "NonHispanic". `_search` retries such a phrase, but only in
forms the page itself dictates and always by exact search — never by similarity,
so an unlocated anchor still comes back unlocated.
"""

from __future__ import annotations

import re
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

_DASHES = "-\u2010\u2011"  # hyphen-minus, Unicode hyphen, non-breaking hyphen
_MAX_VARIANTS = 8  # a phrase full of dashes is not worth a combinatorial search
_LINE_BREAK_HYPHEN = re.compile(rf"(\w+)[{_DASHES}\u00ad]\n(\w+)")


def _dash_variants(phrase: str) -> list[str]:
    """The phrase, then one-space-at-one-dash variants of it.

    A page that breaks a hyphenated word across lines reads as "Non- Hispanic"
    to `search_for` — the line break is a space — and a model quoting that
    passage tidies it to "Non-Hispanic", which that occurrence does not carry.
    Only
    whitespace beside a dash the phrase already has moves here: no character is
    invented, no similarity is computed, and every candidate is still located by
    exact text search, so a hit sits on text the page really carries.
    """
    variants = [phrase]
    for i, ch in enumerate(phrase):
        if ch not in _DASHES or i == 0 or i == len(phrase) - 1:
            continue
        rest = phrase[i + 1:]
        variant = phrase[: i + 1] + (rest.lstrip(" ") if rest[0] == " " else " " + rest)
        if variant not in variants:
            variants.append(variant)
        if len(variants) >= _MAX_VARIANTS:
            break
    return variants


def _as_the_page_breaks_it(page, phrase: str) -> str:
    """`phrase` with every word this page splits at a hyphen put back as it reads.

    The ingest text says "approximately" and the page says "approxi-" / newline
    "mately"; `search_for` reads that break as a space, so the phrase is on the
    page only as "approxi- mately". The rewrite is dictated by the page — no
    split position is guessed, and a word the page does not break is untouched.
    """
    for tail, head in _LINE_BREAK_HYPHEN.findall(page.get_text()):
        joined = tail + head
        if joined in phrase:
            phrase = re.sub(rf"(?<!\w){re.escape(joined)}(?!\w)", f"{tail}- {head}", phrase)
    return phrase


def _search(page, phrase: str) -> list:
    """Locate `phrase` anywhere on `page`; retry the page's own hyphenation of it.

    Every candidate is still an exact `search_for`, and every difference from
    the model's phrase comes from the page: whitespace beside a dash the phrase
    already carries, or a break the page itself makes. Returns [] when the page
    carries none of them — a genuine miss stays a miss, and the caller records
    anchor_located = False rather than boxing something that resembles the quote.

    Deliberately takes no `clip`: `search_for`'s own clip drops a match that
    straddles the region's edge WHOLE, so a quote continuing into the next
    column matched nothing at all. Callers bound the boxes themselves — see
    `crop_evidence`.
    """
    for candidate in _dash_variants(phrase):
        hits = page.search_for(candidate)
        if hits:
            return hits
    broken = _as_the_page_breaks_it(page, phrase)  # last, it costs a text extraction
    return page.search_for(broken) if broken != phrase else []


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
        for hit in _search(page, phrase):
            # the region bounds what is DRAWN, not what counts as a match. A hit
            # elsewhere on the page is not this block's evidence and would land
            # outside the image; a hit that merely continues past the edge is.
            if not rect.intersects(hit):
                continue
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


def crop_for_anchor(anchor, claim_id: int, sources_dir: Path, ingest_root: Path,
                    out_dir: Path) -> list[str]:
    """Produce the evidence crops for one anchored judgement, in reading order.

    `anchor` is anything carrying source_slug / source_page / source_block /
    anchor_phrases / anchor_located — a ClaimResult (its headline) or a single
    SourceJudgement. A multi-source claim crops once per source, so the reader
    sees the passage behind each verdict rather than one paper standing in for
    all of them; the slug is in the filename, so the crops cannot collide.

    **One image per rectangle the passage occupies.** A paragraph that continues
    into the next column or onto the next page is several rectangles, and
    cropping only the first showed the reader where the evidence began and never
    where it was: measured on a real audit, 17 of 31 anchored judgements had
    located text outside that first rectangle. The rectangles come from
    `Block.regions` — the converter's own per-rectangle provenance — so which
    ones exist is recorded rather than inferred from where the hits fell.

    `sources_dir` holds the PDFs (named `<slug>.pdf`); `ingest_root/<slug>/`
    holds each source's source_map.json. Returns the crops' paths, first one
    first, or [] when there is no usable anchor.

    Sets `anchor.anchor_located` so the report can tell a boxed crop from an
    unboxed one — True when ANY of the images carries a box, because a passage
    whose evidence is in its continuation is located, not missing.
    """
    from .models import SourceMap  # local import to avoid cycles

    if not (anchor.source_slug and anchor.source_page):
        return []
    pdf = sources_dir / f"{anchor.source_slug}.pdf"
    if not pdf.exists():
        return []
    # a model can name a page the source does not have. Bail before any
    # doc[page - 1]: the IndexError used to abort the whole highlight step, and
    # anchor_located stays None because nothing was ever searched.
    n_pages = source_page_count(pdf)
    if anchor.source_page > n_pages:
        return []

    regions: list[tuple[int, tuple[float, float, float, float]]] = []
    smap_path = ingest_root / anchor.source_slug / "source_map.json"
    if anchor.source_block and smap_path.exists():
        block = SourceMap.from_json(smap_path).find(anchor.source_block)
        if block:
            # The page test is against ANY region, not `block.page`: that field
            # is the block's OPENING, so a judgement naming the page the
            # continuation is on used to fail the gate and fall through to the
            # hit-union below, losing the block's own rectangles.
            if block.regions and any(r.page == anchor.source_page for r in block.regions):
                regions = [(r.page, r.bbox) for r in block.regions if 1 <= r.page <= n_pages]
            elif block.page == anchor.source_page:
                # a map written before regions were recorded: empty means not
                # recorded, so this is the one rectangle anyone ever had
                regions = [(block.page, block.bbox)]
    if not regions:
        # fall back to the union of phrase hits on the page, padded
        doc = fitz.open(pdf)
        page = doc[anchor.source_page - 1]
        hits = [h for p in anchor.anchor_phrases for h in _search(page, p)]
        doc.close()
        if not hits:
            # the phrases were searched against the real page and matched
            # nothing — that is "attempted and not located", not "unknown"
            if anchor.anchor_phrases:
                anchor.anchor_located = False
            return []
        u = hits[0]
        for h in hits[1:]:
            u |= h
        regions = [(anchor.source_page, (u.x0 - 40, u.y0 - 14, u.x1 + 40, u.y1 + 14))]

    out: list[str] = []
    boxes = 0
    for i, (page_no, region) in enumerate(regions, start=1):
        # the ordinal, not the page: two regions can share a page — a left and a
        # right column — and `crop_evidence` saves unconditionally, so a name
        # keyed on the page alone would leave one image holding the other's
        # picture. The first keeps its historical name, asserted literally in
        # tests/test_pipeline.py and tests/test_multisource.py.
        suffix = "" if i == 1 else f"_cont{i}"
        out_path = (
            out_dir / f"claim_{claim_id:02d}_{anchor.source_slug}_p{page_no}{suffix}.png"
        )
        boxes += crop_evidence(pdf, page_no, region, anchor.anchor_phrases, out_path)
        out.append(str(out_path))
    # a crop with no box is still useful context, but the report must not
    # caption it as matched text. No phrases to search for is a THIRD state:
    # `False` would assert we looked and missed, when nothing was ever looked for.
    anchor.anchor_located = (boxes > 0) if anchor.anchor_phrases else None
    return out


def crop_for_claim(claim, sources_dir: Path, ingest_root: Path, out_dir: Path) -> list[str]:
    """The claim's own headline anchor — the pre-multi-source entry point."""
    return crop_for_anchor(claim, claim.id, sources_dir, ingest_root, out_dir)
