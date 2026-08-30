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
from dataclasses import asdict, dataclass, field
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

    @property
    def preview(self) -> str:
        return self.text[:100]


@dataclass
class SourceMap:
    doc: str  # source filename
    pages: int
    converter: str = "pymupdf"  # which ingest backend produced this map
    blocks: list[Block] = field(default_factory=list)

    def to_json(self, path: Path) -> None:
        payload = {
            "doc": self.doc,
            "pages": self.pages,
            "converter": self.converter,
            "blocks": [
                {**asdict(b), "bbox": list(b.bbox), "text_preview": b.preview} for b in self.blocks
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
            )
            for b in data["blocks"]
        ]
        return cls(
            doc=data["doc"],
            pages=data["pages"],
            converter=data.get("converter", "pymupdf"),
            blocks=blocks,
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


@dataclass
class RefManifest:
    manuscript: str
    entries: list[RefEntry] = field(default_factory=list)
    # content identity of the audited manuscript. Absent on manifests written
    # before content hashing — those fall back to comparing the file name, and
    # say so; they self-heal on the next `papertrace refs`.
    manuscript_sha256: str | None = None

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
            "summary": {
                "total": len(self.entries),
                "available": len(self.retrieved),
                "by_status": {
                    s: sum(1 for e in self.entries if e.status == s) for s in REF_STATUSES
                },
            },
            "entries": [asdict(e) for e in self.entries],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: Path) -> RefManifest:
        data = json.loads(path.read_text())
        return cls(
            manuscript=data["manuscript"],
            entries=[RefEntry(**e) for e in data["entries"]],
            manuscript_sha256=data.get("manuscript_sha256"),
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
    verdict: str = "unchecked"  # one of VERDICTS
    note: str = ""
    source_page: int | None = None
    source_block: str | None = None
    anchor_phrases: list[str] = field(default_factory=list)
    evidence_image: str | None = None  # relative path, filled by highlight
    anchor_located: bool | None = None

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict.upper())


# how adverse each judgement is, for picking a claim's headline. A single cited
# source contradicting the claim is the finding a reviewer needs, so it wins
# over any number of sources that support it — the per-source breakdown beside
# it is what keeps that from overstating.
_ADVERSITY = {"supported": 1, "partial": 2, "contradicted": 3}


@dataclass
class ClaimResult:
    id: int
    claim: str  # the claim, quoted or tightly paraphrased
    location: str  # where in the manuscript, e.g. "Methods §2"
    refs: list[str] = field(default_factory=list)  # citation labels, e.g. ["14"]
    verdict: str = "not_retrieved"  # one of VERDICTS
    note: str = ""  # one/two-sentence finding
    # evidence anchor (set when a source page was read)
    source_slug: str | None = None
    source_page: int | None = None
    source_block: str | None = None  # block id in the source's source_map
    anchor_phrases: list[str] = field(default_factory=list)  # phrases to box in red
    evidence_image: str | None = None  # relative path, filled by highlight step
    # one entry per AVAILABLE cited source, each judged in its own model call
    judgements: list[SourceJudgement] = field(default_factory=list)
    # co-cited refs that could NOT be obtained, so were never opened. They must
    # not be read as having backed the verdict. Sources that WERE available are
    # in `judgements`, not here.
    unjudged_refs: list[str] = field(default_factory=list)
    # False when no anchor phrase was found on the page: the crop is still
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

    def to_json(self, path: Path) -> None:
        payload = {
            "manuscript": self.manuscript,
            "checker": self.checker,
            "date": self.date,
            "refs": {"total": self.refs_total, "available": self.refs_available},
            "converter": self.converter,
            "counts": self.counts(),
            "claims": [asdict(c) for c in self.claims],
            "uncited": [asdict(u) for u in self.uncited],
            "coverage": self.coverage,
            "truncated": self.truncated,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

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
    keyword hits); `overlooked` holds what existed by the paper's year but is
    absent from its reference list. Both are candidates for the user's
    judgement — search-based, so absence from these lists proves nothing.
    A non-empty `error` means the scan soft-failed and may be incomplete.
    """

    paper_title: str = ""
    paper_doi: str = ""
    paper_year: int | None = None
    resolved_via: str = ""  # "doi" | "title" | ""
    query: str = ""  # the keyword query used for the related search
    date: str = ""
    newer: list[ScoutHit] = field(default_factory=list)
    overlooked: list[ScoutHit] = field(default_factory=list)
    error: str = ""

    def to_json(self, path: Path) -> None:
        payload = {
            "paper": {
                "title": self.paper_title,
                "doi": self.paper_doi,
                "year": self.paper_year,
                "resolved_via": self.resolved_via,
            },
            "query": self.query,
            "date": self.date,
            "counts": {"newer": len(self.newer), "overlooked": len(self.overlooked)},
            "newer": [asdict(h) for h in self.newer],
            "overlooked": [asdict(h) for h in self.overlooked],
            "error": self.error,
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: Path) -> ScoutResults:
        data = json.loads(path.read_text())
        paper = data.get("paper", {})
        return cls(
            paper_title=paper.get("title", ""),
            paper_doi=paper.get("doi", ""),
            paper_year=paper.get("year"),
            resolved_via=paper.get("resolved_via", ""),
            query=data.get("query", ""),
            date=data.get("date", ""),
            newer=[ScoutHit(**h) for h in data.get("newer", [])],
            overlooked=[ScoutHit(**h) for h in data.get("overlooked", [])],
            error=data.get("error", ""),
        )
