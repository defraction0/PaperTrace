"""One case folder, one paper — including when the folder predates hashing.

A case folder is the unit of work and `_guard_case` is what keeps two audits
from mixing. The gap this file pins is an ordering one: `refs` used to parse
references out of the *cached* source map before the identity guard ran, then
stamp the supplied manuscript's hash onto the resulting manifest. A legacy case
plus a same-named different PDF therefore produced a manifest that looked
content-verified while describing the previous paper.
"""

import json
import sys
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
                        lambda entries, dest, email, provided_dir=None, progress=None: entries)
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
    cli.refs(manuscript=old_pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    payload = json.loads((case / "refs_manifest.json").read_text())
    del payload["manuscript_sha256"]
    (case / "refs_manifest.json").write_text(json.dumps(payload))
    assert RefManifest.from_json(case / "refs_manifest.json").manuscript_sha256 is None

    # now the same filename, different content
    cli.refs(manuscript=new_pdf, case=case, provided=None, email="test@example.org",
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

    cli.refs(manuscript=pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    assert RefManifest.from_json(case / "refs_manifest.json").manuscript_sha256 is not None

    calls: list[Path] = []
    import papertrace.ingest as ing

    real = ing.ingest_pdf
    monkeypatch.setattr(ing, "ingest_pdf",
                        lambda p, o, **kw: calls.append(p) or real(p, o, **kw))

    cli.refs(manuscript=pdf, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    assert calls == [], "a verified case re-ingested when it did not need to"


def test_a_different_paper_in_a_hashed_case_is_still_refused(tmp_path, offline):
    """The existing hard guard must survive the reordering."""
    case = tmp_path / "case"
    one = _paper(tmp_path / "one" / "alpha.pdf", "ALPHA", "10.1000/alpha")
    two = _paper(tmp_path / "two" / "beta.pdf", "BETA", "10.1000/beta")

    cli.refs(manuscript=one, case=case, provided=None, email="test@example.org",
             parse_only=False, backend="pymupdf")
    with pytest.raises(typer.Exit) as e:
        cli.refs(manuscript=two, case=case, provided=None, email="test@example.org",
                 parse_only=False, backend="pymupdf")
    assert e.value.exit_code == 2
