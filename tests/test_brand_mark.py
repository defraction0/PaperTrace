"""The PaperTrace mark reaches every surface that carries the name.

A dot on a line of the paper, routed like a circuit trace into a red box
around the evidence — the same red as the evidence boxes in the reports. It
appears in three places, and each has its own failure to guard against: the
viewer inlines it, so the page must not grow a `<metadata>` block or a network
fetch; the README links to it, so the files it names must exist at the paths
it names; the terminal draws it in box glyphs, so the frame must stay a frame
once the colour markup is stripped.
"""

import re
import sys
from pathlib import Path

from rich.cells import cell_len
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import papertrace  # noqa: E402
from papertrace.brand import BANNER  # noqa: E402
from papertrace.models import ClaimResult, RunResults  # noqa: E402
from papertrace.report import write_reports  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BRAND = Path(papertrace.__file__).parent / "templates" / "brand"
# the trace: from the dot, right, down and into the box — the one path every
# variant of the mark shares
TRACE = 'd="M11.5 6 H14.5 Q16 6 16 7.5 V10.5 Q16 12 17.5 12 H19"'


def _results() -> RunResults:
    claim = ClaimResult(id=1, claim="X causes Y.", location="Intro", refs=["1"],
                        verdict="supported", source_slug="a-2020", source_page=2)
    return RunResults(manuscript="m.pdf", date="2026-01-01", claims=[claim])


# --- the files ---------------------------------------------------------------


def test_the_brand_files_ship_as_package_data():
    """Package data, not repo assets: the viewer is rendered from an installed
    wheel, and the README links into the same folder so there is one copy."""
    for name in ("papertrace-icon.svg", "papertrace-mark-light.svg",
                 "papertrace-mark-dark.svg", "papertrace-logo-light.svg",
                 "papertrace-logo-dark.svg"):
        svg = (BRAND / name).read_text()
        assert TRACE in svg, f"{name} does not carry the trace"
        assert "#e5484d" in svg, f"{name} lost the trace red"


def test_the_dark_lockup_is_the_bare_mark_with_the_wordmark_in_paper():
    """The designer's note: the bare dark mark is for dark surfaces, README
    dark mode included. The light lockup's ink wordmark is unreadable there."""
    dark = (BRAND / "papertrace-logo-dark.svg").read_text()
    light = (BRAND / "papertrace-logo-light.svg").read_text()
    assert 'viewBox="0 0 168 32"' in dark and 'viewBox="0 0 168 32"' in light
    assert ">PaperTrace</text>" in dark
    assert 'fill="#f4f1ea">PaperTrace' in dark, "the wordmark must be paper on dark"
    assert '<rect width="32" height="32"' not in dark, "no dark square on a dark surface"


# --- the viewer --------------------------------------------------------------


def test_the_viewer_header_carries_the_mark_inline(tmp_path):
    write_reports(_results(), None, tmp_path, png=False, formats=("viewer",))
    html = (tmp_path / "report_viewer.html").read_text()
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert 'class="logo"' in header and "<svg" in header and TRACE in header
    assert ">Pt<" not in html, "the placeholder square is gone"


def test_the_inlined_mark_drops_its_provenance_block_and_stays_offline(tmp_path):
    """The delivered files carry an 8 KB content-credentials manifest each;
    verbatim in the package, stripped where the page inlines them — a signed
    manifest inside an HTML page verifies nothing and bloats every report."""
    write_reports(_results(), None, tmp_path, png=False, formats=("viewer",))
    html = (tmp_path / "report_viewer.html").read_text()
    assert "<metadata" not in html and "c2pa" not in html
    assert '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' in html
    assert not re.search(r'<link rel="icon"[^>]+href="https?://', html)


# --- the README ------------------------------------------------------------


def test_the_readme_lockup_points_at_files_that_exist():
    readme = (ROOT / "README.md").read_text()
    linked = re.findall(
        r"https://raw\.githubusercontent\.com/defraction0/PaperTrace/main/"
        r"(src/papertrace/templates/brand/[\w.-]+)", readme,
    )
    assert {Path(u).name for u in linked} >= {
        "papertrace-logo-light.svg", "papertrace-logo-dark.svg"
    }, linked
    for rel in linked:
        assert (ROOT / rel).exists(), f"README links to {rel}, which is not in the repo"
    # the pixel-art logo may be named in prose (it still feeds the social
    # preview) but is no longer displayed
    assert "main/assets/logo.png" not in readme, "the pixel-art logo is still embedded"


# --- the terminal ------------------------------------------------------------


def test_the_banner_draws_the_mark_inside_an_aligned_frame():
    """Colour markup is invisible to a terminal's column count, so the frame
    is measured on the plain text, cell by cell — a `●` or `━` that a font
    renders wide still has to line up with the border."""
    plain = Text.from_markup(BANNER).plain
    lines = [ln for ln in plain.splitlines() if ln.strip()]
    assert len({cell_len(ln) for ln in lines}) == 1, [cell_len(ln) for ln in lines]
    assert lines[0].strip().startswith("┌") and lines[0].strip().endswith("┐")
    assert lines[-1].strip().startswith("└") and lines[-1].strip().endswith("┘")
    for ln in lines[1:-1]:
        assert ln.strip().startswith("│") and ln.strip().endswith("│"), ln
    assert "●" in plain and "━━━" in plain and "PaperTrace" in plain
    assert "▛▀▜" not in plain, "the old page pictogram is gone"


def test_the_banner_is_valid_rich_markup():
    text = Text.from_markup(BANNER)
    assert any(span.style == "red" for span in text.spans), "the trace is red"


def test_the_wizard_opens_with_the_banner():
    from papertrace import wizard

    src = Path(wizard.__file__).read_text()
    assert "console.print(BANNER)" in src
    assert "[bold]PaperTrace[/bold] · guided audit" not in src
