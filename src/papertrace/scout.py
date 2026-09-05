"""Scout the literature around a paper — what its reference list doesn't know.

Three registers, all candidates for the user's judgement, never accusations:

- ``newer``      — appeared after the paper: articles that cite it, plus later
                   keyword hits. What the paper could not have known.
- ``overlooked`` — in print *before* the paper's year and absent from its
                   reference list. What it could have cited.
- ``same_year``  — the paper's own year. Split out because it answers neither
                   question: it may have appeared after submission, so it is
                   not a citation the authors owed, and it did not come after,
                   so it is not literature published since.

Search-based (Europe PMC) and therefore incomplete by construction — absence
from these lists proves nothing. Network failures soft-fail: the error is
recorded in ``scout.json`` and the pipeline continues.
"""

from __future__ import annotations

import datetime
import html
import re
from pathlib import Path

import httpx

from .models import (
    RefManifest,
    ScoutHit,
    ScoutResults,
    SourceMap,
    paper_title,
    titles_match,
)
from .refs import UA

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
NEWER_CAP = 25
OVERLOOKED_CAP = 15

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "using", "based", "toward",
    "towards", "study", "analysis", "review", "novel", "between", "among",
    "their", "this", "that", "after", "before", "during", "versus",
    # verbs and framing nouns that state what a paper CLAIMS, not what it is
    # about. `improves` matched a stroke abstract shouting "IMPROVES" at a
    # pancreatic-cancer paper, which is how this list grew.
    "improve", "improves", "improved", "improving", "improvement",
    "increase", "increases", "increased", "reduce", "reduces", "reduced",
    "enhance", "enhances", "enhanced", "enables", "enabling",
    "assessment", "evaluation", "comparison", "investigation",
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
    """The n most specific-looking words of a title, for the neighbour search.

    Ranked by length, not by position. Taking the first n searched the opening
    of the title and never reached its subject: "Image registration improves
    inter-reader agreement ... in CT assessment of pancreas adenocarcinoma"
    produced `image AND registration AND improves AND inter-reader`, so the
    query described a method and omitted the disease entirely.

    Length is a proxy for topical specificity and nothing more — `adenocarcinoma`
    over `image`. It is a heuristic, but it is one rule rather than a word list
    that has to grow with every title style. The stop list only holds words that
    carry no topic in any paper; guessing at more is how a filter starts
    dropping real subject terms.
    """
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", title.lower())
    seen: dict[str, int] = {}
    for i, w in enumerate(words):
        if w not in _STOPWORDS and w not in seen:
            seen[w] = i
    ranked = sorted(seen, key=lambda w: (-len(w), seen[w]))
    return ranked[:n]


def _hit(d: dict, via: str) -> ScoutHit:
    # Europe PMC escapes the markup its titles carry, so `CTV<sub>boost</sub>`
    # arrives as `CTV&lt;sub&gt;boost&lt;/sub&gt;` and was rendered verbatim
    # into the report. Decoded once, here, where every hit is built.
    return ScoutHit(
        title=" ".join(html.unescape(d.get("title") or "").split()).rstrip("."),
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
    """The paper's title from the ingest output on disk. `--doi` overrides.

    The rule itself lives in `models.paper_title`, because `refs` needs the same
    title to ask whether a Crossref record is this paper.
    """
    smap_path = case / "ingest" / "manuscript" / "source_map.json"
    if not smap_path.exists():
        return ""
    return paper_title(SourceMap.from_json(smap_path))


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
                # Which failure this was decides what the reader should do, and
                # the two are not the same fact. Telling an operator who just
                # passed --doi to pass --doi sent them to verify by hand what
                # the tool already knew.
                if doi:
                    res.paper_doi = doi  # so the artifact shows what was tried
                    res.error = (
                        f"Europe PMC returned no record for DOI {doi}, so this paper is "
                        "not indexed there — usual for an in-press or pre-proof article. "
                        "Both registers below are empty for want of a starting point, "
                        "which is absence of data, not a clean literature search"
                    )
                else:
                    res.error = (
                        "paper not identified in Europe PMC — pass --doi to pin it "
                        "(title heuristics can miss)"
                    )
                return res
            res.paper_title = paper["title"]
            res.paper_doi = paper["doi"]
            res.paper_year = paper["year"]
            res.resolved_via = paper["via"]

            # Is the record this paper? `resolved_via == "doi"` used to stand in
            # for "identified reliably", and it stopped meaning that when `run`
            # began reading the DOI off page 1 — a funder, data-availability or
            # erratum DOI resolves to somebody else's paper, and both registers
            # would then describe that paper while the artifact said `doi`.
            own_title = _title_from_case(case)
            identity = titles_match(own_title, paper["title"])
            res.paper_identity = (
                "confirmed" if identity else "mismatch" if identity is False else "unverified"
            )
            if identity is False:
                # The registers ARE the finding, so they are not built from a
                # record this tool can see is not the paper. Empty-and-disclosed,
                # like every other unreadable source here.
                res.error = (
                    f"the DOI {doi} resolves to \u201c{paper['title']}\u201d, which is "
                    "not this paper — nothing was scanned, because both registers would "
                    "have described that paper instead. Check the DOI on the paper's "
                    "first page, or pass the right one with --doi"
                )
                return res

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
                    elif _probably_cited(h, cited_dois, cited_slugs):
                        continue
                    elif res.paper_year and h.year == res.paper_year:
                        # its own year is neither "since" nor "should have
                        # known" — see ScoutResults for why it gets a register
                        res.same_year.append(h)
                    else:
                        res.overlooked.append(h)

            for reg in (res.newer, res.overlooked, res.same_year):
                reg.sort(key=lambda h: (-(h.year or 0), h.title))
            res.newer = res.newer[:NEWER_CAP]
            res.overlooked = res.overlooked[:OVERLOOKED_CAP]
            res.same_year = res.same_year[:OVERLOOKED_CAP]
    except httpx.HTTPError as e:
        res.error = f"network: {type(e).__name__} — scan incomplete"
    return res
