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

from . import __version__
from .models import RefEntry, looks_like_reference

# Two user agents on purpose. The contact address is sent ONLY to the services
# that ask for one — Unpaywall requires it, Crossref's polite pool uses it. One
# shared client carrying `mailto:` sent it to Europe PMC, arXiv and whatever
# third party hosts the PDF, while the wizard disclosed two recipients.
# derived, not written out: this said 0.3 while the package was 0.3.1, and an
# identifier that misstates its version is worse than useless to the service
# on the other end of a polite-pool request
UA = f"PaperTrace/{__version__} (+https://github.com/defraction0/PaperTrace)"
UA_CONTACT = (
    f"PaperTrace/{__version__} (+https://github.com/defraction0/PaperTrace; mailto:{{email}})"
)


def _contact(email: str) -> dict[str, str]:
    """Per-request header for the two services that want a contact address."""
    return {"User-Agent": UA_CONTACT.format(email=email)}
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
    for i, (start, end, _num) in enumerate(seq):
        stop = seq[i + 1][0] if i + 1 < len(seq) else len(text)
        raw = re.sub(r"\s+", " ", text[end:stop]).strip()
        if not raw:
            continue
        e = _entry(str(_num), raw)
        # This label occurs again inside its own span, so one of the two is
        # printed in a title and we cannot tell which. Guessing either way is a
        # wrong-paper route: taking the first splices reference N-1's tail onto
        # entry N (and its DOI with it), taking the last truncates a real entry
        # whose text repeats its own label. Disclose instead.
        if any(start < s < stop and n == _num for s, _e, n in hits):
            e.boundary_ambiguous = True
            e.doi = None
            e.reason = (
                f"reference boundary ambiguous — the label [{_num}] appears more than once "
                "before the next reference, so where this entry begins is a guess; "
                "not resolved rather than risk judging a claim against the wrong paper"
            )
        entries.append(e)
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


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": UA},
        timeout=25.0,
        follow_redirects=True,
    )


def _crossref_doi(client: httpx.Client, raw: str, email: str) -> str | None:
    r = client.get(
        "https://api.crossref.org/works",
        params={"query.bibliographic": raw[:250], "rows": 1},
        headers=_contact(email),
    )
    r.raise_for_status()
    items = r.json().get("message", {}).get("items", [])
    return items[0].get("DOI") if items else None


def _unpaywall_pdf(client: httpx.Client, doi: str, email: str) -> str | None:
    r = client.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email},
                   headers=_contact(email))
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

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)


def _title_tokens(raw: str) -> set[str]:
    """The reference's own distinctive words — URLs removed first.

    A URL is not part of a title, and a *tracking parameter* least of all:
    `?utm_source=chatgpt.com` on a cited news page contributed `chatgpt` and
    `source` to this set, and the wrong paper Crossref returned was an
    editorial about ChatGPT. Path segments do the same from the other side,
    inflating the denominator with `firstmedical`, `assuranceprogram` and
    `publications` — words no first page will carry, so they dilute the ratio
    the check is measured on.
    """
    return set(re.findall(r"[a-z]{5,}", _URL_RE.sub(" ", raw).lower())) - _TITLE_STOPWORDS


# Four distinct words, not three. The observed false positive cleared the 0.35
# ratio on `artificial`, `intelligence` and `medical` — three words that are the
# subject of most papers in this field, so no stopword list can retire them
# without rejecting correct matches. Falling below the floor yields
# `unverifiable`, never `mismatch`: too few words to tell is not evidence of a
# different paper, and a `mismatch` would discard a possibly-correct download.
_TITLE_MIN_MATCHES = 4


# the three answers the check can give. "unverifiable" used to share `None`
# with "verified", so a scanned PDF someone supplied by hand was reported as
# `matched ...` — a successful identity check that never happened.
TITLE_VERIFIED = "verified"
TITLE_UNVERIFIABLE = "unverifiable"
TITLE_MISMATCH = "mismatch"


def _title_check_text(raw: str, page_text: str) -> tuple[str, str]:
    """Does the retrieved first page look like the cited reference?

    Returns (state, detail). Unverifiable is not the same as wrong and neither
    is the same as right — collapsing the first two into the third is how a
    scanned wrong paper passes as the source.
    """
    page = re.sub(r"\s+", " ", page_text).lower()
    if not page.strip():
        return TITLE_UNVERIFIABLE, "no readable text on its first page (scanned or image-only)"
    tokens = _title_tokens(raw)
    if not tokens:
        return TITLE_UNVERIFIABLE, "the reference string has no distinctive words to match on"
    found = sum(1 for t in tokens if t in page)
    if found / len(tokens) >= 0.35:
        if found < _TITLE_MIN_MATCHES:
            return (
                TITLE_UNVERIFIABLE,
                f"only {found} of the reference's {len(tokens)} distinctive words appear on its "
                "first page — too few to tell this paper from another on the same subject",
            )
        return TITLE_VERIFIED, f"{found}/{len(tokens)} reference tokens on its first page"
    return (
        TITLE_MISMATCH,
        f"title check failed: {found}/{len(tokens)} reference tokens on the retrieved first page",
    )


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


def _title_check(entry: RefEntry, pdf_path: Path) -> tuple[str, str]:
    return _title_check_text(entry.raw, _first_page_text(pdf_path))


def _accept(
    entry: RefEntry, client: httpx.Client, url: str, dest: Path, resolver: str, why: str
) -> bool:
    """Download one candidate copy and title-sanity-check it. On mismatch the
    bytes are discarded, the reason is recorded, and the chain continues."""
    if not _download_pdf(client, url, dest):
        return False
    state, detail = _title_check(entry, dest)
    entry.title_check = state
    if state == TITLE_MISMATCH:
        dest.unlink(missing_ok=True)
        entry.status = "mismatch"
        entry.reason = (
            f"retrieved PDF looks like a different paper ({detail}) — wrong or "
            "mistyped DOI in the reference? Drop the correct copy into sources/"
        )
        return False
    entry.status, entry.resolver, entry.pdf_path = "retrieved", resolver, str(dest)
    # Carry the check's own evidence. The mismatch branch above already states
    # its detail; the accepting branch discarded it, so `title_check: verified`
    # and `title_check: unverifiable` reached the manifest as bare assurances
    # with nothing behind them — and those two mean very different things.
    entry.reason = f"{why} · title check: {detail}" if detail else why
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


# The structural marks of a journal article besides a DOI: an identifier, a
# volume, a page range, a `volume:page` pair. Their ABSENCE is what the webpage
# gate keys on, so this set is deliberately small — every pattern added here
# sends one more reference into a title search.
_ARTICLE_SIGNAL_RE = re.compile(
    r"\bdois?\b"
    r"|\bpm(?:id|cid)\b"
    r"|\barxiv\b|\bbiorxiv\b|\bmedrxiv\b|\bssrn\b|\bisbn\b"
    r"|\bvol(?:ume)?\b\.?\s*\d"  # vol. 12 / volume 12
    r"|\bpp?\b\.\s*\d"  # p. 225 / pp. 225-232
    r"|\b\d+\s*\(\s*\d+\s*\)\s*[:,]?\s*\d"  # 89(1061):225
    r"|\b\d+\s*:\s*e?\d"  # 11:2624 / 5:e230024
    # a page range: 1068-1083. Two four-digit years either side of the dash are
    # a date span in a headline ("digital health 2020-2025"), not pages, and a
    # journal citation that really does span 1981-1990 carries its volume with
    # it — `388:1981` matches the pattern above.
    r"|(?<!\d)(?!(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}(?!\d))\d{1,4}\s*[-–—]\s*\d{1,4}(?!\d)"
    r"|\bin press\b|\bepub\b|\bforthcoming\b",
    re.I,
)


# A DOI naming a PART of a work: Crossref mints these for tables, figures and
# supplements, and a title search will happily return one. `/table-1` came back
# for the caption "Table 1. Dataset characteristics" and was reported as a
# paywalled cited work.
_COMPONENT_DOI_RE = re.compile(
    r"/(?:table|figure|fig|scheme|supp(?:l|lement(?:al|ary)?)?)[-_.]?\d+/?$"
    r"|\.s\d{3,}$",
    re.I,
)


def _component_doi_reason(doi: str | None) -> str:
    return (
        f"the only DOI available ({doi}) names a table, figure or supplement, not a "
        "paper — a part of a work is never the work a reference cites. Recorded as "
        "no DOI rather than resolved, because fetching it would judge claims against "
        "someone else's table"
    )


def _is_component_doi(doi: str | None) -> bool:
    """Does this DOI name a table, figure or supplement rather than a work?

    A part of a paper is never the thing a reference cites, so accepting one is
    always wrong — whether it arrived from a Crossref title search or was
    printed in the reference itself. Anchored at the end of the DOI so an
    ordinary suffix that merely contains the word (`.../figures-in-radiology`)
    is untouched.
    """
    return bool(doi and _COMPONENT_DOI_RE.search(doi))


def _is_webpage_reference(raw: str) -> bool:
    """Is this reference a web page rather than an article?

    A URL plus none of the structural marks of an article. Both halves matter:
    publishers' own reference styles print a link beside the volume and page
    range, and those references resolve well — while a reference with no URL at
    all is exactly what a bibliographic search is for.

    Two alternatives were weighed and rejected. Judging by how much of the
    string is URL measures nothing: a news page cited with a long headline and a
    short link scores low, a journal reference carrying a long publisher link
    scores high. A domain or TLD list is an arms race with every press office,
    newsroom and society website in existence.

    The error this accepts is the harmless one. A wrongly gated article ends at
    `no_doi` — a recorded gap; a wrongly searched web page ends with a real
    paper downloaded, title-checked against a news headline and judged for
    claims it never made.
    """
    if not _URL_RE.search(raw):
        return False  # no link: nothing here suggests a web page
    if DOI_RE.search(raw):
        return False  # a DOI anywhere counts, including inside the link itself
    # the rest of the marks are looked for with the URL removed, so that a path
    # segment or a query string cannot impersonate a volume or a page range
    return not _ARTICLE_SIGNAL_RE.search(_URL_RE.sub(" ", raw))


def resolve_entry(
    entry: RefEntry,
    dest_dir: Path,
    email: str,
    client: httpx.Client,
    provided_dir: Path | None = None,
) -> RefEntry:
    """Resolve one reference in place. Never raises — failures land in status/reason."""
    # before anything else: an ambiguous boundary makes `raw` two references
    # spliced together, so the slug, the title and any Crossref lookup derived
    # from it can all name the wrong paper. A recorded gap is the honest result.
    if entry.boundary_ambiguous:
        entry.status = "no_doi"
        return entry

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
        state, detail = _title_check(entry, provided)
        entry.title_check = state
        if state == TITLE_VERIFIED:
            note = f" — identity confirmed: {detail}"
        else:
            # "unverified" for both remaining states, because both mean the same
            # thing to a reader: nobody established that this file is the paper
            note = f" — identity unverified: {detail}"
        entry.reason = f"matched {provided.name} in your sources folder{others}{note}"
        return entry

    try:
        if arxiv := ARXIV_RE.search(entry.raw):
            url = f"https://arxiv.org/pdf/{arxiv.group(1)}"
            if _accept(entry, client, url, dest, "arxiv", "arXiv"):
                return entry

        # A bibliographic title search always returns *something*, and for a web
        # page that something is a confident wrong answer: `ACR launches first
        # medical practice artificial intelligence QA program` fetched an ACR
        # Open Rheumatology editorial (American College of Rheumatology, not
        # Radiology), which then passed the title check on the shared vocabulary.
        # A news page was never retrievable as a PDF anyway, so nothing is lost.
        if not entry.doi and _is_webpage_reference(entry.raw):
            entry.status = "no_doi"
            entry.reason = (
                "this reference is a web page, not an article — no DOI, and no volume, "
                "page range or identifier to look one up with. Not searched by title: "
                "Crossref would answer with the closest-looking journal article, and "
                "judging a claim against that is worse than recording the gap"
            )
            return entry

        # The parser is fallible, so this is the second line of defence. An
        # entry with no year, no DOI and no arXiv id is not a citable work, and
        # a bibliographic search always answers with *something*: three of one
        # paper's own table captions were searched by title and came back as
        # table-component DOIs belonging to unrelated papers, then published as
        # paywalled references.
        if not entry.doi and not looks_like_reference(entry.raw):
            entry.status = "no_doi"
            entry.reason = (
                "this entry carries no year, DOI or arXiv id, so nothing here reads as "
                "a cited work — it is more likely a caption or a heading the reference "
                "parser swept in. Not searched by title: Crossref would answer with the "
                "closest-looking record, and inventing a reference is worse than "
                "reporting one the parser got wrong"
            )
            return entry

        # a DOI printed in the reference can name a part of a paper too
        if _is_component_doi(entry.doi):
            entry.status, entry.reason = "no_doi", _component_doi_reason(entry.doi)
            entry.doi = None
            return entry

        if not entry.doi:
            try:
                found = _crossref_doi(client, entry.raw, email)
                if _is_component_doi(found):
                    # a title match is not a work match — a table's title is the
                    # table's, and this one belonged to a different paper
                    entry.status, entry.reason = "no_doi", _component_doi_reason(found)
                    return entry
                entry.doi = found
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
    with _client() as client:
        for entry in entries:
            resolve_entry(entry, dest_dir, email, client, provided_dir)
            if progress:
                progress(entry)
    return entries
