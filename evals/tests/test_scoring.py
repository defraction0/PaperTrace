"""Orchestration: eligibility, populations, caveats and the record shape."""

from evals import scoring


def test_the_absent_class_caveat_is_not_emitted_when_the_class_is_present(
    gold_mini, results_mini
):
    """The mini fixture has a gold `partial` case that the run got wrong.

    The old caveat said `class(es) partial absent from this set` — a false
    sentence printed beside a macro F1 that had deleted the class.
    """
    rec = scoring.score(gold_mini, results_mini)
    assert not any("partial absent" in c for c in rec["caveats"])
    assert rec["metrics"]["macro_f1"]["classes_excluded"] == []


def test_macro_f1_on_the_mini_fixture_keeps_the_class_it_got_wrong(
    gold_mini, results_mini
):
    """0.833 was produced by dropping the class the run failed. 0.556 is the
    mean over supported (0.667), partial (0.0) and contradicted (1.0)."""
    rec = scoring.score(gold_mini, results_mini)
    assert round(rec["metrics"]["macro_f1"]["value"], 3) == 0.556
    assert rec["metrics"]["per_class"]["partial"]["f1"] == 0.0


def test_the_absence_caveat_still_fires_for_a_genuinely_absent_class(
    gold_mini, results_mini
):
    """Honesty runs both ways: a truly absent class must still be named."""
    trimmed = {**gold_mini, "cases": [
        c for c in gold_mini["cases"] if c["case_id"] != "m-c03"
    ]}
    rec = scoring.score(trimmed, results_mini)
    kinds = {e["class"]: e["kind"] for e in rec["metrics"]["macro_f1"]["exclusions"]}
    assert kinds == {"partial": "absent_from_set"}
    assert any("partial" in c and "no gold" in c for c in rec["caveats"])


def test_an_attrition_caveat_does_not_claim_the_class_is_absent(
    gold_mini, results_mini
):
    """A class eliminated by attrition is present in the set.

    Calling it absent is the same false sentence the constant
    `excluded_reason` told, relocated into the caveat list.
    """
    results_mini.claims[1].verdict = "unchecked"   # the only contradicted pair
    rec = scoring.score(gold_mini, results_mini)
    kinds = {e["class"]: e["kind"] for e in rec["metrics"]["macro_f1"]["exclusions"]}
    assert kinds["contradicted"] == "eliminated_by_attrition"

    caveats = " ".join(rec["caveats"])
    assert "contradicted absent from this set" not in caveats
    assert "none survived" in caveats or "did not reach" in caveats
