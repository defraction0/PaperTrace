"""Metric arithmetic. Deterministic, fixture-driven, no model, no network."""

from dataclasses import dataclass, field

from evals import metrics
from evals.metrics import Rate


@dataclass
class P:
    """Minimal stand-in for ClaimResult — only the fields metrics touch."""

    verdict: str
    source_page: int | None = None
    anchor_phrases: list[str] = field(default_factory=list)
    note: str = ""


def g(case_id, verdict, page=None, passage=None):
    return {"case_id": case_id, "gold_verdict": verdict,
            "gold_source_page": page, "decisive_passage": passage}


def test_rate_value_is_none_on_zero_denominator():
    assert Rate(0, 0).value is None
    assert Rate(0, 3).value == 0.0  # a real zero is not the same as undefined


def test_judgment_accuracy_excludes_not_retrieved_gold():
    pairs = [
        (g("a", "supported"), P("supported")),
        (g("b", "not_retrieved"), P("not_retrieved")),
    ]
    r = metrics.judgment_accuracy(pairs)
    assert (r.k, r.n) == (1, 1)  # the retrieval case is not a judgement


def test_judgment_accuracy_excludes_unchecked_predictions():
    """A harness crash must shrink the denominator, not count as a wrong answer."""
    pairs = [
        (g("a", "supported"), P("supported")),
        (g("b", "contradicted"), P("unchecked")),
    ]
    r = metrics.judgment_accuracy(pairs)
    assert (r.k, r.n) == (1, 1)


def test_unchecked_prediction_still_surfaces_in_the_error_rate():
    """Excluded from accuracy, but never invisible."""

    @dataclass
    class R:
        claims: list
        uncited: list = field(default_factory=list)
        coverage: dict = field(default_factory=dict)

    results = R(claims=[P("supported"), P("unchecked")])
    pairs = [(g("a", "supported"), P("unchecked"))]
    e = metrics.errors(results, pairs)
    assert e["unchecked_rate"]["k"] == 1
    assert e["unchecked_rate_matched"]["k"] == 1


def test_per_class_precision_is_none_when_class_never_predicted():
    pairs = [(g("a", "supported"), P("supported"))]
    pc = metrics.per_class(pairs, [g("a", "supported")])
    assert pc["contradicted"]["precision"] is None  # not 0.0, and not 1.0
    assert pc["contradicted"]["recall"] is None


def test_per_class_on_a_known_confusion():
    pairs = [
        (g("a", "supported"), P("supported")),
        (g("b", "supported"), P("contradicted")),
        (g("c", "contradicted"), P("contradicted")),
    ]
    pc = metrics.per_class(pairs, [g("a", "supported"), g("b", "supported"),
                                   g("c", "contradicted")])
    assert pc["supported"]["precision"]["value"] == 1.0     # 1 of 1 predicted
    assert pc["supported"]["recall"]["value"] == 0.5        # 1 of 2 gold
    assert pc["contradicted"]["precision"]["value"] == 0.5  # 1 of 2 predicted
    assert pc["contradicted"]["recall"]["value"] == 1.0


def test_recall_pessimistic_counts_unmatched_as_a_miss():
    pairs = [(g("a", "supported"), P("supported"))]
    all_gold = [g("a", "supported"), g("b", "supported")]  # b never matched
    pc = metrics.per_class(pairs, all_gold)
    assert pc["supported"]["recall"]["value"] == 1.0
    assert pc["supported"]["recall_pessimistic"]["value"] == 0.5
    assert pc["supported"]["attrition"]["unmatched"] == 1


def test_macro_f1_excludes_undefined_classes_and_names_them():
    pairs = [(g("a", "supported"), P("supported")),
             (g("b", "contradicted"), P("contradicted"))]
    pc = metrics.per_class(pairs, [g("a", "supported"), g("b", "contradicted")])
    m = metrics.macro_f1(pc)
    assert m["value"] == 1.0
    assert m["classes_excluded"] == ["partial", "not_addressed"]
    assert m["classes_included"] == ["supported", "contradicted"]


def test_macro_f1_of_a_perfect_single_class_run_is_one_not_one_third():
    """The sklearn zero_division=0 trap: dividing by 3 would report 0.33."""
    pairs = [(g("a", "supported"), P("supported"))]
    pc = metrics.per_class(pairs, [g("a", "supported")])
    assert metrics.macro_f1(pc)["value"] == 1.0


def test_empty_inputs_yield_none_not_zero():
    pc = metrics.per_class([], [])
    assert metrics.judgment_accuracy([]).value is None
    assert metrics.macro_f1(pc)["value"] is None
    assert all(v["precision"] is None for v in pc.values())


def test_missed_gap_rate_flags_a_verdict_on_an_unavailable_source():
    """The safety metric: the tool judged something it could not have read."""
    pairs = [(g("a", "not_retrieved"), P("supported"))]
    assert metrics.retrieval(pairs)["missed_gap_rate"]["value"] == 1.0


def test_false_gap_rate_flags_a_gap_that_should_not_exist():
    pairs = [(g("a", "supported"), P("not_retrieved"))]
    r = metrics.retrieval(pairs)
    assert r["false_gap_rate"]["value"] == 1.0
    assert r["missed_gap_rate"]["value"] is None  # no gold gaps at all


def test_source_page_exact_and_pm1_and_missing_are_three_things():
    pairs = [
        (g("a", "supported", page=4), P("supported", source_page=4)),
        (g("b", "supported", page=4), P("supported", source_page=5)),
        (g("c", "supported", page=4), P("supported", source_page=None)),
    ]
    p = metrics.pages(pairs)
    assert p["source_page_accuracy"]["value"] == 0.5       # 1 of 2 answered
    assert p["source_page_accuracy_pm1"]["value"] == 1.0
    assert p["page_missing_rate"]["value"] == 1 / 3        # counted separately


def test_anchor_in_gold_passage_uses_normalized_substring():
    pairs = [
        (g("a", "supported", passage="an external ROC AUC of 0.77"),
         P("supported", anchor_phrases=["0.77"])),
        (g("b", "supported", passage="an external ROC AUC of 0.77"),
         P("supported", anchor_phrases=["completely unrelated"])),
    ]
    a = metrics.anchors(pairs)
    assert a["anchor_in_gold_passage"]["value"] == 0.5
    assert a["anchor_found_on_page_rate"] is None  # live-only, needs the PDF


def test_anchor_empty_rate_counts_silent_answers():
    pairs = [(g("a", "supported"), P("supported", anchor_phrases=[]))]
    assert metrics.anchors(pairs)["anchor_empty_rate"]["value"] == 1.0


def test_binary_contradiction_detection_survives_the_partial_boundary():
    """Wrong on supported-vs-partial, right on the question that matters."""
    pairs = [(g("a", "partial"), P("supported")),
             (g("b", "contradicted"), P("contradicted"))]
    assert metrics.judgment_accuracy(pairs).value == 0.5
    assert metrics.binary_contradiction_detection(pairs).value == 1.0


def test_label_detection_differs_from_claim_coverage(gold_mini, results_mini):
    """A regex that cannot see a style tanks detection while claim coverage
    still looks fine — they must never be conflated."""
    blind = type(results_mini)(
        manuscript="m.pdf", claims=[],
        coverage={"labels_in_text": ["1"], "covered": ["1"], "missing": []},
    )
    c = metrics.coverage(gold_mini, blind)
    assert c["label_detection_accuracy"]["value"] == 0.25  # saw 1 of 4 real labels
    assert c["claim_label_coverage"]["value"] == 1.0       # of what it saw, all covered


def test_uncited_recall_matches_a_paraphrase(gold_mini, results_mini):
    assert metrics.coverage(gold_mini, results_mini)["uncited_recall"]["value"] == 1.0


def test_pair_collapse_is_detected(gold_mini):
    """Both members getting the same verdict is the pattern-matching signature."""
    cases = [c for c in gold_mini["cases"] if c.get("pair")]
    matched = {c["case_id"]: P("supported") for c in cases}
    m = metrics.pairs_metric(cases, matched)
    assert m["pair_collapse_rate"]["value"] == 1.0
    assert m["pair_discrimination"]["value"] == 0.0


def test_confusion_rows_sum_to_the_judgment_population():
    pairs = [(g("a", "supported"), P("supported")),
             (g("b", "contradicted"), P("partial"))]
    conf = metrics.confusion(pairs)
    assert sum(sum(r.values()) for r in conf.values()) == len(
        metrics.judgment_population(pairs)
    )


# --- F1 definedness is a count question, not a p + r question ---------------


def test_f1_is_zero_for_a_class_present_in_gold_and_always_wrong():
    """The reported defect: the class the run got 100% wrong vanished.

    `(p + r) == 0` is true here, so the old code returned None and macro F1
    silently averaged over the classes the run happened to get right.
    """
    pairs = [(g("a", "supported"), P("partial")),
             (g("b", "partial"), P("supported"))]
    pc = metrics.per_class(pairs, [g("a", "supported"), g("b", "partial")])
    assert pc["supported"]["f1"] == 0.0
    assert pc["partial"]["f1"] == 0.0
    assert metrics.macro_f1(pc)["classes_excluded"] == ["contradicted", "not_addressed"]


def test_f1_is_zero_for_a_class_in_gold_but_never_predicted():
    pairs = [(g("a", "partial"), P("supported"))]
    pc = metrics.per_class(pairs, [g("a", "partial")])
    assert pc["partial"]["f1"] == 0.0          # recall 0, precision undefined
    assert pc["partial"]["precision"] is None  # still undefined, and says so


def test_f1_is_zero_for_a_class_predicted_but_never_in_gold():
    pairs = [(g("a", "supported"), P("partial"))]
    pc = metrics.per_class(pairs, [g("a", "supported")])
    assert pc["partial"]["f1"] == 0.0
    assert pc["partial"]["recall"] is None


def test_f1_is_undefined_only_when_the_class_is_in_neither_column_of_j():
    pairs = [(g("a", "supported"), P("supported"))]
    pc = metrics.per_class(pairs, [g("a", "supported")])
    assert pc["contradicted"]["f1"] is None
    assert pc["contradicted"]["f1_support"]["support"] == 0


def test_f1_support_carries_the_counts_the_verdict_rests_on():
    pairs = [(g("a", "supported"), P("supported")),
             (g("b", "supported"), P("partial"))]
    pc = metrics.per_class(pairs, [g("a", "supported"), g("b", "supported")])
    assert pc["supported"]["f1_support"] == {
        "tp": 1, "predicted_in_j": 1, "gold_in_j": 2, "gold_total": 2, "support": 3,
    }


def test_macro_f1_exclusion_kind_is_derived_not_constant():
    """The old `excluded_reason` was a constant string and could be false."""
    pairs = [(g("a", "supported"), P("supported"))]
    pc = metrics.per_class(pairs, [g("a", "supported")])
    m = metrics.macro_f1(pc)
    assert "excluded_reason" not in m
    kinds = {e["class"]: e["kind"] for e in m["exclusions"]}
    assert kinds == {"partial": "absent_from_set", "contradicted": "absent_from_set",
                     "not_addressed": "absent_from_set"}


def test_macro_f1_names_attrition_when_gold_exists_but_none_reached_j():
    """A class whose only gold case never reached J is not 'absent'."""
    pairs = [(g("a", "supported"), P("supported")),
             (g("b", "contradicted"), P("unchecked"))]
    pc = metrics.per_class(pairs, [g("a", "supported"), g("b", "contradicted")])
    m = metrics.macro_f1(pc)
    kinds = {e["class"]: e["kind"] for e in m["exclusions"]}
    assert kinds["contradicted"] == "eliminated_by_attrition"
    assert kinds["partial"] == "absent_from_set"
    detail = next(e for e in m["exclusions"] if e["class"] == "contradicted")["detail"]
    assert "absent" not in detail


# --- population leaks: a denominator that drifted from what it claims -------


def test_matched_population_drops_a_null_gold_pair():
    pairs = [(g("a", "supported"), P("supported")), (g("n", None), P("supported"))]
    assert [x[0]["case_id"] for x in metrics.matched_population(pairs)] == ["a"]


def test_retrieval_denominator_is_the_matched_population_not_every_pair():
    """Reproduced 4/4 -> 5/5. A case nobody could label scored as CORRECT,
    because `(None != "not_retrieved") == (pred != "not_retrieved")`."""
    real = [
        (g("a", "supported"), P("supported")),
        (g("b", "supported"), P("supported")),
        (g("c", "contradicted"), P("contradicted")),
        (g("d", "not_retrieved"), P("not_retrieved")),
    ]
    assert metrics.retrieval(real)["retrieval_outcome_accuracy"]["n"] == 4
    leaked = real + [(g("null", None), P("supported"))]
    out = metrics.retrieval(leaked)["retrieval_outcome_accuracy"]
    assert (out["k"], out["n"]) == (4, 4)


def test_a_null_gold_case_changes_no_retrieval_rate():
    real = [(g("a", "supported"), P("supported")),
            (g("d", "not_retrieved"), P("not_retrieved"))]
    assert metrics.retrieval(real) == metrics.retrieval(
        real + [(g("null", None), P("partial"))]
    )


def test_unchecked_rate_matched_uses_the_matched_population():
    @dataclass
    class R:
        claims: list
        uncited: list = field(default_factory=list)
        coverage: dict = field(default_factory=dict)

    results = R(claims=[P("supported"), P("unchecked")])
    pairs = [(g("a", "supported"), P("unchecked")), (g("n", None), P("supported"))]
    e = metrics.errors(results, pairs)
    assert (e["unchecked_rate_matched"]["k"], e["unchecked_rate_matched"]["n"]) == (1, 1)
    assert e["unchecked_rate_matched"]["population"] == "M"
    # the run-level rates deliberately stay over every prediction
    assert e["unchecked_rate"]["n"] == 2


def _paired(cid, role, pair_id="p1", verdict="supported"):
    c = g(cid, verdict)
    c["pair"] = {"pair_id": pair_id, "role": role}
    return c


def test_a_half_pair_is_not_scored_as_a_pair():
    """A pair whose sibling is excluded became a one-member group scoring
    `pair_discrimination 1/1 = 100%` — and `pair_collapse_rate 1/1` too, since
    one verdict is trivially one distinct verdict."""
    all_gold = [_paired("a", "faithful"), _paired("b", "altered", verdict="contradicted")]
    matched = {"a": P("supported"), "b": P("contradicted")}
    both = metrics.pairs_metric(all_gold, matched, {"a", "b"})
    assert both["n_pairs"] == 1
    assert both["pair_discrimination"]["value"] == 1.0

    half = metrics.pairs_metric(all_gold, matched, {"a"})   # b excluded
    assert half["n_pairs"] == 0
    assert half["pair_discrimination"] is None      # undefined, not 100%
    assert half["pair_collapse_rate"] is None


def test_incomplete_pair_groups_are_listed_not_silently_dropped():
    all_gold = [_paired("a", "faithful"), _paired("b", "altered")]
    half = metrics.pairs_metric(all_gold, {"a": P("supported")}, {"a", "b"})
    assert half["n_pairs_incomplete"] == 1
    assert half["incomplete"][0]["pair_id"] == "p1"
    assert "b" in half["incomplete"][0]["why"]


def test_every_rate_declares_a_population():
    pairs = [(g("a", "supported"), P("supported"))]
    for rate in (metrics.judgment_accuracy(pairs),
                 metrics.binary_contradiction_detection(pairs)):
        assert rate.to_dict()["population"]
    for rate in metrics.retrieval(pairs).values():
        assert rate["population"]


# ---------------------------------------------------------------------------
# occurrence-level coverage metrics
# ---------------------------------------------------------------------------


def _v2_coverage(**kw) -> dict:
    items = kw.pop("items", [
        {"id": "block_0002:10:1", "label": "1", "page": 2, "block": "block_0002",
         "offset": 10, "group": "1", "section": "Intro", "sentence": "a [1].",
         "status": "covered", "claim_id": 1},
        {"id": "block_0002:40:1", "label": "1", "page": 2, "block": "block_0002",
         "offset": 40, "group": "1", "section": "Intro", "sentence": "b [1].",
         "status": "uncovered", "claim_id": None},
        {"id": "block_0003:10:4", "label": "4", "page": 3, "block": "block_0003",
         "offset": 10, "group": "4", "section": "Methods", "sentence": "c [4].",
         "status": "uncertain", "claim_id": None},
        {"id": "block_0003:60:4", "label": "4", "page": 3, "block": "block_0003",
         "offset": 60, "group": "4", "section": "Methods", "sentence": "d [4].",
         "status": "uncertain", "claim_id": None},
    ])
    counts = {"total": len(items)}
    for status in ("covered", "uncovered", "uncertain"):
        counts[status] = sum(1 for o in items if o["status"] == status)
    base = {
        "schema": "coverage/2",
        "labels_in_text": ["1", "2", "3", "4"], "covered": ["1", "2", "3"],
        "missing": ["4"], "unit": "occurrence", "source": "source_map",
        "labels_partially_covered": ["1"], "labels_uncertain_only": ["4"],
        "occurrences": {**counts, "items": items},
        "attribution": {"method": "location+similarity", "min_ratio": 0.45,
                        "min_margin": 0.10, "claims_unattributed": []},
    }
    base.update(kw)
    return base


def test_occurrence_metrics_are_absent_on_a_v1_run(gold_mini, results_mini):
    """A label-level run cannot answer the occurrence question at all. The
    metric is absent — which is a different claim from a Rate with n == 0, and
    must not render as one."""
    c = metrics.coverage(gold_mini, results_mini)
    for key in ("claim_occurrence_coverage", "occurrence_uncertain_rate",
                "occurrence_detection_accuracy"):
        assert c[key] is None, key
    # the label-level rates are untouched, so no demo re-baselining
    assert c["claim_label_coverage"]["value"] == 0.75


def test_occurrence_coverage_counts_uncertain_separately(gold_mini, results_mini):
    """`uncertain` is never folded into covered, and never into uncovered."""
    import dataclasses

    run = dataclasses.replace(results_mini, coverage=_v2_coverage())
    c = metrics.coverage(gold_mini, run)

    assert c["claim_occurrence_coverage"]["numerator"] == 1
    assert c["claim_occurrence_coverage"]["denominator"] == 4
    assert c["claim_occurrence_coverage"]["value"] == 0.25
    assert c["occurrence_uncertain_rate"]["numerator"] == 2
    assert c["occurrence_uncertain_rate"]["value"] == 0.5
    # and the label-level rates still say exactly what they said before
    assert c["claim_label_coverage"]["value"] == 0.75


def test_occurrence_detection_is_keyed_by_label_page_ordinal_not_block_id(gold_mini,
                                                                         results_mini):
    """Block ids depend on the ingest backend, so the gold set refuses them —
    `eval_gold.schema.json` already applies that reasoning to source anchors."""
    import dataclasses

    gold = {**gold_mini, "coverage_gold": {
        **gold_mini["coverage_gold"],
        "occurrences_in_text": [
            {"label": "1", "page": 2, "ordinal": 1},
            {"label": "1", "page": 2, "ordinal": 2},
            {"label": "4", "page": 3, "ordinal": 1},
            {"label": "9", "page": 7, "ordinal": 1},   # a style the regex never saw
        ],
    }}
    run = dataclasses.replace(results_mini, coverage=_v2_coverage())
    c = metrics.coverage(gold, run)

    assert c["occurrence_detection_accuracy"]["numerator"] == 3
    assert c["occurrence_detection_accuracy"]["denominator"] == 4
    # the tool found a second [4] the human did not record: an over-count, not
    # a match, and it must not inflate detection
    assert c["occurrence_detection_accuracy"]["value"] == 0.75


def test_detection_accuracy_is_absent_when_occurrences_carry_no_page(gold_mini,
                                                                     results_mini):
    """The clean.md fallback yields pageless occurrences. The gold key is
    (label, page, ordinal), so the comparison cannot be made — and reporting
    0% would blame the tool for a question nobody asked."""
    import dataclasses

    pageless = [{**o, "page": None, "block": None} for o in _v2_coverage()["occurrences"]["items"]]
    gold = {**gold_mini, "coverage_gold": {
        **gold_mini["coverage_gold"],
        "occurrences_in_text": [{"label": "1", "page": 2, "ordinal": 1}],
    }}
    run = dataclasses.replace(results_mini,
                              coverage=_v2_coverage(items=pageless, source="clean.md"))
    c = metrics.coverage(gold, run)

    assert c["occurrence_detection_accuracy"] is None
    assert c["claim_occurrence_coverage"] is not None  # this one still answers


def test_a_v2_run_still_satisfies_the_populations_contract(gold_mini, results_mini):
    """Every rate in the record must name a population the record declares.
    The occurrence figures are reported with their denominator inline instead,
    so scoring a coverage/2 run cannot introduce an undeclared population."""
    import dataclasses

    from evals import scoring

    run = dataclasses.replace(results_mini, coverage=_v2_coverage())
    record = scoring.score(gold_mini, run)

    known = set(record["populations"])

    def rates(node):
        if isinstance(node, dict):
            if {"k", "n", "value"} <= node.keys():
                yield node
                return
            for v in node.values():
                yield from rates(v)
        elif isinstance(node, list):
            for v in node:
                yield from rates(v)

    for rate in rates(record):
        assert rate["population"] in known, rate
        assert rate["n"] == record["populations"][rate["population"]]["n"]
