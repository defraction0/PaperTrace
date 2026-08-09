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
from pathlib import Path

from .models import ClaimResult, RefManifest, UncitedClaim

CLAUDE_TIMEOUT = 600

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

For each claim output:
- verdict: "supported" (source states it), "partial" (kernel true but scope,
  strength or object differs — say what differs), or "contradicted" (source
  says otherwise — quote its actual figure).
- note: ≤2 sentences, the why.
- source_page: page of the decisive passage (integer).
- source_block: its block id, e.g. "block_0042".
- anchor_phrases: 1–3 short VERBATIM strings copied from that block that a
  text search will find (numbers and distinctive wording; unique within the
  block).

Answer with ONLY a JSON array, no prose, no code fences:
[{"id":3,"verdict":"partial","note":"...","source_page":5,
  "source_block":"block_0042","anchor_phrases":["p < 0.001"]}, ...]

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
    case_dir: Path, model: str | None = None
) -> tuple[list[ClaimResult], list[UncitedClaim]]:
    annotated = case_dir / "ingest" / "manuscript" / "annotated.md"
    raw = _ask(EXTRACT_PROMPT + annotated.read_text()[:180_000], model)
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


def coverage_audit(case_dir: Path, claims: list[ClaimResult]) -> dict:
    """Compare labels present in the manuscript against labels covered by claims.

    Mechanical and prompt-independent: if extraction skipped a citation, the
    missing label shows up here and lands in the report.
    """
    clean = case_dir / "ingest" / "manuscript" / "clean.md"
    in_text = citation_labels_in_text(clean.read_text()) if clean.exists() else set()
    covered = {r for c in claims for r in c.refs}
    missing = sorted(in_text - covered, key=int)
    return {
        "labels_in_text": sorted(in_text, key=int),
        "covered": sorted(covered & in_text, key=int),
        "missing": missing,
    }


def _slug_for_ref(manifest: RefManifest, label: str):
    return next((e for e in manifest.entries if e.num == label), None)


def check_claims(
    claims: list[ClaimResult],
    manifest: RefManifest,
    case_dir: Path,
    model: str | None = None,
    progress=None,
    on_error=None,
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
        entries = [_slug_for_ref(manifest, r) for r in c.refs]
        avail = [e for e in entries if e and e.status in ("retrieved", "provided") and e.slug]
        if not avail:
            c.verdict = "not_retrieved"
            reasons = {e.status for e in entries if e}
            c.note = f"cited source not available ({', '.join(sorted(reasons)) or 'unknown ref'})"
            continue
        # judge against the first available cited source; multi-ref nuance is
        # the interactive mode's job
        primary = avail[0]
        c.source_slug = primary.slug
        by_slug.setdefault(primary.slug, []).append(c)

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
                .replace("<<SOURCE>>", annotated.read_text()[:150_000])
            )
            try:
                raw = _ask(prompt, model)
            except (RuntimeError, ValueError):
                raw = _ask(prompt, model)  # one retry — claude -p fails transiently
            verdicts = {v["id"]: v for v in _parse_json_array(raw)}
        except Exception as e:  # noqa: BLE001 — a failed check must never kill the run
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            for c in group:
                c.verdict = "unchecked"
                c.note = (
                    f"check failed ({msg}) — the source WAS retrieved; "
                    f"re-run `papertrace check` to retry"
                )
            if on_error:
                on_error(slug, msg)
            continue
        for c in group:
            v = verdicts.get(c.id)
            if not v:
                c.verdict, c.note = "unchecked", "model returned no verdict for this claim"
                continue
            c.verdict = v.get("verdict", "partial")
            c.note = v.get("note", "")
            c.source_page = v.get("source_page")
            c.source_block = v.get("source_block")
            c.anchor_phrases = [str(p) for p in v.get("anchor_phrases", [])]
        if progress:
            progress(slug, group)
    return claims
