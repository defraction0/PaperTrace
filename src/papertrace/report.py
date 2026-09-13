"""Render RunResults into the four report formats.

- report.md               plain markdown with inline evidence images (the tool's native voice)
- report_editor.html      the report open in a dark editor window
- report_terminal.html    a terminal run of the check
- report_viewer.html      the manuscript with every audited sentence underlined, and an
                          evidence panel beside it — the case data embedded, so it works offline
Optional PNGs of the editor and terminal looks via render.html_to_png.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from functools import partial
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from . import __version__
from .disclosures import (
    Disclosure,
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

    The one value that bypasses this is the viewer's embedded case data and
    script, marked safe in `_script_json` and `_script` below — inside a
    `<script>` element entities are not decoded, so HTML escaping would be the
    wrong escaping there, and the JSON is escaped for that context instead.
    """
    return bool(name) and name.endswith((".html.j2", ".htm.j2"))


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=_autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )


# the looks a caller may ask for. "md" is the audit's record; "editor" and
# "terminal" are for sharing and for the screenshots; "viewer" is the page to
# review the audit in. Published as a tuple so the CLI's help text and the
# validation below cannot drift apart.
FORMATS = ("md", "editor", "terminal", "viewer")
_HTML_FORMATS = ("editor", "terminal", "viewer")
# the looks a PNG is a shot of. The viewer is a page you use rather than one
# you look at — a sticky header over a panel that scrolls on its own — so a
# static shot of it records nothing, and `--png` neither implies it nor
# screenshots it.
_PNG_FORMATS = ("editor", "terminal")


def write_reports(
    results: RunResults,
    manifest: RefManifest | None,
    out_dir: Path,
    png: bool = False,
    scout: ScoutResults | None = None,
    formats: Sequence[str] = FORMATS,
    annotated: str | None = None,
) -> list[Path]:
    """Write report.md and any requested HTML looks (+ PNGs if possible).

    `formats` defaults to every look because this is the seam the disclosure-
    parity suite drives, and that suite has to render all of them or it stops
    comparing anything. The narrower default belongs to the CLI, where the
    user's intent is. `report.md` is written regardless of what was asked for:
    it is the record of the audit, not one presentation of it among four.

    `annotated` is the ingested manuscript with its block markers
    (`ingest/manuscript/annotated.md`), which only the viewer reads: it is what
    lets the page underline each audited sentence in the paper itself. `None`
    means the case folder has none, and the page then shows the audited
    sentences alone and says so — it is never handed an empty string to stand
    in for a manuscript nobody ingested.

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
    html = [f for f in _HTML_FORMATS if f in formats]
    # PNG is a screenshot OF the HTML, so asking for one without the other
    # cannot be honoured literally: it would render nothing and say nothing.
    if png:
        html += [f for f in _PNG_FORMATS if f not in html]

    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env()

    # `in JUDGMENT_VERDICTS`, not `not in PIPELINE_STATES`: a junk verdict is
    # not a judgement, and must never be counted as checked
    checked = [c for c in results.claims if c.verdict in JUDGMENT_VERDICTS]
    gaps = results.gaps_by_location()
    disclosures = run_disclosures(results, manifest)
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
        "disclosures": disclosures,
        # bound here, not in the templates: the numbering taint is the only
        # claim-level disclosure that needs the manifest, and four templates
        # each threading a second argument is four chances to drop it
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

    if "viewer" in html:
        # built only when asked for: the payload carries the whole manuscript
        ctx["viewer_data"] = _script_json(
            _viewer_payload(results, manifest, scout, annotated, disclosures)
        )
        ctx["viewer_logic"] = _script("viewer_logic.js")
        ctx["viewer_app"] = _script("viewer_app.js")

    for look in html:
        name = f"report_{look}"
        rendered = env.get_template(f"{name}.html.j2").render(**ctx)
        html_path = out_dir / f"{name}.html"
        html_path.write_text(rendered)
        written.append(html_path)
        if png and look in _PNG_FORMATS:
            from .render import html_to_png

            png_path = out_dir / f"{name}.png"
            if html_to_png(html_path, png_path):
                written.append(png_path)

    return written


# --------------------------------------------------------------------------
# the viewer's embedded data
# --------------------------------------------------------------------------


def _disclosure_dict(d: Disclosure) -> dict:
    return {
        "key": d.key,
        "level": d.level,
        "token": d.token,
        "text": d.text,
        "short": d.short,
        "rows": list(d.rows),
    }


def _manifest_for_viewer(manifest: RefManifest) -> dict:
    """The retrieval manifest as the page needs it — and without `pdf_path`.

    Not `to_json`'s payload: that carries the absolute path of every PDF on
    the machine that ran the audit, and the viewer is the one report meant to
    be sent to someone else. The reference text, status and reason are what a
    reader uses to see why a source was or was not judged.
    """
    return {
        "manuscript": manifest.manuscript,
        "reference_source": manifest.reference_source,
        "numbering_verified": manifest.numbering_verified,
        "numbering_note": manifest.numbering_note,
        "unverified_from": manifest.unverified_from,
        "references_resumed": manifest.references_resumed,
        "entries": [
            {
                "num": e.num,
                "raw": e.raw,
                "doi": e.doi,
                "status": e.status,
                "reason": e.reason,
                "resolver": e.resolver,
                "slug": e.slug,
                "title_check": e.title_check,
                "supplements": [{"slug": s.slug, "verified": s.verified} for s in e.supplements],
            }
            for e in manifest.entries
        ],
        "manuscript_supplements": [
            {"slug": s.slug, "verified": s.verified} for s in manifest.manuscript_supplements
        ],
    }


def _viewer_payload(
    results: RunResults,
    manifest: RefManifest | None,
    scout: ScoutResults | None,
    annotated: str | None,
    run_level: list[Disclosure],
) -> dict:
    """Everything the page renders, decided here and only styled there.

    The disclosures travel *with* the data rather than being re-derived in the
    browser: a JavaScript copy of `disclosures.py` would be a second set of
    rules, and the parity suite could not see it drift from the other three
    looks. Claim-level entries are keyed by claim id; judgement-level entries
    are listed per claim in judgement order, because a slug alone does not
    name a judgement once a claim is checked against a work and its supplement.
    """
    disclose = partial(claim_disclosures, manifest=manifest)
    return {
        "version": __version__,
        "results": results.to_dict(),
        "annotated": annotated,
        "scout": scout.to_dict() if scout is not None else None,
        "manifest": _manifest_for_viewer(manifest) if manifest is not None else None,
        "disclosures": {
            "run": [_disclosure_dict(d) for d in run_level],
            "claims": {
                str(c.id): [_disclosure_dict(d) for d in disclose(c)] for c in results.claims
            },
            "judgements": {
                str(c.id): [
                    [_disclosure_dict(d) for d in judgement_disclosures(j)] for j in c.judgements
                ]
                for c in results.claims
            },
        },
    }


def _script_json(payload: dict) -> Markup:
    """JSON that is safe inside a `<script>` element, marked so.

    HTML entities are not decoded inside `<script>`, so autoescape's `&lt;`
    would reach the page as six literal characters and `JSON.parse` would
    choke on them. The hazard in that context is different: a literal
    `</script>` in a cited source's text ends the element, and whatever the
    text says next runs as script. So every `<`, `>` and `&` is written as a
    JSON `\\uXXXX` escape instead — the parser reads the character back, the
    HTML parser never sees a tag — and the two line separators JavaScript's
    string literals used to reject go the same way.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for ch, esc in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ):
        text = text.replace(ch, esc)
    return Markup(text)


def _script(name: str) -> Markup:
    """One of the viewer's own scripts, inlined so the page is a single file.

    Package data read at render time, never a `<script src>`: an opened report
    must not depend on a sibling file that did not travel with it, and never
    on the network.
    """
    return Markup((TEMPLATES / name).read_text())
