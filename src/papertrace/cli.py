"""PaperTrace CLI — batch mode.

`papertrace run` drives the whole pipeline; the individual commands
exist so each step can be run, inspected and re-run on its own. Interactive
reviewing lives in the Claude Code skills (`/review`), which call these same
commands.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import typer
from rich.console import Console

from .models import RefManifest, RunResults

app = typer.Typer(add_completion=False, rich_markup_mode="rich", no_args_is_help=True)
console = Console()

BANNER = r"""[bold]
  ┌──────────────────────────┐
  │  ▛▀▜ PaperTrace          │
  │  ▌█▐ every claim, traced │
  │  ▙▄▟ back to its source  │
  └──────────────────────────┘[/bold]
"""

STATUS_MARK = {
    "retrieved": "[green]✓[/green]",
    "provided": "[green]✓[/green]",
    "paywalled": "[yellow]⚠[/yellow]",
    "no_doi": "[yellow]⚠[/yellow]",
    "unpublished": "[yellow]⚠[/yellow]",
    "error": "[red]✗[/red]",
}


def _case_conflict(case: Path, manuscript_name: str) -> str | None:
    """A case folder belongs to one paper. Returns the previous paper's name
    when `case` already holds an audit of a different one, else None."""
    marker = case / "refs_manifest.json"
    if not marker.exists():
        return None
    previous = RefManifest.from_json(marker).manuscript
    return previous if previous != manuscript_name else None


def _guard_case(case: Path, manuscript: Path) -> None:
    if previous := _case_conflict(case, manuscript.name):
        console.print(
            f"[red]case folder [bold]{case}[/bold] already holds an audit of "
            f"[bold]{previous}[/bold].[/red]\n"
            f"One case per paper — give this one its own, e.g. "
            f"[cyan]-c {manuscript.stem}[/cyan], or delete [cyan]{case}/[/cyan] "
            f"to reuse the name. (Re-running the [i]same[/i] paper in its case is fine.)"
        )
        raise typer.Exit(2)


def _email(cli_value: str | None) -> str:
    # MANUSCRIPTAGENT_EMAIL is honored as a fallback for pre-rename setups
    email = (
        cli_value
        or os.environ.get("PAPERTRACE_EMAIL", "")
        or os.environ.get("MANUSCRIPTAGENT_EMAIL", "")
    )
    if not email:
        console.print(
            "[yellow]No contact email set — Unpaywall requires one "
            "(pass --email or set PAPERTRACE_EMAIL).[/yellow]"
        )
        raise typer.Exit(2)
    return email


@app.command()
def init(case: Path = typer.Argument(Path("case"), help="Case folder to create")) -> None:
    """Create a case folder skeleton (gitignored by design — keep manuscripts local)."""
    for sub in ("sources", "form", "ingest", "out/evidence"):
        (case / sub).mkdir(parents=True, exist_ok=True)
    (case / ".gitignore").write_text("*\n")
    console.print(BANNER)
    console.print(f"case folder ready: [cyan]{case}/[/cyan]")
    console.print("  put reference PDFs you already have into [cyan]sources/[/cyan]")
    console.print("  put your questions or form-field screenshots into [cyan]form/[/cyan]")


@app.command()
def ingest(
    pdf: Path = typer.Argument(..., exists=True, help="PDF to convert"),
    out: Path = typer.Option(None, "--out", "-o", help="Output dir (default case/ingest/<stem>)"),
    backend: str = typer.Option("auto", "--backend", help="auto | docling | pymupdf"),
) -> None:
    """PDF → clean.md + annotated.md + source_map.json (page + bbox provenance)."""
    from .ingest import ingest_pdf

    out = out or Path("case") / "ingest" / pdf.stem
    smap = ingest_pdf(pdf, out, backend=backend)
    by_type = {t: sum(1 for b in smap.blocks if b.type == t) for t in
               ("sectionheader", "text", "table", "picture", "list")}
    parts = " · ".join(f"{n} {t}" for t, n in by_type.items() if n)
    console.print(
        f"[green]✓[/green] {pdf.name} → {out}/ · {smap.pages} pages · "
        f"converter [cyan]{smap.converter}[/cyan] · {parts}"
    )
    if smap.converter == "pymupdf":
        console.print(
            "  [yellow]⚠ flat-text ingest — tables are linearized and figures invisible."
            " Install the layout backend: pip install 'papertrace[docling]'[/yellow]"
        )


@app.command()
def refs(
    manuscript: Path = typer.Argument(..., exists=True),
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    provided: Path = typer.Option(None, "--provided", help="Folder of PDFs you already have"),
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

    manifest = RefManifest(manuscript=manuscript.name, entries=entries)
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


@app.command()
def scout(
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    doi: str = typer.Option(None, "--doi", help="DOI of the paper itself (skips the title lookup)"),
    email: str = typer.Option(None, "--email", envvar=["PAPERTRACE_EMAIL", "MANUSCRIPTAGENT_EMAIL"]),
) -> None:
    """Scan Europe PMC for what the reference list doesn't know: literature
    published since the paper, and candidates that existed but went uncited."""
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


@app.command()
def check(
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    model: str = typer.Option(None, "--model", help="Model override for claude -p"),
) -> None:
    """Extract citation-backed claims and judge each against its cited source (claude -p)."""
    from .check import check_claims, claude_available, extract_claims

    if not claude_available():
        console.print(
            "[red]The `claude` CLI is required for batch checking[/red] — "
            "install Claude Code (https://claude.com/claude-code) and log in, "
            "or run the interactive `/review` skill instead."
        )
        raise typer.Exit(2)

    manifest = RefManifest.from_json(case / "refs_manifest.json")
    with console.status("extracting claims (cited + uncited)…"):
        claims, uncited = extract_claims(case, model)
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

    with console.status("reading claims against their cited pages…"):
        check_claims(claims, manifest, case, model, progress=tick)

    from .check import coverage_audit
    from .models import SourceMap

    coverage = coverage_audit(case, claims)
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    converter = SourceMap.from_json(smap_path).converter if smap_path.exists() else "pymupdf"

    results = RunResults(
        manuscript=manifest.manuscript,
        date=str(datetime.date.today()),
        refs_total=len(manifest.entries),
        refs_available=len(manifest.retrieved),
        converter=converter,
        claims=claims,
        uncited=uncited,
        coverage=coverage,
    )
    (case / "out").mkdir(parents=True, exist_ok=True)
    results.to_json(case / "out" / "results.json")

    c = results.counts()
    console.print(
        f"\n[bold]verdicts[/bold]  [green]● {c['supported']} supported[/green]   "
        f"[yellow]● {c['partial']} partial[/yellow]   [red]● {c['contradicted']} contradicted[/red]   "
        f"[dim]○ {c['not_retrieved']} not retrieved[/dim]"
    )
    n_text, n_missing = len(coverage["labels_in_text"]), len(coverage["missing"])
    if n_missing:
        console.print(
            f"[yellow]coverage: {n_text - n_missing}/{n_text} citation labels covered — "
            f"missing: {', '.join(coverage['missing'])}[/yellow]"
        )
    elif n_text:
        console.print(f"[green]coverage: all {n_text} citation labels covered[/green]")
    elif claims:
        console.print(
            "[yellow]coverage: no numbered citation markers found in the text — "
            "bare-superscript citation styles are not yet recognized; "
            "coverage not audited[/yellow]"
        )
    if uncited:
        console.print(f"[cyan]{len(uncited)} uncited assertions[/cyan] — see report section")


@app.command()
def highlight(
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    claim: int = typer.Option(None, "--claim", help="Only this claim id"),
) -> None:
    """Produce red-box evidence crops for every claim with a page anchor."""
    from .highlight import crop_for_claim

    results = RunResults.from_json(case / "out" / "results.json")
    out_dir = case / "out" / "evidence"
    done = 0
    for c in results.claims:
        if claim is not None and c.id != claim:
            continue
        img = crop_for_claim(c, case / "sources_resolved", case / "ingest", out_dir)
        if img is None and c.source_slug:
            # sources provided by the user live elsewhere — try the manifest path
            manifest = RefManifest.from_json(case / "refs_manifest.json")
            entry = next((e for e in manifest.entries if e.slug == c.source_slug), None)
            if entry and entry.pdf_path:
                src = Path(entry.pdf_path)
                tmp = case / "sources_resolved" / f"{c.source_slug}.pdf"
                if src.exists() and not tmp.exists():
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_bytes(src.read_bytes())
                    img = crop_for_claim(c, case / "sources_resolved", case / "ingest", out_dir)
        if img:
            c.evidence_image = str(Path(img).relative_to(case / "out"))
            done += 1
            console.print(f"  [green]✓[/green] claim {c.id}: {c.evidence_image}")
    results.to_json(case / "out" / "results.json")
    console.print(f"[bold]{done}[/bold] evidence crops written")


@app.command()
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


@app.command()
def run(
    manuscript: Path = typer.Argument(..., exists=True),
    case: Path = typer.Option(Path("case"), "--case", "-c"),
    provided: Path = typer.Option(None, "--provided", help="Folder of PDFs you already have"),
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
    ingest(manuscript, case / "ingest" / "manuscript", backend)
    refs(manuscript, case, provided, email, parse_only=False, backend=backend)
    if with_scout:
        scout(case, doi, email)
    check(case, model)
    highlight(case, None)
    report(case, png)
    console.print(
        "\n[bold green]done[/bold green] — open "
        f"[cyan]{case/'out'/'report.md'}[/cyan] · the gap register is part of the result."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
