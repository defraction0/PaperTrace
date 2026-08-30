"""Stability of the checker across repeated runs of the same gold set.

Each run is aligned to *gold* independently; runs are never aligned to each
other. A case that failed to align in one run contributes the sentinel
`UNMATCHED` in that position rather than being dropped — dropping it would
bias agreement upward on exactly the cases the model handled inconsistently.

**Two sentinels, deliberately not one.**

- `UNMATCHED` — the run *was* asked and the aligner failed. That is a real
  measured outcome about the model, and it belongs in the score.
- `ABSENT` — the harness never asked: the case is missing from that run's
  record entirely. That is an operator bookkeeping gap.

Collapsing ABSENT into UNMATCHED would charge the operator's gap to the model.
Kappa assumes every item is rated by every rater, so Fleiss returns `None` with
a reason whenever anything is ABSENT rather than computing over a table it does
not describe. `fleiss_kappa` itself is correct and is not touched; the guard
lives in `agreement()`.

**The run count is passed, never inferred.** Deriving `k` from the first vector
is only safe when something upstream has already guaranteed equal lengths —
which used to be the caller's intersection filter, the very thing that
introduced the bias this module documents. Removing the filter without passing
the count would replace a disclosed upward bias with an undisclosed arithmetic
error. Ragged input raises.
"""

from __future__ import annotations

UNMATCHED = "__unmatched__"
ABSENT = "__absent__"


def agreement(vectors: dict[str, list[str]], runs: int) -> dict:
    """`vectors` maps case_id -> [verdict in run 1, ..., verdict in run k].

    `runs` is the number of runs the caller actually compared. Every vector
    must be exactly that long; a short one is an error, not a smaller k.
    """
    if not vectors:
        return {"modal_agreement": None, "unanimous_rate": None,
                "fleiss": {"value": None, "reason": "no cases"},
                "runs": runs, "cases": 0, "per_case": {}, "absent_cases": []}

    for cid, votes in sorted(vectors.items()):
        if len(votes) != runs:
            raise ValueError(
                f"ragged agreement input: case {cid!r} has {len(votes)} vote(s) "
                f"but {runs} run(s) were compared. Pad missing positions with "
                f"ABSENT rather than shortening the vector."
            )

    k = runs
    per_case, modal_scores, unanimous = {}, [], 0
    for cid, votes in vectors.items():
        counts = {v: votes.count(v) for v in set(votes)}
        top = max(counts.values())
        modal_scores.append(top / k)
        if top == k:
            unanimous += 1
        per_case[cid] = {"votes": votes, "modal": max(counts, key=counts.get),
                         "modal_count": top, "absent": votes.count(ABSENT)}

    absent_cases = sorted(cid for cid, v in vectors.items() if ABSENT in v)
    if absent_cases:
        fleiss = {"value": None,
                  "reason": ("some cases were not rated in every run; kappa "
                             "assumes every item is rated by every rater"),
                  "absent_cases": absent_cases}
    else:
        categories = sorted({v for votes in vectors.values() for v in votes})
        table = [[votes.count(c) for c in categories] for votes in vectors.values()]
        fleiss = fleiss_kappa(table)

    return {
        # headline: robust at small n, unlike kappa
        "modal_agreement": sum(modal_scores) / len(modal_scores),
        "unanimous_rate": unanimous / len(vectors),
        "fleiss": fleiss,
        "runs": k,
        "cases": len(vectors),
        "per_case": per_case,
        "absent_cases": absent_cases,
    }


def require_one_set_id(set_ids: list[str | None]) -> str | None:
    """Refuse to aggregate two gold sets. A category error, not a partial view.

    Averaging agreement across different sets produces a number that describes
    no set, and there is no caveat that repairs it — so it is refused outright
    rather than reported with a warning.
    """
    distinct = sorted({s for s in set_ids}, key=lambda x: (x is None, x))
    if len(distinct) > 1:
        raise ValueError(
            "refusing to aggregate runs from different gold sets: "
            f"{', '.join(repr(d) for d in distinct)}. Agreement is only "
            "defined within one (set_id, prompt fingerprint, converter) triple."
        )
    return distinct[0] if distinct else None


def agreement_report(vectors: dict[str, list[str]], runs: int,
                     run_labels: list[str], set_ids: list[str | None]) -> dict:
    """Both bounds, side by side, with the omissions named.

    Reporting only the intersection silently drops the cases one run never
    produced; reporting only the union charges the harness's own gaps to the
    model. Neither number is the answer on its own, so both are printed and
    labelled as what they are.
    """
    set_id = require_one_set_id(set_ids)
    if len(run_labels) != runs:
        raise ValueError(
            f"{len(run_labels)} run label(s) for {runs} run(s)")

    intersection_vectors = {c: v for c, v in vectors.items() if ABSENT not in v}
    omissions = {
        label: sorted(c for c, v in vectors.items() if v[i] == ABSENT)
        for i, label in enumerate(run_labels)
    }

    union = agreement(vectors, runs)
    union["bound"] = "lower"
    union["bound_note"] = (
        "includes every case seen in any run; a case the harness never asked a "
        "run about counts as disagreement, so this understates stability")
    inter = agreement(intersection_vectors, runs)
    inter["bound"] = "upper"
    inter["bound_note"] = (
        "only cases present in every run; excludes the harness's own gaps, so "
        "this overstates stability")

    return {
        "set_id": set_id,
        "runs": runs,
        "run_labels": list(run_labels),
        "union": union,
        "intersection": inter,
        "omissions": omissions,
        "n_omitted": sum(len(v) for v in omissions.values()),
    }


def fleiss_kappa(table: list[list[int]]) -> dict:
    """Fleiss' kappa plus the terms it is built from.

    Reported as a footnote, never as the headline: below roughly 30 cases the
    prevalence paradox makes kappa read near zero even at high raw agreement,
    because expected agreement approaches 1.
    """
    n_cases = len(table)
    if not n_cases:
        return {"value": None, "reason": "no cases"}
    k = sum(table[0])
    if k < 2:
        return {"value": None, "reason": "fewer than 2 runs", "k": k}

    p_i = [
        (sum(c * c for c in row) - k) / (k * (k - 1)) if k > 1 else 0.0 for row in table
    ]
    p_bar = sum(p_i) / n_cases
    totals = [sum(row[j] for row in table) for j in range(len(table[0]))]
    p_e_bar = sum((t / (n_cases * k)) ** 2 for t in totals)

    if abs(1 - p_e_bar) < 1e-12:
        # every rating fell in one category — kappa is undefined, not perfect
        return {"value": None, "reason": "all ratings in one category",
                "p_bar": p_bar, "p_e_bar": p_e_bar, "k": k, "n": n_cases}
    return {"value": (p_bar - p_e_bar) / (1 - p_e_bar), "p_bar": p_bar,
            "p_e_bar": p_e_bar, "k": k, "n": n_cases,
            "note": "uninformative below ~30 cases (prevalence paradox)"}
