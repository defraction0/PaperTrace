"""Ingest: PDF → clean.md + annotated.md + source_map.json.

Two backends behind one contract:

- ``pymupdf`` — always available, fast, flat text. Tables are linearized and
  figures are invisible; the source map is stamped ``converter: pymupdf`` so
  that degradation is *disclosed*, never silent.
- ``docling``  — optional (``pip install papertrace[docling]``), layout-
  aware: real tables (GFM markdown), figures with captions, lists. First run
  downloads docling's layout models (~500 MB, once).

``backend="auto"`` prefers docling when importable and falls back loudly.
Everything downstream (check, highlight, report) reads only the source-map
contract and does not care which backend produced it.
"""

from __future__ import annotations

from pathlib import Path

from ..models import Block, SourceMap
from .pymupdf_ import ingest_blocks_pymupdf, references_section

__all__ = ["ingest_pdf", "references_section", "available_backends"]


def _docling_available() -> bool:
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def available_backends() -> list[str]:
    return ["docling", "pymupdf"] if _docling_available() else ["pymupdf"]


def ingest_pdf(pdf_path: Path, out_dir: Path, backend: str = "auto") -> SourceMap:
    """Convert one PDF with the chosen backend and write the three outputs."""
    if backend == "auto":
        backend = "docling" if _docling_available() else "pymupdf"
    if backend == "docling":
        if not _docling_available():
            raise RuntimeError(
                "docling backend requested but docling is not installed — "
                "pip install 'papertrace[docling]' (heavyweight: pulls torch; "
                "first run downloads layout models)"
            )
        from .docling_ import ingest_blocks_docling

        pages, blocks, version = ingest_blocks_docling(pdf_path)
        converter = f"docling {version}"
    else:
        pages, blocks = ingest_blocks_pymupdf(pdf_path)
        converter = "pymupdf"

    smap = SourceMap(doc=pdf_path.name, pages=pages, converter=converter, blocks=blocks)
    write_outputs(smap, out_dir)
    return smap


# ---------------------------------------------------------------------------
# shared writers — one rendering for every backend
# ---------------------------------------------------------------------------


def _render_block(b: Block, annotated: bool) -> str:
    marker = f"  <!-- {b.id}, page {b.page} -->" if annotated else ""
    if b.type == "sectionheader":
        return f"## {b.text}{marker}\n"
    if b.type == "table":
        # table text is already GFM markdown — marker goes on its own line above
        head = f"<!-- {b.id}, page {b.page}, table -->\n" if annotated else ""
        return f"{head}{b.text}\n"
    if b.type == "picture":
        return f"{b.text}{marker}\n"
    if b.type == "list":
        return f"{b.text}{marker}\n"
    return f"{b.text}{marker}\n"


def write_outputs(smap: SourceMap, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    smap.to_json(out_dir / "source_map.json")
    (out_dir / "clean.md").write_text(
        "\n".join(_render_block(b, annotated=False) for b in smap.blocks)
    )
    (out_dir / "annotated.md").write_text(
        "\n".join(_render_block(b, annotated=True) for b in smap.blocks)
    )
