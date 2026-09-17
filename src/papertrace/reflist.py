"""One model's reading of a printed bibliography, verified field by field.

A model cannot introduce a paper here, only agree with a converter about one:
every value it proposes is searched for, verbatim, in one of the two extractions
it was shown, and a value that is not found is discarded. What survives is the
page's own ink re-assembled, not the model's assertion. The ceiling is the same
one either converter has — it read the same document, so a reference the layout
destroyed is one it may also have missed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import ask
from .models import RefEntry, _fold
from .refs import DOI_RE

REFLIST_PROMPT = """You are the reference-list reading step of a peer-review fact-checker.

Below are TWO extractions of the SAME printed bibliography, out of the same PDF,
made by two different converters (<<A_LABEL>> and <<B_LABEL>>). Either one may
have merged two references into one, split one across two, lost a numeral, or
broken a DOI across a line. Your job is to say what numbered list the page
actually carries.

Rules:
1. COPY, do not compose. Every value you return must appear as a substring of
   TEXT A or TEXT B. Do not translate, do not expand an abbreviation, do not
   fill in a journal name you recognise, do not correct a spelling, and do not
   add an author the text does not print.
2. `num` is the numeral PRINTED for that entry, copied as a string — NOT its
   position in your list. If the page prints 1, 2, 3, 5 then the fourth entry's
   `num` is "5". Never renumber it to "4".
3. Never repair a DOI. If a DOI is broken across a line, truncated, or you
   cannot see all of it, return null for it. Half a DOI names a different paper.
4. `reading` is "A" when the entry is legible only in TEXT A, "B" when only in
   TEXT B, "AB" when both carry it. Those three are the only answers; anything
   else is dropped.
5. Do not merge two references into one entry and do not split one across two.
   Where the two texts disagree about where an entry begins, follow the printed
   NUMERALS; if you still cannot tell, OMIT the entry rather than guess at it.
6. Omit any field you cannot copy. An omitted field is a recorded gap; an
   invented one is a wrong paper.
7. If the text below is not a reference list at all, answer with [].

Your reply is checked field by field against TEXT A and TEXT B before any of it
is used. A value not found verbatim in one of them is discarded, and an entry
whose title is not found discards the entire reply. Copying is the only way to
be believed, and guessing costs you every entry, not just the one.

Answer with ONLY a JSON array, no prose, no code fences:
[{"num":"6","authors":"Fujita S, Mori S, Onda K, et al","year":"2023",
  "title":"Characterization of brain volume changes in aging individuals",
  "journal":"JAMA Netw Open. 6(6):e2318153",
  "doi":"10.1001/jamanetworkopen.2023.18153","reading":"A"}]

TEXT A (<<A_LABEL>>):
<<A>>

TEXT B (<<B_LABEL>>):
<<B>>
"""

# ---------------------------------------------------------------------------
# the two normalisation layers, and what each field is allowed to be folded by
# ---------------------------------------------------------------------------

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_PUNCT = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}
_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")


def _layer_e(s: str, *, dehyphenate: bool = True) -> str:
    """Undo what the extractor did to the ink, and nothing else.

    Applied to BOTH sides of every comparison, which is what makes it safe: a
    normalisation applied to one side only invents matches, and folding one side
    of a substring test is exactly the bug Plan A had to fix in
    `_title_check_text`.
    """
    for src, dst in {**_LIGATURES, **_PUNCT}.items():
        s = s.replace(src, dst)
    if dehyphenate:
        s = _HYPHEN_BREAK.sub(r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()


# Layer O is `models._fold`, which already lowercases, transliterates
# `ß ø æ œ đ ð þ ł ı ħ ŧ` and decomposes combining marks. Authors and titles get
# it; a DOI, year or journal string does not — folding a DOI would make two
# different registrants compare equal.
_RULES = {
    "authors": lambda s: _fold(_layer_e(s)),
    "title": lambda s: _fold(_layer_e(s)),
    "year": lambda s: _layer_e(s),
    "journal": lambda s: _layer_e(s),
    "doi": lambda s: _layer_e(s, dehyphenate=False).lower(),
}


def _found(field_name: str, value: str, readings: tuple[str, ...]) -> bool:
    """Is this value printed in one of the texts the model was shown?"""
    norm = _RULES[field_name]
    needle = norm(value)
    return bool(needle) and any(needle in norm(r) for r in readings)


def _usable_doi(raw: str) -> str | None:
    """A DOI shaped like a whole one, or None — never a repair.

    `DOI_RE` accepts a trailing hyphen, so `10.1038/s41591-` (a DOI the
    extractor broke across a line) matches it, and the verbatim check passes
    too because that prefix really is printed on the page. Requiring the last
    character to be alphanumeric is what tells a whole DOI from half of one.
    """
    s = _layer_e(raw, dehyphenate=False).strip().rstrip(".,;)]")
    if not DOI_RE.fullmatch(s) or not s[-1:].isalnum():
        return None
    return s


@dataclass
class ReflistProvenance:
    """What the model reading cost, kept, and threw away — the report's record."""

    model: str = ""
    entries_proposed: int = 0
    fields_discarded: list[str] = field(default_factory=list)
    # Kept apart from `fields_discarded` on purpose. A discarded field is "this
    # value was not printed, so it was dropped"; a numbering finding is "this
    # reading's labels do not add up", which the spec gives a different
    # consequence — it marks the candidate as not covering, not as missing a
    # value. One list holding both made `reflist_fields_discarded` report a
    # count of discarded fields that included things no field ever lost.
    #
    # There is deliberately no `covers` flag: "not covering" needs no separate
    # signal because `label_agreement` already enforces it, refusing to let a
    # reading speak for a label it carries twice, and simply not seeing a label
    # the reading never carried. A flag would be a published field with no
    # consumer.
    numbering_findings: list[str] = field(default_factory=list)
    # non-empty means the whole reading was refused, and says why. It is never
    # "the model agreed": a reading that could not be checked is not a reading.
    # ALWAYS set after a reply was actually obtained (`outcome == "read"`) —
    # never before the call, and never for a call that did not return one.
    discarded_whole: str = ""
    readings: list[str] = field(default_factory=list)
    # Three states, not a boolean paired with a string — a boolean asked to
    # discriminate three outcomes (never called; called and failed; called and
    # answered) sends a failed call down the same branch as a successful one
    # whenever the failure happens to leave no name attached, which is exactly
    # the regression a two-valued `attempted` produced. Set by `propose` to
    # `"read"` only once a reply was actually obtained (never derived from
    # `model`: `ask._ask` records a name only when the subprocess's own JSON
    # reports one, so a call that answered and verified cleanly can still
    # leave `model == ""`). Set by `_llm_reference_reading` to `"failed"` in
    # its `except` branch. Left at the default for every case where no
    # subprocess call was ever made — disabled, `--parse-only`, the backend
    # leaving no second reading, or `claude` not on PATH.
    outcome: str = "not_attempted"
    # Why there is no reading, when `outcome` is not `"read"` — never a value
    # that failed verification (that is `fields_discarded`'s job) and never a
    # reading that was checked and refused (that is `discarded_whole`'s job,
    # and requires a reply to have been checked at all). This is the one slot
    # for "why is there nothing to show", whether nothing was ever attempted
    # or an attempt was made and did not return.
    failure: str = ""


# published so a consumer has something to check membership against, and so a
# future fifth branch of "why there is no reading" cannot be added as a bare
# string nobody enumerated — the same discipline as `LABEL_AGREEMENT`
REFLIST_OUTCOMES = ("not_attempted", "failed", "read")


# order matters only for `raw` below, which is re-assembled in printed order
_VERIFIED_FIELDS = ("authors", "year", "title", "journal", "doi")

# the only three answers the prompt allows for `reading` (rule 4) — anything
# else, including a substring match on a stray "a" or "b" in a sentence, is
# not one of the answers the model was given and must be dropped, not guessed
_READING_LETTERS = ("A", "B", "AB")


def propose(
    reading_a: str,
    reading_b: str,
    *,
    label_a: str,
    label_b: str,
    model: str | None = None,
) -> tuple[list[RefEntry], ReflistProvenance]:
    """The numbered list a model says these two extractions carry, verified.

    Returns an empty candidate and a stated reason for every failure it can
    meet — an unusable reply, an unverifiable title, no text to check against.
    The refs stage has three other readings to proceed on, so nothing here
    raises on the model's behalf. A failure of the *call itself* does propagate:
    `cli` must be able to tell "the model was asked and answered nonsense" from
    "the model could not be asked", and two different reasons reach the report.
    """
    prov = ReflistProvenance(readings=[label_a, label_b])
    readings = tuple(r for r in (reading_a, reading_b) if r.strip())
    if len(readings) < 2:
        # one text is half the verification at full price, and no way to say so
        # in the report — a value found in the only reading available has been
        # checked against nothing but itself. `outcome` stays "not_attempted":
        # no subprocess call happens on this path at all, so nothing was
        # "checked and refused" (`discarded_whole`'s meaning) — the reason
        # belongs in `failure`, the same slot a disabled or claude-absent call
        # uses for "why there is no reading"
        prov.failure = (
            "only one of the two extractions of the bibliography had any text, so "
            "nothing the model proposed could have been checked against a second reading"
        )
        return [], prov

    # labels before texts: a label is a short fixed string ("pymupdf",
    # "docling 2.8.0") and cannot contain a placeholder, while page text
    # conceivably could
    prompt = (
        REFLIST_PROMPT
        .replace("<<A_LABEL>>", label_a)
        .replace("<<B_LABEL>>", label_b)
        .replace("<<A>>", reading_a)
        .replace("<<B>>", reading_b)
    )
    # Qualified on purpose. `check.py` must call `_ask` by bare name because 62
    # tests patch `check._ask`; this module has no such sites and its tests patch
    # `ask._ask`, which only a qualified call sees.
    with ask.for_site(ask.SITE_REFS):
        raw = ask._ask(prompt, model)
    # a reply was obtained — everything from here on describes what came back,
    # never whether anything came back at all. If `ask._ask` raised instead,
    # this line is never reached and `prov` (with `outcome` still at its
    # "not_attempted" default) is never returned — `_llm_reference_reading`'s
    # `except` builds a fresh one with `outcome="failed"` instead.
    prov.outcome = "read"
    prov.model = ask.model_for(ask.SITE_REFS) or ""

    try:
        proposed = ask._parse_json_array(raw)
    except ValueError as e:
        prov.discarded_whole = f"the model's reply was not a JSON array ({str(e)[:120]})"
        return [], prov
    if not isinstance(proposed, list):
        prov.discarded_whole = "the model's reply was not a JSON array"
        return [], prov
    prov.entries_proposed = len(proposed)

    out: list[RefEntry] = []
    for i, item in enumerate(proposed, start=1):
        if not isinstance(item, dict):
            prov.fields_discarded.append(f"entry {i}: not a JSON object")
            continue
        # NOT verbatim-checked, deliberately: "6" is a substring of almost any
        # bibliography, so searching for it is not a check. What constrains a
        # numeral is rule 3 below (uniqueness and coverage) and the fact that the
        # entry dies entirely if its title is not printed.
        num = _layer_e(str(item.get("num") or "")).strip()
        if not num:
            prov.fields_discarded.append(f"entry {i}: no printed numeral")
            continue

        # Rule 4, before anything is kept: a title nobody printed is the one
        # failure that cannot be localised to a field. Every other field
        # describes a paper; the title IS the paper. A reply that named a work
        # the page does not carry has demonstrated invention, and the entries
        # beside it are not worth more for being next to it.
        title = item.get("title")
        if isinstance(title, str) and title.strip() and not _found("title", title, readings):
            prov.fields_discarded.append(f"[{num}] title")
            prov.discarded_whole = (
                f"the title proposed for [{num}] was not found in either reading of the "
                "bibliography, so the model's whole reading was discarded rather than "
                "used — it named a paper the page does not print"
            )
            return [], prov

        kept: dict[str, str] = {}
        for name in _VERIFIED_FIELDS:
            value = item.get(name)
            if not isinstance(value, str) or not value.strip():
                continue  # an omitted field is a gap the model was told to leave
            if name == "doi":
                doi = _usable_doi(value)
                if doi is None or not _found("doi", doi, readings):
                    # a recorded gap: the entry will resolve `no_doi` and say so,
                    # which is what a broken DOI actually leaves behind
                    prov.fields_discarded.append(f"[{num}] doi")
                    continue
                kept["doi"] = doi
                continue
            if not _found(name, value, readings):
                prov.fields_discarded.append(f"[{num}] {name}")
                continue
            kept[name] = _layer_e(value)

        # Translated here, not stored as the letters: `seen_in` is a published
        # field whose vocabulary is reading NAMES, and the letters exist only
        # inside the prompt. An unrecognised answer yields nothing rather than
        # defaulting to A — unknown provenance is recorded as unknown.
        #
        # Exact match against the three answers the prompt allows, never a
        # substring test: "whichever one was clearer".upper() contains a stray
        # "A" (from "WAS") that a substring check would mistake for the letter
        # "A" — landing a sentence the model wrote in a published field whose
        # only legal values are "A", "B" and "AB".
        said = item.get("reading")
        if said is not None and not isinstance(said, str):
            # a reply of `5` or `{"text": "A"}` is a wrong SHAPE, which is a
            # different report from a field the model simply left out
            prov.fields_discarded.append(f"[{num}] reading: not a string")
            said_norm = ""
        else:
            said_norm = said.strip().upper() if isinstance(said, str) else ""
        letters = list(said_norm) if said_norm in _READING_LETTERS else []
        seen_in = [{"A": label_a, "B": label_b}[c] for c in letters]
        if said_norm and not letters:
            prov.fields_discarded.append(f"[{num}] reading")

        # `raw` is COMPOSED from the verified fields rather than copied from the
        # reply — and that is legitimate, because every piece of it was found
        # verbatim in a text the model was shown. It has to be composed: the
        # reply carries no single printed string, and `_same_work` and
        # `_title_tokens` read `raw` to decide whether two readings name one
        # paper. Composing it from unverified values would be the invention this
        # module refuses; composing it from verified ones is re-assembly.
        # An entry that names no paper is not an entry. The whole-candidate
        # discard above fires only when a title was PROPOSED and failed; a reply
        # supplying a numeral and nothing else never reaches it, and would
        # otherwise become a voter carrying no claim about any work. That voter
        # cannot produce a wrong verdict — `label_agreement` compares through
        # `_comparably_same`, which refuses to call two uncomparable entries
        # `agreed` — but it can make a label `disputed` and withhold verdicts
        # the model never said anything against. A degenerate reply may cost
        # this reading its vote; it may not cost the audit its answers.
        if "title" not in kept and "doi" not in kept:
            prov.fields_discarded.append(f"[{num}]: no verified title or doi, so no entry")
            continue

        parts = [kept[k] for k in ("authors", "year", "title", "journal") if k in kept]
        raw_text = ". ".join(parts)
        if "doi" in kept:
            raw_text = f"{raw_text} doi:{kept['doi']}".strip()
        out.append(
            RefEntry(
                num=num,
                raw=raw_text,
                doi=kept.get("doi"),
                title=kept.get("title"),
                year=kept.get("year"),
                # `status` keeps its default. This entry is a voter and nothing
                # else: it never reaches `reconcile`'s arguments or
                # `resolve_all`, so no status of it is ever acted on, and adding
                # a `pending` to REF_STATUSES would publish a vocabulary entry
                # no reader ever sees.
                reason="read from the printed list by a model; a voter only, never resolved",
                seen_in=seen_in,
            )
        )

    # Rule 3. A numeral proposed twice, or missing from 1..max, means this
    # reading cannot say which paper that label is — which is the question. It
    # is RECORDED, and nothing is renumbered, merged or dropped to tidy it:
    # `label_agreement` refuses to let a reading speak for a label it carries
    # twice, and `_covers` fails on the gap. Repairing it here would hand the
    # arbiter a list that had already guessed.
    nums = [e.num for e in out]
    twice = sorted({n for n in nums if nums.count(n) > 1}, key=_label_key)
    if twice:
        prov.numbering_findings.append("numerals proposed twice: " + ", ".join(twice))
    if gaps := _numeral_gaps(nums):
        prov.numbering_findings.append("numerals absent from 1..max: " + ", ".join(gaps))
    return out, prov


def _label_key(label: str) -> tuple[int, int, str]:
    """Numeric labels in numeric order, anything else after them, by name."""
    return (0, int(label), "") if label.isdigit() else (1, 0, label)


def _numeral_gaps(nums: list[str]) -> list[str]:
    """Numerals `1..max` that this reading carries no entry for.

    Non-numeric numerals are not counted and not repaired: a list printing `1a`
    has an extent this cannot measure, and guessing at one is how a reading
    comes to claim coverage it does not have.
    """
    numeric = sorted({int(n) for n in nums if n.isdigit()})
    if not numeric:
        return []
    present = set(numeric)
    return [str(n) for n in range(1, max(numeric) + 1) if n not in present]
