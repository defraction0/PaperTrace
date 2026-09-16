"""Resolver chain tests on a mocked transport — no network, CI-safe."""

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import models  # noqa: E402
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


# --- a web page is not an article, and a title search will not admit that ---
#
# Reference [8] of a real audited paper is an ACR news page with no DOI. With no
# DOI to look up, `resolve_entry` fell through to a Crossref *bibliographic
# title search*, which answered `10.1002/acr2.11538` — ACR Open Rheumatology
# (American College of Rheumatology, not Radiology). Unpaywall served that
# journal's editorial about ChatGPT, the title check passed it at 6/15, and two
# claims were judged `not_addressed` against a rheumatology editorial.

ACR_WEBPAGE_REF = (
    "ACR launches first medical practice artificial intelligence QA program. "
    "https://www.acr.org/News-and-Publications/Media-Center/2024/ACR-Launches-FirstMedical-"
    "Practice-Artificial-Intelligence-Quality-AssuranceProgram?utm_source=chatgpt.com."
)

# Verbatim excerpt of the wrong paper's first page as PyMuPDF extracts it —
# ligatures and all. Inlined so the test stays offline and self-contained.
ACR_EDITORIAL_FIRST_PAGE = (
    "E D I T O R I A L\n"
    "ChatGPT, et al … Artiﬁcial Intelligence, Authorship, and Medical Publishing\n"
    "Daniel H. Solomon,1 Kelli D. Allen,2 Patricia Katz,3 Amr H. Sawalha,4 and Ed Yelin3\n"
    "If you have not yet heard of ChatGPT, you will! This artiﬁcial intelligence "
    "(AI)-based chatbot is making waves in medicine, education, academic publishing, "
    "and more widely. GPT, generative pretrained transformer, describes the next "
    "generation in AI-powered chatbots that not only construct full sentences on topic "
    "but now synthesize information from many ﬁelds, from many sources, and with "
    "tremendous nuance. The American College of Rheumatology (ACR) journal editors and "
    "the ACR Committee on Journal Publications have agreed that co-authorship is not "
    "appropriate, since authorship according to the International Committee of Medical "
    "Journal Editors requires that authors agree to be accountable. "
    "This is an open access article under the terms of the Creative Commons "
    "Attribution-NonCommercial-NoDerivs License, provided the original work is properly "
    "cited. ChatGPT, et al … Artificial Intelligence, Authorship, and Medical Publishing"
)


def test_a_url_only_reference_never_enters_a_bibliographic_title_search(tmp_path):
    """Crossref's bibliographic search always returns *something*; for a news
    page that something is a confident wrong answer. `no_doi` costs nothing
    real — the page was never retrievable as a PDF — and it is the only honest
    answer, so the refusal happens before any request goes out."""
    from papertrace.refs import resolve_entry

    e = RefEntry(num="8", raw=ACR_WEBPAGE_REF, slug="acr-2024")
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: calls.append(str(r.url)) or httpx.Response(
            200, json={"message": {"items": [{"DOI": "10.1002/acr2.11538"}]}}
        )
    ))
    out = resolve_entry(entry=e, dest_dir=tmp_path, email="a@b.org",
                       client=client, provided_dir=None)

    assert calls == [], f"a web page reached the network: {calls}"
    assert out.status == "no_doi"
    assert out.doi is None, "a title search must not attach a DOI to a web page"
    assert out.pdf_path is None
    assert "web page" in out.reason.lower(), out.reason


def test_a_journal_reference_that_merely_includes_a_url_still_resolves(tmp_path):
    """The gate keys on the *absence* of article structure, not the presence of
    a URL — publishers' own reference styles print a link beside the volume and
    page range, and those references are exactly what Crossref answers well."""
    from papertrace.refs import resolve_entry

    raw = (
        "Smith A, Jones B (2021) Deep learning for chest radiographs: a systematic "
        "review. Radiology 298:120-130. Available at: "
        "https://pubs.rsna.org/journal/radiology"
    )
    page = (
        "Deep learning for chest radiographs: a systematic review. "
        "A. Smith, B. Jones. Radiology 2021; 298:120-130."
    )
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "api.crossref.org" in str(request.url):
            return httpx.Response(200, json={"message": {"items": [{"DOI": "10.1148/r.2021"}]}})
        if "api.unpaywall.org" in str(request.url):
            return httpx.Response(
                200, json={"best_oa_location": {"url_for_pdf": "https://x/oa.pdf"}}
            )
        return httpx.Response(200, content=_real_pdf_bytes(page))

    e = RefEntry(num="9", raw=raw, slug="smith-2021")
    out = resolve_entry(entry=e, dest_dir=tmp_path, email="a@b.org",
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        provided_dir=None)

    assert any("api.crossref.org" in c for c in calls), f"never asked Crossref: {calls}"
    assert out.status == "retrieved" and out.doi == "10.1148/r.2021"


def test_a_year_span_in_a_headline_is_not_a_page_range():
    """The gate reads a page range as proof of an article, and `2020-2025` in a
    policy document's title has that shape. Two four-digit years either side of
    a dash are a date span, not pages — the pattern that clears the gate has to
    be able to tell the difference, or every government report with a date
    range in its title goes to a title search."""
    from papertrace.refs import _is_webpage_reference

    assert _is_webpage_reference(
        "World Health Organization. Global strategy on digital health 2020-2025. "
        "https://www.who.int/publications/i/item/9789240020924"
    )
    # and a real page range still clears it
    assert not _is_webpage_reference(
        "Smith A. Deep learning triage. Clin Radiol. 2022; pages 1068-1083. "
        "https://www.clinicalradiologyonline.net/toc"
    )


def test_a_doi_that_lives_only_inside_the_link_is_still_a_doi():
    """URLs are stripped before the article signals are looked for, which hides
    a DOI written only as `https://doi.org/10.…`. `resolve_entry` happens not to
    ask in that case — it consults the gate only when no DOI was found — but a
    predicate that is wrong on its own is a trap for the next caller."""
    from papertrace.refs import _is_webpage_reference

    assert not _is_webpage_reference(
        "Zenodo dataset for the segmentation challenge. "
        "Available at: https://doi.org/10.5281/zenodo.1234567"
    )


def test_a_tracking_parameter_is_not_part_of_a_title():
    """`?utm_source=chatgpt.com` put `chatgpt` and `source` into the reference's
    token set, and the wrong paper is an editorial about ChatGPT — so the URL
    supplied two of the six matches that passed it. URL fragments inflate both
    the numerator and the denominator; neither belongs to a title."""
    from papertrace.refs import _title_tokens

    tokens = _title_tokens(ACR_WEBPAGE_REF)
    assert "chatgpt" not in tokens, "a tracking parameter matched the wrong paper's subject"
    assert "source" not in tokens
    assert not any(t in tokens for t in ("firstmedical", "assuranceprogram", "publications"))
    assert {"launches", "practice", "artificial"} <= tokens, "the title's own words survive"


def test_three_generic_domain_words_are_not_an_identity_check():
    """The last line of defence, in case a URL-only reference reaches it by some
    other route: with the URL stripped the ACR news page still scores 3/7 =
    0.43 against the rheumatology editorial, on `artificial`, `intelligence`
    and `medical` alone. A ratio is trivially cleared by a short reference full
    of generic domain vocabulary, and in this field that vocabulary is most
    papers' subject."""
    from papertrace.refs import _title_check_text

    state, detail = _title_check_text(ACR_WEBPAGE_REF, ACR_EDITORIAL_FIRST_PAGE)
    assert state != "verified", detail
    # and not laundered the other way either: nobody established this is a
    # different paper, only that the check cannot tell
    assert state == "unverifiable", detail
    # a page with none of the reference's words is still called wrong outright
    assert _title_check_text(ACR_WEBPAGE_REF, RIGHT_PAGE)[0] == "mismatch"


# --- the resolver must not mint bibliographic facts -------------------------
#
# A first real audit reported 46 references on a paper citing 43. Three of the
# paper's own table captions reached the resolver, and Crossref answered a title
# search for "Table 1. Dataset characteristics" with 10.7717/peerj.7892/table-1
# — a table-component DOI belonging to an unrelated paper. The report published
# all three as `paywalled`, i.e. as real works held behind a paywall. The title
# sanity check never fired, because it only runs on the download path and
# nothing was ever downloaded.


def test_a_non_reference_is_never_title_searched(tmp_path):
    """The parser is fallible, so the resolver is the second line. An entry with
    no year, no DOI and no arXiv id is not a citable work, and Crossref always
    answers a title search with *something*."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(
            200, json={"message": {"items": [{"DOI": "10.7717/peerj.7892/table-1"}]}}
        )

    e = RefEntry(num="44", raw="Table 1. Dataset characteristics")
    resolve_entry(e, tmp_path, "t@example.org", httpx.Client(transport=httpx.MockTransport(handler)))

    assert asked == [], f"a table caption was sent to a bibliographic search: {asked}"
    assert e.status == "no_doi"
    assert e.doi is None


def test_the_refusal_says_why_rather_than_reading_as_a_lookup_failure(tmp_path):
    """`no_doi` alone would read as "we looked and found nothing"."""
    e = RefEntry(num="44", raw="Table 2. Accuracy and reliability of thresholding models")
    resolve_entry(e, tmp_path, "t@example.org", _client({}))
    assert "not searched by title" in e.reason.lower()


def test_a_component_doi_from_a_title_search_is_refused(tmp_path):
    """Belt and braces: even a reference-shaped entry must not accept a DOI that
    names a table, a figure or a supplement. Those are parts of a work, never a
    work, whatever the title matched."""
    e = RefEntry(num="7", raw="Someone S. A real-looking reference. J Imaging 2020;5:1-9.")
    client = _client({
        "api.crossref.org": httpx.Response(
            200, json={"message": {"items": [{"DOI": "10.7717/peerj-cs.847/table-10"}]}}
        ),
    })
    resolve_entry(e, tmp_path, "t@example.org", client)

    assert e.doi is None, f"accepted a component DOI: {e.doi}"
    assert e.status == "no_doi"
    assert "table" in e.reason.lower() or "component" in e.reason.lower()


def test_a_component_doi_printed_in_the_reference_is_also_refused(tmp_path):
    """The same rule wherever the DOI came from — a supplement DOI printed in
    the reference itself is still not the paper.

    Parsed through `parse_references` on purpose: building a RefEntry by hand
    leaves `doi` unset, so the test would pass without exercising the guard.
    """
    (e,) = parse_references(
        "References\n1. Someone S. A paper. J Imaging 2020. doi:10.1234/abcd.2020.s001\n"
    )
    assert e.doi == "10.1234/abcd.2020.s001", "fixture did not parse the DOI it is testing"

    resolve_entry(e, tmp_path, "t@example.org", _client({}))
    assert e.status == "no_doi"
    assert e.doi is None


def test_an_ordinary_reference_still_reaches_crossref(tmp_path):
    """The guard must not gate real references — the failure that matters most
    here is the strict one, because a dropped reference is silent."""
    e = RefEntry(num="1", raw="Fixture F, Example E (2023) A method. J Synth Methods 5:e230024")
    client = _client({
        "api.crossref.org": httpx.Response(
            200, json={"message": {"items": [{"DOI": "10.1148/ryai.230024"}]}}
        ),
        "api.unpaywall.org": httpx.Response(200, json={}),
    })
    resolve_entry(e, tmp_path, "t@example.org", client)
    assert e.doi == "10.1148/ryai.230024"
    assert e.status == "paywalled"


def test_an_accepted_download_records_what_its_title_check_rested_on(tmp_path):
    """`title_check: verified` with no evidence beside it is a bare assurance.

    The mismatch branch always stated its detail; the accepting branch threw it
    away, so a real audit's manifest showed six sources marked verified with
    nothing a reader could weigh — and `unverifiable` looked the same.
    """
    pdf = _real_pdf_bytes("Preoperative deltoid size and fatty infiltration of the deltoid")
    (e,) = parse_references(
        "References\n1. B.P. Wiater et al. Preoperative deltoid size and fatty "
        "infiltration of the deltoid. Clin Orthop 2015. doi:10.1007/s11999-014-4047-2\n"
    )
    client = _client({
        "api.unpaywall.org": httpx.Response(
            200, json={"best_oa_location": {"url_for_pdf": "https://x/oa.pdf"}}
        ),
        "https://x/oa.pdf": httpx.Response(200, content=pdf),
    })
    resolve_entry(e, tmp_path, "t@example.org", client)

    assert e.status == "retrieved" and e.title_check == "verified"
    assert "title check:" in e.reason, e.reason
    assert "tokens on its first page" in e.reason, e.reason


def test_a_truncated_but_real_reference_is_still_searched(tmp_path):
    """The strict-direction error, caught on real data before it shipped.

    Two references in one audit reached the resolver truncated mid-title —
    authors plus half a title, no journal, no volume, no year — because the
    converter cut them short. Crossref found both correct DOIs from exactly
    that string. A shape test keyed on the year alone refused them, turning two
    resolvable references into recorded gaps: the silent failure, and the one
    that matters more than letting a stray caption through.
    """
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if "api.crossref.org" in str(request.url):
            return httpx.Response(
                200, json={"message": {"items": [{"DOI": "10.1177/0363546512452714"}]}}
            )
        return httpx.Response(404)

    e = RefEntry(
        num="14",
        raw="M.A. Slabaugh, N.A. Friel, V. Karas, A.A. Romeo, N.N. Verma, B.J. Cole, "
            "Interobserver and intraobserver reliability of the Goutallier Classification using",
    )
    resolve_entry(e, tmp_path, "t@example.org",
                  httpx.Client(transport=httpx.MockTransport(handler)))

    assert any("crossref" in u for u in asked), "a real reference was never looked up"
    assert e.doi == "10.1177/0363546512452714"


def test_a_surname_with_an_umlaut_still_contributes_tokens():
    """`_title_tokens` is `[a-z]{5,}`, so a diacritic splits a word or deletes
    it. `Späth` and `Müller` contributed NOTHING, and this set is what
    `titles_match`, `_same_work` and `_title_check_text` all compare — a
    surname that vanished cannot agree with its own paper.

    `_fold` existed in `refs.py` and was not used here. NFKD alone is not
    enough: it leaves ß, ø, æ, đ, ł undecomposed.
    """
    assert "spath" in models._title_tokens("Späth C, Makowski MR")
    assert "muller" in models._title_tokens("Müller H")
    assert "cristobal" in models._title_tokens("Romero-Cristóbal M")
    assert "kustner" in models._title_tokens("Küstner T")
    assert "bjornsson" in models._title_tokens("Bjørnsson B")


def test_folding_does_not_invent_or_merge_tokens():
    """Gate 4, the other direction: an ASCII title must tokenise exactly as
    before, and the stopword list must still bite after folding.

    Note the stopword list holds `commun`, not `communications` — so
    "Nature Communications" keeps a token. These four are all in the list.
    """
    assert models._title_tokens("Nature Science volume press") == set()
    assert models._title_tokens("Robust segmentation of anatomic structures") == {
        "robust",
        "segmentation",
        "anatomic",
        "structures",
    }
