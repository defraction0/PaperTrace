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

    # cited-by-DOI, cited-by-slug and the paper itself never reach overlooked —
    # and neither does a same-year hit, which has its own register
    overlooked = {h.title for h in res.overlooked}
    assert overlooked == {"Old uncited candidate"}
    assert {h.title for h in res.same_year} == {"Same-year neighbour"}

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
    assert data["counts"] == {"newer": 2, "overlooked": 1, "same_year": 1}
    again = ScoutResults.from_json(out)
    assert again.paper_year == 2020
    assert {h.title for h in again.newer} == {h.title for h in res.newer}
    assert again.newer[0].via in ("citing", "search")


def test_keywords_drop_stopwords():
    kws = _keywords("Towards a novel deep learning analysis of chest radiographs")
    assert "towards" not in kws and "novel" not in kws and "analysis" not in kws
    # ranked by length rather than by position, so the specific words win
    # wherever they sit in the title — `radiographs` over `deep`
    assert kws[:2] == ["radiographs", "learning"], kws


def test_email_fallback_old_env_var(monkeypatch):
    from papertrace.cli import _email

    monkeypatch.delenv("PAPERTRACE_EMAIL", raising=False)
    monkeypatch.setenv("MANUSCRIPTAGENT_EMAIL", "old@example.org")
    assert _email(None) == "old@example.org"
    monkeypatch.setenv("PAPERTRACE_EMAIL", "new@example.org")
    assert _email(None) == "new@example.org"  # new name wins


# --- a failure has to say which failure it was ------------------------------
#
# On a real audit the operator was told "paper not identified in Europe PMC —
# pass --doi to pin it", so they found the DOI and re-ran with it. Same message.
# They then curled Europe PMC by hand to establish what the tool already knew:
# the paper is a Journal Pre-proof and simply is not indexed. `scout.json` also
# recorded `"doi": ""`, so the artifact could not show what had been tried.


def _no_hits(request):
    return httpx.Response(200, json={"resultList": {"result": []}})


def test_a_pinned_doi_that_finds_nothing_does_not_ask_for_a_doi(tmp_path):
    case = _case(tmp_path)
    res = scout_case(case, doi="10.1016/j.ejrad.2026.113206",
                     transport=httpx.MockTransport(_no_hits))

    assert "--doi" not in res.error, res.error
    assert "10.1016/j.ejrad.2026.113206" in res.error, res.error


def test_a_pinned_doi_that_finds_nothing_says_the_paper_is_not_indexed(tmp_path):
    """A DOI lookup returning nothing is a stronger fact than a failed title
    heuristic, and a different one: absence of indexing, not absence of skill.
    Zero candidates must not read as a clean literature search."""
    case = _case(tmp_path)
    res = scout_case(case, doi="10.1016/j.ejrad.2026.113206",
                     transport=httpx.MockTransport(_no_hits))

    assert "not indexed" in res.error.lower(), res.error
    assert res.newer == [] and res.overlooked == []


def test_the_doi_that_was_tried_survives_into_the_artifact(tmp_path):
    """`scout.json` carried an empty doi, so the null was uninterpretable from
    the file alone."""
    case = _case(tmp_path)
    res = scout_case(case, doi="10.1016/j.ejrad.2026.113206",
                     transport=httpx.MockTransport(_no_hits))

    assert res.paper_doi == "10.1016/j.ejrad.2026.113206"


def test_without_a_doi_the_advice_to_pin_one_still_stands(tmp_path):
    """The original message is right when no DOI was given — keep it."""
    case = _case(tmp_path)
    res = scout_case(case, transport=httpx.MockTransport(_no_hits))
    assert "--doi" in res.error


# --- the keyword query has to be about the subject ---------------------------
#
# A live audit of "Image registration improves inter-reader agreement of
# objective response in CT assessment of pancreas adenocarcinoma" searched for
# `image AND registration AND improves AND inter-reader`. Two faults in one
# line: `improves` is a verb carrying no topic, and taking the FIRST four
# content words never reaches the subject, which in this title sits at the end.
# The register came back with a stroke conference abstract whose shouted title
# contained "IMPROVES".

_REAL_TITLE = (
    "Image registration improves inter-reader agreement of objective response "
    "in CT assessment of pancreas adenocarcinoma"
)


def test_the_keyword_query_reaches_the_subject_of_the_paper():
    kws = _keywords(_REAL_TITLE)
    assert "adenocarcinoma" in kws, kws
    assert "registration" in kws, kws


def test_a_title_verb_is_not_a_keyword():
    """`improves` matched an unrelated abstract on the same verb."""
    assert "improves" not in _keywords(_REAL_TITLE)


def test_keyword_selection_does_not_depend_on_position_in_the_title():
    """The subject is as often at the end of a title as the start."""
    front = _keywords("Pancreas adenocarcinoma assessed by registration of CT")
    back = _keywords("Registration of CT for assessment of pancreas adenocarcinoma")
    assert "adenocarcinoma" in front and "adenocarcinoma" in back


# --- a same-year paper is not an overlooked one ------------------------------


def test_a_same_year_hit_is_not_filed_as_overlooked(tmp_path):
    """"Existed but uncited" invites the reader to ask what the authors missed.
    A paper from the manuscript's own year may have appeared after submission,
    so holding it to that standard is unfair — and on a real 2026 paper every
    one of the fifteen candidates was from 2026."""
    case = _case(tmp_path)
    res = scout_case(case, transport=_mock_transport())

    assert "Same-year neighbour" not in {h.title for h in res.overlooked}


def test_a_same_year_hit_is_kept_in_its_own_register(tmp_path):
    """Not dropped either: a paper published early in the same year is exactly
    the kind of thing a reviewer might legitimately raise. It is a third
    status, and folding it into either neighbour states something false."""
    case = _case(tmp_path)
    res = scout_case(case, transport=_mock_transport())

    assert "Same-year neighbour" in {h.title for h in res.same_year}
    assert "Same-year neighbour" not in {h.title for h in res.newer}


def test_the_same_year_register_round_trips(tmp_path):
    """Gate 2 — a new field is a schema change and a round-trip test."""
    from papertrace.models import ScoutResults

    case = _case(tmp_path)
    res = scout_case(case, transport=_mock_transport())
    path = tmp_path / "scout.json"
    res.to_json(path)

    back = ScoutResults.from_json(path)
    assert [h.title for h in back.same_year] == [h.title for h in res.same_year]
    assert json.loads(path.read_text())["counts"]["same_year"] == len(res.same_year)


def test_an_older_uncited_hit_is_still_overlooked(tmp_path):
    """The register keeps its job — this is a narrowing, not a removal."""
    case = _case(tmp_path)
    res = scout_case(case, transport=_mock_transport())
    assert "Old uncited candidate" in {h.title for h in res.overlooked}


# --- Europe PMC returns escaped markup ---------------------------------------


def test_markup_entities_in_a_title_are_decoded(tmp_path):
    """Real hits arrived as `CTV&lt;sub&gt;boost&lt;/sub&gt;` and were rendered
    verbatim into the report."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/citations" in url:
            return httpx.Response(200, json={"citationList": {"citation": []}})
        q = request.url.params.get("query", "")
        if q.startswith('DOI:"') or q.startswith('TITLE:"'):
            return httpx.Response(200, json={"resultList": {"result": [{
                "id": "33333333", "source": "MED", "doi": "10.1000/PAPER",
                "title": PAPER_TITLE, "pubYear": "2020",
            }]}})
        return httpx.Response(200, json={"resultList": {"result": [
            _epmc_result("Improving CTV&lt;sub&gt;boost&lt;/sub&gt; delineation",
                         2018, doi="10.1000/esc"),
        ]}})

    res = scout_case(_case(tmp_path), transport=httpx.MockTransport(handler))
    titles = " ".join(h.title for h in res.overlooked)
    assert "&lt;" not in titles, titles
    assert "CTV<sub>boost</sub>" in titles


def test_the_same_year_register_reaches_all_three_report_formats(tmp_path):
    """A register the reader of one format cannot see is a register that does
    not exist for them — the same rule the disclosure parity test enforces."""
    from papertrace.models import RunResults, ScoutHit, ScoutResults
    from papertrace.report import write_reports

    scout = ScoutResults(
        paper_title="A paper", paper_year=2026, date="2026-09-04",
        # no apostrophe: the HTML looks autoescape their interpolations, so a
        # literal assertion on the rendered page must not straddle an escape
        same_year=[ScoutHit(title="A neighbour from the same publication year", year=2026,
                            doi="10.1000/sy", via="search", journal="Eur J Radiol")],
    )
    out = tmp_path / "out"
    write_reports(RunResults(manuscript="m.pdf"), None, out, png=False, scout=scout)

    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        body = (out / name).read_text()
        assert "A neighbour from the same publication year" in body, f"missing from {name}"
        assert "same year" in body.lower(), f"unlabelled in {name}"
