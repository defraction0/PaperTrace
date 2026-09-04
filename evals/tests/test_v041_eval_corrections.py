"""Six evaluation defects that let an ineligible or incomparable case count.

Grouped in one file because they share a theme: the harness was measuring a
population it had not established. A case excluded from scoring still competed
for predictions and still voted in agreement; two runs of different prompts
were averaged together; a duplicate prediction id let input order pick a
winner; and the two agreement figures were labelled as bounds when only one of
them is one.
"""

import copy
import json

import pytest

from evals import scoring
from evals.agreement import ABSENT, agreement_report, require_one_provenance
from evals.align import align

# --- 1. eligibility is decided before alignment, not after ------------------


def _gold_with_an_ineligible_rival(gold_mini: dict) -> dict:
    """An unresolved case that shadows an eligible one on the same label.

    Both cases sit on label [1] and read almost alike, so they compete for the
    same prediction. The unresolved one carries no gold verdict, so it can
    never be scored — but it used to consume the prediction anyway, and the
    eligible case was then reported as the tool's extraction failure.
    """
    gold = copy.deepcopy(gold_mini)
    eligible = next(c for c in gold["cases"] if c["case_id"] == "m-c01")
    rival = copy.deepcopy(eligible)
    rival.update({
        "case_id": "m-c00-unresolved",
        "claim_text": eligible["claim_text"] + " overall",
        "gold_verdict": None,
        "ambiguity": "labellers split on whether this is one claim or two",
    })
    rival.pop("pair", None)
    gold["cases"].insert(0, rival)
    return gold


def test_an_ineligible_case_cannot_consume_an_eligible_cases_prediction(
    gold_mini, results_mini
):
    gold = _gold_with_an_ineligible_rival(gold_mini)
    rec = scoring.score(gold, results_mini)

    rows = {r["case_id"]: r for r in rec["per_case"]}
    assert rows["m-c00-unresolved"]["eligible"] is False
    assert rows["m-c01"]["predicted"] is not None, (
        "an unscoreable case took the prediction the eligible case needed"
    )


def test_the_ineligible_case_still_gets_a_row_and_a_reason(gold_mini, results_mini):
    """Excluding it from alignment must not delete it from the record."""
    rec = scoring.score(_gold_with_an_ineligible_rival(gold_mini), results_mini)
    row = next(r for r in rec["per_case"] if r["case_id"] == "m-c00-unresolved")
    assert row["excluded_reason"] == "gold_verdict_unresolved"
    assert "labellers" in row["excluded_detail"]


# --- 2. duplicate prediction ids ---------------------------------------------


def test_duplicate_prediction_ids_are_refused_not_silently_collapsed(
    gold_mini, results_mini
):
    """`{c.id: c for c in results.claims}` kept whichever came last, so input
    order decided which of two same-id claims was graded. Alignment is
    documented as order-independent; this was the one place it was not."""
    doubled = copy.deepcopy(results_mini)
    clash = copy.deepcopy(doubled.claims[1])
    clash.id = doubled.claims[0].id
    doubled.claims.append(clash)

    with pytest.raises(ValueError, match="duplicate prediction id"):
        align(gold_mini, doubled)


def test_the_duplicate_error_names_the_offending_ids(gold_mini, results_mini):
    doubled = copy.deepcopy(results_mini)
    clash = copy.deepcopy(doubled.claims[1])
    clash.id = doubled.claims[0].id
    doubled.claims.append(clash)

    with pytest.raises(ValueError) as exc:
        align(gold_mini, doubled)
    assert str(doubled.claims[0].id) in str(exc.value)


# --- 3. agreement compares like with like -------------------------------------


def _prov(prompt="sha256:aaaa", converter="pymupdf"):
    return {"prompt_fingerprint": {"scheme": "sha256-content",
                                   "EXTRACT_PROMPT": prompt, "CHECK_PROMPT": prompt},
            "converter": converter}


def test_one_provenance_triple_accepts_matching_runs():
    assert require_one_provenance(["demo-v1", "demo-v1"], [_prov(), _prov()]) == "demo-v1"


def test_a_different_prompt_fingerprint_is_refused():
    """Two runs of different prompts describe different systems. Averaging
    their agreement produces a number about neither."""
    with pytest.raises(ValueError, match="prompt"):
        require_one_provenance(["demo-v1", "demo-v1"],
                               [_prov(), _prov(prompt="sha256:bbbb")])


def test_a_different_converter_is_refused():
    """The judge read different text, so a disagreement is not the model's."""
    with pytest.raises(ValueError, match="converter"):
        require_one_provenance(["demo-v1", "demo-v1"],
                               [_prov(), _prov(converter="docling 2.118.1")])


def test_a_different_set_id_is_still_refused():
    with pytest.raises(ValueError, match="gold sets"):
        require_one_provenance(["demo-v1", "other-v2"], [_prov(), _prov()])


# --- 4. the two agreement figures are named for what they are -----------------


def test_the_two_figures_are_complete_case_and_penalized_not_bounds():
    """`intersection` was labelled the upper bound. It is not one: dropping a
    case whose true agreement is high pulls the mean DOWN, so the complete-case
    figure can sit below the true value as easily as above it. Only the
    penalized figure is a genuine bound, and only downward."""
    vectors = {"c1": ["supported", "supported", "supported"],
               "c2": ["partial", "partial", ABSENT]}
    r = agreement_report(vectors, runs=3, run_labels=["a", "b", "c"],
                         set_ids=["s", "s", "s"], provenances=[_prov()] * 3)

    assert set(r) >= {"complete_case", "penalized"}
    assert "intersection" not in r and "union" not in r
    assert r["penalized"]["bound"] == "lower"
    assert r["complete_case"]["bound"] is None, (
        "the complete-case figure is not a bound in either direction"
    )
    assert "not a bound" in r["complete_case"]["bound_note"]


def test_three_runs_with_a_missing_case_keep_both_populations_visible():
    vectors = {"c1": ["supported", "supported", "supported"],
               "c2": ["partial", "partial", ABSENT]}
    r = agreement_report(vectors, runs=3, run_labels=["a", "b", "c"],
                         set_ids=["s", "s", "s"], provenances=[_prov()] * 3)

    assert r["complete_case"]["cases"] == 1
    assert r["penalized"]["cases"] == 2
    assert r["omissions"]["c"] == ["c2"]
    assert r["n_omitted"] == 1


# --- 5. not_addressed is a rendered column, not just a row --------------------


def test_the_confusion_matrix_renders_the_not_addressed_column(gold_mini, results_mini):
    """The matrix has always computed four classes. The template printed three,
    so a run that answered `not_addressed` when the gold said `supported` had
    the mistake counted and then hidden."""
    from evals.eval_report import render

    rec = scoring.score(gold_mini, results_mini)
    md = render(rec)
    header = next(line for line in md.splitlines() if line.startswith("| gold \\"))
    assert "not_addressed" in header, header


def test_a_not_addressed_prediction_against_not_addressed_gold_is_a_hit(
    gold_mini, results_mini
):
    """The diagonal cell for the fourth class must be reachable at all."""
    from evals import metrics

    gold = copy.deepcopy(gold_mini)
    case = next(c for c in gold["cases"] if c["case_id"] == "m-c01")
    case["gold_verdict"] = "not_addressed"
    preds = copy.deepcopy(results_mini)
    pred = preds.claims[0]
    pred.verdict = "not_addressed"

    m = metrics.confusion([(case, pred)])
    assert m["not_addressed"]["not_addressed"] == 1


# --- 6. an ineligible case does not vote in agreement -------------------------


def test_score_agreement_ignores_cases_that_were_never_scoreable(tmp_path):
    """An excluded case is in `per_case` by design — it must not therefore be
    counted as the model disagreeing with itself."""
    from evals.runners.score_only import score_agreement

    def _run(name: str, predicted: str) -> None:
        d = tmp_path / name
        d.mkdir()
        (d / "eval.json").write_text(json.dumps({
            "gold": {"set_id": "s"},
            "provenance": _prov(),
            "per_case": [
                {"case_id": "ok", "eligible": True, "predicted": "supported"},
                {"case_id": "dropped", "eligible": False, "predicted": predicted},
            ],
        }))

    _run("runA", "supported")
    _run("runB", "contradicted")

    out = score_agreement([tmp_path / "runA", tmp_path / "runB"], tmp_path / "out")
    result = json.loads((out / "agreement.json").read_text())
    assert result["penalized"]["cases"] == 1
    assert "dropped" not in result["penalized"]["per_case"]
