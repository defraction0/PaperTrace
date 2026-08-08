"""Scout the literature around a paper — what its reference list doesn't know.

Two registers, both candidates for the user's judgement, never accusations:

- ``newer``      — appeared after the paper: articles that cite it, plus later
                   keyword hits. What the paper could not have known.
- ``overlooked`` — existed by the paper's year but is absent from its
                   reference list. What it could have cited.

Search-based (Europe PMC) and therefore incomplete by construction — absence
from these lists proves nothing. Network failures soft-fail: the error is
recorded in ``scout.json`` and the pipeline continues.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import httpx

from .models import RefManifest, ScoutHit, ScoutResults, SourceMap
from .refs import UA

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
NEWER_CAP = 25
OVERLOOKED_CAP = 15

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "using", "based", "toward",
    "towards", "study", "analysis", "review", "novel", "between", "among",
    "their", "this", "that", "after", "before", "during", "versus",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ua(email: str) -> str:
    return UA.format(email=email) if email else UA.replace("; mailto:{email}", "")


def _client(email: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": _ua(email)},
        timeout=25.0,
        follow_redirects=True,
        transport=transport,
    )


def _year(v) -> int | None:
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower())


def _keywords(title: str, n: int = 4) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", title.lower())
    return [w for w in words if w not in _STOPWORDS][:n]


def _hit(d: dict, via: str) -> ScoutHit:
    return ScoutHit(
        title=" ".join((d.get("title") or "").split()).rstrip("."),
        year=_year(d.get("pubYear")),
        doi=(d.get("doi") or "").lower(),
        via=via,
        journal=d.get("journalTitle") or d.get("journalAbbreviation") or "",
        authors=d.get("authorString") or "",
    )


def _keys(h: ScoutHit) -> set[str]:
    """Both identities of a hit — DOI and normalized title. Dedup must use the
    union: Europe PMC citation records often lack the DOI that the same
    article carries in search results, so either key alone lets dups through."""
    return {k for k in (h.doi, _norm_title(h.title)) if k}


def _probably_cited(h: ScoutHit, cited_dois: set[str], cited_slugs: set[str]) -> bool:
    """DOI match against the reference list, or first-author-lastname + year
    matching a cited slug (catches references whose entry carries no DOI)."""
    if h.doi and h.doi in cited_dois:
        return True
    if h.authors and h.year:
        last = re.split(r"[\s,]", h.authors.strip())[0]
        last = re.sub(r"[^A-Za-z\-]", "", last).lower()
        if last and f"{last}-{h.year}" in cited_slugs:
            return True
    return False


# ---------------------------------------------------------------------------
# Europe PMC calls
# ---------------------------------------------------------------------------


def _search(client: httpx.Client, query: str, page_size: int) -> list[dict]:
    r = client.get(
        f"{EPMC}/search",
        params={"query": query, "format": "json", "pageSize": page_size},
    )
    r.raise_for_status()
    return r.json().get("resultList", {}).get("result", [])


def _citing(client: httpx.Client, source: str, ext_id: str, page_size: int = 100) -> list[dict]:
    if not source or not ext_id:
        return []
    r = client.get(
        f"{EPMC}/{source}/{ext_id}/citations",
        params={"format": "json", "pageSize": page_size},
    )
    if r.status_code != 200:
        return []
    return r.json().get("citationList", {}).get("citation", [])


def _resolve_paper(client: httpx.Client, doi: str | None, title: str) -> dict | None:
    """Identify the paper itself in Europe PMC — by DOI when given, else by
    exact-title search. Returns id/source/doi/title/year/via, or None."""
    queries: list[tuple[str, str]] = []
    if doi:
        queries.append((f'DOI:"{doi}"', "doi"))
    if title:
        safe = title.replace('"', " ").strip()
        queries.append((f'TITLE:"{safe}"', "title"))
    for q, via in queries:
        hits = _search(client, q, 1)
        if hits:
            h = hits[0]
            return {
                "id": h.get("id", ""),
                "source": h.get("source", ""),
                "doi": (h.get("doi") or "").lower(),
                "title": " ".join((h.get("title") or "").split()).rstrip("."),
                "year": _year(h.get("pubYear")),
                "via": via,
            }
    return None


def _title_from_case(case: Path) -> str:
    """Best-effort paper title from the ingest output — the first substantial
    section header, else the first substantial text block. `--doi` overrides."""
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    if not smap_path.exists():
        return ""
    smap = SourceMap.from_json(smap_path)
    for b in smap.blocks:
        if b.type == "sectionheader" and len(b.text.strip()) >= 15:
            return " ".join(b.text.split())[:220]
    for b in smap.blocks:
        if b.type == "text" and len(b.text.strip()) >= 25:
            return " ".join(b.text.split())[:220]
    return ""


# ---------------------------------------------------------------------------
# the scan
# ---------------------------------------------------------------------------


def scout_case(
    case: Path,
    doi: str | None = None,
    email: str = "",
    transport: httpx.BaseTransport | None = None,
    page_size: int = 25,
) -> ScoutResults:
    """Run the full scan for a case. Never raises on network trouble —
    failures land in `.error` and the registers stay honest (possibly empty)."""
    res = ScoutResults(date=str(datetime.date.today()))

    manifest = RefManifest.from_json(case / "refs_manifest.json")
    cited_dois = {e.doi.lower() for e in manifest.entries if e.doi}
    cited_slugs = {e.slug for e in manifest.entries if e.slug}

    try:
        with _client(email, transport) as client:
            paper = _resolve_paper(client, doi, _title_from_case(case))
            if paper is None:
                res.error = (
                    "paper not identified in Europe PMC — pass --doi to pin it "
                    "(title heuristics can miss)"
                )
                return res
            res.paper_title = paper["title"]
            res.paper_doi = paper["doi"]
            res.paper_year = paper["year"]
            res.resolved_via = paper["via"]

            self_keys = {k for k in (paper["doi"], _norm_title(paper["title"])) if k}
            seen: set[str] = set()

            # articles that cite the paper are post-publication by definition
            for c in _citing(client, paper["source"], paper["id"]):
                h = _hit(c, via="citing")
                ks = _keys(h)
                if not ks or ks & seen or ks & self_keys:
                    continue
                seen |= ks
                res.newer.append(h)

            # keyword neighbourhood, split by the paper's year
            kws = _keywords(paper["title"])
            res.query = " AND ".join(kws)
            if kws:
                for d in _search(client, res.query, page_size):
                    h = _hit(d, via="search")
                    ks = _keys(h)
                    if not ks or ks & seen or ks & self_keys:
                        continue
                    seen |= ks
                    if h.year is None:
                        continue  # undatable → can't be placed honestly
                    if res.paper_year and h.year > res.paper_year:
                        res.newer.append(h)
                    elif not _probably_cited(h, cited_dois, cited_slugs):
                        res.overlooked.append(h)

            res.newer.sort(key=lambda h: (-(h.year or 0), h.title))
            res.overlooked.sort(key=lambda h: (-(h.year or 0), h.title))
            res.newer = res.newer[:NEWER_CAP]
            res.overlooked = res.overlooked[:OVERLOOKED_CAP]
    except httpx.HTTPError as e:
        res.error = f"network: {type(e).__name__} — scan incomplete"
    return res
