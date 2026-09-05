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
