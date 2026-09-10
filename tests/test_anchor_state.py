"""True, False and None are three different facts, and stay three.

- `True`  — an anchor phrase was searched for and located.
- `False` — searched for and NOT located.
- `None`  — no search was possible or attempted (no phrases offered, or the
  highlight step never ran).

Two ways the tri-state was being flattened. The CLI branched on truthiness, so
`None` printed "no anchor phrase found on the page" — asserting a search that
never happened. And both disclosure helpers gated on `evidence_image`, so a
judgement with a page but no crop disclosed nothing at all: the reader saw a
verdict with a page number and no statement about whether anything backed it.

The gate is *provenance*, not the picture. A claim with no page says nothing,
because there is nothing to say.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.disclosures import (  # noqa: E402
    ANCHOR_LOCATED_TOKEN,
    ANCHOR_NOT_LOCATED_TOKEN,
    ANCHOR_UNKNOWN_TOKEN,
    anchor_state,
    claim_disclosures,
    judgement_disclosures,
)
from papertrace.models import ClaimResult, SourceJudgement  # noqa: E402


def _j(**kw) -> SourceJudgement:
    base = dict(source_slug="a-2020", ref="1", verdict="supported", source_page=3,
                source_block="block_0007", anchor_phrases=["84.3%"])
    base.update(kw)
    return SourceJudgement(**base)


def test_anchor_state_keeps_three_names():
    assert anchor_state(_j(anchor_located=True)) == "located"
    assert anchor_state(_j(anchor_located=False)) == "not_located"
    assert anchor_state(_j(anchor_located=None)) == "unknown"


def test_a_judgement_with_no_crop_still_discloses_its_anchor_state():
    """The old gate. No `evidence_image`, so the reader was told nothing —
    while the report still printed "Page 3" as if it were provenance."""
    ds = judgement_disclosures(_j(anchor_located=False, evidence_image=None))
    assert [d.token for d in ds] == [ANCHOR_NOT_LOCATED_TOKEN]


def test_an_unknown_anchor_with_no_crop_is_disclosed_too():
    ds = judgement_disclosures(_j(anchor_phrases=[], anchor_located=None, evidence_image=None))
    assert [d.token for d in ds] == [ANCHOR_UNKNOWN_TOKEN]


def test_a_judgement_with_no_page_discloses_nothing():
    """Silence about nothing is not a dropped disclosure. An unretrieved source
    has no page, so there is no anchor claim to qualify."""
    assert judgement_disclosures(_j(source_page=None, verdict="unchecked")) == []


def test_the_no_crop_wording_never_mentions_a_crop():
    """Same token — the parity contract holds — but the sentence must not
    describe a picture that was not written."""
    with_crop = judgement_disclosures(_j(anchor_located=False, evidence_image="e/x.png"))[0]
    without = judgement_disclosures(_j(anchor_located=False, evidence_image=None))[0]

    assert with_crop.token == without.token == ANCHOR_NOT_LOCATED_TOKEN
    assert "crop" in with_crop.text
    assert "crop" not in without.text
    assert "no evidence image" in without.text


def test_located_needs_no_no_crop_variant_but_still_only_fires_with_provenance():
    d = judgement_disclosures(_j(anchor_located=True, evidence_image="e/x.png"))[0]
    assert d.token == ANCHOR_LOCATED_TOKEN


def test_claim_level_disclosure_follows_the_same_rule():
    claim = ClaimResult(id=1, claim="c", location="Results", refs=["1"],
                        verdict="supported", source_slug="a-2020", source_page=3,
                        anchor_phrases=["84.3%"], anchor_located=False)
    keys = [d.key for d in claim_disclosures(claim)]
    assert "anchor" in keys

    ungrounded = ClaimResult(id=2, claim="c", location="Results", refs=["1"],
                             verdict="not_retrieved")
    assert "anchor" not in [d.key for d in claim_disclosures(ungrounded)]
