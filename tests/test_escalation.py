"""The disagreement is shown before the question is asked.

A reader who is offered a choice between two readings of a bibliography and has
not been shown either one is being asked to rubber-stamp, not to decide. So
step one writes the whole disagreement to a file and prints the path, and it is
unskippable — `test_the_disagreement_is_written_before_the_question_is_asked`
is that requirement encoded, and it fails if the two are ever reordered.

Driven through `_refs_pipeline` rather than through `_escalate_disputed`
directly for most of these: the thing under test is that a REAL run reaches the
file before it reaches the prompt, and a unit test of the escalation alone
cannot see the ordering the user asked for. `_reference_readings` is real and
unmocked — it already returns a single dict, matching every other consumer of
it (`tests/test_reference_readings.py`), so the two extra readings are made to
disagree by forcing `_needs_flat_reading` True (the same technique that
module's own `_wire_offline` uses) and by fabricating the flat-text extraction,
never by pretending the pure dict-builder returns something it does not.
"""

import json
import sys
from pathlib import Path

import pymupdf
import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402
from papertrace import reflist as reflist_mod  # noqa: E402
from papertrace.models import RefManifest  # noqa: E402
from papertrace.refs import _entry  # noqa: E402

# --- the interactive escalation: shown first, then offered ------------------


def _paper(path: Path) -> Path:
    """A one-page PDF citing [1] and [2], both printed in the References section.

    The disagreement at [2] is injected by fabricating the flat-text reading
    below — the point of a real PDF is that `_refs_pipeline` runs its whole
    length, ingest included, rather than being handed a fake `SourceMap`.
    """
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Agreement in CT of the Pancreas", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. First paper. 2020.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. Second paper. 2021.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


# The fabricated second reading of the same bibliography: agrees at [1], and
# names a different paper at [2]. `references_span_flat` is faked to return
# this rather than left real, because on a real pymupdf-backend ingest it is
# "identical to the existing parse on a pymupdf one" (its own docstring) — the
# self-corroboration case `_needs_flat_reading` exists to skip. Forcing the
# predicate without also controlling the text it feeds would make "parsed" and
# "pymupdf" agree on everything, and there would be no disagreement to show.
FLAT_TEXT = (
    "References\n"
    "[1] Alpha A. First paper. 2020.\n"
    "[2] Gamma G. A wholly different paper. 2019.\n"
)


@pytest.fixture()
def disputed(tmp_path, monkeypatch):
    """A run that reaches the escalation with [2] disputed, and nothing else stubbed.

    Returns `(pdf, case, answers)`; the caller fills `answers` with the menu
    keystrokes it wants `Prompt.ask` to return.
    """
    import papertrace.ingest as ingest_pkg
    import papertrace.refs as refs_mod
    from papertrace.refs import CROSSREF_NO_DOI, CrossrefDeposit

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None,
                        taken=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit",
                        lambda client, doi, email: CrossrefDeposit(absent=CROSSREF_NO_DOI))
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    # forces the predicate, not the backend — same technique
    # `tests/test_reference_readings.py`'s `_wire_offline` uses, so a pymupdf
    # ingest (fast, no docling download) still gets a second reading to compare
    monkeypatch.setattr(cli, "_needs_flat_reading", lambda smap: True)
    monkeypatch.setattr(ingest_pkg, "references_span_flat",
                        lambda pdf_path: (FLAT_TEXT, False))
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    answers: list[str] = []
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: answers.pop(0)))
    return _paper(tmp_path / "paper.pdf"), tmp_path / "case", answers


def _run_refs(pdf: Path, case: Path) -> RefManifest:
    # llm_refs=False: an offline test must not risk a live `claude -p` call even
    # on a machine that happens to have the CLI on PATH — the brief's own draft
    # of this fixture omitted it, which would have made every test below spend
    # money (or hang) on a host where `claude` is installed.
    cli._refs_pipeline(manuscript=pdf, case=case, provided=None,
                       email="t@example.org", parse_only=False, backend="pymupdf",
                       doi=None, supplement=None, llm_refs=False)
    return RefManifest.from_json(case / "refs_manifest.json")


def test_the_disagreement_is_written_before_the_question_is_asked(disputed, monkeypatch):
    """The user's instruction, encoded as a test: show, then offer.

    The fake `Prompt.ask` asserts the file is already on disk when it is
    called. That is the only way to pin the ordering — a test that checks the
    file afterwards passes just as well on an implementation that asks first.
    """
    pdf, case, answers = disputed
    path = case / "out" / "reference_disagreement.md"
    seen = {}

    def ask(*a, **k):
        seen["existed"] = path.exists()
        seen["text"] = path.read_text(encoding="utf-8") if path.exists() else ""
        return "2"

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(ask))
    _run_refs(pdf, case)

    assert seen["existed"] is True, "the menu was reached before the evidence was written"
    # both readings of the disputed label, and the text each was read from
    assert "Beta B. Second paper" in seen["text"]
    assert "Gamma G. A wholly different paper" in seen["text"]
    assert "[2]" in seen["text"]
    # and not a word about the label the readings agree on being a finding —
    # its own printed-list context is fine, its structured fields are not shown
    assert "First paper" in seen["text"], "the agreed label's text is context, not a finding"


def test_the_path_to_the_disagreement_file_is_printed(disputed, monkeypatch, capsys):
    """A file nobody is told about is a file nobody reads, and the menu's first
    option spends money on a question the reader could answer by looking.

    A wide console is forced (m10 of the Task 7 review): the absolute
    `tmp_path` printed here is one unbroken "word" to rich's wrapper, and at
    the ~80-column default it can hard-break mid-filename rather than at a
    space, which would fail this assertion on any tmp_path long enough — this
    one happened to pass only by being short.
    """
    from rich.console import Console

    monkeypatch.setattr(cli, "console", Console(width=1000))
    pdf, case, answers = disputed
    answers.append("2")
    _run_refs(pdf, case)
    assert "reference_disagreement.md" in capsys.readouterr().out


def test_the_default_answer_asks_the_model_and_records_the_user_as_choosing(
    disputed, monkeypatch
):
    """Option 1, taken by pressing return. `chosen_by` is `user` even on the
    default: the person was shown the disagreement and consented to proceed,
    which is an input to record, not evidence of anything. The resolution call
    itself is mocked at `reflist.resolve_disputed` — a live model call has no
    place in an offline test."""
    pdf, case, answers = disputed
    answers.append("1")
    resolved_entry = _entry("2", "Beta B. Second paper. 2021. doi:10.1000/beta")
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, cands, texts, disputing=None, model=None: reflist_mod.Resolution(
            resolved={"2": resolved_entry}, still_disputed=[],
            provenance=reflist_mod.ReflistProvenance(model="claude-opus-5", outcome="read"),
        ),
    )
    manifest = _run_refs(pdf, case)
    assert manifest.numbering_choice == "llm_resolved"
    assert manifest.numbering_chosen_by == "user"
    # True, and correctly so: `numbering_verified` means the chosen list
    # accounts for exactly the labels the body cites — a question of EXTENT,
    # which this fixture's parse does answer. A disputed label is a question
    # of CONTENT. No branch of `_escalate_disputed` assigns `rec.verified`
    # (grep it) — option 1 only swaps individual entries within the same
    # list, never its length or numbering, so the extent claim this fixture
    # already earned stands. `test_a_resolved_label_judges_against_the_resolved_entry`
    # below pins the direction actually feared (a resolution must not flip
    # this from False to True); Task 8 is the one that closes the remaining
    # menu choices over more fixtures.
    assert manifest.numbering_verified is True
    assert manifest.labels_resolved == ["2"]
    assert manifest.labels_disputed == []
    # option 1 substitutes the resolved entry — the label will be judged
    # against the paper the resolution named, not the one it ruled against
    two = next(e for e in manifest.entries if e.num == "2")
    assert two.doi == "10.1000/beta"
    assert two.slug, "C1: a slug-less entry is silently dropped by check.py"


def test_a_real_resolution_call_produces_a_usable_entry_end_to_end(disputed, monkeypatch):
    """C1, through the REAL `resolve_disputed` — not a stub.

    Every other option-1 test in this file replaces `resolve_disputed` with a
    lambda whose entry is built through `refs._entry`, which sets the one
    field the real function forgot (`slug`). A test double built from a
    production helper can supply an invariant the code under test omits, and
    then hide exactly the bug it looks like it is testing — that is how a
    slug-less resolved entry (silently dropped by `check.py`, downloaded to
    `sources_resolved/None.pdf`) survived review. This mocks only the seam
    `resolve_disputed` itself calls (`ask._ask`), so the real field
    verification and `refs._entry`-based construction actually run.
    """
    import papertrace.ask as ask_mod

    pdf, case, answers = disputed
    answers.append("1")
    # "Second paper" must be found verbatim in one of the two texts shown to
    # the call — it is, in `refs_text`, printed as "[2] Beta B. Second paper.
    # 2021." — so this is a genuine verbatim-verified answer, not a stub.
    reply = json.dumps([{"num": "2", "title": "Second paper", "year": "2021"}])
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: reply)

    manifest = _run_refs(pdf, case)

    assert manifest.numbering_choice == "llm_resolved"
    assert manifest.labels_resolved == ["2"]
    two = next(e for e in manifest.entries if e.num == "2")
    assert two.title == "Second paper"
    assert two.slug, "the real resolve_disputed must produce a usable slug (C1)"


def test_a_resolver_failure_leaves_every_label_disputed_and_is_reported(disputed, monkeypatch):
    """The resolution call can raise — `claude` not on PATH, a timeout, a
    non-zero exit — the same shape `_llm_reference_reading` already has to
    handle. `refs` has done useful work by this point and must not go down
    with a failed subprocess call: every disputed label stays disputed and
    withheld, and `chosen_by` is still "user" because a person did consent to
    trying, even though the attempt produced nothing."""
    pdf, case, answers = disputed
    answers.append("1")

    def boom(*a, **k):
        raise RuntimeError("claude -p timed out after 600s")

    monkeypatch.setattr(reflist_mod, "resolve_disputed", boom)
    manifest = _run_refs(pdf, case)

    assert manifest.numbering_choice == "withheld"
    assert manifest.numbering_chosen_by == "user"
    assert manifest.labels_disputed == ["2"]
    assert manifest.labels_resolved == []
    # True, and correctly so: `numbering_verified` is an EXTENT claim about
    # this fixture's parse, unaffected by a resolution attempt that raised
    # before touching anything. No branch of `_escalate_disputed` assigns
    # `rec.verified` (grep it); Task 8's job is to close this over every
    # remaining menu choice with its own fixtures.
    assert manifest.numbering_verified is True


def test_withholding_all_of_them_is_the_second_option(disputed):
    pdf, case, answers = disputed
    answers.append("2")
    manifest = _run_refs(pdf, case)
    assert manifest.numbering_choice == "withheld"
    assert manifest.numbering_chosen_by == "user"
    assert manifest.labels_disputed == ["2"]
    assert manifest.labels_resolved == []


def test_choosing_one_reading_whole_records_which_one(disputed):
    """Option 3. Both offered readings are deterministic parses, so this is a
    choice between two things the tool produced — never the model's list, which
    is a voter only and can reach neither `reconcile` nor `resolve_all`."""
    pdf, case, answers = disputed
    answers += ["3", "pymupdf"]
    manifest = _run_refs(pdf, case)
    assert manifest.numbering_choice == "pymupdf"
    assert manifest.numbering_chosen_by == "user"
    # False, correctly: `reconcile` measured EXTENT against the "parsed"
    # reading, not the "pymupdf" one just adopted in its place. Carrying the
    # old True forward would print "numbering confirmed" about a list nobody
    # measured (Task 7 review, M1) — cleared here, never widened to a second
    # meaning, which is why it is False rather than some other value.
    assert manifest.numbering_verified is False
    assert manifest.numbering_note.startswith("you chose")
    # M1's other half: `reference_source` must name the reading actually
    # adopted, not the one `reconcile` measured before it was discarded
    assert manifest.reference_source == "pymupdf"
    assert manifest.numbering_ledger == {}
    # the entries carried forward are that reading's
    assert [e.num for e in manifest.entries] == ["1", "2"]
    gamma = next(e for e in manifest.entries if e.num == "2")
    assert "Gamma" in gamma.raw


def test_aborting_writes_no_manifest(disputed):
    """Option 4 leaves the case as it was. A half-written manifest describing a
    numbering the user walked away from is worse than none."""
    pdf, case, answers = disputed
    answers.append("4")
    with pytest.raises(typer.Exit):
        cli._refs_pipeline(manuscript=pdf, case=case, provided=None,
                           email="t@example.org", parse_only=False, backend="pymupdf",
                           doi=None, supplement=None, llm_refs=False)
    assert not (case / "refs_manifest.json").exists()


def test_a_non_interactive_session_withholds_the_disputed_labels_without_asking(
    disputed, monkeypatch
):
    """The user's decision: suppress only the disputed labels.

    Not the whole audit — that throws away a useful audit over a numbering the
    reader can check by hand — and not a reading picked on their behalf, which
    would record a choice nobody made.
    """
    pdf, case, answers = disputed
    monkeypatch.setattr(cli, "_interactive", lambda: False)

    def boom(*a, **k):
        raise AssertionError("a non-interactive run must not prompt")

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(boom))
    manifest = _run_refs(pdf, case)

    assert manifest.numbering_choice == "withheld"
    assert manifest.numbering_chosen_by == "default"
    assert manifest.labels_disputed == ["2"]
    # the agreed label keeps its verdicts — it is the disputed one that is dropped
    assert "1" not in manifest.labels_disputed


def test_a_closed_stdin_is_not_interactive(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert cli._interactive() is False


def test_ci_is_not_interactive_even_on_a_tty(monkeypatch):
    """CI runs on a tty often enough that the tty test alone is not the gate, and
    a prompt in CI is a hung build, not a question."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("CI", "true")
    assert cli._interactive() is False


def test_a_tty_with_no_ci_flag_is_interactive(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert cli._interactive() is True


# --- `_escalate_disputed` directly: the one substitution rule -------------


def _rec(**kw):
    from papertrace.refs import Reconciliation

    return Reconciliation(**kw)


def test_a_resolved_label_judges_against_the_resolved_entry(tmp_path, monkeypatch):
    """Un-disputing a label without substituting its entry is the original bug.

    Menu option 1 takes a label out of `labels_disputed`, so `check` judges it
    again. If the chosen list still holds the entry the resolution ruled
    against, the verdict is printed against the very paper the model said was
    wrong — and the report says the user accepted it. The resolved entries exist
    in `Resolution.resolved`; the failure mode is computing them and throwing
    them away.
    """
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    resolved = _entry("13", "Weston AD (2019) The paper the label really names. "
                            "https://doi.org/10.1148/x13")
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, cands, texts, disputing=None, model=None: reflist_mod.Resolution(
            resolved={"13": resolved}, still_disputed=[],
            provenance=reflist_mod.ReflistProvenance(model="claude-opus-5", outcome="read"),
        ),
    )

    rec = _rec(labels_disputed=["13"])
    wrong = _entry("13", "Somebody Else (1999) A different paper entirely. "
                         "https://doi.org/10.1148/x99")
    out = cli._escalate_disputed(
        tmp_path, rec, [wrong], {"parsed": [wrong]}, {"parsed": "text"},
    )

    assert rec.labels_disputed == []          # the label will be judged again
    assert out[0].doi == "10.1148/x13"         # against the RESOLVED paper
    assert rec.chosen_by == "user"             # which is what authorises it
    assert rec.verified is False               # and it is still not confirmed


def test_zero_disputed_labels_never_prompts(tmp_path, monkeypatch):
    """An escalation with nothing to escalate is a no-op — even in a real
    terminal, nobody should be asked a question about an empty list."""

    def boom(*a, **k):
        raise AssertionError("nothing was in dispute — there was no question to ask")

    monkeypatch.setattr(cli, "_interactive", boom)
    rec = _rec(labels_disputed=[])
    entries = [_entry("1", "Alpha A. 2020.")]
    out = cli._escalate_disputed(tmp_path, rec, entries, {"parsed": entries}, {"parsed": "text"})
    assert out is entries
    assert rec.choice == ""
    assert rec.chosen_by == ""


def test_a_run_never_says_confirmed_beside_a_withheld_label(disputed, capsys):
    """The contradiction a partial Task 7 surfaced, pinned at the surface.

    `numbering_verified` means the chosen list accounts for exactly the labels
    the body cites — EXTENT. A disputed label is a question of CONTENT, which no
    count answers, so both can be true at once and the flag keeps its published
    meaning. What must not happen is one line saying the numbering is confirmed
    while the line above it withholds verdicts on a label: whichever of the two
    a reader believed, the other would be a lie.

    The first attempt at this was to un-verify the numbering, which broke two of
    Plan A's merged tests — correctly, because it silently widened a boolean
    that four report formats already read. The fix belongs in the sentence.
    """
    pdf, case, answers = disputed
    answers.append("2")
    manifest = _run_refs(pdf, case)
    out = capsys.readouterr().out

    assert manifest.numbering_verified is True
    assert manifest.labels_disputed == ["2"]
    # "do not agree", not "disagree": `disputed` also covers "nothing in the
    # readings could be compared", and the console may not assert the
    # contradicting cause for both — see `refs._label_state`
    assert "the readings do not agree at [2]" in out
    assert "numbering confirmed" not in out, "confirmed printed beside a withheld label"
    assert "accounts for every cited label" in out


# --- Task 7 review, Majors 8 and 9: a reading cannot launder its own coin-flip


def test_option_3_excludes_a_reading_that_duplicates_the_disputed_label(tmp_path, monkeypatch):
    """M8. `label_agreement` marks a label `disputed` outright when a single
    reading carries it twice — "downgrading that to `single` ... would let a
    verdict rest on the coin-flip" (`refs.py`). Offering that same reading in
    option 3's menu would let a person adopt it whole anyway, laundering the
    coin-flip into `labels_resolved`. It must not be offered at all."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    calls = []

    def ask(prompt_text, **kw):
        calls.append((prompt_text, kw.get("choices")))
        return {"  choice": "3", "  which reading": "pymupdf"}.get(prompt_text, "3")

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(ask))

    dup_a = _entry("2", "Alpha A. First. 2020.")
    dup_b = _entry("2", "Alpha A. Different. 2021.")
    single = _entry("2", "Gamma G. A different paper. 2019.")
    rec = _rec(labels_disputed=["2"])
    out = cli._escalate_disputed(
        tmp_path, rec, [dup_a],
        {"parsed": [dup_a, dup_b], "pymupdf": [single]},
        {"parsed": "text a", "pymupdf": "text b"},
    )

    top_menu = next(c for c in calls if c[0] == "  choice")
    assert "3" in top_menu[1], "pymupdf alone still qualifies, so option 3 must be offered"
    which_call = next(c for c in calls if c[0] == "  which reading")
    assert which_call[1] == ["pymupdf"], "the duplicating reading must not be offered"
    assert rec.choice == "pymupdf"
    assert out == [single]


def test_option_3_is_never_offered_when_nothing_qualifies(tmp_path, monkeypatch):
    """M9. `offered[0]` used to raise `IndexError` once neither `parsed` nor
    `pymupdf` qualified — reachable whenever the run's own parse duplicates
    the disputed label (this fixture) or is simply absent (a crossref+llm-only
    dispute). The fix is at the menu: option 3 is never offered, so `choices`
    passed to the real `Prompt.ask` never contains it. This pins the
    degradation for a caller that ignores `choices` anyway (a misbehaving
    test double, never a real terminal) — it falls through to the
    model-resolution path rather than crashing.
    """
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    calls = []

    def ask(prompt_text, **kw):
        calls.append((prompt_text, kw.get("choices")))
        return "3"

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(ask))
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, names, texts, disputing=None, model=None: reflist_mod.Resolution(
            still_disputed=list(labels),
            # a double must satisfy the SHAPE its consumer reads: a Resolution
            # that came back from a real call records `outcome="read"`, and
            # `_escalate_disputed` now branches on it
            provenance=reflist_mod.ReflistProvenance(outcome="read"),
        ),
    )
    dup_a = _entry("2", "Alpha A. First. 2020.")
    dup_b = _entry("2", "Alpha A. Different. 2021.")
    rec = _rec(labels_disputed=["2"])
    out = cli._escalate_disputed(
        tmp_path, rec, [dup_a], {"parsed": [dup_a, dup_b]}, {"parsed": "text"},
    )

    top_menu = next(c for c in calls if c[0] == "  choice")
    assert "3" not in top_menu[1]
    assert not any(c[0] == "  which reading" for c in calls), "never reached — nothing offered"
    assert rec.choice == "llm_resolved"
    assert out == [dup_a]


# --- Task 7 review, m5 and M3: an orphaned "resolved" label, and honest reasons


def test_a_resolved_label_absent_from_entries_stays_disputed(tmp_path, monkeypatch):
    """m5. `resolve_disputed` only ever answers about labels it was asked
    about, but those labels come from `rec.labels_disputed` — computed over
    ALL candidate readings (crossref, llm included), not just the chosen
    `entries` list. A label the chosen list never carried at all has nothing
    to substitute, and must not be recorded resolved with no entry behind it."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    orphan = _entry("99", "Nobody In The Chosen List. 2020.")
    real = _entry("2", "Beta B. The real one resolved. 2021.")
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, names, texts, disputing=None, model=None: reflist_mod.Resolution(
            resolved={"2": real, "99": orphan}, still_disputed=[],
            provenance=reflist_mod.ReflistProvenance(model="claude-opus-5", outcome="read"),
        ),
    )
    chosen = _entry("2", "Beta B. Original. 2021.")
    rec = _rec(labels_disputed=["2", "99"])
    out = cli._escalate_disputed(
        tmp_path, rec, [chosen], {"parsed": [chosen]}, {"parsed": "text"},
    )

    assert rec.labels_resolved == ["2"], "99 has no entry to substitute"
    assert rec.labels_disputed == ["99"], "orphaned, not silently dropped"
    assert out == [real]


def test_the_console_names_the_real_reason_a_label_stayed_disputed(tmp_path, monkeypatch, capsys):
    """M3. A malformed reply, an unprinted title and a genuine "cannot tell"
    are three different findings `resolve_disputed` already records in
    `provenance` — asserting one blanket "the model could not tell" over all
    three is a plausible-looking cause standing in for the honest one."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    prov = reflist_mod.ReflistProvenance(model="claude-opus-5", outcome="read")
    prov.fields_discarded.append("[9].title")
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, names, texts, disputing=None, model=None: reflist_mod.Resolution(
            still_disputed=["9"], provenance=prov,
        ),
    )
    rec = _rec(labels_disputed=["9"])
    entry = _entry("9", "Somebody. 2020.")
    cli._escalate_disputed(tmp_path, rec, [entry], {"parsed": [entry]}, {"parsed": "text"})
    out = capsys.readouterr().out

    assert "not printed in either text" in out
    assert "could not tell which paper it names" not in out


# --- Task 7 review, m1: the span window backs up to the previous line break -


def test_label_span_backs_up_past_a_long_preceding_entry():
    """m1. The old fixed-distance backtrack (`_SPAN_BEFORE = 260`) degraded
    silently once the preceding entry was longer than it — routine with a
    full author list. Backing up to the previous line break is exactly one
    entry by construction and cannot degrade regardless of length."""
    long_entry = "A" * 400
    text = f"[1] {long_entry}\n[2] Beta B. Second paper. 2021.\n"
    span = cli._label_span(text, "2")
    assert long_entry in span, "a 400-char preceding entry must still be included"


def test_label_span_at_the_very_start_of_the_text():
    text = "[1] Alpha A. First paper. 2020.\n[2] Beta B. Second paper. 2021.\n"
    span = cli._label_span(text, "1")
    assert span.startswith("[1]")


def test_label_span_is_empty_when_the_numeral_was_never_printed():
    assert cli._label_span("no numbered list here", "7") == ""


def test_a_resolved_entrys_slug_collision_is_disambiguated(tmp_path, monkeypatch):
    """C1's remaining half. `resolve_all` never re-slugs, so a resolved entry
    whose freshly computed slug collides with one already carried in the
    chosen list would share a download path with it — the second download
    overwrites the first. `_unique_slugs` re-runs over the WHOLE substituted
    list, not just the newly resolved entries, so a collision introduced by
    the substitution itself is caught too.
    """
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    kept = _entry("1", "Smith J. An unrelated paper. 2020.")
    wrong_two = _entry("2", "Wrong Wrong. 1999.")
    resolved_same_slug = _entry("2", "Smith J. The resolved paper. 2020.")
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, names, texts, disputing=None, model=None: reflist_mod.Resolution(
            resolved={"2": resolved_same_slug}, still_disputed=[],
            provenance=reflist_mod.ReflistProvenance(model="claude-opus-5", outcome="read"),
        ),
    )
    rec = _rec(labels_disputed=["2"])
    out = cli._escalate_disputed(
        tmp_path, rec, [kept, wrong_two],
        {"parsed": [kept, wrong_two]}, {"parsed": "text"},
    )
    slugs = [e.slug for e in out]
    assert len(slugs) == len(set(slugs)), f"colliding slugs would share a download path: {slugs}"


# --- whole-branch review, Critical 2: the call sees what the report says it saw


REFS_TEXT = (
    "References\n"
    "[1] Alpha A. First paper. 2020.\n"
    "[2] Beta B. Second paper. 2021.\n"
)


def _capture_ask(monkeypatch, reply: str) -> dict:
    """Intercept the one seam `resolve_disputed` calls, and keep the prompt."""
    import papertrace.ask as ask_mod

    seen: dict = {}

    def fake_ask(prompt, model=None):
        seen["prompt"] = prompt
        return reply

    monkeypatch.setattr(ask_mod, "_ask", fake_ask)
    monkeypatch.setattr(ask_mod, "model_for", lambda site: "claude-opus-5")
    return seen


def test_the_resolution_call_is_shown_the_reading_it_is_ruling_against(tmp_path, monkeypatch):
    """Critical 2. `reading_texts` holds only the readings with a printed
    span, so a dispute between the parse and the DEPOSIT — the case this
    whole feature was built for — was handed to a model that had never seen
    the deposit's entry, while `rec.note` and the per-claim disclosure both
    said it was shown both.

    The disputing entries travel as CONTEXT, not as a text to copy from:
    `_found` still verifies every returned value against the printed
    extraction alone, so a title the deposit carries and the page does not
    still cannot be believed."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    seen = _capture_ask(monkeypatch, json.dumps([{"num": "2", "title": "Second paper"}]))
    parsed = [_entry("1", "Alpha A. First paper. 2020."),
              _entry("2", "Beta B. Second paper. 2021.")]
    deposit = [_entry("2", "Gamma G. A wholly different paper. 2019.")]
    rec = _rec(labels_disputed=["2"])

    cli._escalate_disputed(
        tmp_path, rec, parsed,
        {"parsed": parsed, "crossref": deposit},
        {"parsed": REFS_TEXT},
    )

    assert "Gamma G. A wholly different paper" in seen["prompt"], seen["prompt"]
    assert "crossref" in seen["prompt"]


def test_one_printed_extraction_is_never_rendered_as_an_empty_second_reading(
    tmp_path, monkeypatch
):
    """`--backend pymupdf` leaves exactly one printed span, and the prompt
    rendered `--- READING B ---` followed by nothing — telling the model an
    extraction existed and was empty. `propose` refuses outright on fewer
    than two texts; this call cannot, because the printed page is still the
    only authority it needs, so it says how many it has instead."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    seen = _capture_ask(monkeypatch, json.dumps([{"num": "2", "title": "Second paper"}]))
    parsed = [_entry("2", "Beta B. Second paper. 2021.")]
    rec = _rec(labels_disputed=["2"])

    cli._escalate_disputed(
        tmp_path, rec, parsed, {"parsed": parsed}, {"parsed": REFS_TEXT},
    )

    prompt = seen["prompt"]
    assert "READING B" not in prompt, prompt
    assert "one extraction" in prompt


def test_the_note_names_the_readings_the_model_was_actually_shown(tmp_path, monkeypatch):
    """The other half of Critical 2: the report said "a model reading of both
    texts" on a run where the model saw one. It names them now."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    _capture_ask(monkeypatch, json.dumps([{"num": "2", "title": "Second paper"}]))
    parsed = [_entry("2", "Beta B. Second paper. 2021.")]
    rec = _rec(labels_disputed=["2"])

    cli._escalate_disputed(
        tmp_path, rec, parsed, {"parsed": parsed}, {"parsed": REFS_TEXT},
    )

    assert "both texts" not in rec.note
    assert "the parsed extraction" in rec.note


# --- whole-branch review, Major 4: the resolution call's own provenance


def test_the_resolution_calls_provenance_reaches_the_manifest(disputed, monkeypatch):
    """Major 4. A model call was made, paid for, verified field by field and
    allowed to change which papers are judged — and its model name, outcome
    and discarded fields existed nowhere structured, only inside a prose
    note. Meanwhile the `reflist_*` fields, which describe a DIFFERENT call,
    recorded that no model read the reference list at all."""
    import jsonschema

    pdf, case, answers = disputed
    answers.append("1")
    _capture_ask(monkeypatch, json.dumps([{"num": "2", "title": "Second paper"}]))

    manifest = _run_refs(pdf, case)

    assert manifest.resolution_outcome == "read"
    assert manifest.resolution_model == "claude-opus-5"
    assert manifest.resolution_readings == ["parsed", "pymupdf"]
    assert manifest.resolution_failure == ""
    # and it is a published field, not a local convenience
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json")
        .read_text()
    )
    payload = json.loads((case / "refs_manifest.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(payload)
    assert payload["resolution_outcome"] == "read"


def test_a_resolution_call_that_did_not_return_is_recorded_as_one_that_happened(
    disputed, monkeypatch
):
    """The third state, which a boolean could not hold: the call was made and
    raised. `resolution_outcome` says `failed` and carries why, so the run is
    never described as one where no model was asked."""
    pdf, case, answers = disputed
    answers.append("1")

    def boom(*a, **k):
        raise RuntimeError("claude -p timed out after 600s")

    monkeypatch.setattr(reflist_mod, "resolve_disputed", boom)

    manifest = _run_refs(pdf, case)

    assert manifest.resolution_outcome == "failed"
    assert "timed out" in manifest.resolution_failure
    assert manifest.resolution_model == ""


def test_a_manifest_with_no_resolution_call_says_nothing_about_one(disputed):
    """"" everywhere, and `[]` for the readings — never computed, which is
    what a run that never escalated actually knows."""
    pdf, case, answers = disputed
    answers.append("2")

    manifest = _run_refs(pdf, case)

    assert manifest.resolution_outcome == ""
    assert manifest.resolution_model == ""
    assert manifest.resolution_readings == []
    assert manifest.resolution_fields_discarded == []


# --- whole-branch review, minors 2 and 3


def test_seen_in_survives_a_whole_reading_substitution(disputed):
    """Minor 2. `stamp_seen_in` ran before the escalation, and option 3
    replaces every entry with one from a reading that was never stamped — so
    the manifest published `seen_in: []`, which the schema reads as "no
    reading was established as carrying this", for a run that established
    it."""
    pdf, case, answers = disputed
    answers += ["3", "pymupdf"]

    manifest = _run_refs(pdf, case)

    assert manifest.numbering_choice == "pymupdf"
    assert all(e.seen_in for e in manifest.entries), [e.seen_in for e in manifest.entries]


def test_adopting_a_reading_whole_is_described_as_adopting_it_whole(disputed):
    """Minor 3. Option 3 returns `list(candidates[pick])` — every entry, not
    only the disputed ones — while the note said "you chose X for the 1 label
    in dispute"."""
    pdf, case, answers = disputed
    answers += ["3", "pymupdf"]

    manifest = _run_refs(pdf, case)

    assert "whole" in manifest.numbering_note
    assert manifest.numbering_note.startswith("you chose")


def test_a_resolution_call_that_was_never_made_is_not_a_model_that_could_not_tell(
    tmp_path, monkeypatch, capsys
):
    """`resolve_disputed` can decline before spending anything — the two
    extractions are too long to send. Every label stays disputed, and the
    console must not print `_disputed_reason`'s "the model could not tell
    which paper it names" over a call nobody made: that is a plausible-looking
    cause standing in for the honest one, which is the failure this codebase
    exists to refuse."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, names, texts, disputing=None, model=None: reflist_mod.Resolution(
            still_disputed=list(labels),
            provenance=reflist_mod.ReflistProvenance(
                readings=list(names),
                failure="the extractions of the bibliography are too long to send",
            ),
        ),
    )
    two = _entry("2", "Beta B. Second paper. 2021.")
    rec = _rec(labels_disputed=["2"], note="a note")
    capsys.readouterr()

    cli._escalate_disputed(tmp_path, rec, [two], {"parsed": [two]}, {"parsed": REFS_TEXT})

    out = " ".join(capsys.readouterr().out.split())
    assert "could not tell" not in out
    assert "too long to send" in out
    assert rec.labels_disputed == ["2"]
    assert rec.labels_resolved == []
    assert rec.choice == "withheld"
    assert rec.resolution_outcome == "not_attempted"
    assert "too long to send" in rec.resolution_failure
    # and the note must not claim a reading that never happened
    assert "verified verbatim" not in rec.note
