"""Layout-aware ingest backend on docling (optional dependency).

Produces the full block taxonomy: real tables as GFM markdown, figures with
captions, lists as lists. The mapping core (`blocks_from_docling`) is
duck-typed and unit-tested against stubs, so the adapter's logic — label
mapping and the bottom-left → top-left bbox conversion — is verified even
where docling itself isn't installed.

Coordinate note: docling reports bounding boxes with a BOTTOMLEFT origin;
PyMuPDF (and our highlight step) use TOPLEFT. `_to_top_left` converts using
the page height — getting this wrong mirrors every red box vertically.

Fidelity note: docling deletes the hyphen when it joins a word split across two
lines, which is right for a syllabic break ("approxi-" / "mately") and wrong for
a lexical one — a page reading "Non-" / "Hispanic" arrives as "NonHispanic".
`hyphen_joins` separates the two by evidence from the document itself, never by
guessing at the joined text; `restore_hyphen_joins` repairs only what it proved.
Locating a quote on the page is a separate job, and stays in `highlight.py`.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 module name (the bare `fitz` import is deprecated)
except ImportError:  # pragma: no cover — older PyMuPDF exposes only `fitz`
    import fitz

from ..models import Block, Region

_HEADING_LABELS = {"section_header", "title"}
_LIST_LABELS = {"list_item"}
_SKIP_LABELS = {"page_header", "page_footer", "footnote"}

# the characters docling treats as a line-break hyphen and deletes: ASCII
# hyphen-minus (its `\x02` soft-hyphen marker is rewritten to this before the
# join), Unicode hyphen, non-breaking hyphen, soft hyphen
_JOIN_DASHES = "-\u2010\u2011\u00ad"
_LINE_BREAK_HYPHEN = re.compile(rf"(\w+)[{_JOIN_DASHES}]\n(\w+)")


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


def _item_regions(item, page_heights: dict[int, float]) -> list[Region]:
    """Every rectangle docling gives this item, in the order it states them.

    `prov` is a list and used to be read as `prov[0]`, so a paragraph continuing
    into the next column or onto the next page kept only the rectangle its
    opening sat in — measured on one paper, 14,367 characters outside the box
    their block claimed. Each entry carries its own `page_no`, so the height
    used to flip the origin is looked up per entry and not once per block.

    `charspan` is taken as given rather than recomputed: it is docling's own
    statement of which characters that rectangle holds, and it is the only thing
    that orders two rectangles sharing a page.
    """
    out: list[Region] = []
    for prov in getattr(item, "prov", None) or []:
        page = int(prov.page_no)
        span = getattr(prov, "charspan", None) or (0, 0)
        out.append(
            Region(
                page=page,
                bbox=_to_top_left(prov.bbox, page_heights.get(page, 842.0)),
                char_start=int(span[0]),
                char_end=int(span[1]),
            )
        )
    return out


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
        regions = _item_regions(item, page_heights)
        if not regions:
            continue
        # the first region IS `page`/`bbox` — every existing consumer reads those
        page, bbox = regions[0].page, regions[0].bbox

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
                regions=regions,
            )
        )
    return blocks


def hyphen_joins(pdf_path: Path) -> dict[int, dict[str, str]]:
    """docling's dehyphenated word → the compound the document itself proves,
    per 1-based page number.

    docling deletes the hyphen whenever it joins a word split across two lines.
    That is *right* far more often than it is wrong — "approxi-" / "mately" is
    the single word "approximately", and on the paper this was measured against
    87 of 94 breaks were of that kind — and wrong when the hyphen belongs to the
    word: "Non-" / "Hispanic" is "Non-Hispanic", never "NonHispanic".

    Nothing in the joined text tells the two apart. A lower→upper junction is
    not evidence (`HbA1c`, `PaperTrace`, `Timedependent` all have one, and only
    the last is broken), and "missioncritical" has no junction at all. So the
    document is asked instead: an entry appears only where the hyphenated form
    occurs somewhere in the PDF **unbroken**, which is the author writing the
    compound out. No evidence, no entry — the text then stays exactly as docling
    produced it rather than being repaired by guesswork.
    """
    doc = fitz.open(pdf_path)
    try:
        pages = [doc[index].get_text() for index in range(doc.page_count)]
    finally:
        doc.close()
    whole = "\n".join(pages)  # the compound may be written out on any page

    joins: dict[int, dict[str, str]] = {}
    for number, raw in enumerate(pages, start=1):
        found: dict[str, str] = {}
        for tail, head in _LINE_BREAK_HYPHEN.findall(raw):
            joined, compound = tail + head, f"{tail}-{head}"
            if compound not in whole:
                continue  # nowhere written out: no evidence the hyphen is lexical
            if re.search(rf"(?<!\w){re.escape(joined)}(?!\w)", raw):
                # this page carries the joined form as a word of its own too;
                # which of the two a rewrite would hit is unknowable
                continue
            found[joined] = compound
        if found:
            joins[number] = found
    return joins


def restore_hyphen_joins(blocks: list[Block], joins: dict[int, dict[str, str]]) -> list[Block]:
    """Put back a hyphen docling deleted, for the joins `hyphen_joins` proved.

    Word-bounded so a short join can never land inside an unrelated word, and
    scoped to the block's page so one page's break cannot rewrite another's.
    A block docling got right is returned untouched.
    """
    out: list[Block] = []
    for block in blocks:
        text = block.text
        for joined, on_page in joins.get(block.page, {}).items():
            if joined in text:
                text = re.sub(rf"(?<!\w){re.escape(joined)}(?!\w)", on_page, text)
        out.append(block if text == block.text else replace(block, text=text))
    return out


def _quiet_third_party_loggers() -> None:
    """docling's model stack (RapidOCR, torch dynamo, transformers) floods the
    terminal with INFO/WARNING logs, tqdm weight-loading bars and torch
    UserWarnings, burying the pipeline ticker — keep errors only."""
    import warnings

    for name in ("RapidOCR", "rapidocr", "torch._dynamo", "transformers", "docling"):
        logging.getLogger(name).setLevel(logging.ERROR)
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    # torch (re)configures its dynamo loggers lazily at first use, overriding
    # plain setLevel — its own env var is the only pre-import control
    had_torch_logs = "TORCH_LOGS" in os.environ
    os.environ.setdefault("TORCH_LOGS", "-dynamo,-inductor")
    # ...and calling set_logs() as well makes torch print "Using TORCH_LOGS
    # environment variable for log settings, ignoring call to set_logs" — a
    # warning that existed only because we did both. The env var wins, so it is
    # the one we keep.
    if not had_torch_logs and not os.environ.get("TORCH_LOGS"):
        _set_torch_logs()
    warnings.filterwarnings("ignore", category=UserWarning, module=r"torch\.nn\.modules\.conv")
    try:
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        hf_logging.disable_progress_bar()
    except Exception:  # noqa: BLE001 — cosmetic only, never fatal
        pass


def _set_torch_logs() -> None:
    """Quiet torch's dynamo logger through its API, for when the env var is not
    ours to set. Never called alongside TORCH_LOGS — torch warns when both are
    used, and that warning is louder than what it suppresses."""
    try:
        import logging as _logging

        import torch._logging as _tlog

        _tlog.set_logs(dynamo=_logging.ERROR)
    except Exception:  # noqa: BLE001 — cosmetic only, never fatal
        pass


@contextmanager
def _silence_model_stack():
    """Suppress third-party INFO chatter for the duration of one conversion.

    Blunt on purpose, because there is no seam to be precise in.
    `rapidocr/utils/log.py` creates its logger, calls `setLevel` on it *itself*,
    and attaches its **own** console handler — all while docling builds the OCR
    models, which happens inside a single `convert()` call. A level set before
    the import is overwritten, and a private handler emits regardless of what
    the parent logger is set to. `logging.disable` is the documented mechanism
    that holds whenever a logger appears and whatever handlers it owns.

    This is not the hiding this project forbids: what goes quiet is third-party
    model-loading chatter, never a statement PaperTrace makes about its own
    fidelity — every one of those travels by `rich` console print, not
    `logging`. WARNING and above still get through, the scope is one call, and
    the previous level is restored even when the conversion raises.
    """
    previous = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        yield
    finally:
        logging.disable(previous)


def ingest_blocks_docling(pdf_path: Path) -> tuple[int, list[Block], str]:
    """Run docling on a PDF. Returns (page_count, blocks, docling_version)."""
    _quiet_third_party_loggers()
    import docling
    from docling.document_converter import DocumentConverter

    with _silence_model_stack():
        result = DocumentConverter().convert(str(pdf_path))
    doc = result.document

    page_heights: dict[int, float] = {}
    for no, page in getattr(doc, "pages", {}).items():
        size = getattr(page, "size", None)
        if size is not None:
            page_heights[int(no)] = float(size.height)
    pages = len(page_heights) or 1

    version = getattr(docling, "__version__", "?")
    blocks = blocks_from_docling(doc, page_heights)
    # only where the paper writes the compound out somewhere: docling's join is
    # the author's word for a syllabic break, and wrong for a lexical hyphen
    blocks = restore_hyphen_joins(blocks, hyphen_joins(pdf_path))
    return pages, blocks, version
