"""`ingest()` the Typer command and `_ingest_pipeline()` the logic it delegates to
are two different things now, on purpose.

A positional call to a Typer-decorated function silently receives an `OptionInfo`
sentinel instead of the value the help screen shows, because Typer only resolves
its defaults when it dispatches the call itself. That flaw already shipped twice
(`backend`, then `doi`) — see `run()`'s own comment in cli.py. `_ingest_pipeline`
has ordinary Python defaults and is keyword-only, so the same mistake now raises
immediately instead of silently taking a wrong default.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402


def test_ingest_pipeline_rejects_a_positional_call():
    with pytest.raises(TypeError):
        cli._ingest_pipeline(Path("whatever.pdf"))


def test_ingest_command_delegates_to_the_pipeline_function(tmp_path, monkeypatch):
    seen = {}

    def fake_pipeline(**kw):
        seen.update(kw)

    monkeypatch.setattr(cli, "_ingest_pipeline", fake_pipeline)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.ingest(pdf=pdf, out=None, case=None, backend="pymupdf")

    assert seen == {"pdf": pdf, "out": None, "case": None, "backend": "pymupdf"}


def test_refs_pipeline_rejects_a_positional_call():
    with pytest.raises(TypeError):
        cli._refs_pipeline(Path("whatever.pdf"))


def test_refs_command_delegates_to_the_pipeline_function(tmp_path, monkeypatch):
    seen = {}

    def fake_pipeline(**kw):
        seen.update(kw)

    monkeypatch.setattr(cli, "_refs_pipeline", fake_pipeline)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.refs(manuscript=pdf, case=None, provided=None, email=None,
             parse_only=False, backend="auto", doi=None)

    assert seen == {
        "manuscript": pdf, "case": None, "provided": None, "email": None,
        "parse_only": False, "backend": "auto", "doi": None,
    }


def test_report_pipeline_rejects_a_positional_call():
    with pytest.raises(TypeError):
        cli._report_pipeline(Path("some-case"))


def test_report_command_delegates_to_the_pipeline_function(monkeypatch, tmp_path):
    seen = {}

    def fake_pipeline(**kw):
        seen.update(kw)

    monkeypatch.setattr(cli, "_report_pipeline", fake_pipeline)

    cli.report(case=tmp_path, png=False, formats=["md"])

    assert seen == {"case": tmp_path, "png": False, "formats": ["md"]}


def test_run_hands_report_its_formats_rather_than_an_option_info(monkeypatch, tmp_path):
    """`run()` calls the pipeline function, not the Typer command. Were it still
    calling `report(...)` and omitting the new parameter, `formats` would arrive
    as an `OptionInfo` and every run would render whatever that truthy sentinel
    happened to mean — the failure this split exists to make impossible."""
    seen = {}
    monkeypatch.setattr(cli, "_report_pipeline", lambda **kw: seen.update(kw))
    for name in ("_ingest_pipeline", "_refs_pipeline", "scout", "check", "highlight"):
        monkeypatch.setattr(cli, name, lambda **kw: None)
    monkeypatch.setattr(cli, "_resolve_case", lambda case, manuscript: tmp_path)
    monkeypatch.setattr(cli, "_guard_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_open_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.run(manuscript=pdf, case=tmp_path, provided=None, email=None, model=None,
            png=False, backend="pymupdf", with_scout=False, doi=None, formats=["md"])

    assert seen["formats"] == ["md"], "run() must pass formats through explicitly"
