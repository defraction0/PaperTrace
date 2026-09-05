"""The only seam that calls a model must not call it from wherever the user happened
to be standing.

`_ask` inherited the caller's `cwd` and the CLI's full default toolset. Judging
from inside a repo silently fed that repo's own CLAUDE.md into every verdict, and
the judge held Bash/Edit/WebFetch while it is only ever supposed to read
third-party PDF text and return one. Neither was disclosed anywhere in the
report — a silent scope creep this project otherwise refuses to have.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import check as check_mod  # noqa: E402


class _FakeCompleted:
    def __init__(self):
        self.returncode = 0
        self.stdout = json.dumps({"result": "ok", "model": "claude-sonnet-5"})
        self.stderr = ""


def test_ask_does_not_run_from_the_caller_s_working_directory(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeCompleted()

    monkeypatch.setattr(check_mod.subprocess, "run", fake_run)

    check_mod._ask("judge this")

    assert captured["kwargs"].get("cwd") is not None
    assert captured["kwargs"]["cwd"] != "."


def test_ask_disables_customizations_and_tool_access(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(check_mod.subprocess, "run", fake_run)

    check_mod._ask("judge this")

    cmd = captured["cmd"]
    assert "--safe-mode" in cmd
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""


def test_ask_does_not_run_from_the_shared_temp_root(monkeypatch):
    """The system temp root (`/tmp`) is world-writable and predictable — a
    private, per-process directory is required so another local user cannot
    plant config the judging call would then walk into."""
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(kwargs.get("cwd"))
        return _FakeCompleted()

    monkeypatch.setattr(check_mod.subprocess, "run", fake_run)

    check_mod._ask("judge this")
    check_mod._ask("judge this again")

    assert all(cwd != tempfile.gettempdir() for cwd in captured)
    assert all(Path(cwd).is_dir() for cwd in captured)
    assert len(set(captured)) == 1, "the scratch directory should be reused, not recreated per call"
