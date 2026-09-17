"""Data model for a PaperTrace run.

Plain dataclasses with dict round-tripping — the JSON files they produce
(`source_map.json`, `refs_manifest.json`, `results.json`) are the contract
between the pipeline steps and between the agent and the deterministic tools.
JSON Schemas for them live in `schemas/`.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# ---------------------------------------------------------------------------
# source map (ingest output)
# ---------------------------------------------------------------------------


BLOCK_TYPES = ("sectionheader", "text", "table", "picture", "list")


# Where the bibliography begins — ONE rule, because two readers need it and
# each having its own is a defect this codebase already shipped. `refs` parses
# the reference list and `coverage_audit` must stop counting citations at the
# same block; when they disagreed, every `[N]` in the reference list was counted
# as a body citation and the audit reported invented gaps.
#
# A `sectionheader` may merely START with the word (docling labels it properly).
# A body-typed block must be the word and nothing else: flat ingest guesses
# headings from font size and gets it wrong, but "References were checked by
# hand" must not be allowed to swallow the rest of the paper.
_REFS_HEADING_PREFIX = re.compile(r"^\s*#*\s*(references|bibliography|literature)\b", re.I)
_REFS_HEADING_EXACT = re.compile(
    r"^\s*#*\s*(?:\d+\.?\s*)?(references|bibliography|literature)\s*:?\s*$", re.I
)


def is_references_heading(block_type: str, text: str) -> bool:
    """True when this block is the heading that opens the reference list."""
    text = (text or "").strip()
    if block_type == "sectionheader":
        return bool(_REFS_HEADING_PREFIX.match(text))
    return bool(_REFS_HEADING_EXACT.match(text))


# What a PDF declares as its title but is not one. Three bounded rules, each
# from an observed shape, not a list that grows with every journal:
# a banner or placeholder (too few words), a producer's filename, and a
# producer's prefix. `Microsoft Word - Manuscript revised final clean.docx` is
# the shape that matters — a Word-produced manuscript is this tool's main case,
# and four confident words describing no paper would let the identity check
# report a mismatch and discard a good deposit.
_TITLE_FILE_SUFFIX = re.compile(r"\.(docx?|tex|dvi|indd|pdf|rtf|odt|pages)$", re.I)
_TITLE_PRODUCER = re.compile(r"^\s*microsoft\s+(word|powerpoint)\s*-", re.I)


def _declared_title_is_usable(title: str) -> bool:
    title = (title or "").strip()
    return (
        len(title.split()) >= 3
        and not _TITLE_FILE_SUFFIX.search(title)
        and not _TITLE_PRODUCER.match(title)
    )


_TITLE_STOPWORDS = frozenset(
    {"commun", "nature", "science", "journal", "lancet", "article",
     "elsevier", "springer", "wiley", "volume", "press", "https"}
)

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)


# What NFKD cannot decompose, because these are distinct letters rather than a
# base plus a combining mark. Without them `Weiß` folds to `wei` and `Bjørnsson`
# to `bjrnsson` — a surname that changed, not one that normalised.
_TRANSLITERATE = str.maketrans(
    {
        "ß": "ss",
        "ø": "o",
        "æ": "ae",
        "œ": "oe",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "ł": "l",
        "ı": "i",
        "ħ": "h",
        "ŧ": "t",
    }
)


def _fold(text: str) -> str:
    """Lowercase, transliterated, diacritics decomposed away — `İnce` → `ince`.

    Lives here because five call sites across `models` and `refs` need it and
    neither module may import the other — the same reason the shared title rules
    below it live here. `_slug` deletes non-ASCII instead (`[^A-Za-z\\-]`), which
    is why `İnce O` slugs `nce-2023` and `Müller` slugs `mller`. Folding is what
    a name comparison needs.

    **Both sides of any comparison must be folded.** `_title_tokens` folds, so a
    haystack that is merely lowercased matches none of the folded tokens —
    `kustner` is not in `küstner`, which deleted correct downloads and blamed
    the manuscript for a mistyped DOI.
    """
    # lowercase BEFORE translate: `_TRANSLITERATE` is keyed on lowercase letters
    # only, so reordering these two silently stops `Ø`, `Æ` and `Ł` folding
    lowered = (text or "").lower().translate(_TRANSLITERATE)
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


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
    # folded, not merely lowercased: `[a-z]{5,}` over raw text drops `Späth`
    # entirely and truncates `Cristóbal` to `crist`
    return set(re.findall(r"[a-z]{5,}", _fold(_URL_RE.sub(" ", raw)))) - _TITLE_STOPWORDS


# Four distinct words, not three. The observed false positive cleared the 0.35
# ratio on `artificial`, `intelligence` and `medical` — three words that are the
# subject of most papers in this field, so no stopword list can retire them
# without rejecting correct matches. Falling below the floor yields
# `unverifiable`, never `mismatch`: too few words to tell is not evidence of a
# different paper, and a `mismatch` would discard a possibly-correct download.
_TITLE_MIN_MATCHES = 4


def titles_match(a: str, b: str) -> bool | None:
    """Do these two title strings name the same work? True, False, or None.

    **None means "cannot tell"**, and it is a third answer rather than a
    collapsed False for a measured reason: `paper_title` is a heuristic over the
    first blocks of a page, and on a seven-paper spread the block it offers was
    an article-type banner four times — `CLINICAL GUIDELINE`, `RESEARCH
    ARTICLE`. Two comparable words are not evidence of a different paper, and a
    confident False there discards a good deposit or a correct Europe PMC record.

    Lives here because three readers need it and none may import another:
    `refs` asks whether a Crossref deposit belongs to this paper, `scout` asks
    the same of a Europe PMC record, and `refs._title_check_text` asks it of a
    downloaded first page. The rule was `refs`-private until the second reader
    appeared; a copy in `scout` is the defect this module's other shared rules
    exist to prevent.
    """
    ta, tb = _title_tokens(a), _title_tokens(b)
    if min(len(ta), len(tb)) < _TITLE_MIN_MATCHES:
        return None
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.5


def paper_title(smap) -> str:
    """Best-effort title of the paper a source map describes.

    What the PDF declares about itself first, then the layout: the first
    substantial section header, else the first substantial text block. Third
    rule to live here for the reason the two above it do — `scout` needs it to
    identify the paper in Europe PMC and `refs` needs it to check that the
    Crossref record behind a DOI is this paper, and neither module may import
    the other.

    The declaration comes first because the layout is measurably worse at this:
    on a seven-paper spread the first heading was the article-type banner every
    time it was wrong, and a banner identifies nothing. It is not trusted
    blindly either — an author's PDF declares its Word filename — so a
    declaration that is not title-shaped is passed over for the layout.

    Still best-effort, and treated as such by both callers: this is why the
    identity check it feeds has an "unverifiable" answer and never reads a thin
    title as a mismatch.
    """
    if _declared_title_is_usable(getattr(smap, "declared_title", "")):
        return " ".join(smap.declared_title.split())[:220]
    for b in smap.blocks:
        if b.type == "sectionheader" and len(b.text.strip()) >= 15:
            return " ".join(b.text.split())[:220]
    for b in smap.blocks:
        if b.type == "text" and len(b.text.strip()) >= 25:
            return " ".join(b.text.split())[:220]
    return ""


# What marks a line as a bibliographic reference rather than back matter.
# Deliberately three cheap structural marks and nothing else — the question is
# only "is this a citable work at all", not "is this a good reference".
_REF_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_REF_DOI = re.compile(r"10\.\d{4,9}/\S", re.I)
_REF_ARXIV = re.compile(r"arxiv[:\s]*\d{4}\.\d{4,5}", re.I)
# An author list, in the two styles that actually turn up: `M.A. Slabaugh` and
# `Slabaugh MA`. Two names, not one — a single match is easy to hit by accident.
_REF_AUTHORS = re.compile(
    r"\b[A-Z]\.(?:\s*[A-Z]\.)*\s*[A-Z][a-z]+"   # M.A. Slabaugh
    r"|\b[A-Z][a-z]+\s+[A-Z]{1,3}\b"              # Slabaugh MA
)


def looks_like_reference(text: str) -> bool:
    """Could this line be a cited work? A year, a DOI or an arXiv id.

    Lives here rather than in `refs.py` or `ingest/` for the same reason
    `is_references_heading` does: two readers need the rule, they cannot import
    each other, and each keeping its own copy is a defect this codebase has
    already shipped once.

    The bar is deliberately low. This is not a quality test on a reference — it
    is the difference between a cited work and the paper's own back matter.
    `Table 1. Dataset characteristics` carries none of the three, and three of
    those became references 44-46 of a 43-reference paper, were title-searched
    against Crossref, and came back as table-component DOIs belonging to other
    papers.

    Being wrong in the permissive direction is the cheap error: a stray line
    that sneaks through is one bad entry in a manifest. Being wrong in the
    strict direction drops a real reference from the audit entirely, and that
    failure is silent.
    """
    text = text or ""
    if _REF_YEAR.search(text) or _REF_DOI.search(text) or _REF_ARXIV.search(text):
        return True
    # An author list, for the references that arrive truncated. Two real
    # references in one audit reached the resolver as authors plus half a title
    # and nothing else — no journal, no year — and Crossref found both correct
    # DOIs from exactly that. A year-only test threw them away.
    return len(_REF_AUTHORS.findall(text)) >= 2


# How a citation marker is written. The THIRD rule to live here for the reason
# `is_references_heading` and `looks_like_reference` do: two modules need it and
# neither may import the other. `check.py` reads these to audit coverage and
# `refs.py` reads them to learn which references the manuscript actually cites —
# and those two readings are only worth comparing if they are the same rule.
_LABEL_GROUP = re.compile(r"\[(\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*)\]")


def _expand_label_group(group: str) -> set[str]:
    labels: set[str] = set()
    for part in re.split(r"\s*,\s*", group):
        m = re.match(r"^(\d{1,3})\s*[–—-]\s*(\d{1,3})$", part.strip())
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= hi and hi - lo <= 50:
                labels.update(str(n) for n in range(lo, hi + 1))
        elif part.strip().isdigit():
            labels.add(part.strip())
    return labels


def citation_labels(text: str) -> set[str]:
    """Every bracketed numeric citation label in this text: `[3]`, `[7,8]`, `[11-13]`.

    Bracketed numeric styles only — author-year and bare superscripts are not
    read, here or anywhere else in the tool, and an empty set from a paper that
    plainly cites things means the style was not recognised rather than that
    nothing was cited. `check.citation_labels_in_text` wraps this to exclude the
    reference list; callers that want the whole document use this directly.
    """
    labels: set[str] = set()
    for m in _LABEL_GROUP.finditer(text or ""):
        labels.update(_expand_label_group(m.group(1)))
    return labels


@dataclass(frozen=True)
class Region:
    """One rectangle of a block, and the slice of its text that rectangle holds.

    A paragraph that continues into the next column or onto the next page
    occupies several rectangles, and docling states all of them — `page_no`,
    `bbox` and `charspan` per provenance entry. Ingest used to keep `prov[0]`
    and drop the rest, so a block's bbox bounded only its opening: measured on
    one 14-page paper, 14,367 characters sat outside the rectangle their block
    claimed, and five blocks were on two pages at once.

    `char_start`/`char_end` are what put these in reading order. y-coordinates
    cannot: two rectangles can share a page, and the continuation is usually
    HIGHER up it than the opening, because it is the top of the next column.
    """

    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 (top-left origin)
    char_start: int  # offset into Block.text, inclusive
    char_end: int  # offset into Block.text, exclusive


@dataclass
class Block:
    """One layout block of a source document, with page-level provenance.

    `table` blocks carry their content as GitHub-flavoured markdown in `text`;
    `picture` blocks carry "[FIGURE: <caption>]"; both keep their page bbox so
    the region can be cropped and shown.
    """

    id: str  # "block_0001"
    type: str  # one of BLOCK_TYPES
    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 (PDF points, top-left origin)
    heading_path: list[str]
    text: str
    # every rectangle this block's text occupies, in reading order. `page` and
    # `bbox` above are the FIRST of these and keep their old meaning, so every
    # consumer that reads them is unaffected. EMPTY means "not recorded" — a map
    # written before regions existed — and never "this block has no
    # continuation": a reader that finds it empty falls back to the single
    # rectangle, which is what it would have done anyway.
    regions: list[Region] = field(default_factory=list)

    @property
    def preview(self) -> str:
        return self.text[:100]


@dataclass
class SourceMap:
    doc: str  # source filename
    pages: int
    converter: str = "pymupdf"  # which ingest backend produced this map
    blocks: list[Block] = field(default_factory=list)
    # sha256 of the PDF this map was built from. `doc` cannot serve: a cited
    # source is stored as `<slug>.pdf`, so every source map in a case says the
    # same thing about a different paper. Without a content identity, a
    # directory named after a slug is trusted to hold whatever it holds — and
    # slugs are not eternal, so a re-run can read the previous occupant.
    source_sha256: str | None = None
    # the title the PDF declares about itself (XMP / Info dictionary), verbatim
    # and unjudged. Publishers populate it and the layout does not: measured on
    # seven papers, the first heading is the article-type banner — `CLINICAL
    # GUIDELINE`, `RESEARCH ARTICLE`, `Journal Pre-proofs`, `Editorial` — while
    # the metadata carried the exact title for six of the seven. Recording it
    # raw is provenance; deciding whether it is usable is `paper_title`'s job.
    declared_title: str = ""
    # fidelity warnings the table converter raised while reading this file,
    # verbatim — a cell it could not place is text missing from the document.
    # THREE answers: a list of messages (losses), `[]` (watched, nothing lost),
    # and `None` (nobody watched — a map written before this, or a backend with
    # no table model). Reading `None` as "nothing lost" would be exactly the
    # reassurance this codebase refuses to invent.
    table_warnings: list[str] | None = None

    def to_json(self, path: Path) -> None:
        payload = {
            "doc": self.doc,
            "pages": self.pages,
            "converter": self.converter,
            "source_sha256": self.source_sha256,
            "declared_title": self.declared_title,
            "table_warnings": self.table_warnings,
            "blocks": [
                {
                    **asdict(b),
                    "bbox": list(b.bbox),
                    "regions": [{**asdict(r), "bbox": list(r.bbox)} for r in b.regions],
                    "text_preview": b.preview,
                }
                for b in self.blocks
            ],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: Path) -> SourceMap:
        data = json.loads(path.read_text())
        blocks = [
            Block(
                id=b["id"],
                type=b["type"],
                page=b["page"],
                bbox=tuple(b["bbox"]),
                heading_path=b.get("heading_path", []),
                text=b.get("text", ""),
                # absent on maps written before regions were recorded: empty
                # means not recorded, never "no continuation"
                regions=[
                    Region(
                        page=r["page"],
                        bbox=tuple(r["bbox"]),
                        char_start=r.get("char_start", 0),
                        char_end=r.get("char_end", 0),
                    )
                    for r in b.get("regions", [])
                ],
            )
            for b in data["blocks"]
        ]
        return cls(
            doc=data["doc"],
            pages=data["pages"],
            converter=data.get("converter", "pymupdf"),
            blocks=blocks,
            # absent on maps written before content hashing — None means
            # "unknown", never "matches", so a reader must re-establish it
            source_sha256=data.get("source_sha256"),
            declared_title=data.get("declared_title", ""),
            # absent means NOT RECORDED, never "nothing was lost"
            table_warnings=data.get("table_warnings"),
        )

    def find(self, block_id: str) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)


# ---------------------------------------------------------------------------
# references manifest (refs output)
# ---------------------------------------------------------------------------

REF_STATUSES = ("retrieved", "provided", "paywalled", "mismatch", "no_doi", "unpublished", "error")


def manuscript_fingerprint(path: Path) -> str:
    """Streamed sha256 of a manuscript's bytes — a case folder's real identity.

    A file name is not an identity: two different papers are routinely both
    called `manuscript.pdf`, and one paper is routinely renamed between drafts.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# What a judgeable file *is*, as distinct from which reference it answers for.
# `article` is the cited work itself; `supplement` accompanies one; the audited
# paper's own supplementary material is neither — it answers for no citation
# label at all, which is why it cannot just be a `supplement` with an empty ref.
DOCUMENT_KINDS = ("article", "supplement", "own_supplement")


@dataclass
class Supplement:
    """One supplementary file the user handed over, and the slug it is read as.

    The slug is derived from the FILE STEM, never from an ordinal position in
    the folder. `-suppl1`/`-suppl2` assigned in sorted order is the same defect
    CLAUDE.md rejects for citation occurrences: remove one file and every id
    after it silently shifts, so a re-run points last run's verdicts and
    evidence crops at a different PDF.

    There is no `title_check`. Every article this tool accepts is checked
    against the reference that names it; a supplement's own title does not
    match its parent's, so that check cannot apply and is not faked. A
    supplement is attached on a filename match alone — the weakest provenance
    anything here carries — and the report says so rather than letting it pass
    as an equal of a verified source.
    """

    slug: str
    pdf_path: str
    # did anything establish that this file belongs to the work it is attached
    # to? True when its own title or DOI named that work; False when it was
    # attached because its FILENAME carried the reference's tokens, which is a
    # guess nobody checked. The report tells the two apart rather than warning
    # about both equally.
    verified: bool = False


@dataclass(frozen=True)
class Document:
    """One file a claim can be judged against, with the reference it answers for.

    The join key everything downstream already uses is the *slug*:
    `ingest/<slug>/`, `sources_resolved/<slug>.pdf`, `SourceJudgement.source_slug`
    and `RunResults.source_converters` all key off it. Four call sites used to
    hand-roll `next(e for e in manifest.entries if e.slug == slug)`, which can
    only ever find an article. Resolving through here instead means none of them
    has to learn that supplements exist.
    """

    slug: str
    pdf_path: str | None
    ref_num: str  # the citation label this document answers for; "" for the paper's own
    kind: str  # one of DOCUMENT_KINDS
    parent_slug: str | None  # the article this accompanies, or None


@dataclass
class RefEntry:
    num: str  # citation label as used in the manuscript, e.g. "14"
    raw: str  # the reference string as printed
    doi: str | None = None
    title: str | None = None
    year: str | None = None
    status: str = "error"  # one of REF_STATUSES
    reason: str = ""  # human-readable why (esp. for failures)
    resolver: str | None = None  # crossref | unpaywall | europepmc | arxiv | user
    pdf_path: str | None = None  # local path when retrieved/provided
    slug: str | None = None  # short id used in reports, e.g. "smith-2019"
    # the label appeared twice before the next reference, so where this entry
    # begins is a guess — nothing derived from `raw` may be trusted to identify
    # a paper, and `resolve_entry` refuses rather than fetch a possible wrong one
    boundary_ambiguous: bool = False
    # did anyone establish that this file is the paper the reference names?
    # "verified" | "unverifiable" | "mismatch" | None (no copy to check).
    # A single nullable "did the check fail" flag conflated the first two, so a
    # scanned PDF read as a successful match.
    title_check: str | None = None
    # supplementary files the user supplied for THIS reference. Only ever
    # non-empty when the reference itself is available: a supplement with no
    # article behind it is set aside, because judging a claim against an
    # appendix while calling it the cited source is the laundering this
    # codebase exists to prevent.
    supplements: list[Supplement] = field(default_factory=list)
    # which named readings of the bibliography (e.g. "parsed", "pymupdf",
    # "llm") contributed THIS entry. An entry seen only in the model's reading
    # must be spottable — `["llm"]` says so; `[]` on an older manifest means
    # never computed, not "seen nowhere".
    seen_in: list[str] = field(default_factory=list)


def _ref_entry_from(d: dict) -> RefEntry:
    """One manifest entry, hydrated — the counterpart of `_claim_from` below.

    Two things a bare `RefEntry(**d)` got wrong. It handed `supplements` back as
    a list of plain dicts, because nothing in this manifest was a nested
    dataclass until now and `asdict` flattens on the way out. And it raised
    `TypeError` on any key it did not declare, so a manifest written by a NEWER
    papertrace killed an older one outright instead of ignoring what it could
    not use — the opposite of how every other reader here defaults forward.
    """
    known = {f.name for f in fields(RefEntry)}
    kwargs = {k: v for k, v in d.items() if k in known and k != "supplements"}
    return RefEntry(
        **kwargs,
        supplements=[Supplement(**s) for s in d.get("supplements", [])],
    )


@dataclass
class RefManifest:
    manuscript: str
    entries: list[RefEntry] = field(default_factory=list)
    # content identity of the audited manuscript. Absent on manifests written
    # before content hashing — those fall back to comparing the file name, and
    # say so; they self-heal on the next `papertrace refs`.
    manuscript_sha256: str | None = None
    # the reference list was picked up again after an intervening section, so
    # the entry numbering spans a boundary the parser chose to cross. It is a
    # guess — a defensible one, gated on block type and run length — and the
    # reader has to be able to check it, because the alternative failure is
    # silent: a list parsed short simply reports fewer references.
    references_resumed: bool = False
    # Which reading of the reference list this manifest holds, and whether
    # anything checked it. `parse_references` was the only stage that could not
    # report its own failure, and the label is the join key — a numbering off by
    # one judges every later claim against the wrong paper, silently. The
    # defaults are the honest reading of an older manifest: the parser's list,
    # never checked.
    reference_source: str = "parsed"  # crossref | parsed
    numbering_verified: bool = False
    numbering_note: str = ""
    # the first label from which the numbering is in doubt, or None when it is
    # not in doubt. 1 means "from the very start" — used when there was only one
    # candidate, because a single unchecked reading gives no evidence about
    # *where* it went wrong
    unverified_from: int | None = None
    # A second reading stopped naming the same paper at or below a label the body
    # cites, even though the chosen reading matched the body's labels. `_covers`
    # tests extent, not content, so this is worth the reader's eye even when the
    # count checks out — and a deposit that is merely longer is not it.
    numbering_contested: bool = False
    # Absent means never computed, NOT "nothing was dropped".
    numbering_ledger: dict = field(default_factory=dict)
    # the AUDITED paper's own supplementary material. Not a RefEntry: it answers
    # for no citation label, and putting it in `entries` would inflate
    # `refs_total` and let `_slug_for_ref` hand it to a claim citing a number.
    manuscript_supplements: list[Supplement] = field(default_factory=list)
    # A second, independent axis from `numbering_verified` above: whether
    # EVERY label the body cites was agreed by >= 2 readings. Absent means
    # never computed — not "not corroborated", the same three-state
    # discipline as `numbering_ledger`.
    numbering_corroborated: bool = False
    corroborating_readings: list[str] = field(default_factory=list)
    # Absent means never computed, NOT "none disputed" — a manifest written
    # before this feature says nothing about disputes, it does not assert
    # there were none.
    labels_disputed: list[str] = field(default_factory=list)
    labels_resolved: list[str] = field(default_factory=list)
    # How a disputed numbering was left; "" means the interactive escalation
    # never ran. Never set from a model's own say-so alone.
    numbering_choice: str = ""  # "" | withheld | llm_resolved | parsed | pymupdf
    numbering_chosen_by: str = ""  # "" | default | user
    # The model that produced the LLM's structured reading of the
    # bibliography, when --llm-refs ran and produced anything usable. "" means
    # no such call was made, or nothing it proposed survived verbatim
    # verification against the two texts it was shown — same sentence as the
    # schema's, on purpose: two accounts of what "" means is one too many.
    reflist_model: str = ""
    # Which fields of the LLM's proposed reading could not be found verbatim
    # in either text it was shown, and were discarded rather than trusted.
    reflist_fields_discarded: list[str] = field(default_factory=list)

    def document(self, slug: str) -> Document | None:
        """The judgeable file this slug names, article or supplement, or None."""
        for e in self.entries:
            if e.slug == slug:
                return Document(slug, e.pdf_path, e.num, "article", None)
            for s in e.supplements:
                if s.slug == slug:
                    return Document(slug, s.pdf_path, e.num, "supplement", e.slug)
        for s in self.manuscript_supplements:
            if s.slug == slug:
                return Document(slug, s.pdf_path, "", "own_supplement", None)
        return None

    def documents(self) -> list[Document]:
        """Every judgeable file, once, in reading order.

        A caller walking `entries` sees only articles — which is how the reports
        came to disclose how each *source* was read while saying nothing at all
        about the supplements judged beside them.
        """
        out: list[Document] = []
        for e in self.entries:
            if e.slug:
                out.append(Document(e.slug, e.pdf_path, e.num, "article", None))
            out += [
                Document(s.slug, s.pdf_path, e.num, "supplement", e.slug)
                for s in e.supplements
            ]
        out += [
            Document(s.slug, s.pdf_path, "", "own_supplement", None)
            for s in self.manuscript_supplements
        ]
        return out

    def label_is_doubtful(self, label: str) -> bool:
        """Does a claim citing this label rest on a numbering nobody confirmed?

        An unconfirmed numbering with **no recorded scope** puts every label in
        doubt, rather than none. `unverified_from is None` used to answer False
        for every label while the run-level disclosure rendered "every entry is
        affected" for the same reason — the report asserted that every entry was
        suspect and marked no claim suspect, so a reader acting on a single
        verdict was told nothing. Two shapes reach that state: a manifest
        written before the list was reconciled at all, and two readings that
        agree entry for entry with no arbiter to confirm either. Neither
        establishes *which* entries are wrong, and unknown scope has to read the
        same way in both places.
        """
        if self.numbering_verified:
            return False
        if self.unverified_from is None:
            return True
        try:
            return int(label) >= self.unverified_from
        except (TypeError, ValueError):
            return False

    @property
    def retrieved(self) -> list[RefEntry]:
        return [e for e in self.entries if e.status in ("retrieved", "provided")]

    def summary(self) -> str:
        ok = len(self.retrieved)
        return f"{ok}/{len(self.entries)} sources available"

    def to_json(self, path: Path) -> None:
        payload = {
            "manuscript": self.manuscript,
            "manuscript_sha256": self.manuscript_sha256,
            "references_resumed": self.references_resumed,
            "reference_source": self.reference_source,
            "numbering_verified": self.numbering_verified,
            "numbering_note": self.numbering_note,
            "unverified_from": self.unverified_from,
            "numbering_contested": self.numbering_contested,
            "numbering_ledger": self.numbering_ledger,
            "numbering_corroborated": self.numbering_corroborated,
            "corroborating_readings": self.corroborating_readings,
            "labels_disputed": self.labels_disputed,
            "labels_resolved": self.labels_resolved,
            "numbering_choice": self.numbering_choice,
            "numbering_chosen_by": self.numbering_chosen_by,
            "reflist_model": self.reflist_model,
            "reflist_fields_discarded": self.reflist_fields_discarded,
            "summary": {
                "total": len(self.entries),
                "available": len(self.retrieved),
                "by_status": {
                    s: sum(1 for e in self.entries if e.status == s) for s in REF_STATUSES
                },
            },
            "entries": [asdict(e) for e in self.entries],
            "manuscript_supplements": [asdict(s) for s in self.manuscript_supplements],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: Path) -> RefManifest:
        data = json.loads(path.read_text())
        return cls(
            manuscript=data["manuscript"],
            entries=[_ref_entry_from(e) for e in data["entries"]],
            manuscript_supplements=[
                Supplement(**s) for s in data.get("manuscript_supplements", [])
            ],
            manuscript_sha256=data.get("manuscript_sha256"),
            # .get: a manifest written before this field must still load
            references_resumed=bool(data.get("references_resumed", False)),
            # an older manifest carries the parser's list and never checked it,
            # which is exactly what these defaults say
            reference_source=data.get("reference_source", "parsed"),
            numbering_verified=bool(data.get("numbering_verified", False)),
            numbering_note=data.get("numbering_note", ""),
            unverified_from=data.get("unverified_from"),
            numbering_contested=bool(data.get("numbering_contested", False)),
            numbering_ledger=data.get("numbering_ledger", {}),
            numbering_corroborated=bool(data.get("numbering_corroborated", False)),
            corroborating_readings=data.get("corroborating_readings", []),
            # absent means never computed, not "none disputed" or "none resolved"
            labels_disputed=data.get("labels_disputed", []),
            labels_resolved=data.get("labels_resolved", []),
            numbering_choice=data.get("numbering_choice", ""),
            numbering_chosen_by=data.get("numbering_chosen_by", ""),
            reflist_model=data.get("reflist_model", ""),
            reflist_fields_discarded=data.get("reflist_fields_discarded", []),
        )


# ---------------------------------------------------------------------------
# claim results (check output)
# ---------------------------------------------------------------------------

# a model may answer these — they are judgements about a source it read
JUDGMENT_VERDICTS = ("supported", "partial", "contradicted", "not_addressed")
# only pipeline code assigns these — a model returning one is out of contract
PIPELINE_STATES = ("not_retrieved", "unchecked")
# the pipeline states stay LAST: `not_addressed` was appended to the judgement
# group, so every pre-existing value keeps its position and only the new one is
# additive. counts() gains a key; it never reorders or drops one.
VERDICTS = JUDGMENT_VERDICTS + PIPELINE_STATES

VERDICT_LABEL = {
    "supported": "✅ SUPPORTED",
    "partial": "⚠️ PARTIALLY SUPPORTED",
    "contradicted": "❌ CONTRADICTED",
    # the source was read and simply does not speak to the claim. NOT a
    # contradiction (it says nothing otherwise) and NOT partial (there is no
    # true kernel) — an inapt citation is its own finding
    "not_addressed": "◌ DOES NOT ADDRESS THE CLAIM",
    "not_retrieved": "⊘ NOT RETRIEVED",
    # the source WAS available but the check itself failed — never disguised
    # as a retrieval gap
    "unchecked": "⚠️ NOT CHECKED (check failed — source available)",
}


@dataclass
class SourceJudgement:
    """One cited source's verdict on one claim.

    A claim citing [3] and [5] gets one of these per *available* source: they
    were both offered as support, so both are checked. Each carries its own
    note, page anchor and evidence crop, because a reader comparing two sources
    needs to see which passage each verdict rests on.
    """

    source_slug: str
    ref: str  # the citation label this source answers for, e.g. "3"
    # which of DOCUMENT_KINDS this document is. Stored rather than looked up in
    # the manifest: the templates are handed `results` alone and
    # `run_disclosures` takes the manifest optionally, so a reader with only
    # `results.json` must still be able to tell an appendix from an article.
    kind: str = "article"
    # for a supplement: did anything establish it belongs to the work it was
    # attached to? Carried here for the reason `kind` is — the reader of a
    # results.json alone has no manifest to consult.
    verified: bool = False
    verdict: str = "unchecked"  # one of VERDICTS
    note: str = ""
    source_page: int | None = None
    source_block: str | None = None
    anchor_phrases: list[str] = field(default_factory=list)
    evidence_image: str | None = None  # relative path, filled by highlight
    # the rest of the passage, when it crosses a column or page break: one image
    # per further rectangle, in reading order. Empty for the ordinary
    # single-rectangle passage, and on results written before continuations
    # existed. `evidence_image` stays the first image, so every consumer that
    # reads only that one keeps working.
    continuation_images: list[str] = field(default_factory=list)
    anchor_located: bool | None = None

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict.upper())

    @property
    def origin(self) -> str:
        """Where this verdict came from, in the reader's terms.

        One property rather than `cited as [{{ j.ref }}]` written out in three
        templates: the paper's own supplement answers for no label at all, and
        every one of them would otherwise have rendered `cited as []`.
        """
        if self.kind == "own_supplement":
            return "this paper's own supplement"
        if self.kind == "supplement":
            return f"supplement to [{self.ref}]"
        return f"cited as [{self.ref}]"


# how adverse each judgement is, for picking a claim's headline. A single cited
# source contradicting the claim is the finding a reviewer needs, so it wins
# over any number of sources that support it — the per-source breakdown beside
# it is what keeps that from overstating.
_ADVERSITY = {"supported": 1, "partial": 2, "contradicted": 3}


@dataclass
class ClaimResult:
    id: int
    claim: str  # the claim, tightly paraphrased — what a headline reads well
    # the manuscript's own sentence, verbatim. Empty when the model did not
    # return one: never back-filled from `claim`, which would reinstate exactly
    # the compression the quote exists to remove
    quote: str = ""
    # the claim points at the AUDITED paper's own supplementary material —
    # "Table S3", "eFigure 2", "Supplementary Methods". Not a citation: there is
    # no label, so it cannot travel in `refs`, and a statement whose evidence the
    # paper located precisely is not an assertion made without one.
    own_supplement: bool = False
    location: str = ""  # where in the manuscript, e.g. "Methods §2"
    # ids of the citation occurrences this claim was extracted from, as resolved
    # from the `ctx_NNNN` labels the inventory offered the extractor. Empty when
    # the model named none, or named one that was not in the inventory: never
    # back-filled by guessing which occurrence of the label it must have meant.
    ctx_ids: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)  # citation labels, e.g. ["14"]
    verdict: str = "not_retrieved"  # one of VERDICTS
    note: str = ""  # one/two-sentence finding
    # evidence anchor (set when a source page was read)
    source_slug: str | None = None
    source_page: int | None = None
    source_block: str | None = None  # block id in the source's source_map
    anchor_phrases: list[str] = field(default_factory=list)  # phrases to box in red
    evidence_image: str | None = None  # relative path, filled by highlight step
    # the rest of the passage where it crosses a column or page break — see
    # SourceJudgement.continuation_images. Follows the deciding judgement.
    continuation_images: list[str] = field(default_factory=list)
    # one entry per AVAILABLE cited source, each judged in its own model call
    judgements: list[SourceJudgement] = field(default_factory=list)
    # co-cited refs that could NOT be obtained, so were never opened. They must
    # not be read as having backed the verdict. Sources that WERE available are
    # in `judgements`, not here.
    unjudged_refs: list[str] = field(default_factory=list)
    # False when no anchor phrase was found inside the cropped region — which is
    # one block's bbox, so this is NOT "absent from the page". The crop is still
    # written for context, but it carries no red box and must not claim one
    anchor_located: bool | None = None

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict.upper())

    def is_multi_source(self) -> bool:
        return len(self.judgements) > 1

    def headline_verdict(self) -> str:
        """The most adverse verdict any cited source gave.

        `not_addressed` and `unchecked` cannot become the headline while a
        source actually spoke to the claim — but when none did, saying so *is*
        the answer.

        `unchecked` outranks `not_addressed`, and the reverse order was a bug.
        `not_addressed` asserts that every available source *was read* and none
        spoke to the claim — an inapt citation, a real finding about the paper.
        A source whose check failed was not read, so that assertion is
        unavailable: the tool does not know whether it addressed the claim.
        Ranking `not_addressed` first turned a run failure into a finding, in
        the one field a reader looks at before anything else.
        """
        if not self.judgements:
            return self.verdict
        rated = [j for j in self.judgements if j.verdict in _ADVERSITY]
        if rated:
            return max(rated, key=lambda j: _ADVERSITY[j.verdict]).verdict
        if any(j.verdict == "unchecked" for j in self.judgements):
            return "unchecked"
        if any(j.verdict == "not_addressed" for j in self.judgements):
            return "not_addressed"
        return "unchecked"

    def headline_qualifier(self) -> str:
        """What the headline actually ranged over, for rendering beside it.

        The headline is one source's verdict. On a multi-source claim it reads
        as a statement about the claim, and a compound sentence may legitimately
        draw different parts from different references — so `❌ CONTRADICTED`
        with one dissenter of four overstates by exactly the amount a reader
        cannot see from the status line alone.

        Empty when there is nothing to qualify: one source means the headline
        *is* the claim's verdict, and no judgements means nothing was ranked, so
        naming a comparison that never happened would be its own invention.
        """
        if not self.is_multi_source():
            return ""
        # "cited sources" is false the moment a supplement is among them: one
        # cited work read as two documents is not two cited works, and the
        # count would overstate how many independent papers were consulted.
        noun = "documents" if any(j.kind != "article" for j in self.judgements) else "cited sources"
        return f"most adverse of {len(self.judgements)} {noun}"

    def deciding_judgement(self) -> SourceJudgement | None:
        """The judgement the headline came from — whose page the crop shows."""
        want = self.headline_verdict()
        return next((j for j in self.judgements if j.verdict == want), None)

    def apply_headline(self) -> None:
        """Copy the deciding judgement up to the claim-level fields.

        The top-level verdict/slug/page predate multi-source checking and stay
        the wire format every consumer already reads; they must agree with the
        judgement they came from, or the crop shown beside the headline belongs
        to a different paper.
        """
        if not self.judgements:
            return
        self.verdict = self.headline_verdict()
        d = self.deciding_judgement()
        if d is None:
            return
        self.note = d.note
        self.source_slug = d.source_slug
        self.source_page = d.source_page
        self.source_block = d.source_block
        self.anchor_phrases = list(d.anchor_phrases)
        self.evidence_image = d.evidence_image
        # with the primary, never apart from it: the headline showing one
        # source's opening beside another's continuation is the mismatch this
        # method exists to prevent
        self.continuation_images = list(d.continuation_images)
        self.anchor_located = d.anchor_located

    def judgement_summary(self) -> dict[str, int]:
        """How the cited sources fell out — the count beside the claim."""
        out = {v: 0 for v in VERDICTS}
        for j in self.judgements:
            out[j.verdict] = out.get(j.verdict, 0) + 1
        out["total"] = len(self.judgements)
        return out


@dataclass
class UncitedClaim:
    """An assertive factual statement carrying no citation — flagged for the
    reviewer's judgement, never auto-verified."""

    id: int
    claim: str
    quote: str = ""  # the manuscript's own sentence — what the reviewer judges
    location: str = ""


def _claim_from(d: dict) -> ClaimResult:
    """Rebuild a claim, nested judgements included.

    `.get` so a results.json written before multi-source checking still loads:
    it has no `judgements`, and its claim-level verdict was already the answer.
    """
    d = dict(d)
    d["judgements"] = [SourceJudgement(**j) for j in d.get("judgements", [])]
    return ClaimResult(**d)


@dataclass
class RunResults:
    manuscript: str
    checker: str = "Claude"
    date: str = ""
    refs_total: int = 0
    refs_available: int = 0
    converter: str = "pymupdf"  # ingest backend used for the manuscript
    # slug -> the converter that read THAT cited source. Separate from
    # `converter` above, which is the manuscript's: the two can differ, and a
    # verdict resting on a linearized table is weaker than one resting on the
    # table. An EMPTY dict means the run never recorded this (every 0.4.x
    # file), which is not the same as "all of them were read flat".
    source_converters: dict[str, str] = field(default_factory=dict)
    # per source slug, the fidelity warnings its table converter raised. Only
    # sources that lost something appear; an empty dict means the run did not
    # record this, never that no source lost anything.
    source_table_warnings: dict[str, list[str]] = field(default_factory=dict)
    claims: list[ClaimResult] = field(default_factory=list)
    uncited: list[UncitedClaim] = field(default_factory=list)
    # deterministic citation-label audit: which [N] labels appear in the text,
    # and which of them no extracted claim covers
    coverage: dict = field(default_factory=dict)
    # inputs the character limits cut short — text past the cut was never read,
    # so the run cannot claim to have checked it
    truncated: dict = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {v: sum(1 for c in self.claims if c.verdict == v) for v in VERDICTS}

    def gaps_by_location(self) -> dict[str, list[ClaimResult]]:
        """Unverified claims by section — source not retrieved, or the check
        itself failed (`unchecked`). Both are gaps to report, never silence."""
        out: dict[str, list[ClaimResult]] = {}
        for c in self.claims:
            if c.verdict in PIPELINE_STATES:
                key = c.location.split("§")[0].split("¶")[0].strip() or "Other"
                out.setdefault(key, []).append(c)
        return out

    def to_dict(self) -> dict:
        """The `results.json` payload — the one wire format, whoever consumes it.

        Split from `to_json` so the viewer can embed exactly what the file
        holds: a second serialisation written for the page would be a second
        contract to keep in step with `schemas/results.schema.json`.
        """
        return {
            "manuscript": self.manuscript,
            "checker": self.checker,
            "date": self.date,
            "refs": {"total": self.refs_total, "available": self.refs_available},
            "converter": self.converter,
            "source_converters": self.source_converters,
            "source_table_warnings": self.source_table_warnings,
            "counts": self.counts(),
            "claims": [asdict(c) for c in self.claims],
            "uncited": [asdict(u) for u in self.uncited],
            "coverage": self.coverage,
            "truncated": self.truncated,
        }

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: Path) -> RunResults:
        data = json.loads(path.read_text())
        return cls(
            manuscript=data["manuscript"],
            checker=data.get("checker", "Claude"),
            date=data.get("date", ""),
            refs_total=data.get("refs", {}).get("total", 0),
            refs_available=data.get("refs", {}).get("available", 0),
            converter=data.get("converter", "pymupdf"),
            source_converters=data.get("source_converters", {}),
            source_table_warnings=data.get("source_table_warnings", {}),
            claims=[_claim_from(c) for c in data["claims"]],
            uncited=[UncitedClaim(**u) for u in data.get("uncited", [])],
            coverage=data.get("coverage", {}),
            truncated=data.get("truncated", {}),
        )


# ---------------------------------------------------------------------------
# scout results (literature the reference list doesn't know)
# ---------------------------------------------------------------------------


@dataclass
class ScoutHit:
    """One candidate article surfaced by the literature scout."""

    title: str
    year: int | None = None
    doi: str = ""
    via: str = "search"  # "citing" (cites the paper) | "search" (keyword hit)
    journal: str = ""
    authors: str = ""


@dataclass
class ScoutResults:
    """Post-publication scan around one paper.

    `newer` holds what appeared after the paper (citing articles + later
    keyword hits); `overlooked` holds what was in print *before* the paper's
    year and is absent from its reference list. Both are candidates for the
    user's judgement — search-based, so absence from these lists proves nothing.
    A non-empty `error` means the scan soft-failed and may be incomplete.

    `same_year` is the third register, and it is deliberately not folded into
    either neighbour. A paper from the manuscript's own year may have appeared
    after submission, so "existed but uncited" holds it to a standard no author
    can meet — on one real 2026 manuscript all fifteen overlooked candidates
    were from 2026. It is not `newer` either, since it did not appear after.
    Dropping it would lose a real finding: a paper published early in the same
    year is exactly what a reviewer might legitimately raise.
    """

    paper_title: str = ""
    paper_doi: str = ""
    paper_year: int | None = None
    resolved_via: str = ""  # "doi" | "title" | ""
    # Did anyone establish that the record found is this paper?
    # "confirmed" | "unverified" | "mismatch" | "" (nothing resolved).
    # `resolved_via` cannot answer it: `_resolve_paper` records "doi" whenever a
    # DOI is supplied, and `run` reads the DOI off page 1, so the "wrong paper?"
    # warning stopped firing exactly when the DOI became a guess. The provenance
    # is not recoverable here and is the wrong question anyway — the record's own
    # title is comparable with the paper's.
    paper_identity: str = ""
    query: str = ""  # the keyword query used for the related search
    date: str = ""
    newer: list[ScoutHit] = field(default_factory=list)
    overlooked: list[ScoutHit] = field(default_factory=list)
    # the paper's own year — neither "since" nor "should have known"
    same_year: list[ScoutHit] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        """The `scout.json` payload, for the file and for the viewer alike."""
        return {
            "paper": {
                "title": self.paper_title,
                "doi": self.paper_doi,
                "year": self.paper_year,
                "resolved_via": self.resolved_via,
                "identity": self.paper_identity,
            },
            "query": self.query,
            "date": self.date,
            "counts": {
                "newer": len(self.newer),
                "overlooked": len(self.overlooked),
                "same_year": len(self.same_year),
            },
            "newer": [asdict(h) for h in self.newer],
            "overlooked": [asdict(h) for h in self.overlooked],
            "same_year": [asdict(h) for h in self.same_year],
            "error": self.error,
        }

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: Path) -> ScoutResults:
        data = json.loads(path.read_text())
        paper = data.get("paper", {})
        return cls(
            paper_title=paper.get("title", ""),
            paper_doi=paper.get("doi", ""),
            paper_year=paper.get("year"),
            resolved_via=paper.get("resolved_via", ""),
            # absent on scout.json written before the check existed: "" reads as
            # not recorded, never as confirmed
            paper_identity=paper.get("identity", ""),
            query=data.get("query", ""),
            date=data.get("date", ""),
            newer=[ScoutHit(**h) for h in data.get("newer", [])],
            overlooked=[ScoutHit(**h) for h in data.get("overlooked", [])],
            # .get: a scout.json written before the third register still loads
            same_year=[ScoutHit(**h) for h in data.get("same_year", [])],
            error=data.get("error", ""),
        )
