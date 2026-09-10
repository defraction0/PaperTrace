"""A substantive verdict must point at a passage the reader can be shown.

`supported`, `partial` and `contradicted` all assert that a specific piece of
the source settles the claim. Before this, the assertion was unchecked: the
model could name page 99999 and `block_nope`, and the verdict stood. The
report then printed "Page 99999" as provenance and `crop_for_anchor` quietly
produced nothing, so the one claim the reader most wanted to verify was the
one with no picture.

The bar is the picture. `crop_for_anchor` writes a crop when the region comes
from a valid block, or from an anchor phrase that actually matched the page —
so requiring a **valid block** is what makes the image unconditional. With one,
`crop_evidence` always writes the block; the red box is drawn on top if a
phrase matches inside it. Anchor phrases stay optional because they decide
whether there is a box, not whether there is a picture.

`not_addressed` is exempt and must stay exempt: the source was read and says
nothing, so there is no passage to point at, and demanding one would force the
model to invent a citation for an absence.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import check as check_mod  # noqa: E402
from papertrace.check import SourceProvenance, _judgement_from, check_claims  # noqa: E402
from papertrace.models import ClaimResult, RefEntry, RefManifest, SourceMap  # noqa: E402

# one page, one block on it — the smallest source a verdict can point into
ONE_PAGE = SourceProvenance(pages=1, block_pages={"block_0001": 1})
TWO_PAGE = SourceProvenance(pages=2, block_pages={"block_0001": 1, "block_0007": 2})


def _entry(**kw) -> dict:
    base = {
        "id": 1,
        "verdict": "supported",
        "note": "the source states it",
        "source_page": 1,
        "source_block": "block_0001",
        "anchor_phrases": ["84.3%"],
    }
    base.update(kw)
    return base


# --- what a valid judgement still looks like -------------------------------


def test_a_valid_block_on_the_named_page_is_accepted():
    fields, why = _judgement_from(_entry(), ONE_PAGE)
    assert why == ""
    assert fields["verdict"] == "supported"
    assert fields["source_block"] == "block_0001"


def test_a_valid_block_with_no_anchor_phrases_is_still_accepted():
    """The block is what guarantees the crop; the phrases only add the red box.

    Rejecting this would discard a reading the model got right and merely
    under-quoted — and the reader still gets an image of the exact block, shown
    unboxed and captioned as such.
    """
    fields, why = _judgement_from(_entry(anchor_phrases=[]), ONE_PAGE)
    assert why == ""
    assert fields["anchor_phrases"] == []


def test_not_addressed_still_needs_no_page_or_block():
    """Absence of relevant content has no decisive passage by construction."""
    fields, why = _judgement_from(
        {"id": 1, "verdict": "not_addressed", "note": "silent on mortality"}, ONE_PAGE
    )
    assert why == ""
    assert fields["source_page"] is None and fields["source_block"] is None


def test_not_addressed_survives_an_unreadable_source_map():
    """It asserts nothing about a location, so there is nothing to validate."""
    fields, why = _judgement_from({"id": 1, "verdict": "not_addressed", "note": "n"}, None)
    assert why == "" and fields["verdict"] == "not_addressed"


# --- provenance that cannot be true ----------------------------------------


@pytest.mark.parametrize(
    ("entry", "expect_in_note"),
    [
        (_entry(source_page=99999, source_block=None), "1 page"),
        (_entry(source_page=2, source_block=None), "1 page"),
        (_entry(source_block="block_9999"), "block_9999"),
        (_entry(source_block=None), "no source_block"),
    ],
)
def test_impossible_provenance_is_unchecked_not_a_verdict(entry, expect_in_note):
    fields, why = _judgement_from(entry, ONE_PAGE)
    assert fields is None
    assert expect_in_note in why


def test_a_block_on_another_page_is_refused():
    """`block_0007` is real, but it is on page 2 — so page 1 is not where it is,
    and a crop of page 1 would show the reader something else entirely."""
    fields, why = _judgement_from(
        _entry(source_page=1, source_block="block_0007"), TWO_PAGE
    )
    assert fields is None
    assert "block_0007" in why and "page 2" in why


def test_an_unreadable_source_map_refuses_substantive_verdicts():
    """Unverifiable provenance is not verified provenance. The note names the
    fix, because the cause is our own artifact, not the model."""
    fields, why = _judgement_from(_entry(), None)
    assert fields is None
    assert "source map" in why and "papertrace ingest" in why


# --- through check_claims, where it actually matters ------------------------


def _write_source(case: Path, slug: str, pages: int = 1,
                  bbox: tuple = (0.0, 0.0, 10.0, 10.0)) -> None:
    """An ingested source: the text and the map that says where its blocks are.

    `bbox` matters only where a crop is actually drawn — the highlight tests
    need a region that covers the text on the generated page.
    """
    from papertrace.models import Block

    d = case / "ingest" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "annotated.md").write_text("<!-- block_0001, page 1 -->\nThe rate was 84.3%.\n")
    SourceMap(
        doc=f"{slug}.pdf",
        pages=pages,
        blocks=[Block("block_0001", "text", 1, bbox, [], "The rate was 84.3%.")],
    ).to_json(d / "source_map.json")


def _manifest(slug: str) -> RefManifest:
    return RefManifest(
        manuscript="paper.pdf",
        entries=[RefEntry(num="1", raw="ref", status="retrieved", slug=slug,
                          pdf_path=f"/nonexistent/{slug}.pdf")],
    )


def test_check_claims_downgrades_an_impossible_page_to_unchecked(tmp_path, monkeypatch):
    _write_source(tmp_path, "smith-2020")
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: json.dumps(
            [{"id": 1, "verdict": "supported", "note": "yes",
              "source_page": 99999, "source_block": "nope", "anchor_phrases": []}]
        ),
    )
    claims = [ClaimResult(id=1, claim="the rate was 84.3%", location="Results", refs=["1"])]
    check_claims(claims, _manifest("smith-2020"), tmp_path, backend="pymupdf")

    j = claims[0].judgements[0]
    assert j.verdict == "unchecked"
    assert claims[0].verdict == "unchecked"
    # the source WAS retrieved — this must never be laundered into a gap
    assert claims[0].verdict != "not_retrieved"


def test_check_claims_keeps_a_verdict_whose_block_is_real(tmp_path, monkeypatch):
    _write_source(tmp_path, "smith-2020")
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: json.dumps(
            [{"id": 1, "verdict": "contradicted", "note": "says 48%",
              "source_page": 1, "source_block": "block_0001",
              "anchor_phrases": ["84.3%"]}]
        ),
    )
    claims = [ClaimResult(id=1, claim="the rate was 84.3%", location="Results", refs=["1"])]
    check_claims(claims, _manifest("smith-2020"), tmp_path, backend="pymupdf")
    assert claims[0].judgements[0].verdict == "contradicted"
    assert claims[0].verdict == "contradicted"


# --- the backstop: a verdict with no picture is not a verdict ---------------
#
# Check-time validation makes this nearly unreachable, and "nearly" is not the
# standard. The PDF can be missing from sources_resolved/, and a source map can
# disagree with the PDF it was built from. Without a backstop at the point the
# image is actually produced, the rule holds by inference rather than by
# construction — so `highlight` enforces it again, against reality this time.


def _case_with_judgement(tmp_path: Path, *, verdict: str = "supported") -> Path:
    from papertrace.models import RunResults, SourceJudgement

    case = tmp_path / "case"
    (case / "out").mkdir(parents=True)
    _write_source(case, "a-2020", bbox=(72.0, 90.0, 300.0, 110.0))

    claim = ClaimResult(
        id=1, claim="the rate was 84.3%", location="Results", refs=["1"],
        judgements=[SourceJudgement(
            source_slug="a-2020", ref="1", verdict=verdict, note="the source states it",
            source_page=1, source_block="block_0001", anchor_phrases=["84.3%"],
        )],
    )
    claim.apply_headline()
    RunResults(manuscript="m.pdf", claims=[claim]).to_json(case / "out" / "results.json")
    _manifest("a-2020").to_json(case / "refs_manifest.json")
    return case


def test_a_verdict_that_produced_no_evidence_image_is_downgraded(tmp_path):
    """The block validated at check time, but the PDF is not in the case folder,
    so no crop exists. A `supported` a reader cannot look at is not `supported`."""
    from papertrace import cli
    from papertrace.models import RunResults

    case = _case_with_judgement(tmp_path)
    cli.highlight(case=case, claim=None)

    after = RunResults.from_json(case / "out" / "results.json")
    j = after.claims[0].judgements[0]
    assert j.verdict == "unchecked"
    assert "evidence image" in j.note
    assert after.claims[0].verdict == "unchecked"


def test_not_addressed_is_not_downgraded_for_having_no_image(tmp_path):
    """It never claimed a passage, so there is no picture it owes anyone."""
    from papertrace import cli
    from papertrace.models import RunResults

    case = _case_with_judgement(tmp_path, verdict="not_addressed")
    cli.highlight(case=case, claim=None)

    after = RunResults.from_json(case / "out" / "results.json")
    assert after.claims[0].judgements[0].verdict == "not_addressed"


def test_a_verdict_whose_crop_was_written_survives(tmp_path):
    """The positive control: a real PDF, a real block, a crop on disk."""
    import pymupdf

    from papertrace import cli
    from papertrace.models import RunResults

    case = _case_with_judgement(tmp_path)
    pdf = case / "sources_resolved" / "a-2020.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "The rate was 84.3% overall.", fontsize=11)
    doc.save(pdf)
    doc.close()

    cli.highlight(case=case, claim=None)

    after = RunResults.from_json(case / "out" / "results.json")
    j = after.claims[0].judgements[0]
    assert j.verdict == "supported"
    assert j.evidence_image and (case / "out" / j.evidence_image).exists()
    assert j.anchor_located is True


# --- a source directory must hold the source it is named after ----------------
#
# `check_claims` reused `case/ingest/<slug>/annotated.md` whenever it existed,
# keyed on the slug alone. `SourceMap.doc` is `<slug>.pdf` whichever paper's
# bytes are inside, so nothing could tell one from another. Slugs are not
# eternal — a collision fix renames one of two colliding entries, and the
# reconciler can hand `refs` the Crossref list on one run and the parsed list on
# the next — so a re-run of an existing case could judge a claim against the
# previous occupant of that directory: a confident verdict on the wrong paper,
# which is the one thing this codebase refuses to do.


def _pdf_saying(path: Path, text: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=12)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_the_ingest_of_a_source_records_which_file_it_read(tmp_path):
    """Additive field, round-tripped, and absent-safe for older maps."""
    import json as _json

    import jsonschema

    from papertrace.ingest import ingest_pdf
    from papertrace.models import SourceMap, manuscript_fingerprint

    pdf = _pdf_saying(tmp_path / "a.pdf", "Paper A says alpha.")
    smap = ingest_pdf(pdf, tmp_path / "out", backend="pymupdf")
    assert smap.source_sha256 == manuscript_fingerprint(pdf)

    path = tmp_path / "out" / "source_map.json"
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "source_map.schema.json"
    schema = _json.loads(schema_path.read_text())
    payload = _json.loads(path.read_text())
    jsonschema.validate(payload, schema)
    assert SourceMap.from_json(path).source_sha256 == smap.source_sha256

    del payload["source_sha256"]  # a map written before this field
    path.write_text(_json.dumps(payload))
    jsonschema.validate(_json.loads(path.read_text()), schema)
    assert SourceMap.from_json(path).source_sha256 is None


def test_a_stale_source_directory_is_re_ingested_not_reused(tmp_path, monkeypatch):
    """The wrong-paper route, stated as the thing that must not happen: the
    directory holds paper B, the manifest entry points at paper A, and the
    model must be shown A."""
    from papertrace.check import check_claims
    from papertrace.ingest import ingest_pdf
    from papertrace.models import ClaimResult, RefEntry, RefManifest

    case = tmp_path / "case"
    sources = case / "sources_resolved"
    a = _pdf_saying(sources / "smith-2020.pdf", "Paper A reports alpha in twelve patients.")
    b = _pdf_saying(tmp_path / "b.pdf", "Paper B reports beta in nine patients.")

    # the stale state: ingest/smith-2020 built from B, while the manifest's
    # smith-2020 now points at A
    ingest_pdf(b, case / "ingest" / "smith-2020", backend="pymupdf")
    assert "beta" in (case / "ingest" / "smith-2020" / "annotated.md").read_text()

    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="5", raw="Smith J. Paper A. 2020.", status="retrieved",
                          slug="smith-2020", pdf_path=str(a))],
    )
    (case / "refs_manifest.json").parent.mkdir(parents=True, exist_ok=True)
    manifest.to_json(case / "refs_manifest.json")

    claims = [ClaimResult(id=1, claim="alpha was reported", location="Results", refs=["5"])]
    prompts: list[str] = []

    def _fake_ask(prompt, model=None):
        prompts.append(prompt)
        return '[{"id":1,"verdict":"not_addressed","note":"n/a"}]'

    monkeypatch.setattr("papertrace.check._ask", _fake_ask)
    check_claims(claims, manifest, case, backend="pymupdf")

    assert prompts, "no model call was made"
    body = prompts[0]
    assert "alpha" in body, "the model was not shown the paper the manifest names"
    assert "beta" not in body, "the model was shown the previous occupant of the directory"


def test_a_source_read_by_the_other_backend_is_re_ingested(tmp_path):
    """A case folder built before layout-aware source ingest holds pymupdf
    source maps. Re-running `check` on it must not reuse them while the run
    claims docling: the whole point of reading sources with the layout backend
    is that a table in a source is readable, and a stale flat map silently
    gives the judge the linearized version instead.

    `source_sha256` cannot catch this — it is the same PDF. The converter is
    the fact that changed.
    """
    from papertrace.check import _stale_ingest
    from papertrace.models import manuscript_fingerprint

    pdf = tmp_path / "smith-2020.pdf"
    _pdf_saying(pdf, "HR 0.88 (0.79-0.98).")
    ingest_dir = tmp_path / "ingest" / "smith-2020"
    ingest_dir.mkdir(parents=True)
    smap = {
        "doc": "smith-2020.pdf", "pages": 1, "converter": "pymupdf",
        "source_sha256": manuscript_fingerprint(pdf),
        "blocks": [{"id": "block_0001", "page": 1, "type": "text",
                    "bbox": [0, 0, 10, 10], "text": "HR 0.88 (0.79-0.98)."}],
    }
    (ingest_dir / "source_map.json").write_text(json.dumps(smap))

    # same backend that wrote it: nothing to redo
    assert _stale_ingest(ingest_dir, str(pdf), backend="pymupdf") is False
    # a different backend: the map does not answer the question being asked now
    assert _stale_ingest(ingest_dir, str(pdf), backend="docling") is True


def test_auto_is_resolved_before_the_converter_is_compared(tmp_path):
    """`--backend auto` is not a converter name, so comparing it literally
    would report every existing map as stale and re-ingest the whole reference
    list on every run."""
    from papertrace.check import _stale_ingest
    from papertrace.ingest import _docling_available
    from papertrace.models import manuscript_fingerprint

    pdf = tmp_path / "smith-2020.pdf"
    _pdf_saying(pdf, "text")
    ingest_dir = tmp_path / "ingest" / "smith-2020"
    ingest_dir.mkdir(parents=True)
    resolved = "docling" if _docling_available() else "pymupdf"
    (ingest_dir / "source_map.json").write_text(json.dumps({
        "doc": "smith-2020.pdf", "pages": 1,
        # a docling map records its version too — "docling 2.x", not "docling"
        "converter": f"{resolved} 9.9.9" if resolved == "docling" else resolved,
        "source_sha256": manuscript_fingerprint(pdf),
        "blocks": [],
    }))

    assert _stale_ingest(ingest_dir, str(pdf), backend="auto") is False


def test_a_source_ingest_that_fails_unchecks_only_that_source(tmp_path, monkeypatch):
    """Reading sources with the layout backend introduces a failure mode flat
    text never had: docling can run out of memory, fail to fetch its models, or
    choke on a malformed PDF. That must uncheck the one source and name the
    reason — not abort a run that has already paid for retrieval, and never be
    laundered into `not_retrieved`, which would blame the publisher for a local
    failure.
    """
    pdf = tmp_path / "sources_resolved" / "smith-2020.pdf"
    _pdf_saying(pdf, "HR 0.88 (0.79-0.98).")
    manifest = RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="Smith J (2020)", status="retrieved", slug="smith-2020",
                 pdf_path=str(pdf))])
    claim = ClaimResult(id=1, claim="Mortality fell.", location="Results", refs=["1"])

    import papertrace.ingest as ingest_mod

    def boom(*a, **kw):
        raise RuntimeError("docling layout model could not be loaded")

    monkeypatch.setattr(ingest_mod, "ingest_pdf", boom)
    called = []
    monkeypatch.setattr(check_mod, "_ask", lambda *a, **k: called.append(1) or "[]")

    errors: list[tuple[str, str]] = []
    check_claims([claim], manifest, tmp_path, on_error=lambda s, m: errors.append((s, m)),
                 backend="docling")

    assert claim.verdict == "unchecked", "a local ingest failure is not a retrieval gap"
    assert "docling layout model" in claim.note
    assert "re-run" in claim.note, "the note must name the fix"
    assert called == [], "no model call once there is no source text to send"
    assert errors and errors[0][0] == "smith-2020"
