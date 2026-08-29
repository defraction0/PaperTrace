"""Resolver chain tests on a mocked transport — no network, CI-safe."""

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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
    from papertrace.refs import _title_check_text

    entries = parse_references(LITTLEJOHNS_REFS)
    raw = entries[0].raw
    assert _title_check_text(raw, RIGHT_PAGE) is None  # matching paper passes
    reason = _title_check_text(raw, WRONG_PAGE)
    assert reason is not None and "title check failed" in reason
    # unverifiable is not the same as wrong — empty page text passes
    assert _title_check_text(raw, "") is None


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


def test_an_unreadable_provided_file_is_not_called_a_mismatch(tmp_path):
    """`_title_check_text` documents that an empty or unreadable page passes —
    unverifiable is not the same as wrong — and a scanned source is exactly the
    kind people supply by hand. It must not be reported as the wrong paper."""
    d = _folder(tmp_path, "littlejohns-2020.pdf")  # not a real PDF: no text
    e = _entry()
    resolve_entry(e, tmp_path, "t@example.org", _client({}), provided_dir=d)
    assert e.status == "provided"
    assert "unverified" not in e.reason.lower(), e.reason
