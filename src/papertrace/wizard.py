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
from .ask import ASK_ATTEMPTS, claude_available
from .brand import BANNER
from .ingest import _docling_available as docling_available
from .ingest import resolve_backend
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
    # `run_wizard` always requests `backend="auto"`, which `resolve_backend`
    # turns into docling when it is importable and pymupdf otherwise. On a
    # pymupdf run the flat second reading is skipped as identical to the first
    # (`cli._needs_flat_reading`'s own predicate) — so the reference-list call
    # is never attempted either, and the estimate must not promise a call this
    # run's own backend will not produce.
    llm_call = 1 if resolve_backend("auto") != "pymupdf" else 0
    return {
        "pages": pages,
        "places": len(groups),
        "multi": multi,
        "labels": len(labels),
        # how many sources each citation place cites, in reading order — what
        # `apply_limits` cuts to the first N places when the claims are capped
        "group_sizes": [len(g) for g in groups],
        # the reference-list reading, 0 or 1. Kept as its own key rather than
        # only folded into the two totals because `apply_limits` has to add it
        # back: a limit cuts claims and sources, and this call reads the
        # bibliography, so no limit can remove it.
        "reflist_calls": llm_call,
        # one reference-list reading (when the backend leaves a second text to
        # check it against), one extraction call, then one per cited source.
        # The reference-list call gets no retry — `reflist.propose` asks once
        # and reports what it got — so it adds exactly `llm_call` to the base
        # figure and to the ceiling, on top of the retried figure below rather
        # than inside it.
        "model_calls": 1 + llm_call + cited_source_calls,
        # the retry is real spend: ASK_ATTEMPTS attempts for extraction, and
        # ASK_ATTEMPTS attempts per judging call. Derived from ask.py rather
        # than a local multiplier, so the estimate cannot drift from the policy.
        "model_calls_max": llm_call + ASK_ATTEMPTS * (1 + cited_source_calls),
        "style_unrecognised": len(groups) == 0,
    }


def apply_limits(w: dict, *, max_claims: int | None, max_sources: int | None) -> dict:
    """The cost estimate with the limits applied — still an upper bound.

    The first N claims can only ever cost the first N citation places, and at
    most N sources obtained means at most N judging calls, one per document.
    Neither figure may exceed the unlimited one, and the retry ceiling is
    derived the way `workload` derives it, so the two cannot drift.

    The reference-list reading survives every limit: it reads the bibliography,
    not the claims, so `--max-claims` and `--max-sources` cannot remove it.
    Dropping it here would advertise a ceiling the run then exceeds, which is
    the one thing a cost estimate may never do.
    """
    sizes = list(w.get("group_sizes") or [])
    if max_claims is not None:
        sizes = sizes[:max_claims]
    judging = sum(sizes)
    if max_sources is not None:
        judging = min(judging, max_sources)
    reflist = w.get("reflist_calls") or 0
    return {
        **w,
        "model_calls": 1 + reflist + judging,
        # same shape as `workload`: the reference-list call is not retried, so
        # it adds to the ceiling rather than being multiplied into it
        "model_calls_max": reflist + ASK_ATTEMPTS * (1 + judging),
    }


def parse_limit(raw: str) -> int | None:
    """A limit as typed: a positive whole number, or None for blank.

    Zero is not a limit anyone means and "five" is not a number; both raise, so
    the question is asked again rather than quietly read as no limit.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(
            f"{raw!r} is not a whole number of at least 1 — leave it blank for no limit"
        )
    return int(raw)


def supplement_workload(provided_dir: Path | None, supplement: list[Path]) -> int:
    """How many extra judging calls the supplements on hand will cost.

    One per supplementary *document*, not per claim: `check_claims` groups every
    claim for a document into a single call. An upper bound like the rest of
    this estimate — a supplement whose reference no claim cites is never opened,
    and one whose article turns out to be unavailable is set aside entirely.

    Counted from the folder rather than from the manifest because this runs
    before `refs` does. The alternative is to state a price that leaves the
    supplements out, and ask.py's own comment on ASK_ATTEMPTS is the rule
    here: a cost ceiling that gets exceeded is a false promise about money.
    """
    from .refs import _SUPPLEMENT_RE

    n = len(supplement or [])
    if provided_dir and provided_dir.is_dir():
        n += sum(1 for p in provided_dir.glob("*.pdf") if _SUPPLEMENT_RE.search(p.stem))
    return n


def equivalent_command(
    *,
    manuscript: Path,
    case: Path,
    doi: str | None,
    png: bool,
    with_scout: bool,
    provided: Path | None,
    email: str | None = None,
    supplement: list[Path] | None = None,
    formats: list[str] | None = None,
    max_claims: int | None = None,
    max_sources: int | None = None,
) -> str:
    """The `papertrace run` line this session amounts to.

    Printed at the end on purpose: a wizard that hides the CLI leaves its user
    unable to repeat, script or share what they just did — which only holds if
    the line actually runs. Built as argv and joined with `shlex.join`, because
    interpolating a path with a space in it printed a command that split into
    the wrong arguments. `--email` is included for the same reason: without it
    the replay either fails or silently picks up a different saved address —
    and so is `-f`, or the replay would drop the page the user reviewed in, and
    so are the limits, or the replay would run (and bill) the whole paper.
    """
    argv = ["papertrace", "run", str(manuscript), "-c", str(case)]
    if provided:
        argv += ["--provided", str(provided)]
    for s in supplement or []:
        argv += ["--supplement", str(s)]
    if doi:
        argv += ["--doi", doi]
    if email:
        argv += ["--email", email]
    for look in formats or []:
        argv += ["-f", look]
    if max_claims:
        argv += ["--max-claims", str(max_claims)]
    if max_sources:
        argv += ["--max-sources", str(max_sources)]
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


def _ask_case(paper: Path) -> Path:
    """Where the audit is written — the one path answer that creates, not finds.

    Routed through `clean_path` like every other path prompt. It was not, and a
    quoted answer — what Finder's drag-and-drop produces for any path holding a
    space — became a *relative* name starting with a literal quote, so the case
    landed under the cwd while every printed line named an absolute folder that
    did not exist. The other prompts were immune only because they check
    `.exists()`; nothing can contradict a folder that is about to be created.

    `default_case` already refused the working directory as a case location for
    this reason ("appears wherever the user happened to be standing"), so a
    relative answer here is worth a word rather than a silent accept: it is
    resolved and the resolution is printed. A relative answer is legitimate —
    `-c demo_case` is in the README — but it is also what a mangled path
    degrades into, and the resolved line is the one place the difference shows
    before any money is spent.
    """
    while True:
        raw = Prompt.ask(
            "\n[bold]Where should I keep this audit?[/bold]\n  folder",
            default=_suggest_case(paper),
        )
        case = clean_path(raw)
        if case.is_absolute():
            return case
        if case == Path("."):
            # `default_case` refuses the working directory outright, and an
            # answer of whitespace arrives here as `.` — the one relative answer
            # that names no folder of its own
            console.print(
                "  [red]That is the folder you are standing in.[/red] An audit needs "
                "its own,\n  so it cannot be mistaken for the rest of the directory."
            )
            continue
        # soft_wrap: a path broken across two lines is a path a reader skims
        # past, which is the failure this print exists to prevent
        console.print(f"  [dim]relative — writing the audit to[/dim] {case.resolve()}", soft_wrap=True)
        return case.resolve()


def _ask_sources(case: Path) -> Path | None:
    """The folder of reference PDFs the user already holds.

    Never asked before this: `run_wizard` passed `provided=None`, so the guided
    path could not reach a flag the CLI has had all along, and a first-time user
    following the wizard silently got open-access retrieval only.
    """
    default = case / "sources"
    # the folder's contents answer this better than any fixed default can: a
    # user whose PDFs are already sitting there should not skip them by pressing
    # return, and a user with none should not be handed a path prompt at all
    waiting = default.is_dir() and any(default.glob("*.pdf"))
    if not Confirm.ask(
        "\n[bold]Do you have any of the cited PDFs already?[/bold]"
        + (f"\n  [dim]{default} looks like it holds some.[/dim]" if waiting else ""),
        default=waiting,
    ):
        return None
    console.print(
        "  [dim]Point me at a folder. Names do not have to be tidy — each PDF is "
        "identified\n  by its own title or DOI, so a publisher download works as is. A "
        "file named for\n  its reference ([cyan]pyrros-2023.pdf[/cyan]) is taken at your "
        "word instead.\n  Supplementary material for a cited paper goes in the same "
        "folder; each is\n  judged as its own document, and anything I cannot place I "
        "will name.[/dim]"
    )
    raw = Prompt.ask("  folder", default=str(default)).strip()
    if not raw:
        return None
    path = clean_path(raw)
    if not path.is_dir():
        console.print(f"  [dim]No folder at {path} — continuing without one.[/dim]")
        return None
    n = len(list(path.glob("*.pdf")))
    console.print(f"  [green]✓[/green] {n} PDF{'' if n == 1 else 's'} in {path}")
    return path


def _ask_supplements() -> list[Path]:
    """Supplementary material belonging to the paper under audit.

    Asked separately because the sources folder is matched against *reference*
    slugs, and the audited paper has none for a filename to key on.
    """
    if not Confirm.ask(
        "\n[bold]Does this paper have supplementary material of its own?[/bold]",
        default=False,
    ):
        return []
    console.print(
        "  [dim]A claim that points at Table S3 or eFigure 2 is read against these; "
        "with\n  nothing supplied it is reported as not retrieved, never guessed.[/dim]"
    )
    out: list[Path] = []
    while True:
        raw = Prompt.ask(
            "  path (blank when done)" if out else "  path (blank to skip)", default=""
        ).strip()
        if not raw:
            return out
        path = clean_path(raw)
        if not path.is_file():
            console.print(f"  [red]No file at {path}[/red] — try again, or drag it in.")
            continue
        out.append(path)
        console.print(f"  [green]✓[/green] {path.name}")


def _ask_limit(question: str) -> int | None:
    while True:
        try:
            return parse_limit(Prompt.ask(question, default=""))
        except ValueError as e:
            console.print(f"  [red]{e}[/red]")


def _ask_limits() -> tuple[int | None, int | None]:
    """A slice of the audit, on request: the first N claims, at most N sources.

    Both blank by default. Offered because a first pass on five claims costs a
    fraction of the paper and answers "is this worth the whole run" — and
    because whatever is left out is stated on the report's last lines, so a
    limited run cannot be mistaken for a smaller paper.
    """
    console.print(
        "\n[bold]Limit this audit?[/bold] [dim]Both optional. A first pass on a few claims "
        "costs less, and every report ends by stating what was left out.[/dim]"
    )
    max_claims = _ask_limit("  Check only the first N claims — blank for all")
    max_sources = _ask_limit("  Obtain at most N cited sources — blank for all")
    return max_claims, max_sources


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

    console.print(BANNER)
    console.print("[dim]guided audit — one question at a time[/dim]\n")
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

    case = _ask_case(paper)
    provided = _ask_sources(case)
    supplement = _ask_supplements()
    doi, with_scout = _ask_doi(paper)
    email = _ask_email()

    # the viewer is the page to review the audit in; report.md is written either
    # way. Offered here, default yes, because the flag is the one thing a
    # newcomer would not know to ask for — and the wizard exists for newcomers
    viewer = Confirm.ask(
        "\n  Also write the interactive viewer (report_viewer.html) beside report.md?",
        default=True,
    )
    formats = ["viewer"] if viewer else None

    png = False
    if png_available:
        png = Confirm.ask("  Also export PNG pictures of the reports?", default=False)

    # asked last, next to the price they change
    max_claims, max_sources = _ask_limits()

    # each supplement is one more document, so one more judging call. Folded in
    # here rather than in `workload()` because it is not known until the sources
    # folder has been named, which happens after the paper is measured.
    extra = supplement_workload(provided, supplement)
    bounded = apply_limits(w, max_claims=max_claims, max_sources=max_sources)
    calls = bounded["model_calls"] + extra
    calls_max = bounded["model_calls_max"] + ASK_ATTEMPTS * extra

    console.print("\n[bold]Ready.[/bold]")
    console.print(
        "  This makes live requests to Crossref, Unpaywall"
        + (" and Europe PMC" if with_scout else "")
        + f", and about [bold]{calls}[/bold] model calls through `claude -p`"
        + (f" — up to [bold]{calls_max}[/bold] if calls have to be retried."
           if calls_max != calls else ".")
    )
    if max_claims or max_sources:
        limits = [
            f"the first {_n(max_claims, 'claim')}" if max_claims else "",
            f"at most {_n(max_sources, 'cited source')}" if max_sources else "",
        ]
        console.print(
            f"  [dim]Limited on request to {' and '.join(p for p in limits if p)} — the "
            "report ends by stating what was left out.[/dim]"
        )
    if extra:
        console.print(
            f"  [dim]{_n(extra, 'supplementary document')} included — each is judged "
            "separately from the article it accompanies.[/dim]"
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
        with_scout=with_scout, provided=provided, email=email, supplement=supplement,
        formats=formats, max_claims=max_claims, max_sources=max_sources,
    )
    console.print(f"\n[dim]Same thing as one command, for next time:[/dim]\n  [cyan]{cmd}[/cyan]\n")

    from .cli import run as run_cmd

    # every parameter `run` declares is named here, including the ones taking
    # their default: `run` is a Typer command, so an omitted argument arrives as
    # an OptionInfo sentinel rather than the default the help screen shows.
    # `formats=None` means report.md alone — the PNG answer above already pulls
    # in the HTML looks when it needs them, since a PNG is a shot of one.
    run_cmd(
        manuscript=paper, case=case, provided=provided, email=email, model=None,
        png=png, backend="auto", with_scout=with_scout, doi=doi, formats=formats,
        supplement=supplement, llm_refs=True,
        max_claims=max_claims, max_sources=max_sources,
    )


__all__ = [
    "Check",
    "apply_limits",
    "clean_path",
    "detect_doi",
    "equivalent_command",
    "parse_limit",
    "preflight",
    "run_wizard",
    "supplement_workload",
    "workload",
]
