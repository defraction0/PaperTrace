"""Repeated-run agreement, including the kappa edge cases."""

import json

import pytest

from evals.agreement import (
    ABSENT,
    UNMATCHED,
    agreement,
    agreement_report,
    fleiss_kappa,
    require_one_provenance,
)


def test_identical_runs_agree_completely():
    a = agreement({"c1": ["supported", "supported"],
                   "c2": ["partial", "partial"]}, runs=2)
    assert a["modal_agreement"] == 1.0
    assert a["unanimous_rate"] == 1.0


def test_two_of_three_is_two_thirds():
    a = agreement({"c1": ["supported", "supported", "partial"]}, runs=3)
    assert a["modal_agreement"] == 2 / 3
    assert a["unanimous_rate"] == 0.0


def test_an_unaligned_run_lowers_agreement_rather_than_being_dropped():
    """Dropping it would flatter exactly the inconsistent cases."""
    kept = agreement({"c1": ["supported", UNMATCHED]}, runs=2)
    assert kept["modal_agreement"] == 0.5
    assert UNMATCHED in kept["per_case"]["c1"]["votes"]


def test_kappa_is_none_for_a_single_run():
    assert fleiss_kappa([[1, 0]])["value"] is None
    assert "fewer than 2" in fleiss_kappa([[1, 0]])["reason"]


def test_kappa_is_none_when_every_rating_is_one_category():
    """Not 1.0, not a ZeroDivisionError — undefined, and it says why."""
    k = fleiss_kappa([[3, 0], [3, 0]])
    assert k["value"] is None
    assert "one category" in k["reason"]
    assert k["p_bar"] == 1.0


def test_kappa_is_defined_with_spread_and_disagreement():
    k = fleiss_kappa([[3, 0], [0, 3]])
    assert k["value"] == 1.0  # perfect agreement, two categories used
    assert "uninformative below" in k["note"]


def test_empty_input_is_survivable():
    a = agreement({}, runs=0)
    assert a["modal_agreement"] is None and a["cases"] == 0


# --- k is passed, not inferred ---------------------------------------------


def test_ragged_vectors_raise_rather_than_guessing_k():
    """`k = len(next(iter(vectors.values())))` was safe only because the caller
    filtered to the intersection first. Removing that filter without passing
    the run count in would swap a disclosed upward bias for an undisclosed
    arithmetic error."""
    with pytest.raises(ValueError) as e:
        agreement({"c1": ["supported", "supported"], "c2": ["partial"]}, runs=2)
    assert "c2" in str(e.value)


def test_the_run_count_comes_from_the_caller_not_from_vector_zero():
    with pytest.raises(ValueError):
        agreement({"c1": ["supported", "supported"]}, runs=3)


# --- ABSENT is not UNMATCHED ------------------------------------------------


def test_absent_is_distinct_from_unmatched():
    """UNMATCHED: the run was asked and the aligner failed — a measured fact
    about the model. ABSENT: the harness never asked — an operator gap.
    Conflating them charges the operator's bookkeeping to the model."""
    assert ABSENT != UNMATCHED
    a = agreement({"c1": ["supported", ABSENT]}, runs=2)
    assert a["absent_cases"] == ["c1"]
    assert agreement({"c1": ["supported", UNMATCHED]}, runs=2)["absent_cases"] == []


def test_fleiss_is_none_with_a_reason_when_any_rating_is_absent():
    """Kappa assumes every item is rated by every rater."""
    a = agreement({"c1": ["supported", ABSENT], "c2": ["partial", "partial"]}, runs=2)
    assert a["fleiss"]["value"] is None
    assert "not rated in every run" in a["fleiss"]["reason"]


def test_fleiss_still_runs_when_nothing_is_absent():
    a = agreement({"c1": ["supported", "supported"],
                   "c2": ["partial", "partial"]}, runs=2)
    assert "reason" in a["fleiss"] or a["fleiss"]["value"] is not None


# --- two populations, and the omissions by name -----------------------------


def test_the_penalized_figure_is_a_lower_bound_and_complete_case_is_not_a_bound():
    """Renamed from intersection/union. Only the penalized figure is a bound:
    filling in an ABSENT vote can only raise the modal count. Dropping a case
    can move the mean either way, so complete-case is a population, not a
    ceiling — see `agreement_report`."""
    vectors = {"c1": ["supported", "supported"], "c2": ["partial", ABSENT]}
    r = agreement_report(vectors, runs=2, run_labels=["runA", "runB"],
                         set_ids=["demo-v1", "demo-v1"])
    assert r["penalized"]["bound"] == "lower"
    assert r["complete_case"]["bound"] is None
    assert r["complete_case"]["cases"] == 1
    assert r["penalized"]["cases"] == 2
    assert r["penalized"]["modal_agreement"] <= r["complete_case"]["modal_agreement"]


def test_per_run_omissions_are_named():
    """`if len(v) == n` dropped these silently — the caller defeating its own
    module's documented contract."""
    vectors = {"c1": ["supported", "supported"], "c2": ["partial", ABSENT]}
    r = agreement_report(vectors, runs=2, run_labels=["runA", "runB"],
                         set_ids=["demo-v1", "demo-v1"])
    assert r["omissions"] == {"runA": [], "runB": ["c2"]}


def test_two_different_set_ids_are_refused():
    """A category error, not a partial comparison."""
    with pytest.raises(ValueError) as e:
        require_one_provenance(["demo-v1", "other-v2"])
    assert "demo-v1" in str(e.value) and "other-v2" in str(e.value)
    assert require_one_provenance(["demo-v1", "demo-v1"]) == "demo-v1"

    with pytest.raises(ValueError):
        agreement_report({"c1": ["supported", "supported"]}, runs=2,
                         run_labels=["a", "b"], set_ids=["x", "y"])


def test_score_only_does_not_drop_cases_missing_from_one_run(tmp_path):
    """The bias the module's own docstring names, reintroduced by its caller."""
    from evals.runners import score_only

    def _run(name, rows):
        d = tmp_path / name
        d.mkdir()
        (d / "eval.json").write_text(json.dumps(
            {"gold": {"set_id": "demo-v1"}, "per_case": rows}))
        return d

    a = _run("a", [{"case_id": "c1", "predicted": "supported"},
                   {"case_id": "c2", "predicted": "partial"}])
    b = _run("b", [{"case_id": "c1", "predicted": "supported"}])
    out = score_only.score_agreement([a, b], tmp_path / "out")
    result = json.loads((out / "agreement.json").read_text())

    assert result["penalized"]["cases"] == 2
    assert result["complete_case"]["cases"] == 1
    assert result["omissions"]["b"] == ["c2"]
    md = (out / "AGREEMENT.md").read_text()
    assert "c2" in md and "not a bound" in md and "lower bound" in md
