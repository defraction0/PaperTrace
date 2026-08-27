"""The test that generalises all five defects.

Findings 1 and 10 are both instances of one shape: a denominator drifting away
from the population it claims to be over. `retrieval` said |M| and computed
`len(pairs)`; `unchecked_rate_matched` said nothing at all. Rather than pinning
each denominator individually, walk the whole record and check every rate
against the independently computed `populations` block. That catches the next
one too.
"""

import json
from pathlib import Path

import pytest

from evals import scoring

_ROOT = Path(__file__).resolve().parents[2]


def _rates(node, path="record"):
    """Yield (json path, rate dict) for every rate anywhere in the record."""
    if isinstance(node, dict):
        if {"k", "n", "value"} <= node.keys():
            yield path, node
            return
        for key, value in node.items():
            yield from _rates(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _rates(value, f"{path}[{i}]")


@pytest.fixture()
def demo_record():
    from papertrace.models import RunResults

    gold = json.loads((_ROOT / "evals" / "gold" / "demo_v1.gold.json").read_text())
    results = RunResults.from_json(_ROOT / "evals" / "gold" / "demo_v1.observed.json")
    return scoring.score(gold, results)


@pytest.fixture()
def mini_record(gold_mini, results_mini):
    return scoring.score(gold_mini, results_mini)


@pytest.fixture(params=["mini", "demo"])
def record(request, mini_record, demo_record):
    return mini_record if request.param == "mini" else demo_record


def test_the_record_carries_rates_at_all(record):
    """Guard: a walker that finds nothing would pass every test below."""
    assert len(list(_rates(record))) > 10


def test_every_rate_declares_a_population(record):
    unnamed = [p for p, r in _rates(record) if not r.get("population")]
    assert not unnamed, f"rates with no declared population: {unnamed}"


def test_every_declared_population_exists_in_the_populations_block(record):
    known = set(record["populations"])
    missing = sorted({r["population"] for _, r in _rates(record)} - known)
    assert not missing, f"populations named by a rate but never declared: {missing}"


def test_every_rate_in_the_record_matches_its_declared_population(record):
    """The generalisation. `retrieval_outcome_accuracy` claimed M and counted
    every matched pair including the ones nobody could label."""
    wrong = [
        (path, r["population"], r["n"], record["populations"][r["population"]]["n"])
        for path, r in _rates(record)
        if r["n"] != record["populations"][r["population"]]["n"]
    ]
    assert not wrong, f"denominator disagrees with its population: {wrong}"


def test_a_null_gold_case_changes_no_scored_denominator(gold_mini, results_mini):
    """Finding 10, stated as a property rather than a single rate."""
    without = scoring.score(
        {**gold_mini, "cases": [c for c in gold_mini["cases"]
                                if c.get("gold_verdict") is not None]},
        results_mini,
    )
    with_null = scoring.score(gold_mini, results_mini)
    assert with_null["metrics"]["retrieval"] == without["metrics"]["retrieval"]
    assert with_null["metrics"]["judgment_accuracy"] == \
        without["metrics"]["judgment_accuracy"]
    assert with_null["populations"]["M"]["n"] == without["populations"]["M"]["n"]


def test_every_population_definition_is_a_sentence(record):
    for name, pop in record["populations"].items():
        assert pop["definition"].strip(), name
        assert isinstance(pop["n"], int)
