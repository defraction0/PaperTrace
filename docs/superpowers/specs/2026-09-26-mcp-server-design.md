# An MCP server — PaperTrace as a tool for any MCP client

Design, 2026-09-26. Status: implemented alongside this document on
`claude/gifted-ritchie-ga60nx`; open to review.

## Why

The roadmap has carried one line since 0.5.0: *MCP server — drive PaperTrace as
a tool from any MCP-capable client.* Today there are three ways in: the CLI, the
wizard, and the two Claude Code skills, which drive the CLI through a shell. A
host that is not Claude Code — Claude Desktop, Cursor, VS Code, anything that
speaks the Model Context Protocol — has no way to start an audit or read one
without a shell. MCP is the standard way to hand a local tool to those hosts.

The risk is specific to this codebase. Every rule in `CLAUDE.md` was written for
readers of a **file** — a report, a manifest, a `results.json` — and for a
process that **lives for one command**. An MCP server is a new reader (a model)
and a new process shape (one that outlives many audits). Each rule has to be
re-established for both, or the server becomes the path by which a verdict
reaches a reader without its caveats.

## What it is

`papertrace mcp` serves over **stdio**, behind an optional extra
(`pip install -e ".[mcp]"`, `mcp>=2.2,<3` — the SDK's own versioning policy
promises breaking changes only in a major). Nine tools, in two groups:

| Tool | Group | What it returns |
|---|---|---|
| `audit_summary` | read | counts, references, checker, coverage headline, **every run-level disclosure**, the scope statement last |
| `list_claims` | read | one row per claim (optionally one verdict), each with its claim-level caveats |
| `get_claim` | read | one claim in full: quote, per-source judgements, rationale, page, block, anchor phrases, every disclosure |
| `get_evidence` | read | the red-box crops for one claim (or one of its sources) **as images**, captioned with the anchor state |
| `list_references` | read | the retrieval manifest without local paths, with the numbering state as recorded |
| `list_gaps` | read | uncited assertions, citation places no claim reached, claims never checked |
| `get_scout` | read | the three scout registers, with the scout's own caveat |
| `start_audit` | drive | starts the full pipeline (`papertrace run`) as a background job and returns at once |
| `audit_status` | drive | the job's state and log tail; long-polls up to 50 s |

Read tools are deterministic, offline and free: they read the case folder the
pipeline wrote. `start_audit` is the only tool that spends — model calls through
`claude -p`, and the open-access services `refs` and `scout` query.

## The rules it inherits, and how each is kept

1. **An unread source never receives a verdict.** The server computes no
   verdict. It reads what the pipeline wrote, or runs the pipeline itself.
   Nothing here derives, re-ranks or summarises a verdict.
2. **Disclosures travel with every count and every verdict.** The read tools
   call `run_disclosures`, `claim_disclosures` and `judgement_disclosures` — the
   same producers the four report formats use — and serialise them with the
   viewer's own `report._disclosure_dict`. The MCP output is a fifth reader, and
   the parity test is extended to it: every token reaches it. A host can read
   one claim without ever reading the summary, so `get_claim`, `list_claims`
   and the evidence caption carry the run-level disclosures too.
3. **A limited run never reads as a smaller paper.** The scope disclosure is
   first among the disclosures, as in every report, and every result that states
   counts or lists claims ends with a `limited` field carrying the same sentence
   — the JSON a model reads has an end, and the scope is said there too.
4. **Absent is not zero.** A missing `results.json` is a `ToolError` naming what
   is missing, never a result with empty counts. Three-state manifest fields
   (`labels_uncomparable`, `numbering_corroborated`) pass through unchanged, so
   `null` stays `null`. A coverage audit that never ran is `null`, not `""`.
5. **Failure is said, never guessed.** In SDK v2 only a `ToolError`'s message
   reaches the model; any other exception becomes a message-less
   `Error executing tool …`. So every refusal is a `ToolError` carrying the
   reason — the same sentence the CLI would have printed — and a job that fails
   records its reason in its state instead of surfacing as an anonymous error.
6. **The interactive gate on disputed labels cannot be reached.** The job runs
   with nobody to ask: `sys.stdin` is an empty stream for its duration, so
   `cli._interactive()` is False and any prompt reads end-of-file rather than
   waiting. A disputed label is therefore withheld with
   `numbering_chosen_by: "default"`, exactly as the CLI does without a
   terminal. An MCP client cannot settle a dispute; a person at a terminal can,
   with `papertrace refs`.
7. **`ask.py` stays the only file in `src/` that starts a process.** The server
   runs the pipeline **in-process, on a thread** — never a `papertrace run`
   child. `tests/test_ask.py` would go red otherwise, and the rule protects the
   seam, not a module.
8. **The model a run pinned is the model it reports.** `ask._MODELS` was
   correct because one process ran one command. A server runs audit after
   audit, and a second audit whose judging made no calls would print the first
   audit's judge as its `Checker:` — the bug `ask.py` was restructured to end.
   `ask.forget_models()` clears the record at the start of every audit.
9. **Nothing reaches stdout but the protocol.** Under stdio, stdout *is* the
   wire. The pipeline prints through `cli.console`; for a job that console is
   swapped for a sink whose lines become the job's log. `papertrace mcp` prints
   nothing before serving, and the SDK's stdio transport diverts fd 1 to stderr
   while it serves, as a second line of defence.
10. **No path is served that the tool did not write.** References are listed
    without `pdf_path`, as in the viewer. An evidence image is read only when it
    resolves inside `<case>/out/` and is a `.png` — a `results.json` edited to
    point at `../../somewhere` is refused, not followed.
11. **A case is not a result until its audit has ended.** A rerun can rewrite
    the manifest and die before `check`, leaving one run's references beside
    another's verdicts. The job writes `<case>/mcp_audit.json` — `running`
    before the pipeline starts, the outcome when it ends — and read tools
    refuse a case whose last MCP audit failed or never finished. The job
    refreshes the record every 15 s, so `running` is measured rather than
    assumed: fresh, the audit is live in some server process — perhaps another
    host's own `papertrace mcp` — and the folder is neither read nor audited
    again; unrefreshed for 60 s, the server stopped mid-run and the audit is
    `interrupted`. The record on disk decides, not a server's memory: a failed
    audit is superseded by a `results.json` written after it, and one run
    without the scout by a `scout.json` written after it, so a case completed
    since by other means reads again.

## Long runs: a job, not a blocking call

An audit takes minutes — the demo took 95 s with the layout models cached, and
the first docling run downloads about 500 MB. A client built on the TypeScript
SDK times a request out at **60 s** (`DEFAULT_REQUEST_TIMEOUT_MSEC = 60000` in
`@modelcontextprotocol/sdk` 1.30.1; resetting on progress is opt-in). A blocking
`run_audit` would fail there while the audit carried on unseen, still spending.

So `start_audit` validates what it can synchronously — the manuscript exists,
`claude` is on this server's `PATH`, a contact email resolves, the case folder
is not another paper's — and then returns at once. `audit_status` reports
`running`, `finished` or `failed` with the log tail, and waits up to
`wait_seconds` (at most 50, under that 60 s) for a running job to end.

**One audit at a time per server process.** The pipeline's console, `ask`'s
per-site model record and `sys.stdin` are process-global; two audits sharing
them would interleave logs and misattribute models. A second `start_audit` is
refused, naming the audit in progress. Across processes the record is the
guard: a fresh `running` record is another server's live audit, and
`start_audit` refuses that folder too.

**Read tools refuse a case whose audit is running, failed or never finished.**
The files on disk then belong to an earlier run, are half-written, or mix the
two, and presenting them as a result would be the silent failure this codebase
exists to refuse.

`audit_status` never returns counts. A count without its disclosures is exactly
what rule 2 forbids, so a finished job points at `audit_summary`.

## What is rejected, and why

- **A subprocess per audit.** The cleanest isolation — its own stdout, its own
  globals, killable — and a second process-spawning file in `src/`, which
  `tests/test_ask.py` exists to refuse.
- **A blocking `run_audit`.** Fails on 60 s clients and keeps spending after the
  client has given up.
- **MCP elicitation to settle a disputed label.** The gate in
  `_escalate_disputed` is a *person* shown the disagreement. An MCP client may be
  a model answering on its own behalf, and `numbering_chosen_by: "user"` would
  then record a choice nobody made.
- **One tool per stage** (`ingest`, `extract`, `refs`, `check`, `highlight`,
  `report`). The `/review` skill already drives stages one by one in Claude
  Code; for other hosts one audit plus inspection covers the use, and every
  extra spending tool is another cost surface. Cherry-picking claims — `check`
  on an array of ids, which `select_claims` already takes — is the natural next
  tool, and is left for its own change.
- **MCP resources for `report.md` and `results.json`.** Tools are supported by
  every host; resources are surfaced unevenly. Deferred.
- **MCP-level logging (`ctx.info`).** Deprecated by the 2026-07-28 protocol
  revision (SEP-2577). The job log is returned by `audit_status` instead.
- **Streamable HTTP.** A case folder stays on the machine that made it, by
  design. stdio only.
- **An output schema only in `tools/list`.** The SDK publishes one generated
  from each tool's `TypedDict`, but `schemas/` is where this repository keeps
  its contracts (gate 2), so `schemas/mcp_tools.schema.json` holds them too —
  every tool output is validated against it, and each definition's keys are
  held equal to its `TypedDict`'s, so the two cannot drift apart silently.
  `schemas/mcp_audit.schema.json` covers the one file the server writes.

## Deliberate omissions

- `--png` is not exposed: it needs playwright and a browser, and the images the
  server serves are the evidence crops, which need neither.
- A running audit cannot be cancelled. A thread cannot be stopped safely
  mid-write; the CLI's Ctrl-C carries the same exposure. If the host stops the
  server, no further stage and no further model call starts — but a `claude -p`
  call already in flight runs to its end, bounded by `ask.py`'s timeout — and
  the record left saying `running` marks the case incomplete.
- A job's log is not persisted; its outcome is, in `mcp_audit.json`, and an
  earlier server's audit reports `log: null` rather than an empty log.
- Liveness across processes is a heartbeat window, not a lock. A server whose
  refreshes stop for 60 s — a machine that slept — reads as stopped: its case
  is refused rather than read, the safe side, but a second audit started in
  that window is not refused. The CLI takes no part in the record at all.
- A CLI run that fails midway writes no record, so the server reads that case
  as the reports would.

## Testing

- Every tool through the SDK's in-memory `Client(build_server())`: offline, no
  model, fixtures built from the dataclasses, PNGs drawn with pymupdf.
- The job is exercised with `cli._run_pipeline` faked; the fake asserts what
  the job guarantees from inside it (nobody to ask, no stale model, the console
  captured).
- One real stdio round trip through a subprocess, and one test that
  `papertrace mcp` writes nothing to stdout when its stdin closes.
- Parity: every run-level and claim-level disclosure token appears in the MCP
  output for the same fixtures `test_disclosure_parity.py` uses.
- Packaging: `mcp` is in `[mcp]`, in `[full]`, and in `[dev]` — so CI runs these
  tests rather than skipping them.
