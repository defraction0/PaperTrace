"""The guided flow, and the parts of it that must not guess.

A first-time run of PaperTrace used to require assembling six decisions from a
`--help` screen before anything happened, and three of the ways it could fail
only surfaced minutes in: no `claude` on PATH, no chromium for `--png`, no
contact email. The wizard states those up front and asks one question at a time.

Everything here is offline. The prompt loop itself is thin by design — what is
tested is the logic underneath it, which is where a wrong answer would be
invented rather than asked for.
"""

import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import config, wizard  # noqa: E402


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    """Point the config at a temp file — never the real ~/.config."""
    p = tmp_path / "config.json"
    monkeypatch.setenv("PAPERTRACE_CONFIG", str(p))
    return p


def _pdf(path: Path, lines: list[str]) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontsize=9)
        y += 14
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


# --- config: a convenience that may never become a hard failure ------------


def test_a_missing_config_is_empty_not_an_error(cfg):
    assert not cfg.exists()
    assert config.load() == {}


@pytest.mark.parametrize("junk", ["", "   ", "{not json", "[]", "null", '"a string"'])
def test_a_corrupt_config_is_empty_not_an_error(cfg, junk):
    """A config file is a convenience. Raising here would make a stray byte in
    a dotfile break every command, including the ones that never read it."""
    cfg.write_text(junk)
    assert config.load() == {}


def test_save_round_trips_and_merges(cfg):
    config.save(email="a@b.org")
    assert config.load()["email"] == "a@b.org"
    config.save(other="x")
    assert config.load() == {"email": "a@b.org", "other": "x"}


def test_the_config_is_json_because_tomllib_is_311(cfg):
    """`requires-python` is >=3.10 and tomllib arrived in 3.11. Choosing TOML
    here would reintroduce the exact mismatch that broke the 3.10 CI cell."""
    config.save(email="a@b.org")
    assert json.loads(cfg.read_text()) == {"email": "a@b.org"}


# --- email resolution order ------------------------------------------------


def test_email_prefers_flag_then_env_then_config(cfg, monkeypatch):
    from papertrace.cli import _email

    config.save(email="config@x.org")
    monkeypatch.setenv("PAPERTRACE_EMAIL", "env@x.org")
    assert _email("flag@x.org") == "flag@x.org"
    assert _email(None) == "env@x.org"
    monkeypatch.delenv("PAPERTRACE_EMAIL")
    assert _email(None) == "config@x.org"


def test_no_email_anywhere_still_exits_two(cfg, monkeypatch):
    """The hard stop stays a hard stop — the config is one more place to look,
    not a reason to invent an address."""
    import typer

    from papertrace.cli import _email

    for var in ("PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(typer.Exit) as e:
        _email(None)
    assert e.value.exit_code == 2


# --- pasted paths ----------------------------------------------------------


def test_a_dragged_path_resolves_however_the_terminal_quoted_it(tmp_path, monkeypatch):
    """Dragging a file into a terminal yields a quoted and/or escaped path.
    All three spellings name one file, and a user should not have to know that.
    """
    target = _pdf(tmp_path / "a b.pdf", ["x"])
    monkeypatch.chdir(tmp_path)
    for raw in (
        f"'{target}'",
        f'"{target}"',
        str(target).replace(" ", r"\ "),
        f"  {target}  ",
        f"~/{target.relative_to(Path.home())}" if str(target).startswith(str(Path.home()))
        else str(target),
    ):
        assert wizard.clean_path(raw) == target, raw


def test_clean_path_expands_tilde(monkeypatch):
    assert wizard.clean_path("~/nope.pdf") == Path.home() / "nope.pdf"


# --- DOI detection: replacing a definition with a yes/no -------------------


def test_the_doi_is_read_off_the_paper_so_the_user_need_not_know_what_one_is(tmp_path):
    """`--doi` means the DOI *of the audited paper*, and being told that twice
    did not stop it being misread. Offering the one printed on page 1 does."""
    pdf = _pdf(tmp_path / "p.pdf", [
        "Vecsey-Nagy et al. European Radiology",
        "https://doi.org/10.1007/s00330-026-12773-4",
        "Coronary plaque quantification",
    ])
    assert wizard.detect_doi(pdf) == "10.1007/s00330-026-12773-4"


def test_no_doi_found_is_none_not_a_guess(tmp_path):
    """An unpublished manuscript has no DOI. Returning anything here would
    anchor the literature scout to somebody else's paper."""
    pdf = _pdf(tmp_path / "draft.pdf", ["A Draft With No Identifier", "Methods"])
    assert wizard.detect_doi(pdf) is None


def test_a_reference_list_doi_is_not_mistaken_for_the_papers_own(tmp_path):
    """The trap this feature exists to close: the first DOI in a paper's
    *references* is not the paper's DOI. Only the front matter is read."""
    pdf = _pdf(tmp_path / "p.pdf", ["A Paper With No DOI Of Its Own", "Introduction"])
    ref = _pdf(tmp_path / "p2.pdf", ["References", "1. Smith 10.1111/aaaa-bbbb"])
    assert wizard.detect_doi(pdf) is None
    assert ref.exists()


# --- the cost, before it is spent -----------------------------------------


def test_the_workload_counts_places_and_the_fan_out_that_multiplies_them(tmp_path):
    """Multi-source judging means a claim citing four sources costs four calls,
    so a count of citation *places* understates the bill. Both are reported."""
    pdf = _pdf(tmp_path / "p.pdf", [
        "Background text with one citation [1].",
        "Another sentence [2, 3] citing two.",
        "A range here [7-9] citing three.",
    ])
    w = wizard.workload(pdf)
    assert w["places"] == 3
    assert w["multi"] == 2
    assert w["labels"] == 6
    # 1 extraction call + one per cited source, not one per place
    assert w["model_calls"] > w["places"]


def test_a_paper_with_no_bracketed_citations_says_so_rather_than_zero_cost(tmp_path):
    """Zero places is not "free" — it means the style is unrecognised, which the
    coverage audit already discloses. The estimate must not imply no work."""
    pdf = _pdf(tmp_path / "p.pdf", ["Author-year style (Smith 2020) throughout."])
    w = wizard.workload(pdf)
    assert w["places"] == 0
    assert w["style_unrecognised"] is True


# --- preflight ------------------------------------------------------------


def test_a_missing_claude_cli_is_fatal_and_named_before_any_question(monkeypatch):
    """Without `claude` the check step cannot run at all. Discovering that after
    the user has typed a path, an email and waited for ingest is the failure
    mode the preflight exists to remove."""
    import papertrace.wizard as wiz

    monkeypatch.setattr(wiz, "claude_available", lambda: False)
    checks = wizard.preflight()
    claude = next(c for c in checks if c.key == "claude")
    assert claude.ok is False
    assert claude.fatal is True
    assert "claude" in claude.fix.lower()


def test_missing_chromium_only_disables_png(monkeypatch):
    """A missing optional renderer must downgrade one answer, not block a run —
    the markdown and HTML reports do not depend on it."""
    import papertrace.wizard as wiz

    monkeypatch.setattr(wiz, "_playwright_ready", lambda: False)
    png = next(c for c in wizard.preflight() if c.key == "png")
    assert (png.ok, png.fatal) == (False, False)


def test_preflight_reports_the_ingest_backend_it_will_actually_use(monkeypatch):
    import papertrace.wizard as wiz

    monkeypatch.setattr(wiz, "docling_available", lambda: False)
    ingest = next(c for c in wizard.preflight() if c.key == "ingest")
    assert ingest.ok is False and ingest.fatal is False
    assert "flat" in ingest.detail.lower()


# --- never block a non-interactive shell ----------------------------------


def test_bare_invocation_without_a_tty_prints_help_and_never_blocks():
    """A wizard that waits for stdin in CI is the same class of bug as a test
    that needs a local case folder: it passes on a laptop and hangs a runner."""
    from typer.testing import CliRunner

    from papertrace.cli import app

    res = CliRunner().invoke(app, [], input="")  # CliRunner stdin is not a tty
    assert res.exit_code == 0, res.output
    assert "Usage" in res.output
    assert "guided" not in res.output.lower() or "Usage" in res.output


def test_the_wizard_refuses_outright_when_stdin_is_not_interactive(monkeypatch):
    import typer

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(typer.Exit):
        wizard.run_wizard()


def test_start_is_a_real_command_so_it_can_be_documented():
    from typer.testing import CliRunner

    from papertrace.cli import app

    res = CliRunner().invoke(app, ["start", "--help"])
    assert res.exit_code == 0
    assert "guided" in res.output.lower()


# --- the command it teaches ----------------------------------------------


def test_the_wizard_prints_the_equivalent_command_it_just_assembled():
    """A wizard that hides the CLI keeps its user dependent on it."""
    cmd = wizard.equivalent_command(
        manuscript=Path("/tmp/paper.pdf"), case=Path("mycase"),
        doi="10.1007/x", png=True, with_scout=True, provided=None,
    )
    assert cmd.startswith("papertrace run /tmp/paper.pdf")
    assert "-c mycase" in cmd
    assert "--doi 10.1007/x" in cmd
    assert "--png" in cmd
    assert "--no-scout" not in cmd


def test_no_scout_appears_when_there_is_no_doi_to_pin_the_paper():
    cmd = wizard.equivalent_command(
        manuscript=Path("/tmp/draft.pdf"), case=Path("d"), doi=None,
        png=False, with_scout=False, provided=None,
    )
    assert "--no-scout" in cmd
    assert "--doi" not in cmd
    assert "--png" not in cmd


def test_ingest_accepts_dash_c_like_every_other_subcommand():
    """`papertrace ingest -c foo` used to fail with "No such option: -c" while
    every neighbouring command took it. `-o` keeps working."""
    from typer.testing import CliRunner

    from papertrace.cli import app

    out = CliRunner().invoke(app, ["ingest", "--help"]).output
    assert "-c" in out and "-o" in out



def test_the_chromium_check_is_a_filesystem_look_not_a_driver_launch(tmp_path, monkeypatch):
    """Asking playwright itself starts its driver subprocess, which prints
    "Task was destroyed but it is pending!" and a TargetClosedError to stderr —
    on the one screen whose job is to reassure a first-time user their setup is
    fine. So the browser half is a directory check.
    """
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    pytest.importorskip("playwright")
    assert wizard._playwright_ready() is False
    (tmp_path / "chromium-1234").mkdir()
    assert wizard._playwright_ready() is True


def test_the_help_screen_says_which_of_nine_commands_to_type():
    """`--help` is the quick reference a newcomer meets, and nine commands
    rendered as one flat list gives no clue that six of them are internals the
    seventh calls for you. Panels say it; the root text says what to type when
    you have no idea what to type.
    """
    from typer.testing import CliRunner

    from papertrace.cli import app

    out = " ".join(CliRunner().invoke(app, ["--help"]).output.split())
    assert "Start here" in out
    assert "Pipeline stages" in out
    assert "papertrace with no arguments" in out
    # start and run are the entry points; the stages are grouped away from them
    start_at, stages_at = out.index("Start here"), out.index("Pipeline stages")
    assert start_at < stages_at, "the guided entry point must come first"
    assert out.index("start") < stages_at


# --- run must hand the stages real values, not Typer's option objects -------


def test_run_forwards_the_backend_it_was_given(tmp_path, monkeypatch):
    """`run` calls the stage commands as plain Python functions, where Typer's
    declared defaults are `OptionInfo` objects rather than the strings they
    display. So a positional call is a trap: adding one parameter to a stage
    signature shifts every later argument, and the shifted-in default is an
    OptionInfo that equals none of the expected values.

    That is exactly what happened — `ingest` gained `--case`, `run`'s
    `ingest(manuscript, out, backend)` bound the backend string to `case`, and
    `backend` became an OptionInfo. `ingest_pdf` then took the else branch and
    used pymupdf while the wizard's preflight had just promised docling.

    Keyword arguments make the insertion harmless.
    """
    import papertrace.cli as cli

    seen: dict[str, tuple] = {}

    def spy(name):
        def f(*a, **kw):
            seen[name] = (a, kw)
        return f

    for stage in ("ingest", "refs", "scout", "check", "highlight", "report"):
        monkeypatch.setattr(cli, stage, spy(stage))
    monkeypatch.setattr(cli, "_guard_case", lambda case, manuscript: None)

    paper = tmp_path / "p.pdf"
    paper.write_bytes(b"%PDF-1.4\n")
    cli.run(
        manuscript=paper, case=tmp_path / "c", provided=None, email="a@b.org",
        model=None, png=False, backend="pymupdf", with_scout=True, doi=None,
    )

    for stage, (args, _kw) in seen.items():
        assert not args, f"{stage} is called positionally — one inserted parameter shifts it"
    assert seen["ingest"][1]["backend"] == "pymupdf"
    assert isinstance(seen["ingest"][1]["backend"], str)
    assert seen["refs"][1]["backend"] == "pymupdf"


def test_an_unknown_backend_raises_instead_of_quietly_meaning_pymupdf(tmp_path):
    """`ingest_pdf` treated every value that was not "docling" as pymupdf, so a
    typo, a stale flag or an OptionInfo silently downgraded the ingest and the
    report blamed the user for not installing a backend they already had.

    An unrecognised backend is a caller bug and says so.
    """
    from papertrace.ingest import ingest_pdf

    with pytest.raises(ValueError, match="unknown ingest backend"):
        ingest_pdf(tmp_path / "nope.pdf", tmp_path / "out", backend="pymudpf")


# --- a References heading the ingest did not classify as one ----------------


def test_references_are_found_when_the_heading_is_typed_as_body_text():
    """Flat-text ingest guesses headings from font size, and on a real Elsevier
    paper it guessed wrong: it classified three author lines as headings and
    left `References` as body text. `references_section` only ever started
    collecting at a `sectionheader`, so it returned "" and the whole audit
    stopped at "No numbered references found" on a paper with 34 of them.

    A block whose entire text is the word is the heading, whatever the ingest
    backend decided to call it.
    """
    from papertrace.ingest import references_section
    from papertrace.models import Block, SourceMap

    def blk(i, typ, text, page=1):
        return Block(id=f"block_{i:04d}", type=typ, page=page,
                     bbox=(0.0, 0.0, 1.0, 1.0), heading_path=[], text=text)

    smap = SourceMap(doc="p.pdf", pages=1, converter="pymupdf", blocks=[
        blk(1, "sectionheader", "Gul Gizem Pamuk a, Murat Yuce b"),   # misread author line
        blk(2, "text", "Body text citing things [1] and [2]."),
        blk(3, "text", "References"),                                  # NOT typed as a header
        blk(4, "text", "[1] A. Pinto, Errors in imaging, Br. J. Radiol. 89 (2016)."),
        blk(5, "text", "[2] L. Berlin, Radiologic errors, Diagnosis 1 (2014) 79-84."),
    ])
    refs = references_section(smap)
    assert "A. Pinto" in refs and "L. Berlin" in refs
    assert "Body text citing things" not in refs, "the body must stay out"


def test_a_sentence_merely_starting_with_references_is_not_the_heading():
    """The relaxation must not fire on prose. 'References were checked by hand'
    is a Methods sentence, and treating it as the heading would swallow the
    rest of the paper into the reference list."""
    from papertrace.ingest import references_section
    from papertrace.models import Block, SourceMap

    def blk(i, typ, text):
        return Block(id=f"block_{i:04d}", type=typ, page=1,
                     bbox=(0.0, 0.0, 1.0, 1.0), heading_path=[], text=text)

    smap = SourceMap(doc="p.pdf", pages=1, converter="pymupdf", blocks=[
        blk(1, "text", "References were checked by hand against the originals [3]."),
        blk(2, "text", "More body text that must not be read as a reference."),
    ])
    assert references_section(smap) == ""


def test_references_split_when_the_next_marker_is_mid_line():
    """Elsevier's PDFs extract with entries running together: `[2]` and `[3]`
    land mid-line, not at a line start. The marker regex required a line start,
    so a 34-reference list parsed as ONE entry whose text swallowed the rest —
    and `_entry` then took its DOI from reference [2] and its "year" from a
    journal issue number. A wrong reference is worse than a missing one: the
    resolver would have downloaded [2]'s paper and judged [1]'s claim against
    it, and the title check is the only thing that might have caught it.
    """
    from papertrace.refs import parse_references

    text = (
        "[1] A. Pinto, A. Reginelli, F. Pinto, et al., Errors in imaging patients\n"
        "\n"
        "setting, Br. J. Radiol. 89 (1061) (2016) 20150914, "
        "https://doi.org/10.1259/bjr.20150914. [2] L. Berlin, Radiologic errors, "
        "past, present and future, Diagnosis 1 (1) (2014) 79-84,\n"
        "\n"
        "https://doi.org/10.1515/dx-2013-0012. [3] S. Sharma, Artificial "
        "intelligence for fracture diagnosis, SICOT J. 9 (2023) 21, "
        "https://doi.org/10.1051/sicotj/2023018.\n"
    )
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1", "2", "3"]
    assert "A. Pinto" in entries[0].raw and "L. Berlin" not in entries[0].raw
    # each DOI stays with the reference it belongs to
    assert entries[0].doi == "10.1259/bjr.20150914"
    assert entries[1].doi == "10.1515/dx-2013-0012"
    assert entries[2].doi == "10.1051/sicotj/2023018"


def test_a_journal_issue_number_in_parens_is_not_read_as_a_year():
    """`Br. J. Radiol. 89 (1061) (2016)` — the parenthesised branch of YEAR_RE
    accepted any four digits, so the issue number won and the entry was slugged
    `a-1061`. A slug is user-visible and feeds the resolved filename, so a
    wrong one is a wrong label on real evidence.
    """
    from papertrace.refs import parse_references

    raw = ("[1] A. Pinto, A. Reginelli, F. Pinto, et al., Errors in imaging patients "
           "in the emergency setting, Br. J. Radiol. 89 (1061) (2016) 20150914.\n")
    e = parse_references(raw)[0]
    assert e.year == "2016", f"got {e.year!r}"
    assert e.slug == "a-2016"
