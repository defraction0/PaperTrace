"""Render RunResults into the three report formats.

- report.md               plain markdown with inline evidence images (the tool's native voice)
- report_editor.html      the report open in a dark editor window
- report_terminal.html    a terminal run of the check
Optional PNGs of the two HTML looks via render.html_to_png.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__
from .disclosures import (
    anchor_state,
    claim_disclosures,
    judgement_disclosures,
    run_disclosures,
)
from .models import JUDGMENT_VERDICTS, RefManifest, RunResults, ScoutResults

# package data, not a repo-relative path: an installed wheel has no repo
TEMPLATES = Path(str(resources.files("papertrace") / "templates"))


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def write_reports(
    results: RunResults,
    manifest: RefManifest | None,
    out_dir: Path,
    png: bool = True,
    scout: ScoutResults | None = None,
) -> list[Path]:
    """Write report.md + both HTML looks (+ PNGs if possible). Returns paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env()

    # `in JUDGMENT_VERDICTS`, not `not in PIPELINE_STATES`: a junk verdict is
    # not a judgement, and must never be counted as checked
    checked = [c for c in results.claims if c.verdict in JUDGMENT_VERDICTS]
    gaps = results.gaps_by_location()
    ctx = {
        "r": results,
        "counts": results.counts(),
        "checked": checked,
        "with_evidence": [c for c in checked if c.evidence_image],
        "gaps": gaps,
        "gap_total": sum(len(v) for v in gaps.values()),
        "manifest": manifest,
        "scout": scout,
        "version": __version__,
        # disclosures are decided here, once, and only styled by the templates —
        # a format cannot silently drop one without failing the parity test
        "disclosures": run_disclosures(results, manifest),
        "claim_disclosures": claim_disclosures,
        "anchor_state": anchor_state,
        "judgement_disclosures": judgement_disclosures,
    }

    written: list[Path] = []

    md = env.get_template("report.md.j2").render(**ctx)
    (out_dir / "report.md").write_text(md)
    written.append(out_dir / "report.md")

    # bundle fonts next to the HTML so the pages are self-contained
    assets_src = TEMPLATES / "assets"
    assets_dst = out_dir / "assets"
    if assets_src.exists():
        shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)

    for name in ("report_editor", "report_terminal"):
        html = env.get_template(f"{name}.html.j2").render(**ctx)
        html_path = out_dir / f"{name}.html"
        html_path.write_text(html)
        written.append(html_path)
        if png:
            from .render import html_to_png

            png_path = out_dir / f"{name}.png"
            if html_to_png(html_path, png_path):
                written.append(png_path)

    return written
