"""Render an eval record as Markdown.

A template rather than an f-string builder: the guardrail wording is the point
of this file, and a human reviewing whether the caveats are intact should be
able to read them top to bottom without tracing string concatenation.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(str(resources.files("evals") / "templates"))


def _pct(rate: dict | None) -> str:
    """Percentages always carry (k/n); undefined renders as an em dash.

    "0% of 0" and "0% of 40" are different claims. Rendering both as 0% is the
    single easiest way for an eval report to mislead.
    """
    if not rate or rate.get("value") is None:
        return "—"
    return f"{rate['value'] * 100:.0f}% ({rate['k']}/{rate['n']})"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _banners(record: dict) -> list[str]:
    prov = record.get("provenance") or {}
    out = []
    if record["metrics"]["errors"]["unchecked_rate"]["k"]:
        out.append("This run partially failed — some claims are `unchecked`.")
    if prov.get("git_dirty"):
        out.append("Working tree was dirty; this run is not reproducible from a commit.")
    if prov.get("model_unidentified"):
        out.append("The judging model could not be identified and was not guessed.")
    if prov.get("version_mismatch"):
        out.append("Installed version disagrees with `__version__`.")
    drift = record.get("eligibility", {}) or {}
    if record.get("gold", {}).get("n_excluded_drift"):
        out.append("A source's availability changed since the gold set was frozen — "
                   "affected cases are invalidated, not scored.")
    if drift.get("refs_verification"):
        # a check that did not complete must not read as a check that passed
        labels = ", ".join(f"[{d.get('label')}]" for d in drift["refs_verification"])
        out.append(f"Source availability could not be verified for {labels} — the "
                   "freeze check did not complete. Unchanged is not being claimed.")
    if prov.get("truncated"):
        out.append("Input was truncated before the model saw it.")
    return out


def render(record: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(enabled_extensions=(), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("eval.md.j2").render(
        r=record, pct=_pct, num=_num, banners=_banners(record)
    )
