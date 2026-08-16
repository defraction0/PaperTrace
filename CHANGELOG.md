# Changelog

All notable changes to PaperTrace are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [0.3.0] — 2026-08-16 (beta)

First public release under the PaperTrace name.

### Added

- Full batch pipeline: ingest → refs → scout → check → highlight → report
  (`papertrace run`), plus interactive `/review` and `/fact-check` skills for
  Claude Code.
- Open-access-only reference resolution (Crossref → Unpaywall → Europe PMC →
  arXiv) with an honest per-reference manifest: `retrieved / provided /
  paywalled / mismatch / no_doi / unpublished / error`, each with a reason.
- **Title sanity check on retrieved PDFs**: a downloaded copy whose first
  page doesn't look like the cited reference is rejected as `mismatch`
  instead of being judged against the wrong text (catches mistyped DOIs in
  reference lists).
- Page-level evidence: red-box crops placed by text search on the real
  source page — including table cells and in-figure numbers with the
  layout-aware (docling) ingest.
- Deterministic citation-coverage audit ("labels 7, 12 unaddressed"), an
  uncited-assertions register, and explicit disclosure when a citation style
  isn't recognized (bare superscripts) instead of a vacuous pass.
- A failed model check surfaces as `unchecked` with the reason — never
  disguised as a retrieval gap.
- Literature scout (Europe PMC): published-since and existed-but-uncited
  candidates, clearly framed as candidates.
- One case folder per paper — pointing a different paper at a used case is
  refused, so two audits can never mix.
- Reports as markdown, dark editor-window HTML, terminal-run HTML, and
  optional PNG export (`--png`, playwright).
- Committed demo: a fictional mini-review with planted citation errors, its
  pre-generated report in `examples/demo/output/`, and the four-step
  sequence to reproduce it.

### Known limitations

- Claim checking requires a local [Claude Code](https://claude.com/claude-code)
  login (`claude -p`); no API-key path yet.
- Bare-superscript citation styles (Nature-family layouts) are not parsed by
  the coverage audit — the report discloses this instead of auditing.
- The scout uses Europe PMC only; absence from its lists proves nothing.
- Verdict wording can vary slightly between runs of the model checker.

### Pre-history

Versions 0.1–0.2 were developed under the working name *ManuscriptAgent*
(manuscript-review focus). 0.3.0 reframes the tool to post-publication paper
auditing: published papers by design, retrieval gaps as first-class results.

[0.3.0]: https://github.com/defraction0/PaperTrace/releases/tag/v0.3.0
