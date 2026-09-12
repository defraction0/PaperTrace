"""A passage that crosses a break gets an image for every rectangle it occupies.

`crop_for_anchor` cropped the block's single bbox, so a reader saw where the
passage began and never saw the rest. On a real audited claim the decisive
evidence was in the next column and the crop showed 57 points of the column
before it — the phrase was on the page, outside the frame, and the caption
correctly said nothing could be boxed.

Measured on a real 101-reference audit: 17 of 31 anchored judgements had located
anchor text outside the crop region, and 4 of those continued onto the next
page. This is the common case.

The regions come from `Block.regions`, which is docling's own per-rectangle
provenance — so which rectangles exist is recorded, not inferred. `crop_evidence`
is unchanged: it already boxes exactly the hits intersecting the region it is
given, so calling it per region boxes the right text in every image.

Offline. PDFs are built in-test and never committed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pymupdf  # noqa: E402

from papertrace.highlight import crop_for_anchor  # noqa: E402
from papertrace.models import Block, Region, SourceJudgement, SourceMap  # noqa: E402

LEFT = (40.0, 60.0, 290.0, 240.0)       # where the passage opens
RIGHT = (305.0, 55.0, 555.0, 250.0)     # where it continues — HIGHER up the page
PAGE2 = (40.0, 60.0, 290.0, 160.0)      # and where it finishes, overleaf

OPENS = "Eligible visits were those with two or fewer claims"
CONTINUES = "codes were excluded from the denominator entirely"
FINISHES = "sensitivity analyses confirmed the primary finding"


def _two_page_pdf(dir_: Path) -> Path:
    """A two-column paper whose paragraph runs left column → right column → p2."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=790)
    p1.insert_textbox(pymupdf.Rect(*LEFT[:3], 240),
                      "Cohort selection. " + "We restricted the sample. " * 4 + OPENS,
                      fontsize=9, fontname="helv")
    p1.insert_textbox(pymupdf.Rect(*RIGHT[:3], 250),
                      CONTINUES + " " + "The remainder were retained. " * 4,
                      fontsize=9, fontname="helv")
    p2 = doc.new_page(width=595, height=790)
    p2.insert_textbox(pymupdf.Rect(*PAGE2[:3], 160), FINISHES,
                      fontsize=9, fontname="helv")
    pdf = dir_ / "a-2020.pdf"
    doc.save(pdf)
    doc.close()
    return pdf


def _case(tmp_path: Path, regions: list[Region]) -> tuple[Path, Path, Path]:
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    _two_page_pdf(tmp_path).replace(sources / "a-2020.pdf")
    ingest = tmp_path / "ingest" / "a-2020"
    ingest.mkdir(parents=True, exist_ok=True)
    SourceMap(doc="a-2020.pdf", pages=2, blocks=[
        Block("block_0007", "text", 1, LEFT, [], "the whole passage", regions=regions),
    ]).to_json(ingest / "source_map.json")
    return sources, tmp_path / "ingest", tmp_path / "evidence"


def _judgement(**kw) -> SourceJudgement:
    base = dict(source_slug="a-2020", ref="1", verdict="supported", source_page=1,
                source_block="block_0007",
                anchor_phrases=[OPENS, CONTINUES, FINISHES])
    base.update(kw)
    return SourceJudgement(**base)


THREE = [Region(1, LEFT, 0, 100), Region(1, RIGHT, 100, 200), Region(2, PAGE2, 200, 300)]


def test_one_image_per_region(tmp_path):
    """The defect itself: two of three rectangles were never shown."""
    sources, ingest, out = _case(tmp_path, THREE)
    j = _judgement()
    imgs = crop_for_anchor(j, 1, sources, ingest, out)
    assert len(imgs) == 3, imgs


def test_every_image_boxes_its_own_matched_text(tmp_path):
    """The user's requirement: the relevant text is boxed in ALL images, not
    only the first. Each crop is checked by re-searching its own page."""
    sources, ingest, out = _case(tmp_path, THREE)
    j = _judgement()
    imgs = crop_for_anchor(j, 1, sources, ingest, out)

    doc = pymupdf.open(sources / "a-2020.pdf")
    for img, region, phrase in zip(imgs, THREE, [OPENS, CONTINUES, FINISHES], strict=True):
        assert Path(img).exists(), img
        hits = doc[region.page - 1].search_for(phrase)
        assert hits, f"fixture: {phrase!r} is not on page {region.page}"
        r = pymupdf.Rect(*region.bbox)
        assert any(r.intersects(h) for h in hits), \
            f"{phrase!r} is not inside the region this image renders"
    doc.close()


def test_the_regions_are_cropped_in_reading_order(tmp_path):
    """Not y-order: the continuation sits HIGHER on the page than the opening,
    so sorting by position would show the passage backwards."""
    sources, ingest, out = _case(tmp_path, THREE)
    imgs = crop_for_anchor(_judgement(), 1, sources, ingest, out)
    assert RIGHT[1] < LEFT[1], "fixture: region 2 must be higher up than region 1"
    # page 1 twice then page 2 — the charspan order, which is the passage's order
    assert [Path(i).name for i in imgs] == [
        "claim_01_a-2020_p1.png", "claim_01_a-2020_p1_cont2.png",
        "claim_01_a-2020_p2_cont3.png",
    ], imgs


def test_two_regions_on_one_page_do_not_overwrite_each_other(tmp_path):
    """`crop_evidence` saves unconditionally, so a filename keyed on the page
    alone would silently leave one image holding the other's picture."""
    sources, ingest, out = _case(tmp_path, THREE)
    imgs = crop_for_anchor(_judgement(), 1, sources, ingest, out)
    same_page = [i for i, r in zip(imgs, THREE, strict=True) if r.page == 1]
    assert len(same_page) == 2
    assert len(set(same_page)) == 2, "both page-1 regions wrote to one filename"
    assert Path(same_page[0]).read_bytes() != Path(same_page[1]).read_bytes()


def test_located_is_true_when_only_a_continuation_holds_the_evidence(tmp_path):
    """The real fujima case: nothing matches in the opening rectangle, and the
    boxes are all in the continuation. That is located, not unlocated."""
    sources, ingest, out = _case(tmp_path, THREE)
    j = _judgement(anchor_phrases=[CONTINUES])   # only in region 2
    imgs = crop_for_anchor(j, 1, sources, ingest, out)
    assert len(imgs) == 3
    assert j.anchor_located is True


def test_nothing_anywhere_is_still_not_located(tmp_path):
    """Honest degradation survives: a phrase on none of the rectangles is a miss."""
    sources, ingest, out = _case(tmp_path, THREE)
    j = _judgement(anchor_phrases=["a phrase this paper does not contain at all"])
    crop_for_anchor(j, 1, sources, ingest, out)
    assert j.anchor_located is False


def test_a_single_region_block_still_produces_exactly_one_image(tmp_path):
    """The 193-of-205 case. The name must not change either — it is asserted
    literally in tests/test_pipeline.py and tests/test_multisource.py."""
    sources, ingest, out = _case(tmp_path, [Region(1, LEFT, 0, 100)])
    imgs = crop_for_anchor(_judgement(anchor_phrases=[OPENS]), 1, sources, ingest, out)
    assert [Path(i).name for i in imgs] == ["claim_01_a-2020_p1.png"]


def test_a_source_map_without_regions_behaves_exactly_as_before(tmp_path):
    """Absent regions mean NOT RECORDED, so fall back to the one bbox — the
    behaviour a map written before this change already had."""
    sources, ingest, out = _case(tmp_path, [])
    imgs = crop_for_anchor(_judgement(anchor_phrases=[OPENS]), 1, sources, ingest, out)
    assert [Path(i).name for i in imgs] == ["claim_01_a-2020_p1.png"]


def _rendered(tmp_path: Path, **kw) -> dict[str, str]:
    from papertrace.models import ClaimResult, RunResults
    from papertrace.report import write_reports

    j = SourceJudgement(source_slug="a-2020", ref="1", verdict="supported",
                        source_page=1, source_block="block_0007",
                        anchor_phrases=["x"], anchor_located=True,
                        evidence_image="evidence/claim_01_a-2020_p1.png", **kw)
    claim = ClaimResult(id=1, claim="X causes Y.", location="Intro", refs=["1"],
                        verdict="supported", judgements=[j])
    claim.apply_headline()
    write_reports(RunResults(manuscript="m.pdf", date="2026-01-01", claims=[claim]),
                  None, tmp_path, png=False)
    return {p.name: p.read_text() for p in tmp_path.glob("report*.*")}


def test_every_continuation_image_reaches_all_three_formats(tmp_path):
    """Emitting them is half the job — the user asked for them in the report.
    A crop written to disk and referenced by no format is invisible."""
    conts = ["evidence/claim_01_a-2020_p1_cont2.png",
             "evidence/claim_01_a-2020_p2_cont3.png"]
    rendered = _rendered(tmp_path, continuation_images=conts)
    assert len(rendered) == 3, sorted(rendered)
    for name, body in rendered.items():
        for img in conts:
            assert img in body, f"{name} does not reference {img}"


def test_the_reader_is_told_the_passage_crosses_a_break(tmp_path):
    """Three unexplained images of one source would read as three passages."""
    rendered = _rendered(tmp_path, continuation_images=["evidence/a_cont2.png"])
    for name, body in rendered.items():
        assert "crosses a column or page break" in body, name


def test_the_opening_image_never_claims_a_box_it_may_not_have(tmp_path):
    """Found by rendering and reading it. The judgement's anchor caption was
    inside the FIRST image's figcaption, so a crop where the passage merely
    opens — the real fujima case, no box in it at all — was captioned "red box =
    matched text". The caption describes the set, so it moves below the images."""
    import re

    rendered = _rendered(tmp_path, continuation_images=["evidence/a_cont2.png"])
    caps = re.findall(r"<figcaption>(.*?)</figcaption>", rendered["report_editor.html"], re.S)
    assert caps, "no figcaptions rendered"
    assert "red box = matched text" not in caps[0], caps[0]
    assert "the boxes may be in a later image" in caps[0], caps[0]
    # and it is still disclosed, once, for the judgement
    assert rendered["report_editor.html"].count("red box = matched text") == 1


def test_no_caption_claims_the_box_is_on_the_page_the_verdict_names(tmp_path):
    """A continuation can be on the NEXT page, so "located on this page" was
    false for exactly the case this feature exists to serve."""
    rendered = _rendered(tmp_path, continuation_images=["evidence/a_p3_cont2.png"])
    for name, body in rendered.items():
        assert "located on this page" not in body, name


def test_an_ordinary_single_image_judgement_gains_no_caption(tmp_path):
    """The 193-of-205 case must not grow a sentence about a break it has not."""
    rendered = _rendered(tmp_path, continuation_images=[])
    for name, body in rendered.items():
        assert "crosses a column or page break" not in body, name
        assert "_cont" not in body, name


def test_continuation_images_round_trip_and_older_results_still_load(tmp_path):
    """New field ⇒ schema + round-trip + absent-safe."""
    import json

    from papertrace.models import ClaimResult, RunResults

    j = SourceJudgement(source_slug="a-2020", ref="1", verdict="supported",
                        evidence_image="evidence/p1.png",
                        continuation_images=["evidence/p1_cont2.png"])
    c = ClaimResult(id=1, claim="c", location="Intro", refs=["1"],
                    verdict="supported", judgements=[j])
    c.apply_headline()
    RunResults(manuscript="m.pdf", claims=[c]).to_json(tmp_path / "r.json")
    back = RunResults.from_json(tmp_path / "r.json")
    assert back.claims[0].judgements[0].continuation_images == ["evidence/p1_cont2.png"]
    # apply_headline carried it with the primary, not apart from it
    assert back.claims[0].continuation_images == ["evidence/p1_cont2.png"]

    (tmp_path / "old.json").write_text(json.dumps({
        "manuscript": "m.pdf",
        "claims": [{"id": 1, "claim": "c", "location": "Intro", "refs": ["1"],
                    "verdict": "supported"}],
    }))
    old = RunResults.from_json(tmp_path / "old.json")
    assert old.claims[0].continuation_images == []


def test_a_verdict_shown_only_in_a_continuation_is_not_downgraded(tmp_path):
    """`_downgrade_unshowable` turns a substantive verdict with no image into
    `unchecked`. A passage whose boxes are in its continuation CAN be shown."""
    from papertrace.cli import _downgrade_unshowable

    j = SourceJudgement(source_slug="a-2020", ref="1", verdict="partial",
                        source_page=1, evidence_image=None,
                        continuation_images=["evidence/p2_cont2.png"])
    assert _downgrade_unshowable(j) is False
    assert j.verdict == "partial"


def test_a_page_the_model_named_from_a_continuation_still_crops(tmp_path):
    """The block's own `page` is its opening, so a judgement citing the page the
    continuation is on used to fail the page gate and fall through."""
    sources, ingest, out = _case(tmp_path, THREE)
    j = _judgement(source_page=2, anchor_phrases=[FINISHES])
    imgs = crop_for_anchor(j, 2, sources, ingest, out)
    assert len(imgs) == 3, imgs
    assert j.anchor_located is True
