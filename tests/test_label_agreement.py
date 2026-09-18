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
    corroborating_readings,
    label_agreement,
    stamp_seen_in,
    uncomparable_labels,
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
    # `None`, not `False`: the field's own description has always said absent
    # means never computed, and as a plain boolean it could not say so — the
    # same three-state discipline `reflist_entries_proposed` is written under
    assert older.numbering_corroborated is None
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

    NEITHER entry carries a DOI or a single title token, so nothing comparable
    was said by anybody — see
    `test_a_label_every_voter_is_mute_about_is_disputed_not_single` for the
    ruling that keeps this `disputed` now that a cannot-tell voter abstains.
    """
    a = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    b = RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")
    assert label_agreement({"parsed": [a], "pymupdf": [b]}, {"7"}) == {"7": "disputed"}


def test_one_garbled_reading_cannot_corroborate_a_legible_one():
    """Broader than it looks: the benefit of the doubt applied when **either**
    side yielded no tokens, not only when both did. So a real reference paired
    with an unreadable one was `agreed`, and `agreed` is the single state that
    lets a verdict through untouched.

    RE-RULED at the whole-branch review: the answer is `single`, not
    `disputed`. The intent of this test — a garbled reading must not
    CORROBORATE a legible one — is preserved by abstention, which is what
    `single` records: one reading spoke, nothing agreed with it, nothing
    contradicted it. Counting the garbled reading as dissent instead withheld
    the verdict on a label nothing had said anything against, and on a
    bare-DOI Crossref deposit it withheld every verdict in the run.
    """
    good = RefEntry(
        num="7",
        raw="Fujita S (2023) Characterization of brain volume changes in aging individuals. "
            "JAMA Netw Open.",
    )
    garbled = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    assert label_agreement({"parsed": [good], "llm": [garbled]}, {"7"}) == {"7": "single"}
    assert corroborating_readings({"parsed": [good], "llm": [garbled]}, {"7"}) == []


# --- "cannot compare" is a third answer, and it abstains --------------------


def _bare_doi(num: str, doi: str) -> RefEntry:
    """A Crossref deposit of a DOI and nothing else — `_reference_raw`'s
    documented fallback, and 49 of 52 references on the Wiley paper that
    prompted this. It yields no title tokens at all, permanently, by the
    deposit's shape."""
    return RefEntry(num=num, raw=f"https://doi.org/{doi}", doi=doi)


def _printed(num: str) -> RefEntry:
    """One entry as a reference list that prints no DOIs carries it — normal
    for many journals, and the other half of Critical 1's shape: the deposit
    has a DOI and nothing else, the page has everything but."""
    return _entry(
        num,
        f"Fujita S, Mori S. Characterization of brain volume changes, entry {num}. "
        "JAMA Netw Open. 2023;6(6).",
    )


def test_comparably_same_has_a_third_answer_for_nothing_to_compare():
    """`None`, the same three-valued vocabulary `models.titles_match`
    publishes for the same reason. `False` here is a claim about the two
    papers being different, and a bare-DOI deposit is not evidence of that."""
    assert _comparably_same(_bare_doi("7", "10.1002/hep.31884"), _printed("7")) is None
    assert _comparably_same(_work("7", "10.1000/x7"), _work("7", "10.1000/x7")) is True
    assert _comparably_same(_work("7", "10.1000/x7"), _work("7", "10.1000/x9")) is False


def test_a_bare_doi_deposit_does_not_withhold_a_label_the_parses_agree_on():
    """Critical 1 of the whole-branch review, reproduced.

    A publisher deposits bare DOIs; the printed list prints none. The deposit
    can be compared with neither parse, but the two parses agree with each
    other perfectly. Counting the uncomparable voter as dissent made every
    cited label `disputed`, every source withheld and every claim
    `unchecked` — an audit with no verdicts in it, on a paper where nothing
    was wrong."""
    candidates = {
        "crossref": [_bare_doi("7", "10.1002/hep.31884")],
        "parsed": [_printed("7")],
        "pymupdf": [_printed("7")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "agreed"}
    # and the voter nobody could compare is not named as having agreed
    assert corroborating_readings(candidates, {"7"}) == ["parsed", "pymupdf"]


def test_dropping_the_uncomparable_voter_can_leave_one_and_that_prints():
    """The second-order effect of the abstention rule, pinned deliberately.

    On a `--backend pymupdf` run there is no flat reading, so the deposit and
    the parse are the only two voters and the deposit cannot be compared.
    One comparable voter remains, which is `single`: no corroboration is
    claimed and no verdict is withheld. That is the state a one-reading run
    has always been in, and it is what the pre-branch behaviour was — the
    laundering this branch removed is `agreed`, which is what would claim the
    deposit backed the parse."""
    candidates = {
        "crossref": [_bare_doi("7", "10.1002/hep.31884")],
        "parsed": [_printed("7")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "single"}
    assert corroborating_readings(candidates, {"7"}) == []


def test_a_label_every_voter_is_mute_about_is_disputed_not_single():
    """The cell the abstention rule does not cover, ruled at the review.

    Two readings carry [7] and NEITHER says anything a comparison could use.
    `absent` is false (two readings carry it), `single` is false (no reading
    spoke comparably), `agreed` is the laundering. `disputed` is the true one:
    nothing establishes that these name one paper."""
    a = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    b = RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")
    assert label_agreement({"parsed": [a], "pymupdf": [b]}, {"7"}) == {"7": "disputed"}


def test_a_label_no_voter_carries_at_all_is_absent_not_disputed():
    """One character apart from the cell above and opposite in meaning: zero
    voters is `absent` (nobody read it), zero COMPARABLE voters with the label
    carried is `disputed` (it was read and nothing could be compared)."""
    a = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    b = RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")
    assert label_agreement({"parsed": [a], "pymupdf": [b]}, {"9"}) == {"9": "absent"}


# --- the model's reading may dispute, and that is all it may do -------------


def test_the_model_reading_never_corroborates_what_it_copied_out_of():
    """Major 1. `reflist.propose` may only COPY from the two extractions, and
    both of those are voters in their own right — so the model agreeing with
    one of them is one text read twice, not two readings agreeing. Here the
    flat parse lost [7] entirely and the model echoed it back out of text A:
    counting that as a second reading turned `numbering_corroborated` True and
    printed "two readings of the reference list agree" over one text."""
    e = _work("7", "10.1000/x7")
    candidates = {"parsed": [e], "pymupdf": [], "llm": [_work("7", "10.1000/x7")]}
    assert label_agreement(candidates, {"7"}) == {"7": "single"}
    assert corroborating_readings(candidates, {"7"}) == []


def test_the_model_reading_can_still_dispute_a_label():
    """The other half of Major 1's ruling, and the only power the safety
    property grants the model reading: it may disagree. Excluding it from
    corroboration must not also excuse it from the comparison."""
    candidates = {
        "parsed": [_work("7", "10.1000/x7")],
        "pymupdf": [_work("7", "10.1000/x7")],
        "llm": [_work("7", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}


def test_the_model_reading_is_not_named_beside_two_real_readings_that_agree():
    """Three voters all agreeing: the two that read the page are named, the
    one that copied out of them is not. The printed count is then 2, which is
    what `NUMBERING_CORROBORATION_TOKEN` asserts."""
    candidates = {
        "parsed": [_work("7", "10.1000/x7")],
        "pymupdf": [_work("7", "10.1000/x7")],
        "llm": [_work("7", "10.1000/x7")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "agreed"}
    assert corroborating_readings(candidates, {"7"}) == ["parsed", "pymupdf"]


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
    # `None`, not `False`: the two functions are siblings answering opposite
    # questions, and each says "cannot tell" in its own caller's safe
    # direction — `_same_work` as True, `_comparably_same` as a third answer
    assert _comparably_same(a, b) is None


# --- seen_in: which readings also carried a chosen entry's work ------------


def test_an_entry_only_the_model_found_is_spottable_in_the_manifest():
    """The published field's whole purpose. An entry no deterministic reading
    carried is the one a reader most needs to see flagged, and `seen_in ==
    ["llm"]` is how they see it."""
    chosen = [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")]
    candidates = {
        "parsed": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "llm": [_work("1", "10.1000/x1")],
    }
    stamp_seen_in(chosen, candidates)
    assert chosen[0].seen_in == ["llm", "parsed"]
    assert chosen[1].seen_in == ["parsed"]


def test_a_reading_naming_a_different_paper_under_the_same_label_is_not_provenance():
    """Sharing a numeral is not agreeing about a work.

    Stamping on the label alone would record the disagreeing reading as having
    corroborated this entry — the extent-for-content substitution `_covers` is
    documented as blind to, reintroduced in a published field."""
    chosen = [_work("5", "10.1000/x5")]
    candidates = {"parsed": [_work("5", "10.1000/x5")],
                  "pymupdf": [_work("5", "10.1000/x99")]}
    stamp_seen_in(chosen, candidates)
    assert chosen[0].seen_in == ["parsed"]


def test_seen_in_never_names_a_reading_label_agreement_says_disagrees():
    """Major 3. `stamp_seen_in` matched through the lenient `_same_work`,
    which gives the benefit of the doubt when either side yields no title
    tokens — so one manifest published, for one label in one run, both "two
    readings contributed this entry" (`seen_in`) and "the readings do not
    agree about it" (`labels_disputed`). Two published fields contradicting
    each other. The comparator here is now the same one the agreement axis
    uses."""
    chosen = [RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")]
    other = RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")
    candidates = {"parsed": chosen, "pymupdf": [other]}
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}
    stamp_seen_in(chosen, candidates)
    assert chosen[0].seen_in == ["parsed"]


def test_a_chosen_entry_is_always_credited_to_the_reading_it_came_from():
    """Identity, not comparison: the chosen list IS one of the readings'
    lists, so an entry nothing could be compared against still records where
    it came from. Without this, a garbled entry's `seen_in` would be `[]` —
    which the schema reads as "no reading was established as carrying this" —
    for a reading that demonstrably carried it."""
    garbled = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    chosen = [garbled]
    stamp_seen_in(chosen, {"parsed": [garbled], "pymupdf": []})
    assert chosen[0].seen_in == ["parsed"]


def test_stamping_twice_never_erases_a_provenance_an_earlier_pass_recorded():
    """`cli` stamps AFTER the escalation, so an entry substituted by
    `reflist.resolve_disputed` already carries the reading its values were
    copied from. That name is a fact this pass cannot recompute — the
    readings that disputed the label carry no entry matching the resolved
    one — so it is merged, never overwritten."""
    resolved = RefEntry(num="7", raw="A resolved title.", seen_in=["pymupdf"])
    stamp_seen_in([resolved], {"parsed": [], "pymupdf": []})
    assert resolved.seen_in == ["pymupdf"]


# --- corroborating_readings: named only where a reading actually voted -----
# (N3 of the Task 6 re-review: `any(e.num in body for e in cand)` — the filter
# that shipped in round 1 — named a reading that carried only SOME cited
# labels, or whose only carrier of one was `boundary_ambiguous`, as having
# corroborated ALL of them.)


def test_corroborating_readings_excludes_a_reading_that_voted_on_only_some_labels():
    """A reading that skipped an entry corroborated the ones it carried, not
    the ones it did not. `llm` here votes on [1] and never mentions [2] at
    all — both cited labels still come back `agreed` (crossref and parsed
    carry [2] between them), so the round-1 filter named `llm` as one of the
    readings that agreed on `every cited label`, which it never spoke to."""
    body = {"1", "2"}
    candidates = {
        "parsed": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "crossref": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "llm": [_work("1", "10.1000/x1")],
    }
    assert label_agreement(candidates, body) == {"1": "agreed", "2": "agreed"}
    assert corroborating_readings(candidates, body) == ["crossref", "parsed"]


def test_corroborating_readings_excludes_a_reading_whose_only_carrier_is_boundary_ambiguous():
    """A `boundary_ambiguous` carrier casts no vote at all (invariant 5 of
    `label_agreement`) — sharing that fact with `corroborating_readings` is
    the point of factoring `_label_voters` out, rather than a coincidence two
    unrelated filters happen to agree on.

    The two readings that DO vote here are `crossref` and `pymupdf`. This
    fixture used to name `llm` as the second voter, which since Major 1 is
    excluded from corroboration for a different reason entirely — so the test
    would have gone green through a rule it was never about. Two independent
    exclusions must not be tested by one fixture."""
    ambiguous_1 = _work("1", "10.1000/x1")
    ambiguous_1.boundary_ambiguous = True
    ambiguous_2 = _work("2", "10.1000/x2")
    ambiguous_2.boundary_ambiguous = True
    body = {"1", "2"}
    candidates = {
        "parsed": [ambiguous_1, ambiguous_2],
        "crossref": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "pymupdf": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
    }
    assert label_agreement(candidates, body) == {"1": "agreed", "2": "agreed"}
    assert corroborating_readings(candidates, body) == ["crossref", "pymupdf"]


def test_corroborating_readings_is_empty_unless_every_cited_label_agreed():
    """The caller (`cli._refs_pipeline`) also gates on `all(... == "agreed"
    ...)`, but this function must not rely on that: naming any reading at all
    when one cited label is disputed would still be the overstatement."""
    body = {"1", "2"}
    candidates = {
        "parsed": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "llm": [_work("1", "10.1000/x1"), _work("2", "10.1000/x9")],
    }
    assert corroborating_readings(candidates, body) == []


# --- fix round 2: a DOI-only deposit abstains on EVERY DOI shape -----------


def test_a_doi_only_deposit_abstains_whatever_its_doi_tokenises_to():
    """Critical 1, reopened and closed properly.

    `_reference_raw`'s fallback is the DOI string itself — bare, not a URL —
    and `[a-z]{5,}` lifts a journal slug out of most publisher DOIs. Only the
    third shape here has no five-letter run, and it was the only one the
    first fix reached: the other two came back `False` and disputed every
    label they voted on, which is the audit-with-no-verdicts outcome the
    finding named.
    """
    page = _printed("7")
    for doi in ("10.1148/radiol.2019181432",          # tokenised to {'radiol'}
                "10.1001/jamanetworkopen.2023.18153",  # {'jamanetworkopen'}
                "10.1038/s41591-019-0673-2",           # set()
                "10.1016/j.neuroimage.2020.117161",    # {'neuroimage'}
                "10.1093/bioinformatics/btaa123"):     # {'bioinformatics'}
        deposit = RefEntry(num="7", raw=doi, doi=doi)
        assert _comparably_same(deposit, page) is None, doi
        assert label_agreement({"crossref": [deposit], "parsed": [page]}, {"7"}) == {
            "7": "single"
        }, doi
        assert label_agreement(
            {"crossref": [deposit], "parsed": [page], "pymupdf": [_printed("7")]}, {"7"}
        ) == {"7": "agreed"}, doi


def test_two_doi_only_deposits_still_compare_by_doi_and_never_reach_the_mute_cell():
    """The ordering the `_comparably_same` branches rest on, re-checked under
    the corrected tokeniser: with DOIs stripped, two deposits have no tokens
    on either side — but they never get that far, because the DOI comparison
    comes first. Only an entry with NEITHER a DOI nor a title word is mute."""
    a = RefEntry(num="7", raw="10.1148/radiol.2019181432", doi="10.1148/radiol.2019181432")
    b_same = RefEntry(num="7", raw="10.1148/radiol.2019181432", doi="10.1148/radiol.2019181432")
    b_other = RefEntry(num="7", raw="10.1038/nature12373", doi="10.1038/nature12373")

    assert _comparably_same(a, b_same) is True
    assert _comparably_same(a, b_other) is False
    assert label_agreement({"crossref": [a], "parsed": [b_same]}, {"7"}) == {"7": "agreed"}
    assert label_agreement({"crossref": [a], "parsed": [b_other]}, {"7"}) == {"7": "disputed"}
    # and the mute cell still needs an entry carrying neither
    mute = RefEntry(num="7", raw="A. B. 2020. 14(3):1-9.")
    assert label_agreement(
        {"parsed": [mute], "pymupdf": [RefEntry(num="7", raw="Q. Z. 2020. 88(1):4-7.")]}, {"7"}
    ) == {"7": "disputed"}


# --- fix round 2: which of the two causes put a label in dispute -----------


def test_uncomparable_labels_names_only_the_labels_nothing_could_compare():
    """`disputed` has two causes and they warrant different reader actions:
    a contradiction means one reading is wrong and a human should look;
    nothing comparable means no conflict is known and the withholding is
    precautionary. [1] is a contradiction, [2] is uncomparable, [3] agrees."""
    body = {"1", "2", "3"}
    candidates = {
        "parsed": [_work("1", "10.1000/x1"),
                   RefEntry(num="2", raw="A. B. 2020. 14(3):1-9."),
                   _work("3", "10.1000/x3")],
        "pymupdf": [_work("1", "10.1000/x9"),
                    RefEntry(num="2", raw="Q. Z. 2020. 88(1):4-7."),
                    _work("3", "10.1000/x3")],
    }
    assert label_agreement(candidates, body) == {
        "1": "disputed", "2": "disputed", "3": "agreed",
    }
    assert uncomparable_labels(candidates, body) == ["2"]


def test_uncomparable_labels_is_always_a_subset_of_the_disputed_ones():
    """The invariant, pinned rather than assumed. Major 3 on this branch was
    exactly two published fields disagreeing on one manifest, and this pair
    is the next candidate: both come out of `_label_state`, which is the one
    place the rule lives, and a label can only be uncomparable by way of
    being disputed."""
    body = {"1", "2", "3", "4"}
    candidates = {
        "parsed": [_work("1", "10.1000/x1"),
                   RefEntry(num="2", raw="A. B. 2020. 14(3):1-9."),
                   _work("3", "10.1000/x3"), _work("4", "10.1000/x4")],
        "pymupdf": [_work("1", "10.1000/x9"),
                    RefEntry(num="2", raw="Q. Z. 2020. 88(1):4-7."),
                    _work("3", "10.1000/x3"), _work("4", "10.1000/x4"),
                    _work("4", "10.1000/x44")],
    }
    agreement = label_agreement(candidates, body)
    disputed = {lbl for lbl, state in agreement.items() if state == "disputed"}
    assert disputed == {"1", "2", "4"}, agreement
    assert set(uncomparable_labels(candidates, body)) <= disputed


def test_a_duplicate_is_disputed_for_its_own_reason_not_an_uncomparable_one():
    """A reading carrying the label twice is judgeable — two papers, one of
    which would really be downloaded — which is a different finding from
    nothing being comparable, and must not be filed under it."""
    candidates = {
        "parsed": [_work("7", "10.1000/x7"), _work("7", "10.1000/x77")],
        "pymupdf": [_work("7", "10.1000/x7")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}
    assert uncomparable_labels(candidates, {"7"}) == []
