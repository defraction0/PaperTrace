"""PaperTrace as an MCP server — audits started, followed and read from any MCP host.

The server is a fifth reader of everything the four report formats carry, and
it is held to their rules: it computes no verdict, every count travels with the
disclosures `disclosures.py` decided for it, and every refusal says why. The
design and what it rejected: `docs/superpowers/specs/2026-09-26-mcp-server-design.md`.
"""

# No `from __future__ import annotations`, unlike the rest of the package: the
# SDK builds each tool's input and output schema from its annotations when the
# tool is registered, and live types are what it reads without guessing.

from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field

# Not `typing.TypedDict`: below Python 3.12 pydantic cannot build a schema from
# it, and the SDK then drops the tool to unvalidated text without a word — every
# tool here published no output schema on 3.10 and 3.11 until a test asked.
# `structured_output=True` on each tool turns any repeat into an error at start.
from typing_extensions import TypedDict

from . import __version__
from .disclosures import (
    anchor_disclosure,
    claim_disclosures,
    coverage_headline,
    ids_span,
    judgement_disclosures,
    run_disclosures,
)
from .models import (
    PIPELINE_STATES,
    REF_STATUSES,
    VERDICTS,
    ClaimResult,
    RefManifest,
    RunResults,
    ScoutResults,
    SourceJudgement,
)

# the viewer's serialisation of a disclosure, reused rather than restated: one
# shape for every reader that receives disclosures as data
from .report import _disclosure_dict

INSTRUCTIONS = """\
PaperTrace checks what a scientific paper claims against what its cited sources say.

Workflow: start_audit, then audit_status until it is finished (wait_seconds up to 50),
then audit_summary. From there: list_claims, get_claim, get_evidence (the red-box crop
of the passage a verdict rests on), list_references, list_gaps, get_scout. A case folder
made by the `papertrace` CLI can be read the same way.

Relaying results:
- An unread source never receives a verdict. `not_retrieved` means nobody read the
  source: report it as a gap, never as support or contradiction.
- Relay the disclosures that come with a result. They say what the numbers do not.
- `limited` set means the audit was cut short on request: its counts describe a slice,
  not the paper.
- Verdicts are a model's drafts for a person's judgement; the evidence images show the
  passage each rests on. Scout hits are candidates, not accusations.
"""

# The CLI's sentence, verbatim: the scout's candidates are search results.
SCOUT_CAVEAT = (
    "search-based — absence from these lists proves nothing; presence is a candidate "
    "for your judgement, not an accusation."
)

# read tools change nothing and consult nothing outside the case folder
_READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)

# The vocabularies `models.py` defines, not copies of them. `Literal` over the
# tuple is the same type as the values spelled out.
Verdict = Literal[VERDICTS]
RefStatus = Literal[REF_STATUSES]

CaseArg = Annotated[
    str,
    Field(description=(
        "The case folder of one audit: the folder holding refs_manifest.json and out/. "
        "Use an absolute path — a relative one is resolved against this server's working "
        "directory, which the host chose."
    )),
]
ClaimId = Annotated[int, Field(description="A claim's number, as list_claims gives it.", ge=1)]


# --------------------------------------------------------------------------
# what the tools return — each shape is also the published output schema
# --------------------------------------------------------------------------


class DisclosureOut(TypedDict):
    """One thing a result owes its reader, in the words every report format uses."""

    key: str
    level: str
    token: str
    text: str
    short: str
    rows: list[str]


class RefCounts(TypedDict):
    total: int
    available: int


class AuditSummary(TypedDict):
    case: str
    manuscript: str
    checker: str
    date: str
    converter: str
    references: RefCounts
    claims: int
    counts: dict[str, int]
    uncited_assertions: int
    coverage: str | None
    disclosures: list[DisclosureOut]
    reports: list[str]
    limited: str | None


class ClaimRow(TypedDict):
    id: int
    verdict: str
    label: str
    qualifier: str
    refs: list[str]
    location: str
    claim: str
    quote: str
    caveats: list[str]


class ClaimList(TypedDict):
    case: str
    verdict_filter: str | None
    claims_total: int
    claims: list[ClaimRow]
    limited: str | None


class JudgementOut(TypedDict):
    source: str
    origin: str
    kind: str
    verdict: str
    label: str
    rationale: str
    page: int | None
    block: str | None
    anchor_phrases: list[str]
    anchor_located: bool | None
    evidence_images: list[str]
    disclosures: list[DisclosureOut]


class ClaimDetail(TypedDict):
    case: str
    id: int
    verdict: str
    label: str
    qualifier: str
    claim: str
    quote: str
    location: str
    refs: list[str]
    own_supplement: bool
    rationale: str
    source: str | None
    page: int | None
    block: str | None
    anchor_phrases: list[str]
    anchor_located: bool | None
    evidence_images: list[str]
    judgements: list[JudgementOut]
    unjudged_refs: list[str]
    withheld_refs: list[str]
    disclosures: list[DisclosureOut]


class SupplementOut(TypedDict):
    slug: str
    verified: bool


class ReferenceRow(TypedDict):
    label: str
    status: str
    reason: str
    resolver: str | None
    doi: str | None
    title: str | None
    year: str | None
    slug: str | None
    title_check: str | None
    skipped_by: str | None
    boundary_ambiguous: bool
    seen_in: list[str]
    supplements: list[SupplementOut]
    raw: str


class NumberingOut(TypedDict):
    source: str
    verified: bool
    note: str
    unverified_from: int | None
    contested: bool
    corroborated: bool | None
    corroborating_readings: list[str]
    labels_disputed: list[str]
    labels_uncomparable: list[str] | None
    labels_resolved: list[str]
    choice: str
    chosen_by: str


class ReferenceList(TypedDict):
    case: str
    manuscript: str
    total: int
    available: int
    by_status: dict[str, int]
    numbering: NumberingOut
    limits: dict[str, Any]
    status_filter: str | None
    references: list[ReferenceRow]


class UncitedOut(TypedDict):
    id: int
    claim: str
    quote: str
    location: str


class UnreachedOut(TypedDict):
    label: str
    status: str
    section: str
    sentence: str


class UncheckedOut(TypedDict):
    id: int
    verdict: str
    label: str
    refs: list[str]
    location: str
    note: str


class Gaps(TypedDict):
    case: str
    uncited_assertions: list[UncitedOut]
    coverage: str | None
    unreached_labels: list[str] | None
    unreached_citations: list[UnreachedOut] | None
    unchecked_claims: list[UncheckedOut]
    disclosures: list[DisclosureOut]
    limited: str | None


class ScoutPaperOut(TypedDict):
    title: str
    doi: str
    year: int | None
    resolved_via: str
    identity: str


class ScoutHitOut(TypedDict):
    title: str
    year: int | None
    doi: str
    via: str
    journal: str
    authors: str


class ScoutOut(TypedDict):
    case: str
    paper: ScoutPaperOut
    query: str
    date: str
    counts: dict[str, int]
    newer: list[ScoutHitOut]
    overlooked: list[ScoutHitOut]
    same_year: list[ScoutHitOut]
    error: str
    caveat: str


# --------------------------------------------------------------------------
# reading a case folder — every failure is a sentence, never an empty result
# --------------------------------------------------------------------------


def _case_dir(case: str) -> Path:
    return Path(case).expanduser().resolve()


def _read(path: Path, load, stage: str):
    """One case file, loaded — or a refusal naming why it could not be."""
    try:
        return load(path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ToolError(
            f"{path} is unreadable ({type(e).__name__}: {str(e)[:200]}) — an audit may be "
            f"writing it right now; if not, re-run `papertrace {stage}` for this case"
        ) from None


def _results(case: Path) -> RunResults:
    path = case / "out" / "results.json"
    if not path.is_file():
        # absent is not zero: empty counts would read as an audit that found nothing
        raise ToolError(
            f"no audit results at {path}"
            + ("" if case.is_dir() else f" — {case} does not exist")
            + ". No claim has been checked in this case folder: start_audit runs the "
            "whole pipeline, and `papertrace check -c <case>` runs the judging alone."
        )
    return _read(path, RunResults.from_json, "check")


def _manifest(case: Path, *, required: bool) -> RefManifest | None:
    path = case / "refs_manifest.json"
    if not path.is_file():
        if required:
            raise ToolError(
                f"no retrieval manifest at {path} — the refs stage has not run in this "
                "case folder, so no reference has been looked up there"
            )
        return None
    return _read(path, RefManifest.from_json, "refs")


def _disclosures(fired) -> list[DisclosureOut]:
    return [_disclosure_dict(d) for d in fired]


def _limited(fired) -> str | None:
    """The scope sentence, when the audit was limited on request — else None.

    Taken from the run disclosures rather than composed here, so the last thing
    a result says is the sentence every report format ends with.
    """
    return next((d.text for d in fired if d.key == "scope"), None)


def _find_claim(results: RunResults, claim_id: int) -> ClaimResult:
    for c in results.claims:
        if c.id == claim_id:
            return c
    ids = [c.id for c in results.claims]
    raise ToolError(
        f"no claim {claim_id} in this audit — its claims are {ids_span(ids) if ids else 'none'}"
        + (" (the audit was limited on request; audit_summary's `limited` says what was left "
           "out)" if results.scope else "")
    )


def _claim_row(c: ClaimResult, manifest: RefManifest | None) -> ClaimRow:
    return {
        "id": c.id,
        "verdict": c.verdict,
        "label": c.label,
        "qualifier": c.headline_qualifier(),
        "refs": list(c.refs),
        "location": c.location,
        "claim": c.claim,
        "quote": c.quote,
        # the terse line of each claim disclosure: every one carries its token
        "caveats": [d.short for d in claim_disclosures(c, manifest)],
    }


def _images_of(a) -> list[str]:
    return [p for p in [a.evidence_image, *a.continuation_images] if p]


def _judgement_out(j: SourceJudgement) -> JudgementOut:
    return {
        "source": j.source_slug,
        "origin": j.origin,
        "kind": j.kind,
        "verdict": j.verdict,
        "label": j.label,
        "rationale": j.note,
        "page": j.source_page,
        "block": j.source_block,
        "anchor_phrases": list(j.anchor_phrases),
        "anchor_located": j.anchor_located,
        "evidence_images": _images_of(j),
        "disclosures": _disclosures(judgement_disclosures(j)),
    }


def _evidence_target(claim: ClaimResult, source: str | None):
    """The judgement whose crop to show: the headline's, or the named source's."""
    if source is None:
        return claim.deciding_judgement() or claim
    for j in claim.judgements:
        if j.source_slug == source:
            return j
    if not claim.judgements and claim.source_slug == source:
        return claim
    known = [j.source_slug for j in claim.judgements] or [s for s in [claim.source_slug] if s]
    raise ToolError(
        f"claim {claim.id} was not judged against {source!r} — "
        f"its sources are {', '.join(known) if known else 'none'}"
    )


def _no_evidence(claim: ClaimResult, target) -> str:
    """Why there is no picture — one reason per state, never a blank."""
    who = f"claim {claim.id}"
    if target.verdict == "not_retrieved":
        return (
            f"{who} has no evidence to show: its cited source was not retrieved, so nothing "
            "was read — a recorded gap, not a finding"
        )
    if target.verdict == "unchecked":
        return (
            f"{who} has no evidence to show: no verdict was reached "
            f"({target.note or 'the check did not complete'})"
        )
    if target.verdict == "not_addressed":
        return (
            f"{who}'s source was read and does not address the claim — that verdict rests "
            "on no passage, so it carries no crop by design"
        )
    return (
        f"{who} is {target.verdict}, but no evidence crop has been written for it — the "
        "highlight stage writes the crops (`papertrace highlight -c <case>`)"
    )


def _crop_paths(case: Path, claim: ClaimResult, rels: list[str]) -> list[Path]:
    """The crops, resolved — refused if one points anywhere but `<case>/out/`."""
    out = (case / "out").resolve()
    paths = []
    for rel in rels:
        p = (out / rel).resolve()
        # `results.json` is a file anyone can edit: a path is followed only when
        # it lands inside the folder the highlight stage writes, and is a PNG
        if not p.is_relative_to(out) or p.suffix.lower() != ".png":
            raise ToolError(
                f"claim {claim.id}'s evidence path {rel!r} resolves outside {out} or is not "
                "a PNG — refused rather than followed; this server serves only the crops "
                "the highlight stage writes into the case folder"
            )
        paths.append(p)
    return paths


def _caption(claim: ClaimResult, target, shown: list[Path], missing: list[str]) -> str:
    qualifier = claim.headline_qualifier()
    lines = [f"claim {claim.id} — {claim.label}" + (f" ({qualifier})" if qualifier else "")]
    if claim.quote:
        lines.append(f"the paper's sentence: {claim.quote}")
    origin = f"{target.origin} " if isinstance(target, SourceJudgement) else ""
    lines.append(
        f"{target.label} — {origin}{target.source_slug}"
        + (f", page {target.source_page}" if target.source_page else "")
    )
    if (d := anchor_disclosure(target)) is not None:
        lines.append(d.text)
    if target.anchor_phrases:
        lines.append("anchor phrases: " + " · ".join(f"“{p}”" for p in target.anchor_phrases))
    if len(shown) > 1:
        lines.append(
            f"the passage crosses a column or page break — {len(shown)} images, in reading order"
        )
    if missing:
        lines.append(f"not in the case folder, so not shown: {', '.join(missing)}")
    return "\n".join(lines)


def _coverage_audited(coverage: dict) -> bool:
    """Did the coverage audit run? The rule `run_disclosures` applies."""
    return bool(coverage.get("labels_in_text") or (coverage.get("occurrences") or {}).get("total"))


def _scout_caveat(scout: ScoutResults) -> str:
    """The scout's own caveat, led by the identity warning the CLI prints."""
    identity = scout.paper_identity
    if not scout.paper_title or identity == "confirmed":
        lead = ""
    elif identity == "mismatch":
        lead = "The record found is NOT this paper, so these registers describe another one. "
    elif identity == "unverified":
        lead = "The paper's identity in Europe PMC is unverified — check the record is this paper. "
    else:
        lead = "The paper's identity was not recorded — check the record is this paper. "
    return lead + SCOUT_CAVEAT[0].upper() + SCOUT_CAVEAT[1:]


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------


def build_server() -> MCPServer:
    """A fresh server with every tool registered. Nothing is served until `run()`."""
    server = MCPServer("papertrace", instructions=INSTRUCTIONS, version=__version__)

    @server.tool(title="Audit summary", annotations=_READ, structured_output=True)
    def audit_summary(case: CaseArg) -> AuditSummary:
        """The result of one audit: verdict counts, references obtained, the model that
        judged, coverage, and every run-level disclosure — relay those with the counts.
        `limited` is set when the audit was cut short on request."""
        root = _case_dir(case)
        results = _results(root)
        manifest = _manifest(root, required=False)
        fired = run_disclosures(results, manifest)
        return {
            "case": str(root),
            "manuscript": results.manuscript,
            "checker": results.checker,
            "date": results.date,
            "converter": results.converter,
            "references": {"total": results.refs_total, "available": results.refs_available},
            "claims": len(results.claims),
            "counts": results.counts(),
            "uncited_assertions": len(results.uncited),
            # null when the audit could not run — never "" or a zero ratio
            "coverage": coverage_headline(results.coverage or {}) or None,
            "disclosures": _disclosures(fired),
            "reports": sorted(str(p) for p in (root / "out").glob("report*.*") if p.is_file()),
            "limited": _limited(fired),
        }

    @server.tool(title="List claims", annotations=_READ, structured_output=True)
    def list_claims(
        case: CaseArg,
        verdict: Annotated[
            Verdict | None,
            Field(description="Only claims whose headline verdict is this one."),
        ] = None,
    ) -> ClaimList:
        """Every checked claim, one row each: its headline verdict, the paper's own
        sentence, the labels it cites, and its caveats. A claim citing several sources is
        headlined by the most adverse verdict; get_claim shows each source's."""
        root = _case_dir(case)
        results = _results(root)
        manifest = _manifest(root, required=False)
        rows = [_claim_row(c, manifest) for c in results.claims if verdict in (None, c.verdict)]
        return {
            "case": str(root),
            "verdict_filter": verdict,
            "claims_total": len(results.claims),
            "claims": rows,
            "limited": _limited(run_disclosures(results, manifest)),
        }

    @server.tool(title="Claim detail", annotations=_READ, structured_output=True)
    def get_claim(case: CaseArg, claim_id: ClaimId) -> ClaimDetail:
        """One claim in full: the paper's sentence, each cited source's verdict with its
        rationale, page, block and anchor phrases, and every disclosure the claim owes its
        reader. get_evidence shows the passage itself."""
        root = _case_dir(case)
        results = _results(root)
        manifest = _manifest(root, required=False)
        c = _find_claim(results, claim_id)
        return {
            "case": str(root),
            "id": c.id,
            "verdict": c.verdict,
            "label": c.label,
            "qualifier": c.headline_qualifier(),
            "claim": c.claim,
            "quote": c.quote,
            "location": c.location,
            "refs": list(c.refs),
            "own_supplement": c.own_supplement,
            "rationale": c.note,
            "source": c.source_slug,
            "page": c.source_page,
            "block": c.source_block,
            "anchor_phrases": list(c.anchor_phrases),
            "anchor_located": c.anchor_located,
            "evidence_images": _images_of(c),
            "judgements": [_judgement_out(j) for j in c.judgements],
            "unjudged_refs": list(c.unjudged_refs),
            "withheld_refs": list(c.withheld_refs),
            "disclosures": _disclosures(claim_disclosures(c, manifest)),
        }

    @server.tool(title="Evidence crops", annotations=_READ, structured_output=False)
    def get_evidence(
        case: CaseArg,
        claim_id: ClaimId,
        source: Annotated[
            str | None,
            Field(description="A source slug from get_claim's judgements; default: the source "
                              "behind the headline verdict."),
        ] = None,
    ) -> list[TextContent | Image]:
        """The page crop a verdict rests on, as images, with the matched text boxed in red.
        The boxes were drawn by code finding the model's anchor phrases in the PDF, not
        placed by the model; the caption says whether the phrase was found."""
        root = _case_dir(case)
        claim = _find_claim(_results(root), claim_id)
        target = _evidence_target(claim, source)
        rels = _images_of(target)
        if not rels:
            raise ToolError(_no_evidence(claim, target))
        paths = _crop_paths(root, claim, rels)
        out = (root / "out").resolve()
        missing = [str(p.relative_to(out)) for p in paths if not p.is_file()]
        shown = [p for p in paths if p.is_file()]
        if not shown:
            raise ToolError(
                f"claim {claim.id}'s evidence crop{'s' if len(missing) > 1 else ''} "
                f"{', '.join(missing)} {'are' if len(missing) > 1 else 'is'} named in "
                f"results.json but not in {out} — re-run `papertrace highlight -c <case>`"
            )
        return [
            TextContent(type="text", text=_caption(claim, target, shown, missing)),
            *(Image(path=p) for p in shown),
        ]

    @server.tool(title="References", annotations=_READ, structured_output=True)
    def list_references(
        case: CaseArg,
        status: Annotated[
            RefStatus | None,
            Field(description="Only references with this retrieval status."),
        ] = None,
    ) -> ReferenceList:
        """The retrieval manifest: each cited reference, whether it was obtained and why
        not, and the state of the reference numbering — the label is what joins a claim to
        its source, so an unconfirmed or disputed numbering is a caveat on every verdict
        that rests on it. Local file paths are not included."""
        root = _case_dir(case)
        m = _manifest(root, required=True)
        rows: list[ReferenceRow] = [
            {
                "label": e.num,
                "status": e.status,
                "reason": e.reason,
                "resolver": e.resolver,
                "doi": e.doi,
                "title": e.title,
                "year": e.year,
                "slug": e.slug,
                "title_check": e.title_check,
                "skipped_by": e.skipped_by,
                "boundary_ambiguous": e.boundary_ambiguous,
                "seen_in": list(e.seen_in),
                "supplements": [{"slug": s.slug, "verified": s.verified} for s in e.supplements],
                "raw": e.raw,
            }
            for e in m.entries
            if status in (None, e.status)
        ]
        return {
            "case": str(root),
            "manuscript": m.manuscript,
            "total": len(m.entries),
            "available": len(m.retrieved),
            "by_status": {s: sum(1 for e in m.entries if e.status == s) for s in REF_STATUSES},
            # passed through as recorded: `null` is "never computed" and `[]` a
            # measurement, and no reader may be handed one for the other
            "numbering": {
                "source": m.reference_source,
                "verified": m.numbering_verified,
                "note": m.numbering_note,
                "unverified_from": m.unverified_from,
                "contested": m.numbering_contested,
                "corroborated": m.numbering_corroborated,
                "corroborating_readings": list(m.corroborating_readings),
                "labels_disputed": list(m.labels_disputed),
                "labels_uncomparable": (
                    None if m.labels_uncomparable is None else list(m.labels_uncomparable)
                ),
                "labels_resolved": list(m.labels_resolved),
                "choice": m.numbering_choice,
                "chosen_by": m.numbering_chosen_by,
            },
            "limits": dict(m.limits),
            "status_filter": status,
            "references": rows,
        }

    @server.tool(title="Gaps", annotations=_READ, structured_output=True)
    def list_gaps(case: CaseArg) -> Gaps:
        """What the audit could not vouch for: assertions carrying no citation, citation
        places no extracted claim reached, and claims never checked (source not retrieved,
        or no verdict reached). `null` means that audit did not run — not that it found
        nothing."""
        root = _case_dir(case)
        results = _results(root)
        manifest = _manifest(root, required=False)
        coverage = results.coverage or {}
        audited = _coverage_audited(coverage)
        occurrences = coverage.get("occurrences")
        fired = run_disclosures(results, manifest)
        return {
            "case": str(root),
            "uncited_assertions": [
                {"id": u.id, "claim": u.claim, "quote": u.quote, "location": u.location}
                for u in results.uncited
            ],
            "coverage": coverage_headline(coverage) or None,
            "unreached_labels": list(coverage.get("missing") or []) if audited else None,
            "unreached_citations": (
                [
                    {"label": o["label"], "status": o["status"],
                     "section": o.get("section", ""), "sentence": o.get("sentence", "")}
                    for o in occurrences.get("items", [])
                    if o.get("status") in ("uncovered", "uncertain")
                ]
                if audited and occurrences else None
            ),
            "unchecked_claims": [
                {"id": c.id, "verdict": c.verdict, "label": c.label, "refs": list(c.refs),
                 "location": c.location, "note": c.note}
                for c in results.claims
                if c.verdict in PIPELINE_STATES
            ],
            "disclosures": _disclosures(fired),
            "limited": _limited(fired),
        }

    @server.tool(title="Literature scout", annotations=_READ, structured_output=True)
    def get_scout(case: CaseArg) -> ScoutOut:
        """Literature around the paper from Europe PMC: work published since, work that
        existed before it and went uncited, and work from the same year. Search-based:
        candidates for a person's judgement, and absence proves nothing."""
        root = _case_dir(case)
        path = root / "out" / "scout.json"
        if not path.is_file():
            raise ToolError(
                f"no literature scan at {path} — the audit ran without the scout, or the "
                "scout stage has not run (`papertrace scout -c <case>`)"
            )
        scout = _read(path, ScoutResults.from_json, "scout")
        return {"case": str(root), **scout.to_dict(), "caveat": _scout_caveat(scout)}

    return server
