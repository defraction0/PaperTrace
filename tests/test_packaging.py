"""The sdist must ship the evaluation harness it advertises.

v0.3.1's source distribution carried exactly one file under `evals/` —
`evals/README.md` — while that very README told the reader to run
`python evals/runners/score_only.py ...`. The runners, the gold sets, the
metrics and the harness's own tests were all absent, so the only instruction
the packaged file gave could not be followed from the archive.

The cause was not an oversight in the file list. Hatchling's include/exclude
patterns are gitignore-style, so the bare entry `"README.md"` matched at *any*
depth: `evals/README.md` and `examples/demo/README.md` travelled, and nothing
else from either tree did. These tests read the pyproject tables directly and
assert what they would match, so the table cannot silently narrow again.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# tomllib is stdlib from 3.11; `[dev]` supplies tomli below that. Skipping was
# the earlier answer and it cost more than it looked: 3.10 is the OLDEST version
# requires-python allows, so the cell that skipped was the one whose packaging
# behaviour mattered most - all 26 tests were silently absent there while CI
# reported green. A module-level bare `import` is still wrong (a collection
# error interrupts the whole session, losing every test, not just this file),
# hence importorskip on the fallback rather than an import.
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = pytest.importorskip("tomli", reason="pip install -e '.[dev]' supplies tomli on 3.10")


def _repo_root() -> Path:
    """Walk up to the directory holding pyproject.toml.

    Works from a checkout and from an extracted sdist alike — which matters,
    because `tests/` now ships and this file runs from inside the archive.
    """
    for candidate in (Path(__file__).resolve(), *Path(__file__).resolve().parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("no pyproject.toml above tests/test_packaging.py")


ROOT = _repo_root()
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

SDIST = PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]
WHEEL = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]
INCLUDE = SDIST.get("include", [])
EXCLUDE = SDIST.get("exclude", [])


# ---------------------------------------------------------------------------
# gitignore-style matching
# ---------------------------------------------------------------------------
#
# APPROXIMATION, deliberately. Hatchling delegates to `pathspec`'s
# `gitwildmatch` dialect. This is a small model of the subset of that dialect
# the two tables actually use, written so the tests assert against something
# readable rather than against the build backend's behaviour observed once.
#
# Modelled:
#   * a leading "/" anchors the pattern at the archive root
#   * a pattern containing no "/" matches that basename at ANY depth
#     (this is the exact rule that caused the bug, so it must be reproduced)
#   * a pattern that contains a "/" anywhere else is anchored at the root
#   * "**" spans separators; a leading "**/" also matches at depth zero
#   * "*" and "?" stay inside a single path segment
#   * matching a DIRECTORY matches everything beneath it, so a path matches if
#     the pattern matches the path itself or any of its ancestor prefixes
#
# NOT modelled, and not relied upon by any test below:
#   * "!" negation and include/exclude precedence ordering
#   * character classes such as [a-z]
#   * hatchling's own always-present files (pyproject.toml, PKG-INFO,
#     .gitignore) which appear in the archive regardless of these tables
#   * file-vs-directory patterns: a trailing "/" is stripped, since neither
#     table uses that form
#
# The archive checks in docs/RELEASING.md are what confirm the real backend
# agrees with this model. These tests are the fast guard; `tar -tzf` is the
# proof.


def _translate(pattern: str) -> re.Pattern[str]:
    pattern = pattern.rstrip("/")
    anchored = pattern.startswith("/") or "/" in pattern.strip("/")
    body = pattern.lstrip("/")

    prefix = ""
    if body.startswith("**/"):
        # gitignore: "**/foo" matches foo at any depth, root included
        prefix = "(?:.*/)?"
        body = body[3:]
    elif not anchored:
        prefix = "(?:.*/)?"

    out = []
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            out.append(".*")
            i += 2
        elif body[i] == "*":
            out.append("[^/]*")
            i += 1
        elif body[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(body[i]))
            i += 1
    return re.compile(prefix + "".join(out))


def _ancestors(path: str) -> list[str]:
    """The path itself plus every directory prefix, longest first."""
    parts = path.strip("/").split("/")
    return ["/".join(parts[: n + 1]) for n in reversed(range(len(parts)))]


def matches(pattern: str, path: str) -> bool:
    rx = _translate(pattern)
    return any(rx.fullmatch(candidate) for candidate in _ancestors(path))


def matching_entries(patterns: list[str], path: str) -> list[str]:
    return [p for p in patterns if matches(p, path)]


# ---------------------------------------------------------------------------
# the matcher's own tests — otherwise every assertion below leans on an
# untested oracle
# ---------------------------------------------------------------------------


def test_matcher_bare_basename_matches_at_depth():
    """The bug, reproduced. This is why the table must anchor its root files."""
    assert matches("README.md", "evals/README.md")
    assert matches("README.md", "examples/demo/README.md")
    assert matches("README.md", "README.md")


def test_matcher_anchored_pattern_does_not_match_at_depth():
    assert matches("/README.md", "README.md")
    assert not matches("/README.md", "evals/README.md")
    assert not matches("/README.md", "examples/demo/README.md")


def test_matcher_directory_include_covers_children():
    assert matches("/evals", "evals/runners/score_only.py")
    assert matches("/src", "src/papertrace/check.py")
    assert not matches("/evals", "evaluation/other.py")


def test_matcher_doublestar_spans_and_matches_at_root():
    assert matches("**/__pycache__/**", "evals/__pycache__/x.pyc")
    assert matches("**/__pycache__/**", "__pycache__/x.pyc")
    assert matches("**/*.pyc", "a/b/c.pyc")
    assert matches("evals/runs/**", "evals/runs/x.json")


# ---------------------------------------------------------------------------
# what the sdist must carry
# ---------------------------------------------------------------------------

# Every one of these is named by evals/README.md or evals/DESIGN.md as
# something the reader runs or inspects. Shipping the README that advertises
# them without the files themselves is what this test exists to prevent.
ADVERTISED = [
    "evals/DESIGN.md",
    "evals/metrics.py",
    "evals/runners/score_only.py",
    "evals/gold/demo_v1.gold.json",
    "evals/templates/eval.md.j2",
    "evals/tests/test_metrics.py",
    "tests/test_coverage.py",
]


@pytest.mark.parametrize("path", ADVERTISED)
def test_advertised_paths_are_included(path):
    assert matching_entries(INCLUDE, path), (
        f"{path} is advertised to users but no sdist include entry matches it; "
        f"include = {INCLUDE}"
    )
    assert not matching_entries(EXCLUDE, path), (
        f"{path} is advertised to users but sdist exclude "
        f"{matching_entries(EXCLUDE, path)} removes it again"
    )


@pytest.mark.parametrize("path", ADVERTISED)
def test_advertised_paths_exist_on_disk(path):
    """An include entry for a file that does not exist ships nothing."""
    assert (ROOT / path).exists(), f"{path} is in the include contract but missing"


# ---------------------------------------------------------------------------
# what the sdist must not carry
# ---------------------------------------------------------------------------

# Asserted against EXCLUDE specifically, not merely "absent from the archive".
# "It happens not to be matched by any include" is the accident that produced
# the original bug; an explicit exclude is a decision.
NEVER_SHIPPED = [
    "evals/runs/x.json",
    ".serena/project.yml",
    ".claude/settings.local.json",
    "evals/__pycache__/x.pyc",
]


@pytest.mark.parametrize("path", NEVER_SHIPPED)
def test_run_artefacts_and_local_paths_are_excluded(path):
    assert matching_entries(EXCLUDE, path), (
        f"{path} must be excluded explicitly rather than left to chance; "
        f"exclude = {EXCLUDE}"
    )


# ---------------------------------------------------------------------------
# the config that travels must describe an archive that works
# ---------------------------------------------------------------------------


def test_every_pytest_testpath_ships():
    """pyproject travels inside the sdist, so its testpaths must exist there.

    An archive whose `testpaths` names a directory it does not carry makes a
    bare `pytest` fail on collection for anyone who unpacks it. Derived from
    the config rather than hard-coded, so adding a testpath without shipping
    it turns this red.
    """
    testpaths = PYPROJECT["tool"]["pytest"]["ini_options"]["testpaths"]
    assert testpaths, "no testpaths configured — this test would be vacuous"
    for entry in testpaths:
        assert matching_entries(INCLUDE, entry), (
            f"testpaths names {entry!r} but no sdist include entry matches it, "
            f"so bare pytest fails inside the archive; include = {INCLUDE}"
        )
        assert not matching_entries(EXCLUDE, entry), (
            f"testpaths names {entry!r} but sdist exclude "
            f"{matching_entries(EXCLUDE, entry)} removes it"
        )


def test_include_entries_are_anchored_or_pathful():
    """The regression guard for the root cause rather than for its symptom.

    A bare basename entry matches at any depth. If someone re-adds
    `"README.md"`, the advertised-paths test above still passes while the
    archive silently narrows — exactly what happened in v0.3.1.
    """
    unanchored = [e for e in INCLUDE if not e.startswith("/") and "/" not in e]
    assert not unanchored, (
        f"sdist include entries {unanchored} are bare basenames and match at "
        "any depth; anchor them with a leading '/'"
    )


def test_declared_readme_is_included_at_the_root():
    readme = PYPROJECT["project"]["readme"]
    assert matching_entries(INCLUDE, readme), (
        f"[project].readme is {readme!r} but no include entry matches it"
    )


# ---------------------------------------------------------------------------
# the wheel does not change
# ---------------------------------------------------------------------------


def test_wheel_target_unchanged():
    """`evals` must not enter the wheel.

    `evals/__init__.py` makes it a *top-level* package named `evals`; putting
    that in every user's site-packages is generic-name squatting. It would not
    even make the documented command work: score_only.py resolves its imports
    against a source tree (`Path(__file__).resolve().parents[2]`), not against
    an installed distribution. The sdist is the right home; this guards the
    decision against a well-meaning future edit.
    """
    assert WHEEL.get("packages") == ["src/papertrace"]

    def _mentions_evals(value) -> bool:
        if isinstance(value, str):
            return "evals" in value
        if isinstance(value, dict):
            return any(_mentions_evals(k) or _mentions_evals(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(_mentions_evals(v) for v in value)
        return False

    assert not _mentions_evals(WHEEL), f"wheel target mentions evals: {WHEEL}"
