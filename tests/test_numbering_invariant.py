"""One flag staying False, across every reply shape and every interactive choice.

`numbering_verified` means exactly one thing: the manuscript's own `[N]` markers
accounted for exactly the labels a candidate reading carried. A model reading of
the bibliography is not that, and neither is a person answering a prompt — so
this module parametrises over every shape a model reply can take and every
choice the escalation offers, and asserts the flag is unmoved by every one of
them.

"Unmoved" is not "always False". `_covers` is an extent check, and a fixture
whose extent legitimately matches gets `verified = True` — asserting `False`
there would assert an impossible value (the parametrisation task's own brief
made exactly this mistake, in the same shape three assertions in
`tests/test_escalation.py` had to be corrected for already: see the report for
this task). So the reply-shape test below runs every reply against BOTH a
fixture that legitimately verifies and one that legitimately does not, and
checks that no reply — however it is shaped — moves either one off the value
`_covers` alone determined.

The companion test asserts `numbering_corroborated` CAN be True in the same
run. The two are independent axes on purpose; a second axis that moves only
when the first does is not a second axis, and the false-alarm corroboration
exists to silence would still be firing.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import cli as cli_mod  # noqa: E402
from papertrace import reflist as reflist_mod  # noqa: E402
from papertrace.models import RefEntry, RefManifest  # noqa: E402
from papertrace.refs import (  # noqa: E402,E501
    LABEL_AGREEMENT,
    corroborating_readings,
    label_agreement,
    reconcile,
)

# Reading A and reading B of one bibliography, as text. Every fixture DOI has a
# 4-9 digit registrant: `10.1/a` does not match `DOI_RE` and a plan already lost
# a round to that.
_TEXT_A = (
    "1. Alpha A. First paper on registration. 2020. doi:10.1000/alpha\n"
    "2. Beta B. Second paper on segmentation. 2021. doi:10.1000/beta\n"
)
_TEXT_B = (
    "1. Alpha A. First paper on registration. 2020. doi:10.1000/alpha\n"
    "2. Gamma G. A wholly different paper. 2019. doi:10.0002/b\n"
)

_BODY = {"1", "2"}


def _reading(pairs: list[tuple[str, str, str | None]]) -> list[RefEntry]:
    return [RefEntry(num=n, raw=raw, doi=doi) for n, raw, doi in pairs]


def _parsed() -> list[RefEntry]:
    return _reading([
        ("1", "Alpha A. First paper on registration. 2020.", "10.1000/alpha"),
        ("2", "Beta B. Second paper on segmentation. 2021.", "10.1000/beta"),
    ])


def _flat() -> list[RefEntry]:
    return _reading([
        ("1", "Alpha A. First paper on registration. 2020.", "10.1000/alpha"),
        ("2", "Gamma G. A wholly different paper. 2019.", "10.0002/b"),
    ])


# Every shape a reply can take, named by what is wrong with it. `agrees` is in
# here deliberately: the dangerous case is not the malformed reply — that fails
# loudly — it is the well-formed one that confirms the parse, because that is
# the reply a reader is most tempted to promote to a check.
REPLIES = {
    "agrees_with_the_parse": json.dumps([
        {"num": "1", "title": "First paper on registration", "doi": "10.1000/alpha"},
        {"num": "2", "title": "Second paper on segmentation", "doi": "10.1000/beta"},
    ]),
    "agrees_with_nothing": json.dumps([
        {"num": "1", "title": "A title printed in neither reading"},
        {"num": "2", "title": "Nor is this one"},
    ]),
    "abstains": json.dumps([{"num": "1", "cannot_tell": True},
                            {"num": "2", "cannot_tell": True}]),
    "malformed_json": "here is what I think: [1] is Alpha, probably",
    "empty_array": "[]",
    "title_printed_nowhere": json.dumps([
        {"num": "2", "title": "Characterization of Brain Volume Changes",
         "doi": "10.1000/beta"},
    ]),
    "truncated_doi": json.dumps([
        {"num": "2", "title": "Second paper on segmentation", "doi": "10.1038/s41591-"},
    ]),
    "a_label_nobody_cites": json.dumps([
        {"num": "99", "title": "Second paper on segmentation"},
    ]),
}

# Two extents, both legitimate. "covers" is `_parsed()` unchanged: it carries
# exactly the body's two labels, so `_covers` passes and `verified` is
# correctly True. "extra_reference" adds one entry the body never cites (the
# 22-vs-19 shape: a reference cited only in a supplement) — `_covers` requires
# `len(entries) == max(body)` exactly, so this one fails it and `verified` is
# correctly False. Both are computed once, by the real `reconcile`, rather than
# asserted from outside — a hand-picked expectation here would be exactly the
# mistake this module exists to catch elsewhere.
_EXTRA = RefEntry(num="3", raw="Delta D. Cited only in the supplement. 2022.")


def _extent_parsed(extent: str) -> list[RefEntry]:
    return _parsed() + [_EXTRA] if extent == "extra_reference" else _parsed()


@pytest.mark.parametrize("extent", ["covers", "extra_reference"])
@pytest.mark.parametrize("shape", sorted(REPLIES))
def test_no_model_reply_shape_can_set_numbering_verified(shape, extent, monkeypatch):
    """Parametrised over every model reply shape, on both a verified-True and a
    verified-False fixture.

    Most of this design's safety lives in one flag staying at whatever
    `reconcile`'s extent check put it at. The model's reading is a voter: it
    never becomes one of `reconcile`'s arguments (`reconcile(_BODY, None,
    parsed)` below never mentions `proposed`), so no reply can cause a source
    to be resolved, downloaded or judged, and no reply can move `verified` off
    the value the extent check alone produced — in either direction. The
    `covers` cell proves a reply cannot manufacture a False from a legitimate
    True; the `extra_reference` cell proves a reply cannot manufacture a True
    from a legitimate False, which is the direction that would actually be
    dangerous.

    Patches `ask._ask`, not `reflist._ask` — `reflist.py` has no such name of
    its own; it imports the whole `ask` module and calls `ask._ask` qualified.
    A monkeypatch on the wrong attribute sets an attribute nothing reads and
    lets the real subprocess seam run, which is exactly the live-model-call
    this suite's offline gate exists to forbid.
    """
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: REPLIES[shape])
    proposed, prov = reflist_mod.propose(_TEXT_A, _TEXT_B, label_a="parsed", label_b="pymupdf")
    assert prov.outcome == "read"  # the call happened; what came back is the point

    parsed = _extent_parsed(extent)
    candidates = {"parsed": parsed, "pymupdf": _flat(), "llm": proposed}
    states = label_agreement(candidates, _BODY)

    # the parse and the deposit are what reconcile arbitrates between; `llm` is
    # not passed, and a task that passes it has broken the design
    entries, rec = reconcile(_BODY, None, parsed)
    manifest = RefManifest(
        manuscript="p.pdf",
        entries=entries,
        numbering_verified=rec.verified,
        labels_disputed=[k for k, v in states.items() if v == "disputed"],
    )

    expected = extent == "covers"
    assert rec.verified is expected, f"{shape}/{extent} moved rec.verified"
    assert manifest.numbering_verified is expected, f"{shape}/{extent} moved the manifest"
    assert all(v in LABEL_AGREEMENT for v in states.values())


@pytest.mark.parametrize("initial_verified", [True, False], ids=["was_verified", "was_unverified"])
@pytest.mark.parametrize("choice", ["1", "2", "3", "4"])
def test_no_interactive_choice_can_set_numbering_verified(choice, initial_verified, monkeypatch, tmp_path):
    """Parametrised over all four menu choices, including abort, on both a
    verified-True and a verified-False starting point.

    `chosen_by: "user"` is what makes the escalation honest — a person
    consenting to proceed is an input, not evidence. So every branch of the
    menu must leave `rec.verified` no more true than `reconcile` left it: for
    choices 1, 2 and 4 that means exactly unchanged (nothing in their code
    paths touches `rec.verified` at all), and this is the test that says so for
    the branch a reviewer would be most tempted to "improve" — option 3, where
    the user positively asserts which reading is right. Option 3 is the one
    exception, and by design: it substitutes a whole different list than the
    one `reconcile` measured, so it explicitly resets `verified` to the literal
    `False` — never derived from the answer, never derived from which reading
    was picked — because the extent of the newly adopted list was never
    checked. That reset can only ever produce `False`; it is asserted here on
    both starting points to show it never produces `True`.
    """
    import typer

    candidates = {"parsed": _parsed(), "pymupdf": _flat()}
    texts = {"parsed": _TEXT_A, "pymupdf": _TEXT_B}
    # `_parsed()` legitimately verifies (see the test above); overridden here
    # because this test is about `_escalate_disputed`'s own behaviour, not
    # `reconcile`'s — a disputed label and a verified extent are independent
    # findings, so the escalation must be exercised from both starting values.
    _entries, rec = reconcile(_BODY, None, _parsed())
    rec.labels_disputed = ["2"]
    rec.verified = initial_verified

    answers = iter([choice, "pymupdf"])
    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(cli_mod.Prompt, "ask", staticmethod(lambda *a, **k: next(answers)))
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: REPLIES["agrees_with_the_parse"])

    if choice == "4":
        with pytest.raises(typer.Exit):
            cli_mod._escalate_disputed(tmp_path, rec, _parsed(), candidates, texts)
    else:
        cli_mod._escalate_disputed(tmp_path, rec, _parsed(), candidates, texts)

    expected = False if choice == "3" else initial_verified
    assert rec.verified is expected, f"menu choice {choice} moved rec.verified"
    manifest = RefManifest(
        manuscript="p.pdf",
        numbering_verified=rec.verified,
        numbering_choice=rec.choice,
        numbering_chosen_by=rec.chosen_by,
        labels_resolved=rec.labels_resolved,
        labels_disputed=rec.labels_disputed,
    )
    assert manifest.numbering_verified is expected
    if choice != "4":
        assert manifest.numbering_chosen_by == "user"


@pytest.mark.parametrize("initial_verified", [True, False], ids=["was_verified", "was_unverified"])
def test_a_raising_resolver_cannot_set_numbering_verified(initial_verified, monkeypatch, tmp_path):
    """Option 1's `except Exception` is where a live subprocess failure
    (timeout, non-zero exit, unparseable stdout) surfaces mid-escalation — a
    failed call is a third outcome, not a licence to guess a verdict. It must
    leave `rec.verified` exactly where it found it, same as every other branch
    that does not explicitly reset it.

    `reflist.resolve_disputed` is patched as a module attribute, not handed to
    `cli` directly: `_escalate_disputed` does `from .reflist import
    resolve_disputed` inside its own body, so the name is looked up fresh from
    `reflist`'s namespace on every call and a monkeypatch on the module
    attribute is what a function-local import actually sees.
    """
    candidates = {"parsed": _parsed(), "pymupdf": _flat()}
    texts = {"parsed": _TEXT_A, "pymupdf": _TEXT_B}
    _entries, rec = reconcile(_BODY, None, _parsed())
    rec.labels_disputed = ["2"]
    rec.verified = initial_verified

    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(cli_mod.Prompt, "ask", staticmethod(lambda *a, **k: "1"))

    def _boom(*args, **kwargs):
        raise RuntimeError("claude -p failed: simulated for this test")

    monkeypatch.setattr(reflist_mod, "resolve_disputed", _boom)

    entries_out = cli_mod._escalate_disputed(tmp_path, rec, _parsed(), candidates, texts)

    assert entries_out == _parsed()  # nothing substituted — the call never answered
    assert rec.verified is initial_verified, "a raising resolver moved rec.verified"
    assert rec.chosen_by == "user"  # a person consented to trying, even though it failed
    manifest = RefManifest(
        manuscript="p.pdf",
        numbering_verified=rec.verified,
        numbering_chosen_by=rec.chosen_by,
    )
    assert manifest.numbering_verified is initial_verified


def test_corroboration_can_be_true_in_the_same_run_that_keeps_verified_false():
    """The two axes must be independently settable, or the second is pointless.

    This is the 22-vs-19 case the corroboration axis exists for: every cited
    label is read the same way by two readings, and `_covers` still fails
    because three of the references are cited only in the supplement. Before
    the second axis, that run could say nothing but "unconfirmed" — crying
    wolf on a numbering two independent readings agreed about entry for entry.
    """
    candidates = {"parsed": _parsed(), "pymupdf": _parsed()}
    states = label_agreement(candidates, _BODY)
    assert set(states.values()) == {"agreed"}

    # a list longer than the highest cited label: `_covers` requires equality
    longer = _parsed() + [RefEntry(num="3", raw="Delta D. Cited only in the supplement. 2022.")]
    _entries, rec = reconcile(_BODY, None, longer)
    assert rec.verified is False, "the extent check still fails — that is the premise"

    manifest = RefManifest(
        manuscript="p.pdf",
        numbering_verified=rec.verified,
        numbering_corroborated=all(v == "agreed" for v in states.values()),
        corroborating_readings=corroborating_readings(candidates, _BODY),
    )
    assert manifest.numbering_verified is False
    assert manifest.numbering_corroborated is True
    assert manifest.corroborating_readings == ["parsed", "pymupdf"]


_SRC = Path(__file__).resolve().parent.parent / "src" / "papertrace"
# the two names that may only ever be set from the extent check
_GUARDED = ("numbering_verified", "rec.verified", ".verified")
# what must never appear on the right-hand side of one
_FORBIDDEN = ("reflist", "propose", "resolve_disputed", "Prompt.ask", "_ask(",
              "answer", "choice", "chosen_by", "res.resolved")


def test_no_source_file_assigns_numbering_verified_from_a_model_or_a_prompt():
    """A grep, and honest about being one.

    What this proves: no source line *textually* assigns one of the guarded
    names from an expression mentioning the reflist module, a prompt answer or
    the seam. That catches the change a future task is actually likely to make
    — `rec.verified = bool(res.resolved)`, written in good faith by someone who
    read "resolved" as "checked".

    What it cannot prove: that no value *derived* from a model reply reaches
    the flag through an intermediate variable, a helper or a dict. A grep has
    no dataflow. The parametrised tests above are what cover that, by running
    the real code over every reply shape and every choice; this one is the
    cheap tripwire that fails at review time rather than at runtime, and it is
    worth having for exactly that reason and no more.
    """
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "=" not in code or "==" in code:
                continue
            lhs, _, rhs = code.partition("=")
            if not any(g in lhs for g in _GUARDED):
                continue
            if any(f in rhs for f in _FORBIDDEN):
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_the_guard_above_would_actually_catch_the_change_it_describes(tmp_path):
    """A guard test nobody has seen fail is a guard test nobody knows works.

    The grep is re-run over a file holding the exact line the real test
    forbids, so the assertion has been observed both ways. Written out again
    rather than factored into a shared helper: a helper broken the same way
    would make both tests pass together, which is the failure mode a self-test
    exists to exclude.
    """
    bad = tmp_path / "papertrace" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("rec.verified = bool(res.resolved)\n", encoding="utf-8")

    offenders = []
    for path in sorted(bad.parent.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "=" not in code or "==" in code:
                continue
            lhs, _, rhs = code.partition("=")
            if any(g in lhs for g in _GUARDED) and any(f in rhs for f in _FORBIDDEN):
                offenders.append(f"{path.name}:{lineno}")
    assert offenders, "the grep would not catch the line it exists to catch"
