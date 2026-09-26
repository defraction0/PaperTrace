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
import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# `[dev]` installs the SDK, so CI runs this module rather than skipping it;
# `tests/test_packaging.py` asserts that it stays there. A 1.x SDK — other tools
# install one — is skipped with the same reason rather than failing collection.
pytest.importorskip("mcp", reason="pip install -e '.[dev]' supplies the MCP SDK")

from importlib.metadata import version as _installed  # noqa: E402

if int(_installed("mcp").split(".")[0]) < 2:
    pytest.skip("the server is written against the MCP SDK 2.x — pip install -e '.[dev]'",
                allow_module_level=True)

import anyio  # noqa: E402
import typer  # noqa: E402
from mcp import Client  # noqa: E402

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import check as check_mod  # noqa: E402
from papertrace import cli, mcp_server  # noqa: E402
from papertrace.disclosures import (  # noqa: E402
    ANCHOR_LOCATED_TOKEN,
    ANCHOR_NOT_LOCATED_TOKEN,
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
# one call to each read tool, with whatever arguments it needs beyond `case`
READ_CALLS = (
    ("audit_summary", {}), ("list_claims", {}), ("get_claim", {"claim_id": 1}),
    ("get_evidence", {"claim_id": 1}), ("list_references", {}), ("list_gaps", {}),
    ("get_scout", {}),
)
ROOT = Path(__file__).resolve().parent.parent


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


# --------------------------------------------------------------------------
# start_audit and audit_status — the one tool that spends, run as a job
# --------------------------------------------------------------------------


def test_the_server_lists_nine_tools_and_marks_the_one_that_spends():
    tools = _tools()

    assert set(tools) == READ_TOOLS | {"start_audit", "audit_status"}
    status = tools["audit_status"].annotations
    assert status.read_only_hint is True and status.open_world_hint is False
    # a host decides whether to ask before calling on these hints: the one tool
    # that spends model calls, queries the network and rewrites a case folder
    # must say all three
    spend = tools["start_audit"].annotations
    assert spend.read_only_hint is False
    assert spend.destructive_hint is True
    assert spend.open_world_hint is True


def test_every_backend_start_audit_offers_is_one_ingest_accepts():
    """The backend vocabulary has no constant to reuse, so the offer is checked
    against the rule that enforces it: `resolve_backend` refuses anything else."""
    from papertrace.ingest import resolve_backend

    offered = _enum(_tools()["start_audit"].input_schema["properties"]["backend"])
    assert offered == ["auto", "docling", "pymupdf"]
    for backend in offered:
        resolve_backend(backend)


@pytest.fixture
def ready(monkeypatch, tmp_path):
    """Everything start_audit checks before it spends, satisfied offline — and
    never read from the machine: `_email` falls back to a SAVED CONFIG in the
    developer's home, which is how a CLI test once passed locally and failed on
    every CI python."""
    monkeypatch.setattr(check_mod, "claude_available", lambda: True)
    monkeypatch.setenv("PAPERTRACE_EMAIL", "t@example.org")
    monkeypatch.delenv("MANUSCRIPTAGENT_EMAIL", raising=False)
    monkeypatch.setenv("PAPERTRACE_CONFIG", str(tmp_path / "no-saved-config.json"))


def _paper(tmp_path, name="paper.pdf") -> Path:
    pdf = tmp_path / name
    pdf.write_bytes(b"%PDF-1.4\n% never parsed: the pipeline is faked\n" + name.encode())
    return pdf


def _fake_pipeline(monkeypatch, body=None) -> list[dict]:
    calls: list[dict] = []

    def fake(**kw):
        calls.append(kw)
        if body is not None:
            body(kw)

    monkeypatch.setattr(cli, "_run_pipeline", fake)
    return calls


def _start(server, **args) -> dict:
    return _json(_call("start_audit", {k: str(v) if isinstance(v, Path) else v
                                       for k, v in args.items()}, server))


def _await(server, case: str) -> dict:
    return _json(_call("audit_status", {"case": case, "wait_seconds": 20}, server))


def test_start_audit_refuses_a_manuscript_that_is_not_there(ready, monkeypatch, tmp_path):
    calls = _fake_pipeline(monkeypatch)

    result = _call("start_audit", {"manuscript": str(tmp_path / "absent.pdf")})

    assert result.is_error
    assert "absent.pdf" in _text(result)
    assert calls == []


def test_start_audit_refuses_when_claude_is_not_on_its_path(ready, monkeypatch, tmp_path):
    """A host starts the server with a PATH of its own choosing. Finding that
    out after ingest has run would be finding it out late."""
    calls = _fake_pipeline(monkeypatch)
    monkeypatch.setattr(check_mod, "claude_available", lambda: False)

    result = _call("start_audit", {"manuscript": str(_paper(tmp_path))})

    assert result.is_error
    assert "claude" in _text(result) and "PATH" in _text(result)
    assert calls == []


def test_start_audit_refuses_without_a_contact_email(ready, monkeypatch, tmp_path):
    calls = _fake_pipeline(monkeypatch)
    monkeypatch.delenv("PAPERTRACE_EMAIL")

    result = _call("start_audit", {"manuscript": str(_paper(tmp_path))})

    assert result.is_error
    assert "email" in _text(result).lower()
    assert calls == []


def test_start_audit_refuses_a_case_folder_holding_another_paper(ready, monkeypatch, tmp_path):
    """One case folder per paper — `_guard_case`'s rule, applied before anything
    is spent rather than after ingest and extraction already were."""
    calls = _fake_pipeline(monkeypatch)
    case = tmp_path / "case"
    case.mkdir()
    RefManifest(manuscript="other.pdf", manuscript_sha256="f" * 64).to_json(
        case / "refs_manifest.json")

    result = _call("start_audit", {"manuscript": str(_paper(tmp_path)), "case": str(case)})

    assert result.is_error
    assert "other.pdf" in _text(result)
    assert calls == []


def test_the_job_hands_the_pipeline_every_parameter(ready, monkeypatch, tmp_path):
    import inspect

    params = set(inspect.signature(cli._run_pipeline).parameters)
    calls = _fake_pipeline(monkeypatch)
    pdf = _paper(tmp_path)
    own = _paper(tmp_path, "paper-supplement.pdf")
    provided = tmp_path / "my_pdfs"
    provided.mkdir()
    server = mcp_server.build_server()

    started = _start(server, manuscript=pdf, case=tmp_path / "case", model="claude-opus-5",
                     backend="pymupdf", scout=False, doi="10.1/x", formats=["viewer"],
                     provided=provided, supplements=[str(own)], llm_refs=False,
                     max_claims=3, max_sources=2)
    assert _await(server, started["case"])["state"] == "finished"

    assert calls == [{
        "manuscript": pdf.resolve(), "case": (tmp_path / "case").resolve(),
        "provided": provided.resolve(), "email": "t@example.org", "model": "claude-opus-5",
        "png": False, "backend": "pymupdf", "with_scout": False, "doi": "10.1/x",
        "formats": ["viewer"], "supplement": [own.resolve()], "llm_refs": False,
        "max_claims": 3, "max_sources": 2,
    }]
    assert set(calls[0]) == params, "a pipeline parameter the job never decides on"


def test_the_job_runs_the_real_pipeline_body_and_keeps_what_it_prints(
    ready, monkeypatch, tmp_path, capfd
):
    """The other job tests replace `_run_pipeline` whole. Here only its seven
    stages are stubbed, so the real body runs inside the job — email, case,
    guard, the case folder's own .gitignore, the banner, the stage order and
    the closing line — and anything it printed around `cli.console` would
    reach the process's real stdout, which this test reads."""
    order = []
    for name in ("_ingest_pipeline", "_extract_pipeline", "_refs_pipeline", "scout",
                 "_check_pipeline", "highlight", "_report_pipeline"):
        monkeypatch.setattr(cli, name, lambda _n=name, **kw: order.append(_n))
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    server = mcp_server.build_server()
    case = tmp_path / "case"

    status = _await(server, _start(server, manuscript=_paper(tmp_path), case=case,
                                   formats=["viewer"])["case"])

    assert status["state"] == "finished", status
    assert order == ["_ingest_pipeline", "_extract_pipeline", "_refs_pipeline", "scout",
                     "_check_pipeline", "highlight", "_report_pipeline"]
    assert (case / ".gitignore").read_text() == "*\n"
    assert any("PaperTrace" in line for line in status["log"]), "the banner is in the log"
    assert any("report_viewer.html" in line for line in status["log"])
    out, _ = capfd.readouterr()
    assert out == "", "the real pipeline printed around the captured console"

def test_start_audit_defaults_are_papertrace_runs(ready, monkeypatch, tmp_path):
    """No case named: beside the paper, named after it, as `papertrace run`
    does. Every other default is the CLI's own."""
    calls = _fake_pipeline(monkeypatch)
    pdf = _paper(tmp_path)
    server = mcp_server.build_server()

    started = _start(server, manuscript=pdf)
    _await(server, started["case"])

    assert started["case"] == str((tmp_path / "paper").resolve())
    assert calls[0] | {"manuscript": None, "case": None} == {
        "manuscript": None, "case": None, "provided": None, "email": "t@example.org",
        "model": None, "png": False, "backend": "auto", "with_scout": True, "doi": None,
        "formats": [], "supplement": [], "llm_refs": True, "max_claims": None,
        "max_sources": None,
    }


class _Terminal:
    """A stream that says it is a terminal — what `_interactive()` looks for."""

    def __init__(self):
        self.written = []

    def isatty(self):
        return True

    def write(self, s):
        self.written.append(s)
        return len(s)

    def flush(self):
        pass

    def readline(self):
        return "1\n"  # a person answering the first menu, were one asked


def test_inside_the_job_nobody_can_be_asked_and_no_old_model_lingers(
    ready, monkeypatch, tmp_path
):
    """Three things the job guarantees, observed from inside it.

    A terminal on both streams and no `CI`: outside the job, `_interactive()`
    would be True here, and a disputed label would be put to a person who is
    not there. Inside it, nobody can be asked — a prompt reads end-of-file.

    A model left behind by an earlier audit in this process is gone, so a run
    whose judging makes no calls cannot report that model as its judge.

    The pipeline's console lands in the job's log, never on stdout, which
    under stdio is the protocol's own wire.
    """
    terminal_in, terminal_out = _Terminal(), _Terminal()
    monkeypatch.setattr(sys, "stdin", terminal_in)
    monkeypatch.setattr(sys, "stdout", terminal_out)
    monkeypatch.delenv("CI", raising=False)
    assert cli._interactive(), "the fixture must look like a terminal, or this proves nothing"
    monkeypatch.setattr(ask_mod, "_MODELS", {ask_mod.SITE_CHECK: "claude-haiku-4-5"})
    seen = {}

    def body(kw):
        seen["interactive"] = cli._interactive()
        try:
            seen["answer"] = input()
        except EOFError:
            seen["answer"] = "end of file"
        seen["model"] = ask_mod.model_for(ask_mod.SITE_CHECK)
        cli.console.print("[green]✓[/green] said by the pipeline")

    _fake_pipeline(monkeypatch, body)
    console = cli.console
    server = mcp_server.build_server()

    status = _await(server, _start(server, manuscript=_paper(tmp_path))["case"])

    assert status["state"] == "finished", status
    assert seen == {"interactive": False, "answer": "end of file", "model": None}
    assert "✓ said by the pipeline" in status["log"]
    assert not any("said by the pipeline" in s for s in terminal_out.written)
    assert cli.console is console, "the console must be handed back when the job ends"


def test_a_pipeline_that_stops_is_failed_with_the_reason_it_printed(ready, monkeypatch, tmp_path):
    def body(kw):
        cli.console.print("[red]refs_manifest.json not found[/red] — run `papertrace refs` first")
        raise typer.Exit(1)

    _fake_pipeline(monkeypatch, body)
    server = mcp_server.build_server()

    status = _await(server, _start(server, manuscript=_paper(tmp_path))["case"])

    assert status["state"] == "failed"
    assert "refs_manifest.json not found" in status["error"]


def test_an_unexpected_exception_is_failed_with_its_type_and_message(
    ready, monkeypatch, tmp_path
):
    """In SDK v2 an exception escaping a tool reaches the model as a bare
    `Error executing tool`. A job records its reason instead of losing it."""
    def body(kw):
        raise RuntimeError("the layout models could not be loaded")

    _fake_pipeline(monkeypatch, body)
    server = mcp_server.build_server()

    status = _await(server, _start(server, manuscript=_paper(tmp_path))["case"])

    assert status["state"] == "failed"
    assert status["error"] == "RuntimeError: the layout models could not be loaded"


def test_a_second_audit_is_refused_while_one_runs(ready, monkeypatch, tmp_path):
    """The console, the model record and stdin are process-global: two audits
    sharing them would interleave logs and misattribute models."""
    import threading

    gate = threading.Event()
    _fake_pipeline(monkeypatch, lambda kw: gate.wait(20))
    server = mcp_server.build_server()
    first = _start(server, manuscript=_paper(tmp_path), case=tmp_path / "first")

    try:
        refused = _call("start_audit", {"manuscript": str(_paper(tmp_path, "other.pdf")),
                                        "case": str(tmp_path / "second")}, server)
        assert refused.is_error
        assert first["case"] in _text(refused)
    finally:
        gate.set()
    assert _await(server, first["case"])["state"] == "finished"

    # and the slot is free again once it has ended
    second = _start(server, manuscript=_paper(tmp_path, "other.pdf"), case=tmp_path / "second")
    assert _await(server, second["case"])["state"] == "finished"


def test_read_tools_refuse_the_case_whose_audit_is_running(ready, monkeypatch, tmp_path):
    """The results.json on disk then belongs to the previous run, or is half
    written. Presenting it as this run's would be the silent failure."""
    import threading

    gate = threading.Event()
    _fake_pipeline(monkeypatch, lambda kw: gate.wait(20))
    case = _case(tmp_path)
    server = mcp_server.build_server()
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    started = _start(server, manuscript=_paper(tmp_path), case=case)

    try:
        for tool, args in READ_CALLS:
            result = _call(tool, {"case": started["case"], **args}, server)
            assert result.is_error, tool
            assert "running" in _text(result), tool
    finally:
        gate.set()
    _await(server, started["case"])
    assert not _call("audit_summary", {"case": started["case"]}, server).is_error


def test_audit_status_waits_for_the_end_and_no_longer(ready, monkeypatch, tmp_path):
    import time

    _fake_pipeline(monkeypatch, lambda kw: time.sleep(0.3))
    server = mcp_server.build_server()
    started = _start(server, manuscript=_paper(tmp_path))

    t0 = time.monotonic()
    status = _json(_call("audit_status", {"case": started["case"], "wait_seconds": 15}, server))

    assert status["state"] == "finished"
    assert time.monotonic() - t0 < 10
    # under the 60 s a TypeScript-SDK host allows a request, by schema
    too_long = _call("audit_status", {"case": started["case"], "wait_seconds": 51}, server)
    assert too_long.is_error


def test_a_finished_status_points_at_the_summary_and_carries_no_counts(
    ready, monkeypatch, tmp_path
):
    """A count without its disclosures is what every reader here is protected
    from, so the status says where the result is rather than quoting it."""
    _fake_pipeline(monkeypatch)
    server = mcp_server.build_server()

    status = _await(server, _start(server, manuscript=_paper(tmp_path))["case"])

    assert "counts" not in status
    assert "audit_summary" in status["next"]


def test_the_status_of_a_case_this_server_never_ran_says_so(tmp_path):
    case = _case(tmp_path)

    status = _json(_call("audit_status", {"case": str(case)}))

    assert status["state"] == "not_started"
    assert "MCP server" in status["next"]


# --------------------------------------------------------------------------
# `papertrace mcp` — the command a host launches, over real stdio
# --------------------------------------------------------------------------

SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_papertrace_mcp_writes_nothing_to_stdout_when_its_stdin_closes(tmp_path):
    """Under stdio, stdout IS the protocol. A banner, a warning or a stray print
    before serving begins lands on the wire and corrupts the first message, and
    no in-memory test can see it — only a real process can."""
    import os
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "papertrace", "mcp"],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
        env={**os.environ, "PYTHONPATH": SRC,
             "PAPERTRACE_CONFIG": str(tmp_path / "no-saved-config.json")},
    )

    assert proc.returncode == 0, proc.stderr.decode()[-2000:]
    assert proc.stdout == b"", proc.stdout[:500]


def test_a_host_lists_the_tools_and_reads_a_case_over_real_stdio(tmp_path):
    """The whole path a host takes: launch the command, speak MCP over its
    pipes, read an audit. The in-memory client skips exactly this."""
    from mcp import StdioServerParameters

    case = _case(tmp_path)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "papertrace", "mcp"],
        env={"PYTHONPATH": SRC, "PAPERTRACE_CONFIG": str(tmp_path / "no-saved-config.json")},
    )

    async def go(client):
        names = {t.name for t in (await client.list_tools()).tools}
        return names, await client.call_tool("audit_summary", {"case": str(case)})

    names, summary = _session(params, go)

    assert names == READ_TOOLS | {"start_audit", "audit_status"}
    assert _json(summary)["counts"] == _results().counts()


def test_without_the_sdk_papertrace_mcp_says_how_to_install_it(monkeypatch, capsys):
    """On stderr, and nothing on stdout — a host reading the wire would take
    even an error message for a malformed protocol message."""
    # every cached `mcp.*`, not only `mcp`: an import finds a cached submodule
    # without consulting its parent, so masking the package alone hides nothing
    for name in [n for n in sys.modules if n == "mcp" or n.startswith("mcp.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.delitem(sys.modules, "papertrace.mcp_server", raising=False)
    monkeypatch.setattr("importlib.metadata.version", _no_distribution)

    with pytest.raises(typer.Exit) as stop:
        cli.mcp_command()

    out, err = capsys.readouterr()
    assert stop.value.exit_code == 2
    assert out == ""
    assert "pip install 'papertrace[mcp]'" in err


def _no_distribution(name):
    from importlib.metadata import PackageNotFoundError

    raise PackageNotFoundError(name)


# --------------------------------------------------------------------------
# review findings — each a way the server could present a case as a result
# it is not, or a verdict without what qualifies it
# --------------------------------------------------------------------------


def _record(case: Path) -> dict:
    return json.loads((case / "mcp_audit.json").read_text())


def _left_record(case: Path, state: str, *, age: float = 0, **fields) -> Path:
    """A record as another server process left it, last refreshed `age` s ago."""
    import time

    path = case / "mcp_audit.json"
    path.write_text(json.dumps({
        "schema": "mcp_audit/1", "state": state, "manuscript": "/papers/paper.pdf",
        "started": "2026-09-26T10:00:00+00:00", "ended": None, "error": "", "scout": True,
        **fields,
    }))
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def _age_file(path: Path, seconds: float) -> None:
    """Move a file's mtime by `seconds` — positive is later, negative earlier."""
    import time

    t = time.time() + seconds
    os.utime(path, (t, t))


def test_after_a_failed_audit_nothing_on_disk_is_served_as_its_result(
    ready, monkeypatch, tmp_path
):
    """A rerun can rewrite the manifest and die before `check`, leaving one
    run's references beside another run's verdicts. The case is refused with
    the failure, never served as a result."""
    def body(kw):
        RefManifest(manuscript="paper.pdf").to_json(Path(kw["case"]) / "refs_manifest.json")
        raise RuntimeError("claude -p failed: not logged in")

    case = _case(tmp_path)  # an earlier, complete audit
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    _fake_pipeline(monkeypatch, body)
    server = mcp_server.build_server()
    started = _start(server, manuscript=_paper(tmp_path), case=case)
    assert _await(server, started["case"])["state"] == "failed"

    for tool, args in READ_CALLS:
        result = _call(tool, {"case": started["case"], **args}, server)
        assert result.is_error, tool
        assert "failed" in _text(result) and "not logged in" in _text(result), tool


def test_an_audit_its_server_never_finished_is_refused_and_reported_interrupted(tmp_path):
    """A host that stops the server stops the audit mid-run. Whatever it wrote
    is on disk, and the only thing that says it is incomplete is the record the
    job wrote when it started — read here by a server that never ran it."""
    case = _case(tmp_path)
    _left_record(case, "running", age=120)
    server = mcp_server.build_server()

    status = _json(_call("audit_status", {"case": str(case)}, server))
    summary = _call("audit_summary", {"case": str(case)}, server)

    assert status["state"] == "interrupted"
    assert status["started"] == "2026-09-26T10:00:00+00:00"
    assert summary.is_error and "did not finish" in _text(summary)


def test_the_record_outlives_the_server_that_wrote_it(ready, monkeypatch, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    _fake_pipeline(monkeypatch)
    first = mcp_server.build_server()
    case = _await(first, _start(first, manuscript=_paper(tmp_path), scout=False)["case"])["case"]

    status = _json(_call("audit_status", {"case": case}, mcp_server.build_server()))

    assert status["state"] == "finished"
    assert "not kept" in status["next"], "a log that did not survive must say so"
    record = _record(Path(case))
    assert record["state"] == "finished" and record["scout"] is False
    jsonschema.validate(record, json.loads((ROOT / "schemas" / "mcp_audit.schema.json").read_text()))


def test_a_claim_read_on_its_own_still_carries_the_runs_caveats(tmp_path):
    """A host can call get_claim without ever calling audit_summary. The source
    behind claim 1's headline is one whose identity nobody confirmed — the
    reports say so above every verdict, so a verdict read alone says it too."""
    results = _results(scope=LIMITED_SCOPE)
    case = _case(tmp_path, results=results)

    for tool, args in (("get_claim", {"claim_id": 1}), ("list_claims", {})):
        body = _text(_call(tool, {"case": str(case), **args}))
        for d in run_disclosures(results, _manifest()):
            assert d.token in body, (tool, d.key)


def test_the_evidence_caption_carries_every_caveat_on_its_verdict(tmp_path):
    """The crop is shown under its verdict, so the verdict's caveats come with
    it — here [6] sits past where the numbering stopped being confirmed, and a
    crop captioned without that would show the wrong paper as evidence."""
    claim = _paraphrase_claim(True)
    claim.refs = ["6"]
    results = _results(claims=[claim])
    case = _case(tmp_path, results=results)

    caption = _text(_call("get_evidence", {"case": str(case), "claim_id": 4}))

    fired = claim_disclosures(claim, _manifest()) + run_disclosures(results, _manifest())
    assert "claim_numbering" in {d.key for d in fired}
    for d in fired:
        assert d.token in caption, d.key


def test_start_audit_will_not_put_an_audit_wherever_the_host_started_it(
    ready, monkeypatch, tmp_path
):
    """`papertrace run` keeps the audit in the working directory when the
    paper's folder is read-only — a directory the person chose by standing in
    it. A host chose this server's, so start_audit asks for a case instead."""
    calls = _fake_pipeline(monkeypatch)
    pdf = _paper(tmp_path)
    access = os.access
    monkeypatch.setattr(os, "access", lambda p, mode: Path(p) != pdf.parent and access(p, mode))

    result = _call("start_audit", {"manuscript": str(pdf)})

    assert result.is_error
    assert str(pdf.parent) in _text(result) and "`case`" in _text(result)
    assert calls == []


def test_what_the_checks_said_before_the_audit_is_kept_in_its_log(ready, monkeypatch, tmp_path):
    """A legacy case folder is accepted with a warning that its identity was
    compared by name only. That warning is printed before the job starts, and
    it belongs in the job's log, not a buffer nobody reads."""
    _fake_pipeline(monkeypatch)
    case = tmp_path / "case"
    case.mkdir()
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    server = mcp_server.build_server()

    status = _await(server, _start(server, manuscript=_paper(tmp_path), case=case)["case"])

    assert any("predates content hashing" in line for line in status["log"])


def test_an_incomplete_scan_says_so_in_its_caveat(tmp_path):
    scout = ScoutResults(paper_title="A paper", paper_identity="confirmed",
                         error="Europe PMC timed out")
    case = _case(tmp_path, scout=scout)

    assert "Europe PMC timed out" in _json(_call("get_scout", {"case": str(case)}))["caveat"]


def test_a_scan_the_latest_audit_did_not_run_is_not_served_as_its_own(
    ready, monkeypatch, tmp_path
):
    """An audit run without the scout leaves an earlier run's scout.json on
    disk, anchored to whatever paper and DOI that run used."""
    case = _case(tmp_path, scout=ScoutResults(paper_title="A paper", paper_identity="confirmed"))
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    _fake_pipeline(monkeypatch)
    server = mcp_server.build_server()
    _await(server, _start(server, manuscript=_paper(tmp_path), case=case, scout=False)["case"])

    result = _call("get_scout", {"case": str(case)}, server)

    assert result.is_error
    assert "without the scout" in _text(result)


def test_an_sdk_too_old_for_the_server_gets_the_install_line_too(monkeypatch, capsys):
    """A 1.x SDK — other tools install one — has no `MCPServer`, and fails with
    an ImportError that is not a ModuleNotFoundError."""
    for name in [n for n in sys.modules if n == "mcp" or n.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, name)
    old = types.ModuleType("mcp")
    old.__path__ = []
    monkeypatch.setitem(sys.modules, "mcp", old)
    monkeypatch.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
    monkeypatch.delitem(sys.modules, "papertrace.mcp_server", raising=False)
    monkeypatch.setattr("importlib.metadata.version", lambda name: "1.28.1")

    with pytest.raises(typer.Exit) as stop:
        cli.mcp_command()

    out, err = capsys.readouterr()
    assert stop.value.exit_code == 2 and out == ""
    assert "pip install 'papertrace[mcp]'" in err


STRUCTURED = {
    "audit_summary": "AuditSummary", "list_claims": "ClaimList", "get_claim": "ClaimDetail",
    "list_references": "ReferenceList", "list_gaps": "Gaps", "get_scout": "ScoutOut",
    "audit_status": "AuditStatus",
}


def test_every_tool_output_matches_its_schema_in_schemas(tmp_path):
    """Gate 2: what the server publishes is a contract, and `schemas/` is where
    this repository's contracts live. Each output validates against it, and
    each schema names exactly the keys its tool returns — a field added to one
    and not the other turns this red."""
    jsonschema = pytest.importorskip("jsonschema")
    published = json.loads((ROOT / "schemas" / "mcp_tools.schema.json").read_text())
    case = _case(tmp_path, results=_results(scope=LIMITED_SCOPE),
                 scout=ScoutResults(paper_title="A paper", paper_identity="unverified"))
    args = {"get_claim": {"claim_id": 1}}

    for tool, shape in STRUCTURED.items():
        out = _json(_call(tool, {"case": str(case), **args.get(tool, {})}))
        jsonschema.validate(out, {**published, "$ref": f"#/$defs/{tool}"})
        declared = set(published["$defs"][tool]["properties"])
        assert declared == set(getattr(mcp_server, shape).__annotations__), tool


# --------------------------------------------------------------------------
# second review pass — the record says only what it measured
# --------------------------------------------------------------------------


def test_a_named_sources_crop_carries_only_its_own_anchor_state(tmp_path):
    """Claim 1's headline source (beta-2021) was not boxed; alpha-2020 was.
    alpha's crop captioned with beta's anchor state would put two
    contradictory statements under one picture."""
    case = _case(tmp_path)

    caption = _text(_call("get_evidence", {"case": str(case), "claim_id": 1,
                                            "source": "alpha-2020"}))

    assert ANCHOR_LOCATED_TOKEN in caption
    assert ANCHOR_NOT_LOCATED_TOKEN not in caption


def test_an_audit_another_server_is_running_is_neither_read_nor_restarted(
    ready, monkeypatch, tmp_path
):
    """Two hosts can each launch their own server. A record refreshed moments
    ago is a live audit in another process — not an interrupted one — and a
    second pipeline in the same folder would be the mixing `_guard_case`
    exists to prevent."""
    case = _case(tmp_path)
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    _left_record(case, "running", age=1)
    calls = _fake_pipeline(monkeypatch)
    server = mcp_server.build_server()

    status = _json(_call("audit_status", {"case": str(case)}, server))
    summary = _call("audit_summary", {"case": str(case)}, server)
    start = _call("start_audit", {"manuscript": str(_paper(tmp_path)), "case": str(case)}, server)

    assert status["state"] == "running" and status["log"] is None
    assert summary.is_error and "another server process" in _text(summary)
    assert start.is_error and "another server process" in _text(start)
    assert calls == []


def test_an_interrupted_audit_can_be_started_again(ready, monkeypatch, tmp_path):
    case = _case(tmp_path)
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    _left_record(case, "running", age=600)
    _fake_pipeline(monkeypatch)
    server = mcp_server.build_server()

    status = _await(server, _start(server, manuscript=_paper(tmp_path), case=case)["case"])

    assert status["state"] == "finished"
    assert _record(case)["state"] == "finished"


def test_the_job_keeps_its_record_fresh_while_it_runs(ready, monkeypatch, tmp_path):
    """Freshness is the measurement that tells a live audit from an abandoned
    one, so the job refreshes its record for as long as it runs."""
    import time

    monkeypatch.setattr(mcp_server, "HEARTBEAT_SECONDS", 0.05)
    seen = {}

    def body(kw):
        record = Path(kw["case"]) / "mcp_audit.json"
        _age_file(record, -600)
        time.sleep(0.5)
        seen["age"] = time.time() - record.stat().st_mtime

    _fake_pipeline(monkeypatch, body)
    server = mcp_server.build_server()

    _await(server, _start(server, manuscript=_paper(tmp_path))["case"])

    assert seen["age"] < 5, seen


def test_a_label_audit_that_never_ran_is_null_even_when_occurrences_were_counted(tmp_path):
    """Without clean.md the label-level lists are empty because nothing read
    them, while the occurrence audit ran from the source map. `[]` would say
    every cited label was reached."""
    occurrences_only = {
        "labels_in_text": [], "covered": [], "missing": [],
        "occurrences": {**OCCURRENCE_COVERAGE["occurrences"]},
    }
    case = _case(tmp_path, results=_results(coverage=occurrences_only))

    gaps = _json(_call("list_gaps", {"case": str(case)}))

    assert gaps["unreached_labels"] is None
    assert [o["label"] for o in gaps["unreached_citations"]] == ["8", "4"]


def test_the_status_names_the_manuscript_the_same_way_before_and_after_a_restart(
    ready, monkeypatch, tmp_path
):
    """A published field has one meaning: the audited PDF's absolute path."""
    _fake_pipeline(monkeypatch)
    pdf = _paper(tmp_path)
    server = mcp_server.build_server()
    running = _start(server, manuscript=pdf)
    finished = _await(server, running["case"])

    later = _json(_call("audit_status", {"case": running["case"]}, mcp_server.build_server()))

    assert running["manuscript"] == finished["manuscript"] == later["manuscript"]
    assert later["manuscript"] == str(pdf.resolve())


def test_a_later_run_that_finished_the_judging_supersedes_a_failed_audit(
    ready, monkeypatch, tmp_path
):
    """The failure was the last thing an MCP server saw — not the last thing
    that happened. A results.json written after it is a later run's judging
    of the manifest now on disk, so the case reads again, in this server too."""
    def body(kw):
        raise RuntimeError("claude -p failed: not logged in")

    case = _case(tmp_path)
    RefManifest(manuscript="paper.pdf").to_json(case / "refs_manifest.json")
    _fake_pipeline(monkeypatch, body)
    server = mcp_server.build_server()
    _await(server, _start(server, manuscript=_paper(tmp_path), case=case)["case"])
    assert _call("audit_summary", {"case": str(case)}, server).is_error

    _results().to_json(case / "out" / "results.json")  # e.g. `papertrace check`, later
    _age_file(case / "out" / "results.json", +5)

    assert not _call("audit_summary", {"case": str(case)}, server).is_error


def test_a_scan_written_after_an_audit_without_the_scout_is_served(tmp_path):
    case = _case(tmp_path, scout=ScoutResults(paper_title="A paper", paper_identity="confirmed"))
    _left_record(case, "finished", age=60, ended="2026-09-26T10:05:00+00:00", scout=False)
    _age_file(case / "out" / "scout.json", 0)  # `papertrace scout`, after that audit

    result = _call("get_scout", {"case": str(case)})

    assert not result.is_error, _text(result)


def test_a_broken_import_inside_a_current_sdk_keeps_its_traceback(monkeypatch):
    """With a 2.x SDK installed, an import that still fails is a real fault. The
    install line would contradict itself ("needs version 2 — found 2.x") and
    hide the one thing that says what broke."""
    for name in [n for n in sys.modules if n == "mcp" or n.startswith("mcp.")]:
        monkeypatch.delitem(sys.modules, name)
    broken = types.ModuleType("mcp")
    broken.__path__ = []
    monkeypatch.setitem(sys.modules, "mcp", broken)
    monkeypatch.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
    monkeypatch.delitem(sys.modules, "papertrace.mcp_server", raising=False)
    monkeypatch.setattr("importlib.metadata.version", lambda name: "2.3.0")

    with pytest.raises(ImportError, match="MCPServer"):
        cli.mcp_command()


def test_a_recorded_failure_does_not_send_the_host_to_a_log_that_was_not_kept(tmp_path):
    case = _case(tmp_path)
    _left_record(case, "failed", age=60, ended="2026-09-26T10:05:00+00:00",
                 error="claude -p failed: not logged in")

    status = _json(_call("audit_status", {"case": str(case)}))

    assert status["state"] == "failed" and status["log"] is None
    assert "the log" not in status["next"]
    assert "not kept" in status["next"]


@pytest.mark.parametrize("field", [{"scout": "false"}, {"schema": "mcp_audit/2"}],
                         ids=["scout-not-a-bool", "a-later-schema"])
def test_a_record_this_server_cannot_vouch_for_is_refused(tmp_path, field):
    """A record read under the wrong rules is a guess. "false" is not False,
    and a v2 record may mean something v1 does not."""
    case = _case(tmp_path)
    _left_record(case, "finished", age=60, ended="2026-09-26T10:05:00+00:00", **field)

    result = _call("audit_summary", {"case": str(case)})

    assert result.is_error and "unreadable" in _text(result)


def test_every_status_a_record_can_produce_matches_the_schema(tmp_path):
    """The status schema describes what the server returns from its own job AND
    from a record another server left — not only the shape one test happened
    to build."""
    jsonschema = pytest.importorskip("jsonschema")
    published = json.loads((ROOT / "schemas" / "mcp_tools.schema.json").read_text())
    cases = {
        "not_started": {},
        "running": {"state": "running", "age": 1},
        "interrupted": {"state": "running", "age": 600},
        "finished": {"state": "finished", "age": 60, "ended": "2026-09-26T10:05:00+00:00"},
        "failed": {"state": "failed", "age": 60, "ended": "2026-09-26T10:05:00+00:00",
                   "error": "RuntimeError: boom"},
    }

    for expected, record in cases.items():
        case = _case(tmp_path / expected)
        if record:
            _left_record(case, record.pop("state"), **record)
        status = _json(_call("audit_status", {"case": str(case)}))
        assert status["state"] == expected
        jsonschema.validate(status, {**published, "$ref": "#/$defs/audit_status"})


def test_the_published_timings_are_the_ones_the_server_uses():
    """The schemas state the refresh and staleness windows in prose, because a
    reader of a record has to know them — so a changed constant must not leave
    the contract quoting the old one."""
    record = json.loads((ROOT / "schemas" / "mcp_audit.schema.json").read_text())
    tools = json.loads((ROOT / "schemas" / "mcp_tools.schema.json").read_text())
    state = tools["$defs"]["audit_status"]["properties"]["state"]["description"]

    for prose in (record["description"], state):
        assert f"every {mcp_server.HEARTBEAT_SECONDS:g} s" in prose
        assert f"{mcp_server.STALE_AFTER_SECONDS:g} s" in prose
