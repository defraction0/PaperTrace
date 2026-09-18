"""PaperTrace CLI — batch mode.

`papertrace run` drives the whole pipeline; the individual commands
exist so each step can be run, inspected and re-run on its own. Interactive
reviewing lives in the Claude Code skills (`/review`), which call these same
commands.
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.prompt import Prompt

from . import __version__
from .brand import BANNER
from .models import ClaimResult, RefEntry, RefManifest, RunResults, manuscript_fingerprint

if TYPE_CHECKING:
    # not at module level: `.reflist` pulls in `.refs`, which this module only
    # ever imports lazily inside `_refs_pipeline` to keep `httpx` and the
    # network-facing stack out of every other command's import cost
    from .reflist import ReflistProvenance

app = typer.Typer(add_completion=False, rich_markup_mode="rich", invoke_without_command=True)
console = Console()

STATUS_MARK = {
    "retrieved": "[green]✓[/green]",
    "provided": "[green]✓[/green]",
    "paywalled": "[yellow]⚠[/yellow]",
    "mismatch": "[yellow]⚠[/yellow]",
    "no_doi": "[yellow]⚠[/yellow]",
    "unpublished": "[yellow]⚠[/yellow]",
    "error": "[red]✗[/red]",
}


CASE_NAME_MAX = 60  # a folder name, not a title — some journals' stems run long


def default_case(manuscript: Path) -> Path:
    """Where this paper's audit lives when `-c` was not given: beside the paper,
    named after it.

    Not the working directory, for two reasons a real first run hit at once. A
    folder literally named `case` is the *same* folder for every paper, so a
    second audit lands on the first unless the user remembers `-c`. And it
    appears wherever the user happened to be standing — for that user, the root
    of a git clone, which `.gitignore` covers only under the name `case/`. The
    paper's own folder is the one location that is stable across invocations, so
    a re-run finds its case again without a flag.
    """
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", manuscript.stem).strip("-.")[:CASE_NAME_MAX]
    parent = manuscript.parent
    if not os.access(parent, os.W_OK):
        # a read-only volume (a mounted share, an email attachment folder): say
        # where the audit went instead, never fail for want of a default
        console.print(
            f"[yellow]⚠ {parent}/ is not writable, so the audit cannot sit beside the "
            f"paper — keeping it in {Path.cwd()}/ instead.[/yellow]"
        )
        parent = Path.cwd()
    return parent / (name or "case")


def _sibling_case(case: Path) -> Path:
    """`<name>-2`, `-3`, … — the first that does not exist yet."""
    n = 2
    while (candidate := case.with_name(f"{case.name}-{n}")).exists():
        n += 1
    return candidate


def _open_case(case: Path) -> Path:
    """Create the case folder, ignoring itself.

    `.gitignore` blocks `case/`, `cases/` and `demo_case/` by name; a folder
    named after a manuscript matches none of them, so the guardrail travels
    inside the folder rather than depending on where it was created.
    """
    case.mkdir(parents=True, exist_ok=True)
    marker = case / ".gitignore"
    if not marker.exists():
        marker.write_text("*\n")
    return case


def _stage_case(case: Path | None) -> Path:
    """Which case folder a stage that has no manuscript works on.

    `check`, `highlight`, `report` and `scout` take no paper, so they have no
    name to derive and there is nothing honest to default to: picking one of
    several audits in the current directory is exactly the mixing `_guard_case`
    exists to prevent. `case/` is still accepted when it is there, because it
    was the default through 0.4.0 — anything else is refused with the folders
    that do look like audits, rather than guessed.
    """
    if case is not None:
        return case
    if (legacy := Path("case")).is_dir():
        return legacy
    found = sorted(
        p.name for p in Path().iterdir() if p.is_dir() and (p / "refs_manifest.json").exists()
    )
    hint = (
        "  audits in this folder: " + ", ".join(f"[cyan]-c {n}[/cyan]" for n in found[:8])
        if found
        else "  no case folder found in the current directory — "
        "`papertrace run <paper.pdf>` makes one."
    )
    console.print(f"[red]which audit? this step needs [bold]-c <case folder>[/bold].[/red]\n{hint}")
    raise typer.Exit(2)


def _case_conflict(case: Path, manuscript: Path) -> tuple[str | None, str]:
    """A case folder belongs to one paper. Returns the previous paper's name
    when `case` already holds an audit of a different one (else None), and what
    that answer rests on: "content" (hashes compared), "name" (a pre-hash
    manifest, so only the file name could be compared) or "empty" (no manifest).
    """
    marker = case / "refs_manifest.json"
    if not marker.exists():
        return None, "empty"
    previous = RefManifest.from_json(marker)
    if previous.manuscript_sha256:
        same = previous.manuscript_sha256 == manuscript_fingerprint(manuscript)
        return (None if same else previous.manuscript), "content"
    # a legacy manifest genuinely holds no better information than the name
    same = previous.manuscript == manuscript.name
    return (None if same else previous.manuscript), "name"


def _manuscript_slot_owner(out: Path) -> Path | None:
    """The case folder whose manuscript slot `out` is, or None.

    `<case>/ingest/manuscript` is the one output path that stands for the
    audited paper itself. Recognised by shape rather than by flag, so `--out`
    cannot walk in behind `-c`'s back.
    """
    out = Path(out)
    if out.name != "manuscript" or out.parent.name != "ingest":
        return None
    return out.parent.parent


def _guard_case(case: Path, manuscript: Path) -> str:
    """Refuse a case that holds another paper; return what that rested on.

    The basis matters to the caller: on "name" the folder's cached artifacts may
    have come from a different file, so anything derived from them has to be
    regenerated rather than trusted.
    """
    previous, basis = _case_conflict(case, manuscript)
    if previous:
        # without this clause the message is baffling: it names the same file
        # name back at you as if it were a different paper
        collision = " — same file name, different file" if previous == manuscript.name else ""
        console.print(
            f"[red]case folder [bold]{case}[/bold] already holds an audit of "
            f"[bold]{previous}[/bold]{collision}.[/red]\n"
            f"One case per paper — give this one its own, e.g. "
            f"[cyan]-c {manuscript.stem}[/cyan], or delete [cyan]{case}/[/cyan] "
            f"to reuse the name. (Re-running the [i]same[/i] paper in its case is fine.)"
        )
        raise typer.Exit(2)
    if basis == "name":
        # warn, never hard-fail: refusing a legacy case would be equally
        # uninformed and less usable, and `refs` re-ingests to make it true
        console.print(
            "[yellow]⚠ this case folder predates content hashing, so its identity is "
            "unverified — only the file name was compared, and two different papers "
            "are routinely both called the same thing. The paper is re-read from "
            "scratch rather than trusted from cache.[/yellow]"
        )
    return basis


def _resolve_case(case: Path | None, manuscript: Path) -> Path:
    """The case folder this invocation works in, asking only when it chose the name.

    An explicit `-c` is returned untouched — including when it already holds
    this paper, which is a legitimate re-run and what the guard's own message
    tells people to do. The question is put only for a *derived* folder, where
    the tool picked the name and the user has no reason to expect a collision.
    """
    if case is not None:
        return case
    case = default_case(manuscript)
    if not (case / "refs_manifest.json").exists():
        # an absent or half-ingested folder holds no audit, so there is nothing
        # to amend and nothing to lose — only the name is worth stating
        console.print(f"[dim]case folder: {case}/ — named after the paper; -c chooses another[/dim]")
        return case
    previous, _ = _case_conflict(case, manuscript)
    if previous:
        return case  # a different paper: `_guard_case` refuses it, and says why
    console.print(f"[yellow]{case}/ already holds an audit of this paper.[/yellow]")
    fresh = _sibling_case(case)
    if not sys.stdin.isatty():
        # A pipe, a cron job or CI has nobody to answer, and must never sit on
        # stdin. Amend is the documented choice: it is what re-running the same
        # paper did through 0.4.0, it deletes nothing, and it keeps the report's
        # path predictable — `fresh` would move the output somewhere the caller
        # never named.
        console.print(f"  [dim]no terminal to ask, so amending {case}/ — pass -c to choose[/dim]")
        return case
    console.print(
        f"  [bold]amend[/bold]  reuse it — references are resolved again, so source PDFs "
        f"you have added since are picked up\n"
        f"  [bold]fresh[/bold]  audit this paper from scratch in {fresh}/, leaving "
        f"{case}/ untouched"
    )
    answer = Prompt.ask("  amend or fresh?", choices=["amend", "fresh"], default="amend")
    return case if answer == "amend" else fresh


def _verdict_line(c: dict[str, int]) -> str:
    """The one-line tally, built so a reader's own arithmetic works.

    Every bucket that is non-zero is printed. `not_addressed` was added to the
    three report templates and missed here, so a real run announced 31 of its 34
    claims — the console is the surface read first, and a tally that does not add
    up is the same class of defect as a report that does not.

    Zero-valued optional buckets stay out: an ordinary audit must not grow empty
    columns for verdicts it never produced.
    """
    parts = [
        f"[green]● {c['supported']} supported[/green]",
        f"[yellow]● {c['partial']} partial[/yellow]",
        f"[red]● {c['contradicted']} contradicted[/red]",
    ]
    if c.get("not_addressed"):
        parts.append(f"[yellow]◌ {c['not_addressed']} does not address[/yellow]")
    parts.append(f"[dim]○ {c['not_retrieved']} not retrieved[/dim]")
    if c.get("unchecked"):
        parts.append(f"[red]⚠ {c['unchecked']} unchecked (check failed)[/red]")
    return "\n[bold]verdicts[/bold]  " + "   ".join(parts)


# glyphs match _verdict_line, so the running display and the tally read alike
_JUDGEMENT_MARK = {
    "supported": "[green]●[/green]",
    "partial": "[yellow]●[/yellow]",
    "contradicted": "[red]●[/red]",
    "not_addressed": "[yellow]◌[/yellow]",
    "unchecked": "[red]✗[/red]",
}


def _tick_marks(slug: str, group: list[ClaimResult]) -> str:
    """One glyph per claim in a source's group, from *that source's* judgement.

    Never `c.verdict`: progress fires per group, while `apply_headline()` runs
    only after every group, so mid-run the field still holds its default
    `not_retrieved` — whose glyph is the one the final tally uses for a source
    that was never obtained. The demo judged 2 supported and 2 contradicted
    while this line printed `○ ○ ○ ○`.
    """
    marks = []
    for c in group:
        j = next((j for j in c.judgements if j.source_slug == slug), None)
        marks.append(_JUDGEMENT_MARK.get(j.verdict, "[dim]·[/dim]") if j else "[dim]·[/dim]")
    return " ".join(marks)


def _provenance_line(converter: str) -> str:
    """Which backend read the manuscript, and how the sources were read.

    Both halves matter. The manuscript's backend decides whether tables and
    figures exist at all, and the cited sources are now read with the *same*
    backend — so the one name covers both, which is exactly why it has to say
    so. This line used to promise the opposite ("sources are always read as
    flat text"), and a stale reassurance is worse than none.
    """
    flat = converter.startswith("pymupdf")
    manuscript = (
        f"[yellow]{converter} — flat text, tables linearized[/yellow]"
        if flat
        else f"[cyan]{converter}[/cyan] — layout-aware"
    )
    return (
        f"  read with: {manuscript}\n"
        f"  [dim]cited sources are read with the same backend — the report names any "
        f"that fell back to flat text[/dim]"
    )


def _email(cli_value: str | None) -> str:
    # MANUSCRIPTAGENT_EMAIL is honored as a fallback for pre-rename setups
    from .config import load as _load_config

    email = (
        cli_value
        or os.environ.get("PAPERTRACE_EMAIL", "")
        or os.environ.get("MANUSCRIPTAGENT_EMAIL", "")
        # last, so an explicit flag or env var always wins over a saved default
        or (_load_config().get("email") or "")
    ).strip()
    if not email:
        console.print(
            "[yellow]No contact email set — Unpaywall requires one.[/yellow]\n"
            "Pass [cyan]--email you@example.org[/cyan], set "
            "[cyan]PAPERTRACE_EMAIL[/cyan], or run [cyan]papertrace[/cyan] "
            "once to save it."
        )
        raise typer.Exit(2)
    return email


def _version(value: bool) -> None:
    """Print the installed version and stop.

    Read from `papertrace.__version__`, which `docs/RELEASING.md` names as the
    version's one home — a literal here would drift at the next release and
    answer confidently wrong, which is the failure this codebase exists to
    refuse.
    """
    if value:
        console.print(f"papertrace {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-V", callback=_version, is_eager=True,
        help="Print the installed version and exit",
    ),
) -> None:
    """Fact-check a paper's citations against the actual cited sources.

    New here? Run [bold]papertrace[/bold] with no arguments and answer the
    questions — it checks your setup, asks what it needs, and prints the
    equivalent one-line command when it is done.
    """
    if ctx.invoked_subcommand is not None:
        return
    # A bare `papertrace` is what a first-time user types. Walk them through it
    # when there is somebody there to answer; print help when there is not, so
    # a pipe or a CI job can never sit waiting on stdin.
    if sys.stdin.isatty():
        from .wizard import run_wizard

        run_wizard()
        raise typer.Exit(0)
    console.print(ctx.get_help())
    raise typer.Exit(0)


@app.command(rich_help_panel="Start here")
def start() -> None:
    """Guided audit — asks for the paper, the DOI and your email, one at a time."""
    from .wizard import run_wizard

    run_wizard()


@app.command(rich_help_panel="Utilities")
def init(
    case: Path = typer.Argument(None, help="Case folder to create (default: ./case)"),
    manuscript: Path = typer.Option(
        None, "--for", exists=True,
        help="Name the folder the way `run`/`refs` would for this paper, so a plain "
        "follow-up run finds it on its own instead of leaving ./case/ orphaned",
    ),
) -> None:
    """Create a case folder skeleton (gitignored by design — keep manuscripts local)."""
    # an explicit folder name always wins; --for only fills in what an
    # unnamed default would otherwise have to guess
    if case is None:
        case = default_case(manuscript) if manuscript else Path("case")
    _open_case(case)
    for sub in ("sources", "form", "ingest", "out/evidence"):
        (case / sub).mkdir(parents=True, exist_ok=True)
    console.print(BANNER)
    console.print(f"case folder ready: [cyan]{case}/[/cyan]")
    console.print("  put reference PDFs you already have into [cyan]sources/[/cyan]")
    console.print("  put your questions or form-field screenshots into [cyan]form/[/cyan]")
    if manuscript:
        console.print(
            f"  [dim]papertrace run {manuscript} will find this folder automatically[/dim]"
        )
    else:
        # `run` and `refs` name their own folder after the paper, so a hand-made
        # one is only used if it is passed - saying so here beats orphaned sources/
        console.print(f"  [dim]hand this folder to every step: [cyan]-c {case}[/cyan][/dim]")


def _ingest_pipeline(
    *,
    pdf: Path,
    out: Path | None = None,
    case: Path | None = None,
    backend: str = "auto",
) -> None:
    """PDF → clean.md + annotated.md + source_map.json (page + bbox provenance).

    Keyword-only and plain-default on purpose: `ingest()` below is a Typer
    command, and Typer's declared defaults are `OptionInfo` objects rather than
    the values the help screen shows — calling it directly (as `run()` and the
    tests do) with a shifted or missing argument used to take that sentinel as
    the value. This function is what they actually call; `ingest()` is a thin
    CLI adapter over it.
    """
    from .ingest import ingest_pdf

    # -c means the same thing here as in every other subcommand; `papertrace
    # ingest -c foo` used to fail with "No such option: -c" while its
    # neighbours all took it. --out stays authoritative and unchanged.
    out = out or (case or default_case(pdf)) / "ingest" / pdf.stem
    # the guard is about the manuscript SLOT, not the folder. A cited source
    # ingested into <case>/ingest/<slug> is not the audited paper and must stay
    # ingestable — `check` does exactly that. But <case>/ingest/manuscript is
    # what `refs` filled and `coverage_audit` reads, so a different paper
    # landing there is the mixing `_guard_case` exists to prevent, reached by a
    # command that never asked it.
    if (owner := _manuscript_slot_owner(out)) is not None:
        _guard_case(owner, pdf)
    smap = ingest_pdf(pdf, out, backend=backend)
    by_type = {t: sum(1 for b in smap.blocks if b.type == t) for t in
               ("sectionheader", "text", "table", "picture", "list")}
    parts = " · ".join(f"{n} {t}" for t, n in by_type.items() if n)
    console.print(
        f"[green]✓[/green] {pdf.name} → {out}/ · {smap.pages} pages · "
        f"converter [cyan]{smap.converter}[/cyan] · {parts}"
    )
    if smap.converter == "pymupdf":
        from .ingest import _docling_available

        # WHY it was flat text, not just that it was. Advising an install to
        # somebody who already has docling installed reads as a broken tool and
        # sends them to fix the wrong thing.
        why = (
            "docling is installed, so this was a --backend choice"
            if _docling_available()
            # rich eats [docling] as a style tag - escaping it is what makes the
            # instruction say 'papertrace[docling]' instead of 'papertrace'
            else r"install the layout backend: pip install 'papertrace\[docling]'"
        )
        console.print(
            "  [yellow]⚠ flat-text ingest — tables are linearized and figures "
            f"invisible. {why}[/yellow]"
        )


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def ingest(
    pdf: Path = typer.Argument(..., exists=True, help="PDF to convert"),
    out: Path = typer.Option(None, "--out", "-o", help="Output dir (default <case>/ingest/<stem>)"),
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder; writes <case>/ingest/<stem>. Ignored when --out is given",
    ),
    backend: str = typer.Option("auto", "--backend", help="auto | docling | pymupdf"),
) -> None:
    """PDF → clean.md + annotated.md + source_map.json (page + bbox provenance)."""
    _ingest_pipeline(pdf=pdf, out=out, case=case, backend=backend)


def _body_citation_labels(smap, citation_labels, is_references_heading) -> set[str]:
    """The `[N]` markers the manuscript's body actually cites.

    Read from the source map and stopped at the bibliography, matching what
    `coverage_audit` counts — the two readings are only worth comparing because
    they come from one rule in `models.py`. Passed its two functions rather than
    importing them, so this stays a pure function of the map.

    Table blocks are skipped: a 95% CI column like `[51, 77]` matches the same
    bracket-and-comma syntax as a citation group `[7,8]`, and a table's own
    numbers are never citations.
    """
    body: list[str] = []
    for b in smap.blocks:
        if is_references_heading(b.type, b.text):
            break
        if b.type != "table":
            body.append(b.text)
    return citation_labels("\n".join(body))


def _interactive() -> bool:
    """May this run stop and ask a person something?

    Both streams, and not under CI. `stdin.isatty()` alone is not the gate: CI
    runners frequently allocate a tty, and a prompt in CI is a hung build rather
    than a question. `stdout` is checked too because a run whose output is being
    piped into a file has a reader who is not watching.
    """
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("CI")


_DISAGREEMENT_NAME = "reference_disagreement.md"
# How far past the label to keep quoting. No lower bound needed: the window
# opens at the PREVIOUS line break, whatever that costs, so it needs no constant.
_SPAN_CHARS = 600


def _label_span(text: str, label: str) -> str:
    """The printed text around this label, or "" when the numeral is not printed.

    A window, not a parse. This file exists so a reader can check the parse, so
    a second parse deciding what to show them would fail in the same place and
    hide the same reference. `""` where the numeral was never printed is the
    honest answer, and that absence is often the disagreement itself.
    """
    m = re.search(rf"(?:(?<=\n)|\A)\s*\[?{re.escape(label)}[\].:]?\s", text)
    if m is None:
        return ""
    # Open the window at the PREVIOUS line break, not at the label. When a
    # reading splits one entry into two, every label after the split is
    # shifted and the damage started in the entry *before* the first disputed
    # one — a window that begins at the disputed label shows the symptom and
    # hides the cause. A fixed backtrack distance degrades silently the moment
    # the preceding entry is longer than it (a full author list, easily): the
    # previous line break is exactly one entry by construction and cannot.
    start = text.rfind("\n", 0, m.start() - 1) + 1 if m.start() else 0
    return text[start : m.start() + _SPAN_CHARS].strip()


def _write_disagreement(
    case: Path,
    labels: list[str],
    candidates: dict[str, list[RefEntry]],
    texts: dict[str, str],
) -> Path:
    """Every reading of every disputed label, with the text each was read from.

    Written before any question is asked. The structured fields alone are not
    enough to settle a disagreement — they are what disagreed — so the verbatim
    source span travels with them, and a reader who wants to answer the question
    themselves can, without opening the PDF.
    """
    out = case / "out"
    out.mkdir(parents=True, exist_ok=True)  # not created by `_refs_pipeline` before this point
    lines = [
        "# Reference numbering — where the readings disagree",
        "",
        "One section per citation label whose readings of the bibliography did not "
        "agree on one paper — either they name different papers, or nothing in them "
        "could be compared. Each reading's structured fields sit above the verbatim "
        "text it was read from, so the disagreement can be settled by eye against "
        "the printed list.",
        "",
        f"Readings compared: {', '.join(sorted(candidates))}.",
        "",
        "Verdicts on claims citing these labels are withheld — reported "
        "`unchecked`, never guessed — unless a reading is adopted for them.",
        "",
    ]
    for label in labels:
        lines += [f"## [{label}]", ""]
        for name in sorted(candidates):
            hits = [e for e in candidates[name] if e.num == label]
            lines += [f"### {name}", ""]
            if not hits:
                lines += ["Carries no entry for this label.", ""]
                continue
            # more than one hit IS the finding on a reading that carried the
            # label twice — which of the two it means is precisely the question,
            # so both are printed rather than the first
            for e in hits:
                lines += [
                    f"- doi: `{e.doi or '—'}`",
                    f"- year: `{e.year or '—'}`",
                    f"- slug: `{e.slug or '—'}`",
                    f"- refused to number itself: `{e.boundary_ambiguous}`",
                    "",
                    "Read from:",
                    "",
                    "```",
                    e.raw,
                    "```",
                    "",
                ]
        for name in sorted(texts):
            if span := _label_span(texts[name], label):
                lines += [f"### {name} — the printed list around it", "",
                          "```", span, "```", ""]
    path = out / _DISAGREEMENT_NAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _detected_doi(manuscript: Path) -> str | None:
    """The DOI printed on the paper's own front matter, or None.

    Imported lazily: `wizard` pulls in pymupdf and the interactive stack, and
    `refs` should not pay for that to look up one string.
    """
    from .wizard import detect_doi

    return detect_doi(manuscript)


def _needs_flat_reading(smap) -> bool:
    """Is a flat-text reading of this bibliography a second reading at all?

    `smap.converter` is `"pymupdf"` or `"docling <version>"`. On a run whose
    backend already IS pymupdf, `references_span_flat` returns the same text
    `references_span` did, so the cost buys nothing — and counting it as a
    reading would turn every `single` label into `agreed` and report
    corroboration that nothing corroborated.
    """
    # `"".split()` is `[]`, not `[""]` — guard rather than let a `source_map.json`
    # that never recorded a converter (empty string) crash the whole refs stage
    # on an IndexError instead of just treating it as "not known to be pymupdf"
    return (smap.converter.split() or [""])[0] != "pymupdf"


def _reference_readings(
    *,
    smap,
    crossref: list[RefEntry] | None = None,
    parsed: list[RefEntry] | None = None,
    flat: list[RefEntry] | None = None,
    llm: list[RefEntry] | None = None,
) -> dict[str, list[RefEntry]]:
    """The readings of the bibliography that are entitled to a vote, by name.

    Pure, so the agreement axis is testable without a PDF, a network or a model.

    A reading that is ABSENT is omitted, never present as `[]`: `label_agreement`
    counts the readings that carry a label, so an empty list votes against every
    label it does not have — which is all of them — and turns "we did not ask"
    into "one reading says no".

    NOTHING here reaches `reconcile`'s arguments or `resolve_all`. `reconcile`
    still chooses between the deposit and the run backend's parse; this dict is
    a second, orthogonal axis whose only power is to withhold a verdict.
    """
    out: dict[str, list[RefEntry]] = {}
    if crossref:
        out["crossref"] = crossref
    if parsed:
        out["parsed"] = parsed
    # double-guarded on purpose: the caller skips the *work* on a pymupdf run,
    # and this refuses the *vote* even if handed one, so a future caller cannot
    # reintroduce self-corroboration by passing the text in anyway
    if flat and _needs_flat_reading(smap):
        out["pymupdf"] = flat
    if llm:
        out["llm"] = llm
    return out


def _llm_reference_reading(
    reading_a: str,
    reading_b: str,
    *,
    label_a: str,
    label_b: str,
    enabled: bool,
    disabled_reason: str = "",
) -> tuple[list[RefEntry], ReflistProvenance]:
    """A model's reading of the bibliography, or a stated reason there is none.

    Returns `(entries, provenance)` — the provenance object itself, **always**,
    never `None` and never a tuple of loose pieces. `reflist.propose` reports
    two distinct kinds of finding (`fields_discarded`, a value that was not
    printed; `numbering_findings`, a reading whose labels do not add up) and a
    3-tuple of `(entries, notes, model)` could only carry one of them. An
    earlier draft of this plan did exactly that and would have dropped every
    duplicate and gap on the floor, unreported.

    `provenance.outcome` is set to exactly one of `reflist.REFLIST_OUTCOMES` on
    every return, and `provenance.failure` carries why whenever `outcome` is
    not `"read"`. A boolean here could not tell "never called" apart from
    "called and failed" — a failed call used to fall through to the same
    branch a successful one takes, published as a value that failed
    verification when nothing was ever proposed at all.

    `disabled_reason` names a STRUCTURAL cause when `not enabled` — the run's
    own backend leaves no second reading to compare against — as opposed to a
    bare, silent `ReflistProvenance()` for the two causes that are simply the
    caller's choice (`--no-llm-refs`, `--parse-only`) and need no explaining.
    """
    from . import ask
    from .reflist import ReflistProvenance, propose

    if not enabled:
        # a caller's own choice is silent (empty `failure`); a structural
        # reason the caller had no choice about is disclosed, same as "claude
        # not on PATH" below. `outcome` stays at its "not_attempted" default.
        return [], ReflistProvenance(failure=disabled_reason)
    if not ask.claude_available():
        # the ordinary case for someone who installed papertrace and not Claude
        # Code. The run proceeds on the deterministic readings and says so —
        # never that a model agreed with them. Worded with no substring in
        # common with the backend-skip reason above, so a test (or a reader)
        # asserting on the CONTENT of one can never be satisfied by the other.
        return [], ReflistProvenance(
            failure="claude is not on PATH, so no model read the reference list"
        )
    try:
        entries, prov = propose(reading_a, reading_b, label_a=label_a, label_b=label_b)
    except Exception as e:  # noqa: BLE001 — a failed corroboration is not a failed refs stage
        # Blanket, and here specifically: `refs` has already parsed the list and
        # is about to fetch the sources, and this call is the one thing in the
        # stage that leaves the machine. The seam raises RuntimeError for a
        # timeout and a non-zero exit and ValueError for unparseable stdout, and
        # a subprocess can surface OSError besides — enumerating them would fail
        # closed on the next one. The failure is recorded, not swallowed:
        # `reflist_failure` carries it into the manifest and a console line
        # says it at the time. Same shape as `check.py:996`.
        # `outcome="failed"`, never "not_attempted": every exception reaching
        # here came out of `propose` actually invoking the subprocess — its own
        # two early returns for "not enough text" and "not a JSON array" return
        # normally, they do not raise. A fresh `ReflistProvenance` is built
        # rather than reusing `propose`'s local one, which never gets returned:
        # the exception propagates past `propose`'s own `return` statement.
        return [], ReflistProvenance(
            outcome="failed",
            failure=f"{type(e).__name__}: {str(e)[:200]}",
        )
    if prov.discarded_whole:
        # the entries are already `[]` in this branch; the reason is what travels,
        # and it goes FIRST so the console line quotes it rather than a field name
        prov.fields_discarded.insert(0, f"reading discarded — {prov.discarded_whole}")
    return entries, prov


_READING_LABEL = {"parsed": "the run backend's parse", "pymupdf": "the flat-text parse"}


def _duplicated_in(entries: list[RefEntry], labels: list[str]) -> bool:
    """Does this reading carry any of `labels` more than once?

    A reading that duplicates a disputed label is not a whole reading to adopt
    for it: `label_agreement` already refused to let the duplicate speak for
    the label (that is WHY it is disputed at all, on a run with only one
    reading present). Offering it in the menu anyway would let option 3 adopt
    the very coin-flip the agreement check exists to refuse, and record it as
    settled by a person.
    """
    return any(sum(1 for e in entries if e.num == lbl) > 1 for lbl in labels)


def _disputed_reason(label: str, prov) -> str:
    """Why this label is still disputed after a resolution call, for the console.

    Never one blanket "the model could not tell" for every unresolved label:
    that is a plausible-looking cause standing in for the honest one whenever
    the real reason was a malformed reply (`prov.discarded_whole`) or a title
    the model named that is not printed in either text
    (`prov.fields_discarded`) — both already recorded, both distinct from a
    genuine `cannot_tell`. Printing abstention over either is the cardinal
    rule's own example of the failure this codebase exists to refuse.
    """
    if prov.discarded_whole:
        return "the reply could not be read at all"
    if f"[{label}].title" in prov.fields_discarded:
        return "named a paper that is not printed in either text"
    return "the model could not tell which paper it names"


def _escalate_disputed(
    case: Path,
    rec,
    entries: list[RefEntry],
    candidates: dict[str, list[RefEntry]],
    texts: dict[str, str],
    model: str | None = None,
) -> list[RefEntry]:
    """Show the disagreement, then offer to resolve it. Mutates `rec`, returns the list to use.

    Step 1 is unskippable and happens before the menu: nobody is asked to choose
    blind. Nothing here may set `rec.verified` — the honest record is *which*
    choice was made and *by whom*. `chosen_by: "user"` is the whole point: a
    person consenting to proceed is an input, not evidence. This codebase
    already ranks that signal — "a filename carries the user's assertion,
    content carries none."

    Changed by menu option 3 (a whole reading substituted) AND by option 1
    (each resolved label's entry substituted) — never option 1 leaving the
    chosen list untouched while un-disputing the label. That would let `check`
    judge the label again against the very entry the resolution ruled against,
    under a report line saying a person accepted it: the original wrong-paper
    bug, reached through the one path a human authorised.
    """
    from .refs import _label_list, _unique_slugs  # top-level in `refs.py`; not visible from
    # `_refs_pipeline`'s own local import, since this is a different function's frame

    labels = list(rec.labels_disputed)
    n = len(labels)
    if not n:
        return entries

    if not _interactive():
        # Suppress the disputed labels and nothing else. Not the whole audit —
        # that throws away a useful audit over a numbering the reader can check
        # by hand — and not a reading picked on the user's behalf, which would
        # record a choice nobody made. The evidence file is still written (no
        # network, no model, no prompt): nobody was asked, but the next person
        # to open this case folder should not have to re-run interactively just
        # to see what disagreed.
        path = _write_disagreement(case, labels, candidates, texts)
        rec.choice, rec.chosen_by = "withheld", "default"
        console.print(
            f"[yellow]⚠ {n} citation label{'' if n == 1 else 's'} in dispute[/yellow] — "
            f"{_label_list(labels)}. Not a tty, so nothing was asked: verdicts on claims "
            "citing them are withheld and reported unchecked. Re-run in a terminal to "
            f"settle them. [dim]full disagreement: {path}[/dim]"
        )
        return entries

    # Step 1, unskippable: the file exists before the first question is asked.
    path = _write_disagreement(case, labels, candidates, texts)
    console.print(
        f"\n[yellow]⚠ {n} citation label{'' if n == 1 else 's'} in dispute[/yellow] — "
        f"{_label_list(labels)}"
    )
    for label in labels:
        for name in sorted(candidates):
            hits = [e for e in candidates[name] if e.num == label]
            shown = hits[0].raw[:70] if hits else "— no entry —"
            console.print(f"  [{label:>3}] [cyan]{name:<8}[/cyan] {shown}")
    console.print(f"  [dim]full disagreement, with the text each reading came from:[/dim] {path}")

    # A reading that itself carries a duplicate of one of these labels is not
    # offered for option 3: adopting it whole would carry the coin-flip
    # `label_agreement` already refused into `labels_resolved`, recorded as
    # settled by a person.
    offered = [
        r for r in ("parsed", "pymupdf")
        if r in candidates and not _duplicated_in(candidates[r], labels)
    ]
    menu = [
        f"1. Ask the model to resolve the {n} disputed label{'' if n == 1 else 's'} "
        "[dim][default][/dim]",
        f"2. Withhold verdicts on all {n}",
    ]
    choices = ["1", "2", "4"]
    if offered:
        menu.append(f"3. Use one reading whole: {' / '.join(offered)}")
        choices.append("3")
    menu.append("4. Abort")
    console.print("\n" + "\n".join(menu))
    answer = Prompt.ask("  choice", choices=choices, default="1")

    if answer == "4":
        # the manifest is what every later stage trusts, and a half-written one
        # describing a numbering the user walked away from is worse than none —
        # but the evidence file above was written before the menu, on purpose,
        # and stays on disk: only the manifest is what "nothing was written" means
        console.print("[dim]aborted — no manifest was written.[/dim]")
        raise typer.Exit(1)

    if answer == "2":
        rec.choice, rec.chosen_by = "withheld", "user"
        return entries

    if answer == "3" and offered:
        pick = Prompt.ask("  which reading", choices=offered, default=offered[0])
        rec.choice, rec.chosen_by = pick, "user"
        rec.labels_resolved = sorted(labels, key=int)
        rec.labels_disputed = []
        # The adopted list is a DIFFERENT list from the one `reconcile` measured
        # `verified`/`source`/`ledger` against — all three describe the reading
        # a person just discarded, and carrying them forward would print an
        # extent claim ("numbering confirmed") about a list nobody measured,
        # naming the wrong reading as its source. Cleared, not recomputed:
        # `_covers`/`_refusals_unconfirm` are `reconcile`'s own logic, and a
        # second copy here is a second place for the two to drift apart.
        rec.verified = False
        rec.contested = False
        rec.unverified_from = None
        rec.ledger = {}
        rec.source = pick
        rec.note = (
            f"you chose {_READING_LABEL.get(pick, pick)} for the {n} label"
            f"{'' if n == 1 else 's'} in dispute. That is your reading, not a check of "
            "it — neither its content nor its extent has been confirmed"
        )
        return list(candidates[pick])

    from .reflist import resolve_disputed

    # `names[i]` must be the reading `texts[k]`, sorted the same way, actually
    # came from — passed explicitly rather than derived from `candidates`,
    # which can hold readings (crossref, llm) never shown to this call at all.
    names = tuple(sorted(texts))
    try:
        res = resolve_disputed(labels, names, tuple(texts[k] for k in names), model=model)
    except Exception as e:
        # The broad except IS the point: the refs stage has already done
        # useful work and must not go down with a failed subprocess call.
        # Every disputed label is left exactly where it was — disputed and
        # withheld — and the failure is named rather than swallowed.
        # `chosen_by` stays "user": a person did consent to trying, even
        # though the attempt itself produced nothing. No suppression comment
        # here: `BLE` (flake8-bugbear's blind-except code) is not in this
        # repo's ruff rule set (`E,F,W,I,UP,B`), so one at `_llm_reference_
        # reading`'s identical except is already a no-op — Task 7 review, m8.
        console.print(
            f"[yellow]⚠ the model could not resolve the disputed label"
            f"{'' if n == 1 else 's'} — the call did not return: "
            f"{type(e).__name__}: {str(e)[:200]}[/yellow]"
        )
        rec.choice, rec.chosen_by = "withheld", "user"
        rec.note += (
            f"; asked a model to resolve the {n} disputed label{'' if n == 1 else 's'} and "
            "the call failed, so they remain withheld"
        )
        return entries

    # A resolved label absent from `entries` has no entry to substitute — the
    # chosen list simply never carried that numeral. Recording it resolved
    # anyway would be a label marked settled with nothing behind it, so it is
    # filtered back into `labels_disputed` instead of trusted blind.
    chosen_nums = {e.num for e in entries}
    usable = {num: e for num, e in res.resolved.items() if num in chosen_nums}
    orphaned = sorted(set(res.resolved) - chosen_nums, key=int)

    rec.choice, rec.chosen_by = "llm_resolved", "user"
    rec.labels_resolved = sorted(usable, key=int)
    rec.labels_disputed = sorted({*res.still_disputed, *orphaned}, key=int)
    done, left = len(rec.labels_resolved), len(rec.labels_disputed)
    console.print(
        f"  [green]{done} resolved[/green]"
        + (f" · [yellow]{left} still disputed[/yellow]" if left else "")
    )
    if left:
        # partial resolution is the normal outcome, and each abstention is
        # named with the reason `resolve_disputed` actually recorded — a
        # malformed reply, an unprinted title and a genuine "cannot tell" are
        # three different findings, and one blanket line for all three is the
        # plausible-looking cause the cardinal rule refuses.
        parts = [f"[{lbl}] {_disputed_reason(lbl, res.provenance)}" for lbl in rec.labels_disputed]
        console.print(f"  [dim]{'; '.join(parts)} — verdicts on these stay withheld.[/dim]")
    named_model = res.provenance.model or "a model that did not report its own name"
    rec.note += (
        f"; {done} of the {n} disputed label{'' if n == 1 else 's'} were resolved by "
        f"{named_model} reading of both texts, with every field verified verbatim, and "
        "you accepted that. It is a reading, not a confirmation — the numbering is "
        "still unconfirmed"
    )
    if res.provenance.fields_discarded:
        rec.note += (
            f"; {len(res.provenance.fields_discarded)} field(s) of the resolution reply "
            "were not printed in either text and were discarded"
        )
    # The resolved entry REPLACES the one in the chosen list, in place, same
    # order, same length. Returning `entries` unchanged here takes the label
    # out of `labels_disputed` — so `check` judges it again — while leaving the
    # entry the resolution just ruled AGAINST as the paper it judges against.
    # That is the original wrong-paper bug reached through the one path a user
    # consented to, under a report line saying they accepted it.
    #
    # `_unique_slugs` runs again over the WHOLE substituted list: a resolved
    # entry's freshly computed slug can collide with one already carried
    # (same first author, same year), and `resolve_all` never re-slugs — two
    # entries sharing a slug share a download path, and the second overwrites
    # the first.
    return _unique_slugs([usable.get(e.num, e) for e in entries])


def _refs_pipeline(
    *,
    manuscript: Path,
    case: Path | None = None,
    provided: Path | None = None,
    email: str | None = None,
    parse_only: bool = False,
    backend: str = "auto",
    doi: str | None = None,
    supplement: list[Path] | None = None,
    llm_refs: bool = True,
) -> None:
    """Parse the References section, then retrieve open-access copies with an honest manifest.

    Keyword-only and plain-default on purpose: `refs()` below is a Typer
    command, and Typer's declared defaults are `OptionInfo` objects rather than
    the values the help screen shows — calling it directly (as `run()` and the
    tests do) with a shifted or omitted argument used to take that sentinel as
    the value. This function is what they actually call; `refs()` is a thin CLI
    adapter over it.
    """
    from .ingest import ingest_pdf, references_span, references_span_flat
    from .models import SourceMap, citation_labels, is_references_heading, paper_title

    # `_label_key` is shared with `reflist.propose`'s own numbering-findings
    # sort, rather than a third inline copy of the same tuple key — a
    # divergence between them would sort the same labels two ways in one report
    from .reflist import _label_key
    from .refs import (
        _client,
        corroborating_readings,
        crossref_deposit,
        deposit_corroborates,
        deposit_is_this_paper,
        label_agreement,
        manuscript_supplements,
        parse_references,
        reconcile,
        resolve_all,
        stamp_seen_in,
        unused_provided,
    )

    case = _resolve_case(case, manuscript)  # named after the paper unless -c said otherwise
    # identity first — the cached source map below is a manuscript-derived
    # artifact, and reading it before the guard is how references from one paper
    # ended up in a manifest stamped with another paper's hash
    basis = _guard_case(case, manuscript)
    _open_case(case)  # before ingest writes into it, so the folder is never briefly untracked

    ingest_dir = case / "ingest" / "manuscript"
    cached = ingest_dir / "source_map.json"
    # on "name" the cached map may have come from a different file that happened
    # to share this one's name, so re-read the paper we were actually given
    if cached.exists() and basis != "name":
        smap = SourceMap.from_json(cached)
    elif parse_only:
        # --parse-only is an inspection: "List references, no network". It must
        # not rewrite the case's manuscript slot and then return before the
        # manifest catches up, which left the source map describing one paper
        # and the manifest another. Read the paper somewhere disposable instead.
        with tempfile.TemporaryDirectory() as scratch:
            smap = ingest_pdf(manuscript, Path(scratch), backend=backend)
    else:
        smap = ingest_pdf(manuscript, ingest_dir, backend=backend)

    refs_text, references_resumed = references_span(smap)
    entries = parse_references(refs_text)
    if not entries:
        console.print("[red]No numbered references found — is there a References section?[/red]")
        raise typer.Exit(1)
    console.print(f"parsed [bold]{len(entries)}[/bold] numbered references")
    # `entries` is rebound to the reconciled list below, which destroys the parse
    # as a separate reading — and the parse is one of the four voters
    parsed = entries

    # The manuscript's own [N] markers arbitrate. Free, offline, and the only
    # one of the readings that is definitionally right about what the paper
    # cites — every reading of the bibliography is a candidate measured against
    # it. --parse-only stays offline: it gets the flat-text reading, which is
    # local, and never the model's, which is spend.
    body_labels = _body_citation_labels(smap, citation_labels, is_references_heading)
    crossref_entries, absent, identity_note = None, "", ""
    if not parse_only:
        doi = doi or _detected_doi(manuscript)
        with _client() as client:
            deposit = crossref_deposit(client, doi, _email(email))
        absent = deposit.absent
        # Is the record behind that DOI this paper at all? The DOI is typed by
        # hand or scraped off page 1, and this is the one retrieval route in
        # `refs` that can replace the *entire* reference list — every other one
        # has been title-checked since a wrong download was judged as a source.
        identity = (
            deposit_is_this_paper(paper_title(smap), deposit.title)
            if deposit.entries else None
        )
        if deposit.unrenderable:
            # the tool's shortfall, named as the tool's. A list this one could
            # only half read must not be mapped onto [1]..[n] — that would drop
            # the rest silently — but the reader is told whose limitation it is
            absent = (
                f"{deposit.publisher or 'the publisher'} deposited {deposit.deposited} "
                f"references and this tool could only read {len(deposit.entries)} of "
                "them, so the deposit was set aside rather than used to renumber the "
                "list. The gap is this tool's, not the publisher's"
            )
        elif identity is False:
            absent = (
                f"the DOI used ({doi}) belongs to a Crossref record titled "
                f"\u201c{deposit.title}\u201d, which is not this paper, so the "
                f"{len(deposit.entries)} references it deposited were not used to "
                "renumber this list"
            )
            console.print(
                f"[yellow]⚠ the DOI {doi} resolves to a different paper[/yellow] — "
                f"“{deposit.title[:70]}”. Its reference list was not used."
            )
        elif deposit.entries:
            crossref_entries = deposit.entries
            # A title this tool cannot read is common — an article-type banner
            # where the title should be, and no metadata behind it. The paper's
            # own bibliography settles it instead: two readings of one reference
            # list agree about the works, and no other paper's list does.
            corroboration = (
                deposit_corroborates(deposit.entries, entries) if identity is None else None
            )
            # a verified identity is worth as much as the count match it licenses,
            # and an unverifiable one must not be read as either
            if identity:
                identity_note = f". The DOI {doi} was confirmed as this paper by title"
            elif corroboration and corroboration.confirms:
                identity_note = (
                    f". The paper's title could not be compared with the record's, but "
                    f"{corroboration.found} of the {corroboration.total} references the "
                    "DOI's record deposited appear in the list printed in this paper, "
                    "which another paper's bibliography would not"
                )
            else:
                identity_note = (
                    f". The DOI {doi} could not be confirmed as this paper — too little "
                    "title to compare, and "
                    + ("too few references to compare either"
                       if corroboration and corroboration.too_few
                       else f"only {corroboration.found} of the {corroboration.total} "
                            "references it deposited appear in this paper's own list"
                            if corroboration else "no deposit to compare")
                    + ", so the identity behind this list is unverified"
                )
            console.print(
                f"crossref: [bold]{len(deposit.entries)}[/bold] references deposited by "
                f"{deposit.publisher or 'the publisher'} "
                f"[dim](DOI {doi}; identity "
                f"{'confirmed by title' if identity else 'confirmed by bibliography' if corroboration and corroboration.confirms else 'unverified'})[/dim]"
            )

    # The bibliography, read again. Both extra readings are voters only: neither
    # reaches `reconcile`'s arguments or `resolve_all`, so no model output and no
    # flat-text reading can cause a source to be resolved, downloaded or judged.
    # They can only cause a verdict to be withheld.
    flat_text, flat_entries = "", None
    needs_flat = _needs_flat_reading(smap)
    if needs_flat:
        flat_text, _ = references_span_flat(manuscript)
        flat_entries = parse_references(flat_text)
    # --parse-only promises "no network"; a `claude -p` subprocess is both
    # network and spend, so the flag is forced off here rather than trusted to
    # the caller. `needs_flat` is decided HERE, where the backend is known —
    # on a pymupdf run the model would be handed the same text twice, which
    # `reflist.propose` can only read as "one of the two extractions had no
    # text" and blame on the PDF rather than on this deliberate skip.
    llm_wanted = llm_refs and not parse_only
    llm_entries, reflist_prov = _llm_reference_reading(
        refs_text,
        flat_text,
        label_a=smap.converter,
        label_b="pymupdf",
        enabled=llm_wanted and needs_flat,
        # worded with no substring in common with "claude is not on PATH" —
        # the two structural reasons must never be mistaken for each other by
        # anything matching on their text
        disabled_reason=(
            "this run's backend is pymupdf, so a second flat reading of the "
            "bibliography would be the same text"
            if llm_wanted and not needs_flat
            else ""
        ),
    )
    others = _reference_readings(
        smap=smap, crossref=crossref_entries, parsed=parsed,
        flat=flat_entries, llm=llm_entries,
    )
    # Three states, printed three different ways — never fewer, or a call
    # that failed prints as one that succeeded (the regression this replaced):
    # see `reflist.ReflistProvenance.outcome`'s docstring.
    if reflist_prov.outcome == "read":
        # named, even when the reply itself did not — same wording
        # `disclosures._reflist` uses for this state, so the console and the
        # written report never describe one call two different ways
        named_model = reflist_prov.model or "a model that did not report its own name"
        whole = next(
            (n.removeprefix("reading discarded — ")
             for n in reflist_prov.fields_discarded if n.startswith("reading discarded")),
            "",
        )
        if whole:
            # a reply was obtained and the WHOLE reading was refused — never
            # the same line as a success with fields dropped, which read as
            # "N fields discarded as not printed" for a reply that was not a
            # JSON array at all, or named a paper the page does not print
            console.print(
                f"the reference list was also read by [bold]{named_model}[/bold], "
                f"and its reading was [yellow]discarded[/yellow] — {whole}"
            )
        elif reflist_prov.entries_proposed == 0:
            # NF3: the exact mirror of the `whole` branch above, in the other
            # direction — a reply of `[]` is a real reply, not a refused one,
            # but "0 fields discarded as not printed — a second reading, not
            # confirmation" reads as a clean corroboration from a model that
            # proposed nothing to corroborate anything with
            console.print(
                f"the reference list was also read by [bold]{named_model}[/bold] — "
                f"it proposed no entries at all [dim]— a second reading, not "
                f"confirmation[/dim]"
            )
        else:
            dropped = len(reflist_prov.fields_discarded)
            console.print(
                f"the reference list was also read by [bold]{named_model}[/bold] · "
                f"{dropped} field{'' if dropped == 1 else 's'} discarded as "
                f"not printed [dim]— a second reading, not confirmation[/dim]"
            )
        # reported separately, because it is a different kind of finding: not a
        # value that was missing, but a reading whose own labels do not add up
        for finding in reflist_prov.numbering_findings:
            console.print(f"  [yellow]⚠ the model reading's {finding}[/yellow]")
    elif reflist_prov.outcome == "failed":
        console.print(
            "[yellow]⚠ the reference list could not be read by a model — "
            f"the call did not return: {reflist_prov.failure}[/yellow]"
        )
    elif reflist_prov.failure:
        console.print(
            "[yellow]⚠ no model reading of the reference list[/yellow] — "
            f"{reflist_prov.failure}"
        )

    entries, rec = reconcile(body_labels, crossref_entries, entries, crossref_absent=absent)
    if rec.source == "crossref" and identity_note:
        # the note is what a reader of `refs_manifest.json` gets, so a list taken
        # from a publisher's record says on whose authority it was adopted
        rec.note += identity_note

    # A second, independent axis. `rec.verified` keeps its exact meaning and
    # nothing here may touch it: two readings of one document agreeing says
    # nothing about a reference the layout destroyed in both.
    agreement = label_agreement(others, body_labels)
    rec.labels_disputed = sorted(
        (label for label, state in agreement.items() if state == "disputed"),
        key=_label_key,
    )
    # every cited label, or it is not corroboration. One disputed label is not
    # "mostly corroborated", and `single` is not agreement — it is one reading
    rec.corroborated = bool(body_labels) and all(
        agreement.get(label) == "agreed" for label in body_labels
    )
    # Named only when corroboration holds, and only the readings that actually
    # voted `agreed` on EVERY cited label — `refs.corroborating_readings`
    # recomputes each label's voters with `label_agreement`'s own rule and
    # intersects their names, rather than approximating "carried a cited
    # label" (which named a reading that skipped an entry, or whose only
    # carrier of one was `boundary_ambiguous` and so cast no vote at all —
    # both measured; see the Task 6 re-review's N3).
    rec.corroborating_readings = (
        corroborating_readings(others, body_labels) if rec.corroborated else []
    )
    # `rec.corroborated` is per-label (every cited label agreed by >= 2
    # readings); `corroborating_readings` is per-reading (this reading agreed
    # on ALL of them). They can now disagree — every label agreed, but no
    # SINGLE reading spans every label, leaving 0 or 1 names. A count under 2
    # cannot back a "N readings agree" claim (agreement takes two), so this
    # gates on the list itself rather than on `rec.corroborated` alone — the
    # same fix `disclosures._numbering_corroboration` makes for the report.
    if rec.corroborated and len(rec.corroborating_readings) >= 2:
        console.print(
            f"[green]✓ {len(rec.corroborating_readings)} readings of the reference list "
            f"agree[/green] on every cited label "
            f"[dim]({', '.join(rec.corroborating_readings)}) — corroboration, not a "
            f"confirmed numbering[/dim]"
        )
    elif rec.corroborated:
        console.print(
            "[dim]every cited label was independently agreed by at least two readings, "
            "but no single reading agreed on all of them — not printed as "
            "corroboration[/dim]"
        )
    if rec.labels_disputed:
        console.print(
            f"[yellow]⚠ the readings do not agree at "
            f"[{'], ['.join(rec.labels_disputed)}][/yellow] — either they name different "
            "papers there or nothing in them could be compared; verdicts on claims citing "
            "those labels are withheld unless resolved below"
        )
    stamp_seen_in(entries, others)

    if rec.verified and rec.labels_disputed:
        # `numbering_verified` means EXTENT — the chosen list accounts for
        # exactly the labels the body cites — and a disputed label is a question
        # of CONTENT, which no count can answer. Both can be true at once, and
        # the flag's published meaning is not widened to cover the second (that
        # would redefine a boolean four report formats already read). What must
        # not happen is this line saying "confirmed" beside the line above
        # withholding verdicts: whichever of the two a reader believed, the
        # other would be a lie.
        console.print(
            f"[green]✓ numbering accounts for every cited label[/green] — {rec.note}"
        )
    elif rec.verified:
        console.print(f"[green]✓ numbering confirmed[/green] — {rec.note}")
    else:
        console.print(f"[yellow]⚠ numbering unconfirmed[/yellow] — {rec.note}")

    if references_resumed:
        # a list interrupted by another section used to end at the interruption:
        # 9 of 15 references parsed, and the last 6 never retrieved or checked
        console.print(
            "[yellow]⚠ the reference list continues past an intervening section and was "
            "picked up again — a boundary was crossed, so check the tail of the list "
            "above against the paper.[/yellow]"
        )
    if parse_only:
        for e in entries:
            console.print(f"  [{e.num:>3}] {e.raw[:90]}")
        return

    # After --parse-only returns: the escalation can spend a model call, and
    # --parse-only promises no network. Only "parsed" and "pymupdf" carry a
    # printed text to show a reader — "crossref" and "llm" are voters with no
    # separate span of the page, so they are omitted here rather than passed as "".
    reading_texts: dict[str, str] = {"parsed": refs_text}
    if needs_flat:
        reading_texts["pymupdf"] = flat_text
    entries = _escalate_disputed(case, rec, entries, others, reading_texts, model=None)

    if provided is not None and provided.is_file():
        console.print(
            f"[red]--provided expects a folder of PDFs, got a file:[/red] {provided}\n"
            "Put your PDFs into a folder, named so they match their reference — "
            "[cyan]<firstauthor>-<year>.pdf[/cyan], e.g. [cyan]pyrros-2023.pdf[/cyan]."
        )
        raise typer.Exit(2)
    provided = provided or (case / "sources")
    dest = case / "sources_resolved"
    console.print(
        f"resolving via crossref → unpaywall → europepmc → arxiv "
        f"(provided: [cyan]{provided}[/cyan])"
    )

    def tick(e):
        mark = STATUS_MARK.get(e.status, "?")
        via = f" via {e.resolver}" if e.resolver else ""
        console.print(f"  {mark} [{e.num:>3}] {e.status:<10}{via:<16} {e.reason}")
        if e.supplements:
            n = len(e.supplements)
            names = ", ".join(s.slug for s in e.supplements)
            console.print(
                f"        [cyan]+ {n} supplement{'' if n == 1 else 's'}[/cyan] "
                f"[dim]{names} — judged as separate documents[/dim]"
            )

    # the paper's own supplements claim their slugs FIRST, then `resolve_all`
    # works around them: one namespace, because both end up as `ingest/<slug>/`
    # and `sources_resolved/<slug>.pdf`
    taken: set[str] = {e.slug for e in entries if e.slug}
    own = manuscript_supplements(list(supplement or []), taken)
    for s in own:
        console.print(f"  [cyan]+[/cyan] {Path(s.pdf_path).name} → this paper's own supplement")

    resolve_all(entries, dest, _email(email), provided_dir=provided, progress=tick, taken=taken)

    # a file the user deliberately put in the folder that then did nothing is the
    # quietest possible failure — they would go on believing it had been read
    for pdf, why in unused_provided(entries, provided):
        console.print(f"  [yellow]⚠ {pdf.name} set aside — {why}[/yellow]")

    manifest = RefManifest(
        manuscript=manuscript.name,
        entries=entries,
        manuscript_supplements=own,
        manuscript_sha256=manuscript_fingerprint(manuscript),  # identity, not the name
        references_resumed=references_resumed,
        reference_source=rec.source,
        numbering_verified=rec.verified,
        numbering_note=rec.note,
        unverified_from=rec.unverified_from,
        numbering_contested=rec.contested,
        numbering_ledger=rec.ledger,
        numbering_corroborated=rec.corroborated,
        corroborating_readings=rec.corroborating_readings,
        labels_disputed=rec.labels_disputed,
        labels_resolved=rec.labels_resolved,
        numbering_choice=rec.choice,
        numbering_chosen_by=rec.chosen_by,
        reflist_outcome=reflist_prov.outcome,
        reflist_failure=reflist_prov.failure,
        reflist_model=reflist_prov.model,
        reflist_fields_discarded=reflist_prov.fields_discarded,
        reflist_numbering_findings=reflist_prov.numbering_findings,
        # `None`, not `0`, for every outcome but `"read"`: `entries_proposed`
        # is only ever measured once a reply exists, and writing the
        # provenance's own unmeasured default would publish "measured zero"
        # for a call that never returned anything to measure
        reflist_entries_proposed=(
            reflist_prov.entries_proposed if reflist_prov.outcome == "read" else None
        ),
    )
    manifest.to_json(case / "refs_manifest.json")
    ok = len(manifest.retrieved)
    misses = len(entries) - ok
    console.print(
        f"\n[bold]{ok}/{len(entries)} sources available[/bold]"
        + (f" · [yellow]{misses} not obtainable[/yellow] (see refs_manifest.json)" if misses else "")
    )
    console.print("[dim]not obtainable is a recorded result — those claims will be reported as"
                  " unverifiable, never guessed.[/dim]")


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def refs(
    manuscript: Path = typer.Argument(..., exists=True),
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder (default: a folder named after the paper, beside the paper)",
    ),
    provided: Path = typer.Option(
        None, "--provided",
        help="Folder of reference PDFs you already have; files match by name "
             "<firstauthor>-<year>.pdf (e.g. pyrros-2023.pdf)",
    ),
    email: str = typer.Option(None, "--email", envvar=["PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"]),
    parse_only: bool = typer.Option(False, "--parse-only", help="List references, no network"),
    backend: str = typer.Option("auto", "--backend", help="auto | docling | pymupdf"),
    doi: str = typer.Option(
        None, "--doi",
        help="DOI of the paper itself — fetches the publisher's own reference list to "
             "check the parsed numbering against (default: the DOI printed on page 1)",
    ),
    supplement: list[Path] = typer.Option(
        None, "--supplement", exists=True,
        help="Supplementary material for THIS paper (repeatable). A cited work's "
             "supplement needs no flag — drop it in the sources folder named after "
             "the reference, e.g. pyrros-2023-supplement.pdf",
    ),
    llm_refs: bool = typer.Option(
        True, "--llm-refs/--no-llm-refs",
        help="Also have a model read the printed reference list as a second opinion "
             "on the numbering (one extra model call — none under --parse-only, or on a "
             "pymupdf backend, which leaves no second reading to compare)",
    ),
) -> None:
    """Parse the References section, then retrieve open-access copies with an honest manifest."""
    _refs_pipeline(manuscript=manuscript, case=case, provided=provided, email=email,
                    parse_only=parse_only, backend=backend, doi=doi, supplement=supplement,
                    llm_refs=llm_refs)


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def scout(
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder holding the audit (required unless ./case exists)",
    ),
    doi: str = typer.Option(None, "--doi", help="DOI of the paper itself (skips the title lookup)"),
    email: str = typer.Option(None, "--email", envvar=["PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"]),
) -> None:
    """Scan Europe PMC for literature the reference list doesn't know."""
    from .scout import scout_case

    case = _stage_case(case)
    if not (case / "refs_manifest.json").exists():
        console.print("[red]refs_manifest.json not found[/red] — run `papertrace refs` first")
        raise typer.Exit(1)

    with console.status("scouting the literature around the paper…"):
        res = scout_case(case, doi=doi, email=email or "")
    (case / "out").mkdir(parents=True, exist_ok=True)
    res.to_json(case / "out" / "scout.json")

    if res.error:
        console.print(f"[yellow]⚠ scout incomplete: {res.error}[/yellow]")
    if res.paper_title:
        # `via doi` stopped meaning "identified reliably" when `run` began
        # reading the DOI off page 1, so the warning turns on the identity check
        # rather than on which query happened to answer
        caveat = {
            "confirmed": "",
            "unverified": " — identity unverified, check this is your paper",
            "mismatch": " — NOT this paper",
        }.get(res.paper_identity, " — wrong paper? pass --doi")
        console.print(
            f"paper: [bold]{res.paper_title[:80]}[/bold] ({res.paper_year or '?'})"
            f" · [dim]identified via {res.resolved_via}, identity "
            f"{res.paper_identity or 'not recorded'}{caveat}[/dim]"
        )
    console.print(f"[green]▸[/green] published since: [bold]{len(res.newer)}[/bold] candidates")
    for h in res.newer[:5]:
        console.print(f"    [cyan]{h.year or '?'}[/cyan] {h.title[:76]} [dim]({h.via})[/dim]")
    if len(res.newer) > 5:
        console.print(f"    [dim]… {len(res.newer) - 5} more in scout.json[/dim]")
    console.print(
        f"[yellow]▸[/yellow] existed but uncited: [bold]{len(res.overlooked)}[/bold] candidates"
    )
    for h in res.overlooked[:5]:
        console.print(f"    [cyan]{h.year or '?'}[/cyan] {h.title[:76]}")
    if len(res.overlooked) > 5:
        console.print(f"    [dim]… {len(res.overlooked) - 5} more in scout.json[/dim]")
    if res.same_year:
        console.print(
            f"[yellow]▸[/yellow] same year as the paper: [bold]{len(res.same_year)}[/bold]"
            " candidates [dim]— may postdate submission, so neither newer nor owed[/dim]"
        )
        for h in res.same_year[:5]:
            console.print(f"    [cyan]{h.year or '?'}[/cyan] {h.title[:76]}")
        if len(res.same_year) > 5:
            console.print(f"    [dim]… {len(res.same_year) - 5} more in scout.json[/dim]")
    console.print(
        "[dim]search-based — absence from these lists proves nothing; presence is a"
        " candidate for your judgement, not an accusation.[/dim]"
    )


def _check_pipeline(
    *,
    case: Path | None = None,
    model: str | None = None,
    backend: str = "auto",
) -> None:
    """`check`'s work, with ordinary Python defaults.

    Keyword-only for the reason the other pipeline functions are: an omitted
    argument to the Typer command is an `OptionInfo`, not the default `--help`
    shows. `backend` is what made this split necessary — it reaches
    `ingest_pdf`, which refuses an unrecognised value loudly, so a sentinel
    arriving here would fail an audit at the judging step after the retrieval
    work was already done.
    """
    from .ask import claude_available
    from .check import Truncations, check_claims, extract_claims

    case = _stage_case(case)
    if not claude_available():
        console.print(
            "[red]The `claude` CLI is required for batch checking[/red] — "
            "install Claude Code (https://claude.com/claude-code) and log in, "
            "or run the interactive `/review` skill instead."
        )
        raise typer.Exit(2)

    manifest = RefManifest.from_json(case / "refs_manifest.json")
    truncations = Truncations()  # one accumulator per run — never module state
    with console.status("extracting claims (cited + uncited)…"):
        claims, uncited = extract_claims(case, model, truncations=truncations)
    console.print(
        f"[green]✓[/green] {len(claims)} citation-backed claims · "
        f"{len(uncited)} uncited assertions flagged"
    )

    def tick(slug, group):
        console.print(f"  checked against [cyan]{slug}[/cyan]: {_tick_marks(slug, group)}")

    def fail(slug, msg):
        console.print(
            f"  [red]✗ {slug}: check failed — {msg}[/red]\n"
            f"    [yellow]claims marked ⚠ unchecked (source was retrieved) — "
            f"re-run `papertrace check` to retry[/yellow]"
        )

    with console.status("reading claims against their cited pages…"):
        check_claims(
            claims, manifest, case, model, progress=tick, on_error=fail,
            truncations=truncations, backend=backend,
            # Task 3 populates this; [] on any manifest it never touched, so an
            # older run or one where nothing disagreed withholds nothing here.
            disputed=set(manifest.labels_disputed),
        )

    from .check import coverage_audit
    from .models import SourceMap

    coverage = coverage_audit(case, claims)
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    converter = SourceMap.from_json(smap_path).converter if smap_path.exists() else "pymupdf"
    # how each cited source was read, recorded per slug. The manuscript's
    # converter above says nothing about them, and until this was carried the
    # markdown and HTML reports said nothing about them either.
    source_converters: dict[str, str] = {}
    source_table_warnings: dict[str, list[str]] = {}
    for doc in manifest.documents():
        sp = case / "ingest" / doc.slug / "source_map.json"
        if sp.exists() and doc.slug not in source_table_warnings:
            _tw = SourceMap.from_json(sp).table_warnings
            if _tw:  # None = nobody watched, [] = watched and clean
                source_table_warnings[doc.slug] = _tw
        if sp.exists() and doc.slug not in source_converters:
            source_converters[doc.slug] = SourceMap.from_json(sp).converter

    from .check import last_model

    results = RunResults(
        manuscript=manifest.manuscript,
        checker=f"claude -p · {model or last_model() or 'account default model'}",
        date=str(datetime.date.today()),
        refs_total=len(manifest.entries),
        refs_available=len(manifest.retrieved),
        converter=converter,
        source_converters=source_converters,
        source_table_warnings=source_table_warnings,
        claims=claims,
        uncited=uncited,
        coverage=coverage,
        truncated=truncations.report(),
    )
    (case / "out").mkdir(parents=True, exist_ok=True)
    results.to_json(case / "out" / "results.json")

    console.print(_verdict_line(results.counts()))
    from .disclosures import coverage_headline

    occ = coverage.get("occurrences") or {}
    headline = coverage_headline(coverage)
    # the uncertain count travels with the ratio, here as in the report: a run
    # whose attributions were mostly refused has a nearly meaningless ratio
    unresolved = (occ.get("uncovered", 0) + occ.get("uncertain", 0)) if occ \
        else len(coverage["missing"])
    if headline:
        console.print(
            f"[{'yellow' if unresolved else 'green'}]coverage: {headline}"
            f"[/{'yellow' if unresolved else 'green'}]"
        )
    elif claims:
        console.print(
            "[yellow]coverage: no bracketed numeric citation markers found — only "
            "[12]/[7,8]/[9-11] styles are audited; coverage not audited[/yellow]"
        )
    if uncited:
        console.print(f"[cyan]{len(uncited)} uncited assertions[/cyan] — see report section")


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def check(
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder holding the audit (required unless ./case exists)",
    ),
    model: str = typer.Option(None, "--model", help="Model override for claude -p"),
    backend: str = typer.Option("auto", "--backend", help="auto | docling | pymupdf"),
) -> None:
    """Extract citation-backed claims and judge each against its cited source (claude -p)."""
    _check_pipeline(case=case, model=model, backend=backend)


def _downgrade_unshowable(anchor) -> bool:
    """A substantive verdict with no evidence image stops being a verdict.

    `check` validates page and block against the source map, which is what
    normally guarantees a crop. This is the same rule enforced against reality:
    the PDF can be absent from `sources_resolved/`, and a source map can
    disagree with the PDF it was built from. `not_addressed` is exempt — it
    never claimed a passage, so it owes no picture.

    Returns True when it downgraded, so the caller can say so on the console.
    """
    substantive = ("supported", "partial", "contradicted")
    # `or continuation_images`: a passage crossing a column break can have its
    # boxes in the continuation, and that verdict CAN be shown — downgrading it
    # would discard a judgement the reader is perfectly able to check
    if anchor.verdict not in substantive or anchor.evidence_image or anchor.continuation_images:
        return False
    anchor.verdict = "unchecked"
    anchor.note = (
        "no evidence image could be produced for the passage this verdict rests on "
        f"(page {anchor.source_page}"
        + (f", {anchor.source_block}" if anchor.source_block else "")
        + ") — the source PDF is missing from sources_resolved/, or its pages no "
        "longer match the source map it was ingested from. Re-run "
        "`papertrace refs` and `papertrace check` for this source."
    )
    return True


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def highlight(
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder holding the audit (required unless ./case exists)",
    ),
    claim: int = typer.Option(None, "--claim", help="Only this claim id"),
) -> None:
    """Produce red-box evidence crops for every claim with a page anchor."""
    from .highlight import crop_for_anchor, source_page_count

    case = _stage_case(case)
    results = RunResults.from_json(case / "out" / "results.json")
    out_dir = case / "out" / "evidence"
    done = 0
    for c in results.claims:
        if claim is not None and c.id != claim:
            continue
        # one crop per cited source, so a multi-source claim shows the passage
        # behind each verdict. A results.json written before multi-source
        # checking has no judgements; its own headline anchor is the one target.
        for a in c.judgements or [c]:
            imgs = crop_for_anchor(a, c.id, case / "sources_resolved", case / "ingest", out_dir)
            if not imgs and a.source_slug:
                # sources provided by the user live elsewhere — try the manifest
                # path. `document()` and not a scan of `entries`: a supplement is
                # never in `entries`, so scanning them left every supplement
                # verdict with no crop and no reason given.
                manifest = RefManifest.from_json(case / "refs_manifest.json")
                doc = manifest.document(a.source_slug)
                if doc and doc.pdf_path:
                    src = Path(doc.pdf_path)
                    tmp = case / "sources_resolved" / f"{a.source_slug}.pdf"
                    if src.exists() and not tmp.exists():
                        tmp.parent.mkdir(parents=True, exist_ok=True)
                        tmp.write_bytes(src.read_bytes())
                        imgs = crop_for_anchor(
                            a, c.id, case / "sources_resolved", case / "ingest", out_dir
                        )
            tag = f"claim {c.id}" + (f" · {a.source_slug}" if c.is_multi_source() else "")
            if imgs:
                rel = [str(Path(i).relative_to(case / "out")) for i in imgs]
                a.evidence_image, a.continuation_images = rel[0], rel[1:]
                done += len(rel)
                # `is True` / `is False` / `is None` — never truthiness. None
                # means nothing was ever searched for, and calling that "not
                # found" asserts a search that did not happen.
                if a.anchor_located is True:
                    console.print(f"  [green]✓[/green] {tag}: {a.evidence_image}")
                elif a.anchor_located is False:
                    # "in the cropped region", not "on the page" — the region is
                    # one block, and a quote continuing into the next column is
                    # on the page and outside it at once
                    console.print(
                        f"  [yellow]○ {tag}: {a.evidence_image} — the anchor phrase "
                        f"was not found inside the cropped region; crop written "
                        f"unboxed[/yellow]"
                    )
                else:
                    console.print(
                        f"  [yellow]○ {tag}: {a.evidence_image} — no anchor phrase "
                        f"was offered, so none was searched for; crop written "
                        f"unboxed[/yellow]"
                    )
                # after the verdict on the first image, not before it: the line
                # above names that image, and this says where the rest of the
                # passage went
                if a.continuation_images:
                    console.print(
                        f"    [cyan]↳ the passage crosses a break — "
                        f"{len(a.continuation_images)} further "
                        f"image{'' if len(a.continuation_images) == 1 else 's'}: "
                        f"{', '.join(Path(i).name for i in a.continuation_images)}[/cyan]"
                    )
            elif a.source_slug and a.source_page:
                # a page the source does not have is not the same as a page that
                # held nothing — say which it was rather than just writing no crop
                pdf = case / "sources_resolved" / f"{a.source_slug}.pdf"
                if pdf.exists() and a.source_page > (n := source_page_count(pdf)):
                    console.print(
                        f"  [yellow]○ {tag}: the check named page {a.source_page}, "
                        f"but {a.source_slug} has {n} — no page to read, so no crop "
                        f"and no anchor claim[/yellow]"
                    )
            if _downgrade_unshowable(a):
                console.print(
                    f"  [yellow]⚠ {tag}: {a.note}[/yellow]"
                )
        # the claim-level evidence_image must follow the deciding judgement, or
        # the crop shown beside the headline belongs to a different source
        c.apply_headline()
    results.to_json(case / "out" / "results.json")
    console.print(f"[bold]{done}[/bold] evidence crops written")


def _report_pipeline(
    *,
    case: Path | None = None,
    png: bool = False,
    formats: list[str] | None = None,
) -> None:
    """`report`'s work, with ordinary Python defaults.

    Keyword-only so `run()` and the tests calling it directly cannot silently
    receive a Typer `OptionInfo` in place of a value — the flaw that has shipped
    twice here already. `formats` is the parameter that made this split
    necessary: `run()` used to call `report(case=..., png=...)`, so a new
    option would have arrived as a truthy sentinel and rendered whatever that
    happened to mean.
    """
    from .models import ScoutResults
    from .report import FORMATS, write_reports

    # a mistyped flag is user error, answered before the results are loaded so
    # it cannot half-write a report folder — and with a line, not a traceback
    if bad := [f for f in (formats or []) if f not in FORMATS]:
        console.print(
            f"[red]unknown --format {', '.join(bad)}[/red] — "
            f"expected any of {', '.join(f'[cyan]{f}[/cyan]' for f in FORMATS)}"
        )
        raise typer.Exit(2)

    case = _stage_case(case)
    results = RunResults.from_json(case / "out" / "results.json")
    manifest_path = case / "refs_manifest.json"
    manifest = RefManifest.from_json(manifest_path) if manifest_path.exists() else None
    scout_path = case / "out" / "scout.json"
    scout_res = ScoutResults.from_json(scout_path) if scout_path.exists() else None
    # the ingested manuscript, for the viewer to underline the audited sentences
    # in. Absent means None: the page then shows the sentences alone and says
    # so, rather than being handed an empty manuscript to keep quiet about
    annotated_path = case / "ingest" / "manuscript" / "annotated.md"
    annotated = annotated_path.read_text() if annotated_path.exists() else None
    # how the paper was read, restated where it can be seen. `ingest` says this
    # once, minutes earlier and above a wall of model-loading logs; a standalone
    # `papertrace report` never said it at all.
    console.print(_provenance_line(results.converter))
    paths = write_reports(results, manifest, case / "out", png=png, scout=scout_res,
                          formats=formats or ["md"], annotated=annotated)
    for p in paths:
        console.print(f"  [green]✓[/green] {p.relative_to(case)}")


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def report(
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder holding the audit (required unless ./case exists)",
    ),
    png: bool = typer.Option(
        False, "--png/--no-png",
        help="Also export PNG images of the report looks (one-time: playwright install chromium)",
    ),
    formats: list[str] = typer.Option(
        None, "--format", "-f",
        help="Extra looks to render beside report.md: editor | terminal | viewer (repeatable)",
    ),
) -> None:
    """Render report.md — and the editor/terminal/viewer looks on request — from results.json."""
    _report_pipeline(case=case, png=png, formats=formats)


@app.command(rich_help_panel="Start here")
def run(
    manuscript: Path = typer.Argument(..., exists=True),
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder (default: a folder named after the paper, beside the paper)",
    ),
    provided: Path = typer.Option(
        None, "--provided",
        help="Folder of reference PDFs you already have; files match by name "
             "<firstauthor>-<year>.pdf (e.g. pyrros-2023.pdf)",
    ),
    email: str = typer.Option(None, "--email", envvar=["PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"]),
    model: str = typer.Option(None, "--model"),
    png: bool = typer.Option(
        False, "--png/--no-png",
        help="Also export PNG images of the report looks (one-time: playwright install chromium)",
    ),
    backend: str = typer.Option("auto", "--backend", help="auto | docling | pymupdf"),
    with_scout: bool = typer.Option(
        True, "--scout/--no-scout",
        help="Also scan Europe PMC for newer + uncited literature",
    ),
    doi: str = typer.Option(
        None, "--doi",
        help="DOI of the paper itself — checks the reference numbering against the "
             "publisher's deposited list, and pins the scout's literature search",
    ),
    formats: list[str] = typer.Option(
        None, "--format", "-f",
        help="Extra looks to render beside report.md: editor | terminal | viewer (repeatable)",
    ),
    supplement: list[Path] = typer.Option(
        None, "--supplement", exists=True,
        help="Supplementary material for THIS paper (repeatable). A cited work's "
             "supplement needs no flag — drop it in the sources folder named after "
             "the reference, e.g. pyrros-2023-supplement.pdf",
    ),
    llm_refs: bool = typer.Option(
        True, "--llm-refs/--no-llm-refs",
        help="Also have a model read the printed reference list as a second opinion "
             "on the numbering (one extra model call — none under --parse-only, or on a "
             "pymupdf backend, which leaves no second reading to compare)",
    ),
) -> None:
    """Full pipeline: ingest → refs → scout → check → highlight → report."""
    console.print(BANNER)
    email = _email(email)  # fail fast — before the ingest models load, not after
    # resolved once, here, and passed down by keyword: a stage that re-derived
    # its own folder could put the same question six times, or disagree
    case = _resolve_case(case, manuscript)
    _guard_case(case, manuscript)  # one case folder per paper — never mix two audits
    _open_case(case)
    # KEYWORDS ONLY, deliberately, for every stage below still called through its
    # Typer command. Typer's declared defaults are OptionInfo objects rather than
    # the values they display, so a positional call breaks silently the moment a
    # stage gains a parameter: the arguments shift, the shifted-in default is an
    # OptionInfo that equals none of the expected strings, and the stage takes a
    # fallback branch. Adding `--case` to `ingest` did exactly that — the backend
    # became an OptionInfo and every audit ingested as flat text while claiming
    # layout-aware ingest. `_ingest_pipeline`, `_refs_pipeline`,
    # `_check_pipeline` and `_report_pipeline` below are split out of their
    # Typer commands specifically to make that mistake impossible rather than
    # just avoided by convention — `scout` and `highlight` are still
    # convention-only, and each should be split the next time it gains a
    # parameter.
    _ingest_pipeline(pdf=manuscript, out=case / "ingest" / "manuscript", case=case, backend=backend)
    # detected once, here, and handed to both consumers. `refs` detects for
    # itself when called alone, so forwarding the raw option left the scout
    # guessing by title on the very runs where the paper's DOI was sitting on
    # page 1 — and a wrong title match anchors the whole scan to another paper
    # without erroring.
    doi = doi or _detected_doi(manuscript)
    _refs_pipeline(manuscript=manuscript, case=case, provided=provided, email=email,
                    parse_only=False, backend=backend, doi=doi, supplement=supplement,
                    llm_refs=llm_refs)
    if with_scout:
        scout(case=case, doi=doi, email=email)
    _check_pipeline(case=case, model=model, backend=backend)
    highlight(case=case, claim=None)
    _report_pipeline(case=case, png=png, formats=formats)
    # a four-minute run should not need scrolling to learn how the paper was
    # read, so the backend rides on the last line too
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    from .models import SourceMap

    backend_used = SourceMap.from_json(smap_path).converter if smap_path.exists() else "unknown"
    # name the page the user will actually review in, when one was written.
    # `isinstance`, because a direct call that omits `formats` hands this an
    # OptionInfo sentinel, and `in` over one raises
    if isinstance(formats, list) and "viewer" in formats:
        where = f"[cyan]{case/'out'/'report_viewer.html'}[/cyan] in a browser"
    else:
        where = f"[cyan]{case/'out'/'report.md'}[/cyan]"
    console.print(
        f"\n[bold green]done[/bold green] — open {where}"
        f" · read with [bold]{backend_used}[/bold]"
        " · the gap register is part of the result."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
