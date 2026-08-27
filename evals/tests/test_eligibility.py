"""Which cases may be scored, and if not, why — defined once.

Findings 1 and 10 are the same defect (a case that must not be counted is
counted), so they share one mechanism rather than two ad-hoc filters.
"""

from evals import eligibility as elig


def _case(cid, verdict="supported", **kw):
    return {"case_id": cid, "claim_text": "t", "citation_labels": ["1"],
            "gold_verdict": verdict, **kw}


def test_unresolved_gold_is_excluded_with_a_detail_from_the_case():
    gold = {"cases": [_case("a"), _case("b", None, ambiguity="unresolved",
                                        why="labellers split on the boundary")]}
    scored, excluded = elig.eligibility(gold)
    assert [c["case_id"] for c in scored] == ["a"]
    assert len(excluded) == 1
    assert excluded[0].case_id == "b"
    assert excluded[0].reason == elig.GOLD_VERDICT_UNRESOLVED
    assert "labellers split" in excluded[0].detail


def test_a_drifted_source_excludes_the_cases_that_depend_on_it():
    gold = {"sources": [{"slug": "s4", "label": "4"}],
            "cases": [_case("a", citation_labels=["1"]),
                      _case("b", gold_source_slug="s4", citation_labels=["1"])]}
    drift = [{"label": "4", "slug": "s4", "expected": "paywalled",
              "observed": "retrieved", "kind": "changed"}]
    scored, excluded = elig.eligibility(gold, drift)
    assert [c["case_id"] for c in scored] == ["a"]
    assert excluded[0].reason == elig.REFS_STATUS_DRIFT
    assert "paywalled" in excluded[0].detail and "retrieved" in excluded[0].detail


def test_a_drifted_source_is_matched_by_label_when_the_case_has_no_slug():
    """A `not_retrieved` gold case has a null slug by construction, so label
    matching is not optional — it is the only handle those cases have."""
    gold = {"cases": [_case("b", "not_retrieved", gold_source_slug=None,
                            citation_labels=["4"])]}
    drift = [{"label": "4", "slug": "s4", "expected": "paywalled",
              "observed": "retrieved", "kind": "changed"}]
    scored, excluded = elig.eligibility(gold, drift)
    assert scored == []
    assert excluded[0].case_id == "b"


def test_a_not_verified_source_is_disclosed_but_does_not_exclude():
    """`unverifiable` is not the same as `wrong`.

    Excluding on not-verified would empty every set scored without a reachable
    manifest — including the documented offline invocation — and would treat an
    unanswered question as a failed one.
    """
    gold = {"cases": [_case("b", gold_source_slug="s4", citation_labels=["4"])]}
    drift = [{"label": "4", "slug": "s4", "expected": "paywalled",
              "observed": None, "kind": "not_verified",
              "why": "refs_manifest.json not found"}]
    scored, excluded = elig.eligibility(gold, drift)
    assert [c["case_id"] for c in scored] == ["b"]
    assert excluded == []
    assert elig.not_verified(drift) == drift


def test_an_exclusion_carries_no_constant_sentence():
    gold = {"cases": [_case("b", None)]}
    _, excluded = elig.eligibility(gold)
    assert excluded[0].to_dict()["case_id"] == "b"
    assert excluded[0].to_dict()["reason"] == elig.GOLD_VERDICT_UNRESOLVED
    assert excluded[0].detail
