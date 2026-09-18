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
                                      readings=["docling", "pymupdf"], outcome="read"),
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
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=False)

    assert calls == []

    # a plain disable is silent, not "asked for and never obtained" — caught
    # in this round's own verification (see the outcome/`_reflist` guard fix)
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RefManifest, RunResults

    manifest = RefManifest.from_json(case / "refs_manifest.json")
    assert manifest.reflist_outcome == "not_attempted"
    assert manifest.reflist_failure == ""
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert fired == [], "a run that never asked for a model reading must disclose nothing about it"


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
    assert manifest.reflist_outcome == "not_attempted"
    assert manifest.reflist_model == ""
    assert "this run's backend is pymupdf" in manifest.reflist_failure, manifest.reflist_failure
    assert "only one of the two extractions" not in manifest.reflist_failure, (
        "the published reason must name the deliberate skip, not blame the PDF's extraction"
    )


def test_an_unavailable_claude_degrades_to_a_stated_absence(tmp_path, monkeypatch):
    """The cardinal rule, at this seam. Never "it agreed" — "it was not asked".

    `claude` missing from PATH is the ordinary case for someone who installed
    papertrace and not Claude Code. The run proceeds on the deterministic
    readings, the manifest records the reason, and no format anywhere says a
    model corroborated anything.

    N2 of the Task 6 re-review: the round-1 version of this test drove
    `needs_flat=False`, which makes `enabled` (`llm_wanted and needs_flat`)
    `False` — so `_llm_reference_reading` returns at its FIRST guard and
    `ask.claude_available` is never consulted at all. All five of that
    version's assertions passed with `claude` PRESENT too, because the
    `"not attempted"` substring it matched was the backend-skip reason, not
    the missing-CLI one — the very thing this test claims to cover was
    untested. `needs_flat=True` here is what actually reaches the
    availability check, and the assertions read the CONTENT of the reason
    (`"claude is not on PATH"`) rather than a prefix both reasons used to
    share.
    """
    import papertrace.ask as ask_mod

    _wire_offline(monkeypatch, needs_flat=True)
    monkeypatch.setattr(ask_mod, "claude_available", lambda: False)
    monkeypatch.setattr(
        ask_mod, "_ask",
        lambda prompt, model=None: pytest.fail("claude was absent and asked anyway"),
    )
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["reflist_model"] == ""
    assert payload["reflist_outcome"] == "not_attempted"
    assert "claude is not on PATH" in payload["reflist_failure"], payload["reflist_failure"]
    assert "llm" not in payload["corroborating_readings"]
    # incidental to this test, not the fact under it (N2): with `needs_flat=True`
    # the real flat-text reading of this same pymupdf PDF genuinely corroborates
    # "parsed" with no model involved at all, so `numbering_corroborated` being
    # `True` here says nothing about claude's absence — `"llm" not in
    # corroborating_readings` above is the assertion that actually matters.

    from papertrace.disclosures import run_disclosures
    from papertrace.models import RefManifest, RunResults

    manifest = RefManifest.from_json(case / "refs_manifest.json")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "claude is not on PATH" in fired[0].text
    assert "agreed" not in fired[0].text
    assert fired[0].level == "warn"


def test_a_failed_model_call_does_not_kill_the_refs_stage(tmp_path, offline, monkeypatch):
    """A timeout at the bibliography must not cost the user the retrieval.

    `refs` has already parsed the list and is about to fetch the sources. A
    RuntimeError out of the seam is a failed corroboration attempt, not a failed
    refs stage — and the reason lands where a reader can see it.

    N1 of the Task 6 re-review, reproduced and now closed: before the fix, a
    timeout published as `reflist_model: ""`, `reflist_fields_discarded: ["not
    obtained — RuntimeError: ..."]`, `attempted: True` — and `_reflist` read
    that shape as a SUCCESSFUL reading, because its note did not start with
    "reading discarded" and `attempted` alone could not tell "called and
    failed" apart from "called and voted". The disclosure said "1 value it
    proposed was not found in the printed text" at `level=info`, for a call
    that proposed nothing at all. `reflist_outcome == "failed"` is the
    dedicated third state; `reflist_failure` (never `reflist_fields_discarded`)
    is where the reason lives, so nothing downstream can miscount it as a
    discarded value.
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
    assert payload["reflist_outcome"] == "failed"
    assert "timed out" in payload["reflist_failure"], payload["reflist_failure"]
    assert payload["reflist_fields_discarded"] == [], (
        "the failure reason must not be counted as a discarded value"
    )
    assert payload["reflist_model"] == ""

    from papertrace.disclosures import run_disclosures
    from papertrace.models import RefManifest, RunResults

    manifest = RefManifest.from_json(case / "refs_manifest.json")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "timed out" in fired[0].text
    assert "was not found in the printed text" not in fired[0].text, (
        "a call that never returned must not read as a value that failed verification"
    )
    assert fired[0].level == "warn", "a call that did not return is not an aside"


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
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2, outcome="read"),
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

    `corroborating_readings` names `parsed` and `pymupdf`: `offline`'s default
    `needs_flat=True` means the real, local flat-text reading of this same PDF
    also runs and also carries labels [1] and [2] (identical text to the
    parse, since the real backend here is pymupdf too) — so it votes exactly
    where "parsed" does.

    `llm` is NOT among them, and the model here is agreeing entry for entry:
    that is the whole-branch review's Major 1. `reflist.propose` may only copy
    from the two extractions, and both of those vote here in their own right,
    so the model's agreement is one text read twice. It can still dispute; it
    cannot corroborate. The count printed on the console — 2 — is therefore
    the number of readings that actually read the page.
    """
    agrees = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020", seen_in=["docling", "pymupdf"]),
         RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                  title="A second paper", year="2021", seen_in=["docling", "pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2, outcome="read"),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: agrees)
    pdf = _paper(tmp_path / "paper.pdf", extra_uncited=True)
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["numbering_verified"] is False
    assert payload["numbering_corroborated"] is True
    assert payload["corroborating_readings"] == ["parsed", "pymupdf"]


def test_a_successful_reading_that_named_no_model_still_discloses_the_attempt(
    tmp_path, monkeypatch
):
    """Critical 1 of the Task 6 review, reproduced end to end through the real
    pipeline rather than only at the disclosure layer.

    `ask._ask` records a model name only when `claude -p`'s own JSON reports
    one — a case `ask.py` explicitly anticipates ("a reply that names no
    model is not evidence the model changed"). `reflist.propose` mirrors that
    exactly: a real call that verifies cleanly and votes can still leave
    `prov.model == ""`. Before `reflist_outcome` existed, that state
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
        # `outcome="read"`, a real vote in hand
        reflist_mod.ReflistProvenance(model="", entries_proposed=2, outcome="read"),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: unnamed)

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert manifest.reflist_outcome == "read", "a call that voted must be recorded as read"
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
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2, outcome="read"),
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

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="read",
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


def test_a_reflist_model_with_no_recorded_outcome_still_settles_as_read(tmp_path):
    """N4 of the Task 6 re-review. A manifest that carries `reflist_model` but
    never had `reflist_outcome` computed — only reachable from a case folder
    written by an earlier commit of this same unmerged branch, since
    `reflist_model` predates `reflist_outcome` by one round and neither is on
    `main` — must not lose its required disclosure and ceiling sentence.

    `reflist_model` non-empty is POSITIVE PROOF a call happened and answered
    with a name; settling `outcome` as `"read"` from that is not the forbidden
    derivation. Only the inverse — "no name and no reason recorded, so no call
    happened" — would be, and that is exactly the shape that returns `None`
    below it.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="claude-opus-5")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "claude-opus-5" in fired[0].text
    assert "second reading, not confirmation" in fired[0].text


def test_neither_model_nor_outcome_nor_failure_discloses_nothing(tmp_path):
    """The one shape that must still return `None`: nothing was ever asked
    for and nothing was ever obtained. `reflist_model` empty is not positive
    proof of anything by itself — only its PRESENCE licenses an inference,
    never its absence."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert fired == []


def test_a_plain_disable_with_a_recorded_outcome_still_discloses_nothing(tmp_path):
    """Caught in verification, not in the review: once `reflist_outcome` is
    always a real value once computed, `not outcome` alone can no longer tell
    "nothing to report" apart from "asked for and never obtained" — both
    leave `outcome == "not_attempted"`. `--no-llm-refs` and `--parse-only`
    record that outcome too (`_llm_reference_reading`'s `not enabled` branch),
    with an empty `failure` — a plain `not outcome` guard read that as
    something to report and produced 'This run asked that the reference list
    was also read by a model, and no reading was obtained: . …', a broken
    sentence with an empty reason, for a run that never asked at all."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="not_attempted",
                           reflist_failure="")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert fired == []


def test_a_model_reading_that_was_not_obtained_still_discloses_that(tmp_path):
    """Silence would be read as "no news". The attempt and its failure are news.

    The token stays true in this branch because the sentence is phrased around
    it — the same discipline `SUPPLEMENT_IDENTITY_TOKEN` is written under.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(
        manuscript="p.pdf", reflist_outcome="not_attempted", reflist_model="",
        reflist_failure="claude is not on PATH, so no model read the reference list",
    )
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "no reading was obtained" in fired[0].text
    assert "claude is not on PATH" in fired[0].text
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

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="read", reflist_model="",
                           reflist_fields_discarded=[])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    text = fired[0].text
    assert "not obtained" not in text
    assert "did not report which model answered" in text
    assert fired[0].level == "info", "a reading that ran and voted is routine information"


def test_a_reply_proposing_no_entries_is_not_worded_as_a_clean_corroboration(tmp_path):
    """N3b of the Task 6 re-review. A reply of `[]` used to be described as
    "every value it proposed was found in the printed text" — vacuously true,
    and it reads as a clean second opinion. A model that proposed no entries
    at all corroborated nothing, and the sentence must say so, not describe an
    empty set as a value-by-value success.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="read",
                           reflist_model="claude-opus-5", reflist_entries_proposed=0)
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    text = fired[0].text
    assert "it proposed no entries" in text
    assert "every value it proposed was found" not in text


def test_a_reading_discarded_whole_is_not_worded_as_a_second_reading_on_the_console(
    tmp_path, monkeypatch, capsys
):
    """Caught in this round's own verification, walking the outcome table: a
    reply that was obtained but not a JSON array at all (or one whose title
    was never printed) sets `discarded_whole`, and the console used to print
    the SAME "was also read by X · N fields discarded as not printed" line a
    genuine success gets — true of nothing, since no field was individually
    checked and dropped; the whole reading was refused before any field-level
    check could run.
    """
    unusable = ([], reflist_mod.ReflistProvenance(
        model="claude-opus-5", outcome="read", discarded_whole="the model's reply "
        "was not a JSON array (no JSON array in model output: not json)",
        fields_discarded=["reading discarded — the model's reply was not a JSON array "
                          "(no JSON array in model output: not json)"],
    ))
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: unusable)
    capsys.readouterr()

    _run_refs(tmp_path, monkeypatch, llm_refs=True)

    out = capsys.readouterr().out
    assert "reading was" in out and "discarded" in out
    assert "0 fields discarded as not printed" not in out
    assert "· 1 field discarded as not printed" not in out


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


# --- NF1: corroborated (per-label) and corroborating_readings (per-reading) --
# can disagree; the disclosure and console must not claim "N readings agree"
# over a list that cannot back it up.


@pytest.mark.parametrize("readings", [[], ["pymupdf"]])
def test_a_corroborating_readings_list_under_two_is_not_asserted_as_agreement(readings):
    """`numbering_corroborated` is per-label (every cited label agreed by >= 2
    readings); `corroborating_readings` is per-reading (this reading agreed on
    ALL of them). The two are computed independently, so `numbering_corroborated`
    can be `True` while `corroborating_readings` has 0 or 1 names — reproduced
    directly here rather than trusting it cannot happen.
    """
    from papertrace.disclosures import (
        NUMBERING_CORROBORATION_TOKEN,
        NUMBERING_NO_SPANNING_READING_TOKEN,
        run_disclosures,
    )
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", numbering_verified=False,
                           numbering_corroborated=True, corroborating_readings=readings)
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "numbering_corroboration"]
    assert len(fired) == 1, fired
    d = fired[0]
    assert d.token == NUMBERING_NO_SPANNING_READING_TOKEN
    assert NUMBERING_CORROBORATION_TOKEN not in d.text, (
        "a list of fewer than two readings cannot back the token's own claim of 'two readings'"
    )
    assert "the readings taken" not in d.text, (
        "the never-computed fallback must not render for a legitimately computed empty list"
    )


def test_a_corroborating_readings_list_of_two_or_more_is_asserted_normally():
    """The ordinary case still works: two or more names really do back the claim."""
    from papertrace.disclosures import NUMBERING_CORROBORATION_TOKEN, run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", numbering_verified=False,
                           numbering_corroborated=True,
                           corroborating_readings=["parsed", "pymupdf"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "numbering_corroboration"]
    assert len(fired) == 1, fired
    assert fired[0].token == NUMBERING_CORROBORATION_TOKEN
    assert "parsed, pymupdf" in fired[0].text


def test_a_realistic_split_vote_prints_neither_a_bad_count_nor_a_false_token(
    tmp_path, monkeypatch, capsys
):
    """NF1 of the second Task 6 re-review, reproduced end to end — not merely
    constructed. `parsed` carries a `boundary_ambiguous` entry at one cited
    label (so it casts no vote there — the 0.7.0 numbering case); the
    `crossref` deposit skips a different cited label entirely. Every cited
    label still ends up `agreed` (by the readings that DO carry it), so
    `numbering_corroborated` is `True` — but no single reading voted on both,
    so `corroborating_readings` cannot name two. Before the fix this printed
    `✓ 1 readings … agree … (pymupdf)` — ungrammatical, and asserting an
    agreement the list does not contain.

    The third reading is the deposit and not the model's: since the
    whole-branch review's Major 1 a `DERIVED_READINGS` reading is never
    credited with an agreement, so an `llm` third voter would make [1]
    `single` for a reason this test is not about, and the split vote NF1 is
    about would stop being reproduced at all.
    """
    ambiguous_1 = RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                           title="A first paper", year="2020", boundary_ambiguous=True)
    llm_reading = (
        [RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                  title="A second paper", year="2021")],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=1,
                                      outcome="read"),
    )

    def _fake_propose(reading_a, reading_b, *, label_a, label_b, model=None):
        # `parsed`'s own entry [1] is boundary_ambiguous, forced after the
        # fact below since `propose` builds its own RefEntry objects
        return llm_reading

    monkeypatch.setattr(reflist_mod, "propose", _fake_propose)
    capsys.readouterr()

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)
    # force the realistic split: [1] is boundary_ambiguous in the PARSED
    # reading too, so `parsed` casts no vote on it (matching `label_agreement`'s
    # own invariant 5) while `pymupdf`'s flat reading — identical text — does
    for e in manifest.entries:
        if e.num == "1":
            e.boundary_ambiguous = True

    from papertrace.refs import corroborating_readings, label_agreement
    others = {
        "parsed": [ambiguous_1,
                   RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                            title="A second paper", year="2021")],
        "pymupdf": [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                             title="A first paper", year="2020"),
                    RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                             title="A second paper", year="2021")],
        "crossref": [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                              title="A first paper", year="2020")],
    }
    body = {"1", "2"}
    agreement = label_agreement(others, body)
    assert agreement == {"1": "agreed", "2": "agreed"}, agreement
    corroborated = all(agreement.get(label) == "agreed" for label in body)
    assert corroborated is True
    named = corroborating_readings(others, body)
    assert len(named) < 2, named  # nobody voted on both [1] and [2]


def test_the_console_never_prints_fewer_than_two_readings_agree(tmp_path, monkeypatch, capsys):
    """The console half of NF1. `rec.corroborated` (per-label, from
    `label_agreement`) and `rec.corroborating_readings` (per-reading, from
    `refs.corroborating_readings`) are computed independently — on this
    fixture's own two-reference paper the flat "pymupdf" reading is always
    identical to "parsed", which would make a naturally-arising split vote
    hard to reproduce through the full pipeline without a real converter
    difference. `refs.corroborating_readings` is monkeypatched to return a
    single name regardless of its arguments, which decouples the two exactly
    the way a realistic split vote does (see
    `test_a_realistic_split_vote_prints_neither_a_bad_count_nor_a_false_token`
    for that the split is independently reachable), and isolates the console
    print's own condition for this test.
    """
    import papertrace.refs as refs_mod

    monkeypatch.setattr(refs_mod, "corroborating_readings", lambda others, body: ["pymupdf"])
    capsys.readouterr()

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=False)

    assert manifest.numbering_corroborated is True  # the two identical readings still agree
    assert manifest.corroborating_readings == ["pymupdf"]
    out = " ".join(capsys.readouterr().out.split())
    assert "0 readings" not in out
    assert "1 readings" not in out
    assert "not printed as corroboration" in out


# --- NF2: `reflist_entries_proposed` is a three-state field; `None` (never ---
# recorded) must not be read as "0" (measured, proposed nothing).


def test_a_manifest_that_never_recorded_the_count_is_not_told_it_proposed_nothing():
    """NF2 of the second Task 6 re-review, reproduced. A round-1-shaped
    manifest — a named model, no discarded fields, no `reflist_entries_proposed`
    at all — must not read as "it proposed no entries at all", which is a
    MEASURED claim this manifest never made."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="read",
                           reflist_model="claude-opus-5")
    assert manifest.reflist_entries_proposed is None
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    text = fired[0].text
    assert "it proposed no entries at all" not in text
    assert "never recorded" in text


def test_the_existing_ceiling_test_still_shows_the_discarded_count_not_zero_entries(tmp_path):
    """The exact live fixture the re-review named
    (`test_the_reflist_disclosure_reaches_every_format_and_carries_the_ceiling`)
    would have been green over a false 'it proposed no entries at all' sentence
    that contradicted its own `short` ('— 2 discarded'). Re-asserted here as its
    own regression test, independent of that test's own assertions."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="claude-opus-5",
                           reflist_fields_discarded=["[2] journal", "[3] doi"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    text, short = fired[0].text, fired[0].short
    assert "it proposed no entries at all" not in text, "contradicts its own short below"
    assert "2 values" in text
    assert short.endswith("— 2 discarded")


# --- NF3: the console must not describe a reply of `[]` as a clean second --
# opinion, the same fix N3b already made in the written report.


def test_the_console_does_not_call_an_empty_reply_a_clean_second_reading(
    tmp_path, monkeypatch, capsys
):
    """NF3: the exact mirror of self-found bug 2, in the other direction. The
    report already says "it proposed no entries at all" for a reply of `[]`;
    the console used to still print "0 fields discarded as not printed — a
    second reading, not confirmation", which reads as a clean corroboration
    from a model that proposed nothing to corroborate anything with.
    """
    empty_reply = (
        [],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=0, outcome="read"),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: empty_reply)
    capsys.readouterr()

    _run_refs(tmp_path, monkeypatch, llm_refs=True)

    # Rich wraps long console lines at terminal width, which can split this
    # exact phrase across a newline (and leave the join with a double space)
    # — collapse all whitespace runs before matching
    out = " ".join(capsys.readouterr().out.split())
    assert "it proposed no entries at all" in out
    assert "0 fields discarded as not printed" not in out


# --- NF5: the N4 normalization must also read `reflist_fields_discarded`, ---
# not `reflist_model` alone — a real, unnamed reading must not go silent.


def test_discarded_fields_alone_are_positive_proof_of_a_reading_on_the_load_path():
    """NF5: round 1's guard read `notes`; round 2's normalization read only
    `model` and `failure`, so a loaded manifest recording a real, UNNAMED
    reading that dropped a field lost its required disclosure entirely —
    exactly the silence `_reflist`'s own docstring says must never happen."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="",
                           reflist_fields_discarded=["[2] journal"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "2] journal" in fired[0].short or "[2] journal" in "".join(fired[0].rows)


# --- Self-found (round 3): a round-1-shaped failure note, loaded from disk,
# must not be counted as positive proof of a successful reading. Before
# `reflist_failure` existed, `_llm_reference_reading` stored a failed call as
# `"not obtained — <Exc>: …"` and an absent `claude` as `"not attempted — …"`
# INSIDE `reflist_fields_discarded` — the exact field NF5's fix (above) just
# taught to count as positive proof of "read". Found walking the
# written-by-an-earlier-commit axis of this round's own state table, not
# named in the review.


def test_a_round1_shaped_timeout_note_loaded_from_disk_is_not_read_as_success():
    """The N1 bug, reachable again on the LOAD path by NF5's own fix: a
    manifest written by round 1's code recorded the timeout as a "discarded"
    note because `reflist_failure` did not exist yet. Settling `outcome` as
    "read" from ANY non-empty `notes` — NF5's literal fix — would read that
    old note as "1 value it proposed was not found in the printed text and
    was discarded", the exact false statement N1 was raised to close.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(
        manuscript="p.pdf", reflist_model="",
        reflist_fields_discarded=["not obtained — RuntimeError: claude -p timed out after 600s"],
    )
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "was not found in the printed text" not in fired[0].text
    assert "timed out" in fired[0].text
    assert fired[0].level == "warn"


def test_a_round1_shaped_claude_absent_note_loaded_from_disk_settles_not_attempted():
    """The other round-1 prefix (`"not attempted — …"`, `claude` unavailable),
    loaded the same way."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(
        manuscript="p.pdf", reflist_model="",
        reflist_fields_discarded=["not attempted — claude is not on PATH, so no model read the list"],
    )
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "was not found in the printed text" not in fired[0].text
    assert "claude is not on PATH" in fired[0].text
    assert fired[0].level == "warn"


# --- NF6: an outcome outside REFLIST_OUTCOMES must fail closed. ------------


def test_an_unrecognised_outcome_fails_closed_rather_than_reading_as_success():
    """NF6: a hand edit, or a manifest from a future version with a fourth
    outcome, must not fall through to the branch that asserts a reading was
    taken and used — that is the one claim an unrecognised state can least
    afford to make."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="attempted",
                           reflist_model="claude-opus-5")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert fired[0].level == "warn"
    assert "every value it proposed was found in the printed text" not in fired[0].text
    assert "does not recognise" in fired[0].text
    assert "unrecognised outcome" in fired[0].short


# --- NF7: no prefix stutter between the console and the report. ------------


def test_the_report_strips_the_same_prefix_the_console_does():
    """NF7: round 2 added `removeprefix("reading discarded — ")` at the
    console and left the report reading the raw note, producing "…was
    discarded rather than used: reading discarded — the model's reply…" —
    the same fact said twice in one sentence."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(
        manuscript="p.pdf", reflist_outcome="read", reflist_model="",
        reflist_fields_discarded=[
            "reading discarded — the model's reply was not a JSON array "
            "(no JSON array in model output: not json at all)"
        ],
    )
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "reading discarded — reading discarded" not in fired[0].text
    assert fired[0].text.count("reading discarded") <= 1


# --- NF9: numbering findings only belong to a state where a reply exists. --


def test_numbering_findings_are_not_appended_to_a_call_that_never_returned():
    """NF9: `if numbering:` used to append unconditionally, so a hand-built
    or forward-version manifest combining `outcome="failed"` with a
    numbering finding read as "the call did not return … Its own numbering
    did not add up either", describing a reply that was never obtained."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="failed",
                           reflist_failure="RuntimeError: claude -p timed out after 600s",
                           reflist_numbering_findings=["numerals proposed twice: 2"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "Its own numbering did not add up" not in fired[0].text


# --- NF4: propose's own len(readings) < 2 guard names itself, not "the model"


def test_propose_own_guard_does_not_blame_a_model_that_was_never_asked():
    """NF4: the reason text, written when this path meant "a reply was
    checked and refused", used to read "…so nothing THE MODEL PROPOSED could
    have been checked" in a branch where no model was ever asked at all."""
    entries, prov = reflist_mod.propose(
        "some real text with content", "   ", label_a="docling 2.8.0", label_b="pymupdf"
    )
    assert entries == []
    assert prov.outcome == "not_attempted"
    assert "the model proposed" not in prov.failure
    assert "not attempted" in prov.failure


def test_the_reflist_outcome_schema_names_the_propose_guard_as_a_cause():
    """NF4: `not_attempted`'s published prose must enumerate every route to
    it, including `propose`'s own `len(readings) < 2` guard — distinct from
    the backend-skip cause (row 3), since here `enabled` was `True` and
    `claude` WAS available; the model simply had nothing to check itself
    against."""
    schema = json.loads(SCHEMA_PATH.read_text())
    desc = schema["properties"]["reflist_outcome"]["description"]
    assert "no text to check the model's own reading against" in desc


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
    # `None` — never computed — which is what "absent means never computed"
    # requires a field to be able to say, and a plain boolean could not
    assert old.numbering_corroborated is None
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


def test_reflist_outcome_and_failure_round_trip_and_validate(tmp_path):
    """Gate 2, for the fields that replaced `reflist_attempted` in this same
    unmerged task (N1 of the Task 6 re-review) — replacing rather than adding
    is correct because `reflist_attempted` was never a published contract.
    Absent `reflist_outcome` means NEVER COMPUTED, not `"not_attempted"` — the
    same three-state discipline as `numbering_ledger` — and never folded into
    `reflist_model`'s own emptiness, which cannot by itself distinguish "no
    call" from "a call that named no model" (see `RefManifest.reflist_outcome`'s
    own docstring).
    """
    import jsonschema

    m = RefManifest(manuscript="p.pdf", reflist_outcome="read", reflist_model="",
                    reflist_failure="")
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
    assert "reflist_outcome" not in schema.get("required", [])
    assert "reflist_failure" not in schema.get("required", [])

    back = RefManifest.from_json(path)
    assert back.reflist_outcome == "read"
    assert back.reflist_model == ""
    assert back.reflist_failure == ""

    for key in ("reflist_outcome", "reflist_failure"):
        del payload[key]
    path.write_text(json.dumps(payload))
    old = RefManifest.from_json(path)
    assert old.reflist_outcome == ""  # never computed — NOT "not_attempted"
    assert old.reflist_failure == ""


def test_reflist_failure_round_trips_for_the_failed_outcome(tmp_path):
    """The other real value `reflist_outcome` takes, with a reason attached."""
    import jsonschema

    m = RefManifest(manuscript="p.pdf", reflist_outcome="failed",
                    reflist_failure="RuntimeError: claude -p timed out after 600s")
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)

    back = RefManifest.from_json(path)
    assert back.reflist_outcome == "failed"
    assert "timed out" in back.reflist_failure


def test_reflist_entries_proposed_round_trips_and_validates(tmp_path):
    """Gate 2, for the field N3b needed: without it, "0 fields discarded"
    cannot be told apart from a reply that proposed nothing to discard in the
    first place.

    `None`, never `0`, is the absent/never-recorded default (NF2 of the
    second Task 6 re-review): `0` is a MEASURED value — a reply that really
    did propose zero entries — and conflating the two is exactly what let a
    round-1 manifest that recorded discarded fields but never this count
    read as "it proposed no entries at all".
    """
    import jsonschema

    m = RefManifest(manuscript="p.pdf", reflist_outcome="read",
                    reflist_entries_proposed=3)
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(payload, schema)
    assert "reflist_entries_proposed" not in schema.get("required", [])

    back = RefManifest.from_json(path)
    assert back.reflist_entries_proposed == 3

    del payload["reflist_entries_proposed"]
    path.write_text(json.dumps(payload))
    old = RefManifest.from_json(path)
    assert old.reflist_entries_proposed is None


def test_reflist_outcomes_matches_its_own_schema_enum():
    """The published vocabulary (`REFLIST_OUTCOMES`, the way `LABEL_AGREEMENT`
    and `REF_STATUSES` are) against the schema's enum for `reflist_outcome` —
    modelled on `test_ref_statuses_matches_its_own_schema_enum`'s idiom. `""`
    is the schema's own "never computed" sentinel and is not part of the
    published vocabulary of REAL outcomes, so it is added on both sides
    rather than smuggled into `REFLIST_OUTCOMES` itself.
    """
    from papertrace.reflist import REFLIST_OUTCOMES

    schema = json.loads(SCHEMA_PATH.read_text())
    enum = schema["properties"]["reflist_outcome"]["enum"]
    assert set(REFLIST_OUTCOMES) | {""} == set(enum)


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
            outcome="read",
        ),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: proposed)

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert manifest.reflist_numbering_findings == ["numerals proposed twice: 2"]
    assert not any(
        "twice" in f for f in manifest.reflist_fields_discarded
    ), manifest.reflist_fields_discarded


# --- whole-branch review, Major 4: the SECOND model call has provenance too


def _resolved_manifest(**kw) -> RefManifest:
    """A manifest from a run that escalated: a disputed label was settled by
    the resolution call, which is a different call from the reflist reading."""
    base = dict(
        manuscript="p.pdf",
        labels_resolved=["2"],
        numbering_choice="llm_resolved",
        numbering_chosen_by="user",
        resolution_outcome="read",
        resolution_model="claude-opus-5",
        resolution_readings=["parsed"],
    )
    base.update(kw)
    return RefManifest(**base)


def test_a_run_whose_only_model_call_was_the_resolution_does_not_deny_it():
    """Major 4. On `--backend pymupdf` the reflist reading is deliberately
    skipped, so the `reflist` disclosure said "This run asked that the
    reference list was also read by a model, and no reading was obtained" —
    directly beside a numbering resolution saying a model settled two labels
    and the user accepted it. A model call was made, paid for, verified and
    allowed to change which papers are judged."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = _resolved_manifest(
        reflist_outcome="not_attempted",
        reflist_failure="this run's backend is pymupdf, so a second flat reading of the "
                        "bibliography would be the same text",
    )
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]

    assert len(fired) == 1, fired
    assert "claude-opus-5" in fired[0].text
    assert "settle the labels" in fired[0].text


def test_a_silent_disable_still_discloses_a_resolution_call_that_happened():
    """`--no-llm-refs` records `not_attempted` with no reason and the
    disclosure stays silent — correctly, for the reading. But the escalation
    is a separate, user-consented call that runs anyway, and silence about it
    is the same false negative one rung down."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = _resolved_manifest(reflist_outcome="not_attempted", reflist_failure="")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]

    assert len(fired) == 1, fired
    assert "claude-opus-5" in fired[0].text
    assert "the parsed extraction" in fired[0].text


def test_a_resolution_call_that_failed_is_disclosed_though_it_settled_nothing():
    """`_numbering_resolution` needs a resolved label to fire, so a
    resolution call that raised would otherwise reach no structured surface
    at all — the manifest would record a run in which no model was asked."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(
        manuscript="p.pdf",
        labels_disputed=["2"],
        numbering_choice="withheld",
        resolution_outcome="failed",
        resolution_failure="RuntimeError: claude -p timed out after 600s",
        resolution_readings=["parsed", "pymupdf"],
    )
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]

    assert len(fired) == 1, fired
    assert "did not return" in fired[0].text
    assert "timed out" in fired[0].text


def test_a_manifest_with_no_resolution_call_gains_no_sentence_about_one():
    """`""` is never computed, and an absent call says nothing."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="claude-opus-5")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]

    assert len(fired) == 1, fired
    assert "settle the labels" not in fired[0].text


def test_the_numbering_resolution_names_the_model_and_what_it_was_shown():
    """Critical 2's report half: "a model reading of both texts" was printed
    on runs where the model saw one text, and on runs where the reading it
    ruled against had no text at all."""
    from papertrace.disclosures import _numbering_resolution

    d = _numbering_resolution(_resolved_manifest())

    assert d is not None
    assert "both texts" not in d.text
    assert "claude-opus-5" in d.text
    assert "the parsed extraction" in d.text


def test_the_resolved_claim_disclosure_does_not_claim_the_model_saw_both():
    """The per-claim counterpart of the sentence above."""
    from papertrace.disclosures import _claim_pairing
    from papertrace.models import ClaimResult

    claim = ClaimResult(id=1, claim="x", location="Results", refs=["2"])
    d = _claim_pairing(claim, _resolved_manifest())

    assert d is not None
    assert "shown both" not in d.text
    assert "the parsed extraction" in d.text


def test_a_resolution_whose_manifest_recorded_no_readings_says_so():
    """`[]` is never recorded, not "it was shown nothing" — and the sentence
    must not silently read as the latter, nor assert the fuller account of
    what this build's call is shown."""
    from papertrace.disclosures import _numbering_resolution

    d = _numbering_resolution(_resolved_manifest(resolution_readings=[]))

    assert d is not None
    assert "does not record which extractions" in d.text
    assert "what each reading said" not in d.text


# --- whole-branch review, minors 1 and 6


def test_numbering_corroborated_keeps_never_computed_apart_from_measured_false(tmp_path):
    """Minor 1. The dataclass and the schema both documented three states in
    a `bool` that can hold two, so "absent means never computed" was
    unexpressible. `None` is what the branch's own standard
    (`reflist_entries_proposed`) already uses for exactly this."""
    import jsonschema

    old = {"manuscript": "m.pdf", "entries": []}
    p = tmp_path / "old.json"
    p.write_text(json.dumps(old))
    assert RefManifest.from_json(p).numbering_corroborated is None

    measured = tmp_path / "new.json"
    RefManifest(manuscript="m.pdf", numbering_corroborated=False).to_json(measured)
    assert RefManifest.from_json(measured).numbering_corroborated is False

    schema = json.loads(SCHEMA_PATH.read_text())
    for path in (measured,):
        jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))
    RefManifest(manuscript="m.pdf", numbering_corroborated=None).to_json(measured)
    jsonschema.Draft202012Validator(schema).validate(json.loads(measured.read_text()))


def test_a_mid_branch_manifest_recording_only_that_a_reading_was_attempted(tmp_path):
    """Minor 6. A case folder written between `b74ba01` and `67ab473` carries
    `reflist_attempted: true` and none of the fields that replaced it. The
    key is dropped at load, so `_reflist` fell through every normalisation
    branch and returned `None` — the required disclosure vanished for a
    reading that actually happened, the exact false negative `67ab473` was
    written to fix.

    Neither `read` nor `failed` is provable from that flag, so neither is
    claimed: the outcome is recorded as never computed and the report says
    what it knows."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    p = tmp_path / "old.json"
    p.write_text(json.dumps({"manuscript": "m.pdf", "entries": [], "reflist_attempted": True}))
    manifest = RefManifest.from_json(p)

    fired = [d for d in run_disclosures(RunResults(manuscript="m.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "does not record what came of it" in fired[0].text


def test_a_mid_branch_manifest_with_a_model_name_still_settles_as_read(tmp_path):
    """The stronger inference still wins: a recorded model name is positive
    proof a reply was obtained, so the legacy flag adds nothing and must not
    downgrade it."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "manuscript": "m.pdf", "entries": [],
        "reflist_attempted": True, "reflist_model": "claude-opus-5",
    }))
    manifest = RefManifest.from_json(p)

    fired = [d for d in run_disclosures(RunResults(manuscript="m.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "claude-opus-5" in fired[0].text
    assert "does not record what came of it" not in fired[0].text


def test_a_run_with_a_resolution_never_says_the_numbering_rests_on_nothing_else():
    """Found by enumerating the states rather than the branches: the
    not-obtained and failed heads end "the reference numbering rests on the
    readings above it and nothing else", which the appended resolution
    sentence then contradicts in the same paragraph — a resolution
    substitutes an entry, so it is precisely something else the numbering
    rests on."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    for reflist_state in (
        dict(reflist_outcome="not_attempted", reflist_failure="backend is pymupdf"),
        dict(reflist_outcome="failed", reflist_failure="RuntimeError: timed out"),
        dict(reflist_outcome="wat"),
    ):
        manifest = _resolved_manifest(**reflist_state)
        fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
                 if d.key == "reflist"]
        assert len(fired) == 1, (reflist_state, fired)
        assert "nothing else" not in fired[0].text, reflist_state
        assert "claude-opus-5" in fired[0].text, reflist_state


def test_a_run_with_no_resolution_keeps_the_nothing_else_claim():
    """The other side: without a resolution the claim is true and load-bearing
    — it is what tells a reader the numbering rests on the deterministic
    readings alone."""
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_outcome="failed",
                           reflist_failure="RuntimeError: timed out")
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "nothing else" in fired[0].text


def test_a_resolution_from_an_earlier_commit_claims_only_what_it_recorded():
    """The load axis, which is where four of this branch's defects lived: a
    manifest written by an earlier commit of this same branch resolved a
    label and recorded none of the `resolution_*` fields — because that call
    was ALSO not shown the reading it ruled against. So the report may not
    assert, for that manifest, either which extractions the model saw or that
    it was shown what each reading said; both are true only of a run this
    build made."""
    from papertrace.disclosures import _claim_pairing, _numbering_resolution
    from papertrace.models import ClaimResult

    old = RefManifest(manuscript="p.pdf", labels_resolved=["2"],
                      numbering_choice="llm_resolved", numbering_chosen_by="user")

    run = _numbering_resolution(old)
    assert run is not None
    assert "what each reading said" not in run.text
    assert "does not record" in run.text

    claim = _claim_pairing(ClaimResult(id=1, claim="x", location="R", refs=["2"]), old)
    assert claim is not None
    assert "what each reading said" not in claim.text
    assert "does not record" in claim.text


# --- fix round 2: labels_uncomparable, a documented subset of labels_disputed


def test_labels_uncomparable_round_trips_and_validates(tmp_path):
    """Gate 2: additive, not required, and three-valued. `None` is never
    computed and `[]` is "computed, every dispute was a contradiction" —
    a single empty list could not say both, which is `table_warnings`'
    rule and the one this branch has now broken four times."""
    import jsonschema

    m = RefManifest(manuscript="p.pdf", labels_disputed=["6", "7"],
                    labels_uncomparable=["7"])
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))
    assert "labels_uncomparable" not in schema.get("required", [])

    assert RefManifest.from_json(path).labels_uncomparable == ["7"]

    payload = json.loads(path.read_text())
    del payload["labels_uncomparable"]
    path.write_text(json.dumps(payload))
    assert RefManifest.from_json(path).labels_uncomparable is None

    m2 = RefManifest(manuscript="p.pdf", labels_disputed=["6"], labels_uncomparable=[])
    m2.to_json(path)
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))
    assert RefManifest.from_json(path).labels_uncomparable == []


def test_the_run_level_disclosure_says_which_cause_applies():
    """The disjunction was honest but weak, and the two causes warrant
    different reader actions."""
    from papertrace.disclosures import _labels_disputed

    contradiction = _labels_disputed(
        RefManifest(manuscript="p", labels_disputed=["6"], labels_uncomparable=[])
    )
    assert "named different papers" in contradiction.text
    assert "could be compared" not in contradiction.text

    uncomparable = _labels_disputed(
        RefManifest(manuscript="p", labels_disputed=["6"], labels_uncomparable=["6"])
    )
    assert "nothing in them could be compared" in uncomparable.text
    assert "named different papers" not in uncomparable.text

    mixed = _labels_disputed(
        RefManifest(manuscript="p", labels_disputed=["6", "7"], labels_uncomparable=["7"])
    )
    assert "[6]" in mixed.text and "[7]" in mixed.text
    assert "named different papers" in mixed.text
    assert "could be compared" in mixed.text


def test_a_manifest_that_never_split_the_causes_keeps_the_disjunction():
    """`None` is never computed — every manifest written before this field,
    including one from an earlier commit of this branch — and the report may
    not pick a cause for it."""
    from papertrace.disclosures import _labels_disputed

    d = _labels_disputed(RefManifest(manuscript="p", labels_disputed=["6"]))
    assert "either" in d.text
    assert "named different papers" in d.text and "could be compared" in d.text
