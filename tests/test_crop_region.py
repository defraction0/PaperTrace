"""The crop region bounds what is *rendered*, not what counts as a match.

A two-column page splits a sentence at the column break, and docling emits the
two halves as two blocks. `check.py` names one of them, so the crop region is
one column — but the quote the judge copied out runs across the break.

`page.search_for(phrase, clip=rect)` cannot serve as the match test there:
PyMuPDF discards the **whole** match when any part of it falls outside the clip,
not just the outside part. Measured on the fixture below, one phrase:

    unclipped        [[268, 206, 278, 219], [40, 220, 154, 232], [305, 60, 407, 72]]
    clip = the block []

So `crop_evidence` returned 0 boxes and the caller recorded
`anchor_located = False` — the report captioning a verbatim, locatable quote as
unboxed. The search is unclipped and the hits are filtered by intersection with
the region instead, which keeps the invariant that decides whether a box can be
trusted: a box is only ever drawn where `search_for` found the text, and only
inside the region actually rendered.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pymupdf  # noqa: E402

from papertrace.highlight import crop_evidence, crop_for_anchor  # noqa: E402
from papertrace.models import Block, SourceJudgement, SourceMap  # noqa: E402

# the left column's paragraph — what a source_map block covers, and the whole of
# what a one-block crop can show
BLOCK = (40.0, 60.0, 290.0, 240.0)
RIGHT_COLUMN = pymupdf.Rect(305, 60, 555, 730)

# runs from the second-to-last line of the left column into the right column's
# first line: three line rects, two of them inside BLOCK
SPANNING = "for evaluation and management codes (CPT codes 99202"


def _two_column_pdf(dir_: Path) -> Path:
    """A page that breaks a sentence at the column break, as a journal does."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=790)
    page.insert_textbox(
        pymupdf.Rect(*BLOCK[:2], BLOCK[2], 730),
        "Cohort selection. "
        + "We restricted the sample to adults. " * 18
        + "Eligible visits were those with two or fewer claims "
        + "for evaluation and management",
        fontsize=9,
        fontname="helv",
    )
    page.insert_textbox(
        RIGHT_COLUMN,
        "codes (CPT codes 99202 to 99499) and five or fewer unique encounters. "
        + "The remaining records were excluded. " * 16,
        fontsize=9,
        fontname="helv",
    )
    pdf = dir_ / "two-column.pdf"
    doc.save(pdf)
    doc.close()
    return pdf


def test_a_quote_crossing_the_column_break_is_boxed(tmp_path):
    """The defect itself: the phrase is on the page, and nothing was boxed."""
    pdf = _two_column_pdf(tmp_path)
    page = pymupdf.open(pdf)[0]
    # the premise — the quote really does straddle the break, and the clipped
    # search really does find none of it
    assert len(page.search_for(SPANNING)) == 3
    assert page.search_for(SPANNING, clip=pymupdf.Rect(*BLOCK)) == []

    assert crop_evidence(pdf, 1, BLOCK, [SPANNING], tmp_path / "crop.png") >= 1


def test_every_hit_inside_the_region_is_boxed_and_only_those(tmp_path):
    """The rule, not a magic number: the region bounds the boxes, and the page
    decides where they go. The third hit is in the other column — outside the
    rendered image, so drawing it would put a red rectangle on the wrong text."""
    pdf = _two_column_pdf(tmp_path)
    page = pymupdf.open(pdf)[0]
    region = pymupdf.Rect(*BLOCK)
    inside = [h for h in page.search_for(SPANNING) if region.intersects(h)]
    assert len(inside) == 2  # guards the fixture: the split is a real one

    assert crop_evidence(pdf, 1, BLOCK, [SPANNING], tmp_path / "crop.png") == len(inside)


def test_a_phrase_outside_the_region_is_never_boxed(tmp_path):
    """What the clip was really for. Unclipping the search must not start
    boxing text the crop does not show — the phrase below is on the page, in
    the other column, and is not this block's evidence."""
    pdf = _two_column_pdf(tmp_path)
    page = pymupdf.open(pdf)[0]
    assert page.search_for("The remaining records were excluded") != []

    boxes = crop_evidence(
        pdf, 1, BLOCK, ["The remaining records were excluded"], tmp_path / "crop.png"
    )
    assert boxes == 0


def test_a_spanning_quote_is_reported_as_located(tmp_path):
    """The consequence a reader sees. `anchor_located = False` captions the crop
    as unboxed — an admission of failure the tool had not actually suffered."""
    sources = tmp_path / "sources"
    sources.mkdir()
    pdf = _two_column_pdf(tmp_path)
    pdf.replace(sources / "a-2020.pdf")

    ingest = tmp_path / "ingest" / "a-2020"
    ingest.mkdir(parents=True)
    SourceMap(
        doc="a-2020.pdf",
        pages=1,
        blocks=[Block("block_0007", "text", 1, BLOCK, [], "Cohort selection.")],
    ).to_json(ingest / "source_map.json")

    j = SourceJudgement(
        source_slug="a-2020", ref="1", verdict="supported", source_page=1,
        source_block="block_0007", anchor_phrases=[SPANNING],
    )
    out = crop_for_anchor(j, 1, sources, tmp_path / "ingest", tmp_path / "evidence")

    assert out is not None
    assert j.anchor_located is True
