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
   the parity test is extended to it: every token reaches it.
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
refused, naming the audit in progress.

**Read tools refuse a case whose audit is running.** The `results.json` on disk
then belongs to the previous run, or is half-written, and presenting it as this
run's would be the silent failure this codebase exists to refuse.

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
- **A hand-written schema in `schemas/`.** The output schema is generated from
  the tools' `TypedDict` return types and published by `tools/list`; a copy
  would be a second contract to drift. No case-folder file gains a field, so
  gate 2 is untouched.

## Deliberate omissions

- `--png` is not exposed: it needs playwright and a browser, and the images the
  server serves are the evidence crops, which need neither.
- A running audit cannot be cancelled. A thread cannot be stopped safely
  mid-write; the CLI's Ctrl-C carries the same exposure. If the host stops the
  server, the job's daemon thread stops with it and the case folder is
  re-runnable.
- Job state is not persisted across server restarts. `audit_status` says so
  ("no audit of this case was started by this server process") rather than
  guessing from the files on disk.

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
