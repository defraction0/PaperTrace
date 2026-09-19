"""One flag unmoved, through the real pipeline, read back off the artifact.

`numbering_verified` has a published meaning (`schemas/refs_manifest.schema.json`):
the chosen list accounts for exactly the labels the body cites, and no entry
refuses its own label. It is a test of EXTENT. A model's reading of the
bibliography is not that, and neither is a person answering a prompt — so this
module runs `cli._refs_pipeline` over every shape a model reply can take and
every choice the escalation offers, and asserts against the
`refs_manifest.json` each run actually wrote.

The seam on one side, the artifact on the other, and the production wiring in
between. That shape is the whole point of the rewrite: the previous version
built `candidates` and called `reconcile` by hand, so "the model's reading
never becomes one of `reconcile`'s arguments" was enforced by the test's own
source text rather than by the product's. Measured — handing the `llm` reading
to `reconcile` in `_refs_pipeline`, the design's central prohibition, left all
29 of its cells green. Never re-create the pipeline's wiring here; a cell that
does can only ever confirm its own re-creation.

"Unmoved" is not "always False". `_covers` is an extent check, and a fixture
whose extent legitimately matches gets `verified = True` — asserting `False`
there asserts an impossible value (three assertions in
`tests/test_escalation.py` had to be corrected for exactly that). So every
reply and every choice is run against BOTH a fixture that legitimately
verifies and one that legitimately does not, and the assertion is that neither
moves off the value the extent check alone produced.

A reply IS allowed to do one thing: withhold. So each shape also carries what
it is allowed to have moved — `labels_disputed` and `numbering_corroborated` —
because an invariant asserted alone cannot tell "the reply changed nothing"
from "the reply never reached the code". `reflist_outcome == "read"` is
asserted for the same reason, on every cell.
"""

import json
import re
import sys
from pathlib import Path

import pymupdf
import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import cli as cli_mod  # noqa: E402
from papertrace import reflist as reflist_mod  # noqa: E402
from papertrace import refs as refs_mod  # noqa: E402

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json"


def _paper(path: Path, *, extra_uncited: bool = False) -> Path:
    """A one-page PDF citing [1] and [2], with both references printed.

    `extra_uncited=True` prints a THIRD, uncited [3]: the parse then carries
    three entries against two cited labels, `_covers` requires equality, and
    `verified` is correctly False. Without that flag `_covers` is satisfied by
    the parse alone and `verified` is correctly True. The two together are the
    only way a cell can show a reply moved the flag in EITHER direction.

    Written out again rather than imported from `tests/test_reference_readings.py`:
    `tests/` has no `conftest.py` by house rule and no test module imports
    another, so the alternative is not a shared fixture but a hidden
    dependency between two files that are meant to fail independently.
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


# What the printed list carries, per extent: the numerals, and the opening of
# each entry's `raw`. A model-composed entry is re-assembled from verified
# FIELDS (`reflist.propose`: authors, year, title, journal, doi joined by ". "),
# so it can never reproduce the printed string — which is what makes these two
# a check that the manifest's entries came off the page and not out of a reply.
_PRINTED = {
    "covers": (["1", "2"], ["Alpha A.", "Beta B."]),
    "extra_reference": (["1", "2", "3"], ["Alpha A.", "Beta B.", "Gamma G."]),
}

# The marker that tells the two model calls apart inside one `ask._ask` double.
# Asserted to be real, and to be unique, by its own test below — a double that
# mis-routes would answer the resolution call with a reading reply and every
# cell would still be green.
_RESOLVE_MARKER = "LABELS IN DISPUTE:"


def _seam(reading: str, resolution: str = "[]"):
    """One `ask._ask` standing in for both calls the refs stage can make.

    `reflist.py` has no module-level `_ask` of its own — it does `from . import
    ask` and calls `ask._ask` qualified (`reflist.py:267-269` says so), so a
    monkeypatch on `reflist._ask` sets an attribute nothing reads and lets the
    real `claude -p` subprocess run. This module patches `ask._ask` and nothing
    else, which is also what keeps `reflist.propose` and `reflist.resolve_disputed`
    themselves REAL: every verbatim check they perform is exercised by these
    cells rather than stubbed past.
    """

    def _ask(prompt, model=None):
        return resolution if _RESOLVE_MARKER in prompt else reading

    return _ask


def _wire_offline(monkeypatch, *, reading: str, resolution: str = "[]", ask=None) -> None:
    """No network and no model call: retrieval is a no-op, Crossref answers
    nothing, and `claude` is treated as present regardless of the host.

    That last patch is load-bearing even though the seam below is what answers:
    `_llm_reference_reading` checks `ask.claude_available()` BEFORE calling
    `propose`, so on a machine with no `claude` CLI on PATH — CI, but not a
    developer's laptop — every cell would silently take the "not attempted"
    branch and the reply would never be read at all.

    `_needs_flat_reading` is forced rather than the backend changed. These
    tests ingest with `backend="pymupdf"` for speed (docling downloads ~500 MB
    on first use), and on a genuine pymupdf run `_refs_pipeline` skips the
    model call by design — so without this patch there is no reply to test.
    Force the PREDICATE, not the backend.
    """
    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None,
                        taken=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit",
                        lambda client, doi, email: refs_mod.CrossrefDeposit(
                            absent="no deposit, in a test"))
    monkeypatch.setattr(cli_mod, "_detected_doi", lambda m: None)
    monkeypatch.setattr(ask_mod, "claude_available", lambda: True)
    monkeypatch.setattr(cli_mod, "_needs_flat_reading", lambda smap: True)
    monkeypatch.setattr(ask_mod, "_ask", ask or _seam(reading, resolution))


def _run(tmp_path: Path, monkeypatch, *, extent: str, reading: str,
         resolution: str = "[]", ask=None) -> Path:
    """Run the real refs stage on the fixture paper; return the case folder.

    The case folder, not the manifest: menu choice 4 aborts before a manifest
    is written, and "no manifest exists" is that cell's assertion.
    """
    _wire_offline(monkeypatch, reading=reading, resolution=resolution, ask=ask)
    pdf = _paper(tmp_path / "paper.pdf", extra_uncited=(extent == "extra_reference"))
    case = tmp_path / "case"
    cli_mod._refs_pipeline(manuscript=pdf, case=case, provided=None,
                           email="test@example.org", parse_only=False,
                           backend="pymupdf", llm_refs=True)
    return case


def _manifest(case: Path) -> dict:
    """The written artifact, schema-validated on the way out — Gate 2 for free."""
    import jsonschema

    payload = json.loads((case / "refs_manifest.json").read_text())
    jsonschema.validate(payload, json.loads(SCHEMA_PATH.read_text()))
    return payload


# Every shape a reply can take, named by what is wrong with it, with what that
# shape is ALLOWED to have moved. `agrees_with_the_parse` is in here
# deliberately: the dangerous reply is not the malformed one — that fails
# loudly — it is the well-formed one confirming the parse, because that is the
# reply a reader is most tempted to promote to a check.
#
# `disputed` and `corroborated` are the only two things a reading may move, and
# they are here rather than asserted uniformly because an invariant asserted
# alone cannot tell "the reply changed nothing" from "the reply never ran". The
# values were observed, not predicted: a shape whose entries all fail
# verification never becomes a voter at all, so `parsed` and `pymupdf` carry
# the corroboration by themselves.
REPLIES = {
    "agrees_with_the_parse": {
        "reply": json.dumps([
            {"num": "1", "title": "A first paper", "year": "2020",
             "journal": "J Fixture", "reading": "AB"},
            {"num": "2", "title": "A second paper", "year": "2021",
             "journal": "J Fixture", "reading": "AB"},
        ]),
        "disputed": [], "corroborated": True,
    },
    # the one shape `reflist._found` exists for: a title nobody printed. The
    # whole reading must be refused, so this reply casts no vote — if the
    # verbatim check were disabled it would dispute [2] instead.
    "names_a_paper_the_page_does_not_print": {
        "reply": json.dumps([
            {"num": "1", "title": "A first paper", "year": "2020"},
            {"num": "2", "title": "Characterization of Brain Volume Changes", "year": "2021"},
        ]),
        "disputed": [], "corroborated": True,
    },
    "abstains": {
        "reply": json.dumps([{"num": "1", "cannot_tell": True},
                             {"num": "2", "cannot_tell": True}]),
        "disputed": [], "corroborated": True,
    },
    "malformed_json": {
        "reply": "here is what I think: [1] is Alpha, probably",
        "disputed": [], "corroborated": True,
    },
    "empty_array": {"reply": "[]", "disputed": [], "corroborated": True},
    "numeral_only_no_fields": {
        "reply": json.dumps([{"num": "1"}, {"num": "2"}]),
        "disputed": [], "corroborated": True,
    },
    # `DOI_RE` accepts a trailing hyphen and the prefix really is printed, so
    # only `_usable_doi`'s last-character rule tells half a DOI from a whole one
    "truncated_doi": {
        "reply": json.dumps([{"num": "2", "title": "A second paper",
                              "doi": "10.1038/s41591-"}]),
        "disputed": [], "corroborated": True,
    },
    "a_label_nobody_cites": {
        "reply": json.dumps([{"num": "99", "title": "A second paper"}]),
        "disputed": [], "corroborated": True,
    },
    # the one shape that legitimately withholds: a reading carrying one label
    # twice cannot say which paper it names, which is `label_agreement`'s own
    # rule, and a withheld verdict is the ONLY thing a reply is allowed to cause
    "duplicate_numeral": {
        "reply": json.dumps([{"num": "1", "title": "A first paper"},
                             {"num": "1", "title": "A second paper"}]),
        "disputed": ["1"], "corroborated": False,
    },
}


@pytest.mark.parametrize("extent", ["covers", "extra_reference"])
@pytest.mark.parametrize("shape", sorted(REPLIES))
def test_no_model_reply_shape_can_set_numbering_verified(shape, extent, tmp_path, monkeypatch):
    """Every reply shape, through `_refs_pipeline`, asserted on the manifest.

    The model's reading is a voter and nothing else: it never becomes one of
    `reconcile`'s arguments, so no reply can cause a source to be resolved,
    downloaded or judged, and no reply can move `numbering_verified` off the
    value the extent check alone produced. The `covers` cell proves a reply
    cannot manufacture a False from a legitimate True; the `extra_reference`
    cell proves it cannot manufacture a True from a legitimate False, which is
    the direction that would actually be dangerous.

    `expected` is hardcoded from the extent rather than derived. Deriving it
    from a second `reconcile` call would be tautological — it would assert the
    extent check agrees with itself — and `_covers`' rule ("as many entries as
    the highest cited label") is exactly what the two fixtures were built to
    straddle.
    """
    case = _run(tmp_path, monkeypatch, extent=extent, reading=REPLIES[shape]["reply"])
    payload = _manifest(case)

    # the reply reached the code. Without this the cells below are satisfied by
    # a run in which no model was ever asked, which is not what they claim.
    assert payload["reflist_outcome"] == "read", f"{shape}: the reply never reached propose"

    expected = extent == "covers"
    assert payload["numbering_verified"] is expected, f"{shape}/{extent} moved numbering_verified"

    # withholding is the one thing a reply may cause, and only on a cause it
    # demonstrated — a reply whose fields are not printed casts no vote at all
    assert payload["labels_disputed"] == REPLIES[shape]["disputed"], shape
    assert payload["numbering_corroborated"] is REPLIES[shape]["corroborated"], shape
    assert "llm" not in payload["corroborating_readings"], (
        "a model reading may only copy from the two extractions, which vote in "
        "their own right — it can dispute, it cannot corroborate"
    )

    # the resolved list is the PRINTED one, entry for entry
    nums, openings = _PRINTED[extent]
    assert [e["num"] for e in payload["entries"]] == nums, shape
    assert [e["raw"][:len(o)] for e, o in zip(payload["entries"], openings, strict=True)] == \
        openings, f"{shape}: an entry in the manifest did not come off the page"


@pytest.mark.parametrize("extent", ["covers", "extra_reference"])
@pytest.mark.parametrize("choice", ["1", "2", "3", "4"])
def test_no_interactive_choice_can_set_numbering_verified(choice, extent, tmp_path, monkeypatch):
    """Every menu choice, through `_refs_pipeline`, asserted on the manifest.

    The dispute is REAL: the `duplicate_numeral` reply makes `label_agreement`
    refuse to let the model reading speak for [1], which is what puts the label
    in `labels_disputed` and what makes `_refs_pipeline` escalate at all. No
    `rec` is built by hand and no `labels_disputed` is assigned by the test.

    `chosen_by: "user"` is what makes the escalation honest — a person
    consenting to proceed is an input, not evidence. So every branch must leave
    the flag no more true than `reconcile` left it. Option 3 is the one branch
    that touches it, and by design in one direction only: it substitutes a
    whole different list than the one `reconcile` measured, so it resets
    `verified` to the literal `False`. Asserted from both starting values to
    show the reset can never produce `True`.

    Choice 4 aborts before the manifest is written, so its assertion is that no
    manifest exists — and that the evidence file written before the menu still
    does, because "nothing was written" means the manifest and nothing else.
    """
    answers = iter([choice, "parsed"])

    def _prompt(*args, **kwargs):
        answer = next(answers)
        # the real menu refuses an answer it did not offer, and a double that
        # does not is a double more permissive than the thing it stands for —
        # the exact shape that hid a Critical earlier in this plan
        assert answer in kwargs.get("choices", [answer]), (
            f"the menu did not offer {answer!r}; it offered {kwargs.get('choices')}"
        )
        return answer

    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(cli_mod.Prompt, "ask", staticmethod(_prompt))

    kw = dict(extent=extent, reading=REPLIES["duplicate_numeral"]["reply"],
              # a resolution that settles [1] on a title printed on the page,
              # so choice 1 takes the branch that substitutes an entry rather
              # than the abstention branch, which touches less
              resolution=json.dumps([{"num": "1", "title": "A first paper",
                                      "year": "2020", "seen_in": "A"}]))
    if choice == "4":
        with pytest.raises(typer.Exit):
            _run(tmp_path, monkeypatch, **kw)
        assert not (tmp_path / "case" / "refs_manifest.json").exists(), (
            "an abort must leave no manifest for a later stage to trust"
        )
        assert (tmp_path / "case" / "out" / "reference_disagreement.md").exists(), (
            "the evidence file is written before the menu and survives the abort"
        )
        return

    payload = _manifest(_run(tmp_path, monkeypatch, **kw))

    expected = False if choice == "3" else (extent == "covers")
    assert payload["numbering_verified"] is expected, f"choice {choice} moved numbering_verified"
    assert payload["numbering_chosen_by"] == "user"
    assert payload["numbering_choice"] == {"1": "llm_resolved", "2": "withheld",
                                           "3": "parsed"}[choice]


@pytest.mark.parametrize("extent", ["covers", "extra_reference"])
def test_a_resolution_call_that_raises_cannot_set_numbering_verified(
    extent, tmp_path, monkeypatch
):
    """Option 1's `except Exception` is where a live subprocess failure
    (timeout, non-zero exit, unparseable stdout) surfaces mid-escalation. A
    failed call is a third outcome, not a licence to guess a verdict: it must
    leave the flag exactly where it found it, and say that it failed.

    The failure is injected at `ask._ask`, the seam that really raises — NOT by
    stubbing `reflist.resolve_disputed`. `resolve_disputed` calls `ask._ask`
    with no `try` of its own, so raising at the seam reaches the same `cli`
    branch with the real resolver in the loop. A stub there would keep this
    green if `resolve_disputed` ever grew its own broad `except` and returned a
    `Resolution` carrying confabulated entries — the double outliving the code
    it stands for, which is this plan's most expensive scar.
    """
    def _raising(prompt, model=None):
        if _RESOLVE_MARKER in prompt:
            raise RuntimeError("claude -p failed: simulated for this test")
        return REPLIES["duplicate_numeral"]["reply"]

    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(cli_mod.Prompt, "ask", staticmethod(lambda *a, **k: "1"))

    payload = _manifest(_run(tmp_path, monkeypatch, extent=extent,
                             reading="unused — the seam below answers", ask=_raising))

    assert payload["numbering_verified"] is (extent == "covers"), (
        "a resolution call that raised moved numbering_verified"
    )
    assert payload["resolution_outcome"] == "failed"
    assert payload["labels_disputed"] == ["1"], "a failed call resolves nothing"
    assert payload["labels_resolved"] == []
    assert payload["numbering_chosen_by"] == "user"  # a person consented to trying
    nums, openings = _PRINTED[extent]
    assert [e["num"] for e in payload["entries"]] == nums


def test_corroboration_can_be_true_in_the_same_run_that_keeps_verified_false(
    tmp_path, monkeypatch
):
    """The two axes must be independently settable, or the second is pointless.

    This is the 22-vs-19 case the corroboration axis exists for: every cited
    label is read the same way by two readings, and `_covers` still fails
    because a reference is printed that the body never cites. Before the second
    axis, that run could say nothing but "unconfirmed" — crying wolf on a
    numbering two independent readings agreed about entry for entry.

    Kept as its own cell although the parametrisation above covers the same
    run: the parametrisation asserts the two values against a table, and what
    is being claimed here is the RELATION between them — that one is False
    while the other is True in one manifest. A table cell cannot say that, and
    a future edit narrowing the table would not notice it had stopped.

    The model read and agreed here, and is still not named among the
    corroborating readings: `reflist.propose` may only copy from the two
    extractions, and both of those already vote in their own right, so its
    agreement is one text read twice.
    """
    payload = _manifest(_run(tmp_path, monkeypatch, extent="extra_reference",
                             reading=REPLIES["agrees_with_the_parse"]["reply"]))

    assert payload["reflist_outcome"] == "read"
    assert payload["numbering_verified"] is False
    assert payload["numbering_corroborated"] is True
    assert payload["corroborating_readings"] == ["parsed", "pymupdf"]


def test_the_two_model_calls_are_told_apart_by_a_marker_only_one_prompt_carries():
    """The `ask._ask` double routes on `_RESOLVE_MARKER`, so the marker has to
    be in one prompt and only one.

    If `RESOLVE_PROMPT` ever stopped carrying it, every cell above would answer
    the resolution call with a reading reply — the resolution would be read as
    unparseable, the escalation would take its abstention branch, and the whole
    suite would stay green while testing a branch it does not name. A double is
    only as honest as the thing it discriminates on.
    """
    assert _RESOLVE_MARKER in reflist_mod.RESOLVE_PROMPT
    assert _RESOLVE_MARKER not in reflist_mod.REFLIST_PROMPT


_SRC = Path(__file__).resolve().parent.parent / "src" / "papertrace"
# the names that may only ever be set from the extent check
_GUARDED = ("numbering_verified", "rec.verified", ".verified")
# what must never appear on the right-hand side of one. `corroborated` is in
# here because corroboration is the OTHER axis: `numbering_verified=rec.verified
# or rec.corroborated` is, literally, a model reply setting the flag — a model
# reading is a corroboration voter.
_FORBIDDEN = ("reflist", "propose", "resolve_disputed", "Prompt.ask", "_ask(",
              "answer", "choice", "chosen_by", "res.resolved", "corroborated")

# The first `=` that is an assignment: not `==`, `!=`, `<=` or `>=`. Splitting
# on it beats discarding any line that contains a comparison — under that older
# rule `rec.verified = bool(res.resolved) if answer == "1" else rec.verified`,
# the docstring's own forbidden spelling plus a ternary, was never examined.
_ASSIGN = re.compile(r"(?<![=!<>])=(?!=)")


def test_no_source_file_assigns_numbering_verified_from_a_model_or_a_prompt():
    """A grep, and honest about being one.

    What this proves: no source line *textually* assigns one of the guarded
    names from an expression mentioning the reflist module, a prompt answer,
    the seam, or the corroboration axis. That catches the changes a future task
    is actually likely to make — `rec.verified = bool(res.resolved)`, written
    in good faith by someone who read "resolved" as "checked", and
    `numbering_verified=rec.verified or rec.corroborated`, written by someone
    who read the two axes as one.

    What it cannot prove: that no value *derived* from a model reply reaches
    the flag through an intermediate variable, a helper, a dict or a
    `dataclasses.replace`. A grep has no dataflow, and every one of those
    spellings walks straight through it. The parametrised tests above are what
    cover that — they run `_refs_pipeline` itself and read the manifest it
    wrote, so a value arriving by any route at all shows up in the artifact.
    This one is the cheap tripwire that fails at review time rather than at
    runtime, and it is worth having for that and no more.
    """
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if not (m := _ASSIGN.search(code)):
                continue
            lhs, rhs = code[:m.start()], code[m.end():]
            if not any(g in lhs for g in _GUARDED):
                continue
            if any(f in rhs for f in _FORBIDDEN):
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_the_guard_above_would_actually_catch_the_changes_it_describes(tmp_path):
    """A guard test nobody has seen fail is a guard test nobody knows works.

    The grep is re-run over a file holding the exact lines the real test
    forbids — including the two the previous rule missed: a ternary (skipped
    wholesale for containing `==`) and the corroboration widening (whose
    spelling was not in `_FORBIDDEN` at all). Every planted line must be
    caught, so a narrowing of either constant fails here rather than silently.

    Written out again rather than factored into a shared helper: a helper
    broken the same way would make both tests pass together, which is the
    failure mode a self-test exists to exclude.
    """
    planted = [
        'rec.verified = bool(res.resolved)',
        'rec.verified = bool(res.resolved) if answer == "1" else rec.verified',
        '        numbering_verified=rec.verified or rec.corroborated,',
    ]
    bad = tmp_path / "papertrace" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("\n".join(planted) + "\n", encoding="utf-8")

    offenders = []
    for path in sorted(bad.parent.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if not (m := _ASSIGN.search(code)):
                continue
            lhs, rhs = code[:m.start()], code[m.end():]
            if any(g in lhs for g in _GUARDED) and any(f in rhs for f in _FORBIDDEN):
                offenders.append(lineno)
    assert offenders == list(range(1, len(planted) + 1)), (
        f"the grep caught {offenders} of {len(planted)} lines it exists to catch"
    )
