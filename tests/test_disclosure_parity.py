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


# --- an unverified source identity must reach every reader ------------------


def _manifest_with(*title_checks: str):
    """A manifest whose provided sources had the given identity-check outcomes."""
    from papertrace.models import RefEntry, RefManifest

    return RefManifest(
        manuscript="m.pdf",
        entries=[
            RefEntry(num=str(i + 1), raw=f"Author {i}. A paper. 2020.", status="provided",
                     slug=f"author{i}-2020", title_check=tc,
                     reason=f"matched author{i}-2020.pdf in your sources folder")
            for i, tc in enumerate(title_checks)
        ],
    )


@pytest.mark.parametrize("checks", [
    ("unverifiable",),
    ("mismatch",),
    ("verified", "unverifiable"),
])
def test_an_unverified_source_identity_is_disclosed_in_all_three_formats(tmp_path, checks):
    """The retrieval manifest is rendered only in `report.md.j2`, so a source
    whose identity nobody confirmed was invisible to editor and terminal
    readers. The token has to travel like every other disclosure.
    """
    manifest = _manifest_with(*checks)
    results = RunResults(manuscript="m.pdf", converter="pymupdf", claims=[_claim()])

    write_reports(results, manifest, tmp_path, png=False)
    rendered = {name: (tmp_path / name).read_text() for name in FORMATS}

    fired = run_disclosures(results, manifest)
    identity = next((d for d in fired if d.key == "source_identity"), None)
    assert identity is not None, [d.key for d in fired]
    for name, body in rendered.items():
        assert identity.token in body, f"token {identity.token!r} missing from {name}"


def test_all_identities_verified_adds_no_disclosure(tmp_path):
    """An ordinary run must not grow a warning it has not earned."""
    fired = run_disclosures(
        RunResults(manuscript="m.pdf", converter="pymupdf", claims=[_claim()]),
        _manifest_with("verified", "verified"),
    )
    assert not any(d.key == "source_identity" for d in fired)


def test_the_terminal_template_names_every_disclosure_key_that_exists():
    """Mechanical, so it cannot rot. `report.md.j2` and `report_editor.html.j2`
    render run-level disclosures through a catch-all (`d.key not in (...)`), but
    `report_terminal.html.j2` filters by explicit key — so a newly added
    disclosure appears in two formats and silently vanishes from the third.
    That is what happened to `source_identity`, and the parity loop only caught
    it because a test happened to construct the input that fires it. A
    disclosure with no parity case would still slip through; this check does not
    depend on anyone remembering to add one.
    """
    import re

    from papertrace import disclosures as mod

    src = Path(mod.__file__).read_text()
    keys = set(re.findall(r'key="([a-z_]+)"', src))
    assert keys, "no Disclosure keys found — did the constructor change shape?"

    templates = Path(mod.__file__).parent / "templates"
    terminal = (templates / "report_terminal.html.j2").read_text()
    claim_level = {"anchor", "sources", "unjudged_refs", "judgement_anchor"}

    missing = {k for k in keys - claim_level if f'"{k}"' not in terminal}
    assert not missing, (
        f"report_terminal.html.j2 renders no branch for {sorted(missing)} — "
        "its filter is an allow-list, so a new disclosure is dropped, not surfaced"
    )


# --- a reference list read across a section break says so ------------------


def test_a_resumed_reference_list_is_disclosed_in_all_three_formats(tmp_path):
    """Crossing a section boundary to finish the bibliography is a guess, and
    the numbering of the later entries depends on it. The previous behaviour —
    stopping at the first heading — failed the other way and failed silently:
    a real paper reported 9 references and never attempted the other 6."""
    from papertrace.models import RefEntry, RefManifest

    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num=str(i), raw=f"Author {i}. A paper. 2020.", status="paywalled")
                 for i in range(1, 16)],
        references_resumed=True,
    )
    results = RunResults(manuscript="m.pdf", converter="pymupdf", claims=[_claim()])

    write_reports(results, manifest, tmp_path, png=False)
    rendered = {name: (tmp_path / name).read_text() for name in FORMATS}

    d = next((x for x in run_disclosures(results, manifest) if x.key == "references_resumed"), None)
    assert d is not None, [x.key for x in run_disclosures(results, manifest)]
    for name, body in rendered.items():
        assert d.token in body, f"token {d.token!r} missing from {name}"


def test_an_uninterrupted_reference_list_adds_no_warning(tmp_path):
    """An ordinary paper must not grow a caveat it has not earned."""
    from papertrace.models import RefEntry, RefManifest

    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="Author. A paper. 2020.", status="retrieved")],
    )
    fired = run_disclosures(
        RunResults(manuscript="m.pdf", converter="pymupdf", claims=[_claim()]), manifest
    )
    assert not any(d.key == "references_resumed" for d in fired)


def test_references_resumed_round_trips_and_older_manifests_still_load(tmp_path):
    """New field ⇒ schema update plus a round-trip test, and absent-safe: a
    manifest written before this field has no such key."""
    import json as _json

    import jsonschema

    from papertrace.models import RefEntry, RefManifest

    path = tmp_path / "refs_manifest.json"
    RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="Author. A paper. 2020.", status="retrieved")],
        references_resumed=True,
    ).to_json(path)

    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json"
    payload = _json.loads(path.read_text())
    jsonschema.validate(payload, _json.loads(schema_path.read_text()))
    assert RefManifest.from_json(path).references_resumed is True

    del payload["references_resumed"]          # a pre-field manifest
    path.write_text(_json.dumps(payload))
    jsonschema.validate(_json.loads(path.read_text()), _json.loads(schema_path.read_text()))
    assert RefManifest.from_json(path).references_resumed is False
