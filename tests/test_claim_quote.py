"""The judge used to read a ≤160-character paraphrase of the sentence it was
checking, never the sentence.

`EXTRACT_PROMPT` asked for the statement "tightly paraphrased, ≤160 chars", and
`CHECK_PROMPT` was handed `{id, claim, location}` — so population, conditions,
effect direction, uncertainty and every other qualification that decides whether
a citation supports a statement had to survive a compression the judge could not
undo. "Mortality fell by 12% in the subgroup over 65 (HR 0.88, 95% CI
0.79-0.98)" and "mortality fell by 12%" are different claims, and only one of
them is checkable.

The claim now carries the manuscript's own sentence alongside the paraphrase.
The paraphrase stays because it is what a report headline reads well; the quote
is what gets judged.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import check as check_mod  # noqa: E402
from papertrace.models import ClaimResult, RunResults  # noqa: E402
from papertrace.report import write_reports  # noqa: E402

SCHEMA = json.loads((Path(__file__).parent.parent / "schemas" / "results.schema.json").read_text())


# --- the prompts ask for it and pass it on ---------------------------------


def test_the_extraction_prompt_asks_for_a_verbatim_quote():
    p = check_mod.EXTRACT_PROMPT
    assert "quote" in p
    assert "verbatim" in p.lower(), "a quote that is not required to be verbatim is a paraphrase"


def test_the_paraphrase_cap_is_no_longer_160_chars():
    """160 characters cannot hold a population, an interval and a direction at
    once. The cap stays — an unbounded 'paraphrase' is just the quote again —
    but at a width that fits a real clinical sentence."""
    assert "≤160 chars" not in check_mod.EXTRACT_PROMPT


def test_the_judge_is_shown_the_quote_not_only_the_paraphrase(tmp_path, monkeypatch):
    """The whole point. A quote stored but never sent leaves the judgement
    exactly as compressed as it was."""
    seen = {}

    def fake_ask(prompt, model=None):
        seen["prompt"] = prompt
        return json.dumps([{"id": 1, "verdict": "supported", "note": "Yes.",
                            "source_page": 1, "source_block": "block_0001",
                            "anchor_phrases": ["0.88"]}])

    _stub_source(tmp_path, monkeypatch, fake_ask)
    claim = ClaimResult(id=1, claim="Mortality fell 12%.", location="Results",
                        refs=["1"], quote="Mortality fell by 12% in the subgroup "
                                          "over 65 (HR 0.88, 95% CI 0.79-0.98).")
    check_mod.check_claims([claim], _manifest(), tmp_path, backend="pymupdf")

    assert "95% CI 0.79-0.98" in seen["prompt"], "the judge never saw the real sentence"


# --- parsing degrades honestly ---------------------------------------------


def test_a_response_with_no_quote_is_not_invented(tmp_path, monkeypatch):
    """An older or sloppier model answer omits the field. The claim keeps an
    empty quote — it must never be back-filled from the paraphrase, which would
    silently reinstate exactly the compression this change removes."""
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": [{"id": 1, "claim": "X causes Y.", "location": "Intro", "refs": ["1"]}],
         "uncited": []}))
    _annotated(tmp_path)
    cited, _ = check_mod.extract_claims(tmp_path)
    assert cited[0].quote == ""
    assert cited[0].claim == "X causes Y."


def test_a_quote_that_is_present_is_kept_verbatim(tmp_path, monkeypatch):
    quote = "Mortality fell by 12% in the subgroup over 65 (HR 0.88, 95% CI 0.79-0.98)."
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": [{"id": 1, "claim": "Mortality fell 12%.", "location": "Results",
                    "refs": ["1"], "quote": quote}], "uncited": []}))
    _annotated(tmp_path)
    cited, _ = check_mod.extract_claims(tmp_path)
    assert cited[0].quote == quote


# --- the wire format ------------------------------------------------------


def test_the_quote_round_trips_and_validates(tmp_path):
    import jsonschema

    quote = 'He said "12%" — <em>not</em> 21%.'
    r = RunResults(manuscript="m.pdf", claims=[
        ClaimResult(id=1, claim="X.", location="Intro", refs=["1"],
                    verdict="supported", quote=quote)])
    path = tmp_path / "results.json"
    r.to_json(path)
    jsonschema.validate(json.loads(path.read_text()), SCHEMA)
    assert RunResults.from_json(path).claims[0].quote == quote


def test_a_0_4_x_results_file_without_a_quote_still_loads(tmp_path):
    """`from_json` uses `.get` defaults so an older case folder keeps opening —
    the quote is absent, not empty-because-the-manuscript-was-silent."""
    legacy = {
        "manuscript": "m.pdf", "date": "2026-01-01",
        "claims": [{"id": 1, "claim": "X.", "location": "Intro", "refs": ["1"],
                    "verdict": "supported", "note": "n"}],
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(legacy))
    assert RunResults.from_json(path).claims[0].quote == ""


# --- it reaches the reader ------------------------------------------------


def test_the_quote_reaches_all_three_formats(tmp_path):
    from papertrace.report import write_reports

    quote = "Mortality fell by 12% in the subgroup over 65 (HR 0.88)."
    claim = ClaimResult(id=1, claim="Mortality fell 12%.", location="Results",
                        refs=["1"], verdict="supported", note="Stated.", quote=quote)
    write_reports(RunResults(manuscript="m.pdf", claims=[claim]), None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        text = " ".join((tmp_path / name).read_text().split())
        assert "Mortality fell by 12% in the subgroup over 65" in text, f"{name} drops the quote"


# --- helpers --------------------------------------------------------------


def _annotated(case: Path) -> None:
    d = case / "ingest" / "manuscript"
    d.mkdir(parents=True, exist_ok=True)
    (d / "annotated.md").write_text("<!-- block_0001, page 1 -->\nX causes Y [1].\n")


def _manifest():
    from papertrace.models import RefEntry, RefManifest

    return RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="Smith J (2020)", status="retrieved", slug="smith-2020",
                 pdf_path="/nonexistent/smith-2020.pdf")])


def _stub_source(case: Path, monkeypatch, fake_ask) -> None:
    """A cited source already ingested, so `check_claims` reaches the model."""
    d = case / "ingest" / "smith-2020"
    d.mkdir(parents=True, exist_ok=True)
    (d / "annotated.md").write_text("<!-- block_0001, page 1 -->\nHR 0.88 (0.79-0.98).\n")
    json.dump({"doc": "smith-2020.pdf", "pages": 1, "converter": "pymupdf",
               "blocks": [{"id": "block_0001", "page": 1, "type": "text",
                           "bbox": [0, 0, 10, 10], "text": "HR 0.88 (0.79-0.98)."}]},
              (d / "source_map.json").open("w"))
    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    monkeypatch.setattr(check_mod, "_stale_ingest", lambda *a, **k: False)


# --- the coverage matcher gets a real sentence to compare against ----------


def test_attribution_matches_on_the_quote_when_there_is_one():
    """`_attribute_label` compares the claim against each occurrence's own
    verbatim sentence. Comparing a paraphrase to a sentence was the weak side of
    that ratio; comparing the sentence to itself is not. This is why the 0.45
    floor and 0.10 margin are left exactly where they were — the evidence under
    them improved, so retuning them here would confound two changes."""
    occs = [
        {"id": "block_0001:10:3", "label": "3", "section": "Results",
         "sentence": "Mortality fell by 12% in the subgroup over 65 (HR 0.88, "
                     "95% CI 0.79-0.98) [3]."},
        {"id": "block_0002:40:3", "label": "3", "section": "Results",
         "sentence": "Readmission was unchanged across all strata [3]."},
    ]
    claim = ClaimResult(
        id=7, claim="Mortality improved.", location="Results", refs=["3"],
        quote="Mortality fell by 12% in the subgroup over 65 (HR 0.88, 95% CI 0.79-0.98).",
    )
    assigned, unattributed, refused = check_mod._attribute_label(occs, [claim])

    assert assigned == {"block_0001:10:3": 7}, "the quote should pick its own sentence"
    assert not unattributed and not refused


def test_attribution_still_falls_back_to_the_paraphrase():
    """An extraction that returned no quote must keep working exactly as it did
    — the audit of an older case folder does not get worse because a field it
    never had is now available."""
    occs = [
        {"id": "a:1:3", "label": "3", "section": "Results",
         "sentence": "Mortality fell by 12% in the over-65 subgroup [3]."},
        {"id": "b:2:3", "label": "3", "section": "Methods",
         "sentence": "Scans were reconstructed with a sharp kernel [3]."},
    ]
    claim = ClaimResult(id=7, claim="Mortality fell by 12% in the over-65 subgroup.",
                        location="Results", refs=["3"])
    assigned, _, refused = check_mod._attribute_label(occs, [claim])
    assert assigned == {"a:1:3": 7}
    assert not refused


# --- gate 4: the failure paths still degrade honestly ---------------------


def test_a_malformed_extraction_response_still_raises_rather_than_guessing(tmp_path, monkeypatch):
    """Adding a field must not turn a broken response into a partial success.
    Extraction has no honest half-answer — the manuscript was never read — so
    it raises and `check` reports the run as failed."""
    import pytest

    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: "not json at all")
    _annotated(tmp_path)
    with pytest.raises((ValueError, RuntimeError)):
        check_mod.extract_claims(tmp_path)


def test_a_quoted_claim_whose_source_was_never_retrieved_is_still_not_retrieved(tmp_path):
    """A quote is manuscript-side evidence and says nothing about the source.
    A claim with a perfect quote and no obtainable source must still be
    `not_retrieved`, with no model call — the rule this whole codebase is
    built on."""
    from papertrace.models import RefEntry, RefManifest

    calls = []
    manifest = RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="Smith J (2020)", status="paywalled", slug="smith-2020")])
    claim = ClaimResult(id=1, claim="Mortality fell 12%.", location="Results",
                        refs=["1"], quote="Mortality fell by 12% (HR 0.88).")

    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert claim.verdict == "not_retrieved"
    assert calls == [], "an unretrieved source must cost no model call"
    assert claim.quote == "Mortality fell by 12% (HR 0.88).", "the quote survives the gap"


def test_an_unchecked_claim_keeps_its_quote(tmp_path, monkeypatch):
    """The source was available and the call failed. The verdict is `unchecked`
    and the quote is still the manuscript's sentence — a failed check does not
    retract what the manuscript says."""
    def boom(prompt, model=None):
        raise RuntimeError("claude -p timed out")

    _stub_source(tmp_path, monkeypatch, boom)
    claim = ClaimResult(id=1, claim="Mortality fell 12%.", location="Results",
                        refs=["1"], quote="Mortality fell by 12% (HR 0.88).")

    check_mod.check_claims([claim], _manifest(), tmp_path, backend="pymupdf")

    assert claim.verdict == "unchecked"
    assert "timed out" in claim.note or "claude" in claim.note.lower()
    assert claim.quote == "Mortality fell by 12% (HR 0.88)."


# --- a claim judged without its own sentence says so ----------------------


def test_a_claim_judged_on_its_paraphrase_discloses_that():
    """The verbatim quote is what makes a verdict trustworthy, so a verdict
    reached without one is weaker evidence and the reader is told. Otherwise
    "no quote" is indistinguishable from "quote identical to the paraphrase",
    and the README's promise that the report shows what was judged is false."""
    from papertrace.disclosures import NO_QUOTE_TOKEN, claim_disclosures

    judged = ClaimResult(id=1, claim="Mortality fell.", location="Results",
                         refs=["3"], verdict="supported", source_page=2)
    keys = [d.key for d in claim_disclosures(judged)]
    assert "no_quote" in keys
    assert any(NO_QUOTE_TOKEN in d.text for d in claim_disclosures(judged))


def test_a_claim_with_a_quote_says_nothing_extra():
    from papertrace.disclosures import claim_disclosures

    quoted = ClaimResult(id=1, claim="Mortality fell.", location="Results", refs=["3"],
                         verdict="supported", source_page=2, quote="Mortality fell by 12%.")
    assert "no_quote" not in [d.key for d in claim_disclosures(quoted)]


def test_an_unretrieved_claim_is_not_nagged_about_a_missing_quote():
    """Nothing judged it, so the quote changed nothing. Firing here would put
    the notice on every gap in the register and train readers to ignore it."""
    from papertrace.disclosures import claim_disclosures

    gap = ClaimResult(id=1, claim="Mortality fell.", location="Results",
                      refs=["3"], verdict="not_retrieved")
    assert "no_quote" not in [d.key for d in claim_disclosures(gap)]


# --- how the SOURCES were read, disclosed where verdicts are read ---------


def test_a_flat_read_source_is_named_in_all_three_formats(tmp_path):
    """`RunResults.converter` is the *manuscript's* backend, and the terminal
    line was the only place that said anything about the sources. Now that a
    source can be read either way, a verdict resting on a linearized table has
    to say so in the report — a table's rows are exactly the evidence a
    subgroup claim turns on."""
    from papertrace.disclosures import SOURCE_FIDELITY_TOKEN, run_disclosures

    r = RunResults(
        manuscript="m.pdf", converter="docling 2.1.0",
        claims=[ClaimResult(id=1, claim="x", location="Intro", refs=["4"],
                            verdict="supported", source_slug="flat-2020", source_page=1)],
        source_converters={"flat-2020": "pymupdf", "rich-2021": "docling 2.1.0"},
    )
    fired = [d for d in run_disclosures(r, None) if d.key == "source_fidelity"]
    assert fired, "a flat-read source disclosed nothing"
    assert "flat-2020" in fired[0].text

    write_reports(r, None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        text = " ".join((tmp_path / name).read_text().split())
        assert SOURCE_FIDELITY_TOKEN in text, f"{name} drops the source-fidelity notice"


def test_sources_all_read_layout_aware_disclose_nothing():
    """No loss, nothing to warn about. A notice that fires on the good path is
    a notice readers learn to skip."""
    from papertrace.disclosures import run_disclosures

    r = RunResults(manuscript="m.pdf", converter="docling 2.1.0",
                   source_converters={"a-2020": "docling 2.1.0", "b-2021": "docling 2.1.0"})
    assert [d for d in run_disclosures(r, None) if d.key == "source_fidelity"] == []


def test_a_run_that_recorded_no_source_converters_claims_nothing():
    """A 0.4.x results.json has no such field. Absent is not "all flat" — the
    run simply never recorded it, and inventing either answer is the failure
    this codebase is built to avoid."""
    from papertrace.disclosures import run_disclosures

    r = RunResults(manuscript="m.pdf", converter="pymupdf")
    assert [d for d in run_disclosures(r, None) if d.key == "source_fidelity"] == []


def test_source_converters_round_trip(tmp_path):
    r = RunResults(manuscript="m.pdf", source_converters={"a-2020": "pymupdf"})
    p = tmp_path / "results.json"
    r.to_json(p)
    assert RunResults.from_json(p).source_converters == {"a-2020": "pymupdf"}
