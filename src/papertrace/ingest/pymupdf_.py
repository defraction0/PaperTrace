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

from ..models import Block, SourceMap, is_references_heading, looks_like_reference

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


# A reference list interrupted by another section is not necessarily over, but
# resuming across the break is a guess, so it takes two independent signals.
#
# `list` only, never `text`: docling types reference entries as `list`, which is
# structurally distinct from prose. Under the flat backend they are `text` —
# the same type as every paragraph in the paper — so resuming on a run of
# `text` would swallow the Discussion of any paper whose references are not
# last. That asymmetry is the whole reason this is restricted rather than
# general, and it means a *flat-ingested* split list is still parsed short.
_RESUMABLE_TYPE = "list"
# one bulleted block after an unrelated heading is far more likely a sentence
# than the tail of a bibliography
_MIN_RESUME_RUN = 2


def _mostly_references(run) -> bool:
    """Does this run of same-typed blocks read as a bibliography?

    Half, not all. Requiring every entry would drop a real continuation over one
    bare-URL entry; requiring one would let a single dated line drag a whole
    section of back matter in behind it.
    """
    hits = sum(1 for b in run if looks_like_reference(b.text))
    return hits * 2 >= len(run)


def references_span(smap: SourceMap) -> tuple[str, bool]:
    """Reference-list text, and whether it was resumed across a section break.

    A block whose *entire* text is the heading word counts as the heading even
    when ingest typed it as body text. Flat-text ingest guesses headings from
    font size, and on a real Elsevier paper it guessed wrong — three author
    lines became headings and `References` stayed body text, so the audit
    stopped at "No numbered references found" on a paper with 34 of them.

    Requiring the whole block to be the word, not merely to start with it, is
    what keeps "References were checked by hand" from swallowing the paper.

    The second return value says the list was picked up again after an
    intervening section. That is worth surfacing rather than hiding: a real
    pre-proof put refs 1-9 on page 7, `Declaration of interests` next, then refs
    10-15 on page 8, and stopping at the first header lost six sources without
    saying so. The prose of the intervening section never enters the list, which
    matters because `_parse_bulleted` appends a non-bullet line to the *previous*
    entry, so a stray paragraph corrupts a reference rather than merely adding
    noise.

    A run has to look like references, not merely share their block type. This
    docstring used to claim that and it was false — the only test was the type,
    so three `list` blocks under a `TABLE TITLES` heading became references
    44-46 of a 43-reference paper, and the resolver title-searched the paper's
    own table captions into table-component DOIs belonging to other papers.
    The test is applied to the run rather than to each entry: a genuine
    continuation can hold a bare URL entry with no year, and rejecting the whole
    run over it would undo the fix above.
    """
    blocks = smap.blocks
    start = next(
        # the SAME rule coverage_audit uses — see models.is_references_heading
        (i + 1 for i, b in enumerate(blocks) if is_references_heading(b.type, b.text)),
        None,
    )
    if start is None:
        return "", False

    out: list[str] = []
    entry_type: str | None = None
    i = start
    while i < len(blocks):
        b = blocks[i]
        if b.type == "sectionheader" or is_references_heading(b.type, b.text):
            break  # the next real heading ends the contiguous run
        if entry_type is None:
            entry_type = b.type
        out.append(b.text)
        i += 1

    if not out or entry_type != _RESUMABLE_TYPE:
        return "\n".join(out), False

    resumed = False
    rest = blocks[i:]
    k = 0
    while k < len(rest):
        if rest[k].type != entry_type:
            k += 1
            continue
        j = k
        while j < len(rest) and rest[j].type == entry_type:
            j += 1
        if j - k >= _MIN_RESUME_RUN and _mostly_references(rest[k:j]):
            out.extend(b.text for b in rest[k:j])
            resumed = True
        k = j
    return "\n".join(out), resumed


def references_section(smap: SourceMap) -> str:
    """The reference-list text. See `references_span` for the resume signal."""
    return references_span(smap)[0]
