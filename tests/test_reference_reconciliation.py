"""The reference list is reconciled against what the manuscript actually cites.

`parse_references` was the one stage with no honest-degradation state: it always
returned a confident list, and nothing ever checked the count. A live audit
misnumbered 27 of 41 references — docling stripped the `[N]` numerals, a running
header split reference [14] across a page break, and every label from [15] on
shifted by one. The label is the join key, so claims citing >= [15] were judged
against the wrong papers.

Offline: Crossref is faked with `httpx.MockTransport`, no model calls.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import RefEntry, citation_labels  # noqa: E402
from papertrace.refs import _entry  # noqa: E402

# --- the label rule, now shared ---------------------------------------------


def test_citation_labels_reads_singles_lists_and_ranges():
    """The rule `coverage_audit` has always used, now callable by `refs` too."""
    assert citation_labels("as shown [3] and [7,8] and [11-13]") == {
        "3", "7", "8", "11", "12", "13"
    }


def test_citation_labels_is_the_same_rule_the_coverage_audit_uses():
    """Moved, not reimplemented. A second copy of this rule is exactly the
    defect the codebase already shipped for `is_references_heading`."""
    from papertrace.check import citation_labels_in_text

    body = "one [1] two [2,3] three [5-7]\n\n## References\n[9] Never cited in body.\n"
    assert citation_labels_in_text(body) == {"1", "2", "3", "5", "6", "7"}
    # the wrapper's only job is cutting the reference list off first
    assert citation_labels(body) == {"1", "2", "3", "5", "6", "7", "9"}


# --- the Crossref leg --------------------------------------------------------


def _deposit(refs: list[dict], total: int | None = None) -> httpx.MockTransport:
    """A Crossref `/works/{doi}` response carrying a deposited reference list."""
    body = {
        "message": {
            "publisher": "Fixture Publishing",
            "references-count": total if total is not None else len(refs),
            "reference": refs,
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.crossref.org/works/" in str(request.url), request.url
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _structured(n: int) -> list[dict]:
    return [
        {
            "key": f"10.1/x_b{i * 5:04d}",  # publisher-specific: never a numbering signal
            "DOI": f"10.1234/fixture.{i}",
            "author": f"Author{i} A",
            "article-title": f"A paper about topic {i}",
            "journal-title": "J Fixture",
            "volume": "12",
            "first-page": str(100 + i),
            "year": "2020",
        }
        for i in range(1, n + 1)
    ]


def test_a_deposited_list_is_numbered_by_array_order_not_by_key():
    """Keys are publisher-specific — `_b0005`, `_bib1`, `3400_CR1`,
    `R10-45-20210317`, and two schemes inside one Elsevier deposit. Array order
    is the only portable signal, and reading a number out of a key would
    renumber every reference of every publisher that does not use `_bibN`."""
    from papertrace.refs import crossref_reference_list

    with httpx.Client(transport=_deposit(_structured(3))) as client:
        entries = crossref_reference_list(client, "10.1/paper", "t@example.org")

    assert [e.num for e in entries] == ["1", "2", "3"]
    assert entries[0].doi == "10.1234/fixture.1"
    assert "A paper about topic 1" in entries[0].raw


def test_an_unstructured_deposit_keeps_the_citation_string_verbatim():
    """`raw` is what `_title_check` and `_slug` consume, so it has to read like
    a printed reference whichever shape the deposit arrives in."""
    from papertrace.refs import crossref_reference_list

    refs = [{"key": "ref1", "unstructured": "Smith J. A paper. J Fixture 2019;12:100-9."}]
    with httpx.Client(transport=_deposit(refs)) as client:
        entries = crossref_reference_list(client, "10.1/paper", "t@example.org")

    assert entries[0].raw == "Smith J. A paper. J Fixture 2019;12:100-9."
    assert entries[0].year == "2019"
    assert entries[0].slug == "smith-2019"


def test_a_component_doi_in_a_deposit_is_refused_like_any_other():
    """A publisher can deposit a reference to its own table. A part of a work is
    never the work a reference cites, wherever the DOI came from."""
    from papertrace.refs import crossref_reference_list

    refs = [{"key": "ref1", "DOI": "10.7717/peerj.7892/table-1",
             "article-title": "Table 1. Dataset characteristics", "year": "2019"}]
    with httpx.Client(transport=_deposit(refs)) as client:
        entries = crossref_reference_list(client, "10.1/paper", "t@example.org")

    assert entries[0].doi is None, "a table-component DOI was accepted from Crossref"


def test_no_deposit_is_none_not_an_empty_list():
    """None means "this publisher deposits nothing"; `[]` would read as "this
    paper cites nothing", and the reconciler must be able to tell them apart."""
    from papertrace.refs import crossref_reference_list

    def handler(request):
        return httpx.Response(200, json={"message": {"publisher": "X", "reference": []}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert crossref_reference_list(client, "10.1/paper", "t@example.org") is None


@pytest.mark.parametrize("status", [404, 500])
def test_crossref_unreachable_is_none_and_never_raises(status):
    """A missing DOI and a Crossref outage are both "no candidate from this
    leg" — neither may take the audit down with it."""
    from papertrace.refs import crossref_reference_list

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        assert crossref_reference_list(client, "10.1/paper", "t@example.org") is None


def test_a_doi_only_deposit_is_kept_not_discarded():
    """Wiley deposits references as a bare DOI and nothing else. An earlier
    version rendered those to an empty string, dropped them, and reported
    "the publisher deposited 3 of its own declared 52" — blaming Wiley for a
    complete 52-reference deposit this tool could not read. They are the *best*
    references in a deposit: the DOI is already resolved, so retrieval skips the
    title search entirely."""
    from papertrace.refs import crossref_deposit

    refs = [{"key": f"e_1_2_{i}", "doi-asserted-by": "publisher",
             "DOI": f"10.1056/NEJMoa19117{i:02d}"} for i in range(1, 6)]
    with httpx.Client(transport=_deposit(refs)) as client:
        deposit = crossref_deposit(client, "10.1/paper", "t@example.org")

    assert len(deposit.entries) == 5, "DOI-only references were discarded"
    assert deposit.unrenderable == 0
    assert deposit.entries[0].doi == "10.1056/NEJMoa1911701"
    # named for what is actually known about it, which is the DOI, not an author
    assert deposit.entries[0].slug == "nejmoa1911701"
    assert len({e.slug for e in deposit.entries}) == 5


def test_a_shortfall_is_reported_as_this_tools_limitation():
    """`references-count` counts the references *deposited*, so it always equals
    the array length — verified live on Elsevier, Springer, Wiley, PLOS and
    RSNA records. A shortfall can therefore only mean this tool failed to render
    an entry, and must be counted and named as such rather than described as the
    publisher depositing less than it claimed."""
    from papertrace.refs import crossref_deposit

    refs = [{"key": "r1", "author": "Smith J", "article-title": "One", "year": "2020"},
            {"key": "r2"}]  # nothing renderable: no author, no title, no DOI
    with httpx.Client(transport=_deposit(refs)) as client:
        deposit = crossref_deposit(client, "10.1/paper", "t@example.org")

    assert deposit.deposited == 2
    assert len(deposit.entries) == 1
    assert deposit.unrenderable == 1


def test_a_short_deposit_is_caught_by_the_body_labels_not_by_a_count_field():
    """The real protection against a genuinely short deposit. One publisher
    deposited 2 references for a ~40-reference paper; the record reports
    `references-count: 2`, so nothing in the payload gives it away. The
    manuscript's own labels do."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 41)}
    _entries, rec = reconcile(body, crossref=_parsed([1, 2]), parsed=_parsed(list(range(1, 41))))
    assert rec.source == "parsed" and rec.verified is True


# --- the reconciler ----------------------------------------------------------


# Distinct, reference-shaped content for every entry, sharing no word with any
# other. `_same_work` compares *content* — a fixture whose entries all read
# alike cannot tell a right list from a shuffled one, and the one that read
# `f"Parsed reference {n}. 2020."` could not: every entry shared the token
# `reference`, so a divergent entry compared equal and the two divergence tests
# went red the moment slug comparison was replaced.
_SYL = ("mar", "hol", "dahl", "vik", "berg", "rud", "gren", "stad", "lund", "sund")


def _ref_text(n: int) -> str:
    """Every word of length >= 5 carries the entry's own stem.

    `_title_tokens` keeps words of five letters or more, and `_same_work`
    accepts a 0.34 overlap — so two shared boilerplate words ("after",
    "Fixture") in a six-word reference were enough to make every entry the same
    work as every other. Real reference strings are long, and the ratio means
    something there; a fixture has to earn that the hard way.
    """
    stem = _SYL[n % 10] + _SYL[(n // 10) % 10]
    return (f"{(stem + 'sen').capitalize()} A, {(stem + 'strom').capitalize()} B. "
            f"{(stem + 'osis').capitalize()} and {stem}ectomy in {stem}opathy. "
            f"{(stem + 'ology').capitalize()} 2020;1:1-9.")


def _parsed(nums: list[int]) -> list[RefEntry]:
    """Built through `_entry` — the production path, which parses the year.

    Constructing `RefEntry` directly left `year` empty, so `_same_work` fell
    through to its token comparison with nothing to contradict it.
    """
    return [_entry(str(n), _ref_text(n)) for n in nums]


def test_the_real_failure_crossref_matches_the_body_and_the_parse_does_not():
    """The audit that started this: body cites [1]-[41], the PDF parse yields
    43 entries, the deposit yields 41."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 42)}
    entries, rec = reconcile(body, crossref=_parsed(list(range(1, 42))), parsed=_parsed(
        list(range(1, 44))))

    assert rec.source == "crossref"
    assert rec.verified is True
    assert len(entries) == 41
    assert rec.unverified_from is None


def test_ovids_partial_deposit_loses_to_the_parse():
    """Two references deposited, forty in the paper. The body's labels decide,
    and they say the deposit is wrong — so the parse is used and the deposit is
    not silently blended in."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 41)}
    entries, rec = reconcile(body, crossref=_parsed([1, 2]), parsed=_parsed(list(range(1, 41))))

    assert rec.source == "parsed"
    assert rec.verified is True
    assert len(entries) == 40


def test_when_neither_candidate_matches_the_body_nothing_is_verified():
    """Continue, disclose loudly, taint the affected verdicts — decided with the
    user. Refusing to run would be the wrong trade: the audit is still useful,
    it just must not present a numbering it cannot stand behind."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 42)}
    entries, rec = reconcile(body, crossref=_parsed(list(range(1, 40))), parsed=_parsed(
        list(range(1, 44))))

    assert rec.verified is False
    assert rec.source == "parsed", "the parse is the fallback — it is at least the paper's own text"
    assert len(entries) == 43
    assert rec.note, "an unverified numbering owes the reader a reason"


def test_two_disagreeing_candidates_locate_the_first_divergence():
    """`[1]`-`[14]` agreed in the real failure, and saying so is worth more than
    a blanket warning: a reader can still trust the head of the list."""
    from papertrace.refs import reconcile

    crossref = _parsed(list(range(1, 20)))
    parsed = _parsed(list(range(1, 20)))
    parsed[14] = _entry("15", "Quite B. Another work entirely. Nature 1999;1:1.")

    body = {str(n) for n in range(1, 30)}  # matches neither
    _entries, rec = reconcile(body, crossref=crossref, parsed=parsed)

    assert rec.verified is False
    assert rec.unverified_from == 15


def test_a_single_unchecked_candidate_never_claims_to_know_where_it_went_wrong():
    """With nothing to compare against, the first divergence is unknowable.
    Claiming `unverified_from = 40` on a 41-entry list would present 39 entries
    as verified on no evidence at all."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 42)}
    _entries, rec = reconcile(body, crossref=None, parsed=_parsed(list(range(1, 44))))

    assert rec.verified is False
    assert rec.unverified_from == 1


def test_a_clean_paper_gains_no_warning_it_has_not_earned():
    """`main.pdf`: 35 references, both readings agree with the body."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 36)}
    entries, rec = reconcile(body, crossref=_parsed(list(range(1, 36))), parsed=_parsed(
        list(range(1, 36))))

    assert rec.verified is True
    assert rec.source == "crossref", "both matched — prefer the leg whose DOIs are already resolved"
    assert rec.unverified_from is None
    assert len(entries) == 35


def test_no_body_labels_means_nothing_to_arbitrate_with():
    """An author-year paper cites nothing this tool can read, so the arbiter
    does not exist. That is not the same as "the parse is correct", and it is
    not the same as "the parse is wrong" — it is a third fact."""
    from papertrace.refs import reconcile

    entries, rec = reconcile(set(), crossref=None, parsed=_parsed(list(range(1, 20))))

    assert rec.verified is False
    assert rec.source == "parsed"
    assert "no bracketed numeric citation" in rec.note.lower()
    assert len(entries) == 19
    assert rec.unverified_from == 1, "one unchecked reading is evidence about nothing"


def test_a_shuffled_reading_is_not_corroborated():
    """The assertion the fixtures could not make until they carried distinct
    content. With every entry reading `Parsed reference N. 2020.`, a *fully
    shuffled* list reported `_first_divergence -> None`, so
    `test_two_disagreeing_candidates_locate_the_first_divergence` would have
    passed on a list where no entry was the paper its label names. This pins the
    fixture's discriminating power, not just the reconciler's."""
    from papertrace.refs import reconcile

    crossref = _parsed(list(range(1, 11)))
    # the same ten works, in reverse, renumbered [1]..[10] — the shape of the
    # bug this whole feature exists to catch, at maximum severity
    shuffled = [_entry(str(i), e.raw) for i, e in enumerate(reversed(crossref), 1)]

    _entries, rec = reconcile(set(), crossref=crossref, parsed=shuffled)

    assert rec.verified is False
    assert rec.unverified_from == 1, "a shuffled list was reported as corroborated"
    assert "agree as far as" not in rec.note


def test_two_agreeing_candidates_narrow_the_doubt_even_with_no_arbiter():
    """Measured on a real spread: three of six journals use superscript-numeric
    citations, whose markers vanish when the PDF is flattened to text, so there
    is no arbiter for half the papers this tool will meet. A PDF parse and a
    publisher deposit have no common failure mode, so where they agree is
    evidence about those entries — not confirmation, but not nothing either."""
    from papertrace.refs import reconcile

    crossref = _parsed(list(range(1, 31)))
    parsed = _parsed(list(range(1, 31)))
    parsed[19] = _entry("20", "Quite B. Another work entirely. Nature 1999;1:1.")

    _entries, rec = reconcile(set(), crossref=crossref, parsed=parsed)

    assert rec.verified is False, "corroboration is not confirmation"
    assert rec.unverified_from == 20
    assert "agree as far as entry [19]" in rec.note


def test_the_three_ways_a_crossref_candidate_can_be_absent_read_differently():
    """No DOI, no deposit, and Crossref unreachable are three different facts.
    Collapsing them is the scout's "pass --doi" bug, which told an operator who
    had just passed --doi to pass --doi."""
    from papertrace.refs import CROSSREF_NO_DEPOSIT, CROSSREF_NO_DOI, CROSSREF_UNREACHABLE

    notes = {CROSSREF_NO_DOI, CROSSREF_NO_DEPOSIT, CROSSREF_UNREACHABLE}
    assert len(notes) == 3, "two of the three absences share wording"
    assert all(n.strip() for n in notes)


# --- carrying it through the manifest ----------------------------------------


def test_the_new_manifest_fields_round_trip_and_older_manifests_still_load(tmp_path):
    """New JSON field ⇒ schema update plus a round-trip test (gate 2), and
    absent-safe: a manifest written before these fields has no such keys."""
    import jsonschema

    from papertrace.models import RefManifest

    path = tmp_path / "refs_manifest.json"
    RefManifest(
        manuscript="m.pdf",
        entries=_parsed([1, 2]),
        reference_source="crossref",
        numbering_verified=False,
        numbering_note="the body cites [1]-[41]; this list has 43 entries",
        unverified_from=15,
    ).to_json(path)

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json").read_text()
    )
    payload = json.loads(path.read_text())
    jsonschema.validate(payload, schema)

    back = RefManifest.from_json(path)
    assert back.reference_source == "crossref"
    assert back.numbering_verified is False
    assert back.unverified_from == 15

    for key in ("reference_source", "numbering_verified", "numbering_note", "unverified_from"):
        del payload[key]
    path.write_text(json.dumps(payload))
    jsonschema.validate(json.loads(path.read_text()), schema)
    old = RefManifest.from_json(path)
    assert old.reference_source == "parsed"
    # an old manifest never checked its numbering, and must not claim it did
    assert old.numbering_verified is False
    assert old.unverified_from is None


# --- the reader has to be told ------------------------------------------------


def _results():
    from papertrace.models import ClaimResult, RunResults

    return RunResults(
        manuscript="m.pdf",
        converter="pymupdf",
        claims=[ClaimResult(id=1, claim="a claim", location="Methods", refs=["20"],
                            verdict="supported", source_slug="x-2020", source_page=1)],
    )


def _unverified_manifest():
    from papertrace.models import RefManifest

    return RefManifest(
        manuscript="m.pdf",
        entries=_parsed(list(range(1, 44))),
        reference_source="parsed",
        numbering_verified=False,
        numbering_note="the manuscript cites [1]-[41], the parsed list has 43 references",
        unverified_from=15,
    )


def test_an_unconfirmed_numbering_is_disclosed_in_all_three_formats(tmp_path):
    """The whole point of reconciling: the failure has to be visible where the
    report is read, not only in `refs_manifest.json`."""
    from papertrace.disclosures import run_disclosures
    from papertrace.report import write_reports

    manifest = _unverified_manifest()
    results = _results()
    write_reports(results, manifest, tmp_path, png=False)

    d = next((x for x in run_disclosures(results, manifest) if x.key == "numbering"), None)
    assert d is not None, [x.key for x in run_disclosures(results, manifest)]
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        assert d.token in (tmp_path / name).read_text(), f"token missing from {name}"


def test_a_confirmed_numbering_adds_no_warning():
    """An ordinary paper must not grow a caveat it has not earned."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RefManifest

    manifest = RefManifest(
        manuscript="m.pdf", entries=_parsed([1, 2]),
        reference_source="crossref", numbering_verified=True,
    )
    assert not any(d.key == "numbering" for d in run_disclosures(_results(), manifest))


def test_a_claim_citing_a_doubtful_label_is_tainted_in_all_three_formats(tmp_path):
    """A run-level banner is not enough. The label is the join key, so the
    verdict itself may be about a different paper — and that has to be said
    beside the verdict, where someone acting on it will read it."""
    from papertrace.disclosures import claim_disclosures
    from papertrace.report import write_reports

    manifest = _unverified_manifest()
    results = _results()  # the claim cites [20], and doubt starts at [15]
    write_reports(results, manifest, tmp_path, png=False)

    d = next((x for x in claim_disclosures(results.claims[0], manifest)
              if x.key == "claim_numbering"), None)
    assert d is not None
    for name in ("report.md", "report_editor.html", "report_terminal.html"):
        assert d.token in (tmp_path / name).read_text(), f"token missing from {name}"


def test_a_claim_below_the_divergence_keeps_its_verdict_clean():
    """`[1]`-`[14]` agreed in the real failure. Tainting them too would spend
    the warning's credibility on claims that are fine."""
    from papertrace.disclosures import claim_disclosures
    from papertrace.models import ClaimResult

    claim = ClaimResult(id=2, claim="an early claim", location="Intro", refs=["3"],
                        verdict="supported", source_slug="y-2019", source_page=1)
    fired = claim_disclosures(claim, _unverified_manifest())
    assert not any(d.key == "claim_numbering" for d in fired)


def test_claim_disclosures_without_a_manifest_is_unchanged():
    """Every existing caller passes one argument, and must keep working."""
    from papertrace.disclosures import claim_disclosures
    from papertrace.models import ClaimResult

    claim = ClaimResult(id=3, claim="a claim", location="Methods", refs=["20"],
                        verdict="supported", source_slug="x-2020", source_page=1)
    assert not any(d.key == "claim_numbering" for d in claim_disclosures(claim))


# --- the CLI seam -------------------------------------------------------------


def test_an_unpassed_doi_option_is_never_mistaken_for_a_doi():
    """Typer's declared default is an `OptionInfo`, not the value the help
    screen shows — and these stages are called as plain functions too. An
    `OptionInfo` is truthy, so `doi or detect_doi(...)` took it for a real DOI
    and built a Crossref URL out of its repr: the offline test suite began
    making live calls and passed, because the machine had network.
    """
    import typer

    from papertrace.cli import _text_opt

    unpassed = typer.Option(None, "--doi", help="DOI of the paper itself")
    assert _text_opt(unpassed) is None
    assert _text_opt(None) is None
    assert _text_opt("   ") is None
    assert _text_opt("10.1234/real") == "10.1234/real"


def _one_page_paper(path: Path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Study", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. First paper. 2020.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. Second paper. 2021.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_refs_records_the_reconciliation_it_performed(tmp_path, monkeypatch):
    """End to end through the CLI stage, with both network legs stubbed: the
    body cites [1] and [2], the parse finds two references, so the numbering is
    confirmed and the manifest says which reading it holds."""
    import papertrace.cli as cli_mod
    import papertrace.refs as refs_mod
    from papertrace.models import RefManifest
    from papertrace.refs import CROSSREF_NO_DOI, CrossrefDeposit

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None: entries)
    seen = {}

    def _no_deposit(client, doi, email):
        seen["doi"] = doi
        return CrossrefDeposit(absent=CROSSREF_NO_DOI)

    monkeypatch.setattr(refs_mod, "crossref_deposit", _no_deposit)

    pdf = _one_page_paper(tmp_path / "paper.pdf")
    cli_mod.refs(manuscript=pdf, case=tmp_path / "case", provided=None,
                 email="t@example.org", parse_only=False, backend="pymupdf", doi=None)

    assert seen["doi"] is None, "an unpublished manuscript has no DOI to look up"
    manifest = RefManifest.from_json(tmp_path / "case" / "refs_manifest.json")
    assert manifest.numbering_verified is True
    assert manifest.reference_source == "parsed"
    assert manifest.unverified_from is None
    # the reader is told the deposit was never seen, and why
    assert "no DOI for the manuscript itself" in manifest.numbering_note


def _titled_paper(path: Path, title: str):
    """Like `_one_page_paper`, but the first block is a real-looking title.

    `paper_title` takes the first substantial block, so a fixture whose opening
    line is `A Study` gives the identity check almost nothing to work with —
    which is a real condition, tested separately, not the one under test here.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), title, fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. First paper. 2020.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. Second paper. 2021.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _refs_with_deposit(tmp_path, monkeypatch, deposit, title="Image registration improves "
                       "inter-reader agreement in CT of the pancreas"):
    """Run the `refs` stage with a stubbed deposit, and return the manifest."""
    import papertrace.cli as cli_mod
    import papertrace.refs as refs_mod
    from papertrace.models import RefManifest

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit", lambda client, doi, email: deposit)

    pdf = _titled_paper(tmp_path / "paper.pdf", title)
    cli_mod.refs(manuscript=pdf, case=tmp_path / "case", provided=None,
                 email="t@example.org", parse_only=False, backend="pymupdf",
                 doi="10.1234/asserted")
    return RefManifest.from_json(tmp_path / "case" / "refs_manifest.json")


def test_a_deposit_from_another_paper_is_not_used_to_renumber(tmp_path, monkeypatch):
    """`deposit_is_this_paper` existed, was documented and was unit-tested — and
    nothing called it. The DOI is scraped off page 1 or typed by hand, and a
    companion paper, an erratum or an earlier version can carry exactly as many
    references as the body cites, so `_covers` passes and the run prints
    "numbering confirmed" over another paper's bibliography. Every other
    retrieval route in this module is title-checked; the one route that can
    replace the whole list was not."""
    from papertrace.refs import CrossrefDeposit

    manifest = _refs_with_deposit(
        tmp_path, monkeypatch,
        CrossrefDeposit(entries=_parsed([1, 2]), deposited=2,
                        publisher="Fixture Publishing",
                        title="Maternal urinary fluoride and child neurobehavior at age three"),
    )

    assert manifest.reference_source == "parsed", "another paper's list was adopted"
    assert [e.num for e in manifest.entries] == ["1", "2"]
    assert "Alpha A" in manifest.entries[0].raw, manifest.entries[0].raw
    assert "10.1234/asserted" in manifest.numbering_note
    assert "not this paper" in manifest.numbering_note


def test_a_deposit_confirmed_as_this_paper_is_still_used(tmp_path, monkeypatch):
    """The gate must not cost the feature its point: a matching record is used,
    and the note says the identity was checked rather than assumed."""
    from papertrace.refs import CrossrefDeposit

    manifest = _refs_with_deposit(
        tmp_path, monkeypatch,
        CrossrefDeposit(entries=_parsed([1, 2]), deposited=2,
                        publisher="Fixture Publishing",
                        title="Image registration improves inter-reader agreement in CT "
                              "of the pancreas"),
    )

    assert manifest.reference_source == "crossref"
    assert manifest.numbering_verified is True
    # "confirmed", not merely "not refused" — and distinct from the unverifiable
    # wording, which contains the same phrase negated
    assert "was confirmed as this paper by title" in manifest.numbering_note
    assert "could not be confirmed" not in manifest.numbering_note


def test_an_identity_that_cannot_be_checked_is_disclosed_not_assumed(tmp_path, monkeypatch):
    """Tri-state, like every other title check here. A record with no title, or
    a paper whose title the ingest could not find, is an unknown — and an
    unknown is not a match and not a mismatch. Discarding it would throw away
    good deposits for a thin first page; using it silently would stamp
    "confirmed" on an identity nobody established."""
    from papertrace.refs import CrossrefDeposit

    manifest = _refs_with_deposit(
        tmp_path, monkeypatch,
        CrossrefDeposit(entries=_parsed([1, 2]), deposited=2,
                        publisher="Fixture Publishing", title=""),
    )

    assert manifest.reference_source == "crossref"
    assert manifest.numbering_verified is True
    assert "could not be confirmed as this paper" in manifest.numbering_note
    assert "too little title to compare" in manifest.numbering_note


def test_parse_only_reaches_no_network_leg_at_all(tmp_path, monkeypatch):
    """`--parse-only` is documented as "List references, no network". The
    reconciler's Crossref leg must not quietly break that promise."""
    import papertrace.cli as cli_mod
    import papertrace.refs as refs_mod

    def _boom(*a, **k):
        raise AssertionError("--parse-only reached the network")

    monkeypatch.setattr(refs_mod, "crossref_deposit", _boom)
    monkeypatch.setattr(refs_mod, "resolve_all", _boom)

    pdf = _one_page_paper(tmp_path / "paper.pdf")
    cli_mod.refs(manuscript=pdf, case=tmp_path / "case", provided=None,
                 email="t@example.org", parse_only=True, backend="pymupdf", doi=None)


# --- one slug, one source file ------------------------------------------------
#
# Found by a live run on a JAMA editorial, and far worse than the TypeError that
# revealed it. `_slug` takes the first token of `raw`, strips non-letters, and
# falls back to the literal "ref" when nothing is left. `_parse_bulleted` leaves
# the printed numeral at the front of `raw`, so every entry slugged `ref-<year>`
# — 23 of 28 references on that paper — and `resolve_all` writes each download
# to `<slug>.pdf`. Eleven retrieved sources wrote ONE file, each overwriting the
# last, and every claim citing them would be judged against whichever paper
# happened to download last. That is the wrong-paper failure this whole project
# exists to prevent, arriving silently.


def test_a_bulleted_reference_does_not_keep_its_printed_numeral():
    """The numeral is the list marker the converter failed to strip, not part of
    the reference. Leaving it in makes the first token non-alphabetic, which is
    what collapsed every slug onto the fallback."""
    from papertrace.refs import parse_references

    text = "\n".join([
        "- 1 . Rivara FP. The privilege of being Editor-in-Chief. JAMA Netw Open. 2024;7(6):e2421821.",
        "- 2 . Perencevich EN. Adapting to open access publishing. JAMA Netw Open. 2024;7(7):e2425000.",
        "- 3 . Loftfield E, Abnet CC, et al. Multivitamin use and mortality risk. JAMA Netw Open. 2024.",
    ])
    entries = parse_references(text)

    assert [e.num for e in entries] == ["1", "2", "3"]
    assert entries[0].raw.startswith("Rivara FP"), entries[0].raw
    assert entries[0].slug == "rivara-2024"
    assert entries[1].slug == "perencevich-2024"


def test_two_references_can_never_share_a_slug():
    """`resolve_all` writes each download to `<slug>.pdf`, so a shared slug is a
    shared file. Even a genuine collision — the same first author and year cited
    twice — must not put two different papers at one path."""
    from papertrace.refs import parse_references

    text = "\n".join([
        "1. Smith J. First paper by Smith. J Fixture. 2020;1:1-9.",
        "2. Smith J. A different paper by the same Smith. J Fixture. 2020;1:10-19.",
        "3. Smith J. A third one, same year again. J Fixture. 2020;1:20-29.",
    ])
    entries = parse_references(text)

    slugs = [e.slug for e in entries]
    assert len(set(slugs)) == len(slugs), slugs
    assert slugs[0] == "smith-2020", "the first keeps the natural slug"


def test_a_deposited_list_also_gets_unique_slugs():
    """The Crossref leg is the other producer of entries and needs the same
    guarantee — a deposit routinely carries the same author twice."""
    from papertrace.refs import crossref_reference_list

    refs = [
        {"key": "r1", "author": "Smith J", "article-title": "One", "year": "2020"},
        {"key": "r2", "author": "Smith J", "article-title": "Two", "year": "2020"},
    ]
    with httpx.Client(transport=_deposit(refs)) as client:
        entries = crossref_reference_list(client, "10.1/paper", "t@example.org")

    assert len({e.slug for e in entries}) == 2, [e.slug for e in entries]


def test_every_retrieved_source_gets_its_own_file(tmp_path):
    """The damage, stated as the thing that must not happen."""
    from papertrace.refs import parse_references

    text = "\n".join([
        "- 1 . Alpha A. A paper. J Fixture. 2020;1:1-9.",
        "- 2 . Beta B. Another paper. J Fixture. 2020;1:10-19.",
        "- 3 . Gamma G. A third paper. J Fixture. 2020;1:20-29.",
    ])
    entries = parse_references(text)
    dests = [tmp_path / f"{e.slug}.pdf" for e in entries]
    assert len(set(dests)) == len(dests), dests


def test_run_detects_the_doi_once_and_gives_it_to_both_stages(monkeypatch, tmp_path):
    """`refs` detects the printed DOI for the numbering check, but `run` used to
    forward its own raw `--doi` to the scout — so on `papertrace run paper.pdf`
    the numbering check got the detected DOI and the scout silently fell back to
    title matching, which the README warns can anchor to somebody else's paper.
    One detection, both consumers."""
    import papertrace.cli as cli_mod

    seen = {}
    monkeypatch.setattr(cli_mod, "_detected_doi", lambda m: "10.1234/detected")
    monkeypatch.setattr(cli_mod, "ingest", lambda **kw: None)
    monkeypatch.setattr(cli_mod, "refs", lambda **kw: seen.__setitem__("refs", kw.get("doi")))
    monkeypatch.setattr(cli_mod, "scout", lambda **kw: seen.__setitem__("scout", kw.get("doi")))
    monkeypatch.setattr(cli_mod, "check", lambda **kw: None)
    monkeypatch.setattr(cli_mod, "highlight", lambda **kw: None)
    monkeypatch.setattr(cli_mod, "report", lambda **kw: None)
    monkeypatch.setattr(cli_mod, "_guard_case", lambda *a, **k: "hash")
    monkeypatch.setattr(cli_mod, "_open_case", lambda *a, **k: None)

    pdf = _one_page_paper(tmp_path / "paper.pdf")
    cli_mod.run(manuscript=pdf, case=tmp_path / "case", provided=None,
                email="t@example.org", model=None, png=False, backend="pymupdf",
                with_scout=True, doi=None)

    assert seen["refs"] == "10.1234/detected"
    assert seen["scout"] == "10.1234/detected", "the scout was left to guess by title"


def test_a_deposit_from_the_wrong_paper_is_refused(tmp_path):
    """The DOI is scraped off page 1, or typed by hand. Nothing checked that the
    record behind it IS this manuscript — and an erratum, a preprint version or
    a companion paper can easily carry the same number of references, so
    `_covers` would pass and the report would print "numbering confirmed" over
    another paper's reference list. Every other retrieval path in this module is
    title-checked; the one path that can replace the entire list was not."""
    from papertrace.refs import deposit_is_this_paper

    assert deposit_is_this_paper(
        "Image registration improves inter-reader agreement in CT of pancreas",
        "Image registration improves inter-reader agreement in CT of pancreas") is True
    # a different paper entirely
    assert deposit_is_this_paper(
        "Image registration improves inter-reader agreement in CT of pancreas",
        "Maternal urinary fluoride and child neurobehavior at age three") is False
    # nothing to compare with is not a mismatch — it is an unknown, and an
    # unknown must not silently discard a good deposit
    assert deposit_is_this_paper("", "Some title") is None
    assert deposit_is_this_paper("Some title", "") is None


def test_a_confirmed_numbering_still_says_when_the_other_reading_disagreed():
    """`_covers` is a cardinality test. If the parse matches the body's labels
    the numbering is confirmed — but a deposit that disagreed is a second
    independent reading saying the list is wrong, and burying it in a field no
    template renders tells the reader nothing."""
    from papertrace.refs import reconcile

    body = {str(n) for n in range(1, 42)}
    _entries, rec = reconcile(body, crossref=_parsed(list(range(1, 40))),
                              parsed=_parsed(list(range(1, 42))))

    assert rec.verified is True and rec.source == "parsed"
    assert rec.contested is True, "the deposit disagreed and nobody is told"


def test_both_readings_agreeing_leaves_no_phantom_divergence():
    """`_first_divergence` returned `min(len)+1` when the readings agreed, so a
    41-entry list warned about "entries from [42] onward" while tainting no
    claim at all — the banner and the per-claim layer contradicting each other."""
    from papertrace.refs import reconcile

    same = lambda: _parsed(list(range(1, 42)))  # noqa: E731
    _entries, rec = reconcile(set(), crossref=same(), parsed=same())

    assert rec.verified is False, "no arbiter, so nothing is confirmed"
    assert rec.unverified_from is None, "agreement is not a divergence"
    assert "agree throughout" in rec.note


def test_a_deposits_author_initials_do_not_become_the_slug():
    """Springer deposits `"author": "C Huang"`, so the first letter-bearing
    token is the initial and the slug became `c-2020`. The slug is the download
    filename, the report's source id, and the `--provided` match key that
    `refs --provided`'s own help documents as `<firstauthor>-<year>.pdf`."""
    from papertrace.refs import crossref_reference_list

    refs = [
        {"key": "r1", "author": "C Huang", "article-title": "A paper", "year": "2020"},
        {"key": "r2", "author": "BP Abbott", "article-title": "Another", "year": "2016"},
        {"key": "r3", "author": "Foy", "article-title": "A third", "year": "2019"},
    ]
    with httpx.Client(transport=_deposit(refs)) as client:
        entries = crossref_reference_list(client, "10.1/paper", "t@example.org")

    assert [e.slug for e in entries] == ["huang-2020", "abbott-2016", "foy-2019"]


# --- the paper's own title, as the PDF declares it ---------------------------
#
# Measured on seven papers, four publishers: the block heuristic returns the
# article-type banner, not the title — `CLINICAL GUIDELINE` (Wiley),
# `RESEARCH ARTICLE` (Springer), `Journal Pre-proofs` (Elsevier), `Editorial`
# (AMA). The PDF's own metadata carries the exact title for six of the seven.
# Docling does not help: on the seventh it emits no `title` item at all and
# labels the real title `section_header`, behind the banner.


def _pdf_with_metadata_title(path: Path, title: str, first_block: str = "CLINICAL GUIDELINE"):
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": title})
    page = doc.new_page()
    page.insert_text((72, 100), first_block, fontsize=16)
    page.insert_text((72, 140), "Body text long enough to be a candidate title block.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_ingest_records_the_title_the_pdf_declares(tmp_path):
    """Recorded verbatim at ingest, for both backends, because it is provenance:
    what the document says about itself. Whether it is *usable* is a separate
    question, answered by `paper_title`."""
    from papertrace.ingest import ingest_pdf

    pdf = _pdf_with_metadata_title(tmp_path / "p.pdf", "A Real Article Title About Pancreas CT")
    smap = ingest_pdf(pdf, tmp_path / "out", backend="pymupdf")
    assert smap.declared_title == "A Real Article Title About Pancreas CT"

    from papertrace.models import SourceMap

    assert SourceMap.from_json(tmp_path / "out" / "source_map.json").declared_title == \
        "A Real Article Title About Pancreas CT"


def test_a_source_map_without_a_declared_title_still_loads(tmp_path):
    """Additive, like every other field added here: an older map opens, and the
    absence reads as "not recorded" rather than as an empty title."""
    import json

    from papertrace.models import SourceMap

    p = tmp_path / "old.json"
    p.write_text(json.dumps({"doc": "p.pdf", "pages": 1, "converter": "pymupdf", "blocks": []}))
    assert SourceMap.from_json(p).declared_title == ""


def test_paper_title_prefers_what_the_pdf_declares_over_the_first_block(tmp_path):
    """The whole point: `CLINICAL GUIDELINE` is what the layout offers and it
    identifies nothing."""
    from papertrace.ingest import ingest_pdf
    from papertrace.models import paper_title

    pdf = _pdf_with_metadata_title(tmp_path / "p.pdf",
                                   "Gaussian mixture modelling of intramuscular fat")
    smap = ingest_pdf(pdf, tmp_path / "out", backend="pymupdf")
    assert paper_title(smap) == "Gaussian mixture modelling of intramuscular fat"


def test_a_producer_artifact_is_not_a_title(tmp_path):
    """What a Word-produced manuscript declares — this tool's main case. Reading
    `Microsoft Word - Manuscript revised clean.docx` as the paper's title would
    hand the identity check four confident words that describe no paper, and a
    confident mismatch discards a good deposit."""
    from papertrace.ingest import ingest_pdf
    from papertrace.models import paper_title

    for junk in ("Microsoft Word - Manuscript revised final clean.docx",
                 "manuscript_revised_final_clean.docx", "untitled", ""):
        pdf = _pdf_with_metadata_title(tmp_path / "p.pdf", junk,
                                       first_block="Body block that is long enough to serve")
        smap = ingest_pdf(pdf, tmp_path / "out", backend="pymupdf")
        assert paper_title(smap) == "Body block that is long enough to serve", junk


# --- the bibliography as a fingerprint ---------------------------------------
#
# Measured on the 41-reference audit: 38 of 41 deposited works appear somewhere
# in the printed list (93%), and 0 of 41 appear in a different paper's list.
# Compared as a set, not positionally — the same pair scores 34% in order,
# because that paper's parse is the misnumbered one this feature exists to
# catch, and order is precisely what is in question.


def test_two_readings_of_one_bibliography_confirm_the_paper(tmp_path):
    """Identity without a title at all. This has to survive misnumbering, or it
    would only work on the papers that never needed it."""
    from papertrace.refs import deposit_corroborates

    deposit = _parsed(list(range(1, 21)))
    shuffled = [_entry(str(i), e.raw) for i, e in enumerate(reversed(deposit), 1)]

    c = deposit_corroborates(deposit, shuffled)
    assert c.confirms is True
    assert (c.found, c.total) == (20, 20)


def test_another_papers_bibliography_does_not_confirm_but_does_not_refute(tmp_path):
    """Asymmetric on purpose: agreement is evidence of identity, disagreement is
    not evidence of difference — two lists that disagree may be one paper read
    badly, which is this module's whole subject. So a low overlap says "no
    evidence", and the caller leaves the identity unconfirmed rather than
    calling the record another paper."""
    from papertrace.refs import deposit_corroborates

    c = deposit_corroborates(_parsed(list(range(1, 21))), _parsed(list(range(40, 60))))
    assert c.confirms is False
    assert c.found == 0
    assert c.refutes is False, "a bad parse must not be reported as a wrong paper"


def test_a_short_list_agreeing_proves_nothing():
    """Three references matching is a coincidence a two-page comment can produce."""
    from papertrace.refs import deposit_corroborates

    c = deposit_corroborates(_parsed([1, 2, 3]), _parsed([1, 2, 3]))
    assert c.confirms is False
    assert c.too_few is True


def test_an_unverifiable_title_falls_back_to_the_bibliography(tmp_path, monkeypatch):
    """The AMA-shaped paper: no metadata title, and docling offers only
    `Editorial`. The deposit is still checkable — against the list printed in
    the paper itself."""
    from papertrace.refs import CrossrefDeposit

    manifest = _refs_with_deposit(
        tmp_path, monkeypatch,
        CrossrefDeposit(entries=_parsed([1, 2]), deposited=2,
                        publisher="Fixture Publishing", title=""),
        title="Editorial",
    )
    # two entries is below the floor, so the bibliography cannot settle it either
    assert "could not be confirmed as this paper" in manifest.numbering_note


def _paper_with_n_refs(path: Path, title: str, n: int):
    """A paper citing [1]..[n], whose printed list holds the same works
    `_parsed` builds — so a deposit of those works corroborates it."""
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": title})
    page = doc.new_page()
    page.insert_text((72, 60), title, fontsize=16)
    cites = " ".join(f"[{i}]" for i in range(1, n + 1))
    page.insert_text((72, 90), f"Body text citing {cites} here.", fontsize=9)
    page.insert_text((72, 120), "References", fontsize=14)
    for i in range(1, n + 1):
        page.insert_text((72, 140 + i * 14), f"[{i}] {_ref_text(i)}", fontsize=7)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_the_bibliography_confirms_the_deposit_when_the_title_cannot(tmp_path, monkeypatch):
    """The AMA shape with a real reference list: `Editorial` where the title
    should be, no usable metadata — and the deposit still checked, against the
    list printed in the paper itself. Without this the gate is inert on the
    papers whose titles are unreadable, which measurement put at four of seven."""
    import papertrace.cli as cli_mod
    import papertrace.refs as refs_mod
    from papertrace.models import RefManifest
    from papertrace.refs import CrossrefDeposit

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit", lambda client, doi, email: CrossrefDeposit(
        entries=_parsed(list(range(1, 9))), deposited=8,
        publisher="Fixture Publishing", title="",  # the record offers no title either
    ))

    pdf = _paper_with_n_refs(tmp_path / "paper.pdf", "Editorial", 8)
    cli_mod.refs(manuscript=pdf, case=tmp_path / "case", provided=None,
                 email="t@example.org", parse_only=False, backend="pymupdf",
                 doi="10.1234/asserted")

    manifest = RefManifest.from_json(tmp_path / "case" / "refs_manifest.json")
    assert manifest.reference_source == "crossref"
    assert "8 of the 8 references" in manifest.numbering_note
    assert "another paper's bibliography would not" in manifest.numbering_note
    assert "unverified" not in manifest.numbering_note


# --- the banner and the per-claim layer must say the same thing --------------


def test_an_unnarrowed_doubt_taints_every_label_not_none(tmp_path):
    """`_numbering` renders "every entry is affected" whenever the doubt could
    not be narrowed, and `label_is_doubtful` returned False for *every* label
    for exactly the same reason — `unverified_from is None`. So the report's
    most severe warn-level disclosure asserted every entry was suspect while
    marking no claim suspect, and a reader acting on one verdict saw nothing.

    Two shapes land here: a manifest written before the reference list was
    reconciled at all, and a run where the two readings agree entry for entry
    with no arbiter to confirm either — the superscript-citation case. In both,
    nobody established which entries are wrong, and "unknown scope" has to read
    as "all of them" in both places or in neither."""
    from papertrace.disclosures import _numbering
    from papertrace.models import RefManifest

    old = RefManifest(manuscript="p.pdf", entries=_parsed([1, 2, 7]))
    assert old.numbering_verified is False and old.unverified_from is None

    assert "every entry is affected" in _numbering(old).short
    assert [x for x in ("1", "2", "7", "99") if old.label_is_doubtful(x)] == \
        ["1", "2", "7", "99"], "the banner claims every entry and the labels claim none"


def test_a_confirmed_numbering_still_taints_nothing():
    """The other direction, so the fix cannot be "taint everything always"."""
    from papertrace.models import RefManifest

    ok = RefManifest(manuscript="p.pdf", entries=_parsed([1, 2]),
                     numbering_verified=True, unverified_from=None)
    assert [x for x in ("1", "2", "99") if ok.label_is_doubtful(x)] == []


def test_a_narrowed_doubt_still_taints_only_the_tail():
    """And a located divergence keeps its scope: [1]-[14] stay trustworthy."""
    from papertrace.models import RefManifest

    m = RefManifest(manuscript="p.pdf", entries=_parsed(list(range(1, 20))),
                    numbering_verified=False, unverified_from=15)
    assert [x for x in ("1", "14", "15", "19") if m.label_is_doubtful(x)] == ["15", "19"]


# --- a file nobody named for this reference ----------------------------------


def _paper_pdf(path: Path, title: str, byline: str):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), title, fontsize=14)
    page.insert_text((72, 140), byline, fontsize=10)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_a_token_matched_provided_file_that_is_another_paper_is_not_used(tmp_path, monkeypatch):
    """`_unique_slugs` renames the second of two colliding entries to
    `smith-2019-r7`, and `_provided_candidates` drops slug tokens of three
    characters or fewer — so `r7`, the only thing distinguishing them, is
    invisible and `sources/smith-2019.pdf` matches *both*. Measured before this
    change: entry [7] came back `status=provided`, `title_check=mismatch`,
    `pdf_path=smith-2019.pdf` — judged against entry [2]'s paper.

    "Disclosed, not fatal" is right for a file the user *named* for a reference:
    they chose it, there is nothing to fall back to, and a scanned PDF yields no
    text to check. It is wrong for a file a token match found, because nobody
    chose it for this reference and the check says it is a different paper. Then
    the honest move is to keep looking, and to say the file was set aside."""
    import papertrace.refs as refs_mod
    from papertrace.refs import _entry, _unique_slugs, resolve_all

    monkeypatch.setattr(refs_mod, "_crossref_doi", lambda client, raw, email: None)
    prov, dest = tmp_path / "sources", tmp_path / "resolved"
    dest.mkdir()
    _paper_pdf(prov / "smith-2019.pdf",
               "Ultrasound elastography of the thyroid gland in children",
               "Smith J, Jones B. Journal of Paediatric Radiology 2019;12:100-9.")

    ents = _unique_slugs([
        _entry("2", "Smith J, Jones B. Ultrasound elastography of the thyroid gland in "
                    "children. J Paediatr Radiol 2019;12:100-9."),
        _entry("7", "Smith J, Patel R. Deep learning segmentation of renal cysts on CT. "
                    "Eur J Radiol 2019;44:220-8."),
    ])
    two, seven = resolve_all(ents, dest, email="t@example.org", provided_dir=prov)

    # the reference the file really is: unchanged, and confirmed
    assert (two.status, two.title_check) == ("provided", "verified")
    assert Path(two.pdf_path).name == "smith-2019.pdf"

    # the other one is not judged against it
    assert seven.pdf_path is None, "a claim would be judged against another paper"
    assert seven.status == "no_doi"
    # and the file it declined is named, because the tool knows it considered one
    assert "smith-2019.pdf" in seven.reason
    assert "different paper" in seven.reason
