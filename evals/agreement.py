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

**Two figures, and only one of them is a bound.** `agreement_report` reports
a *penalized* figure over every case seen in any run and a *complete-case*
figure over the cases every run answered. The penalized one is a true lower
bound; the complete-case one is a different population and is labelled as such,
because a dropped case whose true agreement is high pulls the mean down.

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


def _distinct(values: list) -> list:
    """Stable, sortable distinct — values may be dicts, which are unhashable."""
    out: list = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


def require_one_provenance(set_ids: list[str | None],
                           provenances: list[dict] | None = None) -> str | None:
    """Refuse to aggregate runs that are not comparable. Returns the set id.

    Agreement is only defined within one **(set_id, prompt fingerprint,
    converter)** triple. That sentence was already in the error message while
    only the first third was checked: two runs of different prompts, or of
    different ingest backends, were averaged into a single stability figure
    that describes neither. A disagreement between them is not the model being
    unstable — it is two different systems being compared.

    A category error, not a partial view, so it is refused outright rather than
    reported with a caveat.
    """
    sets = _distinct(sorted(set_ids, key=lambda x: (x is None, x)))
    if len(sets) > 1:
        raise ValueError(
            "refusing to aggregate runs from different gold sets: "
            f"{', '.join(repr(d) for d in sets)}. Agreement is only "
            "defined within one (set_id, prompt fingerprint, converter) triple."
        )

    for field, label in (("prompt_fingerprint", "prompt fingerprint"),
                         ("converter", "ingest converter")):
        values = [(p or {}).get(field) for p in (provenances or [])]
        if len(_distinct(values)) > 1:
            raise ValueError(
                f"refusing to aggregate runs with a different {label}: "
                f"{'; '.join(repr(v) for v in _distinct(values))}. A "
                f"disagreement between two runs that read different text, or "
                f"answered different prompts, is not the model being unstable."
            )

    return sets[0] if sets else None


def agreement_report(vectors: dict[str, list[str]], runs: int,
                     run_labels: list[str], set_ids: list[str | None],
                     provenances: list[dict] | None = None) -> dict:
    """Two populations, side by side, with the omissions named.

    **Neither is called a bound except the one that is.** The penalized figure
    counts every case seen in any run and scores an ABSENT vote as
    disagreement; replacing an ABSENT with any real vote can only raise the
    modal count, so it genuinely understates stability and is a lower bound.

    The complete-case figure is *not* an upper bound, and calling it one was
    wrong. It drops cases rather than penalising them, and a dropped case whose
    true agreement is high pulls the reported mean **down**, not up. With three
    or more runs the dropped set can sit anywhere relative to the kept set, so
    the complete-case figure is simply a different population — reported
    because it answers "how stable was the model where we actually asked it",
    and labelled as that rather than as a bound in either direction.
    """
    set_id = require_one_provenance(set_ids, provenances)
    if len(run_labels) != runs:
        raise ValueError(
            f"{len(run_labels)} run label(s) for {runs} run(s)")

    complete_vectors = {c: v for c, v in vectors.items() if ABSENT not in v}
    omissions = {
        label: sorted(c for c, v in vectors.items() if v[i] == ABSENT)
        for i, label in enumerate(run_labels)
    }

    penalized = agreement(vectors, runs)
    penalized["bound"] = "lower"
    penalized["bound_note"] = (
        "every case seen in any run; a case the harness never asked a run about "
        "counts as disagreement. Filling in any real vote could only raise this, "
        "so it is a genuine lower bound on stability")
    complete = agreement(complete_vectors, runs)
    complete["bound"] = None
    complete["bound_note"] = (
        "only cases present in every run — a different population, not a bound. "
        "The omitted cases could have agreed more or less than the kept ones, so "
        "this can sit either side of the true figure")

    return {
        "set_id": set_id,
        "runs": runs,
        "run_labels": list(run_labels),
        "complete_case": complete,
        "penalized": penalized,
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
