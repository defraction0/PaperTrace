"""Every disclosure reaches every reader — in every report format.

The formats phrase each disclosure at their own length, so the contract is the
`token`: a short literal from `papertrace.disclosures` that must appear
verbatim in the markdown, the editor HTML, the terminal HTML and the viewer's
embedded data (which the page renders, never re-derives). Asserting the
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

# four, since the interactive viewer: a disclosure the browser page cannot
# render is a disclosure one reader in four never sees
FORMATS = ("report.md", "report_editor.html", "report_terminal.html", "report_viewer.html")

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "papertrace" / "templates"
JINJA_FORMATS = ("report.md.j2", "report_editor.html.j2", "report_terminal.html.j2")


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
def test_every_run_disclosure_appears_in_every_format(tmp_path, coverage):
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
def test_every_claim_disclosure_appears_in_every_format(tmp_path, anchor_located):
    claim = _claim(
        unjudged_refs=["7", "9"],
        evidence_image="evidence/claim_01.png",
        anchor_located=anchor_located,
    )
    results = RunResults(manuscript="m.pdf", claims=[claim])
    rendered = _render(results, tmp_path)

    fired = claim_disclosures(claim)
    # `no_quote` belongs here: the fixture claim carries no verbatim quote, so
    # a verdict on it rests on the paraphrase and the reader is owed that in
    # every format. Pinning the set is what makes a newly added disclosure
    # arrive in this loop instead of quietly missing one template.
    assert {d.key for d in fired} == {"unjudged_refs", "anchor", "no_quote"}
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
def test_an_unverified_source_identity_is_disclosed_in_every_format(tmp_path, checks):
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
    # claim-level keys are covered by
    # test_every_claim_disclosure_key_is_rendered_by_every_jinja_format, which
    # checks all three Jinja formats rather than only this one
    missing = {k for k in keys - set(mod.CLAIM_KEYS) if f'"{k}"' not in terminal}
    assert not missing, (
        f"report_terminal.html.j2 renders no branch for {sorted(missing)} — "
        "its filter is an allow-list, so a new disclosure is dropped, not surfaced"
    )


# --- a reference list read across a section break says so ------------------


def test_a_resumed_reference_list_is_disclosed_in_every_format(tmp_path):
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


# --- claims whose HEADLINE is a pipeline state ------------------------------
#
# `gaps_by_location()` routes any claim whose headline is `not_retrieved` or
# `unchecked` out of the main loop and into the gap section — which printed the
# claim text and nothing else. So a claim citing [1,2] where source 1's check
# failed and source 2 was never obtainable said neither thing, and a
# `not_addressed` from a source that WAS successfully read vanished behind the
# `unchecked` headline that outranks it.


def _gap_claim() -> ClaimResult:
    """One source read and silent, one source's check failed, one never obtained.

    The headline is `unchecked`: a failed check makes "every available source
    was read and none addressed it" an assertion the run cannot make.
    """
    from papertrace.models import SourceJudgement

    claim = ClaimResult(
        id=4,
        claim="the intervention halved readmissions",
        location="Discussion",
        refs=["1", "2", "3"],
        judgements=[
            SourceJudgement(source_slug="read-2019", ref="1", verdict="not_addressed",
                            note="reports incidence only; silent on readmission"),
            SourceJudgement(source_slug="failed-2021", ref="2", verdict="unchecked",
                            note="check failed (TimeoutError) — the source WAS retrieved"),
        ],
        unjudged_refs=["3"],
    )
    claim.apply_headline()
    assert claim.verdict == "unchecked"
    return claim


def test_a_gap_claims_disclosures_reach_every_format(tmp_path):
    claim = _gap_claim()
    results = RunResults(manuscript="m.pdf", claims=[claim])
    rendered = _render(results, tmp_path)

    fired = claim_disclosures(claim)
    assert {d.key for d in fired} == {"sources", "unjudged_refs"}
    for d in fired:
        for name, body in rendered.items():
            assert d.token in body, f"{d.key}: token {d.token!r} missing from {name}"


def test_a_gap_claim_names_each_source_and_its_verdict(tmp_path):
    """The per-source rows themselves, not just the summary. A reader has to be
    able to see that [1] was read and said nothing while [2] was never read."""
    rendered = _render(RunResults(manuscript="m.pdf", claims=[_gap_claim()]), tmp_path)

    for name, body in rendered.items():
        assert "read-2019" in body, f"the source that WAS read is missing from {name}"
        assert "failed-2021" in body, f"the source whose check failed is missing from {name}"
        assert "not_addressed" in body or "DOES NOT ADDRESS" in body, (
            f"a successfully-checked not_addressed verdict is invisible in {name}"
        )


def test_a_gap_claim_keeps_its_note_in_every_format(tmp_path):
    """The two HTML looks printed no note at all for gap claims."""
    rendered = _render(RunResults(manuscript="m.pdf", claims=[_gap_claim()]), tmp_path)
    for name, body in rendered.items():
        assert "silent on readmission" in body, f"per-source note missing from {name}"


def test_a_gap_section_row_does_not_label_a_mixed_section_with_one_verdict(tmp_path):
    """The editor look printed `items[0].verdict` for the whole row, so a
    section holding one `not_retrieved` and one `unchecked` claimed both were
    whichever came first."""
    gap = _gap_claim()
    other = ClaimResult(id=5, claim="a second claim", location="Discussion", refs=["9"],
                        verdict="not_retrieved", note="cited source not available (paywalled)")
    rendered = _render(RunResults(manuscript="m.pdf", claims=[gap, other]), tmp_path)

    # scoped to the gap table: both verdicts appear elsewhere on the page (the
    # summary counts them), so an unscoped assertion passes even unfixed
    editor = rendered["report_editor.html"]
    table = editor.split('<table class="gaptbl">')[1].split("</table>")[0]
    assert "not retrieved" in table, table
    assert "unchecked" in table, table


# --- provenance without a picture -------------------------------------------


@pytest.mark.parametrize("anchor_located", [False, None])
def test_an_anchor_state_without_a_crop_still_reaches_every_format(tmp_path, anchor_located):
    """The disclosure used to be gated on `evidence_image`, so a verdict with a
    page and no crop told the reader nothing about what backed it."""
    claim = _claim(evidence_image=None, anchor_located=anchor_located, source_page=3)
    rendered = _render(RunResults(manuscript="m.pdf", claims=[claim]), tmp_path)

    anchor = next(d for d in claim_disclosures(claim) if d.key == "anchor")
    for name, body in rendered.items():
        assert anchor.token in body, f"anchor token missing from {name}"


# --- the HTML reports actually escape what they interpolate ------------------


def test_markup_in_source_text_cannot_reach_the_html_reports_unescaped(tmp_path):
    """`select_autoescape(["html"])` matches names ending `.html`. The templates
    are named `report_editor.html.j2`, so nothing ever matched and autoescape
    was off for all three formats — including the two that emit HTML.

    It went unnoticed because Europe PMC pre-escapes the markup in its titles,
    so the one field carrying angle brackets arrived already safe. Cited source
    PDFs are downloaded from third parties, and their text reaches the report.
    """
    hostile = '<script>alert("xss")</script>'
    claim = _claim(claim=f"a claim containing {hostile}", verdict="supported")
    rendered = _render(RunResults(manuscript="m.pdf", claims=[claim]), tmp_path)

    for name in ("report_editor.html", "report_terminal.html"):
        assert "<script>" not in rendered[name], f"{name} interpolated raw markup"
        assert "&lt;script&gt;" in rendered[name], f"{name} did not escape it"

    # markdown is not HTML and must not grow entities — it stays verbatim
    assert hostile in rendered["report.md"]


def test_claim_keys_names_only_keys_a_producer_actually_emits():
    """A key in the set that no producer emits is dead weight that silently
    widens the exemption below. `judgement_anchor` was exactly that."""
    import re

    from papertrace import disclosures as mod

    src = Path(mod.__file__).read_text()
    real = set(re.findall(r'key="([a-z_]+)"', src))
    assert mod.CLAIM_KEYS <= real, f"not emitted by any producer: {sorted(mod.CLAIM_KEYS - real)}"


def test_every_claim_disclosure_key_is_rendered_by_every_jinja_format():
    """The viewer renders claim disclosures generically; the other three filter
    by explicit key. So a new claim-level disclosure reaches one reader in four
    and vanishes for the rest — the `source_identity` failure one layer down,
    and the layer with no guard on it.

    The six keys that exist today all pass. The point is that the seventh
    cannot be added without this going red first.
    """
    from papertrace import disclosures as mod

    for name in JINJA_FORMATS:
        src = (TEMPLATES / name).read_text()
        missing = {k for k in mod.CLAIM_KEYS if f'"{k}"' not in src}
        assert not missing, (
            f"{name} renders no branch for {sorted(missing)} — it filters claim "
            "disclosures by explicit key, so a new one is dropped, not surfaced"
        )
