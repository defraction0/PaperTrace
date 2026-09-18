"""What a run must disclose — decided once in Python, rendered by every format.

Deliberately not a shared Jinja macro. The report formats need genuinely
different markup, so one macro would carry format switches; `report.py` escapes
`.html.j2` templates and not `.md.j2` ones, so a macro shared between them is
escaped differently depending on which template imported it — a correctness
hazard on the one surface whose job is not lying; and a macro is testable only
by rendering. The viewer does not render these in Jinja at all: it embeds them
as data and draws them in the browser, which is why they must never be
re-derived there.

Every `Disclosure` carries a `token`: a short literal that must appear verbatim
in **all four** formats. Each format phrases at its own length around it, and
the parity test asserts the substring — which makes "every disclosure reaches
every reader" a loop rather than a hand-maintained checklist.
"""

from __future__ import annotations

from dataclasses import dataclass

# the one place the judgement vocabulary is defined — a local copy of the four
# names here would be a second vocabulary to keep in step
from .models import JUDGMENT_VERDICTS

# Tokens are the contract. Changing one is a change to every template, and
# tests/test_disclosure_parity.py is what says so out loud.
TRUNCATION_TOKEN = "text past the cut was never read"
COVERAGE_TOKEN = "reached by an extracted claim"
# published contract — tests/test_coverage.py asserts this literal
COVERAGE_CAVEAT_TOKEN = "coverage not audited"
COVERAGE_ATTRIBUTION_TOKEN = "attribution is the context the extractor named"
UNJUDGED_TOKEN = "could not be obtained, so was never opened"
MULTISOURCE_TOKEN = "cited sources checked"
# same falseness `headline_qualifier` avoids: one cited work read as its article
# plus a supplement is not two cited works, and this count sits under the headline
MULTISOURCE_DOCUMENTS_TOKEN = "documents checked"
ANCHOR_LOCATED_TOKEN = "red box = matched text"
# says nothing about the rest of the page, because nothing established it. The
# crop region is one block's bbox, so a passage continuing into the next column
# is on the page and outside the region at once — "was found on this page" made
# that an absence, and the token is shared by both branches below, so it has to
# stay true whether the phrase is elsewhere on the page or nowhere at all.
ANCHOR_NOT_LOCATED_TOKEN = "no anchor phrase could be boxed"
ANCHOR_UNKNOWN_TOKEN = "anchor match not recorded"
SOURCE_IDENTITY_TOKEN = "identity was never confirmed"
REFERENCES_RESUMED_TOKEN = "reference list continued past a section break"
NUMBERING_TOKEN = "reference numbering could not be confirmed"
CLAIM_NUMBERING_TOKEN = "cites a reference whose numbering was never confirmed"
NUMBERING_CONTESTED_TOKEN = "second reading of the reference list disagreed"
# no apostrophe, matching NO_QUOTE_TOKEN's own rule above: autoescape rewrites
# one to `&#39;` in the two HTML formats but not in markdown, so a token that
# carries one passes here and fails the parity test that asserts it verbatim
# in all four — "reference list's" was tried first and had to be reworded.
LABELS_DISPUTED_TOKEN = "labels where the reference list readings disagree"
# `claim_pairing` gains three more states in Task 7 (resolved by a model call,
# chosen by the user, corroborated by a second agreeing reading) — each needs
# its own token, the same way ANCHOR_LOCATED_TOKEN / ANCHOR_NOT_LOCATED_TOKEN /
# ANCHOR_UNKNOWN_TOKEN are one Disclosure key with one token per state actually
# reached.
CLAIM_PAIRING_WITHHELD_TOKEN = "verdict withheld — the reference list readings disagree"
# A DIFFERENT fact from the one above, and it needs its own words: the
# readings disagree AND nothing was ever fetched under the label, so there is
# no source that "may not be the paper the manuscript cites" — there is no
# source. `check.py` keeps such a label out of `withheld_refs` for this
# reason; this token is how the report keeps the distinction it computed.
CLAIM_PAIRING_UNRETRIEVED_TOKEN = "the readings disagree, and no source was retrieved for it"
# "of the printed list", never "of both texts": the call is shown the
# extractions that HAVE a printed span, which is one on a pymupdf-backend
# run, and the reading it rules against (the deposit, the model's own) may
# have none at all. A token is asserted verbatim in all four formats, so it
# has to stay true in every one of those shapes.
CLAIM_PAIRING_RESOLVED_TOKEN = "resolved by a model reading of the printed list, accepted by you"
CLAIM_PAIRING_CHOSEN_TOKEN = "you chose which reading of the reference list to use"
CLAIM_PAIRING_CORROBORATED_TOKEN = "agreed by every reading of the reference list"
# no apostrophe, `&`, `<` or `>` in either — same HTML-autoescape rule as
# LABELS_DISPUTED_TOKEN above, since both are asserted verbatim in two formats
# that escape and two that do not
REFLIST_TOKEN = "the reference list was also read by a model"
NUMBERING_CORROBORATION_TOKEN = "two readings of the reference list agree on every cited label"
# Task 7 review, M6: without this, a run that resolved 11 of 14 disputed
# labels says so only on the claims that happen to cite one of them — the
# withholding disclosure below (`labels_disputed`) already has a run-level
# counterpart, and the resolution deserves the same one.
NUMBERING_RESOLUTION_TOKEN = "labels resolved after the reference list readings disagreed"
# a DIFFERENT token for a DIFFERENT fact — `numbering_corroborated` is a
# per-label property (every cited label was independently agreed by >= 2
# readings), while `corroborating_readings` is a per-reading one (this
# reading agreed on ALL of them). The two can now disagree: every label
# agreed, but no single reading spans every label. Forcing
# NUMBERING_CORROBORATION_TOKEN's literal words ("two readings … agree on
# every cited label") to cover that state would assert something the
# `corroborating_readings` list — 0 or 1 names — cannot back up.
NUMBERING_NO_SPANNING_READING_TOKEN = "no single reading agreed on every cited label"
# The keys `claim_disclosures()` can emit. Declared here, where the producers
# live, because three of the four report formats filter claim disclosures by
# explicit key and so drop an unlisted one without erroring. The viewer is
# generic and would keep rendering it, which is what makes the loss silent.
CLAIM_KEYS = frozenset(
    {
        "anchor",
        "sources",
        "unjudged_refs",
        "no_quote",
        "supplement_headline",
        "claim_numbering",
        "claim_pairing",
    }
)
# no apostrophe, and no `&`, `<` or `>`: a token is asserted as a literal in the
# HTML formats too, and autoescape would rewrite it there but not in markdown —
# so the parity test would fail on a difference the reader never sees
NO_QUOTE_TOKEN = "judged on a paraphrase, not the sentence in the paper"
SOURCE_FIDELITY_TOKEN = "cited sources read as flat text"
# A cell the table converter could not place is text missing from the source.
# It reached the user only as a stray WARNING from a third-party logger, under
# roughly two hundred lines of that logger's own repair messages.
TABLE_LOSS_TOKEN = "table cells were dropped by the converter"
# how many of the converter's own messages the sentence quotes before counting
_TABLE_LOSS_SHOWN = 2
# Neutral on purpose. The token is asserted verbatim in every format, so it
# must stay true whether every supplement was checked, none was, or some were —
# "carry no identity check" was true when nothing could be verified and became a
# falsehood about the checked ones the moment some could.
SUPPLEMENT_IDENTITY_TOKEN = "how each supplement was attached"
SUPPLEMENT_COVERAGE_TOKEN = "citations inside a supplement are not counted"
SUPPLEMENT_HEADLINE_TOKEN = "this verdict rests on supplementary material"


@dataclass(frozen=True)
class Disclosure:
    """One thing the report owes its reader, in three lengths."""

    key: str  # truncation | converter | coverage | coverage_caveat
    #          | coverage_attribution | sources | unjudged_refs | anchor
    #          | no_quote | claim_numbering | numbering | references_resumed
    #          | source_identity | source_fidelity
    level: str  # info | warn
    token: str  # SHORT literal that must appear verbatim in ALL FOUR formats
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
            # not "on this page": a passage crossing a page break is shown in
            # several images, and the box can be on a page other than the one
            # the judgement names
            f"{ANCHOR_LOCATED_TOKEN} — the anchor phrase was located by text "
            "search, not placed by hand or by the model."
        ),
        short=ANCHOR_LOCATED_TOKEN,
    ),
    "not_located": Disclosure(
        key="anchor",
        level="warn",
        token=ANCHOR_NOT_LOCATED_TOKEN,
        text=(
            f"{ANCHOR_NOT_LOCATED_TOKEN} — none was found inside the region this "
            "crop shows, so the crop is shown for context only."
        ),
        short=f"{ANCHOR_NOT_LOCATED_TOKEN} — crop shown for context",
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

# The same three facts when no crop was written. Same tokens on purpose — the
# parity contract is the token, so a format cannot drop one by taking this
# branch — but the sentence must not describe a picture that does not exist.
ANCHOR_NO_IMAGE: dict[str, Disclosure] = {
    "located": Disclosure(
        key="anchor",
        level="info",
        token=ANCHOR_LOCATED_TOKEN,
        text=(
            f"{ANCHOR_LOCATED_TOKEN} — the anchor phrase was located by text "
            "search, though no evidence image was written for it."
        ),
        short=f"{ANCHOR_LOCATED_TOKEN} — no evidence image",
    ),
    "not_located": Disclosure(
        key="anchor",
        level="warn",
        token=ANCHOR_NOT_LOCATED_TOKEN,
        text=(
            f"{ANCHOR_NOT_LOCATED_TOKEN}, and no evidence image was produced — so "
            "the page named above is the only provenance this verdict carries."
        ),
        short=f"{ANCHOR_NOT_LOCATED_TOKEN} — and no evidence image",
    ),
    "unknown": Disclosure(
        key="anchor",
        level="warn",
        token=ANCHOR_UNKNOWN_TOKEN,
        text=(
            f"{ANCHOR_UNKNOWN_TOKEN} — no anchor phrase was searched for, or the "
            "highlight step did not run, and no evidence image was produced. "
            "Nothing here claims a match."
        ),
        short=f"{ANCHOR_UNKNOWN_TOKEN} — and no evidence image",
    ),
}


def anchor_disclosure(anchor) -> Disclosure | None:
    """The anchor caption for one claim or judgement, or None if it owes none.

    Gated on *provenance*, not on the picture. A judgement that names a page has
    made a claim about where the evidence is, and owes the reader a statement
    about whether anything was found there — whether or not a crop was written.
    Gating on `evidence_image` was how a verdict with a page number and no crop
    came to disclose nothing at all.

    A claim with no page (`not_retrieved`, or a check that failed before any
    location was offered) gets None: silence about nothing is not a dropped
    disclosure.
    """
    if getattr(anchor, "source_page", None) is None:
        return None
    table = ANCHOR if getattr(anchor, "evidence_image", None) else ANCHOR_NO_IMAGE
    return table[anchor_state(anchor)]


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

    Shorter than it was, because one of the weaknesses is gone: attribution
    used to be a similarity match between a paraphrase and a sentence, which
    could place a claim on the wrong one of two look-alike sentences and
    refused close calls outright. Extraction is now shown the occurrence list
    and returns the id it used. What remains is that naming the id is still a
    model step.
    """
    return Disclosure(
        key="coverage_attribution",
        level="warn",
        token=COVERAGE_ATTRIBUTION_TOKEN,
        text=(
            f"How to read that figure: {COVERAGE_ATTRIBUTION_TOKEN}. Extraction is "
            "shown every place this paper cites something and returns which of them "
            "each claim came from, so the pointer is no longer a text comparison — "
            "but naming it is still a model step, and a claim can be placed on the "
            "wrong sentence. A claim that names no place at all leaves that "
            "reference's remaining places counted as NOT covered, never as covered, "
            "so the figure understates coverage there. And detection still reads "
            "bracketed numeric markers only — a citation style it cannot see "
            "contributes no occurrences at all, which makes this ratio look better "
            "than reality, not worse."
        ),
        short=(
            f"{COVERAGE_ATTRIBUTION_TOKEN}, which is still a model step; a claim "
            "placed nowhere counts as not covered; unseen citation styles "
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


def _references_resumed(total: int) -> Disclosure:
    """The reference list continued past an intervening section.

    Worth the reader's eye in both directions. Crossing the boundary is a guess,
    so the numbering of the later entries could be wrong — but *not* crossing it
    was the previous behaviour, and that failed silently: a real pre-proof put
    refs 1-9 on one page, a declaration section next, then refs 10-15, and the
    audit simply reported nine references and never attempted the other six.
    """
    return Disclosure(
        key="references_resumed",
        level="warn",
        token=REFERENCES_RESUMED_TOKEN,
        text=(
            f"The {REFERENCES_RESUMED_TOKEN}, and the parser followed it — all {total} "
            "entries here span that boundary. Check the numbering of the later entries "
            "against the paper: a list read short would instead have gone unmentioned."
        ),
        short=f"{REFERENCES_RESUMED_TOKEN} — {total} entries, numbering worth a check",
    )


def _numbering(manifest) -> Disclosure:
    """Nobody established that entry [N] is the work the manuscript's [N] means.

    The one disclosure that can invalidate every other finding on the page. The
    citation label is the join key between a claim and the source it is judged
    against, so a list off by one does not produce a *worse* audit — it produces
    a confident audit of the wrong papers. One live run misnumbered 27 of 41
    references and said so nowhere, because `parse_references` was the only
    stage in the pipeline that could not report its own failure.
    """
    start = manifest.unverified_from
    scope = (
        f"Entries from [{start}] onward are affected"
        if start and start > 1
        else "Every entry is affected"
    )
    detail = manifest.numbering_note or (
        "this manifest was written before the reference list was reconciled against "
        "the manuscript's own citation labels, so nothing ever checked it"
    )
    return Disclosure(
        key="numbering",
        level="warn",
        token=NUMBERING_TOKEN,
        text=(
            f"The {NUMBERING_TOKEN} — {detail}. {scope}. The citation label is what "
            "joins a claim to the source it is judged against, so where the numbering "
            "is wrong the verdict is about a different paper than the one named. "
            "Check the retrieval manifest against the paper's own reference list."
        ),
        short=f"{NUMBERING_TOKEN} — {scope.lower()}",
    )


def _numbering_contested(manifest) -> Disclosure | None:
    """Fires whether or not the numbering was verified.

    `_numbering` is gated on `not verified`, so a contested-but-verified run
    disclosed nothing in any format — which is the compensating-parse-error case
    `Reconciliation.contested` was added to catch.

    The text says only what the signal now carries. `contested` was the mere
    existence of a deposit that failed the extent check, and this said "another
    reading named different papers" over a deposit identical for every cited
    label and longer by two references nobody cites. It is now the point where
    the two readings stop describing one paper, and that point being at or below
    a label the body cites — which is a disagreement about an entry some verdict
    rests on, but can still be one reading ending where the other continues.
    """
    if not getattr(manifest, "numbering_contested", False):
        return None
    return Disclosure(
        key="numbering_contested",
        level="warn",
        token=NUMBERING_CONTESTED_TOKEN,
        text=(
            f"A {NUMBERING_CONTESTED_TOKEN} — the reading used here accounts for exactly "
            "the labels the body cites, but the other reading of the same bibliography "
            "stops describing the same paper at an entry the body cites: it names a "
            "different work there, or ends before reaching it. An extent check cannot see "
            "a parse that merges one pair of references and splits another, so check the "
            "retrieval manifest against the paper's own reference list before relying on "
            "a verdict."
        ),
        short=f"{NUMBERING_CONTESTED_TOKEN} — the two readings diverge at a cited entry",
    )


def _labels_disputed(manifest) -> Disclosure | None:
    """Run-level roll-up of every label a claim's verdict was withheld for.

    A reader who stops at the top of the report should see the scope before
    finding it claim by claim: `claim_pairing` says the same thing per claim,
    but only for the claims that actually cite one of these labels.
    """
    labels = sorted(getattr(manifest, "labels_disputed", None) or [], key=lambda r: int(r))
    if not labels:
        return None
    named = f"[{'], ['.join(labels)}]"
    return Disclosure(
        key="labels_disputed",
        level="warn",
        token=LABELS_DISPUTED_TOKEN,
        text=(
            f"{named} are {LABELS_DISPUTED_TOKEN}: two or more readings of the "
            "reference list did not agree on which paper each of these labels "
            "names — they either named different papers, or nothing in them "
            "could be compared — so every claim citing one had that source "
            "withheld from judgement rather than risk a verdict about the wrong "
            "paper. See each claim's own note for which of its citations this "
            "affected."
        ),
        short=f"{named} {LABELS_DISPUTED_TOKEN}",
    )


def _reflist(manifest) -> Disclosure | None:
    """The required disclosure for the model reading, across every real outcome.

    Fires on a reading used, a reading discarded, a reading asked for and not
    obtained, and a reading asked for whose call did not return — never only
    on success, because silence about an attempt reads as no news. The token
    is phrased AROUND in every branch, the way `SUPPLEMENT_IDENTITY_TOKEN` is:
    it is asserted verbatim in all four formats, so a branch where it is not
    literally true would make one format lie.

    Branches on `reflist_outcome` — one of `reflist.REFLIST_OUTCOMES`, never on
    whether `reflist_model` is set and never on a single boolean. A boolean
    cannot tell "never called" apart from "called and failed": that shape of
    field once sent a failed call (`outcome` would have been `True` either
    way) down the SAME prose as a successful one, because the failure's own
    note did not happen to start with the one prefix that branch checked for —
    a plausible-looking "N values … discarded" in place of an admission that
    the call never returned at all. `claude -p` also only reports a model name
    when its own JSON does — a reply naming none is not evidence nothing was
    asked — so a call that happened, verified cleanly and voted can still
    leave `reflist_model == ""`; that is a SEPARATE fact from `outcome` and is
    handled by `named` below, never by conflating the two.
    """
    from .reflist import REFLIST_OUTCOMES  # lazy: `.reflist` pulls in `.refs` -> `httpx`

    outcome = getattr(manifest, "reflist_outcome", "") or ""
    model = getattr(manifest, "reflist_model", "") or ""
    failure = getattr(manifest, "reflist_failure", "") or ""
    notes = list(getattr(manifest, "reflist_fields_discarded", []) or [])
    # a different kind of finding, and it must reach the reader too: a reading
    # whose own labels do not add up is worth knowing about even when every
    # value it proposed was printed
    numbering = list(getattr(manifest, "reflist_numbering_findings", []) or [])
    # `None` — never `0` as a default — because `0` is a MEASURED value: a
    # manifest that recorded a real reading (via `model` OR `notes`) without
    # ever recording this count must not be told apart from one that measured
    # zero. See `RefManifest.reflist_entries_proposed`'s own docstring.
    entries_proposed = getattr(manifest, "reflist_entries_proposed", None)
    # set only where `outcome` below is a GUESS rather than a record — see the
    # `elif failure:` branch, which is the one inference here that cannot be
    # backed by positive evidence
    inferred = False
    if not outcome:
        # An older manifest from before `reflist_outcome` existed.
        # `reflist_fields_discarded` used to be the ONLY slot for "why there
        # is no reading" too, before `reflist_failure` existed — round 1's
        # `_llm_reference_reading` stored a failed call there as
        # `"not obtained — <Exc>: …"` and an unavailable `claude` as
        # `"not attempted — …"`. Those two prefixes are NOT positive proof of
        # a reply (the exact false-success shape N1 was raised about, now
        # reachable again on the LOAD path if they were counted as discarded
        # values); a note without either prefix — `"[2] journal"` — is.
        failure_notes = [n for n in notes if n.startswith(("not obtained — ", "not attempted — "))]
        value_notes = [n for n in notes if n not in failure_notes]
        if model or value_notes:
            # A reported model name AND a genuinely discarded VALUE are each
            # positive proof a call happened and a reply was obtained — settle
            # as "read" from either, not from `model` alone (a real, unnamed
            # reading with discarded fields but no `reflist_outcome` used to
            # lose its required disclosure entirely).
            outcome = "read"
        elif failure_notes:
            old = failure_notes[0]
            outcome = "failed" if old.startswith("not obtained") else "not_attempted"
            failure = failure or old.split(" — ", 1)[-1]
        elif failure:
            # A `failure` reason alone says SOMETHING was asked for even
            # though which state it reached was never recorded; treat it as
            # "not_attempted" rather than manufacture a third guess. Only the
            # inverse ("no positive evidence -> no call") would be the
            # forbidden derivation, and that is exactly the case below that
            # returns `None`.
            #
            # This is a WEAKER inference than the ones above, and worth
            # naming as such: a bare `failure` string cannot actually
            # distinguish "never attempted" from "attempted and failed" (both
            # states write to the same field), so "not_attempted" here is a
            # guess at which of the two happened, not positive proof of it —
            # only reachable from a hand-written manifest, or from a case
            # folder written mid-branch whose `reflist_attempted: true`
            # `from_json` translates into exactly such a reason, since a live
            # run always records `reflist_outcome` itself. `inferred` is what
            # keeps the sentence below honest about which it is: "no reading
            # was obtained" is a claim this branch has no evidence for.
            outcome, inferred = "not_attempted", True
        else:
            outcome = ""
    elif outcome not in REFLIST_OUTCOMES:
        # A value this build's vocabulary does not recognise — a hand edit, or
        # a manifest from a future version with a fourth outcome. Fail CLOSED:
        # the alternative (falling through to the success branch below) would
        # assert the token as a reading that was taken and used, which is the
        # one claim an unrecognised state can least afford to make.
        return Disclosure(
            key="reflist",
            level="warn",
            token=REFLIST_TOKEN,
            text=(
                f"This run recorded that {REFLIST_TOKEN}, with an outcome "
                f"({outcome!r}) this build of papertrace does not recognise. "
                "Treat the reference numbering as resting on the readings above this "
                "and nothing else, the same as an outcome that was never obtained."
            ),
            short=f"{REFLIST_TOKEN}: unrecognised outcome {outcome!r}",
        )
    resolution = _resolution_clause(manifest)
    if not outcome or (outcome == "not_attempted" and not failure):
        # the second clause is the caller's own silent choice (`--no-llm-refs`,
        # `--parse-only`): `outcome` is recorded as `"not_attempted"` either
        # way, so `not outcome` alone cannot tell "nothing to report" apart
        # from "asked for, and never happened" — only the absence of a reason
        # can, the same distinction `_llm_reference_reading`'s `disabled_reason`
        # makes when it decides whether to fill `failure` at all
        if not resolution:
            return None  # no model reading was asked for, and nothing to report
        # ...but the ESCALATION is a second, separate call that runs whether
        # or not the reading was asked for, and a run that made it is not a
        # run where no model read the reference list. Silence here was the
        # same false negative one rung down.
        return Disclosure(
            key="reflist",
            level="info",
            token=REFLIST_TOKEN,
            text=(
                f"No reading of the whole list was taken on this run, but {REFLIST_TOKEN} "
                "to settle a disagreement. "
                + _resolution_clause(manifest, lead="Here")
            ),
            short=f"{REFLIST_TOKEN} — to settle a disagreement only",
        )
    ceiling = (
        "A model agreeing with a parse is a second reading, not confirmation: it read "
        "the same document, so a reference the layout destroyed is one it may also "
        "have missed."
    )
    # named even when the reply itself did not — see this function's own
    # docstring for why `model` alone cannot stand in for "this happened".
    # Identical wording to `cli._refs_pipeline`'s console line for the same
    # state, so the two surfaces never describe one call two different ways.
    named = model or "a model that did not report its own name"
    if outcome == "not_attempted" and inferred:
        # `outcome` here is a GUESS (see the `elif failure:` branch above), so
        # the sentence says what is recorded and no more. "no reading was
        # obtained" below is a claim, and this state has no evidence for it:
        # the manifest says a reading was asked for and stops there.
        head = (
            f"This run asked that {REFLIST_TOKEN}, and this manifest does not record "
            f"what came of it: {failure}. Nothing from such a reading reached the list "
            "below, so the reference numbering rests on the readings above this and "
            "nothing else."
        )
        short = f"{REFLIST_TOKEN}: asked for, outcome never recorded — {failure}"
        level = "warn"
    elif outcome == "not_attempted":
        head = (
            f"This run asked that {REFLIST_TOKEN}, and no reading was obtained: "
            f"{failure}. The reference numbering therefore rests on the readings above "
            f"it and nothing else."
        )
        short = f"{REFLIST_TOKEN}: asked for, not obtained — {failure}"
        level = "warn"  # a structural gap, not routine information
    elif outcome == "failed":
        # textually distinct from the branch above on purpose: this is a call
        # that WAS made and did not return, never "no reading was obtained" —
        # the two must not read as the same fact
        head = (
            f"This run asked that {REFLIST_TOKEN}, and the call did not return an "
            f"answer: {failure}. Nothing it might have proposed reached the list "
            f"below, and the reference numbering rests on the readings above this "
            f"and nothing else."
        )
        short = f"{REFLIST_TOKEN}: the call failed — {failure}"
        level = "warn"  # a call that did not return is not an aside
    elif any(n.startswith("reading discarded") for n in notes):
        # `removeprefix`, not the raw note: the note is stored WITH the
        # "reading discarded — " marker so the console can find it (it looks
        # for that same prefix), but this sentence already says "was
        # discarded rather than used" in its own words — keeping the marker
        # here reads as "discarded … reading discarded — the model's reply …",
        # the exact stutter the console strips and this branch used not to
        why = next(
            n.removeprefix("reading discarded — ")
            for n in notes if n.startswith("reading discarded")
        )
        head = (
            f"Here {REFLIST_TOKEN} ({named}), and its reading was discarded rather than "
            f"used: {why}. Nothing it proposed contributed to the list below."
        )
        short = f"{REFLIST_TOKEN} ({named}) — discarded as unusable"
        level = "info"
    else:
        if entries_proposed == 0:
            # distinct from "every value it proposed was found" below: THAT
            # sentence describes a reading that proposed something and none of
            # it was dropped, which reads as a clean corroboration — a reply
            # that proposed nothing at all corroborated nothing
            dropped = "it proposed no entries at all"
        elif notes:
            dropped = (
                f"{len(notes)} value{'' if len(notes) == 1 else 's'} it proposed "
                f"{'was' if len(notes) == 1 else 'were'} not found in the printed text and "
                f"{'was' if len(notes) == 1 else 'were'} discarded"
            )
        elif entries_proposed:
            # >0 and nothing discarded — a real, positive corroboration
            dropped = "every value it proposed was found in the printed text"
        else:
            # `entries_proposed is None`: never recorded, and `notes` empty is
            # not evidence of anything either, since a manifest that never
            # recorded the count may equally never have recorded a discard.
            # Saying nothing about the count is the honest degradation here —
            # "every value … was found" would claim corroboration this
            # manifest never measured, the same overstatement `entries_proposed
            # == 0` used to make in the other direction.
            dropped = (
                "no discarded values were recorded for it, though this manifest never "
                "recorded how many entries it proposed either"
            )
        head = (
            f"Here {REFLIST_TOKEN} ({named}), shown two extractions of the same printed "
            f"bibliography and asked what numbered list it carries; {dropped}."
        )
        if not model:
            # a reading was taken and voted — the gap is what the CLI could
            # report about it, not whether anything happened at all
            head += " The CLI did not report which model answered."
        short = f"{REFLIST_TOKEN} ({named}) — {len(notes)} discarded"
        level = "info"
    if numbering and outcome not in ("not_attempted", "failed"):
        # appended rather than folded into `dropped`: "3 values discarded" and
        # "it numbered one entry twice" are different facts, and a reader who
        # sees them as one number cannot tell which happened. Gated to the
        # states where a reply actually exists to have numbered anything —
        # `not_attempted`/`failed` provenances never carry numbering findings
        # in a live run (`ReflistProvenance()` defaults empty), but a
        # hand-built or forward-version manifest could combine "the call
        # never returned" with a numbering finding that describes a reply
        # that was never obtained.
        head += (
            " Its own numbering did not add up either — "
            + "; ".join(numbering)
            + "."
        )
    return Disclosure(
        key="reflist",
        level=level,
        token=REFLIST_TOKEN,
        # the OTHER call, appended in every branch: whatever became of the
        # reading, a run that also asked a model to settle a disagreement is
        # not a run where no model read the reference list
        text=f"{head}{resolution} {ceiling}",
        short=short,
        # both kinds, numbering first: it describes the reading as a whole, and
        # a truncated list should not lose the structural finding to six field
        # names
        rows=tuple((numbering + notes)[:6]),
    )


def _numbering_corroboration(manifest) -> Disclosure | None:
    """Fires on agreement, and deliberately NOT gated on `numbering_verified`.

    The unconfirmed-numbering warning is right to fire whenever nothing checked
    the numbering, and on a paper whose readings all agree it is also the whole
    of what the report says about the reference list — which is how it came to
    read as an alarm about the 22-vs-19 case on papers where nothing was wrong.
    This is the other half of that sentence, and it is careful not to become the
    claim the warning is about: agreement between readings is not a checked
    numbering.

    `numbering_corroborated` is a PER-LABEL fact (every cited label was
    independently agreed by >= 2 readings); `corroborating_readings` is a
    PER-READING one (this reading agreed on ALL of them, computed by
    `refs.corroborating_readings`'s intersection). The two can be true and
    short respectively: every label agreed, but no single reading spans every
    label — 0 or 1 names in the list. `NUMBERING_CORROBORATION_TOKEN`'s own
    words say "two readings … agree on every cited label", so it is asserted
    only when the list actually has two or more names to back it; the
    `len(readings) < 2` branch says the honest, weaker thing instead, under a
    DIFFERENT token, rather than stretch that token over a list that cannot
    support it (the "the readings taken" fallback used to do exactly that for
    a legitimately computed, and now reachable, empty list).
    """
    if not getattr(manifest, "numbering_corroborated", False):
        return None
    readings = list(getattr(manifest, "corroborating_readings", []) or [])
    if len(readings) < 2:
        return Disclosure(
            key="numbering_corroboration",
            level="info",
            token=NUMBERING_NO_SPANNING_READING_TOKEN,
            text=(
                f"On this run {NUMBERING_NO_SPANNING_READING_TOKEN}: each label the body "
                "cites was independently agreed by at least two readings of the "
                "bibliography, but no reading agreed on every one of them at once. That "
                "is short of corroboration, not a stronger version of it, and it does not "
                "make the numbering verified."
            ),
            short=NUMBERING_NO_SPANNING_READING_TOKEN,
        )
    named = ", ".join(readings)
    return Disclosure(
        key="numbering_corroboration",
        level="info",
        token=NUMBERING_CORROBORATION_TOKEN,
        text=(
            f"On this run {NUMBERING_CORROBORATION_TOKEN} ({named}). That is corroboration, "
            "not confirmation, and it does not make the numbering verified: these are "
            "readings of one printed page, so a reference its layout destroyed is one they "
            "can all have missed in the same way. Any label they disagreed about is listed "
            "separately, and no verdict was printed for it."
        ),
        short=f"{NUMBERING_CORROBORATION_TOKEN} ({named})",
    )


def _numbering_resolution(manifest) -> Disclosure | None:
    """Run-level surface for what `cli._escalate_disputed` settled.

    `_labels_disputed` above is the run-level roll-up of what was withheld;
    this is its counterpart for what was NOT — a reader who stops at the top
    of the report should see that some verdicts below rest on a model reading
    or a person's choice, not discover it claim by claim on only the claims
    that happen to cite a resolved label (Task 7 review, M6).
    """
    choice = getattr(manifest, "numbering_choice", "") or ""
    resolved = sorted(getattr(manifest, "labels_resolved", None) or [], key=lambda r: int(r))
    if not resolved or choice not in ("llm_resolved", "parsed", "pymupdf"):
        return None
    named = f"[{'], ['.join(resolved)}]"
    # names the model and the extractions it was SHOWN, never "both texts":
    # the call sees the readings that have a printed span, which is one on a
    # pymupdf-backend run, and the reading it ruled against may have no span
    # at all. The claim was false on the first and misleading on the second.
    model = (
        getattr(manifest, "resolution_model", "")
        or "a model that did not report its own name"
    )
    shown = _readings_phrase(getattr(manifest, "resolution_readings", []))
    by = (
        f"{model}, which read {shown} and was shown what each reading said at those "
        "labels, verified field by field against that text, and which you accepted"
        if choice == "llm_resolved"
        else f"you, who chose the `{choice}` reading whole for every disputed label"
    )
    return Disclosure(
        key="numbering_resolution",
        level="warn",
        token=NUMBERING_RESOLUTION_TOKEN,
        text=(
            f"{named} {NUMBERING_RESOLUTION_TOKEN}, settled by {by}. That is a reading, "
            "not a confirmation — the numbering stays recorded as unconfirmed for these "
            "labels, and each claim citing one carries its own note."
        ),
        short=f"{named} {NUMBERING_RESOLUTION_TOKEN}",
    )


def _label_group(labels: list[str]) -> str:
    """`["4", "5"]` -> `[4], [5]` — a citation-label group as the reader sees it."""
    return f"[{'], ['.join(labels)}]"


def _claim_numbering(claim, manifest) -> Disclosure:
    """The run-level warning, said again where the verdict is read.

    A banner at the top of a report is not where someone acting on a single
    verdict is looking. The labels are named, because the reader's next move is
    to check those specific references by hand.
    """
    doubtful = sorted(
        (r for r in claim.refs if manifest.label_is_doubtful(r)),
        key=lambda r: int(r),
    )
    labels = _label_group(doubtful)
    return Disclosure(
        key="claim_numbering",
        level="warn",
        token=CLAIM_NUMBERING_TOKEN,
        text=(
            f"This claim {CLAIM_NUMBERING_TOKEN}: {labels}. The source judged here was "
            "chosen by that label, so if the reference list is misnumbered this verdict "
            "is about a different paper. Verify the reference before relying on it."
        ),
        short=f"{CLAIM_NUMBERING_TOKEN}: {labels}",
    )


def _source_fidelity(flat: list[str], total: int) -> Disclosure:
    """Which cited sources were read as flat text, and what that costs.

    `_converter` above says how the *audited paper* was read. This says how the
    papers it was judged **against** were read, which the report never stated:
    the terminal line said it, once, and the markdown and HTML said nothing.
    A subgroup claim usually turns on a table row, and a linearized table has
    lost the row.
    """
    named = ", ".join(f"`{s}`" for s in flat)
    return Disclosure(
        key="source_fidelity",
        level="warn",
        token=SOURCE_FIDELITY_TOKEN,
        text=(
            f"{len(flat)} of {total} {SOURCE_FIDELITY_TOKEN} — {named}. Tables in "
            "those sources were linearized and their figures were invisible to "
            "the judge, so a verdict resting on one is weaker than a verdict "
            "resting on a table that was read as a table."
        ),
        short=f"{len(flat)} of {total} {SOURCE_FIDELITY_TOKEN}",
    )


def _table_loss(losses: dict[str, list[str]]) -> Disclosure:
    """Cited sources whose tables lost cells while being read.

    `_source_fidelity` above says a table was linearized; this says a table was
    read as a table and came out incomplete — the converter found cells it could
    not fit to any row or column and discarded them. A verdict resting on such a
    table rests on a table with holes in it.

    The converter's own message goes into the sentence rather than a `rows`
    detail list, because the run-level formats render `text` alone: a `rows`
    entry here would reach no reader. Each message names how many cells, of how
    many, and the grid they did not fit, so the count is the finding and is not
    paraphrased.
    """
    named = ", ".join(f"`{slug}`" for slug in sorted(losses))
    messages = [f"{slug} — {m}" for slug in sorted(losses) for m in losses[slug]]
    shown = "; ".join(messages[:_TABLE_LOSS_SHOWN])
    more = len(messages) - _TABLE_LOSS_SHOWN
    if more > 0:
        shown += f"; and {more} more (see source_table_warnings in results.json)"
    return Disclosure(
        key="table_loss",
        level="warn",
        token=TABLE_LOSS_TOKEN,
        text=(
            f"{TABLE_LOSS_TOKEN} while reading {len(losses)} cited "
            f"source{'' if len(losses) == 1 else 's'} — {named}. Those cells are "
            f"text the judge never saw, so a verdict resting on one of those "
            f"tables rests on an incomplete one. {shown}."
        ),
        short=(
            f"{TABLE_LOSS_TOKEN} — {len(losses)} source"
            f"{'' if len(losses) == 1 else 's'}"
        ),
    )


def _supplement_verification(results) -> tuple[list[str], list[str]]:
    """Supplementary documents read, split into (checked, taken on the filename)."""
    seen: dict[str, bool] = {}
    for c in results.claims:
        for j in c.judgements:
            if j.kind in ("supplement", "own_supplement"):
                seen[j.source_slug] = seen.get(j.source_slug, False) or j.verified
    return sorted(s for s, v in seen.items() if v), sorted(s for s, v in seen.items() if not v)


def _supplement_identity(checked: list[str], named: list[str]) -> Disclosure:
    """Which supplements were established to belong to their work, and which were not.

    An article is always checked against the reference that names it. A
    supplement can be checked only when its own title or DOI names the work it
    accompanies — often it does, and the publisher forms usually say
    "Supplementary Information for <title>" outright. When it does not, the file
    was attached because its NAME carried the reference's tokens, and nothing
    read it. Those are different provenances and the report states which.
    """
    n = len(checked) + len(named)
    parts = []
    if checked:
        parts.append(
            f"{len(checked)} by {'its' if len(checked) == 1 else 'their'} own title or DOI "
            f"naming that work ({', '.join(f'`{s}`' for s in checked)})"
        )
    if named:
        parts.append(
            f"{len(named)} by filename alone, which nothing checked "
            f"({', '.join(f'`{s}`' for s in named)}) — a supplement carries its own "
            "title and not the title of the article it accompanies, so the identity "
            "check that guards every cited source cannot be applied to one"
        )
    return Disclosure(
        key="supplement_identity",
        # only a guess warrants a warning; a checked attachment is information
        level="warn" if named else "info",
        token=SUPPLEMENT_IDENTITY_TOKEN,
        text=(
            f"{n} supplementary {'document was' if n == 1 else 'documents were'} read. "
            f"This is {SUPPLEMENT_IDENTITY_TOKEN}: " + "; ".join(parts) + "."
        ),
        short=f"{n} supplementary read · {SUPPLEMENT_IDENTITY_TOKEN}: "
              f"{len(checked)} checked, {len(named)} by filename",
        rows=tuple(checked + named),
    )


def _supplement_coverage() -> Disclosure:
    """The coverage audit reads the manuscript, and only the manuscript."""
    return Disclosure(
        key="supplement_coverage",
        level="info",
        token=SUPPLEMENT_COVERAGE_TOKEN,
        text=(
            f"The coverage audit reads the manuscript alone, so {SUPPLEMENT_COVERAGE_TOKEN}. "
            "A reference cited only inside supplementary material is absent from the "
            "labels below rather than reported as uncovered, and the ratio is over the "
            "main text only."
        ),
        short=SUPPLEMENT_COVERAGE_TOKEN,
    )


def _supplement_headline(claim) -> Disclosure:
    """The claim's headline came from an appendix, not the article of record."""
    d = claim.deciding_judgement()
    if d.kind == "own_supplement":
        where = "this paper's own supplementary material, not its main text"
        caveat = "which was supplied by hand and whose contents nobody checked against the claim"
    else:
        where = f"supplementary material accompanying [{d.ref}], not the article body"
        caveat = "which was attached by filename and whose identity nobody confirmed"
    return Disclosure(
        key="supplement_headline",
        level="warn",
        token=SUPPLEMENT_HEADLINE_TOKEN,
        text=(
            f"The headline above is the verdict of `{d.source_slug}` — {where}. So "
            f"{SUPPLEMENT_HEADLINE_TOKEN}, {caveat}. Read the per-document breakdown "
            "before relying on it."
        ),
        short=SUPPLEMENT_HEADLINE_TOKEN,
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
    # an empty dict means the run never recorded this, which is not the same as
    # "all of them were read flat" — a 0.4.x file must not grow a warning it
    # has no evidence for
    if recorded := (getattr(results, "source_converters", None) or {}):
        flat = sorted(s for s, c in recorded.items() if c.split()[0] == "pymupdf")
        if flat:
            out.append(_source_fidelity(flat, len(recorded)))
    # the empties are filtered BEFORE the truthiness test: `{"a": []}` is a
    # truthy dict, and it means that source was watched and lost nothing — it
    # produced a disclosure reading "0 cited sources" until this was measured.
    # An empty dict means the run did not record it, never that nothing was lost.
    losses = {
        slug: msgs
        for slug, msgs in (getattr(results, "source_table_warnings", None) or {}).items()
        if msgs
    }
    if losses:
        out.append(_table_loss(losses))
    checked_sup, named_sup = _supplement_verification(results)
    supplements = checked_sup + named_sup
    if supplements:
        out.append(_supplement_identity(checked_sup, named_sup))
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
            # gated on the audit having produced labels: on a run with no
            # citations at all there is no ratio for the blind spot to qualify
            if supplements:
                out.append(_supplement_coverage())
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
        if getattr(manifest, "references_resumed", False):
            out.append(_references_resumed(len(manifest.entries)))
        # the numbering is the join key, so an unconfirmed one outranks
        # everything above it — a reader who stops reading should have read this
        if not getattr(manifest, "numbering_verified", False):
            out.append(_numbering(manifest))
        if d := _numbering_contested(manifest):
            out.append(d)
        if d := _labels_disputed(manifest):
            out.append(d)
        if d := _numbering_resolution(manifest):
            out.append(d)
        if d := _numbering_corroboration(manifest):
            out.append(d)
        if d := _reflist(manifest):
            out.append(d)
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
    token = (MULTISOURCE_DOCUMENTS_TOKEN
             if any(j.kind != "article" for j in claim.judgements)
             else MULTISOURCE_TOKEN)
    return Disclosure(
        key="sources",
        level="warn" if s["contradicted"] else "info",
        token=token,
        text=(
            f"{s['total']} {token} for this claim: {breakdown}."
            + (" The sources disagree — each verdict below rests only on that "
               "source's own text." if split else "")
        ),
        short=f"{s['total']} {token}: {breakdown}",
        rows=tuple(
            f"{j.origin} {j.source_slug} — {j.verdict}"
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
    d = anchor_disclosure(j)
    return [d] if d else []


def _no_quote(claim) -> Disclosure:
    """This verdict was reached without the manuscript's own sentence.

    Extraction is asked for a verbatim quote every time, so an empty one means
    the model did not return it — and the judgement then rests on a paraphrase
    that may already have dropped the population, the interval or the hedging
    the verdict turns on. Weaker evidence, said so rather than left to be
    inferred from a missing blockquote: "no quote" and "quote identical to the
    paraphrase" look the same on the page otherwise.
    """
    return Disclosure(
        key="no_quote",
        level="warn",
        token=NO_QUOTE_TOKEN,
        text=(
            f"{NO_QUOTE_TOKEN} — extraction returned no verbatim sentence for this "
            "claim, so the source was checked against the short paraphrase above. "
            "Any scope, interval or hedging the paraphrase dropped was not judged."
        ),
        short=NO_QUOTE_TOKEN,
    )


def _resolution_clause(manifest, *, lead: str = " Separately, later in this run") -> str:
    """What the RESOLUTION call did, as a sentence to append — or "".

    `lead` is how the sentence opens, because the same facts are appended to
    a disclosure about the OTHER call and used as a disclosure of their own
    when there was no other call — one producer, so the two surfaces cannot
    describe one call two ways.

    Two model calls can be made in one run and they are not the same call.
    `reflist_*` describes the one that reads the whole bibliography;
    `resolution_*` describes the one a person asked for, to settle the labels
    the readings did not agree on. A `--backend pymupdf` run never makes the
    first and can still make the second, and folding them left the report
    saying no model read the reference list on a run where one was asked,
    verified field by field and allowed to change which papers are judged.
    """
    outcome = getattr(manifest, "resolution_outcome", "") or ""
    if outcome not in ("read", "failed"):
        return ""  # "" is never computed, and an absent call says nothing
    if outcome == "failed":
        why = getattr(manifest, "resolution_failure", "") or "no reason was recorded"
        return (
            f"{lead} a model was asked to settle the labels the readings did not agree "
            f"on, and that call did not return: {why}."
        )
    named = (
        getattr(manifest, "resolution_model", "")
        or "a model that did not report its own name"
    )
    shown = _readings_phrase(getattr(manifest, "resolution_readings", []))
    return (
        f"{lead} {named} was asked to settle the labels the readings did not agree on, "
        f"shown {shown}; what it settled is recorded with the numbering resolution."
    )


def _readings_phrase(readings) -> str:
    """The extractions a model call was actually shown, named.

    Never "both texts". The resolution call is shown the readings that have a
    printed span of the page, which is TWO on a docling run and ONE on a
    pymupdf one — and the reading it is ruling against can be the deposit or
    the model's own, neither of which has a span at all. One phrase, used by
    the note and by the report, so the two cannot describe one call two ways.
    """
    names = [n for n in (readings or []) if n]
    if not names:
        return "text this manifest does not name"
    if len(names) == 1:
        return f"the {names[0]} extraction"
    return f"the {' and '.join(names)} extractions"


def _was_obtained(manifest, label: str) -> bool:
    """Did this manifest actually retrieve a judgeable source for this label?

    The same predicate `check.py`'s retrieval filter applies before it
    partitions the withheld from the judged, restated here rather than
    inferred from the emptiness of `withheld_refs` — an empty list is also
    what a `results.json` written before that field existed carries.
    """
    e = next((x for x in getattr(manifest, "entries", []) if x.num == label), None)
    return bool(e and e.status in ("retrieved", "provided") and e.slug)


def _claim_pairing(claim, manifest=None) -> Disclosure | None:
    """Which pairing state this claim's cited labels are in, if not a clean one.

    Five states, five tokens — the same one-key-many-tokens shape `anchor`
    uses. `withheld` reads `claim.withheld_refs` and fires with no `manifest`
    at all (as it always has); the other four read the manifest's escalation
    fields and are silent without one. `corroborated` is the only `info`-level
    state, and `claim_disclosures` gates it on the claim already carrying a
    `warn`: reassurance repeated on every clean claim is noise a reader stops
    reading.

    A disputed label is in exactly one of two of those states, and the split
    is the manifest entry's own status: withheld (a source was retrieved and
    set aside) or unretrieved (there is no source at all). Both withhold a
    verdict; only one of them has a fetched paper to warn about.
    """
    cited = set(claim.refs)
    disputed = sorted(cited & set(getattr(manifest, "labels_disputed", None) or []), key=int)
    resolved = sorted(cited & set(getattr(manifest, "labels_resolved", None) or []), key=int)
    # A disputed label splits in two, and `check.py` already computed the
    # split once: a label whose source WAS obtained is in `withheld_refs`; one
    # that was never retrieved is deliberately kept out of it
    # (`check.py`'s retrieval filter, and the test that pins it). Reading only
    # `labels_disputed` when `withheld_refs` is empty undid that care and told
    # a reader "the source fetched under that label" about a source nobody
    # fetched. The entry's own status is what decides, NOT the emptiness of
    # `withheld_refs`: a `results.json` written before that field existed
    # carries `[]` for every claim, and choosing the never-retrieved wording
    # from that emptiness would be the same mistake in the other direction.
    unretrieved = [r for r in disputed if not _was_obtained(manifest, r)]
    withheld = sorted(set(claim.withheld_refs) | (set(disputed) - set(unretrieved)), key=int)
    withheld = [r for r in withheld if r in cited]

    if withheld:
        labels = _label_group(withheld)
        one = len(withheld) == 1
        text = (
            f"This claim cites {labels}, and the {CLAIM_PAIRING_WITHHELD_TOKEN} about "
            f"{'that label' if one else 'those labels'}: the readings of the "
            "bibliography did not agree on which paper it names — they either named "
            "different papers or left nothing that could be compared — so the source "
            "retrieved under that label may not be the paper the manuscript actually "
            f"cites. No verdict was reached on {'it' if one else 'them'} — check the "
            "retrieval manifest before treating this claim as checked."
        )
        if unretrieved:
            # named separately, never folded into the sentence above: nothing
            # was fetched under these, so "the source retrieved under that
            # label" is not true of them and the reader must not read one
            # sentence as covering both
            text += (
                f" It also cites {_label_group(unretrieved)}, where the readings did "
                "not agree either and the source was never retrieved, so nothing was "
                "judged under that label at all."
            )
        if resolved:
            # This claim cites BOTH a still-disputed label and one that WAS
            # resolved — the withheld state is the more urgent finding and
            # keeps the token/level, but a reader who stops here must not be
            # left thinking every citation on this claim shares the same fate
            # (m6 of the Task 7 review): the other label's verdict rests on a
            # resolution, not a withholding, and that is a different caveat.
            text += (
                f" This claim also cites {_label_group(resolved)}, where the readings "
                "disagreed too but a resolution was accepted for it — see that label's "
                "own reason in the retrieval manifest."
            )
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_WITHHELD_TOKEN,
            text=text,
            short=f"{labels} {CLAIM_PAIRING_WITHHELD_TOKEN}",
        )

    if unretrieved:
        # Nothing was withheld here, because nothing was obtained to withhold.
        # Its own token and its own sentence: the withheld one asserts a
        # source was retrieved under the label, which is the one thing that
        # did not happen.
        labels = _label_group(unretrieved)
        one = len(unretrieved) == 1
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_UNRETRIEVED_TOKEN,
            text=(
                f"This claim cites {labels}, and {CLAIM_PAIRING_UNRETRIEVED_TOKEN}: the "
                "readings of the bibliography did not agree on which paper "
                f"{'it names' if one else 'they name'}, and the source was never "
                "retrieved either, so nothing was judged under "
                f"{'that label' if one else 'those labels'} in either direction. The "
                "disagreement is recorded, not resolved."
            ),
            short=f"{labels} {CLAIM_PAIRING_UNRETRIEVED_TOKEN}",
        )

    if resolved and manifest is not None and manifest.numbering_choice == "llm_resolved":
        labels = _label_group(resolved)
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_RESOLVED_TOKEN,
            text=(
                f"This claim cites {labels}, {CLAIM_PAIRING_RESOLVED_TOKEN}. The readings "
                "of the bibliography did not agree there. A model was shown "
                f"{_readings_phrase(getattr(manifest, 'resolution_readings', []))} and "
                "what each reading said at that label, every field of its answer was "
                "found verbatim in that printed text, and you accepted the result — so "
                "this verdict rests on a reading nobody checked against the printed "
                "page, and you are who accepted it. The numbering is still recorded as "
                "unconfirmed."
            ),
            short=f"{labels} {CLAIM_PAIRING_RESOLVED_TOKEN}",
        )

    if resolved and manifest is not None and manifest.numbering_choice in ("parsed", "pymupdf"):
        labels = _label_group(resolved)
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_CHOSEN_TOKEN,
            text=(
                f"This claim cites {labels}. Two readings of the bibliography disagreed "
                f"there and {CLAIM_PAIRING_CHOSEN_TOKEN} — the `{manifest.numbering_choice}` "
                "one, for every disputed label. That is an assertion about which reading "
                "is right, not a check of it, and this verdict is about whichever paper "
                "that reading names."
            ),
            short=f"{labels} {CLAIM_PAIRING_CHOSEN_TOKEN} ({manifest.numbering_choice})",
        )

    # `info`, and gated by the caller on this claim already carrying a `warn`:
    # corroboration is reassurance, and reassurance on a clean claim is noise
    if manifest is not None and getattr(manifest, "numbering_corroborated", False) and cited:
        labels = _label_group(sorted(cited, key=int))
        readings = ", ".join(manifest.corroborating_readings)
        return Disclosure(
            key="claim_pairing",
            level="info",
            token=CLAIM_PAIRING_CORROBORATED_TOKEN,
            text=(
                f"{labels} was {CLAIM_PAIRING_CORROBORATED_TOKEN} ({readings}). That is "
                "evidence for this pairing, not confirmation of it — every reading read "
                "the same document, so a reference the layout destroyed is one they may "
                "all have missed."
            ),
            short=f"{labels} {CLAIM_PAIRING_CORROBORATED_TOKEN}",
        )
    return None


def claim_disclosures(claim, manifest=None) -> list[Disclosure]:
    """Every claim-level disclosure this ClaimResult owes its reader.

    `manifest` is optional because most callers have no reason to hold one, and
    every disclosure that does not depend on it must keep firing without it.
    `report.py` binds it once so the templates keep their one-argument call.
    """
    out: list[Disclosure] = []
    if claim.is_multi_source():
        out.append(_sources(claim))
    if claim.unjudged_refs:
        out.append(_unjudged(claim))
    # only where a judgement actually happened: nothing read an unretrieved
    # source, so the quote changed nothing there and the notice would land on
    # every row of the gap register until readers stopped seeing it
    if not claim.quote and claim.verdict in JUDGMENT_VERDICTS:
        out.append(_no_quote(claim))
    # the headline is an appendix's word, not the article's. A run-level note
    # that supplements were read does not tell a reader that THIS verdict is
    # one of them, and the headline is what most readers act on
    _d = claim.deciding_judgement()
    if _d is not None and _d.kind != "article" and claim.verdict in JUDGMENT_VERDICTS:
        out.append(_supplement_headline(claim))
    if manifest is not None and any(manifest.label_is_doubtful(r) for r in claim.refs):
        out.append(_claim_numbering(claim, manifest))
    # `claim_pairing` at info level (corroboration) is reassurance, and
    # reassurance on a claim with nothing else to say about it is noise on
    # every row of the report — so it appends only beside an existing `warn`.
    # A `warn`-level pairing disclosure is unconditional, `manifest` or not:
    # `withheld` reads only `claim.withheld_refs`.
    if (d := _claim_pairing(claim, manifest)) is not None:
        if d.level == "warn" or any(x.level == "warn" for x in out):
            out.append(d)
    if (d := anchor_disclosure(claim)) is not None:
        out.append(d)
    return out
