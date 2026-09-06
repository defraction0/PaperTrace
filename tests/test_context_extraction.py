"""Extraction is told where the citations are, instead of being asked to
describe where it looked.

The old flow threw the location away and then spent 130 lines getting it back:
the model returned a paraphrase plus a free-text `location` ("Methods ¶2"), and
Python guessed which of several `[3]` markers that paraphrase had come from —
normalising both sides, scoring them with `SequenceMatcher`, and accepting a
pairing only on `ratio ≥ 0.45` **and** `margin ≥ 0.10`. Everything it could not
decide became `uncertain`, which is honest but was avoidable.

The citation inventory was already built, deterministically, from
`source_map.json` — just *after* the model call rather than before it. Now it
goes into the prompt with a stable `ctx_NNNN` per occurrence, claims come back
carrying the ids they were extracted from, and coverage is a set lookup.

The uncertainty does not vanish; it moves to one place. A claim that cites a
label and names no usable context for it leaves that label's unreached
occurrences `uncertain` — because a claim *did* reach one of them and nobody
can say which. That is the same third status, kept for the one case that can
still produce it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import check as check_mod  # noqa: E402
from papertrace.models import ClaimResult, RunResults  # noqa: E402

SCHEMA = json.loads((Path(__file__).parent.parent / "schemas" / "results.schema.json").read_text())


# --- fixtures --------------------------------------------------------------


def _case(tmp_path: Path, blocks: list[dict]) -> Path:
    """A case folder whose manuscript source map holds `blocks`."""
    d = tmp_path / "ingest" / "manuscript"
    d.mkdir(parents=True, exist_ok=True)
    smap = {"doc": "m.pdf", "pages": 1, "converter": "pymupdf", "blocks": blocks}
    (d / "source_map.json").write_text(json.dumps(smap))
    (d / "annotated.md").write_text("\n".join(b["text"] for b in blocks))
    (d / "clean.md").write_text("\n".join(b["text"] for b in blocks))
    return tmp_path


def _block(bid: str, text: str, page: int = 1, heading: str = "Results") -> dict:
    return {"id": bid, "page": page, "type": "text", "bbox": [0, 0, 10, 10],
            "text": text, "heading_path": [heading]}


TWO_PLACES = [
    _block("block_0001", "Mortality fell by 12% in the over-65 subgroup [3]."),
    _block("block_0002", "Readmission was unchanged across all strata [3]."),
]


# --- the inventory reaches the model --------------------------------------


def test_the_extraction_prompt_carries_the_citation_inventory(tmp_path, monkeypatch):
    """The whole point: the model is shown the places, so it never has to be
    asked to describe one."""
    seen = {}
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: seen.setdefault("p", p) and "" or
                        json.dumps({"cited": [], "uncited": []}))
    check_mod.extract_claims(_case(tmp_path, TWO_PLACES))

    p = seen["p"]
    assert "ctx_0001" in p and "ctx_0002" in p, "the inventory is not in the prompt"
    assert "over-65 subgroup" in p, "an inventory without the sentences names nothing"
    assert "block_0001" not in p, "internal ids are not the model's to copy"


def test_the_inventory_is_ordered_and_stable():
    """`ctx_NNNN` is assigned in reading order over the occurrence list, and the
    map back to real occurrence ids is built in the same pass — so no consumer
    ever re-derives the pairing by position. Re-deriving it is the
    reading-order-zipping mistake in a new costume."""
    occs = [
        {"id": "block_0001:46:3", "label": "3", "page": 1, "section": "Results",
         "sentence": "A [3]."},
        {"id": "block_0002:44:3", "label": "3", "page": 2, "section": "Discussion",
         "sentence": "B [3]."},
    ]
    text, mapping = check_mod._render_inventory(occs)
    assert mapping == {"ctx_0001": "block_0001:46:3", "ctx_0002": "block_0002:44:3"}
    assert "ctx_0001" in text and "ctx_0002" in text
    assert text.index("ctx_0001") < text.index("ctx_0002")


# --- a claim keeps the context it came from -------------------------------


def _extract(tmp_path, blocks, cited, monkeypatch):
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": cited, "uncited": []}))
    return check_mod.extract_claims(_case(tmp_path, blocks))[0]


def test_a_claim_records_the_occurrence_it_was_extracted_from(tmp_path, monkeypatch):
    claims = _extract(tmp_path, TWO_PLACES, [
        {"id": 1, "ctx": ["ctx_0002"], "quote": "Readmission was unchanged across all strata.",
         "claim": "Readmission unchanged.", "location": "Results", "refs": ["3"]},
    ], monkeypatch)
    assert claims[0].ctx_ids == ["block_0002:44:3"], "the ctx label was not resolved to an id"


def test_one_claim_can_span_two_occurrences(tmp_path, monkeypatch):
    """A sentence citing [2] and [3] is one claim over two occurrences, and a
    group `[7,8]` is one marker over two. Neither can be expressed by a single
    id, which is why this is a list."""
    blocks = [_block("block_0001", "Both cohorts agree [2], and the imaging arm too [3].")]
    claims = _extract(tmp_path, blocks, [
        {"id": 1, "ctx": ["ctx_0001", "ctx_0002"], "quote": "Both cohorts agree, and the "
         "imaging arm too.", "claim": "Cohorts agree.", "location": "Results",
         "refs": ["2", "3"]},
    ], monkeypatch)
    assert len(claims[0].ctx_ids) == 2


def test_an_unknown_context_id_is_dropped_not_guessed(tmp_path, monkeypatch):
    """A model returning `ctx_9999` has told us nothing. Falling back to "the
    first occurrence of that label" would manufacture exactly the confident,
    wrong attribution this redesign removes."""
    claims = _extract(tmp_path, TWO_PLACES, [
        {"id": 1, "ctx": ["ctx_9999"], "quote": "q", "claim": "c",
         "location": "Results", "refs": ["3"]},
    ], monkeypatch)
    assert claims[0].ctx_ids == []


def test_a_missing_ctx_field_is_not_an_error(tmp_path, monkeypatch):
    """Older prompts and sloppier answers omit it. The claim is still a claim —
    it simply carries no context, and coverage says so."""
    claims = _extract(tmp_path, TWO_PLACES, [
        {"id": 1, "quote": "q", "claim": "c", "location": "Results", "refs": ["3"]},
    ], monkeypatch)
    assert claims[0].ctx_ids == []
    assert claims[0].claim == "c"


# --- coverage becomes bookkeeping -----------------------------------------


def _cov(tmp_path, blocks, claims):
    return check_mod.coverage_audit(_case(tmp_path, blocks), claims)


def test_an_occurrence_is_covered_only_if_a_claim_named_it(tmp_path):
    c = ClaimResult(id=1, claim="c", location="Results", refs=["3"],
                    ctx_ids=["block_0002:44:3"])
    cov = _cov(tmp_path, TWO_PLACES, [c])
    by_id = {o["id"]: o for o in cov["occurrences"]["items"]}
    assert by_id["block_0002:44:3"]["status"] == "covered"
    assert by_id["block_0002:44:3"]["claim_id"] == 1
    assert by_id["block_0001:46:3"]["status"] == "uncovered"
    assert cov["occurrences"]["covered"] == 1
    assert cov["occurrences"]["uncertain"] == 0


def test_a_claim_citing_a_label_with_no_usable_context_leaves_it_uncertain(tmp_path):
    """The one case that can still produce doubt, and the reason `uncertain`
    survives the redesign: a claim reached one of these two sentences and
    nothing can say which. Calling both uncovered would cry wolf; calling
    either covered would be the overstatement."""
    c = ClaimResult(id=1, claim="c", location="Results", refs=["3"], ctx_ids=[])
    cov = _cov(tmp_path, TWO_PLACES, [c])
    assert {o["status"] for o in cov["occurrences"]["items"]} == {"uncertain"}
    assert cov["occurrences"]["uncertain"] == 2
    assert cov["occurrences"]["covered"] == 0
    assert cov["labels_uncertain_only"] == ["3"]


def test_an_uncited_label_is_uncovered_not_uncertain(tmp_path):
    """No claim mentioned [3] at all, so nothing is in doubt — it was simply
    missed, which is the finding the audit exists to report."""
    cov = _cov(tmp_path, TWO_PLACES, [])
    assert {o["status"] for o in cov["occurrences"]["items"]} == {"uncovered"}
    assert cov["missing"] == ["3"]


def test_a_partly_placed_label_reports_both_states(tmp_path):
    """One claim names one of the two places; a second claim cites the label
    and names nothing. The named place is covered, the other is uncertain —
    not uncovered, because a claim did reach it."""
    named = ClaimResult(id=1, claim="a", location="Results", refs=["3"],
                        ctx_ids=["block_0001:46:3"])
    vague = ClaimResult(id=2, claim="b", location="Results", refs=["3"], ctx_ids=[])
    cov = _cov(tmp_path, TWO_PLACES, [named, vague])
    by_id = {o["id"]: o["status"] for o in cov["occurrences"]["items"]}
    assert by_id["block_0001:46:3"] == "covered"
    assert by_id["block_0002:44:3"] == "uncertain"
    assert cov["labels_partially_covered"] == ["3"]


def test_a_claim_that_named_nothing_is_reported_as_unattributed(tmp_path):
    c = ClaimResult(id=7, claim="c", location="Results", refs=["3"], ctx_ids=[])
    cov = _cov(tmp_path, TWO_PLACES, [c])
    assert cov["attribution"]["claims_unattributed"] == [{"claim_id": 7, "label": "3"}]


# --- the label-level contract is byte-for-byte unchanged ------------------


def test_the_label_level_keys_keep_their_meaning(tmp_path):
    """CLAUDE.md pins these three: `evals/align.py` reads `missing` as a list of
    label strings to decide whether an unmatched gold case is the tool's fault
    or the harness's. Reshaping them would move that blame with no test going
    red."""
    c = ClaimResult(id=1, claim="c", location="Results", refs=["3"],
                    ctx_ids=["block_0001:46:3"])
    cov = _cov(tmp_path, TWO_PLACES, [c])
    assert cov["labels_in_text"] == ["3"]
    assert cov["covered"] == ["3"]
    assert cov["missing"] == []
    assert all(isinstance(x, str) for x in cov["missing"] + cov["covered"])


# --- what is gone ---------------------------------------------------------


def test_the_similarity_matcher_and_its_thresholds_are_gone():
    """The deletion this change is for. Leaving them behind as dead code would
    keep two answers to "which occurrence" in the tree, and the report's
    attribution caveat would still be describing one of them."""
    for name in ("_attribute_label", "_normalize_for_match", "_ratio",
                 "_location_matches", "OCCURRENCE_MIN_RATIO", "OCCURRENCE_MIN_MARGIN"):
        assert not hasattr(check_mod, name), f"{name} survived the redesign"


def test_the_audit_no_longer_advertises_a_similarity_threshold(tmp_path):
    cov = _cov(tmp_path, TWO_PLACES, [])
    assert "min_ratio" not in cov["attribution"]
    assert "min_margin" not in cov["attribution"]
    assert "similarity" not in cov["attribution"]["method"]
    assert cov["schema"] == "coverage/3"


# --- the wire format ------------------------------------------------------


def test_ctx_ids_round_trip_and_validate(tmp_path):
    import jsonschema

    r = RunResults(manuscript="m.pdf", claims=[
        ClaimResult(id=1, claim="c", location="Results", refs=["3"],
                    ctx_ids=["block_0001:46:3"], verdict="supported")])
    p = tmp_path / "results.json"
    r.to_json(p)
    jsonschema.validate(json.loads(p.read_text()), SCHEMA)
    assert RunResults.from_json(p).claims[0].ctx_ids == ["block_0001:46:3"]


def test_a_0_4_x_claim_without_ctx_ids_still_loads(tmp_path):
    p = tmp_path / "results.json"
    p.write_text(json.dumps({
        "manuscript": "m.pdf",
        "claims": [{"id": 1, "claim": "c", "location": "Results", "refs": ["3"],
                    "verdict": "supported"}],
    }))
    assert RunResults.from_json(p).claims[0].ctx_ids == []


# --- gate 4: the new failure paths degrade honestly -----------------------


def test_a_manuscript_with_no_bracketed_citations_still_extracts(tmp_path, monkeypatch):
    """The inventory can legitimately be empty — an author-year paper, or a
    style the detector cannot see. Extraction must still run (Task 2, the
    uncited register, does not depend on citations existing) and the prompt must
    say the list is empty rather than trailing off after a heading."""
    seen = {}

    def fake(p, m=None):
        seen["p"] = p
        return json.dumps({"cited": [], "uncited": [{"id": 1, "claim": "x", "location": "Intro"}]})

    monkeypatch.setattr(check_mod, "_ask", fake)
    blocks = [_block("block_0001", "Prevalence is high (Smith 2020).")]
    cited, uncited = check_mod.extract_claims(_case(tmp_path, blocks))

    assert cited == []
    assert len(uncited) == 1
    assert "(none found)" in seen["p"], "an empty inventory must say so"


def test_a_case_with_no_ingest_artifacts_does_not_crash(tmp_path, monkeypatch):
    """`citation_occurrences` returns `("none")` when there is neither a source
    map nor clean.md. Extraction still has a manuscript to read."""
    d = tmp_path / "ingest" / "manuscript"
    d.mkdir(parents=True)
    (d / "annotated.md").write_text("Some prose with no citations.")
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": [], "uncited": []}))
    assert check_mod.extract_claims(tmp_path) == ([], [])


def test_a_truncated_inventory_is_recorded_and_disclosed(tmp_path, monkeypatch):
    """Contexts past the cut are never offered to the model, so nothing can be
    attributed to them and they can only come back `uncovered` — a coverage
    figure depressed by a limit rather than by the paper. That is exactly the
    class of thing this codebase discloses rather than absorbing."""
    from papertrace.check import Truncations

    monkeypatch.setattr(check_mod, "CONTEXT_CHAR_LIMIT", 80)
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": [], "uncited": []}))
    blocks = [_block(f"block_{i:04d}", f"Finding number {i} was reported [{i + 1}].")
              for i in range(1, 8)]
    t = Truncations()
    check_mod.extract_claims(_case(tmp_path, blocks), truncations=t)

    assert "citation contexts" in t.report(), "the inventory cut was not recorded"
    assert t.report()["citation contexts"]["limit"] == 80


def test_the_truncated_inventory_cut_reaches_the_report(tmp_path):
    """A recorded cut nobody renders is not a disclosure. `_truncation` names
    every key it was given, so this only has to prove the key travels."""
    from papertrace.disclosures import TRUNCATION_TOKEN, run_disclosures

    r = RunResults(manuscript="m.pdf",
                   truncated={"citation contexts": {"chars": 900, "limit": 80}})
    d = next(x for x in run_disclosures(r, None) if x.key == "truncation")
    assert TRUNCATION_TOKEN in d.text
    assert "citation contexts" in d.text
