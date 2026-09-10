"""`papertrace init` then `papertrace run paper.pdf` used to leave an orphaned
`case/sources/` — `run` and `refs` name their own folder after the paper, so a
hand-made `./case/` is only ever used again if the user remembers `-c`.

`init --for <paper>` closes that gap by naming the folder the same way `run`
would, so a plain follow-up `papertrace run paper.pdf` finds it automatically.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from typer.testing import CliRunner  # noqa: E402

from papertrace.cli import app, default_case  # noqa: E402


def _pdf(tmp_path: Path, name: str = "zhang2025.pdf") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    return pdf


def test_init_with_no_arguments_still_makes_the_legacy_case_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["init"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "case" / "sources").is_dir()


def test_init_for_a_paper_names_the_folder_the_way_run_would(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf = _pdf(tmp_path)
    res = CliRunner().invoke(app, ["init", "--for", str(pdf)])
    assert res.exit_code == 0, res.output
    expected = default_case(pdf)
    assert (expected / "sources").is_dir()
    assert not (tmp_path / "case").exists()


def test_init_for_a_paper_says_run_will_find_it_automatically(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf = _pdf(tmp_path)
    out = " ".join(CliRunner().invoke(app, ["init", "--for", str(pdf)]).output.split())
    assert "automatically" in out.lower()
    assert "-c" not in out


def test_an_explicit_case_folder_still_wins_over_for(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdf = _pdf(tmp_path)
    res = CliRunner().invoke(app, ["init", "chosen-name", "--for", str(pdf)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "chosen-name" / "sources").is_dir()
    assert not default_case(pdf).exists()
