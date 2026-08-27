"""Alignment: gold cases to the claims a run actually produced."""

import random
from dataclasses import dataclass, field

import pytest

from evals.align import Alignment, align, matched_pairs, normalize_claim


@dataclass
class Claim:
    id: int
    claim: str
    refs: list[str]
    verdict: str = "supported"


@dataclass
class Res:
    claims: list
    coverage: dict = field(default_factory=dict)
    uncited: list = field(default_factory=list)


def gold(*cases, **kw):
    return {"set_id": "t", "cases": list(cases), **kw}


def case(cid, text, labels, **kw):
    return {"case_id": cid, "claim_text": text, "citation_labels": labels,
            "gold_verdict": kw.pop("verdict", "supported"), **kw}


def test_normalization_folds_dashes_quotes_case_and_trailing_period():
    assert normalize_claim("Aged 40–69 years.") == normalize_claim("aged 40-69 years")
    assert normalize_claim("The “cohort”") == normalize_claim('the "cohort"')
    assert normalize_claim("  **bold**  text ") == "bold text"


def test_exact_match_scores_one():
    g = gold(case("c1", "The AUC was 0.91.", ["1"]))
    r = Res([Claim(1, "The AUC was 0.91.", ["1"])])
    a = align(g, r)
    assert a.matches[0].stage == "exact"
    assert a.matches[0].score == 1.0
    assert a.fuzzy_fraction == 0.0


def test_paraphrase_matches_by_similarity_within_the_same_label():
    g = gold(case("c1", "The reported AUC was 0.91 overall.", ["1"]))
    r = Res([Claim(1, "The AUC reported was 0.91 overall.", ["1"])])
    a = align(g, r)
    assert a.matches[0].stage == "similarity"
    assert a.matches[0].pred_id == 1
    assert a.fuzzy_fraction == 1.0


def test_unrelated_text_is_not_force_matched_to_the_nearest():
    g = gold(case("c1", "The reported AUC was 0.91 overall.", ["1"]))
    r = Res([Claim(1, "Completely unrelated sentence about turtles.", ["1"])])
    a = align(g, r)
    assert a.unmatched_gold and a.unmatched_gold[0][0] == "c1"
    assert a.unmatched_predictions == [1]


def test_two_claims_on_the_same_label_are_split_by_match_keys():
    """The real littlejohns case: two claims cite [3] and must not swap."""
    g = gold(
        case("c-target", "The imaging enhancement targets 100,000 participants.", ["3"],
             match_keys={"must_contain_any": ["100,000"], "must_not_contain": ["attend"]}),
        case("c-attend", "Nearly one in five had not attended a centre.", ["3"],
             match_keys={"must_contain_any": ["not attended", "one in five"]}),
    )
    r = Res([
        Claim(1, "The imaging enhancement targets 100,000 participants.", ["3"]),
        Claim(2, "Nearly one in five had not attended a centre.", ["3"]),
    ])
    a = align(g, r)
    got = {m.case_id: m.pred_id for m in a.matches}
    assert got == {"c-target": 1, "c-attend": 2}


def test_a_genuine_tie_is_reported_ambiguous_not_guessed():
    g = gold(case("c1", "The value was measured.", ["1"]))
    r = Res([Claim(1, "The value was measured.x", ["1"]),
             Claim(2, "The value was measured.y", ["1"])])
    a = align(g, r)
    # near-identical candidates inside the margin: refuse rather than pick
    assert a.ambiguous and a.ambiguous[0][0] == "c1"
    assert a.unmatched_gold[0][0] == "c1"
    assert "match_keys" in a.unmatched_gold[0][1]


def test_unmatched_gold_is_labelled_extraction_gap_when_the_tool_agrees():
    g = gold(case("c1", "Nobody extracted this.", ["4"]))
    r = Res([], coverage={"labels_in_text": ["4"], "covered": [], "missing": ["4"]})
    a = align(g, r)
    assert a.unmatched_gold == [("c1", "extraction_gap")]


def test_out_of_scope_predictions_are_listed_not_scored():
    g = gold(case("c1", "In scope.", ["1"]))
    r = Res([Claim(1, "In scope.", ["1"]), Claim(9, "Not in the gold set.", ["8"])])
    a = align(g, r)
    assert a.unmatched_predictions == [9]
    assert len(matched_pairs(g, r, a)) == 1


def test_a_prediction_is_never_used_twice():
    """One-to-one, tested on cases that are genuinely distinguishable.

    The version this replaces used two *identical* gold texts and asserted the
    single prediction was consumed once — which passed only because the
    first-listed case won by list order. That encoded the order-dependence bug
    as intended behaviour.
    """
    g = gold(case("c1", "The AUC was 0.91 in the held-out set.", ["1"]),
             case("c2", "Attendance fell in the second wave.", ["1"]))
    r = Res([Claim(1, "The AUC was 0.91 in the held out set.", ["1"])])
    a = align(g, r)
    used = [m.pred_id for m in a.matches if m.pred_id is not None]
    assert used == [1]
    assert len(used) == len(set(used))


def test_identical_competing_gold_cases_are_ambiguous_not_awarded_by_list_order():
    """Two gold cases with the same text cannot both be that prediction, and
    nothing in the data says which one it is. List position is not evidence."""
    g = gold(case("c1", "Same text.", ["1"]), case("c2", "Same text.", ["1"]))
    a = align(g, Res([Claim(1, "Same text.", ["1"])]))
    assert [m.pred_id for m in a.matches] == [None, None]
    assert {c for c, _ in a.ambiguous} == {"c1", "c2"}
    # the contest freezes the cases and leaves the prediction free
    assert a.unmatched_predictions == [1]

    swapped = align(gold(case("c2", "Same text.", ["1"]),
                         case("c1", "Same text.", ["1"])),
                    Res([Claim(1, "Same text.", ["1"])]))
    assert swapped == a


def test_a_column_contest_inside_the_margin_is_refused_not_guessed():
    """The reproduced failure. Two gold cases compete for ONE prediction.

    Today each case's *row* holds a single candidate, so the margin test never
    fires and whichever case is listed first is awarded the prediction on a
    0.019 margin. The margin has to work in the column direction too.
    """
    hi = "The cohort included 500,000 adult participants overall."
    lo = "The cohort included 500,000 older adult participants."
    pred = "The cohort included 500,000 adult participants."
    g = gold(case("c-hi", hi, ["2"]), case("c-lo", lo, ["2"]))
    a = align(g, Res([Claim(1, pred, ["2"])]))
    assert [m.pred_id for m in a.matches] == [None, None]
    assert {c for c, _ in a.ambiguous} == {"c-hi", "c-lo"}


def test_the_discriminator_records_the_assigned_pairs_score_not_the_rivals():
    """Latent bug: the branch recorded `best`, the score of the candidate the
    discriminator had just thrown out."""
    g = gold(case("c1", "The value was measured in the cohort of turtles.", ["1"],
                  match_keys={"must_not_contain": ["today"]}))
    r = Res([Claim(1, "The value was measured in the cohort of turtles today.", ["1"]),
             Claim(2, "The value was measured in the cohort of tortoises.", ["1"])])
    a = align(g, r)
    m = a.matches[0]
    assert (m.stage, m.pred_id) == ("discriminator", 2)
    assert round(m.score, 4) == 0.9375   # the survivor's, not the rival's 0.94


def test_an_unreadable_coverage_audit_yields_attribution_undetermined():
    """A coverage block the harness cannot parse must not default to blaming
    the evaluator's matcher. That transfers blame away from the tool."""
    g = gold(case("c1", "Nobody extracted this.", ["4"]))
    r = Res([], coverage={"labels_in_text": ["4"], "covered": [], "missing": "4"})
    a = align(g, r)
    cid, reason = a.unmatched_gold[0]
    assert cid == "c1"
    assert reason.startswith("attribution_undetermined")
    assert "alignment_failure" not in reason


def test_alignment_outputs_are_sorted_by_case_id():
    g = gold(case("c3", "Third claim about things.", ["3"]),
             case("c1", "First claim about things.", ["1"]),
             case("c2", "Second claim about things.", ["2"]))
    a = align(g, Res([Claim(1, "First claim about things.", ["1"])]))
    assert [m.case_id for m in a.matches] == ["c1", "c2", "c3"]
    assert [c for c, _ in a.unmatched_gold] == ["c2", "c3"]


def test_alignment_is_identical_under_permutation_of_both_lists():
    """Order-independence asserted on the WHOLE Alignment, not just the map.

    The fixture carries one exact match, one fuzzy match, a contested pair the
    aligner must refuse, and a gold case nothing extracted.
    """
    cases = [
        case("c-exact", "The AUC was 0.91.", ["1"]),
        case("c-fuzzy", "The reported AUC was 0.88 overall.", ["2"]),
        case("c-hi", "The cohort included 500,000 adult participants overall.", ["3"]),
        case("c-lo", "The cohort included 500,000 older adult participants.", ["3"]),
        case("c-gone", "A sentence nobody extracted at all.", ["9"]),
    ]
    claims = [
        Claim(11, "The AUC was 0.91.", ["1"]),
        Claim(12, "The AUC reported was 0.88 overall.", ["2"]),
        Claim(13, "The cohort included 500,000 adult participants.", ["3"]),
        Claim(14, "An out-of-scope sentence about something else.", ["8"]),
    ]
    baseline = align(gold(*cases), Res(claims))
    assert baseline.matches[0].stage == "exact"
    assert baseline.ambiguous                      # the contest really fired

    rng = random.Random(7)
    for _ in range(20):
        c2, p2 = cases[:], claims[:]
        rng.shuffle(c2)
        rng.shuffle(p2)
        assert align(gold(*c2), Res(p2)) == baseline


def test_alignment_is_deterministic_under_reordering():
    cases = [case(f"c{i}", f"Claim number {i} about things.", [str(i)]) for i in range(1, 6)]
    claims = [Claim(i, f"Claim number {i} about things.", [str(i)]) for i in range(1, 6)]
    baseline = {m.case_id: m.pred_id for m in align(gold(*cases), Res(claims)).matches}

    rng = random.Random(0)
    for _ in range(5):
        c2, p2 = cases[:], claims[:]
        rng.shuffle(c2)
        rng.shuffle(p2)
        got = {m.case_id: m.pred_id for m in align(gold(*c2), Res(p2)).matches}
        assert got == baseline


def test_every_gold_case_gets_a_row_even_unmatched():
    g = gold(case("c1", "Matched.", ["1"]), case("c2", "Never extracted.", ["2"]))
    a = align(g, Res([Claim(1, "Matched.", ["1"])]))
    assert {m.case_id for m in a.matches} == {"c1", "c2"}


def test_fuzzy_fraction_ignores_unmatched_rows():
    a = Alignment()
    assert a.fuzzy_fraction == 0.0


# ---------------------------------------------------------------------------
# occurrence-level coverage lands under the aligner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coverage", [
    # v1: label-level, the shape shipping before occurrence coverage
    {"labels_in_text": ["4"], "covered": [], "missing": ["4"]},
    # v2: the same fact, in a coverage block that also carries occurrences
    {"schema": "coverage/2", "labels_in_text": ["4"], "covered": [], "missing": ["4"],
     "unit": "occurrence", "source": "source_map",
     "labels_partially_covered": [], "labels_uncertain_only": [],
     "occurrences": {"total": 1, "covered": 0, "uncovered": 1, "uncertain": 0,
                     "items": [{"id": "block_0002:10:4", "label": "4", "status": "uncovered"}]},
     "attribution": {"method": "location+similarity", "min_ratio": 0.45,
                     "min_margin": 0.10, "claims_unattributed": []}},
], ids=["coverage_v1", "coverage_v2"])
def test_extraction_gap_survives_the_coverage_shape_change(coverage):
    """`missing` keeps its label-list shape under coverage/2 precisely so this
    attribution cannot flip silently from the tool to the evaluator."""
    g = gold(case("c1", "Something the tool never extracted.", ["4"]))
    a = align(g, Res([], coverage=coverage))
    assert a.unmatched_gold == [("c1", "extraction_gap")]


def test_a_partially_covered_label_is_an_extraction_gap_not_an_alignment_failure():
    """The improvement runs OPPOSITE to the risk. A gold case sitting on the
    second occurrence of a label the tool *did* reach was blamed on the
    evaluator's matcher; occurrence data blames the tool, which is where it
    belongs. Net movement of blame is toward the tool, never away."""
    g = gold(case("c1", "Nearly one in five never attended.", ["3"]))
    r = Res([Claim(1, "Imaging enhancement targets 100,000 participants.", ["3"])],
            coverage={"schema": "coverage/2",
                      "labels_in_text": ["3"], "covered": ["3"], "missing": [],
                      "labels_partially_covered": ["3"], "labels_uncertain_only": []})
    a = align(g, r)
    assert a.unmatched_gold == [("c1", "extraction_gap")]


def test_an_unreadable_partial_coverage_list_is_not_silently_ignored():
    """The same wholesale refusal `missing` gets: a shape we cannot read means
    'attribution undetermined', never 'nothing was partially covered'."""
    g = gold(case("c1", "Nearly one in five never attended.", ["3"]))
    r = Res([], coverage={"labels_in_text": ["3"], "covered": ["3"], "missing": [],
                          "labels_partially_covered": 3})
    a = align(g, r)
    assert a.unmatched_gold[0][1].startswith("attribution_undetermined")
