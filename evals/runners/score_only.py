#!/usr/bin/env python3
"""Score an existing results.json against a gold set. Offline: no model, no network.

    python evals/runners/score_only.py --gold evals/gold/demo_v1.gold.json \
        --results <case>/out/results.json --out evals/runs

    python evals/runners/score_only.py --agreement evals/runs/<a> evals/runs/<b> ...
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from evals import provenance, scoring  # noqa: E402
from evals.agreement import ABSENT, UNMATCHED, agreement_report  # noqa: E402
from evals.eval_report import render  # noqa: E402
from papertrace.models import RunResults  # noqa: E402


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _manifest_for(results_path: Path, case_dir: Path | None) -> Path:
    """Where to look for the frozen-source check.

    The check used to run only under `--case-dir`, and the documented
    invocation in `evals/README.md` omits it — so in practice it never ran, and
    its absence was indistinguishable from a clean result. It now always runs:
    with no reachable manifest it reports every expected source as
    `not_verified` rather than reporting nothing.
    """
    if case_dir:
        return Path(case_dir) / "refs_manifest.json"
    parent = results_path.parent
    for candidate in (parent / "refs_manifest.json",
                      parent.parent / "refs_manifest.json"):
        if candidate.exists():
            return candidate
    return parent / "refs_manifest.json"


def score_one(gold_path: Path, results_path: Path, out_root: Path,
              case_dir: Path | None = None) -> Path:
    gold = json.loads(gold_path.read_text())
    results = RunResults.from_json(results_path)
    now = _stamp()
    prov = provenance.capture(
        gold, gold_path, results, case_dir or results_path.parent, now, now
    )
    prov["refs_status_drift"] = provenance.check_refs_drift(
        gold, _manifest_for(results_path, case_dir)
    )
    record = scoring.score(gold, results, gold_path, prov)

    run_dir = out_root / f"{now}__{gold.get('set_id', 'gold')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eval.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    (run_dir / "EVAL.md").write_text(render(record))
    # keep the scored input beside the score, so it stays re-derivable
    (run_dir / "results.json").write_text(results_path.read_text())
    return run_dir


def score_agreement(run_dirs: list[Path], out_root: Path) -> Path:
    """Compare repeated runs of the SAME gold set.

    The version this replaces filtered to the complete-case set with a bare
    `if len(v) == n`, silently dropping every case one run never produced —
    defeating `agreement.py`'s own documented contract, in the direction that
    flatters the model. Both populations are now reported and the omissions
    named, with only the penalized one called a bound.
    """
    labels, set_ids, provenances, per_run = [], [], [], []
    for d in run_dirs:
        record = json.loads((Path(d) / "eval.json").read_text())
        labels.append(Path(d).name)
        set_ids.append((record.get("gold") or {}).get("set_id"))
        provenances.append(record.get("provenance") or {})
        # `per_case` carries excluded rows on purpose — they are rendered in
        # their own section. They must not therefore vote here: a case that was
        # never scoreable cannot be evidence of the model disagreeing with
        # itself, and an unresolved gold label is the harness's gap, not the
        # model's instability. `.get("eligible", True)` so a record written
        # before the flag existed still counts every row, as it used to.
        per_run.append({row["case_id"]: row.get("predicted") or UNMATCHED
                        for row in record["per_case"]
                        if row.get("eligible", True)})

    n = len(run_dirs)
    all_cases = sorted({c for run in per_run for c in run})
    # a case missing from a run is ABSENT — the harness never asked — which is
    # a different fact from UNMATCHED, where it asked and the aligner failed
    vectors = {c: [run.get(c, ABSENT) for run in per_run] for c in all_cases}
    result = agreement_report(vectors, n, labels, set_ids, provenances)

    out = out_root / f"agg__{_stamp()}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "agreement.json").write_text(json.dumps(result, indent=2))
    (out / "AGREEMENT.md").write_text(_agreement_md(result))
    return out


def _fmt(value: float | None, spec: str) -> str:
    return "—" if value is None else format(value, spec)


def _agreement_md(r: dict) -> str:
    names = {"complete_case": "complete-case", "penalized": "penalized"}
    lines = [
        f"# Repeated-run agreement — {r['set_id'] or 'unknown set'}", "",
        f"{r['runs']} runs: {', '.join(f'`{x}`' for x in r['run_labels'])}.", "",
        "Two populations, because neither answers the question alone. The",
        "complete-case figure covers only the cases every run answered; the",
        "penalized figure covers every case seen in any run and scores the gaps",
        "as disagreement. Only the penalized figure is a bound.", "",
        "| | Cases | Modal agreement | Unanimous | Fleiss' kappa |",
        "|---|---|---|---|---|",
    ]
    for key, label in names.items():
        b = r[key]
        qualifier = f" ({b['bound']} bound)" if b["bound"] else " (not a bound)"
        lines.append(
            f"| **{label}{qualifier}** | {b['cases']} | "
            f"{_fmt(b['modal_agreement'], '.2f')} | "
            f"{_fmt(b['unanimous_rate'], '.0%')} | "
            f"{_fmt(b['fleiss'].get('value'), '.2f')}"
            f" ({b['fleiss'].get('reason', b['fleiss'].get('note', ''))}) |"
        )
    lines += ["", f"- *complete-case* — {r['complete_case']['bound_note']}",
              f"- *penalized* — {r['penalized']['bound_note']}", ""]
    if r["n_omitted"]:
        lines += ["## Cases the harness never asked about", "",
                  "Not model disagreement — an operator gap, named so it is not",
                  "silently charged to the model.", ""]
        for label, cases in r["omissions"].items():
            if cases:
                lines.append(f"- `{label}` — missing {', '.join(cases)}")
        lines.append("")
    lines += [
        "Modal agreement is the headline; kappa is a footnote because the",
        "prevalence paradox makes it read near zero on small, class-skewed sets",
        "even at high raw agreement.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--case-dir", type=Path, default=None,
                    help="case folder, for refs_manifest drift detection")
    ap.add_argument("--out", type=Path, default=_ROOT / "evals" / "runs")
    ap.add_argument("--agreement", nargs="+", type=Path, default=None,
                    help="run directories to compare")
    args = ap.parse_args()

    if args.agreement:
        try:
            print(score_agreement(args.agreement, args.out))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    if not (args.gold and args.results):
        ap.error("--gold and --results are required unless --agreement is used")
    run_dir = score_one(args.gold, args.results, args.out, args.case_dir)
    print(run_dir / "EVAL.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
