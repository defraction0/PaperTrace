"""PaperTrace as an MCP server — audits started, followed and read from any MCP host.

The server is a fifth reader of everything the four report formats carry, and
it is held to their rules: it computes no verdict, every count travels with the
disclosures `disclosures.py` decided for it, and every refusal says why. The
design and what it rejected: `docs/superpowers/specs/2026-09-26-mcp-server-design.md`.
"""

# No `from __future__ import annotations`, unlike the rest of the package: the
# SDK builds each tool's input and output schema from its annotations when the
# tool is registered, and live types are what it reads without guessing.

import datetime
import io
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field
from rich.console import Console

# Not `typing.TypedDict`: below Python 3.12 pydantic cannot build a schema from
# it, and the SDK then drops the tool to unvalidated text without a word — every
# tool here published no output schema on 3.10 and 3.11 until a test asked.
# `structured_output=True` on each tool turns any repeat into an error at start.
from typing_extensions import TypedDict

from . import __version__, cli
from .ask import forget_models
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
from .report import FORMATS, _disclosure_dict

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
# The one tool that spends says all of it, because a host decides on these
# whether to ask first: it rewrites the case folder, queries the network and
# calls a model, and no two runs of a model are the same run.
_SPEND = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True
)

# The vocabularies `models.py` and `report.py` define, not copies of them.
# `Literal` over the tuple is the same type as the values spelled out.
Verdict = Literal[VERDICTS]
RefStatus = Literal[REF_STATUSES]
Format = Literal[FORMATS]
# No constant holds this one; `ingest.resolve_backend` refuses anything else,
# and a test holds the two together.
Backend = Literal["auto", "docling", "pymupdf"]

# A TypeScript-SDK host gives a request 60 s; a status call waits for less.
MAX_WAIT_SECONDS = 50

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


class AuditStatus(TypedDict):
    """Where an audit this server started stands. Never its counts: those travel
    only with the disclosures that qualify them, in audit_summary."""

    case: str
    state: Literal["running", "finished", "failed", "not_started"]
    manuscript: str | None
    started: str | None
    ended: str | None
    error: str
    log: list[str]
    log_lines_total: int
    next: str


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
# the audit job — the pipeline, in this process, with nobody to ask
# --------------------------------------------------------------------------


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class _Lines(io.TextIOBase):
    """A text stream kept as whole lines — the file the pipeline's console writes to."""

    def __init__(self, lines: list[str]):
        self._lines = lines
        self._open = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        with self._lock:
            *whole, self._open = (self._open + s).split("\n")
            self._lines.extend(whole)
        return len(s)

    def close(self) -> None:
        with self._lock:
            if self._open:  # a last line printed without its newline is still said
                self._lines.append(self._open)
                self._open = ""
        super().close()


@contextmanager
def _console_into(lines: list[str]) -> Iterator[list[str]]:
    """What the pipeline prints, as lines of text instead of output on stdout.

    Every stage prints through the module global `cli.console`, which writes to
    stdout — under stdio, the protocol's own wire. Swapped for the length of one
    call and handed back, so a job's log is exactly what the CLI would have shown.
    """
    sink = _Lines(lines)
    saved = cli.console
    cli.console = Console(file=sink, force_terminal=False, no_color=True, soft_wrap=True)
    try:
        yield lines
    finally:
        cli.console = saved
        sink.close()


@contextmanager
def _nobody_to_ask() -> Iterator[None]:
    """Nobody is at an MCP call to answer a question, so nothing may wait for one.

    With stdin an empty stream, `cli._interactive()` is False and any prompt
    reads end-of-file at once. A disputed reference label is then withheld with
    `numbering_chosen_by: "default"` — the CLI's own answer when no terminal is
    attached — and the gate on settling it, which is a person shown the
    disagreement, is never reached by a host that may be a model.
    """
    saved = sys.stdin
    sys.stdin = io.StringIO()
    try:
        yield
    finally:
        sys.stdin = saved


def _last_said(lines: list[str], n: int = 4) -> str:
    """The last lines a stage printed — where the CLI says why it stopped."""
    return " ".join(line.strip() for line in [x for x in lines if x.strip()][-n:])


@dataclass
class _Job:
    """One audit this server process started."""

    case: Path
    manuscript: Path
    started: str
    state: str = "running"  # running | finished | failed
    ended: str | None = None
    error: str = ""
    log: list[str] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)


class _Jobs:
    """The audits this server process started — at most one running at a time.

    One, because what a run touches is process-global: `cli.console`, `ask`'s
    per-site model record and `sys.stdin`. Two audits sharing them would
    interleave their logs and name each other's models.
    """

    def __init__(self):
        self._lock = threading.Lock()  # held across refuse, prepare and launch
        self._running: _Job | None = None
        self._by_case: dict[Path, _Job] = {}

    def last(self, case: Path) -> _Job | None:
        return self._by_case.get(case)

    def refuse_running(self, case: Path) -> None:
        """A case whose audit is running has nothing on disk that belongs to it."""
        job = self._running
        if job is not None and job.case == case:
            raise ToolError(
                f"an audit of {case} is running now (started {job.started}) — what is on "
                "disk belongs to an earlier run or is half-written, so nothing is read from "
                "it until the audit ends. Call audit_status."
            )

    def start(self, prepare: Callable[[], tuple[Path, Path, dict]]) -> _Job:
        """Run `prepare` — every check that can refuse before anything is spent —
        then the pipeline on its own thread, and return without waiting for it."""
        with self._lock:
            if (running := self._running) is not None:
                raise ToolError(
                    f"an audit is already running in this server: {running.case} (started "
                    f"{running.started}). One audit runs at a time — follow it with "
                    "audit_status, and start this one when it has ended."
                )
            case, manuscript, kwargs = prepare()
            job = _Job(case=case, manuscript=manuscript, started=_now())
            self._running = self._by_case[case] = job
            # daemon: when the host stops the server, the audit stops with it
            # rather than spending on for a client that has gone
            threading.Thread(
                target=self._run, args=(job, kwargs), name="papertrace-audit", daemon=True
            ).start()
            return job

    def _run(self, job: _Job, kwargs: dict) -> None:
        state, error = "failed", ""
        try:
            with _console_into(job.log), _nobody_to_ask():
                # the process outlives one audit: a model an earlier audit
                # recorded must not be reported as this one's judge
                forget_models()
                cli._run_pipeline(**kwargs)
            state = "finished"
        except typer.Exit as e:
            if e.exit_code == 0:
                state = "finished"
            else:
                error = _last_said(job.log) or f"the pipeline stopped (exit {e.exit_code})"
        except Exception as e:
            # Broad on purpose, and recorded rather than swallowed: an exception
            # leaving a tool reaches a model as a bare "Error executing tool", and
            # one leaving this thread would leave the job "running" forever. The
            # type and message are the finding, so the job keeps both.
            error = f"{type(e).__name__}: {str(e)[:400]}"
        finally:
            if state == "failed" and not error:
                error = "the audit was interrupted before it could finish"
            job.state, job.error, job.ended = state, error, _now()
            with self._lock:
                if self._running is job:
                    self._running = None
            job.done.set()  # last: whoever wakes on it sees the final state


_NEXT = {
    "running": "call audit_status again, with wait_seconds up to 50, until the audit has ended",
    "finished": "call audit_summary for the result — its counts travel with the caveats that "
                "qualify them",
    "failed": "read `error` and the log, fix what they name, and call start_audit again",
    "not_started": "no audit of this case was started by this server process — audit_summary "
                   "reads whatever an earlier run left on disk",
}


def _status(case: Path, job: _Job | None, log_lines: int) -> AuditStatus:
    if job is None:
        return {
            "case": str(case), "state": "not_started", "manuscript": None, "started": None,
            "ended": None, "error": "", "log": [], "log_lines_total": 0,
            "next": _NEXT["not_started"],
        }
    log = list(job.log)
    return {
        "case": str(job.case),
        "state": job.state,
        "manuscript": str(job.manuscript),
        "started": job.started,
        "ended": job.ended,
        "error": job.error,
        "log": log[-log_lines:] if log_lines else [],
        "log_lines_total": len(log),
        "next": _NEXT[job.state],
    }


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------


def build_server() -> MCPServer:
    """A fresh server with every tool registered. Nothing is served until `run()`."""
    server = MCPServer("papertrace", instructions=INSTRUCTIONS, version=__version__)
    jobs = _Jobs()

    def _open(case: str) -> Path:
        """A case folder to read — unless this server is writing it right now."""
        root = _case_dir(case)
        jobs.refuse_running(root)
        return root

    @server.tool(title="Audit summary", annotations=_READ, structured_output=True)
    def audit_summary(case: CaseArg) -> AuditSummary:
        """The result of one audit: verdict counts, references obtained, the model that
        judged, coverage, and every run-level disclosure — relay those with the counts.
        `limited` is set when the audit was cut short on request."""
        root = _open(case)
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
        root = _open(case)
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
        root = _open(case)
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
        root = _open(case)
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
        root = _open(case)
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
        root = _open(case)
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
        root = _open(case)
        path = root / "out" / "scout.json"
        if not path.is_file():
            raise ToolError(
                f"no literature scan at {path} — the audit ran without the scout, or the "
                "scout stage has not run (`papertrace scout -c <case>`)"
            )
        scout = _read(path, ScoutResults.from_json, "scout")
        return {"case": str(root), **scout.to_dict(), "caveat": _scout_caveat(scout)}

    @server.tool(title="Start an audit", annotations=_SPEND, structured_output=True)
    def start_audit(
        manuscript: Annotated[str, Field(description="The paper to audit, a PDF — by absolute path.")],
        case: Annotated[str | None, Field(description=(
            "Case folder to write the audit into. Default: a folder named after the paper, "
            "beside it; an earlier audit of the same paper there is amended."))] = None,
        email: Annotated[str | None, Field(description=(
            "Contact email Unpaywall requires. Default: PAPERTRACE_EMAIL, or the address "
            "`papertrace` saved."))] = None,
        model: Annotated[str | None, Field(description=(
            "Model for `claude -p`, e.g. claude-opus-5. Pin one for a reproducible audit; "
            "default: the account's default model."))] = None,
        backend: Annotated[Backend, Field(description=(
            "How PDFs are read: docling is layout-aware (tables stay tables), pymupdf is flat "
            "text; auto prefers docling."))] = "auto",
        scout: Annotated[bool, Field(description=(
            "Also scan Europe PMC for literature published since, or uncited."))] = True,
        doi: Annotated[str | None, Field(description=(
            "DOI of the paper itself. Default: the one printed on page 1."))] = None,
        formats: Annotated[list[Format] | None, Field(description=(
            "Report looks to write beside report.md; `viewer` is the interactive page."))] = None,
        provided: Annotated[str | None, Field(description=(
            "Folder of cited PDFs already at hand; each is identified by its own DOI or "
            "title."))] = None,
        supplements: Annotated[list[str] | None, Field(description=(
            "The paper's own supplementary PDFs, by absolute path."))] = None,
        llm_refs: Annotated[bool, Field(description=(
            "Also have a model read the printed reference list as a further check on its "
            "numbering (one more model call)."))] = True,
        max_claims: Annotated[int | None, Field(ge=1, description=(
            "Check only the first N claims; only the references they cite are fetched. The "
            "result says what was left out."))] = None,
        max_sources: Annotated[int | None, Field(ge=1, description=(
            "Obtain and judge against at most N cited sources. The result says what was left "
            "out."))] = None,
    ) -> AuditStatus:
        """Start a full audit of one paper and return at once; follow it with audit_status.

        The pipeline `papertrace run` runs: read the PDF, extract its citation-backed
        claims, fetch the cited sources through legal open-access routes, scout the
        literature, judge each claim against its cited pages, crop the evidence and write
        the reports. It SPENDS: `claude -p` model calls on this machine's logged-in Claude
        account (one to extract, one to read the reference list, one per cited source
        judged, each retried on failure), and queries to Crossref, Unpaywall, Europe PMC
        and arXiv. It takes minutes. One audit runs at a time per server.

        A reference label whose readings disagree is withheld, never settled here: that
        needs a person shown the disagreement, at a terminal (`papertrace refs`)."""
        pdf = _case_dir(manuscript)
        if not pdf.is_file():
            raise ToolError(f"no manuscript at {pdf} — pass the paper's PDF by absolute path")
        own = [_case_dir(s) for s in supplements or []]
        if absent := [str(s) for s in own if not s.is_file()]:
            raise ToolError(f"no supplement at {', '.join(absent)}")
        pdfs = _case_dir(provided) if provided else None
        if pdfs is not None and not pdfs.is_dir():
            raise ToolError(f"`provided` must be a folder of PDFs, and {pdfs} is not one")

        def prepare() -> tuple[Path, Path, dict]:
            # everything that can refuse, refused before a model or a network is touched
            from .check import claude_available

            if not claude_available():
                raise ToolError(
                    "the `claude` CLI is not on this server's PATH, and every claim is "
                    "judged with `claude -p` — install Claude Code and log in. If it is "
                    "installed, the host started this server with a PATH that does not "
                    "reach it: set PATH in the server's configuration."
                )
            said: list[str] = []
            try:
                with _console_into(said):
                    root = (_case_dir(case) if case else cli.default_case(pdf)).resolve()
                    address = cli._email(email)
                    cli._guard_case(root, pdf)  # one case folder per paper
            except typer.Exit:
                raise ToolError(
                    "refused before anything was spent — " + _last_said(said, n=8)
                ) from None
            return root, pdf, {
                "manuscript": pdf, "case": root, "provided": pdfs, "email": address,
                "model": model, "png": False, "backend": backend, "with_scout": scout,
                "doi": doi, "formats": list(formats or []), "supplement": own,
                "llm_refs": llm_refs, "max_claims": max_claims, "max_sources": max_sources,
            }

        job = jobs.start(prepare)
        return _status(job.case, job, log_lines=40)

    @server.tool(title="Audit status", annotations=_READ, structured_output=True)
    def audit_status(
        case: CaseArg,
        wait_seconds: Annotated[int, Field(ge=0, le=MAX_WAIT_SECONDS, description=(
            "Wait up to this many seconds for a running audit to end before answering "
            "(at most 50, inside the 60 s a host allows a request)."))] = 0,
        log_lines: Annotated[int, Field(ge=0, le=1000, description=(
            "How many of the audit's latest console lines to include."))] = 40,
    ) -> AuditStatus:
        """Where an audit this server started stands — running, finished or failed — with
        the last lines it printed, which carry its warnings as it goes. Poll with
        wait_seconds rather than in a tight loop. A finished audit's result is
        audit_summary; this never repeats its counts without their caveats."""
        root = _case_dir(case)
        job = jobs.last(root)
        if job is not None and wait_seconds:
            job.done.wait(wait_seconds)
        return _status(root, job, log_lines)

    return server


def serve() -> None:
    """Serve over stdio until the host closes the connection.

    stdio only: a case folder stays on the machine that made it, and the host
    that launched this process is the one client it answers.
    """
    build_server().run("stdio")
