"""Gold-set and eval-record validity, plus internal self-consistency.

These also give the four pre-existing schemas their first automated check —
nothing validated against them before.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from evals import scoring

_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = _ROOT / "schemas"
GOLD_SETS = sorted((_ROOT / "evals" / "gold").glob("*.gold.json"))


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text())


@pytest.mark.parametrize("name", [p.name for p in sorted(SCHEMAS.glob("*.schema.json"))])
def test_every_schema_file_is_itself_valid(name):
    jsonschema.Draft202012Validator.check_schema(_schema(name))


@pytest.mark.parametrize("path", GOLD_SETS, ids=lambda p: p.name)
def test_committed_gold_sets_validate(path):
    jsonschema.Draft202012Validator(_schema("eval_gold.schema.json")).validate(
        json.loads(path.read_text())
    )


@pytest.mark.parametrize("path", GOLD_SETS, ids=lambda p: p.name)
def test_case_ids_are_unique(path):
    ids = [c["case_id"] for c in json.loads(path.read_text())["cases"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", GOLD_SETS, ids=lambda p: p.name)
def test_anchor_phrases_are_substrings_of_the_decisive_passage(path):
    """Catches authoring typos: an anchor that isn't in the passage it cites
    would silently fail the anchor metric and look like a model error."""
    for c in json.loads(path.read_text())["cases"]:
        passage = (c.get("decisive_passage") or "").lower()
        for a in c.get("gold_anchor_phrases", []):
            assert a.lower() in passage, f"{c['case_id']}: {a!r} not in decisive_passage"


@pytest.mark.parametrize("path", GOLD_SETS, ids=lambda p: p.name)
def test_pairs_are_well_formed(path):
    groups: dict[str, list[dict]] = {}
    for c in json.loads(path.read_text())["cases"]:
        if c.get("pair"):
            groups.setdefault(c["pair"]["pair_id"], []).append(c)
    for pid, members in groups.items():
        roles = [m["pair"]["role"] for m in members]
        assert roles.count("faithful") == 1, f"{pid}: needs exactly one faithful member"
        assert len(members) >= 2, f"{pid}: a pair needs at least two members"
        slugs = {m.get("gold_source_slug") for m in members}
        assert len(slugs) == 1, f"{pid}: members must share one source"


def test_unchecked_is_not_a_legal_gold_verdict():
    """It is a harness error state, never a correct answer."""
    enum = _schema("eval_gold.schema.json")["$defs"]["case"]["properties"]["gold_verdict"]["enum"]
    assert "unchecked" not in enum
    assert None in enum  # unresolved cases are kept, not deleted


def test_not_retrieved_case_requires_a_reason_and_forbids_a_passage():
    v = jsonschema.Draft202012Validator(
        _schema("eval_gold.schema.json")["$defs"]["case"]
    )
    ok = {"case_id": "x", "claim_text": "c", "citation_labels": ["1"],
          "gold_verdict": "not_retrieved", "expected_unavailability": "paywalled",
          "decisive_passage": None, "gold_source_page": None}
    v.validate(ok)
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**ok, "expected_unavailability": None})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({**ok, "decisive_passage": "should not be here"})


def test_judgment_case_requires_page_and_decisive_passage():
    v = jsonschema.Draft202012Validator(
        _schema("eval_gold.schema.json")["$defs"]["case"]
    )
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"case_id": "x", "claim_text": "c", "citation_labels": ["1"],
                    "gold_verdict": "supported"})


def test_scored_record_validates_against_eval_run_schema(gold_mini, results_mini):
    record = scoring.score(gold_mini, results_mini)
    jsonschema.Draft202012Validator(_schema("eval_run.schema.json")).validate(record)


def test_demo_run_record_validates(tmp_path):
    from papertrace.models import RunResults

    gold = json.loads((_ROOT / "evals" / "gold" / "demo_v1.gold.json").read_text())
    results = RunResults.from_json(_ROOT / "evals" / "gold" / "demo_v1.observed.json")
    record = scoring.score(gold, results)
    jsonschema.Draft202012Validator(_schema("eval_run.schema.json")).validate(record)


def test_observed_fixture_matches_the_results_schema():
    """The transcribed demo run must be a legal results.json."""
    data = json.loads((_ROOT / "evals" / "gold" / "demo_v1.observed.json").read_text())
    jsonschema.Draft202012Validator(_schema("results.schema.json")).validate(data)


def test_occurrences_in_text_is_optional_and_keyed_without_block_ids():
    """Gold occurrences are keyed by (label, page, ordinal). Block ids depend
    on the ingest backend, so the set refuses them for the same reason it
    refuses them everywhere else — and the whole array stays optional, so
    demo_v1 keeps validating while reporting the occurrence metrics as absent."""
    v = jsonschema.Draft202012Validator(_schema("eval_gold.schema.json"))
    base = json.loads((_ROOT / "evals" / "gold" / "demo_v1.gold.json").read_text())
    v.validate(base)  # no occurrences_in_text at all
    assert "occurrences_in_text" not in base.get("coverage_gold", {})

    ok = {**base, "coverage_gold": {
        **base["coverage_gold"],
        "occurrences_in_text": [
            {"label": "3", "page": 1, "ordinal": 2, "sentence": "…had not attended…"},
        ],
    }}
    v.validate(ok)

    bad = {**base, "coverage_gold": {
        **base["coverage_gold"],
        "occurrences_in_text": [{"label": "3", "page": 1, "block": "block_0007"}],
    }}
    with pytest.raises(jsonschema.ValidationError):
        v.validate(bad)

    missing_page = {**base, "coverage_gold": {
        **base["coverage_gold"],
        "occurrences_in_text": [{"label": "3"}],
    }}
    with pytest.raises(jsonschema.ValidationError):
        v.validate(missing_page)
