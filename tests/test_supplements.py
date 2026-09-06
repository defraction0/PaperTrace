"""Supplemental material: a supplement is a document, never the article.

Offline like the rest of the suite — no network, no model calls.
"""

import json
import sys
from pathlib import Path

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
    assert set(sup) == {"slug", "pdf_path"}, sup
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
