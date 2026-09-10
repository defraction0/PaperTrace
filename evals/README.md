# `evals/` — measuring the checker, not the plumbing

`tests/` proves the code works. This measures whether the **model's verdicts
are right**, which is a different question and needs human labels.

Read [`DESIGN.md`](DESIGN.md) before trusting any number out of here. The short
version: what ships today is a *demonstration* of five cases from a synthetic
manuscript, labelled by the same person who wrote the prompts. It shows the
harness runs. It measures nothing about real manuscripts.

## Score an existing run — offline, free

```bash
python evals/runners/score_only.py \
    --gold evals/gold/demo_v1.gold.json \
    --results evals/gold/demo_v1.observed.json \
    --case-dir demo_case \
    --out evals/runs
```

Writes `eval.json` (machine-readable) and `EVAL.md` (readable) into a run
directory. No model call, no network.

`--case-dir <case>` points the **frozen-source check** at that case's
`refs_manifest.json`. A gold `not_retrieved` case only tests retrieval for as
long as the DOI stays paywalled, so the runner compares each source's
`expected_ref_status` against what the manifest actually observed. A source
whose status **changed** invalidates the cases resting on it; they are excluded
from the judgement metrics, kept in the record, and listed in their own report
section.

**Omit `--case-dir` and the check still runs** — it reports every expected
source as `not verified` and says so on the report's face. It does not exclude
anything, because unverifiable is not the same as wrong. Earlier this flag
gated the check entirely, and this README's own documented invocation omitted
it, so the freeze check never ran and its silence was indistinguishable from a
pass.

Compare repeated runs:

```bash
python evals/runners/score_only.py --agreement evals/runs/<a> evals/runs/<b> evals/runs/<c>
```

Reports the **complete-case** figure (only cases every run produced — a
different population, not a bound in either direction) and the **penalized**
figure (every case seen in any run, with the gaps charged to the model, which
is a genuine lower bound) side by side, and names which cases each run omitted.
Cases that were never eligible for scoring do not vote. Runs that differ in
gold set, prompt fingerprint or ingest converter are refused outright rather
than averaged.

## Run a live evaluation — costs money

```bash
python evals/runners/run_eval.py --gold evals/gold/demo_v1.gold.json --runs 3
```

Prompts for confirmation with the call count, fetches sources over the network,
and defaults its case directory to a fresh path under `$TMPDIR` — outside this
repo, so `claude -p` cannot pick up the project's own skills and settings as
ambient context. It refuses to start under `CI`.

## Layout

| Path | What it is |
|---|---|
| `DESIGN.md` | the protocol: unit, pairing, metrics, provenance, gold policy |
| `PROPOSAL.md` | draft text for the paired-benchmark issue |
| `align.py` | gold ↔ prediction matching, global score-sorted assignment |
| `tool_coverage.py` | shape-defensive reading of the tool's own coverage audit |
| `eligibility.py` | which cases may be scored, and if not, why |
| `metrics.py` | pure metric arithmetic, no I/O |
| `agreement.py` | repeated-run stability |
| `provenance.py` | what produced a run — model, prompt hash, commit |
| `scoring.py` | orchestration → the eval record |
| `eval_report.py` + `templates/` | the Markdown artefact and its guardrails |
| `gold/` | frozen gold sets and the transcribed demo run |
| `runners/` | `score_only.py` (offline) · `run_eval.py` (live, paid) |
| `tests/` | deterministic tests of the arithmetic — these **do** run in CI |
| `runs/` | output; gitignored except for `.gitkeep` |

## Adding a gold case

Validate against
[`schemas/eval_gold.schema.json`](../schemas/eval_gold.schema.json); the tests
also check that every `gold_anchor_phrases` entry is a substring of its
`decisive_passage`, which catches authoring typos that would otherwise look
like model errors.

Two rules worth repeating:

- **`unchecked` is not a legal gold verdict.** It is a harness error, never a
  correct answer.
- **If labellers cannot agree, set `gold_verdict: null` and keep the case.**
  Deleting it makes the set easier and inflates every score. The case is
  excluded from every denominator by `eligibility.py`, stays in `per_case`, and
  is listed under *Excluded cases* on the report.
