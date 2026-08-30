"""What a run must disclose — decided once in Python, rendered three times in Jinja.

Deliberately not a shared Jinja macro. The three report formats need genuinely
different markup, so one macro would carry format switches; `report.py` sets
`autoescape=select_autoescape(["html"])`, so a macro shared between `.md.j2`
and `.html.j2` is escaped differently depending on which template imported it —
a correctness hazard on the one surface whose job is not lying; and a macro is
testable only by rendering.

Every `Disclosure` carries a `token`: a short literal that must appear verbatim
in **all three** formats. Each format phrases at its own length around it, and
the parity test asserts the substring — which makes "every disclosure reaches
every reader" a loop rather than a hand-maintained checklist.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tokens are the contract. Changing one is a change to all three templates, and
# tests/test_disclosure_parity.py is what says so out loud.
TRUNCATION_TOKEN = "text past the cut was never read"
COVERAGE_TOKEN = "reached by an extracted claim"
# published contract — tests/test_coverage.py asserts this literal
COVERAGE_CAVEAT_TOKEN = "coverage not audited"
COVERAGE_ATTRIBUTION_TOKEN = "attribution is a text match that can be wrong"
UNJUDGED_TOKEN = "could not be obtained, so was never opened"
MULTISOURCE_TOKEN = "cited sources checked"
ANCHOR_LOCATED_TOKEN = "red box = matched text"
ANCHOR_NOT_LOCATED_TOKEN = "no anchor phrase was found on this page"
ANCHOR_UNKNOWN_TOKEN = "anchor match not recorded"
SOURCE_IDENTITY_TOKEN = "identity was never confirmed"


@dataclass(frozen=True)
class Disclosure:
    """One thing the report owes its reader, in three lengths."""

    key: str  # truncation | converter | coverage | coverage_caveat
    #          | coverage_attribution | sources | unjudged_refs | anchor
    level: str  # info | warn
    token: str  # SHORT literal that must appear verbatim in ALL THREE formats
    text: str  # full sentence for markdown / editor
    short: str  # terse line for terminal
    rows: tuple[str, ...] = ()  # detail lines the formats cap at their own length


# --------------------------------------------------------------------------
# anchor state — the tri-state the templates must not flatten
# --------------------------------------------------------------------------


def anchor_state(claim) -> str:
    """`located` | `not_located` | `unknown` for one ClaimResult.

    `unknown` is not `not_located`: it means nothing was ever searched for (no
    anchor phrases), or the highlight step never ran. Rendering it as either
    of the other two asserts a fact the run does not have.
    """
    if claim.anchor_located is True:
        return "located"
    if claim.anchor_located is False:
        return "not_located"
    return "unknown"


ANCHOR: dict[str, Disclosure] = {
    "located": Disclosure(
        key="anchor",
        level="info",
        token=ANCHOR_LOCATED_TOKEN,
        text=(
            f"{ANCHOR_LOCATED_TOKEN} — the anchor phrase was located on this page "
            "by text search, not placed by hand or by the model."
        ),
        short=ANCHOR_LOCATED_TOKEN,
    ),
    "not_located": Disclosure(
        key="anchor",
        level="warn",
        token=ANCHOR_NOT_LOCATED_TOKEN,
        text=(
            f"{ANCHOR_NOT_LOCATED_TOKEN} — the crop is shown for context and "
            "nothing is boxed."
        ),
        short=f"{ANCHOR_NOT_LOCATED_TOKEN} — nothing is boxed",
    ),
    "unknown": Disclosure(
        key="anchor",
        level="warn",
        token=ANCHOR_UNKNOWN_TOKEN,
        text=(
            f"{ANCHOR_UNKNOWN_TOKEN} for this crop — no anchor phrase was searched "
            "for, or the highlight step did not run, so nothing here claims a match."
        ),
        short=f"{ANCHOR_UNKNOWN_TOKEN} — nothing here claims a match",
    ),
}


# --------------------------------------------------------------------------
# run-level and claim-level rules
# --------------------------------------------------------------------------


def _truncation(truncated: dict) -> Disclosure:
    detail = ", ".join(
        f"{what} ({t.get('chars')} chars, cut at {t.get('limit')})"
        for what, t in truncated.items()
    )
    return Disclosure(
        key="truncation",
        level="warn",
        token=TRUNCATION_TOKEN,
        text=(
            f"Input truncated — {detail}. These inputs exceeded the character limit "
            f"sent to the model, and the {TRUNCATION_TOKEN}, so it was never checked."
        ),
        short=f"input truncated — {detail} — {TRUNCATION_TOKEN}",
    )


def _converter(name: str) -> Disclosure:
    """The ingest backend is named unconditionally: a docling run that goes
    unstamped is a run whose fidelity the reader cannot judge either way."""
    name = name or "unknown"
    flat = name == "pymupdf"
    token = f"converter: {name}"
    if flat:
        return Disclosure(
            key="converter",
            level="warn",
            token=token,
            text=(
                f"Ingest `{token}` — flat text. Tables were linearized and figures are "
                "not represented in this run, so claims living inside tables may be "
                "under-checked."
            ),
            short=f"{token} · flat text — tables linearized",
        )
    return Disclosure(
        key="converter",
        level="info",
        token=token,
        text=f"Ingest `{token}` — layout-aware.",
        short=token,
    )


def coverage_headline(coverage: dict) -> str:
    """The one sentence the coverage audit is allowed to be summarised as.

    Two shapes, because an old `results.json` carries no occurrences and must
    keep rendering its label-level line rather than an empty ratio. The
    uncertain count is always printed *beside* the ratio and never folded into
    it: when `uncertain` is large the ratio is close to meaningless, and a
    reader cannot see that from a percentage alone.
    """
    occ = coverage.get("occurrences")
    if occ:
        labels = {o["label"] for o in occ.get("items", [])}
        return (
            f"{occ['covered']}/{occ['total']} citation occurrences {COVERAGE_TOKEN}, "
            f"across {len(labels)} labels — {occ['uncovered']} unaddressed, "
            f"{occ['uncertain']} uncertain"
        )

    labels_in_text = coverage.get("labels_in_text") or []
    covered = coverage.get("covered") or []
    missing = coverage.get("missing") or []
    if not labels_in_text:
        return ""
    # "all N" when nothing is unaddressed, "N/M" when something is: the two
    # readings are different claims and tests/test_coverage.py pins both
    head = (
        f"{len(covered)}/{len(labels_in_text)} citation labels {COVERAGE_TOKEN}"
        if missing
        else f"all {len(labels_in_text)} citation labels {COVERAGE_TOKEN}"
    )
    return head + (f" — unaddressed: [{'], ['.join(missing)}]" if missing else "")


def _occurrence_rows(coverage: dict) -> tuple[str, ...]:
    """One line per occurrence NO claim reached, or reached ambiguously.

    Only those: the report's job here is to show what it did *not* do. Each
    row carries the page and the sentence, because a bare label is not
    something a reader can act on.
    """
    rows = []
    for o in (coverage.get("occurrences") or {}).get("items", []):
        if o["status"] == "covered":
            continue
        where = f"p.{o['page']}" if o.get("page") else "page not recorded"
        section = f" · {o['section']}" if o.get("section") else ""
        rows.append(f"[{o['label']}] {o['status']} · {where}{section} — “{o['sentence']}”")
    return tuple(rows)


def _coverage(coverage: dict) -> Disclosure:
    occ = coverage.get("occurrences")
    head = coverage_headline(coverage)
    unresolved = (
        (occ["uncovered"] + occ["uncertain"]) if occ else bool(coverage.get("missing"))
    )
    tail = (
        "Coverage counts places an extracted claim *reached*, not sources that were read."
        if occ
        else "Coverage counts labels an extracted claim *cites*, not sources that were read."
    )
    return Disclosure(
        key="coverage",
        level="warn" if unresolved else "info",
        token=COVERAGE_TOKEN,
        text=f"{head}. {tail}",
        short=head,
        rows=_occurrence_rows(coverage),
    )


def _coverage_attribution() -> Disclosure:
    """The self-caveat occurrence-level coverage owes for existing.

    Label-level coverage was set arithmetic and could not produce a false
    positive. This can, so the ways it is weaker are printed on the report's
    face rather than merely known.
    """
    return Disclosure(
        key="coverage_attribution",
        level="warn",
        token=COVERAGE_ATTRIBUTION_TOKEN,
        text=(
            f"How to read that figure: {COVERAGE_ATTRIBUTION_TOKEN} — deciding which "
            "citation a claim came from is a text comparison, so the counts can be "
            "right while a pointer is wrong. An attribution the tool cannot make "
            "counts as NOT covered, never as covered — and it refuses close "
            "calls, so two similar sentences citing one reference can both read "
            "as unaddressed where a reader would pair them at a glance. This "
            "figure understates coverage there. A sentence citing the same "
            "reference twice needs two extracted claims, so the ratio is not "
            "comparable between papers. And detection still reads bracketed numeric "
            "markers only — a citation style it cannot see contributes no "
            "occurrences at all, which makes this ratio look better than reality, "
            "not worse."
        ),
        short=(
            f"{COVERAGE_ATTRIBUTION_TOKEN}; unattributable = not covered, "
            "including close calls it refuses to decide; unseen citation styles "
            "contribute no occurrences, so the ratio flatters the run"
        ),
    )


def _coverage_caveat() -> Disclosure:
    return Disclosure(
        key="coverage_caveat",
        level="warn",
        token=COVERAGE_CAVEAT_TOKEN,
        text=(
            "No bracketed numeric citation markers found — only bracketed styles like "
            "[12], [7,8] and [9-11] are recognized, and author-year and bare-superscript "
            f"styles are not — {COVERAGE_CAVEAT_TOKEN} for this paper."
        ),
        short=(
            "no bracketed numeric citation markers found — only [12]/[7,8]/[9-11] "
            f"styles are audited; {COVERAGE_CAVEAT_TOKEN}"
        ),
    )


def _source_identity(unverified: list, mismatched: list) -> Disclosure:
    """Sources in use that nobody confirmed are the paper the reference names.

    The retrieval manifest carries the per-source detail, but it is rendered in
    the markdown report only — so this fact reached one of three readers. A
    provided file is still used (the user named it, and a scanned PDF is common
    for exactly the papers people supply by hand), which is precisely why the
    reader has to be told the check did not happen.
    """
    n = len(unverified) + len(mismatched)
    slugs = ", ".join(sorted(e.slug or e.num for e in unverified + mismatched))
    kinds = []
    if unverified:
        kinds.append(f"{len(unverified)} unreadable or unmatchable first page")
    if mismatched:
        kinds.append(f"{len(mismatched)} whose title did not match the reference")
    return Disclosure(
        key="source_identity",
        level="warn",
        token=SOURCE_IDENTITY_TOKEN,
        text=(
            f"{n} cited source{'s' if n != 1 else ''} in use whose {SOURCE_IDENTITY_TOKEN}"
            f" — {'; '.join(kinds)} ({slugs}). Verdicts resting on "
            f"{'them' if n != 1 else 'it'} could be about a different paper; the retrieval "
            f"manifest carries the reason per source."
        ),
        short=f"{n} source{'s' if n != 1 else ''} whose {SOURCE_IDENTITY_TOKEN}",
    )


def run_disclosures(results, manifest=None) -> list[Disclosure]:
    """Every run-level disclosure this RunResults owes its reader.

    Reads `results.truncated` — the dataclass field, the only place truncation
    is recorded. There is no module-global fallback to read instead.
    """
    out: list[Disclosure] = []
    if results.truncated:
        out.append(_truncation(results.truncated))
    out.append(_converter(results.converter))
    coverage = results.coverage or {}
    if coverage:
        # occurrences without labels means clean.md was missing while the source
        # map was not: the audit ran, so it must not read as "not audited"
        if coverage.get("labels_in_text") or (coverage.get("occurrences") or {}).get("total"):
            out.append(_coverage(coverage))
            # only occurrence-level coverage owes the attribution caveat: the
            # label-level audit it replaces could not make that mistake
            if coverage.get("occurrences"):
                out.append(_coverage_attribution())
        else:
            out.append(_coverage_caveat())
    # a source whose identity nobody established is a run-level fact: it is not
    # attached to one claim, and every verdict resting on that file inherits it
    if manifest is not None:
        in_use = [e for e in manifest.entries if e.status in ("retrieved", "provided")]
        unverified = [e for e in in_use if e.title_check == "unverifiable"]
        mismatched = [e for e in in_use if e.title_check == "mismatch"]
        if unverified or mismatched:
            out.append(_source_identity(unverified, mismatched))
    return out


def _unjudged(claim) -> Disclosure:
    """Co-citations that could not be retrieved.

    Every cited source that *was* obtainable is judged and appears in the
    breakdown, so an entry here carries one meaning only: nobody read it. It is
    not a negative finding about the source — it is the absence of a finding.
    """
    labels = f"[{'], ['.join(claim.unjudged_refs)}]"
    one = len(claim.unjudged_refs) == 1
    return Disclosure(
        key="unjudged_refs",
        level="warn",
        token=UNJUDGED_TOKEN,
        text=(
            f"Co-cited {labels} {UNJUDGED_TOKEN} — nothing here speaks to "
            f"{'it' if one else 'them'} either way; the retrieval manifest says why."
        ),
        short=f"co-cited {labels} {UNJUDGED_TOKEN}",
    )


def _sources(claim) -> Disclosure:
    """How the cited sources fell out — the count that sits under a claim.

    Only for genuinely multi-source claims: a "1 cited source checked" banner
    on every single-reference claim would be noise, and the plural in the token
    would be a lie.
    """
    s = claim.judgement_summary()
    # singular/plural per bucket: "1 contradict it" reads as a typo and makes
    # the one line a reviewer scans hardest look careless
    phrasing = (
        ("supported", "fully supports it", "fully support it"),
        ("partial", "partially supports it", "partially support it"),
        ("contradicted", "contradicts it", "contradict it"),
        ("not_addressed", "does not address it", "do not address it"),
        ("unchecked", "could not be checked", "could not be checked"),
    )
    parts = [
        f"{s[key]} {one if s[key] == 1 else many}"
        for key, one, many in phrasing
        if s[key]
    ]
    breakdown = "; ".join(parts)
    # a lone dissenter is the whole reason this box exists, so name the split
    # rather than leaving the reader to compare numbers
    split = s["supported"] and (s["contradicted"] or s["partial"])
    return Disclosure(
        key="sources",
        level="warn" if s["contradicted"] else "info",
        token=MULTISOURCE_TOKEN,
        text=(
            f"{s['total']} {MULTISOURCE_TOKEN} for this claim: {breakdown}."
            + (" The sources disagree — each verdict below rests only on that "
               "source's own text." if split else "")
        ),
        short=f"{s['total']} {MULTISOURCE_TOKEN}: {breakdown}",
        rows=tuple(
            f"[{j.ref}] {j.source_slug} — {j.verdict}"
            + (f" (p{j.source_page})" if j.source_page else "")
            for j in claim.judgements
        ),
    )


def judgement_disclosures(j) -> list[Disclosure]:
    """What one source's judgement owes its reader: the caption for its crop.

    Separate from `claim_disclosures` because a judgement is not a claim — it
    has no co-citations and no breakdown of its own, only the anchor state of
    the single page it points at.
    """
    return [ANCHOR[anchor_state(j)]] if j.evidence_image else []


def claim_disclosures(claim) -> list[Disclosure]:
    """Every claim-level disclosure this ClaimResult owes its reader."""
    out: list[Disclosure] = []
    if claim.is_multi_source():
        out.append(_sources(claim))
    if claim.unjudged_refs:
        out.append(_unjudged(claim))
    if claim.evidence_image:
        out.append(ANCHOR[anchor_state(claim)])
    return out
