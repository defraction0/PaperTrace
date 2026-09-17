"""Four readings of one bibliography, and the one thing the extra two may do.

`reconcile` still chooses between the Crossref deposit and the run backend's
parse. The flat-text reading and the model's reading are voters on a second
axis: they can cause a verdict to be withheld and nothing else. The first test
here is the load-bearing one — it is the whole safety property of the feature,
stated as an equality.
"""

import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402
from papertrace import reflist as reflist_mod  # noqa: E402
from papertrace.models import RefEntry, RefManifest, SourceMap  # noqa: E402
from papertrace.refs import _entry  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json"


def _paper(path: Path) -> Path:
    """A one-page PDF citing [1] and [2], with both references printed."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Fixture Imaging Study", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and also [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. A first paper. J Fixture. 2020;1:1-9.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. A second paper. J Fixture. 2021;2:10-19.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _work(num: str, doi: str) -> RefEntry:
    """Built through `_entry`, the production parse path — not by setting
    fields by hand, which would bypass the code every real candidate goes
    through (mirrors `tests/test_label_agreement.py`'s own `_work`)."""
    return _entry(num, f"Author for entry {num}. A distinctive title. doi:{doi}")


def _wire_offline(monkeypatch) -> None:
    """No network and no model call by default: retrieval is a no-op, Crossref
    answers nothing, and `claude` is treated as PRESENT regardless of the host.

    That last patch matters even though `reflist.propose` is what most of these
    tests mock, not `ask.claude_available`: `_llm_reference_reading` checks
    availability BEFORE calling `propose`, so on a machine with no `claude` CLI
    on PATH — the normal case in CI, and not the case on a developer's own
    machine, which is exactly the trap `ask_mod.claude_available` vs.
    `check_mod.claude_available` already caught once in this plan — every test
    below that mocks `propose` to return a fabricated reading would silently
    take the "not attempted" branch instead and never call the mock at all.
    The one test that wants the real "claude absent" branch overrides this
    itself, after this fixture runs.
    """
    import papertrace.ask as ask_mod
    import papertrace.refs as refs_mod
    from papertrace.refs import CrossrefDeposit

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None,
                        taken=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit",
                        lambda client, doi, email: CrossrefDeposit(absent="no deposit, in a test"))
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    monkeypatch.setattr(ask_mod, "claude_available", lambda: True)
    monkeypatch.setenv("PAPERTRACE_EMAIL", "test@example.org")


@pytest.fixture()
def offline(monkeypatch):
    _wire_offline(monkeypatch)


def _run_refs(tmp_path: Path, monkeypatch, **kw) -> RefManifest:
    """Wire the offline doubles, run `_refs_pipeline` on the fixture paper, and
    hand back the manifest it wrote — schema-validated on the way, so every
    caller of this helper gets Gate 2 for free rather than each writing its own
    `jsonschema.validate`.

    A plain function, not a fixture: Step 4's test needs per-call keyword
    overrides (`llm_refs=True` here, forwarded straight to `_refs_pipeline`),
    which a fixture cannot take.
    """
    import jsonschema

    _wire_offline(monkeypatch)
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"
    args = dict(manuscript=pdf, case=case, provided=None, email="test@example.org",
               parse_only=False, backend="pymupdf", llm_refs=True)
    args.update(kw)
    cli._refs_pipeline(**args)

    payload = json.loads((case / "refs_manifest.json").read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
    return RefManifest.from_json(case / "refs_manifest.json")


def _entries(case: Path) -> str:
    """The resolved entries, canonically serialised — the bytes Ruling 2 is about."""
    payload = json.loads((case / "refs_manifest.json").read_text())
    return json.dumps(payload["entries"], sort_keys=True, ensure_ascii=False)


def _smap(converter: str) -> SourceMap:
    """Only `converter` matters to the voter dict, and only its first token."""
    return SourceMap(doc="paper.pdf", pages=1, blocks=[], converter=converter)


def test_a_model_naming_different_papers_changes_no_resolved_entry(tmp_path, offline, monkeypatch):
    """Ruling 2, as an equality. The safety property the whole design rests on.

    The model's reading is a voter. It can withhold a verdict; it cannot put a
    paper into the manifest, cannot change a numeral, and cannot cause anything
    to be resolved or downloaded. So a run whose model reading names two
    completely different works must produce byte-identical entries to a run that
    never asked a model at all. If this test ever fails, `others["llm"]` has
    reached `reconcile`'s arguments or `resolve_all`, and no other test in this
    suite would notice.

    `backend="pymupdf"`, not `"docling"`: the brief's own draft of this test used
    `"docling"` on a real PDF with no mock in front of `ingest_pdf`, which would
    have triggered a real docling conversion — a ~500 MB model download on first
    use, per `CLAUDE.md` — on every run of an "offline" test. `reflist.propose`
    is fully mocked below and ignores its arguments, so which backend produced
    the parse is irrelevant to what this test actually checks.
    """
    pdf = _paper(tmp_path / "paper.pdf")
    fabricated = (
        [RefEntry(num="1", raw="Zeta Z. An entirely different paper. 1999.",
                  title="An entirely different paper", year="1999", seen_in=["docling"]),
         RefEntry(num="2", raw="Eta E. Another different paper. 1998.",
                  title="Another different paper", year="1998", seen_in=["pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2,
                                      readings=["docling", "pymupdf"]),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: fabricated)

    with_llm, without = tmp_path / "with", tmp_path / "without"
    cli._refs_pipeline(manuscript=pdf, case=with_llm, provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=True)
    cli._refs_pipeline(manuscript=pdf, case=without, provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=False)

    assert _entries(with_llm) == _entries(without)
    assert "different paper" not in _entries(with_llm)


def test_the_pymupdf_reading_is_skipped_on_a_pymupdf_backend_run():
    """A reading agreeing with itself is not corroboration.

    The flat-text voter exists because docling and pymupdf read a hanging-indent
    bibliography differently. On a run whose backend already IS pymupdf it is
    the identical text, so counting it would turn every `single` label into an
    `agreed` one and report corroboration that nothing corroborated.
    """
    parsed = [RefEntry(num="1", raw="Alpha A. A first paper. 2020.")]
    flat = [RefEntry(num="1", raw="Alpha A. A first paper. 2020.")]

    flat_run = cli._reference_readings(smap=_smap("pymupdf"), parsed=parsed, flat=flat)
    layout_run = cli._reference_readings(smap=_smap("docling 2.8.0"), parsed=parsed, flat=flat)

    assert set(flat_run) == {"parsed"}, flat_run
    assert set(layout_run) == {"parsed", "pymupdf"}, layout_run


def test_a_reading_that_is_absent_is_omitted_rather_than_empty():
    """An empty list is a reading that found nothing; an absent key is no reading.

    `label_agreement` counts readings that have a label. A reading present as
    `[]` votes against every label it does not carry, which is every label —
    turning "we did not ask" into "one reading says no".
    """
    got = cli._reference_readings(smap=_smap("docling 2.8.0"),
                                  parsed=[RefEntry(num="1", raw="Alpha A. 2020.")],
                                  crossref=None, flat=None, llm=None)
    assert set(got) == {"parsed"}, got


def test_no_llm_refs_makes_zero_model_calls(tmp_path, offline, monkeypatch):
    """The flag has to actually gate the spend, not just the disclosure."""
    import papertrace.ask as ask_mod

    calls = []
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: calls.append(1) or "[]")
    pdf = _paper(tmp_path / "paper.pdf")

    cli._refs_pipeline(manuscript=pdf, case=tmp_path / "case", provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=False)

    assert calls == []


def test_parse_only_makes_no_model_call_even_with_llm_refs_on(tmp_path, offline, monkeypatch):
    """`--parse-only` is documented as "List references, no network".

    A paid subprocess is exactly what that promise excludes, and the promise is
    the reason someone reaches for the flag. The flat-text reading still runs —
    it is local and needs no network — which is what closes the gap the comment
    at cli.py:560-563 describes.
    """
    import papertrace.ask as ask_mod

    calls = []
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: calls.append(1) or "[]")
    pdf = _paper(tmp_path / "paper.pdf")

    cli._refs_pipeline(manuscript=pdf, case=tmp_path / "case", provided=None,
                       email="test@example.org", parse_only=True, backend="pymupdf",
                       llm_refs=True)

    assert calls == []


def test_an_unavailable_claude_degrades_to_a_stated_absence(tmp_path, offline, monkeypatch):
    """The cardinal rule, at this seam. Never "it agreed" — "it was not asked".

    `claude` missing from PATH is the ordinary case for someone who installed
    papertrace and not Claude Code. The run proceeds on the deterministic
    readings, the manifest records the reason, and no format anywhere says a
    model corroborated anything.
    """
    import papertrace.ask as ask_mod

    monkeypatch.setattr(ask_mod, "claude_available", lambda: False)
    monkeypatch.setattr(ask_mod, "_ask",
                        lambda prompt, model=None: pytest.fail("claude was absent and asked anyway"))
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["reflist_model"] == ""
    assert any("not attempted" in f for f in payload["reflist_fields_discarded"]), payload
    assert "llm" not in payload["corroborating_readings"]
    assert payload["numbering_corroborated"] is False


def test_a_failed_model_call_does_not_kill_the_refs_stage(tmp_path, offline, monkeypatch):
    """A timeout at the bibliography must not cost the user the retrieval.

    `refs` has already parsed the list and is about to fetch the sources. A
    RuntimeError out of the seam is a failed corroboration attempt, not a failed
    refs stage — and the reason lands where a reader can see it.
    """
    def boom(*a, **kw):
        raise RuntimeError("claude -p timed out after 600s")

    monkeypatch.setattr(reflist_mod, "propose", boom)
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert len(payload["entries"]) == 2
    assert any("timed out" in f for f in payload["reflist_fields_discarded"]), payload
    assert payload["reflist_model"] == ""


def test_corroboration_requires_every_cited_label_to_agree(tmp_path, offline, monkeypatch):
    """One disputed label is not "mostly corroborated".

    The flag is read as a reassurance, so it must mean what it says: every label
    the body cites was agreed by at least two readings. Any disagreement is
    `disputed` — no majority vote and no ranking, because "a non-unique match is
    refused, never ranked".
    """
    pdf = _paper(tmp_path / "paper.pdf")
    disagrees = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020", seen_in=["docling", "pymupdf"]),
         RefEntry(num="2", raw="Gamma G. A wholly unrelated third paper. 2015.",
                  title="A wholly unrelated third paper", year="2015", seen_in=["A"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: disagrees)
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["numbering_corroborated"] is False
    assert payload["labels_disputed"] == ["2"], payload["labels_disputed"]
    assert payload["corroborating_readings"] == []


def test_no_model_reply_can_set_numbering_verified(tmp_path, offline, monkeypatch):
    """The invariant, asserted here as well as in Task 8.

    Task 8 parametrises this over every reply shape; this is the one shape that
    would be most tempting — a model agreeing with the parse entry for entry.
    Corroboration is a second axis, not evidence of a checked numbering.

    Not `_paper()`: that fixture cites exactly the two references it prints, so
    `_covers` — the extent check `reconcile` uses on its own, with no model in
    the loop at all — is already satisfied by the parse alone, and `verified`
    would come back `True` regardless of anything this test does. Nothing here
    could then tell "verified because the parse already covered the body's
    labels" apart from "verified because a mocked model agreed with it". This
    fixture prints a THIRD, uncited reference, so the parse alone has three
    entries against two cited labels and fails `_covers` on its own — the only
    way to make the two cases distinguishable.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Fixture Imaging Study", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and also [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. A first paper. J Fixture. 2020;1:1-9.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. A second paper. J Fixture. 2021;2:10-19.", fontsize=11)
    page.insert_text((72, 270), "[3] Gamma G. A third, uncited paper. J Fixture. 2019;3:1-5.",
                     fontsize=11)
    pdf = tmp_path / "paper.pdf"
    doc.save(pdf)
    doc.close()
    agrees = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020", seen_in=["docling", "pymupdf"]),
         RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                  title="A second paper", year="2021", seen_in=["docling", "pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: agrees)
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["numbering_verified"] is False
    assert payload["numbering_corroborated"] is True
    assert payload["corroborating_readings"] == ["llm", "parsed"]


def test_both_commands_declare_the_flag_and_run_forwards_it(tmp_path, monkeypatch):
    """`run` calls `_refs_pipeline` directly, so a parameter it forgets is dropped.

    That failure is silent: the audit runs without the reading and says nothing
    about it. `refs` and `run` must both declare the flag, and `run` must pass it.
    """
    import inspect

    # captured BEFORE `_refs_pipeline` is monkeypatched below: the brief's own
    # draft of this test inspected `cli._refs_pipeline` AFTER replacing it with
    # a bare `lambda **kw: seen.update(kw)`, so the "signature" it asserted on
    # was the lambda's, which has no `llm_refs` parameter at all — a `KeyError`,
    # not the intended pass. The real function's signature has to be read while
    # it is still the real function.
    refs_pipeline_sig = inspect.signature(cli._refs_pipeline)

    seen = {}
    monkeypatch.setattr(cli, "_ingest_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "_refs_pipeline", lambda **kw: seen.update(kw))
    monkeypatch.setattr(cli, "scout", lambda **kw: None)
    monkeypatch.setattr(cli, "_check_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "highlight", lambda **kw: None)
    monkeypatch.setattr(cli, "_report_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    pdf = _paper(tmp_path / "paper.pdf")

    cli.run(manuscript=pdf, case=tmp_path / "case", provided=None, email="test@example.org",
            model=None, png=False, backend="pymupdf", with_scout=False, doi=None,
            formats=None, supplement=None, llm_refs=False)

    assert seen["llm_refs"] is False
    for command in (cli.refs, cli.run):
        assert "llm_refs" in inspect.signature(command).parameters, command.__name__
    assert refs_pipeline_sig.parameters["llm_refs"].default is True


def test_the_reflist_disclosure_reaches_every_format_and_carries_the_ceiling(tmp_path):
    """The required disclosure, and the sentence that bounds what it means.

    "A model agreeing with a parse is a second reading, not confirmation" is the
    only honest claim available about this reading, and a reader who sees the
    model named without it will read it as confirmation.
    """
    from papertrace.disclosures import REFLIST_TOKEN, run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="claude-opus-5",
                           reflist_fields_discarded=["[2] journal"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    d = fired[0]
    assert d.token == REFLIST_TOKEN
    assert "second reading, not confirmation" in d.text
    assert "may also have missed" in d.text
    assert "claude-opus-5" in d.text


def test_a_model_reading_that_was_not_obtained_still_discloses_that(tmp_path):
    """Silence would be read as "no news". The attempt and its failure are news.

    The token stays true in this branch because the sentence is phrased around
    it — the same discipline `SUPPLEMENT_IDENTITY_TOKEN` is written under.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="",
                           reflist_fields_discarded=["not attempted — claude is not on PATH"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "not attempted" in fired[0].text
    assert "agreed" not in fired[0].text


def test_corroboration_is_disclosed_without_claiming_the_numbering_was_verified(tmp_path):
    """The false alarm this fixes, and the overstatement it must not become.

    On the 22-vs-19 case the numbering warning fires for a real reason. Where
    two readings do agree on every cited label, a reader deserves to be told —
    and deserves not to be told the numbering was checked, because it was not.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", numbering_verified=False,
                           numbering_corroborated=True,
                           corroborating_readings=["llm", "parsed", "pymupdf"])
    fired = {d.key for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)}
    assert "numbering_corroboration" in fired
    assert "numbering" in fired, "corroboration must not suppress the unconfirmed-numbering warning"


def test_the_new_manifest_fields_round_trip_and_validate(tmp_path):
    """Gate 2. Five additive fields, none required, all readable by an older file.

    These five landed already in Task 3, and this test exercises them again
    from Task 6's own module — it is the one Step 2 offered, kept for parity
    with the brief, and `test_reflist_numbering_findings_round_trips_and_validates`
    below covers the one field this task actually adds.
    """
    import jsonschema

    m = RefManifest(manuscript="p.pdf", numbering_corroborated=True,
                    corroborating_readings=["parsed", "pymupdf"],
                    labels_disputed=["6", "7"], reflist_model="claude-opus-5",
                    reflist_fields_discarded=["[6] journal"])
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
    assert not set(schema.get("required", [])) & {
        "numbering_corroborated", "corroborating_readings", "labels_disputed",
        "reflist_model", "reflist_fields_discarded",
    }

    back = RefManifest.from_json(path)
    assert back.numbering_corroborated is True
    assert back.corroborating_readings == ["parsed", "pymupdf"]
    assert back.labels_disputed == ["6", "7"]
    assert back.reflist_model == "claude-opus-5"
    assert back.reflist_fields_discarded == ["[6] journal"]

    # a manifest written before these fields must still load, and must not read
    # as "nothing disputed" — absent means never computed
    for key in ("numbering_corroborated", "corroborating_readings", "labels_disputed",
                "reflist_model", "reflist_fields_discarded"):
        del payload[key]
    path.write_text(json.dumps(payload))
    old = RefManifest.from_json(path)
    assert old.numbering_corroborated is False
    assert old.labels_disputed == []


def test_reflist_numbering_findings_round_trips_and_validates(tmp_path):
    """Gate 2, for the one field this task actually adds.

    Kept apart from `reflist_fields_discarded` on purpose — see
    `ReflistProvenance.numbering_findings`'s own docstring for why a duplicated
    or missing numeral is a different kind of finding from a dropped value.
    """
    import jsonschema

    m = RefManifest(manuscript="p.pdf",
                    reflist_numbering_findings=["numerals proposed twice: 2"])
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
    assert "reflist_numbering_findings" not in schema.get("required", [])

    back = RefManifest.from_json(path)
    assert back.reflist_numbering_findings == ["numerals proposed twice: 2"]

    del payload["reflist_numbering_findings"]
    path.write_text(json.dumps(payload))
    old = RefManifest.from_json(path)
    assert old.reflist_numbering_findings == []


def test_a_numbering_finding_from_the_model_reading_reaches_the_manifest(tmp_path, monkeypatch):
    """`reflist.propose` reports a duplicated or missing numeral separately from
    a discarded field, because the two mean different things. An earlier draft
    of this step read only `fields_discarded`, so every duplicate and gap the
    model reading found was silently dropped before any reader saw it."""
    proposed = (
        [_work("2", "10.1000/x2"), _work("2", "10.1000/x22")],
        reflist_mod.ReflistProvenance(
            model="claude-opus-5",
            entries_proposed=2,
            numbering_findings=["numerals proposed twice: 2"],
            readings=["pymupdf", "docling"],
        ),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: proposed)

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert manifest.reflist_numbering_findings == ["numerals proposed twice: 2"]
    assert not any(
        "twice" in f for f in manifest.reflist_fields_discarded
    ), manifest.reflist_fields_discarded
