"""Metric arithmetic over aligned (gold, prediction) pairs. No I/O.

Populations, defined once and referenced throughout:

- **M**       every matched (gold case, ClaimResult) pair that is ELIGIBLE:
              the gold verdict is resolved and no drifted source invalidates
              it. "Every matched pair" was the old definition and it was the
              bug — a case nobody could label was scored as a correct
              retrieval, because `(None != "not_retrieved")` happened to agree
              with the prediction.
- **J_gold**  pairs whose *gold* verdict is a judgement
              (supported / partial / contradicted).
- **J**       pairs in J_gold whose *prediction* is also a judgement.
              This is the confusion-matrix population.

`not_retrieved` is a retrieval fact and `unchecked` is a harness error; neither
is a judgement, so neither is scored as a wrong verdict. Both get their own
rates instead — relocated, never hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from papertrace.models import JUDGMENT_VERDICTS


@dataclass(frozen=True)
class Rate:
    """A fraction that remembers its denominator *and* which population it came from.

    `value` is None when n == 0 — "undefined" and "0.0" are different claims
    and must never render the same way.

    `population` names the set n was drawn from. Two rates with n = 4, one over
    M and one over J_gold, are otherwise indistinguishable in JSON — which is
    precisely the neighbouring-bare-percentage hazard this harness exists to
    avoid. The name is checked against the record's `populations` block by
    `test_every_rate_in_the_record_matches_its_declared_population`, so a
    denominator that drifts away from the population it claims fails a test
    rather than printing a plausible number.
    """

    k: int
    n: int
    population: str = ""

    @property
    def value(self) -> float | None:
        return self.k / self.n if self.n else None

    def to_dict(self) -> dict:
        return {"k": self.k, "n": self.n, "value": self.value,
                "population": self.population}


def _judgement(verdict: str | None) -> bool:
    return verdict in JUDGMENT_VERDICTS


def matched_population(pairs: list[tuple[dict, object]]) -> list[tuple[dict, object]]:
    """M — matched pairs carrying a resolved gold verdict.

    Defence in depth. `scoring.score()` already filters ineligible cases out
    before any metric sees them; this guard means a future caller that forgets
    cannot reopen the leak. A null gold verdict cannot be right or wrong, so it
    cannot sit in a denominator — and it must never sit in a *numerator*, which
    is how it inflated `retrieval_outcome_accuracy` from 4/4 to 5/5.
    """
    return [(g, p) for g, p in pairs if g.get("gold_verdict") is not None]


def judgment_population(pairs: list[tuple[dict, object]]) -> list[tuple[dict, object]]:
    """J — both sides are judgements, so a confusion matrix is meaningful."""
    return [
        (g, p)
        for g, p in pairs
        if _judgement(g.get("gold_verdict")) and _judgement(getattr(p, "verdict", None))
    ]


def judgment_gold_population(pairs: list[tuple[dict, object]]) -> list[tuple[dict, object]]:
    """J_gold — the gold side is a judgement, whatever the tool answered."""
    return [(g, p) for g, p in pairs if _judgement(g.get("gold_verdict"))]


def judgment_accuracy(pairs: list[tuple[dict, object]]) -> Rate:
    j = judgment_population(pairs)
    return Rate(sum(1 for g, p in j if p.verdict == g["gold_verdict"]), len(j), "J")


def binary_contradiction_detection(pairs: list[tuple[dict, object]]) -> Rate:
    """supported+partial vs contradicted.

    Far more robust than 3-class accuracy: the supported/partial boundary is a
    judgement call that moves between runs, while "does this contradict the
    source" is the question a user actually asked.
    """
    j = judgment_population(pairs)
    hit = sum(
        1
        for g, p in j
        if (p.verdict == "contradicted") == (g["gold_verdict"] == "contradicted")
    )
    return Rate(hit, len(j), "J")


def confusion(pairs: list[tuple[dict, object]]) -> dict[str, dict[str, int]]:
    m = {g: {p: 0 for p in JUDGMENT_VERDICTS} for g in JUDGMENT_VERDICTS}
    for g, p in judgment_population(pairs):
        m[g["gold_verdict"]][p.verdict] += 1
    return m


def per_class(pairs: list[tuple[dict, object]], all_gold: list[dict]) -> dict[str, dict]:
    """Precision, recall and F1 per judgement class.

    Two recalls are reported on purpose. `recall` is conditional on the pair
    reaching J; `recall_pessimistic` counts every attrition path — unmatched,
    answered `not_retrieved`, answered `unchecked` — as a miss. `attrition`
    breaks down the difference so the gap is auditable rather than mysterious.
    """
    j = judgment_population(pairs)
    matched_ids = {g["case_id"] for g, _ in pairs}
    out: dict[str, dict] = {}

    for c in JUDGMENT_VERDICTS:
        tp = sum(1 for g, p in j if g["gold_verdict"] == c and p.verdict == c)
        predicted_c = sum(1 for _, p in j if p.verdict == c)
        gold_c_in_j = sum(1 for g, _ in j if g["gold_verdict"] == c)
        gold_c_total = [g for g in all_gold if g.get("gold_verdict") == c]

        attrition = {
            "unmatched": sum(1 for g in gold_c_total if g["case_id"] not in matched_ids),
            "pred_not_retrieved": sum(
                1 for g, p in pairs
                if g.get("gold_verdict") == c and getattr(p, "verdict", None) == "not_retrieved"
            ),
            "pred_unchecked": sum(
                1 for g, p in pairs
                if g.get("gold_verdict") == c and getattr(p, "verdict", None) == "unchecked"
            ),
        }

        precision = Rate(tp, predicted_c, f"J_predicted_{c}")
        recall = Rate(tp, gold_c_in_j, f"J_gold_{c}")
        recall_pess = Rate(tp, len(gold_c_total), f"gold_all_{c}")
        out[c] = {
            "precision": precision.to_dict() if predicted_c else None,
            "recall": recall.to_dict() if gold_c_in_j else None,
            "recall_pessimistic": recall_pess.to_dict() if gold_c_total else None,
            "f1": _f1(tp, predicted_c, gold_c_in_j),
            # the counts F1's definedness rests on, so a reader can check it
            "f1_support": {
                "tp": tp,
                "predicted_in_j": predicted_c,
                "gold_in_j": gold_c_in_j,
                "gold_total": len(gold_c_total),
                "support": predicted_c + gold_c_in_j,
            },
            "attrition": attrition,
        }
    return out


def _f1(tp: int, predicted_c: int, gold_c_in_j: int) -> float | None:
    """F1 = 2TP/(2TP+FP+FN), written as a count question.

    Definedness is not a `p + r == 0` question. That test conflates three
    distinct situations — present-and-always-wrong, in-gold-never-predicted,
    and predicted-never-in-gold — and made all three vanish from the macro
    mean, deleting exactly the classes a run failed. F1 is undefined only when
    the class appears in **neither column of J**: nothing predicted it and no
    gold instance reached the confusion population.
    """
    support = predicted_c + gold_c_in_j
    return None if support == 0 else 2 * tp / support


def macro_f1(per_class_scores: dict[str, dict]) -> dict:
    """Mean F1 over the classes where F1 is defined — and it names the rest.

    Scoring an *absent* class as 0.0 (the common `zero_division=0` convention)
    would report ~0.67 for a flawless run on a set with no `partial` example.
    Excluding and naming is honest; silently dividing by 3 is not.

    The converse trap is the one this harness fell into: a class that is
    *present* and always wrong scores 0.0 and must stay in the mean. Dropping
    it deletes the run's failures from its own grade.
    """
    included = [c for c, v in per_class_scores.items() if v["f1"] is not None]
    excluded = [c for c in per_class_scores if c not in included]
    value = (
        sum(per_class_scores[c]["f1"] for c in included) / len(included) if included else None
    )
    return {
        "value": value,
        # kept as list[str]: the template and the record consumers read these
        "classes_included": included,
        "classes_excluded": excluded,
        "exclusions": [_exclusion(c, per_class_scores[c]) for c in excluded],
        "n_classes": len(per_class_scores),
    }


def _exclusion(cls: str, scores: dict) -> dict:
    """Why a class left the mean — derived from the counts, never asserted.

    The old `excluded_reason` was one constant sentence covering every
    exclusion, so it was false whenever the class was present but eliminated.
    A constant cannot go stale quietly; a derivation can only be wrong if the
    counts are.
    """
    s = scores["f1_support"]
    if s["gold_total"]:
        kind = "eliminated_by_attrition"
        detail = (
            f"{s['gold_total']} gold case(s) of class '{cls}' exist, but none reached "
            "the confusion population J (unmatched, not_retrieved, unchecked or "
            f"excluded) and no prediction chose it, so F1 has no support"
        )
    else:
        kind = "absent_from_set"
        detail = (
            f"class '{cls}' has no gold instance in this set and was never "
            "predicted within J, so F1 is undefined rather than zero"
        )
    return {"class": cls, "kind": kind, "detail": detail, "counts": s}


def retrieval(pairs: list[tuple[dict, object]]) -> dict:
    """Did the tool decline exactly when it should have?"""
    m = matched_population(pairs)
    gold_gap = [(g, p) for g, p in m if g.get("gold_verdict") == "not_retrieved"]
    j_gold = judgment_gold_population(m)
    outcome = Rate(
        sum(
            1 for g, p in m
            if (g.get("gold_verdict") == "not_retrieved") == (p.verdict == "not_retrieved")
        ),
        len(m),
        "M",
    )
    return {
        "retrieval_outcome_accuracy": outcome.to_dict(),
        # claimed a gap that shouldn't exist — usually a resolver failure
        "false_gap_rate": Rate(
            sum(1 for _, p in j_gold if p.verdict == "not_retrieved"), len(j_gold),
            "J_gold",
        ).to_dict(),
        # SAFETY: produced a verdict against a source that was never available
        "missed_gap_rate": Rate(
            sum(1 for _, p in gold_gap if _judgement(p.verdict)), len(gold_gap),
            "gold_gaps",
        ).to_dict(),
    }


def pages(pairs: list[tuple[dict, object]]) -> dict:
    """Page accuracy over pairs that answered; "didn't answer" is separate."""
    j = judgment_population(pairs)
    with_gold = [(g, p) for g, p in j if g.get("gold_source_page") is not None]
    answered = [(g, p) for g, p in with_gold if p.source_page is not None]
    exact = sum(1 for g, p in answered if p.source_page == g["gold_source_page"])
    near = sum(1 for g, p in answered if abs(p.source_page - g["gold_source_page"]) <= 1)
    return {
        "source_page_accuracy": Rate(exact, len(answered), "J_answered_page").to_dict(),
        # a decisive passage can straddle a page break, so ±1 is reported too
        "source_page_accuracy_pm1": Rate(near, len(answered), "J_answered_page").to_dict(),
        "page_missing_rate": Rate(len(with_gold) - len(answered), len(with_gold),
                                  "J_with_gold_page").to_dict(),
    }


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def anchors(pairs: list[tuple[dict, object]]) -> dict:
    """Did the model quote something that actually settles the claim?

    `anchor_in_gold_passage` needs no PDFs, so it runs offline in CI. Whether
    the phrase is findable on the real page is a live-only question.
    """
    j = judgment_population(pairs)
    scoreable = [
        (g, p) for g, p in j if g.get("decisive_passage") and p.anchor_phrases
    ]
    hit = sum(
        1
        for g, p in scoreable
        if any(_norm(a) in _norm(g["decisive_passage"]) for a in p.anchor_phrases)
    )
    return {
        "anchor_in_gold_passage": Rate(hit, len(scoreable), "J_anchor_scoreable").to_dict(),
        "anchor_empty_rate": Rate(
            sum(1 for _, p in j if not p.anchor_phrases), len(j), "J"
        ).to_dict(),
        # requires the real PDFs; the offline scorer leaves it null
        "anchor_found_on_page_rate": None,
    }


def _occurrence_rate(k: int, n: int, denominator: str) -> dict:
    """A rate that carries its denominator inline instead of naming a population.

    Deliberately NOT a `Rate`. Every `Rate` in the record must name a
    population declared in the record's `populations` block, and that block is
    computed in `scoring.py` over gold cases and matched pairs — occurrences
    are neither. Rather than declare a population the record cannot verify,
    the count travels beside the value, so the number and its denominator
    cannot drift apart in the first place.

    The `Rate` contract still holds where it matters: `value` is None when the
    denominator is zero, never 0.0.
    """
    return {"numerator": k, "denominator": n, "value": (k / n if n else None),
            "counted_over": denominator}


def _gold_occurrence_keys(gold: dict) -> set[tuple[str, int, int]] | None:
    """Gold occurrences keyed by (label, page, ordinal), never by occurrence id.

    An occurrence id embeds a block id, and block ids depend on which ingest
    backend ran — `eval_gold.schema.json` refuses them for that reason, and the
    same reasoning applies here. `ordinal` is the 1-based rank of this
    occurrence among the ones sharing a label and a page.
    """
    listed = (gold.get("coverage_gold") or {}).get("occurrences_in_text")
    if not listed:
        return None
    return {(str(o["label"]), int(o["page"]), int(o.get("ordinal", 1))) for o in listed}


def _tool_occurrence_keys(items: list[dict]) -> set[tuple[str, int, int]] | None:
    """The same key over the tool's occurrences, or None if it cannot be built.

    The clean.md fallback records no page, so the key does not exist. Scoring
    that as 0% would blame the tool for a question nobody could ask it.
    """
    ranks: dict[tuple[str, int], int] = {}
    keys: set[tuple[str, int, int]] = set()
    for o in items:
        if o.get("page") is None:
            return None
        head = (str(o["label"]), int(o["page"]))
        ranks[head] = ranks.get(head, 0) + 1
        keys.add((*head, ranks[head]))
    return keys


def coverage(gold: dict, results) -> dict:
    """Four different things the report must not conflate.

    `label_detection_accuracy` grades the tool's *regex* against a human.
    `claim_label_coverage` grades the *extractor*. A style the regex cannot
    see makes the first collapse while leaving the second looking perfect.

    The two label-level rates are untouched by occurrence coverage, on
    purpose: they are what the demo run was baselined on. The occurrence
    figures are **absent** — the whole metric is None — for a run whose
    coverage audit is label-level, which is a different statement from a rate
    with n == 0 and must not render as one.
    """
    gold_labels = set(gold.get("coverage_gold", {}).get("labels_in_text", []))
    tool = results.coverage or {}
    found = set(tool.get("labels_in_text", []))
    covered = set(tool.get("covered", []))
    occ = tool.get("occurrences") or {}
    items = occ.get("items") or []

    uncited_gold = gold.get("uncited_gold", [])
    predicted_uncited = [u.claim for u in results.uncited]
    matched = sum(
        1
        for u in uncited_gold
        if any(_overlaps(u["text"], q) for q in predicted_uncited)
    )
    gold_occ = _gold_occurrence_keys(gold)
    tool_occ = _tool_occurrence_keys(items) if occ else None
    detection = (
        _occurrence_rate(len(gold_occ & tool_occ), len(gold_occ), "gold_occurrences")
        if gold_occ is not None and tool_occ is not None
        else None
    )
    return {
        "label_detection_accuracy": Rate(
            len(gold_labels & found), len(gold_labels), "gold_labels"
        ).to_dict(),
        "claim_label_coverage": Rate(len(covered), len(found), "tool_labels").to_dict(),
        "label_false_positives": sorted(found - gold_labels),
        # None means "this run cannot answer the question": a label-level audit
        # has no occurrences, and the clean.md fallback has no pages to key on
        "claim_occurrence_coverage": (
            _occurrence_rate(occ.get("covered", 0), occ.get("total", 0), "tool_occurrences")
            if occ else None
        ),
        "occurrence_uncertain_rate": (
            _occurrence_rate(occ.get("uncertain", 0), occ.get("total", 0), "tool_occurrences")
            if occ else None
        ),
        "occurrence_detection_accuracy": detection,
        "uncited_recall": Rate(matched, len(uncited_gold), "uncited_gold").to_dict(),
        # the gold uncited register is not exhaustive, so extra flags are NOT
        # false positives and no precision figure is reported
        "uncited_predicted_total": len(predicted_uncited),
    }


def _overlaps(a: str, b: str, threshold: float = 0.6) -> bool:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= threshold


def errors(results, pairs: list[tuple[dict, object]]) -> dict:
    """Operational properties of the run, never folded into accuracy."""
    claims = results.claims
    m = matched_population(pairs)
    return {
        # run-level: over EVERY prediction, including ones whose gold case was
        # excluded. An excluded case still cost the run a claim, and hiding it
        # here would make an invalidated set look cleaner than it ran.
        "unchecked_rate": Rate(
            sum(1 for c in claims if c.verdict == "unchecked"), len(claims),
            "all_predictions",
        ).to_dict(),
        "not_retrieved_rate": Rate(
            sum(1 for c in claims if c.verdict == "not_retrieved"), len(claims),
            "all_predictions",
        ).to_dict(),
        "unchecked_rate_matched": Rate(
            sum(1 for _, p in m if p.verdict == "unchecked"), len(m), "M",
        ).to_dict(),
    }


def pairs_metric(gold_cases: list[dict], matched: dict[str, object],
                 eligible_ids: set[str] | None = None) -> dict:
    """Faithful/altered discrimination — the sharpest signal in the set.

    A model that pattern-matches plausibility rather than reading the source
    gives both members of a pair the same verdict. `pair_collapse_rate` counts
    exactly that.

    **A half-pair is not a pair.** Groups are built from *all* gold cases, not
    from the scoreable subset: building from the subset turned a pair whose
    sibling was excluded into a one-member group scoring `pair_discrimination`
    1/1 = 100% — and `pair_collapse_rate` 1/1 as well, since a single verdict
    is trivially one distinct verdict. Incomplete groups are listed, never
    scored, and never counted in `n_pairs`.
    """
    groups: dict[str, list[dict]] = {}
    for c in gold_cases:
        pair = c.get("pair")
        if pair:
            groups.setdefault(pair["pair_id"], []).append(c)

    complete: list[list[dict]] = []
    incomplete: list[dict] = []
    for pair_id, members in sorted(groups.items()):
        missing = [
            m["case_id"] for m in members
            if (eligible_ids is not None and m["case_id"] not in eligible_ids)
            or matched.get(m["case_id"]) is None
        ]
        if missing:
            incomplete.append({
                "pair_id": pair_id,
                "members": [m["case_id"] for m in members],
                "why": ("not scoreable as a pair: "
                        f"{', '.join(missing)} excluded or unmatched"),
            })
        else:
            complete.append(members)

    scored = collapsed = 0
    for members in complete:
        preds = [matched[m["case_id"]] for m in members]
        if all(p.verdict == m["gold_verdict"] for p, m in zip(preds, members, strict=True)):
            scored += 1
        if len({p.verdict for p in preds}) == 1:
            collapsed += 1

    n = len(complete)
    return {
        "n_pairs": n,
        "n_pairs_incomplete": len(incomplete),
        "incomplete": incomplete,
        "pair_discrimination": Rate(scored, n, "pairs_complete").to_dict() if n else None,
        "pair_collapse_rate": Rate(collapsed, n, "pairs_complete").to_dict() if n else None,
    }
