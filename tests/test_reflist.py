"""The model's reading of a bibliography, and every rule that disbelieves it.

One `claude -p` call is shown two extractions of the same printed reference list
and asked what numbered list the page carries. Nothing it replies is accepted on
its own word: each field is searched for, verbatim, in one of the two texts it
was shown. These tests are the rules, one per test, because each of them is a
way this module could otherwise name a paper the page never printed.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import reflist  # noqa: E402
from papertrace.refs import DOI_RE  # noqa: E402

# Reading A: the run backend's extraction. Reading B: the flat-text one. The
# same three references, printed the same way, with the damage each converter
# leaves behind.
READING_A = """References
1. Fujita S, Mori S, Onda K, et al. Characterization of brain volume changes in
aging individuals. JAMA Netw Open. 2023;6(6):e2318153. doi:10.1001/jamanetworkopen.2023.18153
2. Küstner T, Müller M. Longitudinal segmentation of the ageing cortex.
Nat Aging. 2020;4(11):1619-1634. doi:10.1038/s41591-019-0673-2
3. Wachinger C. Atlas-based methods. Med Image Anal. 2019;51:1-10.
"""

READING_B = """References
1 . Fujita S, Mori S, Onda K, et al. Characterization of brain volume changes in
aging individuals. JAMA Netw Open. 2023;6(6):e2318153.
2 . Kustner T, Muller M. Longitu-
dinal segmentation of the ageing cortex. Nat Aging. 2020;4(11):1619-1634.
3 . Wachinger C. Atlas-based methods. Med Image Anal. 2019;51:1-10.
"""


def _reply(monkeypatch, payload: str, *, model: str = "claude-opus-5"):
    """Patch the seam itself — `reflist` calls it by qualified name on purpose."""
    calls = []

    def fake(prompt, model_arg=None):
        calls.append({"prompt": prompt, "site": ask_mod._SITE})
        return payload

    monkeypatch.setattr(ask_mod, "_ask", fake)
    monkeypatch.setattr(ask_mod, "_MODELS", {ask_mod.SITE_REFS: model})
    return calls


def _propose(monkeypatch, payload: str, **kwargs):
    _reply(monkeypatch, payload)
    kwargs.setdefault("label_a", "docling 2.8.0")
    kwargs.setdefault("label_b", "pymupdf")
    return reflist.propose(READING_A, READING_B, **kwargs)


def test_a_field_not_found_verbatim_in_either_reading_is_discarded(monkeypatch):
    """The field goes, the entry stays.

    An invented journal name is a wrong citation string, not a wrong paper, and
    dropping the whole entry for it would throw away a title and a DOI that
    *were* printed. The gap is recorded so the report can name it.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"3","authors":"Wachinger C","year":"2019",
       "title":"Atlas-based methods","journal":"Nature Reviews Neurology","reading":"AB"}
    ]""")

    assert [e.num for e in entries] == ["3"]
    assert "Nature Reviews Neurology" not in entries[0].raw
    assert "Atlas-based methods" in entries[0].raw
    assert any("journal" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_title_not_found_verbatim_discards_the_whole_candidate(monkeypatch):
    """A title nobody printed is the one failure that cannot be localised.

    Every other field describes a paper; the title *is* the paper. If the model
    named a work the page does not carry, nothing else it said about that entry
    is worth keeping either — and a list one of whose entries names the wrong
    paper cannot be used as a reading of the list at all.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"3","authors":"Wachinger C","year":"2019",
       "title":"Deep learning for cortical parcellation","reading":"A"}
    ]""")

    assert entries == []
    assert "[3]" in prov.discarded_whole and "title" in prov.discarded_whole, prov.discarded_whole


def test_a_line_broken_doi_yields_no_doi_rather_than_a_repair(monkeypatch):
    """Ruling 3. The truncated prefix passes both gates it would have had.

    `DOI_RE` is `10\\.\\d{4,9}/[^\\s"'<>]+` and a trailing hyphen is a fine
    `[^\\s"'<>]`, so `10.1038/s41591-` matches it — and the verbatim check passes
    too, because that prefix really is printed on the page. A DOI is an identity:
    half of one resolves to nothing or to something else, and a repaired one
    would name whichever paper the guess landed on.
    """
    truncated = "10.1038/s41591-"
    # both gates the spec assumed would stop this, measured, on this fixture
    assert DOI_RE.fullmatch(truncated), "premise changed: DOI_RE now rejects the prefix"
    assert reflist._found("doi", truncated, (READING_A, READING_B)), (
        "premise changed: the prefix is no longer printed in the readings"
    )
    assert reflist._usable_doi(truncated) is None, "a half DOI was accepted"

    entries, prov = _propose(monkeypatch, """[
      {"num":"2","authors":"Kustner T, Muller M",
       "title":"Longitudinal segmentation of the ageing cortex",
       "doi":"10.1038/s41591-","reading":"A"}
    ]""")

    assert [e.num for e in entries] == ["2"]
    assert entries[0].doi is None
    assert "10.1038/s41591-" not in (entries[0].raw or "")
    assert any("doi" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_whole_doi_still_verifies_case_insensitively(monkeypatch):
    """The negative above must not be bought by rejecting real DOIs.

    Publishers print `doi:10.1001/...` in mixed case and the two extractions
    disagree about that case, so the DOI comparison folds case and nothing else
    — de-hyphenating a DOI would join `s41591-019` into a different registrant.
    """
    entries, _ = _propose(monkeypatch, """[
      {"num":"1","title":"Characterization of brain volume changes in\\naging individuals",
       "doi":"10.1001/JAMANETWORKOPEN.2023.18153","reading":"AB"}
    ]""")

    assert entries[0].doi == "10.1001/JAMANETWORKOPEN.2023.18153"


def test_an_umlaut_in_an_author_name_still_verifies_both_ways(monkeypatch):
    """`Küstner` printed, `Kustner` proposed — and the reverse.

    The two extractions of one page disagree about diacritics, so a comparison
    that did not fold them would discard a correctly copied author on half the
    non-English bibliography in existence. Layer O is applied to BOTH sides;
    folding one side of a substring test is the bug Plan A had to fix in
    `_title_check_text`.
    """
    readings = (READING_A, READING_B)
    assert reflist._found("authors", "Kustner T, Muller M", readings)
    assert reflist._found("authors", "Küstner T, Müller M", readings)

    entries, prov = _propose(monkeypatch, """[
      {"num":"2","authors":"Kustner T, Muller M",
       "title":"Longitudinal segmentation of the ageing cortex","reading":"AB"}
    ]""")

    assert "Kustner" in entries[0].raw
    assert not any("authors" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_ligature_and_a_soft_hyphenated_title_still_verify(monkeypatch):
    """Extraction damage is not disagreement.

    pymupdf prints `Longitu-\\ndinal` where docling prints `Longitudinal`, and a
    PDF font's `ﬁ` reaches one converter as one codepoint. Layer E undoes what
    the extractor did to the ink and nothing else, on both sides.
    """
    assert reflist._found("title", "Longitudinal segmentation", (READING_B,))
    assert reflist._found("title", "Characterization of fine detail",
                          ("Characterization of ﬁne detail",))
    # and it must still refuse the same string with the ligature's letters gone
    assert not reflist._found("title", "Characterization of ne detail",
                              ("Characterization of ﬁne detail",))


def test_a_duplicated_numeral_is_recorded_and_never_repaired(monkeypatch):
    """A numeral is the join key; renumbering it is how a verdict changes paper.

    Two entries proposed as `[2]` means the reading cannot say which paper `[2]`
    is — which is precisely the question. Both entries are kept exactly as
    proposed, the duplication is recorded, and `label_agreement` refuses to let
    this reading speak for `2` at all. Nothing is renumbered to `3`.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"2","title":"Longitudinal segmentation of the ageing cortex","reading":"A"},
      {"num":"2","title":"Atlas-based methods","reading":"B"}
    ]""")

    assert [e.num for e in entries] == ["2", "2"], "an entry was dropped or renumbered"
    # a numbering finding, not a discarded field — the two carry different
    # consequences and are reported separately
    assert any("twice" in f for f in prov.numbering_findings), prov.numbering_findings
    assert not any("twice" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_reply_that_is_not_json_yields_an_empty_candidate_not_an_exception(monkeypatch):
    """`propose` reports its own failure; it does not raise it at the refs stage.

    The refs stage has three other readings to proceed on. A model that
    answered with prose has told us nothing, and "nothing" is a representable
    result — an exception here would take down a stage that was doing fine.
    """
    entries, prov = _propose(monkeypatch, "I could not read that bibliography, sorry.")

    assert entries == []
    assert prov.discarded_whole, "the failure was silent"
    assert prov.entries_proposed == 0


def test_a_paper_printed_in_neither_reading_contributes_nothing(monkeypatch):
    """The reply mixes one real entry with one fabricated paper.

    This is the failure mode the whole module exists for, and the real entry
    beside it is the temptation: keeping it would mean proceeding on a reading
    that demonstrably invents. Neither entry survives, and the reason names the
    fabricated one.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"1","title":"Characterization of brain volume changes in\\naging individuals","reading":"AB"},
      {"num":"4","authors":"Nobody N","year":"2024",
       "title":"A paper that was never printed","journal":"J Fabrication","reading":"A"}
    ]""")

    assert entries == []
    assert "[4]" in prov.discarded_whole, prov.discarded_whole
    assert prov.entries_proposed == 2, "the count of what was proposed is still recorded"


def test_propose_makes_exactly_one_model_call_at_the_refs_site(monkeypatch):
    """One call per bibliography, attributed to `refs`, not to `check`.

    Two things break if this drifts. A per-entry call would read one reference
    list at N times the cost for no extra evidence; and a call attributed to the
    wrong site makes the report's `Checker:` line name the reference-list model
    as the judge of verdicts it never saw — the bug Task 1 exists to prevent.
    """
    calls = _reply(monkeypatch, "[]")
    entries, prov = reflist.propose(READING_A, READING_B,
                                    label_a="docling 2.8.0", label_b="pymupdf")

    assert len(calls) == 1, calls
    assert calls[0]["site"] == ask_mod.SITE_REFS
    assert entries == []
    assert prov.model == "claude-opus-5"
    assert prov.readings == ["docling 2.8.0", "pymupdf"]
    # both texts reached the prompt, each in its own slot
    assert "Kustner T, Muller M" in calls[0]["prompt"]
    assert "Küstner T, Müller M" in calls[0]["prompt"]
    assert "<<A>>" not in calls[0]["prompt"] and "<<B_LABEL>>" not in calls[0]["prompt"]


def test_the_models_reading_letter_is_translated_into_a_reading_name(monkeypatch):
    """One published field, one vocabulary — the translation happens here.

    The model answers in the terms the prompt gave it (`A`, `B`, `AB`), because
    that is what it was shown. `RefEntry.seen_in` is a *published* field whose
    vocabulary is reading NAMES, so that a consumer can ask `"llm" in seen_in`
    and get a true answer on every manifest. Letting `A` reach the field would
    put two vocabularies in one wire-format key, and a consumer checking either
    one would silently never match manifests written by the other path.
    """
    entries, _ = _propose(
        monkeypatch,
        """[
      {"num":"3","title":"Atlas-based methods","reading":"AB"}
    ]""",
        label_a="docling",
        label_b="pymupdf",
    )

    assert entries[0].seen_in == ["docling", "pymupdf"]


def test_an_unrecognised_reading_letter_is_dropped_rather_than_guessed(monkeypatch):
    """A model answering "the docling one" must not land a sentence in a
    published field, and must not be *assumed* to have meant reading A either.
    Unknown provenance is recorded as unknown."""
    entries, _ = _propose(monkeypatch, """[
      {"num":"3","title":"Atlas-based methods","reading":"whichever one was clearer"}
    ]""")

    assert entries[0].seen_in == [], entries[0].seen_in


def test_an_empty_reading_makes_no_model_call_at_all(monkeypatch):
    """Nothing to check against means nothing can be believed.

    A run whose second extraction came back empty would otherwise pay for a call
    whose every field could only be verified against one text — half the
    verification, at full price, with no way for the report to say so.
    """
    calls = _reply(monkeypatch, "[]")
    entries, prov = reflist.propose(READING_A, "   ", label_a="docling 2.8.0", label_b="pymupdf")

    assert calls == []
    assert entries == []
    # not `discarded_whole`: no reply was ever obtained to check and refuse —
    # the call never happened, which `failure` says and `outcome` confirms
    assert prov.failure
    assert prov.outcome == "not_attempted"


def test_the_entry_level_seen_in_round_trips_through_the_manifest(tmp_path):
    """Gate 2. A new persisted field that `from_json` drops is a silent loss.

    `_ref_entry_from` filters on `fields(RefEntry)`, so declaring the field is
    enough — this test is what says so rather than assuming it.
    """
    import json

    import jsonschema

    from papertrace.models import RefEntry, RefManifest

    m = RefManifest(manuscript="p.pdf",
                    entries=[RefEntry(num="1", raw="A. A paper. 2020.", seen_in=["docling", "pymupdf"])])
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)

    payload = json.loads(path.read_text())
    assert payload["entries"][0]["seen_in"] == ["docling", "pymupdf"]
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json").read_text()
    )
    jsonschema.validate(payload, schema)
    assert "seen_in" not in schema["properties"]["entries"]["items"].get("required", [])
    assert RefManifest.from_json(path).entries[0].seen_in == ["docling", "pymupdf"]


# --- an entry that names no paper is not an entry ---------------------------


def test_a_reply_with_a_numeral_and_nothing_else_yields_no_entry(monkeypatch):
    """The whole-candidate discard fires when a title is PROPOSED and fails.

    A reply supplying only `{"num": "9", "reading": "A"}` never reaches it, and
    would otherwise become a voter carrying no claim about any work. It cannot
    produce a wrong verdict — `label_agreement` compares through
    `_comparably_same`, which refuses to call two uncomparable entries `agreed`
    — but it can make the label `disputed` and withhold verdicts the model never
    said anything against. A degenerate reply may cost this reading its vote; it
    may not cost the audit its answers.
    """
    entries, prov = _propose(monkeypatch, '[{"num":"9","reading":"A"}]')

    assert entries == []
    assert any("no verified title or doi" in f for f in prov.fields_discarded), (
        prov.fields_discarded
    )


def test_an_entry_kept_on_a_doi_alone_still_counts(monkeypatch):
    """The rule is "names no paper", not "has no title". A DOI identifies a work
    on its own — `_same_work` settles on it before it looks at anything else —
    so an entry whose title the converter mangled but whose DOI is printed and
    whole is a legitimate voter."""
    entries, _ = _propose(
        monkeypatch,
        '[{"num":"7","doi":"10.1038/s41591-019-0673-2","reading":"A"}]',
    )

    assert len(entries) == 1
    assert entries[0].doi == "10.1038/s41591-019-0673-2"
    assert entries[0].title is None


def test_a_numbering_finding_is_not_reported_as_a_discarded_field(monkeypatch):
    """Two different things, two different lists.

    A discarded field is "this value was not printed, so it was dropped". A
    numbering finding is "this reading's labels do not add up", which the spec
    gives a different consequence. Sharing one list made
    `reflist_fields_discarded` report a count of discarded fields that included
    things no field ever lost.
    """
    entries, prov = _propose(
        monkeypatch,
        '[{"num":"6","title":"Characterization of brain volume changes in aging '
        'individuals","reading":"A"}]',
    )

    assert len(entries) == 1
    assert prov.fields_discarded == [], prov.fields_discarded
    assert any("absent from 1..max" in f for f in prov.numbering_findings), (
        prov.numbering_findings
    )


def test_a_reading_letter_of_the_wrong_type_is_reported_as_a_wrong_shape(monkeypatch):
    """A reply of `5` is a different failure from a field the model left out,
    and folding both into "nothing to say" loses the distinction. Either way
    `seen_in` stays empty — the outcome was always honest, only the record was
    silent."""
    entries, prov = _propose(
        monkeypatch,
        '[{"num":"6","title":"Characterization of brain volume changes in aging '
        'individuals","reading":5}]',
    )

    assert entries[0].seen_in == []
    assert any("reading: not a string" in f for f in prov.fields_discarded), (
        prov.fields_discarded
    )


# --- resolving a disputed label: three rules on the reply, and a slug -------
#
# `resolve_disputed` is the only place a model reading changes what `check`
# judges — a person has already seen the disagreement and asked for this. Zero
# of these tests existed before this round (M2 of the Task 7 review): every
# test that reached menu option 1 replaced this function with a stub built
# through `refs._entry`, which sets the one field the real function forgot
# (C1 — a slug-less entry is silently dropped from judgement by `check.py` and
# downloads to `sources_resolved/None.pdf`, overwriting itself label after
# label). These are direct, so that mistake cannot repeat.

_RESOLVE_A = "6. Fujita S, Mori S. Characterization of Brain Volume Changes. 2023. doi:10.1001/jamanetworkopen.2023.18153\n"
_RESOLVE_B = "6. Wachinger C. Something else entirely. 2019. doi:10.1000/wach\n"
_RESOLVE_NAMES = ("docling 2.8.0", "pymupdf")


def _resolve_reply(monkeypatch, payload: str, *, model: str = "claude-opus-5"):
    """Same seam, same qualification rule as `_reply` above: `reflist` calls
    `ask._ask` by qualified name, so the fake is installed on `ask_mod`."""
    calls = []

    def fake(prompt, model_arg=None):
        calls.append({"prompt": prompt})
        return payload

    monkeypatch.setattr(ask_mod, "_ask", fake)
    monkeypatch.setattr(ask_mod, "_MODELS", {ask_mod.SITE_REFS: model})
    return calls


def test_a_resolved_label_names_a_title_printed_in_one_of_the_readings(monkeypatch):
    """The whole permission structure of this call. Nothing invented may name a
    paper, so the title comes back only if it can be grepped out of the text the
    model was shown."""
    reply = json.dumps([{
        "num": "6",
        "title": "Characterization of Brain Volume Changes",
        "doi": "10.1001/jamanetworkopen.2023.18153",
        "year": "2023",
    }])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert list(res.resolved) == ["6"]
    assert res.still_disputed == []
    assert res.resolved["6"].doi == "10.1001/jamanetworkopen.2023.18153"


def test_a_resolved_entry_carries_a_slug(monkeypatch):
    """C1. `resolve_all` never re-slugs and `check.py` refuses to judge a
    slug-less entry — a bare `RefEntry(...)` here is what let a resolved label
    reach the manifest as "resolved" while `check` silently dropped it. Built
    through `refs._entry`, the one place that knows what a complete entry
    needs, so this cannot regress to a hand-rolled constructor again."""
    reply = json.dumps([{
        "num": "6",
        "title": "Characterization of Brain Volume Changes",
        "doi": "10.1001/jamanetworkopen.2023.18153",
        "year": "2023",
    }])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved["6"].slug, "a resolved entry with no slug is dropped by check.py"
    assert res.resolved["6"].slug != "ref"


def test_a_title_printed_in_neither_reading_leaves_the_label_disputed(monkeypatch):
    """An unverifiable *field* is discarded; an unverifiable *title* is the label
    staying disputed. The title is the only field that identifies a paper, so a
    reply whose title cannot be found has answered nothing."""
    reply = json.dumps([{"num": "6", "title": "A Paper Nobody Printed", "year": "2023"}])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved == {}
    assert res.still_disputed == ["6"]


def test_an_unverifiable_doi_is_discarded_without_costing_the_label(monkeypatch):
    """A truncated DOI is extraction damage, not a different paper. The field
    goes; the resolution stands and the entry carries a recorded gap."""
    reply = json.dumps([{
        "num": "6",
        "title": "Characterization of Brain Volume Changes",
        "doi": "10.1038/s41591-",
    }])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved["6"].doi is None
    assert "doi" in res.provenance.fields_discarded


def test_cannot_tell_is_an_answer_and_keeps_the_label_disputed(monkeypatch):
    """Abstention is first-class. A call that must always answer will
    confabulate on exactly the labels two readings disagree about."""
    reply = json.dumps([{"num": "6", "cannot_tell": True}])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved == {}
    assert res.still_disputed == ["6"]


def test_partial_resolution_is_representable(monkeypatch):
    """11 of 14 resolved and 3 still disputed is a real outcome, not an error.
    No code path may require all-or-nothing."""
    text_a = _RESOLVE_A + "7. Weston AD. Automated abdominal segmentation. 2019.\n"
    text_b = _RESOLVE_B + "7. Someone Else. A different paper. 2015.\n"
    reply = json.dumps([
        {"num": "6", "title": "Characterization of Brain Volume Changes"},
        {"num": "7", "cannot_tell": True},
    ])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6", "7"], _RESOLVE_NAMES, (text_a, text_b))
    assert list(res.resolved) == ["6"]
    assert res.still_disputed == ["7"]


def test_a_label_the_reply_never_mentions_stays_disputed(monkeypatch):
    """Silence is not consent. A reply that answers 1 of 2 questions has
    answered 1, and the unanswered label is not repaired into agreement."""
    reply = json.dumps([{"num": "6", "title": "Characterization of Brain Volume Changes"}])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6", "9"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.still_disputed == ["9"]


def test_a_malformed_reply_resolves_nothing_rather_than_raising(monkeypatch):
    """`refs` has already done useful work by this point. A model that returns
    prose leaves every label disputed — the state the run was in before it
    asked — and says so in the provenance. "I think maybe [6]?" is not prose
    without brackets by accident: `ask._parse_json_array`'s first-`[`-to-last-
    `]` slice reads it as the syntactically valid one-element list `[6]`, and
    `_parse_array`'s dict check is what still refuses it."""
    _resolve_reply(monkeypatch, "I think maybe [6]?")
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved == {}
    assert res.still_disputed == ["6"]
    assert res.provenance.discarded_whole


def test_seen_in_is_translated_using_the_names_matching_the_texts_order(monkeypatch):
    """M4. `names[i]` must be the reading `texts[i]` actually came from — never
    derived from a different dict of candidates that can hold MORE readings
    (crossref, llm) than were shown to this call. A reply of "B" must land the
    SECOND name passed here, not the second name of some other collection."""
    reply = json.dumps([{
        "num": "6", "title": "Characterization of Brain Volume Changes", "seen_in": "B",
    }])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved["6"].seen_in == ["pymupdf"]


def test_an_unrecognised_seen_in_is_dropped_rather_than_guessed(monkeypatch):
    reply = json.dumps([{
        "num": "6", "title": "Characterization of Brain Volume Changes",
        "seen_in": "whichever one was clearer",
    }])
    _resolve_reply(monkeypatch, reply)
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.resolved["6"].seen_in == []


def test_outcome_is_read_once_a_reply_is_obtained(monkeypatch):
    """m8. `propose` sets this the moment a reply comes back, before it is even
    parsed — `resolve_disputed` must do the same, or a caller that inspects
    `provenance.outcome` after a malformed reply sees the module's own default
    ("not_attempted") for a call that plainly happened."""
    _resolve_reply(monkeypatch, "not json at all")
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert res.provenance.outcome == "read"


def test_no_labels_makes_no_model_call_at_all(monkeypatch):
    """Nothing disputed is nothing to ask about — never spend a call for it."""
    calls = _resolve_reply(monkeypatch, "[]")
    res = reflist.resolve_disputed([], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))
    assert calls == []
    assert res.resolved == {}
    assert res.still_disputed == []


# --- whole-branch review, Critical 2 and minor 7 ---------------------------


def test_the_resolution_prompt_shows_what_each_reading_says_at_the_label(monkeypatch):
    """Critical 2. The disagreement being adjudicated has to be IN the prompt,
    including the readings with no printed span of their own — the deposit and
    the model's reading, which are exactly the ones a dispute is often with."""
    from papertrace.models import RefEntry

    calls = _resolve_reply(monkeypatch, json.dumps([{"num": "6", "cannot_tell": True}]))
    reflist.resolve_disputed(
        ["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B),
        disputing={"crossref": [RefEntry(num="6", raw="Deposited: a third paper entirely")]},
    )

    prompt = calls[0]["prompt"]
    assert "Deposited: a third paper entirely" in prompt
    assert "crossref" in prompt


def test_a_value_printed_only_in_the_disputing_entry_is_still_discarded(monkeypatch):
    """The context block is context. Widening verification to it would let a
    title the deposit carries and the page does not be believed — which is the
    whole permission structure of this module inverted."""
    from papertrace.models import RefEntry

    _resolve_reply(monkeypatch, json.dumps([{"num": "6", "title": "A third paper entirely"}]))
    res = reflist.resolve_disputed(
        ["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B),
        disputing={"crossref": [RefEntry(num="6", raw="Deposited: A third paper entirely")]},
    )

    assert res.resolved == {}
    assert res.still_disputed == ["6"]
    assert "[6].title" in res.provenance.fields_discarded


def test_one_printed_extraction_renders_one_block_not_an_empty_second(monkeypatch):
    calls = _resolve_reply(monkeypatch, json.dumps([{"num": "6", "cannot_tell": True}]))
    reflist.resolve_disputed(["6"], ("pymupdf",), (_RESOLVE_A,))

    prompt = calls[0]["prompt"]
    assert "READING A (pymupdf)" in prompt
    assert "READING B" not in prompt


def test_a_reply_repeating_a_numeral_is_counted_once_per_object(monkeypatch):
    """Minor 5. `entries_proposed` was measured after de-duplication, so a
    reply with two objects for [6] — the malformed shape a reader most needs
    to know about — was recorded as one proposed entry. `propose` counts
    before de-duplication; so does this now."""
    _resolve_reply(monkeypatch, json.dumps([
        {"num": "6", "cannot_tell": True},
        {"num": "6", "cannot_tell": True},
    ]))
    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (_RESOLVE_A, _RESOLVE_B))

    assert res.provenance.entries_proposed == 2


def test_an_oversized_bibliography_is_refused_before_the_call_not_after(monkeypatch):
    """Minor 7. Both extractions were interpolated whole, with no limit and no
    truncation record — the one model input in this codebase that could
    plausibly exceed a context window. Refused rather than clipped: a clipped
    reading proposes a list missing the entries past the cut, and nothing in
    the provenance could say which. A refusal costs nothing and states itself.
    """
    calls = _reply(monkeypatch, "[]")
    big = "6. A reference. 2020.\n" * (reflist.REFLIST_CHAR_LIMIT // 10)

    entries, prov = reflist.propose(big, big, label_a="docling 2.8.0", label_b="pymupdf")

    assert calls == []
    assert entries == []
    assert prov.outcome == "not_attempted"
    assert "too long" in prov.failure


def test_an_oversized_resolution_input_leaves_every_label_disputed(monkeypatch):
    """The same guard on the other call, and the state it must leave behind:
    every label exactly where it was, with a reason that is not "the model
    could not tell" — nothing was asked."""
    calls = _resolve_reply(monkeypatch, "[]")
    big = "6. A reference. 2020.\n" * (reflist.REFLIST_CHAR_LIMIT // 10)

    res = reflist.resolve_disputed(["6"], _RESOLVE_NAMES, (big, big))

    assert calls == []
    assert res.resolved == {}
    assert res.still_disputed == ["6"]
    assert res.provenance.outcome == "not_attempted"
    assert "too long" in res.provenance.failure


def test_a_long_run_of_absent_numerals_is_counted_rather_than_listed(monkeypatch):
    """The count is the finding; past a handful the list stops being one.

    A reply naming only `[99]` makes every numeral below it absent, which
    printed 98 consecutive integers — 410 characters — into
    `reflist_numbering_findings`, a field the report shows a reader. The first
    few locate the gap; the rest only prove the reader stopped reading. The
    finding itself is unchanged and nothing is hidden: the total leads.
    """
    text = "99. Weston AD (2019) A paper. Radiology. https://doi.org/10.1148/radiol.2019181432"
    _reply(monkeypatch, '[{"num":"99","title":"A paper","reading":"AB"}]')
    _, prov = reflist.propose(text, text, label_a="docling", label_b="pymupdf")

    gaps = next(f for f in prov.numbering_findings if "absent" in f)
    assert gaps.startswith("98 numerals absent"), gaps
    assert "and 92 more" in gaps, gaps
    assert len(gaps) < 100, f"{len(gaps)} chars in a reader-facing field: {gaps}"
