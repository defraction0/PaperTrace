# Contributing to PaperTrace

Thanks for considering a contribution — issues, paper-format reports and PRs
are all welcome.

## Dev setup

```bash
git clone https://github.com/defraction0/PaperTrace && cd PaperTrace
pip install -e ".[dev,png]"
pytest          # fixtures only — no network, no LLM, CI-safe
ruff check src tests scripts evals
```

Or with uv, without managing a venv yourself (verified working):

```bash
uv run --with-editable . papertrace --help
```

`pytest` covers `tests/` and `evals/tests/`. The evaluation *harness* is
tested like any other code; running an actual evaluation against a live
model is a separate script that no test and no CI job invokes — see
[`evals/README.md`](evals/README.md).

Tests must stay offline: resolver tests run on `httpx.MockTransport`, PDFs
are generated in-test with pymupdf. If your change needs a model call, put
the deterministic part under test and keep the model call behind a seam.

## The lowest-friction contribution: paper-format reports

Real-world papers are the test suite this project can't ship. Reporting how
a specific journal's layout, reference list, or citation style broke a step
(template below) is the feedback that improves the tool fastest. (Journal
review packs — journal-specific reviewer-form templates — may return in the
future; watch the roadmap.)

## Reporting problems with a specific paper

Papers break parsers in creative ways — that's exactly the feedback we need.
Use the *paper-format failure* issue template and include the journal,
publisher, citation style, and which pipeline step misbehaved.

**Never attach a manuscript or source PDF to an issue.** Many papers are
paywalled and some manuscripts are confidential; the repo's `.gitignore`
refuses `*.pdf` for the same reason. Text excerpts of the failing structure
(a few reference-list lines, a mangled table) are enough.

## What to contribute

Ranked by how much they help, with where the code lives:

| Contribution | Where | What a good one looks like |
|---|---|---|
| **PDF-format regression fixture** | the `tests/test_pipeline.py` pattern — PDFs built in-test with pymupdf, no binaries committed | a real journal layout that breaks a step, reproduced in ≤30 lines |
| **Reference-resolution fixture** | `tests/test_refs.py`, on `httpx.MockTransport` | an actual resolver response shape that currently mis-parses |
| **Gold evaluation case** | `evals/gold/` against [`schemas/eval_gold.schema.json`](schemas/eval_gold.schema.json) | a claim, its source, the decisive passage, page and anchor phrases — see [`evals/DESIGN.md`](evals/DESIGN.md) |
| **New citation style** | `_LABEL_GROUP` in `src/papertrace/check.py` + a case in `tests/test_coverage.py` | the style *plus* the test proving it is audited rather than silently passed — **and** an occurrence case: two sentences citing the same label, proving the second is reported |
| **A new report disclosure** | `src/papertrace/disclosures.py`, then all three templates | the rule in one place with its own `token`, and `tests/test_disclosure_parity.py` asserting that token reaches every format. Adding a disclosure straight to a template is the drift this codebase already suffered once |
| **Another model backend** | the `_ask` seam in `src/papertrace/check.py` | a backend behind the same seam, with the model recorded in the report |

Gold cases are the highest-leverage thing right now: the evaluation set is the
piece this project most obviously lacks, and it needs labellers who did **not**
write the prompts. Two rules from the design doc are worth restating —
`unchecked` is never a legal gold verdict, and a case labellers cannot agree on
is kept with `gold_verdict: null`, not deleted.

## Code contributions

- One behavioral change per PR, with a test that fails without it.
- Honest degradation over silent failure: if a step can't do its job, it
  must say so in the report, never guess. This is the project's core rule —
  an unread source never receives a verdict.
- New reference statuses, verdicts or JSON fields need a schema update in
  [`schemas/`](schemas/) and a round-trip test.
- Match the codebase's comment style: comments state constraints the code
  can't, not narration.

## Licensing of contributions

Everything is MIT — code, prompts and skills alike (see `LICENSE`). By
contributing you agree your contribution is licensed accordingly.
