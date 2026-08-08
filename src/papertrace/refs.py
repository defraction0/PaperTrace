"""Reference resolution with an honest manifest.

Chain: DOI-in-text → Crossref lookup → Unpaywall → Europe PMC → arXiv.
Downloads open-access copies only — a paywalled reference stays `paywalled`,
with the reason recorded. `not obtainable` is a result, not a failure: the
fact-check step reports those claims as unverifiable instead of guessing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import httpx

from .models import RefEntry

UA = "PaperTrace/0.3 (+https://github.com/defraction0/PaperTrace; mailto:{email})"
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
ARXIV_RE = re.compile(r"arxiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
YEAR_RE = re.compile(r"\((\d{4})\)|\b(19|20)\d{2}\b")

ProgressCb = Callable[[RefEntry], None]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_references(text: str) -> list[RefEntry]:
    """Split a References section into numbered entries.

    Handles `1. Foo`, `[1] Foo` and `1 Foo` markers at line starts, and keeps
    only a strictly ascending sequence so stray numbers inside an entry (DOIs,
    page ranges) don't split it.
    """
    marker = re.compile(r"(?:(?<=\n)|\A)\s*\[?(\d{1,3})[\].:]?\s+", re.M)
    hits = [(m.start(), m.end(), int(m.group(1))) for m in marker.finditer(text)]

    seq: list[tuple[int, int, int]] = []
    expected = 1
    for start, end, num in hits:
        if num == expected:
            seq.append((start, end, num))
            expected += 1

    entries: list[RefEntry] = []
    for i, (_start, end, _num) in enumerate(seq):
        stop = seq[i + 1][0] if i + 1 < len(seq) else len(text)
        raw = re.sub(r"\s+", " ", text[end:stop]).strip()
        if not raw:
            continue
        e = RefEntry(num=str(_num), raw=raw)
        if m := DOI_RE.search(raw):
            doi = m.group(0).rstrip(".,;")
            while doi.endswith(")") and doi.count(")") > doi.count("("):
                doi = doi[:-1].rstrip(".,;")
            e.doi = doi
        if m := YEAR_RE.search(raw):
            e.year = m.group(1) or m.group(0)
        e.slug = _slug(raw, e.year)
        entries.append(e)
    return entries


def _slug(raw: str, year: str | None) -> str:
    first = re.split(r"[,\s]", raw.strip(), maxsplit=1)[0]
    first = re.sub(r"[^A-Za-z\-]", "", first).lower() or "ref"
    return f"{first}-{year}" if year else first


# ---------------------------------------------------------------------------
# resolution chain
# ---------------------------------------------------------------------------


def _client(email: str) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA.format(email=email)},
        timeout=25.0,
        follow_redirects=True,
    )


def _crossref_doi(client: httpx.Client, raw: str) -> str | None:
    r = client.get(
        "https://api.crossref.org/works",
        params={"query.bibliographic": raw[:250], "rows": 1},
    )
    r.raise_for_status()
    items = r.json().get("message", {}).get("items", [])
    return items[0].get("DOI") if items else None


def _unpaywall_pdf(client: httpx.Client, doi: str, email: str) -> str | None:
    r = client.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email})
    if r.status_code != 200:
        return None
    data = r.json()
    loc = data.get("best_oa_location") or {}
    return loc.get("url_for_pdf")


def _epmc_pdf(client: httpx.Client, doi: str) -> str | None:
    r = client.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        params={"query": f'DOI:"{doi}"', "format": "json", "pageSize": 1},
    )
    if r.status_code != 200:
        return None
    hits = r.json().get("resultList", {}).get("result", [])
    if not hits:
        return None
    pmcid = hits[0].get("pmcid")
    if not pmcid:
        return None
    return f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf"


def _download_pdf(client: httpx.Client, url: str, dest: Path) -> bool:
    try:
        r = client.get(url)
        if r.status_code != 200 or not r.content.startswith(b"%PDF"):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except httpx.HTTPError:
        return False


def _match_provided(entry: RefEntry, provided_dir: Path | None) -> Path | None:
    if not provided_dir or not provided_dir.is_dir():
        return None
    tokens = [t for t in (entry.slug or "").split("-") if len(t) > 3]
    for pdf in provided_dir.glob("*.pdf"):
        name = pdf.name.lower()
        if tokens and all(t in name for t in tokens):
            return pdf
    return None


def resolve_entry(
    entry: RefEntry,
    dest_dir: Path,
    email: str,
    client: httpx.Client,
    provided_dir: Path | None = None,
) -> RefEntry:
    """Resolve one reference in place. Never raises — failures land in status/reason."""
    dest = dest_dir / f"{entry.slug}.pdf"

    if provided := _match_provided(entry, provided_dir):
        entry.status, entry.resolver = "provided", "user"
        entry.pdf_path = str(provided)
        entry.reason = f"matched {provided.name} in your sources folder"
        return entry

    try:
        if arxiv := ARXIV_RE.search(entry.raw):
            url = f"https://arxiv.org/pdf/{arxiv.group(1)}"
            if _download_pdf(client, url, dest):
                entry.status, entry.resolver, entry.pdf_path = "retrieved", "arxiv", str(dest)
                entry.reason = "arXiv"
                return entry

        if not entry.doi:
            try:
                entry.doi = _crossref_doi(client, entry.raw)
                if entry.doi:
                    entry.resolver = "crossref"
            except httpx.HTTPError:
                pass  # Crossref down is not fatal — later steps may still work

        if not entry.doi:
            entry.status, entry.reason = "no_doi", "no DOI found in text or via Crossref"
            return entry

        if pdf_url := _unpaywall_pdf(client, entry.doi, email):
            if _download_pdf(client, pdf_url, dest):
                entry.status, entry.resolver, entry.pdf_path = "retrieved", "unpaywall", str(dest)
                entry.reason = "open-access copy via Unpaywall"
                return entry

        if pdf_url := _epmc_pdf(client, entry.doi):
            if _download_pdf(client, pdf_url, dest):
                entry.status, entry.resolver, entry.pdf_path = "retrieved", "europepmc", str(dest)
                entry.reason = "open-access copy via Europe PMC"
                return entry

        entry.status = "paywalled"
        entry.reason = "DOI resolved but no legal open-access copy found"
    except httpx.HTTPError as e:
        entry.status, entry.reason = "error", f"network: {type(e).__name__}"
    return entry


def resolve_all(
    entries: list[RefEntry],
    dest_dir: Path,
    email: str,
    provided_dir: Path | None = None,
    progress: ProgressCb | None = None,
) -> list[RefEntry]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with _client(email) as client:
        for entry in entries:
            resolve_entry(entry, dest_dir, email, client, provided_dir)
            if progress:
                progress(entry)
    return entries
