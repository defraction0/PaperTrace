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

import typer
from rich.console import Console
from rich.prompt import Prompt

from .models import ClaimResult, RefManifest, RunResults, manuscript_fingerprint

app = typer.Typer(add_completion=False, rich_markup_mode="rich", invoke_without_command=True)
console = Console()

BANNER = r"""[bold]
  ┌──────────────────────────┐
  │  ▛▀▜ PaperTrace          │
  │  ▌█▐ claims traced back  │
  │  ▙▄▟ to their sources    │
  └──────────────────────────┘[/bold]
"""

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
        else "  no case folder found here — `papertrace run <paper.pdf>` makes one."
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
    figures exist at all. The sources are ingested flat-text *always* and on
    purpose (`check.py` passes `backend="pymupdf"`), which no reader can infer
    from a line that names docling — so it is said rather than assumed.
    """
    flat = converter.startswith("pymupdf")
    manuscript = (
        f"[yellow]{converter} — flat text, tables linearized[/yellow]"
        if flat
        else f"[cyan]{converter}[/cyan] — layout-aware"
    )
    return (
        f"  read with: {manuscript}\n"
        f"  [dim]cited sources are always read as flat text — text anchors are what "
        f"verdicts and crops need[/dim]"
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


@app.callback()
def _root(ctx: typer.Context) -> None:
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
def init(case: Path = typer.Argument(Path("case"), help="Case folder to create")) -> None:
    """Create a case folder skeleton (gitignored by design — keep manuscripts local)."""
    _open_case(case)
    for sub in ("sources", "form", "ingest", "out/evidence"):
        (case / sub).mkdir(parents=True, exist_ok=True)
    console.print(BANNER)
    console.print(f"case folder ready: [cyan]{case}/[/cyan]")
    console.print("  put reference PDFs you already have into [cyan]sources/[/cyan]")
    console.print("  put your questions or form-field screenshots into [cyan]form/[/cyan]")
    # `run` and `refs` name their own folder after the paper, so a hand-made one
    # is only used if it is passed - saying so here beats orphaned sources/
    console.print(f"  [dim]hand this folder to every step: [cyan]-c {case}[/cyan][/dim]")


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


def _text_opt(value) -> str | None:
    """A Typer string option as a string, or None — including when nobody passed it.

    Typer's declared default is an `OptionInfo`, not the value the help screen
    shows, and these stages are also called as plain Python functions by `run`
    and by the tests. An `OptionInfo` is truthy, so `doi or detect_doi(...)`
    took it for a real DOI and built a request URL out of its repr: the offline
    test suite started making live Crossref calls, and passed, because the
    machine running it had network. This is the same shape as the bug that made
    `ingest`'s backend an OptionInfo and read every paper as flat text while
    reporting layout-aware ingest.
    """
    return value if isinstance(value, str) and value.strip() else None


def _body_citation_labels(smap, citation_labels, is_references_heading) -> set[str]:
    """The `[N]` markers the manuscript's body actually cites.

    Read from the source map and stopped at the bibliography, matching what
    `coverage_audit` counts — the two readings are only worth comparing because
    they come from one rule in `models.py`. Passed its two functions rather than
    importing them, so this stays a pure function of the map.
    """
    body: list[str] = []
    for b in smap.blocks:
        if is_references_heading(b.type, b.text):
            break
        body.append(b.text)
    return citation_labels("\n".join(body))


def _detected_doi(manuscript: Path) -> str | None:
    """The DOI printed on the paper's own front matter, or None.

    Imported lazily: `wizard` pulls in pymupdf and the interactive stack, and
    `refs` should not pay for that to look up one string.
    """
    from .wizard import detect_doi

    return detect_doi(manuscript)


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
) -> None:
    """Parse the References section, then retrieve open-access copies with an honest manifest."""
    from .ingest import ingest_pdf, references_span
    from .models import SourceMap, citation_labels, is_references_heading, paper_title
    from .refs import (
        _client,
        crossref_deposit,
        deposit_corroborates,
        deposit_is_this_paper,
        parse_references,
        reconcile,
        resolve_all,
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

    # The manuscript's own [N] markers arbitrate. Free, offline, and the only
    # one of the three readings that is definitionally right about what the
    # paper cites — the parse and the deposit are both candidates measured
    # against it. --parse-only stays offline, so it gets no second candidate.
    body_labels = _body_citation_labels(smap, citation_labels, is_references_heading)
    crossref_entries, absent, identity_note = None, "", ""
    if not parse_only:
        given = _text_opt(doi)
        doi = given or _detected_doi(manuscript)
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

    entries, rec = reconcile(body_labels, crossref_entries, entries, crossref_absent=absent)
    if rec.source == "crossref" and identity_note:
        # the note is what a reader of `refs_manifest.json` gets, so a list taken
        # from a publisher's record says on whose authority it was adopted
        rec.note += identity_note
    if rec.verified:
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

    resolve_all(entries, dest, _email(email), provided_dir=provided, progress=tick)

    manifest = RefManifest(
        manuscript=manuscript.name,
        entries=entries,
        manuscript_sha256=manuscript_fingerprint(manuscript),  # identity, not the name
        references_resumed=references_resumed,
        reference_source=rec.source,
        numbering_verified=rec.verified,
        numbering_note=rec.note,
        unverified_from=rec.unverified_from,
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


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def check(
    case: Path = typer.Option(
        None, "--case", "-c",
        help="Case folder holding the audit (required unless ./case exists)",
    ),
    model: str = typer.Option(None, "--model", help="Model override for claude -p"),
) -> None:
    """Extract citation-backed claims and judge each against its cited source (claude -p)."""
    from .check import Truncations, check_claims, claude_available, extract_claims

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
            truncations=truncations,
        )

    from .check import coverage_audit
    from .models import SourceMap

    coverage = coverage_audit(case, claims)
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    converter = SourceMap.from_json(smap_path).converter if smap_path.exists() else "pymupdf"

    from .check import last_model

    results = RunResults(
        manuscript=manifest.manuscript,
        checker=f"claude -p · {model or last_model() or 'account default model'}",
        date=str(datetime.date.today()),
        refs_total=len(manifest.entries),
        refs_available=len(manifest.retrieved),
        converter=converter,
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
    if anchor.verdict not in substantive or anchor.evidence_image:
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
            img = crop_for_anchor(a, c.id, case / "sources_resolved", case / "ingest", out_dir)
            if img is None and a.source_slug:
                # sources provided by the user live elsewhere — try the manifest path
                manifest = RefManifest.from_json(case / "refs_manifest.json")
                entry = next((e for e in manifest.entries if e.slug == a.source_slug), None)
                if entry and entry.pdf_path:
                    src = Path(entry.pdf_path)
                    tmp = case / "sources_resolved" / f"{a.source_slug}.pdf"
                    if src.exists() and not tmp.exists():
                        tmp.parent.mkdir(parents=True, exist_ok=True)
                        tmp.write_bytes(src.read_bytes())
                        img = crop_for_anchor(
                            a, c.id, case / "sources_resolved", case / "ingest", out_dir
                        )
            tag = f"claim {c.id}" + (f" · {a.source_slug}" if c.is_multi_source() else "")
            if img:
                a.evidence_image = str(Path(img).relative_to(case / "out"))
                done += 1
                # `is True` / `is False` / `is None` — never truthiness. None
                # means nothing was ever searched for, and calling that "not
                # found on the page" asserts a search that did not happen.
                if a.anchor_located is True:
                    console.print(f"  [green]✓[/green] {tag}: {a.evidence_image}")
                elif a.anchor_located is False:
                    console.print(
                        f"  [yellow]○ {tag}: {a.evidence_image} — the anchor phrase "
                        f"was searched for and not found on the page; crop written "
                        f"unboxed[/yellow]"
                    )
                else:
                    console.print(
                        f"  [yellow]○ {tag}: {a.evidence_image} — no anchor phrase "
                        f"was offered, so none was searched for; crop written "
                        f"unboxed[/yellow]"
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
) -> None:
    """Render report.md + the editor/terminal looks from results.json."""
    from .models import ScoutResults
    from .report import write_reports

    case = _stage_case(case)
    results = RunResults.from_json(case / "out" / "results.json")
    manifest_path = case / "refs_manifest.json"
    manifest = RefManifest.from_json(manifest_path) if manifest_path.exists() else None
    scout_path = case / "out" / "scout.json"
    scout_res = ScoutResults.from_json(scout_path) if scout_path.exists() else None
    # how the paper was read, restated where it can be seen. `ingest` says this
    # once, minutes earlier and above a wall of model-loading logs; a standalone
    # `papertrace report` never said it at all.
    console.print(_provenance_line(results.converter))
    paths = write_reports(results, manifest, case / "out", png=png, scout=scout_res)
    for p in paths:
        console.print(f"  [green]✓[/green] {p.relative_to(case)}")


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
) -> None:
    """Full pipeline: ingest → refs → scout → check → highlight → report."""
    console.print(BANNER)
    email = _email(email)  # fail fast — before the ingest models load, not after
    # resolved once, here, and passed down by keyword: a stage that re-derived
    # its own folder could put the same question six times, or disagree
    case = _resolve_case(case, manuscript)
    _guard_case(case, manuscript)  # one case folder per paper — never mix two audits
    _open_case(case)
    # KEYWORDS ONLY, deliberately. These stages are Typer commands called as
    # plain functions, and Typer's declared defaults are OptionInfo objects
    # rather than the values they display. A positional call therefore breaks
    # silently the moment any stage gains a parameter: the arguments shift, the
    # shifted-in default is an OptionInfo that equals none of the expected
    # strings, and the stage takes a fallback branch. Adding `--case` to
    # `ingest` did exactly that — the backend became an OptionInfo and every
    # audit ingested as flat text while claiming layout-aware ingest.
    ingest(pdf=manuscript, out=case / "ingest" / "manuscript", case=case, backend=backend)
    # detected once, here, and handed to both consumers. `refs` detects for
    # itself when called alone, so forwarding the raw option left the scout
    # guessing by title on the very runs where the paper's DOI was sitting on
    # page 1 — and a wrong title match anchors the whole scan to another paper
    # without erroring.
    doi = _text_opt(doi) or _detected_doi(manuscript)
    refs(manuscript=manuscript, case=case, provided=provided, email=email,
         parse_only=False, backend=backend, doi=doi)
    if with_scout:
        scout(case=case, doi=doi, email=email)
    check(case=case, model=model)
    highlight(case=case, claim=None)
    report(case=case, png=png)
    # a four-minute run should not need scrolling to learn how the paper was
    # read, so the backend rides on the last line too
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    from .models import SourceMap

    backend_used = SourceMap.from_json(smap_path).converter if smap_path.exists() else "unknown"
    console.print(
        "\n[bold green]done[/bold green] — open "
        f"[cyan]{case/'out'/'report.md'}[/cyan] · read with [bold]{backend_used}[/bold]"
        " · the gap register is part of the result."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
