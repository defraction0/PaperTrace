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
    assert types == {"sectionheader", "text"}
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
                              "not_retrieved": 1, "unchecked": 0}


def test_case_folder_belongs_to_one_paper(tmp_path):
    """A used case dir must be refused for a different paper (stale scout/
    evidence/manifest files would otherwise bleed into the new report) and
    accepted for a re-run of the same one."""
    from papertrace.cli import _case_conflict
    from papertrace.models import RefEntry, RefManifest

    RefManifest(
        manuscript="first.pdf", entries=[RefEntry(num="1", raw="X (2020) Y.")]
    ).to_json(tmp_path / "refs_manifest.json")

    assert _case_conflict(tmp_path, "second.pdf") == "first.pdf"  # refuse
    assert _case_conflict(tmp_path, "first.pdf") is None  # same paper: fine
    assert _case_conflict(tmp_path / "fresh", "second.pdf") is None  # new case: fine
