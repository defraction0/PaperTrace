"""Supplemental material: a supplement is a document, never the article.

Offline like the rest of the suite — no network, no model calls.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import RefEntry, RefManifest, Supplement  # noqa: E402


def _repo_root() -> Path:
    for d in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (d / "pyproject.toml").exists():
            return d
    raise RuntimeError("no pyproject.toml above this test file")


def _manifest() -> RefManifest:
    return RefManifest(
        manuscript="paper.pdf",
        entries=[
            RefEntry(
                num="14", raw="Pyrros A (2023) Something.", status="provided",
                slug="pyrros-2023", pdf_path="/tmp/mine/pyrros-2023.pdf",
                supplements=[
                    Supplement(slug="pyrros-2023-supplement",
                               pdf_path="/tmp/mine/pyrros-2023-supplement.pdf"),
                    Supplement(slug="pyrros-2023-appendix-b",
                               pdf_path="/tmp/mine/pyrros-2023-appendix-b.pdf"),
                ],
            ),
            RefEntry(num="15", raw="Chen B (2021) Other.", status="retrieved",
                     slug="chen-2021", pdf_path="/tmp/case/sources_resolved/chen-2021.pdf"),
        ],
        manuscript_supplements=[
            Supplement(slug="paper-si", pdf_path="/tmp/paper-si.pdf"),
        ],
    )


# --- the wire format -------------------------------------------------------


def test_supplements_round_trip_as_objects_not_dicts(tmp_path):
    """`RefEntry(**e)` is a bare splat, and the manifest has carried no nested
    dataclass until now — so a `list[Supplement]` serialises correctly through
    `asdict` and reads back as plain dicts unless the reader hydrates it."""
    path = tmp_path / "refs_manifest.json"
    _manifest().to_json(path)

    back = RefManifest.from_json(path)
    got = back.entries[0].supplements
    assert [type(s) for s in got] == [Supplement, Supplement], got
    assert [s.slug for s in got] == ["pyrros-2023-supplement", "pyrros-2023-appendix-b"]
    assert [type(s) for s in back.manuscript_supplements] == [Supplement]
    assert back.manuscript_supplements[0].pdf_path == "/tmp/paper-si.pdf"


def test_the_manifest_schema_declares_supplements(tmp_path):
    """`schemas/` is the published contract, not documentation.

    Validating alone proves nothing here: this schema sets no
    `additionalProperties: false`, so an undeclared key passes silently. The
    contract is only kept if the properties are actually written down.
    """
    import jsonschema

    path = tmp_path / "refs_manifest.json"
    _manifest().to_json(path)
    schema = json.loads((_repo_root() / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(json.loads(path.read_text()), schema)

    entry = schema["properties"]["entries"]["items"]["properties"]
    assert "supplements" in entry, "an entry's supplements are undeclared"
    assert entry["supplements"]["type"] == "array"
    sup = entry["supplements"]["items"]["properties"]
    assert set(sup) == {"slug", "pdf_path", "verified"}, sup
    assert "manuscript_supplements" in schema["properties"]


def test_a_manifest_written_before_supplements_still_loads(tmp_path):
    """Absent means none, never "unknown" — an 0.5.x manifest has no such key."""
    import jsonschema

    path = tmp_path / "refs_manifest.json"
    _manifest().to_json(path)
    payload = json.loads(path.read_text())
    payload.pop("manuscript_supplements", None)
    for e in payload["entries"]:
        e.pop("supplements", None)
    path.write_text(json.dumps(payload))

    schema = json.loads((_repo_root() / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(json.loads(path.read_text()), schema)

    legacy = RefManifest.from_json(path)
    assert [e.supplements for e in legacy.entries] == [[], []]
    assert legacy.manuscript_supplements == []


def test_an_unknown_manifest_key_does_not_crash_the_reader(tmp_path):
    """`RefEntry(**e)` raises TypeError on a key it does not declare, so a
    manifest from a NEWER papertrace killed an older one outright. Every other
    reader in this codebase defaults forward; this one refused to."""
    path = tmp_path / "refs_manifest.json"
    _manifest().to_json(path)
    payload = json.loads(path.read_text())
    payload["entries"][0]["some_field_from_the_future"] = "hello"
    path.write_text(json.dumps(payload))

    back = RefManifest.from_json(path)
    assert back.entries[0].slug == "pyrros-2023"


# --- the lookup that hides the distinction ---------------------------------


def test_document_resolves_an_article_a_supplement_and_the_papers_own(tmp_path):
    """Four call sites hand-rolled `next(e for e in entries if e.slug == slug)`.
    None of them may need to learn what a supplement is."""
    m = _manifest()

    art = m.document("chen-2021")
    assert (art.kind, art.ref_num, art.parent_slug) == ("article", "15", None)
    assert art.pdf_path == "/tmp/case/sources_resolved/chen-2021.pdf"

    sup = m.document("pyrros-2023-appendix-b")
    assert (sup.kind, sup.ref_num, sup.parent_slug) == ("supplement", "14", "pyrros-2023")
    assert sup.pdf_path == "/tmp/mine/pyrros-2023-appendix-b.pdf"

    own = m.document("paper-si")
    assert (own.kind, own.ref_num, own.parent_slug) == ("own_supplement", "", None)

    assert m.document("nobody-2099") is None


def test_documents_lists_every_judgeable_file_once(tmp_path):
    """`_check_pipeline` walks the manifest to record how each source was read.
    Walking `entries` alone would leave every supplement undisclosed."""
    m = _manifest()
    assert [d.slug for d in m.documents()] == [
        "pyrros-2023",
        "pyrros-2023-supplement",
        "pyrros-2023-appendix-b",
        "chen-2021",
        "paper-si",
    ]


def test_a_slug_is_never_shared_between_a_supplement_and_an_article():
    """The whole identity story rests on the slug: `ingest/<slug>/`,
    `sources_resolved/<slug>.pdf` and every judgement key off it, so two
    documents sharing one means a verdict rendered against the wrong paper."""
    m = _manifest()
    slugs = [d.slug for d in m.documents()]
    assert len(slugs) == len(set(slugs)), slugs


# --- refs: attaching, and refusing to attach -------------------------------

PDF = b"%PDF-1.4 fake"


def _folder(tmp_path: Path, *names: str) -> Path:
    d = tmp_path / "mine"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(PDF)
    return d


def _available(slug: str = "littlejohns-2020", num: str = "3") -> RefEntry:
    return RefEntry(num=num, raw=f"{slug} et al.", slug=slug, status="provided",
                    pdf_path=f"/tmp/mine/{slug}.pdf")


def test_several_supplements_attach_to_one_available_reference(tmp_path):
    from papertrace.refs import attach_supplements

    d = _folder(tmp_path, "littlejohns-2020.pdf", "littlejohns-2020-supplement.pdf",
                "littlejohns-2020-appendix-b.pdf")
    e = _available()
    attach_supplements(e, d, taken={e.slug})

    assert [s.slug for s in e.supplements] == [
        "littlejohns-2020-appendix-b", "littlejohns-2020-supplement",
    ]
    assert all(Path(s.pdf_path).exists() for s in e.supplements)


def test_the_article_itself_is_never_attached_as_its_own_supplement(tmp_path):
    from papertrace.refs import attach_supplements

    d = _folder(tmp_path, "littlejohns-2020.pdf", "littlejohns-2020-supplement.pdf")
    e = _available()
    attach_supplements(e, d, taken={e.slug})
    assert [Path(s.pdf_path).name for s in e.supplements] == [
        "littlejohns-2020-supplement.pdf"
    ]


def test_a_supplement_does_not_attach_to_a_reference_nobody_could_obtain(tmp_path):
    """The user's rule, and the one that keeps
    `test_a_supplement_alone_is_not_the_article` true: supplementary material
    with no article behind it is judged against nothing at all."""
    from papertrace.refs import attach_supplements

    d = _folder(tmp_path, "littlejohns-2020-appendix.pdf")
    e = RefEntry(num="3", raw="Littlejohns", slug="littlejohns-2020", status="paywalled")
    attach_supplements(e, d, taken={e.slug})
    assert e.supplements == []


def test_an_orphan_supplement_is_named_with_the_reason_it_was_set_aside(tmp_path):
    """Silently ignoring a file the user deliberately supplied is the failure
    mode this codebase exists to avoid — they would never learn it did nothing."""
    from papertrace.refs import attach_supplements, unused_provided

    d = _folder(tmp_path, "littlejohns-2020-appendix.pdf", "unrelated-supplement.pdf")
    paywalled = RefEntry(num="3", raw="Littlejohns", slug="littlejohns-2020",
                         status="paywalled")
    attach_supplements(paywalled, d, taken={paywalled.slug})

    orphans = dict(unused_provided([paywalled], d))
    assert set(orphans) == {d / "littlejohns-2020-appendix.pdf", d / "unrelated-supplement.pdf"}
    assert "[3]" in orphans[d / "littlejohns-2020-appendix.pdf"]
    assert "could not tell" in orphans[d / "unrelated-supplement.pdf"]


def test_an_attached_supplement_is_not_also_reported_as_an_orphan(tmp_path):
    from papertrace.refs import attach_supplements, unused_provided

    d = _folder(tmp_path, "littlejohns-2020.pdf", "littlejohns-2020-supplement.pdf")
    e = _available()
    e.pdf_path = str(d / "littlejohns-2020.pdf")  # the article really is this file
    attach_supplements(e, d, taken={e.slug})
    assert unused_provided([e], d) == []


def test_a_supplement_slug_survives_a_sibling_being_removed(tmp_path):
    """Ordinal slugs (-suppl1/-suppl2) would renumber here, and a stored verdict
    would then point at a different PDF. Stem slugs are content-addressed."""
    from papertrace.refs import attach_supplements

    d = _folder(tmp_path, "littlejohns-2020-appendix-a.pdf",
                "littlejohns-2020-appendix-b.pdf")
    first = _available()
    attach_supplements(first, d, taken={first.slug})
    before = {s.slug for s in first.supplements}

    (d / "littlejohns-2020-appendix-a.pdf").unlink()
    second = _available()
    attach_supplements(second, d, taken={second.slug})

    assert [s.slug for s in second.supplements] == ["littlejohns-2020-appendix-b"]
    assert "littlejohns-2020-appendix-b" in before


def test_a_supplement_never_takes_a_slug_an_article_already_has(tmp_path):
    from papertrace.refs import attach_supplements

    d = _folder(tmp_path, "littlejohns-2020-supplement.pdf")
    e = _available()
    # a different reference already resolved to exactly this stem
    attach_supplements(e, d, taken={e.slug, "littlejohns-2020-supplement"})
    assert [s.slug for s in e.supplements] == ["littlejohns-2020-supplement-2"]


def test_the_supplement_markers_still_do_not_eat_a_real_author(tmp_path):
    """`si-mohamed-2021` is a real slug from a real audit. The attach path uses
    the same marker list as the exclude path, so a mistake there would now go
    the other way: the article itself judged as its own supplement."""
    from papertrace.refs import attach_supplements

    d = _folder(tmp_path, "si-mohamed-2021.pdf")
    e = _available(slug="si-mohamed-2021")
    attach_supplements(e, d, taken={e.slug})
    assert e.supplements == []


# --- --supplement, for the audited paper's own -----------------------------


def test_manuscript_supplements_take_stem_slugs_in_the_order_given(tmp_path):
    from papertrace.refs import manuscript_supplements

    a, b = tmp_path / "paper SI.pdf", tmp_path / "paper-appendix.pdf"
    for p in (a, b):
        p.write_bytes(PDF)

    got = manuscript_supplements([a, b], taken=set())
    assert [s.slug for s in got] == ["paper-si", "paper-appendix"]
    assert [s.pdf_path for s in got] == [str(a), str(b)]


def test_a_manuscript_supplement_cannot_shadow_a_reference(tmp_path):
    """Both are read as `ingest/<slug>/` and cropped from
    `sources_resolved/<slug>.pdf`, so a shared slug is a shared folder — the
    paper's own appendix overwriting a cited source's ingest."""
    from papertrace.refs import manuscript_supplements

    p = tmp_path / "chen-2021.pdf"
    p.write_bytes(PDF)
    got = manuscript_supplements([p], taken={"chen-2021"})
    assert [s.slug for s in got] == ["chen-2021-2"]


def test_the_refs_command_passes_supplements_to_the_pipeline(tmp_path, monkeypatch):
    from papertrace import cli

    seen = {}
    monkeypatch.setattr(cli, "_refs_pipeline", lambda **kw: seen.update(kw))
    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(PDF)
    si = tmp_path / "p-si.pdf"
    si.write_bytes(PDF)

    cli.refs(manuscript=pdf, case=None, provided=None, email=None, parse_only=False,
             backend="pymupdf", doi=None, supplement=[si])

    assert seen["supplement"] == [si]


def test_run_forwards_supplements_to_refs(tmp_path, monkeypatch):
    """`run` calls the pipeline functions directly, so a parameter it forgets to
    name is simply dropped — the audit would run without the supplement and say
    nothing about it."""
    import inspect

    from papertrace import cli

    seen = {}
    monkeypatch.setattr(cli, "_ingest_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "_refs_pipeline", lambda **kw: seen.update(kw))
    monkeypatch.setattr(cli, "scout", lambda **kw: None)
    monkeypatch.setattr(cli, "_check_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "highlight", lambda **kw: None)
    monkeypatch.setattr(cli, "_report_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "_email", lambda v: "e@example.com")
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(PDF)
    si = tmp_path / "p-si.pdf"
    si.write_bytes(PDF)

    cli.run(manuscript=pdf, case=tmp_path / "c", provided=None, email="e@example.com",
            model=None, png=False, backend="pymupdf", with_scout=False, doi=None,
            formats=None, supplement=[si])

    assert seen["supplement"] == [si]
    # and the flag really is declared on `run`, not silently swallowed by **kw
    assert "supplement" in inspect.signature(cli.run).parameters


# --- check: a cited work's supplements are judged too ----------------------


def _ingested(dirpath: Path, slug: str) -> None:
    from papertrace.models import Block, SourceMap

    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "annotated.md").write_text(
        f"<!-- block_0001, page 1 -->\nText of {slug}.\n"
    )
    SourceMap(
        doc=f"{slug}.pdf", pages=1,
        blocks=[Block("block_0001", "text", 1, (0.0, 0.0, 100.0, 20.0), [],
                      f"Text of {slug}.")],
    ).to_json(dirpath / "source_map.json")


def _case_with_supplement(tmp_path: Path) -> RefManifest:
    """One cited reference [1] carrying two supplements, all already ingested."""
    for slug in ("pyrros-2023", "pyrros-2023-supplement", "pyrros-2023-appendix-b"):
        _ingested(tmp_path / "ingest" / slug, slug)
    return RefManifest(
        manuscript="m.pdf",
        entries=[
            RefEntry(num="1", raw="Pyrros", status="retrieved", slug="pyrros-2023",
                     pdf_path="pyrros-2023.pdf",
                     supplements=[
                         Supplement("pyrros-2023-supplement", "pyrros-2023-supplement.pdf"),
                         Supplement("pyrros-2023-appendix-b", "pyrros-2023-appendix-b.pdf"),
                     ]),
        ],
    )


def _verdicts(mapping: dict[str, str]):
    """A fake `_ask` answering per document, and the record of what it was asked."""
    seen: list[str] = []

    def fake_ask(prompt, model=None):
        slug = next(s for s in mapping if f"SOURCE ({s})" in prompt)
        seen.append(slug)
        return json.dumps([{
            "id": 1, "verdict": mapping[slug], "note": f"per {slug}",
            "source_page": 1, "source_block": "block_0001",
            "anchor_phrases": [f"Text of {slug}"],
        }])

    return fake_ask, seen


def test_a_supplement_is_judged_as_its_own_document(tmp_path, monkeypatch):
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    manifest = _case_with_supplement(tmp_path)
    claim = ClaimResult(id=1, claim="the cohort was imaged twice", location="Methods",
                        refs=["1"])
    fake_ask, seen = _verdicts({
        "pyrros-2023": "not_addressed",
        "pyrros-2023-supplement": "contradicted",
        "pyrros-2023-appendix-b": "not_addressed",
    })
    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert sorted(seen) == [
        "pyrros-2023", "pyrros-2023-appendix-b", "pyrros-2023-supplement",
    ], "one call per document"
    assert {j.source_slug: j.verdict for j in claim.judgements} == {
        "pyrros-2023": "not_addressed",
        "pyrros-2023-supplement": "contradicted",
        "pyrros-2023-appendix-b": "not_addressed",
    }
    # a supplement answers for the label its parent carries
    assert {j.ref for j in claim.judgements} == {"1"}
    assert {j.kind for j in claim.judgements} == {"article", "supplement"}


def test_a_supplement_contradicting_decides_the_headline(tmp_path, monkeypatch):
    """The supplement is part of the cited work. A contradiction found only in
    the appendix is still a contradiction the reviewer needs."""
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    manifest = _case_with_supplement(tmp_path)
    claim = ClaimResult(id=1, claim="c", location="Methods", refs=["1"])
    fake_ask, _ = _verdicts({
        "pyrros-2023": "not_addressed",
        "pyrros-2023-supplement": "contradicted",
        "pyrros-2023-appendix-b": "not_addressed",
    })
    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert claim.verdict == "contradicted"
    assert claim.source_slug == "pyrros-2023-supplement"


def test_the_judge_is_told_which_kind_of_document_it_is_holding(tmp_path, monkeypatch):
    """Handed an appendix with no warning, a judge has no reason to expect
    `not_addressed` to be the ordinary answer."""
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    manifest = _case_with_supplement(tmp_path)
    claim = ClaimResult(id=1, claim="c", location="Methods", refs=["1"])
    prompts: dict[str, str] = {}

    def fake_ask(prompt, model=None):
        slug = next(s for s in ("pyrros-2023-supplement", "pyrros-2023-appendix-b",
                                "pyrros-2023") if f"SOURCE ({s})" in prompt)
        prompts[slug] = prompt
        return json.dumps([{"id": 1, "verdict": "not_addressed", "note": "n"}])

    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert "<<DOCKIND>>" not in prompts["pyrros-2023"], "placeholder left unsubstituted"
    assert "supplementary material" in prompts["pyrros-2023-supplement"].lower()
    assert "supplementary material" not in prompts["pyrros-2023"].lower()


def test_a_supplement_is_not_judged_when_its_reference_is_not_cited(tmp_path, monkeypatch):
    """A claim citing [2] must not pick up [1]'s appendix."""
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    manifest = _case_with_supplement(tmp_path)
    manifest.entries.append(
        RefEntry(num="2", raw="Chen", status="retrieved", slug="chen-2021",
                 pdf_path="chen-2021.pdf")
    )
    _ingested(tmp_path / "ingest" / "chen-2021", "chen-2021")
    claim = ClaimResult(id=1, claim="c", location="Methods", refs=["2"])
    fake_ask, seen = _verdicts({"chen-2021": "supported"})
    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert seen == ["chen-2021"]
    assert [j.source_slug for j in claim.judgements] == ["chen-2021"]


def test_the_results_schema_declares_the_document_kind(tmp_path):
    """A judgement's `kind` is what tells a reader of results.json alone that a
    verdict came from an appendix. Declaring it is the contract; validating is
    not enough, since nothing here forbids an undeclared key."""
    import jsonschema

    from papertrace.models import ClaimResult, RunResults, SourceJudgement

    results = RunResults(
        manuscript="m.pdf", checker="claude -p", date="2026-09-06",
        refs_total=1, refs_available=1,
        claims=[ClaimResult(
            id=1, claim="c", location="Methods", refs=["1"],
            judgements=[
                SourceJudgement("pyrros-2023", "1", kind="article", verdict="not_addressed"),
                SourceJudgement("pyrros-2023-supplement", "1", kind="supplement",
                                verdict="contradicted", source_page=1,
                                source_block="block_0001"),
            ],
        )],
    )
    path = tmp_path / "results.json"
    results.to_json(path)
    schema = json.loads((_repo_root() / "schemas" / "results.schema.json").read_text())
    jsonschema.validate(json.loads(path.read_text()), schema)

    j = schema["properties"]["claims"]["items"]["properties"]["judgements"]["items"]
    assert "kind" in j["properties"], "a judgement's document kind is undeclared"
    assert set(j["properties"]["kind"]["enum"]) == {"article", "supplement", "own_supplement"}

    back = RunResults.from_json(path)
    assert [x.kind for x in back.claims[0].judgements] == ["article", "supplement"]


def test_a_results_file_written_before_supplements_still_loads(tmp_path):
    """Absent `kind` means the article — every judgement before 0.6.0 was one."""
    from papertrace.models import ClaimResult, RunResults, SourceJudgement

    results = RunResults(
        manuscript="m.pdf", checker="claude -p", date="2026-09-06",
        refs_total=1, refs_available=1,
        claims=[ClaimResult(id=1, claim="c", location="M", refs=["1"],
                            judgements=[SourceJudgement("a-2020", "1", verdict="supported")])],
    )
    path = tmp_path / "results.json"
    results.to_json(path)
    payload = json.loads(path.read_text())
    for j in payload["claims"][0]["judgements"]:
        del j["kind"]
    path.write_text(json.dumps(payload))

    back = RunResults.from_json(path)
    assert [x.kind for x in back.claims[0].judgements] == ["article"]
    assert back.claims[0].judgements[0].origin == "cited as [1]"


def test_a_supplement_that_cannot_be_read_never_taints_the_articles_verdict(tmp_path,
                                                                            monkeypatch):
    """Gate 4. The supplement's own PDF is gone and it was never ingested, so
    nobody read it — that is `unchecked`, on that document alone. The article
    was read and its verdict stands; discarding it would report a gap that does
    not exist, and letting the supplement default to anything would be a verdict
    on a document nobody opened."""
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="Pyrros", status="retrieved", slug="pyrros-2023",
                          pdf_path="pyrros-2023.pdf",
                          supplements=[Supplement("pyrros-2023-supplement",
                                                  "/nonexistent/pyrros-2023-supplement.pdf")])],
    )
    _ingested(tmp_path / "ingest" / "pyrros-2023", "pyrros-2023")  # the supplement: not ingested
    claim = ClaimResult(id=1, claim="c", location="Methods", refs=["1"])

    monkeypatch.setattr(check_mod, "_ask", lambda p, model=None: json.dumps([{
        "id": 1, "verdict": "supported", "note": "per the article",
        "source_page": 1, "source_block": "block_0001",
        "anchor_phrases": ["Text of pyrros-2023"],
    }]))
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    by_slug = {j.source_slug: j for j in claim.judgements}
    assert by_slug["pyrros-2023"].verdict == "supported"
    assert by_slug["pyrros-2023-supplement"].verdict == "unchecked"
    assert "re-run" in by_slug["pyrros-2023-supplement"].note
    # the readable document still decides the headline
    assert claim.verdict == "supported"
    # and the gap is NOT laundered into "the source could not be retrieved"
    assert claim.unjudged_refs == []


# --- check: claims that point at this paper's own supplement ---------------


def _manuscript_ingest(case: Path) -> None:
    d = case / "ingest" / "manuscript"
    d.mkdir(parents=True, exist_ok=True)
    (d / "annotated.md").write_text(
        "<!-- block_0001, page 1 -->\nModel AUC was 0.91 (Table S3).\n"
    )


def test_extraction_records_a_pointer_at_this_papers_own_supplement(tmp_path, monkeypatch):
    from papertrace import check as check_mod

    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": [{"id": 1, "claim": "AUC 0.91.", "location": "Results", "refs": [],
                    "own_supplement": True}],
         "uncited": []}))
    _manuscript_ingest(tmp_path)
    cited, _ = check_mod.extract_claims(tmp_path)
    assert cited[0].own_supplement is True


def test_a_claim_with_no_such_pointer_defaults_to_false(tmp_path, monkeypatch):
    from papertrace import check as check_mod

    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: json.dumps(
        {"cited": [{"id": 1, "claim": "X causes Y.", "location": "Intro", "refs": ["1"]}],
         "uncited": []}))
    _manuscript_ingest(tmp_path)
    cited, _ = check_mod.extract_claims(tmp_path)
    assert cited[0].own_supplement is False


def test_the_extraction_prompt_asks_for_the_pointer(tmp_path, monkeypatch):
    from papertrace import check as check_mod

    seen = {}

    def fake_ask(prompt, m=None):
        seen["prompt"] = prompt
        return json.dumps({"cited": [], "uncited": []})

    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    _manuscript_ingest(tmp_path)
    check_mod.extract_claims(tmp_path)
    assert "own_supplement" in seen["prompt"]


def test_such_a_claim_is_judged_against_every_supplement_of_this_paper(tmp_path, monkeypatch):
    """Judged against all of them, matching the rule on the cited side — and it
    avoids asking the extractor to guess which file `S3` lives in."""
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    for slug in ("paper-si", "paper-appendix"):
        _ingested(tmp_path / "ingest" / slug, slug)
    manifest = RefManifest(
        manuscript="m.pdf", entries=[],
        manuscript_supplements=[Supplement("paper-si", "paper-si.pdf"),
                                Supplement("paper-appendix", "paper-appendix.pdf")],
    )
    claim = ClaimResult(id=1, claim="AUC 0.91", location="Results", refs=[],
                        own_supplement=True)
    fake_ask, seen = _verdicts({"paper-si": "supported", "paper-appendix": "not_addressed"})
    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert sorted(seen) == ["paper-appendix", "paper-si"]
    assert {j.kind for j in claim.judgements} == {"own_supplement"}
    assert {j.ref for j in claim.judgements} == {""}
    assert claim.verdict == "supported"
    assert claim.judgements[0].origin == "this paper's own supplement"


def test_a_pointer_with_nothing_supplied_is_not_retrieved_and_says_how_to_fix_it(
    tmp_path, monkeypatch
):
    """The paper named where its evidence was and nobody opened it. Leaving
    that in the uncited register would call it an assertion with no citation,
    which understates it."""
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    manifest = RefManifest(manuscript="m.pdf", entries=[])
    claim = ClaimResult(id=1, claim="AUC 0.91", location="Results", refs=[],
                        own_supplement=True)
    monkeypatch.setattr(check_mod, "_ask", lambda p, m=None: pytest.fail("no call is due"))
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert claim.verdict == "not_retrieved"
    assert "--supplement" in claim.note
    assert claim.judgements == []


def test_a_claim_pointing_at_both_a_citation_and_the_supplement_gets_both(tmp_path,
                                                                          monkeypatch):
    from papertrace import check as check_mod
    from papertrace.models import ClaimResult

    for slug in ("chen-2021", "paper-si"):
        _ingested(tmp_path / "ingest" / slug, slug)
    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="Chen", status="retrieved", slug="chen-2021",
                          pdf_path="chen-2021.pdf")],
        manuscript_supplements=[Supplement("paper-si", "paper-si.pdf")],
    )
    claim = ClaimResult(id=1, claim="c", location="Results", refs=["1"],
                        own_supplement=True)
    fake_ask, seen = _verdicts({"chen-2021": "supported", "paper-si": "supported"})
    monkeypatch.setattr(check_mod, "_ask", fake_ask)
    check_mod.check_claims([claim], manifest, tmp_path, backend="pymupdf")

    assert sorted(seen) == ["chen-2021", "paper-si"]
    assert {j.source_slug: j.kind for j in claim.judgements} == {
        "chen-2021": "article", "paper-si": "own_supplement",
    }


def test_the_results_schema_declares_the_own_supplement_pointer(tmp_path):
    import jsonschema

    from papertrace.models import ClaimResult, RunResults

    results = RunResults(
        manuscript="m.pdf", checker="claude -p", date="2026-09-06",
        refs_total=0, refs_available=0,
        claims=[ClaimResult(id=1, claim="AUC 0.91", location="Results", refs=[],
                            own_supplement=True, verdict="not_retrieved")],
    )
    path = tmp_path / "results.json"
    results.to_json(path)
    schema = json.loads((_repo_root() / "schemas" / "results.schema.json").read_text())
    jsonschema.validate(json.loads(path.read_text()), schema)
    assert "own_supplement" in schema["properties"]["claims"]["items"]["properties"]

    payload = json.loads(path.read_text())
    del payload["claims"][0]["own_supplement"]
    path.write_text(json.dumps(payload))
    assert RunResults.from_json(path).claims[0].own_supplement is False


# --- reports: a headline ranges over documents -----------------------------


def _results_with_supplement(**claim_kw):
    from papertrace.models import ClaimResult, RunResults, SourceJudgement

    base = dict(
        id=1, claim="the cohort was imaged twice", location="Methods", refs=["14"],
        judgements=[
            SourceJudgement("pyrros-2023", "14", kind="article", verdict="not_addressed",
                            note="silent on this"),
            SourceJudgement("pyrros-2023-supplement", "14", kind="supplement",
                            verdict="contradicted", note="Table S2 reports 0.71",
                            source_page=1, source_block="block_0001"),
        ],
    )
    base.update(claim_kw)
    claim = ClaimResult(**base)
    claim.apply_headline()
    return RunResults(manuscript="m.pdf", converter="docling", claims=[claim]), claim


def test_the_qualifier_says_documents_when_a_supplement_is_among_them():
    """"most adverse of 2 cited sources" would be false — there is one cited
    source here, read as two documents."""
    results, claim = _results_with_supplement()
    assert claim.verdict == "contradicted"
    assert claim.headline_qualifier() == "most adverse of 2 documents"


def test_the_qualifier_still_says_cited_sources_when_they_all_are():
    from papertrace.models import ClaimResult, SourceJudgement

    c = ClaimResult(id=1, claim="c", location="M", refs=["1", "2"], judgements=[
        SourceJudgement("a-2020", "1", verdict="supported"),
        SourceJudgement("b-2021", "2", verdict="partial"),
    ])
    c.apply_headline()
    assert c.headline_qualifier() == "most adverse of 2 cited sources"


def test_every_format_names_the_document_a_verdict_came_from(tmp_path):
    from papertrace.report import write_reports

    results, _ = _results_with_supplement()
    write_reports(results, None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        body = (tmp_path / name).read_text()
        assert "supplement to [14]" in body, f"{name} does not say the verdict is from a supplement"


def test_no_format_renders_an_empty_citation_label(tmp_path):
    """The paper's own supplement answers for no label. Three templates used to
    build `cited as [{{ j.ref }}]` by hand, which renders `cited as []`."""
    from papertrace.models import ClaimResult, RunResults, SourceJudgement
    from papertrace.report import write_reports

    c = ClaimResult(id=1, claim="AUC 0.91", location="Results", refs=[],
                    own_supplement=True, judgements=[
                        SourceJudgement("paper-si", "", kind="own_supplement",
                                        verdict="supported", note="Table S3 gives 0.91",
                                        source_page=1, source_block="block_0001")])
    c.apply_headline()
    write_reports(RunResults(manuscript="m.pdf", claims=[c]), None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        body = (tmp_path / name).read_text()
        assert "cited as []" not in body, name
        assert "this paper" in body and "own supplement" in body, name


# --- reports: the disclosures supplements owe the reader -------------------


def test_a_supplement_is_disclosed_as_unverified_in_every_format(tmp_path):
    """Every article is checked against the reference that names it. A
    supplement's title does not match its parent's, so that check cannot apply
    and is not faked — which makes this the weakest provenance in the tool, and
    a reader has to be told."""
    from papertrace.disclosures import run_disclosures
    from papertrace.report import write_reports

    results, _ = _results_with_supplement()
    fired = {d.key: d for d in run_disclosures(results)}
    assert "supplement_identity" in fired

    write_reports(results, None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        body = (tmp_path / name).read_text()
        assert fired["supplement_identity"].token in body, name


def test_the_coverage_blind_spot_is_disclosed_when_supplements_were_read(tmp_path):
    """A [N] occurring only inside a supplement is not counted by the audit,
    which reads the manuscript alone. Stated, not hidden."""
    from papertrace.disclosures import run_disclosures
    from papertrace.report import write_reports

    results, _ = _results_with_supplement()
    results.coverage = {"labels_in_text": ["14"], "covered": ["14"], "missing": []}
    fired = {d.key: d for d in run_disclosures(results)}
    assert "supplement_coverage" in fired

    write_reports(results, None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        assert fired["supplement_coverage"].token in (tmp_path / name).read_text(), name


def test_neither_disclosure_fires_when_no_supplement_was_read():
    from papertrace.disclosures import run_disclosures
    from papertrace.models import ClaimResult, RunResults, SourceJudgement

    c = ClaimResult(id=1, claim="c", location="M", refs=["1"], judgements=[
        SourceJudgement("a-2020", "1", verdict="supported")])
    results = RunResults(manuscript="m.pdf", claims=[c],
                         coverage={"labels_in_text": ["1"], "covered": ["1"], "missing": []})
    keys = {d.key for d in run_disclosures(results)}
    assert "supplement_identity" not in keys
    assert "supplement_coverage" not in keys


def test_a_headline_decided_by_a_supplement_says_so_in_every_format(tmp_path):
    """The claim reads `contradicted` on the strength of an appendix while the
    article of record is silent. A reader acting on the headline alone needs
    that on the claim, not only in a run-level footnote."""
    from papertrace.disclosures import claim_disclosures
    from papertrace.report import write_reports

    results, claim = _results_with_supplement()
    fired = {d.key: d for d in claim_disclosures(claim)}
    assert "supplement_headline" in fired

    write_reports(results, None, tmp_path, png=False)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        assert fired["supplement_headline"].token in (tmp_path / name).read_text(), name


def test_no_such_claim_disclosure_when_the_article_itself_decided():
    from papertrace.disclosures import claim_disclosures
    from papertrace.models import ClaimResult, SourceJudgement

    c = ClaimResult(id=1, claim="c", location="M", refs=["14"], judgements=[
        SourceJudgement("pyrros-2023", "14", kind="article", verdict="contradicted",
                        source_page=1, source_block="block_0001"),
        SourceJudgement("pyrros-2023-supplement", "14", kind="supplement",
                        verdict="not_addressed"),
    ])
    c.apply_headline()
    assert "supplement_headline" not in {d.key for d in claim_disclosures(c)}


def test_the_per_claim_count_does_not_call_a_supplement_a_cited_source():
    """One cited work read as two documents is not two cited works. The count
    sits directly under the headline and would overstate how many independent
    papers were consulted."""
    from papertrace.disclosures import claim_disclosures

    _, claim = _results_with_supplement()
    d = next(x for x in claim_disclosures(claim) if x.key == "sources")
    assert "2 cited sources checked" not in d.text
    assert "2 documents checked" in d.text
    assert d.token in d.text and d.token in d.short


def test_a_plain_multi_source_claim_still_says_cited_sources():
    from papertrace.disclosures import claim_disclosures
    from papertrace.models import ClaimResult, SourceJudgement

    c = ClaimResult(id=1, claim="c", location="M", refs=["1", "2"], judgements=[
        SourceJudgement("a-2020", "1", verdict="supported"),
        SourceJudgement("b-2021", "2", verdict="contradicted"),
    ])
    c.apply_headline()
    d = next(x for x in claim_disclosures(c) if x.key == "sources")
    assert "2 cited sources checked" in d.text


def test_the_papers_own_supplement_is_not_described_as_matched_by_filename():
    """It was named on the command line. Saying a file the user pointed at
    directly was guessed from its name misstates which part is uncertain — the
    identity gap here is that nothing checks the contents, not the file."""
    from papertrace.disclosures import claim_disclosures, run_disclosures
    from papertrace.models import ClaimResult, RunResults, SourceJudgement

    c = ClaimResult(id=1, claim="AUC 0.91", location="Results", refs=[],
                    own_supplement=True, judgements=[
                        SourceJudgement("paper-si", "", kind="own_supplement",
                                        verdict="supported", source_page=1,
                                        source_block="block_0001")])
    c.apply_headline()
    head = next(x for x in claim_disclosures(c) if x.key == "supplement_headline")
    assert "filename" not in head.text

    run = next(x for x in run_disclosures(RunResults(manuscript="m.pdf", claims=[c]))
               if x.key == "supplement_identity")
    assert "matched by filename" not in run.text


# --- the wizard asks for the sources folder it never asked for -------------


def test_the_equivalent_command_replays_the_sources_folder_and_supplements(tmp_path):
    """The wizard prints the one-line command its session amounts to. A line
    that omits the flags the session used does not reproduce the audit."""
    from papertrace.wizard import equivalent_command

    cmd = equivalent_command(
        manuscript=tmp_path / "paper.pdf", case=tmp_path / "case", doi=None, png=False,
        with_scout=False, provided=tmp_path / "sources", email="e@example.com",
        supplement=[tmp_path / "si.pdf", tmp_path / "appendix.pdf"],
    )
    assert "--provided" in cmd
    assert cmd.count("--supplement") == 2
    assert "si.pdf" in cmd and "appendix.pdf" in cmd


def test_the_cost_estimate_counts_the_supplements_it_can_see(tmp_path):
    """Each supplement is one more document, so one more judging call. The
    wizard states the cost before the user agrees to pay it, and an estimate
    that ignores supplements understates what they are agreeing to."""
    from papertrace.wizard import supplement_workload

    d = tmp_path / "sources"
    d.mkdir()
    for n in ("pyrros-2023.pdf", "pyrros-2023-supplement.pdf", "chen-2021-appendix.pdf"):
        (d / n).write_bytes(PDF)

    assert supplement_workload(d, [tmp_path / "own-si.pdf"]) == 3
    assert supplement_workload(d, []) == 2
    assert supplement_workload(None, []) == 0
    assert supplement_workload(tmp_path / "nope", []) == 0


def test_the_wizard_hands_run_the_sources_folder_and_supplements(monkeypatch, tmp_path):
    """`provided=None` was hardcoded, so a wizard user could not use a sources
    folder at all — the flag existed and the guided path could not reach it."""
    import inspect

    from papertrace import wizard as wiz

    src = inspect.getsource(wiz.run_wizard)
    assert "provided=None" not in src, "the sources folder is still hardcoded away"
    assert "supplement=None" not in src, "supplements are still hardcoded away"


# --- the wizard asks before it interrogates --------------------------------


class _Answers:
    """Stands in for rich's Prompt/Confirm, replaying scripted answers."""

    def __init__(self, confirms, prompts=()):
        self.confirms, self.prompts = list(confirms), list(prompts)
        self.asked: list[str] = []

    def confirm(self, text, **kw):
        self.asked.append(text)
        return self.confirms.pop(0)

    def prompt(self, text, **kw):
        self.asked.append(text)
        return self.prompts.pop(0) if self.prompts else kw.get("default", "")


def _script(monkeypatch, answers):
    from papertrace import wizard as wiz

    monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(answers.confirm))
    monkeypatch.setattr(wiz.Prompt, "ask", staticmethod(answers.prompt))
    return answers


def test_saying_no_to_sources_asks_for_no_path(tmp_path, monkeypatch):
    """A user with nothing was made to read two paragraphs and answer a path
    prompt to say so."""
    from papertrace import wizard as wiz

    a = _script(monkeypatch, _Answers(confirms=[False]))
    assert wiz._ask_sources(tmp_path / "case") is None
    assert not a.prompts and len([q for q in a.asked if "folder" in q.lower()]) == 0


def test_saying_no_to_supplements_asks_for_no_path(tmp_path, monkeypatch):
    from papertrace import wizard as wiz

    a = _script(monkeypatch, _Answers(confirms=[False]))
    assert wiz._ask_supplements() == []
    assert len(a.asked) == 1


def test_the_sources_question_defaults_to_yes_when_the_folder_has_pdfs(tmp_path,
                                                                       monkeypatch):
    """The folder's contents are better evidence of the answer than a fixed
    default — and a user whose PDFs are already in place pressing return should
    not silently skip them."""
    from papertrace import wizard as wiz

    seen = {}

    def confirm(text, **kw):
        seen["default"] = kw.get("default")
        return False

    monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(confirm))
    case = tmp_path / "case"
    (case / "sources").mkdir(parents=True)

    wiz._ask_sources(case)
    assert seen["default"] is False, "an empty folder must not suggest yes"

    (case / "sources" / "pyrros-2023.pdf").write_bytes(b"%PDF")
    wiz._ask_sources(case)
    assert seen["default"] is True


def test_saying_yes_still_reaches_the_path_prompt(tmp_path, monkeypatch):
    from papertrace import wizard as wiz

    case = tmp_path / "case"
    (case / "sources").mkdir(parents=True)
    (case / "sources" / "a.pdf").write_bytes(b"%PDF")
    _script(monkeypatch, _Answers(confirms=[True], prompts=[str(case / "sources")]))
    assert wiz._ask_sources(case) == case / "sources"
