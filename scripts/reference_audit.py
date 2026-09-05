#!/usr/bin/env python3
"""Measure the three readings of a paper's reference list, side by side.

Read-only and free: it ingests each PDF, counts what the body cites, parses the
printed list, asks Crossref what the publisher deposited, and prints the three
numbers with the verdict the reconciler would reach. No model calls, no
downloads, no case folder written.

This exists because the reconciliation rules are a design guess until they meet
a spread of journals. Two audited papers agreeing is not evidence that the rule
generalises — it is two data points from one publisher.

    python scripts/reference_audit.py ~/papers/*.pdf
    python scripts/reference_audit.py --email you@example.org paper.pdf

A row reading `parse 43 · crossref 41 · body [1]-[41] → crossref` is the failure
this was built for: the parse invented two references and the deposit caught it.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.ingest import ingest_pdf, references_span  # noqa: E402
from papertrace.models import (  # noqa: E402
    citation_labels,
    is_references_heading,
    paper_title,
)
from papertrace.refs import (  # noqa: E402
    _client,
    crossref_deposit,
    deposit_corroborates,
    deposit_is_this_paper,
    parse_references,
    reconcile,
)
from papertrace.wizard import detect_doi  # noqa: E402


def _body_labels(smap) -> set[str]:
    body: list[str] = []
    for b in smap.blocks:
        if is_references_heading(b.type, b.text):
            break
        body.append(b.text)
    return citation_labels("\n".join(body))


def _span(labels: set[str]) -> str:
    if not labels:
        return "none"
    nums = sorted(int(x) for x in labels)
    gaps = [n for n in range(1, nums[-1] + 1) if n not in set(nums)]
    return f"[1]-[{nums[-1]}]" + (f" ({len(gaps)} gaps)" if gaps else "")


def audit(pdf: Path, email: str, backend: str) -> dict:
    row: dict = {"pdf": pdf.name}
    with tempfile.TemporaryDirectory() as scratch:
        smap = ingest_pdf(pdf, Path(scratch), backend=backend)
    row["converter"] = smap.converter
    body = _body_labels(smap)
    row["body"] = _span(body)

    refs_text, resumed = references_span(smap)
    parsed = parse_references(refs_text)
    row["parsed"] = len(parsed)
    row["resumed"] = resumed

    doi = detect_doi(pdf)
    row["doi"] = doi or "—"
    with _client() as client:
        deposit = crossref_deposit(client, doi, email)
    row["publisher"] = deposit.publisher or "—"
    row["crossref"] = len(deposit.entries) if deposit.entries else None
    row["deposited"] = deposit.deposited
    row["unrenderable"] = deposit.unrenderable
    row["absent"] = deposit.absent

    # the same gate the `refs` command applies — a diagnostic that measures a
    # different pipeline than the tool is measuring nothing
    identity = (
        deposit_is_this_paper(paper_title(smap), deposit.title) if deposit.entries else None
    )
    corroboration = (
        deposit_corroborates(deposit.entries, parsed)
        if identity is None and deposit.entries else None
    )
    row["identity"] = (
        "confirmed" if identity
        else "WRONG PAPER" if identity is False
        else f"confirmed by bibliography ({corroboration.found}/{corroboration.total})"
        if corroboration and corroboration.confirms
        else "unverified"
    )
    usable = not deposit.unrenderable and identity is not False
    candidate = (deposit.entries or None) if usable else None
    absent = deposit.absent or (
        f"the DOI used ({doi}) belongs to a record titled \u201c{deposit.title}\u201d, "
        "which is not this paper" if identity is False else ""
    )
    _entries, rec = reconcile(body, candidate, parsed, crossref_absent=absent)
    row["chosen"] = rec.source
    row["verified"] = rec.verified
    row["note"] = rec.note
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--email", default=os.environ.get("PAPERTRACE_EMAIL", ""),
                    help="contact address for Crossref's polite pool")
    ap.add_argument("--backend", default="auto", help="auto | docling | pymupdf")
    args = ap.parse_args()

    rows = []
    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"  ?  {pdf} — not found")
            continue
        try:
            row = audit(pdf, args.email, args.backend)
        except Exception as e:  # noqa: BLE001 — a diagnostic reports, it does not stop
            print(f"  !  {pdf.name} — {type(e).__name__}: {e}")
            continue
        rows.append(row)
        mark = "OK " if row["verified"] else "  ⚠"
        cr = row["crossref"] if row["crossref"] is not None else "—"
        partial = f" ({row['unrenderable']} unreadable of {row['deposited']})" if row["unrenderable"] else ""
        print(
            f"{mark} {row['pdf'][:44]:<44} "
            f"parse {row['parsed']:>3} · crossref {str(cr):>3}{partial} · "
            f"body {row['body']:<16} → {row['chosen']}"
        )
        print(f"     {row['publisher'][:40]:<40} {row['converter']}"
              f" · doi identity {row['identity']}"
              f"{' · resumed' if row['resumed'] else ''}")
        if not row["verified"]:
            print(f"     {row['note']}")

    if rows:
        ok = sum(1 for r in rows if r["verified"])
        deposits = sum(1 for r in rows if r["crossref"] is not None)
        print(f"\n{ok}/{len(rows)} numberings confirmed · "
              f"{deposits}/{len(rows)} publishers deposited a reference list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
