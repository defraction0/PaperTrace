"""Withholding a disputed citation label's verdict, and the field that carries it.

A citation label whose readings of the bibliography disagree is not evidence
either way about the claim that cites it — it is evidence PaperTrace does not
know which paper was actually fetched under that label. `check_claims`'s
`disputed` parameter drops such a label from judgement without pretending the
source was never obtained: that distinction is `withheld_refs` versus
`unjudged_refs`, and this module is what regresses if the two get merged.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import check as check_mod  # noqa: E402
from papertrace.check import check_claims  # noqa: E402
from papertrace.models import Block, ClaimResult, RefEntry, RefManifest, SourceMap  # noqa: E402


def _write_source(case: Path, slug: str, text: str = "The rate was 84.3%.") -> None:
    """An ingested source: the text and the map that says where its one block is."""
    d = case / "ingest" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "annotated.md").write_text(f"<!-- block_0001, page 1 -->\n{text}\n")
    SourceMap(
        doc=f"{slug}.pdf",
        pages=1,
        blocks=[Block("block_0001", "text", 1, (0.0, 0.0, 10.0, 10.0), [], text)],
    ).to_json(d / "source_map.json")


def _entry(num: str, slug: str, status: str = "retrieved") -> RefEntry:
    return RefEntry(
        num=num, raw=f"ref {num}", status=status, slug=slug,
        pdf_path=f"/nonexistent/{slug}.pdf",
    )


def _manifest(*entries: RefEntry) -> RefManifest:
    return RefManifest(manuscript="paper.pdf", entries=list(entries))


def _reply(claim_id: int, verdict: str = "supported") -> str:
    return json.dumps([{
        "id": claim_id, "verdict": verdict, "note": "reads it",
        "source_page": 1, "source_block": "block_0001", "anchor_phrases": [],
    }])


def _unexpected_call(prompt, model=None):
    raise AssertionError("no source survived withholding — _ask must not be called")


def test_a_disputed_label_is_dropped_and_the_other_cited_source_is_still_judged(
    tmp_path, monkeypatch
):
    """Only the disputed label's source is subtracted from a co-cited claim —
    a targeted subtraction, not a claim killed because one of several labels
    it cites is in dispute. If this regresses to killing the whole claim, a
    reader loses a real verdict from the source that was never in question."""
    _write_source(tmp_path, "six-2019")
    _write_source(tmp_path, "nine-2021")
    manifest = _manifest(_entry("6", "six-2019"), _entry("9", "nine-2021"))
    monkeypatch.setattr(check_mod, "_ask", lambda prompt, model=None: _reply(1, "supported"))
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6", "9"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    slugs = {j.source_slug for j in claims[0].judgements}
    assert slugs == {"nine-2021"}
    assert claims[0].verdict == "supported"
    # the verdict and the judged slugs are both computed from `avail`, so they
    # would survive an edit that only populated `withheld_refs` in the
    # nothing-survives branch — and `claim_pairing` reads this field, so every
    # co-cited claim keeping a real verdict would silently lose its disclosure
    assert claims[0].withheld_refs == ["6"]
    assert claims[0].unjudged_refs == []


def test_a_claim_whose_only_cited_source_is_disputed_is_unchecked_not_not_retrieved(
    tmp_path, monkeypatch
):
    """`not_retrieved` would say the source could not be obtained. It WAS
    obtained — the two readings of the bibliography just disagree about which
    paper the label names — so the only honest verdict is `unchecked`, and the
    disputed label must be named in the note so a reader knows which citation
    to check by hand. `_ask` must never be called: nothing survived to judge."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", _unexpected_call)
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert claims[0].verdict == "unchecked"
    assert claims[0].verdict != "not_retrieved"
    assert "6" in claims[0].note


def test_withheld_refs_holds_the_disputed_label_and_unjudged_refs_does_not(
    tmp_path, monkeypatch
):
    """The two fields mean different things: `unjudged_refs` is "nobody could
    get to it", `withheld_refs` is "it was read and set aside anyway". A label
    landing in both would tell the reader retrieval failed when it did not —
    the exact laundering CLAUDE.md names for `unjudged_refs`."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", _unexpected_call)
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert claims[0].withheld_refs == ["6"]
    assert claims[0].unjudged_refs == []


def test_disputed_none_and_empty_set_behave_identically(tmp_path, monkeypatch):
    """Every caller that predates this feature passes neither argument. `None`
    must be exactly as inert as an explicit empty set, or turning on this
    feature with nothing yet computed as disputed would change verdicts that
    used to be stable — the regression this whole task must not cause."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", lambda prompt, model=None: _reply(1, "supported"))

    claims_none = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]
    check_claims(claims_none, manifest, tmp_path, backend="pymupdf", disputed=None)

    claims_empty = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]
    check_claims(claims_empty, manifest, tmp_path, backend="pymupdf", disputed=set())

    claims_default = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]
    check_claims(claims_default, manifest, tmp_path, backend="pymupdf")

    for other in (claims_empty, claims_default):
        assert other[0].verdict == claims_none[0].verdict == "supported"
        assert other[0].withheld_refs == claims_none[0].withheld_refs == []


def test_a_disputed_label_never_retrieved_stays_not_retrieved(tmp_path):
    """Withholding describes a source that WAS obtained. A label that is both
    disputed and, say, paywalled must not be relabelled `unchecked` — that
    would claim a model read something nobody ever fetched. `withheld_refs`
    is partitioned OUT of the retrieval filter's result, never out of the raw
    cited-labels list, so a label that never passed retrieval cannot appear
    there no matter what `disputed` says about it."""
    manifest = _manifest(_entry("6", "six-2019", status="paywalled"))
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert claims[0].verdict == "not_retrieved"
    assert claims[0].withheld_refs == []


def test_withheld_refs_round_trips_and_validates_against_the_schema(tmp_path):
    """Gate 2: a new claim field is additive in the schema and round-trips
    through `to_json`/`from_json` — `ClaimResult` has no bespoke pair of those,
    it rides `dataclasses.asdict` out and `_claim_from`'s `**d` in, so a field
    with a default needs no code there, only the schema and this proof."""
    import jsonschema

    from papertrace.models import RunResults

    claim = ClaimResult(
        id=1, claim="x", location="Results", refs=["6"],
        verdict="unchecked", withheld_refs=["6"],
    )
    path = tmp_path / "results.json"
    RunResults(manuscript="m.pdf", claims=[claim]).to_json(path)

    back = RunResults.from_json(path).claims[0]
    assert back.withheld_refs == ["6"]

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "results.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))


def test_a_co_cited_label_that_could_not_be_retrieved_survives_a_withholding(tmp_path):
    """Major 2 of the whole-branch review, and a regression against `main`.

    A claim cites a disputed-and-retrieved [3] and a paywalled [4]. Nothing
    survives to judge, so the claim is `unchecked` for the withholding — and
    on the way out of that branch `unjudged_refs` was never filled, so [4]
    was in none of the three accounts a reader has (judged, withheld,
    unjudged) and appeared in no note. A retrieval gap erased by a
    withholding is the cardinal rule's own example of what may not happen.
    """
    _write_source(tmp_path, "three-2019")
    manifest = _manifest(
        _entry("3", "three-2019"),
        _entry("4", "four-2020", status="paywalled"),
    )
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["3", "4"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"3"})

    assert claims[0].verdict == "unchecked"
    assert claims[0].withheld_refs == ["3"]
    assert claims[0].unjudged_refs == ["4"]


def test_an_unretrievable_co_citation_is_reported_where_a_verdict_survives_too(
    tmp_path, monkeypatch
):
    """The same accounting on the path that DOES keep a verdict, so the fix
    is not a special case bolted onto the one branch the review named: [3]
    withheld, [4] paywalled, [5] judged."""
    _write_source(tmp_path, "five-2021")
    manifest = _manifest(
        _entry("3", "three-2019"),
        _entry("4", "four-2020", status="paywalled"),
        _entry("5", "five-2021"),
    )
    monkeypatch.setattr(check_mod, "_ask", lambda prompt, model=None: _reply(1, "supported"))
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["3", "4", "5"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"3"})

    assert claims[0].verdict == "supported"
    assert claims[0].withheld_refs == ["3"]
    assert claims[0].unjudged_refs == ["4"]


# --- what the prose may say about a label whose readings did not agree -----


def test_the_withholding_note_does_not_assert_the_readings_named_different_papers(
    tmp_path,
):
    """`label_agreement` returns `disputed` for two causes — the readings
    actively name different papers, and nothing in them could be compared —
    and no published field tells the two apart. A note asserting the first is
    therefore false whenever the second happened, which is the same mistake
    as describing a source that was never fetched as one that was."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert "different papers" not in claims[0].note
    assert "do not agree" in claims[0].note


def test_the_run_level_disputed_disclosure_covers_both_causes():
    """The run-level roll-up says the same thing as the note above, so it
    inherits the same constraint."""
    from papertrace.disclosures import _labels_disputed

    d = _labels_disputed(RefManifest(manuscript="m.pdf", labels_disputed=["6"]))

    assert d is not None
    # the disjunction, not the assertion: naming both causes is honest,
    # asserting the contradicting one is not
    assert "did not agree on which paper" in d.text
    assert "nothing in them could be compared" in d.text
    assert "name different papers for each of these labels" not in d.text


# --- a disputed label nobody retrieved is not a source that was fetched ----


def test_a_disputed_label_nobody_retrieved_is_not_described_as_fetched():
    """Major 5. `check.py` keeps such a label out of `withheld_refs` on
    purpose (`test_a_disputed_label_never_retrieved_stays_not_retrieved`);
    the disclosure layer undid that care by falling back to
    `labels_disputed`, and printed "the source fetched under that label" for
    a source nobody ever fetched."""
    from papertrace.disclosures import _claim_pairing

    manifest = _manifest(_entry("6", "six-2019", status="paywalled"))
    manifest.labels_disputed = ["6"]
    claim = ClaimResult(id=1, claim="x", location="Results", refs=["6"],
                        verdict="not_retrieved")

    d = _claim_pairing(claim, manifest)

    assert d is not None
    assert "fetched" not in d.text
    assert "never retrieved" in d.text


def test_a_retrieved_disputed_label_still_reads_as_a_source_that_was_fetched():
    """The other side of Major 5's split, and the load-path guard on it: a
    `results.json` written before `withheld_refs` existed carries `[]` for
    every claim, so the new branch must not be chosen from that emptiness
    alone. The manifest's own entry status is what decides, and here the
    source really was retrieved."""
    from papertrace.disclosures import _claim_pairing

    manifest = _manifest(_entry("6", "six-2019"))
    manifest.labels_disputed = ["6"]
    claim = ClaimResult(id=1, claim="x", location="Results", refs=["6"])  # no withheld_refs

    d = _claim_pairing(claim, manifest)

    assert d is not None
    assert "the source retrieved under that label" in d.text


def test_a_claim_citing_both_a_withheld_and_an_unretrieved_disputed_label_names_both():
    """One claim, both causes. The withheld label keeps the token and the
    level — it is the more urgent finding, because a paper WAS fetched under
    it — and the label nobody retrieved is named rather than folded into the
    same sentence."""
    from papertrace.disclosures import _claim_pairing

    manifest = _manifest(
        _entry("6", "six-2019"),
        _entry("7", "seven-2020", status="paywalled"),
    )
    manifest.labels_disputed = ["6", "7"]
    claim = ClaimResult(id=1, claim="x", location="Results", refs=["6", "7"],
                        withheld_refs=["6"])

    d = _claim_pairing(claim, manifest)

    assert d is not None
    assert "[6]" in d.text and "[7]" in d.text
    assert "never retrieved" in d.text


def test_any_iterable_of_labels_withholds_every_claim_not_just_the_first(tmp_path, monkeypatch):
    """`disputed` is typed `set[str] | None`, and a set is what the CLI passes.

    A generator, though, is truthy with no `__len__`, and each `r in disputed`
    consumes it — so the first claim would withhold and every claim after it
    would see an exhausted iterator and be judged normally. A silently partial
    withholding is worse than a loud type error, so the parameter is copied into
    a set on the way in and this test is what says so.
    """
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", lambda prompt, model=None: _reply(1, "supported"))
    claims = [
        ClaimResult(id=1, claim="first", location="Results", refs=["6"]),
        ClaimResult(id=2, claim="second", location="Results", refs=["6"]),
    ]

    check_claims(claims, manifest, tmp_path, backend="pymupdf",
                 disputed=(label for label in ("6",)))

    assert [c.verdict for c in claims] == ["unchecked", "unchecked"]
    assert [c.withheld_refs for c in claims] == [["6"], ["6"]]
