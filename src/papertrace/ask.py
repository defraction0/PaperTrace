"""The one place PaperTrace shells out to a model.

Every model call goes through `_ask`, which is what lets the sandbox be stated
once and audited once: `--safe-mode`, `--tools ""` and a private scratch cwd
mean the subprocess may read the prompt it is given and answer, and nothing
else. `tests/test_ask.py` asserts this is the only file in `src/` that runs a
subprocess at all.

The model is recorded **per call site**, not in one global. Two stages call
this seam — `refs` reads the bibliography, `check` judges the claims — and a
single global lets whichever ran last name itself the judge of verdicts it
never saw.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager

CLAUDE_TIMEOUT = 600

# `claude -p` fails transiently, so a judging call gets one retry. Named here
# because the wizard quotes a worst-case cost and the two must not drift:
# a promised ceiling that the retry can exceed is a false promise about money.
ASK_ATTEMPTS = 2

# Named constants, not literals at the call sites: a typo in one of two string
# keys does not fail, it silently splits one site's history into two half-empty
# entries and makes `model_for` return None about a call that happened.
SITE_CHECK = "check"
SITE_REFS = "refs"

# which site the next `_ask` belongs to, and what model answered at each.
# `_ask`'s signature stays `(prompt, model=None)` deliberately: sixty-two test
# sites monkeypatch it with a two-argument lambda, and a third parameter would
# break every one of them in order to record something the fakes never produce.
_SITE = "unknown"
_MODELS: dict[str, str] = {}


@contextmanager
def for_site(name: str):
    """Attribute every `_ask` inside this block to `name`.

    Restores the previous site on the way out rather than clearing it — a
    leaked site is a model name attributed to a stage that did not use it,
    which is the bug this module exists to prevent.
    """
    global _SITE
    previous, _SITE = _SITE, name
    try:
        yield
    finally:
        _SITE = previous


def model_for(site: str) -> str | None:
    """The model that answered at `site`, when `claude -p` reported one.

    None means no call was made there, or none reported a model. It never means
    "the model the other site used".
    """
    return _MODELS.get(site)


def claude_available() -> bool:
    return shutil.which("claude") is not None


_SCRATCH_CWD: str | None = None


def _scratch_cwd() -> str:
    # /tmp itself is shared and world-writable; a private 0700 directory (one per
    # process, reused across calls) keeps another local user from planting
    # anything the judging call would walk into
    global _SCRATCH_CWD
    if _SCRATCH_CWD is None:
        _SCRATCH_CWD = tempfile.mkdtemp(prefix="papertrace-ask-")
    return _SCRATCH_CWD


def _ask(prompt: str, model: str | None = None) -> str:
    # judging happens wherever the user ran papertrace from — never that repo's own
    # CLAUDE.md, and never with more than the ability to read the prompt and answer
    cmd = ["claude", "-p", "--output-format", "json", "--safe-mode", "--tools", ""]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=_scratch_cwd(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude -p timed out after {CLAUDE_TIMEOUT}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed: {proc.stderr.strip()[:400]}")
    payload = json.loads(proc.stdout)
    usage = payload.get("modelUsage")
    reported = payload.get("model") or (
        next(iter(usage), None) if isinstance(usage, dict) else None
    )
    # a reply that names no model is not evidence the model changed, so the
    # recorded name stands rather than being overwritten with nothing
    if reported:
        _MODELS[_SITE] = reported
    return payload.get("result", "")


def _parse_json_array(text: str) -> list[dict]:
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON array in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def _parse_json_object(text: str) -> dict:
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


__all__ = [
    "ASK_ATTEMPTS",
    "CLAUDE_TIMEOUT",
    "SITE_CHECK",
    "SITE_REFS",
    "claude_available",
    "for_site",
    "model_for",
]
