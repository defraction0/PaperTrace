"""Which gold cases may be scored, and if not, why — answered in one place.

Two findings turned out to be the same defect: a case that must not be counted
is counted. An unresolved gold verdict (`gold_verdict: null`) and a source
whose availability drifted since the set was frozen are different reasons for
the same conclusion, so they share one mechanism rather than two filters that
can fall out of step.

Excluded cases are **kept in the record** and rendered in their own section.
Deleting them would quietly make the set easier — which is the same move, one
level up, that the harness exists to catch.

One decision worth stating rather than burying. Drift comes in two kinds:

- `changed` — the manifest observed a status different from the frozen
  expectation. The case is invalidated: it no longer tests what it was written
  to test.
- `not_verified` — the manifest was missing, unreadable, or silent about this
  source. **This does not exclude.** This project's rule is that unverifiable
  is not the same as wrong; excluding here would treat an unanswered question
  as a failed one, and would empty every set scored without a reachable
  manifest, including the documented offline invocation. It is disclosed
  instead — banner, caveat and its own report section — so the gap is visible
  rather than assumed away in either direction.
"""

from __future__ import annotations

from dataclasses import dataclass

GOLD_VERDICT_UNRESOLVED = "gold_verdict_unresolved"
REFS_STATUS_DRIFT = "refs_status_drift"

__all__ = [
    "GOLD_VERDICT_UNRESOLVED",
    "REFS_STATUS_DRIFT",
    "Exclusion",
    "eligibility",
    "not_verified",
]


@dataclass(frozen=True)
class Exclusion:
    """One case, one reason, and a `detail` computed from the data.

    `detail` is never a constant. A constant explanation cannot notice when it
    has stopped being true — which is exactly how `macro_f1.excluded_reason`
    came to print a false sentence beside a real number.
    """

    case_id: str
    reason: str
    detail: str

    def to_dict(self) -> dict:
        return {"case_id": self.case_id, "reason": self.reason, "detail": self.detail}


def not_verified(refs_drift: list[dict] | None) -> list[dict]:
    """Drift entries the harness could not check. Disclosed, not excluded."""
    return [d for d in (refs_drift or []) if d.get("kind") == "not_verified"]


def _changed(refs_drift: list[dict] | None) -> list[dict]:
    return [d for d in (refs_drift or []) if d.get("kind") == "changed"]


def _depends_on(case: dict, entry: dict) -> bool:
    """Does this case rest on the source that drifted?

    Matched by slug *or* by citation label. Label matching is not a fallback:
    a `not_retrieved` gold case carries `gold_source_slug: null` by
    construction, so the label is the only handle it has.
    """
    slug = entry.get("slug")
    if slug and case.get("gold_source_slug") == slug:
        return True
    label = entry.get("label")
    return bool(label and label in (case.get("citation_labels") or []))


def eligibility(gold: dict, refs_drift: list[dict] | None = None
                ) -> tuple[list[dict], list[Exclusion]]:
    """Return (cases that may be scored, why the rest may not).

    Order of the returned cases follows the gold set, so downstream tables stay
    in authoring order.
    """
    changed = _changed(refs_drift)
    scored: list[dict] = []
    excluded: list[Exclusion] = []

    for case in gold.get("cases", []):
        if case.get("gold_verdict") is None:
            excluded.append(Exclusion(
                case["case_id"], GOLD_VERDICT_UNRESOLVED,
                _unresolved_detail(case),
            ))
            continue
        hit = next((e for e in changed if _depends_on(case, e)), None)
        if hit is not None:
            excluded.append(Exclusion(
                case["case_id"], REFS_STATUS_DRIFT, _drift_detail(hit),
            ))
            continue
        scored.append(case)

    return scored, excluded


def _unresolved_detail(case: dict) -> str:
    note = (case.get("why") or "").strip()
    ambiguity = (case.get("ambiguity") or "").strip()
    parts = ["gold verdict is null: labellers did not resolve this case"]
    if ambiguity:
        parts.append(f"ambiguity '{ambiguity}'")
    if note:
        parts.append(note)
    return "; ".join(parts)


def _drift_detail(entry: dict) -> str:
    label = entry.get("label")
    slug = entry.get("slug") or "unknown source"
    return (
        f"source [{label}] {slug} was frozen as '{entry.get('expected')}'; "
        f"the manifest observed '{entry.get('observed')}', so this case no "
        "longer tests what it was written to test"
    )
