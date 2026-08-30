"""The Markdown artefact. Its guardrail wording is the thing under test."""

import json
import re
from pathlib import Path

import pytest

from evals import scoring
from evals.eval_report import _pct, render

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def record(gold_mini, results_mini):
    return scoring.score(gold_mini, results_mini)


def test_pct_always_carries_k_over_n():
    assert _pct({"k": 1, "n": 2, "value": 0.5}) == "50% (1/2)"


def test_undefined_renders_as_a_dash_never_zero_percent():
    assert _pct({"k": 0, "n": 0, "value": None}) == "—"
    assert _pct(None) == "—"
    assert _pct({"k": 0, "n": 4, "value": 0.0}) == "0% (0/4)"


def test_disclaimer_precedes_the_first_number(record):
    md = render(record)
    disclaimer = md.index("not a benchmark")
    first_digit = re.search(r"\d", md).start()
    # the "5 hand-labelled cases" line is part of the disclaimer block itself
    assert disclaimer < md.index("## Verdicts")
    assert first_digit > md.index("#")


def test_harness_wording_never_claims_validation_or_benchmark_status():
    """Checked against the template, not the rendered page.

    A gold set may legitimately quote a claim containing "validated" — that is
    the *paper's* word, not the harness's. What must stay clean is the prose
    the harness itself puts around the numbers.
    """
    from evals.eval_report import TEMPLATES

    raw = (TEMPLATES / "eval.md.j2").read_text().lower()
    # flatten markdown quote prefixes and line wraps so phrases match across lines
    prose = " ".join(raw.replace("\n>", " ").split())
    for forbidden in ("validated", "state of the art", "proves", "outperforms"):
        assert forbidden not in prose, forbidden
    # the loaded phrases are permitted only inside their denial
    assert "do not estimate accuracy on real manuscripts" in prose
    assert "not a benchmark" in prose
    assert "do not establish clinical or research validity" in prose


def test_rendered_report_carries_the_denial(record):
    md = render(record).lower()
    assert "not a benchmark" in md
    assert "do not estimate" in md


def test_every_percentage_carries_its_denominator(record):
    md = render(record)
    bare = [
        m.group(0)
        for m in re.finditer(r"\d+%(?! \(\d+/\d+\))", md)
        if "fuzzy" not in md[max(0, m.start() - 120):m.start()]
    ]
    assert not bare, f"percentages without (k/n): {bare}"


def test_alignment_table_precedes_the_metrics(record):
    md = render(record)
    assert md.index("## Alignment") < md.index("## Verdicts")


def test_unmatched_registers_appear_in_both_directions(record):
    md = render(record)
    assert "Gold cases with no prediction" in md
    assert "Predictions with no gold case" in md


def test_out_of_scope_predictions_are_labelled_not_errors(record):
    assert "these are not errors" in render(record)


def test_every_gold_case_appears_in_the_per_case_table(record, gold_mini):
    md = render(record)
    for c in gold_mini["cases"]:
        assert c["case_id"] in md


def test_disagreements_section_shows_both_rationales(record):
    md = render(record)
    assert "## Disagreements" in md
    assert "Gold rationale" in md and "Model note" in md


def test_absent_pairs_are_called_out(gold_mini, results_mini):
    stripped = {**gold_mini, "cases": [
        {k: v for k, v in c.items() if k != "pair"} for c in gold_mini["cases"]
    ]}
    md = render(scoring.score(stripped, results_mini))
    assert "No paired cases in this set" in md


def test_unchecked_run_raises_a_banner(gold_mini, results_mini):
    results_mini.claims[0].verdict = "unchecked"
    md = render(scoring.score(gold_mini, results_mini))
    assert "partially failed" in md


def test_truncated_input_raises_a_banner(gold_mini, results_mini):
    rec = scoring.score(gold_mini, results_mini,
                        provenance={"truncated": {"manuscript": {"chars": 1, "limit": 0}}})
    assert "truncated before the model saw it" in render(rec)


def test_unidentified_model_raises_a_banner_and_is_not_guessed(gold_mini, results_mini):
    rec = scoring.score(gold_mini, results_mini,
                        provenance={"model_unidentified": True, "model_reported": None})
    md = render(rec)
    assert "could not be identified and was not guessed" in md
    assert "**unidentified**" in md


def test_markdown_and_json_agree(record):
    md = render(record)
    ja = record["metrics"]["judgment_accuracy"]
    assert f"{ja['k']}/{ja['n']}" in md


def test_demo_gold_scores_the_committed_demo_run():
    """End to end on the real artefact: gold JSON in, EVAL.md out, no model."""
    from papertrace.models import RunResults

    root = Path(__file__).resolve().parents[2]
    gold = json.loads((root / "evals" / "gold" / "demo_v1.gold.json").read_text())
    results = RunResults.from_json(root / "evals" / "gold" / "demo_v1.observed.json")
    rec = scoring.score(gold, results)
    m = rec["metrics"]

    # the population is asserted, not dropped: two rates with n = 4 drawn from
    # different sets are the neighbouring-bare-percentage hazard in miniature
    assert m["judgment_accuracy"] == {"k": 4, "n": 4, "value": 1.0, "population": "J"}
    assert m["retrieval"]["missed_gap_rate"] == {
        "k": 0, "n": 1, "value": 0.0, "population": "gold_gaps"}
    assert m["coverage"]["uncited_recall"]["value"] == 1.0
    assert m["coverage"]["claim_label_coverage"] == {
        "k": 4, "n": 4, "value": 1.0, "population": "tool_labels"}
    assert m["pages"]["source_page_accuracy"] == {
        "k": 4, "n": 4, "value": 1.0, "population": "J_answered_page"}
    # demo_v1 genuinely has no `partial` gold case and never predicts one, so
    # this exclusion is true here — unlike the mini fixture's, which was not
    assert m["macro_f1"]["classes_excluded"] == ["partial", "not_addressed"]
    assert [e["kind"] for e in m["macro_f1"]["exclusions"]] == ["absent_from_set", "absent_from_set"]
    # the committed report records no anchor phrases, so this must be undefined
    assert m["anchors"]["anchor_in_gold_passage"]["value"] is None
    assert rec["alignment"]["unmatched_gold"] == []
    assert render(rec).startswith("# Evaluation — demo-v1")


# --- what the new record blocks must actually put on the page ---------------


def test_the_populations_table_is_rendered(record):
    md = render(record)
    assert "## Populations" in md
    assert "matched pairs with a resolved gold verdict" in md
    for name in ("M", "J_gold", "J"):
        assert f"`{name}`" in md


def test_macro_f1_exclusions_are_rendered_with_their_derived_kind(gold_mini, results_mini):
    trimmed = {**gold_mini, "cases": [
        c for c in gold_mini["cases"] if c["case_id"] != "m-c03"
    ]}
    md = render(scoring.score(trimmed, results_mini))
    assert "absent_from_set" in md
    assert "no gold instance in this set" in md


def test_the_old_constant_exclusion_sentence_is_gone(record):
    assert "no gold and/or no predicted instances" not in render(record)


def test_excluded_cases_are_rendered_in_their_own_section(gold_mini, results_mini):
    drift = [{"label": "3", "slug": "c-2022", "expected": "paywalled",
              "observed": "retrieved", "kind": "changed"}]
    md = render(scoring.score(gold_mini, results_mini, refs_drift=drift))
    assert "## Excluded cases" in md
    assert "m-c04" in md and "refs_status_drift" in md
    assert "frozen as 'paywalled'" in md


def test_the_report_says_which_denominators_still_hold_the_excluded_cases(
    gold_mini, results_mini
):
    """Those percentages are printed beside the ones that shrank."""
    drift = [{"label": "3", "slug": "c-2022", "expected": "paywalled",
              "observed": "retrieved", "kind": "changed"}]
    md = render(scoring.score(gold_mini, results_mini, refs_drift=drift))
    assert "still counted in" in md.lower()
    assert "run-level" in md


def test_an_unverified_freeze_check_is_bannered_as_not_verified(
    gold_mini, results_mini
):
    """A check that did not complete must not read as a check that passed."""
    unverified = [{"label": "3", "slug": "c-2022", "expected": "paywalled",
                   "observed": None, "kind": "not_verified",
                   "why": "refs_manifest.json not found"}]
    md = render(scoring.score(gold_mini, results_mini, refs_drift=unverified))
    assert "could not be verified" in md
    assert "not verified" in md.lower()
    # and it must NOT claim cases were invalidated, because none were
    assert "affected cases are invalidated" not in md


def test_incomplete_pair_groups_are_named_in_the_report(gold_mini, results_mini):
    drift = [{"label": "1", "slug": "a-2020", "expected": "retrieved",
              "observed": "paywalled", "kind": "changed"}]
    rec = scoring.score(gold_mini, results_mini, refs_drift=drift)
    assert rec["metrics"]["pairs"]["n_pairs_incomplete"] == 1
    md = render(rec)
    assert "p1" in md
    assert "not scoreable as a pair" in md


def test_no_percentage_from_the_new_sections_is_bare(gold_mini, results_mini):
    drift = [{"label": "3", "slug": "c-2022", "expected": "paywalled",
              "observed": "retrieved", "kind": "changed"}]
    md = render(scoring.score(gold_mini, results_mini, refs_drift=drift))
    bare = [
        m.group(0)
        for m in re.finditer(r"\d+%(?! \(\d+/\d+\))", md)
        if "fuzzy" not in md[max(0, m.start() - 120):m.start()]
    ]
    assert not bare, f"percentages without (k/n): {bare}"
