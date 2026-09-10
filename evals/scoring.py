"""Turn a gold set plus a run's results.json into the eval record."""

from __future__ import annotations

from pathlib import Path

from . import align as align_mod
from . import eligibility as elig_mod
from . import metrics
from .metrics import JUDGMENT_VERDICTS


def score(gold: dict, results, gold_path: Path | None = None,
          provenance: dict | None = None, refs_drift: list[dict] | None = None) -> dict:
    """Score one run against one gold set.

    `refs_drift` defaults to the `refs_status_drift` key both runners already
    write into provenance. Before this, `score()` took no drift argument at
    all: the runner detected that a source's availability had changed, the
    report printed a banner about it, and every contaminated metric was
    computed anyway, immediately below the banner.
    """
    if refs_drift is None:
        refs_drift = (provenance or {}).get("refs_status_drift")

    # eligibility is decided once, for both reasons, BEFORE alignment — not
    # merely before the metrics. A case that can never be scored used to
    # compete for predictions anyway, and a prediction is consumed once: an
    # unresolved gold case sitting on the same citation label as an eligible
    # one took its match, and the eligible case was then reported as the tool's
    # extraction gap. Blame moved off the harness and onto the tool, silently.
    scoreable, excluded = elig_mod.eligibility(gold, refs_drift)
    eligible_ids = {c["case_id"] for c in scoreable}

    eligible_gold = {**gold, "cases": scoreable}
    alignment = align_mod.align(eligible_gold, results)
    pairs = align_mod.matched_pairs(eligible_gold, results, alignment)
    matched = {g["case_id"]: p for g, p in pairs}
    unresolved = [e for e in excluded if e.reason == elig_mod.GOLD_VERDICT_UNRESOLVED]
    drifted = [e for e in excluded if e.reason == elig_mod.REFS_STATUS_DRIFT]
    unverified = elig_mod.not_verified(refs_drift)

    # every family that consumes the gold verdict or the gold evidence sees
    # only eligible pairs; coverage and the run-level error rates deliberately
    # do not, and the report says so where those percentages are printed
    eligible_pairs = [(g, p) for g, p in pairs if g["case_id"] in eligible_ids]

    per_class = metrics.per_class(eligible_pairs, scoreable)
    macro = metrics.macro_f1(per_class)
    record = {
        "schema": "eval_run/2",
        "gold": {
            "set_id": gold.get("set_id"),
            "kind": gold.get("kind", "demonstration"),
            "title": gold.get("title", ""),
            "n_cases": len(gold["cases"]),
            "n_scored": len(scoreable),
            "n_unresolved": len(unresolved),
            "n_excluded_drift": len(drifted),
        },
        "provenance": provenance or {},
        "eligibility": {
            "n_eligible": len(scoreable),
            "excluded": [e.to_dict() for e in excluded],
            "refs_verification": unverified,
            "still_counted_in": [
                "coverage (grades the extractor and the regex, not the verdict)",
                "unchecked_rate and not_retrieved_rate (run-level, over every "
                "prediction the run produced)",
            ],
        },
        "populations": _populations(gold, results, eligible_pairs, scoreable,
                                    matched, eligible_ids),
        "alignment": alignment.to_dict(),
        "confusion": metrics.confusion(eligible_pairs),
        "metrics": {
            "judgment_accuracy": metrics.judgment_accuracy(eligible_pairs).to_dict(),
            "binary_contradiction_detection":
                metrics.binary_contradiction_detection(eligible_pairs).to_dict(),
            "per_class": per_class,
            "macro_f1": macro,
            "pairs": metrics.pairs_metric(gold["cases"], matched, eligible_ids),
            "retrieval": metrics.retrieval(eligible_pairs),
            "pages": metrics.pages(eligible_pairs),
            "anchors": metrics.anchors(eligible_pairs),
            "coverage": metrics.coverage(gold, results),
            "errors": metrics.errors(results, eligible_pairs),
        },
        "per_case": _per_case(gold, alignment, matched, excluded),
        "caveats": _caveats(gold, alignment, results, per_class, scoreable, macro,
                            drifted, unverified),
    }
    return record


def _populations(gold: dict, results, pairs: list, scoreable: list[dict],
                 matched: dict, eligible_ids: set[str]) -> dict:
    """Every denominator in the record, computed independently of the metrics.

    Deliberately *not* accumulated as the metrics run. The point of this block
    is that it can disagree with a metric — a denominator drifting away from
    the population it claims is exactly findings 1 and 10, and
    `test_every_rate_in_the_record_matches_its_declared_population` turns the
    next instance into a failing test rather than a plausible number.
    """
    m = metrics.matched_population(pairs)
    j_gold = metrics.judgment_gold_population(m)
    j = metrics.judgment_population(m)
    with_page = [(g, p) for g, p in j if g.get("gold_source_page") is not None]
    answered = [(g, p) for g, p in with_page if p.source_page is not None]
    anchorable = [(g, p) for g, p in j if g.get("decisive_passage") and p.anchor_phrases]

    groups: dict[str, list[dict]] = {}
    for c in gold["cases"]:
        if c.get("pair"):
            groups.setdefault(c["pair"]["pair_id"], []).append(c)
    complete = [
        gid for gid, members in groups.items()
        if all(mm["case_id"] in eligible_ids and matched.get(mm["case_id"]) is not None
               for mm in members)
    ]

    pops = {
        "M": (len(m), "matched pairs with a resolved gold verdict and no "
                      "invalidating source drift"),
        "J_gold": (len(j_gold), "M whose gold verdict is a judgement"),
        "J": (len(j), "J_gold whose prediction is also a judgement — the "
                      "confusion-matrix population"),
        "gold_gaps": (len(
            [1 for g, _ in m if g.get("gold_verdict") == "not_retrieved"]),
            "M whose gold verdict is not_retrieved"),
        "J_with_gold_page": (len(with_page), "J carrying a gold source page"),
        "J_answered_page": (len(answered), "J_with_gold_page where the run "
                                           "returned a page"),
        "J_anchor_scoreable": (len(anchorable), "J with a decisive passage and "
                                                "at least one anchor phrase"),
        "all_predictions": (len(results.claims),
                            "every claim the run produced, eligible or not"),
        "gold_labels": (len(set(gold.get("coverage_gold", {}).get("labels_in_text", []))),
                        "citation labels a human found in the manuscript"),
        "tool_labels": (len(set((results.coverage or {}).get("labels_in_text", []))),
                        "citation labels the tool's regex found"),
        "uncited_gold": (len(gold.get("uncited_gold", [])),
                         "planted uncited assertions in the gold register"),
        "pairs_complete": (len(complete),
                           "faithful/altered groups with every member eligible "
                           "and matched"),
    }
    for c in JUDGMENT_VERDICTS:
        pops[f"J_predicted_{c}"] = (
            sum(1 for _, p in j if p.verdict == c), f"J where the run predicted '{c}'")
        pops[f"J_gold_{c}"] = (
            sum(1 for g, _ in j if g["gold_verdict"] == c), f"J whose gold verdict is '{c}'")
        pops[f"gold_all_{c}"] = (
            sum(1 for g in scoreable if g.get("gold_verdict") == c),
            f"every eligible gold case of class '{c}', matched or not")
    return {name: {"n": n, "definition": d} for name, (n, d) in pops.items()}


def _per_case(gold: dict, alignment, matched: dict, excluded: list) -> list[dict]:
    """Every gold case gets a row, including the excluded ones.

    Excluded cases stay in the record and are rendered in their own section.
    Dropping them would shrink the visible set to the part the run could be
    graded on — the same move, one level up, that this harness exists to catch.
    """
    stages = {m.case_id: m for m in alignment.matches}
    why_excluded = {e.case_id: e for e in excluded}
    rows = []
    for c in gold["cases"]:
        p = matched.get(c["case_id"])
        m = stages.get(c["case_id"])
        ex = why_excluded.get(c["case_id"])
        rows.append({
            "eligible": ex is None,
            "excluded_reason": ex.reason if ex else None,
            "excluded_detail": ex.detail if ex else None,
            "case_id": c["case_id"],
            "claim": c["claim_text"],
            "gold": c.get("gold_verdict"),
            "predicted": getattr(p, "verdict", None),
            # an excluded case was never scored, so it cannot be "correct"
            "correct": bool(ex is None and p and p.verdict == c.get("gold_verdict")),
            "match_stage": m.stage if m else "unmatched",
            "match_score": round(m.score, 3) if m else 0.0,
            "gold_page": c.get("gold_source_page"),
            "pred_page": getattr(p, "source_page", None),
            "why": c.get("why", ""),
            "pred_note": getattr(p, "note", ""),
        })
    return rows


def _caveats(gold: dict, alignment, results, per_class: dict,
             scoreable: list[dict], macro: dict,
             drifted: list, unverified: list[dict]) -> list[str]:
    out: list[str] = []
    kind = gold.get("kind", "demonstration")
    if kind == "demonstration":
        out.append(
            f"gold set kind is 'demonstration': {len(gold['cases'])} hand-labelled cases; "
            "it shows the harness works, it does not measure accuracy on real manuscripts"
        )
    if (gold.get("labelling") or {}).get("authored_the_prompts"):
        out.append(
            "conflict of interest: the same party authored the prompts and assigned "
            "the gold verdicts — no validity claim can rest on this set"
        )
    if len(scoreable) < 30:
        out.append(
            f"n = {len(scoreable)} — too small for a confidence interval; "
            "treat every figure as illustrative"
        )
    if alignment.fuzzy_fraction > 0.25:
        out.append(
            f"{alignment.fuzzy_fraction:.0%} of alignments used fuzzy text matching; "
            "all precision figures are conditional on alignment being correct"
        )
    out.extend(_macro_caveats(macro))
    if drifted:
        names = ", ".join(sorted(e.case_id for e in drifted))
        out.append(
            f"{len(drifted)} case(s) excluded because a source's availability "
            f"drifted since the set was frozen ({names}); they are gone from the "
            "judgement, retrieval, page, anchor and pair metrics but STILL "
            "counted in coverage and in the run-level unchecked / not-retrieved "
            "rates, which are printed beside them"
        )
    if unverified:
        labels = ", ".join(f"[{d.get('label')}]" for d in unverified)
        out.append(
            f"source availability could not be verified for {labels} — the "
            "freeze check did not complete, so 'unchanged' is not being claimed; "
            "these cases are still scored, because unverifiable is not the same "
            "as wrong"
        )
    if any(c.verdict == "unchecked" for c in results.claims):
        n = sum(1 for c in results.claims if c.verdict == "unchecked")
        out.append(
            f"run partially failed: {n} claim(s) unchecked; all judgement metrics are "
            "conditional on the surviving subset"
        )
    if getattr(results, "truncated", None):
        out.append(
            "input was truncated before reaching the model; text past the cut was "
            "never checked and cannot be scored"
        )
    if alignment.unmatched_predictions:
        out.append(
            f"{len(alignment.unmatched_predictions)} prediction(s) had no gold case; "
            "the gold set is not exhaustive, so these are NOT counted as errors and "
            "no true precision figure is available"
        )
    return out


def _macro_caveats(macro: dict) -> list[str]:
    """Say why macro F1 is a mean over fewer than three classes — from the
    derived exclusions, never from a constant.

    The sentence this replaces was one fixed string covering every exclusion,
    so it read "class(es) X absent from this set" even when X was present and
    the run had simply eliminated it. Two kinds, two sentences, both computed.
    """
    kept = len(macro.get("classes_included", []))
    total = macro.get("n_classes", kept + len(macro.get("classes_excluded", [])))
    out: list[str] = []
    for kind, phrasing in (
        ("absent_from_set",
         "have no gold instance and were never predicted in this set"),
        ("eliminated_by_attrition",
         "have gold instances, but none survived to the confusion population J "
         "(unmatched, not_retrieved, unchecked or excluded) and none were predicted"),
    ):
        names = [e["class"] for e in macro.get("exclusions", []) if e["kind"] == kind]
        if names:
            out.append(
                f"class(es) {', '.join(names)} {phrasing}; F1 is undefined for "
                f"them and macro F1 is a mean over {kept} of {total} classes"
            )
    return out

