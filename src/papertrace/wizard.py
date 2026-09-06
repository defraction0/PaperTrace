"""The guided audit: one question at a time, and the cost stated before it is spent.

Every flag `papertrace run` takes is correct and documented, but a newcomer had
to assemble six decisions from a `--help` screen before anything happened — and
three of the ways a run can fail only surfaced minutes in. This module asks, in
order, and checks the environment *first* so a missing dependency is a sentence
rather than a traceback twenty minutes later.

It adds no capability. Everything here resolves to a `papertrace run` invocation,
which it prints, so a user finishes knowing the command instead of depending on
the wizard to reproduce it.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

from . import config
from .check import ASK_ATTEMPTS, claude_available
from .ingest import _docling_available as docling_available
from .models import _LABEL_GROUP, _expand_label_group, is_references_heading
from .refs import DOI_RE

console = Console()

# only the front matter is read for the paper's own DOI. A reference list is
# full of other papers' DOIs, and picking one up would anchor the literature
# scout to somebody else's work without any error to notice.
FRONT_MATTER_PAGES = 1


def _browser_dirs() -> list[Path]:
    """Where playwright keeps downloaded browsers, per platform."""
    if override := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return [Path(override).expanduser()]
    home = Path.home()
    return [
        home / "Library" / "Caches" / "ms-playwright",       # macOS
        home / ".cache" / "ms-playwright",                    # Linux
        home / "AppData" / "Local" / "ms-playwright",         # Windows
    ]


def _playwright_ready() -> bool:
    """chromium present, not merely the python package.

    `pip install papertrace[png]` gets the library; the browser is a separate
    one-time download, and that gap is where `--png` silently disappoints.

    The browser half is a **filesystem check on purpose**. Asking playwright
    itself means starting its driver subprocess, which leaves "Task was
    destroyed but it is pending!" and a TargetClosedError on stderr every time
    the wizard starts — noise on a screen whose whole job is to reassure a
    first-time user that their setup is fine. Being wrong here costs one
    downgraded option; `render.html_to_png` still fails loudly with the exact
    install command if the guess was optimistic.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return any(
        d.is_dir() and any(d.glob("chromium-*"))
        for d in _browser_dirs()
    )


@dataclass(frozen=True)
class Check:
    """One environment fact, and what to do about it.

    `fatal` separates "this run cannot happen" from "one option is unavailable".
    Conflating them would either block a perfectly good audit over a missing
    screenshot renderer, or let a run start that has no way to judge anything.
    """

    key: str
    ok: bool
    fatal: bool
    label: str
    detail: str = ""
    fix: str = ""


def preflight() -> list[Check]:
    """What is installed, before the user is asked to type anything."""
    return [
        Check(
            key="claude",
            ok=claude_available(),
            fatal=True,
            label="claude CLI (the checker runs on `claude -p`)",
            fix="Install Claude Code and sign in: https://claude.com/claude-code",
        ),
        Check(
            key="ingest",
            ok=docling_available(),
            fatal=False,
            label="layout-aware ingest",
            detail=(
                "docling found — the paper and its cited sources are read as structure"
                if docling_available()
                else "docling is a required dependency but did not import, so this "
                "install is broken: flat-text ingest only, tables linearized and "
                "figures invisible, in the sources as well as the paper"
            ),
            fix="reinstall: pip install --force-reinstall papertrace",
        ),
        Check(
            key="png",
            ok=_playwright_ready(),
            fatal=False,
            label="PNG export of the reports",
            detail="markdown and HTML reports do not depend on this",
            fix="pip install 'papertrace[png]' && playwright install chromium",
        ),
    ]


def clean_path(raw: str) -> Path:
    """Turn whatever the terminal produced into a path.

    Dragging a file into a terminal yields the path quoted, or with its spaces
    backslash-escaped, depending on the shell. A user should not have to know
    that, so all spellings are accepted.
    """
    s = raw.strip()
    for q in ("'", '"'):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            s = s[1:-1]
            break
    s = re.sub(r"\\(.)", r"\1", s)  # unescape \  and friends
    return Path(s.strip()).expanduser()


def detect_doi(pdf: Path) -> str | None:
    """The DOI printed on the paper's own front matter, or None.

    This exists because `--doi` is a *definition* problem: it means the DOI of
    the paper being audited, not of anything it cites, and saying so twice did
    not stop it being misread. Offering the one on page 1 turns the question
    into a yes/no.

    None is a real answer — an unpublished manuscript has none, and inventing
    one would point the literature scout at a different paper entirely.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        import fitz as pymupdf
    try:
        with pymupdf.open(pdf) as doc:
            text = "\n".join(
                doc[i].get_text() for i in range(min(FRONT_MATTER_PAGES, doc.page_count))
            )
    except Exception:  # noqa: BLE001 - an unreadable PDF is "no DOI found"
        return None
    # a short paper's first page reaches its own reference list, and every DOI
    # printed there belongs to a different paper. The page count alone was never
    # the front-matter boundary the comment above claims it is.
    if m := DOI_RE.search(_before_references(text)):
        return m.group(0).rstrip(".,);]")
    return None


def _before_references(text: str) -> str:
    """The text above the reference list, using the ingest boundary rule."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if is_references_heading("text", line):
            return "\n".join(lines[:i])
    return text


def workload(pdf: Path) -> dict:
    """What this audit will cost, counted from the paper itself.

    Citation *places* alone understate it: a claim citing several sources is now
    judged against each one separately, so `[2, 3]` is two model calls, not one.
    Both numbers are reported so the estimate cannot read as smaller than it is.

    Zero places is not "free" — it means the bracketed-numeric style was not
    recognised, which the coverage audit discloses rather than reporting no gaps.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf
    pages, text = 0, ""
    try:
        with pymupdf.open(pdf) as doc:
            pages = doc.page_count
            text = "\n".join(p.get_text() for p in doc)
    except Exception:  # noqa: BLE001 - report what we could read, never guess
        pass

    cut = max(text.rfind("References"), text.rfind("REFERENCES"))
    body = text[:cut] if cut > 0 else text
    groups = [_expand_label_group(m.group(1)) for m in _LABEL_GROUP.finditer(body)]
    labels = set().union(*groups) if groups else set()
    multi = sum(1 for g in groups if len(g) > 1)
    # one extraction call, then one per cited source. Sources are judged in
    # groups, so this is an upper bound on the judging calls, not a promise.
    cited_source_calls = sum(len(g) for g in groups)
    return {
        "pages": pages,
        "places": len(groups),
        "multi": multi,
        "labels": len(labels),
        "model_calls": 1 + cited_source_calls,
        # the retry is real spend: one extraction plus, per judging call, up to
        # ASK_ATTEMPTS attempts. Derived from check.py rather than a local
        # multiplier, so the estimate cannot drift from the policy.
        "model_calls_max": 1 + ASK_ATTEMPTS * cited_source_calls,
        "style_unrecognised": len(groups) == 0,
    }


def equivalent_command(
    *,
    manuscript: Path,
    case: Path,
    doi: str | None,
    png: bool,
    with_scout: bool,
    provided: Path | None,
    email: str | None = None,
) -> str:
    """The `papertrace run` line this session amounts to.

    Printed at the end on purpose: a wizard that hides the CLI leaves its user
    unable to repeat, script or share what they just did — which only holds if
    the line actually runs. Built as argv and joined with `shlex.join`, because
    interpolating a path with a space in it printed a command that split into
    the wrong arguments. `--email` is included for the same reason: without it
    the replay either fails or silently picks up a different saved address.
    """
    argv = ["papertrace", "run", str(manuscript), "-c", str(case)]
    if provided:
        argv += ["--provided", str(provided)]
    if doi:
        argv += ["--doi", doi]
    if email:
        argv += ["--email", email]
    if not with_scout:
        argv.append("--no-scout")
    if png:
        argv.append("--png")
    return shlex.join(argv)


def _suggest_case(pdf: Path) -> str:
    """The folder batch mode would pick, offered for one keystroke.

    Delegated rather than reimplemented: two answers to "where does this audit
    live" is how the wizard and `papertrace run` came to disagree in the first
    place. Imported inside the function — `cli` imports this module.
    """
    from .cli import default_case

    return str(default_case(pdf))


def _ask_paper() -> Path:
    while True:
        raw = Prompt.ask("[bold]Which paper should I check?[/bold]\n  path")
        path = clean_path(raw)
        if not path.exists():
            console.print(f"  [red]No file at {path}[/red] — try again, or drag it in.")
            continue
        if path.suffix.lower() not in (".pdf", ".docx"):
            console.print(f"  [red]{path.suffix or 'that'} is not a PDF or DOCX.[/red]")
            continue
        return path


def _ask_email() -> str:
    if existing := (config.load().get("email") or "").strip():
        console.print(f"  Using saved contact email: [cyan]{existing}[/cyan]")
        return existing
    console.print(
        "\n[bold]Contact email[/bold] — Unpaywall requires one to look up "
        "open-access copies.\n  It is sent to Unpaywall and Crossref with each "
        "lookup, so use an address you are willing to share."
    )
    email = Prompt.ask("  email").strip()
    if Confirm.ask("  Remember it for next time?", default=True):
        console.print(f"  saved to [cyan]{config.save(email=email)}[/cyan]")
    return email


def _ask_doi(pdf: Path) -> tuple[str | None, bool]:
    """Returns (doi, with_scout). The scout needs the paper identified."""
    found = detect_doi(pdf)
    if found:
        console.print(
            f"\n  I found a DOI in the front matter: [cyan]{found}[/cyan]"
            "\n  [dim]It must be this paper's own — a DOI belonging to something it "
            "cites would point the literature scout at the wrong paper.[/dim]"
        )
        # no default: pressing return used to accept whatever was found, and the
        # cost of a wrong yes is a scout anchored to somebody else's paper
        if Confirm.ask("  Is that this paper's own DOI?"):
            return found, True
    else:
        console.print("\n  No DOI on the first page.")
    if not Confirm.ask("  Is this paper already published?", default=False):
        console.print(
            "  [dim]Then the literature scout cannot identify it, so I'll skip "
            "that step. The citation audit is unaffected.[/dim]"
        )
        return None, False
    typed = Prompt.ask("  Paste its DOI (or leave blank to skip the scout)", default="").strip()
    if not typed:
        console.print("  [dim]Skipping the literature scout.[/dim]")
        return None, False
    return typed, True


def run_wizard() -> None:
    """Walk one audit, then hand off to `papertrace run`."""
    if not sys.stdin.isatty():
        console.print(
            "[yellow]The guided flow needs an interactive terminal.[/yellow]\n"
            "Use the flags directly, e.g. "
            "[cyan]papertrace run paper.pdf -c mycase[/cyan] — see "
            "[cyan]papertrace --help[/cyan]."
        )
        raise typer.Exit(2)

    console.print("\n[bold]PaperTrace[/bold] · guided audit\n")
    console.print("[bold]Checking your setup[/bold]")
    checks = preflight()
    for c in checks:
        mark = "[green]✓[/green]" if c.ok else ("[red]✗[/red]" if c.fatal else "[yellow]○[/yellow]")
        console.print(f"  {mark} {c.label}")
        if c.detail:
            console.print(f"      [dim]{c.detail}[/dim]")
        if not c.ok and c.fix:
            console.print(f"      [dim]fix: {c.fix}[/dim]")
    if fatal := [c for c in checks if c.fatal and not c.ok]:
        console.print(
            f"\n[red]Cannot run without: {', '.join(c.label for c in fatal)}.[/red]\n"
            "Nothing has been changed on disk."
        )
        raise typer.Exit(2)
    png_available = next(c.ok for c in checks if c.key == "png")

    console.print()
    paper = _ask_paper()
    w = workload(paper)
    def _n(count: int, noun: str) -> str:
        return f"{count} {noun}" + ("" if count == 1 else "s")

    console.print(
        f"  [green]✓[/green] {_n(w['pages'], 'page')} · "
        f"{_n(w['labels'], 'distinct reference')} cited · "
        f"{_n(w['places'], 'citation place')}"
    )
    if w["style_unrecognised"]:
        console.print(
            "  [yellow]No bracketed numeric citations found.[/yellow] [dim]The coverage "
            "audit reads [1], [2,3], [7-9] only, so it will report "
            "'coverage not audited' rather than zero gaps.[/dim]"
        )

    case = Path(
        Prompt.ask("\n[bold]Where should I keep this audit?[/bold]\n  folder",
                   default=_suggest_case(paper))
    )
    doi, with_scout = _ask_doi(paper)
    email = _ask_email()

    png = False
    if png_available:
        png = Confirm.ask("\n  Also export PNG pictures of the reports?", default=False)

    console.print("\n[bold]Ready.[/bold]")
    console.print(
        "  This makes live requests to Crossref, Unpaywall"
        + (" and Europe PMC" if with_scout else "")
        + f", and about [bold]{w['model_calls']}[/bold] model calls through `claude -p`"
        + (f" — up to [bold]{w['model_calls_max']}[/bold] if calls have to be retried."
           if w["model_calls_max"] != w["model_calls"] else ".")
    )
    if w["multi"]:
        console.print(
            f"  [dim]{w['multi']} of {w['places']} citation places cite several sources, "
            "and each cited source is judged separately.[/dim]"
        )
    console.print("  [dim]That costs money and takes a few minutes.[/dim]")
    if not Confirm.ask("  Start the audit?", default=False):
        console.print("Nothing was run.")
        raise typer.Exit(0)

    cmd = equivalent_command(
        manuscript=paper, case=case, doi=doi, png=png,
        with_scout=with_scout, provided=None, email=email,
    )
    console.print(f"\n[dim]Same thing as one command, for next time:[/dim]\n  [cyan]{cmd}[/cyan]\n")

    from .cli import run as run_cmd

    # every parameter `run` declares is named here, including the ones taking
    # their default: `run` is a Typer command, so an omitted argument arrives as
    # an OptionInfo sentinel rather than the default the help screen shows.
    # `formats=None` means report.md alone — the PNG answer above already pulls
    # in the HTML looks when it needs them, since a PNG is a shot of one.
    run_cmd(
        manuscript=paper, case=case, provided=None, email=email, model=None,
        png=png, backend="auto", with_scout=with_scout, doi=doi, formats=None,
    )


__all__ = [
    "Check",
    "clean_path",
    "detect_doi",
    "equivalent_command",
    "preflight",
    "run_wizard",
    "workload",
]
