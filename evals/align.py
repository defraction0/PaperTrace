"""Match gold cases to the ClaimResults a run produced.

Neither side offers a stable key. Claim ids are assigned by the model in
reading order, and the extraction prompt asks for a *tight paraphrase*, not the
verbatim sentence — so ids drift between runs and text never matches exactly.

Alignment is therefore a **global score-sorted assignment**, not a per-case
greedy walk. Every candidate (gold case, prediction) pair is scored, the whole
candidate set is sorted by `(-score, case_id, pred_id)`, and pairs are assigned
best-first, skipping any pair whose case or prediction has already been
consumed. That total order does not mention input order, so permuting either
list cannot change the result — a property asserted by
`test_alignment_is_identical_under_permutation_of_both_lists` rather than
merely claimed here, because the previous version of this docstring claimed it
and it was false.

Two passes run in sequence — all exact candidates, then all similarity
candidates — so an exact match is never contested by a similarity candidate
that happens to score 1.0 on text while disagreeing on labels.

**The margin has two directions.** A per-case aligner compares the best
prediction against the runner-up *for that case*: a row comparison. Under
global assignment the unit is the edge, which can be a coin flip two ways — a
row rival (same case, another prediction) and a **column** rival (same
prediction, another case). Sorting globally removes the order-dependence but
would still award a column contest on a margin the harness already refuses to
act on in the row direction. So the competitor is the highest-scoring *live*
candidate sharing **either** endpoint, recomputed at pop time, and assignment
requires `score - competitor >= MATCH_MIN_MARGIN`. A contest freezes its cases
(reported ambiguous) and leaves its predictions free.

A prediction is consumed once, so matching is strictly one-to-one.

The aligner is itself fuzzy, and its mistakes are indistinguishable from model
mistakes in the final score. That cannot be engineered away at this scale, so
instead every match records the stage and score that produced it, and the
report prints the alignment table *before* any metric.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .tool_coverage import missing_labels

MATCH_MIN_RATIO = 0.60
MATCH_MIN_MARGIN = 0.10

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def normalize_claim(text: str) -> str:
    """Fold away the differences that are never semantic: unicode form, case,
    dash and quote variants, markdown emphasis, whitespace, trailing period."""
    t = unicodedata.normalize("NFKC", text or "").translate({**_DASHES, **_QUOTES})
    t = re.sub(r"[*_`]", "", t).casefold()
    t = " ".join(t.split())
    return t.rstrip(".").strip()


@dataclass(frozen=True)
class Match:
    case_id: str
    pred_id: int | None
    stage: str  # exact | similarity | discriminator | unmatched | ambiguous
    score: float
    reason: str = ""


@dataclass
class Alignment:
    matches: list[Match] = field(default_factory=list)
    unmatched_gold: list[tuple[str, str]] = field(default_factory=list)
    unmatched_predictions: list[int] = field(default_factory=list)
    ambiguous: list[tuple[str, list[int]]] = field(default_factory=list)

    @property
    def fuzzy_fraction(self) -> float:
        made = [m for m in self.matches if m.pred_id is not None]
        if not made:
            return 0.0
        return sum(1 for m in made if m.stage != "exact") / len(made)

    def to_dict(self) -> dict:
        made = [m for m in self.matches if m.pred_id is not None]
        return {
            "matched": len(made),
            "exact": sum(1 for m in made if m.stage == "exact"),
            "similarity": sum(1 for m in made if m.stage == "similarity"),
            "discriminator": sum(1 for m in made if m.stage == "discriminator"),
            "fuzzy_fraction": self.fuzzy_fraction,
            "unmatched_gold": [{"case_id": c, "reason": r} for c, r in self.unmatched_gold],
            "unmatched_predictions": self.unmatched_predictions,
            "ambiguous": [{"case_id": c, "candidates": ids} for c, ids in self.ambiguous],
            "detail": [
                {"case_id": m.case_id, "pred_id": m.pred_id, "stage": m.stage,
                 "score": round(m.score, 3), "reason": m.reason}
                for m in self.matches
            ],
        }


def _labels(x) -> set[str]:
    return set(x if isinstance(x, list) else [])


def _keys_allow(case: dict, text: str) -> bool:
    keys = case.get("match_keys") or {}
    n = normalize_claim(text)
    need = keys.get("must_contain_any")
    if need and not any(normalize_claim(k) in n for k in need):
        return False
    for bad in keys.get("must_not_contain", []):
        if normalize_claim(bad) in n:
            return False
    return True


def align(gold: dict, results, min_ratio: float = MATCH_MIN_RATIO,
          min_margin: float = MATCH_MIN_MARGIN) -> Alignment:
    cases = gold["cases"]
    by_case = {c["case_id"]: c for c in cases}
    # a dict comprehension over claim ids silently keeps the LAST duplicate, so
    # permuting the prediction list changed which one was graded — the one place
    # this module's order-independence contract did not hold. Refused, not
    # repaired: the harness cannot know which of two same-id claims was meant.
    counts = Counter(c.id for c in results.claims)
    if clashes := sorted(i for i, n in counts.items() if n > 1):
        raise ValueError(
            f"duplicate prediction id(s) in results.json: "
            f"{', '.join(str(i) for i in clashes)}. Claim ids must be unique — "
            f"alignment consumes each prediction once, and with a duplicate the "
            f"input order would decide which one is graded."
        )
    pool = {c.id: c for c in results.claims}
    a = Alignment()

    matched: dict[str, int] = {}
    frozen: dict[str, set[int]] = {}       # case_id -> the candidates it was tied between
    used: set[int] = set()

    def case_free(cid: str) -> bool:
        return cid not in matched and cid not in frozen

    def assign(order: list[tuple[float, str, int]], stage: str, floor: float) -> None:
        """Assign best-first over a globally sorted candidate list.

        `order` is sorted by (-score, case_id, pred_id) — a total order that
        never mentions the position of a case or prediction in its input list.
        """
        for score, cid, pid in order:
            if not case_free(cid) or pid in used or score < floor:
                continue

            # the competitor is the best LIVE candidate sharing either endpoint.
            # Live is recomputed here, not precomputed: an earlier assignment may
            # already have removed the rival that made this pair look contested.
            # Sub-floor candidates stay in the rival set — a near-tie below the
            # floor is still a reason not to trust the winner.
            rivals = [
                t for t in order
                if (t[1] == cid) != (t[2] == pid)
                and case_free(t[1]) and t[2] not in used
            ]
            competitor = max((t[0] for t in rivals), default=0.0)
            if score - competitor >= min_margin:
                matched[cid] = pid
                used.add(pid)
                a.matches.append(Match(cid, pid, stage, score))
                continue

            # contested: the popped edge plus every live rival inside the margin
            contest = [(score, cid, pid)]
            contest += [t for t in rivals if score - t[0] < min_margin]

            # stage 3 — let the human-authored discriminators decide. In a row
            # contest this filters predictions against one case's match_keys; in
            # a column contest it filters cases against one prediction's text.
            survivors = [t for t in contest if _keys_allow(by_case[t[1]], pool[t[2]].claim)]
            if len(survivors) == 1:
                s_score, s_cid, s_pid = survivors[0]
                matched[s_cid] = s_pid
                used.add(s_pid)
                # the ASSIGNED pair's score, not the rival's — the previous
                # version recorded the candidate the discriminator threw out
                a.matches.append(Match(s_cid, s_pid, "discriminator", s_score))
                continue

            # refuse. Freeze every case in the contest — freezing only the popped
            # one would let its column rival walk in unopposed on the next pop,
            # which is the guess we just declined to make. The predictions stay
            # free, as they were before global assignment.
            for _, c_id, p_id in contest:
                frozen.setdefault(c_id, set()).add(p_id)

    # pass 1 — exact on normalized text AND identical label set
    exact: list[tuple[float, str, int]] = []
    for case in cases:
        want = normalize_claim(case["claim_text"])
        want_labels = set(case["citation_labels"])
        for pid, pred in pool.items():
            if normalize_claim(pred.claim) == want and _labels(pred.refs) == want_labels:
                exact.append((1.0, case["case_id"], pid))
    assign(sorted(exact, key=lambda t: (-t[0], t[1], t[2])), "exact", 0.0)

    # pass 2 — shared citation label, then text similarity with a two-way margin
    similar: list[tuple[float, str, int]] = []
    for case in cases:
        cid = case["case_id"]
        if not case_free(cid):
            continue
        want = normalize_claim(case["claim_text"])
        want_labels = set(case["citation_labels"])
        for pid, pred in pool.items():
            if pid in used or not (_labels(pred.refs) & want_labels):
                continue
            ratio = SequenceMatcher(None, want, normalize_claim(pred.claim)).ratio()
            similar.append((ratio, cid, pid))
    assign(sorted(similar, key=lambda t: (-t[0], t[1], t[2])), "similarity", min_ratio)

    a.ambiguous = sorted((cid, sorted(pids)) for cid, pids in frozen.items())

    # nothing is dropped silently: every gold case gets a row either way.
    #
    # `labels_partially_covered` joins the gap set, and the direction matters:
    # a gold case sitting on the SECOND occurrence of a label the tool reached
    # once was blamed on this matcher, because label-level coverage called the
    # label covered. Occurrence data moves that blame onto the tool, where it
    # belongs. Every change here must move blame toward the tool, never away.
    coverage = results.coverage or {}
    missing, shape_error = missing_labels(coverage.get("missing"))
    partial, partial_error = missing_labels(coverage.get("labels_partially_covered"))
    missing |= partial
    shape_error = shape_error or partial_error
    for case in cases:
        cid = case["case_id"]
        if cid in matched:
            continue
        if cid in frozen:
            reason = "ambiguous — add match_keys.must_contain_any to this case"
        elif shape_error:
            # the tool's audit is unreadable, so we cannot say whether the tool
            # missed this label or our matcher did. Defaulting to
            # alignment_failure_or_paraphrase_drift would move the blame off the
            # tool and onto the harness grading it, silently and in one
            # direction — the exact failure this reason exists to prevent.
            reason = ("attribution_undetermined — the tool's coverage audit "
                      f"could not be read: {shape_error}")
        elif set(case["citation_labels"]) & missing:
            # the tool's own audit already flagged this label as unreached
            reason = "extraction_gap"
        else:
            reason = "alignment_failure_or_paraphrase_drift"
        a.unmatched_gold.append((cid, reason))
        a.matches.append(Match(cid, None, "unmatched", 0.0, reason))

    # canonical ordering, so two runs over permuted inputs are equal as objects
    a.matches.sort(key=lambda m: m.case_id)
    a.unmatched_gold.sort()
    a.unmatched_predictions = sorted(set(pool) - used)
    return a


def matched_pairs(gold: dict, results, alignment: Alignment) -> list[tuple[dict, object]]:
    by_id = {c["case_id"]: c for c in gold["cases"]}
    preds = {c.id: c for c in results.claims}
    return [
        (by_id[m.case_id], preds[m.pred_id])
        for m in alignment.matches
        if m.pred_id is not None
    ]
