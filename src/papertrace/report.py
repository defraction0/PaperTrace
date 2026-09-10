"""Render RunResults into the three report formats.

- report.md               plain markdown with inline evidence images (the tool's native voice)
- report_editor.html      the report open in a dark editor window
- report_terminal.html    a terminal run of the check
Optional PNGs of the two HTML looks via render.html_to_png.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from functools import partial
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

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


def _autoescape(name: str | None) -> bool:
    """Escape interpolations in the HTML looks, never in the markdown one.

    Matched on `.html.j2`, not by `select_autoescape(["html"])`, which tests for
    a name ending in `.html` — these templates end in `.j2`, so nothing ever
    matched and every format rendered unescaped. It stayed invisible because the
    one field that carries angle brackets, a Europe PMC title, arrives
    pre-escaped from the API; decoding those entities is what made it reachable.

    Cited source PDFs are downloaded from third parties and their text reaches
    the report, so this is not hypothetical. No template interpolation is meant
    to emit markup — there is no `|safe` anywhere — so escaping every one of
    them is the whole fix. Markdown is not HTML and is left alone.
    """
    return bool(name) and name.endswith((".html.j2", ".htm.j2"))


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=_autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )


# the looks a caller may ask for. "md" is the audit's record; the other two are
# for sharing and for the screenshots. Published as a tuple so the CLI's help
# text and the validation below cannot drift apart.
FORMATS = ("md", "editor", "terminal")
_HTML_FORMATS = ("editor", "terminal")


def write_reports(
    results: RunResults,
    manifest: RefManifest | None,
    out_dir: Path,
    png: bool = False,
    scout: ScoutResults | None = None,
    formats: Sequence[str] = FORMATS,
) -> list[Path]:
    """Write report.md and any requested HTML looks (+ PNGs if possible).

    `formats` defaults to every look because this is the seam the disclosure-
    parity suite drives, and that suite has to render all three or it stops
    comparing anything. The narrower default belongs to the CLI, where the
    user's intent is. `report.md` is written regardless of what was asked for:
    it is the record of the audit, not one presentation of it among three.

    An unrecognised name raises. Ignoring it would answer `--format pdf` with a
    folder containing no PDF and no complaint — the same silent-downgrade shape
    `ingest_pdf` refuses for an unknown backend.
    """
    if unknown := [f for f in formats if f not in FORMATS]:
        raise ValueError(
            f"unknown report format(s) {', '.join(map(repr, unknown))} — "
            f"expected any of {', '.join(FORMATS)}"
        )
    if not formats:
        # `report.md` is written either way, so an empty request contradicts
        # itself. Say `("md",)` and mean it.
        raise ValueError(f"no report format requested — expected any of {', '.join(FORMATS)}")
    # PNG is a screenshot OF the HTML, so asking for one without the other
    # cannot be honoured literally: it would render nothing and say nothing.
    html = [f for f in _HTML_FORMATS if f in formats] or (list(_HTML_FORMATS) if png else [])

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
        # bound here, not in the templates: the numbering taint is the only
        # claim-level disclosure that needs the manifest, and three templates
        # each threading a second argument is three chances to drop it
        "claim_disclosures": partial(claim_disclosures, manifest=manifest),
        "anchor_state": anchor_state,
        "judgement_disclosures": judgement_disclosures,
    }

    written: list[Path] = []

    md = env.get_template("report.md.j2").render(**ctx)
    (out_dir / "report.md").write_text(md)
    written.append(out_dir / "report.md")

    # bundle fonts next to the HTML so the pages are self-contained — and only
    # then: ~1 MB of typefaces beside a markdown file is litter
    assets_src = TEMPLATES / "assets"
    assets_dst = out_dir / "assets"
    if html and assets_src.exists():
        shutil.copytree(assets_src, assets_dst, dirs_exist_ok=True)

    for look in html:
        name = f"report_{look}"
        rendered = env.get_template(f"{name}.html.j2").render(**ctx)
        html_path = out_dir / f"{name}.html"
        html_path.write_text(rendered)
        written.append(html_path)
        if png:
            from .render import html_to_png

            png_path = out_dir / f"{name}.png"
            if html_to_png(html_path, png_path):
                written.append(png_path)

    return written
