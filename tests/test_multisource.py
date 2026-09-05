"""Co-cited sources were all offered as support, so all of them get checked.

Batch mode used to judge a multi-reference claim against `avail[0]` and record
the rest as never opened. That was honest but thin: a claim citing [3] and [5]
was backed by evidence from one of them, and the reader could not tell whether
the other agreed. Now every *available* cited source is judged, each carries its
own note and evidence crop, and the claim's headline verdict summarises them.

The verdict set gained `not_addressed` for this. A source cited for a claim but
silent on it is not `contradicted` (the source does not say otherwise) and not
`partial` (there is no true kernel) — forcing it into either would invent a
finding. An inapt citation is a real result and now has a name.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import (  # noqa: E402
    JUDGMENT_VERDICTS,
    VERDICTS,
    ClaimResult,
    RunResults,
    SourceJudgement,
)


def _j(slug: str, ref: str, verdict: str, **kw) -> SourceJudgement:
    return SourceJudgement(source_slug=slug, ref=ref, verdict=verdict, **kw)


# --- the vocabulary ---------------------------------------------------------


def test_not_addressed_is_a_judgement_verdict_not_a_pipeline_state():
    """The model may answer it — it read the source and it was silent. That is
    a finding about the citation, not a failure of the pipeline."""
    assert "not_addressed" in JUDGMENT_VERDICTS
    assert "not_addressed" in VERDICTS
    # the pipeline states keep their meaning and stay last
    assert VERDICTS[-2:] == ("not_retrieved", "unchecked")


# --- the headline rule -----------------------------------------------------


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (["supported"], "supported"),
        (["supported", "supported"], "supported"),
        (["supported", "partial"], "partial"),
        (["supported", "contradicted"], "contradicted"),
        (["partial", "contradicted"], "contradicted"),
        # a source that does not speak to the claim cannot lift or lower the
        # headline while another source does speak to it
        (["supported", "not_addressed"], "supported"),
        (["contradicted", "not_addressed"], "contradicted"),
        (["partial", "not_addressed"], "partial"),
        # ... but if NO cited source addressed the claim, that IS the headline
        (["not_addressed", "not_addressed"], "not_addressed"),
        # an unchecked source never becomes the answer while a real one exists
        (["supported", "unchecked"], "supported"),
        (["unchecked", "unchecked"], "unchecked"),
        # `not_addressed` asserts every available source was READ and was
        # silent. One source whose check failed makes that assertion false: the
        # tool does not know whether that source addressed the claim, and must
        # not turn a run failure into a finding about the paper.
        (["not_addressed", "unchecked"], "unchecked"),
        (["unchecked", "not_addressed"], "unchecked"),
        (["not_addressed", "not_addressed", "unchecked"], "unchecked"),
        # one source, each verdict in turn
        (["supported"], "supported"),
        (["partial"], "partial"),
        (["contradicted"], "contradicted"),
        (["not_addressed"], "not_addressed"),
        (["unchecked"], "unchecked"),
    ],
)
def test_the_headline_is_the_most_adverse_verdict_any_cited_source_gave(verdicts, expected):
    """One cited source contradicting the claim is a finding, and averaging it
    away with two that support it would bury exactly what a reviewer needs."""
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=[str(i) for i in range(len(verdicts))],
        judgements=[_j(f"s{i}", str(i), v) for i, v in enumerate(verdicts)],
    )
    assert claim.headline_verdict() == expected


def test_the_headline_carries_the_deciding_sources_anchor():
    """The claim-level page and slug must point at the source that decided the
    verdict, or the evidence crop shown beside it belongs to a different paper.
    """
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["3", "5"],
        judgements=[
            _j("agrees-2020", "3", "supported", source_page=4, source_block="block_0004"),
            _j("disputes-2021", "5", "contradicted", source_page=9, source_block="block_0009"),
        ],
    )
    claim.apply_headline()
    assert (claim.verdict, claim.source_slug, claim.source_page) == (
        "contradicted", "disputes-2021", 9,
    )


def test_a_failed_check_is_never_laundered_into_not_addressed():
    """One source read and silent, one source whose check failed. `not_addressed`
    would assert that every available source was read — a conclusion the run
    cannot support, in the field a reader looks at first."""
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["1", "2"],
        judgements=[
            _j("silent-2020", "1", "not_addressed", note="About mice."),
            _j("failed-2021", "2", "unchecked", note="check failed (RuntimeError) — retry"),
        ],
    )
    assert claim.headline_verdict() == "unchecked"
    d = claim.deciding_judgement()
    assert d is not None and d.source_slug == "failed-2021"
    claim.apply_headline()
    assert claim.verdict == "unchecked"
    # the silent source is still its own visible row, with its own note
    s = claim.judgement_summary()
    assert (s["not_addressed"], s["unchecked"], s["total"]) == (1, 1, 2)


def test_a_read_source_stays_the_headline_and_the_failed_one_stays_visible():
    """`supported` + `unchecked`: a source WAS read and did support the claim,
    so the headline stands on evidence. What must not vanish is the failed
    source — its own row says the run is incomplete."""
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["1", "2"],
        judgements=[
            _j("agrees-2020", "1", "supported", source_page=3, note="ICC 0.94."),
            _j("failed-2021", "2", "unchecked", note="check failed — retry"),
        ],
    )
    claim.apply_headline()
    assert (claim.verdict, claim.source_slug) == ("supported", "agrees-2020")
    assert [(j.source_slug, j.verdict) for j in claim.judgements] == [
        ("agrees-2020", "supported"), ("failed-2021", "unchecked"),
    ]
    assert claim.judgement_summary()["unchecked"] == 1


@pytest.mark.parametrize(
    "verdicts",
    [
        ["supported"], ["partial"], ["contradicted"], ["not_addressed"], ["unchecked"],
        ["supported", "contradicted"], ["supported", "not_addressed"],
        ["supported", "unchecked"], ["not_addressed", "not_addressed"],
        ["not_addressed", "unchecked"], ["unchecked", "unchecked"],
        ["partial", "not_addressed", "unchecked"],
    ],
)
def test_the_deciding_judgement_is_the_row_the_headline_came_from(verdicts):
    """A headline pointing at a row that says something else is how a crop from
    the wrong paper ends up beside a verdict."""
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=[str(i) for i in range(len(verdicts))],
        judgements=[_j(f"s{i}", str(i), v) for i, v in enumerate(verdicts)],
    )
    d = claim.deciding_judgement()
    assert d is not None, "a real verdict with no row behind it"
    assert d.verdict == claim.headline_verdict()


def test_a_claim_with_refs_but_no_judgements_keeps_its_claim_level_verdict():
    """The legacy path: refs that could not be obtained never produce a
    judgement, and the claim-level verdict set by check.py is the whole
    answer."""
    claim = ClaimResult(id=1, claim="x", location="Intro", refs=["1", "2"],
                        verdict="not_retrieved", note="cited source not available (paywalled)")
    assert claim.headline_verdict() == "not_retrieved"
    assert claim.deciding_judgement() is None
    claim.apply_headline()  # no judgements: must not overwrite anything
    assert (claim.verdict, claim.note) == (
        "not_retrieved", "cited source not available (paywalled)",
    )


# --- the summary a reader gets ---------------------------------------------


def test_the_summary_counts_every_source_and_never_hides_a_dissenter():
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["1", "2", "3", "4"],
        judgements=[
            _j("a", "1", "supported"), _j("b", "2", "supported"),
            _j("c", "3", "partial"), _j("d", "4", "contradicted"),
        ],
    )
    s = claim.judgement_summary()
    assert (s["supported"], s["partial"], s["contradicted"]) == (2, 1, 1)
    assert s["total"] == 4


def test_a_single_source_claim_still_reports_one_judgement():
    """The common case must not grow a confusing '1 of 1 sources' apparatus,
    but the data shape stays uniform so the templates have one path."""
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["7"],
        judgements=[_j("only-2020", "7", "supported")],
    )
    assert claim.judgement_summary()["total"] == 1
    assert claim.is_multi_source() is False


# --- the wire format ------------------------------------------------------


def test_judgements_round_trip_and_a_legacy_file_still_loads(tmp_path):
    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["3", "5"],
        judgements=[
            _j("a-2020", "3", "supported", source_page=2, anchor_phrases=["p < 0.001"]),
            _j("b-2021", "5", "not_addressed", note="silent on the cohort size"),
        ],
    )
    claim.apply_headline()
    p = tmp_path / "results.json"
    RunResults(manuscript="m.pdf", claims=[claim]).to_json(p)

    back = RunResults.from_json(p).claims[0]
    assert [j.source_slug for j in back.judgements] == ["a-2020", "b-2021"]
    assert isinstance(back.judgements[0], SourceJudgement)
    assert back.judgements[0].anchor_phrases == ["p < 0.001"]
    assert back.judgements[1].verdict == "not_addressed"

    # a results.json written before judgements existed must still load
    legacy = json.loads(p.read_text())
    for c in legacy["claims"]:
        c.pop("judgements")
    p.write_text(json.dumps(legacy))
    old = RunResults.from_json(p).claims[0]
    assert old.judgements == []
    assert old.verdict == "supported"  # the headline field was always there


def test_an_unchecked_headline_round_trips_and_validates_against_the_schema(tmp_path):
    """The published contract has to admit the value the headline can now take
    for this shape of claim, and say what it means."""
    import jsonschema

    claim = ClaimResult(
        id=1, claim="x", location="Methods", refs=["3", "5"],
        judgements=[
            _j("silent-2020", "3", "not_addressed", note="About mice."),
            _j("failed-2021", "5", "unchecked", note="check failed — retry"),
        ],
    )
    claim.apply_headline()
    path = tmp_path / "results.json"
    RunResults(manuscript="m.pdf", claims=[claim]).to_json(path)

    back = RunResults.from_json(path).claims[0]
    assert back.verdict == "unchecked"
    assert [j.verdict for j in back.judgements] == ["not_addressed", "unchecked"]

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "results.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))


def test_a_hand_built_legacy_results_json_loads_and_still_renders(tmp_path):
    """No `judgements` key anywhere — a results.json from before multi-source
    checking. It must load, keep its claim-level verdict, and render."""
    from papertrace.report import write_reports

    path = tmp_path / "results.json"
    path.write_text(json.dumps({
        "manuscript": "old.pdf",
        "checker": "Claude",
        "claims": [{
            "id": 1, "claim": "Uptake was 42%.", "location": "Results",
            "refs": ["7"], "verdict": "partial", "note": "The source says 38%.",
            "source_slug": "smith-2019", "source_page": 4,
            "source_block": "block_0004", "anchor_phrases": ["38%"],
            "evidence_image": None, "anchor_located": True,
        }],
    }))

    results = RunResults.from_json(path)
    claim = results.claims[0]
    assert claim.judgements == []
    assert claim.verdict == "partial"
    assert claim.headline_verdict() == "partial"

    out = tmp_path / "out"
    out.mkdir()
    write_reports(results, None, out, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        text = (out / name).read_text()
        assert "Uptake was 42%." in text, f"{name} drops the legacy claim"
        assert "The source says 38%." in text, f"{name} drops the legacy note"


# --- the fan-out ----------------------------------------------------------


def _ingested(dirpath, slug: str, pages: int = 2) -> None:
    """An ingested source: the text AND the map that proves where its blocks are.

    Both, always — a source map is no longer optional. `check` validates every
    substantive verdict's page and block against it, so a source without one
    can produce no verdict at all.
    """
    from papertrace.models import Block, SourceMap

    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "annotated.md").write_text(
        f"<!-- block_0001, page 1 -->\nText of {slug}.\n"
        f"<!-- block_0002, page 2 -->\nMore of {slug}.\n"
    )
    SourceMap(
        doc=f"{slug}.pdf", pages=pages,
        blocks=[
            Block("block_0001", "text", 1, (0.0, 0.0, 100.0, 20.0), [], f"Text of {slug}."),
            Block("block_0002", "text", 2, (0.0, 0.0, 100.0, 20.0), [], f"More of {slug}."),
        ],
    ).to_json(dirpath / "source_map.json")


def _case(tmp_path: Path, slugs: list[str]):
    """A case folder with a fully ingested source per slug."""
    from papertrace.models import RefEntry, RefManifest

    for slug in slugs:
        _ingested(tmp_path / "ingest" / slug, slug)
    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[
            RefEntry(num=str(i + 1), raw=f"R{i+1}", status="retrieved", slug=s,
                     pdf_path=f"{s}.pdf")
            for i, s in enumerate(slugs)
        ],
    )
    return manifest


def test_every_available_cited_source_is_judged_in_its_own_call(tmp_path, monkeypatch):
    """Three co-cited, retrieved sources means three model calls and three
    judgements — not one call against `avail[0]` with the rest written off."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims

    manifest = _case(tmp_path, ["a-2020", "b-2021", "c-2022"])
    claim = ClaimResult(id=1, claim="the cohort was imaged twice", location="Methods",
                        refs=["1", "2", "3"])

    seen: list[str] = []
    verdict_for = {"a-2020": "supported", "b-2021": "not_addressed", "c-2022": "contradicted"}

    def fake_ask(prompt, model=None):
        slug = next(s for s in verdict_for if f"SOURCE ({s})" in prompt)
        seen.append(slug)
        return json.dumps([{
            "id": 1, "verdict": verdict_for[slug], "note": f"per {slug}",
            "source_page": 1, "source_block": "block_0001",
            "anchor_phrases": [f"Text of {slug}"],
        }])

    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_claims([claim], manifest, tmp_path)

    assert sorted(seen) == ["a-2020", "b-2021", "c-2022"], "one call per source"
    assert {j.source_slug: j.verdict for j in claim.judgements} == verdict_for
    assert {j.ref for j in claim.judgements} == {"1", "2", "3"}
    # each judgement keeps its own note and anchor, not the headline's
    assert [j.note for j in claim.judgements if j.source_slug == "b-2021"] == ["per b-2021"]
    # the headline is the adverse one, and its anchor points at that source
    assert claim.verdict == "contradicted"
    assert claim.source_slug == "c-2022"
    # nothing was left unopened: every cited source was available
    assert claim.unjudged_refs == []


def test_unjudged_refs_now_means_could_not_be_obtained(tmp_path, monkeypatch):
    """The label used to collect co-citations that were available but not
    chosen. Those are judged now, so an entry here means exactly one thing:
    the source could not be retrieved."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _case(tmp_path, ["a-2020"])
    manifest.entries.append(
        RefEntry(num="2", raw="R2", status="paywalled", slug="locked-2019")
    )
    claim = ClaimResult(id=1, claim="c", location="Intro", refs=["1", "2"])

    monkeypatch.setattr(check_mod, "_ask", lambda p, model=None: json.dumps([{
        "id": 1, "verdict": "supported", "note": "n", "source_page": 1,
        "source_block": "block_0001", "anchor_phrases": ["Text of a-2020"],
    }]))
    check_claims([claim], manifest, tmp_path)

    assert [j.source_slug for j in claim.judgements] == ["a-2020"]
    assert claim.unjudged_refs == ["2"], "the paywalled co-citation, and only that"


def test_a_failed_call_unchecks_only_that_source(tmp_path, monkeypatch):
    """One source failing must not discard the verdict another source gave —
    the old single-source path had nothing to preserve."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims

    manifest = _case(tmp_path, ["good-2020", "bad-2021"])
    claim = ClaimResult(id=1, claim="c", location="Intro", refs=["1", "2"])

    def fake_ask(prompt, model=None):
        if "SOURCE (bad-2021)" in prompt:
            raise RuntimeError("claude -p failed: transient")
        return json.dumps([{
            "id": 1, "verdict": "supported", "note": "n", "source_page": 1,
            "source_block": "block_0001", "anchor_phrases": ["Text of good-2020"],
        }])

    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_claims([claim], manifest, tmp_path)

    by_slug = {j.source_slug: j.verdict for j in claim.judgements}
    assert by_slug["good-2020"] == "supported"
    assert by_slug["bad-2021"] == "unchecked"
    # the surviving real verdict is the headline, not the failure
    assert claim.verdict == "supported"


# --- what the reader actually sees ----------------------------------------


def _render(claim, tmp_path):
    from papertrace.report import write_reports

    write_reports(RunResults(manuscript="m.pdf", claims=[claim]), None, tmp_path, png=False)
    return {
        n: " ".join((tmp_path / n).read_text().split())
        for n in ("report.md", "report_editor.html", "report_terminal.html")
    }


def _four_source_claim():
    claim = ClaimResult(id=4, claim="PCCT improves reproducibility.", location="Discussion",
                        refs=["3", "5", "7", "9"])
    claim.judgements = [
        _j("a-2022", "3", "supported", note="ICC 0.94.", source_page=3,
           evidence_image="evidence/claim_04_a-2022_p3.png", anchor_located=True),
        _j("b-2023", "5", "supported", note="Same effect.", source_page=7,
           evidence_image="evidence/claim_04_b-2023_p7.png", anchor_located=True),
        _j("c-2021", "7", "partial", note="Calcified only.", source_page=5,
           evidence_image="evidence/claim_04_c-2021_p5.png", anchor_located=False),
        _j("d-2020", "9", "contradicted", note="No difference, p = 0.41.", source_page=9,
           evidence_image="evidence/claim_04_d-2020_p9.png", anchor_located=True),
    ]
    claim.apply_headline()
    return claim


def test_the_source_summary_reaches_all_three_formats(tmp_path):
    """The parity contract: a disclosure added to one format only is the drift
    this module exists to prevent, and the summary box is a new disclosure."""
    from papertrace.disclosures import MULTISOURCE_TOKEN

    out = _render(_four_source_claim(), tmp_path)
    for name, text in out.items():
        assert MULTISOURCE_TOKEN in text, f"{name} drops the source summary"
        assert "2 fully support it" in text, f"{name} drops the breakdown"
        assert "1 contradicts it" in text, f"{name} drops the dissenter"


def test_every_source_gets_its_own_crop_note_and_verdict(tmp_path):
    """Four sources, four crops, four notes — one paper must not stand in for
    the rest, and a reader comparing them needs each verdict beside its own
    evidence."""
    out = _render(_four_source_claim(), tmp_path)
    for name, text in out.items():
        for slug, note, img in (
            ("a-2022", "ICC 0.94.", "claim_04_a-2022_p3.png"),
            ("b-2023", "Same effect.", "claim_04_b-2023_p7.png"),
            ("c-2021", "Calcified only.", "claim_04_c-2021_p5.png"),
            ("d-2020", "No difference, p = 0.41.", "claim_04_d-2020_p9.png"),
        ):
            assert slug in text, f"{name} omits source {slug}"
            assert note in text, f"{name} omits {slug}'s own note"
            assert img in text, f"{name} omits {slug}'s crop"


def test_a_per_source_crop_with_no_box_is_never_captioned_as_matched(tmp_path):
    """`c-2021` matched no anchor phrase. Its caption must say so — and the
    three sources that DID match must still say they matched, in the same
    report. One claim now carries both states at once, which the single-source
    layout could never produce."""
    from papertrace.disclosures import ANCHOR_LOCATED_TOKEN, ANCHOR_NOT_LOCATED_TOKEN

    out = _render(_four_source_claim(), tmp_path)
    for name, text in out.items():
        assert ANCHOR_LOCATED_TOKEN in text, f"{name} loses the matched caption"
        assert ANCHOR_NOT_LOCATED_TOKEN in text, f"{name} loses the unboxed caption"


def test_a_claim_no_cited_source_addresses_says_so(tmp_path):
    """Two sources cited, both silent. That is a citation finding, and it must
    not render as a contradiction or as support."""
    claim = ClaimResult(id=1, claim="x", location="Intro", refs=["1", "2"],
                        judgements=[_j("a", "1", "not_addressed", note="About mice."),
                                    _j("b", "2", "not_addressed", note="Incidence only.")])
    claim.apply_headline()
    assert claim.verdict == "not_addressed"
    out = _render(claim, tmp_path)
    for name, text in out.items():
        assert "2 do not address it" in text, f"{name} drops the breakdown"
        # each format phrases it its own way; what must hold everywhere is that
        # the words appear at all, for the claim status and the run summary
        assert "does not address" in text.lower(), f"{name} loses the verdict"
    # the run summary must count it rather than dropping it from every bucket.
    # Only the new bucket is asserted verbatim; the markdown counts line is the
    # one with the number inline, so it is where that is checkable.
    assert "◌ **Does not address:** 1" in out["report.md"]


# --- the headline is one source's verdict, never the claim's ----------------


def test_a_multi_source_headline_names_how_many_sources_it_ranked():
    """`❌ CONTRADICTED` on a four-source claim is one source's verdict, but it
    reads as a statement about the claim. One dissenter of four is a finding
    worth surfacing and worth *qualifying* — a compound sentence may draw
    different parts from different references legitimately."""
    claim = _four_source_claim()
    assert claim.verdict == "contradicted"
    assert claim.headline_qualifier() == "most adverse of 4 cited sources"


def test_a_single_source_headline_carries_no_qualifier():
    """With one source the headline *is* a statement about the claim, and
    "most adverse of 1" would be noise that trains readers to skip the line."""
    claim = ClaimResult(id=1, claim="x", location="Intro", refs=["3"],
                        judgements=[_j("a-2022", "3", "supported", note="Yes.")])
    claim.apply_headline()
    assert claim.headline_qualifier() == ""


def test_a_claim_with_no_judgements_carries_no_qualifier():
    """`not_retrieved` ranked nothing. Claiming it was the most adverse of some
    number of sources would invent a comparison that never happened."""
    claim = ClaimResult(id=1, claim="x", location="Intro", refs=["3"])
    assert claim.verdict == "not_retrieved"
    assert claim.headline_qualifier() == ""


def test_the_headline_qualifier_reaches_all_three_formats(tmp_path):
    """The parity contract again: a status line qualified in the markdown but
    not the HTML would leave the overstatement exactly where it is most often
    read."""
    out = _render(_four_source_claim(), tmp_path)
    for name, text in out.items():
        assert "most adverse of 4 cited sources" in text, f"{name} drops the qualifier"


def test_a_single_source_claim_is_not_qualified_in_any_format(tmp_path):
    claim = ClaimResult(id=1, claim="x", location="Intro", refs=["3"],
                        judgements=[_j("a-2022", "3", "supported", note="Yes.")])
    claim.apply_headline()
    out = _render(claim, tmp_path)
    for name, text in out.items():
        assert "most adverse of" not in text, f"{name} qualifies a single-source claim"
