"""Batch fact-checking via headless Claude Code (`claude -p`).

No API key needed — the user's existing Claude Code login is inherited. Two
stages: extract citation-backed claims from the manuscript, then judge each
claim against the ingested text of its cited source, one call per source so
context stays small. Claims whose source was never retrieved are marked
`not_retrieved` locally — no model call, no guessing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    _LABEL_GROUP,
    JUDGMENT_VERDICTS,
    PIPELINE_STATES,
    ClaimResult,
    RefManifest,
    SourceJudgement,
    UncitedClaim,
    _expand_label_group,
    citation_labels,
    is_references_heading,
)

CLAUDE_TIMEOUT = 600

# `claude -p` fails transiently, so a judging call gets one retry. Named here
# because the wizard quotes a worst-case cost and the two must not drift:
# a promised ceiling that the retry can exceed is a false promise about money.
ASK_ATTEMPTS = 2

# model actually used by the last `claude -p` call, when the CLI reports it —
# stamped into results.json so the report discloses its judge
_LAST_MODEL: str | None = None


def last_model() -> str | None:
    return _LAST_MODEL


# Text past these limits is never sent to the model, so it is never checked.
# A cut is recorded and disclosed in the report rather than passing silently.
MANUSCRIPT_CHAR_LIMIT = 180_000
SOURCE_CHAR_LIMIT = 150_000
# the citation inventory sent with the extraction prompt. A cut here is not
# cosmetic: contexts past it are never offered to the model, so nothing can be
# attributed to them and they can only ever come back `uncovered`. Disclosed
# like every other cut rather than passing quietly.
CONTEXT_CHAR_LIMIT = 60_000

@dataclass
class Truncations:
    """Which inputs the character limits cut short, for ONE run.

    Per-run state, deliberately not a module global: two runs in one process
    must not be able to see each other's cuts, and a run that truncated
    nothing must not be able to inherit a previous run's disclosure.
    """

    cuts: dict[str, dict] = field(default_factory=dict)

    def record(self, what: str, chars: int, limit: int) -> None:
        self.cuts[what] = {"chars": chars, "limit": limit}

    def report(self) -> dict:
        return {k: dict(v) for k, v in self.cuts.items()}


def _clip(text: str, limit: int, what: str, truncations: Truncations | None) -> str:
    # the accumulator is a REQUIRED positional: no future call site can omit it
    # and silently drop the disclosure. None records nothing — no global fallback.
    if len(text) <= limit:
        return text
    if truncations is not None:
        truncations.record(what, len(text), limit)
    return text[:limit]

EXTRACT_PROMPT = """You are the claim-extraction step of a peer-review fact-checker.

Below is a manuscript converted to markdown with provenance markers
(`<!-- block_NNNN, page N -->`).

CITATION CONTEXTS below is the complete list of places this manuscript makes a
citation, found mechanically. Each line is `ctx_NNNN`, its page, its section,
the label(s) cited there, and the sentence.

Task 1 — CITED claims: extract EVERY claim that carries a citation marker,
working through the CITATION CONTEXTS list. Completeness over selectivity:
each context must appear in at least one extracted claim. This includes
numerical results, "X showed Y", methodological attributions, guideline
statements, prevalence claims — and claims made inside TABLES.

Every cited claim carries `ctx`: the ids of the contexts it was taken from,
copied exactly from the list. One sentence citing [2] and [3] is ONE claim
carrying BOTH ids, not two claims. Never invent an id and never guess: if you
cannot tell which context a claim came from, return `"ctx": []` and it will be
reported as unplaced rather than attributed to the wrong sentence.

Task 2 — UNCITED assertions: list assertive factual statements that carry NO
citation but would normally need one (numbers, prevalence, mechanisms,
standard-of-care statements). Exclude the manuscript's own results and
methods descriptions of what the authors themselves did.

Rules for both:
- quote: the manuscript's own sentence carrying the claim, copied VERBATIM,
  ≤500 chars. Copy it exactly as written — do not tidy, shorten or rephrase it,
  and keep the numbers, units, intervals and hedging words as they appear. If
  the claim spans two sentences, quote both. Strip nothing except the citation
  marker itself. This is the text that will be checked against the source.
- claim: the same statement tightly paraphrased for a headline, ≤300 chars.
- location: manuscript section (e.g. "Introduction ¶2", "Methods", "Table 2").
- cited claims also carry refs: citation labels as strings, e.g. ["3"] or ["7","8"].
- own_supplement: true when the claim points at THIS paper's own supplementary
  material — "Table S3", "eFigure 2", "Supplementary Methods", "Appendix A".
  That is a pointer, not a citation, so it does NOT go in refs. A claim can
  carry both: "as in [4] and Table S2" cites [4] and sets own_supplement.
  Reserve it for the paper's own numbering; "the supplement of [4]" is just [4].
  A claim whose ONLY support is such a pointer still belongs in `cited`, with
  an empty refs list — it is not an assertion made without evidence.
- Number each list from 1 in reading order.

Answer with ONLY a JSON object, no prose, no code fences:
{"cited":[{"id":1,"ctx":["ctx_0001"],"quote":"...","claim":"...","location":"...","refs":["1"],"own_supplement":false}],
 "uncited":[{"id":1,"quote":"...","claim":"...","location":"..."}]}

CITATION CONTEXTS:
<<CONTEXTS>>

MANUSCRIPT:
"""

CHECK_PROMPT = """You are the verification step of a peer-review fact-checker.

Judge each CLAIM below strictly against the SOURCE text (a cited paper,
converted to markdown with `<!-- block_NNNN, page N -->` markers). The source
text is the only evidence — never use outside knowledge of the paper.

A claim may cite several sources. You are shown ONE of them. Judge only what
THIS source does or does not say, and do not speculate about the others: each
is judged in its own call and the results are combined afterwards.

WHAT YOU ARE HOLDING: <<DOCKIND>>

Each claim carries `quote`, the manuscript's own sentence, and `claim`, a short
paraphrase of it. **Judge the quote.** It holds the population, the effect
size, the interval and the hedging that decide whether the source supports the
statement; the paraphrase is a label and may have dropped any of them. Where
the two differ, the quote is the claim. A claim with an empty `quote` is all
there is for it — judge the paraphrase, and let the missing scope count against
"supported" rather than for it.

For each claim output:
- verdict: "supported" (source states it), "partial" (kernel true but scope,
  strength or object differs — say what differs), "contradicted" (source says
  otherwise — quote its actual figure), or "not_addressed" (this source simply
  does not speak to the claim).
- note: ≤2 sentences, the why.
- source_page: page of the decisive passage (integer).
- source_block: its block id, e.g. "block_0042".
- anchor_phrases: 1–3 short VERBATIM strings copied from that block that a
  text search will find (numbers and distinctive wording; unique within the
  block).

Use "not_addressed" when the source is about something else, or covers the
topic but never states the specific fact claimed. It is a normal, useful
answer, not a failure — a citation that does not support what it is cited for
is exactly what this review is looking for. Do NOT reach for "contradicted"
(which asserts the source says otherwise) or "partial" (which asserts a true
kernel) to describe silence. For "not_addressed", omit source_page,
source_block and anchor_phrases: there is no passage to point at.

Answer with ONLY a JSON array, no prose, no code fences:
[{"id":3,"verdict":"partial","note":"...","source_page":5,
  "source_block":"block_0042","anchor_phrases":["p < 0.001"]},
 {"id":4,"verdict":"not_addressed","note":"Reports incidence only; says
  nothing about mortality."}, ...]

CLAIMS:
<<CLAIMS>>

SOURCE (<<SLUG>>):
<<SOURCE>>
"""


def claude_available() -> bool:
    return shutil.which("claude") is not None


_SCRATCH_CWD: str | None = None


def _scratch_cwd() -> str:
    # /tmp itself is shared and world-writable; a private 0700 directory (one per
    # process, reused across calls) keeps another local user from planting
    # anything the judging call would walk into
    global _SCRATCH_CWD
    if _SCRATCH_CWD is None:
        _SCRATCH_CWD = tempfile.mkdtemp(prefix="papertrace-ask-")
    return _SCRATCH_CWD


def _ask(prompt: str, model: str | None = None) -> str:
    # judging happens wherever the user ran papertrace from — never that repo's own
    # CLAUDE.md, and never with more than the ability to read the prompt and answer
    cmd = ["claude", "-p", "--output-format", "json", "--safe-mode", "--tools", ""]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=_scratch_cwd(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude -p timed out after {CLAUDE_TIMEOUT}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed: {proc.stderr.strip()[:400]}")
    payload = json.loads(proc.stdout)
    global _LAST_MODEL
    usage = payload.get("modelUsage")
    _LAST_MODEL = (
        payload.get("model")
        or (next(iter(usage), None) if isinstance(usage, dict) else None)
        or _LAST_MODEL
    )
    return payload.get("result", "")


def _parse_json_array(text: str) -> list[dict]:
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON array in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def _parse_json_object(text: str) -> dict:
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def _ctx_labels(claim: dict) -> list[str]:
    """The `ctx` values one returned claim carries, however it phrased them.

    A list is the contract, but a model that has exactly one context sometimes
    sends the bare string. Accepting both costs one branch; rejecting the
    string form would discard a correct answer over its punctuation.
    """
    raw = claim.get("ctx")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _render_inventory(occurrences: list[dict]) -> tuple[str, dict[str, str]]:
    """The citation inventory as the model sees it, and the map back.

    Returns the `ctx_NNNN` block for the prompt and `{ctx label -> occurrence
    id}`. Both come out of **one pass** over the same list, on purpose: the
    caller resolves the model's answer through this map rather than re-deriving
    the pairing from position later. Re-deriving it would be reading-order
    zipping wearing a different hat — one dropped occurrence and every id after
    it points at the wrong sentence, silently.

    Internal occurrence ids (`block_0012:345:7`) are deliberately not shown.
    They are long, punctuated, and a model asked to copy one exactly will
    sometimes not; `ctx_0007` it copies.
    """
    lines: list[str] = []
    mapping: dict[str, str] = {}
    for n, o in enumerate(occurrences, start=1):
        ctx = f"ctx_{n:04d}"
        mapping[ctx] = o["id"]
        where = f"p{o['page']}" if o.get("page") else "p?"
        section = f" §{o['section']}" if o.get("section") else ""
        lines.append(f"{ctx}  {where}{section}  [{o.get('group') or o['label']}]  {o['sentence']}")
    return "\n".join(lines), mapping


def extract_claims(
    case_dir: Path,
    model: str | None = None,
    *,
    truncations: Truncations | None = None,
) -> tuple[list[ClaimResult], list[UncitedClaim]]:
    annotated = case_dir / "ingest" / "manuscript" / "annotated.md"
    text = _clip(annotated.read_text(), MANUSCRIPT_CHAR_LIMIT, "manuscript", truncations)
    # the inventory is built BEFORE the call, from source_map.json, and handed
    # to the model — rather than built afterwards and matched back against a
    # paraphrase. This is the whole redesign.
    occurrences, _source = citation_occurrences(case_dir)
    inventory, ctx_map = _render_inventory(occurrences)
    prompt = EXTRACT_PROMPT.replace(
        "<<CONTEXTS>>",
        _clip(inventory or "(none found)", CONTEXT_CHAR_LIMIT, "citation contexts", truncations),
    )
    raw = _ask(prompt + text, model)
    data = _parse_json_object(raw)
    cited = [
        ClaimResult(
            id=int(c["id"]),
            claim=str(c["claim"]),
            # `.get`, and NOT falling back to `claim`: a missing quote means the
            # model did not give one, and copying the paraphrase in would put
            # the compression back while looking like it had been removed
            quote=str(c.get("quote", "")),
            location=str(c.get("location", "")),
            # resolved here, where the map is in hand. An id that is not in the
            # inventory is DROPPED, never repaired into "the first occurrence of
            # that label" — coverage reports the claim as unplaced instead, and
            # the label's occurrences as uncertain.
            ctx_ids=[ctx_map[k] for k in _ctx_labels(c) if k in ctx_map],
            refs=[str(r) for r in c.get("refs", [])],
            own_supplement=bool(c.get("own_supplement", False)),
        )
        for c in data.get("cited", [])
    ]
    uncited = [
        UncitedClaim(
            id=int(u["id"]),
            claim=str(u["claim"]),
            quote=str(u.get("quote", "")),
            location=str(u.get("location", "")),
        )
        for u in data.get("uncited", [])
    ]
    return cited, uncited


# ---------------------------------------------------------------------------
# deterministic citation-label coverage audit
# ---------------------------------------------------------------------------

def _body_before_references(clean_md: str) -> str:
    """`clean_md` up to the line where the bibliography begins.

    Uses `models.is_references_heading` — the rule `refs` and the occurrence
    walk already share — instead of a second regex of its own. That regex
    required a markdown `##`, which needs ingest to have *typed* the block as a
    heading; flat-text ingest guesses headings from font size, so a `References`
    line at body size reaches `clean.md` as plain text. The shared rule is built
    for exactly that case and says True where this cut said False, and the two
    disagreeing is how every `[N]` printed in the reference list came to be
    counted as a body citation — reporting gaps that do not exist, in the one
    figure the audit computes mechanically so that it cannot.

    A markdown-marked line is read as a heading, so `## References and further
    reading` cuts. A plain line must be the word and nothing else, which is what
    keeps `References were checked by hand [1].` from swallowing the paper.
    """
    out: list[str] = []
    for line in clean_md.splitlines(keepends=True):
        marked = line.lstrip().startswith("#")
        if is_references_heading("sectionheader" if marked else "text", line):
            break
        out.append(line)
    return "".join(out)


_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _strip_table_rows(text: str) -> str:
    """Blank out GitHub-flavoured-markdown table rows.

    `clean.md` is flat text with no block-type information, so a table can only
    be recognised by its own linearized shape (`| ... |`, one row per line \u2014 see
    `Block` in `models.py`). A results table's own numbers are not citations: a
    95% CI column like `[51, 77]` matches the same bracket-and-comma syntax as a
    citation group `[7,8]`, and a live audit read a table's CI columns as
    citations to references that did not exist at that number.
    """
    return "\n".join("" if _TABLE_ROW.match(line) else line for line in text.splitlines())


def citation_labels_in_text(clean_md: str) -> set[str]:
    """Every citation label appearing in the body text (References section excluded).

    The label rule itself lives in `models.citation_labels` \u2014 `refs.py` needs the
    same reading to reconcile the reference list against what the manuscript
    cites, and two independent readings of one fact are only evidence when they
    come from one rule. This wrapper owns the one thing that is local to
    coverage: stopping at the bibliography, so its own `[N]` markers are not
    counted as body citations, and skipping table rows, whose own numbers are not
    citations either.
    """
    return citation_labels(_strip_table_rows(_body_before_references(clean_md)))


# ---------------------------------------------------------------------------
# occurrence-level coverage
#
# Label-level coverage was pure set arithmetic — mechanical and incapable of a
# false positive, but blind: two sentences citing [3] with one extracted claim
# reported [3] as covered and left the other sentence invisible. Occurrences fix
# the blindness.
#
# Occurrences first bought a new failure mode with it: the inventory was built
# AFTER the model call, so a paraphrase had to be matched back to a sentence by
# text similarity, and the *pointer* could be wrong while the counts were right.
# That is gone. The inventory is now built first and handed to the extractor as
# `ctx_NNNN`, and attribution is a lookup of the ids it returned.
#
# `uncertain` stays, for the one case that can still produce doubt: a claim
# cites a label and names none of that label's contexts, so a claim did reach
# one of them and nothing can say which.
# ---------------------------------------------------------------------------

_EXCERPT_RADIUS = 120

# structural, not textual: the source map says a block IS a section header, so
# the References cut no longer depends on ingest happening to emit `## `
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _excerpt(text: str, start: int, end: int) -> str:
    """The sentence carrying the marker, clipped to ±120 chars around it.

    The excerpt is what turns an uncovered occurrence from a bare number into
    something a reader can act on, and it is also what the extractor is shown
    in the `ctx_NNNN` inventory — so it is the sentence, not an arbitrary
    window. A window that cut mid-clause would ask the model to place a claim
    against half of the sentence it came from.
    """
    begin = 0
    for m in _SENTENCE_END.finditer(text, 0, start):
        begin = m.end()
    tail = _SENTENCE_END.search(text, end)
    stop = tail.start() if tail else len(text)
    lo, hi = max(begin, start - _EXCERPT_RADIUS), min(stop, end + _EXCERPT_RADIUS)
    snippet = " ".join(text[lo:hi].split())
    return f"{'…' if lo > begin else ''}{snippet}{'…' if hi < stop else ''}"


def _occurrences_in(text: str, *, block: str | None, page: int | None, section: str) -> list[dict]:
    """Every citation occurrence in one block (or in the whole body text).

    A group `[7,8]` yields two occurrences sharing block and offset, distinct
    by label: two citations were made in one place, and only one of them may
    ever be reached by a claim.
    """
    out: list[dict] = []
    for m in _LABEL_GROUP.finditer(text):
        sentence = _excerpt(text, m.start(), m.end())
        for label in sorted(_expand_label_group(m.group(1)), key=int):
            out.append({
                "id": f"{block or 'clean'}:{m.start()}:{label}",
                "label": label,
                "block": block,
                "page": page,
                "offset": m.start(),
                "group": m.group(1),
                "section": section,
                "sentence": sentence,
            })
    return out


def citation_occurrences(case_dir: Path) -> tuple[list[dict], str]:
    """Every place in the manuscript body that makes a citation, in reading order.

    Reads `source_map.json`, never `annotated.md`: the annotated copy injects
    `<!-- block_NNNN, page N -->` markers inline and shifts every offset. The
    source map also supplies block id, page and heading path for free, and
    makes the References exclusion structural — stop at the first section
    header that names a reference list.

    `clean.md` is the fallback, because a case folder can legitimately carry no
    source map. It costs the page and the block; the label-level keys are
    identical either way.
    """
    manuscript = case_dir / "ingest" / "manuscript"
    smap = manuscript / "source_map.json"
    if smap.exists():
        from .models import SourceMap

        out: list[dict] = []
        for b in SourceMap.from_json(smap).blocks:
            # the SAME rule `references_section` uses — see models.is_references_heading
            if is_references_heading(b.type, b.text or ""):
                break
            if b.type == "table":
                # a table's own numbers are not citations — see _strip_table_rows
                continue
            out += _occurrences_in(
                b.text or "",
                block=b.id,
                page=b.page,
                section=(b.heading_path[-1] if b.heading_path else ""),
            )
        return out, "source_map"

    clean = manuscript / "clean.md"
    if clean.exists():
        # the same cut as the label reading above: a fallback that counted
        # reference-list markers as occurrences would inflate the denominator of
        # the coverage ratio, not just the label set
        body = _strip_table_rows(_body_before_references(clean.read_text()))
        return _occurrences_in(body, block=None, page=None, section=""), "clean.md"
    return [], "none"


def attribute_occurrences(
    occurrences: list[dict], claims: list[ClaimResult]
) -> tuple[list[dict], list[dict]]:
    """Stamp a `status` and a `claim_id` on every occurrence.

    Bookkeeping, not matching: an occurrence is `covered` when some claim's
    `ctx_ids` names it. The extractor was shown this exact inventory and told
    which ids to copy, so nothing here has to work out where a paraphrase came
    from.

    `uncertain` survives, for the one case that can still produce doubt: a
    claim cites the label but named no usable context for it, so a claim *did*
    reach one of these places and nothing can say which. Calling those covered
    restores the overstatement occurrences exist to remove; calling them
    uncovered cries wolf about a citation that was in fact read. It is never
    counted as covered.

    A claim naming a context that is not in the inventory lands here too, and
    on purpose — a hallucinated `ctx_9999` and an honest `"ctx": []` are the
    same amount of information about which sentence was meant.
    """
    reached: dict[str, int] = {}
    for c in claims:
        for cid in c.ctx_ids:
            reached.setdefault(cid, c.id)

    by_label: dict[str, list[dict]] = {}
    for o in occurrences:
        by_label.setdefault(o["label"], []).append(o)

    status: dict[str, tuple[str, int | None]] = {}
    unattributed: list[dict] = []
    for label, occs in by_label.items():
        here = {o["id"] for o in occs}
        # a claim is unplaced *for this label* when it cites the label and
        # named none of the label's own contexts — including when it named a
        # context belonging to some other label
        unplaced = [c.id for c in claims if label in c.refs and not (set(c.ctx_ids) & here)]
        rest = "uncertain" if unplaced else "uncovered"
        for o in occs:
            claim_id = reached.get(o["id"])
            status[o["id"]] = ("covered", claim_id) if claim_id is not None else (rest, None)
        unattributed += [{"claim_id": cid, "label": label} for cid in unplaced]

    items = [{**o, "status": status[o["id"]][0], "claim_id": status[o["id"]][1]}
             for o in occurrences]
    unattributed.sort(key=lambda u: (u["claim_id"], int(u["label"])))
    return items, unattributed


def coverage_audit(case_dir: Path, claims: list[ClaimResult]) -> dict:
    """Compare citations present in the manuscript against what claims reached.

    Mechanical and prompt-independent: if extraction skipped a citation, it
    shows up here and lands in the report.

    `labels_in_text`, `covered` and `missing` keep their label-level meaning
    byte for byte. They are the published contract — `evals/align.py` reads
    `missing` as a list of label strings to decide whether an unmatched gold
    case is the tool's failure or the evaluator's, and reshaping it would move
    that blame silently. Everything occurrence-level is additive. In
    particular `covered` is NOT redefined as "labels with ≥1 covered
    occurrence": that would push an all-uncertain label into `missing`.
    """
    clean = case_dir / "ingest" / "manuscript" / "clean.md"
    in_text = citation_labels_in_text(clean.read_text()) if clean.exists() else set()
    covered = {r for c in claims for r in c.refs}
    missing = sorted(in_text - covered, key=int)

    occurrences, source = citation_occurrences(case_dir)
    items, unattributed = attribute_occurrences(occurrences, claims)
    counts = {s: sum(1 for o in items if o["status"] == s)
              for s in ("covered", "uncovered", "uncertain")}

    seen: dict[str, set[str]] = {}
    for o in items:
        seen.setdefault(o["label"], set()).add(o["status"])
    partial = sorted((lab for lab, st in seen.items() if "covered" in st and st != {"covered"}),
                     key=int)
    uncertain_only = sorted((lab for lab, st in seen.items() if st == {"uncertain"}), key=int)

    return {
        "labels_in_text": sorted(in_text, key=int),
        "covered": sorted(covered & in_text, key=int),
        "missing": missing,
        "schema": "coverage/3",
        "unit": "occurrence",
        "source": source,
        "labels_partially_covered": partial,
        "labels_uncertain_only": uncertain_only,
        "occurrences": {"total": len(items), **counts, "items": items},
        "attribution": {
            # no thresholds to report any more: the extractor was shown this
            # inventory and returned the ids it used, so there is nothing to
            # tune and no close call to refuse
            "method": "context id returned by extraction, resolved against the inventory",
            "claims_unattributed": unattributed,
        },
    }


def _stale_ingest(ingest_dir: Path, pdf_path: str | None, *, backend: str) -> bool:
    """Must `ingest_dir` be rebuilt — wrong PDF, or read by the wrong backend?

    The directory is named after the reference's slug, and a slug is not an
    identity that holds still. Fixing a slug collision renames one of the two
    colliding entries, and the reconciler can hand `refs` the publisher's list
    on one run and the parsed list on the next — so re-running an existing case
    could hand the model the directory's previous occupant and judge a claim,
    confidently, against a different paper. `SourceMap.doc` cannot catch it:
    every cited source is stored as `<slug>.pdf`, so it reads the same either
    way.

    An unhashed map — written before source maps recorded what they read — is
    treated as stale. Re-ingesting is local, free and quick; trusting it is a
    guess about which paper is in a file, and that guess is the whole thing this
    module refuses to make.

    The **converter** is checked for the same reason, and it is not the same
    question as the hash: a case folder built before sources were read
    layout-aware holds `pymupdf` maps of exactly the right PDFs. Reusing one
    under `--backend docling` would hand the judge a linearized table while the
    run reports layout-aware source ingest — the fidelity claim would be true
    of the paper and false of the papers it is judged against.
    """
    if not pdf_path or not Path(pdf_path).exists():
        return False  # nothing better to ingest; SourceProvenance reports the gap
    smap_path = ingest_dir / "source_map.json"
    if not smap_path.exists():
        return True
    try:
        from .models import SourceMap

        smap = SourceMap.from_json(smap_path)
        recorded, converter = smap.source_sha256, smap.converter
    except (OSError, ValueError, KeyError, TypeError):
        return True
    from .ingest import resolve_backend
    from .models import manuscript_fingerprint

    if recorded != manuscript_fingerprint(Path(pdf_path)):
        return True
    # "auto" is not a converter name, and a docling map records its version
    # ("docling 2.53.0"), so compare the resolved backend against the first word
    return resolve_backend(backend) != converter.split()[0]


def _slug_for_ref(manifest: RefManifest, label: str):
    return next((e for e in manifest.entries if e.num == label), None)


_MAX_PAGE_DIGITS = 5  # a page number, not an integer literal


@dataclass(frozen=True)
class SourceProvenance:
    """What one source actually contains, read from its own source map.

    The yardstick a judgement is held to. Without it a verdict's page and block
    are the model's unchecked word for it, which is how `page 99999` and
    `block_nope` survived into a report as provenance.
    """

    pages: int
    block_pages: dict[str, int]  # block id -> the page it is on

    @classmethod
    def from_map(cls, smap) -> SourceProvenance:
        return cls(pages=smap.pages, block_pages={b.id: b.page for b in smap.blocks})

    @classmethod
    def read(cls, source_map: Path) -> SourceProvenance | None:
        """None when the map is missing or unreadable — never a permissive default.

        A guessed yardstick measures nothing. The caller turns None into
        `unchecked`, so an unverifiable location is refused rather than trusted.
        """
        if not source_map.exists():
            return None
        try:
            from .models import SourceMap

            return cls.from_map(SourceMap.from_json(source_map))
        except (OSError, ValueError, KeyError, TypeError):
            return None


def _judgement_from(entry, provenance: SourceProvenance | None) -> tuple[dict | None, str]:
    """Validate one model response object into claim fields, or say why not.

    Total by construction: every branch is an isinstance test or a lookup, so
    this cannot raise. A validator that throws would turn a bug in OUR code into
    a note blaming the model — the same laundering `unchecked` exists to prevent.

    Rejection is all-or-nothing. The caller writes no field unless every field
    validated, so a bad response never leaves half-applied provenance behind.

    `provenance` is the source's own source map. A substantive verdict must
    name a page that exists and a block that exists **on that page**, because
    the block is what guarantees the reader an evidence image: `crop_for_anchor`
    takes its region from the block's bbox, so a valid block always produces a
    crop and the anchor phrases only decide whether a red box is drawn on it.
    """
    if not isinstance(entry, dict):
        return None, f"model returned an unusable verdict (not an object: {type(entry).__name__})"

    verdict = entry.get("verdict")
    if verdict in PIPELINE_STATES:
        return None, (
            f"model returned an unusable verdict ({verdict!r}) — that is a "
            f"pipeline state only PaperTrace assigns, not a judgement"
        )
    if verdict not in JUDGMENT_VERDICTS:
        return None, f"model returned an unusable verdict ({verdict!r})"

    # `not_addressed` is the one verdict with no decisive passage to point at:
    # the source was read and says nothing about the claim, so there is no page
    # to show. Demanding one would force the model to invent a citation for an
    # absence — the exact fabrication this validator exists to stop.
    if verdict == "not_addressed":
        note = entry.get("note")
        return {
            "verdict": verdict,
            "note": note if isinstance(note, str) else "",
            "source_page": None,
            "source_block": None,
            "anchor_phrases": [],
        }, ""

    # source_page is REQUIRED for every other verdict: without it the report
    # prints a literal "Page None", crop_for_claim bails so no evidence exists,
    # and the reader cannot falsify it. bool is excluded first —
    # isinstance(True, int) is True.
    raw_page = entry.get("source_page")
    unusable_page = (
        f"model returned an unusable verdict for this claim: {verdict!r} with no "
        f"usable source_page ({raw_page!r}) — nothing to show, nothing to check"
    )
    if isinstance(raw_page, bool) or not isinstance(raw_page, (int, str)):
        return None, unusable_page
    if isinstance(raw_page, str):
        digits = raw_page.strip()
        # .isascii() carries weight: "\u00b2".isdigit() is True but int() raises on it
        if not (digits.isascii() and digits.isdigit()):
            return None, unusable_page
        # CPython refuses to convert more than 4300 digits, so isdigit() alone
        # left a ValueError escaping this function — which is documented total.
        # No PDF has a six-digit page, so the bound costs nothing real.
        if len(digits) > _MAX_PAGE_DIGITS:
            return None, unusable_page
        try:
            page = int(digits)
        except ValueError:  # narrow on purpose — a blanket catch here would
            return None, unusable_page  # relabel our own bugs as the model's
    else:
        page = raw_page
    if page < 1:
        return None, unusable_page  # highlight does doc[page - 1]

    # the source map is the only thing that can contradict the model here. With
    # no map nothing can, so nothing does — and a location nobody can check is
    # refused rather than trusted.
    if provenance is None:
        return None, (
            f"model returned {verdict!r} but the source map could not be read, so "
            f"the page and block it names cannot be checked against the source — "
            f"re-run `papertrace ingest` for this source, then `papertrace check`"
        )
    if page > provenance.pages:
        return None, (
            f"model returned {verdict!r} for a passage on page {page}, but the "
            f"source has {provenance.pages} page{'s' if provenance.pages != 1 else ''} "
            f"— there is no such page to show"
        )

    # REQUIRED, not optional: the block's bbox is what `crop_for_anchor` uses as
    # the crop region, so a judgement without one can leave the reader with no
    # evidence image at all — a verdict nobody can look at.
    block = entry.get("source_block")
    if not isinstance(block, str) or not block.strip():
        return None, (
            f"model returned {verdict!r} with no source_block ({block!r}) — without "
            f"one there is no region to crop, so the verdict would carry no evidence "
            f"image a reader could check"
        )
    block = block.strip()
    block_page = provenance.block_pages.get(block)
    if block_page is None:
        return None, (
            f"model returned {verdict!r} citing {block}, which is not a block of "
            f"this source — nothing to crop, nothing to check"
        )
    if block_page != page:
        return None, (
            f"model returned {verdict!r} citing {block}, which is on page "
            f"{block_page}, not the page {page} it named — a crop of page {page} "
            f"would show the reader a different passage"
        )

    # absent means "none offered" and is allowed, as is an empty list. null, a
    # dict or a number is a positive assertion of the wrong type — and null is
    # the value that used to take the whole check stage down with a TypeError.
    phrases = entry.get("anchor_phrases", [])
    if not isinstance(phrases, list) or not all(isinstance(p, str) for p in phrases):
        return None, (
            f"model returned an unusable verdict for this claim: {verdict!r} with "
            f"anchor_phrases that are not a list of strings ({phrases!r})"
        )

    note = entry.get("note")
    return {
        "verdict": verdict,
        "note": "" if note is None else str(note),
        "source_page": page,
        "source_block": block,
        "anchor_phrases": list(phrases),
    }, ""


# What the judge is actually reading. A supplement handed over unannounced gets
# treated as the article: `not_addressed` is the ordinary answer for an appendix
# that covers a different part of the work, and a judge with no reason to expect
# it reaches for `partial` instead and invents a true kernel.
_DOCKIND = {
    "article": "the cited article itself.",
    "supplement": (
        "supplementary material accompanying the cited article — an appendix, "
        "supporting information, or an online-only data supplement. It is part of "
        "the cited work, so what it states counts. But it covers only part of that "
        "work, so a claim it simply does not speak to is `not_addressed`, and that "
        "is the expected answer here far more often than for an article."
    ),
    "own_supplement": (
        "supplementary material belonging to the manuscript UNDER REVIEW, not to a "
        "cited work. The claim points at it — a table, figure or section number the "
        "paper names. Judge whether this document actually states what the paper "
        "says it does. `not_addressed` means the paper pointed here and the thing it "
        "pointed at is not here."
    ),
}


def check_claims(
    claims: list[ClaimResult],
    manifest: RefManifest,
    case_dir: Path,
    model: str | None = None,
    progress=None,
    on_error=None,
    *,
    truncations: Truncations | None = None,
    # REQUIRED, like `_clip`'s accumulator above: this decides whether a table
    # in a cited source is readable at all, and neither possible default is
    # honest. "auto" drags docling into an offline test run; "pymupdf" silently
    # downgrades a caller who asked for layout. So there is no default.
    backend: str,
) -> list[ClaimResult]:
    """Fill verdicts in place. One model call per source that carries claims.

    A failed check is NEVER disguised as a retrieval gap: if the source was
    available but the model call or source ingest failed (after one retry),
    the claims get verdict `unchecked`, the reason lands in the note, and
    `on_error(slug, message)` fires so the CLI can say so loudly.

    Sources are ingested with the SAME backend as the audited paper. They used
    to be read flat on the theory that text anchors are all a verdict needs,
    but the decisive evidence for a claim is often a table — a subgroup row, a
    confidence interval in a column — and a linearized table loses the
    relationships that make those readable. Spending layout fidelity on the
    paper and not on the papers it is judged against had the asymmetry
    backwards.
    """
    by_slug: dict[str, list[ClaimResult]] = {}
    for c in claims:
        pairs = [(r, _slug_for_ref(manifest, r)) for r in c.refs]
        avail = [(r, e) for r, e in pairs if e and e.status in ("retrieved", "provided") and e.slug]
        own = manifest.manuscript_supplements if c.own_supplement else []
        if not avail and not own:
            c.verdict = "not_retrieved"
            if c.own_supplement:
                # the paper said exactly where its evidence was and nobody
                # opened it. That is a retrieval gap, not an uncited assertion.
                c.note = (
                    "points at this paper's own supplementary material, which was not "
                    "provided — pass it with --supplement"
                )
            else:
                reasons = {e.status for _, e in pairs if e}
                c.note = (
                    f"cited source not available ({', '.join(sorted(reasons)) or 'unknown ref'})"
                )
            continue
        # Co-citation is an offer of support: every source cited for this claim
        # was put forward as backing it, so every one that could be obtained is
        # judged. One call per source, so each verdict rests on that source's
        # text alone and a long source cannot crowd out a short one.
        seen_slugs: set[str] = set()
        for r, e in avail:
            if e.slug in seen_slugs:  # the same paper cited under two labels
                continue
            seen_slugs.add(e.slug)
            c.judgements.append(SourceJudgement(source_slug=e.slug, ref=r, kind="article"))
            by_slug.setdefault(e.slug, []).append(c)
            # A supplement is part of the work that was cited, so it is read for
            # every claim citing that label rather than only when the article
            # turns out to be silent — a supplement contradicting a claim the
            # article supports is exactly the finding that would be missed.
            # Costs one extra call per supplement, not per claim: the loop below
            # groups every claim for a document into a single call.
            for s in e.supplements:
                if s.slug in seen_slugs:
                    continue
                seen_slugs.add(s.slug)
                c.judgements.append(
                    SourceJudgement(source_slug=s.slug, ref=r, kind="supplement",
                                    verified=s.verified)
                )
                by_slug.setdefault(s.slug, []).append(c)
        # the paper's own supplements answer for no citation label, so `ref` is
        # empty: filling in a number would say the claim cited something it did
        # not. Every one provided is read, matching the cited side and sparing
        # the extractor a guess about which file "S3" lives in.
        for s in own:
            if s.slug in seen_slugs:
                continue
            seen_slugs.add(s.slug)
            c.judgements.append(
                SourceJudgement(source_slug=s.slug, ref="", kind="own_supplement")
            )
            by_slug.setdefault(s.slug, []).append(c)
        # what is left here could NOT be obtained — the only remaining reason a
        # cited source goes unopened
        avail_refs = {r for r, _ in avail}
        c.unjudged_refs = [r for r in c.refs if r not in avail_refs]

    for slug, group in by_slug.items():
        try:
            ingest_dir = case_dir / "ingest" / slug
            annotated = ingest_dir / "annotated.md"
            # a missing source_map.json is NOT re-ingested here: `entry.pdf_path`
            # may be gone, and turning one absent artifact into a group-wide
            # FileNotFoundError buries the real problem. It degrades per
            # judgement instead, with a note naming the fix — see
            # SourceProvenance.read.
            doc = manifest.document(slug)
            if doc is None:  # pragma: no cover - every judged slug names a document
                raise KeyError(f"no document named {slug!r} in the manifest")
            if not annotated.exists() or _stale_ingest(
                ingest_dir, doc.pdf_path, backend=backend
            ):
                from .ingest import ingest_pdf

                ingest_pdf(Path(doc.pdf_path), ingest_dir, backend=backend)
            # the quote goes with the paraphrase, not instead of it: the judge
            # is told to rule on the quote, and the paraphrase stays so a claim
            # whose extraction returned no quote is still judgeable
            claims_json = json.dumps(
                [
                    {"id": c.id, "quote": c.quote, "claim": c.claim, "location": c.location}
                    for c in group
                ]
            )
            prompt = (
                CHECK_PROMPT.replace("<<CLAIMS>>", claims_json)
                .replace("<<DOCKIND>>", _DOCKIND[doc.kind])
                .replace("<<SLUG>>", slug)
                .replace(
                    "<<SOURCE>>",
                    _clip(annotated.read_text(), SOURCE_CHAR_LIMIT, f"source:{slug}", truncations),
                )
            )
            try:
                raw = _ask(prompt, model)
            except (RuntimeError, ValueError):
                raw = _ask(prompt, model)  # one retry — claude -p fails transiently
            verdicts = {v["id"]: v for v in _parse_json_array(raw)}
        except Exception as e:  # noqa: BLE001 — a failed check must never kill the run
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            for c in group:
                # only THIS source's judgement fails. Another co-cited source
                # may already have produced a real verdict, and discarding it
                # would report a gap that does not exist.
                j = next((x for x in c.judgements if x.source_slug == slug), None)
                if j is not None:
                    j.verdict = "unchecked"
                    j.note = (
                        f"check failed ({msg}) — the source WAS retrieved; "
                        f"re-run `papertrace check` to retry"
                    )
            if on_error:
                on_error(slug, msg)
            continue
        # deliberately NO per-claim `except Exception`: a blanket catch would
        # relabel our own bugs as the model's fault. The per-group except above
        # stays as scoped — ingest/prompt/_ask failures really are group-wide.
        provenance = SourceProvenance.read(case_dir / "ingest" / slug / "source_map.json")
        for c in group:
            j = next((x for x in c.judgements if x.source_slug == slug), None)
            if j is None:  # pragma: no cover - group membership implies one
                continue
            v = verdicts.get(c.id)
            if v is None:
                j.verdict, j.note = "unchecked", "model returned no verdict for this claim"
                continue
            fields, why = _judgement_from(v, provenance)
            if fields is None:
                j.verdict, j.note = "unchecked", why
                continue
            j.verdict = fields["verdict"]
            j.note = fields["note"]
            j.source_page = fields["source_page"]
            j.source_block = fields["source_block"]
            j.anchor_phrases = fields["anchor_phrases"]
        if progress:
            progress(slug, group)

    # the claim-level fields are a summary of the judgements, never a separate
    # opinion — derive them once, after every source has answered
    for c in claims:
        c.apply_headline()
    return claims
