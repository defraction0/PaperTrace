"""The interactive viewer is a fourth look on the same results — and it degrades out loud.

`report_viewer.html` embeds the case's data as JSON and renders it in the
browser, so what can go wrong is different from the other three looks: the
JSON can be broken by the text it carries, the manuscript can be absent, a
disclosure decided in Python can fail to reach the page, and the page can
quietly depend on the network. Each is pinned here; the browser-side logic has
its own suite in `test_report_viewer_js.py`.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402
from papertrace.disclosures import (  # noqa: E402
    claim_disclosures,
    judgement_disclosures,
    run_disclosures,
)
from papertrace.models import (  # noqa: E402
    ClaimResult,
    RefEntry,
    RefManifest,
    RunResults,
    ScoutHit,
    ScoutResults,
    SourceJudgement,
    UncitedClaim,
)
from papertrace.report import FORMATS, write_reports  # noqa: E402

DATA_BLOCK = re.compile(r'<script id="pt-data" type="application/json">(.*?)</script>', re.S)


def _embedded(html: str) -> dict:
    m = DATA_BLOCK.search(html)
    assert m, "the viewer carries its case data in a <script id=\"pt-data\"> block"
    return json.loads(m.group(1))


def _results(**kw) -> RunResults:
    judged = ClaimResult(
        id=1, claim="X causes Y.", quote="We found that X causes Y (7).", location="Results ¶2",
        refs=["7"], verdict="supported", note="Stated on page 2.",
        judgements=[SourceJudgement(
            source_slug="a-2020", ref="7", verdict="supported", note="Stated on page 2.",
            source_page=2, source_block="block_0002", anchor_phrases=["X causes Y"],
            evidence_image="evidence/claim_01_a-2020_p2.png",
            continuation_images=["evidence/claim_01_a-2020_p3_cont2.png"],
            anchor_located=True,
        )],
    )
    judged.apply_headline()
    gap = ClaimResult(id=2, claim="Z is common.", quote="Z is common (8).", location="Intro ¶1",
                      refs=["8"], verdict="not_retrieved",
                      note="cited source not available (paywalled)")
    base = dict(
        manuscript="m.pdf", date="2026-01-01", checker="claude -p · test",
        refs_total=2, refs_available=1, converter="docling 2.0.0",
        claims=[judged, gap],
        uncited=[UncitedClaim(id=1, claim="W is rare.", quote="W is rare.", location="Intro ¶1")],
        coverage={"labels_in_text": ["7", "8"], "covered": ["7", "8"], "missing": []},
    )
    base.update(kw)
    return RunResults(**base)


def _manifest() -> RefManifest:
    return RefManifest(
        manuscript="m.pdf",
        entries=[
            RefEntry(num="7", raw="Author A. A paper. 2020.", status="retrieved", slug="a-2020",
                     resolver="unpaywall", title_check="verified", doi="10.1/a"),
            RefEntry(num="8", raw="Author B. Another paper. 2021.", status="paywalled",
                     reason="DOI resolved but no legal open-access copy found", slug="b-2021"),
        ],
        numbering_verified=True,
    )


ANNOTATED = (
    "## A title long enough to keep  <!-- block_0001, page 1 -->\n\n"
    "## Results  <!-- block_0002, page 2 -->\n\n"
    "We found that X causes Y (7). More text here.  <!-- block_0003, page 2 -->\n"
)


# --- the format exists and is written on request only --------------------


def test_viewer_is_a_published_format_written_on_request(tmp_path):
    assert "viewer" in FORMATS
    paths = write_reports(_results(), None, tmp_path, png=False, formats=("viewer",))
    assert {p.name for p in paths} == {"report.md", "report_viewer.html"}
    assert not (tmp_path / "report_editor.html").exists()
    assert not (tmp_path / "report_terminal.html").exists()


def test_the_viewer_is_not_written_unless_asked_for(tmp_path):
    write_reports(_results(), None, tmp_path, png=False, formats=("md", "editor"))
    assert not (tmp_path / "report_viewer.html").exists()


def test_png_never_screenshots_the_viewer(tmp_path, monkeypatch):
    """`--png` pulls in the looks it screenshots. The viewer is an interactive
    page — a sticky header and a scrolling panel — so a static shot of it is
    not a record of anything, and asking for the viewer must not produce one."""
    import papertrace.render as render_mod

    shot = []
    monkeypatch.setattr(render_mod, "html_to_png",
                        lambda src, dst: shot.append(src.name) or False)
    write_reports(_results(), None, tmp_path, png=True, formats=("viewer",))
    assert (tmp_path / "report_viewer.html").exists()
    assert shot == ["report_editor.html", "report_terminal.html"]


# --- what the page carries -------------------------------------------------


def test_the_page_is_a_self_contained_document(tmp_path):
    write_reports(_results(), None, tmp_path, png=False, formats=("viewer",))
    html = (tmp_path / "report_viewer.html").read_text()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<title>" in html
    # no network at all: a report on an unpublished manuscript must not phone
    # a font or script host the moment it is opened
    assert not re.search(r'<(link|script)[^>]+(href|src)="https?://', html), (
        "the viewer loads something from the network"
    )
    # fonts ride with the page, like the other HTML looks
    assert (tmp_path / "assets").is_dir()


def test_embedded_results_are_exactly_the_results_json_payload(tmp_path):
    """The viewer reads the same wire format every other consumer does — not a
    second serialisation that can drift from `results.json`."""
    results = _results()
    write_reports(results, None, tmp_path, png=False, formats=("viewer",))
    results.to_json(tmp_path / "results.json")

    embedded = _embedded((tmp_path / "report_viewer.html").read_text())
    assert embedded["results"] == json.loads((tmp_path / "results.json").read_text())


def test_the_manuscript_text_travels_when_given_and_is_null_when_not(tmp_path):
    """Absent is `null`, never an empty string: the page builds a skeleton from
    the audited sentences and says so, and the two must not look alike."""
    write_reports(_results(), None, tmp_path, png=False, formats=("viewer",),
                  annotated=ANNOTATED)
    assert _embedded((tmp_path / "report_viewer.html").read_text())["annotated"] == ANNOTATED

    other = tmp_path / "bare"
    write_reports(_results(), None, other, png=False, formats=("viewer",))
    assert _embedded((other / "report_viewer.html").read_text())["annotated"] is None


def test_scout_and_manifest_travel_when_given_and_are_null_when_not(tmp_path):
    scout = ScoutResults(paper_title="A paper", query="x AND y",
                         newer=[ScoutHit(title="Newer", year=2027, doi="10.1/n")])
    write_reports(_results(), _manifest(), tmp_path, png=False, formats=("viewer",), scout=scout)
    embedded = _embedded((tmp_path / "report_viewer.html").read_text())

    scout.to_json(tmp_path / "scout.json")
    assert embedded["scout"] == json.loads((tmp_path / "scout.json").read_text())

    entries = embedded["manifest"]["entries"]
    assert [e["num"] for e in entries] == ["7", "8"]
    assert entries[1]["status"] == "paywalled"
    assert entries[1]["reason"].startswith("DOI resolved")
    assert entries[0]["slug"] == "a-2020"
    assert embedded["manifest"]["numbering_verified"] is True

    other = tmp_path / "bare"
    write_reports(_results(), None, other, png=False, formats=("viewer",))
    bare = _embedded((other / "report_viewer.html").read_text())
    assert bare["scout"] is None
    assert bare["manifest"] is None


def test_hostile_text_cannot_break_out_of_the_data_block(tmp_path):
    """Cited source text reaches the page inside a `<script>` element, where
    HTML entities do not apply: a literal `</script>` in a note ends the
    element and whatever follows runs as script. Every `<`, `>` and `&` is
    emitted as a JSON escape instead, and the text still round-trips."""
    hostile = '</script><script>alert("xss")</script><!-- & -->'
    results = _results()
    results.claims[0].note = hostile
    results.claims[0].judgements[0].note = hostile
    write_reports(results, None, tmp_path, png=False, formats=("viewer",),
                  annotated=f"Body {hostile}  <!-- block_0001, page 1 -->\n")
    html = (tmp_path / "report_viewer.html").read_text()

    block = DATA_BLOCK.search(html).group(1)
    assert "<" not in block and ">" not in block and "&" not in block
    assert html.count("<script") == html.count("</script>")
    embedded = _embedded(html)
    assert embedded["results"]["claims"][0]["note"] == hostile
    assert hostile in embedded["annotated"]


# --- every disclosure decided in Python reaches the page ---------------------


def test_run_claim_and_judgement_disclosures_are_embedded_for_the_page(tmp_path):
    """Decided once in Python, rendered by the page — never re-derived in JS,
    where a second copy of the rules would drift from the other three looks."""
    results = _results(truncated={"manuscript": {"chars": 90000, "limit": 60000}})
    manifest = _manifest()
    write_reports(results, manifest, tmp_path, png=False, formats=("viewer",))
    embedded = _embedded((tmp_path / "report_viewer.html").read_text())["disclosures"]

    fired = run_disclosures(results, manifest)
    assert [d["key"] for d in embedded["run"]] == [d.key for d in fired]
    for got, want in zip(embedded["run"], fired, strict=True):
        assert got == {"key": want.key, "level": want.level, "token": want.token,
                       "text": want.text, "short": want.short, "rows": list(want.rows)}

    for c in results.claims:
        want = claim_disclosures(c, manifest=manifest)
        assert [d["key"] for d in embedded["claims"][str(c.id)]] == [d.key for d in want]
        assert [d["token"] for d in embedded["claims"][str(c.id)]] == [d.token for d in want]
        per_judgement = embedded["judgements"][str(c.id)]
        assert len(per_judgement) == len(c.judgements)
        for got, j in zip(per_judgement, c.judgements, strict=True):
            assert [d["token"] for d in got] == [d.token for d in judgement_disclosures(j)]


# --- the CLI stage hands over the ingested manuscript ------------------------


def _case(tmp_path: Path, annotated: str | None) -> Path:
    case = tmp_path / "case"
    (case / "out").mkdir(parents=True)
    _results().to_json(case / "out" / "results.json")
    if annotated is not None:
        (case / "ingest" / "manuscript").mkdir(parents=True)
        (case / "ingest" / "manuscript" / "annotated.md").write_text(annotated)
    return case


def test_the_report_stage_embeds_the_ingested_manuscript_when_it_exists(tmp_path):
    case = _case(tmp_path, ANNOTATED)
    cli._report_pipeline(case=case, formats=["viewer"])
    html = (case / "out" / "report_viewer.html").read_text()
    assert _embedded(html)["annotated"] == ANNOTATED


def test_the_report_stage_says_nothing_it_does_not_have(tmp_path):
    """No `annotated.md` in the case folder means the page shows audited
    sentences only — and must learn that from a `null`, not from an empty file
    somebody invented for it."""
    case = _case(tmp_path, None)
    cli._report_pipeline(case=case, formats=["viewer"])
    html = (case / "out" / "report_viewer.html").read_text()
    assert _embedded(html)["annotated"] is None


def test_a_mistyped_viewer_flag_is_still_refused(tmp_path):
    import typer

    case = _case(tmp_path, None)
    with pytest.raises(typer.Exit) as exc:
        cli._report_pipeline(case=case, formats=["viewr"])
    assert exc.value.exit_code == 2


# --- the documentation names it, and its pictures exist ----------------------


def test_every_surface_that_offers_the_viewer_is_documented():
    """The README is a correctness surface: each way in has to be written down,
    and each screenshot it embeds has to be a file in the repo — a raw URL to a
    missing PNG renders as nothing, silently.

    A repo invariant, not an artefact one: the sdist ships neither `docs/` (3.8
    MB, and every README image is an absolute raw URL precisely so it need not)
    nor `.claude/`, so from inside an extracted archive this can only fail.
    """
    root = Path(__file__).resolve().parent.parent
    if not (root / "docs").is_dir() or not (root / ".claude").is_dir():
        pytest.skip("repo-only directory")
    readme = (root / "README.md").read_text()
    assert "-f viewer" in readme
    assert "report_viewer.html" in readme
    for shot in re.findall(r"docs/(viewer_[\w-]+\.png)", readme):
        assert (root / "docs" / shot).exists(), f"README embeds docs/{shot}, which is missing"
    assert re.findall(r"docs/viewer_[\w-]+\.png", readme), "the viewer section has no picture"

    skill = (root / ".claude" / "skills" / "review" / "SKILL.md").read_text()
    assert "-f viewer" in skill and "report_viewer.html" in skill

    demo = (root / "examples" / "demo" / "README.md").read_text()
    assert "-f viewer" in demo
