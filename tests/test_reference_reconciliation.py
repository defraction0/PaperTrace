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
