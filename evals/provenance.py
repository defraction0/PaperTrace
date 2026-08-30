"""Capture what produced a run, so two scores can be compared honestly.

`RunResults` carries none of this, so the harness records it out of band. The
model is never guessed: when it cannot be identified the record says so.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

PROMPT_ATTRS = ("EXTRACT_PROMPT", "CHECK_PROMPT")


def prompt_fingerprint() -> dict:
    """Hash the prompt text rather than trusting a hand-maintained constant.

    A manual PROMPT_VERSION is a promise a human has to keep, and the classic
    eval failure is a prompt edit that doesn't bump it — silently making two
    runs incomparable while the artefacts claim otherwise. A content hash
    cannot be forgotten. `evals/DESIGN.md` maps each prefix to a human label.
    """
    from papertrace import check as check_mod

    fp = {"scheme": "sha256-content"}
    for name in PROMPT_ATTRS:
        text = getattr(check_mod, name, "")
        fp[name] = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return fp


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def papertrace_version() -> dict:
    import papertrace

    dunder = getattr(papertrace, "__version__", None)
    try:
        from importlib.metadata import version

        installed = version("papertrace")
    except Exception:  # noqa: BLE001 — provenance must never break a run
        installed = None
    return {
        "papertrace_version": installed or dunder,
        "papertrace_version_source": "importlib.metadata" if installed else "__version__",
        "papertrace_dunder_version": dunder,
        "version_mismatch": bool(installed and dunder and installed != dunder),
    }


def model_identity(results) -> dict:
    """Who judged. `model_unidentified` is a first-class answer."""
    from papertrace.check import last_model

    reported = last_model()
    checker = getattr(results, "checker", "") or ""
    if not reported and "·" in checker:
        candidate = checker.split("·", 1)[1].strip()
        reported = None if candidate == "account default model" else candidate
    return {
        "checker_string": checker,
        "model_reported": reported,
        "model_unidentified": reported is None,
    }


def ambient_context(case_dir: Path) -> dict:
    """`claude -p` inherits project context from its working directory.

    Running an eval inside this checkout can feed the model the repo's own
    skills and CLAUDE.md — which would flatter the score in a way a user's run
    would not. Recorded so the contamination is at least visible.
    """
    repo = Path(__file__).resolve().parent.parent
    case_dir = Path(case_dir).resolve()
    claude_md = case_dir / "CLAUDE.md"
    return {
        "cwd": str(case_dir),
        "inside_repo": str(case_dir).startswith(str(repo)),
        "claude_md_present": claude_md.exists(),
        "claude_md_sha256": _sha256(claude_md) if claude_md.exists() else None,
    }


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def capture(gold: dict, gold_path: Path, results, case_dir: Path,
            started: str, finished: str) -> dict:
    manifest_drift: list[dict] = []
    record = {
        "started_at": started,
        "finished_at": finished,
        "gold_set_id": gold.get("set_id"),
        "gold_kind": gold.get("kind"),
        "gold_sha256": _sha256(Path(gold_path)),
        **papertrace_version(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        **model_identity(results),
        "claude_cli_version": _cli_version(),
        "prompt_fingerprint": prompt_fingerprint(),
        "ambient_context": ambient_context(case_dir),
        "converter": getattr(results, "converter", None),
        "truncated": getattr(results, "truncated", {}) or {},
        "refs_status_drift": manifest_drift,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "interpreter": sys.executable,
    }
    return record


def _cli_version() -> str | None:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def check_refs_drift(gold: dict, manifest_path: Path) -> list[dict]:
    """A `not_retrieved` gold case depends on a DOI still being paywalled.

    If a source becomes open access the case silently stops testing retrieval
    and starts testing judgement — corrupting missed_gap_rate with no error.
    Drift invalidates the affected cases rather than scoring them.

    The check must be able to answer three ways, not two. Two silent-failure
    holes are closed here:

    - **A missing manifest returned `[]`** — the same value as *checked, and
      nothing changed*. An unasked question rendered as a reassuring answer.
    - **A source absent from the manifest yielded no entry** — *unobservable*
      rendered as *unchanged*.

    Both now emit `kind: "not_verified"`. A not-verified source does **not**
    invalidate its cases (see `eligibility.py`: unverifiable is not the same as
    wrong); it is disclosed so the gap is visible instead of being assumed away
    in the flattering direction.
    """
    expected = [s for s in gold.get("sources", []) if s.get("expected_ref_status")]
    if not expected:
        return []

    path = Path(manifest_path)
    if not path.exists():
        return [_unverified(s, f"{path.name} not found at {path}") for s in expected]
    try:
        data = json.loads(path.read_text())
        observed = {e["num"]: e.get("status") for e in data.get("entries", [])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        why = f"{path.name} could not be read: {type(exc).__name__}: {exc}"
        return [_unverified(s, why) for s in expected]

    drift = []
    for src in expected:
        want = src["expected_ref_status"]
        label = src["label"]
        if label not in observed:
            drift.append(_unverified(src, f"label {label} is absent from the manifest"))
            continue
        got = observed[label]
        if got is None:
            drift.append(_unverified(src, f"label {label} carries no status"))
        elif want != got:
            drift.append({"label": label, "slug": src.get("slug"),
                          "expected": want, "observed": got, "kind": "changed",
                          "why": f"expected '{want}', manifest observed '{got}'"})
    return drift


def _unverified(src: dict, why: str) -> dict:
    """An unanswered question, recorded as one. Never as a clean answer."""
    return {
        "label": src["label"],
        "slug": src.get("slug"),
        "expected": src.get("expected_ref_status"),
        "observed": None,
        "kind": "not_verified",
        "why": why,
    }
