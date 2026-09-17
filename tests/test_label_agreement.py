"""Per cited label: do the readings that carry it still name one paper?

`label_agreement` is the second, independent axis `reconcile` gains in this
plan — see `.superpowers/facts/reconcile.md` — and it is deliberately not
built on `_first_divergence`, which zips two readings by POSITION. A reading
that split one entry into two shifts every label after it by one, so
comparing "reading A's 6th entry" against "reading B's 6th entry" would report
a disagreement about the wrong label, or an agreement that is really two
different labels lining up by coincidence. The join key here is `e.num`, the
printed numeral — the same fact `parse_references` already treats as ground
truth wherever the converter left it legible.

Nothing in `src/` calls `label_agreement` yet. This module tests it alone, the
way `tests/test_reflist.py` will later test `reflist.py` alone — the design's
own promise is that the deterministic parts are testable without a model, and
this is the part with no model in it at all.
"""

import json
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import (  # noqa: E402
    DOCUMENT_KINDS,
    REF_STATUSES,
    RefEntry,
    RefManifest,
)
from papertrace.refs import (  # noqa: E402
    LABEL_AGREEMENT,
    _comparably_same,
    _entry,
    _same_work,
    label_agreement,
)

ROOT = Path(__file__).resolve().parent.parent


def _work(num: str, doi: str) -> RefEntry:
    """Built through `_entry` — the production path, which parses the DOI out
    of `raw` the same way a real reading would, rather than setting it by
    hand and bypassing the exact code every candidate actually goes through."""
    return _entry(num, f"Author for entry {num}. A distinctive title. doi:{doi}")


# --- the five states, one rule each ----------------------------------------


def test_two_readings_naming_the_same_work_at_a_label_agree():
    """The base case a corroborating second reading exists to produce."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x5")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "agreed"}


def test_two_readings_naming_different_works_at_a_label_are_disputed():
    """The failure this whole feature exists to catch: two readings of one
    bibliography naming different papers at the same printed label."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "disputed"}


def test_three_readings_two_agreeing_and_one_not_is_disputed_not_majority():
    """`reconcile`'s own rule, restated for this function: 'a non-unique
    match is refused, never ranked.' Two votes for one paper and one for
    another must not out-vote the dissent into a false `agreed` — if this
    regresses to a majority rule, it will look like an improvement (fewer
    labels withheld) right up until it prints a verdict against the paper the
    minority reading was right about."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x5")],
        "llm": [_work("5", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "disputed"}


def test_one_reading_carrying_a_label_is_single():
    """`single` must print (invariant 4): a pymupdf-backend run whose only
    other candidate was discarded still has ONE reading, and every one of its
    labels is `single` — not `absent`, which would say nobody read it at all,
    and not `disputed`, which would withhold a verdict nothing contradicts."""
    candidates = {"parsed": [_work("5", "10.1000/x5")]}
    assert label_agreement(candidates, {"5"}) == {"5": "single"}


def test_no_reading_carrying_a_label_is_absent():
    """Already `not_retrieved` downstream — `absent` just names the reason: no
    candidate reading has anything to say about this label at all."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x5")],
    }
    assert label_agreement(candidates, {"9"}) == {"9": "absent"}


# --- the two entries that must not speak for their label --------------------


def test_a_boundary_ambiguous_entry_does_not_corroborate_a_matching_reading():
    """Invariant 5: a `boundary_ambiguous` entry has already refused to say
    what paper its label names — `parse_references` sets it precisely because
    the entry's start is a guess. Counting it as a second vote would turn that
    refusal into evidence. Here it is given the SAME doi as the one real
    voter, so a regression that let it speak would wrongly report `agreed`
    instead of the correct `single`."""
    ambiguous = _work("5", "10.1000/x5")
    ambiguous.boundary_ambiguous = True
    candidates = {
        "parsed": [ambiguous],
        "pymupdf": [_work("5", "10.1000/x5")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "single"}


def test_a_reading_carrying_the_same_label_twice_disputes_it_on_its_own():
    """A duplicate is judgeable, so it is `disputed` with no second opinion.

    This is the discriminating case: one reading is clean and agrees with
    nothing else, one reading carries [7] twice with two different DOIs, and
    there is no third reading to break anything. Under a "a duplicate simply
    does not vote" rule this comes back `single` and prints — meaning a verdict
    on [7] rests on whichever of the two papers `resolve_entry` happened to
    download. It must be `disputed`.
    """
    candidates = {
        "parsed": [_work("7", "10.1000/x7"), _work("7", "10.1000/x77")],
        "pymupdf": [_work("7", "10.1000/x7")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}


def test_a_duplicate_does_not_hide_a_disagreement_between_the_other_readings():
    """The same rule with two other readings that genuinely disagree, so the
    result is `disputed` for two independent reasons at once. Kept alongside the
    test above because that one pins the rule and this one pins that the rule
    does not accidentally short-circuit the pairwise comparison."""
    candidates = {
        "parsed": [_work("7", "10.1000/x7")],
        "pymupdf": [_work("7", "10.1000/x7"), _work("7", "10.1000/x77")],
        "llm": [_work("7", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}


def test_a_refused_entry_can_leave_a_label_single_and_that_is_deliberate():
    """The other half of the asymmetry, pinned so nobody "fixes" it.

    A `boundary_ambiguous` entry does not vote, so one clean reading leaves the
    label `single`, which prints. That is safe because `resolve_entry` honours
    `boundary_ambiguous` by setting `no_doi` and fetching nothing — the refused
    entry cannot be the source of any verdict, whichever way this function
    labels it. A duplicate has no such brake, which is why the two cases are
    treated differently."""
    refused = _work("7", "10.1000/x7")
    refused.boundary_ambiguous = True
    candidates = {"parsed": [refused], "pymupdf": [_work("7", "10.1000/x7")]}
    assert label_agreement(candidates, {"7"}) == {"7": "single"}


# --- the case this plan exists for ------------------------------------------


def test_the_real_manuscripts_shape_agrees_up_to_the_split_and_disputes_after():
    """The reported defect, reproduced without a model or a converter. A
    correct 22-entry reading and a 24-entry reading whose [12] is a
    split-title fragment: everything from [13] on is shifted one label late,
    so the label and the paper it names have come apart. [1]-[11] sit before
    the split and must still agree; [13] on must all be disputed, because the
    printed numeral now names a different work than it did in the correct
    reading. This is the six-wrong-verdicts case from
    `docs/superpowers/specs/2026-09-16-llm-reference-list-design.md`."""
    correct = [_work(str(n), f"10.1000/x{n}") for n in range(1, 23)]

    # the second reading: 1-11 intact, 12 a fragment (excluded from every
    # assertion below), 13-23 each carrying the DOI that belongs one label
    # earlier in `correct`, 24 an extra nobody cites
    split = (
        [_work(str(n), f"10.1000/x{n}") for n in range(1, 12)]
        + [_entry("12", "an unresolved title fragment with no doi of its own")]
        + [_work(str(n), f"10.1000/x{n - 1}") for n in range(13, 24)]
        + [_work("24", "10.1000/x99")]
    )

    body = {str(n) for n in range(1, 23)}
    agreement = label_agreement({"parsed": correct, "pymupdf": split}, body)

    for n in range(1, 12):
        assert agreement[str(n)] == "agreed", agreement
    for n in range(13, 23):
        assert agreement[str(n)] == "disputed", agreement


# --- the vocabulary, and the one wire-format check it does not have yet -----


def test_label_agreement_never_prints_outside_its_own_published_vocabulary():
    """`LABEL_AGREEMENT` is published so a future consumer — the withholding
    filter, the disclosure — has something to check membership against. If
    this function ever returned a fifth word, that check would silently pass
    it through as if it were one of the four this plan specified."""
    candidates = {
        "parsed": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "pymupdf": [_work("1", "10.1000/x1"), _work("2", "10.1000/x9")],
    }
    for state in label_agreement(candidates, {"1", "2", "3"}).values():
        assert state in LABEL_AGREEMENT


def test_ref_statuses_matches_its_own_schema_enum():
    """`tests/test_coverage.py` asserts `set(VERDICTS) == set(schema_enum)`
    for `results.schema.json`, but nothing did the equivalent for
    `REF_STATUSES` against `refs_manifest.schema.json` — the exact gap this
    plan's own facts file names. Modelled on that test's idiom, not a new
    one."""
    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    enum = schema["properties"]["entries"]["items"]["properties"]["status"]["enum"]
    assert set(REF_STATUSES) == set(enum)


def test_document_kinds_matches_its_own_schema_enum():
    """The existing check compares the schema against a hardcoded literal list
    rather than against `DOCUMENT_KINDS`, so adding a kind to the constant and
    to the schema while forgetting the literal leaves a green suite. Compare the
    constant, which is the thing `SourceJudgement.kind` is validated against."""
    schema = json.loads((ROOT / "schemas" / "results.schema.json").read_text())
    enum = (
        schema["properties"]["claims"]["items"]["properties"]["judgements"]
        ["items"]["properties"]["kind"]["enum"]
    )
    assert set(DOCUMENT_KINDS) == set(enum)


def test_the_title_check_vocabulary_matches_its_own_schema_enum():
    """`title_check` is the third undocumented vocabulary the facts file names.
    Its schema enum carries `null` as a real state — "too few words to tell" is
    not `mismatch` — so the comparison drops the null and compares the strings,
    and this docstring is where that asymmetry is recorded."""
    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    enum = schema["properties"]["entries"]["items"]["properties"]["title_check"]["enum"]
    assert None in enum, "null is a real title_check state and must stay in the enum"
    assert {e for e in enum if e is not None} == {"verified", "unverifiable", "mismatch"}


# --- Gate 2: the new persisted fields round-trip, and old manifests still load


def test_the_six_new_reconciliation_fields_round_trip_through_a_manifest(tmp_path):
    """Gate 2. `Reconciliation` itself is never serialised — its fields reach
    disk only via the `RefManifest` fields `cli._refs_pipeline` copies them
    into (Task 6's job). This test exercises that copy shape directly, against
    the schema, so the round trip is proven before anything wires it in."""
    m = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="x", status="paywalled")],
        numbering_corroborated=True,
        corroborating_readings=["parsed", "pymupdf"],
        labels_disputed=["13", "14"],
        labels_resolved=["13"],
        numbering_choice="llm_resolved",
        numbering_chosen_by="user",
        reflist_model="claude-opus-5",
        reflist_fields_discarded=["title"],
    )
    p = tmp_path / "refs_manifest.json"
    m.to_json(p)

    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(json.loads(p.read_text()), schema)

    back = RefManifest.from_json(p)
    assert back.numbering_corroborated is True
    assert back.corroborating_readings == ["parsed", "pymupdf"]
    assert back.labels_disputed == ["13", "14"]
    assert back.labels_resolved == ["13"]
    assert back.numbering_choice == "llm_resolved"
    assert back.numbering_chosen_by == "user"
    assert back.reflist_model == "claude-opus-5"
    assert back.reflist_fields_discarded == ["title"]

    # a manifest written before this feature carries none of these keys
    old = {"manuscript": "m.pdf", "entries": [{"num": "1", "raw": "x", "status": "paywalled"}]}
    p.write_text(json.dumps(old))
    older = RefManifest.from_json(p)
    assert older.numbering_corroborated is False
    assert older.corroborating_readings == []
    assert older.labels_disputed == []
    assert older.labels_resolved == []
    assert older.numbering_choice == ""
    assert older.numbering_chosen_by == ""
    assert older.reflist_model == ""
    assert older.reflist_fields_discarded == []


def test_seen_in_round_trips_on_an_entry_and_defaults_empty_on_an_older_one(tmp_path):
    """Gate 2. An entry seen only in the model's reading has to be spottable —
    `seen_in == ["llm"]` is what makes that visible on a real manifest, and it
    must not silently become `[]` on the way through JSON."""
    e = RefEntry(num="1", raw="x", status="retrieved", seen_in=["parsed", "llm"])
    m = RefManifest(manuscript="m.pdf", entries=[e])
    p = tmp_path / "refs_manifest.json"
    m.to_json(p)

    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(json.loads(p.read_text()), schema)

    assert RefManifest.from_json(p).entries[0].seen_in == ["parsed", "llm"]

    old = {"manuscript": "m.pdf", "entries": [{"num": "1", "raw": "x", "status": "retrieved"}]}
    p.write_text(json.dumps(old))
    assert RefManifest.from_json(p).entries[0].seen_in == []


# --- agreement needs evidence, not the absence of counter-evidence ----------


def test_two_uncomparable_entries_are_disputed_rather_than_agreed():
    """The laundering this feature could otherwise perform.

    `_same_work` answers "is there evidence these differ?" and returns True
    when there is nothing to compare — correct for `_first_divergence`, which
    must not manufacture a divergence out of silence. A per-label vote asks the
    opposite question, so silence must not manufacture agreement either.
    Measured before `_comparably_same` existed: these two came back `agreed`.
    """
    a = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    b = RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")
    assert label_agreement({"parsed": [a], "pymupdf": [b]}, {"7"}) == {"7": "disputed"}


def test_one_garbled_reading_cannot_corroborate_a_legible_one():
    """Broader than it looks: the benefit of the doubt applied when **either**
    side yielded no tokens, not only when both did. So a real reference paired
    with an unreadable one was `agreed`, and `agreed` is the single state that
    lets a verdict through untouched."""
    good = RefEntry(
        num="7",
        raw="Fujita S (2023) Characterization of brain volume changes in aging individuals. "
            "JAMA Netw Open.",
    )
    garbled = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    assert label_agreement({"parsed": [good], "llm": [garbled]}, {"7"}) == {"7": "disputed"}


def test_two_doi_less_readings_that_do_share_a_title_still_agree():
    """The tightening must not go so far that a DOI-less bibliography can never
    corroborate itself. Some publishers print no DOIs in a reference list, and
    title-token overlap is the only evidence available there — refusing it would
    make every label on such a paper `disputed` and the audit worthless.

    The two fixtures overlap **partially** (ratio 0.4, above `_same_work`'s 0.34
    floor), not identically. An identical pair would exercise the overlap
    arithmetic trivially and would still pass if the floor were raised almost to
    1.0, so it would guard the structural case and nothing else. These two
    differ the way two converters' readings of one entry actually differ —
    author list truncated on one side, `aging`/`ageing`, a subtitle read two
    ways — and share only `fujita` and `characterization`.
    """
    a = RefEntry(num="7", raw="Fujita S (2023) Characterization of brain volume changes in aging")
    b = RefEntry(
        num="7",
        raw="Fujita, Mori, Onda. Characterization of cerebral volume trajectories "
            "during healthy ageing",
    )
    assert label_agreement({"parsed": [a], "pymupdf": [b]}, {"7"}) == {"7": "agreed"}


def test_same_work_keeps_its_own_direction_for_its_own_callers():
    """`_same_work` is NOT changed. `reconcile` derives `contested` from
    `_first_divergence`, which derives it from `_same_work`, and that path needs
    "nothing to compare" to mean "no divergence found". Tightening the shared
    helper would have turned every unreadable entry into a reported divergence
    and re-tainted whole reports. Two functions, two directions, one test that
    says so."""
    a = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    b = RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")
    assert _same_work(a, b) is True
    assert _comparably_same(a, b) is False
