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


def test_the_path_to_the_disagreement_file_is_printed(disputed, capsys):
    """A file nobody is told about is a file nobody reads, and the menu's first
    option spends money on a question the reader could answer by looking."""
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
        lambda labels, cands, texts, model=None: reflist_mod.Resolution(
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
    # of CONTENT. The escalation may not touch this flag in either
    # direction, and asserting it is unchanged is what pins that;
    # `tests/test_numbering_invariant.py` proves no model path can set it
    # where it is legitimately False.
    assert manifest.numbering_verified is True
    assert manifest.labels_resolved == ["2"]
    assert manifest.labels_disputed == []
    # option 1 substitutes the resolved entry — the label will be judged
    # against the paper the resolution named, not the one it ruled against
    two = next(e for e in manifest.entries if e.num == "2")
    assert two.doi == "10.1000/beta"


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
    # True, and correctly so: `numbering_verified` means the chosen list
    # accounts for exactly the labels the body cites — a question of EXTENT,
    # which this fixture's parse does answer. A disputed label is a question
    # of CONTENT. The escalation may not touch this flag in either
    # direction, and asserting it is unchanged is what pins that;
    # `tests/test_numbering_invariant.py` proves no model path can set it
    # where it is legitimately False.
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
    # True, and correctly so: `numbering_verified` means the chosen list
    # accounts for exactly the labels the body cites — a question of EXTENT,
    # which this fixture's parse does answer. A disputed label is a question
    # of CONTENT. The escalation may not touch this flag in either
    # direction, and asserting it is unchanged is what pins that;
    # `tests/test_numbering_invariant.py` proves no model path can set it
    # where it is legitimately False.
    assert manifest.numbering_verified is True
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
        lambda labels, cands, texts, model=None: reflist_mod.Resolution(
            resolved={"13": resolved}, still_disputed=[],
            provenance=reflist_mod.ReflistProvenance(model="claude-opus-5"),
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
    assert "readings disagree at [2]" in out
    assert "numbering confirmed" not in out, "confirmed printed beside a withheld label"
    assert "accounts for every cited label" in out
