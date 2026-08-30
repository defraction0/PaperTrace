"""The live runner's wiring, tested without spending money.

`one_run` drives the real CLI stage functions in process, where Typer's
declared defaults are `OptionInfo` objects rather than the strings and paths
they display. Calling them positionally is therefore a trap: one inserted
parameter shifts every later argument and the shifted-in default is an
OptionInfo that satisfies no downstream check. `tests/test_wizard.py` pins the
same property for `cli.run`; this file pins it for the paid runner, which no CI
job ever executes and where the first symptom would be a wasted evaluation.

Nothing here calls a model or the network: the stages are spies and the
scoring side is stubbed.
"""

import inspect
import json

import pytest

from evals.runners import run_eval

STAGES = ("ingest", "refs", "check")


def _real_signatures() -> dict:
    """The stages' true signatures, taken before any spy replaces them.

    Typer's `@app.command()` returns the undecorated function, so these are the
    real parameter names the runner has to hit.
    """
    from papertrace import cli

    return {s: inspect.signature(getattr(cli, s)) for s in STAGES}


def _stage_spies(monkeypatch, results_path):
    """Replace the three CLI stages with spies; `check` writes results.json."""
    from papertrace import cli

    seen: dict[str, tuple] = {}

    def spy(name):
        def f(*a, **kw):
            seen[name] = (a, kw)
            if name == "check":
                results_path.parent.mkdir(parents=True, exist_ok=True)
                results_path.write_text(json.dumps(
                    {"manuscript": "m.pdf", "date": "2026-01-01", "claims": []}
                ))
        return f

    for stage in STAGES:
        monkeypatch.setattr(cli, stage, spy(stage))
    return seen


def _stub_scoring(monkeypatch):
    """The runner's own scoring side, neutralised — this file grades wiring."""
    monkeypatch.setattr(run_eval.provenance, "capture",
                        lambda *a, **kw: {})
    monkeypatch.setattr(run_eval.provenance, "check_refs_drift",
                        lambda *a, **kw: [])
    monkeypatch.setattr(run_eval.scoring, "score", lambda *a, **kw: {"set_id": "x"})
    monkeypatch.setattr(run_eval, "render", lambda record: "# EVAL\n")


def test_one_run_calls_every_stage_by_keyword(tmp_path, monkeypatch):
    """A positional call bound the backend string to `ingest`'s `case`
    parameter and left `backend` as an OptionInfo, so the run died in the
    dispatcher — `unknown ingest backend <typer.models.OptionInfo object>` —
    before any claim was judged.
    """
    signatures = _real_signatures()

    case_dir = tmp_path / "case"
    seen = _stage_spies(monkeypatch, case_dir / "out" / "results.json")
    _stub_scoring(monkeypatch)

    manuscript = tmp_path / "m.pdf"
    manuscript.write_bytes(b"%PDF-1.4\n")
    gold_path = tmp_path / "g.json"
    gold_path.write_text(json.dumps({"set_id": "demo_v1"}))

    run_eval.one_run({"set_id": "demo_v1"}, gold_path, manuscript, case_dir,
                     "some-model", tmp_path / "out", "a@b.org", "pymupdf")

    assert set(seen) == set(STAGES)
    for stage, (args, _kw) in seen.items():
        assert not args, f"{stage} is called positionally — one inserted parameter shifts it"

    # every keyword must be one the real signature accepts — the spy would
    # otherwise swallow a name the CLI does not have
    for stage, (_args, kw) in seen.items():
        signatures[stage].bind(**kw)

    assert seen["ingest"][1]["backend"] == "pymupdf"
    assert isinstance(seen["ingest"][1]["backend"], str)
    assert seen["ingest"][1]["pdf"] == manuscript
    assert seen["ingest"][1]["out"] == case_dir / "ingest" / "manuscript"

    assert seen["refs"][1]["backend"] == "pymupdf"
    assert seen["refs"][1]["manuscript"] == manuscript
    assert seen["refs"][1]["case"] == case_dir
    assert seen["refs"][1]["email"] == "a@b.org"
    assert seen["refs"][1]["parse_only"] is False

    assert seen["check"][1]["case"] == case_dir
    assert seen["check"][1]["model"] == "some-model"


def test_one_run_forwards_no_typer_sentinels(tmp_path, monkeypatch):
    """The generic form of the same defect: an OptionInfo reaching any stage
    means an argument shifted, whatever the parameter happened to be called."""
    import typer

    case_dir = tmp_path / "case"
    seen = _stage_spies(monkeypatch, case_dir / "out" / "results.json")
    _stub_scoring(monkeypatch)

    manuscript = tmp_path / "m.pdf"
    manuscript.write_bytes(b"%PDF-1.4\n")
    gold_path = tmp_path / "g.json"
    gold_path.write_text(json.dumps({"set_id": "demo_v1"}))

    run_eval.one_run({"set_id": "demo_v1"}, gold_path, manuscript, case_dir,
                     None, tmp_path / "out", None, "auto")

    for stage, (args, kw) in seen.items():
        for name, value in list(kw.items()) + list(enumerate(args)):
            assert not isinstance(value, (typer.models.OptionInfo, typer.models.ArgumentInfo)), (
                f"{stage} received Typer's sentinel for {name} — an argument shifted"
            )


def test_ci_refuses_to_spend_money(monkeypatch, capsys, tmp_path):
    """The refusal is the only thing standing between a CI job and a bill."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr("sys.argv", ["run_eval.py", "--gold", str(tmp_path / "absent.json")])

    assert run_eval.main() == 2
    assert "Refusing to run a paid" in capsys.readouterr().err


def test_allow_ci_gets_past_the_refusal(monkeypatch, tmp_path):
    """--allow-ci must actually override, or the escape hatch is decoration.
    Proven by the *next* failure — the missing gold file — not by a live run."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(
        "sys.argv",
        ["run_eval.py", "--gold", str(tmp_path / "absent.json"), "--allow-ci"],
    )

    with pytest.raises(FileNotFoundError):
        run_eval.main()
