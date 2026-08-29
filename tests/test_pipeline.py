"""Ingest → highlight → report on a generated fixture PDF. No network, no LLM."""

import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 module name (the bare `fitz` import is deprecated)
except ImportError:  # pragma: no cover - older PyMuPDF exposes only `fitz`
    import fitz
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.highlight import crop_for_claim  # noqa: E402
from papertrace.ingest import ingest_pdf, references_section  # noqa: E402
from papertrace.models import (  # noqa: E402
    ClaimResult,
    RefEntry,
    RefManifest,
    RunResults,
    SourceMap,
)
from papertrace.report import write_reports  # noqa: E402


@pytest.fixture()
def fixture_pdf(tmp_path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "A Fixture Paper About Nothing", fontsize=16)
    page.insert_text((72, 120), "Abstract", fontsize=13)
    page.insert_text((72, 140), "We trained on CT scans only. Dice was 0.943 overall.", fontsize=10)
    page.insert_text((72, 180), "References", fontsize=13)
    page.insert_text((72, 200), "1. Smith J (2020) Prior work. J Things 1:1-10.", fontsize=9)
    page.insert_text((72, 214), "2. Jones K (2021) Other work. J Stuff 2:2-20.", fontsize=9)
    pdf = tmp_path / "sources" / "fixture-2020.pdf"
    pdf.parent.mkdir()
    doc.save(pdf)
    doc.close()
    return pdf


def test_ingest_blocks_and_headings(fixture_pdf, tmp_path):
    smap = ingest_pdf(fixture_pdf, tmp_path / "ingest" / "fixture-2020")
    assert smap.pages == 1
    types = {b.type for b in smap.blocks}
    # docling may additionally classify the numbered references as a list block
    assert {"sectionheader", "text"} <= types
    heads = [b.text for b in smap.blocks if b.type == "sectionheader"]
    assert "Abstract" in heads and "References" in heads
    # provenance round-trip
    reloaded = SourceMap.from_json(tmp_path / "ingest" / "fixture-2020" / "source_map.json")
    assert reloaded.find(smap.blocks[0].id).text == smap.blocks[0].text


def test_references_section(fixture_pdf, tmp_path):
    smap = ingest_pdf(fixture_pdf, tmp_path / "i")
    refs = references_section(smap)
    assert "Smith" in refs and "Jones" in refs
    assert "Dice" not in refs  # body text stays out


def test_crop_and_reports(fixture_pdf, tmp_path):
    ingest_root = tmp_path / "ingest"
    smap = ingest_pdf(fixture_pdf, ingest_root / "fixture-2020")
    target = next(b for b in smap.blocks if "CT scans only" in b.text)

    claim = ClaimResult(
        id=1, claim="the model was trained on MRI", location="Methods", refs=["1"],
        verdict="partial", note="Source says CT only.",
        source_slug="fixture-2020", source_page=target.page, source_block=target.id,
        anchor_phrases=["CT scans only"],
    )
    out = tmp_path / "out"
    img = crop_for_claim(claim, fixture_pdf.parent, ingest_root, out / "evidence")
    assert img and Path(img).exists()
    claim.evidence_image = str(Path(img).relative_to(out))

    gap = ClaimResult(id=2, claim="X is common", location="Introduction",
                      refs=["2"], verdict="not_retrieved")
    results = RunResults(manuscript="m.pdf", date="2026-01-01",
                         refs_total=2, refs_available=1, claims=[claim, gap])
    manifest = RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="Smith J (2020)", status="provided", slug="fixture-2020"),
        RefEntry(num="2", raw="Jones K (2021)", status="paywalled", reason="no OA copy"),
    ])
    paths = write_reports(results, manifest, out, png=False)
    names = {p.name for p in paths}
    assert names == {"report.md", "report_editor.html", "report_terminal.html"}

    md = (out / "report.md").read_text()
    assert "⊘ **Not retrieved:** 1" in md
    assert "claim_01_fixture-2020_p1.png" in md
    assert "never filled in from memory" in md

    editor = (out / "report_editor.html").read_text()
    assert "PARTIALLY SUPPORTED" in editor and "fixture-2020" in editor

    # results.json round-trip keeps verdicts
    results.to_json(out / "results.json")
    again = RunResults.from_json(out / "results.json")
    assert again.counts() == {"supported": 0, "partial": 1, "contradicted": 0,
                              "not_addressed": 0, "not_retrieved": 1, "unchecked": 0}


def _case_with(tmp_path, manuscript: Path, *, hashed: bool):
    """A case folder already holding an audit of `manuscript`."""
    from papertrace.models import RefEntry, RefManifest, manuscript_fingerprint

    case = tmp_path / "case"
    case.mkdir(exist_ok=True)
    RefManifest(
        manuscript=manuscript.name,
        entries=[RefEntry(num="1", raw="X (2020) Y.")],
        manuscript_sha256=manuscript_fingerprint(manuscript) if hashed else None,
    ).to_json(case / "refs_manifest.json")
    return case


def _pdf(path: Path, body: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_case_folder_is_identified_by_content_not_by_name(tmp_path):
    """A case folder belongs to one paper — and a file NAME is not a paper.
    `/a/manuscript.pdf` and `/b/manuscript.pdf` are routinely two different
    papers, so identity is the content hash."""
    from papertrace.cli import _case_conflict

    first = _pdf(tmp_path / "a" / "manuscript.pdf", b"%PDF-1.4 the first paper")
    # same basename, different bytes — the case the old name check waved through
    second = _pdf(tmp_path / "b" / "manuscript.pdf", b"%PDF-1.4 an entirely different paper")
    case = _case_with(tmp_path, first, hashed=True)

    assert _case_conflict(case, second) == ("manuscript.pdf", "content")  # refuse
    assert _case_conflict(case, first) == (None, "content")  # same paper: fine
    assert _case_conflict(tmp_path / "fresh", second) == (None, "empty")  # new case: fine


def test_the_same_file_under_a_new_name_is_accepted(tmp_path):
    """Renaming a manuscript does not make it a different paper — the bonus
    the content hash buys, which the basename check got wrong in the other
    direction."""
    from papertrace.cli import _case_conflict

    body = b"%PDF-1.4 one and the same paper"
    original = _pdf(tmp_path / "a" / "manuscript.pdf", body)
    renamed = _pdf(tmp_path / "b" / "final-submission.pdf", body)
    case = _case_with(tmp_path, original, hashed=True)

    assert _case_conflict(case, renamed) == (None, "content")


def test_a_legacy_manifest_without_a_hash_falls_back_to_name_comparison(tmp_path, capsys):
    """Case folders written before content hashing carry no hash. They contain
    no better information, so identity degrades to the file name, says so in
    yellow, and self-heals on the next `papertrace refs` — warn, never
    hard-fail: refusing would be equally uninformed and less usable."""
    from papertrace.cli import _case_conflict, _guard_case

    first = _pdf(tmp_path / "a" / "first.pdf", b"%PDF-1.4 first")
    second = _pdf(tmp_path / "b" / "second.pdf", b"%PDF-1.4 second")
    same_name_other_bytes = _pdf(tmp_path / "c" / "first.pdf", b"%PDF-1.4 not actually first")
    case = _case_with(tmp_path, first, hashed=False)

    assert _case_conflict(case, second) == ("first.pdf", "name")
    assert _case_conflict(case, first) == (None, "name")
    # the honest limit of the fallback, stated rather than hidden
    assert _case_conflict(case, same_name_other_bytes) == (None, "name")

    _guard_case(case, first)  # must not raise
    assert "unverified" in capsys.readouterr().out


def test_refs_manifest_roundtrips_the_manuscript_hash(tmp_path):
    import json

    from papertrace.models import RefEntry, RefManifest, manuscript_fingerprint

    pdf = _pdf(tmp_path / "m.pdf", b"%PDF-1.4 body")
    digest = manuscript_fingerprint(pdf)
    assert len(digest) == 64

    path = tmp_path / "refs_manifest.json"
    RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="X (2020) Y.")],
        manuscript_sha256=digest,
    ).to_json(path)
    assert RefManifest.from_json(path).manuscript_sha256 == digest

    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"manuscript": "m.pdf", "entries": []}))
    assert RefManifest.from_json(legacy).manuscript_sha256 is None

    # the omission from `required` IS the compatibility story: every manifest
    # already on disk keeps validating
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json").read_text()
    )
    assert "manuscript_sha256" in schema["properties"]
    assert "manuscript_sha256" not in schema["required"]


def test_matched_anchor_sets_anchor_located_and_captions_the_box(fixture_pdf, tmp_path):
    ingest_root = tmp_path / "ingest"
    smap = ingest_pdf(fixture_pdf, ingest_root / "fixture-2020")
    target = next(b for b in smap.blocks if "CT scans only" in b.text)

    claim = ClaimResult(
        id=1, claim="trained on CT", location="Methods", refs=["1"], verdict="supported",
        source_slug="fixture-2020", source_page=target.page, source_block=target.id,
        anchor_phrases=["CT scans only"],
    )
    out = tmp_path / "out"
    img = crop_for_claim(claim, fixture_pdf.parent, ingest_root, out / "evidence")
    assert claim.anchor_located is True

    claim.evidence_image = str(Path(img).relative_to(out))
    write_reports(RunResults(manuscript="m.pdf", claims=[claim]), None, out, png=False)
    md = (out / "report.md").read_text()
    assert "*red box = matched text*" in md
    assert "no anchor phrase was found" not in md


def test_zero_box_crop_is_disclosed_not_captioned_as_matched(fixture_pdf, tmp_path):
    """A crop is still written when no anchor phrase matches (context is
    useful), but it carries no red box — so the report must not caption it as
    matched text."""
    ingest_root = tmp_path / "ingest"
    smap = ingest_pdf(fixture_pdf, ingest_root / "fixture-2020")
    target = next(b for b in smap.blocks if "CT scans only" in b.text)

    claim = ClaimResult(
        id=1, claim="trained on MRI", location="Methods", refs=["1"], verdict="partial",
        source_slug="fixture-2020", source_page=target.page, source_block=target.id,
        # a phrase that is genuinely absent from the page
        anchor_phrases=["positron emission tomography"],
    )
    out = tmp_path / "out"
    img = crop_for_claim(claim, fixture_pdf.parent, ingest_root, out / "evidence")

    assert img and Path(img).exists()      # the crop is still produced
    assert claim.anchor_located is False   # and it is honest about being unboxed

    claim.evidence_image = str(Path(img).relative_to(out))
    write_reports(RunResults(manuscript="m.pdf", claims=[claim]), None, out, png=False)
    # the disclosure is one token now, and every format must carry it — the
    # editor and terminal reports each used to phrase this their own way, and
    # the terminal did not say it at all
    from papertrace.disclosures import ANCHOR_LOCATED_TOKEN, ANCHOR_NOT_LOCATED_TOKEN

    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        body = (out / name).read_text()
        assert ANCHOR_NOT_LOCATED_TOKEN in body, f"{name} hides the unboxed crop"
        assert ANCHOR_LOCATED_TOKEN not in body, f"{name} claims a match it does not have"


def test_page_beyond_the_source_is_not_a_crash_and_not_a_missed_match(fixture_pdf, tmp_path):
    """A model can name a page the source does not have.

    Two things must not happen. The stage must not die — `doc[page - 1]` used
    to raise `IndexError` out of `crop_for_claim`, taking the whole highlight
    step and, under `run`, the report with it. And the claim must not come back
    `anchor_located = False`: that asserts we searched the page and the phrase
    was absent, when there was no page to search.
    """
    ingest_root = tmp_path / "ingest"
    smap = ingest_pdf(fixture_pdf, ingest_root / "fixture-2020")
    assert smap.pages == 1  # the fixture is one page; 47 is beyond it

    claim = ClaimResult(
        id=1, claim="trained on CT", location="Methods", refs=["1"], verdict="supported",
        source_slug="fixture-2020", source_page=47, source_block=None,
        anchor_phrases=["CT scans only"],
    )
    out = tmp_path / "out"
    img = crop_for_claim(claim, fixture_pdf.parent, ingest_root, out / "evidence")

    assert img is None
    assert claim.anchor_located is None, "never looked is not 'looked and missed'"


def test_crop_evidence_refuses_an_out_of_range_page(fixture_pdf, tmp_path):
    """The bound sits where the indexing happens, not only in the caller.

    It raises rather than returning 0, because 0 already means "crop written,
    no phrase matched" — which `crop_for_claim` turns into
    `anchor_located = False`. Reusing it here would fabricate a failed search
    against a page that does not exist.
    """
    import pytest as _pytest

    from papertrace.highlight import crop_evidence

    with _pytest.raises(ValueError, match=r"page 47 is not in .*1 pages"):
        crop_evidence(
            fixture_pdf, 47, (0.0, 0.0, 100.0, 100.0), ["CT scans only"],
            tmp_path / "nope.png",
        )
    assert not (tmp_path / "nope.png").exists()


@pytest.mark.parametrize("docling_installed", [False, True])
def test_the_flat_ingest_warning_says_why_it_was_flat(fixture_pdf, tmp_path,
                                                      monkeypatch, docling_installed):
    """Two different situations, two different sentences.

    Not installed: name the extra, and name it correctly — rich reads
    `[docling]` as a style tag and silently drops it, so this once read
    `pip install 'papertrace'`, telling the reader to install what they already
    had. Installed but unused: say so, because repeating the install advice to
    somebody who already ran it reads as a broken tool and sends them to fix
    the wrong thing. A real run hit exactly that.
    """
    from typer.testing import CliRunner

    import papertrace.cli as cli_mod
    import papertrace.ingest as ingest_mod
    from papertrace.cli import app

    monkeypatch.setattr(ingest_mod, "_docling_available", lambda: docling_installed)
    monkeypatch.setattr(cli_mod, "_docling_available", lambda: docling_installed,
                        raising=False)
    res = CliRunner().invoke(
        app,
        ["ingest", str(fixture_pdf), "-o", str(tmp_path / "ing"), "--backend", "pymupdf"],
    )
    assert res.exit_code == 0, res.output
    out = " ".join(res.output.split())
    if docling_installed:
        assert "docling is installed" in out
        assert "pip install" not in out, "do not tell them to install what they have"
    else:
        assert "papertrace[docling]" in out
