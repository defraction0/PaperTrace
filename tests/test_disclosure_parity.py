"""Every disclosure reaches every reader — in all three report formats.

The three formats phrase each disclosure at their own length, so the contract
is the `token`: a short literal from `papertrace.disclosures` that must appear
verbatim in the markdown, the editor HTML and the terminal HTML. Asserting the
token turns "no format silently drops a disclosure" into a loop instead of a
hand-maintained checklist — which is exactly what was missing when the editor
report shipped with zero mentions of `unjudged_refs`.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.disclosures import (  # noqa: E402
    ANCHOR_LOCATED_TOKEN,
    claim_disclosures,
    run_disclosures,
)
from papertrace.models import ClaimResult, RunResults  # noqa: E402
from papertrace.report import write_reports  # noqa: E402

FORMATS = ("report.md", "report_editor.html", "report_terminal.html")


def _render(results: RunResults, out: Path) -> dict[str, str]:
    write_reports(results, None, out, png=False)
    return {name: (out / name).read_text() for name in FORMATS}


def _claim(**kw) -> ClaimResult:
    base = dict(
        id=1,
        claim="the cohort was imaged twice",
        location="Methods",
        refs=["7", "9"],
        verdict="supported",
        source_slug="fixture-2020",
        source_page=1,
    )
    base.update(kw)
    return ClaimResult(**base)


@pytest.mark.parametrize("coverage", [
    # the audited path and the unrecognised-style caveat are mutually
    # exclusive, so both branches need a run of their own
    {"labels_in_text": ["1", "2"], "covered": ["1"], "missing": ["2"]},
    {"labels_in_text": [], "covered": [], "missing": []},
    # coverage/2 — without this case the loop never sees the occurrence
    # disclosures, so the attribution caveat could drift out of a format
    # unnoticed. A guard that cannot reach a branch does not guard it.
    {
        "schema": "coverage/2", "unit": "occurrence", "source": "source_map",
        "labels_in_text": ["1", "3"], "covered": ["1", "3"], "missing": [],
        "labels_partially_covered": ["3"], "labels_uncertain_only": [],
        "occurrences": {
            "total": 3, "covered": 2, "uncovered": 1, "uncertain": 0,
            "items": [{
                "occ_id": "block_0007:118:3", "label": "3", "block": "block_0007",
                "page": 1, "offset": 118, "group": "[3]", "section": "Methods",
                "sentence": "a second sentence citing the same source",
                "covered_by": [], "status": "uncovered",
            }],
        },
        "attribution": {
            "method": "location_filter+text_similarity",
            "min_ratio": 0.45, "min_margin": 0.10, "claims_unattributed": [],
        },
    },
])
def test_every_run_disclosure_appears_in_all_three_formats(tmp_path, coverage):
    results = RunResults(
        manuscript="m.pdf",
        converter="pymupdf",
        claims=[_claim()],
        coverage=coverage,
        truncated={"manuscript": {"chars": 90000, "limit": 60000}},
    )
    rendered = _render(results, tmp_path)

    fired = run_disclosures(results)
    assert {d.key for d in fired} >= {"truncation", "converter"}
    for d in fired:
        for name, body in rendered.items():
            assert d.token in body, f"{d.key}: token {d.token!r} missing from {name}"


@pytest.mark.parametrize("anchor_located", [True, False, None])
def test_every_claim_disclosure_appears_in_all_three_formats(tmp_path, anchor_located):
    claim = _claim(
        unjudged_refs=["7", "9"],
        evidence_image="evidence/claim_01.png",
        anchor_located=anchor_located,
    )
    results = RunResults(manuscript="m.pdf", claims=[claim])
    rendered = _render(results, tmp_path)

    fired = claim_disclosures(claim)
    assert {d.key for d in fired} == {"unjudged_refs", "anchor"}
    for d in fired:
        for name, body in rendered.items():
            assert d.token in body, f"{d.key}: token {d.token!r} missing from {name}"


def test_unknown_anchor_state_never_claims_a_match(tmp_path):
    """`anchor_located is None` is not `True`. A crop whose anchor was never
    resolved must not be captioned as a located match in any format — and the
    footers must not assert one on its behalf either."""
    claim = _claim(evidence_image="evidence/claim_01.png", anchor_located=None)
    rendered = _render(RunResults(manuscript="m.pdf", claims=[claim]), tmp_path)

    for name, body in rendered.items():
        assert ANCHOR_LOCATED_TOKEN not in body, f"{name} claims a match it cannot establish"


def test_truncation_is_disclosed_without_a_coverage_object(tmp_path):
    """The editor report nested its truncation warning inside the coverage
    block, so a run with truncation and no coverage disclosed nothing."""
    results = RunResults(
        manuscript="m.pdf",
        claims=[_claim()],
        coverage={},
        truncated={"source:fixture-2020": {"chars": 120000, "limit": 60000}},
    )
    rendered = _render(results, tmp_path)

    truncation = next(d for d in run_disclosures(results) if d.key == "truncation")
    for name, body in rendered.items():
        assert truncation.token in body, f"truncation missing from {name}"


@pytest.mark.parametrize("converter", ["pymupdf", "docling 2.118.1"])
def test_converter_is_named_in_every_format_for_both_backends(tmp_path, converter):
    """The backend is named unconditionally: md and editor used to stamp
    nothing unless the converter was pymupdf, leaving a docling run
    unattributed in two formats out of three."""
    results = RunResults(manuscript="m.pdf", converter=converter, claims=[_claim()])
    rendered = _render(results, tmp_path)

    disclosure = next(d for d in run_disclosures(results) if d.key == "converter")
    assert disclosure.token == f"converter: {converter}"
    for name, body in rendered.items():
        assert disclosure.token in body, f"converter name missing from {name}"
