"""Three report files were written whether or not anyone wanted three.

`report.md` is the artefact almost every run is read through; the editor and
terminal looks are for sharing and for screenshots. Writing all three plus a
bundled font directory on every run was output nobody asked for, so the CLI now
writes markdown alone and takes the HTML looks on request.

`write_reports` itself still defaults to every format: it is the seam the
disclosure-parity suite drives, and that suite must keep rendering all three or
it stops testing parity. The *policy* moved to the CLI, which is where the
user's intent actually is.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402
from papertrace.models import ClaimResult, RunResults  # noqa: E402
from papertrace.report import FORMATS, write_reports  # noqa: E402


def _results() -> RunResults:
    claim = ClaimResult(id=1, claim="X causes Y.", location="Intro", refs=["1"],
                        verdict="supported", note="Stated on page 2.",
                        source_slug="a-2020", source_page=2, source_block="block_0002")
    return RunResults(manuscript="m.pdf", date="2026-01-01", claims=[claim])


# --- the library seam: unchanged by default --------------------------------


def test_write_reports_still_writes_every_format_by_default(tmp_path):
    """The parity suite calls this with no `formats` and asserts a disclosure
    reached all three looks. Narrowing this default would leave that suite
    green while it silently stopped comparing anything."""
    paths = write_reports(_results(), None, tmp_path, png=False)
    assert {p.name for p in paths} == {
        "report.md", "report_editor.html", "report_terminal.html", "report_viewer.html"
    }


def test_the_format_vocabulary_is_published(tmp_path):
    assert FORMATS == ("md", "editor", "terminal", "viewer")


# --- asking for less -------------------------------------------------------


def test_markdown_alone_writes_no_html_and_no_font_bundle(tmp_path):
    paths = write_reports(_results(), None, tmp_path, png=False, formats=("md",))
    assert {p.name for p in paths} == {"report.md"}
    assert not (tmp_path / "report_editor.html").exists()
    assert not (tmp_path / "report_terminal.html").exists()
    # the fonts exist only to make the HTML self-contained; copying ~1 MB of
    # them beside a markdown file nobody will open in a browser is litter
    assert not (tmp_path / "assets").exists()


def test_one_html_look_does_not_drag_in_the_other(tmp_path):
    paths = write_reports(_results(), None, tmp_path, png=False,
                          formats=("md", "editor"))
    assert {p.name for p in paths} == {"report.md", "report_editor.html"}
    assert not (tmp_path / "report_terminal.html").exists()
    assert (tmp_path / "assets").exists(), "an HTML look still needs its fonts"


def test_markdown_is_always_written_even_if_not_asked_for(tmp_path):
    """`report.md` is the machine-and-human readable record of the audit. A
    request for only a screenshot look must not leave the case folder without
    the report itself."""
    paths = write_reports(_results(), None, tmp_path, png=False,
                          formats=("terminal",))
    assert (tmp_path / "report.md") in paths


# --- refusing what it cannot do -------------------------------------------


def test_an_unknown_format_is_refused_rather_than_ignored(tmp_path):
    """Silently dropping an unrecognised name would answer "--format pdf" with
    a folder that has no PDF in it and no complaint — the same shape as the
    unknown-backend bug, which is why `ingest_pdf` raises there too."""
    with pytest.raises(ValueError, match="unknown report format"):
        write_reports(_results(), None, tmp_path, png=False, formats=("pdf",))


def test_no_formats_at_all_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown report format|no report format"):
        write_reports(_results(), None, tmp_path, png=False, formats=())


# --- PNG is rendered FROM the HTML, so it implies it -----------------------


def test_png_pulls_in_the_html_it_is_rendered_from(tmp_path, monkeypatch):
    """`--png --format md` cannot mean "screenshot a file I told you not to
    write". Honouring it literally would produce no PNG and say nothing."""
    shot = []
    import papertrace.render as render_mod

    monkeypatch.setattr(render_mod, "html_to_png",
                        lambda src, dst: shot.append(src.name) or False)
    write_reports(_results(), None, tmp_path, png=True, formats=("md",))
    assert (tmp_path / "report_editor.html").exists()
    assert (tmp_path / "report_terminal.html").exists()
    assert shot == ["report_editor.html", "report_terminal.html"]


# --- the CLI is where the default changed ----------------------------------


def _case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    (case / "out").mkdir(parents=True)
    _results().to_json(case / "out" / "results.json")
    return case


def test_the_report_stage_writes_markdown_alone_by_default(tmp_path):
    case = _case(tmp_path)
    cli._report_pipeline(case=case)
    assert (case / "out" / "report.md").exists()
    assert not (case / "out" / "report_editor.html").exists()
    assert not (case / "out" / "report_terminal.html").exists()


def test_a_format_flag_adds_exactly_that_look(tmp_path):
    case = _case(tmp_path)
    cli._report_pipeline(case=case, formats=["editor"])
    assert (case / "out" / "report_editor.html").exists()
    assert not (case / "out" / "report_terminal.html").exists()


def test_repeated_format_flags_accumulate(tmp_path):
    case = _case(tmp_path)
    cli._report_pipeline(case=case, formats=["editor", "terminal"])
    assert (case / "out" / "report_editor.html").exists()
    assert (case / "out" / "report_terminal.html").exists()


def test_a_mistyped_format_is_refused_before_anything_is_written(tmp_path):
    """A typo in a flag is user error, and this CLI answers user error with a
    red line and exit 2 — not a ValueError traceback. Checked before the
    results are loaded, so a bad flag cannot half-write a report folder."""
    import typer

    case = _case(tmp_path)
    with pytest.raises(typer.Exit) as exc:
        cli._report_pipeline(case=case, formats=["pdf"])
    assert exc.value.exit_code == 2
    assert not (case / "out" / "report.md").exists()
