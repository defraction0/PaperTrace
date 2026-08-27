#!/usr/bin/env python3
"""LIVE evaluation runner. Makes real `claude -p` calls and fetches PDFs.

This is a plain script, never a pytest test. Three reasons, in this repo's
terms: a marker-based `live` test would run by default when a contributor types
the documented `pytest`, i.e. spend money by accident; an env-var gate leaves a
permanently-skipped test in every CI run, and skip noise trains people to ignore
skips; and neither can ask for consent. `scripts/make_logo.py` and
`examples/demo/make_manuscript.py` set the precedent for repo tooling that no
CI runs.

    python evals/runners/run_eval.py --gold evals/gold/demo_v1.gold.json --runs 3

The pipeline is driven **in process**, not via `subprocess papertrace run`,
because `check.last_model()` reads a module global — shelling out loses the
identity of the model that judged. Do not "simplify" that back to a subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from evals import provenance, scoring  # noqa: E402
from evals.eval_report import render  # noqa: E402


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _confirm(gold: dict, runs: int, case_dir: Path, assume_yes: bool) -> bool:
    n_sources = len(gold.get("sources", []))
    print(
        f"\nLive evaluation of gold set '{gold.get('set_id')}'.\n"
        f"  · ~{n_sources + 1} `claude -p` calls per run × {runs} run(s) — this costs money\n"
        f"  · {n_sources} PDF(s) fetched over the network per run\n"
        f"  · case dir: {case_dir}"
        f"{' (inside the repo — may contaminate context)' if str(case_dir).startswith(str(_ROOT)) else ' (outside the repo)'}\n"
    )
    if assume_yes:
        return True
    return input("Continue? [y/N] ").strip().lower() in ("y", "yes")


def one_run(gold: dict, gold_path: Path, manuscript: Path, case_dir: Path,
            model: str | None, out_root: Path, email: str | None,
            backend: str) -> Path:
    """Drive the real pipeline in process, then score what it produced.

    The steps are the CLI's own functions, not a private reimplementation — a
    copy of the wiring would drift from the tool it is meant to measure. In
    process rather than `subprocess papertrace run`, because `last_model()`
    reads a module global that a subprocess would take with it.
    """
    from papertrace import cli
    from papertrace.models import RunResults

    started = _stamp()
    case_dir.mkdir(parents=True, exist_ok=True)

    cli.ingest(manuscript, case_dir / "ingest" / "manuscript", backend)
    cli.refs(manuscript, case_dir, None, email, parse_only=False, backend=backend)
    cli.check(case_dir, model)

    results_path = case_dir / "out" / "results.json"
    results = RunResults.from_json(results_path)
    finished = _stamp()

    prov = provenance.capture(gold, gold_path, results, case_dir, started, finished)
    prov["refs_status_drift"] = provenance.check_refs_drift(
        gold, case_dir / "refs_manifest.json"
    )
    record = scoring.score(gold, results, gold_path, prov)

    run_dir = out_root / f"{started}__{gold.get('set_id')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eval.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
    (run_dir / "EVAL.md").write_text(render(record))
    (run_dir / "results.json").write_text(results_path.read_text())
    return run_dir


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--manuscript", type=Path, default=None,
                    help="defaults to the manuscript the gold set names")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--model", default=None)
    ap.add_argument("--case-dir", type=Path, default=None,
                    help="defaults to a fresh dir under $TMPDIR, outside the repo")
    ap.add_argument("--out", type=Path, default=_ROOT / "evals" / "runs")
    ap.add_argument("--email", default=None,
                    help="Unpaywall contact; falls back to $PAPERTRACE_EMAIL")
    ap.add_argument("--backend", default="auto", help="auto | docling | pymupdf")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--allow-ci", action="store_true")
    args = ap.parse_args()

    if os.environ.get("CI") and not args.allow_ci:
        # not a real guard so much as an unmissable statement of intent
        print("Refusing to run a paid, non-deterministic evaluation in CI. "
              "Pass --allow-ci if you truly mean it.", file=sys.stderr)
        return 2

    gold = json.loads(args.gold.read_text())
    manuscript = args.manuscript
    if manuscript is None:
        builder = gold.get("manuscript", {}).get("builder")
        guess = _ROOT / "examples" / "demo" / gold["manuscript"]["name"]
        if not guess.exists():
            print(f"Manuscript not found: {guess}\n"
                  f"Build it first: python {builder}" if builder else
                  f"Manuscript not found: {guess}", file=sys.stderr)
            return 2
        manuscript = guess

    base = args.case_dir or Path(tempfile.mkdtemp(prefix="pt-eval-"))
    if not _confirm(gold, args.runs, base, args.yes):
        print("Aborted.")
        return 1

    run_dirs = []
    for i in range(args.runs):
        case_dir = base / f"run{i + 1:02d}"
        if case_dir.exists():
            shutil.rmtree(case_dir)
        run_dirs.append(one_run(gold, args.gold, manuscript, case_dir, args.model,
                                args.out, args.email, args.backend))
        print(f"  run {i + 1}/{args.runs} → {run_dirs[-1] / 'EVAL.md'}")

    if len(run_dirs) > 1:
        from evals.runners.score_only import score_agreement

        print(f"  agreement → {score_agreement(run_dirs, args.out) / 'AGREEMENT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
