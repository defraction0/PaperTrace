"""The MCP server: PaperTrace as a tool for any MCP host.

Every tool is called the way a host calls it — through the SDK's in-memory
`Client` — so what these tests read is what a model reads. Offline: case
folders are written with the dataclasses, evidence PNGs are drawn with
pymupdf, and the one tool that spends is exercised with the pipeline faked.

The server is a fifth reader of every disclosure. The parity loops below are
`test_disclosure_parity.py`'s contract extended to it: a token a report format
must carry, the MCP output must carry too.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# `[dev]` installs the SDK, so CI runs this module rather than skipping it;
# `tests/test_packaging.py` asserts that it stays there
pytest.importorskip("mcp", reason="pip install -e '.[dev]' supplies the MCP SDK")

import anyio  # noqa: E402
from mcp import Client  # noqa: E402

from papertrace import mcp_server  # noqa: E402
from papertrace.disclosures import (  # noqa: E402
    SCOPE_TOKEN,
    claim_disclosures,
    judgement_disclosures,
    run_disclosures,
)
from papertrace.models import (  # noqa: E402
    REF_STATUSES,
    VERDICTS,
    ClaimResult,
    RefEntry,
    RefManifest,
    RunResults,
    ScoutHit,
    ScoutResults,
    SourceJudgement,
    UncitedClaim,
)

READ_TOOLS = {
    "audit_summary", "list_claims", "get_claim", "get_evidence",
    "list_references", "list_gaps", "get_scout",
}


# --------------------------------------------------------------------------
# calling the server the way a host does
# --------------------------------------------------------------------------


def _session(server, fn):
    async def go():
        async with Client(server, raise_exceptions=True) as client:
            return await fn(client)

    return anyio.run(go)


def _call(tool: str, args: dict, server=None):
    server = server or mcp_server.build_server()
    return _session(server, lambda c: c.call_tool(tool, args))


def _tools(server=None) -> dict:
    server = server or mcp_server.build_server()
    listing = _session(server, lambda c: c.list_tools())
    return {t.name: t for t in listing.tools}


def _text(result) -> str:
    """Everything a model reads from one result, as one string."""
    return "\n".join(b.text for b in result.content if getattr(b, "type", "") == "text")


def _json(result) -> dict:
    """The result as the model reads it — the JSON text, keys in the order sent."""
    assert not result.is_error, _text(result)
    return json.loads(_text(result))


# --------------------------------------------------------------------------
# a case folder, written the way the pipeline writes one
# --------------------------------------------------------------------------


def _png(path: Path) -> None:
    try:
        import pymupdf
    except ImportError:  # PyMuPDF < 1.24
        import fitz as pymupdf
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=80, height=40)
    page.insert_text((5, 20), path.stem[-12:])
    page.get_pixmap().save(str(path))
    doc.close()


def _split_claim() -> ClaimResult:
    """Two cited sources that disagree: the headline is the contradiction."""
    c = ClaimResult(
        id=1,
        claim="the model reached an AUC of 0.94",
        quote="The model reached an AUC of 0.94 [3, 4].",
        location="Results §2",
        refs=["3", "4"],
        judgements=[
            SourceJudgement(
                source_slug="alpha-2020", ref="3", verdict="supported",
                note="Table 2 reports 0.94.", source_page=2, source_block="block_0012",
                anchor_phrases=["AUC 0.94"], evidence_image="evidence/claim_01_alpha-2020_p2.png",
                anchor_located=True,
            ),
            SourceJudgement(
                source_slug="beta-2021", ref="4", verdict="contradicted",
                note="The source reports 0.77, not 0.94.", source_page=5,
                source_block="block_0040", anchor_phrases=["AUC of 0.77"],
                evidence_image="evidence/claim_01_beta-2021_p5.png",
                continuation_images=["evidence/claim_01_beta-2021_p5_cont2.png"],
                anchor_located=False,
            ),
        ],
    )
    c.apply_headline()
    return c


def _unread_claim() -> ClaimResult:
    return ClaimResult(
        id=2, claim="attendance was 99%", quote="Attendance was 99% [7].",
        location="Discussion", refs=["7"], verdict="not_retrieved", unjudged_refs=["7"],
    )


def _withheld_claim() -> ClaimResult:
    return ClaimResult(
        id=3, claim="the cohort was imaged twice", quote="The cohort was imaged twice [9].",
        location="Methods", refs=["9"], verdict="unchecked", withheld_refs=["9"],
        note="verdict withheld — the readings of the reference list disagree about [9]",
    )


def _paraphrase_claim(anchor_located=None) -> ClaimResult:
    return ClaimResult(
        id=4, claim="screening halves mortality", location="Introduction", refs=["3"],
        verdict="supported", note="Stated in the abstract.", source_slug="alpha-2020",
        source_page=1, source_block="block_0002", anchor_phrases=["halves mortality"],
        evidence_image="evidence/claim_04_alpha-2020_p1.png", anchor_located=anchor_located,
    )


def _manifest() -> RefManifest:
    return RefManifest(
        manuscript="paper.pdf",
        manuscript_sha256="0" * 64,
        numbering_verified=False,
        numbering_note="the parse and the deposit disagree from [5]",
        unverified_from=5,
        labels_disputed=["9"],
        labels_uncomparable=[],
        entries=[
            RefEntry(num="3", raw="Alpha A. A paper. 2020.", doi="10.1/alpha", status="retrieved",
                     resolver="unpaywall", slug="alpha-2020", title_check="verified",
                     pdf_path="/private/home/someone/sources_resolved/alpha-2020.pdf"),
            RefEntry(num="4", raw="Beta B. Another. 2021.", status="provided", resolver="user",
                     slug="beta-2021", title_check="unverifiable",
                     pdf_path="/private/home/someone/my_pdfs/beta.pdf"),
            RefEntry(num="7", raw="Gamma G. Paywalled. 2019.", doi="10.1/gamma",
                     status="paywalled", reason="no open-access copy found"),
            RefEntry(num="8", raw="Delta D. Unasked. 2018.", status="skipped",
                     skipped_by="claims", reason="no selected claim cites it"),
            RefEntry(num="9", raw="Epsilon E. Disputed. 2022.", status="retrieved",
                     resolver="crossref", slug="epsilon-2022", title_check="verified",
                     pdf_path="/private/home/someone/sources_resolved/epsilon-2022.pdf"),
        ],
    )


OCCURRENCE_COVERAGE = {
    "schema": "coverage/3", "unit": "occurrence", "source": "source_map",
    "labels_in_text": ["3", "4", "7", "8", "9"], "covered": ["3", "4", "7", "9"],
    "missing": ["8"], "labels_partially_covered": [], "labels_uncertain_only": [],
    "occurrences": {
        "total": 6, "covered": 4, "uncovered": 1, "uncertain": 1,
        "items": [
            {"occ_id": "block_0020:40:0", "label": "8", "block": "block_0020", "page": 3,
             "offset": 40, "group": "[8]", "section": "Methods",
             "sentence": "Imaging followed the usual protocol [8].",
             "covered_by": [], "status": "uncovered"},
            {"occ_id": "block_0031:12:0", "label": "4", "block": "block_0031", "page": 4,
             "offset": 12, "group": "[4]", "section": "Results",
             "sentence": "A second sentence citing the same source [4].",
             "covered_by": [], "status": "uncertain"},
        ],
    },
}


def _results(claims=None, **kw) -> RunResults:
    base = dict(
        manuscript="paper.pdf", checker="claude -p · claude-opus-5", date="2026-09-26",
        refs_total=5, refs_available=3, converter="pymupdf",
        source_converters={"alpha-2020": "pymupdf", "beta-2021": "docling 2.118.1"},
        claims=claims if claims is not None else [
            _split_claim(), _unread_claim(), _withheld_claim(), _paraphrase_claim(),
        ],
        uncited=[UncitedClaim(id=1, claim="most clinics now screen",
                              quote="Most clinics now screen routinely.", location="Introduction")],
        coverage=OCCURRENCE_COVERAGE,
        truncated={"manuscript": {"chars": 90000, "limit": 60000}},
    )
    base.update(kw)
    return RunResults(**base)


def _case(tmp_path, results=None, manifest="default", scout=None, images=True) -> Path:
    case = tmp_path / "case"
    (case / "out").mkdir(parents=True)
    results = results if results is not None else _results()
    results.to_json(case / "out" / "results.json")
    manifest = _manifest() if manifest == "default" else manifest
    if manifest is not None:
        manifest.to_json(case / "refs_manifest.json")
    if scout is not None:
        scout.to_json(case / "out" / "scout.json")
    if images:
        for c in results.claims:
            for a in [*c.judgements, c]:
                for rel in [a.evidence_image, *a.continuation_images]:
                    if rel:
                        _png(case / "out" / rel)
    (case / "out" / "report.md").write_text("# report\n")
    return case


LIMITED_SCOPE = {
    "claims": {"requested": [1, 2], "judged": [1, 2], "extracted": 4},
    "sources": {"cap": None, "selection": [1, 2], "skipped_for_claims": ["8"],
                "skipped_by_cap": []},
}


# --------------------------------------------------------------------------
# what the server advertises
# --------------------------------------------------------------------------


def test_every_read_tool_is_marked_read_only_and_closed_world():
    tools = _tools()

    assert READ_TOOLS <= set(tools)
    for name in READ_TOOLS:
        ann = tools[name].annotations
        assert ann.read_only_hint is True, name
        assert ann.open_world_hint is False, name


def _enum(schema):
    """The first `enum` anywhere in a JSON schema fragment."""
    if isinstance(schema, dict):
        if "enum" in schema:
            return schema["enum"]
        schema = list(schema.values())
    for part in schema if isinstance(schema, list) else []:
        if (found := _enum(part)) is not None:
            return found
    return None


def test_the_filters_are_the_models_own_vocabularies():
    """A verdict or status spelled here a second time is a second vocabulary to
    keep in step, and the one place it is defined is `models.py`."""
    tools = _tools()

    verdict = tools["list_claims"].input_schema["properties"]["verdict"]
    status = tools["list_references"].input_schema["properties"]["status"]
    assert _enum(verdict) == list(VERDICTS)
    assert _enum(status) == list(REF_STATUSES)
    for name in READ_TOOLS - {"get_evidence"}:
        assert tools[name].output_schema, f"{name} publishes no output schema"


# --------------------------------------------------------------------------
# audit_summary — every run-level disclosure reaches the model
# --------------------------------------------------------------------------


@pytest.mark.parametrize("coverage", [
    OCCURRENCE_COVERAGE,
    {"labels_in_text": ["1", "2"], "covered": ["1"], "missing": ["2"]},
    {"labels_in_text": [], "covered": [], "missing": []},
])
def test_audit_summary_carries_every_run_disclosure(tmp_path, coverage):
    results = _results(coverage=coverage, scope=LIMITED_SCOPE)
    case = _case(tmp_path, results=results)

    body = _text(_call("audit_summary", {"case": str(case)}))

    fired = run_disclosures(results, _manifest())
    assert {d.key for d in fired} >= {
        "scope", "truncation", "converter", "source_fidelity", "source_identity",
        "numbering", "labels_disputed",
    }
    for d in fired:
        assert d.token in body, f"{d.key}: token {d.token!r} never reaches the MCP reader"


def test_audit_summary_states_the_counts_the_file_holds(tmp_path):
    case = _case(tmp_path)

    summary = _json(_call("audit_summary", {"case": str(case)}))

    assert summary["counts"] == _results().counts()
    assert summary["checker"] == "claude -p · claude-opus-5"
    assert summary["references"] == {"total": 5, "available": 3}
    assert summary["uncited_assertions"] == 1
    assert summary["case"] == str(case.resolve())
    assert str(case.resolve() / "out" / "report.md") in summary["reports"]


def test_a_limited_audit_is_stated_first_and_last(tmp_path):
    """`CLAUDE.md`: the scope is stated with the caveats at the top AND at the
    end of every format. The JSON a model reads has an end too."""
    case = _case(tmp_path, results=_results(scope=LIMITED_SCOPE))

    summary = _json(_call("audit_summary", {"case": str(case)}))
    listing = _json(_call("list_claims", {"case": str(case)}))

    assert summary["disclosures"][0]["key"] == "scope"
    for result in (summary, listing):
        assert list(result)[-1] == "limited"
        assert SCOPE_TOKEN in result["limited"]


def test_a_whole_paper_audit_says_nothing_was_left_out(tmp_path):
    case = _case(tmp_path)

    assert _json(_call("audit_summary", {"case": str(case)}))["limited"] is None
    assert _json(_call("list_claims", {"case": str(case)}))["limited"] is None


def test_a_case_without_results_is_refused_rather_than_reported_empty(tmp_path):
    """Absent is not zero. Empty counts for a case nobody checked would read as
    an audit that found nothing."""
    case = tmp_path / "case"
    case.mkdir()

    result = _call("audit_summary", {"case": str(case)})

    assert result.is_error
    assert "results.json" in _text(result)
    assert "counts" not in _text(result)


def test_an_unreadable_results_file_is_refused_with_its_reason(tmp_path):
    case = tmp_path / "case"
    (case / "out").mkdir(parents=True)
    (case / "out" / "results.json").write_text("{ half a file")

    result = _call("audit_summary", {"case": str(case)})

    assert result.is_error
    assert "unreadable" in _text(result)


def test_a_relative_case_path_is_resolved_and_said_back(tmp_path, monkeypatch):
    """A host starts the server in a working directory of its own choosing, so a
    relative path is echoed back resolved — the model can see where it looked."""
    _case(tmp_path)
    monkeypatch.chdir(tmp_path)

    summary = _json(_call("audit_summary", {"case": "case"}))

    assert summary["case"] == str((tmp_path / "case").resolve())


# --------------------------------------------------------------------------
# list_claims and get_claim — every claim-level disclosure reaches the model
# --------------------------------------------------------------------------


def test_list_claims_rows_carry_their_caveats(tmp_path):
    case = _case(tmp_path)

    rows = _json(_call("list_claims", {"case": str(case)}))["claims"]

    assert [r["id"] for r in rows] == [1, 2, 3, 4]
    assert [r["verdict"] for r in rows] == ["contradicted", "not_retrieved", "unchecked",
                                           "supported"]
    for c, row in zip(_results().claims, rows, strict=True):
        for d in claim_disclosures(c, _manifest()):
            assert any(d.token in line for line in row["caveats"]), (c.id, d.key)


def test_list_claims_filters_on_the_headline_verdict(tmp_path):
    """Claim 1 has a supporting source, and its headline is the contradiction:
    filtering on `supported` must not return it as if it were supported."""
    case = _case(tmp_path)

    listing = _json(_call("list_claims", {"case": str(case), "verdict": "supported"}))

    assert [r["id"] for r in listing["claims"]] == [4]
    assert listing["claims_total"] == 4


@pytest.mark.parametrize("claim", [
    _split_claim(), _unread_claim(), _withheld_claim(),
    _paraphrase_claim(True), _paraphrase_claim(False), _paraphrase_claim(None),
], ids=["split", "unread", "withheld", "anchor-located", "anchor-not-located", "anchor-unknown"])
def test_every_claim_disclosure_reaches_get_claim(tmp_path, claim):
    case = _case(tmp_path, results=_results(claims=[claim]))

    body = _text(_call("get_claim", {"case": str(case), "claim_id": claim.id}))

    fired = claim_disclosures(claim, _manifest())
    fired += [d for j in claim.judgements for d in judgement_disclosures(j)]
    assert fired, "a fixture that fires nothing proves nothing"
    for d in fired:
        assert d.token in body, f"claim {claim.id} {d.key}: {d.token!r} never reaches the model"


def test_get_claim_returns_each_source_judgement_with_its_rationale(tmp_path):
    case = _case(tmp_path)

    detail = _json(_call("get_claim", {"case": str(case), "claim_id": 1}))

    assert detail["verdict"] == "contradicted"
    assert detail["quote"] == "The model reached an AUC of 0.94 [3, 4]."
    by_source = {j["source"]: j for j in detail["judgements"]}
    assert by_source["beta-2021"]["rationale"] == "The source reports 0.77, not 0.94."
    assert by_source["beta-2021"]["page"] == 5
    assert by_source["beta-2021"]["anchor_located"] is False
    assert by_source["alpha-2020"]["verdict"] == "supported"


def test_get_claim_names_the_ids_that_exist_when_asked_for_one_that_does_not(tmp_path):
    case = _case(tmp_path)

    result = _call("get_claim", {"case": str(case), "claim_id": 99})

    assert result.is_error
    assert "99" in _text(result) and "1–4" in _text(result)


# --------------------------------------------------------------------------
# get_evidence — the crops themselves, never a picture the tool did not cut
# --------------------------------------------------------------------------


def _images(result) -> list:
    return [b for b in result.content if getattr(b, "type", "") == "image"]


def test_get_evidence_returns_the_headline_crops_with_their_anchor_caption(tmp_path):
    """The headline is beta-2021's contradiction, whose passage crosses a break
    and whose anchor was not boxed — two images, captioned as unboxed."""
    case = _case(tmp_path)

    result = _call("get_evidence", {"case": str(case), "claim_id": 1})

    assert not result.is_error, _text(result)
    images = _images(result)
    assert len(images) == 2
    assert {i.mime_type for i in images} == {"image/png"}
    caption = _text(result)
    assert "beta-2021" in caption
    for d in judgement_disclosures(_split_claim().judgements[1]):
        assert d.token in caption


def test_get_evidence_for_a_named_source_shows_that_source(tmp_path):
    case = _case(tmp_path)

    result = _call("get_evidence", {"case": str(case), "claim_id": 1, "source": "alpha-2020"})

    assert len(_images(result)) == 1
    assert "alpha-2020" in _text(result)


def test_get_evidence_for_an_unread_source_says_nothing_was_read(tmp_path):
    case = _case(tmp_path)

    result = _call("get_evidence", {"case": str(case), "claim_id": 2})

    assert result.is_error
    assert not _images(result)
    assert "not retrieved" in _text(result)


def test_get_evidence_refuses_an_image_outside_the_case(tmp_path):
    """`results.json` is a file anyone can edit. A crop path that resolves
    outside `<case>/out/` is refused, never followed."""
    elsewhere = tmp_path / "elsewhere.png"
    _png(elsewhere)
    claim = _paraphrase_claim(True)
    claim.evidence_image = "../../elsewhere.png"
    case = _case(tmp_path, results=_results(claims=[claim]), images=False)

    result = _call("get_evidence", {"case": str(case), "claim_id": 4})

    assert result.is_error
    assert not _images(result)
    assert "outside" in _text(result)


def test_get_evidence_names_a_crop_that_was_never_written(tmp_path):
    case = _case(tmp_path, images=False)

    result = _call("get_evidence", {"case": str(case), "claim_id": 4})

    assert result.is_error
    assert "claim_04_alpha-2020_p1.png" in _text(result)


# --------------------------------------------------------------------------
# list_references — the manifest, as recorded
# --------------------------------------------------------------------------


def test_list_references_carries_no_local_path(tmp_path):
    """The viewer strips `pdf_path` for the same reason: where a PDF sits on the
    auditor's disk is not a finding about the paper."""
    case = _case(tmp_path)

    body = _text(_call("list_references", {"case": str(case)}))

    assert "/private/home/someone" not in body
    assert "pdf_path" not in body


def test_three_state_numbering_fields_keep_their_null(tmp_path):
    """`[]` is a measurement and `null` is "never computed". Coercing one into
    the other is the defect `CLAUDE.md` names twice."""
    measured = _manifest()
    never = _manifest()
    never.labels_uncomparable = None
    never.numbering_corroborated = None
    measured.numbering_corroborated = False

    for manifest, uncomparable, corroborated in ((measured, [], False), (never, None, None)):
        case = _case(tmp_path / str(uncomparable), manifest=manifest)
        numbering = _json(_call("list_references", {"case": str(case)}))["numbering"]
        assert numbering["labels_uncomparable"] == uncomparable
        assert numbering["corroborated"] is corroborated
        assert numbering["verified"] is False
        assert numbering["labels_disputed"] == ["9"]


def test_a_skipped_reference_says_why_it_was_skipped(tmp_path):
    case = _case(tmp_path)

    listing = _json(_call("list_references", {"case": str(case), "status": "skipped"}))

    assert [r["label"] for r in listing["references"]] == ["8"]
    assert listing["references"][0]["skipped_by"] == "claims"
    assert listing["references"][0]["reason"] == "no selected claim cites it"
    assert listing["by_status"]["skipped"] == 1
    assert listing["total"] == 5


# --------------------------------------------------------------------------
# list_gaps — the gap register is part of the result
# --------------------------------------------------------------------------


def test_list_gaps_lists_every_register(tmp_path):
    case = _case(tmp_path)

    gaps = _json(_call("list_gaps", {"case": str(case)}))

    assert [u["quote"] for u in gaps["uncited_assertions"]] == ["Most clinics now screen routinely."]
    assert [(o["label"], o["status"]) for o in gaps["unreached_citations"]] == [
        ("8", "uncovered"), ("4", "uncertain"),
    ]
    assert gaps["unreached_labels"] == ["8"]
    assert [c["id"] for c in gaps["unchecked_claims"]] == [2, 3]
    assert "reached by an extracted claim" in gaps["coverage"]


def test_a_coverage_audit_that_never_ran_is_null_not_empty(tmp_path):
    """No bracketed labels means the audit could not run — `null`, and the
    caveat saying so — never an empty list that reads as nothing missed."""
    case = _case(tmp_path, results=_results(
        coverage={"labels_in_text": [], "covered": [], "missing": []}))

    gaps = _json(_call("list_gaps", {"case": str(case)}))

    assert gaps["coverage"] is None
    assert gaps["unreached_labels"] is None
    assert gaps["unreached_citations"] is None
    assert any(d["key"] == "coverage_caveat" for d in gaps["disclosures"])


# --------------------------------------------------------------------------
# get_scout — candidates, with the scout's own caveat
# --------------------------------------------------------------------------


def test_get_scout_carries_its_caveat(tmp_path):
    scout = ScoutResults(
        paper_title="A paper", paper_year=2022, resolved_via="doi", paper_identity="unverified",
        newer=[ScoutHit(title="A later paper", year=2024, doi="10.1/later", via="citing")],
    )
    case = _case(tmp_path, scout=scout)

    result = _json(_call("get_scout", {"case": str(case)}))

    assert [h["title"] for h in result["newer"]] == ["A later paper"]
    assert result["paper"]["identity"] == "unverified"
    assert "absence from these lists proves nothing" in result["caveat"]


def test_get_scout_without_a_scan_is_refused(tmp_path):
    case = _case(tmp_path)

    result = _call("get_scout", {"case": str(case)})

    assert result.is_error
    assert "scout.json" in _text(result)
