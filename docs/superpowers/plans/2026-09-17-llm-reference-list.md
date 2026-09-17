# LLM Reference List as a Reconcile Candidate — Implementation Plan (Plan B of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop printing a verdict for a citation label whose two readings of the bibliography name different papers — by adding two more candidate readings and withholding, per label, exactly where they contradict each other.

**Architecture:** `reconcile` goes from two candidate readings of the reference list to four: the Crossref deposit, the run backend's parse, a flat-text (pymupdf) parse of the same PDF, and a structured list proposed by a model. Nothing replaces the parser and no model output is trusted on its own word — every field of the model's reply must be found verbatim in one of the two texts it was shown, or that field is discarded. Agreement is then computed **per printed citation label**, never by position, and a label whose readings disagree is dropped from the set of sources `check.py` will judge, landing `unchecked` with a note rather than a confident verdict about a possibly different paper.

**Tech Stack:** Python 3.10+, pytest, pymupdf (`fitz`), Jinja2, Typer, rich, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-16-llm-reference-list-design.md`

**Depends on:** Plan A (`docs/superpowers/plans/2026-09-16-reference-numbering-foundation.md`), merged to `main` at `2e8a803`. Plan A's `_unwrap_table_rows`, `_numerals_agree_with_position`, numbering ledger, `CLAIM_KEYS` parity guard and `models._fold` are all load-bearing here.

**Not in this plan:** the pairing **evaluation task** (spec PR 9). It lives entirely in `evals/`, touches four files this plan never opens (`metrics.py`, `scoring.py`, `eligibility.py`, `provenance.py`), and grades work this plan must first produce. It is a sibling plan, written separately.

## Global Constraints

- **Tests stay offline.** No network, no model calls, ever. HTTP is faked with `httpx.MockTransport`; model calls are reached only through `ask._ask`, which every test monkeypatches. PDFs are generated in-test with pymupdf and never committed.
- **No `conftest.py` in `tests/`.** Every test module does `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))` then imports with `# noqa: E402`.
- **`ruff check src tests scripts evals` must be clean.** Line length 100, target py310, rules `E,F,W,I,UP,B`, `E501` ignored. **Never run `ruff format`.**
- **Python 3.10+ syntax**: `X | None`, never `Optional[X]`.
- **Comments state constraints the code can't, not narration.** Every new module opens with a one-line docstring naming its job.
- **Status words stay lowercase** in prose and reports (`unchecked`, `disputed`, `not retrieved`).
- **The cardinal rule.** An unread source never receives a verdict; a step that cannot do its job says so in the report. No new code path may emit a plausible-looking value in place of an admission of ignorance. In this plan that has one concrete form: **`numbering_verified` must stay `False` through every model reply and every interactive choice.**
- **Gate 2:** a new status, verdict or JSON field requires a `schemas/` update **and** a round-trip test. `schemas/refs_manifest.schema.json` has no version field and `additionalProperties` is never `false`, so the house convention is: additive properties, **nothing** added to `required`, and every description states explicitly what **absent** means.
- **Gate 4:** any task touching `refs.py`, `check.py` or `scout.py` must prove the failure path still degrades honestly, with pasted test output.
- **Gate 5:** Tasks 1–8 change no ingest, highlight or report *code*. Templates gain branches, which the parity tests cover. **Say this explicitly in each task's report rather than skipping the gate silently.** Task 9 (docs) re-runs nothing either. If any task finds itself editing `highlight.py` or `report.py` logic, that is a signal the task has grown beyond its brief — stop and report it.
- **Local commits on branch `feat/llm-reference-list` are authorised for this plan's execution. Pushing is not, and no PR may be opened.** Never commit a `*.pdf`, `*.docx` or a `case/` folder.
- **Never claim an accuracy figure.** No number describing how well any of this works may reach `README.md`, because none has been measured.
- Run tests with `/Users/danielgutmann/anaconda3/bin/python -m pytest` from the repo root, or the `pytest` on that interpreter's path. The homebrew `python3` has no pytest.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/papertrace/ask.py` *(new, ~90 lines)* | The one hardened `claude -p` subprocess call: `--safe-mode`, `--tools ""`, scratch cwd, timeout, and the model name recorded **per call site** so two callers cannot clobber each other | 1 |
| `src/papertrace/check.py` *(modified)* | Loses the seam, keeps every prompt and every verdict rule. Gains the withholding filter | 1, 4 |
| `src/papertrace/ingest/__init__.py` *(modified, +1 function)* | `references_span_flat(pdf)` — the flat-text reading of a bibliography without writing a source map | 2 |
| `src/papertrace/refs.py` *(modified)* | `label_agreement` computes per-label states across N named readings; `stamp_seen_in` records which readings carried each chosen entry. **`reconcile` itself is not touched** — see the note below | 3, 6 |
| `src/papertrace/reflist.py` *(new, ~200 lines)* | Builds the two-reading prompt, calls `ask`, parses the reply, verifies every field verbatim against the two input texts, returns `(list[RefEntry], ReflistProvenance)` | 5 |
| `src/papertrace/disclosures.py` *(modified)* | Four new keys — three run-level, one claim-level | 3, 4, 6 |
| `src/papertrace/templates/*` *(modified)* | The terminal allow-list and the two claim-level explicit-key filters | 3, 4, 6 |
| `src/papertrace/cli.py` *(modified)* | `--llm-refs/--no-llm-refs`, the third and fourth candidates wired in, the interactive escalation | 2, 6, 7 |
| `schemas/refs_manifest.schema.json` *(modified)* | Ten additive manifest fields plus one entry field | 3, 5, 7 |
| `docs/adr/0003-llm-reference-list.md` *(new)* | The decision ADR 0002 explicitly withheld | 9 |

**Six** new test modules: `tests/test_ask.py`, `tests/test_label_agreement.py`, `tests/test_reflist.py`, `tests/test_withholding.py`, `tests/test_reference_readings.py`, `tests/test_escalation.py`, plus `tests/test_numbering_invariant.py` — seven. Existing modules gain tests: `tests/test_refs.py`, `tests/test_disclosure_parity.py`, `tests/test_reference_reconciliation.py`, `tests/test_pipeline_split.py`, `tests/test_ingest_backends.py`, `tests/test_wizard.py`, `tests/test_coverage.py`.

**There is no `tests/test_cli.py`, no `tests/test_check.py` and no `tests/test_schemas.py` in this repo** — 36 modules, verified with `ls tests/`. The CLI's stage wiring is tested in `test_pipeline.py` / `test_pipeline_split.py`; `check_claims` is exercised from `test_multisource.py`, `test_supplements.py`, `test_check_provenance.py`, `test_claim_quote.py`, `test_coverage.py` and `test_provided_identity.py`; the only module that validates against `schemas/` with `jsonschema` is `test_supplements.py`, and the vocabulary-vs-enum comparisons read the schema JSON directly (`test_coverage.py:384`'s idiom). Three earlier drafts of this plan named files that do not exist; if a task's text still does, that is a plan defect — fix it to a real module and say so, do not create a duplicate home for an existing concern.

## Task Map and Dependencies

```
1  ask.py extraction ............ gates 5, 6   (no behaviour change)
2  flat-text second reading ..... gates 3
3  label_agreement + vocabulary . gates 4      (pure function, no wiring)
4  withholding in check.py ...... needs 3
5  reflist.py ................... needs 1
6  wire the LLM candidate ....... needs 2, 3, 5
7  interactive escalation ....... needs 6
8  the invariant test ........... needs 4, 7
9  docs ......................... needs all
```

Tasks 1 and 3 are independent of each other and of 2; 5 needs only 1. Nothing here may be implemented in parallel in the same worktree — the dependency arrows are for ordering and for knowing what a failure blocks.

**`reconcile()` keeps its current signature, deliberately.** The spec's architecture diagram writes `reconcile(body_labels, candidates)`. Do not do that. `reconcile` is a 200-line function with 18 direct call sites in `tests/test_reference_reconciliation.py`, and its job — *which reading do we adopt, and does it account for exactly the labels the body cites* — is unchanged by this feature. The spec's own semantics section calls corroboration **"a second, independent axis"**, and that is the shape taken here: `label_agreement` is orthogonal and pure, and `numbering_verified` keeps its exact published meaning so no model path can flip it. A reviewer who proposes folding the two together should be shown this paragraph.

**Three tasks edit `src/papertrace/templates/report_terminal.html.j2` and `src/papertrace/disclosures.py`** — 4, 6 and 7. That template is the only allow-list of the four formats, so each new key needs a branch in it, and the line numbers shift under each other. **Locate every insertion point by the surrounding code quoted in the step, never by the line number**, and re-grep the template before editing it. Task 7 must *extend* the `claim_pairing` producer Task 4 created, not rewrite it.

---

### Task 1: Extract the model seam into `ask.py`, and record the model per call site

**Why:** `refs` is about to need a model call, and CLAUDE.md's rule — *"`check.py` is the only module that calls a model"* — was protecting the seam, not the module. Extracting it makes the real rule *enforceable* rather than conventional: one file in `src/` shells out, and a test can say so.

It also fixes a live misattribution hazard. `_LAST_MODEL` is a single module global overwritten by **every** `_ask` call, and `cli.py:898` renders it as the report's `Checker:` line. Once `refs` calls the same seam, a run whose judging made zero calls (every cited source `not_retrieved`, so `by_slug` is empty) would print the *reference-list* model as the judge of verdicts it never saw.

**No behaviour change.** Same flags, same timeout, same retry count, same `Checker:` line on every existing path.

**Files:**
- Create: `src/papertrace/ask.py`
- Modify: `src/papertrace/check.py` (imports at `:10-31`; `CLAUDE_TIMEOUT` `:33`; `ASK_ATTEMPTS` `:35-38`; `_LAST_MODEL` + `last_model` `:40-46`; `claude_available` `:194-195`; `_SCRATCH_CWD`/`_scratch_cwd` `:198-208`; `_ask` `:211-238`; the three call sites `:314`, `:992`, `:994`)
- Modify: `src/papertrace/cli.py:838` (import `claude_available` from `.ask`)
- Modify: `src/papertrace/wizard.py:29` (import `ASK_ATTEMPTS, claude_available` from `.ask`)
- Modify: `tests/test_ask_boot_hygiene.py` (retarget `check_mod.subprocess` → `ask_mod.subprocess`, 4 lines)
- Modify: `tests/test_wizard.py:670` (import `ASK_ATTEMPTS` from `papertrace.ask`)
- Test: `tests/test_ask.py` *(new)*

**Interfaces:**
- Consumes: nothing.
- Produces, all in `src/papertrace/ask.py`:
  - `_ask(prompt: str, model: str | None = None) -> str` — **this signature is frozen.** Sixty-two monkeypatch sites across nine test files replace it with a two-argument lambda. A third parameter breaks every one of them.
  - `for_site(name: str)` — context manager; attributes every `_ask` inside the block to `name`.
  - `model_for(site: str) -> str | None` — the model that answered at `site`, or `None`.
  - `SITE_CHECK = "check"`, `SITE_REFS = "refs"` — string constants, so a typo cannot silently split the dict into two half-populated keys.
  - `CLAUDE_TIMEOUT`, `ASK_ATTEMPTS`, `claude_available()`, `_scratch_cwd()` — moved verbatim.
  - `_parse_json_array(text) -> list[dict]` and `_parse_json_object(text) -> dict` — moved verbatim. They are shared model-reply plumbing, not judging logic: `reflist.py` needs the array parser in Task 5, and having it import `check` would defeat the whole point of making these two modules siblings of one seam. **Exactly one caller outside `check.py` exists** (`tests/test_coverage.py:10-14` imports `_parse_json_object`), verified by grep, so the move costs one import line.
- `check.py` keeps exporting `last_model() -> str | None` (two consumers: `cli.py:898` and `evals/provenance.py:66`), now delegating to `ask.model_for(ask.SITE_CHECK)`.

**The constraint that dictates the whole shape of this task.** `check.py` must keep calling the *bare name* `_ask(...)`, having imported it with `from .ask import _ask`. A bare global lookup resolves in `check`'s own module namespace at call time, so `monkeypatch.setattr(check_mod, "_ask", fake)` — and the one dotted-string variant at `tests/test_check_provenance.py:354` — both still intercept. **Do not rewrite any call site as `ask._ask(...)`.** That would bypass every patch and turn 62 offline tests into live, paid model calls that fail in CI with no network. This is the single most dangerous mistake available in this task.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ask.py`:

```python
"""The seam, and the two things about it that are now mechanically checked.

`_ask` is the only place this codebase shells out to a model. That used to be a
convention stated in CLAUDE.md; since `refs` also needs a model reading of the
bibliography, it is a rule with a test behind it.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import check as check_mod  # noqa: E402


class _Completed:
    def __init__(self, model: str | None):
        self.returncode = 0
        payload: dict = {"result": "ok"}
        if model:
            payload["model"] = model
        self.stdout = json.dumps(payload)
        self.stderr = ""


def test_only_ask_py_shells_out_to_a_model():
    """One file in `src/` may run a subprocess, and this is the test that says so.

    CLAUDE.md used to phrase this as "check.py is the only module that calls a
    model", which was a convention with nothing enforcing it. `refs` needs a
    reading of the bibliography from a model, so the rule moves to the seam —
    where it can be checked.
    """
    src = Path(__file__).resolve().parent.parent / "src" / "papertrace"
    shelling = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "subprocess.run(" in p.read_text()
    )
    assert shelling == ["ask.py"], shelling


def test_two_call_sites_do_not_clobber_each_others_model(monkeypatch):
    """The report's `Checker:` line names the model that judged the claims.

    One global cannot carry that once two stages call the seam: a run whose
    judging made zero calls would print the reference-list model as its judge.
    """
    models = iter(["claude-opus-5", "claude-haiku-4-5"])
    monkeypatch.setattr(ask_mod.subprocess, "run",
                        lambda cmd, **kw: _Completed(next(models)))
    monkeypatch.setattr(ask_mod, "_MODELS", {})

    with ask_mod.for_site(ask_mod.SITE_REFS):
        ask_mod._ask("read this bibliography")
    with ask_mod.for_site(ask_mod.SITE_CHECK):
        ask_mod._ask("judge this claim")

    assert ask_mod.model_for(ask_mod.SITE_REFS) == "claude-opus-5"
    assert ask_mod.model_for(ask_mod.SITE_CHECK) == "claude-haiku-4-5"


def test_a_site_nobody_called_has_no_model_rather_than_a_plausible_one(monkeypatch):
    """`model_for` on an unused site is None, not the other site's model.

    A reference-list reading that never happened must not be able to name a
    model, and a judging pass that never happened must not inherit one.
    """
    monkeypatch.setattr(ask_mod.subprocess, "run",
                        lambda cmd, **kw: _Completed("claude-opus-5"))
    monkeypatch.setattr(ask_mod, "_MODELS", {})

    with ask_mod.for_site(ask_mod.SITE_REFS):
        ask_mod._ask("read this bibliography")

    assert ask_mod.model_for(ask_mod.SITE_CHECK) is None


def test_a_reply_that_names_no_model_does_not_erase_the_one_recorded(monkeypatch):
    """`claude -p` does not always report which model answered.

    A silent call is no evidence that the model changed, so the recorded name
    stands. Overwriting it with None would make the report stop naming a judge
    that did in fact judge.
    """
    replies = iter([_Completed("claude-opus-5"), _Completed(None)])
    monkeypatch.setattr(ask_mod.subprocess, "run", lambda cmd, **kw: next(replies))
    monkeypatch.setattr(ask_mod, "_MODELS", {})

    with ask_mod.for_site(ask_mod.SITE_CHECK):
        ask_mod._ask("judge one")
        ask_mod._ask("judge two")

    assert ask_mod.model_for(ask_mod.SITE_CHECK) == "claude-opus-5"


def test_for_site_restores_the_previous_site_on_the_way_out(monkeypatch):
    """Nesting must not leak. A leaked site is a misattributed model."""
    monkeypatch.setattr(ask_mod, "_SITE", "outer")
    with ask_mod.for_site(ask_mod.SITE_REFS):
        assert ask_mod._SITE == ask_mod.SITE_REFS
    assert ask_mod._SITE == "outer"


def test_check_still_exposes_the_seam_its_tests_patch():
    """Sixty-two monkeypatch sites across nine test modules do
    `monkeypatch.setattr(check_mod, "_ask", ...)`, and one does it by the dotted
    string `"papertrace.check._ask"`. Both need `_ask` to be an attribute of
    `check`, and need `check`'s own call sites to use the bare name so the patch
    is seen. If this assertion ever fails, those tests are not failing — they
    are making live, paid model calls."""
    assert check_mod._ask is ask_mod._ask


def test_the_judging_retry_attempts_exactly_ask_attempts_times(monkeypatch):
    """`ASK_ATTEMPTS` exists so the wizard's advertised worst-case bill cannot
    drift from the real retry policy — `CHANGELOG.md` records the incident. The
    retry loop did not read it: it hardcoded one retry, and matched the constant
    only because the constant happens to be 2."""
    calls = []

    def boom(prompt, model=None):
        calls.append(1)
        raise RuntimeError("transient")

    monkeypatch.setattr(check_mod, "_ask", boom)
    monkeypatch.setattr(check_mod, "ASK_ATTEMPTS", 3)
    with pytest_raises_runtimeerror():
        check_mod._ask_judge("prompt", None)
    assert len(calls) == 3, calls
```

Add at the top of that file, after the imports:

```python
import contextlib

import pytest


@contextlib.contextmanager
def pytest_raises_runtimeerror():
    with pytest.raises(RuntimeError):
        yield
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_ask.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'papertrace.ask'`. That is a legitimate first failure for a task whose first deliverable is the module.

- [ ] **Step 3: Create `src/papertrace/ask.py`**

```python
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
    leaked site is a model名 attributed to a stage that did not use it, which
    is the bug this module exists to prevent.
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
```

The two parsers move verbatim — they strip a fenced code block, then take the outermost bracket pair. `import re` joins `import json` at the top of `ask.py` for them.

**Note for the implementer:** the docstring of `for_site` above contains a typo introduced by the plan author — `model名`. Write it as `model name`. Reported here rather than silently, because a plan that prescribes code verbatim is responsible for the characters it prescribes.

- [ ] **Step 4: Rewire `check.py`**

Delete from `check.py`: `import shutil`, `import subprocess`, `import tempfile` (verify none is used elsewhere in the file first — `grep -n 'shutil\.\|subprocess\.\|tempfile\.' src/papertrace/check.py` must come back empty after the edit), and the definitions of `CLAUDE_TIMEOUT`, `ASK_ATTEMPTS`, `_LAST_MODEL`, `claude_available`, `_SCRATCH_CWD`, `_scratch_cwd`, `_ask`, `_parse_json_array` and `_parse_json_object`.

`import json` and `import re` **stay** — `check.py` uses both for its own prompt assembly and label regexes. Confirm with grep before deleting anything.

Add to the import block, `I`-sorted (`from .ask import ...` sorts before `from .models import ...`):

```python
from .ask import (
    ASK_ATTEMPTS,
    CLAUDE_TIMEOUT,
    SITE_CHECK,
    _ask,
    _parse_json_array,
    _parse_json_object,
    claude_available,
    for_site,
    model_for,
)
```

Then update the one external importer: `tests/test_coverage.py:10-14` imports `_parse_json_object` from `papertrace.check` — point it at `papertrace.ask`.

`CLAUDE_TIMEOUT` and `claude_available` are re-exported unused-by-`check` names; ruff's `F401` will flag them. Keep them and silence it explicitly, because two external modules import them from here today and this task is not the place to change that:

```python
__all__ = [..., "ASK_ATTEMPTS", "CLAUDE_TIMEOUT", "claude_available", "last_model"]
```

If `check.py` has no `__all__`, add the `# noqa: F401` to the import block instead. Check which before writing.

Replace `last_model` (`check.py:45-46`) with:

```python
def last_model() -> str | None:
    """The model that judged the claims, for the report's `Checker:` line.

    Reads the `check` site rather than one shared global: `refs` calls the same
    seam now, and a run whose judging made zero calls — every cited source
    `not_retrieved`, so there is nothing to judge — would otherwise report the
    reference-list model as the judge of verdicts it never produced.
    """
    return model_for(SITE_CHECK)
```

Add, immediately below it:

```python
def _ask_judge(prompt: str, model: str | None = None) -> str:
    """One judging call, attributed to this stage and retried `ASK_ATTEMPTS` times.

    The retry was a hardcoded second attempt that never read `ASK_ATTEMPTS` —
    it matched only because the constant is 2. The wizard prints a worst-case
    bill derived from that constant, and `CHANGELOG.md` records the incident
    where the advertised ceiling drifted from the real policy, so the two are
    wired together here rather than left agreeing by coincidence.
    """
    with for_site(SITE_CHECK):
        for attempt in range(ASK_ATTEMPTS):
            try:
                return _ask(prompt, model)
            except (RuntimeError, ValueError):
                if attempt == ASK_ATTEMPTS - 1:
                    raise
        raise AssertionError("unreachable: ASK_ATTEMPTS must be >= 1")
```

Then replace the three call sites:

- `check.py:314` (in `extract_claims`): `_ask(prompt, model)` → `_ask_judge(prompt, model)`. **This gives extraction the retry it did not have.** That is a behaviour change, so it is deliberate and belongs in the report: extraction is one call whose failure fails the whole run, and it was the only model call in the codebase with no retry at all. The wizard's ceiling already assumed `1 + ASK_ATTEMPTS * sources`, i.e. it already charged extraction one attempt and no retry — so the ceiling is now one attempt *low*. Fix it in the same task: `wizard.workload`'s `"model_calls_max"` becomes `ASK_ATTEMPTS * (1 + cited_source_calls)`, and `tests/test_wizard.py:677` updates with it.
- `check.py:991-994`: replace the whole `try/except` with `raw = _ask_judge(prompt, model)`.

- [ ] **Step 5: Retarget the boot-hygiene tests**

In `tests/test_ask_boot_hygiene.py`, change the import to `from papertrace import ask as ask_mod` and replace all five `check_mod` references with `ask_mod`. Nothing else in that file changes — it tests `_ask`'s body, which moved intact.

- [ ] **Step 6: Update the two production imports and the one test import**

- `src/papertrace/cli.py:838` — `claude_available` moves to `from .ask import claude_available`; keep the rest of that line importing from `.check`.
- `src/papertrace/wizard.py:29` — `from .ask import ASK_ATTEMPTS, claude_available`, and delete those two names from the `.check` import if it becomes empty.
- `tests/test_wizard.py:670` — `from papertrace.ask import ASK_ATTEMPTS`.

- [ ] **Step 7: Run everything**

```bash
~/anaconda3/bin/python -m pytest tests/test_ask.py tests/test_ask_boot_hygiene.py -q
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

Expected: all green, `881 + new` tests. **Paste the real output into the report.** If any test outside `tests/test_ask*.py`, `tests/test_wizard.py` changed behaviour, stop — this task is supposed to be behaviour-neutral everywhere except the two changes named in Step 4, and any other movement means a call site was rewritten as `ask._ask(...)`.

- [ ] **Step 8: Gate 4 — prove honest degradation still holds**

```bash
~/anaconda3/bin/python -m pytest tests/test_check_provenance.py tests/test_supplements.py tests/test_coverage.py -q
```

These are the modules that assert a failed call yields `unchecked` with a note and never a default verdict. Paste the output. State in the report that Gate 5 does not apply (no ingest, highlight or report code changed) and that Gate 2 does not apply (no new JSON field).

- [ ] **Step 9: Commit**

```bash
git add src/papertrace/ask.py src/papertrace/check.py src/papertrace/cli.py \
        src/papertrace/wizard.py tests/test_ask.py tests/test_ask_boot_hygiene.py \
        tests/test_wizard.py
git commit -m "ask: one seam, one file, and the model recorded per call site

$(cat <<'BODY'
`refs` is about to read the bibliography with a model, so "check.py is the only
module that calls a model" becomes "one file shells out, and a test says which".

The judging model was a single global overwritten by every call. With two
stages calling the seam, a run whose judging made zero calls would print the
reference-list model as the judge of verdicts it never saw. The site travels
out of band in a context manager so `_ask`'s signature stays frozen — sixty-two
test sites monkeypatch it with a two-argument lambda.

The retry now reads ASK_ATTEMPTS instead of hardcoding a second attempt, and
extraction gets the retry it never had. The wizard's advertised ceiling moves
with it.
BODY
)"
```

### Task 2: The flat-text second reading

**Why:** `reconcile` is about to arbitrate between four readings of the
bibliography instead of two, and the cheapest of the two new ones is a plain
pymupdf parse of the same PDF the run already has open. It costs nothing extra
on a pymupdf run (identical to the existing parse — Task 6 decides whether to
skip it there) and, on a docling run, gives a second opinion whose failure
modes do not overlap docling's: docling can render five bibliography rows as a
GFM table and read the numeral column wrong; pymupdf never sees a table at all
and cannot make that particular mistake, though it has its own (a
multi-column layout's reading order). Two readings that fail differently are
worth more than one read twice.

This task adds exactly one function and wires it nowhere — `reconcile` still
takes two candidates until Task 6. The function is deliberately a *reading*,
not an ingest: it must be cheap enough to call on every run without asking
permission, so it may not write a source map, call a model, or touch the
network. `--parse-only` already carries the scar of getting the "read without
disturbing the case" contract wrong once — reading the manuscript into the
case's own `ingest/manuscript/` slot and then returning early left that slot
describing a paper the run never finished auditing (`cli.py:543-549`). A
second reading taken purely to vote on a label must not risk the same mistake
against the case's real `source_map.json`, so it gets no directory, no
`write_outputs` call, and no `out_dir` argument to accidentally point at one.

**Files:**
- Modify: `src/papertrace/ingest/__init__.py` — add `references_span_flat`
  immediately after `ingest_pdf` (before the `# --- shared writers ---`
  banner), and add its name to `__all__`.
- Test: `tests/test_ingest_flat_reading.py` *(new)*. Neither existing ingest
  test module fits: `tests/test_ingest_backends.py` stubs docling's objects
  and never builds a real PDF or calls pymupdf at all, and
  `tests/test_reference_list.py` hand-builds `SourceMap`s in memory to test
  `references_span`'s boundary logic — this function's whole point (a real
  file on disk, read, and *nothing* written back) can only be checked against
  a real path and a real `cwd`, which would dilute either file's stated scope.
  A third module keeps that assertion legible as its own thing.

**Interfaces:**
- Consumes: `ingest_blocks_pymupdf` (`ingest/pymupdf_.py`, already imported
  into `ingest/__init__.py`), `references_span` (same), `SourceMap`
  (`models.py`, already imported into `ingest/__init__.py`).
- Produces: `ingest.references_span_flat(pdf_path: Path) -> tuple[str, bool]`
  — same contract as `references_span`: the reference-list text, and whether
  it was picked up again after an intervening section.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest_flat_reading.py`:

```python
"""A second, independent reading of the bibliography — no ingest, no write.

`references_span_flat` exists to give `reconcile` a plain pymupdf reading of
the References section, cheap enough to run on every docling run without
asking permission. Neither existing ingest test module fits what this needs
checked: `test_ingest_backends.py` stubs docling and never touches a real PDF
or pymupdf, and `test_reference_list.py` hand-builds `SourceMap`s in memory.
This module's central claim — a real file read, and NOTHING written back to
disk — needs a real path and a real `cwd` to check at all.
"""

import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 module name (the bare `fitz` import is deprecated)
except ImportError:  # pragma: no cover - older PyMuPDF exposes only `fitz`
    import fitz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.ingest import references_span_flat  # noqa: E402
from papertrace.refs import parse_references  # noqa: E402


def _paper(path: Path, *, with_references: bool) -> Path:
    """A two-page PDF: page 1 is body text, page 2 is a References section
    with three numbered entries, or an unrelated heading when
    `with_references` is False."""
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 100), "A Study of Something", fontsize=16)
    page1.insert_text((72, 140), "Body text citing [1], [2] and [3] here.", fontsize=11)
    page2 = doc.new_page()
    if with_references:
        page2.insert_text((72, 100), "References", fontsize=14)
        page2.insert_text((72, 130), "[1] Alpha A. A first paper. 2019.", fontsize=11)
        page2.insert_text((72, 150), "[2] Beta B. A second paper. 2020.", fontsize=11)
        page2.insert_text((72, 170), "[3] Gamma C. A third paper. 2021.", fontsize=11)
    else:
        page2.insert_text((72, 100), "Acknowledgements", fontsize=14)
        page2.insert_text((72, 130), "We thank nobody in particular.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def test_the_flat_reading_holds_the_printed_entries_and_parses_to_the_same_count(tmp_path):
    """The whole point of a second reading: it must actually see the
    bibliography, not merely avoid crashing on one."""
    pdf = _paper(tmp_path / "paper.pdf", with_references=True)

    text, resumed = references_span_flat(pdf)

    assert resumed is False
    for surname in ("Alpha", "Beta", "Gamma"):
        assert surname in text, f"{surname} missing from the flat reading — {text!r}"
    assert len(parse_references(text)) == 3


def test_a_paper_with_no_references_heading_degrades_honestly(tmp_path):
    """No heading found means `(\"\", False)` — never an exception, and never
    a guess at where the list might be."""
    pdf = _paper(tmp_path / "paper.pdf", with_references=False)

    assert references_span_flat(pdf) == ("", False)


def test_the_flat_reading_writes_nothing_to_disk(tmp_path, monkeypatch):
    """This is a READING, not an ingest. `--parse-only` already carries the
    scar of a second read touching the case's real `source_map.json`
    (`cli.py:543-549`) — a reading taken purely to vote on a label must not
    risk doing that even by accident, so it never calls `write_outputs`, never
    takes an `out_dir`, and never touches the cwd it happens to run in."""
    monkeypatch.chdir(tmp_path)
    pdf = _paper(tmp_path / "sources" / "paper.pdf", with_references=True)

    references_span_flat(pdf)

    assert list(tmp_path.rglob("source_map.json")) == []
    assert list(tmp_path.rglob("clean.md")) == []
    assert list(tmp_path.rglob("annotated.md")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_ingest_flat_reading.py -q
```

Expected: `ImportError: cannot import name 'references_span_flat' from 'papertrace.ingest'` — the legitimate first failure for a task whose first deliverable is the function.

- [ ] **Step 3: Add `references_span_flat` to `src/papertrace/ingest/__init__.py`**

Insert immediately after `ingest_pdf` (which ends with `write_outputs(smap, out_dir); return smap`) and before the `# --- shared writers ---` banner:

```python
def references_span_flat(pdf_path: Path) -> tuple[str, bool]:
    """A second, independent reading of the bibliography, from flat pymupdf text.

    Same contract as `references_span`: the reference-list text, and whether it
    was picked up again after an intervening section. Built by running the
    pymupdf block extraction and handing its blocks straight to
    `references_span`, which reads only `smap.blocks` — nothing else on the
    `SourceMap` needs to be genuine, so no converter tag, no fingerprint, no
    declared title are computed here.

    Exists because docling and pymupdf lose different things. Docling's table
    model can render bibliography rows as a GFM table and misplace the numeral
    column; pymupdf never renders a table at all, so it cannot make that
    mistake — it has its own instead (a multi-column layout's reading order).
    Two readings whose failure modes do not overlap are worth checking against
    each other, and cheaply: this is free on a docling run and identical to the
    existing parse on a pymupdf one.

    **Writes nothing, ever.** This is a READING, not an ingest — it has to be
    cheap enough to run on every docling run without asking permission, so it
    never calls `write_outputs`, takes no `out_dir`, and touches neither a
    model nor the network. `--parse-only` already carries the scar of a
    read that forgot this distinction: it used to risk overwriting the case's
    own `ingest/manuscript/source_map.json` with a reading of a paper the run
    was not finishing (`cli.py:543-549`, fixed by reading into a scratch
    `tempfile.TemporaryDirectory()` instead). A second reading taken purely to
    vote on a citation label must not reopen that risk, so it is given no
    directory to write into at all.
    """
    pages, blocks = ingest_blocks_pymupdf(pdf_path)
    smap = SourceMap(doc=pdf_path.name, pages=pages, blocks=blocks)
    return references_span(smap)
```

Update `__all__`:

```python
__all__ = ["ingest_pdf", "references_section", "references_span", "references_span_flat",
           "available_backends", "resolve_backend"]
```

No new imports: `ingest_blocks_pymupdf`, `references_span` and `SourceMap` are already imported at the top of this module (`from .pymupdf_ import (declared_title, ingest_blocks_pymupdf, references_section, references_span)` and `from ..models import Block, SourceMap, manuscript_fingerprint`).

- [ ] **Step 4: Run the new tests, then the full ingest and reference-list suites**

```bash
~/anaconda3/bin/python -m pytest tests/test_ingest_flat_reading.py -q
~/anaconda3/bin/python -m pytest tests/test_ingest*.py tests/test_reference_list.py -q
ruff check src/papertrace/ingest/__init__.py tests/test_ingest_flat_reading.py
```

Paste the real output into the report.

- [ ] **Step 5: Gate 5 — say so explicitly, then run it anyway**

Gate 5 reads "Tasks 1–8 change no ingest, highlight or report *code*", and
this task **does** touch a file under `ingest/` — so the gate is not skipped
silently, it is ruled on: `references_span_flat` is a brand-new function with
no existing caller, and `ingest_pdf` (the one function `--run`/`--refs` actually
calls) is edited nowhere. There is no code path for the demo to regress
through, because nothing reaches the new function yet. State this in the
report rather than assume it, and back it with the two other things Gate 5
protects — the ingest and reference-list suites, run above in Step 4, plus a
full-suite pass so nothing else moved:

```bash
~/anaconda3/bin/python -m pytest -q
```

Paste the output. If anything outside `tests/test_ingest_flat_reading.py`
changed status, stop — this task is additive by construction and any other
movement means `ingest_pdf` or `write_outputs` was touched by mistake.

- [ ] **Step 6: Commit**

```bash
git add src/papertrace/ingest/__init__.py tests/test_ingest_flat_reading.py
git commit -m "ingest: a free second reading of the bibliography, and a promise it writes nothing

$(cat <<'BODY'
reconcile is about to arbitrate four readings instead of two. The cheapest new
one is a plain pymupdf parse of the PDF the run already has open — free on a
docling run, and a second opinion whose failure modes do not overlap docling's
own (a mis-rendered bibliography table versus a scrambled multi-column reading
order).

references_span_flat is a READING, not an ingest: no out_dir, no
write_outputs, no model, no network. --parse-only already carries the scar of
a read that blurred that line and risked the case's own source_map.json
describing a paper the run never finished auditing. Nothing calls the new
function yet — Task 6 wires it into reconcile.
BODY
)"
```

---

### Task 3: `label_agreement`, the vocabulary, and the persisted fields

**Why:** This is the pure-function heart of per-label corroboration, and this
task ends with **no caller**. `label_agreement` is written, tested against
every rule the design specifies, and then left unreached — Task 4 uses it to
withhold verdicts, Task 6 wires the pymupdf and LLM candidates that make it
worth calling. Say this plainly rather than let a reviewer flag a function
nothing calls: the alternative is landing the vocabulary, the persisted
fields and the withholding logic in one task, which is the shape of change
this plan's own "one behavioural change per PR" rule forbids. The six
`Reconciliation` fields and the eight `RefManifest` fields are the same
story — state that gets written and read back by a round-trip test, with
nothing yet setting them to anything but their defaults in a real run.

**Files:**
- Modify: `src/papertrace/refs.py` — one new import (`itertools`, `I`-sorted
  before `re`); `LABEL_AGREEMENT` and `label_agreement` inserted between
  `_first_divergence` (ends at line 720) and `reconcile` (starts at line 723);
  six new fields on `Reconciliation` (lines 580–606, appended before the
  dataclass's closing blank line).
- Modify: `src/papertrace/models.py` — one new field on `RefEntry` (lines
  492–517, appended after `supplements`); eight new fields on `RefManifest`
  (lines 539–572, appended after `manuscript_supplements`, before
  `def document`); `to_json` (line 643) and `from_json` (line 667) gain the
  eight keys.
- Modify: `schemas/refs_manifest.schema.json` — nine additive properties (the
  eight manifest-level fields, plus `seen_in` on an entry).
- Test: `tests/test_label_agreement.py` *(new)*.

**Interfaces:**
- Consumes: `RefEntry` (`models.py`), `_same_work` (`refs.py`, unchanged —
  the pairwise "do these two entries name the same paper" primitive this
  function is built on).
- Produces:
  - `refs.LABEL_AGREEMENT = ("agreed", "single", "disputed", "absent")` — a
    published vocabulary tuple, so a future consumer (Task 4's withholding
    filter, Task 6's disclosure) has something to validate membership
    against rather than trusting a bare string.
  - `refs.label_agreement(candidates: dict[str, list[RefEntry]], body: set[str]) -> dict[str, str]`.
  - Six new fields on `refs.Reconciliation`, all defaulted.
  - Eight new fields on `models.RefManifest`, all defaulted, additive in
    `to_json`/`from_json`.
  - One new field on `models.RefEntry`: `seen_in: list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_label_agreement.py`:

```python
"""Per cited label: do the readings that carry it still name one paper?

`label_agreement` is the second, independent axis `reconcile` gains in this
plan — see `.superpowers/facts/reconcile.md` — and it is deliberately not
built on `_first_divergence`, which zips two readings by POSITION. A reading
that split one entry into two shifts every label after it by one, so
comparing "reading A's 6th entry" against "reading B's 6th entry" would report
a disagreement about the wrong label, or an agreement that is really two
different labels lining up by coincidence. The join key here is `e.num`, the
printed numeral — the same fact `parse_references` already treats as ground
truth wherever the converter left it legible.

Nothing in `src/` calls `label_agreement` yet. This module tests it alone, the
way `tests/test_reflist.py` will later test `reflist.py` alone — the design's
own promise is that the deterministic parts are testable without a model, and
this is the part with no model in it at all.
"""

import json
import sys
from pathlib import Path

import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace.models import (  # noqa: E402
    DOCUMENT_KINDS,
    REF_STATUSES,
    RefEntry,
    RefManifest,
)
from papertrace.refs import LABEL_AGREEMENT, _entry, label_agreement  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _work(num: str, doi: str) -> RefEntry:
    """Built through `_entry` — the production path, which parses the DOI out
    of `raw` the same way a real reading would, rather than setting it by
    hand and bypassing the exact code every candidate actually goes through."""
    return _entry(num, f"Author for entry {num}. A distinctive title. doi:{doi}")


# --- the five states, one rule each ----------------------------------------


def test_two_readings_naming_the_same_work_at_a_label_agree():
    """The base case a corroborating second reading exists to produce."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x5")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "agreed"}


def test_two_readings_naming_different_works_at_a_label_are_disputed():
    """The failure this whole feature exists to catch: two readings of one
    bibliography naming different papers at the same printed label."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "disputed"}


def test_three_readings_two_agreeing_and_one_not_is_disputed_not_majority():
    """`reconcile`'s own rule, restated for this function: 'a non-unique
    match is refused, never ranked.' Two votes for one paper and one for
    another must not out-vote the dissent into a false `agreed` — if this
    regresses to a majority rule, it will look like an improvement (fewer
    labels withheld) right up until it prints a verdict against the paper the
    minority reading was right about."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x5")],
        "llm": [_work("5", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "disputed"}


def test_one_reading_carrying_a_label_is_single():
    """`single` must print (invariant 4): a pymupdf-backend run whose only
    other candidate was discarded still has ONE reading, and every one of its
    labels is `single` — not `absent`, which would say nobody read it at all,
    and not `disputed`, which would withhold a verdict nothing contradicts."""
    candidates = {"parsed": [_work("5", "10.1000/x5")]}
    assert label_agreement(candidates, {"5"}) == {"5": "single"}


def test_no_reading_carrying_a_label_is_absent():
    """Already `not_retrieved` downstream — `absent` just names the reason: no
    candidate reading has anything to say about this label at all."""
    candidates = {
        "parsed": [_work("5", "10.1000/x5")],
        "pymupdf": [_work("5", "10.1000/x5")],
    }
    assert label_agreement(candidates, {"9"}) == {"9": "absent"}


# --- the two entries that must not speak for their label --------------------


def test_a_boundary_ambiguous_entry_does_not_corroborate_a_matching_reading():
    """Invariant 5: a `boundary_ambiguous` entry has already refused to say
    what paper its label names — `parse_references` sets it precisely because
    the entry's start is a guess. Counting it as a second vote would turn that
    refusal into evidence. Here it is given the SAME doi as the one real
    voter, so a regression that let it speak would wrongly report `agreed`
    instead of the correct `single`."""
    ambiguous = _work("5", "10.1000/x5")
    ambiguous.boundary_ambiguous = True
    candidates = {
        "parsed": [ambiguous],
        "pymupdf": [_work("5", "10.1000/x5")],
    }
    assert label_agreement(candidates, {"5"}) == {"5": "single"}


def test_a_reading_carrying_the_same_label_twice_disputes_it_on_its_own():
    """A duplicate is judgeable, so it is `disputed` with no second opinion.

    This is the discriminating case: one reading is clean and agrees with
    nothing else, one reading carries [7] twice with two different DOIs, and
    there is no third reading to break anything. Under a "a duplicate simply
    does not vote" rule this comes back `single` and prints — meaning a verdict
    on [7] rests on whichever of the two papers `resolve_entry` happened to
    download. It must be `disputed`.
    """
    candidates = {
        "parsed": [_work("7", "10.1000/x7"), _work("7", "10.1000/x77")],
        "pymupdf": [_work("7", "10.1000/x7")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}


def test_a_duplicate_does_not_hide_a_disagreement_between_the_other_readings():
    """The same rule with two other readings that genuinely disagree, so the
    result is `disputed` for two independent reasons at once. Kept alongside the
    test above because that one pins the rule and this one pins that the rule
    does not accidentally short-circuit the pairwise comparison."""
    candidates = {
        "parsed": [_work("7", "10.1000/x7")],
        "pymupdf": [_work("7", "10.1000/x7"), _work("7", "10.1000/x77")],
        "llm": [_work("7", "10.1000/x9")],
    }
    assert label_agreement(candidates, {"7"}) == {"7": "disputed"}


def test_a_refused_entry_can_leave_a_label_single_and_that_is_deliberate():
    """The other half of the asymmetry, pinned so nobody "fixes" it.

    A `boundary_ambiguous` entry does not vote, so one clean reading leaves the
    label `single`, which prints. That is safe because `resolve_entry` honours
    `boundary_ambiguous` by setting `no_doi` and fetching nothing — the refused
    entry cannot be the source of any verdict, whichever way this function
    labels it. A duplicate has no such brake, which is why the two cases are
    treated differently."""
    refused = _work("7", "10.1000/x7")
    refused.boundary_ambiguous = True
    candidates = {"parsed": [refused], "pymupdf": [_work("7", "10.1000/x7")]}
    assert label_agreement(candidates, {"7"}) == {"7": "single"}


# --- the case this plan exists for ------------------------------------------


def test_the_real_manuscripts_shape_agrees_up_to_the_split_and_disputes_after():
    """The reported defect, reproduced without a model or a converter. A
    correct 22-entry reading and a 24-entry reading whose [12] is a
    split-title fragment: everything from [13] on is shifted one label late,
    so the label and the paper it names have come apart. [1]-[11] sit before
    the split and must still agree; [13] on must all be disputed, because the
    printed numeral now names a different work than it did in the correct
    reading. This is the six-wrong-verdicts case from
    `docs/superpowers/specs/2026-09-16-llm-reference-list-design.md`."""
    correct = [_work(str(n), f"10.1000/x{n}") for n in range(1, 23)]

    # the second reading: 1-11 intact, 12 a fragment (excluded from every
    # assertion below), 13-23 each carrying the DOI that belongs one label
    # earlier in `correct`, 24 an extra nobody cites
    split = (
        [_work(str(n), f"10.1000/x{n}") for n in range(1, 12)]
        + [_entry("12", "an unresolved title fragment with no doi of its own")]
        + [_work(str(n), f"10.1000/x{n - 1}") for n in range(13, 24)]
        + [_work("24", "10.1000/x99")]
    )

    body = {str(n) for n in range(1, 23)}
    agreement = label_agreement({"parsed": correct, "pymupdf": split}, body)

    for n in range(1, 12):
        assert agreement[str(n)] == "agreed", agreement
    for n in range(13, 23):
        assert agreement[str(n)] == "disputed", agreement


# --- the vocabulary, and the one wire-format check it does not have yet -----


def test_label_agreement_never_prints_outside_its_own_published_vocabulary():
    """`LABEL_AGREEMENT` is published so a future consumer — the withholding
    filter, the disclosure — has something to check membership against. If
    this function ever returned a fifth word, that check would silently pass
    it through as if it were one of the four this plan specified."""
    candidates = {
        "parsed": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "pymupdf": [_work("1", "10.1000/x1"), _work("2", "10.1000/x9")],
    }
    for state in label_agreement(candidates, {"1", "2", "3"}).values():
        assert state in LABEL_AGREEMENT


def test_ref_statuses_matches_its_own_schema_enum():
    """`tests/test_coverage.py` asserts `set(VERDICTS) == set(schema_enum)`
    for `results.schema.json`, but nothing did the equivalent for
    `REF_STATUSES` against `refs_manifest.schema.json` — the exact gap this
    plan's own facts file names. Modelled on that test's idiom, not a new
    one."""
    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    enum = schema["properties"]["entries"]["items"]["properties"]["status"]["enum"]
    assert set(REF_STATUSES) == set(enum)


def test_document_kinds_matches_its_own_schema_enum():
    """The existing check compares the schema against a hardcoded literal list
    rather than against `DOCUMENT_KINDS`, so adding a kind to the constant and
    to the schema while forgetting the literal leaves a green suite. Compare the
    constant, which is the thing `SourceJudgement.kind` is validated against."""
    schema = json.loads((ROOT / "schemas" / "results.schema.json").read_text())
    enum = (
        schema["properties"]["claims"]["items"]["properties"]["judgements"]
        ["items"]["properties"]["kind"]["enum"]
    )
    assert set(DOCUMENT_KINDS) == set(enum)


def test_the_title_check_vocabulary_matches_its_own_schema_enum():
    """`title_check` is the third undocumented vocabulary the facts file names.
    Its schema enum carries `null` as a real state — "too few words to tell" is
    not `mismatch` — so the comparison drops the null and compares the strings,
    and this docstring is where that asymmetry is recorded."""
    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    enum = schema["properties"]["entries"]["items"]["properties"]["title_check"]["enum"]
    assert None in enum, "null is a real title_check state and must stay in the enum"
    assert {e for e in enum if e is not None} == {"verified", "unverifiable", "mismatch"}


# --- Gate 2: the new persisted fields round-trip, and old manifests still load


def test_the_six_new_reconciliation_fields_round_trip_through_a_manifest(tmp_path):
    """Gate 2. `Reconciliation` itself is never serialised — its fields reach
    disk only via the `RefManifest` fields `cli._refs_pipeline` copies them
    into (Task 6's job). This test exercises that copy shape directly, against
    the schema, so the round trip is proven before anything wires it in."""
    m = RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="1", raw="x", status="paywalled")],
        numbering_corroborated=True,
        corroborating_readings=["parsed", "pymupdf"],
        labels_disputed=["13", "14"],
        labels_resolved=["13"],
        numbering_choice="llm_resolved",
        numbering_chosen_by="user",
        reflist_model="claude-opus-5",
        reflist_fields_discarded=["title"],
    )
    p = tmp_path / "refs_manifest.json"
    m.to_json(p)

    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(json.loads(p.read_text()), schema)

    back = RefManifest.from_json(p)
    assert back.numbering_corroborated is True
    assert back.corroborating_readings == ["parsed", "pymupdf"]
    assert back.labels_disputed == ["13", "14"]
    assert back.labels_resolved == ["13"]
    assert back.numbering_choice == "llm_resolved"
    assert back.numbering_chosen_by == "user"
    assert back.reflist_model == "claude-opus-5"
    assert back.reflist_fields_discarded == ["title"]

    # a manifest written before this feature carries none of these keys
    old = {"manuscript": "m.pdf", "entries": [{"num": "1", "raw": "x", "status": "paywalled"}]}
    p.write_text(json.dumps(old))
    older = RefManifest.from_json(p)
    assert older.numbering_corroborated is False
    assert older.corroborating_readings == []
    assert older.labels_disputed == []
    assert older.labels_resolved == []
    assert older.numbering_choice == ""
    assert older.numbering_chosen_by == ""
    assert older.reflist_model == ""
    assert older.reflist_fields_discarded == []


def test_seen_in_round_trips_on_an_entry_and_defaults_empty_on_an_older_one(tmp_path):
    """Gate 2. An entry seen only in the model's reading has to be spottable —
    `seen_in == ["llm"]` is what makes that visible on a real manifest, and it
    must not silently become `[]` on the way through JSON."""
    e = RefEntry(num="1", raw="x", status="retrieved", seen_in=["parsed", "llm"])
    m = RefManifest(manuscript="m.pdf", entries=[e])
    p = tmp_path / "refs_manifest.json"
    m.to_json(p)

    schema = json.loads((ROOT / "schemas" / "refs_manifest.schema.json").read_text())
    jsonschema.validate(json.loads(p.read_text()), schema)

    assert RefManifest.from_json(p).entries[0].seen_in == ["parsed", "llm"]

    old = {"manuscript": "m.pdf", "entries": [{"num": "1", "raw": "x", "status": "retrieved"}]}
    p.write_text(json.dumps(old))
    assert RefManifest.from_json(p).entries[0].seen_in == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_label_agreement.py -q
```

Expected: collection error — `ImportError: cannot import name 'LABEL_AGREEMENT' from 'papertrace.refs'`. That is the legitimate first failure for a task whose first deliverable is that name.

- [ ] **Step 3: Add the import**

In `src/papertrace/refs.py`, add `import itertools` above the existing `import re` (alphabetically first, so `ruff check`'s `I` rule keeps it there):

```python
from __future__ import annotations

import itertools
import re
from collections import Counter
```

- [ ] **Step 4: Add `LABEL_AGREEMENT` and `label_agreement` to `src/papertrace/refs.py`**

Insert between the end of `_first_divergence` (`return None`, line 720) and
the blank line before `def reconcile(` (line 723):

```python
# Published so a consumer has something to check membership against, rather
# than trusting a bare string a typo could silently narrow to three states.
LABEL_AGREEMENT = ("agreed", "single", "disputed", "absent")


def label_agreement(
    candidates: dict[str, list[RefEntry]],
    body: set[str],
) -> dict[str, str]:
    """Per cited label, do the readings that carry it still name one paper?

    The join key is `e.num` — the printed numeral, a recorded fact — never
    position. `_first_divergence` cannot be reused here: it zips two readings
    by POSITION, so on a reading that split one entry into two, it would
    compare reading A's 6th entry against reading B's 6th and report a
    disagreement (or a false agreement) about the wrong label entirely. This
    feature's whole point is the label, so the join has to be on it.

    A `boundary_ambiguous` entry does not speak for its label (invariant 5):
    it is a reading that has already refused to say what paper this label
    names, and counting it as a vote would turn that refusal into evidence.
    That can leave a label `single`, which is safe precisely because a refusal
    is self-limiting — `resolve_entry` honours `boundary_ambiguous` by setting
    `no_doi` and fetching nothing, so no verdict rests on it either way.

    A **duplicate** is not self-limiting and is therefore treated harder: a
    reading carrying one label twice makes that label `disputed` outright. Both
    carriers have DOIs, so one of them really would be downloaded, read and
    judged — and which one the printed numeral meant is exactly the question
    the duplicate raises. Downgrading that to `single` on the strength of the
    other reading would let a verdict rest on the coin-flip.

    Any disagreement among the voters that remain is `disputed`, never a
    majority (invariant 3): "a non-unique match is refused, never ranked" is
    this codebase's own rule for exactly this shape of choice, applied here to
    readings instead of provided files.
    """
    result: dict[str, str] = {}
    for label in body:
        voters: list[RefEntry] = []
        duplicated = False
        for entries in candidates.values():
            carriers = [e for e in entries if e.num == label and not e.boundary_ambiguous]
            if len(carriers) == 1:
                voters.append(carriers[0])
            elif carriers:
                duplicated = True  # see the docstring: a duplicate is judgeable
        if duplicated:
            result[label] = "disputed"
        elif not voters:
            result[label] = "absent"
        elif len(voters) == 1:
            result[label] = "single"
        else:
            agree = all(_same_work(x, y) for x, y in itertools.combinations(voters, 2))
            result[label] = "agreed" if agree else "disputed"
    return result
```

**Controller's correction to the interface contract.** The contract's invariant
6 said a duplicate "does not speak", which — combined with invariant 5 — would
have made one clean reading enough to carry the label as `single`. That is
wrong, and the asymmetry above is why: a refused entry resolves to nothing, so
`single` costs nothing, while a duplicated entry resolves to *one of two
papers*. The contract's own test list already said this case must come back
`disputed`; the implementation above is what makes that true for the reason
rather than by accident. Treat this paragraph as the ruling.

- [ ] **Step 5: Add the six new fields to `Reconciliation`**

In `src/papertrace/refs.py`, append after `ledger: dict = field(default_factory=dict)`
(the last line of the `Reconciliation` dataclass, line 606), before its
trailing blank lines:

```python
    # A second, independent axis from `verified`/`contested` above: whether
    # EVERY label the body cites was agreed by two or more readings, named.
    # `numbering_verified` keeps its exact current meaning through this whole
    # plan — nothing here may redefine it or be read as a substitute for it.
    corroborated: bool = False
    corroborating_readings: list[str] = field(default_factory=list)
    # Citation labels where two or more readings actively named different
    # papers — the set `label_agreement` marked `disputed`, and the set whose
    # verdicts a downstream withholding filter must drop.
    labels_disputed: list[str] = field(default_factory=list)
    # Labels that WERE in `labels_disputed` and were then settled — by a
    # targeted model call the user accepted, or by choosing one reading whole.
    labels_resolved: list[str] = field(default_factory=list)
    # How a disputed numbering was left, when the interactive escalation ran.
    # "" means the escalation never ran — nothing here has fired yet.
    choice: str = ""  # "" | withheld | llm_resolved | parsed | pymupdf
    # Who is responsible for `choice`. "user" is what makes an escalation
    # honest: a person consenting to proceed is an input, never evidence that
    # the numbering was verified.
    chosen_by: str = ""  # "" | default | user
```

- [ ] **Step 6: Add the eight new fields to `RefManifest`, and one to `RefEntry`**

In `src/papertrace/models.py`, append to `RefEntry` (after `supplements`, the
last field at line 517):

```python
    # which named readings of the bibliography (e.g. "parsed", "pymupdf",
    # "llm") contributed THIS entry. An entry seen only in the model's reading
    # must be spottable — `["llm"]` says so; `[]` on an older manifest means
    # never computed, not "seen nowhere".
    seen_in: list[str] = field(default_factory=list)
```

Append to `RefManifest` (after `manuscript_supplements`, the last field at
line 572, before `def document`):

```python
    # A second, independent axis from `numbering_verified` above: whether
    # EVERY label the body cites was agreed by >= 2 readings. Absent means
    # never computed — not "not corroborated", the same three-state
    # discipline as `numbering_ledger`.
    numbering_corroborated: bool = False
    corroborating_readings: list[str] = field(default_factory=list)
    # Absent means never computed, NOT "none disputed" — a manifest written
    # before this feature says nothing about disputes, it does not assert
    # there were none.
    labels_disputed: list[str] = field(default_factory=list)
    labels_resolved: list[str] = field(default_factory=list)
    # How a disputed numbering was left; "" means the interactive escalation
    # never ran. Never set from a model's own say-so alone.
    numbering_choice: str = ""  # "" | withheld | llm_resolved | parsed | pymupdf
    numbering_chosen_by: str = ""  # "" | default | user
    # The model that produced the LLM's structured reading of the
    # bibliography, when --llm-refs ran and produced anything usable. "" means
    # no such call was made, or nothing it proposed survived verification.
    reflist_model: str = ""
    # Which fields of the LLM's proposed reading could not be found verbatim
    # in either text it was shown, and were discarded rather than trusted.
    reflist_fields_discarded: list[str] = field(default_factory=list)
```

- [ ] **Step 7: Round-trip the eight `RefManifest` fields through `to_json`/`from_json`**

In `to_json` (line 643), add the eight keys after `"numbering_ledger": self.numbering_ledger,`
and before `"summary": {`:

```python
            "numbering_corroborated": self.numbering_corroborated,
            "corroborating_readings": self.corroborating_readings,
            "labels_disputed": self.labels_disputed,
            "labels_resolved": self.labels_resolved,
            "numbering_choice": self.numbering_choice,
            "numbering_chosen_by": self.numbering_chosen_by,
            "reflist_model": self.reflist_model,
            "reflist_fields_discarded": self.reflist_fields_discarded,
```

In `from_json` (line 667), add the eight matching `.get(...)` defaults after
`numbering_ledger=data.get("numbering_ledger", {}),` and before the closing
`)`:

```python
            numbering_corroborated=bool(data.get("numbering_corroborated", False)),
            corroborating_readings=data.get("corroborating_readings", []),
            # absent means never computed, not "none disputed" or "none resolved"
            labels_disputed=data.get("labels_disputed", []),
            labels_resolved=data.get("labels_resolved", []),
            numbering_choice=data.get("numbering_choice", ""),
            numbering_chosen_by=data.get("numbering_chosen_by", ""),
            reflist_model=data.get("reflist_model", ""),
            reflist_fields_discarded=data.get("reflist_fields_discarded", []),
```

`RefEntry.seen_in` needs no matching edit in `_ref_entry_from`: that function
builds its `kwargs` from `{k: v for k, v in d.items() if k in known and k != "supplements"}`,
where `known = {f.name for f in fields(RefEntry)}` — once `seen_in` is a field,
a manifest that carries the key gets it and one that does not falls through to
the dataclass default `[]`, with no special case required.

- [ ] **Step 8: Add the nine properties to `schemas/refs_manifest.schema.json`**

Add inside `properties.entries.items.properties`, alongside `boundary_ambiguous`
and `title_check`:

```json
          "seen_in": {
            "type": "array",
            "description": "Which named readings of the bibliography (e.g. \"parsed\", \"pymupdf\", \"llm\") contributed this entry. Absent means never computed — either an entry from a manifest written before this feature, or one whose candidate reading was never compared against others. An entry seen only in the model's reading is spottable as a list of length one.",
            "items": {
              "type": "string"
            }
          },
```

Add at the top level of `properties`, alongside `numbering_ledger`:

```json
    "numbering_corroborated": {
      "type": "boolean",
      "description": "True when every label the manuscript cites was agreed by two or more independent readings of the bibliography. A second, independent axis from `numbering_verified`, which is about matching the body's labels, not about readings agreeing with each other — a manifest can be verified and uncorroborated, or corroborated and unverified. Absent means never computed, not 'not corroborated'."
    },
    "corroborating_readings": {
      "type": "array",
      "description": "Which named readings (e.g. 'parsed', 'pymupdf', 'llm') fed `numbering_corroborated`, named rather than merely counted. Absent means never computed.",
      "items": {
        "type": "string"
      }
    },
    "labels_disputed": {
      "type": "array",
      "description": "Citation labels where two or more readings of the bibliography actively named different papers, so the verdict on any claim citing them was withheld rather than printed. Absent means never computed — NOT 'none disputed' — the same three-state discipline as `table_warnings`.",
      "items": {
        "type": "string"
      }
    },
    "labels_resolved": {
      "type": "array",
      "description": "Citation labels that were in `labels_disputed` and were then settled — by a targeted model call the user accepted, or by the user choosing one reading whole. Absent means never computed, not 'none resolved'.",
      "items": {
        "type": "string"
      }
    },
    "numbering_choice": {
      "type": "string",
      "enum": [
        "",
        "withheld",
        "llm_resolved",
        "parsed",
        "pymupdf"
      ],
      "description": "How a disputed numbering was left, when the interactive escalation ran. '' means the escalation never ran, which is true of most manifests. Never set from a model's own say-so alone — see `numbering_chosen_by`."
    },
    "numbering_chosen_by": {
      "type": "string",
      "enum": [
        "",
        "default",
        "user"
      ],
      "description": "Who is responsible for `numbering_choice`: '' when no escalation ran, 'default' when nobody was actually asked, 'user' when a person consented to it. A person consenting to proceed is an input, never evidence that the numbering was verified — `numbering_verified` cannot be set from this."
    },
    "reflist_model": {
      "type": "string",
      "description": "The model that produced the LLM's structured reading of the bibliography, when --llm-refs ran and produced anything usable. '' means no such call was made, or nothing it proposed survived verbatim verification against the two texts it was shown."
    },
    "reflist_fields_discarded": {
      "type": "array",
      "description": "Which fields of the LLM's proposed reading could not be found verbatim in either text it was shown, and were discarded rather than trusted. Absent or empty means either no LLM reading was attempted, or every field it proposed was verified.",
      "items": {
        "type": "string"
      }
    },
```

Nothing is added to `required`, matching the house convention this schema
already follows (no `"schema"` version field, `additionalProperties` never
`false`).

- [ ] **Step 9: Run the tests**

```bash
~/anaconda3/bin/python -m pytest tests/test_label_agreement.py -q
~/anaconda3/bin/python -m pytest tests/test_reference_reconciliation.py tests/test_report_viewer.py -q
~/anaconda3/bin/python -m pytest -q
ruff check src/papertrace/refs.py src/papertrace/models.py tests/test_label_agreement.py
```

Paste the real output into the report. The second command is the regression
check: `Reconciliation` and `RefManifest` are both touched by construction
here, and `test_reference_reconciliation.py` is the file with 18 direct
`reconcile()` call sites and the existing round-trip tests for the fields
already there — none of today's fields changed shape, so none of those 18
should move.

- [ ] **Step 10: State the gates explicitly**

- **Gate 2** (new JSON field ⇒ schema + round-trip test): satisfied by Step 8
  and the two round-trip tests in Step 1 — pasted, not asserted, in Step 9.
- **Gate 4** (touched `refs.py` ⇒ prove the failure path still degrades
  honestly): `refs.py` is touched, but only by addition — `reconcile`,
  `_covers`, `_same_work`, `_first_divergence` and every existing failure path
  are edited nowhere in this task. There is no failure path to re-prove
  because there is no new caller; Task 4 is where `label_agreement`'s output
  first reaches a verdict, and that is where this gate has real work to do.
- **Gate 5** (ingest/highlight/report code): does not apply. This task
  touches `refs.py`, `models.py` and a schema file — none of the three.
- **No wiring, and that is deliberate**: `label_agreement` has no caller,
  `Reconciliation`'s six new fields are never set to anything but their
  defaults by production code, and `RefManifest`'s eight are copied nowhere
  yet. A reviewer diffing this task against a real audit will see the
  function and the fields do nothing — that is the intended shape of a task
  whose only job is to make the next two tasks (4 and 6) additive rather than
  simultaneous with inventing the vocabulary they consume.

- [ ] **Step 11: Commit**

```bash
git add src/papertrace/refs.py src/papertrace/models.py schemas/refs_manifest.schema.json \
        tests/test_label_agreement.py
git commit -m "refs: per-label agreement across N readings, and the state to hold it

$(cat <<'BODY'
label_agreement joins readings of the bibliography on the printed numeral, not
position — _first_divergence zips by position and would compare the wrong
entries once a reading has split or merged one. A boundary_ambiguous entry and
a reading carrying its own label twice both speak for nobody, and any
disagreement among the voters that remain is disputed outright: no majority,
matching the "a non-unique match is refused, never ranked" rule this codebase
already applies to provided files.

Nothing calls this yet. Reconciliation gains six fields and RefManifest eight
(plus RefEntry.seen_in), all defaulted and additive, so the wire format exists
before Task 4 withholds a verdict with it and Task 6 wires the two new
candidate readings that make it worth calling.
BODY
)"
```

---

### Task 4: Withhold the verdict on a disputed citation label

**Why:** `label_agreement` (Task 3) can say that two or more readings of the
bibliography name *different papers* for the same printed label — `disputed`,
never a majority vote, per the codebase's own rule that "a non-unique match is
refused, never ranked." A claim citing that label has a source PaperTrace
fetched, but nobody can say it is the paper the manuscript actually meant. This
task is the one place that distinction becomes a verdict: the disputed label is
subtracted from the set `check.py` will judge, and the claim lands `unchecked`
— never a confident `supported`/`contradicted` about what may be the wrong
paper, and never `not_retrieved`, which is reserved for a source that could not
be obtained at all.

That last sentence is the whole reason this task adds a field instead of
reusing one. CLAUDE.md is explicit: *"`ClaimResult.unjudged_refs` holds only
those that could not be obtained — so an entry there means nobody read it."* A
withheld label's source **was** obtained — it sits on disk, ingested, readable
— and was set aside only because the reference list disagrees about which paper
it is. Filing it in `unjudged_refs` would report a retrieval gap that does not
exist and erase the one that does. So `withheld_refs` is a second field with a
second meaning, and a reviewer who "simplifies" the two back into one has
reintroduced exactly the laundering CLAUDE.md names. `not_retrieved` is wrong
for the same reason, one level up: it is check.py's own local, no-model-call
path for "nothing here was even fetched," and a disputed-but-fetched source
runs through it never.

This task also closes a real gap in the disclosure parity tests: they check
that every key in `CLAIM_KEYS` is declared and rendered, but nothing checks the
other direction — that every key a producer actually emits is *in*
`CLAIM_KEYS`. This task adds exactly such a key (`claim_pairing`), so it adds
the missing direction as a test first.

**Files:**
- Modify: `src/papertrace/check.py` (`check_claims` signature and body,
  `src/papertrace/check.py:862-953`)
- Modify: `src/papertrace/models.py` (`ClaimResult`, one new field, near
  `unjudged_refs` at `:814`)
- Modify: `src/papertrace/disclosures.py` (two new tokens, one run-level
  producer, one claim-level producer, `CLAIM_KEYS`, both wiring points)
- Modify: `src/papertrace/templates/report_terminal.html.j2` (one new
  run-level allow-list block, one new claim-level allow-list block, one gap
  tuple)
- Modify: `src/papertrace/templates/report.md.j2` (one new claim-level
  explicit-key block, one gap tuple — run-level needs nothing, it catches all)
- Modify: `src/papertrace/templates/report_editor.html.j2` (one new
  claim-level explicit-key block, one gap tuple — run-level needs nothing)
- Modify: `src/papertrace/cli.py` (`_check_pipeline`, the `check_claims` call
  site, `:869-872`)
- Modify: `schemas/results.schema.json` (`withheld_refs`, additive, next to
  `unjudged_refs` at `:194-200`)
- Create: `tests/test_withholding.py` (the `check_claims` behaviour)
- Modify: `tests/test_pipeline_split.py` (the `_check_pipeline` wiring — this
  module already drives the split stages, so it is where a "the manifest's
  disputed labels reach `check_claims`" test belongs). **There is no
  `tests/test_cli.py` and no `tests/test_check.py` in this repo** — 36 modules,
  verified with `ls tests/`. The CLI's stage wiring is tested in
  `test_pipeline.py` / `test_pipeline_split.py`, and `check_claims` is exercised
  from `test_multisource.py`, `test_supplements.py` and `test_check_provenance.py`.
- Modify: `tests/test_disclosure_parity.py` (the reverse-direction parity
  test, and extend the existing claim-disclosure-parity test)

**Interfaces:**
- Consumes:
  - `RefManifest.labels_disputed: list[str]` — added in **Task 3**, defaulted
    to `[]` via `.get(...)` in `from_json`, so it is never absent as an
    attribute and an old manifest loads with nothing disputed. This task's
    code must tolerate it being empty — which it does automatically, since an
    empty set withholds nothing.
  - `refs.LABEL_AGREEMENT` (Task 3) — not imported here, but `disputed` is
    documented as the set of labels `label_agreement` returned `"disputed"`
    for. `check.py` does not know or care how the set was computed.
- Produces:
  - `check_claims(..., *, disputed: set[str] | None = None)` — new
    keyword-only parameter, defaulted, so every existing call site (tests
    included) that omits it keeps today's behaviour exactly.
  - `ClaimResult.withheld_refs: list[str]` — new field, `default_factory=list`.
  - `disclosures.claim_disclosures()` gains a `claim_pairing` key (`CLAIM_KEYS`
    grows to seven entries).
  - `disclosures.run_disclosures()` gains a `labels_disputed` key.
  - `schemas/results.schema.json` gains `withheld_refs` on a claim object.

**Not this task's job:** `claim_pairing` has four texts in the design —
`withheld`, `resolved_by_model`, `chosen_by_user`, `corroborated`. This task
implements only `withheld`, because `withheld_refs` is the only field a pairing
state has written to `ClaimResult` so far. The other three read fields Tasks 6
and 7 add (the resolution call's output, the user's interactive choice, a
second agreeing reading) — they arrive as further branches in the *same*
producer function, not a second one, and not a second `CLAIM_KEYS` entry.
`corroborated` must be `level="info"` and gated on the claim already carrying a
warn-level disclosure, so it is silent on a clean claim — that gating is Task
7's requirement to implement, and is noted here only so nobody adds it early
in the wrong shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_withholding.py`:

```python
"""Withholding a disputed citation label's verdict, and the field that carries it.

A citation label whose readings of the bibliography disagree is not evidence
either way about the claim that cites it — it is evidence PaperTrace does not
know which paper was actually fetched under that label. `check_claims`'s
`disputed` parameter drops such a label from judgement without pretending the
source was never obtained: that distinction is `withheld_refs` versus
`unjudged_refs`, and this module is what regresses if the two get merged.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import check as check_mod  # noqa: E402
from papertrace.check import check_claims  # noqa: E402
from papertrace.models import Block, ClaimResult, RefEntry, RefManifest, SourceMap  # noqa: E402


def _write_source(case: Path, slug: str, text: str = "The rate was 84.3%.") -> None:
    """An ingested source: the text and the map that says where its one block is."""
    d = case / "ingest" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "annotated.md").write_text(f"<!-- block_0001, page 1 -->\n{text}\n")
    SourceMap(
        doc=f"{slug}.pdf",
        pages=1,
        blocks=[Block("block_0001", "text", 1, (0.0, 0.0, 10.0, 10.0), [], text)],
    ).to_json(d / "source_map.json")


def _entry(num: str, slug: str, status: str = "retrieved") -> RefEntry:
    return RefEntry(
        num=num, raw=f"ref {num}", status=status, slug=slug,
        pdf_path=f"/nonexistent/{slug}.pdf",
    )


def _manifest(*entries: RefEntry) -> RefManifest:
    return RefManifest(manuscript="paper.pdf", entries=list(entries))


def _reply(claim_id: int, verdict: str = "supported") -> str:
    return json.dumps([{
        "id": claim_id, "verdict": verdict, "note": "reads it",
        "source_page": 1, "source_block": "block_0001", "anchor_phrases": [],
    }])


def _unexpected_call(prompt, model=None):
    raise AssertionError("no source survived withholding — _ask must not be called")


def test_a_disputed_label_is_dropped_and_the_other_cited_source_is_still_judged(
    tmp_path, monkeypatch
):
    """Only the disputed label's source is subtracted from a co-cited claim —
    a targeted subtraction, not a claim killed because one of several labels
    it cites is in dispute. If this regresses to killing the whole claim, a
    reader loses a real verdict from the source that was never in question."""
    _write_source(tmp_path, "six-2019")
    _write_source(tmp_path, "nine-2021")
    manifest = _manifest(_entry("6", "six-2019"), _entry("9", "nine-2021"))
    monkeypatch.setattr(check_mod, "_ask", lambda prompt, model=None: _reply(1, "supported"))
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6", "9"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    slugs = {j.source_slug for j in claims[0].judgements}
    assert slugs == {"nine-2021"}
    assert claims[0].verdict == "supported"


def test_a_claim_whose_only_cited_source_is_disputed_is_unchecked_not_not_retrieved(
    tmp_path, monkeypatch
):
    """`not_retrieved` would say the source could not be obtained. It WAS
    obtained — the two readings of the bibliography just disagree about which
    paper the label names — so the only honest verdict is `unchecked`, and the
    disputed label must be named in the note so a reader knows which citation
    to check by hand. `_ask` must never be called: nothing survived to judge."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", _unexpected_call)
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert claims[0].verdict == "unchecked"
    assert claims[0].verdict != "not_retrieved"
    assert "6" in claims[0].note


def test_withheld_refs_holds_the_disputed_label_and_unjudged_refs_does_not(
    tmp_path, monkeypatch
):
    """The two fields mean different things: `unjudged_refs` is "nobody could
    get to it", `withheld_refs` is "it was read and set aside anyway". A label
    landing in both would tell the reader retrieval failed when it did not —
    the exact laundering CLAUDE.md names for `unjudged_refs`."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", _unexpected_call)
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert claims[0].withheld_refs == ["6"]
    assert claims[0].unjudged_refs == []


def test_disputed_none_and_empty_set_behave_identically(tmp_path, monkeypatch):
    """Every caller that predates this feature passes neither argument. `None`
    must be exactly as inert as an explicit empty set, or turning on this
    feature with nothing yet computed as disputed would change verdicts that
    used to be stable — the regression this whole task must not cause."""
    _write_source(tmp_path, "six-2019")
    manifest = _manifest(_entry("6", "six-2019"))
    monkeypatch.setattr(check_mod, "_ask", lambda prompt, model=None: _reply(1, "supported"))

    claims_none = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]
    check_claims(claims_none, manifest, tmp_path, backend="pymupdf", disputed=None)

    claims_empty = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]
    check_claims(claims_empty, manifest, tmp_path, backend="pymupdf", disputed=set())

    claims_default = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]
    check_claims(claims_default, manifest, tmp_path, backend="pymupdf")

    for other in (claims_empty, claims_default):
        assert other[0].verdict == claims_none[0].verdict == "supported"
        assert other[0].withheld_refs == claims_none[0].withheld_refs == []


def test_a_disputed_label_never_retrieved_stays_not_retrieved(tmp_path):
    """Withholding describes a source that WAS obtained. A label that is both
    disputed and, say, paywalled must not be relabelled `unchecked` — that
    would claim a model read something nobody ever fetched. `withheld_refs`
    is partitioned OUT of the retrieval filter's result, never out of the raw
    cited-labels list, so a label that never passed retrieval cannot appear
    there no matter what `disputed` says about it."""
    manifest = _manifest(_entry("6", "six-2019", status="paywalled"))
    claims = [ClaimResult(id=1, claim="x", location="Results", refs=["6"])]

    check_claims(claims, manifest, tmp_path, backend="pymupdf", disputed={"6"})

    assert claims[0].verdict == "not_retrieved"
    assert claims[0].withheld_refs == []


def test_withheld_refs_round_trips_and_validates_against_the_schema(tmp_path):
    """Gate 2: a new claim field is additive in the schema and round-trips
    through `to_json`/`from_json` — `ClaimResult` has no bespoke pair of those,
    it rides `dataclasses.asdict` out and `_claim_from`'s `**d` in, so a field
    with a default needs no code there, only the schema and this proof."""
    import jsonschema

    from papertrace.models import RunResults

    claim = ClaimResult(
        id=1, claim="x", location="Results", refs=["6"],
        verdict="unchecked", withheld_refs=["6"],
    )
    path = tmp_path / "results.json"
    RunResults(manuscript="m.pdf", claims=[claim]).to_json(path)

    back = RunResults.from_json(path).claims[0]
    assert back.withheld_refs == ["6"]

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "results.schema.json").read_text()
    )
    jsonschema.Draft202012Validator(schema).validate(json.loads(path.read_text()))
```

Add to `tests/test_disclosure_parity.py`, immediately after
`test_claim_keys_names_only_keys_a_producer_actually_emits` (`:432-441`):

```python
def test_claim_disclosures_emits_no_key_missing_from_claim_keys():
    """The direction the test above does NOT check: a key `claim_disclosures()`
    emits at runtime but `CLAIM_KEYS` omits would still reach the viewer (its
    catch-all) while silently vanishing from the other three formats, and
    nothing here would go red. Exercises every branch `claim_disclosures()` can
    take today, including `claim_pairing`, so this would have caught it being
    added to the producer without being added to the set."""
    from papertrace.models import SourceJudgement
    from papertrace.refs import RefManifest as _RM  # only for label_is_doubtful below

    manifest = _RM(manuscript="m.pdf", entries=[], unverified_from=1)
    claims = [
        _claim(refs=["7", "9"], verdict="contradicted",
               judgements=[SourceJudgement(source_slug="a", ref="7", verdict="contradicted"),
                           SourceJudgement(source_slug="b", ref="9", verdict="supported")]),
        _claim(unjudged_refs=["11"]),
        _claim(quote="", verdict="supported"),
        _claim(verdict="supported",
               judgements=[SourceJudgement(source_slug="s", ref="7", kind="supplement",
                                            verdict="supported")]),
        _claim(refs=["1"], verdict="supported"),
        _claim(refs=["9"], verdict="unchecked", withheld_refs=["9"]),
        _claim(evidence_image=None, anchor_located=False, source_page=3, verdict="supported"),
    ]
    seen = set()
    for c in claims:
        for d in claim_disclosures(c, manifest=manifest):
            seen.add(d.key)
    from papertrace import disclosures as mod
    assert seen <= mod.CLAIM_KEYS, f"emitted but not declared: {sorted(seen - mod.CLAIM_KEYS)}"
```

**Note for the implementer:** `RefManifest` is imported from `papertrace.refs`
in that snippet by mistake in an earlier draft of this task — the real class
lives in `papertrace.models`. Import it as
`from papertrace.models import RefManifest` alongside the module's existing
`ClaimResult, RunResults` import instead of adding a second import line.
Reported here rather than silently, because a plan that prescribes code
verbatim is responsible for the characters it prescribes.

Also extend `test_every_claim_disclosure_appears_in_every_format` — add one
more parametrised case so the new producer's token is asserted in all four
formats, immediately after the existing `anchor_located` test:

```python
def test_a_withheld_claim_pairing_disclosure_appears_in_every_format(tmp_path):
    claim = _claim(refs=["9"], verdict="unchecked", withheld_refs=["9"])
    rendered = _render(RunResults(manuscript="m.pdf", claims=[claim]), tmp_path)

    pairing = next(d for d in claim_disclosures(claim) if d.key == "claim_pairing")
    for name, body in rendered.items():
        assert pairing.token in body, f"claim_pairing token missing from {name}"
```

Append to `tests/test_pipeline_split.py`. That module already has the
`sys.path.insert` preamble and `from papertrace import cli  # noqa: E402`
(verified at `tests/test_pipeline_split.py:12-19`), so **add only the two
imports it lacks**, merged into the existing block and `I`-sorted:

```python
from papertrace import check as check_mod  # noqa: E402
from papertrace.models import RefEntry, RefManifest  # noqa: E402
```

Then append the test itself at the end of the module:

```python
# --- withholding a disputed label -------------------------------------------
# The wire the `check_claims` tests cannot see for themselves. `_check_pipeline`
# is where `RefManifest.labels_disputed` (Task 3) actually reaches
# `check_claims`'s `disputed` parameter (Task 4); `check_claims` can only prove
# what happens once the argument has arrived.


def test_check_pipeline_passes_the_manifests_disputed_labels_to_check_claims(
    tmp_path, monkeypatch
):
    """If this regresses, a label Task 3 marked disputed reaches judgement
    anyway — the withholding built into `check_claims` never fires, because
    the CLI never told it which labels to withhold."""
    case = tmp_path / "case"
    case.mkdir()
    RefManifest(
        manuscript="m.pdf",
        entries=[RefEntry(num="9", raw="ref", status="retrieved", slug="x-2020",
                          pdf_path="/nonexistent/x-2020.pdf")],
        labels_disputed=["9"],
    ).to_json(case / "refs_manifest.json")

    monkeypatch.setattr(check_mod, "claude_available", lambda: True)
    monkeypatch.setattr(check_mod, "extract_claims", lambda *a, **kw: ([], []))
    monkeypatch.setattr(check_mod, "coverage_audit", lambda *a, **kw: {})
    monkeypatch.setattr(check_mod, "last_model", lambda: None)

    captured = {}

    def fake_check_claims(claims, manifest, case_dir, model, *, progress=None,
                          on_error=None, truncations=None, backend, disputed=None):
        captured["disputed"] = disputed
        return claims

    monkeypatch.setattr(check_mod, "check_claims", fake_check_claims)

    cli._check_pipeline(case=case, backend="pymupdf")

    assert captured["disputed"] == {"9"}


def test_check_pipeline_tolerates_a_manifest_with_nothing_disputed(tmp_path, monkeypatch):
    """`labels_disputed` defaults to `[]` — the ordinary case, every run before
    Task 3 shipped and every run where nothing disagreed. `set([])` must reach
    `check_claims` rather than `None`, `set()` behaves identically either way
    (proved in `test_check.py`), but this is the one place that constructs it."""
    case = tmp_path / "case"
    case.mkdir()
    RefManifest(manuscript="m.pdf", entries=[]).to_json(case / "refs_manifest.json")

    monkeypatch.setattr(check_mod, "claude_available", lambda: True)
    monkeypatch.setattr(check_mod, "extract_claims", lambda *a, **kw: ([], []))
    monkeypatch.setattr(check_mod, "coverage_audit", lambda *a, **kw: {})
    monkeypatch.setattr(check_mod, "last_model", lambda: None)

    captured = {}

    def fake_check_claims(claims, manifest, case_dir, model, *, progress=None,
                          on_error=None, truncations=None, backend, disputed=None):
        captured["disputed"] = disputed
        return claims

    monkeypatch.setattr(check_mod, "check_claims", fake_check_claims)

    cli._check_pipeline(case=case, backend="pymupdf")

    assert captured["disputed"] == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_withholding.py tests/test_pipeline_split.py -q
~/anaconda3/bin/python -m pytest tests/test_disclosure_parity.py -q
```

Expected: `tests/test_withholding.py` fails with `TypeError: check_claims() got an
unexpected keyword argument 'disputed'` and `AttributeError:
'ClaimResult' object has no attribute 'withheld_refs'`. The new test appended to
`tests/test_pipeline_split.py` fails the same way (or `RefManifest(...) got an
unexpected keyword argument 'labels_disputed'` if Task 3's field is not yet on
this branch —
if so, stop and confirm Task 3 landed first; Task 4 cannot proceed without
it). `test_claim_disclosures_emits_no_key_missing_from_claim_keys` and
`test_a_withheld_claim_pairing_disclosure_appears_in_every_format` fail with
`AttributeError: 'ClaimResult' object has no attribute 'withheld_refs'` too,
since the fixture passes it as a kwarg before the field exists.

- [ ] **Step 3: Add `withheld_refs` to `ClaimResult`**

In `src/papertrace/models.py`, immediately after `unjudged_refs` (`:814`) and
before `anchor_located` (`:818`):

```python
    # co-cited refs that could NOT be obtained, so were never opened. They must
    # not be read as having backed the verdict. Sources that WERE available are
    # in `judgements`, not here.
    unjudged_refs: list[str] = field(default_factory=list)
    # co-cited refs whose source WAS obtained but withheld from judgement,
    # because two or more readings of the reference list disagree about which
    # paper this label names (`refs.label_agreement` returned "disputed" for
    # it). NOT `unjudged_refs`: an entry here means the source was fetched and
    # read. `unjudged_refs` means nobody could get to it at all — conflating
    # the two would report a retrieval gap that does not exist and erase the
    # numbering gap that does.
    withheld_refs: list[str] = field(default_factory=list)
```

No change to `_claim_from` or `to_json`/`from_json`: `ClaimResult` has neither
— it round-trips through `dataclasses.asdict(c)` on the way out and
`ClaimResult(**d)` on the way in (`_claim_from`, `models.py:925-933`), so a
new field with `default_factory=list` is symmetric for free. An older
`results.json` with no `withheld_refs` key loads with `[]`, matching every
other list field on this dataclass.

- [ ] **Step 4: Withhold disputed labels in `check_claims`**

In `src/papertrace/check.py`, change the signature (`:862-876`):

```python
def check_claims(
    claims: list[ClaimResult],
    manifest: RefManifest,
    case_dir: Path,
    model: str | None = None,
    progress=None,
    on_error=None,
    *,
    truncations: Truncations | None = None,
    # REQUIRED, like `_clip`'s accumulator above: this decides whether a table
    # in a cited source is readable at all, and neither possible default is
    # honest. "auto" drags docling into an offline test run; "pymupdf" silently
    # downgrades a caller who asked for layout. So there is no default.
    backend: str,
    # labels `refs.label_agreement` (Task 3) returned "disputed" for — two or
    # more readings of the bibliography name different papers under this
    # printed number. None and an empty set behave identically: nothing is
    # withheld, matching every call site written before this parameter existed.
    disputed: set[str] | None = None,
) -> list[ClaimResult]:
```

Then replace the body from `by_slug: dict[...] = {}` through the
`c.unjudged_refs = [...]` line (`:892-953`) with:

```python
    disputed = disputed or set()
    by_slug: dict[str, list[ClaimResult]] = {}
    for c in claims:
        pairs = [(r, _slug_for_ref(manifest, r)) for r in c.refs]
        avail_all = [
            (r, e) for r, e in pairs if e and e.status in ("retrieved", "provided") and e.slug
        ]
        # Partitioned AFTER the retrieval filter above, never before it: a
        # withheld label must describe a source that was actually fetched and
        # read. A disputed label that was never retrieved for the ordinary
        # reasons (paywalled, no DOI, ...) stays exactly that — see the
        # `avail`/`own` branch below, which never sees it as withheld.
        withheld = [(r, e) for r, e in avail_all if r in disputed]
        avail = [(r, e) for r, e in avail_all if r not in disputed]
        c.withheld_refs = sorted({r for r, _ in withheld}, key=lambda r: int(r))
        own = manifest.manuscript_supplements if c.own_supplement else []
        if not avail and not own:
            if withheld:
                # NOT not_retrieved: every one of these sources WAS fetched and
                # read. not_retrieved would falsely claim the source could not
                # be obtained; the true reason is that two readings of the
                # bibliography disagree about which paper this label names, so
                # no verdict can safely name the paper it was fetched for.
                c.verdict = "unchecked"
                labels = f"[{'], ['.join(c.withheld_refs)}]"
                c.note = (
                    f"withheld: the reference list's readings disagree about {labels}, "
                    "so the source retrieved under that label may not be the paper the "
                    "manuscript cites — check the retrieval manifest before relying on "
                    "this claim"
                )
            else:
                c.verdict = "not_retrieved"
                if c.own_supplement:
                    # the paper said exactly where its evidence was and nobody
                    # opened it. That is a retrieval gap, not an uncited assertion.
                    c.note = (
                        "points at this paper's own supplementary material, which was not "
                        "provided — pass it with --supplement"
                    )
                else:
                    reasons = {e.status for _, e in pairs if e}
                    c.note = (
                        f"cited source not available "
                        f"({', '.join(sorted(reasons)) or 'unknown ref'})"
                    )
            continue
        # Co-citation is an offer of support: every source cited for this claim
        # was put forward as backing it, so every one that could be obtained is
        # judged. One call per source, so each verdict rests on that source's
        # text alone and a long source cannot crowd out a short one.
        seen_slugs: set[str] = set()
        for r, e in avail:
            if e.slug in seen_slugs:  # the same paper cited under two labels
                continue
            seen_slugs.add(e.slug)
            c.judgements.append(SourceJudgement(source_slug=e.slug, ref=r, kind="article"))
            by_slug.setdefault(e.slug, []).append(c)
            # A supplement is part of the work that was cited, so it is read for
            # every claim citing that label rather than only when the article
            # turns out to be silent — a supplement contradicting a claim the
            # article supports is exactly the finding that would be missed.
            # Costs one extra call per supplement, not per claim: the loop below
            # groups every claim for a document into a single call.
            for s in e.supplements:
                if s.slug in seen_slugs:
                    continue
                seen_slugs.add(s.slug)
                c.judgements.append(
                    SourceJudgement(source_slug=s.slug, ref=r, kind="supplement",
                                    verified=s.verified)
                )
                by_slug.setdefault(s.slug, []).append(c)
        # the paper's own supplements answer for no citation label, so `ref` is
        # empty: filling in a number would say the claim cited something it did
        # not. Every one provided is read, matching the cited side and sparing
        # the extractor a guess about which file "S3" lives in.
        for s in own:
            if s.slug in seen_slugs:
                continue
            seen_slugs.add(s.slug)
            c.judgements.append(
                SourceJudgement(source_slug=s.slug, ref="", kind="own_supplement")
            )
            by_slug.setdefault(s.slug, []).append(c)
        # what is left here could NOT be obtained — the only remaining reason a
        # cited source goes unopened. Computed from `avail_all`, not `avail`: a
        # withheld label WAS obtained, so it belongs in `withheld_refs` above,
        # never here — this is retrieval failure only.
        avail_all_refs = {r for r, _ in avail_all}
        c.unjudged_refs = [r for r in c.refs if r not in avail_all_refs]
```

Everything from `for slug, group in by_slug.items():` onward (`:955` to the
end of the function) is unchanged — `by_slug` is built from the already-
filtered `avail`, so the per-source model call, `_judgement_from` validation
and `apply_headline()` never see a withheld source at all.

- [ ] **Step 5: Wire `disputed` through the CLI**

In `src/papertrace/cli.py`, `_check_pipeline` (`:868-872`):

```python
    with console.status("reading claims against their cited pages…"):
        check_claims(
            claims, manifest, case, model, progress=tick, on_error=fail,
            truncations=truncations, backend=backend,
            # Task 3 populates this; [] on any manifest it never touched, so an
            # older run or one where nothing disagreed withholds nothing here.
            disputed=set(manifest.labels_disputed),
        )
```

- [ ] **Step 6: The `claim_pairing` disclosure — withheld state only**

In `src/papertrace/disclosures.py`, add two tokens after `NUMBERING_CONTESTED_TOKEN`
(`:50`):

```python
NUMBERING_CONTESTED_TOKEN = "second reading of the reference list disagreed"
LABELS_DISPUTED_TOKEN = "labels the reference list's readings disagree about"
# `claim_pairing` gains three more states in Tasks 6 and 7 (resolved by a
# model call, chosen by the user, corroborated by a second agreeing reading) —
# each needs its own token, the same way ANCHOR_LOCATED_TOKEN /
# ANCHOR_NOT_LOCATED_TOKEN / ANCHOR_UNKNOWN_TOKEN are one Disclosure key with
# one token per state actually reached. Only this state's token exists yet.
CLAIM_PAIRING_WITHHELD_TOKEN = "verdict withheld — the reference list's readings disagree"
```

Add `"claim_pairing"` to `CLAIM_KEYS` (`:55-64`):

```python
CLAIM_KEYS = frozenset(
    {
        "anchor",
        "sources",
        "unjudged_refs",
        "no_quote",
        "supplement_headline",
        "claim_numbering",
        "claim_pairing",
    }
)
```

Add the run-level producer immediately after `_numbering_contested` (its
closing `)` is at `:504`), before `_claim_numbering` (`:507`):

```python
def _labels_disputed(manifest) -> Disclosure | None:
    """Run-level roll-up of every label a claim's verdict was withheld for.

    A reader who stops at the top of the report should see the scope before
    finding it claim by claim: `claim_pairing` says the same thing per claim,
    but only for the claims that actually cite one of these labels.
    """
    labels = sorted(getattr(manifest, "labels_disputed", None) or [], key=lambda r: int(r))
    if not labels:
        return None
    named = f"[{'], ['.join(labels)}]"
    return Disclosure(
        key="labels_disputed",
        level="warn",
        token=LABELS_DISPUTED_TOKEN,
        text=(
            f"{named} are {LABELS_DISPUTED_TOKEN}: two or more readings of the "
            "reference list name different papers for each of these labels, so "
            "every claim citing one had that source withheld from judgement "
            "rather than risk a verdict about the wrong paper. See each claim's "
            "own note for which of its citations this affected."
        ),
        short=f"{named} {LABELS_DISPUTED_TOKEN}",
    )
```

Add the claim-level producer immediately before `claim_disclosures`
(`:849`):

```python
def _claim_pairing(claim) -> Disclosure | None:
    """Which pairing state this claim's cited labels are in, if not a clean one.

    Only `withheld` exists yet — `withheld_refs` is the only field a pairing
    state has written to `ClaimResult` so far. Tasks 6 and 7 add the
    model-resolved, user-chosen and corroborated states as further branches
    reading their own new fields, never a second function — so `CLAIM_KEYS`
    keeps exactly one claim-level entry covering all four.
    """
    if claim.withheld_refs:
        labels = f"[{'], ['.join(claim.withheld_refs)}]"
        one = len(claim.withheld_refs) == 1
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_WITHHELD_TOKEN,
            text=(
                f"This claim cites {labels}, and the {CLAIM_PAIRING_WITHHELD_TOKEN} about "
                f"{'that label' if one else 'those labels'}: two readings of the "
                "bibliography name different papers for it, so the source fetched under "
                f"that label may not be the paper the manuscript actually cites. No "
                f"verdict was reached on {'it' if one else 'them'} — check the retrieval "
                "manifest before treating this claim as checked."
            ),
            short=f"{labels} {CLAIM_PAIRING_WITHHELD_TOKEN}",
        )
    # Task 6: `resolved_by_model` — the resolution call named a reading and the
    # default (non-interactive) run accepted it.
    # Task 7: `chosen_by_user` — an interactive session picked one reading
    # whole; `corroborated`, level="info", fires only when the claim already
    # carries a warn-level disclosure, so a clean claim stays silent.
    return None
```

Wire both into their collectors. `run_disclosures` (`:742-743`, right before
`return out` at `:744`):

```python
        if d := _numbering_contested(manifest):
            out.append(d)
        if d := _labels_disputed(manifest):
            out.append(d)
    return out
```

`claim_disclosures` (`:872-875`, between the `claim_numbering` branch and the
`anchor` branch):

```python
    if manifest is not None and any(manifest.label_is_doubtful(r) for r in claim.refs):
        out.append(_claim_numbering(claim, manifest))
    if (d := _claim_pairing(claim)) is not None:
        out.append(d)
    if (d := anchor_disclosure(claim)) is not None:
        out.append(d)
    return out
```

- [ ] **Step 7: Template — `report_terminal.html.j2` (the one format where a
  run-level key is not free)**

Add a run-level block after the `numbering_contested` block (`:103-105`):

```
{% for d in disclosures if d.key == "numbering_contested" %}
    <div class="log"><span class="step">▸ resolve</span><span class="amber">⚠ {{ d.short }}</span></div>
{% endfor %}
{% for d in disclosures if d.key == "labels_disputed" %}
    <div class="log"><span class="step">▸ check</span><span class="amber">⚠ {{ d.short }}</span></div>
{% endfor %}
```

Add a claim-level block after the `claim_numbering` block (`:126-127`):

```
{% for d in claim_disclosures(c) if d.key == "claim_numbering" %}      <div class="cl-meta"><span class="amber">⚠ {{ d.short }}</span></div>
{% endfor %}
{% for d in claim_disclosures(c) if d.key == "claim_pairing" %}      <div class="cl-meta"><span class="{{ 'amber' if d.level == 'warn' else 'dim' }}">{{ '⚠ ' if d.level == 'warn' else '' }}{{ d.short }}</span></div>
{% endfor %}
```

Add `claim_pairing` to the gap-section tuple (`:206`):

```
{% for d in claim_disclosures(c) if d.key in ("sources", "unjudged_refs", "claim_numbering", "claim_pairing") %}
```

- [ ] **Step 8: Template — `report.md.j2` and `report_editor.html.j2`**

Both need nothing for `labels_disputed` — the run-level catch-all at
`report.md.j2:19` and `report_editor.html.j2:122` renders any key not
`"coverage"`/`"coverage_caveat"` generically, and this is a brand-new key so
it falls straight into it.

`report.md.j2` — add a claim-level block after the `claim_numbering` block
(`:58-61`):

```
{% for d in claim_disclosures(c) if d.key == "claim_numbering" %}

> ⚠️ {{ d.text }}
{% endfor %}
{% for d in claim_disclosures(c) if d.key == "claim_pairing" %}

> ⚠️ {{ d.text }}
{% endfor %}
```

And add `claim_pairing` to the gap-section tuple (`:137`):

```
{% for d in claim_disclosures(c) if d.key in ("sources", "unjudged_refs", "claim_numbering", "claim_pairing") %}
```

`report_editor.html.j2` — add a claim-level block after the `claim_numbering`
block (`:144-146`):

```
{% for d in claim_disclosures(c) if d.key == "claim_numbering" %}
    <p class="cap"><span style="color:var(--amber)">⚠ {{ d.text }}</span></p>
{% endfor %}
{% for d in claim_disclosures(c) if d.key == "claim_pairing" %}
    <p class="cap"><span style="color:var(--{{ 'amber' if d.level == 'warn' else 'fg' }})">{{ '⚠ ' if d.level == 'warn' else '' }}{{ d.text }}</span></p>
{% endfor %}
```

And add `claim_pairing` to the gap-section tuple (`:233`):

```
{% for d in claim_disclosures(c) if d.key in ("sources", "unjudged_refs", "claim_numbering", "claim_pairing") %}
```

`viewer_app.js` needs no change: run-level disclosures render through the
catch-all at `:195` and claim-level through the catch-all at `:285`/`:301`
(both `filter(d => d.key !== 'anchor')`), and `claim_pairing` is neither
`coverage`-shaped nor `anchor`.

- [ ] **Step 9: `schemas/results.schema.json`**

Confirmed by `ls schemas/`: the file is `schemas/results.schema.json` (no
version field, `additionalProperties` never `false` — additive, nothing added
to `required`, per the house convention). Add `withheld_refs` immediately
after `unjudged_refs` (`:194-200`):

```json
          "unjudged_refs": {
            "type": "array",
            "items": {
              "type": "string"
            },
            "description": "Co-cited labels that could NOT be obtained, so were never opened. The verdict says nothing about these. Sources that WERE available appear in `judgements` instead."
          },
          "withheld_refs": {
            "type": "array",
            "items": {
              "type": "string"
            },
            "description": "Cited labels whose source WAS retrieved but withheld from judgement because two or more readings of the reference list disagree about which paper that label names. Distinct from `unjudged_refs`: an entry here means the source was fetched and read, not that it could not be reached. Absent, or an empty array, means nothing was withheld for this claim."
          },
```

- [ ] **Step 10: Run everything**

```bash
~/anaconda3/bin/python -m pytest tests/test_withholding.py tests/test_pipeline_split.py tests/test_disclosure_parity.py -q
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

Expected: all green. **Paste the real output into the report.** If any test
outside the modules this task touches changed behaviour, stop — `disputed`
defaults to `None` and `None`/`set()` are proved identical to today by
`test_disputed_none_and_empty_set_behave_identically`, so nothing else in the
suite should move.

- [ ] **Step 11: Gate 4 — prove the failure path still degrades honestly**

```bash
~/anaconda3/bin/python -m pytest tests/test_withholding.py -k "not_retrieved or unjudged" -q
~/anaconda3/bin/python -m pytest tests/test_check_provenance.py tests/test_supplements.py tests/test_multisource.py -q
```

Paste the output. `test_a_disputed_label_never_retrieved_stays_not_retrieved`
is the one this gate is specifically about: withholding must never relabel a
source that was genuinely never fetched.

- [ ] **Step 12: Gate 5 — does not apply to code, states so**

No `ingest`, `highlight` or `report.py` *code* changed. `report_terminal.html.j2`,
`report.md.j2` and `report_editor.html.j2` gained template branches only, and
`test_every_claim_disclosure_key_is_rendered_by_every_jinja_format` plus the
two parity tests added in Step 1 cover them. State this explicitly in the
report rather than re-running the demo.

- [ ] **Step 13: Commit**

```bash
git add src/papertrace/check.py src/papertrace/models.py src/papertrace/disclosures.py \
        src/papertrace/cli.py src/papertrace/templates/report_terminal.html.j2 \
        src/papertrace/templates/report.md.j2 src/papertrace/templates/report_editor.html.j2 \
        schemas/results.schema.json tests/test_withholding.py tests/test_pipeline_split.py \
        tests/test_disclosure_parity.py
git commit -m "check: withhold the verdict on a disputed citation label

$(cat <<'BODY'
A label two or more bibliography readings disagree about is dropped from the
set check_claims will judge, after the retrieval filter so withholding only
ever describes a source that really was fetched. The claim lands unchecked,
never not_retrieved — that would falsely say the source could not be obtained
when it was — and the withheld labels are named in the note.

withheld_refs is a new field, not a reuse of unjudged_refs: CLAUDE.md is
explicit that an entry in unjudged_refs means nobody read the source, and a
withheld source was read. Landing it there would erase the retrieval gap that
does exist and invent one that does not.

claim_pairing is a new claim-level disclosure with one state implemented
(withheld) and three more Tasks 6 and 7 will add as further branches in the
same producer. The reverse-direction disclosure parity test — every key a
producer emits must be declared in CLAIM_KEYS, not just the other way round —
is new in this task and would have caught claim_pairing being added without
it.
BODY
)"
```

---

<!--
Tasks 5 and 6 of docs/superpowers/plans/2026-09-17-llm-reference-list.md.
Append verbatim below the `<!-- TASKS APPENDED BELOW -->` marker, in task order.
-->

### Task 5: `reflist.py` — the model's reading, verified field by field

**Why:** the two deterministic readings of a bibliography can both be wrong in
the same place and can disagree in a place neither is wrong about. A third
reading is worth having — but a model's reading of a reference list is exactly
the kind of output that looks right and names a different paper. So this module
is built the other way round from a normal model integration: **nothing the
model replies is believed on its own word.** Every value it proposes must be
found verbatim in one of the two extractions it was shown, or that value is
discarded. What survives is not the model's assertion, it is the page's own ink,
re-assembled with the model's help.

The ceiling this buys, and the ceiling it cannot exceed: a model cannot
introduce a paper here, only agree with a converter about one. It read the same
document both converters read, so a reference the layout destroyed is one it may
also have missed. That sentence is in the module docstring, in the run-level
disclosure, and in this task's commit message, because it is the only honest
claim available about what this reading is worth.

**Files:**
- Create: `src/papertrace/reflist.py`
- Modify: `src/papertrace/ask.py` — move `_parse_json_array` / `_parse_json_object` in from `check.py` (see Step 3; Task 1 created this file)
- Modify: `src/papertrace/check.py` — import the two parsers instead of defining them (deletes `check.py:241-254` as it stands at `2e8a803`)
- Modify: `src/papertrace/models.py` — `RefEntry` gains `seen_in: list[str]`
- Modify: `schemas/refs_manifest.schema.json` — the one new entry-level property
- Modify: `tests/test_coverage.py:12` — see the ruling in Step 3 (the import keeps working; retarget it anyway)
- Test: `tests/test_reflist.py` *(new)*

**Interfaces:**
- Consumes: `ask._ask`, `ask.for_site`, `ask.SITE_REFS`, `ask.model_for`,
  `ask._parse_json_array` (all Task 1 + Step 3); `models.RefEntry`,
  `models._fold`; `refs.DOI_RE`.
- Produces, all in `src/papertrace/reflist.py`:
  - `REFLIST_PROMPT: str` — `<<A>>` / `<<B>>` / `<<A_LABEL>>` / `<<B_LABEL>>`, filled with `.replace()`.
  - `ReflistProvenance` — `model: str = ""`, `entries_proposed: int = 0`, `fields_discarded: list[str]`, `discarded_whole: str = ""`, `readings: list[str]`.
  - `propose(reading_a, reading_b, *, label_a, label_b, model=None) -> tuple[list[RefEntry], ReflistProvenance]`.
  - `_usable_doi`, `_layer_e`, `_found`, `_RULES` — the verification layers.
- Produces in `models.py`: `RefEntry.seen_in: list[str]`.

**Two constraints that shape this module, both stated because their opposites look reasonable.**

1. **`reflist.py` MAY call the seam by qualified name: `ask._ask(...)`.** Task 1's
   brief forbids exactly this in `check.py`, and the reason does not transfer.
   `check.py`'s 62 monkeypatch sites do `monkeypatch.setattr(check_mod, "_ask", …)`,
   which only intercepts a *bare* global lookup in `check`'s own namespace; a
   dotted call there would bypass every patch and turn 62 offline tests into
   live, paid calls. `reflist.py` is a new module with **no existing patch
   sites**, so its tests are written against the seam's own module —
   `monkeypatch.setattr(ask_mod, "_ask", …)` — which a qualified call *does*
   see. The qualified form is therefore the correct one here: it names the
   module that owns the subprocess at the point of use.

2. **`refs` must never import `reflist`.** `reflist` imports `DOI_RE` from
   `refs` and `RefEntry`/`_fold` from `models`. The dependency runs one way and
   has to: `cli` imports `reflist` lazily inside `_refs_pipeline` (Task 6), so
   `refs` importing back would be an import cycle behind a lazy import — the
   kind that fails only on the one entry point nobody tests. `label_agreement`
   (Task 3) lives in `refs.py` and takes `list[RefEntry]`, so it needs nothing
   from `reflist`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reflist.py`:

```python
"""The model's reading of a bibliography, and every rule that disbelieves it.

One `claude -p` call is shown two extractions of the same printed reference list
and asked what numbered list the page carries. Nothing it replies is accepted on
its own word: each field is searched for, verbatim, in one of the two texts it
was shown. These tests are the rules, one per test, because each of them is a
way this module could otherwise name a paper the page never printed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import ask as ask_mod  # noqa: E402
from papertrace import reflist  # noqa: E402
from papertrace.refs import DOI_RE  # noqa: E402

# Reading A: the run backend's extraction. Reading B: the flat-text one. The
# same three references, printed the same way, with the damage each converter
# leaves behind.
READING_A = """References
1. Fujita S, Mori S, Onda K, et al. Characterization of brain volume changes in
aging individuals. JAMA Netw Open. 2023;6(6):e2318153. doi:10.1001/jamanetworkopen.2023.18153
2. Küstner T, Müller M. Longitudinal segmentation of the ageing cortex.
Nat Aging. 2020;4(11):1619-1634. doi:10.1038/s41591-019-0673-2
3. Wachinger C. Atlas-based methods. Med Image Anal. 2019;51:1-10.
"""

READING_B = """References
1 . Fujita S, Mori S, Onda K, et al. Characterization of brain volume changes in
aging individuals. JAMA Netw Open. 2023;6(6):e2318153.
2 . Kustner T, Muller M. Longitu-
dinal segmentation of the ageing cortex. Nat Aging. 2020;4(11):1619-1634.
3 . Wachinger C. Atlas-based methods. Med Image Anal. 2019;51:1-10.
"""


def _reply(monkeypatch, payload: str, *, model: str = "claude-opus-5"):
    """Patch the seam itself — `reflist` calls it by qualified name on purpose."""
    calls = []

    def fake(prompt, model_arg=None):
        calls.append({"prompt": prompt, "site": ask_mod._SITE})
        return payload

    monkeypatch.setattr(ask_mod, "_ask", fake)
    monkeypatch.setattr(ask_mod, "_MODELS", {ask_mod.SITE_REFS: model})
    return calls


def _propose(monkeypatch, payload: str):
    _reply(monkeypatch, payload)
    return reflist.propose(READING_A, READING_B, label_a="docling 2.8.0", label_b="pymupdf")


def test_a_field_not_found_verbatim_in_either_reading_is_discarded(monkeypatch):
    """The field goes, the entry stays.

    An invented journal name is a wrong citation string, not a wrong paper, and
    dropping the whole entry for it would throw away a title and a DOI that
    *were* printed. The gap is recorded so the report can name it.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"3","authors":"Wachinger C","year":"2019",
       "title":"Atlas-based methods","journal":"Nature Reviews Neurology","reading":"AB"}
    ]""")

    assert [e.num for e in entries] == ["3"]
    assert "Nature Reviews Neurology" not in entries[0].raw
    assert "Atlas-based methods" in entries[0].raw
    assert any("journal" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_title_not_found_verbatim_discards_the_whole_candidate(monkeypatch):
    """A title nobody printed is the one failure that cannot be localised.

    Every other field describes a paper; the title *is* the paper. If the model
    named a work the page does not carry, nothing else it said about that entry
    is worth keeping either — and a list one of whose entries names the wrong
    paper cannot be used as a reading of the list at all.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"3","authors":"Wachinger C","year":"2019",
       "title":"Deep learning for cortical parcellation","reading":"A"}
    ]""")

    assert entries == []
    assert "[3]" in prov.discarded_whole and "title" in prov.discarded_whole, prov.discarded_whole


def test_a_line_broken_doi_yields_no_doi_rather_than_a_repair(monkeypatch):
    """Ruling 3. The truncated prefix passes both gates it would have had.

    `DOI_RE` is `10\\.\\d{4,9}/[^\\s"'<>]+` and a trailing hyphen is a fine
    `[^\\s"'<>]`, so `10.1038/s41591-` matches it — and the verbatim check passes
    too, because that prefix really is printed on the page. A DOI is an identity:
    half of one resolves to nothing or to something else, and a repaired one
    would name whichever paper the guess landed on.
    """
    truncated = "10.1038/s41591-"
    # both gates the spec assumed would stop this, measured, on this fixture
    assert DOI_RE.fullmatch(truncated), "premise changed: DOI_RE now rejects the prefix"
    assert reflist._found("doi", truncated, (READING_A, READING_B)), (
        "premise changed: the prefix is no longer printed in the readings"
    )
    assert reflist._usable_doi(truncated) is None, "a half DOI was accepted"

    entries, prov = _propose(monkeypatch, """[
      {"num":"2","authors":"Kustner T, Muller M",
       "title":"Longitudinal segmentation of the ageing cortex",
       "doi":"10.1038/s41591-","reading":"A"}
    ]""")

    assert [e.num for e in entries] == ["2"]
    assert entries[0].doi is None
    assert "10.1038/s41591-" not in (entries[0].raw or "")
    assert any("doi" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_whole_doi_still_verifies_case_insensitively(monkeypatch):
    """The negative above must not be bought by rejecting real DOIs.

    Publishers print `doi:10.1001/...` in mixed case and the two extractions
    disagree about that case, so the DOI comparison folds case and nothing else
    — de-hyphenating a DOI would join `s41591-019` into a different registrant.
    """
    entries, _ = _propose(monkeypatch, """[
      {"num":"1","title":"Characterization of brain volume changes in\\naging individuals",
       "doi":"10.1001/JAMANETWORKOPEN.2023.18153","reading":"AB"}
    ]""")

    assert entries[0].doi == "10.1001/JAMANETWORKOPEN.2023.18153"


def test_an_umlaut_in_an_author_name_still_verifies_both_ways(monkeypatch):
    """`Küstner` printed, `Kustner` proposed — and the reverse.

    The two extractions of one page disagree about diacritics, so a comparison
    that did not fold them would discard a correctly copied author on half the
    non-English bibliography in existence. Layer O is applied to BOTH sides;
    folding one side of a substring test is the bug Plan A had to fix in
    `_title_check_text`.
    """
    readings = (READING_A, READING_B)
    assert reflist._found("authors", "Kustner T, Muller M", readings)
    assert reflist._found("authors", "Küstner T, Müller M", readings)

    entries, prov = _propose(monkeypatch, """[
      {"num":"2","authors":"Kustner T, Muller M",
       "title":"Longitudinal segmentation of the ageing cortex","reading":"AB"}
    ]""")

    assert "Kustner" in entries[0].raw
    assert not any("authors" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_ligature_and_a_soft_hyphenated_title_still_verify(monkeypatch):
    """Extraction damage is not disagreement.

    pymupdf prints `Longitu-\\ndinal` where docling prints `Longitudinal`, and a
    PDF font's `ﬁ` reaches one converter as one codepoint. Layer E undoes what
    the extractor did to the ink and nothing else, on both sides.
    """
    assert reflist._found("title", "Longitudinal segmentation", (READING_B,))
    assert reflist._found("title", "Characterization of fine detail",
                          ("Characterization of ﬁne detail",))
    # and it must still refuse the same string with the ligature's letters gone
    assert not reflist._found("title", "Characterization of ne detail",
                              ("Characterization of ﬁne detail",))


def test_a_duplicated_numeral_is_recorded_and_never_repaired(monkeypatch):
    """A numeral is the join key; renumbering it is how a verdict changes paper.

    Two entries proposed as `[2]` means the reading cannot say which paper `[2]`
    is — which is precisely the question. Both entries are kept exactly as
    proposed, the duplication is recorded, and `label_agreement` refuses to let
    this reading speak for `2` at all. Nothing is renumbered to `3`.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"2","title":"Longitudinal segmentation of the ageing cortex","reading":"A"},
      {"num":"2","title":"Atlas-based methods","reading":"B"}
    ]""")

    assert [e.num for e in entries] == ["2", "2"], "an entry was dropped or renumbered"
    assert any("twice" in f for f in prov.fields_discarded), prov.fields_discarded


def test_a_reply_that_is_not_json_yields_an_empty_candidate_not_an_exception(monkeypatch):
    """`propose` reports its own failure; it does not raise it at the refs stage.

    The refs stage has three other readings to proceed on. A model that
    answered with prose has told us nothing, and "nothing" is a representable
    result — an exception here would take down a stage that was doing fine.
    """
    entries, prov = _propose(monkeypatch, "I could not read that bibliography, sorry.")

    assert entries == []
    assert prov.discarded_whole, "the failure was silent"
    assert prov.entries_proposed == 0


def test_a_paper_printed_in_neither_reading_contributes_nothing(monkeypatch):
    """The reply mixes one real entry with one fabricated paper.

    This is the failure mode the whole module exists for, and the real entry
    beside it is the temptation: keeping it would mean proceeding on a reading
    that demonstrably invents. Neither entry survives, and the reason names the
    fabricated one.
    """
    entries, prov = _propose(monkeypatch, """[
      {"num":"1","title":"Characterization of brain volume changes in\\naging individuals","reading":"AB"},
      {"num":"4","authors":"Nobody N","year":"2024",
       "title":"A paper that was never printed","journal":"J Fabrication","reading":"A"}
    ]""")

    assert entries == []
    assert "[4]" in prov.discarded_whole, prov.discarded_whole
    assert prov.entries_proposed == 2, "the count of what was proposed is still recorded"


def test_propose_makes_exactly_one_model_call_at_the_refs_site(monkeypatch):
    """One call per bibliography, attributed to `refs`, not to `check`.

    Two things break if this drifts. A per-entry call would read one reference
    list at N times the cost for no extra evidence; and a call attributed to the
    wrong site makes the report's `Checker:` line name the reference-list model
    as the judge of verdicts it never saw — the bug Task 1 exists to prevent.
    """
    calls = _reply(monkeypatch, "[]")
    entries, prov = reflist.propose(READING_A, READING_B,
                                    label_a="docling 2.8.0", label_b="pymupdf")

    assert len(calls) == 1, calls
    assert calls[0]["site"] == ask_mod.SITE_REFS
    assert entries == []
    assert prov.model == "claude-opus-5"
    assert prov.readings == ["docling 2.8.0", "pymupdf"]
    # both texts reached the prompt, each in its own slot
    assert "Kustner T, Muller M" in calls[0]["prompt"]
    assert "Küstner T, Müller M" in calls[0]["prompt"]
    assert "<<A>>" not in calls[0]["prompt"] and "<<B_LABEL>>" not in calls[0]["prompt"]


def test_the_models_reading_letter_is_translated_into_a_reading_name(monkeypatch):
    """One published field, one vocabulary — the translation happens here.

    The model answers in the terms the prompt gave it (`A`, `B`, `AB`), because
    that is what it was shown. `RefEntry.seen_in` is a *published* field whose
    vocabulary is reading NAMES, so that a consumer can ask `"llm" in seen_in`
    and get a true answer on every manifest. Letting `A` reach the field would
    put two vocabularies in one wire-format key, and a consumer checking either
    one would silently never match manifests written by the other path.
    """
    entries, _ = _propose(
        monkeypatch,
        """[
      {"num":"3","title":"Atlas-based methods","reading":"AB"}
    ]""",
        label_a="docling",
        label_b="pymupdf",
    )

    assert entries[0].seen_in == ["docling", "pymupdf"]


def test_an_unrecognised_reading_letter_is_dropped_rather_than_guessed(monkeypatch):
    """A model answering "the docling one" must not land a sentence in a
    published field, and must not be *assumed* to have meant reading A either.
    Unknown provenance is recorded as unknown."""
    entries, _ = _propose(monkeypatch, """[
      {"num":"3","title":"Atlas-based methods","reading":"whichever one was clearer"}
    ]""")

    assert entries[0].seen_in == [], entries[0].seen_in


def test_an_empty_reading_makes_no_model_call_at_all(monkeypatch):
    """Nothing to check against means nothing can be believed.

    A run whose second extraction came back empty would otherwise pay for a call
    whose every field could only be verified against one text — half the
    verification, at full price, with no way for the report to say so.
    """
    calls = _reply(monkeypatch, "[]")
    entries, prov = reflist.propose(READING_A, "   ", label_a="docling 2.8.0", label_b="pymupdf")

    assert calls == []
    assert entries == []
    assert prov.discarded_whole


def test_the_entry_level_seen_in_round_trips_through_the_manifest(tmp_path):
    """Gate 2. A new persisted field that `from_json` drops is a silent loss.

    `_ref_entry_from` filters on `fields(RefEntry)`, so declaring the field is
    enough — this test is what says so rather than assuming it.
    """
    import json

    import jsonschema

    from papertrace.models import RefEntry, RefManifest

    m = RefManifest(manuscript="p.pdf",
                    entries=[RefEntry(num="1", raw="A. A paper. 2020.", seen_in=["docling", "pymupdf"])])
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)

    payload = json.loads(path.read_text())
    assert payload["entries"][0]["seen_in"] == ["docling", "pymupdf"]
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json").read_text()
    )
    jsonschema.validate(payload, schema)
    assert "seen_in" not in schema["properties"]["entries"]["items"].get("required", [])
    assert RefManifest.from_json(path).entries[0].seen_in == ["docling", "pymupdf"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_reflist.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'papertrace.reflist'`.
Paste it. That is the legitimate first failure for a task whose first deliverable
is the module.

- [ ] **Step 3: Confirm the two JSON-reply parsers are already at the seam**

`reflist` needs `_parse_json_array`. **Do not import it from `check`, and do not
duplicate it.** The whole point of Task 1 is that `check` and `reflist` are
siblings over one seam; a `from .check import _parse_json_array` in `reflist.py`
would make the reference-list stage import the judging module — pulling in
`EXTRACT_PROMPT`, `coverage_audit` and `SourceProvenance` to strip a code fence
— and would re-establish `check` as the module every model-using stage depends
on, which is the coupling this plan removes. Duplicating the six lines is no
better: two copies of a fence-stripping regex means the next person to meet a
model that wraps its reply in `~~~json` fixes one of them.

**Task 1 already moved both parsers into `ask.py`** for exactly this reason —
one task owns `ask.py`'s shape. So this step is a check, not an edit:

```bash
grep -n "_parse_json_array\|_parse_json_object" src/papertrace/ask.py src/papertrace/check.py
```

Expected: both defined in `ask.py`, and `check.py` importing them in its
`from .ask import (...)` block. If they are still defined in `check.py`, Task 1
did not land as written — stop and report that rather than moving them here,
because two tasks editing `ask.py`'s definitions is how a merge conflict
becomes a silent duplicate.

For reference, this is what Task 1 should have placed in `ask.py` (verbatim from
the old `check.py:241-254`), alongside `import re` in its import block:

```python
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
```

In `src/papertrace/check.py`, delete both definitions (`:241-254`) and add the
two names to the existing `from .ask import (...)` block Task 1 created,
`I`-sorted within it:

```python
from .ask import (
    ASK_ATTEMPTS,
    CLAUDE_TIMEOUT,
    SITE_CHECK,
    _ask,
    _parse_json_array,
    _parse_json_object,
    claude_available,
    for_site,
    model_for,
)
```

**Both names stay used inside `check.py`** — `_parse_json_object` at
`check.py:315` (`extract_claims`) and `_parse_json_array` at `check.py:995`
(`check_claims`) — so ruff's `F401` does not fire and no `# noqa` is needed.

**The `tests/test_coverage.py:12` ruling.** That module does
`from papertrace.check import _parse_json_object, …`, and **it keeps working
unchanged**: an imported name is still an attribute of the importing module, so
`papertrace.check._parse_json_object` resolves after the move. Retarget it
anyway, in this task, to `from papertrace.ask import _parse_json_object` with
the other imports left on `.check` — a test that reaches through a re-export
pins the re-export rather than the function, and the next person to tidy
`check.py`'s imports would break it for a reason they could not see. This is a
tidy-up, not a fix; say so in the report rather than claiming the move would
otherwise have failed.

- [ ] **Step 4: `RefEntry` gains `seen_in`, and the schema says what absent means**

In `src/papertrace/models.py`, in the `RefEntry` dataclass (currently
`:491-517`), after `title_check`:

```python
    # Which readings of the bibliography carried this entry, by NAME —
    # "crossref", "parsed", "pymupdf", "llm". One vocabulary, so a consumer can
    # ask `"llm" in e.seen_in` and get a true answer on any manifest.
    #
    # The model answers in the letters its prompt gave it ("A", "B", "AB"),
    # because that is what it was shown; `reflist.propose` translates those into
    # the names it was handed, and an unrecognised letter yields nothing rather
    # than a guess. Empty means nobody recorded a provenance for this entry —
    # never that it was seen in no reading.
    seen_in: list[str] = field(default_factory=list)
```

`to_json` uses `asdict(e)` and `_ref_entry_from` filters on `fields(RefEntry)`,
so **neither needs editing** — declaring the field is the whole change. The
round-trip test in Step 1 is what establishes that rather than assuming it.

**Controller's ruling on `seen_in`'s vocabulary.** Two task authors reached
incompatible meanings for this one published field: the spec's purpose for it is
*"an entry seen only in the model's reading must be spottable"*, which needs
reading **names**, while `reflist`'s prompt naturally returns `A`/`B`/`AB`,
which are text **letters**. One key with two vocabularies is a wire-format bug:
a consumer checking `"llm" in seen_in` would silently never match a manifest
written by the other path, and a consumer checking `"A" in seen_in` would never
match one written by Task 6. So the letters are translated at the boundary, in
`propose`, and the model's reply key is named `reading` rather than `seen_in` so
the two cannot be confused in a JSON example. Task 6 additionally stamps `"llm"`
onto chosen entries the model agreed with.

In `schemas/refs_manifest.schema.json`, inside
`properties.entries.items.properties`, additive, and **not** added to that
object's `required` (`["num", "raw", "status"]`). Deliberately **no `enum`** —
the reading names are assigned by the CLI and a future fifth reading must not
require a schema bump to be recordable:

```json
    "seen_in": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Which readings of the printed bibliography carried this entry, by name — \"crossref\", \"parsed\", \"pymupdf\", \"llm\". An entry carried only by \"llm\" is one no deterministic reading found, which is worth a reader's eye. Absent or empty means no provenance was recorded for this entry, which is the state of every manifest written before 0.7 and of every run that took only one reading. It is never a claim that the entry was seen in no reading."
    }
```

- [ ] **Step 5: Create `src/papertrace/reflist.py`**

```python
"""One model's reading of a printed bibliography, verified field by field.

A model cannot introduce a paper here, only agree with a converter about one:
every value it proposes is searched for, verbatim, in one of the two extractions
it was shown, and a value that is not found is discarded. What survives is the
page's own ink re-assembled, not the model's assertion. The ceiling is the same
one either converter has — it read the same document, so a reference the layout
destroyed is one it may also have missed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import ask
from .models import RefEntry, _fold
from .refs import DOI_RE

REFLIST_PROMPT = """You are the reference-list reading step of a peer-review fact-checker.

Below are TWO extractions of the SAME printed bibliography, out of the same PDF,
made by two different converters (<<A_LABEL>> and <<B_LABEL>>). Either one may
have merged two references into one, split one across two, lost a numeral, or
broken a DOI across a line. Your job is to say what numbered list the page
actually carries.

Rules:
1. COPY, do not compose. Every value you return must appear as a substring of
   TEXT A or TEXT B. Do not translate, do not expand an abbreviation, do not
   fill in a journal name you recognise, do not correct a spelling, and do not
   add an author the text does not print.
2. `num` is the numeral PRINTED for that entry, copied as a string — NOT its
   position in your list. If the page prints 1, 2, 3, 5 then the fourth entry's
   `num` is "5". Never renumber it to "4".
3. Never repair a DOI. If a DOI is broken across a line, truncated, or you
   cannot see all of it, return null for it. Half a DOI names a different paper.
4. `reading` is "A" when the entry is legible only in TEXT A, "B" when only in
   TEXT B, "AB" when both carry it. Those three are the only answers; anything
   else is dropped.
5. Do not merge two references into one entry and do not split one across two.
   Where the two texts disagree about where an entry begins, follow the printed
   NUMERALS; if you still cannot tell, OMIT the entry rather than guess at it.
6. Omit any field you cannot copy. An omitted field is a recorded gap; an
   invented one is a wrong paper.
7. If the text below is not a reference list at all, answer with [].

Your reply is checked field by field against TEXT A and TEXT B before any of it
is used. A value not found verbatim in one of them is discarded, and an entry
whose title is not found discards the entire reply. Copying is the only way to
be believed, and guessing costs you every entry, not just the one.

Answer with ONLY a JSON array, no prose, no code fences:
[{"num":"6","authors":"Fujita S, Mori S, Onda K, et al","year":"2023",
  "title":"Characterization of brain volume changes in aging individuals",
  "journal":"JAMA Netw Open. 6(6):e2318153",
  "doi":"10.1001/jamanetworkopen.2023.18153","reading":"A"}]

TEXT A (<<A_LABEL>>):
<<A>>

TEXT B (<<B_LABEL>>):
<<B>>
"""

# ---------------------------------------------------------------------------
# the two normalisation layers, and what each field is allowed to be folded by
# ---------------------------------------------------------------------------

_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
_PUNCT = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}
_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")


def _layer_e(s: str, *, dehyphenate: bool = True) -> str:
    """Undo what the extractor did to the ink, and nothing else.

    Applied to BOTH sides of every comparison, which is what makes it safe: a
    normalisation applied to one side only invents matches, and folding one side
    of a substring test is exactly the bug Plan A had to fix in
    `_title_check_text`.
    """
    for src, dst in {**_LIGATURES, **_PUNCT}.items():
        s = s.replace(src, dst)
    if dehyphenate:
        s = _HYPHEN_BREAK.sub(r"\1\2", s)
    return re.sub(r"\s+", " ", s).strip()


# Layer O is `models._fold`, which already lowercases, transliterates
# `ß ø æ œ đ ð þ ł ı ħ ŧ` and decomposes combining marks. Authors and titles get
# it; a DOI, year or journal string does not — folding a DOI would make two
# different registrants compare equal.
_RULES = {
    "authors": lambda s: _fold(_layer_e(s)),
    "title": lambda s: _fold(_layer_e(s)),
    "year": lambda s: _layer_e(s),
    "journal": lambda s: _layer_e(s),
    "doi": lambda s: _layer_e(s, dehyphenate=False).lower(),
}


def _found(field: str, value: str, readings: tuple[str, ...]) -> bool:
    """Is this value printed in one of the texts the model was shown?"""
    norm = _RULES[field]
    needle = norm(value)
    return bool(needle) and any(needle in norm(r) for r in readings)


def _usable_doi(raw: str) -> str | None:
    """A DOI shaped like a whole one, or None — never a repair.

    `DOI_RE` accepts a trailing hyphen, so `10.1038/s41591-` (a DOI the
    extractor broke across a line) matches it, and the verbatim check passes
    too because that prefix really is printed on the page. Requiring the last
    character to be alphanumeric is what tells a whole DOI from half of one.
    """
    s = _layer_e(raw, dehyphenate=False).strip().rstrip(".,;)]")
    if not DOI_RE.fullmatch(s) or not s[-1:].isalnum():
        return None
    return s


@dataclass
class ReflistProvenance:
    """What the model reading cost, kept, and threw away — the report's record."""

    model: str = ""
    entries_proposed: int = 0
    fields_discarded: list[str] = field(default_factory=list)
    # non-empty means the whole reading was refused, and says why. It is never
    # "the model agreed": a reading that could not be checked is not a reading.
    discarded_whole: str = ""
    readings: list[str] = field(default_factory=list)


# order matters only for `raw` below, which is re-assembled in printed order
_VERIFIED_FIELDS = ("authors", "year", "title", "journal", "doi")


def propose(
    reading_a: str,
    reading_b: str,
    *,
    label_a: str,
    label_b: str,
    model: str | None = None,
) -> tuple[list[RefEntry], ReflistProvenance]:
    """The numbered list a model says these two extractions carry, verified.

    Returns an empty candidate and a stated reason for every failure it can
    meet — an unusable reply, an unverifiable title, no text to check against.
    The refs stage has three other readings to proceed on, so nothing here
    raises on the model's behalf. A failure of the *call itself* does propagate:
    `cli` must be able to tell "the model was asked and answered nonsense" from
    "the model could not be asked", and two different reasons reach the report.
    """
    prov = ReflistProvenance(readings=[label_a, label_b])
    readings = tuple(r for r in (reading_a, reading_b) if r.strip())
    if len(readings) < 2:
        # one text is half the verification at full price, and no way to say so
        # in the report — a value found in the only reading available has been
        # checked against nothing but itself
        prov.discarded_whole = (
            "only one of the two extractions of the bibliography had any text, so "
            "nothing the model proposed could have been checked against a second reading"
        )
        return [], prov

    # labels before texts: a label is a short fixed string ("pymupdf",
    # "docling 2.8.0") and cannot contain a placeholder, while page text
    # conceivably could
    prompt = (
        REFLIST_PROMPT
        .replace("<<A_LABEL>>", label_a)
        .replace("<<B_LABEL>>", label_b)
        .replace("<<A>>", reading_a)
        .replace("<<B>>", reading_b)
    )
    # Qualified on purpose. `check.py` must call `_ask` by bare name because 62
    # tests patch `check._ask`; this module has no such sites and its tests patch
    # `ask._ask`, which only a qualified call sees.
    with ask.for_site(ask.SITE_REFS):
        raw = ask._ask(prompt, model)
    prov.model = ask.model_for(ask.SITE_REFS) or ""

    try:
        proposed = ask._parse_json_array(raw)
    except ValueError as e:
        prov.discarded_whole = f"the model's reply was not a JSON array ({str(e)[:120]})"
        return [], prov
    if not isinstance(proposed, list):
        prov.discarded_whole = "the model's reply was not a JSON array"
        return [], prov
    prov.entries_proposed = len(proposed)

    out: list[RefEntry] = []
    for i, item in enumerate(proposed, start=1):
        if not isinstance(item, dict):
            prov.fields_discarded.append(f"entry {i}: not a JSON object")
            continue
        # NOT verbatim-checked, deliberately: "6" is a substring of almost any
        # bibliography, so searching for it is not a check. What constrains a
        # numeral is rule 3 below (uniqueness and coverage) and the fact that the
        # entry dies entirely if its title is not printed.
        num = _layer_e(str(item.get("num") or "")).strip()
        if not num:
            prov.fields_discarded.append(f"entry {i}: no printed numeral")
            continue

        # Rule 4, before anything is kept: a title nobody printed is the one
        # failure that cannot be localised to a field. Every other field
        # describes a paper; the title IS the paper. A reply that named a work
        # the page does not carry has demonstrated invention, and the entries
        # beside it are not worth more for being next to it.
        title = item.get("title")
        if isinstance(title, str) and title.strip() and not _found("title", title, readings):
            prov.fields_discarded.append(f"[{num}] title")
            prov.discarded_whole = (
                f"the title proposed for [{num}] was not found in either reading of the "
                "bibliography, so the model's whole reading was discarded rather than "
                "used — it named a paper the page does not print"
            )
            return [], prov

        kept: dict[str, str] = {}
        for name in _VERIFIED_FIELDS:
            value = item.get(name)
            if not isinstance(value, str) or not value.strip():
                continue  # an omitted field is a gap the model was told to leave
            if name == "doi":
                doi = _usable_doi(value)
                if doi is None or not _found("doi", doi, readings):
                    # a recorded gap: the entry will resolve `no_doi` and say so,
                    # which is what a broken DOI actually leaves behind
                    prov.fields_discarded.append(f"[{num}] doi")
                    continue
                kept["doi"] = doi
                continue
            if not _found(name, value, readings):
                prov.fields_discarded.append(f"[{num}] {name}")
                continue
            kept[name] = _layer_e(value)

        # Translated here, not stored as the letters: `seen_in` is a published
        # field whose vocabulary is reading NAMES, and the letters exist only
        # inside the prompt. An unrecognised answer yields nothing rather than
        # defaulting to A — unknown provenance is recorded as unknown.
        said = item.get("reading")
        letters = [c for c in ("A", "B") if isinstance(said, str) and c in said.upper()]
        seen_in = [{"A": label_a, "B": label_b}[c] for c in letters]
        if isinstance(said, str) and said.strip() and not letters:
            prov.fields_discarded.append(f"[{num}] reading")

        # `raw` is COMPOSED from the verified fields rather than copied from the
        # reply — and that is legitimate, because every piece of it was found
        # verbatim in a text the model was shown. It has to be composed: the
        # reply carries no single printed string, and `_same_work` and
        # `_title_tokens` read `raw` to decide whether two readings name one
        # paper. Composing it from unverified values would be the invention this
        # module refuses; composing it from verified ones is re-assembly.
        parts = [kept[k] for k in ("authors", "year", "title", "journal") if k in kept]
        raw_text = ". ".join(parts)
        if "doi" in kept:
            raw_text = f"{raw_text} doi:{kept['doi']}".strip()
        out.append(
            RefEntry(
                num=num,
                raw=raw_text,
                doi=kept.get("doi"),
                title=kept.get("title"),
                year=kept.get("year"),
                # `status` keeps its default. This entry is a voter and nothing
                # else: it never reaches `reconcile`'s arguments or
                # `resolve_all`, so no status of it is ever acted on, and adding
                # a `pending` to REF_STATUSES would publish a vocabulary entry
                # no reader ever sees.
                reason="read from the printed list by a model; a voter only, never resolved",
                seen_in=seen_in,
            )
        )

    # Rule 3. A numeral proposed twice, or missing from 1..max, means this
    # reading cannot say which paper that label is — which is the question. It
    # is RECORDED, and nothing is renumbered, merged or dropped to tidy it:
    # `label_agreement` refuses to let a reading speak for a label it carries
    # twice, and `_covers` fails on the gap. Repairing it here would hand the
    # arbiter a list that had already guessed.
    nums = [e.num for e in out]
    twice = sorted({n for n in nums if nums.count(n) > 1}, key=_label_key)
    if twice:
        prov.fields_discarded.append("numerals proposed twice: " + ", ".join(twice))
    if gaps := _numeral_gaps(nums):
        prov.fields_discarded.append("numerals absent from 1..max: " + ", ".join(gaps))
    return out, prov


def _label_key(label: str) -> tuple[int, int, str]:
    """Numeric labels in numeric order, anything else after them, by name."""
    return (0, int(label), "") if label.isdigit() else (1, 0, label)


def _numeral_gaps(nums: list[str]) -> list[str]:
    """Numerals `1..max` that this reading carries no entry for.

    Non-numeric numerals are not counted and not repaired: a list printing `1a`
    has an extent this cannot measure, and guessing at one is how a reading
    comes to claim coverage it does not have.
    """
    numeric = sorted({int(n) for n in nums if n.isdigit()})
    if not numeric:
        return []
    present = set(numeric)
    return [str(n) for n in range(1, max(numeric) + 1) if n not in present]
```

- [ ] **Step 6: Run the tests**

```bash
~/anaconda3/bin/python -m pytest tests/test_reflist.py -q
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

Expected: `tests/test_reflist.py` green, whole suite green. Paste both.

Two places the whole suite is the real check, not `test_reflist.py`:
`RefEntry` gained a field, so anything comparing a serialised entry dict for
equality now sees `seen_in` in it; and `check.py`'s two parsers moved, so every
module that reached them through `check` is exercised. If either shows up, it is
a real find — report it, do not paper over it by making `seen_in` conditional in
`to_json`.

- [ ] **Step 7: Gate 4 — prove the failure paths still degrade honestly**

`check.py` was modified (the parsers moved out), and this task is the one that
introduces a *second* module talking to a model. Run the modules that pin honest
degradation, and paste the output:

```bash
~/anaconda3/bin/python -m pytest tests/test_check_provenance.py tests/test_supplements.py \
    tests/test_coverage.py tests/test_multisource.py tests/test_ask.py -q
```

Then state, in the report, the three degradations this task adds and where each
is proven:
- an unusable reply ⇒ empty candidate + stated reason, no exception
  (`test_a_reply_that_is_not_json_yields_an_empty_candidate_not_an_exception`);
- an unverifiable title ⇒ whole candidate discarded, reason names the label
  (`test_a_title_not_found_verbatim_discards_the_whole_candidate`);
- a broken DOI ⇒ `doi=None`, a recorded gap, never a repair
  (`test_a_line_broken_doi_yields_no_doi_rather_than_a_repair`).

**Gate 2: applies** — `RefEntry.seen_in` is a new JSON field. Schema updated
additively in Step 4, nothing added to `required`, the description states what
absent means, and the round-trip test is
`test_the_entry_level_seen_in_round_trips_through_the_manifest`. Paste that
test's line from the run.

**Gate 5: ruled not applicable, explicitly.** This task changes no `ingest.py`,
`highlight.py` or `report.py` code and touches no template — the `reflist`
disclosure and its terminal branch are Task 6's. Say this in the report rather
than skipping the gate silently. Do **not** re-run the demo for this task: it
would make a paid model call for a code path this task does not wire in.

- [ ] **Step 8: Commit**

```bash
git add src/papertrace/reflist.py src/papertrace/ask.py src/papertrace/check.py \
        src/papertrace/models.py schemas/refs_manifest.schema.json \
        tests/test_reflist.py tests/test_coverage.py
git commit -m "reflist: a model's reading of the bibliography, verified field by field

$(cat <<'BODY'
Every value the model proposes is searched for, verbatim, in one of the two
extractions it was shown. A value not found is discarded; a title not found
discards the whole reading. A DOI is never repaired — DOI_RE accepts a trailing
hyphen, so a line-broken 10.1038/s41591- passes both the regex and the verbatim
check, and only the last-character test tells a whole DOI from half of one.

Normalisation is two layers and both sides of every comparison get the same one.
A numeral proposed twice is recorded and never renumbered.

A model agreeing with a parse is a second reading, not confirmation: it read the
same document, so a reference the layout destroyed is one it may also have
missed.

The JSON-reply parsers move from check.py to ask.py, where the call that
produces the text lives — reflist must not import check to strip a code fence.
BODY
)"
```

---

### Task 6: Wire the third and fourth readings into the refs stage

**Why:** Tasks 2, 3 and 5 built a flat-text reading, a per-label arbiter and a
model reading, and none of them is called by anything. This task is where the
refs stage stops having two readings of the bibliography and starts having four
— and where the only thing the extra two are ever allowed to do is *subtract*.

**Ruling 2 is the whole safety property of this plan, and this is the task that
can break it.** `others["llm"]` must never reach `reconcile`'s `crossref` or
`parsed` argument, and never reach `resolve_all`. `reconcile` still chooses
between the Crossref deposit and the run backend's parse; the flat-text and
model readings are voters on a second, orthogonal axis. No model output can
cause a source to be resolved, downloaded or judged — it can only cause a
verdict to be withheld. Step 2's identity test is what says so.

**Files:**
- Modify: `src/papertrace/cli.py` — `_reference_readings` (new), the LLM call, `--llm-refs/--no-llm-refs` on `_refs_pipeline`, `refs` and `run`, and the two console lines. Insertion points at `2e8a803`: `:554` (`entries = parse_references(refs_text)`), `:638` (the one `reconcile` call), `:702-715` (`RefManifest(...)` + `to_json`), `:727` (`refs`), `:1141` (`run`).
- Modify: `src/papertrace/models.py` — five additive `RefManifest` fields
- Modify: `schemas/refs_manifest.schema.json` — the same five, additive
- Modify: `src/papertrace/disclosures.py` — `reflist` and `numbering_corroboration`
- Modify: `src/papertrace/templates/report_terminal.html.j2` — one allow-list branch each, after the `numbering_contested` block at template lines 103-105
- Modify: `src/papertrace/wizard.py:214-226` and `tests/test_wizard.py:670` — the advertised call ceiling
- Test: `tests/test_reference_readings.py` *(new)*

**Test-module ruling: a new `tests/test_reference_readings.py`.** The brief
offered `tests/test_cli.py` — **that file does not exist** (`ls tests/` at
`2e8a803`: 36 modules, no `test_cli.py`; the plan header's "existing modules
gain tests" list names it in error, and Task 9 should correct the header). The
CLI's existing tests are spread by subject, not by module —
`tests/test_case_identity.py`, `tests/test_pipeline_split.py`,
`tests/test_reference_reconciliation.py` — so a new module named for this
subject follows the house pattern rather than inventing a catch-all.

**Interfaces:**
- Consumes: `ingest.references_span_flat` (Task 2), `refs.label_agreement` and
  `refs.LABEL_AGREEMENT` (Task 3), `Reconciliation.corroborated` /
  `.corroborating_readings` / `.labels_disputed` (Task 3),
  `reflist.propose` / `ReflistProvenance` (Task 5), `ask.claude_available` (Task 1).
- Produces in `cli.py`:
  `_reference_readings(*, smap, crossref=None, parsed=None, flat=None, llm=None) -> dict[str, list[RefEntry]]`
  and `_llm_reference_reading(...) -> tuple[list[RefEntry], ReflistProvenance | None]`.
- Produces in `models.RefManifest`: `numbering_corroborated: bool = False`,
  `corroborating_readings: list[str]`, `labels_disputed: list[str]`,
  `reflist_model: str = ""`, `reflist_fields_discarded: list[str]`,
  `reflist_numbering_findings: list[str]`.
- **Left to Task 7, deliberately:** `labels_resolved`, `numbering_choice`,
  `numbering_chosen_by`. Nothing in this task can set them — they record an
  interactive choice — and a field written only as `""` by the task that added
  it is indistinguishable from a field nobody computed.

**Two rulings the brief asked for explicitly.**

**(a) `--parse-only` runs the pymupdf reading and does NOT make the model
call.** `--parse-only`'s documented contract is "List references, no network"
(`cli.py:660`), and the comment at `cli.py:560-563` records that it therefore
"gets no second candidate". A `claude -p` subprocess is both a network call and
paid spend, so it is forbidden — `llm_refs` is forced off under `--parse-only`,
in `_refs_pipeline`, not left to the caller. `references_span_flat` is local
PyMuPDF work on a file already on disk: no network, no spend, no model. It is
therefore **permitted**, and it is the one thing that makes the offline
inspection better than it was — the comment at `:560-563` describes a gap this
closes for free. Update that comment in the same edit so it stops asserting
something that is no longer true.

**(b) The `reflist` disclosure fires on three outcomes, including the two
failures.** `REFLIST_TOKEN` is `"the reference list was also read by a model"`
and a token must stay true wherever it appears (the
`SUPPLEMENT_IDENTITY_TOKEN` lesson, recorded in `disclosures.py`). So the
disclosure's sentence is phrased *around* the token in all three branches —
read-and-used, read-but-discarded, and asked-for-but-not-obtained — and the
not-obtained branch says so in the same breath as the token. It is never
silent about an attempt, and it never says the model agreed.

- [ ] **Step 1: Read the insertion point in full before editing anything**

```bash
sed -n '496,724p' src/papertrace/cli.py
```

Confirm: `entries = parse_references(refs_text)` at `:554`; the comment about
`--parse-only` getting no second candidate at `:560-563`; the single
`reconcile(...)` at `:638`, which **rebinds `entries`** to the reconciled list;
the `--parse-only` early return at `:655-658`, which is **before** the manifest
is written; `RefManifest(...)` at `:702-714` and `to_json` at `:715`. If any
line has moved, use the anchors, not the numbers.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_reference_readings.py`:

```python
"""Four readings of one bibliography, and the one thing the extra two may do.

`reconcile` still chooses between the Crossref deposit and the run backend's
parse. The flat-text reading and the model's reading are voters on a second
axis: they can cause a verdict to be withheld and nothing else. The first test
here is the load-bearing one — it is the whole safety property of the feature,
stated as an equality.
"""

import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli  # noqa: E402
from papertrace import reflist as reflist_mod  # noqa: E402
from papertrace.models import RefEntry, SourceMap  # noqa: E402


def _paper(path: Path) -> Path:
    """A one-page PDF citing [1] and [2], with both references printed."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Fixture Imaging Study", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and also [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. A first paper. J Fixture. 2020;1:1-9.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. A second paper. J Fixture. 2021;2:10-19.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def offline(monkeypatch):
    """No network and no model: retrieval is a no-op, Crossref answers nothing."""
    import papertrace.refs as refs_mod
    from papertrace.refs import CrossrefDeposit

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None,
                        taken=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit",
                        lambda client, doi, email: CrossrefDeposit(absent="no deposit, in a test"))
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    monkeypatch.setenv("PAPERTRACE_EMAIL", "test@example.org")


def _smap(converter: str) -> SourceMap:
    """Only `converter` matters to the voter dict, and only its first token."""
    return SourceMap(doc="paper.pdf", pages=1, blocks=[], converter=converter)


def _entries(case: Path) -> str:
    """The resolved entries, canonically serialised — the bytes Ruling 2 is about."""
    payload = json.loads((case / "refs_manifest.json").read_text())
    return json.dumps(payload["entries"], sort_keys=True, ensure_ascii=False)


def test_a_model_naming_different_papers_changes_no_resolved_entry(tmp_path, offline, monkeypatch):
    """Ruling 2, as an equality. The safety property the whole design rests on.

    The model's reading is a voter. It can withhold a verdict; it cannot put a
    paper into the manifest, cannot change a numeral, and cannot cause anything
    to be resolved or downloaded. So a run whose model reading names two
    completely different works must produce byte-identical entries to a run that
    never asked a model at all. If this test ever fails, `others["llm"]` has
    reached `reconcile`'s arguments or `resolve_all`, and no other test in this
    suite would notice.
    """
    pdf = _paper(tmp_path / "paper.pdf")
    fabricated = (
        [RefEntry(num="1", raw="Zeta Z. An entirely different paper. 1999.",
                  title="An entirely different paper", year="1999", seen_in=["docling"]),
         RefEntry(num="2", raw="Eta E. Another different paper. 1998.",
                  title="Another different paper", year="1998", seen_in=["pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2,
                                      readings=["docling", "pymupdf"]),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: fabricated)

    with_llm, without = tmp_path / "with", tmp_path / "without"
    cli._refs_pipeline(manuscript=pdf, case=with_llm, provided=None,
                       email="test@example.org", parse_only=False, backend="docling",
                       llm_refs=True)
    cli._refs_pipeline(manuscript=pdf, case=without, provided=None,
                       email="test@example.org", parse_only=False, backend="docling",
                       llm_refs=False)

    assert _entries(with_llm) == _entries(without)
    assert "different paper" not in _entries(with_llm)


def test_the_pymupdf_reading_is_skipped_on_a_pymupdf_backend_run():
    """A reading agreeing with itself is not corroboration.

    The flat-text voter exists because docling and pymupdf read a hanging-indent
    bibliography differently. On a run whose backend already IS pymupdf it is
    the identical text, so counting it would turn every `single` label into an
    `agreed` one and report corroboration that nothing corroborated.
    """
    parsed = [RefEntry(num="1", raw="Alpha A. A first paper. 2020.")]
    flat = [RefEntry(num="1", raw="Alpha A. A first paper. 2020.")]

    flat_run = cli._reference_readings(smap=_smap("pymupdf"), parsed=parsed, flat=flat)
    layout_run = cli._reference_readings(smap=_smap("docling 2.8.0"), parsed=parsed, flat=flat)

    assert set(flat_run) == {"parsed"}, flat_run
    assert set(layout_run) == {"parsed", "pymupdf"}, layout_run


def test_a_reading_that_is_absent_is_omitted_rather_than_empty():
    """An empty list is a reading that found nothing; an absent key is no reading.

    `label_agreement` counts readings that have a label. A reading present as
    `[]` votes against every label it does not carry, which is every label —
    turning "we did not ask" into "one reading says no".
    """
    got = cli._reference_readings(smap=_smap("docling 2.8.0"),
                                  parsed=[RefEntry(num="1", raw="Alpha A. 2020.")],
                                  crossref=None, flat=None, llm=None)
    assert set(got) == {"parsed"}, got


def test_no_llm_refs_makes_zero_model_calls(tmp_path, offline, monkeypatch):
    """The flag has to actually gate the spend, not just the disclosure."""
    import papertrace.ask as ask_mod

    calls = []
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: calls.append(1) or "[]")
    pdf = _paper(tmp_path / "paper.pdf")

    cli._refs_pipeline(manuscript=pdf, case=tmp_path / "case", provided=None,
                       email="test@example.org", parse_only=False, backend="pymupdf",
                       llm_refs=False)

    assert calls == []


def test_parse_only_makes_no_model_call_even_with_llm_refs_on(tmp_path, offline, monkeypatch):
    """`--parse-only` is documented as "List references, no network".

    A paid subprocess is exactly what that promise excludes, and the promise is
    the reason someone reaches for the flag. The flat-text reading still runs —
    it is local and needs no network — which is what closes the gap the comment
    at cli.py:560-563 describes.
    """
    import papertrace.ask as ask_mod

    calls = []
    monkeypatch.setattr(ask_mod, "_ask", lambda prompt, model=None: calls.append(1) or "[]")
    pdf = _paper(tmp_path / "paper.pdf")

    cli._refs_pipeline(manuscript=pdf, case=tmp_path / "case", provided=None,
                       email="test@example.org", parse_only=True, backend="pymupdf",
                       llm_refs=True)

    assert calls == []


def test_an_unavailable_claude_degrades_to_a_stated_absence(tmp_path, offline, monkeypatch):
    """The cardinal rule, at this seam. Never "it agreed" — "it was not asked".

    `claude` missing from PATH is the ordinary case for someone who installed
    papertrace and not Claude Code. The run proceeds on the deterministic
    readings, the manifest records the reason, and no format anywhere says a
    model corroborated anything.
    """
    import papertrace.ask as ask_mod

    monkeypatch.setattr(ask_mod, "claude_available", lambda: False)
    monkeypatch.setattr(ask_mod, "_ask",
                        lambda prompt, model=None: pytest.fail("claude was absent and asked anyway"))
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["reflist_model"] == ""
    assert any("not attempted" in f for f in payload["reflist_fields_discarded"]), payload
    assert "llm" not in payload["corroborating_readings"]
    assert payload["numbering_corroborated"] is False


def test_a_failed_model_call_does_not_kill_the_refs_stage(tmp_path, offline, monkeypatch):
    """A timeout at the bibliography must not cost the user the retrieval.

    `refs` has already parsed the list and is about to fetch the sources. A
    RuntimeError out of the seam is a failed corroboration attempt, not a failed
    refs stage — and the reason lands where a reader can see it.
    """
    def boom(*a, **kw):
        raise RuntimeError("claude -p timed out after 600s")

    monkeypatch.setattr(reflist_mod, "propose", boom)
    pdf = _paper(tmp_path / "paper.pdf")
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert len(payload["entries"]) == 2
    assert any("timed out" in f for f in payload["reflist_fields_discarded"]), payload
    assert payload["reflist_model"] == ""


def test_corroboration_requires_every_cited_label_to_agree(tmp_path, offline, monkeypatch):
    """One disputed label is not "mostly corroborated".

    The flag is read as a reassurance, so it must mean what it says: every label
    the body cites was agreed by at least two readings. Any disagreement is
    `disputed` — no majority vote and no ranking, because "a non-unique match is
    refused, never ranked".
    """
    pdf = _paper(tmp_path / "paper.pdf")
    disagrees = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020", seen_in=["docling", "pymupdf"]),
         RefEntry(num="2", raw="Gamma G. A wholly unrelated third paper. 2015.",
                  title="A wholly unrelated third paper", year="2015", seen_in=["A"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: disagrees)
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["numbering_corroborated"] is False
    assert payload["labels_disputed"] == ["2"], payload["labels_disputed"]
    assert payload["corroborating_readings"] == []


def test_no_model_reply_can_set_numbering_verified(tmp_path, offline, monkeypatch):
    """The invariant, asserted here as well as in Task 8.

    Task 8 parametrises this over every reply shape; this is the one shape that
    would be most tempting — a model agreeing with the parse entry for entry.
    Corroboration is a second axis, not evidence of a checked numbering.
    """
    pdf = _paper(tmp_path / "paper.pdf")
    agrees = (
        [RefEntry(num="1", raw="Alpha A. A first paper. J Fixture. 2020;1:1-9.",
                  title="A first paper", year="2020", seen_in=["docling", "pymupdf"]),
         RefEntry(num="2", raw="Beta B. A second paper. J Fixture. 2021;2:10-19.",
                  title="A second paper", year="2021", seen_in=["docling", "pymupdf"])],
        reflist_mod.ReflistProvenance(model="claude-opus-5", entries_proposed=2),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: agrees)
    case = tmp_path / "case"

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None, email="test@example.org",
                       parse_only=False, backend="pymupdf", llm_refs=True)

    payload = json.loads((case / "refs_manifest.json").read_text())
    assert payload["numbering_verified"] is False
    assert payload["numbering_corroborated"] is True
    assert payload["corroborating_readings"] == ["llm", "parsed"]


def test_both_commands_declare_the_flag_and_run_forwards_it(tmp_path, monkeypatch):
    """`run` calls `_refs_pipeline` directly, so a parameter it forgets is dropped.

    That failure is silent: the audit runs without the reading and says nothing
    about it. `refs` and `run` must both declare the flag, and `run` must pass it.
    """
    import inspect

    seen = {}
    monkeypatch.setattr(cli, "_ingest_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "_refs_pipeline", lambda **kw: seen.update(kw))
    monkeypatch.setattr(cli, "scout", lambda **kw: None)
    monkeypatch.setattr(cli, "_check_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "highlight", lambda **kw: None)
    monkeypatch.setattr(cli, "_report_pipeline", lambda **kw: None)
    monkeypatch.setattr(cli, "_detected_doi", lambda m: None)
    pdf = _paper(tmp_path / "paper.pdf")

    cli.run(manuscript=pdf, case=tmp_path / "case", provided=None, email="test@example.org",
            model=None, png=False, backend="pymupdf", with_scout=False, doi=None,
            formats=None, supplement=None, llm_refs=False)

    assert seen["llm_refs"] is False
    for command in (cli.refs, cli.run):
        assert "llm_refs" in inspect.signature(command).parameters, command.__name__
    assert inspect.signature(cli._refs_pipeline).parameters["llm_refs"].default is True


def test_the_reflist_disclosure_reaches_every_format_and_carries_the_ceiling(tmp_path):
    """The required disclosure, and the sentence that bounds what it means.

    "A model agreeing with a parse is a second reading, not confirmation" is the
    only honest claim available about this reading, and a reader who sees the
    model named without it will read it as confirmation.
    """
    from papertrace.disclosures import REFLIST_TOKEN, run_disclosures
    from papertrace.models import RefManifest, RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="claude-opus-5",
                           reflist_fields_discarded=["[2] journal"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    d = fired[0]
    assert d.token == REFLIST_TOKEN
    assert "second reading, not confirmation" in d.text
    assert "may also have missed" in d.text
    assert "claude-opus-5" in d.text


def test_a_model_reading_that_was_not_obtained_still_discloses_that(tmp_path):
    """Silence would be read as "no news". The attempt and its failure are news.

    The token stays true in this branch because the sentence is phrased around
    it — the same discipline `SUPPLEMENT_IDENTITY_TOKEN` is written under.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RefManifest, RunResults

    manifest = RefManifest(manuscript="p.pdf", reflist_model="",
                           reflist_fields_discarded=["not attempted — claude is not on PATH"])
    fired = [d for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)
             if d.key == "reflist"]
    assert len(fired) == 1, fired
    assert "not attempted" in fired[0].text
    assert "agreed" not in fired[0].text


def test_corroboration_is_disclosed_without_claiming_the_numbering_was_verified(tmp_path):
    """The false alarm this fixes, and the overstatement it must not become.

    On the 22-vs-19 case the numbering warning fires for a real reason. Where
    two readings do agree on every cited label, a reader deserves to be told —
    and deserves not to be told the numbering was checked, because it was not.
    """
    from papertrace.disclosures import run_disclosures
    from papertrace.models import RefManifest, RunResults

    manifest = RefManifest(manuscript="p.pdf", numbering_verified=False,
                           numbering_corroborated=True,
                           corroborating_readings=["llm", "parsed", "pymupdf"])
    fired = {d.key for d in run_disclosures(RunResults(manuscript="p.pdf"), manifest)}
    assert "numbering_corroboration" in fired
    assert "numbering" in fired, "corroboration must not suppress the unconfirmed-numbering warning"


def test_the_new_manifest_fields_round_trip_and_validate(tmp_path):
    """Gate 2. Five additive fields, none required, all readable by an older file."""
    import jsonschema

    from papertrace.models import RefManifest

    m = RefManifest(manuscript="p.pdf", numbering_corroborated=True,
                    corroborating_readings=["parsed", "pymupdf"],
                    labels_disputed=["6", "7"], reflist_model="claude-opus-5",
                    reflist_fields_discarded=["[6] journal"])
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    payload = json.loads(path.read_text())
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "refs_manifest.schema.json").read_text()
    )
    jsonschema.validate(payload, schema)
    assert not set(schema.get("required", [])) & {
        "numbering_corroborated", "corroborating_readings", "labels_disputed",
        "reflist_model", "reflist_fields_discarded",
    }

    back = RefManifest.from_json(path)
    assert back.numbering_corroborated is True
    assert back.corroborating_readings == ["parsed", "pymupdf"]
    assert back.labels_disputed == ["6", "7"]
    assert back.reflist_model == "claude-opus-5"
    assert back.reflist_fields_discarded == ["[6] journal"]

    # a manifest written before these fields must still load, and must not read
    # as "nothing disputed" — absent means never computed
    for key in ("numbering_corroborated", "corroborating_readings", "labels_disputed",
                "reflist_model", "reflist_fields_discarded"):
        del payload[key]
    path.write_text(json.dumps(payload))
    old = RefManifest.from_json(path)
    assert old.numbering_corroborated is False
    assert old.labels_disputed == []
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_reference_readings.py -q
```

Expected: `TypeError: _refs_pipeline() got an unexpected keyword argument
'llm_refs'` and `AttributeError: module 'papertrace.cli' has no attribute
'_reference_readings'`. Paste it.

- [ ] **Step 4: `RefManifest` gains ONE field, and the schema says what absent means**

**Read this before editing.** An earlier draft of this step added six fields
here. **Five of them already landed in Task 3** — verify with:

```bash
~/anaconda3/bin/python -c "import sys,dataclasses; sys.path.insert(0,'src'); from papertrace.models import RefManifest; print([f.name for f in dataclasses.fields(RefManifest)])"
```

Expected to be present already: `numbering_corroborated`,
`corroborating_readings`, `labels_disputed`, `labels_resolved`,
`reflist_model`, `reflist_fields_discarded`. **The only field this task adds is
`reflist_numbering_findings`.** The five declarations below are reproduced so
you can confirm the ones on disk match — if any differs, report it rather than
rewriting it; Task 3 is reviewed and closed. Add only the last one, and only its
`to_json` / `from_json` / schema entries.

(`numbering_choice` and `numbering_chosen_by` belong to Task 7. Do not add them.)

In `src/papertrace/models.py`, in `RefManifest`, after `numbering_ledger`:

```python
    # A SECOND, INDEPENDENT AXIS from `numbering_verified`, which keeps its exact
    # meaning: this says every label the body cites was read the same way by at
    # least two readings of the bibliography, not that anything checked the
    # numbering. Two readings of one document can agree about a reference the
    # layout destroyed in both. False means not established — either not
    # computed, or computed and not met.
    numbering_corroborated: bool = False
    # Named, and only when corroboration actually holds: an empty list is "no
    # corroboration was established", never "these readings disagreed".
    corroborating_readings: list[str] = field(default_factory=list)
    # Labels two readings actively contradict each other about, so no verdict is
    # printed for them. Absent means NEVER COMPUTED, not "none disputed" — the
    # same three-state discipline as `numbering_ledger`.
    labels_disputed: list[str] = field(default_factory=list)
    # The model that read the printed bibliography, when one did. Separate from
    # `RunResults.checker`, which names the model that judged the claims: two
    # stages, two models, and one field for both is the misattribution `ask.py`
    # was extracted to end.
    reflist_model: str = ""
    # What that reading cost in dropped values, and — when there was no reading —
    # why there was none. Empty means no model reading was asked for.
    reflist_fields_discarded: list[str] = field(default_factory=list)
    # A different kind of finding, kept in its own field for that reason: the
    # model reading's own labels did not add up — a numeral proposed twice, or a
    # gap in 1..max. Not a value that went missing, so counting it among the
    # discarded fields would report a number of dropped values that never
    # dropped. Empty means none found, which on a reading that produced no
    # entries is not the same as none present.
    reflist_numbering_findings: list[str] = field(default_factory=list)
```

In `to_json`'s payload, beside `numbering_ledger`:

```python
            "numbering_corroborated": self.numbering_corroborated,
            "corroborating_readings": self.corroborating_readings,
            "labels_disputed": self.labels_disputed,
            "reflist_model": self.reflist_model,
            "reflist_fields_discarded": self.reflist_fields_discarded,
            "reflist_numbering_findings": self.reflist_numbering_findings,
```

In `from_json`, `.get(...)` for each so an older file still loads:

```python
            numbering_corroborated=bool(data.get("numbering_corroborated", False)),
            corroborating_readings=data.get("corroborating_readings", []),
            labels_disputed=data.get("labels_disputed", []),
            reflist_model=data.get("reflist_model", ""),
            reflist_fields_discarded=data.get("reflist_fields_discarded", []),
            reflist_numbering_findings=data.get("reflist_numbering_findings", []),
```

In `schemas/refs_manifest.schema.json`, additive in `properties`, nothing added
to `required` (`["manuscript", "entries"]`):

```json
    "numbering_corroborated": {
      "type": "boolean",
      "description": "Every citation label the body uses was read the same way by at least two independent readings of the bibliography. A SECOND AXIS from `numbering_verified`, which is unchanged: this is not a confirmed numbering, because two readings of one document can agree about a reference the layout destroyed in both. Absent or false means not established — either never computed, or computed and not met."
    },
    "corroborating_readings": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Which readings corroborated, named — e.g. `parsed`, `pymupdf`, `crossref`, `llm`. Only populated when `numbering_corroborated` is true; empty means no corroboration was established, never that these readings disagreed."
    },
    "labels_disputed": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Citation labels two readings of the bibliography actively contradict each other about. Claims citing these get no verdict: they are reported `unchecked`, because the source that would be judged may be a different paper. Absent means NEVER COMPUTED, not that nothing was disputed."
    },
    "reflist_model": {
      "type": "string",
      "description": "The model that read the printed bibliography, when one did. Distinct from `results.json`'s `checker`, which names the model that judged the claims. Absent or empty means no model reading was obtained — see `reflist_fields_discarded` for why; it never means one agreed."
    },
    "reflist_fields_discarded": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Values a model proposed for the reference list that were not found verbatim in either extraction of the printed page, and so were discarded — plus, when no reading was obtained at all, the reason. Empty means no model reading was asked for."
    },
    "reflist_numbering_findings": {
      "type": "array",
      "items": { "type": "string" },
      "description": "Findings about the model reading's own labels rather than its values: a numeral it proposed twice, or a gap in 1..max. Kept apart from `reflist_fields_discarded` because nothing went missing — folding the two would report a count of discarded values that includes things no value ever lost. Empty means none were found, which on a reading that produced no entries is not the same as none being present."
    }
```

Add the round-trip assertions for it beside the existing `reflist_*` ones, and
one behavioural test that a duplicate numeral actually reaches the manifest —
the whole point of this field is that `reflist.propose` reports it and an
earlier draft of this plan dropped it on the floor:

```python
def test_a_numbering_finding_from_the_model_reading_reaches_the_manifest(
    tmp_path, monkeypatch
):
    """`reflist.propose` reports a duplicated or missing numeral separately from
    a discarded field, because the two mean different things. An earlier draft
    of this step read only `fields_discarded`, so every duplicate and gap the
    model reading found was silently dropped before any reader saw it."""
    proposed = (
        [_work("2", "10.1000/x2"), _work("2", "10.1000/x22")],
        reflist_mod.ReflistProvenance(
            model="claude-opus-5",
            entries_proposed=2,
            numbering_findings=["numerals proposed twice: 2"],
            readings=["pymupdf", "docling"],
        ),
    )
    monkeypatch.setattr(reflist_mod, "propose", lambda *a, **kw: proposed)

    manifest = _run_refs(tmp_path, monkeypatch, llm_refs=True)

    assert manifest.reflist_numbering_findings == ["numerals proposed twice: 2"]
    assert not any(
        "twice" in f for f in manifest.reflist_fields_discarded
    ), manifest.reflist_fields_discarded
```

Use whatever fixture helper the tests in Step 2 established for driving
`_refs_pipeline` and reading the manifest back; `_run_refs` above is a
placeholder for that helper's real name, which Step 2 fixes — **do not add a
second way to run the stage.**

- [ ] **Step 5: `cli._reference_readings` — the voter dict, and nothing impure**

In `src/papertrace/cli.py`, above `_refs_pipeline` (`:496`):

```python
def _needs_flat_reading(smap) -> bool:
    """Is a flat-text reading of this bibliography a second reading at all?

    `smap.converter` is `"pymupdf"` or `"docling <version>"`. On a run whose
    backend already IS pymupdf, `references_span_flat` returns the same text
    `references_span` did, so the cost buys nothing — and counting it as a
    reading would turn every `single` label into `agreed` and report
    corroboration that nothing corroborated.
    """
    return smap.converter.split()[0] != "pymupdf"


def _reference_readings(
    *,
    smap,
    crossref: list["RefEntry"] | None = None,
    parsed: list["RefEntry"] | None = None,
    flat: list["RefEntry"] | None = None,
    llm: list["RefEntry"] | None = None,
) -> dict[str, list["RefEntry"]]:
    """The readings of the bibliography that are entitled to a vote, by name.

    Pure, so the agreement axis is testable without a PDF, a network or a model.

    A reading that is ABSENT is omitted, never present as `[]`: `label_agreement`
    counts the readings that carry a label, so an empty list votes against every
    label it does not have — which is all of them — and turns "we did not ask"
    into "one reading says no".

    NOTHING here reaches `reconcile`'s arguments or `resolve_all`. `reconcile`
    still chooses between the deposit and the run backend's parse; this dict is
    a second, orthogonal axis whose only power is to withhold a verdict.
    """
    out: dict[str, list[RefEntry]] = {}
    if crossref:
        out["crossref"] = crossref
    if parsed:
        out["parsed"] = parsed
    # double-guarded on purpose: the caller skips the *work* on a pymupdf run,
    # and this refuses the *vote* even if handed one, so a future caller cannot
    # reintroduce self-corroboration by passing the text in anyway
    if flat and _needs_flat_reading(smap):
        out["pymupdf"] = flat
    if llm:
        out["llm"] = llm
    return out


def _llm_reference_reading(
    reading_a: str,
    reading_b: str,
    *,
    label_a: str,
    label_b: str,
    enabled: bool,
) -> tuple[list["RefEntry"], "ReflistProvenance"]:
    """A model's reading of the bibliography, or a stated reason there is none.

    Returns `(entries, provenance)` — the provenance object itself, **always**,
    never `None` and never a tuple of loose pieces. `reflist.propose` reports
    two distinct kinds of finding (`fields_discarded`, a value that was not
    printed; `numbering_findings`, a reading whose labels do not add up) and a
    3-tuple of `(entries, notes, model)` could only carry one of them. An
    earlier draft of this plan did exactly that and would have dropped every
    duplicate and gap on the floor, unreported.

    `provenance.fields_discarded` is never empty when `enabled`:
    an attempt that produced nothing has to leave the reason where the manifest
    and the report can read it, because silence here would be read as "no news"
    about a reading that was asked for and did not happen.
    """
    from . import ask
    from .reflist import ReflistProvenance, propose

    if not enabled:
        return [], ReflistProvenance()
    if not ask.claude_available():
        # the ordinary case for someone who installed papertrace and not Claude
        # Code. The run proceeds on the deterministic readings and says so —
        # never that a model agreed with them.
        return [], ReflistProvenance(
            fields_discarded=["not attempted — claude is not on PATH, so no model read the list"]
        )
    try:
        entries, prov = propose(reading_a, reading_b, label_a=label_a, label_b=label_b)
    except Exception as e:  # noqa: BLE001 — a failed corroboration is not a failed refs stage
        # Blanket, and here specifically: `refs` has already parsed the list and
        # is about to fetch the sources, and this call is the one thing in the
        # stage that leaves the machine. The seam raises RuntimeError for a
        # timeout and a non-zero exit and ValueError for unparseable stdout, and
        # a subprocess can surface OSError besides — enumerating them would fail
        # closed on the next one. The failure is recorded, not swallowed:
        # `reflist_fields_discarded` carries it into the manifest and a console
        # line says it at the time. Same shape as `check.py:996`.
        return [], ReflistProvenance(
            fields_discarded=[f"not obtained — {type(e).__name__}: {str(e)[:200]}"]
        )
    if prov.discarded_whole:
        # the entries are already `[]` in this branch; the reason is what travels,
        # and it goes FIRST so the console line quotes it rather than a field name
        prov.fields_discarded.insert(0, f"reading discarded — {prov.discarded_whole}")
    return entries, prov
```

Add `RefEntry` to `cli.py`'s type-checking imports if it is not already
importable there; the quoted annotations above are what keep the module-level
import list unchanged.

- [ ] **Step 6: Wire it into `_refs_pipeline`**

Add the parameter to the signature (`cli.py:496-505`), after `supplement`:

```python
    llm_refs: bool = True,
```

At `:554`, keep the parse as a reading in its own right:

```python
    entries = parse_references(refs_text)
    # `entries` is rebound to the reconciled list below, which destroys the parse
    # as a separate reading — and the parse is one of the four voters
    parsed = entries
```

Replace the comment at `:560-563`. It currently ends "`--parse-only` stays
offline, so it gets no second candidate", which this task makes false:

```python
    # The manuscript's own [N] markers arbitrate. Free, offline, and the only
    # one of the readings that is definitionally right about what the paper
    # cites — every reading of the bibliography is a candidate measured against
    # it. --parse-only stays offline: it gets the flat-text reading, which is
    # local, and never the model's, which is spend.
```

Immediately **before** the `reconcile` call at `:638`:

```python
    # The bibliography, read again. Both extra readings are voters only: neither
    # reaches `reconcile`'s arguments or `resolve_all`, so no model output and no
    # flat-text reading can cause a source to be resolved, downloaded or judged.
    # They can only cause a verdict to be withheld.
    flat_text, flat_entries = "", None
    if _needs_flat_reading(smap):
        flat_text, _ = references_span_flat(manuscript)
        flat_entries = parse_references(flat_text)
    llm_entries, reflist_prov = _llm_reference_reading(
        refs_text,
        flat_text,
        label_a=smap.converter,
        label_b="pymupdf",
        # --parse-only promises "no network"; a `claude -p` subprocess is both
        # network and spend, so the flag is forced off here rather than trusted
        # to the caller
        enabled=llm_refs and not parse_only,
    )
    others = _reference_readings(
        smap=smap, crossref=crossref_entries, parsed=parsed,
        flat=flat_entries, llm=llm_entries,
    )
    if reflist_prov.model:
        dropped = len(reflist_prov.fields_discarded)
        console.print(
            f"the reference list was also read by [bold]{reflist_prov.model}[/bold] · "
            f"{dropped} field{'' if dropped == 1 else 's'} discarded as "
            f"not printed [dim]— a second reading, not confirmation[/dim]"
        )
        # reported separately, because it is a different kind of finding: not a
        # value that was missing, but a reading whose own labels do not add up
        for finding in reflist_prov.numbering_findings:
            console.print(f"  [yellow]⚠ the model reading's {finding}[/yellow]")
    elif reflist_prov.fields_discarded:
        console.print(
            "[yellow]⚠ no model reading of the reference list[/yellow] — "
            f"{reflist_prov.fields_discarded[0]}"
        )
```

Add `references_span_flat` to the lazy `from .ingest import ...` at `:534` and
`label_agreement` to the lazy `from .refs import ...` at `:536-546`.

**After** the `reconcile` call and its `rec.note` fix-up (`:638-641`), fill the
second axis:

```python
    # A second, independent axis. `rec.verified` keeps its exact meaning and
    # nothing here may touch it: two readings of one document agreeing says
    # nothing about a reference the layout destroyed in both.
    agreement = label_agreement(others, body_labels)
    rec.labels_disputed = sorted(
        (label for label, state in agreement.items() if state == "disputed"),
        key=lambda s: (0, int(s), "") if s.isdigit() else (1, 0, s),
    )
    # every cited label, or it is not corroboration. One disputed label is not
    # "mostly corroborated", and `single` is not agreement — it is one reading
    rec.corroborated = bool(body_labels) and all(
        agreement.get(label) == "agreed" for label in body_labels
    )
    # named only when it holds: an empty list means no corroboration was
    # established, never that these readings disagreed
    rec.corroborating_readings = sorted(others) if rec.corroborated else []
    if rec.corroborated:
        console.print(
            f"[green]✓ {len(others)} readings of the reference list agree[/green] on every "
            f"cited label [dim]({', '.join(sorted(others))}) — corroboration, not a "
            f"confirmed numbering[/dim]"
        )
    if rec.labels_disputed:
        console.print(
            f"[yellow]⚠ readings disagree at [{'], ['.join(rec.labels_disputed)}][/yellow] — "
            "claims citing those labels will be reported unchecked, not guessed"
        )
```

And in `RefManifest(...)` at `:702-714`:

```python
        numbering_corroborated=rec.corroborated,
        corroborating_readings=rec.corroborating_readings,
        labels_disputed=rec.labels_disputed,
        reflist_model=reflist_model,
        reflist_fields_discarded=reflist_prov.fields_discarded,
        reflist_numbering_findings=reflist_prov.numbering_findings,
```

**Note the ordering constraint:** the agreement block must sit above the
`--parse-only` early return at `:655-658` for its console lines to be printed at
all on a `--parse-only` run — that run writes no manifest, so the console is its
only output.

- [ ] **Step 7: Stamp `seen_in` on the chosen entries**

Task 5 declared `RefEntry.seen_in` and gave it a schema, and `reflist` fills it
on its own candidate entries — **which never reach the manifest**, because
Ruling 2 keeps the model's reading out of the chosen list. So without this step
the published field is empty on every manifest ever written, and the spec's
stated purpose for it — *"an entry seen only in the model's reading must be
spottable"* — is unserved.

Add to `src/papertrace/refs.py`, immediately below `label_agreement`, so
`_same_work` stays private to this module:

```python
def stamp_seen_in(chosen: list[RefEntry], candidates: dict[str, list[RefEntry]]) -> None:
    """Record, per chosen entry, which readings also carried that work.

    In place, because the entries are already the manifest's. An entry carried
    by one reading only is the interesting case — especially `["llm"]`, which
    means no deterministic reading found it at all.

    Matched on the label AND `_same_work`: a reading that carries [12] naming a
    different paper has not corroborated this entry, and stamping its name here
    on the strength of the shared numeral would turn a disagreement into
    provenance. That is the same mistake `_covers` makes about extent, and it is
    the reason this is not simply `label in {e.num for e in cand}`.
    """
    for e in chosen:
        e.seen_in = sorted(
            name
            for name, cand in candidates.items()
            if any(o.num == e.num and _same_work(e, o) for o in cand)
        )
```

Call it in `_refs_pipeline`, immediately after the agreement block and **before**
`resolve_all` — the entries are mutated in place and `resolve_all` does not read
`seen_in`, but stamping after the manifest is built would write nothing:

```python
    stamp_seen_in(entries, others)
```

Add `stamp_seen_in` to the `from .refs import (...)` block in `cli.py`, `I`-sorted.

Two tests, appended to `tests/test_label_agreement.py` (it already imports
`_entry` and the fixtures):

```python
def test_an_entry_only_the_model_found_is_spottable_in_the_manifest():
    """The published field's whole purpose. An entry no deterministic reading
    carried is the one a reader most needs to see flagged, and `seen_in ==
    ["llm"]` is how they see it."""
    chosen = [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")]
    candidates = {
        "parsed": [_work("1", "10.1000/x1"), _work("2", "10.1000/x2")],
        "llm": [_work("1", "10.1000/x1")],
    }
    stamp_seen_in(chosen, candidates)
    assert chosen[0].seen_in == ["llm", "parsed"]
    assert chosen[1].seen_in == ["parsed"]


def test_a_reading_naming_a_different_paper_under_the_same_label_is_not_provenance():
    """Sharing a numeral is not agreeing about a work.

    Stamping on the label alone would record the disagreeing reading as having
    corroborated this entry — the extent-for-content substitution `_covers` is
    documented as blind to, reintroduced in a published field."""
    chosen = [_work("5", "10.1000/x5")]
    candidates = {"parsed": [_work("5", "10.1000/x5")],
                  "pymupdf": [_work("5", "10.1000/x99")]}
    stamp_seen_in(chosen, candidates)
    assert chosen[0].seen_in == ["parsed"]
```

Extend the import at the top of that module to
`from papertrace.refs import LABEL_AGREEMENT, _entry, label_agreement, stamp_seen_in  # noqa: E402`.

- [ ] **Step 8: The flag on both commands**

`refs` (`cli.py:727-756`), after `supplement`:

```python
    llm_refs: bool = typer.Option(
        True, "--llm-refs/--no-llm-refs",
        help="Also have a model read the printed reference list as a second opinion "
             "on the numbering (one extra model call; off under --parse-only)",
    ),
```

and add `llm_refs=llm_refs` to the `_refs_pipeline(...)` call below it.

`run` (`cli.py:1141-1178`), the same option declaration after `supplement`, and
`llm_refs=llm_refs` added to the `_refs_pipeline(...)` call at `:1207`. Both
commands, because `papertrace refs` alone and `papertrace run` both write the
manifest `check` will read, and a flag on one of them is a flag the other
silently ignores.

- [ ] **Step 9: The two disclosures, and the terminal template's allow-list**

In `src/papertrace/disclosures.py`, beside the other tokens:

```python
REFLIST_TOKEN = "the reference list was also read by a model"
NUMBERING_CORROBORATION_TOKEN = "two readings of the reference list agree on every cited label"
```

Neither key goes in `CLAIM_KEYS` — both are run-level.

Producers, beside `_numbering_contested`:

```python
def _reflist(manifest) -> Disclosure | None:
    """The required disclosure for the model reading, in all three of its outcomes.

    Fires on a reading used, a reading discarded, and a reading asked for and not
    obtained — never only on success, because silence about an attempt reads as
    no news. The token is phrased AROUND in every branch, the way
    `SUPPLEMENT_IDENTITY_TOKEN` is: it is asserted verbatim in all four formats,
    so a branch where it is not literally true would make one format lie.
    """
    model = getattr(manifest, "reflist_model", "") or ""
    notes = list(getattr(manifest, "reflist_fields_discarded", []) or [])
    # a different kind of finding, and it must reach the reader too: a reading
    # whose own labels do not add up is worth knowing about even when every
    # value it proposed was printed
    numbering = list(getattr(manifest, "reflist_numbering_findings", []) or [])
    if not model and not notes:
        return None  # no model reading was asked for
    ceiling = (
        "A model agreeing with a parse is a second reading, not confirmation: it read "
        "the same document, so a reference the layout destroyed is one it may also "
        "have missed."
    )
    if not model:
        head = (
            f"This run asked that {REFLIST_TOKEN}, and no reading was obtained: "
            f"{notes[0]}. The reference numbering therefore rests on the readings above "
            f"it and nothing else."
        )
        short = f"{REFLIST_TOKEN}: asked for, not obtained — {notes[0]}"
    elif any(n.startswith("reading discarded") for n in notes):
        why = next(n for n in notes if n.startswith("reading discarded"))
        head = (
            f"Here {REFLIST_TOKEN} ({model}), and its reading was discarded rather than "
            f"used: {why}. Nothing it proposed contributed to the list below."
        )
        short = f"{REFLIST_TOKEN} ({model}) — discarded as unusable"
    else:
        dropped = (
            f"{len(notes)} value{'' if len(notes) == 1 else 's'} it proposed "
            f"{'was' if len(notes) == 1 else 'were'} not found in the printed text and "
            f"{'was' if len(notes) == 1 else 'were'} discarded"
            if notes else "every value it proposed was found in the printed text"
        )
        head = (
            f"Here {REFLIST_TOKEN} ({model}), shown two extractions of the same printed "
            f"bibliography and asked what numbered list it carries; {dropped}."
        )
        short = f"{REFLIST_TOKEN} ({model}) — {len(notes)} discarded"
    if numbering:
        # appended rather than folded into `dropped`: "3 values discarded" and
        # "it numbered one entry twice" are different facts, and a reader who
        # sees them as one number cannot tell which happened
        head += (
            " Its own numbering did not add up either — "
            + "; ".join(numbering)
            + "."
        )
    return Disclosure(
        key="reflist",
        level="info",
        token=REFLIST_TOKEN,
        text=f"{head} {ceiling}",
        short=short,
        # both kinds, numbering first: it describes the reading as a whole, and
        # a truncated list should not lose the structural finding to six field
        # names
        rows=tuple((numbering + notes)[:6]),
    )


def _numbering_corroboration(manifest) -> Disclosure | None:
    """Fires on agreement, and deliberately NOT gated on `numbering_verified`.

    The unconfirmed-numbering warning is right to fire whenever nothing checked
    the numbering, and on a paper whose readings all agree it is also the whole
    of what the report says about the reference list — which is how it came to
    read as an alarm about the 22-vs-19 case on papers where nothing was wrong.
    This is the other half of that sentence, and it is careful not to become the
    claim the warning is about: agreement between readings is not a checked
    numbering.
    """
    if not getattr(manifest, "numbering_corroborated", False):
        return None
    readings = list(getattr(manifest, "corroborating_readings", []) or [])
    named = ", ".join(readings) if readings else "the readings taken"
    return Disclosure(
        key="numbering_corroboration",
        level="info",
        token=NUMBERING_CORROBORATION_TOKEN,
        text=(
            f"On this run {NUMBERING_CORROBORATION_TOKEN} ({named}). That is corroboration, "
            "not confirmation, and it does not make the numbering verified: these are "
            "readings of one printed page, so a reference its layout destroyed is one they "
            "can all have missed in the same way. Any label they disagreed about is listed "
            "separately, and no verdict was printed for it."
        ),
        short=f"{NUMBERING_CORROBORATION_TOKEN} ({named})",
    )
```

In `run_disclosures`, inside the `if manifest is not None:` block, after the
`_numbering_contested` line:

```python
        if d := _numbering_corroboration(manifest):
            out.append(d)
        if d := _reflist(manifest):
            out.append(d)
```

`report_terminal.html.j2` is the one format where a run-level key is not free.
After the `numbering_contested` block (template lines 103-105), add:

```jinja
{% for d in disclosures if d.key == "numbering_corroboration" %}
    <div class="log"><span class="step">▸ resolve</span><span class="dim">{{ d.short }}</span></div>
{% endfor %}
{% for d in disclosures if d.key == "reflist" %}
    <div class="log"><span class="step">▸ resolve</span><span class="dim">{{ d.short }}</span></div>
{% endfor %}
```

The other three formats need nothing: `report.md.j2:19`, `report_editor.html.j2:122`
and `viewer_app.js:195` are catch-alls for run-level keys.

**Check before you finish:** `labels_disputed` is the third new run-level key in
the contract and belongs to whichever of Tasks 3/4 fires it. Run
`~/anaconda3/bin/python -m pytest tests/test_disclosure_parity.py -q` — if
`test_the_terminal_template_names_every_disclosure_key_that_exists` is red for
`labels_disputed`, add its branch here too and say so in the report; do not
leave it red for the next task.

- [ ] **Step 10: Move the advertised call ceiling**

`wizard.workload` (`wizard.py:214-226`) promises a worst-case number of model
calls, and the wizard delegates to `_refs_pipeline`, which now makes one more.
`CHANGELOG.md` records why this constant exists: an advertised ceiling that the
real policy can exceed is a false promise about money.

```python
        # one reference-list reading, one extraction call, then one per cited
        # source. The reference-list call gets no retry — `reflist.propose` asks
        # once and reports what it got — so it adds exactly 1 to both figures.
        "model_calls": 2 + cited_source_calls,
        "model_calls_max": 2 + ASK_ATTEMPTS * cited_source_calls,
```

Update `tests/test_wizard.py:670-679` to match (`w["model_calls"] == 3` for the
one-source fixture, `w["model_calls_max"] == 2 + ASK_ATTEMPTS * 1`), and put the
reason in that test's docstring. If Task 1 already moved
`model_calls_max` to `ASK_ATTEMPTS * (1 + cited_source_calls)`, add the
reference-list call to whatever shape is there rather than reverting it.

Beyond the letter of this task's brief, and included on purpose: the flag and
the wizard's estimate are the same promise to the same reader.

- [ ] **Step 11: Run everything**

```bash
~/anaconda3/bin/python -m pytest tests/test_reference_readings.py -q
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

Paste all three. Expected movement outside the new module:
`tests/test_wizard.py` (Step 9), and `tests/test_disclosure_parity.py` gaining
two keys. Anything else is a real find.

- [ ] **Step 12: Gate 4, Gate 2, and the Gate 5 ruling**

**Gate 4: applies in substance, not in letter.** This task edits no line of
`refs.py`, `check.py` or `scout.py` — it fills fields Task 3 added to
`Reconciliation` and consumes `label_agreement`. But the failure paths *are*
this task's subject, so prove them rather than claiming the gate is moot:

```bash
~/anaconda3/bin/python -m pytest tests/test_reference_readings.py \
    tests/test_reference_reconciliation.py tests/test_refs.py \
    tests/test_check_provenance.py -q
```

and state the three degradations with their tests:
`claude` absent ⇒ `test_an_unavailable_claude_degrades_to_a_stated_absence`;
the call raising ⇒ `test_a_failed_model_call_does_not_kill_the_refs_stage`;
a model naming other papers ⇒ `test_a_model_naming_different_papers_changes_no_resolved_entry`.
In all three the run proceeds on the deterministic readings and no format
anywhere says a model agreed.

**Gate 2: applies.** Five additive manifest properties, none in `required`,
every description stating what absent means, round-tripped by
`test_the_new_manifest_fields_round_trip_and_validate` — including the
deleted-keys half, which is what proves absent still means never computed.

**Gate 5: ruled not applicable — stated, not skipped.** This task changes no
`ingest/`, `highlight.py` or `report.py` *code*. `report_terminal.html.j2`
gains two allow-list branches, which is a template, and
`tests/test_disclosure_parity.py` covers it mechanically for all four formats.
Do **not** re-run the demo: it would spend a paid model call, and the showcase
in `examples/demo/output/` is pinned to `--model claude-opus-5` for
reproducibility — regenerating it here would change the committed artifact for
a reason unrelated to this task. The demo re-run belongs to Task 9, once the
feature is whole, and that is where the `--llm-refs` line in the README's
does/does-not list lands too.

- [ ] **Step 13: Commit**

```bash
git add src/papertrace/cli.py src/papertrace/models.py src/papertrace/disclosures.py \
        src/papertrace/templates/report_terminal.html.j2 src/papertrace/wizard.py \
        schemas/refs_manifest.schema.json tests/test_reference_readings.py \
        tests/test_wizard.py
git commit -m "refs: four readings of the bibliography, and a second axis to say so

$(cat <<'BODY'
The flat-text reading and the model's reading are voters. Neither reaches
reconcile's arguments or resolve_all, so no model output can cause a source to be
resolved, downloaded or judged — only a verdict to be withheld. A run whose model
reading names two different papers produces byte-identical entries to --no-llm-refs,
and that equality is a test.

The pymupdf reading is skipped when the run backend already is pymupdf: a reading
agreeing with itself is not corroboration. --parse-only keeps it and loses the
model call, because "no network" is what the flag promises.

numbering_corroborated is a second axis and numbering_verified keeps its exact
meaning. Corroboration is what lets the numbering warning stop being the only
thing a clean reference list gets told about.

claude absent, or the call failing, leaves a stated reason in the manifest and on
the console — never that the model agreed.
BODY
)"
```

---

### Task 7: Show the disagreement, then offer to resolve it

**Why:** Task 6 leaves a run that found contradicting readings of the
bibliography with nothing to do but withhold. That is the right *default* and
the wrong *only* option: on the manuscript that motivated this design, 14 of 22
labels were disputed, and a human with the printed list in front of them can
settle most of them in a minute. So the run offers to help — after showing its
work.

**The user's explicit instruction, which overrides any menu-first reading of
the spec:** *"Show the full disagreement first and then use LLM to fix."* Step 1
is unskippable and happens before any question is asked. A menu offering to
resolve a disagreement the reader has not seen asks for consent to a fact
nobody showed them — and `chosen_by: "user"` is only worth recording if the
user had the disagreement in front of them. The ordering is encoded as a test
(Step 1's `_ask` fake asserts the file exists when it is called), not as a
comment.

**Where it lives, and why not `wizard.py`.** `cli._refs_pipeline`. Both
`papertrace refs` and `papertrace run` need it — the numbering is settled before
`refs_manifest.json` is written, and both `scout` and `check` read that file —
and `wizard.py` already delegates to `_refs_pipeline` for the whole refs stage
(`tests/test_wizard.py:434` spies on exactly that call). Putting the escalation
in the wizard would give the guided path a safety feature the documented CLI
path does not have. A reviewer will suggest moving it; this paragraph is the
answer.

**Files:**
- Modify: `src/papertrace/cli.py` — `_interactive()`, `_label_span()`,
  `_write_disagreement()`, `_escalate_disputed()`, and the call wired in at
  `cli.py:659` (immediately after the `if parse_only: … return` block, so
  `--parse-only` never prompts and never spends a model call — it promises "no
  network")
- Modify: `src/papertrace/reflist.py` — `RESOLVE_PROMPT`, `Resolution`,
  `resolve_disputed()`. Reuses `_found` and `_usable_doi`; reimplementing either
  would give the resolution call a *weaker* verbatim check than the proposal
  call, which is the wrong way round
- Modify: `src/papertrace/disclosures.py` — the three remaining `claim_pairing`
  texts (Task 4 shipped only `withheld`)
- Modify: `schemas/refs_manifest.schema.json` — `numbering_choice`,
  `numbering_chosen_by`, `labels_resolved`
- Test: `tests/test_escalation.py` (the escalation, end to end through
  `_refs_pipeline`), `tests/test_reflist.py` (the three rules on the reply),
  `tests/test_disclosure_parity.py` (already a loop — the three new texts need
  no new parity test, only a producer that emits them)

**No template change.** Task 4 added the `claim_pairing` branch to
`report.md.j2`, `report_editor.html.j2` and the terminal allow-list. This task
adds three more *texts* behind the same key, and `CLAIM_PAIRING_TOKEN` is
unchanged, so the parity loop already covers them. State this in the report
rather than editing a template to be safe: an extra branch on the same key
renders the disclosure twice.

**Interfaces:**
- Consumes: `refs.LABEL_AGREEMENT`, `refs.label_agreement`,
  `Reconciliation.labels_disputed` (Task 3); `cli._reference_readings` (Task 6);
  `reflist._found`, `reflist._usable_doi`, `reflist.ReflistProvenance` (Task 5).
- Produces:
  - `cli._interactive() -> bool`
  - `cli._write_disagreement(case, labels, candidates, texts) -> Path`
  - `cli._escalate_disputed(...) -> list[RefEntry]` — mutates `rec` in place and
    returns the entry list to carry forward. **Changed by menu option 3 (a whole
    reading substituted) and by menu option 1 (each resolved label's entry
    substituted).** An earlier draft of this block said "option 3 only", which
    would leave option 1 un-disputing a label while judging it against the entry
    the resolution ruled against — see Step 5's comment and its test.
  - `reflist.Resolution` — `resolved: dict[str, RefEntry]`,
    `still_disputed: list[str]`, `provenance: ReflistProvenance`
  - `reflist.resolve_disputed(labels, entries, texts, *, model=None) -> Resolution`
  - `reflist.RESOLVE_PROMPT` — `<<LABELS>>` / `<<A>>` / `<<B>>` placeholders,
    `.replace()` idiom, matching `REFLIST_PROMPT` and `check.py`'s `<<CLAIMS>>`
  - `disclosures.CLAIM_PAIRING_TOKEN` is unchanged; `_claim_pairing` gains three
    branches
- Records on `Reconciliation` (all fields already declared in Task 3):
  `choice ∈ ("", "withheld", "llm_resolved", "parsed", "pymupdf")`,
  `chosen_by ∈ ("", "default", "user")`, `labels_resolved`, `labels_disputed`.

**The constraint that dictates the shape of this task.** `chosen_by: "user"` is
what makes the escalation honest — *a person consenting to proceed is an input,
not evidence*. The codebase already ranks that signal, and the comment in
`_escalate_disputed` quotes it: *"a filename carries the user's assertion,
content carries none."* So no branch here may set `rec.verified` or
`manifest.numbering_verified`. Task 8 parametrises over all four menu choices to
prove it.

**Non-interactive fallback, per the user's decision: suppress only the disputed
labels.** A non-interactive run does not prompt, does not call the resolver,
sets `choice="withheld"` and `chosen_by="default"`, and keeps every
non-disputed label's verdicts. It does **not** withhold the whole audit and it
does **not** pick a reading on the user's behalf — the first throws away a
useful audit over a numbering the reader can check by hand, and the second
records a choice nobody made.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_escalation.py`. **There is no `tests/test_cli.py` in this
repo** — 36 test modules, verified with `ls tests/`; the CLI's stage wiring is
tested in `test_pipeline.py` and `test_pipeline_split.py`. The escalation is its
own concern with its own fixtures, so it gets its own module, which matches how
`test_anchor_state.py`, `test_case_identity.py` and `test_table_fidelity.py` are
organised.

Open it with the standard preamble:

```python
"""The disagreement is shown before the question is asked.

A reader who is offered a choice between two readings of a bibliography and has
not been shown either one is being asked to rubber-stamp, not to decide. So
step one writes the whole disagreement to a file and prints the path, and it is
unskippable — `test_the_disagreement_is_written_before_the_question_is_asked`
is that requirement encoded, and it fails if the two are ever reordered.
"""

import os
import sys
from pathlib import Path

import pymupdf
import pytest
from rich.prompt import Prompt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli, reflist  # noqa: E402
from papertrace.models import RefEntry  # noqa: E402
from papertrace.refs import _entry  # noqa: E402

# --- the interactive escalation: shown first, then offered ------------------
#
# Driven through `_refs_pipeline` rather than through `_escalate_disputed`
# directly: the thing under test is that a real run reaches the file before it
# reaches the prompt, and a unit test of the escalation alone cannot see the
# ordering the user asked for.


def _disputing_paper(path: Path) -> Path:
    """A paper whose two readings of the bibliography will disagree about [2].

    The disagreement itself is injected by stubbing `_reference_readings` — the
    point of the fixture is a real PDF with a real References section, so
    `_refs_pipeline` runs its whole length.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Agreement in CT of the Pancreas", fontsize=16)
    page.insert_text((72, 140), "Body text citing [1] and [2] here.", fontsize=11)
    page.insert_text((72, 200), "References", fontsize=14)
    page.insert_text((72, 230), "[1] Alpha A. First paper. 2020.", fontsize=11)
    page.insert_text((72, 250), "[2] Beta B. Second paper. 2021.", fontsize=11)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def disputed(tmp_path, monkeypatch):
    """A run that reaches the escalation with [2] disputed, and nothing else stubbed.

    Returns `(pdf, case, answers)`; the caller fills `answers` with the menu
    keystrokes it wants `Prompt.ask` to return.
    """
    import papertrace.cli as cli_mod
    import papertrace.refs as refs_mod
    from papertrace.models import RefEntry
    from papertrace.refs import CROSSREF_NO_DOI, CrossrefDeposit

    monkeypatch.setattr(refs_mod, "resolve_all",
                        lambda entries, dest, email, provided_dir=None, progress=None,
                        taken=None: entries)
    monkeypatch.setattr(refs_mod, "crossref_deposit",
                        lambda client, doi, email: CrossrefDeposit(absent=CROSSREF_NO_DOI))

    # two readings that name different papers at [2] and the same one at [1]
    readings = {
        "parsed": [
            RefEntry(num="1", raw="Alpha A. First paper. 2020.", doi="10.1000/alpha",
                     year="2020", slug="alpha-2020"),
            RefEntry(num="2", raw="Beta B. Second paper. 2021.", doi="10.1000/beta",
                     year="2021", slug="beta-2021"),
        ],
        "pymupdf": [
            RefEntry(num="1", raw="Alpha A. First paper. 2020.", doi="10.1000/alpha",
                     year="2020", slug="alpha-2020"),
            RefEntry(num="2", raw="Gamma G. A wholly different paper. 2019.",
                     doi="10.1000/gamma", year="2019", slug="gamma-2019"),
        ],
    }
    texts = {
        "parsed": "1. Alpha A. First paper. 2020.\n2. Beta B. Second paper. 2021.\n",
        "pymupdf": "1. Alpha A. First paper. 2020.\n"
                   "2. Gamma G. A wholly different paper. 2019.\n",
    }
    monkeypatch.setattr(cli_mod, "_reference_readings", lambda **kw: (readings, texts))
    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)

    answers: list[str] = []
    monkeypatch.setattr(cli_mod.Prompt, "ask",
                        staticmethod(lambda *a, **k: answers.pop(0)))
    return _disputing_paper(tmp_path / "paper.pdf"), tmp_path / "case", answers


def _run_refs(pdf: Path, case: Path) -> "RefManifest":
    from papertrace.models import RefManifest

    cli._refs_pipeline(manuscript=pdf, case=case, provided=None,
                       email="t@example.org", parse_only=False, backend="pymupdf",
                       doi=None, supplement=None)
    return RefManifest.from_json(case / "refs_manifest.json")


def test_the_disagreement_is_written_before_the_question_is_asked(disputed, monkeypatch):
    """The user's instruction, encoded as a test: show, then offer.

    The fake `Prompt.ask` asserts the file is already on disk when it is
    called. That is the only way to pin the ordering — a test that checks the
    file afterwards passes just as well on an implementation that asks first.
    """
    pdf, case, answers = disputed
    path = case / "out" / "reference_disagreement.md"
    seen = {}

    def ask(*a, **k):
        seen["existed"] = path.exists()
        seen["text"] = path.read_text(encoding="utf-8") if path.exists() else ""
        return "2"

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(ask))
    _run_refs(pdf, case)

    assert seen["existed"] is True, "the menu was reached before the evidence was written"
    # both readings of the disputed label, and the text each was read from
    assert "Beta B. Second paper" in seen["text"]
    assert "Gamma G. A wholly different paper" in seen["text"]
    assert "[2]" in seen["text"]
    # and not a word about the label the readings agree on
    assert "First paper" in seen["text"], "the agreed label's text is context, not a finding"


def test_the_path_to_the_disagreement_file_is_printed(disputed, capsys):
    """A file nobody is told about is a file nobody reads, and the menu's first
    option spends money on a question the reader could answer by looking."""
    pdf, case, answers = disputed
    answers.append("2")
    _run_refs(pdf, case)
    assert "reference_disagreement.md" in _plain(capsys.readouterr().out)


def test_the_default_answer_asks_the_model_and_records_the_user_as_choosing(disputed):
    """Option 1, taken by pressing return. `chosen_by` is `user` even on the
    default: the person was shown the disagreement and consented to proceed,
    which is an input to record, not evidence of anything."""
    pdf, case, answers = disputed
    answers.append("1")
    manifest = _run_refs(pdf, case)
    assert manifest.numbering_choice == "llm_resolved"
    assert manifest.numbering_chosen_by == "user"
    assert manifest.numbering_verified is False


def test_withholding_all_of_them_is_the_second_option(disputed):
    pdf, case, answers = disputed
    answers.append("2")
    manifest = _run_refs(pdf, case)
    assert manifest.numbering_choice == "withheld"
    assert manifest.numbering_chosen_by == "user"
    assert manifest.labels_disputed == ["2"]
    assert manifest.labels_resolved == []


def test_choosing_one_reading_whole_records_which_one(disputed):
    """Option 3. Both offered readings are deterministic parses, so this is a
    choice between two things the tool produced — never the model's list, which
    is a voter only and can reach neither `reconcile` nor `resolve_all`."""
    pdf, case, answers = disputed
    answers += ["3", "pymupdf"]
    manifest = _run_refs(pdf, case)
    assert manifest.numbering_choice == "pymupdf"
    assert manifest.numbering_chosen_by == "user"
    assert manifest.numbering_verified is False
    # the entries carried forward are that reading's
    assert [e.doi for e in manifest.entries] == ["10.1000/alpha", "10.1000/gamma"]


def test_aborting_writes_no_manifest(disputed):
    """Option 4 leaves the case as it was. A half-written manifest describing a
    numbering the user walked away from is worse than none."""
    pdf, case, answers = disputed
    answers.append("4")
    with pytest.raises(typer.Exit):
        cli._refs_pipeline(manuscript=pdf, case=case, provided=None,
                           email="t@example.org", parse_only=False, backend="pymupdf",
                           doi=None, supplement=None)
    assert not (case / "refs_manifest.json").exists()


def test_a_non_interactive_session_withholds_the_disputed_labels_without_asking(
    disputed, monkeypatch
):
    """The user's decision: suppress only the disputed labels.

    Not the whole audit — that throws away a useful audit over a numbering the
    reader can check by hand — and not a reading picked on their behalf, which
    would record a choice nobody made.
    """
    pdf, case, answers = disputed
    monkeypatch.setattr(cli, "_interactive", lambda: False)

    def boom(*a, **k):
        raise AssertionError("a non-interactive run must not prompt")

    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(boom))
    manifest = _run_refs(pdf, case)

    assert manifest.numbering_choice == "withheld"
    assert manifest.numbering_chosen_by == "default"
    assert manifest.labels_disputed == ["2"]
    # the agreed label keeps its verdicts — it is the disputed one that is dropped
    assert "1" not in manifest.labels_disputed


def test_a_closed_stdin_is_not_interactive(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert cli._interactive() is False


def test_ci_is_not_interactive_even_on_a_tty(monkeypatch):
    """CI runs on a tty often enough that the tty test alone is not the gate, and
    a prompt in CI is a hung build, not a question."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("CI", "true")
    assert cli._interactive() is False
```

Append to `tests/test_reflist.py` (created by Task 5, so its imports and
`_ask` monkeypatch fixture already exist):

```python
# --- the resolution call: three rules on the reply -------------------------

_A = "6. Fujita S, Mori S. Characterization of Brain Volume Changes. 2023. doi:10.1001/jamanetworkopen.2023.18153\n"
_B = "6. Wachinger C. Something else entirely. 2019. doi:10.1000/wach\n"


def _disputed_pair():
    from papertrace.models import RefEntry

    return {
        "parsed": [RefEntry(num="6", raw=_A.strip(), doi="10.1001/jamanetworkopen.2023.18153")],
        "pymupdf": [RefEntry(num="6", raw=_B.strip(), doi="10.1000/wach")],
    }


def test_a_resolved_label_names_a_title_printed_in_one_of_the_readings(monkeypatch):
    """The whole permission structure of this call. Nothing invented may name a
    paper, so the title comes back only if it can be grepped out of the text the
    model was shown."""
    reply = json.dumps([{
        "num": "6",
        "title": "Characterization of Brain Volume Changes",
        "doi": "10.1001/jamanetworkopen.2023.18153",
        "year": "2023",
    }])
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: reply)
    res = reflist.resolve_disputed(["6"], _disputed_pair(), (_A, _B))
    assert list(res.resolved) == ["6"]
    assert res.still_disputed == []
    assert res.resolved["6"].doi == "10.1001/jamanetworkopen.2023.18153"


def test_a_title_printed_in_neither_reading_leaves_the_label_disputed(monkeypatch):
    """An unverifiable *field* is discarded; an unverifiable *title* is the label
    staying disputed. The title is the only field that identifies a paper, so a
    reply whose title cannot be found has answered nothing."""
    reply = json.dumps([{"num": "6", "title": "A Paper Nobody Printed", "year": "2023"}])
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: reply)
    res = reflist.resolve_disputed(["6"], _disputed_pair(), (_A, _B))
    assert res.resolved == {}
    assert res.still_disputed == ["6"]


def test_an_unverifiable_doi_is_discarded_without_costing_the_label(monkeypatch):
    """A truncated DOI is extraction damage, not a different paper. The field
    goes; the resolution stands and the entry carries a recorded gap."""
    reply = json.dumps([{
        "num": "6",
        "title": "Characterization of Brain Volume Changes",
        "doi": "10.1038/s41591-",
    }])
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: reply)
    res = reflist.resolve_disputed(["6"], _disputed_pair(), (_A, _B))
    assert res.resolved["6"].doi is None
    assert "doi" in res.provenance.fields_discarded


def test_cannot_tell_is_an_answer_and_keeps_the_label_disputed(monkeypatch):
    """Abstention is first-class. A call that must always answer will
    confabulate on exactly the labels two readings disagree about."""
    reply = json.dumps([{"num": "6", "cannot_tell": True}])
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: reply)
    res = reflist.resolve_disputed(["6"], _disputed_pair(), (_A, _B))
    assert res.resolved == {}
    assert res.still_disputed == ["6"]


def test_partial_resolution_is_representable(monkeypatch):
    """11 of 14 resolved and 3 still disputed is a real outcome, not an error.
    No code path may require all-or-nothing."""
    pair = _disputed_pair()
    pair["parsed"].append(_entry_for("7", "Weston AD. Automated abdominal segmentation. 2019."))
    pair["pymupdf"].append(_entry_for("7", "Someone Else. A different paper. 2015."))
    text_a = _A + "7. Weston AD. Automated abdominal segmentation. 2019.\n"
    text_b = _B + "7. Someone Else. A different paper. 2015.\n"
    reply = json.dumps([
        {"num": "6", "title": "Characterization of Brain Volume Changes"},
        {"num": "7", "cannot_tell": True},
    ])
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: reply)
    res = reflist.resolve_disputed(["6", "7"], pair, (text_a, text_b))
    assert list(res.resolved) == ["6"]
    assert res.still_disputed == ["7"]


def test_a_label_the_reply_never_mentions_stays_disputed(monkeypatch):
    """Silence is not consent. A reply that answers 1 of 2 questions has
    answered 1, and the unanswered label is not repaired into agreement."""
    reply = json.dumps([{"num": "6", "title": "Characterization of Brain Volume Changes"}])
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: reply)
    res = reflist.resolve_disputed(["6", "9"], _disputed_pair(), (_A, _B))
    assert res.still_disputed == ["9"]


def test_a_malformed_reply_resolves_nothing_rather_than_raising(monkeypatch):
    """`refs` has already done useful work by this point. A model that returns
    prose leaves every label disputed — the state the run was in before it
    asked — and says so in the provenance."""
    monkeypatch.setattr(reflist, "_ask", lambda prompt, model=None: "I think maybe [6]?")
    res = reflist.resolve_disputed(["6"], _disputed_pair(), (_A, _B))
    assert res.resolved == {}
    assert res.still_disputed == ["6"]
    assert res.provenance.discarded_whole
```

`_entry_for` is a two-line helper Task 5's module already needs; if it is not
there, add it beside the other helpers:

```python
def _entry_for(num: str, raw: str):
    from papertrace.models import RefEntry

    return RefEntry(num=num, raw=raw)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/anaconda3/bin/python -m pytest tests/test_escalation.py -k escalat -q
~/anaconda3/bin/python -m pytest tests/test_reflist.py -k resolve -q
```

Expected: `AttributeError: module 'papertrace.cli' has no attribute
'_interactive'` and `AttributeError: module 'papertrace.reflist' has no
attribute 'resolve_disputed'`. Paste both.

- [ ] **Step 3: `_interactive()` and the disagreement file, in `cli.py`**

`cli.py` already imports `os`, `re`, `sys` and `from rich.prompt import Prompt`
— no new imports. Add below `_body_citation_labels`:

```python
def _interactive() -> bool:
    """May this run stop and ask a person something?

    Both streams, and not under CI. `stdin.isatty()` alone is not the gate: CI
    runners frequently allocate a tty, and a prompt in CI is a hung build rather
    than a question. `stdout` is checked too because a run whose output is being
    piped into a file has a reader who is not watching.
    """
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("CI")


_DISAGREEMENT_NAME = "reference_disagreement.md"
# How much of the printed list to quote around a disputed label. Enough to show
# the entries either side of it — a merge or a split is only visible against its
# neighbours, and the neighbours are what say which reading dropped a reference.
_SPAN_CHARS = 600


def _label_span(text: str, label: str) -> str:
    """The printed text around this label, or "" when the numeral is not printed.

    A window, not a parse. This file exists so a reader can check the parse, so
    a second parse deciding what to show them would fail in the same place and
    hide the same reference. `""` where the numeral was never printed is the
    honest answer, and that absence is often the disagreement itself.
    """
    m = re.search(rf"(?:(?<=\n)|\A)\s*\[?{re.escape(label)}[\].:]?\s", text)
    if m is None:
        return ""
    return text[m.start() : m.start() + _SPAN_CHARS].strip()


def _write_disagreement(
    case: Path,
    labels: list[str],
    candidates: dict[str, list[RefEntry]],
    texts: dict[str, str],
) -> Path:
    """Every reading of every disputed label, with the text each was read from.

    Written before any question is asked. The structured fields alone are not
    enough to settle a disagreement — they are what disagreed — so the verbatim
    source span travels with them, and a reader who wants to answer the question
    themselves can, without opening the PDF.
    """
    out = case / "out"
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Reference numbering — where the readings disagree",
        "",
        "One section per citation label whose readings of the bibliography name "
        "different papers. Each reading's structured fields sit above the verbatim "
        "text it was read from, so the disagreement can be settled by eye against "
        "the printed list.",
        "",
        f"Readings compared: {', '.join(sorted(candidates))}.",
        "",
        "Verdicts on claims citing these labels are withheld — reported "
        "`unchecked`, never guessed — unless a reading is adopted for them.",
        "",
    ]
    for label in labels:
        lines += [f"## [{label}]", ""]
        for name in sorted(candidates):
            hits = [e for e in candidates[name] if e.num == label]
            lines += [f"### {name}", ""]
            if not hits:
                lines += ["Carries no entry for this label.", ""]
                continue
            # more than one hit IS the finding on a reading that carried the
            # label twice — which of the two it means is precisely the question,
            # so both are printed rather than the first
            for e in hits:
                lines += [
                    f"- doi: `{e.doi or '—'}`",
                    f"- year: `{e.year or '—'}`",
                    f"- slug: `{e.slug or '—'}`",
                    f"- refused to number itself: `{e.boundary_ambiguous}`",
                    "",
                    "Read from:",
                    "",
                    "```",
                    e.raw,
                    "```",
                    "",
                ]
        for name in sorted(texts):
            if span := _label_span(texts[name], label):
                lines += [f"### {name} — the printed list around it", "",
                          "```", span, "```", ""]
    path = out / _DISAGREEMENT_NAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
```

`RefEntry` is already imported at `cli.py:23` via `from .models import
ClaimResult, RefManifest, RunResults, manuscript_fingerprint` — it is **not**.
Add `RefEntry` to that import; it is needed for the annotation above.

- [ ] **Step 4: `RESOLVE_PROMPT`, `Resolution` and `resolve_disputed` in `reflist.py`**

```python
RESOLVE_PROMPT = """You are shown two readings of one printed bibliography, and a list of
citation labels the two readings disagree about — they name different papers at
that label.

For each label, decide which paper the label actually names, using ONLY the two
texts below. Reply with a JSON array, one object per label you can answer:

[{"num": "6",
  "title": "the paper's title, copied character for character from one of the texts",
  "authors": "copied character for character, or omit",
  "year": "copied character for character, or omit",
  "journal": "copied character for character, or omit",
  "doi": "copied character for character, or omit",
  "seen_in": "A" or "B"}]

If you cannot tell which paper a label names, reply for it with
{"num": "6", "cannot_tell": true}. THAT IS A CORRECT ANSWER and is preferred
over a guess: these are exactly the labels where two readings contradict each
other, so a guess here is the error this tool exists to prevent. Answering some
labels and abstaining on others is expected.

Every value you give is checked against the two texts verbatim. A value that is
not printed in one of them is discarded, and a title that is not printed leaves
the label unresolved — so do not correct, expand, complete or tidy anything.

LABELS IN DISPUTE: <<LABELS>>

--- READING A ---
<<A>>

--- READING B ---
<<B>>
"""


@dataclass
class Resolution:
    """What the resolution call settled, what it did not, and on whose words.

    `still_disputed` is not the complement of `resolved` by arithmetic — it is
    built by listing what came back unanswered, abstained on, or unverifiable.
    Deriving it as `set(asked) - set(resolved)` gives the same answer today and
    would silently start counting a dropped label as resolved the first time a
    field-verification branch forgets to record one.
    """

    resolved: dict[str, RefEntry] = field(default_factory=dict)
    still_disputed: list[str] = field(default_factory=list)
    provenance: ReflistProvenance = field(default_factory=ReflistProvenance)


def resolve_disputed(
    labels: list[str],
    entries: dict[str, list[RefEntry]],
    texts: tuple[str, ...],
    *,
    model: str | None = None,
) -> Resolution:
    """Ask the model which paper each disputed label names, and believe none of it on its word.

    Three rules, in this order, and the second is the one that keeps this call
    honest: **abstention is a first-class answer.** `cannot tell` keeps the
    label disputed and withheld. A call that must always answer will confabulate
    on exactly the labels two readings disagree about — that is the population,
    by construction, and it is why this returns a `Resolution` with two lists
    rather than a dict of answers.

    1. Every field is verified verbatim against the texts through `_found`
       (Layer E for everything, Layer O for authors and titles) and, for a DOI,
       `_usable_doi`. An unverifiable field is discarded; an unverifiable title
       leaves the label disputed, because the title is the only field that
       identifies a paper.
    2. `cannot_tell`, a label the reply never mentions, and a reply that is not
       JSON at all all leave their labels disputed.
    3. Partial resolution is the normal outcome, not a degraded one. Nothing
       downstream may require all-or-nothing.
    """
    prov = ReflistProvenance(readings=list(texts))
    prompt = (
        RESOLVE_PROMPT.replace("<<LABELS>>", ", ".join(f"[{n}]" for n in labels))
        .replace("<<A>>", texts[0] if texts else "")
        .replace("<<B>>", texts[1] if len(texts) > 1 else "")
    )
    with for_site(SITE_REFS):
        raw = _ask(prompt, model)
    prov.model = model_for(SITE_REFS) or ""

    items = _parse_array(raw)
    if items is None:
        # A model that returned prose has answered nothing. Every label stays in
        # the state the run was already in, which is the safe one, and the
        # reason is recorded rather than the failure being indistinguishable
        # from an all-abstention reply.
        prov.discarded_whole = "the reply was not a JSON array, so no label was resolved"
        return Resolution(still_disputed=list(labels), provenance=prov)

    by_num = {str(o.get("num", "")).strip(): o for o in items if isinstance(o, dict)}
    prov.entries_proposed = len(by_num)
    resolved: dict[str, RefEntry] = {}
    unresolved: list[str] = []
    for label in labels:
        obj = by_num.get(label)
        if obj is None or obj.get("cannot_tell"):
            unresolved.append(label)
            continue
        title = str(obj.get("title") or "")
        if not _found("title", title, texts):
            # not a discarded field — a discarded answer. Nothing else in the
            # object can name a paper on its own.
            prov.fields_discarded.append(f"[{label}].title")
            unresolved.append(label)
            continue
        doi = _usable_doi(str(obj.get("doi") or ""))
        if doi is not None and not _found("doi", doi, texts):
            doi = None
        if obj.get("doi") and doi is None:
            prov.fields_discarded.append("doi")
        year = str(obj.get("year") or "")
        if not _found("year", year, texts):
            if year:
                prov.fields_discarded.append("year")
            year = ""
        e = RefEntry(num=label, raw=title, doi=doi, title=title, year=year or None)
        e.seen_in = [str(obj.get("seen_in") or "")] if obj.get("seen_in") else []
        resolved[label] = e
    return Resolution(resolved=resolved, still_disputed=unresolved, provenance=prov)
```

`_parse_array` is `reflist.py`'s existing lenient JSON reader from Task 5
(`None` on anything that is not a list of objects). If Task 5 named it something
else, use that name — do not add a second parser.

- [ ] **Step 5: the escalation itself, in `cli.py`**

```python
_READING_LABEL = {"parsed": "the run backend's parse", "pymupdf": "the flat-text parse"}


def _escalate_disputed(
    case: Path,
    rec,
    entries: list[RefEntry],
    candidates: dict[str, list[RefEntry]],
    texts: dict[str, str],
    model: str | None = None,
) -> list[RefEntry]:
    """Show the disagreement, then offer to resolve it. Mutates `rec`, returns the list to use.

    Step 1 is unskippable and happens before the menu: nobody is asked to choose
    blind. Nothing here may set `rec.verified` — the honest record is *which*
    choice was made and *by whom*. `chosen_by: "user"` is the whole point: a
    person consenting to proceed is an input, not evidence. This codebase
    already ranks that signal — "a filename carries the user's assertion,
    content carries none."
    """
    labels = list(rec.labels_disputed)
    n = len(labels)
    if not n:
        return entries

    if not _interactive():
        # Suppress the disputed labels and nothing else. Not the whole audit —
        # that throws away a useful audit over a numbering the reader can check
        # by hand — and not a reading picked on the user's behalf, which would
        # record a choice nobody made.
        rec.choice, rec.chosen_by = "withheld", "default"
        console.print(
            f"[yellow]⚠ {n} citation label{'' if n == 1 else 's'} in dispute[/yellow] — "
            f"{_label_list(labels)}. Not a tty, so nothing was asked: verdicts on claims "
            "citing them are withheld and reported unchecked. Re-run in a terminal to "
            "settle them."
        )
        return entries

    path = _write_disagreement(case, labels, candidates, texts)
    console.print(
        f"\n[yellow]⚠ {n} citation label{'' if n == 1 else 's'} in dispute[/yellow] — "
        f"{_label_list(labels)}"
    )
    for label in labels:
        for name in sorted(candidates):
            hits = [e for e in candidates[name] if e.num == label]
            shown = hits[0].raw[:70] if hits else "— no entry —"
            console.print(f"  [{label:>3}] [cyan]{name:<8}[/cyan] {shown}")
    console.print(f"  [dim]full disagreement, with the text each reading came from:[/dim] {path}")

    offered = [r for r in ("parsed", "pymupdf") if r in candidates]
    console.print(
        f"\n1. Ask the model to resolve the {n} disputed label{'' if n == 1 else 's'} "
        "[dim][default][/dim]\n"
        f"2. Withhold verdicts on all {n}\n"
        f"3. Use one reading whole: {' / '.join(offered)}\n"
        "4. Abort"
    )
    answer = Prompt.ask("  choice", choices=["1", "2", "3", "4"], default="1")

    if answer == "4":
        # nothing written: a manifest describing a numbering the user walked
        # away from is worse than no manifest, because every later stage trusts it
        console.print("[dim]aborted — nothing was written.[/dim]")
        raise typer.Exit(1)

    if answer == "2":
        rec.choice, rec.chosen_by = "withheld", "user"
        return entries

    if answer == "3":
        pick = Prompt.ask("  which reading", choices=offered, default=offered[0])
        rec.choice, rec.chosen_by = pick, "user"
        rec.labels_resolved = sorted(labels, key=int)
        rec.labels_disputed = []
        rec.note += (
            f"; you chose {_READING_LABEL.get(pick, pick)} for the {n} label"
            f"{'' if n == 1 else 's'} in dispute. That is your reading, not a check of it "
            "— the numbering is still unconfirmed"
        )
        return list(candidates[pick])

    from .reflist import resolve_disputed

    res = resolve_disputed(labels, candidates, tuple(texts[k] for k in sorted(texts)),
                           model=model)
    rec.choice, rec.chosen_by = "llm_resolved", "user"
    rec.labels_resolved = sorted(res.resolved, key=int)
    rec.labels_disputed = sorted(res.still_disputed, key=int)
    done, left = len(rec.labels_resolved), len(rec.labels_disputed)
    console.print(
        f"  [green]{done} resolved[/green]"
        + (f" · [yellow]{left} still disputed[/yellow]" if left else "")
    )
    if left:
        # partial resolution is the normal outcome, and the abstentions are named
        # because they are the labels a reader should check by hand
        console.print(
            f"  [dim]{_label_list(rec.labels_disputed)} — the model could not tell which "
            "paper these name, so their verdicts stay withheld.[/dim]"
        )
    rec.note += (
        f"; {done} of the {n} disputed label{'' if n == 1 else 's'} were resolved by a "
        "model reading of both texts, with every field verified verbatim, and you "
        "accepted that. It is a reading, not a confirmation — the numbering is still "
        "unconfirmed"
    )
    # The resolved entry REPLACES the one in the chosen list, in place, same
    # order, same length. Returning `entries` unchanged here — which an earlier
    # draft of this plan did — takes the label out of `labels_disputed` so
    # `check` judges it again, while leaving the entry the resolution just ruled
    # AGAINST as the paper it judges against. That is the original wrong-paper
    # bug reached through the one path a user consented to, and the report would
    # say "you accepted that" over it.
    #
    # This is the ONLY place in the codebase where a model reading changes what
    # gets resolved and judged, and it is legal only because
    # `rec.chosen_by == "user"` is recorded beside it. `rec.verified` stays
    # False regardless; Task 8 proves that over every reply shape and choice.
    return [res.resolved.get(e.num, e) for e in entries]
```

`_label_list` lives in `refs.py`; add it to the `from .refs import (…)` block
inside `_refs_pipeline` rather than reimplementing the bracket join.

**A test this step must carry**, because nothing else in the plan would notice:

```python
def test_a_resolved_label_judges_against_the_resolved_entry(tmp_path, monkeypatch):
    """Un-disputing a label without substituting its entry is the original bug.

    Menu option 1 takes a label out of `labels_disputed`, so `check` judges it
    again. If the chosen list still holds the entry the resolution ruled
    against, the verdict is printed against the very paper the model said was
    wrong — and the report says the user accepted it. The resolved entries exist
    in `Resolution.resolved`; the failure mode is computing them and throwing
    them away.
    """
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", staticmethod(lambda *a, **k: "1"))
    resolved = _entry("13", "Weston AD (2019) The paper the label really names. "
                            "https://doi.org/10.1148/x13")
    monkeypatch.setattr(
        reflist_mod, "resolve_disputed",
        lambda labels, cands, texts, model=None: reflist_mod.Resolution(
            resolved={"13": resolved}, still_disputed=[],
            provenance=reflist_mod.ReflistProvenance(model="claude-opus-5"),
        ),
    )

    rec = _rec(labels_disputed=["13"])
    wrong = _entry("13", "Somebody Else (1999) A different paper entirely. "
                         "https://doi.org/10.1148/x99")
    out = cli._escalate_disputed(
        tmp_path, rec, [wrong], {"parsed": [wrong]}, {"parsed": "text"},
    )

    assert rec.labels_disputed == []          # the label will be judged again
    assert out[0].doi == "10.1148/x13"        # against the RESOLVED paper
    assert rec.chosen_by == "user"            # which is what authorises it
    assert rec.verified is False              # and it is still not confirmed
```

Wire it in at `cli.py:659`, immediately after the `if parse_only: … return`
block:

```python
    # after --parse-only returns: the escalation can spend a model call, and
    # --parse-only promises no network
    entries = _escalate_disputed(case, rec, entries, candidates, reading_texts, model=None)
```

`candidates` and `reading_texts` are what `_reference_readings` returned in Task
6. Then extend the `RefManifest(...)` call at `cli.py:702`:

```python
        numbering_choice=rec.choice,
        numbering_chosen_by=rec.chosen_by,
        labels_resolved=rec.labels_resolved,
        labels_disputed=rec.labels_disputed,
```

**Do not add `numbering_verified=` a second time or change the existing one.**

- [ ] **Step 6: the three remaining `claim_pairing` texts**

In `disclosures.py`, Task 4's `_claim_pairing` has one branch. Replace its body
with four, keeping `CLAIM_PAIRING_TOKEN` and the `key="claim_pairing"` exactly
as Task 4 shipped them:

```python
def _claim_pairing(claim, manifest) -> Disclosure | None:
    """What happened to the labels this claim cites, where the readings differed.

    Four states, four texts. `corroborated` is the only `info` one, and it fires
    only on a claim that already carries a warn-level disclosure: on a clean
    claim it is noise, and a reader who is shown a reassurance about every
    verdict stops reading all of them.
    """
    cited = set(claim.refs)
    withheld = sorted(cited & set(claim.withheld_refs), key=int)
    disputed = sorted(cited & set(manifest.labels_disputed), key=int)
    resolved = sorted(cited & set(manifest.labels_resolved), key=int)

    if withheld or disputed:
        labels = _label_group(withheld or disputed)
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_TOKEN,
            text=(
                f"{CLAIM_PAIRING_TOKEN}: {labels}. Two readings of this paper's "
                "bibliography name different papers at that label, so nothing here can "
                "say which paper the claim cites. The source was not judged — a verdict "
                "against a possibly different paper is the error this withholding exists "
                "to prevent. Check the reference by hand."
            ),
            short=f"{CLAIM_PAIRING_TOKEN}: {labels}",
        )

    if resolved and manifest.numbering_choice == "llm_resolved":
        labels = _label_group(resolved)
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_TOKEN,
            text=(
                f"{CLAIM_PAIRING_TOKEN}: {labels}. Two readings of the bibliography "
                "disagreed there. A model was shown both, every field of its answer was "
                "found verbatim in one of them, and you accepted the result — so this "
                "verdict rests on a reading nobody checked against the printed page, "
                "chosen by you. The numbering is still recorded as unconfirmed."
            ),
            short=f"{CLAIM_PAIRING_TOKEN}: {labels} — resolved by model, accepted by you",
        )

    if resolved and manifest.numbering_choice in ("parsed", "pymupdf"):
        labels = _label_group(resolved)
        return Disclosure(
            key="claim_pairing",
            level="warn",
            token=CLAIM_PAIRING_TOKEN,
            text=(
                f"{CLAIM_PAIRING_TOKEN}: {labels}. Two readings of the bibliography "
                f"disagreed there and you chose the `{manifest.numbering_choice}` reading "
                "for all of them. That is your assertion about which reading is right, "
                "not a check of it, and this verdict is about whichever paper that "
                "reading names."
            ),
            short=f"{CLAIM_PAIRING_TOKEN}: {labels} — you chose the "
                  f"{manifest.numbering_choice} reading",
        )

    # `info`, and gated by the caller on this claim already carrying a warning:
    # corroboration is reassurance, and reassurance on a clean claim is noise
    if manifest.numbering_corroborated and cited:
        readings = ", ".join(manifest.corroborating_readings)
        return Disclosure(
            key="claim_pairing",
            level="info",
            token=CLAIM_PAIRING_TOKEN,
            text=(
                f"{CLAIM_PAIRING_TOKEN}: {_label_group(sorted(cited, key=int))} was read "
                f"the same way by {readings}. That is evidence for this pairing, not "
                "confirmation of it — every reading read the same document, so a "
                "reference the layout destroyed is one they may all have missed."
            ),
            short=f"{CLAIM_PAIRING_TOKEN}: agreed by {readings}",
        )
    return None
```

`_label_group` is `disclosures.py`'s existing `[4], [5]` join (the one
`_claim_numbering` builds inline at `disclosures.py:518`); factor that one
expression out into `_label_group` and call it from both, rather than a third
copy of `f"[{'], ['.join(x)}]"`.

Then, in `claim_disclosures()` (`disclosures.py:849`), the info gate:

```python
    # `claim_pairing` at info level is corroboration, which is reassurance — and
    # reassurance on a claim with nothing else to say about it is noise on every
    # row of the report. A warn-level pairing disclosure is unconditional.
    if manifest is not None and (d := _claim_pairing(claim, manifest)) is not None:
        if d.level == "warn" or any(x.level == "warn" for x in out):
            out.append(d)
```

Place it before the `anchor_disclosure` append, so the anchor state stays last —
`report_editor.html.j2:186` reads it out of the caption position.

- [ ] **Step 7: Schema — Gate 2**

Add to `schemas/refs_manifest.schema.json` `properties` (additive, nothing into
`required`, which stays `["manuscript", "entries"]`):

```json
"numbering_choice": {
  "type": "string",
  "enum": ["", "withheld", "llm_resolved", "parsed", "pymupdf"],
  "description": "What was done about the citation labels whose readings of the bibliography named different papers. `withheld` — their verdicts were dropped; `llm_resolved` — a model was shown both readings and its verbatim-verified answer was accepted; `parsed` / `pymupdf` — one reading was adopted whole. Empty string means no label was in dispute, or the run predates this field. It is NEVER a claim that the numbering was checked: `numbering_verified` is the only field that says that, and no value here can set it."
},
"numbering_chosen_by": {
  "type": "string",
  "enum": ["", "default", "user"],
  "description": "`user` — a person was shown the full disagreement (case/out/reference_disagreement.md) and answered a prompt; `default` — the run was not interactive, so the disputed labels were withheld without asking. A person consenting to proceed is an input, not evidence, which is why this is recorded separately from the choice itself. Absent or empty means nothing was chosen because nothing was in dispute."
},
"labels_resolved": {
  "type": "array",
  "items": {"type": "string"},
  "description": "Citation labels that were in dispute and are no longer, with `numbering_choice` saying on whose reading. Verdicts on claims citing them are printed, carrying a per-claim disclosure naming how the pairing was settled. Absent means never computed, not that none was resolved — the same three-state discipline as `numbering_ledger`. Partial resolution is normal: this and `labels_disputed` are both non-empty on a run where some labels were settled and some were not."
}
```

`labels_disputed`, `numbering_corroborated` and `corroborating_readings` were
added by Task 3 — do not add them again; verify with
`python -c "import json;print(list(json.load(open('schemas/refs_manifest.schema.json'))['properties']))"`.

Round-trip test, into `tests/test_refs.py` beside Task 3's:

```python
def test_a_partial_resolution_round_trips_through_the_manifest(tmp_path):
    """11 resolved and 3 still disputed is a representable state, and the wire
    format is the dataclass — so this is the test that says the two lists can
    both be non-empty at once."""
    from papertrace.models import RefManifest

    m = RefManifest(manuscript="p.pdf", labels_resolved=["6", "7"], labels_disputed=["9"],
                    numbering_choice="llm_resolved", numbering_chosen_by="user")
    path = tmp_path / "refs_manifest.json"
    m.to_json(path)
    back = RefManifest.from_json(path)
    assert back.labels_resolved == ["6", "7"]
    assert back.labels_disputed == ["9"]
    assert back.numbering_choice == "llm_resolved"
    assert back.numbering_chosen_by == "user"
    assert back.numbering_verified is False
```

- [ ] **Step 8: Run everything**

```bash
~/anaconda3/bin/python -m pytest tests/test_escalation.py tests/test_reflist.py \
    tests/test_refs.py tests/test_disclosure_parity.py -q
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

Paste the real output. Also validate the schema loads and the round trip
validates against it if the repo's schema test does that by loop — check
`tests/test_supplements.py` before adding a second validator.

- [ ] **Step 9: Gates**

```bash
~/anaconda3/bin/python -m pytest tests/test_check_provenance.py tests/test_supplements.py \
    tests/test_coverage.py tests/test_reference_reconciliation.py -q
```

- **Gate 4** (touched `refs.py`'s callers and `reflist.py`): the honest-failure
  paths are `test_a_malformed_reply_resolves_nothing_rather_than_raising`,
  `test_cannot_tell_is_an_answer_and_keeps_the_label_disputed` and
  `test_a_non_interactive_session_withholds_the_disputed_labels_without_asking`.
  Paste all three.
- **Gate 5 does not apply** — no ingest, highlight or report *code* changed.
  `disclosures.py` is a producer, not a renderer, and no template was touched.
  Say this in the report rather than skipping it silently.
- **Gate 2** is Step 7.

- [ ] **Step 10: Commit**

```bash
git add src/papertrace/cli.py src/papertrace/reflist.py src/papertrace/disclosures.py \
        schemas/refs_manifest.schema.json tests/test_escalation.py tests/test_reflist.py \
        tests/test_refs.py
git commit -m "refs: show the disagreement, then offer to resolve it

$(cat <<'BODY'
A label whose two readings name different papers had one outcome: withheld.
That is the right default and the wrong only option — a human with the printed
list can settle most of them.

So the run writes case/out/reference_disagreement.md first, with every reading's
fields and the verbatim text each came from, prints the path, and only then asks.
Step 1 is unskippable; the test asserts the file exists when the prompt is
reached, because that ordering is the whole difference between informed consent
and a menu.

Abstention is a first-class answer from the resolution call: "cannot tell" keeps
a label disputed. A call that must always answer confabulates on exactly the
labels two readings disagree about. Partial resolution is normal and
representable — labels_resolved and labels_disputed are both non-empty on a real
run.

Nothing here sets numbering_verified. What is recorded is which choice was made
and by whom: chosen_by "user" is an input, not evidence.

A non-interactive run withholds only the disputed labels and says so.
BODY
)"
```

---

### Task 8: The invariant test

**Why:** Most of this design's safety lives in one flag staying `False`. Six
modules can now reach `Reconciliation`, two of them carry model output, and one
of them asks a human a question — and the reason a wrong reference numbering
produced six confident verdicts about papers the manuscript never cited is that
a boolean said the numbering had been checked. `numbering_verified` means one
thing: the manuscript's own `[N]` markers accounted for exactly the labels a
candidate reading carried. Nothing a model said and nothing a person answered at
a prompt is that.

This task adds **no behaviour**. It is the test that makes the plan's central
constraint mechanical instead of conventional, plus a companion proving the two
axes are independent (a second axis that cannot be set without the first is not
a second axis), plus a grep guard over `src/`.

**Files:**
- Create: `tests/test_numbering_invariant.py`
- Modify: nothing in `src/`. **If this task finds itself editing a source file,
  it has found a real defect** — report it as a finding and fix it in its own
  commit, do not fold it into this one.

**Interfaces:** consumes only. `refs.reconcile`, `refs.label_agreement`,
`reflist.propose`, `reflist.resolve_disputed`, `cli._escalate_disputed`,
`models.RefManifest`.

**A fourth new test module.** The plan's File Structure names three
(`test_ask.py`, `test_label_agreement.py`, `test_reflist.py`). This is a fourth,
deliberately: the invariant is the plan's centrepiece and burying it at the end
of the 1,200-line `tests/test_reference_reconciliation.py` makes it findable only
by someone who already knows it exists.

- [ ] **Step 1: Write the module**

```python
"""One flag staying False, across every reply shape and every interactive choice.

`numbering_verified` means exactly one thing: the manuscript's own `[N]` markers
accounted for exactly the labels a candidate reading carried. A model reading of
the bibliography is not that, and neither is a person answering a prompt — so
this module parametrises over every shape a model reply can take and every
choice the escalation offers, and asserts the flag is False in every cell.

The companion test asserts `numbering_corroborated` CAN be True in the same run.
The two are independent axes on purpose; a second axis that moves only when the
first does is not a second axis, and the false alarm corroboration exists to
silence would still be firing.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from papertrace import cli as cli_mod  # noqa: E402
from papertrace import reflist as reflist_mod  # noqa: E402
from papertrace.models import RefEntry, RefManifest  # noqa: E402
from papertrace.refs import label_agreement, reconcile  # noqa: E402

# Reading A and reading B of one bibliography, as text. Every fixture DOI has a
# 4-9 digit registrant: `10.1/a` does not match `DOI_RE` and a plan already lost
# a round to that.
_TEXT_A = (
    "1. Alpha A. First paper on registration. 2020. doi:10.1000/alpha\n"
    "2. Beta B. Second paper on segmentation. 2021. doi:10.1000/beta\n"
)
_TEXT_B = (
    "1. Alpha A. First paper on registration. 2020. doi:10.1000/alpha\n"
    "2. Gamma G. A wholly different paper. 2019. doi:10.0002/b\n"
)


def _reading(pairs: list[tuple[str, str, str | None]]) -> list[RefEntry]:
    return [RefEntry(num=n, raw=raw, doi=doi) for n, raw, doi in pairs]


def _parsed() -> list[RefEntry]:
    return _reading([
        ("1", "Alpha A. First paper on registration. 2020.", "10.1000/alpha"),
        ("2", "Beta B. Second paper on segmentation. 2021.", "10.1000/beta"),
    ])


def _flat() -> list[RefEntry]:
    return _reading([
        ("1", "Alpha A. First paper on registration. 2020.", "10.1000/alpha"),
        ("2", "Gamma G. A wholly different paper. 2019.", "10.0002/b"),
    ])


# Every shape a reply can take, named by what is wrong with it. `agrees` is in
# here deliberately: the dangerous case is not the malformed reply — that fails
# loudly — it is the well-formed one that confirms the parse, because that is
# the reply a reader is most tempted to promote to a check.
REPLIES = {
    "agrees_with_the_parse": json.dumps([
        {"num": "1", "title": "First paper on registration", "doi": "10.1000/alpha"},
        {"num": "2", "title": "Second paper on segmentation", "doi": "10.1000/beta"},
    ]),
    "agrees_with_nothing": json.dumps([
        {"num": "1", "title": "A title printed in neither reading"},
        {"num": "2", "title": "Nor is this one"},
    ]),
    "abstains": json.dumps([{"num": "1", "cannot_tell": True},
                            {"num": "2", "cannot_tell": True}]),
    "malformed_json": "here is what I think: [1] is Alpha, probably",
    "empty_array": "[]",
    "title_printed_nowhere": json.dumps([
        {"num": "2", "title": "Characterization of Brain Volume Changes",
         "doi": "10.1000/beta"},
    ]),
    "truncated_doi": json.dumps([
        {"num": "2", "title": "Second paper on segmentation", "doi": "10.1038/s41591-"},
    ]),
    "a_label_nobody_cites": json.dumps([
        {"num": "99", "title": "Second paper on segmentation"},
    ]),
}


@pytest.mark.parametrize("shape", sorted(REPLIES))
def test_no_model_reply_shape_can_set_numbering_verified(shape, monkeypatch):
    """Parametrised over every model reply shape.

    Most of this design's safety lives in one flag staying False. The model's
    reading is a voter: it never becomes `reconcile`'s chosen list, so no reply
    can cause a source to be resolved, downloaded or judged — it can only cause
    a verdict to be withheld.
    """
    monkeypatch.setattr(reflist_mod, "_ask", lambda prompt, model=None: REPLIES[shape])
    proposed, prov = reflist_mod.propose(_TEXT_A, _TEXT_B, label_a="parsed", label_b="pymupdf")

    body = {"1", "2"}
    candidates = {"parsed": _parsed(), "pymupdf": _flat(), "llm": proposed}
    states = label_agreement(candidates, body)

    # the parse and the deposit are what reconcile arbitrates between; `llm` is
    # not passed, and a task that passes it has broken the design
    entries, rec = reconcile(body, None, _parsed())
    manifest = RefManifest(
        manuscript="p.pdf",
        entries=entries,
        numbering_verified=rec.verified,
        labels_disputed=[k for k, v in states.items() if v == "disputed"],
    )
    assert rec.verified is False, f"{shape} reached rec.verified"
    assert manifest.numbering_verified is False, f"{shape} reached the manifest"
    assert all(v in ("agreed", "single", "disputed", "absent") for v in states.values())


@pytest.mark.parametrize("choice", ["1", "2", "3", "4"])
def test_no_interactive_choice_can_set_numbering_verified(choice, monkeypatch, tmp_path):
    """Parametrised over all four menu choices, including abort.

    `chosen_by: "user"` is what makes the escalation honest — a person
    consenting to proceed is an input, not evidence. So every branch of the menu
    must leave `rec.verified` exactly where `reconcile` left it, and this is the
    test that says so for the branch a reviewer would be most tempted to
    "improve": option 3, where the user positively asserts which reading is
    right.
    """
    import typer

    candidates = {"parsed": _parsed(), "pymupdf": _flat()}
    texts = {"parsed": _TEXT_A, "pymupdf": _TEXT_B}
    _entries, rec = reconcile({"1", "2"}, None, _parsed())
    rec.labels_disputed = ["2"]
    rec.verified = False

    answers = iter([choice, "pymupdf"])
    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(cli_mod.Prompt, "ask", staticmethod(lambda *a, **k: next(answers)))
    monkeypatch.setattr(
        reflist_mod, "_ask",
        lambda prompt, model=None: REPLIES["agrees_with_the_parse"],
    )

    if choice == "4":
        with pytest.raises(typer.Exit):
            cli_mod._escalate_disputed(tmp_path, rec, _parsed(), candidates, texts)
    else:
        cli_mod._escalate_disputed(tmp_path, rec, _parsed(), candidates, texts)

    assert rec.verified is False, f"menu choice {choice} reached rec.verified"
    manifest = RefManifest(
        manuscript="p.pdf",
        numbering_verified=rec.verified,
        numbering_choice=rec.choice,
        numbering_chosen_by=rec.chosen_by,
        labels_resolved=rec.labels_resolved,
        labels_disputed=rec.labels_disputed,
    )
    assert manifest.numbering_verified is False
    if choice != "4":
        assert manifest.numbering_chosen_by == "user"


def test_corroboration_can_be_true_in_the_same_run_that_keeps_verified_false():
    """The two axes must be independently settable, or the second is pointless.

    This is the 22-vs-19 case the corroboration axis exists for: every cited
    label is read the same way by two readings, and `_covers` still fails
    because three of the references are cited only in the supplement. Before the
    second axis, that run could say nothing but "unconfirmed" — crying wolf on a
    numbering two independent readings agreed about entry for entry.
    """
    body = {"1", "2"}
    candidates = {"parsed": _parsed(), "pymupdf": _parsed()}
    states = label_agreement(candidates, body)
    assert set(states.values()) == {"agreed"}

    # a list longer than the highest cited label: `_covers` requires equality
    longer = _parsed() + [RefEntry(num="3", raw="Delta D. Cited only in the supplement. 2022.")]
    _entries, rec = reconcile(body, None, longer)
    assert rec.verified is False, "the extent check still fails — that is the premise"

    manifest = RefManifest(
        manuscript="p.pdf",
        numbering_verified=rec.verified,
        numbering_corroborated=all(v == "agreed" for v in states.values()),
        corroborating_readings=sorted(candidates),
    )
    assert manifest.numbering_verified is False
    assert manifest.numbering_corroborated is True
    assert manifest.corroborating_readings == ["parsed", "pymupdf"]


_SRC = Path(__file__).resolve().parent.parent / "src" / "papertrace"
# the two names that may only ever be set from the extent check
_GUARDED = ("numbering_verified", "rec.verified", ".verified")
# what must never appear on the right-hand side of one
_FORBIDDEN = ("reflist", "propose", "resolve_disputed", "Prompt.ask", "_ask(",
              "answer", "choice", "chosen_by", "res.resolved")


def test_no_source_file_assigns_numbering_verified_from_a_model_or_a_prompt():
    """A grep, and honest about being one.

    What this proves: no source line *textually* assigns one of the guarded
    names from an expression mentioning the reflist module, a prompt answer or
    the seam. That catches the change a future task is actually likely to make —
    `rec.verified = bool(res.resolved)`, written in good faith by someone who
    read "resolved" as "checked".

    What it cannot prove: that no value *derived* from a model reply reaches the
    flag through an intermediate variable, a helper or a dict. A grep has no
    dataflow. The two parametrised tests above are what cover that, by running
    the real code over every reply shape and every choice; this one is the cheap
    tripwire that fails at review time rather than at runtime, and it is worth
    having for exactly that reason and no more.
    """
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "=" not in code or "==" in code:
                continue
            lhs, _, rhs = code.partition("=")
            if not any(g in lhs for g in _GUARDED):
                continue
            if any(f in rhs for f in _FORBIDDEN):
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)


def test_the_guard_above_would_actually_catch_the_change_it_describes(tmp_path):
    """A guard test nobody has seen fail is a guard test nobody knows works.

    The grep is re-run over a file holding the exact line the real test forbids,
    so the assertion has been observed both ways.
    """
    bad = tmp_path / "papertrace" / "bad.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("rec.verified = bool(res.resolved)\n", encoding="utf-8")

    offenders = []
    for path in sorted(bad.parent.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if "=" not in code or "==" in code:
                continue
            lhs, _, rhs = code.partition("=")
            if any(g in lhs for g in _GUARDED) and any(f in rhs for f in _FORBIDDEN):
                offenders.append(f"{path.name}:{lineno}")
    assert offenders, "the grep would not catch the line it exists to catch"
```

**Note for the implementer.** The grep body is written twice on purpose — once
in the real test and once in the self-test. Factoring it into a helper the
self-test calls would make both tests pass if the helper were broken in the same
way, which is the failure mode a self-test exists to exclude. If you factor it
anyway, the self-test has to construct its offender by a different route.

- [ ] **Step 2: Run it and read the parametrisation**

```bash
~/anaconda3/bin/python -m pytest tests/test_numbering_invariant.py -v
```

Expected: 8 reply shapes + 4 menu choices + 3 others = **15 passing tests**, and
the `-v` output lists every cell by name. Paste the full `-v` output, not a
summary — the value of a parametrised invariant is that the reader can see which
cases were covered, and a `15 passed` line shows none of them.

- [ ] **Step 3: Prove the tests fail against a broken implementation**

A test that passes before your change is not evidence of your change, and these
pass against the correct code by construction. So break it deliberately, twice,
and paste both failures:

```bash
# 1. let a resolved label confirm the numbering
python - <<'PY'
import pathlib
p = pathlib.Path("src/papertrace/cli.py")
s = p.read_text()
s = s.replace('rec.choice, rec.chosen_by = "llm_resolved", "user"',
              'rec.choice, rec.chosen_by = "llm_resolved", "user"\n    rec.verified = True')
p.write_text(s)
PY
~/anaconda3/bin/python -m pytest tests/test_numbering_invariant.py -q   # must FAIL on choice 1
git checkout src/papertrace/cli.py

# 2. let a chosen reading confirm it
python - <<'PY'
import pathlib
p = pathlib.Path("src/papertrace/cli.py")
s = p.read_text()
s = s.replace('rec.choice, rec.chosen_by = pick, "user"',
              'rec.choice, rec.chosen_by = pick, "user"\n        rec.verified = True')
p.write_text(s)
PY
~/anaconda3/bin/python -m pytest tests/test_numbering_invariant.py -q   # must FAIL on choice 3
git checkout src/papertrace/cli.py
```

Confirm `git status` is clean afterwards. **Both failures must be pasted into
the report.** If either mutation passes, the parametrisation does not reach that
branch and the test is decoration.

- [ ] **Step 4: Full suite, lint, gates**

```bash
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

- **Gate 2 does not apply** — no new JSON field.
- **Gate 4 does not apply** — no source file changed; the honest-degradation
  paths this task exercises are pinned by Tasks 4 and 7 and re-run here.
- **Gate 5 does not apply** — no ingest, highlight or report code changed.

Say all three in the report rather than skipping them.

- [ ] **Step 5: Commit**

```bash
git add tests/test_numbering_invariant.py
git commit -m "tests: numbering_verified stays False, over every reply and every choice

$(cat <<'BODY'
Most of this design's safety lives in one flag. Parametrised over eight model
reply shapes — including the well-formed one that agrees with the parse, which
is the tempting case, not the malformed one — and all four menu choices.

A companion asserts numbering_corroborated CAN be True in the same run: the two
axes are independent, and a second axis that moves only with the first would
leave the 22-vs-19 false alarm firing.

Plus a grep guard over src/, whose docstring says what a grep cannot prove and
which has a self-test so it has been observed failing.
BODY
)"
```

---

### Task 9: Docs

**Why:** The README is a correctness surface, not marketing: every technical
statement must match the implementation. Eight tasks changed what the tool does
about its own reference numbering, one of them changed a rule CLAUDE.md states
as an invariant, and ADR 0002 pre-specified this whole remedy and explicitly
declined to authorise it. Leaving that authorisation unwritten would make the
next reader of ADR 0002 believe this shipped without one.

**No code, no tests, no behaviour.** If this task edits anything under `src/`,
stop and report it.

**Files:**

| File | Change |
|---|---|
| `docs/adr/0003-llm-reference-list.md` *(new)* | the decision ADR 0002 explicitly withheld: the trigger met with evidence, the sanctioned shape, and the four rejected alternatives |
| `CLAUDE.md` | the `ask.py` seam rule replacing *"check.py is the only module that calls a model"* (lines 147–155); the `refs.py` section gains per-label agreement (after line 146) |
| `CHANGELOG.md` | under the existing `## [0.7.0] — unreleased`, new Added/Fixed/Changed entries |
| `README.md` | the numbering bullet (line 96); a new "does" bullet; a new **"does not"** bullet stating the ceiling |
| `evals/DESIGN.md` | a pointer to the sibling eval plan, **not** the eval task itself |

**Two constraints that bound every word of this task:**

1. **`README.md` line 216 stays true.** It reads *"Read the source pages as
   images. In batch mode the model receives the cited source as extracted text
   with `page / block` provenance markers — the page picture is for you, in the
   evidence crop, not for the judge."* Nothing in this feature sends an image to
   any model: `_ask` is `claude -p` with `--tools ""`, `claude -p` has no image
   channel, and a pairwise visual vote was rejected by name. **Nothing on line
   216 is retracted, softened or qualified.** The new "does" bullet must be
   phrased so that it cannot be read as an exception to it.
2. **No accuracy figure may appear anywhere**, because none has been measured.
   Not a percentage, not "usually", not "in most cases", not a count of labels
   this caught on the motivating manuscript presented as a rate. The counts that
   *may* appear are the ones already in `CHANGELOG.md` as facts about one
   manuscript — *22 printed references parsed as 18* — because that is a
   reproduced defect, not a performance claim. ADR 0001 and
   `evals/DESIGN.md`'s Status section are the governing text.

- [ ] **Step 1: `docs/adr/0003-llm-reference-list.md`**

Follow `0002-no-grobid.md`'s shape exactly: front-matter list, `## Context`,
`## Decision`, `## Consequences`. Write:

```markdown
# ADR 0003 — An LLM reading of the reference list, as a fourth candidate

- **Status:** accepted
- **Date:** 2026-09-17
- **Related:** ADR [0002](0002-no-grobid.md), ADR [0001](0001-no-gold-benchmark.md),
  `src/papertrace/reflist.py`, `src/papertrace/ask.py`,
  `docs/superpowers/specs/2026-09-16-llm-reference-list-design.md`

## Context

ADR 0002 declined to adopt GROBID and, in the same breath, specified the
escalation it *would* accept and refused to authorise it:

> If reference parsing ever becomes the dominant source of wrong audits, the
> cheap escalation is **a third candidate reading** wired into the existing
> `reconcile` arbitration behind an opt-in flag — not a replacement of the
> parser. That path is strictly additive and leaves the two-reading behaviour
> unchanged when the third is absent. **This ADR does not authorise it; it
> records it as the shape a future proposal should take.**

This ADR is that authorisation. The trigger is now met with evidence.

A manuscript under review with 22 printed references parsed as 18. Docling
renders a hanging-indent numeral column as a GFM table, so five reference rows
arrived as `|  6. | Author A (2019) … |` and were glued onto the entry above
them as wrapped continuations. Every claim citing `[6]` or above was judged
against a different paper than the one cited: six substantive verdicts naming
papers the manuscript never cited.

The verdicts were not merely wrong, they were **confident**. Gluing five
references onto one entry left `_entry` scraping the next reference's DOI onto
it, and the title check *passed* on that DOI, because the glued raw string held
the words of both papers. `title_check: verified` was printed on all six. A
stronger title check would not have helped: the reference string really did name
the paper that was downloaded, alongside the one the label meant. That is the
distinction this ADR turns on — the broken join is `label → entry`, and every
check the tool had verified `entry → PDF`.

Three root causes, all reproduced offline and all fixed deterministically first
(`0.7.0`: `_unwrap_table_rows`, `_numerals_agree_with_position`, and the
`_covers` conflation). Those fixes are not the subject of this ADR and needed no
authorisation — they are parser bugs. What they cannot do is certify themselves,
which is the sentence ADR 0002 already wrote about GROBID: *"a parser cannot
certify itself."* Two readings of one bibliography, both produced by this tool
from the same converter output, share a failure mode. A third and fourth reading
that do not are the only thing that can say so.

## Decision

**A model reading of the reference list is adopted as a candidate — a voter with
no vote of its own — behind `--llm-refs/--no-llm-refs`, default on.**

`reconcile` goes from two candidate readings to four: the Crossref deposit, the
run backend's parse, a flat-text (pymupdf) parse of the same PDF, and a
structured list proposed by a model. Five constraints define the shape, and each
is the answer to a way this could have gone wrong:

1. **The model's reading can only subtract verdicts.** `reconcile` still chooses
   between the deposit and the parse. The flat-text and model readings are
   voters only, so no model output can cause a source to be resolved,
   downloaded or judged — it can only cause a verdict to be withheld. This is
   the safety property the design rests on and
   `tests/test_numbering_invariant.py` tests it directly.
2. **Every field of the model's reply is found verbatim in one of the two texts
   it was shown, or discarded.** A discarded field costs a recorded gap; an
   accepted field is one a reader can grep out of the source text. Nothing
   invented survives to name a paper. A whole candidate is discarded if any
   title fails, and the run says the model's reading was *unusable* — never that
   it agreed.
3. **Agreement is computed per printed citation label, never by position.**
   `_first_divergence` zips by position and would have compared docling's 6th
   entry against pymupdf's 6th and reported divergence for the wrong reason.
   Reading-order zipping has been rejected three times in this codebase and is
   rejected again here.
4. **Any disagreement is `disputed`. No majority vote, no ranking.** The
   codebase's own rule: *"A non-unique match is refused, never ranked."* A
   disputed label is dropped from the set of sources `check.py` judges, and the
   claim lands `unchecked` with a note — explicitly not `not_retrieved`, which
   would claim a retrieval failure that did not happen.
5. **`numbering_verified` keeps its exact current meaning and stays `False`
   through every model reply and every interactive choice.** Corroboration is a
   **second, independent axis** (`numbering_corroborated`), reported beside it.
   No published boolean is redefined and no model path can flip one.

Four alternatives were weighed and rejected:

- **A pairwise visual vote** — "does this bibliography crop match that title
  crop". It verifies `entry → PDF`, which is already deterministically
  title-checked and which *passed* on all six wrong verdicts. The broken join is
  `label → entry`, and no crop carries a label, because the numeral is what the
  converter destroyed. It also has no deterministic verifier, so a false "match"
  would overwrite a correct `numbering_verified = False` with a plausible
  confirmation — the one thing CLAUDE.md forbids. Moot as well: the real
  docling table block has `regions=1` for all five references, so no per-entry
  rectangle exists to crop.
- **The LLM as arbiter.** The body's own `[N]` markers are the only reading
  definitionally right about what the paper cites. Replacing a free, offline,
  definitionally-correct arbiter with a probabilistic one is a strict loss.
- **Field-level splicing across candidates by position.** pymupdf loses two DOIs
  entirely, so from that point two lists are off by two and a `num` from one
  would be stapled to another's `doi`.
- **Images through the seam.** `claude -p` has no image channel (no `--image`;
  `--file` needs an Anthropic Files-API id). Granting `Read` would dismantle
  `--tools ""` and `_scratch_cwd`, and would falsify the README's statement that
  the model never receives page images.

`ask.py` is extracted as part of this decision. CLAUDE.md's rule *"check.py is
the only module that calls a model"* was protecting the seam, not the module;
with `refs` calling it too, the rule becomes *"one file in `src/` shells out,
and a test says which"* — enforceable rather than conventional. It also fixes a
live misattribution: `_LAST_MODEL` was one global overwritten by every call, so a
run whose judging made zero calls would have printed the reference-list model as
the `Checker:` of verdicts it never saw.

**No accuracy figure is claimed for any of this, and none may be.** ADR 0001
governs: a figure needs a gold set this project's authors did not construct,
labelled by at least two people who did not write the prompts, on **both
backends** — the converter is the cause here, not a nuisance parameter. The
evaluation is specified as a second task in `evals/DESIGN.md` and is a sibling
plan, not part of this one.

## Consequences

- Crossref remains a **candidate, not an oracle**, and so does the model.
  Adding a fourth reading does not soften that; it is the same rule applied to a
  fourth voter.
- Two stages now call the model seam, so a per-call-site model record is
  load-bearing rather than tidy. Anything that reads "which model did this" must
  name a site.
- A disputed label costs verdicts. That direction is deliberate and it is the
  expensive one: `correct_numbering_refused_rate` is the cost metric in
  `evals/DESIGN.md`, reported separately from
  `wrong_numbering_confirmed_rate`, and the two are never blended.
- Where nothing passed the extent check and labels are in dispute, an
  interactive run writes the full disagreement to
  `case/out/reference_disagreement.md` **before** it asks anything, and records
  `numbering_chosen_by: "user"` for whatever the user answers. A person
  consenting to proceed is an input, not evidence. A non-interactive run
  withholds the disputed labels and asks nothing.
- ADR 0002's decision is unchanged. GROBID is still not adopted and still not
  benchmarked; this ADR takes the escalation ADR 0002 named, not the one it
  refused.
```

- [ ] **Step 2: `CLAUDE.md` — the seam rule**

Replace `CLAUDE.md:147-155` (the whole `check.py` bullet, from `- **`check.py`**
— the **only** module…` through `…truncation travels in a per-run
`Truncations`.`) with two bullets. The `ask.py` bullet goes **first**, so the
reader meets the seam before the module that uses it:

```markdown
- **`ask.py`** — the **only** file in `src/` that runs a subprocess, and the
  only place this codebase shells out to a model (`claude -p --safe-mode
  --tools ""` in a private scratch cwd; inherits the user's Claude Code login,
  no API key). "`check.py` is the only module that calls a model" was the rule
  until `refs` needed a reading of the bibliography too — the rule was
  protecting the seam, not the module, and `tests/test_ask.py` greps `src/` and
  asserts exactly one file, which makes it enforceable rather than
  conventional. Two callers, `check.py` and `refs.py` (through `reflist.py`),
  and no third without that test going red. The model is recorded **per call
  site** (`for_site`, `model_for`, `SITE_CHECK`, `SITE_REFS`), not in one
  global: `_LAST_MODEL` was overwritten by every call, so a run whose judging
  made zero calls — every cited source `not_retrieved`, nothing to judge —
  printed the reference-list model as the `Checker:` of verdicts it never saw.
  `_ask`'s signature is frozen at `(prompt, model=None)`; sixty-two test sites
  patch it with a two-argument lambda, which is why the site travels out of
  band in a context manager instead of as a third parameter.
- **`check.py`** — every prompt and every verdict rule, and no subprocess of
  its own. Two prompts: `EXTRACT_PROMPT` then `CHECK_PROMPT`, one call per
  **document** so context stays small — an article, each of its supplements,
  and each of the audited paper's own are separate calls with separate
  verdicts. Call the bare name `_ask(...)`, imported `from .ask import _ask`, so
  `monkeypatch.setattr(check_mod, "_ask", …)` still intercepts; rewriting a
  call site as `ask._ask(...)` bypasses every patch and turns sixty-two offline
  tests into live paid calls. `_ask_judge` wraps the seam and reads
  `ASK_ATTEMPTS` rather than hardcoding one retry — the wizard prints a
  worst-case bill derived from that constant. Also holds `coverage_audit()`,
  which is deliberately **mechanical and prompt-independent** — a regex
  (`_LABEL_GROUP`) over bracketed numeric labels, so a citation the extractor
  missed still surfaces. **A `disputed` label is dropped from `avail` before any
  call is made**, so a source whose identity two readings contradict is never
  judged; if nothing survives the claim is `unchecked` with the labels named,
  never `not_retrieved` — that source was obtained, and `withheld_refs` is a
  different field from `unjudged_refs` for exactly that reason. `last_model()`
  reads the `check` site; truncation travels in a per-run `Truncations`.
```

Then insert into the `refs.py` bullet, after line 146 (`…declined to stand
behind.`) and before the `check.py`/`ask.py` bullets:

```markdown
  **Agreement is per printed label, and disagreement is refused rather than
  ranked** (0.7.0). `reconcile` arbitrates between the Crossref deposit and the
  run backend's parse, unchanged; a flat-text pymupdf reading and a model
  reading (`reflist.py`, `--llm-refs`) are **voters only** and reach neither
  `reconcile`'s arguments nor `resolve_all`, so no model output can cause a
  source to be resolved, downloaded or judged — only a verdict to be withheld.
  `label_agreement` joins on `e.num`, the printed numeral, never on position:
  `_first_divergence` zips, and would compare docling's 6th entry against
  pymupdf's 6th and report divergence for the wrong reason. Any pair failing
  `_same_work` is `disputed` — no majority vote, because *"a non-unique match is
  refused, never ranked"* — and `single` deliberately **prints**, since a
  pymupdf-backend run whose model candidate was discarded has one reading, every
  label would be `single`, and the audit would report nothing at all. A
  `boundary_ambiguous` entry does not speak for its label and neither does a
  reading carrying that label twice: the first has said it cannot stand behind
  the label, and for the second, which of the two it means is the question.
  Every field of the model's reply must be found **verbatim** in one of the two
  texts it was shown or it is discarded, and an unverifiable title discards the
  answer — nothing invented may name a paper. `numbering_corroborated` is a
  second, independent axis: `numbering_verified` keeps its exact meaning and
  stays `False` through every model reply and every interactive choice, which
  `tests/test_numbering_invariant.py` parametrises over and which is the one
  thing in this feature that may not be relaxed for convenience.
```

**Write it in that file's voice**: it states constraints and names past bugs, it
does not describe features. Every sentence above either forbids something or
says what went wrong when it was not forbidden. If a sentence you add does
neither, cut it.

- [ ] **Step 3: `CHANGELOG.md`**

The `## [0.7.0] — unreleased` heading and its three existing `### Fixed —` blocks
stay exactly as they are. Add, under the same version heading, after the
existing Fixed blocks:

```markdown
### Added — a third and fourth reading of the reference list, and a per-label verdict on the numbering

`reconcile` arbitrated between two readings of the bibliography: the tool's own
parse and the reference list the publisher deposited with Crossref. Both can be
absent — an unpublished manuscript has no DOI to look up — and where the parse
is the only reading, nothing can contradict it.

Two more candidates now stand beside them. A flat-text (pymupdf) parse of the
same PDF, free on a docling run and skipped on a pymupdf one because it would
be identical. And a structured reference list proposed by a model, which is
shown both texts and given no authority over either: **every field of its reply
must be found verbatim in one of them or it is discarded**, an unverifiable
title discards the whole answer, and the model's list never becomes
`reconcile`'s chosen reading. It is a voter. On by default, `--no-llm-refs` to
turn it off, on both `refs` and `run`.

Agreement is then computed **per printed citation label** — joined on the
numeral the page carries, never on position — and reported as `agreed`,
`single`, `disputed` or `absent`. Where two readings name different papers at a
label, verdicts on claims citing it are **withheld**: the claim is reported
`unchecked` with the label named, not `not_retrieved`, because the source was
obtained and read. `single` deliberately prints, with the caveat it already
carried. `numbering_corroborated` is a new, independent axis beside
`numbering_verified`, which keeps its exact meaning.

Where nothing accounted for the body's labels and labels are in dispute, an
interactive run writes the full disagreement to
`case/out/reference_disagreement.md` — every reading's fields and the verbatim
text each was read from — **prints the path, and only then asks** what to do
about it. Four options: resolve them with a model, withhold them all, adopt one
reading whole, or abort. Whatever is answered is recorded as
`numbering_chosen_by: "user"`; a person consenting to proceed is an input, not
evidence, and nothing a user answers can set `numbering_verified`. A
non-interactive run withholds the disputed labels and asks nothing.

Partial resolution is a normal, representable, reported outcome: `labels_resolved`
and `labels_disputed` are both non-empty on a run where some labels were settled
and some were not, and "cannot tell" from the resolution call is a correct answer
that keeps a label withheld.

### Added — `ask.py`, the one seam, with the model recorded per call site

Every model call went through one `_ask` in `check.py`, which was a convention
stated in `CLAUDE.md` with nothing enforcing it. It is now a file —
`src/papertrace/ask.py`, the only file in `src/` that runs a subprocess — and
`tests/test_ask.py` greps `src/` and asserts exactly that. Same flags, same
timeout, same sandbox: `--safe-mode`, `--tools ""`, a private 0700 scratch cwd.

### Fixed — a run that judged nothing could name a judge

`check._LAST_MODEL` was a single module global overwritten by every `_ask` call,
and the report's `Checker:` line rendered it. With `refs` also calling the seam,
a run whose judging made zero calls — every cited source not retrieved, so
nothing to judge — would have printed the **reference-list** model as the judge
of verdicts it never saw. The model is now recorded per call site, and
`last_model()` reads the judging site only: `None` where no judging happened,
never the other site's answer.

### Fixed — claim extraction had no retry, and the wizard's cost ceiling assumed it did

`ASK_ATTEMPTS` exists so the wizard's advertised worst-case bill cannot drift
from the real retry policy. The retry loop did not read it — it hardcoded one
retry and matched the constant only because the constant happens to be 2 — and
the single extraction call, whose failure fails the whole run, had no retry at
all. Extraction now retries like everything else, and the wizard's ceiling moved
with it.

### Changed — the coverage of `unjudged_refs`, and a second axis on the numbering

`ClaimResult.withheld_refs` is a new field and is **not** `unjudged_refs`. An
entry in `unjudged_refs` means the source could not be obtained — nobody read
it. A withheld reference **was** obtained; what is in doubt is whether it is the
paper the label names. Putting it in `unjudged_refs` would report a retrieval
gap that does not exist.

`numbering_verified` is unchanged in meaning and unchanged in what can set it.
`numbering_corroborated` is reported beside it, which lets the numbering
disclosure stop crying wolf on the case it was firing on wrongly: a list two
readings agree about entry for entry, longer than the highest cited label
because three of its references are cited only in the supplement.
```

Line length is a guide, not an error (`E501` is ignored), but keep prose wrapped
at roughly the width of the surrounding entries.

- [ ] **Step 4: `README.md`**

Three edits. **Re-read line 216 before and after** — it is not touched and not
qualified.

*(a)* The numbering bullet at line 96 opens *"**Check its own reference
numbering before trusting it.**"* and says *"Two independent readings are
taken"*. That number is now wrong. Replace the sentence spanning lines 99–103
(`Two independent readings are taken … arbitrate between them.`) with:

```markdown
  Up to four independent readings are taken — the tool's parse of the printed
  list, a flat-text parse of the same PDF, the reference list the publisher
  deposited with Crossref (`refs --doi`, defaulting to the DOI printed on page
  1), and a structured list proposed by a model that is shown the two texts and
  given authority over neither (`--no-llm-refs` turns it off) — and the
  manuscript's own `[N]` markers arbitrate between them. Only the parse and the
  deposit can be *adopted* as the list; the other two are readings that can
  disagree, and a disagreement is all they can do. Every field of the model's
  reply is checked against the text it was shown and discarded if it is not
  printed there, so nothing it invented can name a paper.
```

Leave the rest of that bullet — the partial-deposit paragraph, the DOI identity
check, the title fallbacks — byte for byte.

*(b)* A new **"does"** bullet, immediately after the numbering bullet (so it
sits before the coverage bullet at line 121):

```markdown
- **Withhold a verdict where two readings of the bibliography name different
  papers.** The citation label is the join key, so a label whose readings
  disagree is a label nothing can say the cited paper for — and a confident
  verdict there is about a paper the manuscript may never have cited. Agreement
  is computed for each printed label separately, on the numeral the page
  carries. Any disagreement withholds: the claim is reported `⚠ not checked`
  with the label named, **not** `⊘ not retrieved`, because that source was
  obtained and read — what is in doubt is whether it is the paper the label
  names. Where nothing accounted for the body's labels and labels are in
  dispute, an interactive run writes the whole disagreement to
  `case/out/reference_disagreement.md` — every reading's fields and the
  verbatim printed text each came from — prints the path, and only then asks
  whether to resolve them, withhold them, adopt one reading, or stop. Nothing
  you answer marks the numbering as confirmed; the report records which choice
  was made and that you made it. A run that is not attached to a terminal
  withholds the disputed labels and asks nothing.
```

*(c)* A new **"does not"** bullet, placed at the head of the
`**PaperTrace does not**` list at line 174 — the ceiling belongs where a reader
meets the list, not at the end of it:

```markdown
- **Confirm** a reference numbering by agreement between its readings. Where
  they agree, the report says the numbering was *corroborated*, and says which
  readings corroborated it — never that it was verified. They all read the same
  document: a reference the page layout destroyed is one every reading may have
  missed, and a model agreeing with the parse read the same damaged text the
  parse did. *"Verified"* still means one thing only — the manuscript's own
  `[N]` markers accounted for exactly the labels a candidate list carried — and
  nothing a model returns and no answer you give at a prompt can set it. Nor
  does the model ever see the page: it is shown extracted text, the same as
  every other model call in this tool.
```

That last sentence is what keeps *(b)* from being readable as an exception to
line 216. Verify after editing:

```bash
grep -n "the page picture is for you" README.md
grep -rniE '\b[0-9]+(\.[0-9]+)?%|\bf1\b|accuracy of|precision of|recall of' README.md
```

The first must still print line 216 (number may shift by the lines added above
it — that is fine; the sentence must be intact). The second must return nothing
new: **no accuracy figure may appear anywhere, because none has been measured.**

- [ ] **Step 5: `evals/DESIGN.md` — a pointer, not the task**

The pairing evaluation is a sibling plan. It touches `metrics.py`,
`scoring.py`, `eligibility.py` and `provenance.py`, none of which this plan
opens, and it grades work this plan had to produce first. Writing its spec here
would put an unimplemented task in the document that says what has been
measured.

So: one subsection, after `## The evaluation unit` (which ends at line 52, just
before `## Paired cases`), naming the second unit and pointing at where it is
specified:

```markdown
### A second unit, specified elsewhere: (citation label, printed reference entry)

The unit above is `(manuscript claim, cited source)` and it grades a model's
judgement. It cannot grade the join that *chooses* the source. That join broke
on a real manuscript — 22 printed references read as 18, and six substantive
verdicts naming papers the manuscript never cited — and every one of them
carried `title_check: verified`, because the glued reference string really did
contain the downloaded paper's words alongside the cited paper's.

So there is a second task, never folded into `judgment_accuracy`, whose unit is
`(citation label, printed reference entry)`. Deliberately **not**
`(label, retrieved PDF)`: that conflates the two joins, and it is exactly the
conflation that let `verified` be printed on six wrong papers. Its gold rows
carry the entry text **verbatim as printed** and no entry index — a gold set
keyed on position breaks whenever parsing changes.

Its two error directions are reported **separately and never blended**: labels
the parse got wrong and the tool confirmed anyway (the safety metric), and
labels the parse got right and the tool refused (the cost). Superscript papers
have no arbiter and cannot be scored at all; they are excluded with a reason and
counted, never deleted.

**It is specified and implemented in its own plan, not here.** It needs
`metrics.confusion()` parameterised off `JUDGMENT_VERDICTS`, a new population in
`scoring._populations`, its own pass in `eligibility.py`, and
`provenance.prompt_fingerprint` extended to cover `REFLIST_PROMPT` and
`RESOLVE_PROMPT` — hashing only `EXTRACT_PROMPT` and `CHECK_PROMPT` while two
more prompts decide the answers is the classic eval failure. `align.py` is not
reusable for it and must not be touched: its candidate scoring is `difflib` over
`(normalize_claim(text), set(labels))` with a 0.60 floor, and a pairing task
wants an exact join on the label. `missing` keeps its `list[str]` meaning byte
for byte — a refused pairing must never be unioned into it, because a
wrongly-paired label is a numbering fault, not an extraction gap.

Nothing in this section has been measured. The Status note at the top of this
document governs it too.
```

- [ ] **Step 6: Verify the docs against the implementation**

Docs are a correctness surface, so check the claims rather than trusting them:

```bash
# the seam claim in CLAUDE.md and the ADR
~/anaconda3/bin/python -m pytest tests/test_ask.py::test_only_ask_py_shells_out_to_a_model -q
# the invariant both the ADR and the README bullet assert
~/anaconda3/bin/python -m pytest tests/test_numbering_invariant.py -q
# the flag names the README quotes
grep -n "llm-refs" src/papertrace/cli.py
# the file path the README and CHANGELOG name
grep -rn "reference_disagreement.md" src/papertrace/cli.py
# every vocabulary word the docs use
~/anaconda3/bin/python -c "from papertrace.refs import LABEL_AGREEMENT; print(LABEL_AGREEMENT)"
```

Paste each. A docs task that cannot show the thing it describes has described
something else.

Then the whole suite and lint, because `CLAUDE.md` and the README are read by
tests (`tests/test_packaging.py` and the `/review` skill's documented commands):

```bash
~/anaconda3/bin/python -m pytest -q
ruff check src tests scripts evals
```

- [ ] **Step 7: Gate 5, and the demo**

**Gate 5 applies to this task only in the negative, and say so explicitly:**
Tasks 1–8 changed no ingest, highlight or report *code*, and Task 9 changes no
code at all — so the end-to-end demo need not be re-run for this task. It
**does** need re-running once for the branch, because templates gained branches
and `examples/demo/output/` is the one committed output. That is a branch-level
step, not this task's, and it needs network and a logged-in `claude` CLI:

```bash
# branch-level, not part of Task 9 — run once before any merge is proposed
python examples/demo/make_manuscript.py
papertrace run examples/demo/demo_manuscript.pdf -c demo_case \
    --model claude-opus-5 --format terminal --format viewer --png
```

Expect the committed showcase's counts unchanged: 1 supported · 2 contradicted ·
1 not retrieved · 1 uncited assertion, 4 claims. The demo's bibliography is
flat text with intact numerals, so every label should come back `agreed` or
`single` and no verdict should be withheld. **If a demo label comes back
`disputed`, that is a finding about the feature, not about the demo** — report
it and stop rather than adjusting the demo to agree.

- Gate 2 does not apply — no new JSON field.
- Gate 4 does not apply — no source file changed.

- [ ] **Step 8: Commit**

```bash
git add docs/adr/0003-llm-reference-list.md CLAUDE.md CHANGELOG.md README.md evals/DESIGN.md
git commit -m "docs: the authorisation ADR 0002 withheld, and the seam rule that replaces a convention

$(cat <<'BODY'
ADR 0002 pre-specified this remedy — a third candidate reading behind an opt-in
flag, not a replacement of the parser — and explicitly declined to authorise
it. ADR 0003 is the authorisation, with the trigger now evidenced: 22 printed
references read as 18, six substantive verdicts about papers the manuscript
never cited, every one of them carrying title_check verified. It records the
four rejected alternatives so they are not re-proposed.

CLAUDE.md's "check.py is the only module that calls a model" becomes "one file
in src/ shells out, and a test says which" — the rule was protecting the seam,
not the module. The refs.py section gains per-label agreement, why the join is
the printed numeral and not the position, and why single prints.

README gains a does bullet and a does-not bullet stating the ceiling:
corroborated is not verified, every reading read the same document, and nothing
a model returns or a user answers sets numbering_verified. Line 216 is
untouched and nothing is retracted. No accuracy figure anywhere — none has been
measured.

evals/DESIGN.md gets a pointer to the sibling eval plan, not the plan.
BODY
)"
```

