"""Resolver chain tests on a mocked transport — no network, CI-safe."""

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import RefEntry  # noqa: E402


def _repo_root() -> Path:
    """Walk up to the directory holding pyproject.toml."""
    for d in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        if (d / "pyproject.toml").exists():
            return d
    raise RuntimeError("no pyproject.toml above this test file")
from papertrace.refs import parse_references, resolve_entry  # noqa: E402

PDF = b"%PDF-1.4 fake"

REFS_TEXT = """References
1. Fixture F, Example E (2023) A convolutional method in a paper whose DOI is not printed. J Synthetic Methods 5:e230024
2. Parenthetical P, Example E (2014) Handling publisher DOIs with parentheses. Lancet 383:1068-1083. doi.org/10.1016/S0140-6736(13)00001-X
3. Du T, Melis L (2023) ReMasker: Imputing tabular data. arXiv:2309.13793
4. Mystery A (1999) A reference nobody can find anywhere.
"""


def _transport(behaviour: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, resp in behaviour.items():
            if key in url:
                return resp() if callable(resp) else resp
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(behaviour):
    return httpx.Client(transport=_transport(behaviour))


def test_parse_references_sequence_and_doi():
    entries = parse_references(REFS_TEXT)
    assert [e.num for e in entries] == ["1", "2", "3", "4"]
    assert entries[1].doi == "10.1016/S0140-6736(13)00001-X"
    assert entries[0].slug == "fixture-2023"
    # numbers inside entries (page ranges, DOIs) must not split entries
    assert "Lancet" in entries[1].raw


def test_unpaywall_path(tmp_path):
    entries = parse_references(REFS_TEXT)
    e = entries[0]  # no DOI in text -> crossref -> unpaywall pdf
    client = _client(
        {
            "api.crossref.org": httpx.Response(
                200, json={"message": {"items": [{"DOI": "10.1148/ryai.230024"}]}}
            ),
            "api.unpaywall.org": httpx.Response(
                200, json={"best_oa_location": {"url_for_pdf": "https://x/oa.pdf"}}
            ),
            "https://x/oa.pdf": httpx.Response(200, content=PDF),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "retrieved" and e.resolver == "unpaywall"
    assert Path(e.pdf_path).read_bytes().startswith(b"%PDF")


def test_epmc_fallback(tmp_path):
    entries = parse_references(REFS_TEXT)
    e = entries[1]  # DOI in text; unpaywall closed -> epmc
    client = _client(
        {
            "api.unpaywall.org": httpx.Response(200, json={"best_oa_location": None}),
            "europepmc/webservices": httpx.Response(
                200, json={"resultList": {"result": [{"pmcid": "PMC123"}]}}
            ),
            "ptpmcrender": httpx.Response(200, content=PDF),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "retrieved" and e.resolver == "europepmc"


def test_arxiv_direct(tmp_path):
    entries = parse_references(REFS_TEXT)
    e = entries[2]
    client = _client({"arxiv.org/pdf": httpx.Response(200, content=PDF)})
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "retrieved" and e.resolver == "arxiv"


def test_paywalled_is_honest(tmp_path):
    entries = parse_references(REFS_TEXT)
    e = entries[1]
    client = _client(
        {
            "api.unpaywall.org": httpx.Response(200, json={"best_oa_location": None}),
            "europepmc/webservices": httpx.Response(200, json={"resultList": {"result": []}}),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "paywalled"
    assert "no legal open-access copy" in e.reason


def test_no_doi(tmp_path):
    entries = parse_references(REFS_TEXT)
    e = entries[3]
    client = _client({"api.crossref.org": httpx.Response(200, json={"message": {"items": []}})})
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "no_doi"


def test_provided_dir_wins(tmp_path):
    provided = tmp_path / "mine"
    provided.mkdir()
    (provided / "fixture-et-al-2023-synthetic-method.pdf").write_bytes(PDF)
    entries = parse_references(REFS_TEXT)
    e = entries[0]
    client = _client({})  # network never consulted
    resolve_entry(e, tmp_path, "t@example.org", client, provided_dir=provided)
    assert e.status == "provided" and e.resolver == "user"


def test_manifest_roundtrip(tmp_path):
    from papertrace.models import RefManifest

    entries = parse_references(REFS_TEXT)
    m = RefManifest(manuscript="m.pdf", entries=entries)
    m.to_json(tmp_path / "refs_manifest.json")
    data = json.loads((tmp_path / "refs_manifest.json").read_text())
    assert data["summary"]["total"] == 4
    m2 = RefManifest.from_json(tmp_path / "refs_manifest.json")
    assert [e.num for e in m2.entries] == ["1", "2", "3", "4"]


# ---------------------------------------------------------------------------
# title sanity check — a retrieved PDF must look like the cited paper
# ---------------------------------------------------------------------------

LITTLEJOHNS_REFS = """References
1. Littlejohns TJ, Holliday J, Gibson LM, et al (2020) The UK Biobank imaging
enhancement of 100,000 participants: rationale, data collection, management
and future directions. Nat Commun 11:2624. doi:10.1038/s41467-020-15948-9
"""

WRONG_PAGE = (
    "Mimicry of emergent traits amplifies coastal restoration success. "
    "Restoration of salt marshes and seagrass beds often fails because "
    "establishment thresholds are not met. Here we show that clustered "
    "planting designs mimicking emergent ecosystem traits improve survival. "
    "Nat Commun 11:3668. doi:10.1038/s41467-020-17438-4"
)

RIGHT_PAGE = (
    "The UK Biobank imaging enhancement of 100,000 participants: rationale, "
    "data collection, management and future directions. "
    "Thomas J. Littlejohns, Jo Holliday, Lorna M. Gibson. "
    "UK Biobank is a population-based cohort of half a million participants. "
    "Nat Commun 11:2624. doi:10.1038/s41467-020-15948-9"
)


def _real_pdf_bytes(text: str) -> bytes:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz

    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(72, 72, 540, 700)
    page.insert_textbox(rect, text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def test_title_check_text_scorer():
    """Three answers, not two. `verified` and `unverifiable` shared a `None`
    return, which is how a scanned PDF came back as a confirmed match."""
    from papertrace.refs import _title_check_text

    entries = parse_references(LITTLEJOHNS_REFS)
    raw = entries[0].raw
    assert _title_check_text(raw, RIGHT_PAGE)[0] == "verified"
    state, detail = _title_check_text(raw, WRONG_PAGE)
    assert state == "mismatch" and "title check failed" in detail
    # unverifiable is not the same as wrong — but it is not the same as right
    assert _title_check_text(raw, "")[0] == "unverifiable"


def test_wrong_pdf_rejected_by_title_check(tmp_path):
    from papertrace.models import REF_STATUSES

    assert "mismatch" in REF_STATUSES
    entries = parse_references(LITTLEJOHNS_REFS)
    e = entries[0]  # DOI in text (the mistyped one) -> unpaywall serves wrong paper
    client = _client(
        {
            "api.unpaywall.org": httpx.Response(
                200, json={"best_oa_location": {"url_for_pdf": "https://x/wrong.pdf"}}
            ),
            "https://x/wrong.pdf": httpx.Response(200, content=_real_pdf_bytes(WRONG_PAGE)),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "mismatch"
    assert "different paper" in e.reason
    assert not (tmp_path / f"{e.slug}.pdf").exists()  # rejected bytes are not kept


def test_matching_pdf_passes_title_check(tmp_path):
    entries = parse_references(LITTLEJOHNS_REFS)
    e = entries[0]
    client = _client(
        {
            "api.unpaywall.org": httpx.Response(
                200, json={"best_oa_location": {"url_for_pdf": "https://x/right.pdf"}}
            ),
            "https://x/right.pdf": httpx.Response(200, content=_real_pdf_bytes(RIGHT_PAGE)),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "retrieved" and e.resolver == "unpaywall"


def test_unparseable_pdf_skips_title_check(tmp_path):
    """Bytes that fitz can't read (or an empty first page) must not be
    rejected — unverifiable is not the same as wrong."""
    entries = parse_references(LITTLEJOHNS_REFS)
    e = entries[0]
    client = _client(
        {
            "api.unpaywall.org": httpx.Response(
                200, json={"best_oa_location": {"url_for_pdf": "https://x/opaque.pdf"}}
            ),
            "https://x/opaque.pdf": httpx.Response(200, content=PDF),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "retrieved"


def test_mismatch_falls_through_to_next_resolver(tmp_path):
    """A rejected unpaywall copy must not end the chain — Europe PMC may
    still hold the right paper."""
    entries = parse_references(LITTLEJOHNS_REFS)
    e = entries[0]
    client = _client(
        {
            "api.unpaywall.org": httpx.Response(
                200, json={"best_oa_location": {"url_for_pdf": "https://x/wrong.pdf"}}
            ),
            "https://x/wrong.pdf": httpx.Response(200, content=_real_pdf_bytes(WRONG_PAGE)),
            "europepmc/webservices": httpx.Response(
                200, json={"resultList": {"result": [{"pmcid": "PMC123"}]}}
            ),
            "ptpmcrender": httpx.Response(200, content=_real_pdf_bytes(RIGHT_PAGE)),
        }
    )
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.status == "retrieved" and e.resolver == "europepmc"


def test_mismatch_roundtrips_in_manifest(tmp_path):
    from papertrace.models import RefManifest

    entries = parse_references(LITTLEJOHNS_REFS)
    entries[0].status = "mismatch"
    entries[0].reason = "retrieved PDF looks like a different paper"
    m = RefManifest(manuscript="m.pdf", entries=entries)
    m.to_json(tmp_path / "refs_manifest.json")
    data = json.loads((tmp_path / "refs_manifest.json").read_text())
    assert data["summary"]["by_status"]["mismatch"] == 1
    m2 = RefManifest.from_json(tmp_path / "refs_manifest.json")
    assert m2.entries[0].status == "mismatch"


def test_bullet_fallback_when_converter_strips_numerals():
    """docling flattens some journals' numbered hanging-indent reference
    lists to plain bullets — the parser must number them by document order
    instead of returning zero entries (found in a real Nature-family run)."""
    text = """References
- Fixture F, Example E (2023) A first bulleted reference. J Synth 1:1-10.
  doi:10.1000/bullet.1
- Sample S (2019) A second one whose line
  wraps onto a continuation line. J Synth 2:2-20.
- Mystery M (1999) A third without a DOI.
"""
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1", "2", "3"]
    assert entries[0].doi == "10.1000/bullet.1"
    assert entries[0].slug == "fixture-2023"
    assert "wraps onto a continuation line" in entries[1].raw
    assert entries[2].doi is None


# ---------------------------------------------------------------------------
# --provided: the article, not its supplement
# ---------------------------------------------------------------------------
#
# `_match_provided` returned the FIRST filename containing every slug token,
# over an unsorted `Path.glob`. So a folder holding both the article and its
# supplement produced an undefined choice — the same sources folder could yield
# different audits on different machines — and a folder holding only a
# supplement supplied it as the source. Nothing caught it: the title check runs
# inside `_accept`, which only sees downloaded candidates, so a provided file
# was accepted with no verification of any kind.


def _entry(slug: str = "littlejohns-2020", raw: str = "Littlejohns TJ et al. (2020) UK Biobank"):
    from papertrace.models import RefEntry

    return RefEntry(num="3", raw=raw, slug=slug)


def _folder(tmp_path: Path, *names: str) -> Path:
    d = tmp_path / "mine"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(PDF)
    return d


def test_the_article_beats_its_supplement_whichever_order_glob_returns(tmp_path):
    """Both orderings must give the article. Seeding the two names in each order
    is the only way to catch a first-hit-wins bug, because glob order follows
    the filesystem and is not sorted."""
    from papertrace.refs import _match_provided

    for names in (
        ("littlejohns-2020.pdf", "littlejohns-2020-supplement.pdf"),
        ("littlejohns-2020-supplement.pdf", "littlejohns-2020.pdf"),
    ):
        d = _folder(tmp_path / str(hash(names)), *names)
        assert _match_provided(_entry(), d).name == "littlejohns-2020.pdf", names


def test_a_supplement_alone_is_not_the_article(tmp_path):
    """No match, so the online chain can still find the real paper. A recorded
    `not_retrieved` is honest; a supplement standing in as the source is not."""
    from papertrace.refs import _match_provided

    d = _folder(tmp_path, "littlejohns-2020-appendix.pdf")
    assert _match_provided(_entry(), d) is None


def test_an_exact_slug_filename_wins_outright(tmp_path):
    from papertrace.refs import _match_provided

    d = _folder(tmp_path, "littlejohns-2020-cohort-profile-imaging.pdf",
                "littlejohns-2020.pdf")
    assert _match_provided(_entry(), d).name == "littlejohns-2020.pdf"


def test_several_plausible_matches_pick_deterministically_and_say_so(tmp_path):
    """Two real candidates is a decision, and the manifest should show one was
    made rather than implying a single file was found."""
    from papertrace.refs import _match_provided, resolve_entry

    d = _folder(tmp_path, "littlejohns-2020-imaging-study.pdf",
                "littlejohns-2020-cohort-profile-long-name.pdf")
    chosen = _match_provided(_entry(), d)
    assert chosen.name == "littlejohns-2020-imaging-study.pdf"  # shortest stem

    e = _entry()
    resolve_entry(e, tmp_path, "t@example.org", _client({}), provided_dir=d)
    assert e.status == "provided"
    assert "2" in e.reason, e.reason  # the count of candidates is disclosed


def test_the_supplement_markers_do_not_eat_a_real_author(tmp_path):
    """`si-mohamed-2021` is a real slug from a real audit. A marker list short
    enough to contain "si" would reject that author's paper outright, which is
    why nothing under five characters goes in it."""
    from papertrace.refs import _match_provided

    d = _folder(tmp_path, "si-mohamed-2021.pdf")
    got = _match_provided(_entry(slug="si-mohamed-2021", raw="Si-Mohamed S (2021)"), d)
    assert got is not None and got.name == "si-mohamed-2021.pdf"


def test_a_provided_file_whose_text_is_the_wrong_paper_says_so(tmp_path):
    """A provided file is title-checked like a downloaded one — but a failure is
    disclosed, not fatal. The user named this file and there is nothing to fall
    back to, so it is still used and the manifest records the mismatch."""
    import pymupdf

    d = tmp_path / "mine"
    d.mkdir(parents=True)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), "An entirely different article about volcanoes", fontsize=11)
    doc.save(d / "littlejohns-2020.pdf")
    doc.close()

    e = _entry()
    resolve_entry(e, tmp_path, "t@example.org", _client({}), provided_dir=d)
    assert e.status == "provided", "the user's explicit choice is still honoured"
    assert "unverified" in e.reason.lower(), e.reason


def test_an_unreadable_provided_file_is_used_but_says_it_is_unverified(tmp_path):
    """A scanned source is exactly the kind people supply by hand, so it is still
    used — the user named it and there is nothing to fall back to. What changed:
    this test used to assert the reason said *nothing*, on the grounds that
    unverifiable is not wrong. That locked in the defect. Not-wrong does not
    license silence: the reader has to be told nobody confirmed the identity."""
    d = _folder(tmp_path, "littlejohns-2020.pdf")  # not a real PDF: no text
    e = _entry()
    resolve_entry(e, tmp_path, "t@example.org", _client({}), provided_dir=d)
    assert e.status == "provided", "the user's explicit choice is still honoured"
    assert e.status != "mismatch", "unverifiable must not be reported as the wrong paper"
    assert e.title_check == "unverifiable"
    assert "identity unverified" in e.reason.lower(), e.reason


# --- a duplicated label means we do not know where the entry begins ---------


def test_a_label_printed_inside_a_reference_cannot_steal_the_next_entry():
    """The mid-line marker rule made `[2]` inside reference 1's title look like
    the start of reference 2, so entry 2 began mid-sentence and took reference
    1's DOI — `10.1000/ref-one`. A claim citing [2] would then be judged
    against paper 1: the wrong-paper hazard the mid-line rule existed to fix,
    arriving from the other direction.

    Neither "first occurrence" nor "last occurrence" is safe — the mirror case
    (a real entry whose own text repeats its label) breaks the opposite guess —
    so an ambiguous boundary is disclosed, never guessed.
    """
    text = ("[1] Smith A. Trial arms: [2] enhanced-dose versus control. "
            "doi:10.1000/ref-one [2] Jones B. The actual second paper. "
            "doi:10.1000/ref-two [3] Lee C. Third. doi:10.1000/ref-three")
    by_num = {e.num: e for e in parse_references(text)}

    assert by_num["2"].doi != "10.1000/ref-one", "entry 2 took reference 1's DOI"
    assert by_num["2"].doi is None, "an ambiguous entry must carry no DOI to resolve"
    assert by_num["2"].boundary_ambiguous is True
    assert "ambiguous" in by_num["2"].reason.lower()
    # the unambiguous neighbours are untouched
    assert by_num["3"].doi == "10.1000/ref-three"
    assert by_num["3"].boundary_ambiguous is False


def test_an_ambiguous_entry_is_never_resolved_against_anything():
    """Clearing the DOI is not enough. `resolve_entry` would still try Crossref
    by title on the raw text — which for an ambiguous entry is two references
    spliced together — and `_provided_candidates` would match on a slug derived
    from that same text. Both are wrong-paper routes, so the refusal is at the
    top."""
    entry = RefEntry(num="2", raw="enhanced-dose versus control. [2] Jones B. Second.",
                     boundary_ambiguous=True, reason="reference boundary ambiguous — ...")
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: calls.append(str(r.url)) or httpx.Response(500)
    ))
    out = resolve_entry(entry=entry, dest_dir=Path("/tmp"), email="a@b.org",
                        client=client, provided_dir=None)

    assert calls == [], f"an ambiguous entry reached the network: {calls}"
    assert out.status == "no_doi"
    assert "ambiguous" in out.reason.lower(), out.reason
    assert out.pdf_path is None


def test_the_legitimate_mid_line_case_still_parses():
    """The regression this must not undo: Elsevier PDFs extract with entries
    running together, so `[2]` and `[3]` sit mid-line. Requiring a line start
    once turned a 34-reference list into one entry."""
    text = ("[1] Smith A. First paper. 2020. doi:10.1000/one [2] Jones B. Second paper. "
            "2021. doi:10.1000/two [3] Lee C. Third paper. 2022. doi:10.1000/three")
    entries = parse_references(text)
    assert [e.num for e in entries] == ["1", "2", "3"]
    assert [e.doi for e in entries] == ["10.1000/one", "10.1000/two", "10.1000/three"]
    assert not any(e.boundary_ambiguous for e in entries)


def test_boundary_ambiguous_round_trips_and_older_manifests_still_load(tmp_path):
    """New field ⇒ schema update plus a round-trip test. It must also be absent-
    safe: a manifest written before this field existed has no such key."""
    import jsonschema

    from papertrace.models import RefManifest

    m = RefManifest(
        manuscript="paper.pdf",
        entries=[
            RefEntry(num="1", raw="Smith A. First.", status="no_doi",
                     boundary_ambiguous=True, reason="reference boundary ambiguous — ..."),
            RefEntry(num="2", raw="Jones B. Second.", status="retrieved", doi="10.1/x"),
        ],
    )
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)

    payload = json.loads(path.read_text())
    schema = json.loads((_repo_root() / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(payload, schema)

    back = RefManifest.from_json(path)
    assert [e.boundary_ambiguous for e in back.entries] == [True, False]

    # a pre-field manifest: the key is simply not there
    del payload["entries"][0]["boundary_ambiguous"]
    del payload["entries"][1]["boundary_ambiguous"]
    path.write_text(json.dumps(payload))
    jsonschema.validate(json.loads(path.read_text()), schema)
    legacy = RefManifest.from_json(path)
    assert [e.boundary_ambiguous for e in legacy.entries] == [False, False]


# --- unverifiable is not the same as verified ------------------------------


def test_a_scanned_provided_pdf_is_not_reported_as_matched(tmp_path):
    """`_title_check_text` returned the same None for "the title matches" and
    "there is no readable text to compare", so a scanned PDF — common for a
    paper someone supplies by hand — got status `provided` and the reason
    `matched smith-2020.pdf in your sources folder`. Nothing had been verified.
    """
    from papertrace.refs import _title_check_text

    state, _ = _title_check_text("Smith A. Deep learning for chest radiographs. 2020.", "   ")
    assert state == "unverifiable"

    state, _ = _title_check_text(
        "Smith A. Deep learning for chest radiographs. 2020.",
        "Deep learning for chest radiographs, Smith, 2020",
    )
    assert state == "verified"

    state, detail = _title_check_text(
        "Smith A. Deep learning for chest radiographs. 2020.",
        "Entirely unrelated paper about volcanic geology in Iceland",
    )
    assert state == "mismatch"
    assert "title check failed" in detail


def test_the_provided_reason_says_which_of_the_three_happened(tmp_path):
    """The manifest reason is what a reader sees. It must distinguish verified
    from unverified-because-unreadable from an outright mismatch."""
    import pymupdf

    from papertrace.refs import resolve_entry

    srcs = tmp_path / "sources"
    srcs.mkdir()

    def _pdf(name: str, text: str) -> None:
        doc = pymupdf.open()
        page = doc.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=11)
        doc.save(srcs / name)
        doc.close()

    _pdf("smith-2020.pdf", "")  # image-only: no extractable text at all
    e = RefEntry(num="1", raw="Smith A. Deep learning for chest radiographs. 2020.",
                 slug="smith-2020")
    out = resolve_entry(
        entry=e, dest_dir=tmp_path / "d", email="a@b.org",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        provided_dir=srcs,
    )
    assert out.status == "provided"
    assert "unverified" in out.reason.lower() or "could not" in out.reason.lower(), out.reason
    assert out.reason != "matched smith-2020.pdf in your sources folder"


# --- the contact address goes only where it is required --------------------


def test_the_contact_email_reaches_only_the_services_that_need_it(tmp_path):
    """One `httpx.Client` carried a `mailto:` User-Agent, so the address went to
    every host the resolver touched — Europe PMC, arXiv and whatever third party
    serves the PDF — while the wizard disclosed only Unpaywall and Crossref.

    Unpaywall requires a contact address and Crossref's polite pool uses one.
    Nothing else does, and a PDF host is an arbitrary third party.
    """
    from papertrace.refs import _client, _crossref_doi, _download_pdf, _epmc_pdf, _unpaywall_pdf

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.headers.get("user-agent", "")
        if "crossref" in request.url.host:
            return httpx.Response(200, json={"message": {"items": [{"DOI": "10.1/x"}]}})
        if "unpaywall" in request.url.host:
            return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": None}})
        if "ebi.ac.uk" in request.url.host:
            return httpx.Response(200, json={"resultList": {"result": []}})
        return httpx.Response(200, content=b"%PDF-1.4 body")

    email = "private@example.org"
    with _client() as client:
        client._transport = httpx.MockTransport(handler)
        _crossref_doi(client, "Smith A. A paper. 2020.", email)
        _unpaywall_pdf(client, "10.1/x", email)
        _epmc_pdf(client, "10.1/x")
        _download_pdf(client, "https://arxiv.org/pdf/2101.00001", tmp_path / "a.pdf")
        _download_pdf(client, "https://cdn.example-publisher.com/x.pdf", tmp_path / "b.pdf")

    needs_it = {"api.crossref.org", "api.unpaywall.org"}
    for host, ua in seen.items():
        if host in needs_it:
            assert f"mailto:{email}" in ua, f"{host} should carry the contact address: {ua}"
        else:
            assert "mailto:" not in ua, f"{host} received the contact address: {ua}"
    assert needs_it <= set(seen), f"test did not exercise both: {sorted(seen)}"
    assert {"arxiv.org", "cdn.example-publisher.com", "www.ebi.ac.uk"} <= set(seen)


def test_the_user_agent_reports_the_real_version():
    """It read `PaperTrace/0.3` — hardcoded, and already a release behind when
    the package was 0.3.1. A polite-pool identifier that misstates its version
    is worse than useless to the service reading it."""
    from papertrace import __version__
    from papertrace.refs import UA, UA_CONTACT

    assert f"PaperTrace/{__version__}" in UA
    assert f"PaperTrace/{__version__}" in UA_CONTACT.format(email="a@b.org")
