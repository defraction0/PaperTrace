"""Deterministic citation-coverage audit + uncited-register parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.check import (  # noqa: E402
    _parse_json_object,
    citation_labels_in_text,
    coverage_audit,
)
from papertrace.models import ClaimResult, RunResults, UncitedClaim  # noqa: E402

TEXT = """## Introduction

Prevalence is high [1, 2]. Prior imaging work [7-9] and one outlier [12]
showed things. En-dash ranges [15–17] parse too. A bare year (2020) must not.

## References

1. Someone A (2020) A paper with [99] inside its title. J Things 1:1-10.
"""


def test_labels_in_text_ranges_and_refs_exclusion():
    labels = citation_labels_in_text(TEXT)
    assert labels == {"1", "2", "7", "8", "9", "12", "15", "16", "17"}
    assert "99" not in labels  # references section excluded


def test_coverage_audit_reports_missing(tmp_path):
    ingest_dir = tmp_path / "ingest" / "manuscript"
    ingest_dir.mkdir(parents=True)
    (ingest_dir / "clean.md").write_text(TEXT)
    claims = [
        ClaimResult(id=1, claim="a", location="Intro", refs=["1", "2"]),
        ClaimResult(id=2, claim="b", location="Intro", refs=["7", "8", "9"]),
    ]
    cov = coverage_audit(tmp_path, claims)
    assert cov["missing"] == ["12", "15", "16", "17"]
    assert cov["covered"] == ["1", "2", "7", "8", "9"]
    assert len(cov["labels_in_text"]) == 9


def test_extract_object_parsing_with_fences():
    raw = """```json
{"cited":[{"id":1,"claim":"x","location":"Intro","refs":["3"]}],
 "uncited":[{"id":1,"claim":"y","location":"Methods"}]}
```"""
    data = _parse_json_object(raw)
    assert data["cited"][0]["refs"] == ["3"]
    assert data["uncited"][0]["claim"] == "y"


def test_results_roundtrip_with_uncited_and_coverage(tmp_path):
    r = RunResults(
        manuscript="m.pdf",
        converter="docling 2.1",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["1"], verdict="supported")],
        uncited=[UncitedClaim(id=1, claim="no ref here", location="Intro")],
        coverage={"labels_in_text": ["1", "2"], "covered": ["1"], "missing": ["2"]},
    )
    r.to_json(tmp_path / "results.json")
    again = RunResults.from_json(tmp_path / "results.json")
    assert again.converter == "docling 2.1"
    assert again.uncited[0].claim == "no ref here"
    assert again.coverage["missing"] == ["2"]
    # old results.json without the new fields still loads
    old = tmp_path / "old.json"
    old.write_text(
        '{"manuscript":"m.pdf","claims":[{"id":1,"claim":"c","location":"L",'
        '"verdict":"supported"}]}'
    )
    legacy = RunResults.from_json(old)
    assert legacy.uncited == [] and legacy.coverage == {} and legacy.converter == "pymupdf"


def test_unrecognized_citation_style_is_disclosed(tmp_path):
    """Zero bracketed labels + existing claims must yield the explicit
    'not audited' disclosure, never a silent or green-zero coverage line."""
    from papertrace.report import write_reports

    r = RunResults(
        manuscript="m.pdf",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["1"], verdict="supported")],
        coverage={"labels_in_text": [], "covered": [], "missing": []},
    )
    write_reports(r, None, tmp_path, png=False)
    md = (tmp_path / "report.md").read_text()
    assert "coverage not audited" in md
    assert "all 0" not in md
    term = (tmp_path / "report_terminal.html").read_text()
    assert "coverage not audited" in term


def test_failed_check_is_unchecked_never_not_retrieved(tmp_path, monkeypatch):
    """A model-call failure on an AVAILABLE source must surface as `unchecked`
    (with the reason and a retry behind it), never as `not_retrieved` — the
    report may not claim a retrieval gap that didn't happen."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry, RefManifest

    src_dir = tmp_path / "ingest" / "good-2020"
    src_dir.mkdir(parents=True)
    (src_dir / "annotated.md").write_text("Some source text.")
    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="Good G (2020) X.", status="retrieved",
                          slug="good-2020", pdf_path="unused.pdf")],
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]

    calls = []

    def boom(prompt, model=None):
        calls.append(1)
        raise RuntimeError("claude -p failed: transient")

    monkeypatch.setattr(check_mod, "_ask", boom)
    errors = []
    check_claims(claims, manifest, tmp_path, on_error=lambda s, m: errors.append((s, m)))

    assert len(calls) == 2  # one retry before giving up
    assert claims[0].verdict == "unchecked"
    assert "source WAS retrieved" in claims[0].note
    assert errors and errors[0][0] == "good-2020"

    # and a genuinely missing source still reads not_retrieved
    claims2 = [ClaimResult(id=2, claim="d", location="Intro", refs=["9"])]
    check_claims(claims2, manifest, tmp_path)
    assert claims2[0].verdict == "not_retrieved"
