"""Literature scout against a mocked Europe PMC — no network, no LLM.

Covers: paper resolution (DOI + title routes), the citing register, the
year-split of keyword hits, dedup against the reference list (by DOI and by
first-author slug), self-exclusion, soft-fail on network errors, and the
JSON round-trip the report step depends on.
"""

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import (  # noqa: E402
    Block,
    RefEntry,
    RefManifest,
    ScoutResults,
    SourceMap,
)
from papertrace.scout import _keywords, scout_case  # noqa: E402

PAPER_TITLE = "Deep learning for chest radiograph triage in population imaging"


def _case(tmp_path: Path) -> Path:
    """A minimal case dir: refs manifest + ingested source map."""
    case = tmp_path / "case"
    ingest = case / "ingest" / "manuscript"
    ingest.mkdir(parents=True)
    smap = SourceMap(
        doc="paper.pdf",
        pages=9,
        blocks=[
            Block("block_0001", "sectionheader", 1, (0, 0, 1, 1), [], PAPER_TITLE),
            Block("block_0002", "text", 1, (0, 0, 1, 1), [], "Some abstract prose."),
        ],
    )
    smap.to_json(ingest / "source_map.json")
    manifest = RefManifest(
        manuscript="paper.pdf",
        entries=[
            RefEntry(num="1", raw="Smith A (2019) …", doi="10.1/CITED.1", slug="smith-2019"),
            RefEntry(num="2", raw="Jones B (2018) no doi here", slug="jones-2018"),
        ],
    )
    manifest.to_json(case / "refs_manifest.json")
    return case


def _epmc_result(title, year, doi="", authors="", journal="J Test"):
    return {
        "id": "999",
        "source": "MED",
        "title": title,
        "pubYear": str(year),
        "doi": doi,
        "authorString": authors,
        "journalTitle": journal,
    }


def _mock_transport():
    """Route /search and /citations like Europe PMC would."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/citations" in url:
            assert "/MED/33333333/citations" in url  # resolved id is used
            return httpx.Response(200, json={"citationList": {"citation": [
                # citations carry no DOI — title-dedup path
                {"id": "44", "source": "MED", "title": "A citing follow-up",
                 "authorString": "Lee K.", "journalAbbreviation": "Radiology",
                 "pubYear": 2022},
            ]}})
        q = request.url.params.get("query", "")
        if q.startswith('DOI:"') or q.startswith('TITLE:"'):
            return httpx.Response(200, json={"resultList": {"result": [{
                "id": "33333333", "source": "MED", "doi": "10.1000/PAPER",
                "title": PAPER_TITLE, "pubYear": "2020",
            }]}})
        # keyword neighbourhood search
        return httpx.Response(200, json={"resultList": {"result": [
            _epmc_result("Newer keyword hit", 2023, doi="10.1000/new1"),
            _epmc_result("Old uncited candidate", 2018, doi="10.1000/old1"),
            _epmc_result("Old but cited by DOI", 2019, doi="10.1/cited.1"),
            _epmc_result("Jones early work without DOI", 2018, authors="Jones B, Roe C."),
            _epmc_result(PAPER_TITLE, 2020, doi="10.1000/paper"),  # the paper itself
            _epmc_result("A citing follow-up", 2022, doi="10.1000/dup44"),  # dup of citing
            _epmc_result("Same-year neighbour", 2020, doi="10.1000/sameyear"),
        ]}})

    return httpx.MockTransport(handler)


def test_scout_registers_and_dedup(tmp_path):
    case = _case(tmp_path)
    res = scout_case(case, transport=_mock_transport())

    assert res.error == ""
    assert res.paper_doi == "10.1000/paper"
    assert res.paper_year == 2020
    assert res.resolved_via == "title"  # no --doi passed

    newer = {h.title for h in res.newer}
    assert newer == {"A citing follow-up", "Newer keyword hit"}
    assert {h.via for h in res.newer} == {"citing", "search"}

    # cited-by-DOI, cited-by-slug and the paper itself never reach overlooked;
    # a same-year hit does (year ≤ paper year, plausibly knowable)
    overlooked = {h.title for h in res.overlooked}
    assert overlooked == {"Old uncited candidate", "Same-year neighbour"}

    # newest first
    assert [h.year for h in res.newer] == [2023, 2022]


def test_scout_doi_override_resolves_via_doi(tmp_path):
    case = _case(tmp_path)
    res = scout_case(case, doi="10.1000/paper", transport=_mock_transport())
    assert res.resolved_via == "doi"
    assert res.error == ""


def test_scout_soft_fails_on_network_error(tmp_path):
    case = _case(tmp_path)

    def boom(request):
        raise httpx.ConnectError("no route")

    res = scout_case(case, transport=httpx.MockTransport(boom))
    assert "ConnectError" in res.error
    assert res.newer == [] and res.overlooked == []


def test_scout_unresolved_paper_is_recorded(tmp_path):
    case = _case(tmp_path)

    def empty(request):
        return httpx.Response(200, json={"resultList": {"result": []}})

    res = scout_case(case, transport=httpx.MockTransport(empty))
    assert "--doi" in res.error
    assert res.newer == []


def test_scout_json_roundtrip(tmp_path):
    case = _case(tmp_path)
    res = scout_case(case, transport=_mock_transport())
    out = tmp_path / "scout.json"
    res.to_json(out)

    data = json.loads(out.read_text())
    assert data["counts"] == {"newer": 2, "overlooked": 2}
    again = ScoutResults.from_json(out)
    assert again.paper_year == 2020
    assert {h.title for h in again.newer} == {h.title for h in res.newer}
    assert again.newer[0].via in ("citing", "search")


def test_keywords_drop_stopwords():
    kws = _keywords("Towards a novel deep learning analysis of chest radiographs")
    assert "towards" not in kws and "novel" not in kws and "analysis" not in kws
    assert kws[:3] == ["deep", "learning", "chest"]


def test_email_fallback_old_env_var(monkeypatch):
    from papertrace.cli import _email

    monkeypatch.delenv("PAPERTRACE_EMAIL", raising=False)
    monkeypatch.setenv("MANUSCRIPTAGENT_EMAIL", "old@example.org")
    assert _email(None) == "old@example.org"
    monkeypatch.setenv("PAPERTRACE_EMAIL", "new@example.org")
    assert _email(None) == "new@example.org"  # new name wins
