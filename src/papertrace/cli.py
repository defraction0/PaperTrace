"""PaperTrace CLI — batch mode.

`papertrace run` drives the whole pipeline; the individual commands
exist so each step can be run, inspected and re-run on its own. Interactive
reviewing lives in the Claude Code skills (`/review`), which call these same
commands.
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from .models import RefManifest, RunResults, manuscript_fingerprint

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


def _guard_case(case: Path, manuscript: Path) -> None:
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
        # uninformed and less usable, and it self-heals on the next `refs`
        console.print(
            "[yellow]⚠ this case folder predates content hashing, so its identity is "
            "unverified — only the file name was compared. A different file with the "
            "same name would not be caught. Re-running `papertrace refs` fixes it.[/yellow]"
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
    for sub in ("sources", "form", "ingest", "out/evidence"):
        (case / sub).mkdir(parents=True, exist_ok=True)
    (case / ".gitignore").write_text("*\n")
    console.print(BANNER)
    console.print(f"case folder ready: [cyan]{case}/[/cyan]")
    console.print("  put reference PDFs you already have into [cyan]sources/[/cyan]")
    console.print("  put your questions or form-field screenshots into [cyan]form/[/cyan]")


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
    out = out or (case or Path("case")) / "ingest" / pdf.stem
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
def refs(
    manuscript: Path = typer.Argument(..., exists=True),
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    provided: Path = typer.Option(
        None, "--provided",
        help="Folder of reference PDFs you already have; files match by name "
             "<firstauthor>-<year>.pdf (e.g. pyrros-2023.pdf)",
    ),
    email: str = typer.Option(None, "--email", envvar=["PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"]),
    parse_only: bool = typer.Option(False, "--parse-only", help="List references, no network"),
    backend: str = typer.Option("auto", "--backend", help="auto | docling | pymupdf"),
) -> None:
    """Parse the References section, then retrieve open-access copies with an honest manifest."""
    from .ingest import ingest_pdf, references_section
    from .models import SourceMap
    from .refs import parse_references, resolve_all

    ingest_dir = case / "ingest" / "manuscript"
    if (ingest_dir / "source_map.json").exists():
        smap = SourceMap.from_json(ingest_dir / "source_map.json")
    else:
        smap = ingest_pdf(manuscript, ingest_dir, backend=backend)

    entries = parse_references(references_section(smap))
    if not entries:
        console.print("[red]No numbered references found — is there a References section?[/red]")
        raise typer.Exit(1)
    console.print(f"parsed [bold]{len(entries)}[/bold] numbered references")
    if parse_only:
        for e in entries:
            console.print(f"  [{e.num:>3}] {e.raw[:90]}")
        return

    _guard_case(case, manuscript)  # one case folder per paper
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
    )
    case.mkdir(parents=True, exist_ok=True)
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
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    doi: str = typer.Option(None, "--doi", help="DOI of the paper itself (skips the title lookup)"),
    email: str = typer.Option(None, "--email", envvar=["PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"]),
) -> None:
    """Scan Europe PMC for literature the reference list doesn't know."""
    from .scout import scout_case

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
        console.print(
            f"paper: [bold]{res.paper_title[:80]}[/bold] ({res.paper_year or '?'})"
            f" · [dim]identified via {res.resolved_via}"
            f"{' — wrong paper? pass --doi' if res.resolved_via == 'title' else ''}[/dim]"
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
    console.print(
        "[dim]search-based — absence from these lists proves nothing; presence is a"
        " candidate for your judgement, not an accusation.[/dim]"
    )


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def check(
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    model: str = typer.Option(None, "--model", help="Model override for claude -p"),
) -> None:
    """Extract citation-backed claims and judge each against its cited source (claude -p)."""
    from .check import Truncations, check_claims, claude_available, extract_claims

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
        marks = " ".join(
            {"supported": "[green]●[/green]", "partial": "[yellow]●[/yellow]",
             "contradicted": "[red]●[/red]"}.get(c.verdict, "○")
            for c in group
        )
        console.print(f"  checked against [cyan]{slug}[/cyan]: {marks}")

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

    c = results.counts()
    unchecked = f"   [red]⚠ {c['unchecked']} unchecked (check failed)[/red]" if c["unchecked"] else ""
    console.print(
        f"\n[bold]verdicts[/bold]  [green]● {c['supported']} supported[/green]   "
        f"[yellow]● {c['partial']} partial[/yellow]   [red]● {c['contradicted']} contradicted[/red]   "
        f"[dim]○ {c['not_retrieved']} not retrieved[/dim]{unchecked}"
    )
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
def highlight(
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    claim: int = typer.Option(None, "--claim", help="Only this claim id"),
) -> None:
    """Produce red-box evidence crops for every claim with a page anchor."""
    from .highlight import crop_for_anchor, source_page_count

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
                if a.anchor_located:
                    console.print(f"  [green]✓[/green] {tag}: {a.evidence_image}")
                else:
                    console.print(
                        f"  [yellow]○ {tag}: {a.evidence_image} — no anchor phrase "
                        f"found on the page; crop written unboxed[/yellow]"
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
        # the claim-level evidence_image must follow the deciding judgement, or
        # the crop shown beside the headline belongs to a different source
        c.apply_headline()
    results.to_json(case / "out" / "results.json")
    console.print(f"[bold]{done}[/bold] evidence crops written")


@app.command(rich_help_panel="Pipeline stages — `run` calls these in order")
def report(
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    png: bool = typer.Option(
        False, "--png/--no-png",
        help="Also export PNG images of the report looks (one-time: playwright install chromium)",
    ),
) -> None:
    """Render report.md + the editor/terminal looks from results.json."""
    from .models import ScoutResults
    from .report import write_reports

    results = RunResults.from_json(case / "out" / "results.json")
    manifest_path = case / "refs_manifest.json"
    manifest = RefManifest.from_json(manifest_path) if manifest_path.exists() else None
    scout_path = case / "out" / "scout.json"
    scout_res = ScoutResults.from_json(scout_path) if scout_path.exists() else None
    paths = write_reports(results, manifest, case / "out", png=png, scout=scout_res)
    for p in paths:
        console.print(f"  [green]✓[/green] {p.relative_to(case)}")


@app.command(rich_help_panel="Start here")
def run(
    manuscript: Path = typer.Argument(..., exists=True),
    case: Path = typer.Option(Path("case"), "--case", "-c"),
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
    doi: str = typer.Option(None, "--doi", help="DOI of the paper itself, for the scout step"),
) -> None:
    """Full pipeline: ingest → refs → scout → check → highlight → report."""
    console.print(BANNER)
    email = _email(email)  # fail fast — before the ingest models load, not after
    _guard_case(case, manuscript)  # one case folder per paper — never mix two audits
    # KEYWORDS ONLY, deliberately. These stages are Typer commands called as
    # plain functions, and Typer's declared defaults are OptionInfo objects
    # rather than the values they display. A positional call therefore breaks
    # silently the moment any stage gains a parameter: the arguments shift, the
    # shifted-in default is an OptionInfo that equals none of the expected
    # strings, and the stage takes a fallback branch. Adding `--case` to
    # `ingest` did exactly that — the backend became an OptionInfo and every
    # audit ingested as flat text while claiming layout-aware ingest.
    ingest(pdf=manuscript, out=case / "ingest" / "manuscript", case=case, backend=backend)
    refs(manuscript=manuscript, case=case, provided=provided, email=email,
         parse_only=False, backend=backend)
    if with_scout:
        scout(case=case, doi=doi, email=email)
    check(case=case, model=model)
    highlight(case=case, claim=None)
    report(case=case, png=png)
    console.print(
        "\n[bold green]done[/bold green] — open "
        f"[cyan]{case/'out'/'report.md'}[/cyan] · the gap register is part of the result."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
