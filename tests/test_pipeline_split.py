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

from papertrace import check as check_mod  # noqa: E402
from papertrace import cli  # noqa: E402
from papertrace.models import ClaimExtraction, RefEntry, RefManifest  # noqa: E402


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
             parse_only=False, backend="auto", doi=None, supplement=None, llm_refs=True,
             model=None, max_claims=None, max_sources=None)

    assert seen == {
        "manuscript": pdf, "case": None, "provided": None, "email": None,
        "parse_only": False, "backend": "auto", "doi": None, "supplement": None,
        "llm_refs": True,
        # `--model` reaches the reference-list reading through here. Without it
        # that call took the account default while judging used the pinned
        # model — one demo run judged with opus and read the bibliography with
        # haiku, which is what `--model` exists to prevent.
        "model": None,
        "claims": None, "max_sources": None,
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
    for name in ("_ingest_pipeline", "_extract_pipeline", "_refs_pipeline", "_check_pipeline",
                 "scout", "highlight"):
        monkeypatch.setattr(cli, name, lambda **kw: None)
    monkeypatch.setattr(cli, "_resolve_case", lambda case, manuscript: tmp_path)
    monkeypatch.setattr(cli, "_guard_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_open_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    # not what is under test, and it must not be read from the machine: `_email`
    # falls back to a SAVED CONFIG in the developer's home, so this test passed
    # locally and failed on every CI python at `raise typer.Exit(2)`
    monkeypatch.setattr(cli, "_email", lambda v: "t@example.org")
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.run(manuscript=pdf, case=tmp_path, provided=None, email=None, model=None,
            png=False, backend="pymupdf", with_scout=False, doi=None, formats=["md"])

    assert seen["formats"] == ["md"], "run() must pass formats through explicitly"


def test_run_pipeline_rejects_a_positional_call():
    with pytest.raises(TypeError):
        cli._run_pipeline(Path("whatever.pdf"))


def test_run_command_delegates_every_parameter_to_the_pipeline_function(monkeypatch, tmp_path):
    """`run` gains a second caller — the MCP server's audit job — which is the
    moment `cli.py`'s own comment names for splitting a stage: a caller that
    omits an option hands a Typer command an `OptionInfo` for it, and a plain
    keyword-only function has ordinary defaults instead.

    The signature comparison is what keeps the adapter whole: a parameter added
    to one and not the other turns this red rather than reaching the pipeline as
    its default without anyone deciding that."""
    import inspect

    # read before the patch below replaces it with a `**kw` lambda
    pipeline_params = set(inspect.signature(cli._run_pipeline).parameters)
    seen = {}
    monkeypatch.setattr(cli, "_run_pipeline", lambda **kw: seen.update(kw))
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    own = tmp_path / "p-supplement.pdf"

    cli.run(manuscript=pdf, case=tmp_path, provided=None, email="t@example.org",
            model="claude-opus-5", png=False, backend="pymupdf", with_scout=False,
            doi="10.1/x", formats=["viewer"], supplement=[own], llm_refs=False,
            max_claims=3, max_sources=2)

    assert seen == {
        "manuscript": pdf, "case": tmp_path, "provided": None, "email": "t@example.org",
        "model": "claude-opus-5", "png": False, "backend": "pymupdf", "with_scout": False,
        "doi": "10.1/x", "formats": ["viewer"], "supplement": [own], "llm_refs": False,
        "max_claims": 3, "max_sources": 2,
    }
    assert set(inspect.signature(cli.run).parameters) == pipeline_params


def test_check_pipeline_rejects_a_positional_call():
    with pytest.raises(TypeError):
        cli._check_pipeline(Path("some-case"))


def test_check_command_delegates_to_the_pipeline_function(monkeypatch, tmp_path):
    seen = {}

    def fake_pipeline(**kw):
        seen.update(kw)

    monkeypatch.setattr(cli, "_check_pipeline", fake_pipeline)

    cli.check(case=tmp_path, model=None, backend="pymupdf", max_claims=3)

    # `--max-claims 3` is the array [1, 2, 3] by the time it reaches the stage:
    # the array is the contract, the flag is one way of writing it
    assert seen == {"case": tmp_path, "model": None, "backend": "pymupdf", "claims": [1, 2, 3]}

    cli.check(case=tmp_path, model=None, backend="pymupdf", max_claims=None)
    assert seen["claims"] is None


def test_run_hands_check_its_backend_rather_than_an_option_info(monkeypatch, tmp_path):
    """`check` gains `--backend` because the cited sources are now read with the
    same backend as the paper. `run()` must pass it, or the sources would be
    ingested against an `OptionInfo` — and `ingest_pdf` refuses an unknown
    backend loudly, so a whole audit would fail at the judging step."""
    seen = {}
    monkeypatch.setattr(cli, "_check_pipeline", lambda **kw: seen.update(kw))
    for name in ("_ingest_pipeline", "_extract_pipeline", "_refs_pipeline", "_report_pipeline",
                 "scout", "highlight"):
        monkeypatch.setattr(cli, name, lambda **kw: None)
    monkeypatch.setattr(cli, "_resolve_case", lambda case, manuscript: tmp_path)
    monkeypatch.setattr(cli, "_guard_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_open_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    # not what is under test, and it must not be read from the machine: `_email`
    # falls back to a SAVED CONFIG in the developer's home, so this test passed
    # locally and failed on every CI python at `raise typer.Exit(2)`
    monkeypatch.setattr(cli, "_email", lambda v: "t@example.org")
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.run(manuscript=pdf, case=tmp_path, provided=None, email=None, model=None,
            png=False, backend="docling", with_scout=False, doi=None, formats=["md"])

    assert seen["backend"] == "docling", "run() must forward the backend to check"


def test_check_claims_will_not_default_its_backend():
    """`check_claims` reads every cited source, so its backend decides whether
    a table in a source is readable at all. There is no honest default: `auto`
    silently pulls docling into an offline test run, and `pymupdf` silently
    downgrades a caller who asked for layout. So it is required, like `_clip`'s
    truncation accumulator in the same module and for the same reason — no
    future call site can omit it and quietly get the wrong one."""
    import inspect

    from papertrace.check import check_claims

    p = inspect.signature(check_claims).parameters["backend"]
    assert p.default is inspect.Parameter.empty, "backend must not have a default"
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


# --- withholding a disputed label -------------------------------------------
# The wire the `check_claims` tests cannot see for themselves. `_check_pipeline`
# is where `RefManifest.labels_disputed` (Task 3) actually reaches
# `check_claims`'s `disputed` parameter (Task 4); `check_claims` can only prove
# what happens once the argument has arrived.


def test_check_pipeline_passes_the_manifests_disputed_labels_to_check_claims(
    tmp_path, monkeypatch
):
    """If this regresses, a label Task 3 marked disputed reaches judgement
    anyway — the withholding built into `check_claims` never fires, because
    the CLI never told it which labels to withhold."""
    case = tmp_path / "case"
    case.mkdir()
    RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="9", raw="ref", status="retrieved", slug="x-2020",
                          pdf_path="/nonexistent/x-2020.pdf")],
        labels_disputed=["9"],
    ).to_json(case / "refs_manifest.json")

    # Patch the GATE, not the predicate behind it. `_check_pipeline` calls
    # `cli._require_claude()`, which does `from .check import claude_available`
    # — and `check.claude_available` is a re-export, a *separate module
    # attribute* from `ask.claude_available` even though both name one
    # function. Patching `ask_mod` left the one this path reads untouched, so
    # the test passed on a machine with the CLI installed and failed all ten
    # CI jobs, which have none. Patching `_require_claude` itself cannot
    # drift with a future refactor of which module it imports from.
    monkeypatch.setattr(cli, "_require_claude", lambda: None)
    # `_check_pipeline` reads the extraction through `cli._extraction_for` now
    # — the `limits` feature made extraction a stage of its own so a claims
    # limit can retrieve only what the selected claims cite, and stubbing
    # `check.extract_claims` no longer intercepts anything this path calls
    monkeypatch.setattr(
        cli, "_extraction_for",
        lambda case, model: ClaimExtraction(
            manuscript="m.pdf", manuscript_sha256=None, extractor="test", date="2026-01-01",
        ),
    )
    # a realistic empty audit, not a bare `{}`: `coverage_audit` never returns
    # a dict without `missing` — `coverage["missing"]` two lines into the
    # summary print is unrelated to what this test is proving and must not be
    # what breaks it
    monkeypatch.setattr(
        check_mod, "coverage_audit",
        lambda *a, **kw: {"labels_in_text": [], "covered": [], "missing": []},
    )
    monkeypatch.setattr(check_mod, "last_model", lambda: None)

    captured = {}

    def fake_check_claims(claims, manifest, case_dir, model, *, progress=None,
                          on_error=None, truncations=None, backend, disputed=None):
        captured["disputed"] = disputed
        return claims

    monkeypatch.setattr(check_mod, "check_claims", fake_check_claims)

    cli._check_pipeline(case=case, backend="pymupdf")

    assert captured["disputed"] == {"9"}


def test_check_pipeline_tolerates_a_manifest_with_nothing_disputed(tmp_path, monkeypatch):
    """`labels_disputed` defaults to `[]` — the ordinary case, every run before
    Task 3 shipped and every run where nothing disagreed. `set([])` must reach
    `check_claims` rather than `None`, `set()` behaves identically either way
    (proved in `test_check.py`), but this is the one place that constructs it."""
    case = tmp_path / "case"
    case.mkdir()
    RefManifest(manuscript="m.pdf", entries=[]).to_json(case / "refs_manifest.json")

    # Patch the GATE, not the predicate behind it. `_check_pipeline` calls
    # `cli._require_claude()`, which does `from .check import claude_available`
    # — and `check.claude_available` is a re-export, a *separate module
    # attribute* from `ask.claude_available` even though both name one
    # function. Patching `ask_mod` left the one this path reads untouched, so
    # the test passed on a machine with the CLI installed and failed all ten
    # CI jobs, which have none. Patching `_require_claude` itself cannot
    # drift with a future refactor of which module it imports from.
    monkeypatch.setattr(cli, "_require_claude", lambda: None)
    # `_check_pipeline` reads the extraction through `cli._extraction_for` now
    # — the `limits` feature made extraction a stage of its own so a claims
    # limit can retrieve only what the selected claims cite, and stubbing
    # `check.extract_claims` no longer intercepts anything this path calls
    monkeypatch.setattr(
        cli, "_extraction_for",
        lambda case, model: ClaimExtraction(
            manuscript="m.pdf", manuscript_sha256=None, extractor="test", date="2026-01-01",
        ),
    )
    monkeypatch.setattr(
        check_mod, "coverage_audit",
        lambda *a, **kw: {"labels_in_text": [], "covered": [], "missing": []},
    )
    monkeypatch.setattr(check_mod, "last_model", lambda: None)

    captured = {}

    def fake_check_claims(claims, manifest, case_dir, model, *, progress=None,
                          on_error=None, truncations=None, backend, disputed=None):
        captured["disputed"] = disputed
        return claims

    monkeypatch.setattr(check_mod, "check_claims", fake_check_claims)

    cli._check_pipeline(case=case, backend="pymupdf")

    assert captured["disputed"] == set()


# --- the extract stage, and the limits `run` hands down ----------------------
#
# Extraction is a stage of its own now: `run` extracts BEFORE resolving
# references, so `--max-claims` can retrieve only what the selected claims
# cite, and `check` reads the numbered list back rather than extracting a
# second time — which would renumber the claims between retrieval and judging.


def _stub_run(monkeypatch, tmp_path):
    """Every stage `run` calls, replaced by a recorder; returns (order, kwargs)."""
    order: list[str] = []
    seen: dict[str, dict] = {}

    def spy(name):
        def f(**kw):
            order.append(name)
            seen[name] = kw
        return f

    for name in ("_ingest_pipeline", "_extract_pipeline", "_refs_pipeline", "scout",
                 "_check_pipeline", "highlight", "_report_pipeline"):
        monkeypatch.setattr(cli, name, spy(name))
    monkeypatch.setattr(cli, "_resolve_case", lambda case, manuscript: tmp_path)
    monkeypatch.setattr(cli, "_guard_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_open_case", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    monkeypatch.setattr(cli, "_email", lambda v: "t@example.org")
    return order, seen


def test_run_extracts_before_resolving_and_hands_both_limits_down(monkeypatch, tmp_path):
    order, seen = _stub_run(monkeypatch, tmp_path)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.run(manuscript=pdf, case=tmp_path, provided=None, email=None, model=None, png=False,
            backend="pymupdf", with_scout=True, doi=None, formats=["md"], supplement=None,
            max_claims=5, max_sources=6)

    assert order == ["_ingest_pipeline", "_extract_pipeline", "_refs_pipeline", "scout",
                     "_check_pipeline", "highlight", "_report_pipeline"]
    assert seen["_refs_pipeline"]["claims"] == [1, 2, 3, 4, 5]
    assert seen["_refs_pipeline"]["max_sources"] == 6
    assert seen["_check_pipeline"]["claims"] == [1, 2, 3, 4, 5]


def test_run_without_limits_hands_the_stages_none_and_not_an_option_info(monkeypatch, tmp_path):
    """The fourth appearance of the sentinel bug class, pre-empted: a direct
    call that omits the new options must reach the stages as `None`."""
    _order, seen = _stub_run(monkeypatch, tmp_path)
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    cli.run(manuscript=pdf, case=tmp_path, provided=None, email=None, model=None, png=False,
            backend="pymupdf", with_scout=False, doi=None, formats=["md"], supplement=None)

    assert seen["_refs_pipeline"]["claims"] is None
    assert seen["_refs_pipeline"]["max_sources"] is None
    assert seen["_check_pipeline"]["claims"] is None


def test_extract_command_delegates_to_the_pipeline_function(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "_extract_pipeline", lambda **kw: seen.update(kw))
    cli.extract(case=tmp_path, model=None)
    assert seen == {"case": tmp_path, "model": None}


def test_extract_pipeline_rejects_a_positional_call():
    with pytest.raises(TypeError):
        cli._extract_pipeline(Path("some-case"))


def _case(tmp_path: Path, sha: str = "abc") -> Path:
    """A case folder with an ingested manuscript slot and a manifest, no model."""
    from papertrace.models import Block, RefEntry, RefManifest, SourceMap

    case = tmp_path / "case"
    slot = case / "ingest" / "manuscript"
    slot.mkdir(parents=True)
    (slot / "annotated.md").write_text("<!-- block_0001, page 1 -->\nA sentence citing [1].\n")
    (slot / "clean.md").write_text("A sentence citing [1].\n")
    SourceMap(
        doc="m.pdf", pages=1, source_sha256=sha,
        blocks=[Block("block_0001", "text", 1, (0.0, 0.0, 10.0, 10.0), [], "A sentence citing [1].")],
    ).to_json(slot / "source_map.json")
    RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="A (2020) X.", status="paywalled", reason="no OA copy"),
    ]).to_json(case / "refs_manifest.json")
    return case


def test_the_extract_stage_writes_the_numbered_claims_into_the_case(monkeypatch, tmp_path):
    import papertrace.check as check_mod
    from papertrace.models import ClaimExtraction, ClaimResult

    case = _case(tmp_path)
    monkeypatch.setattr(check_mod, "claude_available", lambda: True)
    monkeypatch.setattr(
        check_mod, "extract_claims",
        lambda case_dir, model=None, *, truncations=None:
            ([ClaimResult(id=1, claim="c", location="Intro", refs=["1"])], []),
    )
    cli._extract_pipeline(case=case, model=None)

    ex = ClaimExtraction.from_json(case / "out" / "claims.json")
    assert [c.id for c in ex.claims] == [1]
    # stamped with the manuscript's identity, so `check` can tell whether the
    # list it finds is this paper's
    assert ex.manuscript_sha256 == "abc" and ex.manuscript == "m.pdf"


def test_check_reuses_this_papers_extraction_and_re_extracts_another(monkeypatch, tmp_path):
    """Two things are at stake. Reusing the list keeps claim numbers stable
    between retrieval and judging, and across `check` re-runs — the route a
    later cherry-pick interface depends on. Reusing a list that belongs to a
    different paper would judge the wrong claims, so the hash decides."""
    import json

    import papertrace.check as check_mod
    from papertrace.models import ClaimExtraction, ClaimResult, RunResults

    case = _case(tmp_path)
    (case / "out").mkdir()
    ClaimExtraction(
        manuscript="m.pdf", manuscript_sha256="abc", extractor="x", date="2026-01-01",
        claims=[ClaimResult(id=i, claim=f"c{i}", location="Intro", refs=["1"]) for i in (1, 2)],
        uncited=[], truncated={},
    ).to_json(case / "out" / "claims.json")
    monkeypatch.setattr(check_mod, "claude_available", lambda: True)
    calls: list[int] = []

    def fresh(case_dir, model=None, *, truncations=None):
        calls.append(1)
        return [ClaimResult(id=1, claim="fresh", location="", refs=["1"])], []

    monkeypatch.setattr(check_mod, "extract_claims", fresh)
    monkeypatch.setattr(check_mod, "check_claims", lambda claims, *a, **k: claims)

    cli._check_pipeline(case=case, model=None, backend="pymupdf", claims=[2])
    assert calls == [], "the extraction on disk is this paper's and must be reused"
    results = RunResults.from_json(case / "out" / "results.json")
    assert [c.id for c in results.claims] == [2]
    assert results.scope["claims"] == {"requested": [2], "judged": [2], "extracted": 2}

    payload = json.loads((case / "out" / "claims.json").read_text())
    payload["manuscript_sha256"] = "not-this-paper"
    (case / "out" / "claims.json").write_text(json.dumps(payload))
    cli._check_pipeline(case=case, model=None, backend="pymupdf", claims=None)
    assert calls == [1], "a list stamped with another paper's hash is never trusted"
    assert ClaimExtraction.from_json(case / "out" / "claims.json").manuscript_sha256 == "abc"
    assert RunResults.from_json(case / "out" / "results.json").scope == {}


def test_refs_with_a_claims_selection_refuses_to_guess_without_the_extraction(tmp_path):
    """Which references the first five claims cite is a fact `extract` wrote
    down. Without it there is nothing to select by, and retrieving everything
    while claiming a limit would be the silent downgrade this codebase refuses."""
    import typer

    from papertrace.models import ClaimExtraction, ClaimResult

    case = _case(tmp_path)
    with pytest.raises(typer.Exit) as e:
        cli._labels_for_claims(case, [1, 2])
    assert e.value.exit_code == 2

    (case / "out").mkdir()
    ClaimExtraction(
        manuscript="m.pdf", manuscript_sha256="abc", extractor="x", date="2026-01-01",
        claims=[
            ClaimResult(id=1, claim="a", location="", refs=["1", "3"]),
            ClaimResult(id=2, claim="b", location="", refs=["2"]),
            ClaimResult(id=3, claim="c", location="", refs=["9"]),
        ],
        uncited=[], truncated={},
    ).to_json(case / "out" / "claims.json")
    assert cli._labels_for_claims(case, [1, 2]) == {"1", "2", "3"}
