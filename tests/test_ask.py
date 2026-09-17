"""The seam, and the two things about it that are now mechanically checked.

`_ask` is the only place this codebase shells out to a model. That used to be a
convention stated in CLAUDE.md; since `refs` also needs a model reading of the
bibliography, it is a rule with a test behind it.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest  # noqa: E402

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import check as check_mod  # noqa: E402


class _Completed:
    def __init__(self, model: str | None):
        self.returncode = 0
        payload: dict = {"result": "ok"}
        if model:
            payload["model"] = model
        self.stdout = json.dumps(payload)
        self.stderr = ""


# Every way a python file can start another process, not just the one `ask.py`
# happens to use. Matching `subprocess.run(` alone would have let a future file
# shell out via `Popen`, `check_output` or `os.system` and still pass a test
# whose name promises it cannot. `subprocess.TimeoutExpired:` carries no
# parenthesis, so the `\w+\(` is what keeps the `except` clause from matching.
_SHELLS_OUT = re.compile(
    r"subprocess\.\w+\(|\bPopen\(|os\.system\(|os\.popen\(|os\.exec\w*\(|os\.spawn\w*\("
)


def test_only_ask_py_shells_out_to_a_model():
    """One file in `src/` may start a process, and this is the test that says so.

    CLAUDE.md used to phrase this as "check.py is the only module that calls a
    model", which was a convention with nothing enforcing it. `refs` needs a
    reading of the bibliography from a model, so the rule moves to the seam —
    where it can be checked.

    A grep cannot prove a negative about execution — a file could reach a
    process through a name this pattern does not know. What it does prove is
    that no file in `src/` reaches one by any of the spellings a person would
    actually write, which is enough to make an accident loud.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "papertrace"
    shelling = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if _SHELLS_OUT.search(p.read_text())
    )
    assert shelling == ["ask.py"], shelling


def test_a_retry_policy_of_zero_says_so_rather_than_quietly_making_one_call(monkeypatch):
    """`ASK_ATTEMPTS` is the attempt budget, so zero forbids the call the budget
    exists to bound. Clamping it to one would hide a nonsense setting behind a
    working run; the old text claimed the branch was unreachable while standing
    in it. It raises `RuntimeError`, the same type the seam raises for a failed
    or timed-out call, so `check_claims`'s existing handler turns it into
    `unchecked` with a note rather than something new to catch."""
    called = []
    monkeypatch.setattr(check_mod, "_ask", lambda p, model=None: called.append(1) or "")
    monkeypatch.setattr(check_mod, "ASK_ATTEMPTS", 0)

    with pytest.raises(RuntimeError, match="ASK_ATTEMPTS is 0"):
        check_mod._ask_with_retry("prompt", None)
    assert called == [], "no call may be made under a zero attempt budget"


def test_two_call_sites_do_not_clobber_each_others_model(monkeypatch):
    """The report's `Checker:` line names the model that judged the claims.

    One global cannot carry that once two stages call the seam: a run whose
    judging made zero calls would print the reference-list model as its judge.
    """
    models = iter(["claude-opus-5", "claude-haiku-4-5"])
    monkeypatch.setattr(ask_mod.subprocess, "run",
                        lambda cmd, **kw: _Completed(next(models)))
    monkeypatch.setattr(ask_mod, "_MODELS", {})

    with ask_mod.for_site(ask_mod.SITE_REFS):
        ask_mod._ask("read this bibliography")
    with ask_mod.for_site(ask_mod.SITE_CHECK):
        ask_mod._ask("judge this claim")

    assert ask_mod.model_for(ask_mod.SITE_REFS) == "claude-opus-5"
    assert ask_mod.model_for(ask_mod.SITE_CHECK) == "claude-haiku-4-5"


def test_a_site_nobody_called_has_no_model_rather_than_a_plausible_one(monkeypatch):
    """`model_for` on an unused site is None, not the other site's model.

    A reference-list reading that never happened must not be able to name a
    model, and a judging pass that never happened must not inherit one.
    """
    monkeypatch.setattr(ask_mod.subprocess, "run",
                        lambda cmd, **kw: _Completed("claude-opus-5"))
    monkeypatch.setattr(ask_mod, "_MODELS", {})

    with ask_mod.for_site(ask_mod.SITE_REFS):
        ask_mod._ask("read this bibliography")

    assert ask_mod.model_for(ask_mod.SITE_CHECK) is None


def test_a_reply_that_names_no_model_does_not_erase_the_one_recorded(monkeypatch):
    """`claude -p` does not always report which model answered.

    A silent call is no evidence that the model changed, so the recorded name
    stands. Overwriting it with None would make the report stop naming a judge
    that did in fact judge.
    """
    replies = iter([_Completed("claude-opus-5"), _Completed(None)])
    monkeypatch.setattr(ask_mod.subprocess, "run", lambda cmd, **kw: next(replies))
    monkeypatch.setattr(ask_mod, "_MODELS", {})

    with ask_mod.for_site(ask_mod.SITE_CHECK):
        ask_mod._ask("judge one")
        ask_mod._ask("judge two")

    assert ask_mod.model_for(ask_mod.SITE_CHECK) == "claude-opus-5"


def test_for_site_restores_the_previous_site_on_the_way_out(monkeypatch):
    """Nesting must not leak. A leaked site is a misattributed model."""
    monkeypatch.setattr(ask_mod, "_SITE", "outer")
    with ask_mod.for_site(ask_mod.SITE_REFS):
        assert ask_mod._SITE == ask_mod.SITE_REFS
    assert ask_mod._SITE == "outer"


def test_check_still_exposes_the_seam_its_tests_patch():
    """Sixty-two monkeypatch sites across nine test modules do
    `monkeypatch.setattr(check_mod, "_ask", ...)`, and one does it by the dotted
    string `"papertrace.check._ask"`. Both need `_ask` to be an attribute of
    `check`, and need `check`'s own call sites to use the bare name so the patch
    is seen. If this assertion ever fails, those tests are not failing — they
    are making live, paid model calls."""
    assert check_mod._ask is ask_mod._ask


def test_the_judging_retry_attempts_exactly_ask_attempts_times(monkeypatch):
    """`ASK_ATTEMPTS` exists so the wizard's advertised worst-case bill cannot
    drift from the real retry policy — `CHANGELOG.md` records the incident. The
    retry loop did not read it: it hardcoded one retry, and matched the constant
    only because the constant happens to be 2."""
    calls = []

    def boom(prompt, model=None):
        calls.append(1)
        raise RuntimeError("transient")

    monkeypatch.setattr(check_mod, "_ask", boom)
    monkeypatch.setattr(check_mod, "ASK_ATTEMPTS", 3)
    with pytest.raises(RuntimeError):
        check_mod._ask_with_retry("prompt", None)
    assert len(calls) == 3, calls
