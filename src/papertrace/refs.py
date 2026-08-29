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
# a parenthesised four-digit group is not automatically a year: journal
# citations carry issue numbers the same way — "Br. J. Radiol. 89 (1061)
# (2016)" made 1061 the year and slugged the entry `a-1061`
YEAR_RE = re.compile(r"\(((?:19|20)\d{2})\)|\b((?:19|20)\d{2})\b")

ProgressCb = Callable[[RefEntry], None]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def parse_references(text: str) -> list[RefEntry]:
    """Split a References section into numbered entries.

    Handles `1. Foo`, `[1] Foo` and `1 Foo` markers at line starts, plus the
    bracketed `[1] Foo` form **anywhere in a line**, and keeps only a strictly
    ascending sequence so stray numbers inside an entry (DOIs, page ranges)
    don't split it.

    The mid-line case is not exotic: Elsevier PDFs extract with entries running
    together, so `[2]` and `[3]` sit mid-line. Requiring a line start turned a
    34-reference list into one entry that swallowed the rest — and took its DOI
    from reference [2]. That is a mis-attribution, not a shortfall: the resolver
    would fetch the wrong paper and judge a claim against it.

    Only the *bracketed* form is allowed mid-line. A bare `12.` mid-sentence is
    ordinary prose, and splitting on it would invent entries; the ascending-run
    filter is the second guard behind that.
    """
    marker = re.compile(
        r"(?:(?<=\n)|\A)\s*\[?(\d{1,3})[\].:]?\s+"  # line start: `1.` `[1]` `1 `
        r"|\[(\d{1,3})\]\s+",  # bracketed, anywhere in the line
        re.M,
    )
    hits = [
        (m.start(), m.end(), int(m.group(1) or m.group(2)))
        for m in marker.finditer(text)
    ]

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
        if raw:
            entries.append(_entry(str(_num), raw))
    if not entries:
        entries = _parse_bulleted(text)
    return entries


def _parse_bulleted(text: str) -> list[RefEntry]:
    """Fallback for lists whose numerals the converter stripped.

    docling flattens some journals' numbered hanging-indent reference lists
    (e.g. Nature-family layouts) into plain bullets — number the bullets
    sequentially by document order instead of giving up with zero entries.
    """
    items: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "• ", "* ")):
            items.append(s[2:].strip())
        elif items and s:
            items[-1] += " " + s  # wrapped continuation of the previous entry
    return [
        _entry(str(i), re.sub(r"\s+", " ", raw).strip())
        for i, raw in enumerate(items, 1)
        if raw.strip()
    ]


def _entry(num: str, raw: str) -> RefEntry:
    e = RefEntry(num=num, raw=raw)
    if m := DOI_RE.search(raw):
        doi = m.group(0).rstrip(".,;")
        while doi.endswith(")") and doi.count(")") > doi.count("("):
            doi = doi[:-1].rstrip(".,;")
        e.doi = doi
    if m := YEAR_RE.search(raw):
        e.year = m.group(1) or m.group(2)
    e.slug = _slug(raw, e.year)
    return e


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


# journal names and boilerplate that appear on almost any first page —
# they must not let a wrong paper pass the title check
_TITLE_STOPWORDS = frozenset(
    {"commun", "nature", "science", "journal", "lancet", "article",
     "elsevier", "springer", "wiley", "volume", "press", "https"}
)


def _title_check_text(raw: str, page_text: str) -> str | None:
    """Does the retrieved first page look like the cited reference?

    Returns None on pass, else a human-readable reason. An empty/unreadable
    page passes: unverifiable is not the same as wrong.
    """
    page = re.sub(r"\s+", " ", page_text).lower()
    if not page.strip():
        return None
    tokens = set(re.findall(r"[a-z]{5,}", raw.lower())) - _TITLE_STOPWORDS
    if not tokens:
        return None
    found = sum(1 for t in tokens if t in page)
    if found / len(tokens) >= 0.35:
        return None
    return f"title check failed: {found}/{len(tokens)} reference tokens on the retrieved first page"


def _first_page_text(pdf_path: Path) -> str:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz
    try:
        with fitz.open(pdf_path) as doc:
            meta_title = (doc.metadata or {}).get("title") or ""
            return doc[0].get_text() + " " + meta_title
    except Exception:
        return ""  # corrupt/scanned/odd PDF — never crash resolution


def _title_check(entry: RefEntry, pdf_path: Path) -> str | None:
    return _title_check_text(entry.raw, _first_page_text(pdf_path))


def _accept(
    entry: RefEntry, client: httpx.Client, url: str, dest: Path, resolver: str, why: str
) -> bool:
    """Download one candidate copy and title-sanity-check it. On mismatch the
    bytes are discarded, the reason is recorded, and the chain continues."""
    if not _download_pdf(client, url, dest):
        return False
    if detail := _title_check(entry, dest):
        dest.unlink(missing_ok=True)
        entry.status = "mismatch"
        entry.reason = (
            f"retrieved PDF looks like a different paper ({detail}) — wrong or "
            "mistyped DOI in the reference? Drop the correct copy into sources/"
        )
        return False
    entry.status, entry.resolver, entry.pdf_path = "retrieved", resolver, str(dest)
    entry.reason = why
    return True


# Filenames that name supplemental material rather than the paper. Nothing
# shorter than five characters goes in here: `si` would reject the real slug
# `si-mohamed-2021`, and an author's name must never read as a marker.
_SUPPLEMENT_RE = re.compile(
    r"suppl|appendix|supporting[-_ ]?info|\besm\b|online[-_ ]?only", re.I
)


def _provided_candidates(entry: RefEntry, provided_dir: Path | None) -> list[Path]:
    """Every file in the folder that could be this reference, best first.

    Token containment stays loose on purpose — real filenames carry author lists
    and titles, and `tests/test_refs.py` pins that. What is tightened is the
    choice among the matches:

    * an exact `<slug>.pdf` wins outright;
    * otherwise the shortest stem, tie-broken by name. Shortest means fewest
      extra tokens, and the sort makes the answer the same on every machine —
      the old code took the first `glob` hit, which is filesystem order, so one
      folder could produce different audits in different places.

    Supplements are excluded rather than ranked last. Judging a claim against an
    appendix while calling it the cited source is the laundering this codebase
    exists to prevent, and returning nothing lets the online chain try for the
    real article instead.
    """
    if not provided_dir or not provided_dir.is_dir():
        return []
    slug = (entry.slug or "").lower()
    tokens = [t for t in slug.split("-") if len(t) > 3]
    if not tokens:
        return []
    matches = [
        pdf
        for pdf in sorted(provided_dir.glob("*.pdf"))
        if all(t in pdf.name.lower() for t in tokens)
        and not _SUPPLEMENT_RE.search(pdf.stem)
    ]
    return sorted(matches, key=lambda p: (p.stem.lower() != slug, len(p.stem), p.name))


def _match_provided(entry: RefEntry, provided_dir: Path | None) -> Path | None:
    candidates = _provided_candidates(entry, provided_dir)
    return candidates[0] if candidates else None


def resolve_entry(
    entry: RefEntry,
    dest_dir: Path,
    email: str,
    client: httpx.Client,
    provided_dir: Path | None = None,
) -> RefEntry:
    """Resolve one reference in place. Never raises — failures land in status/reason."""
    dest = dest_dir / f"{entry.slug}.pdf"

    if candidates := _provided_candidates(entry, provided_dir):
        provided = candidates[0]
        entry.status, entry.resolver = "provided", "user"
        entry.pdf_path = str(provided)
        others = f" ({len(candidates)} candidates matched; picked the closest name)" \
            if len(candidates) > 1 else ""
        # a provided file is title-checked like a downloaded one, but a failure
        # is DISCLOSED, not fatal: the user named this file, there is nothing to
        # fall back to, and a scanned PDF yields no text at all
        unconfirmed = _title_check(entry, provided)
        note = f" — unverified: {unconfirmed}" if unconfirmed else ""
        entry.reason = f"matched {provided.name} in your sources folder{others}{note}"
        return entry

    try:
        if arxiv := ARXIV_RE.search(entry.raw):
            url = f"https://arxiv.org/pdf/{arxiv.group(1)}"
            if _accept(entry, client, url, dest, "arxiv", "arXiv"):
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
            if _accept(entry, client, pdf_url, dest, "unpaywall", "open-access copy via Unpaywall"):
                return entry

        if pdf_url := _epmc_pdf(client, entry.doi):
            if _accept(entry, client, pdf_url, dest, "europepmc", "open-access copy via Europe PMC"):
                return entry

        if entry.status != "mismatch":  # a recorded mismatch is more informative
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
