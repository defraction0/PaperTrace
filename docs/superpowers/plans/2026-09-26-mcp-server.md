# MCP Server — Implementation Plan

**Goal:** Make PaperTrace usable from any MCP host — start an audit, follow it,
and read every finding with its caveats and its evidence images — without
weakening a single rule in `CLAUDE.md`.

**Architecture:** One new module, `src/papertrace/mcp_server.py`, built on the
official SDK's `MCPServer` (`mcp>=2.2,<3`). Read tools load the case folder's
JSON through the existing dataclasses and serialise the existing disclosures.
The one driving tool runs the existing pipeline in-process on a background
thread. `papertrace mcp` serves it over stdio.

**Spec:** `docs/superpowers/specs/2026-09-26-mcp-server-design.md`

**Decision record:** `docs/adr/0004-mcp-server.md`

## Global Constraints

- **Tests stay offline.** No network, no model calls. Tools are exercised through
  the SDK's in-memory `Client`; the pipeline is faked at `cli._run_pipeline`.
- **No `conftest.py`.** Every test module prepends `src/` to `sys.path` and
  imports with `# noqa: E402`.
- **`ask.py` stays the only file in `src/` that starts a process** —
  `tests/test_ask.py::test_only_ask_py_shells_out_to_a_model` must stay green.
- **No new case-folder field** (gate 2 untouched). **No ingest, highlight or
  report *logic* changes** (gate 5 untouched — say so in the handover rather
  than skip it silently). **`refs.py`, `check.py`, `scout.py` untouched**
  (gate 4 untouched).
- **Red before green, pasted.** Each task: write the test, run it, see it fail
  for the stated reason, implement, run it, see it pass.
- `ruff check src tests scripts evals` clean; never `ruff format`.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/papertrace/cli.py` *(modified)* | `run` split into `_run_pipeline` (keyword-only, plain defaults) and its Typer adapter; `papertrace mcp` command | 1, 5 |
| `src/papertrace/ask.py` *(modified, +1 function)* | `forget_models()` — a long-lived process starts each run with no recorded model | 2 |
| `src/papertrace/mcp_server.py` *(new)* | `build_server()`, the nine tools, the job registry, `serve()` | 3, 4 |
| `pyproject.toml` *(modified)* | `mcp` extra; `mcp` in `[dev]` and `[full]` | 5 |
| `tests/test_pipeline_split.py` *(modified)* | `run` delegates every parameter to `_run_pipeline` | 1 |
| `tests/test_ask.py` *(modified)* | `forget_models` | 2 |
| `tests/test_mcp_server.py` *(new)* | every tool, the parity loop, the job, stdio | 3, 4, 5 |
| `tests/test_packaging.py` *(modified)* | the extra exists and CI installs it | 5 |
| `README.md`, `CHANGELOG.md`, `CLAUDE.md` | docs | 6 |

## Task 1 — split `run` into `_run_pipeline`

`run` is the one stage function still called only through its Typer command,
and the MCP job would be its second caller. Calling a Typer command directly
hands every omitted option an `OptionInfo` sentinel — the flaw `cli.py` records
shipping twice — so the split is made *before* the new caller exists.

- [ ] Red: `test_run_pipeline_rejects_a_positional_call` and
      `test_run_command_delegates_to_the_pipeline_function` (exact kwargs dict,
      in the style of the `refs` test beside them). Expect `AttributeError: no
      _run_pipeline`.
- [ ] Green: move `run`'s body into `_run_pipeline(*, ...)`; `run` passes every
      parameter by keyword. No behaviour change: the existing suite is the proof.

## Task 2 — `ask.forget_models()`

- [ ] Red: seed `_MODELS` for both sites, call `forget_models()`, expect
      `model_for(...) is None` for both — `AttributeError` first.
- [ ] Green: one function, clearing the dict in place (tests monkeypatch the
      attribute, so rebinding it would be invisible to a caller holding it).

## Task 3 — the read tools

Fixture: a case folder written with `RunResults.to_json`,
`RefManifest.to_json`, `ScoutResults.to_json`, and evidence PNGs drawn with
pymupdf. Each test calls through `Client(build_server())`.

- [ ] Red, then green, one behaviour at a time:
  - `list_tools` advertises nine tools; read tools carry
    `read_only_hint=True, open_world_hint=False`; `start_audit` carries
    `destructive_hint=True, open_world_hint=True`.
  - `audit_summary` returns the counts and every run-level disclosure; a case
    with no `results.json` is a `ToolError` naming the missing file; a malformed
    one is a `ToolError`, never empty counts.
  - A limited run: the scope disclosure is first **and** `limited` is the last
    key of the summary and of `list_claims`.
  - Parity: for each fixture in `test_disclosure_parity.py`'s shape, every run
    token is in `audit_summary`'s text and every claim token in `get_claim`'s.
  - `list_claims` filters on the headline verdict; the filter's enum is
    `models.VERDICTS`, not a copy.
  - `get_evidence` returns the headline judgement's primary and continuation
    images plus a caption carrying the anchor disclosure; a named source picks
    that judgement; a `not_retrieved` claim is a `ToolError` saying nothing was
    read; an image path escaping `out/` is refused.
  - `list_references` has no `pdf_path` anywhere; `labels_uncomparable: null`
    stays `null` and `[]` stays `[]`; the status filter's enum is
    `models.REF_STATUSES`.
  - `list_gaps` lists uncited assertions, unreached occurrences and unchecked
    claims; a coverage audit that never ran is `null`.
  - `get_scout` carries the scout's own caveat; no `scout.json` is a
    `ToolError`.

## Task 4 — `start_audit` and `audit_status`

- [ ] Red, then green:
  - Preflight refusals are immediate `ToolError`s: missing manuscript, `claude`
    not on `PATH`, no contact email, a case folder holding another paper.
  - The job passes `_run_pipeline` every parameter explicitly.
  - Inside the job: `cli._interactive()` is False, `input()` raises `EOFError`,
    `ask.model_for(SITE_CHECK)` is None even when a previous audit left one,
    and `cli.console` output lands in the job log, not on stdout.
  - `audit_status` goes `running` → `finished`; a `typer.Exit(2)` from the
    pipeline is `failed` with the console's reason; any other exception is
    `failed` with its type and message.
  - A second `start_audit` while one runs is refused, naming the running case.
  - Read tools refuse the case whose audit is running.
  - `wait_seconds` returns as soon as the job ends, and never waits past 50.
  - A finished status carries no counts.

## Task 5 — `papertrace mcp`, packaging, stdio

- [ ] Red, then green:
  - `[project.optional-dependencies]` has `mcp`, and `[dev]` and `[full]` carry
    the same requirement (packaging tests).
  - `papertrace mcp` with stdin closed exits 0 and writes **zero bytes** to
    stdout (subprocess).
  - A real stdio round trip — `Client(StdioServerParameters(...))` — lists the
    nine tools and reads a fixture case.
  - Without the `mcp` package, `papertrace mcp` exits 2 with the install line on
    **stderr** and nothing on stdout.

## Task 6 — docs

- [ ] README: a "Use it from an MCP host" section (Claude Desktop, Claude Code,
      Cursor/VS Code), the install matrix row, the does/does-not list, the
      roadmap tick. Every statement checked against the code.
- [ ] CHANGELOG: an Unreleased entry.
- [ ] CLAUDE.md: the architecture bullet for `mcp_server.py`, and the
      long-lived-process rule (`forget_models`, one job at a time).

## Task 7 — the review round

A code review of the branch (the `code-review` skill, high effort) returned ten
findings; nine held up against the code, each fixed test-first. The tenth —
that `_run_pipeline`'s OptionInfo guards are dead — did not: tests call
`cli.run()` directly and omit options, which hands the adapter's sentinels
straight through.

- [x] A failed or interrupted audit's mixed case is refused, via the
      `mcp_audit.json` record; `audit_status` reads an earlier server's record.
- [x] Run-level disclosures in `get_claim`, `list_claims` and the evidence
      caption, beside the claim's own.
- [x] No fallback to the server's working directory for an unwritable paper
      folder; the preflight's lines open the job's log.
- [x] A `scout.json` the latest audit did not write is refused; the caveat
      carries the scan's error.
- [x] A 1.x SDK gets the install line; the command is never wrapped in two.
- [x] `schemas/mcp_tools.schema.json` and `schemas/mcp_audit.schema.json`,
      with validation tests.
- [x] `disclosures.coverage_audited` and `cli.SCOUT_CAVEAT`: one rule and one
      sentence each, not two copies.
