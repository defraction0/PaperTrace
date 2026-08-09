"""Flat-text ingest backend on PyMuPDF.

Fast and dependency-light, but layout-blind: tables come out linearized and
figures are not represented. The dispatcher stamps the source map with
``converter: pymupdf`` so reports can disclose that limitation.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 module name (the bare `fitz` import is deprecated)
except ImportError:  # pragma: no cover - older PyMuPDF exposes only `fitz`
    import fitz

from ..models import Block, SourceMap

_HEADING_MAX_LEN = 120


def _block_text(raw: dict) -> tuple[str, float]:
    """Join a PyMuPDF text block; return (text, max font size)."""
    parts: list[str] = []
    max_size = 0.0
    for line in raw.get("lines", []):
        spans = [s["text"] for s in line.get("spans", [])]
        max_size = max([max_size, *[s.get("size", 0.0) for s in line.get("spans", [])]])
        parts.append("".join(spans))
    text = re.sub(r"[ \t]+", " ", " ".join(p.strip() for p in parts if p.strip())).strip()
    return text, max_size


def ingest_blocks_pymupdf(pdf_path: Path) -> tuple[int, list[Block]]:
    """Extract (page_count, blocks) — coordinates are top-left origin."""
    doc = fitz.open(pdf_path)

    staged: list[tuple[int, tuple, str, float]] = []
    for pno in range(doc.page_count):
        for raw in doc[pno].get_text("dict")["blocks"]:
            if raw.get("type", 0) != 0:  # images carry no text here
                continue
            text, size = _block_text(raw)
            if not text:
                continue
            staged.append((pno + 1, tuple(round(v, 2) for v in raw["bbox"]), text, size))

    sizes = [s for _, _, t, s in staged if len(t) > 80]
    body_size = statistics.median(sizes) if sizes else 10.0

    blocks: list[Block] = []
    heading_stack: list[str] = []
    for i, (page, bbox, text, size) in enumerate(staged, start=1):
        is_heading = size > body_size * 1.12 and len(text) <= _HEADING_MAX_LEN
        btype = "sectionheader" if is_heading else "text"
        if is_heading:
            heading_stack = [text]
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

    pages = doc.page_count
    doc.close()
    return pages, blocks


def references_section(smap: SourceMap) -> str:
    """Return the text of the References/Bibliography section, if found."""
    pat = re.compile(r"^(references|bibliography|literature)\b", re.I)
    started = False
    out: list[str] = []
    for b in smap.blocks:
        if b.type == "sectionheader":
            if started:
                break
            if pat.match(b.text.strip()):
                started = True
            continue
        if started:
            out.append(b.text)
    return "\n".join(out)
