"""Gold sets are frozen against a source's availability. Two silent holes.

A `not_retrieved` gold case only tests retrieval for as long as the DOI stays
paywalled. If the source opens up, the case silently stops testing retrieval
and starts testing judgement, corrupting `missed_gap_rate` with no error
anywhere. So the check has to be able to say three different things: unchanged,
changed, and *not verified* — and before this it could only say two.
"""

import json
from pathlib import Path

from evals import provenance, scoring

_ROOT = Path(__file__).resolve().parents[2]

GOLD = {
    "set_id": "drift-v1",
    "sources": [
        {"slug": "a-2020", "label": "1", "expected_ref_status": "retrieved"},
        {"slug": "d-2024", "label": "4", "expected_ref_status": "paywalled"},
    ],
    "cases": [],
}


def _manifest(tmp_path, entries) -> Path:
    p = tmp_path / "refs_manifest.json"
    p.write_text(json.dumps({"manuscript": "m.pdf", "entries": entries}))
    return p


def test_a_missing_manifest_reports_not_verified_not_clean():
    """`[]` was indistinguishable from checked-and-clean. It meant neither."""
    drift = provenance.check_refs_drift(GOLD, Path("/nope/refs_manifest.json"))
    assert drift, "a missing manifest must not read as 'checked, nothing changed'"
    assert {d["kind"] for d in drift} == {"not_verified"}
    assert {d["label"] for d in drift} == {"1", "4"}
    assert "not found" in drift[0]["why"]


def test_an_unreadable_manifest_reports_not_verified_rather_than_raising(tmp_path):
    bad = tmp_path / "refs_manifest.json"
    bad.write_text("{ this is not json")
    drift = provenance.check_refs_drift(GOLD, bad)
    assert {d["kind"] for d in drift} == {"not_verified"}
    assert "could not be read" in drift[0]["why"]


def test_a_source_absent_from_the_manifest_reports_not_verified(tmp_path):
    """Unobservable was rendered as unchanged: no entry, no question asked."""
    m = _manifest(tmp_path, [{"num": "1", "raw": "x", "status": "retrieved"}])
    drift = provenance.check_refs_drift(GOLD, m)
    assert [d["label"] for d in drift] == ["4"]
    assert drift[0]["kind"] == "not_verified"
    assert "absent from the manifest" in drift[0]["why"]


def test_a_changed_status_is_reported_as_changed(tmp_path):
    m = _manifest(tmp_path, [{"num": "1", "raw": "x", "status": "retrieved"},
                             {"num": "4", "raw": "y", "status": "retrieved"}])
    drift = provenance.check_refs_drift(GOLD, m)
    assert [(d["label"], d["kind"]) for d in drift] == [("4", "changed")]
    assert (drift[0]["expected"], drift[0]["observed"]) == ("paywalled", "retrieved")


def test_a_matching_status_produces_no_entry():
    """A real manifest from a real demo run, against the committed gold set.

    The manifest is a committed fixture, not `demo_case/refs_manifest.json`:
    the case folder is gitignored, so reading it made this test pass on the
    machine that produced it and fail on every fresh clone — and fail loudly,
    because a missing manifest now yields `not_verified` rather than `[]`.
    Regenerate with `cp demo_case/refs_manifest.json` after a demo run.
    """
    gold = json.loads((_ROOT / "evals" / "gold" / "demo_v1.gold.json").read_text())
    manifest = Path(__file__).parent / "fixtures" / "refs_manifest_demo_v1.json"
    assert provenance.check_refs_drift(gold, manifest) == []


def test_a_source_with_no_expectation_is_not_checked(tmp_path):
    gold = {"sources": [{"slug": "a-2020", "label": "1"}], "cases": []}
    assert provenance.check_refs_drift(gold, tmp_path / "nothing.json") == []


# --- and the drift has to reach the score ----------------------------------


def _drifted(label="3"):
    return [{"label": label, "slug": "c-2022", "expected": "paywalled",
             "observed": "retrieved", "kind": "changed"}]


def test_a_drifted_case_leaves_the_judgement_metrics(gold_mini, results_mini):
    """Before this, `score()` took no drift argument at all: the banner and the
    contaminated numbers were rendered on the same page."""
    clean = scoring.score(gold_mini, results_mini)
    dirty = scoring.score(gold_mini, results_mini, refs_drift=_drifted())

    assert clean["metrics"]["retrieval"]["missed_gap_rate"]["n"] == 1
    assert dirty["metrics"]["retrieval"]["missed_gap_rate"]["n"] == 0
    assert dirty["populations"]["M"]["n"] == clean["populations"]["M"]["n"] - 1
    assert [e["case_id"] for e in dirty["eligibility"]["excluded"]
            if e["reason"] == "refs_status_drift"] == ["m-c04"]


def test_a_drifted_case_stays_in_coverage_and_the_run_level_error_rates(
    gold_mini, results_mini
):
    """Those percentages sit beside the ones that shrank, so the record has to
    say which denominators still contain the excluded case."""
    clean = scoring.score(gold_mini, results_mini)
    dirty = scoring.score(gold_mini, results_mini, refs_drift=_drifted())
    assert dirty["metrics"]["coverage"] == clean["metrics"]["coverage"]
    assert dirty["metrics"]["errors"]["not_retrieved_rate"] == \
        clean["metrics"]["errors"]["not_retrieved_rate"]
    assert dirty["eligibility"]["still_counted_in"]


def test_an_excluded_case_is_kept_in_the_record(gold_mini, results_mini):
    dirty = scoring.score(gold_mini, results_mini, refs_drift=_drifted())
    row = next(r for r in dirty["per_case"] if r["case_id"] == "m-c04")
    assert row["eligible"] is False
    assert row["excluded_reason"] == "refs_status_drift"
    assert "paywalled" in row["excluded_detail"]
    assert row["correct"] is False   # never scored, so never correct


def test_drift_defaults_to_the_key_both_runners_already_write(gold_mini, results_mini):
    rec = scoring.score(gold_mini, results_mini,
                        provenance={"refs_status_drift": _drifted()})
    assert rec["gold"]["n_excluded_drift"] == 1


def test_a_not_verified_entry_is_disclosed_without_shrinking_anything(
    gold_mini, results_mini
):
    unverifiable = [{"label": "3", "slug": "c-2022", "expected": "paywalled",
                     "observed": None, "kind": "not_verified",
                     "why": "refs_manifest.json not found"}]
    clean = scoring.score(gold_mini, results_mini)
    rec = scoring.score(gold_mini, results_mini, refs_drift=unverifiable)
    assert rec["populations"]["M"]["n"] == clean["populations"]["M"]["n"]
    assert rec["eligibility"]["refs_verification"] == unverifiable
    assert any("could not be verified" in c for c in rec["caveats"])


def test_score_only_checks_drift_without_case_dir(tmp_path):
    """`--case-dir` gates the check, and the documented invocation omits it —
    so in practice the freeze check never ran at all."""
    import sys

    sys.path.insert(0, str(_ROOT / "evals" / "runners"))
    from evals.runners import score_only

    run_dir = score_only.score_one(
        _ROOT / "evals" / "gold" / "demo_v1.gold.json",
        _ROOT / "evals" / "gold" / "demo_v1.observed.json",
        tmp_path,
    )
    record = json.loads((run_dir / "eval.json").read_text())
    drift = record["provenance"]["refs_status_drift"]
    assert drift, "the freeze check must run, or say it did not"
    assert {d["kind"] for d in drift} == {"not_verified"}
    assert "could not be verified" in " ".join(record["caveats"])
