"""`papertrace --version` — the first thing anyone types after installing.

It used to answer `No such option: --version`, so a user who had just installed
from a branch had no way to confirm what they got. The only route was
`python -c "import papertrace; print(papertrace.__version__)"`, which nobody
guesses.

It is eager on purpose: a bare `papertrace` on a TTY starts the guided wizard,
so a `--version` resolved after the callback body would answer the question by
interrogating the user about their manuscript.
"""

import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import __version__  # noqa: E402
from papertrace.cli import app  # noqa: E402


def _plain(output: str) -> str:
    """`output` with the styling removed — what a reader actually sees.

    Every assertion on rendered CLI text goes through this. Rich styles pieces
    of a token independently when colour is on, and colour depends on the
    environment: `--version` comes out as `-` + `-version`, and `0.6.0` as
    `0.6` + `.0`, so a substring assertion on the raw bytes is really a test of
    whoever ran it. One such assertion passed on a laptop and failed on all
    five CI pythons.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_it_prints_the_installed_version_and_exits_cleanly(flag):
    res = CliRunner().invoke(app, [flag])
    assert res.exit_code == 0, res.output
    assert __version__ in _plain(res.output)


def test_the_version_it_prints_is_the_package_version_not_a_literal():
    """A hard-coded string here would drift from `__init__.py` at the next
    release and answer confidently wrong — which is the whole failure mode this
    codebase is built to refuse. `docs/RELEASING.md` names exactly one home for
    the version; this must read from it."""
    from papertrace import cli

    src = Path(cli.__file__).read_text()
    assert "__version__" in src, "cli.py must read the version, not restate it"


def test_it_does_not_start_the_wizard(monkeypatch):
    """Eagerness is the point. `--version` on a terminal must not walk the user
    through a setup interview before answering."""
    from papertrace import wizard

    started = []
    monkeypatch.setattr(wizard, "run_wizard", lambda: started.append(1))
    res = CliRunner().invoke(app, ["--version"])

    assert res.exit_code == 0, res.output
    assert started == [], "--version started the wizard"


def test_it_is_advertised_on_the_help_screen():
    """Asserted on the text a READER sees, with the styling stripped.

    Not a nicety. Where colour is enabled — every CI runner sets `FORCE_COLOR`
    — rich's help highlighter emits the option as
    `\x1b[1;36m-\x1b[0m\x1b[1;36m-version\x1b[0m`: the two dashes are styled
    apart, so the literal `--version` is nowhere in the bytes. Asserting on the
    raw output tested the colour support of whoever ran it, and passed on a
    laptop while failing on all five CI pythons.
    """
    plain = _plain(CliRunner().invoke(app, ["--help"]).output)
    assert "--version" in plain, plain


def test_a_bare_invocation_still_reaches_the_wizard(monkeypatch):
    """The guard: adding an eager option must not break the first-run path it
    was added beside."""
    import types

    from papertrace import cli, wizard

    # patch cli's own `sys` reference, not `sys.stdin`: CliRunner replaces the
    # real stdin inside `invoke`, so a patch on the module attribute is undone
    # before the callback ever reads it
    monkeypatch.setattr(cli, "sys", types.SimpleNamespace(
        stdin=types.SimpleNamespace(isatty=lambda: True)))
    started = []
    monkeypatch.setattr(wizard, "run_wizard", lambda: started.append(1))
    res = CliRunner().invoke(app, [])

    assert res.exit_code == 0, res.output
    assert started == [1], "a bare papertrace no longer opens the wizard"
