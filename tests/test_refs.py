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
