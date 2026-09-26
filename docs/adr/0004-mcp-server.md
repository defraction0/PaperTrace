# ADR 0004 — An MCP server, in-process, that can never ask

- **Status:** accepted
- **Date:** 2026-09-26
- **Related:** ADR [0003](0003-llm-reference-list.md), `src/papertrace/mcp_server.py`,
  `src/papertrace/ask.py`, `docs/superpowers/specs/2026-09-26-mcp-server-design.md`

## Context

The roadmap asked for an MCP server: PaperTrace driven as a tool from any
MCP-capable host, not only from Claude Code through a shell. Two things make
that more than a wrapper here.

The rules this codebase keeps were written for readers of a file and for a
process that lives for one command. An MCP server has a new reader — a model
reading JSON — and a new process shape, one that outlives many audits.
`ask._MODELS` was correct only because a process ran one command.

And an audit takes minutes, while a host built on the TypeScript SDK times a
request out at 60 s by default.

## Decision

**`papertrace mcp` serves nine tools over stdio, behind an optional `[mcp]`
extra. It runs the pipeline in-process on one background thread at a time, and
the thread has nobody to ask.**

1. **In-process, not a subprocess.** `ask.py` remains the only file in `src/`
   that starts a process, as `tests/test_ask.py` asserts. The cost is that the
   server must manage process-global state itself: the pipeline's console is
   swapped for a capture sink, and `ask.forget_models()` clears the model record
   at the start of every audit.
2. **One audit at a time.** The console, the model record and `sys.stdin` are
   process-global. A second `start_audit` is refused, not queued, and read tools
   refuse a case whose audit is still running, because the files on disk are
   then another run's.
3. **Nobody to ask.** The job runs with an empty `sys.stdin`, so the interactive
   escalation of a disputed reference label is unreachable and the label is
   withheld with `numbering_chosen_by: "default"`. ADR 0003's gate is a person
   shown the disagreement; an MCP client can be a model answering for itself,
   so elicitation is not offered as a substitute.
4. **A job, not a blocking call.** `start_audit` returns at once;
   `audit_status` long-polls for at most 50 s, under the 60 s default.
5. **The same disclosures, serialised the viewer's way.** Nothing is
   re-derived. The parity suite asserts every token reaches the MCP output.

## Consequences

- A host that is not Claude Code can run and read an audit, and see the
  evidence crops as images.
- A disputed label cannot be settled from an MCP host. That is deliberate;
  `papertrace refs` in a terminal settles it.
- A running audit cannot be cancelled from the host. Stopping the server starts
  no further stage or model call — a `claude -p` call already in flight runs to
  its end — and the `mcp_audit.json` it leaves saying `running` keeps the half-
  written case from being read as a result until an audit there finishes.
- Any future process-global state in the pipeline must be reset per audit, or
  it will leak from one audit into the next. `CLAUDE.md` says so.
