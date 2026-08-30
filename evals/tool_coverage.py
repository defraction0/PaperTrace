"""Read the tool's own citation-coverage audit without guessing at its shape.

`align.py` derives *why* a gold case went unmatched from
`results.coverage["missing"]`. The distinction it draws is load-bearing:
`extraction_gap` blames the **tool**, `alignment_failure_or_paraphrase_drift`
blames the **evaluator**. If the audit's shape changes under it — a parallel
workstream moves coverage from labels to citation *occurrences* — a naive
`set(...)` intersection quietly becomes empty and every unmatched case is
reattributed away from the tool and onto the harness that grades it. No error,
no failing test.

So the shape is decided over the **whole** collection, and a collection that
does not fit one known shape is refused **wholesale**: an empty label set plus
an error string. A partially parsed audit is the worst outcome available — it
would produce confident, wrong attributions for exactly the entries it failed
to read, and those are indistinguishable from correct ones downstream.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["missing_labels"]


def missing_labels(coverage: object) -> tuple[set[str], str | None]:
    """Return (labels the tool reported as unreached, shape error or None).

    `coverage` is the `missing` collection out of a run's coverage audit, i.e.
    ``(results.coverage or {}).get("missing")``. Two shapes are understood:

    - ``list[str]`` — the label-level audit shipping today.
    - ``list[dict]`` where every element carries a string ``"label"`` — the
      occurrence-level audit.

    `None` means no audit block was present at all. That is not a shape error:
    it is today's semantics (an absent audit contributes no missing labels) and
    a separate disclosure from an audit that ran and cannot be read.

    On any error the label set is **empty**. Callers must treat a non-None
    error as "attribution undetermined" rather than as "nothing was missing".
    """
    if coverage is None:
        return set(), None
    if isinstance(coverage, (str, bytes)) or not isinstance(coverage, Sequence):
        return set(), (
            f"coverage.missing is a {type(coverage).__name__}, expected a list "
            "of label strings or of occurrence objects"
        )

    items = list(coverage)
    if not items:
        return set(), None

    strings = sum(1 for x in items if isinstance(x, str))
    mappings = sum(1 for x in items if isinstance(x, dict))

    if strings == len(items):
        return set(items), None

    if mappings == len(items):
        labels: set[str] = set()
        for i, entry in enumerate(items):
            label = entry.get("label")
            if not isinstance(label, str):
                return set(), (
                    f"coverage.missing occurrence at index {i} has no string "
                    f"'label' (found {type(label).__name__})"
                )
            labels.add(label)
        return labels, None

    if strings and mappings:
        return set(), (
            "coverage.missing mixes label strings and occurrence objects; "
            "the shape is ambiguous and is refused rather than half-read"
        )
    kinds = sorted({type(x).__name__ for x in items})
    return set(), (
        f"coverage.missing holds unrecognised entries ({', '.join(kinds)}); "
        "expected label strings or occurrence objects"
    )
