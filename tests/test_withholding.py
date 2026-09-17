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
