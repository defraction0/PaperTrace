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
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from .models import (
    JUDGMENT_VERDICTS,
    PIPELINE_STATES,
    ClaimResult,
    RefManifest,
    SourceJudgement,
    UncitedClaim,
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

Task 1 — CITED claims: extract EVERY claim that carries a citation marker.
Completeness over selectivity: each bracketed label like [3] or [7,8] or [9-11]
that supports a statement must appear in at least one extracted claim. This
includes numerical results, "X showed Y", methodological attributions,
guideline statements, prevalence claims — and claims made inside TABLES.

Task 2 — UNCITED assertions: list assertive factual statements that carry NO
citation but would normally need one (numbers, prevalence, mechanisms,
standard-of-care statements). Exclude the manuscript's own results and
methods descriptions of what the authors themselves did.

Rules for both:
- claim: the statement, tightly paraphrased, ≤160 chars.
- location: manuscript section (e.g. "Introduction ¶2", "Methods", "Table 2").
- cited claims also carry refs: citation labels as strings, e.g. ["3"] or ["7","8"].
- Number each list from 1 in reading order.

Answer with ONLY a JSON object, no prose, no code fences:
{"cited":[{"id":1,"claim":"...","location":"...","refs":["1"]}],
 "uncited":[{"id":1,"claim":"...","location":"..."}]}

MANUSCRIPT:
"""

CHECK_PROMPT = """You are the verification step of a peer-review fact-checker.

Judge each CLAIM below strictly against the SOURCE text (a cited paper,
converted to markdown with `<!-- block_NNNN, page N -->` markers). The source
text is the only evidence — never use outside knowledge of the paper.

A claim may cite several sources. You are shown ONE of them. Judge only what
THIS source does or does not say, and do not speculate about the others: each
is judged in its own call and the results are combined afterwards.

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


def _ask(prompt: str, model: str | None = None) -> str:
    cmd = ["claude", "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT
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


def extract_claims(
    case_dir: Path,
    model: str | None = None,
    *,
    truncations: Truncations | None = None,
) -> tuple[list[ClaimResult], list[UncitedClaim]]:
    annotated = case_dir / "ingest" / "manuscript" / "annotated.md"
    text = _clip(annotated.read_text(), MANUSCRIPT_CHAR_LIMIT, "manuscript", truncations)
    raw = _ask(EXTRACT_PROMPT + text, model)
    data = _parse_json_object(raw)
    cited = [
        ClaimResult(
            id=int(c["id"]),
            claim=str(c["claim"]),
            location=str(c.get("location", "")),
            refs=[str(r) for r in c.get("refs", [])],
        )
        for c in data.get("cited", [])
    ]
    uncited = [
        UncitedClaim(
            id=int(u["id"]),
            claim=str(u["claim"]),
            location=str(u.get("location", "")),
        )
        for u in data.get("uncited", [])
    ]
    return cited, uncited


# ---------------------------------------------------------------------------
# deterministic citation-label coverage audit
# ---------------------------------------------------------------------------

_LABEL_GROUP = re.compile(r"\[(\d{1,3}(?:\s*[,\u2013\u2014-]\s*\d{1,3})*)\]")
_REFS_HEADING = re.compile(r"^##\s+(references|bibliography|literature)\b", re.I | re.M)


def _expand_label_group(group: str) -> set[str]:
    labels: set[str] = set()
    for part in re.split(r"\s*,\s*", group):
        m = re.match(r"^(\d{1,3})\s*[\u2013\u2014-]\s*(\d{1,3})$", part.strip())
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= hi and hi - lo <= 50:
                labels.update(str(n) for n in range(lo, hi + 1))
        elif part.strip().isdigit():
            labels.add(part.strip())
    return labels


def citation_labels_in_text(clean_md: str) -> set[str]:
    """Every citation label appearing in the body text (References section excluded)."""
    cut = _REFS_HEADING.search(clean_md)
    body = clean_md[: cut.start()] if cut else clean_md
    labels: set[str] = set()
    for m in _LABEL_GROUP.finditer(body):
        labels.update(_expand_label_group(m.group(1)))
    return labels


# ---------------------------------------------------------------------------
# occurrence-level coverage
#
# Label-level coverage was pure set arithmetic — mechanical and incapable of a
# false positive, but blind: two sentences citing [3] with one extracted claim
# reported [3] as covered and left the other sentence invisible. Occurrences
# fix the blindness and buy a new failure mode with it (the *counts* stay
# right; the *pointer* can be wrong), which is why `uncertain` is a third
# status and why the report carries a self-caveat.
# ---------------------------------------------------------------------------

OCCURRENCE_MIN_RATIO = 0.45
OCCURRENCE_MIN_MARGIN = 0.10
_EXCERPT_RADIUS = 120

# structural, not textual: the source map says a block IS a section header, so
# the References cut no longer depends on ingest happening to emit `## `
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _normalize_for_match(text: str) -> str:
    """Fold the differences that are never semantic, for attribution only.

    Deliberately *similar* to `evals/align.py::normalize_claim`, not identical —
    do not collapse the two. This one removes quotes outright and strips
    bracketed citation markers, because it compares a claim against manuscript
    sentences that carry `[3]` the claim text never had; `normalize_claim`
    converts quotes to ASCII and keeps the markers, because it compares two
    claim texts where a marker is signal.

    They stay separate for a second reason: papertrace cannot import `evals`
    (it is not in the wheel), and `evals` must not import a matcher from the
    very thing it grades. Ten lines is the right price.
    """
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(dict.fromkeys(map(ord, "‐‑‒–—―"), "-"))
    t = re.sub(r"[*_`\"'‘’“”]", "", t).casefold()
    t = re.sub(r"\[\d[\d\s,–—-]*\]", " ", t)  # the markers themselves carry no meaning
    return re.sub(r"\s+", " ", t).strip(" .")


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _excerpt(text: str, start: int, end: int) -> str:
    """The sentence carrying the marker, clipped to ±120 chars around it.

    The excerpt is what turns an uncovered occurrence from a bare number into
    something a reader can act on, and it is also what the attributor matches
    against — so it is the sentence, not an arbitrary window.
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
            out += _occurrences_in(
                b.text or "",
                block=b.id,
                page=b.page,
                section=(b.heading_path[-1] if b.heading_path else ""),
            )
        return out, "source_map"

    clean = manuscript / "clean.md"
    if clean.exists():
        text = clean.read_text()
        cut = _REFS_HEADING.search(text)
        body = text[: cut.start()] if cut else text
        return _occurrences_in(body, block=None, page=None, section=""), "clean.md"
    return [], "none"


def _location_matches(location: str, section: str) -> bool:
    """Does a claim's free-text `location` ("Methods §2") name this section?"""
    loc, sec = _normalize_for_match(location), _normalize_for_match(section)
    if not loc or not sec:
        return False
    return sec in loc or loc in sec


def _attribute_label(occs: list[dict], claims: list[ClaimResult]) -> tuple[dict, list[int], bool]:
    """Which occurrence of ONE label each claim citing it reached.

    Returns (occurrence id -> claim id, claim ids attributed to nothing,
    whether any attribution was refused on the margin).

    One occurrence is the whole answer: a claim citing the label reached the
    only place the label appears. With several, the claim's `location` narrows
    the field and text similarity decides, assigned globally best-first. The
    extraction prompt returns a tight ≤160-char paraphrase, so the absolute
    ratio is weak evidence — **the margin is the decisive test**, since the
    question is only *which* occurrence.

    Reading-order zipping (claim 1 → occurrence 1, and so on) is deliberately
    NOT used. `EXTRACT_PROMPT` does ask for reading order, which makes it
    tempting, but the order is unverified and degrades silently on a single
    skipped claim: every later pairing shifts by one and the audit manufactures
    confident, wrong attributions. Refusing to answer is the honest failure.
    """
    if not occs:
        return {}, [], False
    if len(occs) == 1:
        if claims:
            return {occs[0]["id"]: claims[0].id}, [c.id for c in claims[1:]], False
        return {}, [], False

    edges: list[tuple[float, int, str]] = []
    for c in claims:
        narrowed = [o for o in occs if _location_matches(c.location, o["section"])] or occs
        want = _normalize_for_match(c.claim)
        for o in narrowed:
            edges.append((_ratio(want, _normalize_for_match(o["sentence"])), c.id, o["id"]))
    edges.sort(key=lambda t: (-t[0], t[1], t[2]))

    assigned: dict[str, int] = {}
    taken: set[int] = set()
    refused = False
    for score, cid, oid in edges:
        if cid in taken or oid in assigned or score < OCCURRENCE_MIN_RATIO:
            continue
        # the competitor is the best LIVE candidate sharing either endpoint —
        # a row rival (this claim, another occurrence) or a column rival
        # (another claim, this occurrence). Both are coin flips.
        rivals = [
            t for t in edges
            if (t[1] == cid) != (t[2] == oid) and t[1] not in taken and t[2] not in assigned
        ]
        if score - max((t[0] for t in rivals), default=0.0) >= OCCURRENCE_MIN_MARGIN:
            assigned[oid] = cid
            taken.add(cid)
        else:
            refused = True
    return assigned, [c.id for c in claims if c.id not in taken], refused


def attribute_occurrences(
    occurrences: list[dict], claims: list[ClaimResult]
) -> tuple[list[dict], list[dict]]:
    """Stamp a `status` and a `claim_id` on every occurrence.

    `uncertain` is a third status and is NEVER counted as covered: it means a
    claim did reach this label and the tool cannot say which sentence it came
    from. Calling that covered restores the overstatement; calling it
    uncovered cries wolf. Surplus claims (more claims cite the label than there
    are places citing it) are recorded and cast no doubt on anything — every
    occurrence is already attributed, so nothing is left to be uncertain about.
    """
    by_label: dict[str, list[dict]] = {}
    for o in occurrences:
        by_label.setdefault(o["label"], []).append(o)

    status: dict[str, tuple[str, int | None]] = {}
    unattributed: list[dict] = []
    for label, occs in by_label.items():
        citing = [c for c in claims if label in c.refs]
        assigned, orphans, refused = _attribute_label(occs, citing)
        rest = "uncertain" if (refused or orphans) else "uncovered"
        for o in occs:
            claim_id = assigned.get(o["id"])
            status[o["id"]] = ("covered", claim_id) if claim_id is not None else (rest, None)
        unattributed += [{"claim_id": cid, "label": label} for cid in orphans]

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
        "schema": "coverage/2",
        "unit": "occurrence",
        "source": source,
        "labels_partially_covered": partial,
        "labels_uncertain_only": uncertain_only,
        "occurrences": {"total": len(items), **counts, "items": items},
        "attribution": {
            "method": "location narrowing, then text similarity assigned globally best-first",
            "min_ratio": OCCURRENCE_MIN_RATIO,
            "min_margin": OCCURRENCE_MIN_MARGIN,
            "claims_unattributed": unattributed,
        },
    }


def _slug_for_ref(manifest: RefManifest, label: str):
    return next((e for e in manifest.entries if e.num == label), None)


_MAX_PAGE_DIGITS = 5  # a page number, not an integer literal


def _judgement_from(entry) -> tuple[dict | None, str]:
    """Validate one model response object into claim fields, or say why not.

    Total by construction: every branch is an isinstance test, so this cannot
    raise. A validator that throws would turn a bug in OUR code into a note
    blaming the model — the same laundering `unchecked` exists to prevent.

    Rejection is all-or-nothing. The caller writes no field unless every field
    validated, so a bad response never leaves half-applied provenance behind.
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

    # optional — but a non-string block id is never coerced into one
    block = entry.get("source_block")
    if block is not None and not isinstance(block, str):
        return None, (
            f"model returned an unusable verdict for this claim: {verdict!r} with a "
            f"source_block that is not a block id ({block!r})"
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


def check_claims(
    claims: list[ClaimResult],
    manifest: RefManifest,
    case_dir: Path,
    model: str | None = None,
    progress=None,
    on_error=None,
    *,
    truncations: Truncations | None = None,
) -> list[ClaimResult]:
    """Fill verdicts in place. One model call per source that carries claims.

    A failed check is NEVER disguised as a retrieval gap: if the source was
    available but the model call or source ingest failed (after one retry),
    the claims get verdict `unchecked`, the reason lands in the note, and
    `on_error(slug, message)` fires so the CLI can say so loudly.

    Sources are ingested with the flat backend on purpose — fast and
    dependable, and text anchors are what verdicts and crops need. Layout
    fidelity (tables/figures) is spent on the audited paper, not its sources.
    """
    by_slug: dict[str, list[ClaimResult]] = {}
    for c in claims:
        pairs = [(r, _slug_for_ref(manifest, r)) for r in c.refs]
        avail = [(r, e) for r, e in pairs if e and e.status in ("retrieved", "provided") and e.slug]
        if not avail:
            c.verdict = "not_retrieved"
            reasons = {e.status for _, e in pairs if e}
            c.note = f"cited source not available ({', '.join(sorted(reasons)) or 'unknown ref'})"
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
            c.judgements.append(SourceJudgement(source_slug=e.slug, ref=r))
            by_slug.setdefault(e.slug, []).append(c)
        # what is left here could NOT be obtained — the only remaining reason a
        # cited source goes unopened
        avail_refs = {r for r, _ in avail}
        c.unjudged_refs = [r for r in c.refs if r not in avail_refs]

    for slug, group in by_slug.items():
        try:
            ingest_dir = case_dir / "ingest" / slug
            annotated = ingest_dir / "annotated.md"
            if not annotated.exists():
                entry = next(e for e in manifest.entries if e.slug == slug)
                from .ingest import ingest_pdf

                ingest_pdf(Path(entry.pdf_path), ingest_dir, backend="pymupdf")
            claims_json = json.dumps(
                [{"id": c.id, "claim": c.claim, "location": c.location} for c in group]
            )
            prompt = (
                CHECK_PROMPT.replace("<<CLAIMS>>", claims_json)
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
        for c in group:
            j = next((x for x in c.judgements if x.source_slug == slug), None)
            if j is None:  # pragma: no cover - group membership implies one
                continue
            v = verdicts.get(c.id)
            if v is None:
                j.verdict, j.note = "unchecked", "model returned no verdict for this claim"
                continue
            fields, why = _judgement_from(v)
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
