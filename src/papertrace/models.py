"""Data model for a PaperTrace run.

Plain dataclasses with dict round-tripping — the JSON files they produce
(`source_map.json`, `refs_manifest.json`, `results.json`) are the contract
between the pipeline steps and between the agent and the deterministic tools.
JSON Schemas for them live in `schemas/`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# source map (ingest output)
# ---------------------------------------------------------------------------


BLOCK_TYPES = ("sectionheader", "text", "table", "picture", "list")


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

REF_STATUSES = ("retrieved", "provided", "paywalled", "no_doi", "unpublished", "error")


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


@dataclass
class RefManifest:
    manuscript: str
    entries: list[RefEntry] = field(default_factory=list)

    @property
    def retrieved(self) -> list[RefEntry]:
        return [e for e in self.entries if e.status in ("retrieved", "provided")]

    def summary(self) -> str:
        ok = len(self.retrieved)
        return f"{ok}/{len(self.entries)} sources available"

    def to_json(self, path: Path) -> None:
        payload = {
            "manuscript": self.manuscript,
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
        )


# ---------------------------------------------------------------------------
# claim results (check output)
# ---------------------------------------------------------------------------

VERDICTS = ("supported", "partial", "contradicted", "not_retrieved", "unchecked")

VERDICT_LABEL = {
    "supported": "✅ SUPPORTED",
    "partial": "⚠️ PARTIALLY SUPPORTED",
    "contradicted": "❌ CONTRADICTED",
    "not_retrieved": "⊘ NOT RETRIEVED",
    # the source WAS available but the check itself failed — never disguised
    # as a retrieval gap
    "unchecked": "⚠️ NOT CHECKED (check failed — source available)",
}


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

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict.upper())


@dataclass
class UncitedClaim:
    """An assertive factual statement carrying no citation — flagged for the
    reviewer's judgement, never auto-verified."""

    id: int
    claim: str
    location: str = ""


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

    def counts(self) -> dict[str, int]:
        return {v: sum(1 for c in self.claims if c.verdict == v) for v in VERDICTS}

    def gaps_by_location(self) -> dict[str, list[ClaimResult]]:
        """Unverified claims by section — source not retrieved, or the check
        itself failed (`unchecked`). Both are gaps to report, never silence."""
        out: dict[str, list[ClaimResult]] = {}
        for c in self.claims:
            if c.verdict in ("not_retrieved", "unchecked"):
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
            claims=[ClaimResult(**c) for c in data["claims"]],
            uncited=[UncitedClaim(**u) for u in data.get("uncited", [])],
            coverage=data.get("coverage", {}),
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
