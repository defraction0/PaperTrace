"""Audit a slice on request — and say so, bluntly, on the report's last lines.

Two limits, one rule. `--max-claims N` checks the first N extracted claims and
retrieves only the references they cite; `--max-sources N` obtains and judges
against at most N cited sources, in bibliography order. Neither is allowed to
look like a smaller paper: everything left out is recorded — a `skipped`
reference with its reason, the selection in `results.json` — and every report
format ends by stating what the audit did not cover.

Offline like the rest of the suite: the retrieval chain is faked at
`_resolve_by_retrieval`, and no model is called.
"""

import json
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import (  # noqa: E402
    REF_STATUSES,
    ClaimExtraction,
    ClaimResult,
    RefEntry,
    RefManifest,
    RunResults,
    UncitedClaim,
)

ROOT = Path(__file__).resolve().parent.parent
PDF = b"%PDF-1.4 fake"


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text())


# --- the wire format ---------------------------------------------------------


def test_skipped_is_a_reference_status_that_round_trips_with_its_reason(tmp_path):
    """A reference left out on request is not `paywalled` and not `error`: it
    was never attempted, and the manifest has to be able to say so."""
    assert "skipped" in REF_STATUSES
    entry = RefEntry(
        num="8", raw="Author A (2020) A paper. J 1:1.", slug="author-2020", status="skipped",
        reason="skipped on request: past the limit of 6 sources obtained", skipped_by="sources",
    )
    manifest = RefManifest(manuscript="m.pdf", entries=[entry], limits={"max_sources": 6})
    path = tmp_path / "refs_manifest.json"
    manifest.to_json(path)

    payload = json.loads(path.read_text())
    jsonschema.validate(payload, _schema("refs_manifest.schema.json"))
    assert payload["summary"]["by_status"]["skipped"] == 1
    again = RefManifest.from_json(path)
    assert again.entries[0].status == "skipped"
    assert again.entries[0].skipped_by == "sources"
    assert again.limits == {"max_sources": 6}


def test_a_manifest_written_before_limits_existed_still_loads_as_unlimited(tmp_path):
    path = tmp_path / "refs_manifest.json"
    RefManifest(manuscript="m.pdf", entries=[RefEntry(num="1", raw="X (2020) Y.")]).to_json(path)
    payload = json.loads(path.read_text())
    del payload["limits"]
    del payload["entries"][0]["skipped_by"]
    path.write_text(json.dumps(payload))

    jsonschema.validate(json.loads(path.read_text()), _schema("refs_manifest.schema.json"))
    again = RefManifest.from_json(path)
    assert again.limits == {}
    assert again.entries[0].skipped_by is None


def test_results_carry_the_scope_and_older_files_load_with_none(tmp_path):
    scope = {
        "claims": {"requested": [1, 2, 3], "judged": [1, 2], "extracted": 2},
        "sources": {"max": 4, "for_claims": [1, 2, 3], "skipped_for_claims": ["7"],
                    "skipped_by_cap": []},
    }
    results = RunResults(
        manuscript="m.pdf", claims=[ClaimResult(id=1, claim="c", location="Intro", refs=["1"])],
        scope=scope,
    )
    path = tmp_path / "results.json"
    results.to_json(path)

    payload = json.loads(path.read_text())
    jsonschema.validate(payload, _schema("results.schema.json"))
    assert RunResults.from_json(path).scope == scope

    del payload["scope"]
    path.write_text(json.dumps(payload))
    jsonschema.validate(json.loads(path.read_text()), _schema("results.schema.json"))
    assert RunResults.from_json(path).scope == {}


def test_the_extraction_artifact_round_trips_and_states_no_verdict(tmp_path):
    """`out/claims.json` is what the extractor found, numbered in reading order,
    before anything was judged. It must not carry a verdict field at all: a
    default `not_retrieved` written there would read as a finding."""
    extraction = ClaimExtraction(
        manuscript="m.pdf", manuscript_sha256="ab" * 32, extractor="claude -p · some-model",
        date="2026-09-13",
        claims=[ClaimResult(id=1, claim="c", quote="the sentence", location="Intro ¶2",
                            ctx_ids=["block_0009:12:1"], refs=["1"], own_supplement=True)],
        uncited=[UncitedClaim(id=1, claim="u", quote="uq", location="Discussion")],
        truncated={"manuscript": {"chars": 200_000, "limit": 180_000}},
    )
    path = tmp_path / "claims.json"
    extraction.to_json(path)

    payload = json.loads(path.read_text())
    assert payload["schema"] == "claims/1"
    assert "verdict" not in payload["claims"][0], "the extraction knows no verdicts"
    assert "judgements" not in payload["claims"][0]
    jsonschema.validate(payload, _schema("claims.schema.json"))

    again = ClaimExtraction.from_json(path)
    c = again.claims[0]
    assert (c.id, c.claim, c.quote, c.location, c.ctx_ids, c.refs, c.own_supplement) == (
        1, "c", "the sentence", "Intro ¶2", ["block_0009:12:1"], ["1"], True
    )
    assert again.uncited[0].quote == "uq"
    assert again.truncated == extraction.truncated
    assert again.manuscript_sha256 == "ab" * 32
    assert again.extractor == "claude -p · some-model"


# --- the selection -----------------------------------------------------------


def test_select_claims_keeps_reading_order_and_drops_ids_the_paper_does_not_have():
    """The array is the contract — `--max-claims 5` is `[1, 2, 3, 4, 5]`, and a
    cherry-picked `[3, 7]` travels the same road later. An id the extraction
    never produced is simply absent from the result, never invented."""
    from papertrace.check import select_claims

    claims = [ClaimResult(id=i, claim=f"c{i}", location="") for i in (1, 2, 3, 4)]
    assert [c.id for c in select_claims(claims, [3, 1, 9])] == [1, 3]
    assert [c.id for c in select_claims(claims, [1, 2, 3, 4, 5])] == [1, 2, 3, 4]
    assert select_claims(claims, []) == []
    assert select_claims(claims, None) == claims


# --- refs: what is not attempted is recorded, not lost ----------------------


def _entries(n: int) -> list[RefEntry]:
    return [
        RefEntry(num=str(i), raw=f"Author{i} A ({2000 + i}) Paper number {i}. J Things {i}:1-9.",
                 slug=f"author{i}-{2000 + i}", doi=f"10.1000/paper{i}")
        for i in range(1, n + 1)
    ]


def _fake_chain(monkeypatch, *, fail: tuple[str, ...] = ()) -> list[str]:
    """Replace the online chain with one that records every attempt."""
    from papertrace import refs as refs_mod

    attempted: list[str] = []

    def fake(entry, dest, email, client):
        attempted.append(entry.num)
        if entry.num in fail:
            entry.status = "paywalled"
            entry.reason = "DOI resolved but no legal open-access copy found"
        else:
            dest.write_bytes(PDF)
            entry.status, entry.resolver, entry.pdf_path = "retrieved", "unpaywall", str(dest)
            entry.reason = "open-access copy via Unpaywall"
        return entry

    monkeypatch.setattr(refs_mod, "_resolve_by_retrieval", fake)
    return attempted


def test_without_a_limit_every_reference_is_attempted(tmp_path, monkeypatch):
    from papertrace.refs import resolve_all

    attempted = _fake_chain(monkeypatch)
    entries = _entries(4)
    resolve_all(entries, tmp_path / "dest", "t@example.org")
    assert attempted == ["1", "2", "3", "4"]
    assert not any(e.status == "skipped" for e in entries)


def test_a_source_cap_stops_obtaining_after_n_successes_and_skips_the_rest(tmp_path, monkeypatch):
    """The cap counts sources OBTAINED, not attempts: a paywalled reference was
    never retrieved, so it does not use up a slot. What lies past the cap is
    `skipped` with the limit named — never `paywalled`, never silent."""
    from papertrace.refs import resolve_all

    attempted = _fake_chain(monkeypatch, fail=("2",))
    entries = _entries(6)
    resolve_all(entries, tmp_path / "dest", "t@example.org", limit=3)

    assert attempted == ["1", "2", "3", "4"], "three obtained ([1], [3], [4]); [2] failed and did not count"
    assert [e.status for e in entries] == [
        "retrieved", "paywalled", "retrieved", "retrieved", "skipped", "skipped",
    ]
    for e in entries[4:]:
        assert e.skipped_by == "sources"
        assert e.pdf_path is None and e.resolver is None
        assert "skipped on request" in e.reason and "3" in e.reason and "--max-sources" in e.reason


def test_a_claims_selection_retrieves_only_the_references_those_claims_cite(tmp_path, monkeypatch):
    from papertrace.refs import resolve_all

    attempted = _fake_chain(monkeypatch)
    entries = _entries(5)
    resolve_all(entries, tmp_path / "dest", "t@example.org", only_labels={"2", "5"})

    assert attempted == ["2", "5"]
    assert [e.status for e in entries] == ["skipped", "retrieved", "skipped", "skipped", "retrieved"]
    assert entries[0].skipped_by == "claims"
    assert "skipped on request" in entries[0].reason and "none of the claims" in entries[0].reason


def test_both_limits_compose_in_bibliography_order(tmp_path, monkeypatch):
    """Needed references are taken in label order until the cap is reached;
    a reference nobody selected cites is skipped for THAT reason, so the two
    kinds of gap stay tellable apart in the manifest."""
    from papertrace.refs import resolve_all

    attempted = _fake_chain(monkeypatch)
    entries = _entries(6)
    resolve_all(entries, tmp_path / "dest", "t@example.org",
                only_labels={"1", "3", "5", "6"}, limit=2)

    assert attempted == ["1", "3"]
    assert {e.num: e.skipped_by for e in entries if e.status == "skipped"} == {
        "2": "claims", "4": "claims", "5": "sources", "6": "sources",
    }


def test_a_provided_file_for_a_skipped_reference_is_named_with_the_reason(tmp_path, monkeypatch):
    """The user put a PDF in the folder and it did nothing. The reason has to
    be the true one — "skipped on request" — and not the message for a spare
    copy of a paper that already has a file."""
    from test_provided_identity import _entries as identity_entries
    from test_provided_identity import _pdf

    from papertrace.refs import resolve_all, unused_provided

    _fake_chain(monkeypatch)
    d = tmp_path / "src"
    orphan = _pdf(d / "s41467-023-39631-x.pdf",
                  title="Opportunistic detection of type 2 diabetes using deep learning "
                        "from frontal chest radiographs",
                  doi="10.1038/s41467-023-39631-x", body="Article")
    entries = identity_entries()  # [1] Pyrros — the file above is its paper; [2] Sudlow
    resolve_all(entries, tmp_path / "dest", "t@example.org", provided_dir=d, only_labels={"2"})

    assert entries[0].status == "skipped"
    reasons = dict(unused_provided(entries, d))
    assert orphan in reasons
    assert "[1]" in reasons[orphan] and "skipped on request" in reasons[orphan]
    assert "already has a file" not in reasons[orphan]


# --- check: a skipped source is a recorded gap ------------------------------


def test_a_claim_whose_source_was_skipped_is_not_retrieved_and_says_so(tmp_path):
    """No model call, no guess: the source was never opened. The note names
    the cause rather than the generic status word, because "not available
    (skipped)" reads like a retrieval failure and it was a choice."""
    from papertrace.check import check_claims

    manifest = RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="A (2020) X.", slug="a-2020", status="skipped",
                 reason="skipped on request: past the limit of 1 sources obtained",
                 skipped_by="sources"),
    ])
    claims = [ClaimResult(id=1, claim="c", location="Intro", refs=["1"])]
    check_claims(claims, manifest, tmp_path, backend="pymupdf")

    assert claims[0].verdict == "not_retrieved"
    assert claims[0].note == "cited source not available (skipped on request)"


def test_audit_scope_records_what_check_and_refs_left_out():
    from papertrace.check import audit_scope

    extracted = [ClaimResult(id=i, claim=f"c{i}", location="", refs=[str(i)]) for i in range(1, 5)]
    manifest = RefManifest(
        manuscript="m.pdf",
        entries=[
            RefEntry(num="1", raw="a", status="retrieved", slug="a"),
            RefEntry(num="2", raw="b", status="skipped", skipped_by="sources"),
            RefEntry(num="3", raw="c", status="skipped", skipped_by="claims"),
            RefEntry(num="4", raw="d", status="skipped", skipped_by="claims"),
        ],
        limits={"claims": [1, 2], "max_sources": 1},
    )
    scope = audit_scope([1, 2, 9], extracted, extracted[:2], manifest)
    assert scope == {
        "claims": {"requested": [1, 2, 9], "judged": [1, 2], "extracted": 4},
        "sources": {"max": 1, "for_claims": [1, 2], "skipped_for_claims": ["3", "4"],
                    "skipped_by_cap": ["2"]},
    }
    # nothing asked, nothing skipped: no scope at all, so no disclosure fires
    plain = RefManifest(manuscript="m.pdf", entries=[RefEntry(num="1", raw="a", status="retrieved")])
    assert audit_scope(None, extracted, extracted, plain) == {}


# --- the disclosure: every format, and last -----------------------------------

FORMATS = ("report.md", "report_editor.html", "report_terminal.html", "report_viewer.html")


def _render(results: RunResults, out: Path, manifest=None) -> dict[str, str]:
    from papertrace.report import write_reports

    write_reports(results, manifest, out, png=False)
    return {name: (out / name).read_text() for name in FORMATS}


def _claim(**kw) -> ClaimResult:
    base = dict(id=1, claim="the cohort was imaged twice", location="Methods", refs=["1"],
                verdict="supported", source_slug="fixture-2020", source_page=1)
    base.update(kw)
    return ClaimResult(**base)


def test_an_unlimited_run_carries_no_scope_disclosure():
    from papertrace.disclosures import run_disclosures

    fired = run_disclosures(RunResults(manuscript="m.pdf", claims=[_claim()]))
    assert not any(d.key == "scope" for d in fired)


def test_a_limited_audit_is_stated_in_every_format_and_last(tmp_path):
    """The token reaches all four looks, like every disclosure — and in the
    three static ones it is also the last thing said, after the gap register,
    because a reader who skims to the end must not leave with the counts of a
    smaller paper."""
    from papertrace.disclosures import SCOPE_TOKEN, run_disclosures

    results = RunResults(
        manuscript="m.pdf", refs_total=30, refs_available=6, claims=[_claim()],
        scope={
            "claims": {"requested": [1, 2, 3, 4, 5], "judged": [1, 2, 3, 4, 5], "extracted": 23},
            "sources": {"for_claims": [1, 2, 3, 4, 5], "skipped_for_claims": ["7", "9"],
                        "skipped_by_cap": []},
        },
    )
    rendered = _render(results, tmp_path)
    d = next(x for x in run_disclosures(results) if x.key == "scope")
    assert d.level == "warn" and d.token == SCOPE_TOKEN
    for name, body in rendered.items():
        assert SCOPE_TOKEN in body, f"the limit is not stated in {name}"
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        body = rendered[name].lower()
        assert body.rindex(SCOPE_TOKEN) > body.index("not verified"), (
            f"{name} does not restate the limit after the gap register"
        )


def test_the_scope_text_says_what_was_left_out_in_numbers():
    from papertrace.disclosures import run_disclosures

    results = RunResults(
        manuscript="m.pdf", refs_total=30, refs_available=12, claims=[_claim()],
        scope={
            "claims": {"requested": [1, 2, 3, 4, 5], "judged": [1, 2, 3, 4, 5], "extracted": 23},
            "sources": {"for_claims": [1, 2, 3, 4, 5], "skipped_for_claims": ["7", "9", "12"],
                        "skipped_by_cap": []},
        },
    )
    d = next(x for x in run_disclosures(results) if x.key == "scope")
    assert "claims 1–5 of the 23 extracted" in d.text
    assert "`--max-claims 5`" in d.text
    assert "18" in d.text and "unjudged" in d.text
    assert "[7], [9], [12]" in d.text
    assert "not the paper" in d.text
    assert "judged or not" in d.text, "the coverage figure counts every extracted claim"
    assert "1–5 of 23" in d.short and "3 references skipped" in d.short


def test_a_source_cap_names_the_references_it_skipped_and_the_claims_it_cost():
    from papertrace.disclosures import run_disclosures

    results = RunResults(
        manuscript="m.pdf", refs_total=5, refs_available=2,
        claims=[
            _claim(),
            ClaimResult(id=2, claim="second", location="Results", refs=["4"],
                        verdict="not_retrieved", note="cited source not available (skipped on request)"),
            ClaimResult(id=3, claim="third", location="Results", refs=["9"],
                        verdict="not_retrieved", note="cited source not available (paywalled)"),
        ],
        scope={"sources": {"max": 2, "skipped_for_claims": [], "skipped_by_cap": ["3", "4", "5"]}},
    )
    d = next(x for x in run_disclosures(results) if x.key == "scope")
    assert "At most 2 cited sources were obtained (`--max-sources 2`)" in d.text
    assert "[3], [4], [5]" in d.text
    assert "1 checked claim cites only skipped references" in d.text
    assert "at most 2 sources" in d.short


def test_a_limit_that_left_nothing_out_is_still_stated():
    """The limit was set, so it is stated — and stated as having changed
    nothing, which is the one thing a reader could not otherwise know."""
    from papertrace.disclosures import run_disclosures

    results = RunResults(
        manuscript="m.pdf", refs_total=3, refs_available=3, claims=[_claim()],
        scope={
            "claims": {"requested": list(range(1, 51)), "judged": [1, 2, 3], "extracted": 3},
            "sources": {"max": 10, "skipped_for_claims": [], "skipped_by_cap": []},
        },
    )
    d = next(x for x in run_disclosures(results) if x.key == "scope")
    assert "`--max-claims 50`" in d.text and "left nothing out" in d.text
    assert "`--max-sources 10`" in d.text and "was not reached" in d.text


def test_a_cherry_picked_selection_is_named_by_its_ids():
    from papertrace.disclosures import run_disclosures

    results = RunResults(
        manuscript="m.pdf", claims=[_claim()],
        scope={"claims": {"requested": [3, 7, 9], "judged": [3, 7], "extracted": 12}},
    )
    d = next(x for x in run_disclosures(results) if x.key == "scope")
    assert "claims 3 and 7 of the 12 extracted" in d.text
    assert "claims 3, 7 and 9 were asked for" in d.text
    assert "--max-claims" not in d.text


def test_the_skipped_references_are_listed_when_the_manifest_is_at_hand(tmp_path):
    from papertrace.disclosures import run_disclosures

    manifest = RefManifest(manuscript="m.pdf", entries=[
        RefEntry(num="1", raw="Kept K (2020) A paper that was read.", status="retrieved", slug="kept-2020"),
        RefEntry(num="2", raw="Left L (2021) A paper nobody opened.", status="skipped",
                 reason="skipped on request: past the limit of 1 sources obtained (--max-sources 1), so it was neither retrieved nor judged",
                 skipped_by="sources"),
    ], limits={"max_sources": 1})
    results = RunResults(manuscript="m.pdf", refs_total=2, refs_available=1, claims=[_claim()],
                         scope={"sources": {"max": 1, "skipped_for_claims": [], "skipped_by_cap": ["2"]}})
    d = next(x for x in run_disclosures(results, manifest) if x.key == "scope")
    assert len(d.rows) == 1 and "[2]" in d.rows[0] and "nobody opened" in d.rows[0]

    md = _render(results, tmp_path, manifest)["report.md"]
    assert "A paper nobody opened" in md.split("## Scope of this audit")[1]


def test_the_source_counts_do_not_call_a_skipped_reference_unobtainable(tmp_path):
    """`available / total` stays true, but "24 not obtainable" would be false
    for 18 references nobody tried. Each look separates the two."""
    results = RunResults(
        manuscript="m.pdf", refs_total=30, refs_available=6, claims=[_claim()],
        scope={
            "claims": {"requested": [1, 2, 3, 4, 5], "judged": [1, 2, 3, 4, 5], "extracted": 23},
            "sources": {"for_claims": [1, 2, 3, 4, 5],
                        "skipped_for_claims": [str(n) for n in range(13, 31)],
                        "skipped_by_cap": []},
        },
    )
    rendered = _render(results, tmp_path)
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        assert "18 skipped on request" in rendered[name], name
    terminal = rendered["report_terminal.html"]
    assert "6 not obtainable" in terminal
    assert "24 not obtainable" not in terminal
