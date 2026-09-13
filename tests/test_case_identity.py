"""One case folder, one paper — which folder that is, and that it holds one paper.

A case folder is the unit of work and `_guard_case` is what keeps two audits
from mixing. The gap this file pins is an ordering one: `refs` used to parse
references out of the *cached* source map before the identity guard ran, then
stamp the supplied manuscript's hash onto the resulting manifest. A legacy case
plus a same-named different PDF therefore produced a manifest that looked
content-verified while describing the previous paper.

The second half of the file covers *which* folder an audit lands in: every
command used to default to a folder literally named `case`, so consecutive
audits of different papers piled into one folder unless the user remembered
`-c`, and the folder appeared in whatever directory they happened to be in.
The last test checks the commands the `/review` skill tells an agent to run —
documentation is the only interface those lines have to the CLI.
"""

import json
import sys
import types
from pathlib import Path

import pymupdf
import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402
from papertrace.models import RefManifest  # noqa: E402


def _paper(path: Path, marker: str, doi: str) -> Path:
    """A one-page PDF with a References section naming `marker`."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), f"{marker} Imaging Study", fontsize=16)
    page.insert_text((72, 140), f"Body text citing [1] here. {marker} content.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), f"[1] {marker} A. {marker} PAPER reference. 2020. doi:{doi}",
                     fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def offline(monkeypatch):
    """No network: resolution is a no-op that leaves entries as parsed."""
    import papertrace.refs as refs_mod

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None, taken=None: entries)
    monkeypatch.setenv("PAPERTRACE_EMAIL", "test@example.org")


def test_a_legacy_case_reingests_rather_than_certifying_the_old_paper(tmp_path, offline):
    """The reported hazard. A pre-hash case holds OLD's source map and a legacy
    manifest. A *different* file with the same name is passed to `refs`. The
    manifest that comes out must not describe OLD while carrying NEW's hash —
    that is a case which every later run trusts, mixing two papers.
    """
    case = tmp_path / "case"
    old_pdf = _paper(tmp_path / "old" / "paper.pdf", "OLD", "10.1000/old")
    new_pdf = _paper(tmp_path / "new" / "paper.pdf", "NEW", "10.1000/new")

    # build the legacy case: ingest OLD, then strip the hash from its manifest
    cli._refs_pipeline(manuscript=old_pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    payload = json.loads((case / "refs_manifest.json").read_text())
    del payload["manuscript_sha256"]
    (case / "refs_manifest.json").write_text(json.dumps(payload))
    assert RefManifest.from_json(case / "refs_manifest.json").manuscript_sha256 is None

    # now the same filename, different content
    cli._refs_pipeline(manuscript=new_pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")

    written = RefManifest.from_json(case / "refs_manifest.json")
    raws = " ".join(e.raw for e in written.entries)
    assert "OLD PAPER" not in raws, (
        "the manifest describes the previous paper while carrying this one's hash"
    )
    assert "NEW PAPER" in raws, raws
    assert written.manuscript_sha256 == cli.manuscript_fingerprint(new_pdf)


def test_an_ordinary_case_still_reuses_its_cached_ingest(tmp_path, offline, monkeypatch):
    """The fix must not make every `refs` re-ingest. A content-verified case
    re-run on the same paper reads the cached source map, as before."""
    case = tmp_path / "case"
    pdf = _paper(tmp_path / "a" / "paper.pdf", "SAME", "10.1000/same")

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    assert RefManifest.from_json(case / "refs_manifest.json").manuscript_sha256 is not None

    calls: list[Path] = []
    import papertrace.ingest as ing

    real = ing.ingest_pdf
    monkeypatch.setattr(ing, "ingest_pdf",
                        lambda p, o, **kw: calls.append(p) or real(p, o, **kw))

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    assert calls == [], "a verified case re-ingested when it did not need to"


def test_a_different_paper_in_a_hashed_case_is_still_refused(tmp_path, offline):
    """The existing hard guard must survive the reordering."""
    case = tmp_path / "case"
    one = _paper(tmp_path / "one" / "alpha.pdf", "ALPHA", "10.1000/alpha")
    two = _paper(tmp_path / "two" / "beta.pdf", "BETA", "10.1000/beta")

    cli._refs_pipeline(manuscript=one, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    with pytest.raises(typer.Exit) as e:
        cli._refs_pipeline(manuscript=two, case=case, provided=None, email="test@example.org",
                 parse_only=False, backend="pymupdf")
    assert e.value.exit_code == 2


@pytest.fixture()
def terminal(monkeypatch):
    """A tty whose answers are scripted; an unscripted question is an error.

    Returned list is the answer queue — leaving it empty asserts that nothing
    was asked, because `Prompt.ask` then raises IndexError.
    """
    monkeypatch.setattr(cli.sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    answers: list[str] = []

    class _Prompt:
        @staticmethod
        def ask(*_a, **_kw):
            return answers.pop(0)

    monkeypatch.setattr(cli, "Prompt", _Prompt)
    return answers


@pytest.fixture()
def piped(monkeypatch):
    """No tty: a pipe, a cron job, CI. Nothing may block on stdin."""
    monkeypatch.setattr(cli.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))


def test_the_case_folder_defaults_to_the_papers_own_name(tmp_path, offline, monkeypatch):
    """The reported defect: a batch run wrote into `case/` in whatever directory
    the user stood in — a git clone's root, in the report — so a second paper
    landed on the first. The folder is named after the paper and sits beside it.
    """
    (elsewhere := tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(elsewhere)  # the audit must not follow the user's cwd
    pdf = _paper(tmp_path / "papers" / "PIIS0720048X2600522X.pdf", "ONE", "10.1000/one")

    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")

    derived = tmp_path / "papers" / "PIIS0720048X2600522X"
    assert (derived / "refs_manifest.json").exists(), "audit did not land in the derived folder"
    assert not (Path.cwd() / "case").exists(), "still scattering a `case/` into the cwd"
    # the folder is named after a manuscript, so .gitignore's `case/` no longer
    # covers it — a case folder created for the user ignores itself
    assert (derived / ".gitignore").read_text() == "*\n"


def test_an_explicit_case_flag_still_wins(tmp_path, offline, monkeypatch, terminal):
    """`-c` is an instruction, not a suggestion — and never asks a question."""
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "papers" / "alpha.pdf", "ALPHA", "10.1000/alpha")
    chosen = tmp_path / "mycase"

    for _ in range(2):  # twice: an explicit re-run is not interrogated either
        cli._refs_pipeline(manuscript=pdf, case=chosen, provided=None, email="test@example.org",
                 parse_only=False, backend="pymupdf")

    assert (chosen / "refs_manifest.json").exists()
    assert not (tmp_path / "papers" / "alpha").exists()


def test_a_rerun_of_the_same_paper_can_amend_its_case(tmp_path, offline, monkeypatch, terminal):
    """The "I added more source PDFs" case: reuse the folder, pick the new ones up."""
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "papers" / "beta.pdf", "BETA", "10.1000/beta")
    derived = tmp_path / "papers" / "beta"

    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    terminal.append("amend")
    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")

    assert terminal == [], "the collision was not put to the user"
    assert (derived / "refs_manifest.json").exists()
    assert not (tmp_path / "papers" / "beta-2").exists(), "amend must not open a second folder"


def test_a_rerun_can_start_a_fresh_numbered_case(tmp_path, offline, monkeypatch, terminal):
    """The other branch: leave the first audit intact, start the paper over."""
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "papers" / "gamma.pdf", "GAMMA", "10.1000/gamma")

    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    first = (tmp_path / "papers" / "gamma" / "refs_manifest.json").read_bytes()
    terminal.append("fresh")
    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")

    assert (tmp_path / "papers" / "gamma-2" / "refs_manifest.json").exists()
    assert (tmp_path / "papers" / "gamma" / "refs_manifest.json").read_bytes() == first


def test_a_rerun_without_a_terminal_amends_and_says_so(tmp_path, offline, monkeypatch, piped,
                                                       capsys):
    """A pipe, a cron job or CI has nobody to answer. The documented choice is
    amend: it is what the previous default did for a re-run of the same paper,
    it destroys nothing, and it keeps the output path predictable — `fresh`
    would silently move the report somewhere a caller cannot name.
    """
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "papers" / "delta.pdf", "DELTA", "10.1000/delta")

    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    capsys.readouterr()
    cli._refs_pipeline(manuscript=pdf, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    out = capsys.readouterr().out

    assert (tmp_path / "papers" / "delta" / "refs_manifest.json").exists()
    assert not (tmp_path / "papers" / "delta-2").exists()
    assert "amend" in out, out


def test_a_replaced_paper_of_the_same_name_is_still_refused(tmp_path, offline, monkeypatch,
                                                            terminal):
    """The derived name collides only when the file itself was replaced — v2
    saved over v1. That is a different paper in an existing case, so the hard
    guard owns it: exit 2, and no amend/fresh question (`terminal` is empty).
    """
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "papers" / "epsilon.pdf"
    _paper(path, "FIRST", "10.1000/first")
    cli._refs_pipeline(manuscript=path, case=None, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")

    _paper(path, "SECOND", "10.1000/second")  # same name, different paper
    with pytest.raises(typer.Exit) as e:
        cli._refs_pipeline(manuscript=path, case=None, provided=None, email="test@example.org",
                 parse_only=False, backend="pymupdf")
    assert e.value.exit_code == 2


def test_run_derives_one_case_folder_and_hands_it_to_every_stage(tmp_path, offline, monkeypatch):
    """`run` resolves once and passes the result down by keyword, so no stage
    re-derives a folder of its own and the collision is put once, not six times.
    """
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "papers" / "zeta.pdf", "ZETA", "10.1000/zeta")
    seen: dict[str, dict] = {}
    # `ingest` and `refs` are split into a Typer command plus a `_..._pipeline`
    # function, the plain function `run` actually calls — see cli.py's comment
    # on `run()`.
    monkeypatch.setattr(cli, "_ingest_pipeline", lambda **kw: seen.__setitem__("ingest", kw))
    monkeypatch.setattr(cli, "_extract_pipeline", lambda **kw: seen.__setitem__("extract", kw))
    monkeypatch.setattr(cli, "_refs_pipeline", lambda **kw: seen.__setitem__("refs", kw))
    monkeypatch.setattr(cli, "_report_pipeline", lambda **kw: seen.__setitem__("report", kw))
    monkeypatch.setattr(cli, "_check_pipeline", lambda **kw: seen.__setitem__("check", kw))
    for name in ("scout", "highlight"):
        monkeypatch.setattr(cli, name, (lambda n: lambda **kw: seen.__setitem__(n, kw))(name))

    cli.run(manuscript=pdf, case=None, provided=None, email="test@example.org", model=None,
            png=False, backend="pymupdf", with_scout=True, doi=None)

    derived = tmp_path / "papers" / "zeta"
    assert set(seen) == {"ingest", "extract", "refs", "scout", "check", "highlight", "report"}
    assert {n: kw["case"] for n, kw in seen.items()} == dict.fromkeys(seen, derived)


def test_the_wizard_suggests_the_folder_batch_mode_would_use(tmp_path):
    """One answer to "where does this audit live", not two."""
    from papertrace import wizard

    pdf = tmp_path / "papers" / "eta.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n")
    assert wizard._suggest_case(pdf) == str(cli.default_case(pdf))


# --- the two ways around the guard -----------------------------------------
#
# `_guard_case` only ever ran inside `refs`. Two other paths could write into a
# case's manuscript slot: `ingest` never consulted the guard at all, and
# `refs --parse-only` re-ingested a legacy case and then returned before the
# manifest caught up. Both leave one case folder describing two papers, which
# is precisely the state the guard exists to make impossible.


def test_ingest_refuses_to_overwrite_another_papers_manuscript_slot(tmp_path, offline):
    """`papertrace ingest manuscript.pdf -c CASE` writes <case>/ingest/manuscript
    — the same slot `refs` filled and `coverage_audit` reads. A different paper
    landing there leaves the source map describing NEW and the manifest OLD.
    """
    case = tmp_path / "case"
    old_pdf = _paper(tmp_path / "old" / "manuscript.pdf", "OLD", "10.1000/old")
    new_pdf = _paper(tmp_path / "new" / "manuscript.pdf", "NEW", "10.1000/new")

    cli._refs_pipeline(manuscript=old_pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")

    with pytest.raises(typer.Exit):
        cli._ingest_pipeline(pdf=new_pdf, out=None, case=case, backend="pymupdf")

    smap = json.loads((case / "ingest" / "manuscript" / "source_map.json").read_text())
    body = " ".join(b.get("text", "") for b in smap["blocks"])
    assert "NEW" not in body, "a different paper overwrote the case's manuscript"
    assert "OLD" in body


def test_ingest_of_a_cited_source_into_the_same_case_is_untouched(tmp_path, offline):
    """The guard is about the manuscript slot, not the folder. A cited source
    ingested into `<case>/ingest/<slug>` is not the audited paper and must stay
    ingestable — guarding it would break `check`'s own source ingest."""
    case = tmp_path / "case"
    paper = _paper(tmp_path / "a" / "paper.pdf", "PAPER", "10.1000/paper")
    source = _paper(tmp_path / "b" / "smith-2020.pdf", "SOURCE", "10.1000/src")

    cli._refs_pipeline(manuscript=paper, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    cli._ingest_pipeline(pdf=source, out=None, case=case, backend="pymupdf")

    assert (case / "ingest" / "smith-2020" / "source_map.json").exists()


def test_parse_only_on_a_legacy_case_leaves_the_case_coherent(tmp_path, offline):
    """`--parse-only` says "List references, no network" — an inspection. On a
    legacy case it re-ingested into the manuscript slot and then returned before
    writing the manifest, so the source map described NEW while the manifest and
    its (absent) hash still described OLD.
    """
    case = tmp_path / "case"
    old_pdf = _paper(tmp_path / "old" / "paper.pdf", "OLD", "10.1000/old")
    new_pdf = _paper(tmp_path / "new" / "paper.pdf", "NEW", "10.1000/new")

    cli._refs_pipeline(manuscript=old_pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    payload = json.loads((case / "refs_manifest.json").read_text())
    del payload["manuscript_sha256"]
    (case / "refs_manifest.json").write_text(json.dumps(payload))

    cli._refs_pipeline(manuscript=new_pdf, case=case, provided=None, email="test@example.org",
             parse_only=True, backend="pymupdf")

    smap = json.loads((case / "ingest" / "manuscript" / "source_map.json").read_text())
    body = " ".join(b.get("text", "") for b in smap["blocks"])
    manifest = RefManifest.from_json(case / "refs_manifest.json")
    raws = " ".join(e.raw for e in manifest.entries)
    assert ("NEW" in body) == ("NEW PAPER" in raws), (
        "the source map and the manifest describe different papers"
    )


def test_parse_only_still_lists_the_new_papers_references(tmp_path, offline, capsys):
    """Not mutating the case must not mean reading the wrong paper: the listing
    is of the file that was passed, whatever the case folder holds."""
    case = tmp_path / "case"
    old_pdf = _paper(tmp_path / "old" / "paper.pdf", "OLD", "10.1000/old")
    new_pdf = _paper(tmp_path / "new" / "paper.pdf", "NEW", "10.1000/new")

    cli._refs_pipeline(manuscript=old_pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    payload = json.loads((case / "refs_manifest.json").read_text())
    del payload["manuscript_sha256"]
    (case / "refs_manifest.json").write_text(json.dumps(payload))
    capsys.readouterr()

    cli._refs_pipeline(manuscript=new_pdf, case=case, provided=None, email="test@example.org",
             parse_only=True, backend="pymupdf")
    assert "NEW PAPER" in capsys.readouterr().out
