"""Deterministic citation-coverage audit + uncited-register parsing."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from papertrace.check import (  # noqa: E402
    _parse_json_object,
    citation_labels_in_text,
    coverage_audit,
)
from papertrace.models import ClaimResult, RunResults, UncitedClaim  # noqa: E402

TEXT = """## Introduction

Prevalence is high [1, 2]. Prior imaging work [7-9] and one outlier [12]
showed things. En-dash ranges [15–17] parse too. A bare year (2020) must not.

## References

1. Someone A (2020) A paper with [99] inside its title. J Things 1:1-10.
"""


def test_labels_in_text_ranges_and_refs_exclusion():
    labels = citation_labels_in_text(TEXT)
    assert labels == {"1", "2", "7", "8", "9", "12", "15", "16", "17"}
    assert "99" not in labels  # references section excluded


def test_coverage_audit_reports_missing(tmp_path):
    ingest_dir = tmp_path / "ingest" / "manuscript"
    ingest_dir.mkdir(parents=True)
    (ingest_dir / "clean.md").write_text(TEXT)
    claims = [
        ClaimResult(id=1, claim="a", location="Intro", refs=["1", "2"]),
        ClaimResult(id=2, claim="b", location="Intro", refs=["7", "8", "9"]),
    ]
    cov = coverage_audit(tmp_path, claims)
    assert cov["missing"] == ["12", "15", "16", "17"]
    assert cov["covered"] == ["1", "2", "7", "8", "9"]
    assert len(cov["labels_in_text"]) == 9


def test_extract_object_parsing_with_fences():
    raw = """```json
{"cited":[{"id":1,"claim":"x","location":"Intro","refs":["3"]}],
 "uncited":[{"id":1,"claim":"y","location":"Methods"}]}
```"""
    data = _parse_json_object(raw)
    assert data["cited"][0]["refs"] == ["3"]
    assert data["uncited"][0]["claim"] == "y"


def test_results_roundtrip_with_uncited_and_coverage(tmp_path):
    r = RunResults(
        manuscript="m.pdf",
        converter="docling 2.1",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["1"], verdict="supported")],
        uncited=[UncitedClaim(id=1, claim="no ref here", location="Intro")],
        coverage={"labels_in_text": ["1", "2"], "covered": ["1"], "missing": ["2"]},
    )
    r.to_json(tmp_path / "results.json")
    again = RunResults.from_json(tmp_path / "results.json")
    assert again.converter == "docling 2.1"
    assert again.uncited[0].claim == "no ref here"
    assert again.coverage["missing"] == ["2"]
    # old results.json without the new fields still loads
    old = tmp_path / "old.json"
    old.write_text(
        '{"manuscript":"m.pdf","claims":[{"id":1,"claim":"c","location":"L",'
        '"verdict":"supported"}]}'
    )
    legacy = RunResults.from_json(old)
    assert legacy.uncited == [] and legacy.coverage == {} and legacy.converter == "pymupdf"


def test_unrecognized_citation_style_is_disclosed(tmp_path):
    """Zero bracketed labels + existing claims must yield the explicit
    'not audited' disclosure, never a silent or green-zero coverage line."""
    from papertrace.report import write_reports

    r = RunResults(
        manuscript="m.pdf",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["1"], verdict="supported")],
        coverage={"labels_in_text": [], "covered": [], "missing": []},
    )
    write_reports(r, None, tmp_path, png=False)
    md = (tmp_path / "report.md").read_text()
    assert "coverage not audited" in md
    assert "all 0" not in md
    term = (tmp_path / "report_terminal.html").read_text()
    assert "coverage not audited" in term


def test_coverage_line_is_its_own_paragraph(tmp_path):
    """trim_blocks must not glue the coverage line onto the counts line —
    the markdown bold breaks when '…Not retrieved: 0**Citation coverage:**…'
    renders as one line (regression from the inline unchecked-bucket tag)."""
    from papertrace.report import write_reports

    r = RunResults(
        manuscript="m.pdf",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["1"], verdict="supported")],
        coverage={"labels_in_text": ["1"], "covered": ["1"], "missing": []},
    )
    write_reports(r, None, tmp_path, png=False)
    md = (tmp_path / "report.md").read_text()
    assert "\n**Citation coverage:**" in md
    assert "all 1 citation labels reached by an extracted claim" in md


def test_failed_check_is_unchecked_never_not_retrieved(tmp_path, monkeypatch):
    """A model-call failure on an AVAILABLE source must surface as `unchecked`
    (with the reason and a retry behind it), never as `not_retrieved` — the
    report may not claim a retrieval gap that didn't happen."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry, RefManifest

    src_dir = tmp_path / "ingest" / "good-2020"
    src_dir.mkdir(parents=True)
    (src_dir / "annotated.md").write_text("Some source text.")
    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="Good G (2020) X.", status="retrieved",
                          slug="good-2020", pdf_path="unused.pdf")],
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]

    calls = []

    def boom(prompt, model=None):
        calls.append(1)
        raise RuntimeError("claude -p failed: transient")

    monkeypatch.setattr(check_mod, "_ask", boom)
    errors = []
    check_claims(claims, manifest, tmp_path, on_error=lambda s, m: errors.append((s, m)))

    assert len(calls) == 2  # one retry before giving up
    assert claims[0].verdict == "unchecked"
    assert "source WAS retrieved" in claims[0].note
    assert errors and errors[0][0] == "good-2020"

    # and a genuinely missing source still reads not_retrieved
    claims2 = [ClaimResult(id=2, claim="d", location="Intro", refs=["9"])]
    check_claims(claims2, manifest, tmp_path)
    assert claims2[0].verdict == "not_retrieved"


def _ingested(dirpath, slug: str, pages: int = 2) -> None:
    """An ingested source: the text AND the map that proves where its blocks are.

    Both, always — a source map is no longer optional. `check` validates every
    substantive verdict's page and block against it, so a source without one
    can produce no verdict at all.
    """
    from papertrace.models import Block, SourceMap

    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "annotated.md").write_text(
        f"<!-- block_0001, page 1 -->\nText of {slug}.\n"
        f"<!-- block_0002, page 2 -->\nMore of {slug}.\n"
    )
    SourceMap(
        doc=f"{slug}.pdf", pages=pages,
        blocks=[
            Block("block_0001", "text", 1, (0.0, 0.0, 100.0, 20.0), [], f"Text of {slug}."),
            Block("block_0002", "text", 2, (0.0, 0.0, 100.0, 20.0), [], f"More of {slug}."),
        ],
    ).to_json(dirpath / "source_map.json")


def _one_source_manifest(tmp_path, *entries):
    """Manifest + a fully ingested source for every retrieved entry."""
    from papertrace.models import RefManifest

    for e in entries:
        if e.status in ("retrieved", "provided"):
            _ingested(tmp_path / "ingest" / e.slug, e.slug)
    return RefManifest(manuscript="m.pdf", entries=list(entries))


def test_malformed_verdict_becomes_unchecked_never_partial(tmp_path, monkeypatch):
    """A response object the model returns without a usable `verdict` must not
    be laundered into a real-looking `partial` — the source was read, the
    judgement was not."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="1", raw="A (2020) X.", status="retrieved", slug="a-2020", pdf_path="x.pdf"),
    )
    claims = [
        ClaimResult(id=1, claim="missing key", location="Intro", refs=["1"]),
        ClaimResult(id=2, claim="junk value", location="Intro", refs=["1"]),
    ]
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: '[{"id":1,"note":"no verdict key"},'
                                   ' {"id":2,"verdict":"probably fine","note":"junk"}]',
    )
    check_claims(claims, manifest, tmp_path)

    assert claims[0].verdict == "unchecked"
    assert claims[1].verdict == "unchecked"
    assert "unusable verdict" in claims[0].note
    assert all(c.verdict != "partial" for c in claims)


def test_multiref_claim_records_only_the_sources_it_could_not_obtain(tmp_path, monkeypatch):
    """Behaviour changed deliberately: batch used to judge `avail[0]` only and
    file every co-citation as unjudged. Every obtainable cited source is judged
    now, so `unjudged_refs` means one thing — the source could not be had.

    See tests/test_multisource.py for the fan-out itself; this pins the boundary
    between the two registers, which is where a regression would hide.
    """
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="3", raw="C (2020) X.", status="retrieved", slug="c-2020", pdf_path="x.pdf"),
        RefEntry(num="7", raw="D (2021) Y.", status="retrieved", slug="d-2021", pdf_path="y.pdf"),
        RefEntry(num="9", raw="E (2022) Z.", status="paywalled"),
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["3", "7", "9"])]
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: '[{"id":1,"verdict":"supported","note":"ok",'
                                   ' "source_page":1,"source_block":"block_0001",'
                                   ' "anchor_phrases":["Text"]}]',
    )
    check_claims(claims, manifest, tmp_path)

    c = claims[0]
    assert c.verdict == "supported"
    # [3] and [7] were both retrieved, so both were judged...
    assert sorted(j.ref for j in c.judgements) == ["3", "7"]
    assert {j.verdict for j in c.judgements} == {"supported"}
    # ... and only the paywalled [9] remains unopened
    assert c.unjudged_refs == ["9"]


def test_unjudged_refs_are_disclosed_in_the_report(tmp_path):
    """A verdict must say on the page which co-cited sources nobody could read.

    The wording changed with multi-source checking: an obtainable co-citation is
    judged now, so this register means *unobtainable* and says so, rather than
    the older ambiguous "not opened" that covered both.
    """
    from papertrace.disclosures import UNJUDGED_TOKEN
    from papertrace.report import write_reports

    r = RunResults(
        manuscript="m.pdf",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["3", "7"],
                            verdict="supported", source_slug="c-2020",
                            unjudged_refs=["7"])],
    )
    write_reports(r, None, tmp_path, png=False)
    md = (tmp_path / "report.md").read_text()
    assert UNJUDGED_TOKEN in md
    assert "[7]" in md          # the label a reader has to look up
    assert "c-2020" in md


def test_truncated_input_is_recorded_and_disclosed(tmp_path, monkeypatch):
    """Text past the character limit is never sent to the model, so the report
    must say it was never checked rather than passing silently."""
    import papertrace.check as check_mod
    from papertrace.check import Truncations, extract_claims
    from papertrace.report import write_reports

    d = tmp_path / "ingest" / "manuscript"
    d.mkdir(parents=True)
    (d / "annotated.md").write_text("x" * (check_mod.MANUSCRIPT_CHAR_LIMIT + 500))

    seen = {}

    def fake_ask(prompt, model=None):
        seen["len"] = len(prompt)
        return '{"cited":[],"uncited":[]}'

    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    truncations = Truncations()
    extract_claims(tmp_path, truncations=truncations)

    report = truncations.report()
    assert report["manuscript"]["limit"] == check_mod.MANUSCRIPT_CHAR_LIMIT
    assert report["manuscript"]["chars"] == check_mod.MANUSCRIPT_CHAR_LIMIT + 500
    # the over-limit text really was cut before reaching the model: the payload
    # is the prompt plus exactly the limit, not the full manuscript
    assert seen["len"] == len(check_mod.EXTRACT_PROMPT) + check_mod.MANUSCRIPT_CHAR_LIMIT

    r = RunResults(manuscript="m.pdf", claims=[], truncated=report)
    write_reports(r, None, tmp_path, png=False)
    assert "Input truncated" in (tmp_path / "report.md").read_text()


def test_short_input_records_no_truncation(tmp_path, monkeypatch):
    import papertrace.check as check_mod
    from papertrace.check import Truncations, extract_claims

    d = tmp_path / "ingest" / "manuscript"
    d.mkdir(parents=True)
    (d / "annotated.md").write_text("a short manuscript")
    monkeypatch.setattr(check_mod, "_ask", lambda p, model=None: '{"cited":[],"uncited":[]}')
    truncations = Truncations()
    extract_claims(tmp_path, truncations=truncations)
    assert truncations.report() == {}


def test_results_roundtrip_keeps_the_new_honesty_fields(tmp_path):
    r = RunResults(
        manuscript="m.pdf",
        claims=[ClaimResult(id=1, claim="c", location="L", refs=["3", "7"],
                            verdict="supported", unjudged_refs=["7"],
                            anchor_located=False)],
        uncited=[UncitedClaim(id=1, claim="u")],
        truncated={"manuscript": {"chars": 200, "limit": 100}},
    )
    path = tmp_path / "results.json"
    r.to_json(path)
    back = RunResults.from_json(path)
    assert back.claims[0].unjudged_refs == ["7"]
    assert back.claims[0].anchor_located is False
    assert back.truncated == {"manuscript": {"chars": 200, "limit": 100}}


def test_results_json_without_new_fields_still_loads(tmp_path):
    """Case folders written by an older build must keep opening."""
    import json

    path = tmp_path / "results.json"
    path.write_text(json.dumps({
        "manuscript": "m.pdf",
        "claims": [{"id": 1, "claim": "c", "location": "L", "refs": ["1"],
                    "verdict": "supported"}],
    }))
    back = RunResults.from_json(path)
    assert back.claims[0].unjudged_refs == []
    assert back.claims[0].anchor_located is None
    assert back.truncated == {}


def test_model_may_not_assign_a_pipeline_state(tmp_path, monkeypatch):
    """`not_retrieved` and `unchecked` are pipeline states, not judgements: only
    PaperTrace assigns them. A model returning one for a source that WAS
    retrieved must land as `unchecked` — and must keep `source_slug`, because
    the source really was available and that pair is consistent, not a leak."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="1", raw="A (2020) X.", status="retrieved", slug="a-2020", pdf_path="x.pdf"),
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: '[{"id":1,"verdict":"not_retrieved","note":"could not find"}]',
    )
    check_claims(claims, manifest, tmp_path)

    c = claims[0]
    assert c.verdict == "unchecked"
    assert "pipeline state" in c.note
    assert "not_retrieved" in c.note
    assert c.source_slug == "a-2020"  # the source WAS retrieved — keep the pairing


def test_verdict_vocabulary_splits_without_changing_the_wire_format():
    """The split is a refactor of the vocabulary, not of the wire format: the
    concatenation reproduces today's tuple exactly, so counts() key order, the
    schema enum and every rendered report stay byte-identical."""
    import json

    from papertrace.models import JUDGMENT_VERDICTS, PIPELINE_STATES, VERDICTS

    assert VERDICTS == JUDGMENT_VERDICTS + PIPELINE_STATES
    assert not set(JUDGMENT_VERDICTS) & set(PIPELINE_STATES)

    schema = json.loads((ROOT / "schemas" / "results.schema.json").read_text())
    enum = schema["properties"]["claims"]["items"]["properties"]["verdict"]["enum"]
    assert set(VERDICTS) == set(enum)


def test_verdict_without_a_source_page_is_unchecked_not_page_none(tmp_path, monkeypatch):
    """A verdict with no `source_page` is unfalsifiable: the report prints a
    literal "Page None", `crop_for_claim` bails so no evidence crop exists, and
    the reader cannot check it. It becomes `unchecked`, not a bare verdict."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry
    from papertrace.report import write_reports

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="1", raw="A (2020) X.", status="retrieved", slug="a-2020", pdf_path="x.pdf"),
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: '[{"id":1,"verdict":"supported","note":"ok",'
                                   ' "anchor_phrases":["Text"]}]',
    )
    check_claims(claims, manifest, tmp_path)

    assert claims[0].verdict == "unchecked"
    assert "source_page" in claims[0].note

    out = tmp_path / "out"
    write_reports(RunResults(manuscript="m.pdf", claims=claims), None, out, png=False)
    assert "Page None" not in (out / "report.md").read_text()


def test_a_rejected_response_writes_nothing_to_the_claim(tmp_path, monkeypatch):
    """Validation is atomic: a response rejected on one field must not leave
    half-applied provenance from the fields that happened to parse first."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="1", raw="A (2020) X.", status="retrieved", slug="a-2020", pdf_path="x.pdf"),
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]
    # everything valid EXCEPT the required source_page
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: '[{"id":1,"verdict":"supported","note":"ok",'
                                   ' "source_block":"block_0001","anchor_phrases":["Text"]}]',
    )
    check_claims(claims, manifest, tmp_path)

    c = claims[0]
    assert c.verdict == "unchecked"
    assert c.source_page is None
    assert c.source_block is None
    assert c.anchor_phrases == []


def test_null_anchor_phrases_is_rejected_without_poisoning_siblings(tmp_path, monkeypatch):
    """`anchor_phrases: null` used to raise an uncaught TypeError outside the
    per-group try, killing the whole stage. One bad entry must cost exactly one
    claim — its siblings in the same group keep their valid verdicts."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="1", raw="A (2020) X.", status="retrieved", slug="a-2020", pdf_path="x.pdf"),
    )
    claims = [
        ClaimResult(id=1, claim="poisoner", location="Intro", refs=["1"]),
        ClaimResult(id=2, claim="sibling", location="Intro", refs=["1"]),
    ]
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None:
            '[{"id":1,"verdict":"supported","note":"ok","source_page":1,'
            ' "source_block":"block_0001","anchor_phrases":null},'
            ' {"id":2,"verdict":"partial","note":"fine","source_page":2,'
            ' "source_block":"block_0002","anchor_phrases":["Text"]}]',
    )
    check_claims(claims, manifest, tmp_path)  # must not raise

    assert claims[0].verdict == "unchecked"
    assert "anchor_phrases" in claims[0].note
    assert claims[1].verdict == "partial"
    assert claims[1].source_page == 2
    assert claims[1].anchor_phrases == ["Text"]


def test_a_bug_in_our_validator_is_not_relabelled_as_the_models_fault(tmp_path, monkeypatch):
    """There is deliberately NO per-claim `except Exception`. A blanket catch
    would turn an AttributeError in our own code into a note blaming the model
    — verdict laundering moved up one level. Our bugs must surface as crashes."""
    import papertrace.check as check_mod
    from papertrace.check import check_claims
    from papertrace.models import RefEntry

    manifest = _one_source_manifest(
        tmp_path,
        RefEntry(num="1", raw="A (2020) X.", status="retrieved", slug="a-2020", pdf_path="x.pdf"),
    )
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]
    monkeypatch.setattr(
        check_mod, "_ask",
        lambda prompt, model=None: '[{"id":1,"verdict":"supported","note":"ok",'
                                   ' "source_page":1,"anchor_phrases":[]}]',
    )

    def our_bug(entry, provenance):
        raise AttributeError("a bug in PaperTrace, not in the model's answer")

    monkeypatch.setattr(check_mod, "_judgement_from", our_bug)
    with pytest.raises(AttributeError):
        check_claims(claims, manifest, tmp_path)


def test_truncations_cannot_leak_between_runs(tmp_path, monkeypatch):
    """Truncation state belongs to a run, not to the module. A second run in
    the same process must not inherit the first run's cuts, and there must be
    no global left anywhere to fall back to."""
    import papertrace.check as check_mod
    from papertrace.check import Truncations, extract_claims

    assert not hasattr(check_mod, "_TRUNCATED")         # deleted outright...
    assert not hasattr(check_mod, "truncation_report")  # ...and not shimmed

    monkeypatch.setattr(check_mod, "_ask", lambda p, model=None: '{"cited":[],"uncited":[]}')

    def case(name: str, text: str) -> Path:
        d = tmp_path / name / "ingest" / "manuscript"
        d.mkdir(parents=True)
        (d / "annotated.md").write_text(text)
        return tmp_path / name

    long_case = case("long", "x" * (check_mod.MANUSCRIPT_CHAR_LIMIT + 1))
    short_case = case("short", "a short manuscript")

    first = Truncations()
    extract_claims(long_case, truncations=first)
    second = Truncations()
    extract_claims(short_case, truncations=second)

    assert list(first.report()) == ["manuscript"]
    assert second.report() == {}  # the truncated run cannot bleed into this one

    # a run given no accumulator records nothing, anywhere — no global fallback
    extract_claims(long_case)
    assert second.report() == {}
    assert list(first.report()) == ["manuscript"]


# ---------------------------------------------------------------------------
# occurrence-level coverage
#
# Label-level coverage is pure set arithmetic: two sentences citing [3] with
# only one extracted claim reported [3] as covered, and the omitted sentence
# was invisible. These tests pin the occurrence audit that replaces that
# overstatement — including the ways it is *weaker*, which are disclosed on
# the report's face rather than merely known.
# ---------------------------------------------------------------------------



def _case_with_source_map(tmp_path: Path, blocks: list[dict], clean: str | None = None) -> Path:
    """A case folder whose manuscript carries a source map (and a clean.md)."""
    import json

    d = tmp_path / "ingest" / "manuscript"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "doc": "m.pdf",
        "pages": max((b["page"] for b in blocks), default=1),
        "converter": "docling 2.118.1",
        "blocks": [
            {
                "id": b["id"],
                "type": b.get("type", "text"),
                "page": b["page"],
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "heading_path": b.get("heading_path", []),
                "text": b["text"],
                "text_preview": b["text"][:100],
            }
            for b in blocks
        ],
    }
    (d / "source_map.json").write_text(json.dumps(payload))
    if clean is None:
        clean = "\n\n".join(
            ("## " + b["text"]) if b.get("type") == "sectionheader" else b["text"]
            for b in blocks
        )
    (d / "clean.md").write_text(clean)
    return tmp_path


# The demo manuscript's own failing sentence, reproduced verbatim rather than
# read from `demo_case/`. That folder is gitignored — a test that copies out of
# it passes here and fails on a fresh clone, which is exactly the kind of
# "works on my machine" the rest of this suite is built to avoid.
_DEMO_BLOCKS = [
    {"id": "block_0008", "type": "sectionheader", "page": 1,
     "text": "Population imaging", "heading_path": ["Population imaging"]},
    {"id": "block_0009", "page": 1, "heading_path": ["Population imaging"],
     "text": (
         "Dedicated cohorts complement such opportunistic reuse: the UK Biobank "
         "cohort profile describes recruitment of approximately 500,000 adults "
         "aged 40-69 years [2], and its imaging enhancement targets 100,000 "
         "participants [3]. Attendance logistics remain a bottleneck, however - "
         "nearly one in five confirmed participants had not attended an imaging "
         "assessment centre [3]."
     )},
    {"id": "block_0012", "type": "sectionheader", "page": 1, "text": "References"},
    {"id": "block_0013", "page": 1,
     "text": "[2] Sudlow C, et al. UK Biobank. PLoS Med. 2015.\n"
             "[3] Littlejohns TJ, et al. The UK Biobank imaging enhancement. 2020."},
]


def _demo_manuscript(tmp_path: Path) -> Path:
    """The two-sentences-one-label case, self-contained."""
    return _case_with_source_map(tmp_path, _DEMO_BLOCKS)


def test_two_sentences_citing_one_label_are_two_occurrences(tmp_path):
    """The demo manuscript carries this failing case: one line cites [3] twice,
    in two different sentences. Extract only one of them and the other must be
    reported — under label-level arithmetic it was invisible, because the *set*
    {3} was covered by the *set* {3}."""
    case = _demo_manuscript(tmp_path)
    claims = [
        ClaimResult(id=1, claim="The UK Biobank imaging enhancement targets 100,000 participants",
                    location="Population imaging", refs=["3"]),
    ]
    cov = coverage_audit(case, claims)

    three = [o for o in cov["occurrences"]["items"] if o["label"] == "3"]
    assert len(three) == 2, "both sentences citing [3] must be occurrences"
    assert sum(1 for o in three if o["status"] == "covered") <= 1

    # the sentence nobody extracted is named, with a page a reader can turn to
    omitted = next(o for o in three if "not attended an imaging assessment" in o["sentence"])
    assert omitted["status"] in ("uncovered", "uncertain")
    assert omitted["page"] == 1
    assert omitted["block"] and omitted["block"].startswith("block_")

    # ... and the label-level view is unchanged, byte for byte
    assert "3" in cov["covered"] and "3" not in cov["missing"]
    assert "3" in cov["labels_partially_covered"] + cov["labels_uncertain_only"]


def test_a_label_reached_only_once_is_partially_covered_not_covered(tmp_path):
    """The headline the report owes its reader: the ratio counts occurrences,
    and says how many are unaddressed and how many merely uncertain."""
    from papertrace.disclosures import COVERAGE_TOKEN, coverage_headline

    case = _demo_manuscript(tmp_path)
    claims = [
        ClaimResult(id=1, claim="The UK Biobank imaging enhancement targets 100,000 participants",
                    location="Population imaging", refs=["3"]),
    ]
    cov = coverage_audit(case, claims)
    occ = cov["occurrences"]

    assert occ["total"] == occ["covered"] + occ["uncovered"] + occ["uncertain"]
    head = coverage_headline(cov)
    assert COVERAGE_TOKEN in head
    assert "citation occurrences" in head
    assert "uncertain" in head  # never folded into the ratio, always printed


def test_ambiguous_attribution_is_uncertain_and_never_counted_as_covered(tmp_path):
    """Two indistinguishable sentences citing [5], one claim. The tool knows a
    claim reached the label and cannot know which sentence — so neither is
    covered, and `uncertain` is a third status, not a rounding of either."""
    case = _case_with_source_map(tmp_path, [
        {"id": "block_0001", "type": "sectionheader", "page": 1, "text": "Methods"},
        {"id": "block_0002", "page": 1,
         "text": "The cohort was imaged twice [5]. The cohort was imaged twice [5]."},
    ])
    claims = [ClaimResult(id=1, claim="the cohort was imaged twice",
                          location="Methods", refs=["5"])]
    cov = coverage_audit(case, claims)

    assert cov["occurrences"]["covered"] == 0
    assert cov["occurrences"]["uncertain"] == 2
    assert [o["status"] for o in cov["occurrences"]["items"]] == ["uncertain", "uncertain"]
    assert cov["labels_uncertain_only"] == ["5"]
    assert cov["covered"] == ["5"]  # the published label-level key is unchanged


def test_the_audit_reads_the_source_map_and_records_page_and_block(tmp_path):
    """Occurrences come from source_map.json, not annotated.md — whose inline
    `<!-- block_NNNN, page N -->` markers shift every offset."""
    case = _case_with_source_map(tmp_path, [
        {"id": "block_0006", "type": "sectionheader", "page": 3, "text": "Discussion",
         "heading_path": ["Discussion"]},
        {"id": "block_0007", "page": 3, "heading_path": ["Discussion"],
         "text": "Screening uptake was low [7,8] in the second wave."},
    ])
    cov = coverage_audit(case, [])

    assert cov["source"] == "source_map"
    assert cov["unit"] == "occurrence"
    items = cov["occurrences"]["items"]
    assert [o["label"] for o in items] == ["7", "8"]
    for o in items:
        assert o["page"] == 3
        assert o["block"] == "block_0007"
        assert o["section"] == "Discussion"
        assert o["group"] == "7,8"
        assert o["id"] == f"block_0007:{o['offset']}:{o['label']}"
    # a group cites two labels at one place: same block and offset, distinct id
    assert items[0]["offset"] == items[1]["offset"]
    assert items[0]["id"] != items[1]["id"]


def test_clean_md_fallback_produces_identical_label_level_keys(tmp_path):
    """A case folder can legitimately lack a source map. The occurrence view
    degrades (no page, no block); the published label-level keys do not."""
    mapped = _case_with_source_map(tmp_path / "mapped", [
        {"id": "block_0001", "type": "sectionheader", "page": 2, "text": "Introduction"},
        {"id": "block_0002", "page": 2, "text": "Prevalence is high [1, 2] and rising [7-9]."},
    ])
    flat = tmp_path / "flat" / "ingest" / "manuscript"
    flat.mkdir(parents=True)
    (flat / "clean.md").write_text(
        (mapped / "ingest" / "manuscript" / "clean.md").read_text()
    )

    a = coverage_audit(mapped, [])
    b = coverage_audit(tmp_path / "flat", [])
    for key in ("labels_in_text", "covered", "missing"):
        assert a[key] == b[key], key

    assert b["source"] == "clean.md"
    assert all(o["id"].startswith("clean:") for o in b["occurrences"]["items"])
    assert all(o["page"] is None and o["block"] is None
               for o in b["occurrences"]["items"])
    assert b["occurrences"]["total"] == a["occurrences"]["total"] == 5


def test_references_section_occurrences_are_excluded_structurally(tmp_path):
    """The exclusion is a block-type test on the source map, not a regex that
    works only because ingest happens to render headings as `## References`."""
    case = _case_with_source_map(tmp_path, [
        {"id": "block_0001", "type": "sectionheader", "page": 1, "text": "Background"},
        {"id": "block_0002", "page": 1, "text": "Uptake was low [4]."},
        {"id": "block_0003", "type": "sectionheader", "page": 2, "text": "References"},
        {"id": "block_0004", "page": 2, "text": "1. Someone A. A paper with [99] in its title."},
    ], clean="Background\n\nUptake was low [4].\n\nReferences\n\n1. A paper with [99].\n")

    cov = coverage_audit(case, [])
    assert [o["label"] for o in cov["occurrences"]["items"]] == ["4"]
    # The two readings now agree, and that is the point of the fix rather than a
    # restatement of it. This line used to assert the opposite — that the label
    # reading DID see [99] — as proof that the occurrence walk's structural test
    # was doing independent work. It was proof of a defect: the headings here
    # carry no `##`, the label reading cut on `^##\s+references`, and so a label
    # printed only inside the reference list was counted as a body citation and
    # reported as an uncovered gap. Both readings cut on
    # `models.is_references_heading` now.
    assert "99" not in citation_labels_in_text(
        (case / "ingest" / "manuscript" / "clean.md").read_text()
    )
    assert cov["labels_in_text"] == ["4"]


def test_surplus_claims_are_recorded_without_making_anything_uncertain(tmp_path):
    """More claims citing [2] than there are places citing [2]: the extra claim
    is reported as unattributed, and does not cast doubt on the occurrence."""
    case = _case_with_source_map(tmp_path, [
        {"id": "block_0001", "page": 1, "text": "Recruitment reached 500,000 adults [2]."},
    ])
    claims = [
        ClaimResult(id=1, claim="recruitment reached 500,000 adults", location="", refs=["2"]),
        ClaimResult(id=2, claim="the cohort is large", location="", refs=["2"]),
    ]
    cov = coverage_audit(case, claims)

    assert cov["occurrences"]["covered"] == 1
    assert cov["occurrences"]["uncertain"] == 0
    assert [c["claim_id"] for c in cov["attribution"]["claims_unattributed"]] == [2]


def test_coverage_v2_round_trips_and_validates_against_the_schema(tmp_path):
    import json

    import jsonschema

    case = _case_with_source_map(tmp_path / "case", [
        {"id": "block_0001", "page": 1, "text": "Uptake was low [4] and falling [4]."},
    ])
    cov = coverage_audit(case, [ClaimResult(id=1, claim="uptake was low", location="",
                                            refs=["4"])])
    r = RunResults(manuscript="m.pdf", claims=[], coverage=cov)
    path = tmp_path / "results.json"
    r.to_json(path)
    assert RunResults.from_json(path).coverage == cov

    schema = json.loads((ROOT / "schemas" / "results.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))

    # a legacy label-level block still validates: nothing new is required
    legacy = RunResults(manuscript="m.pdf", claims=[],
                        coverage={"labels_in_text": ["1"], "covered": ["1"], "missing": []})
    legacy.to_json(path)
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))


def _render_all(results, out: Path) -> dict[str, str]:
    from papertrace.report import write_reports

    write_reports(results, None, out, png=False)
    return {
        name: (out / name).read_text()
        for name in ("report.md", "report_editor.html", "report_terminal.html")
    }


def _v2_coverage(tmp_path: Path):
    case = _case_with_source_map(tmp_path / "case", [
        {"id": "block_0001", "type": "sectionheader", "page": 4, "text": "Discussion"},
        {"id": "block_0002", "page": 4, "heading_path": ["Discussion"],
         "text": "Attendance was a bottleneck [3]. One in five never attended [3]."},
    ])
    return coverage_audit(
        case,
        [ClaimResult(id=1, claim="attendance was a bottleneck",
                     location="Discussion", refs=["3"])],
    )


def test_uncovered_occurrences_are_listed_with_page_and_sentence_everywhere(tmp_path):
    """The report shows what it did NOT do: every unaddressed or uncertain
    occurrence, with the page and the sentence, in all three formats."""
    cov = _v2_coverage(tmp_path)
    rendered = _render_all(RunResults(manuscript="m.pdf", claims=[], coverage=cov), tmp_path / "out")

    for name, body in rendered.items():
        assert "citation occurrences" in body, name
        assert "one in five never attended" in body.lower(), name
        assert "p.4" in body or "page 4" in body.lower(), name


def test_the_attribution_self_caveat_is_in_all_three_formats(tmp_path):
    """Occurrence coverage stops being pure arithmetic, so the report carries
    its own caveat — as a Disclosure with a token, which is what makes the
    parity rule mechanical rather than a checklist."""
    from papertrace.disclosures import COVERAGE_ATTRIBUTION_TOKEN, run_disclosures

    cov = _v2_coverage(tmp_path)
    results = RunResults(manuscript="m.pdf", claims=[], coverage=cov)
    rendered = _render_all(results, tmp_path / "out")

    caveat = next(d for d in run_disclosures(results) if d.key == "coverage_attribution")
    assert caveat.token == COVERAGE_ATTRIBUTION_TOKEN
    for name, body in rendered.items():
        assert caveat.token in body, f"self-caveat missing from {name}"


def test_a_v1_results_json_still_renders_its_label_level_line(tmp_path):
    """An old results.json carries no `occurrences`. It must keep rendering the
    label-level line rather than a broken or empty occurrence headline."""
    import json

    path = tmp_path / "old.json"
    path.write_text(json.dumps({
        "manuscript": "m.pdf",
        "claims": [{"id": 1, "claim": "c", "location": "L", "refs": ["1"],
                    "verdict": "supported"}],
        "coverage": {"labels_in_text": ["1", "2"], "covered": ["1"], "missing": ["2"]},
    }))
    results = RunResults.from_json(path)
    rendered = _render_all(results, tmp_path / "out")

    for name, body in rendered.items():
        assert "1/2 citation labels reached by an extracted claim" in body, name
        assert "citation occurrences" not in body, name


def test_the_occurrence_list_is_capped_with_a_pointer_to_results_json(tmp_path):
    """A manuscript that cites [9] thirty times must not print thirty rows in
    the terminal look — the full list is in results.json and says so."""
    text = " ".join(f"Sentence number {i} says a thing [9]." for i in range(30))
    case = _case_with_source_map(tmp_path / "case", [
        {"id": "block_0001", "page": 1, "text": text},
    ])
    cov = coverage_audit(case, [])
    assert cov["occurrences"]["uncovered"] == 30

    rendered = _render_all(RunResults(manuscript="m.pdf", claims=[], coverage=cov),
                           tmp_path / "out")
    assert "… 5 more in `results.json`" in rendered["report.md"]
    assert "… 22 more in results.json" in rendered["report_terminal.html"]


# --- _judgement_from is total by construction, and stays that way -----------


def _prov():
    """A three-page source with one block per page — enough that the page-shape
    guards below fail on the shape, not on a location that doesn't exist."""
    from papertrace.check import SourceProvenance

    return SourceProvenance(
        pages=3, block_pages={"block_0001": 1, "block_0002": 2, "block_0003": 3}
    )


@pytest.mark.parametrize("page", [
    "9" * 5000,          # passes isascii() and isdigit(), then int() raises
    "9" * 4301,          # one past CPython's default limit
    "0" * 6000,          # leading zeros: cheap to convert, still not a page
], ids=["9x5000", "9x4301", "0x6000"])
def test_an_absurdly_long_page_number_degrades_instead_of_raising(page):
    """`_judgement_from` is documented total by construction — every branch an
    isinstance test, so a malformed model response yields `unchecked` with a
    note, never an exception. A 5,000-digit ASCII page passed both guards and
    then hit CPython's integer string-conversion limit, so a `ValueError` escaped
    into the group loop and could abort a whole claim group.
    """
    import papertrace.check as check_mod

    j, note = check_mod._judgement_from(
        {"id": 1, "verdict": "supported", "source_page": page,
         "source_block": "block_0003"}, _prov()
    )
    assert j is None
    assert "unusable" in note


@pytest.mark.parametrize("page", [
    float("nan"), float("inf"), [3], {"p": 3}, (3,), b"3", "²", "３", " 3 ", "3.0", "-1", 0,
], ids=["nan", "inf", "list", "dict", "tuple", "bytes", "superscript2",
        "fullwidth3", "padded3", "float-str", "negative", "zero"])
def test_other_page_shapes_still_degrade_rather_than_raise(page):
    """The neighbouring shapes, pinned together so a future guard cannot fix one
    by breaking another. `" 3 "` is deliberately accepted after stripping."""
    import papertrace.check as check_mod

    j, note = check_mod._judgement_from(
        {"id": 1, "verdict": "supported", "source_page": page,
         "source_block": "block_0003"}, _prov()
    )
    if isinstance(page, str) and page.strip() == "3":
        assert j is not None and j["source_page"] == 3  # a dict, not a dataclass
    else:
        assert j is None, f"{page!r} produced a judgement"
        assert "unusable" in note


# --- one boundary for the bibliography, shared by both readers --------------


def _map_with_body_typed_references(case_dir: Path) -> Path:
    """A source map shaped the way flat ingest really produced one.

    On a real Elsevier paper the font-size heuristic typed author lines as
    headings and left `References` as body text. `references_section` was taught
    to accept that; occurrence scanning was not.
    """
    from papertrace.models import Block, SourceMap

    blocks = [
        Block(id="block_0001", type="text", page=1, bbox=(0, 0, 10, 10),
              heading_path=["Methods"], text="Body text citing [1] once."),
        Block(id="block_0002", type="text", page=2, bbox=(0, 0, 10, 10),
              heading_path=[], text="References"),
        Block(id="block_0003", type="text", page=2, bbox=(0, 0, 10, 10),
              heading_path=[], text="[1] Smith A. First paper. 2020."),
        Block(id="block_0004", type="text", page=2, bbox=(0, 0, 10, 10),
              heading_path=[], text="[2] Jones B. Second paper. 2021."),
    ]
    out = case_dir / "ingest" / "manuscript"
    out.mkdir(parents=True)
    smap = SourceMap(doc="p.pdf", pages=2, converter="pymupdf", blocks=blocks)
    smap.to_json(out / "source_map.json")
    return out / "source_map.json"


def test_the_bibliography_is_not_counted_as_body_citations(tmp_path):
    """Coverage counted every `[N]` in the reference list as a manuscript
    citation, so a paper with one real citation reported three occurrences and
    two invented gaps. `references_section` stopped at a body-typed `References`
    block; `citation_occurrences` only stopped at a `sectionheader`.
    """
    from papertrace.check import citation_occurrences

    _map_with_body_typed_references(tmp_path)
    occ, source = citation_occurrences(tmp_path)

    assert source == "source_map"
    assert [o["label"] for o in occ] == ["1"], occ
    assert all(o["block"] == "block_0001" for o in occ)


def test_both_readers_agree_on_where_the_bibliography_starts(tmp_path):
    """The two must not re-derive the boundary independently — that divergence
    is the defect. Same source map, same answer."""
    from papertrace.check import citation_occurrences
    from papertrace.ingest import references_section
    from papertrace.models import SourceMap

    path = _map_with_body_typed_references(tmp_path)
    smap = SourceMap.from_json(path)

    refs_text = references_section(smap)
    assert "Smith" in refs_text and "Jones" in refs_text
    assert "Body text" not in refs_text

    occ, _ = citation_occurrences(tmp_path)
    assert [o["label"] for o in occ] == ["1"]


def test_a_bibliography_heading_the_ingest_did_not_mark_still_ends_the_body():
    """Two boundary rules, one claiming to be the other. `coverage_audit` cut the
    body at `^##\\s+references`, which needs ingest to have emitted a markdown
    heading — but flat-text ingest guesses headings from font size, and a
    `References` line at body size stays body text and is written to `clean.md`
    without `##`. `models.is_references_heading` is built for exactly that case
    and says True; this cut said False, so every `[N]` printed in the reference
    list was counted as a body citation and the audit reported gaps that do not
    exist. Reproduced on a generated flat-ingest paper: labels_in_text held [3],
    a label the body never cites.
    """
    body = (
        "## A Study\n\nBody text citing [1] and [2] here.\n\n"
        "References\n\n"
        "[1] Alpha A. First paper. 2020. [2] Bravo B. Second. 2021. "
        "[3] Gamma G. Never cited in body. 2022.\n"
    )
    assert citation_labels_in_text(body) == {"1", "2"}


def test_a_sentence_about_references_does_not_end_the_body():
    """The other half of the same rule, and why the plain-line test has to be
    exact: a body sentence starting with the word must not swallow the paper."""
    body = "References were checked by hand [1].\n\nMore body citing [2].\n"
    assert citation_labels_in_text(body) == {"1", "2"}


def test_a_marked_heading_with_a_suffix_still_ends_the_body():
    """Unchanged behaviour for a heading ingest did mark: the prefix rule."""
    body = "Body cites [1].\n\n## References and further reading\n\n[1] A. 2020. [9] B. 2021.\n"
    assert citation_labels_in_text(body) == {"1"}
