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


def _paper(path: Path, *, extra_uncited: bool = False) -> Path:
    """A one-page PDF citing [1] and [2], with both references printed.

    `extra_uncited=True` also prints a THIRD, uncited [3] — used by exactly one
    test (`test_no_model_reply_can_set_numbering_verified`) where the parse
    alone must fail `reconcile`'s own extent check (`_covers`), because on the
    plain two-reference fixture that check is satisfied by the parse alone and
    nothing about the model reading could be shown to matter (see that test's
    docstring). One fixture with a flag, not two independent copies that would
    otherwise be free to drift apart.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Fixture Imaging Study", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and also [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. A first paper. J Fixture. 2020;1:1-9.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. A second paper. J Fixture. 2021;2:10-19.", fontsize=11)
    if extra_uncited:
        page.insert_text((72, 270), "[3] Gamma G. A third, uncited paper. J Fixture. 2019;3:1-5.",
                         fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _work(num: str, doi: str) -> RefEntry:
    """Built through `_entry`, the production parse path — not by setting
    fields by hand, which would bypass the code every real candidate goes
    through (mirrors `tests/test_label_agreement.py`'s own `_work`)."""
    return _entry(num, f"Author for entry {num}. A distinctive title. doi:{doi}")


def _wire_offline(monkeypatch, *, needs_flat: bool = True) -> None:
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

    `needs_flat` patches `cli._needs_flat_reading` rather than the smap's own
    converter, because these tests all ingest with `backend="pymupdf"` for
    speed (no docling download) and `_refs_pipeline` now decides `enabled` for
    the model call as `llm_refs and not parse_only and _needs_flat_reading(smap)`
    (Major 2 of the Task 6 review) — so on the real pymupdf backend the model
    call would never even be attempted, and every test below that mocks
    `reflist.propose` directly would be testing a branch that never runs. This
    is the same technique the review used to reproduce the real,
    docling-shaped path without docling: force the PREDICATE, not the backend.
    Default `True` because that is what most of these tests need; the one test
    for the real pymupdf skip (`test_a_pymupdf_backend_run_never_asks_the_model`)
    passes `needs_flat=False` explicitly, which happens to equal what the real
    function already returns for this fixture's backend — set explicitly for
    the reader's benefit, not because the patch changes anything there.
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
    monkeypatch.setattr(cli, "_needs_flat_reading", lambda smap: needs_flat)
    monkeypatch.setenv("PAPERTRACE_EMAIL", "test@example.org")


@pytest.fixture()
def offline(monkeypatch):
    _wire_offline(monkeypatch)


def _run_refs(tmp_path: Path, monkeypatch, *, needs_flat: bool = True, **kw) -> RefManifest:
    """Wire the offline doubles, run `_refs_pipeline` on the fixture paper, and
    hand back the manifest it wrote — schema-validated on the way, so every
    caller of this helper gets Gate 2 for free rather than each writing its own
    `jsonschema.validate`.

    A plain function, not a fixture: Step 4's test needs per-call keyword
    overrides (`llm_refs=True` here, forwarded straight to `_refs_pipeline`),
    which a fixture cannot take.
    """
    import jsonschema

    _wire_offline(monkeypatch, needs_flat=needs_flat)
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

    The model's reading is a voter: it can withhold a verdict, and — via
    `stamp_seen_in` — record which readings also carried a chosen entry's
    work. It cannot put a paper into the manifest, cannot change a numeral,
    and cannot cause anything to be resolved or downloaded. So a run whose
    model reading names two completely different works must produce
    byte-identical entries to a run that never asked a model at all.

    That equality holds only because the fabricated reading DISAGREES (Minor 4
    of the Task 6 review). `stamp_seen_in` is precisely a model-driven write
    into the manifest's entries (`RefEntry.seen_in`): an AGREEING model
    reading legitimately changes `seen_in` and the two runs would NOT be
    byte-identical — see `test_no_model_reply_can_set_numbering_verified` for
    that case. Do not "fix" `stamp_seen_in` to restore byte-identical equality
    under agreement; that was never the invariant, only the disagreeing case
    is.

    The equality alone also cannot see the other half of Ruling 2 (Major 3):
    `resolve_all`'s return value is discarded at the call site (it mutates in
    place), so a violation shaped `resolve_all(entries + others["llm"], ...)`
    would download and judge the fabricated papers while `entries` — and so
    this equality — stayed untouched. The capturing stub below closes that
    gap by recording exactly what reached `resolve_all`.

    `backend="pymupdf"`, not `"docling"`: the brief's own draft of this test used
    `"docling"` on a real PDF with no mock in front of `ingest_pdf`, which would
    have triggered a real docling conversion — a ~500 MB model download on first
    use, per `CLAUDE.md` — on every run of an "offline" test. `reflist.propose`
    is fully mocked below and ignores its arguments, so which backend produced
    the parse is irrelevant to what this test actually checks.
    """
    import papertrace.refs as refs_mod

    pdf = _paper(tmp_path / "paper.pdf")
    fabricated_titles = {"An entirely different paper", "Another different paper"}
    fabricated = (
        [RefEntry(num="1", raw="Zeta Z. An entirely different paper. 1999.",
                  title="An entirely different paper", year="1999", seen_in=["docling"]),
         RefEntry(num="2", raw="Eta E. Another different paper. 1998.",
                  title="Another different paper", year="1998", seen_in=["pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2,
                                      readings=["docling", "pymupdf"], attempted=True),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: fabricated)

    # what actually reaches retrieval — the half of Ruling 2 the
    # entries-equality assertion below cannot see on its own (Major 3)
    captured: list[list[RefEntry]] = []

    def _capturing_resolve_all(entries, dest, email, provided_dir=None, progress=None, taken=None):
        captured.append(list(entries))
        return entries

    monkeypatch.setattr(refs_mod, "resolve_all", _capturing_resolve_all)

    with_llm, without = tmp_path / "with", tmp_path / "without"
    cli._refs_pipeline(manuscript=pdf, case=with_llm, provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=True)
    cli._refs_pipeline(manuscript=pdf, case=without, provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=False)

    assert _entries(with_llm) == _entries(without)
    assert "different paper" not in _entries(with_llm)

    assert len(captured) == 2, "resolve_all should have run once per _refs_pipeline call"
    for handed in captured:
        handed_titles = {e.title for e in handed if e.title}
        assert not (handed_titles & fabricated_titles), (
            f"a fabricated entry reached resolve_all: {handed}"
        )


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


def test_needs_flat_reading_does_not_crash_on_an_empty_converter():
    """Minor 1 of the Task 6 review. `"".split()` is `[]`, not `[""]` —
    `smap.converter.split()[0]` raises `IndexError` on a `source_map.json`
    that never recorded a converter, killing the whole refs stage on
    something the rest of this module treats as a routine "not known to be
    pymupdf" case rather than a crash."""
    assert cli._needs_flat_reading(_smap("")) is True


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


def test_a_pymupdf_backend_run_never_asks_the_model(tmp_path, monkeypatch):
    """Major 2 of the Task 6 review: `--backend pymupdf` — what every other
    test and all of CI pin — must never even reach `reflist.propose`, and the
    published reason must name the deliberate skip rather than blame the PDF.

    `reflist.propose` is deliberately left UNMOCKED here: the point is that it
    is never called at all, not that it is called and handled well. Before the
    fix, `enabled` did not consider the backend, so `propose` ran, was handed
    one empty text (the flat reading, skipped on purpose) and one real one,
    and reported "only one of the two extractions of the bibliography had any
    text" — true of nothing: both extractions had text, the second was simply
    never taken.
    """
    import papertrace.ask as ask_mod

    calls = []
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: calls.append(1) or "[]")
    manifest = _run_refs(tmp_path, monkeypatch, needs_flat=False, llm_refs=True)

    assert calls == [], "propose must never be reached on a pymupdf-backend run"
    assert manifest.reflist_attempted is False
    assert manifest.reflist_model == ""
    assert any(
        "this run's backend is pymupdf" in f for f in manifest.reflist_fields_discarded
    ), manifest.reflist_fields_discarded
    assert not any(
        "only one of the two extractions" in f for f in manifest.reflist_fields_discarded
    ), "the published reason must name the deliberate skip, not blame the PDF's extraction"


def test_an_unavailable_claude_degrades_to_a_stated_absence(tmp_path, monkeypatch):
    """The cardinal rule, at this seam. Never "it agreed" — "it was not asked".

    `claude` missing from PATH is the ordinary case for someone who installed
    papertrace and not Claude Code. The run proceeds on the deterministic
    readings, the manifest records the reason, and no format anywhere says a
    model corroborated anything.

    `needs_flat=False` (not the shared fixture's default): with the flat
    reading also forced on, "parsed" and the real flat-text reading of this
    same pymupdf-backend PDF are identical and genuinely corroborate each
    other with no model involved at all, which would make
    `numbering_corroborated` legitimately `True` for a reason this test is not
    about. Keeping the fixture single-voter here is what isolates "claude was
    unavailable" as the one fact under test.
    """
    import papertrace.ask as ask_mod

    _wire_offline(monkeypatch, needs_flat=False)
    monkeypatch.setattr(ask_mod, "claude_available", lambda: False)
    monkeypatch.setattr(ask_mod, "_ask",
                        lambda prompt, model=None: pytest.fail("claude was absent and asked anyway"))
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["reflist_model"] == ""
    assert payload["reflist_attempted"] is False
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
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2, attempted=True),
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

    Not `_paper()`'s default shape: with only the two cited references printed,
    `_covers` — the extent check `reconcile` uses on its own, with no model in
    the loop at all — is already satisfied by the parse alone, and `verified`
    would come back `True` regardless of anything this test does. Nothing here
    could then tell "verified because the parse already covered the body's
    labels" apart from "verified because a mocked model agreed with it".
    `extra_uncited=True` prints a THIRD, uncited reference, so the parse alone
    has three entries against two cited labels and fails `_covers` on its own —
    the only way to make the two cases distinguishable.

    `corroborating_readings` names three, not two: `offline`'s default
    `needs_flat=True` means the real, local flat-text reading of this same PDF
    also runs and also carries labels [1] and [2] (identical text to the
    parse, since the real backend here is pymupdf too) — so it votes exactly
    where "parsed" does, and Major 1's fix (name only readings that actually
    carried a cited label) correctly counts it alongside "llm" and "parsed".
    """
    agrees = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020", seen_in=["docling", "pymupdf"]),
         RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                  title="A second paper", year="2021", seen_in=["docling", "pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2, attempted=True),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: agrees)
    pdf = _paper(tmp_path / "paper.pdf", extra_uncited=True)
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["numbering_verified"] is False
    assert payload["numbering_corroborated"] is True
    assert payload["corroborating_readings"] == ["llm", "parsed", "pymupdf"]


def test_a_successful_reading_that_named_no_model_still_discloses_the_attempt(tmp_path, monkeypatch):
    """Critical 1 of the Task 6 review, reproduced end to end through the real
    pipeline rather than only at the disclosure layer.

    `ask._ask` records a model name only when `claude -p`'s own JSON reports
    one — a case `ask.py` explicitly anticipates ("a reply that names no
    model is not evidence the model changed"). `reflist.propose` mirrors that
    exactly: a real call that verifies cleanly and votes can still leave
    `prov.model == ""`. Before `reflist_attempted` existed, that state
    published as `reflist_model: ""` (the schema's OWN words for "no such
    call was made") with no console line and no `reflist` disclosure, while
    `numbering_corroboration` told the reader `llm` had corroborated the
    numbering anyway.
    """
    unnamed = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020"),
         RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                  title="A second paper", year="2021")],
        # the exact shape a real `propose()` call produces when
        # `ask.model_for(ask.SITE_REFS)` returns `None` — `model=""`,
        # `attempted=True`, a real vote in hand
        reflist_mod.ReflistProvenance(model="", entries_proposed=2, attempted=True),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: unnamed)

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert manifest.reflist_attempted is True, "a call that voted must be recorded as attempted"
    assert manifest.reflist_model == ""

    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    fired = {d.key for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)}
    assert "reflist" in fired, "a reading that ran and voted must not be silently 'not asked for'"


def test_corroborating_readings_excludes_a_reading_that_carried_no_cited_label(
    tmp_path, monkeypatch, capsys
):
    """Major 1 of the Task 6 review. `others` is every reading TAKEN, not
    every reading that AGREED. A reading proposing entries only for labels the
    body never cites abstains everywhere in `label_agreement` and must not be
    named as having corroborated anything — `stamp_seen_in`'s own `_same_work`
    test already refuses to credit a reading this way for `seen_in`; this is
    the same fact applied to the field a reader is most likely to trust as
    reassurance. The console count must match the named list, not `len(others)`.
    """
    abstains = (
        [_work("3", "10.1000/x3"), _work("4", "10.1000/x4")],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2, attempted=True),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: abstains)
    capsys.readouterr()  # discard anything buffered before this run

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert "llm" not in manifest.corroborating_readings, manifest.corroborating_readings
    assert manifest.corroborating_readings == ["parsed", "pymupdf"]
    assert manifest.numbering_corroborated is True

    out = capsys.readouterr().out
    assert "3 readings of the reference list agree" not in out, (
        "the printed count must match the named readings, not len(others)"
    )
    assert "2 readings of the reference list agree" in out


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

    manifest = RefManifest(manuscript="p.pdf", reflist_attempted=True,
                           reflist_model="claude-opus-5",
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
    assert fired[0].level == "warn", "a failed attempt is a warning, not routine information"


def test_an_attempted_reading_that_named_no_model_is_worded_as_a_gap_in_reporting(tmp_path):
    """Critical 1, at the disclosure layer directly. The two failure modes read
    differently on purpose: "not obtained" means nothing happened; this one
    means something happened and the CLI could not say who answered — and the
    text must not blur the two into one "not obtained" sentence.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_attempted=True, reflist_model="",
                           reflist_fields_discarded=[])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    text = fired[0].text
    assert "not obtained" not in text
    assert "did not report which model answered" in text


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


def test_reflist_attempted_round_trips_and_validates(tmp_path):
    """Gate 2, for Critical 1's field. Absent means never computed / no call
    ever made — the same three-state discipline as `numbering_ledger` — never
    "the model agreed" and never folded into `reflist_model`'s own emptiness,
    which cannot by itself distinguish "no call" from "a call that named no
    model" (see `RefManifest.reflist_attempted`'s own docstring).
    """
    import jsonschema

    m = RefManifest(manuscript="p.pdf", reflist_attempted=True, reflist_model="")
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
    assert "reflist_attempted" not in schema.get("required", [])

    back = RefManifest.from_json(path)
    assert back.reflist_attempted is True
    assert back.reflist_model == ""

    del payload["reflist_attempted"]
    path.write_text(json.dumps(payload))
    old = RefManifest.from_json(path)
    assert old.reflist_attempted is False


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
            attempted=True,
        ),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: proposed)

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert manifest.reflist_numbering_findings == ["numerals proposed twice: 2"]
    assert not any(
        "twice" in f for f in manifest.reflist_fields_discarded
    ), manifest.reflist_fields_discarded
