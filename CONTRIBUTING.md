# Contributing to PaperTrace

Thanks for considering a contribution — issues, paper-format reports and PRs
are all welcome.

## Dev setup

```bash
git clone https://github.com/defraction0/PaperTrace && cd PaperTrace
pip install -e ".[dev,png]"
pytest          # fixtures only — no network, no LLM, CI-safe
ruff check src tests scripts
```

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
