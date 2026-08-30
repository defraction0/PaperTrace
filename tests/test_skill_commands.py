"""Every `papertrace` command a skill documents must actually parse.

A skill is documentation an agent executes verbatim, so a stale flag in one is a
broken command rather than a typo. Four were: `refs ... -o case/` (`refs` has no
`-o`), `report case/` and `highlight case/ --claim <id>` (neither takes a
positional argument), and a prose `refs --parse-only` with no manuscript.
"""

import re
import shlex
import sys
from pathlib import Path

import pytest
import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402

_SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"
_CMD = re.compile(r"papertrace [^`\n]+")


def _skill_command_lines() -> list[tuple[str, str]]:
    """(skill file, command) for every `papertrace ...` line in every skill.

    Every skill, not just `review/`: scanning one file is why a fourth broken
    command — `papertrace highlight case/ --claim <id>` in `fact-check/` — sat
    there while the other three were being fixed.
    """
    if not _SKILLS.is_dir():  # an installed wheel carries no skills
        pytest.skip("repo-only directory")
    out: list[tuple[str, str]] = []
    for md in sorted(_SKILLS.rglob("*.md")):
        rel = str(md.relative_to(_SKILLS))
        out += [(rel, m.group(0).strip().rstrip(").,")) for m in _CMD.finditer(md.read_text())]
    return out


def test_every_papertrace_command_in_every_skill_parses(tmp_path):
    """The skill is documentation an agent executes verbatim, so a stale flag in
    it is a broken command, not a typo. Two were: `refs ... -o case/` (refs has
    no -o) and `report case/` (report has no positional argument).

    Parsed with `make_context`, which validates arguments without invoking
    anything. Appending `--help` instead would not do: `--help` is eager, fires
    before click reports an unexpected extra argument, and would have called
    `report case/` fine — masking one of the two bugs this test exists for.
    """
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4\n")
    sources = tmp_path / "sources"
    sources.mkdir()

    def literal(token: str) -> str:
        if not (token.startswith("<") and token.endswith(">")):
            return token
        name = token[1:-1].lower()
        if "pdf" in name or "paper" in name or "manuscript" in name:
            return str(paper)
        if "dir" in name or "folder" in name or "source" in name or "case" in name:
            return str(sources)
        return "1"  # <id>, <slug> and friends: any scalar will do

    group = typer.main.get_command(cli.app)
    # `group.context_class`, not `click.Context`: typer 0.27 vendors click and
    # declares no dependency on it, so a top-level `import click` is a
    # collection error wherever only typer is installed — and a collection
    # error interrupts the whole pytest session, losing every test in it.
    root = group.context_class(group, info_name="papertrace")
    lines = _skill_command_lines()
    assert lines, "no papertrace commands found — did the extraction regex rot?"

    # every broken line at once: stopping at the first would have hidden the
    # second bug behind the first for as long as one stayed unfixed
    broken = []
    for where, line in lines:
        argv = [literal(t) for t in shlex.split(line)[1:]]
        sub = group.get_command(root, argv[0]) if argv else None
        if sub is None:
            broken.append(f"{where}: {line!r}: no such command")
            continue
        try:
            # typer 0.26 raises its own vendored click exceptions, so catch broadly:
            # any failure to parse is the documented line being unrunnable
            with sub.make_context(argv[0], argv[1:], parent=root):
                pass
        except SystemExit as e:
            broken.append(f"{where}: {line!r}: exits during parsing ({e.code})")
        except Exception as e:  # noqa: BLE001 - the failure is the finding
            broken.append(f"{where}: {line!r}: {type(e).__name__}: {e}")
    assert not broken, "documented commands that do not parse:\n  " + "\n  ".join(broken)
