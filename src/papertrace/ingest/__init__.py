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

from ..models import Block, SourceMap, manuscript_fingerprint
from .pymupdf_ import (
    declared_title,
    ingest_blocks_pymupdf,
    references_section,
    references_span,
)

__all__ = ["ingest_pdf", "references_section", "references_span",
           "references_span_flat", "available_backends", "resolve_backend"]


def _docling_available() -> bool:
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def available_backends() -> list[str]:
    return ["docling", "pymupdf"] if _docling_available() else ["pymupdf"]


def resolve_backend(backend: str) -> str:
    """Turn a backend *request* into the backend that will actually run.

    Shared because two readers need it and a second copy of this rule is how
    they would drift: `ingest_pdf` dispatches on it, and `check._stale_ingest`
    compares it against the converter a source map records — where `"auto"` is
    not a converter name, so comparing the request literally would report every
    existing map as stale and re-ingest the whole reference list every run.

    NOT a silent downgrade to pymupdf for an unrecognised value. Treating one
    as flat text is what hid a caller passing a Typer OptionInfo instead of a
    backend name: the run ingested as flat text and then told the user to
    install a layout backend they already had.
    """
    if backend == "auto":
        return "docling" if _docling_available() else "pymupdf"
    if backend not in ("docling", "pymupdf"):
        raise ValueError(
            f"unknown ingest backend {backend!r} — expected 'auto', 'docling' or 'pymupdf'"
        )
    return backend


def ingest_pdf(pdf_path: Path, out_dir: Path, backend: str = "auto") -> SourceMap:
    """Convert one PDF with the chosen backend and write the three outputs."""
    backend = resolve_backend(backend)
    if backend == "docling":
        if not _docling_available():
            raise RuntimeError(
                "docling backend requested but docling is not installed — "
                "pip install 'papertrace[docling]' (heavyweight: pulls torch; "
                "first run downloads layout models)"
            )
        from .docling_ import ingest_blocks_docling

        pages, blocks, version, table_warnings = ingest_blocks_docling(pdf_path)
        converter = f"docling {version}"
    else:
        pages, blocks = ingest_blocks_pymupdf(pdf_path)
        converter = "pymupdf"
        # `[]`, not None: this backend linearises tables with no model to lose
        # cells in, so "watched and nothing lost" is the honest answer
        table_warnings = []

    # content identity, because `doc` is not one: a cited source is stored as
    # `<slug>.pdf`, so every source map in a case names a different paper the
    # same way, and a directory named after a slug was trusted to hold whatever
    # it held
    smap = SourceMap(
        doc=pdf_path.name,
        pages=pages,
        converter=converter,
        blocks=blocks,
        source_sha256=manuscript_fingerprint(pdf_path),
        # what the file says its title is — the layout's first heading is the
        # article-type banner often enough that it cannot be the first choice
        declared_title=declared_title(pdf_path),
        table_warnings=table_warnings,
    )
    write_outputs(smap, out_dir)
    return smap


def references_span_flat(pdf_path: Path) -> tuple[str, bool]:
    """A second, independent reading of the bibliography, from flat pymupdf text.

    Same contract as `references_span`: the reference-list text, and whether it
    was picked up again after an intervening section. Built by running the
    pymupdf block extraction and handing its blocks straight to
    `references_span`, which reads only `smap.blocks` — nothing else on the
    `SourceMap` needs to be genuine, so no converter tag, no fingerprint, no
    declared title are computed here.

    Exists because docling and pymupdf lose different things. Docling's table
    model can render bibliography rows as a GFM table and misplace the numeral
    column; pymupdf never renders a table at all, so it cannot make that
    mistake — it has its own instead (a multi-column layout's reading order).
    Two readings whose failure modes do not overlap are worth checking against
    each other, and cheaply: this is free on a docling run and identical to the
    existing parse on a pymupdf one.

    **Writes nothing, ever.** This is a READING, not an ingest — it has to be
    cheap enough to run on every docling run without asking permission, so it
    never calls `write_outputs`, takes no `out_dir`, and touches neither a
    model nor the network. `--parse-only` already carries the scar of a
    read that forgot this distinction: it used to risk overwriting the case's
    own `ingest/manuscript/source_map.json` with a reading of a paper the run
    was not finishing (`cli.py:543-549`, fixed by reading into a scratch
    `tempfile.TemporaryDirectory()` instead). A second reading taken purely to
    vote on a citation label must not reopen that risk, so it is given no
    directory to write into at all.
    """
    pages, blocks = ingest_blocks_pymupdf(pdf_path)
    smap = SourceMap(doc=pdf_path.name, pages=pages, blocks=blocks)
    return references_span(smap)


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
