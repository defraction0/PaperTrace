"""Reference resolution with an honest manifest.

Chain: DOI-in-text → Crossref lookup → Unpaywall → Europe PMC → arXiv.
Downloads open-access copies only — a paywalled reference stays `paywalled`,
with the reason recorded. `not obtainable` is a result, not a failure: the
fact-check step reports those claims as unverifiable instead of guessing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
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
    # two entries sharing a slug share a download path — see _unique_slugs
    return _unique_slugs(entries)


def _parse_bulleted(text: str) -> list[RefEntry]:
    """Fallback for lists the converter flattened into bullets.

    **The printed numeral is the label when there is one.** Numbering the
    bullets `1..N` by document order was the single worst bug this module has
    had: a running header interrupting reference [14] at a page break made
    docling emit two bullets, every later label shifted by one, and 27 of 41
    references on a real paper were judged against the wrong papers. The numeral
    was sitting at the front of the text the whole time — `_strip_printed_numeral`
    captured it and threw it away one line before the label was invented.

    A bullet with no numeral, following one that has it, is the tail of an entry
    the converter split; it is joined back on rather than becoming a reference of
    its own. So a page-break split now heals, and a *merge* — two references in
    one bullet — leaves a gap in the labels, which `_covers` reports as
    unconfirmed instead of silently shifting everything after it.

    Sequential numbering survives only for lists that genuinely carry no
    numerals (Nature-family layouts, where the converter really did strip them):
    a guess, but the only reading available, and the reconciler marks it
    unverified.
    """
    items: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "• ", "* ")):
            items.append(s[2:].strip())
        elif items and s:
            items[-1] += " " + s  # wrapped continuation of the previous entry
    items = [re.sub(r"\s+", " ", raw).strip() for raw in items if raw.strip()]

    numerals = [_leading_numeral(raw) for raw in items]
    if _usable_printed_numerals(numerals):
        entries: list[RefEntry] = []
        for num, raw in zip(numerals, items, strict=True):
            if num is None:
                if entries:  # the tail of an entry split across a page break
                    entries[-1].raw = f"{entries[-1].raw} {raw}".strip()
                continue
            entries.append(_entry(str(num), _strip_printed_numeral(raw)))
        # rebuilding re-reads the DOI and year out of the joined text: a split
        # entry's identifiers often live in the half that was cut off
        return [_entry(e.num, e.raw) for e in entries]

    return [_entry(str(i), _strip_printed_numeral(raw)) for i, raw in enumerate(items, 1)]


def _leading_numeral(raw: str) -> int | None:
    m = _PRINTED_NUMERAL_RE.match(raw)
    return int(m.group(1)) if m else None


def _usable_printed_numerals(numerals: list[int | None]) -> bool:
    """Do these bullets carry a reference numbering, or just happen to start with digits?

    Required: a first entry labelled [1], a strictly ascending run, and most
    bullets carrying one. Gaps are allowed and are *informative* — a gap is a
    reference the parser could not isolate, and reporting the gap is the honest
    result where renumbering around it is the silent one.
    """
    seen = [n for n in numerals if n is not None]
    if len(seen) < 2 or seen[0] != 1:
        return False
    if any(b <= a for a, b in zip(seen, seen[1:], strict=False)):
        return False
    return len(seen) * 2 >= len(numerals)


# `1 . Rivara FP` and `1. Rivara FP` — the list numeral the converter turned
# into a bullet without removing. Anchored and bounded: a reference genuinely
# starting with a number ("2019 WHO classification of tumours") keeps it,
# because the separator is required.
_PRINTED_NUMERAL_RE = re.compile(r"^\(?(\d{1,3})\)?\s*[.):\]]\s+")


def _strip_printed_numeral(raw: str) -> str:
    """Drop a leading list numeral the converter left in the reference text.

    Not cosmetic. `_slug` reads the first token carrying letters, so a numeral
    in front is harmless there now — but the numeral also reaches `_title_check`
    and the Crossref bibliographic search as part of the reference string, and
    it is not part of the reference.
    """
    return _PRINTED_NUMERAL_RE.sub("", raw, count=1).strip() or raw


# ---------------------------------------------------------------------------
# the publisher's own reference list
#
# `parse_references` was the only stage in the pipeline with no way to say it
# had failed. It always returned a confident list, and nothing compared that
# list to anything. One live audit misnumbered 27 of 41 references and the
# report said so nowhere — the label is the join key, so claims citing [15] and
# up were judged against the wrong papers.
#
# Crossref carries the list the publisher deposited. It is a second, independent
# reading — NOT an oracle. A deposit can be partial (one publisher returned 2
# references for a paper with about 40), and a partial deposit is more dangerous
# than a bad parse because it looks authoritative. So it is a candidate, and the
# manuscript's own `[N]` markers arbitrate between the candidates.
# ---------------------------------------------------------------------------

CROSSREF_NO_DOI = (
    "no DOI for the manuscript itself, so the publisher's deposited reference list "
    "could not be looked up — usual for a paper under review, which is this tool's "
    "main case. Pass --doi if the paper does have one"
)
CROSSREF_NO_DEPOSIT = (
    "the publisher deposited no reference list for this DOI, so there was nothing to "
    "check the parsed list against. This is a property of the publisher, not of the paper"
)
CROSSREF_UNREACHABLE = (
    "Crossref could not be reached, so the publisher's deposited reference list was "
    "never seen — this run had one reading of the list where it normally has two"
)


@dataclass(frozen=True)
class CrossrefDeposit:
    """What the publisher deposited, and how much of it this tool could read."""

    entries: list[RefEntry] = field(default_factory=list)
    deposited: int = 0  # references in the record, before this tool read them
    publisher: str = ""
    title: str = ""  # the record's own title — is this DOI even this paper?
    absent: str = ""  # one of the three CROSSREF_* notes when there is no list

    @property
    def unrenderable(self) -> int:
        """Deposited references this tool could not turn into an entry.

        Named for whose limitation it is. An earlier version compared
        `len(entries)` against the record's `references-count` and called the
        shortfall a *partial deposit* — but that field counts the references
        **deposited**, so it always equals the array length, and the only way
        the comparison could fire was this tool dropping entries it failed to
        render. Wiley deposits references as a bare DOI and nothing else; 49 of
        its 52 were discarded and the report blamed Wiley for depositing 3.
        A shortfall here is the tool's, and says so.
        """
        return max(0, self.deposited - len(self.entries))


def _surname(author: str | None) -> str:
    """Crossref's `author` is usually `Initials Surname` — keep the surname first.

    Springer deposits `"author": "C Huang"`, so the first letter-bearing token
    was the initial and the entry slugged `c-2020`. The slug is the download
    filename, the report's source id, and the `--provided` match key documented
    as `<firstauthor>-<year>.pdf`, so an initial there quietly stops user files
    matching. Elsevier's bare `"Foy"` and any multi-word surname are untouched:
    only a short all-caps leading token is dropped.
    """
    author = (author or "").strip()
    head, _, rest = author.partition(" ")
    if rest and head.isupper() and len(head) <= 3:
        return rest.strip()
    return author


def _doi_slug(doi: str) -> str:
    """A readable id for a reference known only by its DOI.

    `10.1056/NEJMoa1911793` → `nejmoa1911793`. The suffix is the publisher's own
    article id, so it is both stable and recognisable — and it is honest about
    what is known, which is the DOI and not an author.
    """
    tail = doi.rsplit("/", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]+", "-", tail).strip("-")[:32] or "doi"


def _reference_raw(ref: dict) -> str:
    """One deposited reference as a printed reference string.

    `raw` is what `_slug`, `_title_check` and the Crossref title search all
    consume, so a structured deposit has to be assembled back into the shape
    those readers expect rather than left as a dict.

    A DOI-only deposit falls back to the DOI itself, so the reference survives.
    Dropping those was a silent data loss dressed up as a publisher's fault, and
    they are the *best* references in a deposit, not the worst: the DOI is
    already resolved, so retrieval skips the bibliographic title search that has
    been this module's richest source of wrong-paper bugs. What is lost is the
    title check, which then reports `unverifiable` — a disclosed gap, not a
    guess.
    """
    if unstructured := (ref.get("unstructured") or "").strip():
        return re.sub(r"\s+", " ", unstructured)
    parts = [
        _surname(ref.get("author")),
        ref.get("article-title") or ref.get("volume-title") or ref.get("series-title"),
        ref.get("journal-title"),
        ref.get("volume"),
        ref.get("first-page"),
        ref.get("year"),
    ]
    assembled = re.sub(r"\s+", " ", " ".join(str(p) for p in parts if p)).strip()
    return assembled or (ref.get("DOI") or "").strip()


def crossref_deposit(client: httpx.Client, doi: str | None, email: str) -> CrossrefDeposit:
    """The publisher's deposited reference list for `doi`, numbered by array order.

    **Array order is the only portable numbering signal.** The `key` field looks
    like it carries the number and does not: `_b0005`/`_b0010` and `_bib1` turn up
    on two Elsevier papers — and both schemes inside a single deposit — beside
    `3400_CR1` (Springer), `bibr1-…` (SAGE) and `R10-45-20210317` (Ovid). Parsing
    a number out of any of those renumbers every reference of every publisher
    that spells it differently.

    Never raises. Three different absences are recorded as three different
    notes, because "you have no DOI", "your publisher deposits nothing" and
    "Crossref is down" ask the reader for three different things.
    """
    if not doi:
        return CrossrefDeposit(absent=CROSSREF_NO_DOI)
    try:
        r = client.get(
            f"https://api.crossref.org/works/{doi}",
            headers=_contact(email),
        )
        if r.status_code != 200:
            return CrossrefDeposit(absent=CROSSREF_NO_DEPOSIT if r.status_code == 404
                                   else CROSSREF_UNREACHABLE)
        message = r.json().get("message")
    except (httpx.HTTPError, ValueError):
        # ValueError covers a 200 that is not JSON — a captive portal or an
        # error page, which is Crossref not answering, not Crossref answering no
        return CrossrefDeposit(absent=CROSSREF_UNREACHABLE)
    # a 200 whose body is `null`, a list, or anything but the documented object
    # is also Crossref not answering — and `.get` on it is an AttributeError
    # that would take down a run this function promises never to break
    if not isinstance(message, dict):
        return CrossrefDeposit(absent=CROSSREF_UNREACHABLE)

    refs = message.get("reference")
    refs = refs if isinstance(refs, list) else []
    if not refs:
        return CrossrefDeposit(absent=CROSSREF_NO_DEPOSIT,
                               publisher=message.get("publisher", ""))

    entries: list[RefEntry] = []
    for i, ref in enumerate(refs, 1):
        if not isinstance(ref, dict):
            continue
        raw = _reference_raw(ref)
        if not raw:
            continue
        e = _entry(str(i), raw)
        # `_entry` scrapes a DOI out of the text; the deposit states one, and a
        # stated DOI is better evidence than a scraped one
        if doi_field := (ref.get("DOI") or "").strip():
            e.doi = doi_field
            # a reference known only by its DOI has no author to be named
            # after, so name it after what IS known
            if raw == doi_field:
                e.slug = _doi_slug(doi_field)
        # a publisher can deposit a reference to its own table, and a part of a
        # work is never the work a reference cites — wherever the DOI came from
        if _is_component_doi(e.doi):
            e.doi = None
        entries.append(e)

    titles = message.get("title") or []
    return CrossrefDeposit(
        entries=_unique_slugs(entries),
        deposited=len(refs),
        publisher=message.get("publisher", ""),
        title=titles[0] if isinstance(titles, list) and titles else "",
    )


def deposit_is_this_paper(manuscript_title: str, record_title: str) -> bool | None:
    """Is the Crossref record behind the DOI the paper being audited?

    True, False, or **None for "cannot tell"** — the same tri-state the source
    title check uses, for the same reason: an unknown is not a match and it is
    not a mismatch either, and collapsing it would either discard good deposits
    or wave wrong ones through.

    This is the gate every other retrieval route in this module already has, on
    the one route that can replace the *entire* reference list. The DOI is
    scraped off page 1 or typed by hand; a data-availability DOI, an erratum or
    a preprint version can easily carry the same number of references as the
    paper, so the count test would pass and the report would print "numbering
    confirmed" over another paper's bibliography.
    """
    a, b = _title_tokens(manuscript_title), _title_tokens(record_title)
    # Too few distinctive words on either side to tell — the same floor, and the
    # same constant, that stops `_title_check` calling a thin comparison a
    # mismatch. `paper_title` is a heuristic over the first blocks of a page, so
    # a journal banner or an author line lands here regularly, and a confident
    # `False` on two comparable words would discard a good deposit over the
    # layout of a first page.
    if min(len(a), len(b)) < _TITLE_MIN_MATCHES:
        return None
    return len(a & b) / min(len(a), len(b)) >= 0.5


def crossref_reference_list(
    client: httpx.Client, doi: str | None, email: str
) -> list[RefEntry] | None:
    """The deposited list, or None when there is none.

    None rather than `[]`: an empty list reads as "this paper cites nothing",
    and the reconciler has to be able to tell that apart from "nobody deposited
    anything to read".
    """
    deposit = crossref_deposit(client, doi, email)
    return deposit.entries or None


# ---------------------------------------------------------------------------
# reconciliation — the body's labels arbitrate between two candidate readings
# ---------------------------------------------------------------------------


@dataclass
class Reconciliation:
    """Which reading of the reference list was used, and whether it was checked."""

    source: str = "parsed"  # crossref | parsed
    verified: bool = False
    # the chosen reading matched the body's labels, but the OTHER reading
    # disagreed. `_covers` is a test of extent, not of content, so a second
    # independent reading calling the list wrong is worth the reader's eye even
    # when the count checks out — burying it in a field no template renders was
    # how a compensating parse error could pass unmentioned.
    contested: bool = False
    note: str = ""
    unverified_from: int | None = None  # first label whose numbering is in doubt
    body_labels: int = 0
    crossref_count: int | None = None
    parsed_count: int = 0


def _covers(body: set[str], entries: list[RefEntry]) -> bool:
    """Does this candidate account for exactly the references the body cites?

    Two conditions. Every cited label must exist in the list, and the list must
    be exactly as long as the highest label cited — which under citation-order
    numbering is not a heuristic: in a numeric-citation journal reference N *is*
    the Nth first-cited work, so the body's labels run 1..N by construction.

    **What this cannot see.** Both conditions are about extent, not content. A
    parse that merges one pair of references and splits another keeps the count
    and passes here, with every label between the two errors pointing one paper
    off. The subset test only bites since `_parse_bulleted` began reading the
    *printed* numerals, which can leave gaps; while numbering was positional,
    `nums` was always `{1..len(entries)}` and the subset test was implied by the
    count. So this is a strong test of "is the list the right length" and a weak
    one of "is entry N the right paper" — which is why a matching count is not
    the end of it, and why a second reading that disagrees is still reported.

    The cost is a paper whose reference list holds a work the body never cites:
    it earns a warning it did not deserve. That is the right way round — the
    alternative silently accepts the numbering that judged 27 references against
    the wrong papers.
    """
    if not body or not entries:
        return False
    nums = {e.num for e in entries}
    return body <= nums and len(entries) == max(int(x) for x in body)


def _same_work(x: RefEntry, y: RefEntry) -> bool:
    """Do these two readings name the same paper?

    A DOI settles it when both carry one. Otherwise it is a token overlap, and
    it has to be, because the two readings describe a paper in different
    dialects: Elsevier prints `F.P. Rivara, D.C. Grossman, …` while the deposit
    carries `author: "Rivara"`. Comparing slugs made those two `fp-2019` and
    `rivara-2019` — a reported divergence at entry [1] for a whole class of
    journals, which tainted every claim in the report and made the corroboration
    path unreachable.
    """
    if x.doi and y.doi:
        return x.doi.lower() == y.doi.lower()
    if x.year and y.year and x.year != y.year:
        return False
    tx, ty = _title_tokens(x.raw), _title_tokens(y.raw)
    if not tx or not ty:
        return True  # nothing to compare is not evidence of disagreement
    return len(tx & ty) / min(len(tx), len(ty)) >= 0.34


def _first_divergence(a: list[RefEntry], b: list[RefEntry]) -> int | None:
    """The first 1-based position where two readings stop describing one paper.

    None when they agree the whole way down the shorter list — *not* the index
    past the end. Returning `min(len)+1` produced "entries from [42] onward are
    affected" on a 41-entry list, a warning naming an entry that does not exist
    while `label_is_doubtful` quietly returned False for every real label. The
    banner and the per-claim layer then said opposite things.

    Compared with `_same_work`, not by string or slug: the two legs are a PDF
    parse and a publisher deposit, so they never agree character-for-character
    even when they name the same paper.
    """
    # strict=False on purpose: the two readings having different lengths is the
    # normal case here, and it is the caller's finding, not an error to raise
    for i, (x, y) in enumerate(zip(a, b, strict=False), 1):
        if not _same_work(x, y):
            return i
    if len(a) != len(b):
        # they agree as far as the shorter one goes, and then one simply stops:
        # the first entry the two readings disagree about existing is in doubt
        return min(len(a), len(b)) + 1
    return None


def reconcile(
    body_labels: set[str],
    crossref: list[RefEntry] | None,
    parsed: list[RefEntry],
    crossref_absent: str = "",
) -> tuple[list[RefEntry], Reconciliation]:
    """Choose between two readings of the reference list, and say how sure it is.

    The manuscript's own `[N]` markers are the arbiter — free, needing no DOI and
    no network, and the only one of the three that is definitionally right about
    what the paper cites. Crossref and the PDF parse are candidates measured
    against it.

    On unresolvable disagreement the audit continues: the numbering is marked
    unverified, the report discloses it, and every verdict resting on a doubtful
    label carries the caveat. Refusing to run would throw away a useful audit
    over a numbering the reader can check by hand.
    """
    rec = Reconciliation(
        body_labels=len(body_labels),
        crossref_count=len(crossref) if crossref is not None else None,
        parsed_count=len(parsed),
    )
    cr_ok = _covers(body_labels, crossref or [])
    parse_ok = _covers(body_labels, parsed)
    cited = max((int(x) for x in body_labels), default=0)

    if cr_ok:
        # both matching is not a tie to break: prefer the deposit, whose DOIs
        # are already resolved, which skips the title search that has been this
        # module's richest source of wrong-paper bugs
        rec.source, rec.verified = "crossref", True
        rec.contested = not parse_ok and bool(parsed)
        rec.note = (
            f"the publisher's deposited list has {len(crossref)} references and the "
            f"manuscript cites [1]-[{cited}] — they agree"
            + (f"; the parsed list has {len(parsed)}, which does not, so it was not used"
               if rec.contested else "")
        )
        return list(crossref), rec

    if parse_ok:
        rec.source, rec.verified = "parsed", True
        rec.contested = crossref is not None
        rec.note = (
            f"the parsed list has {len(parsed)} references and the manuscript cites "
            f"[1]-[{cited}] — they agree"
        )
        if crossref is not None:
            rec.note += (
                f"; the publisher deposited {len(crossref)}, which does not. The count "
                "checks out, but a second independent reading calls this list wrong — "
                "and a count cannot tell a right list from one that merged two "
                "references and split another"
            )
        elif crossref_absent:
            rec.note += f"; {crossref_absent}"
        return list(parsed), rec

    # Nothing matched. Use the parse — it is at least a reading of the paper in
    # hand, where a deposit describes the published version, which a manuscript
    # under review is not.
    chosen = parsed or list(crossref or [])
    rec.source = "parsed" if parsed else "crossref"
    rec.verified = False
    # Two independent readings agreeing about entry N is evidence about entry N
    # even with no arbiter to confirm either — a PDF parse and a publisher
    # deposit have no common failure mode. Only where they diverge is the
    # numbering actually in doubt. With one reading there is no such evidence,
    # and claiming a divergence point would present unchecked entries as checked.
    rec.unverified_from = (
        _first_divergence(crossref, parsed) if crossref is not None and parsed else 1
    )
    if not body_labels:
        # A third fact, not a failure of either candidate: the arbiter does not
        # exist. Superscript-numeric styles are the common case and the numbering
        # is still the join key there — the markers are simply invisible once the
        # PDF is flattened to text, so half a spread of journals lands here. The
        # coverage audit is blind to exactly the same papers.
        both = crossref is not None and parsed
        if both and rec.unverified_from is None:
            corroborated = (
                " The parsed list and the publisher's deposit agree throughout, which "
                "is evidence for this numbering but not confirmation of it — they can "
                "still be wrong together."
            )
        elif both and rec.unverified_from > 1:
            corroborated = (
                f" The parsed list and the publisher's deposit agree as far as entry "
                f"[{rec.unverified_from - 1}], which is evidence about those entries "
                "but not confirmation."
            )
        else:
            corroborated = ""
        rec.note = (
            "no bracketed numeric citation markers were found in the body, so there is "
            "nothing to check the reference numbering against — only styles like [12], "
            "[7,8] and [9-11] can be read. The numbering below is unconfirmed."
            + corroborated
        )
        return chosen, rec

    detail = f"the manuscript cites [1]-[{cited}], the parsed list has {len(parsed)} references"
    if crossref is not None:
        detail += f" and the publisher deposited {len(crossref)}"
    elif crossref_absent:
        detail += f", and {crossref_absent}"
    scope = (
        f"Entries from [{rec.unverified_from}] on may name a different paper than the "
        "label they carry, and verdicts on claims citing them are marked accordingly"
        if rec.unverified_from
        else "The two readings agree with each other entry for entry, so both are "
             "wrong in the same way or the body's labels were read incompletely"
    )
    rec.note = (
        f"{detail} — that does not add up, so the numbering could not be confirmed. "
        + scope
    )
    return chosen, rec


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
    """A short id for a reference: first author's surname plus year.

    Takes the first token that actually contains letters, rather than the first
    token. A leading numeral the converter failed to strip left nothing after
    the non-letter filter, so the entry fell back to the literal `ref` — and
    since the slug is also the download's filename, every such reference
    resolved to the same path.
    """
    for token in re.split(r"[,\s]+", raw.strip()):
        if cleaned := re.sub(r"[^A-Za-z\-]", "", token).lower().strip("-"):
            return f"{cleaned}-{year}" if year else cleaned
    return f"ref-{year}" if year else "ref"


def _unique_slugs(entries: list[RefEntry]) -> list[RefEntry]:
    """Guarantee no two entries share a slug, in place.

    `resolve_all` writes each download to `<slug>.pdf`, so two entries sharing a
    slug share a *file*: the second download overwrites the first, and every
    claim citing the first is then judged against the second's paper. A live run
    put 11 retrieved sources at one path this way.

    A genuine collision is possible without any parser bug — the same first
    author and year cited twice — so uniqueness is enforced here rather than
    assumed to fall out of a better slug. The first entry keeps the natural
    slug, so a `--provided` file named `<author>-<year>.pdf` still matches.
    """
    seen: set[str] = set()
    for e in entries:
        base = e.slug or "ref"
        slug = base
        if slug in seen:
            # the reference number is the one thing guaranteed distinct, and it
            # keeps the name legible in a report where the slug is shown
            slug = f"{base}-r{e.num}"
            while slug in seen:
                slug += "x"
        e.slug = slug
        seen.add(slug)
    return entries


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
