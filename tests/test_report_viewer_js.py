"""The viewer's browser-side logic, run under node — deterministic, offline.

`templates/viewer_logic.js` is the part of the viewer that decides things:
how `annotated.md` is read, where each audited sentence sits in it, what a
crop set looks like, which claims a filter keeps. None of it touches the DOM,
so all of it runs under node with no browser and no network. The DOM-binding
half (`viewer_app.js`) only styles what this module decides.

Skipped, visibly, when node is not on PATH — a skip is a line in the output,
which is the honest reading of "could not run", where a silently green suite
would not be.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

LOGIC = Path(__file__).resolve().parent.parent / "src" / "papertrace" / "templates" / "viewer_logic.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed; the viewer's logic runs under it")

HARNESS = """
const PT = require(process.argv[1]);
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', () => {
  const data = JSON.parse(input);
  const fn = new Function('PT', 'data', data.__snippet);
  process.stdout.write(JSON.stringify(fn(PT, data) ?? null));
});
"""


def _run(snippet: str, **data):
    """Evaluate `snippet` (a JS function body) with `PT` (the module) and `data`."""
    proc = subprocess.run(
        [NODE, "-e", HARNESS, str(LOGIC)],
        input=json.dumps({**data, "__snippet": snippet}),
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# --- fixtures ----------------------------------------------------------------

ANNOTATED = "\n".join([
    "[FIGURE]  <!-- block_0001, page 1 -->",
    "",
    "## Radiology  <!-- block_0002, page 1 -->",
    "",
    "## A paper about the effect of X on Y  <!-- block_0003, page 1 -->",
    "",
    "## with a second title line  <!-- block_0004, page 1 -->",
    "",
    "Author A, Author B  <!-- block_0005, page 1 -->",
    "",
    "## Key Results  <!-- block_0006, page 1 -->",
    "",
    "- ■ A bullet finding.  <!-- block_0007, page 1 -->",
    "",
    "## Introduction  <!-- block_0008, page 1 -->",
    "",
    "The increasing incidence of X worldwide underscores the need for comprehensive and accurate "
    "imaging to guide therapy (1). Reliable staging is essential for identifying disease extent "
    "and optimizing management strategies (2,3). In practice a multimodality approach combining "
    "A, B and C is typically used (4). This sentence is here to make the paragraph long enough "
    "to count as body text.  <!-- block_0009, page 1 -->",
    "",
    "[FIGURE: Figure 1: Flowchart of enrollment.]  <!-- block_0010, page 2 -->",
    "",
    "Figure 1: Flowchart of enrollment.  <!-- block_0011, page 2 -->",
    "",
    "<!-- block_0012, page 2, table -->",
    "| Characteristic | Value |",
    "|---|---|",
    "| Table 1: Characteristics | 126 |",
    "| Age (y) | 58.1 |",
    "",
    "Reliable staging is essential for identifying disease extent and optimizing management "
    "strategies (2,3). This second paragraph repeats the sentence on purpose.  "
    "<!-- block_0013, page 2 -->",
    "",
    "## Discussion  <!-- block_0014, page 3 -->",
    "",
    "Nothing was flagged in this paragraph, which is long enough to be a paragraph.  "
    "<!-- block_0015, page 3 -->",
    "",
    "## References  <!-- block_0016, page 4 -->",
    "",
    "- Burstein HJ. Customizing local therapies. Ann Oncol 2021.  <!-- block_0017, page 4 -->",
    "",
    "- Kim HJ. Preoperative diagnosis. Radiology 2025.  <!-- block_0018, page 4 -->",
    "",
])


def _claim(id, quote, refs=("1",), verdict="supported", location="Introduction ¶1", **kw):
    base = dict(id=id, claim=f"claim {id}", quote=quote, location=location, refs=list(refs),
                verdict=verdict, note="", ctx_ids=[], judgements=[], unjudged_refs=[],
                source_slug=None, source_page=None, source_block=None, anchor_phrases=[],
                evidence_image=None, continuation_images=[], anchor_located=None)
    base.update(kw)
    return base


def _payload(claims, annotated=ANNOTATED, uncited=(), scout=None, manifest=None,
             coverage=None, disclosures=None):
    return {
        "results": {
            "manuscript": "paper.pdf", "checker": "claude -p · test", "date": "2026-01-01",
            "refs": {"total": 4, "available": 1}, "converter": "docling 2.0.0",
            "claims": claims, "uncited": list(uncited),
            "coverage": coverage if coverage is not None else
            {"labels_in_text": ["1", "2"], "covered": ["1"], "missing": ["2"]},
            "truncated": {},
        },
        "annotated": annotated, "scout": scout, "manifest": manifest,
        "disclosures": disclosures or {"run": [], "claims": {}, "judgements": {}},
    }


# --- parsing annotated.md ----------------------------------------------------


def test_parse_annotated_reads_every_block_kind():
    out = _run("return PT.parseAnnotated(data.md)", md=ANNOTATED)
    kinds = [(b["type"], b["id"]) for b in out["blocks"]]
    assert ("figure", "block_0001") in kinds
    assert ("heading", "block_0006") in kinds
    assert ("para", "block_0009") in kinds
    assert ("table", "block_0012") in kinds
    assert ("ref", "block_0017") in kinds and ("ref", "block_0018") in kinds
    # the duplicated caption paragraph after a figure is dropped, not shown twice
    assert not any(b["id"] == "block_0011" for b in out["blocks"])
    # a `- ■` list item becomes a bullet paragraph
    bullet = next(b for b in out["blocks"] if b["id"] == "block_0007")
    assert bullet["type"] == "para" and bullet["text"].startswith("• A bullet")
    # references are numbered in order and the heading is kept
    refs = [b for b in out["blocks"] if b["type"] == "ref"]
    assert [r["num"] for r in refs] == [1, 2]
    assert refs[0]["text"].startswith("Burstein HJ")
    # pages come from the markers
    assert next(b for b in out["blocks"] if b["id"] == "block_0015")["page"] == 3


def test_parse_annotated_builds_the_title_and_drops_the_journal_banner():
    out = _run("return PT.parseAnnotated(data.md)", md=ANNOTATED)
    assert out["title"] == "A paper about the effect of X on Y with a second title line"
    texts = [b["text"] for b in out["blocks"] if b["type"] == "heading"]
    assert "Radiology" not in texts, "an under-12-character leading heading is a journal name"
    assert "A paper about the effect of X on Y" not in texts, "title headings leave the body"


def test_parse_annotated_reads_a_table_with_its_caption():
    out = _run("return PT.parseAnnotated(data.md)", md=ANNOTATED)
    table = next(b for b in out["blocks"] if b["type"] == "table")
    assert table["page"] == 2
    assert "| Age (y) | 58.1 |" in table["text"]
    assert table["caption"].startswith("Characteristic")


def test_parse_annotated_of_nothing_is_nothing():
    assert _run("return PT.parseAnnotated(data.md)", md="") == {"blocks": [], "title": ""}


# --- anchoring quotes in the text -------------------------------------------


def test_norm_drops_numeric_citations_and_maps_back_to_offsets():
    out = _run("return PT.norm(data.t)", t="Staging (2,3) is Key (10-12).")
    assert out["out"] == "stagingiskey"
    # each normalised char maps to its original offset
    assert out["map"][0] == 0 and out["map"][-1] == "Staging (2,3) is Key (10-12).".index("y")


def _anchored(claims, annotated=ANNOTATED):
    return _run(
        "const p = PT.parseAnnotated(data.md); PT.anchorClaims(p.blocks, data.claims);"
        "return {claims: data.claims.map(c => ({key: c.key, anchor: c.anchor})),"
        " paras: p.blocks.filter(b => b.type === 'para').map(b => ({id: b.id, segments: b.segments}))}",
        md=annotated,
        claims=[{"key": str(c["id"]), "kind": "claim", **c} for c in claims],
    )


def test_an_exact_quote_is_anchored_and_the_underline_covers_the_citation():
    out = _anchored([_claim(1, "In practice a multimodality approach combining A, B and C is typically used")])
    a = out["claims"][0]["anchor"]
    assert a and a["block"] == "block_0009"
    seg = next(s for p in out["paras"] for s in p["segments"] if s.get("claim") == "1")
    assert seg["text"].endswith("typically used (4).")


def test_a_quote_the_extractor_trimmed_is_found_by_its_head_and_tail():
    quote = ("The increasing incidence of X worldwide underscores … "
             "comprehensive and accurate imaging to guide therapy")
    out = _anchored([_claim(1, quote)])
    assert out["claims"][0]["anchor"]["block"] == "block_0009"


def test_two_claims_on_one_sentence_share_a_span_and_the_page_can_cycle_them():
    q = "Reliable staging is essential for identifying disease extent and optimizing management strategies"
    out = _anchored([_claim(2, q, refs=["2"]), _claim(3, q, refs=["3"])])
    seg = next(s for p in out["paras"] for s in p["segments"] if s.get("claim") == "2")
    assert seg["claims"] == ["2", "3"]
    assert out["claims"][1]["anchor"]["shared"] == "2"


def test_a_repeated_sentence_is_anchored_in_the_block_the_extractor_named():
    """`ctx_ids` name the block the claim came from; text search alone would
    put every repeat on the first paragraph that carries the sentence."""
    q = "Reliable staging is essential for identifying disease extent and optimizing management strategies"
    out = _anchored([_claim(2, q, ctx_ids=["block_0013:0:2"])])
    assert out["claims"][0]["anchor"]["block"] == "block_0013"


def test_an_overlapping_different_match_keeps_the_earlier_one():
    out = _anchored([
        _claim(1, "The increasing incidence of X worldwide underscores the need for comprehensive and accurate imaging to guide therapy"),
        _claim(2, "incidence of X worldwide underscores the need"),
    ])
    assert out["claims"][0]["anchor"] is not None
    assert out["claims"][1]["anchor"] is None


def test_a_short_or_missing_quote_is_not_guessed_at():
    out = _anchored([_claim(1, "X on Y"), _claim(2, "")])
    assert out["claims"][0]["anchor"] is None
    assert out["claims"][1]["anchor"] is None


# --- the model: skeleton fallback, sources, gaps ----------------------------


def test_without_annotated_md_the_model_is_a_skeleton_that_says_so():
    claims = [
        _claim(1, "Sentence one about X.", location="Introduction ¶1"),
        _claim(2, "Sentence one about X.", location="Introduction ¶1", refs=["2"]),
        _claim(3, "A methods sentence about Y.", location="Methods ¶2"),
    ]
    out = _run(
        "const d = PT.buildModel(data.payload); return {synthetic: d.synthetic, blocks: d.blocks,"
        " anchored: d.anchored, n: d.claims.length}",
        payload=_payload(claims, annotated=None),
    )
    assert out["synthetic"] is True
    assert [(b["type"], b["text"], b["page"]) for b in out["blocks"]] == [
        ("heading", "Introduction", 0),
        ("para", "Sentence one about X.", 0),      # one paragraph per DISTINCT quote
        ("heading", "Methods", 0),
        ("para", "A methods sentence about Y.", 0),
    ]
    assert out["anchored"] == 3 and out["n"] == 3


def test_the_model_keys_claims_and_uncited_assertions_apart():
    out = _run(
        "const d = PT.buildModel(data.payload); return d.claims.map(c => [c.key, c.kind, c.verdict, c.section])",
        payload=_payload([_claim(1, "Sentence one about X.")],
                         uncited=[{"id": 3, "claim": "W", "quote": "W is rare in Z.", "location": "Discussion, ¶4"}]),
    )
    assert out == [["1", "claim", "supported", "Introduction"], ["U3", "uncited", "uncited", "Discussion"]]


def test_sources_come_from_judgements_and_older_results_from_the_claim_itself():
    new = _claim(1, "Sentence one about X.", judgements=[
        {"source_slug": "a-2020", "ref": "1", "verdict": "supported", "note": "yes",
         "source_page": 2, "source_block": "block_0002", "anchor_phrases": ["one"],
         "evidence_image": "evidence/claim_01_a-2020_p2.png",
         "continuation_images": ["evidence/claim_01_a-2020_p3_cont2.png"], "anchor_located": True},
    ])
    old = _claim(2, "A methods sentence about Y.", source_slug="b-2019", source_page=5,
                 source_block="block_0009", evidence_image="evidence/claim_02_b-2019_p5.png",
                 anchor_located=False)
    old.pop("continuation_images")  # a results.json written before continuations existed
    out = _run(
        "const d = PT.buildModel(data.payload); return d.claims.map(c => c.judgements.map(j => [j.slug, j.ref, j.images, j.located]))",
        payload=_payload([new, old]),
    )
    assert out == [
        [["a-2020", "1", ["evidence/claim_01_a-2020_p2.png", "evidence/claim_01_a-2020_p3_cont2.png"], True]],
        [["b-2019", "1", ["evidence/claim_02_b-2019_p5.png"], False]],
    ]


def test_crops_caption_the_set_once_and_say_where_each_continues():
    j = {"slug": "li-2017", "ref": "7", "page": 3, "block": "block_0031", "located": True,
         "images": ["evidence/claim_05_li-2017_p3.png",
                    "evidence/claim_05_li-2017_p3_cont2.png",
                    "evidence/claim_05_li-2017_p4_cont3.png"]}
    out = _run("return PT.cropsOf(data.j)", j=j)
    assert [c["page"] for c in out] == [3, 3, 4]
    assert [c["divider"] for c in out] == ["", "continues →", "continues on p.4 →"]
    assert out[0]["cap"] == "crop 1 of 3 · the passage opens here"
    assert out[2]["cap"] == "crop 3 of 3 · p.4"
    single = _run("return PT.cropsOf(data.j)", j={**j, "images": j["images"][:1]})
    assert single[0]["cap"] == "" and single[0]["divider"] == ""


def test_gap_reasons_are_read_from_the_note_and_never_invented():
    reasons = _run(
        "return data.claims.map(c => PT.gapReason(c))",
        claims=[
            {"verdict": "not_retrieved", "note": "cited source not available (paywalled)"},
            {"verdict": "not_retrieved", "note": "cited source not available (unknown ref)"},
            {"verdict": "unchecked", "note": "check failed (TimeoutError)"},
            {"verdict": "not_retrieved", "note": ""},
        ],
    )
    assert reasons == ["paywalled", "unknown ref", "check failed", "not retrieved"]


def test_missing_references_prefer_the_manifest_over_claim_notes():
    manifest = {"entries": [
        {"num": "1", "raw": "Burstein HJ. Customizing.", "status": "retrieved", "slug": "burstein-2021", "reason": ""},
        {"num": "2", "raw": "Kim HJ. Preoperative.", "status": "paywalled", "slug": "kim-2025",
         "reason": "DOI resolved but no legal open-access copy found"},
        {"num": "3", "raw": "Lee X. Untraceable.", "status": "no_doi", "slug": None, "reason": "no DOI found"},
    ], "numbering_verified": True}
    claims = [_claim(1, "Sentence one about X.", judgements=[
        {"source_slug": "burstein-2021", "ref": "1", "verdict": "supported", "note": ""}])]
    out = _run(
        "const d = PT.buildModel(data.payload); return {missing: d.missing, ref2: d.refText('2'), slugRef: d.slugRef}",
        payload=_payload(claims, manifest=manifest),
    )
    assert out["missing"] == {"2": "paywalled", "3": "no doi"}
    assert out["ref2"] == "Kim HJ. Preoperative."
    assert out["slugRef"] == {"burstein-2021": "1"}


def test_without_a_manifest_reference_text_comes_from_the_parsed_list():
    claims = [_claim(1, "Sentence one about X.", refs=["2"], verdict="not_retrieved",
                     note="cited source not available (paywalled)")]
    out = _run(
        "const d = PT.buildModel(data.payload); return {missing: d.missing, ref2: d.refText('2'), ref9: d.refText('9')}",
        payload=_payload(claims),
    )
    assert out["missing"] == {"2": "paywalled"}
    assert out["ref2"].startswith("Kim HJ")
    assert out["ref9"] == ""


def test_the_title_comes_from_scout_then_the_text_then_the_filename():
    scout = {"paper": {"title": "Photon-counting CT and &lt;sup&gt;18&lt;/sup&gt;F-FDG <sup>2</sup>"},
             "query": "q", "newer": [], "overlooked": [], "same_year": []}
    claims = [_claim(1, "Sentence one about X.")]
    titles = _run(
        "return [PT.buildModel(data.a).title, PT.buildModel(data.b).title, PT.buildModel(data.c).title]",
        a=_payload(claims, scout=scout), b=_payload(claims), c=_payload(claims, annotated=None),
    )
    assert titles == [
        "Photon-counting CT and 18F-FDG 2",
        "A paper about the effect of X on Y with a second title line",
        "paper.pdf",
    ]


# --- filtering ---------------------------------------------------------------


def test_filters_combine_and_search_reads_every_field_a_reader_would():
    claims = [
        _claim(1, "Sentence one about X.", verdict="supported", location="Introduction ¶1",
               judgements=[{"source_slug": "burstein-2021", "ref": "1", "verdict": "supported",
                            "note": "the rationale mentions kappa"}]),
        _claim(2, "A methods sentence about Y.", verdict="contradicted", location="Methods ¶2", refs=["2"]),
    ]
    out = _run(
        "const d = PT.buildModel(data.payload); const f = q => d.claims.filter(c => PT.matches(c, q)).map(c => c.key);"
        "return [f({verdicts: ['supported'], section: 'all', query: ''}),"
        " f({verdicts: ['supported', 'contradicted'], section: 'Methods', query: ''}),"
        " f({verdicts: ['supported', 'contradicted'], section: 'all', query: 'KAPPA'}),"
        " f({verdicts: ['supported', 'contradicted'], section: 'all', query: '[2]'}),"
        " f({verdicts: ['supported', 'contradicted'], section: 'all', query: 'burstein'}),"
        " f({verdicts: ['supported'], section: 'Methods', query: ''})]",
        payload=_payload(claims),
    )
    assert out == [["1"], ["2"], ["1"], ["2"], ["1"], []]


# --- the claim map -----------------------------------------------------------


def test_the_claim_map_groups_by_top_level_section_and_stops_at_the_references():
    claims = [_claim(1, "In practice a multimodality approach combining A, B and C is typically used")]
    out = _run(
        "const d = PT.buildModel(data.payload); return PT.mapGroups(d).map(g => [g.label, g.cells.map(c => c.kind + ':' + (c.claim || c.block))])",
        payload=_payload(claims),
    )
    assert out == [
        # front matter: the author line and the bullet before the first top-level heading
        ["Abstract · Introduction", ["para:block_0005", "para:block_0007"]],
        ["Introduction", ["claim:1", "para:block_0013"]],
        ["Discussion", ["para:block_0015"]],
    ]


# --- the markdown export mirrors report.md ----------------------------------


def test_the_markdown_export_carries_verdicts_reviewed_marks_and_the_gap_register():
    claims = [
        _claim(1, "Sentence one about X.", judgements=[
            {"source_slug": "a-2020", "ref": "1", "verdict": "supported", "note": "yes",
             "source_page": 2, "source_block": "block_0002", "evidence_image": "evidence/c1.png"}]),
        _claim(2, "A methods sentence about Y.", refs=["2"], verdict="not_retrieved",
               note="cited source not available (paywalled)"),
    ]
    md = _run(
        "const d = PT.buildModel(data.payload); return PT.markdownReport(d, {'1': '2026-01-01T00:00:00Z'})",
        payload=_payload(claims, uncited=[{"id": 1, "claim": "W", "quote": "W is rare.", "location": "Discussion"}]),
    )
    assert md.startswith("# Fact-Check Report")
    assert "## Claim 1:" in md and "Supported · reviewed" in md
    assert "![evidence](evidence/c1.png)" in md
    assert "## Assertions without citation" in md and "[U1] W" in md
    assert "## Not verified — source not retrieved" in md and "Claim 2:" in md


# --- the scope of a limited audit -------------------------------------------


def _scope_payload(scope, short):
    disc = {
        "run": [{"key": "scope", "level": "warn", "token": "the audit was limited on request",
                 "text": f"Scope — {short}.", "short": short, "rows": []}],
        "claims": {}, "judgements": {},
    }
    payload = _payload([_claim(1, "Sentence one about X.")], disclosures=disc)
    payload["results"]["scope"] = scope
    return payload


def test_a_limited_audit_reaches_the_model_with_its_skipped_references_counted():
    """Decided in Python, carried in the payload, exposed by the model so the
    page can state it in the header and as the last card of the summary — and
    fold the references nobody tried into the retrieval line."""
    short = "the audit was limited on request: at most 2 sources · 2 references skipped"
    out = _run(
        "const m = PT.buildModel(data.p); return {scope: m.scope, skipped: m.refsSkipped}",
        p=_scope_payload({"sources": {"max": 2, "skipped_for_claims": ["3"], "skipped_by_cap": ["4"]}}, short),
    )
    assert out["scope"]["key"] == "scope" and out["scope"]["short"] == short
    assert out["skipped"] == 2


def test_an_unlimited_audit_has_no_scope_in_the_model():
    out = _run(
        "const m = PT.buildModel(data.p); return {scope: m.scope, skipped: m.refsSkipped}",
        p=_payload([_claim(1, "Sentence one about X.")]),
    )
    assert out == {"scope": None, "skipped": 0}


def test_the_markdown_export_ends_with_the_scope_when_there_is_one():
    short = "the audit was limited on request: claims 1–2 of 9 checked (--max-claims 2)"
    md = _run(
        "const d = PT.buildModel(data.payload); return PT.markdownReport(d, {})",
        payload=_scope_payload({"claims": {"requested": [1, 2], "judged": [1, 2], "extracted": 9}}, short),
    )
    assert "## Scope of this audit" in md
    assert md.rindex("the audit was limited on request") > md.index("## Not verified")
