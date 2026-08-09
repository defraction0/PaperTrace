# Demo: a fictional paper with planted citation errors

The paper is invented and clearly watermarked as such — a two-page fictional
mini-review whose references are **real, published papers**. The errors are
planted on purpose:

| Ref | Planted error | Expected verdict |
|---|---|---|
| [1] | "external validation AUC of 0.94" — the cited paper reports **0.77** | ❌ `contradicted` |
| [3] | "nearly one in five confirmed participants had not attended" — the cited flow chart shows **3%** not yet attended | ❌ `contradicted` |
| [4] | a headline claim resting on a **paywalled** reference | ⊘ `not_retrieved` |
| — | "Routine imaging archives are among the largest untapped screening resources in medicine." — assertive, **no citation** | flagged in the uncited register |

The remaining claims citing [2] and [3] state published cohort facts
faithfully and should come back ✅ `supported`.

## Run it

Four steps, **in this order**, from the repo root — wait for each to finish
before the next. (Prerequisite: [Claude Code](https://claude.com/claude-code)
installed and logged in; the checker runs on `claude -p`.)

```bash
# 1) install — skip anything you've already done
pip install -e ".[full]"
playwright install chromium                 # one-time browser download

# 2) set your contact email (Unpaywall requires one; the run refuses to start without it)
export PAPERTRACE_EMAIL="you@example.org"   # use your real address

# 3) build the fictional demo paper — wait for "wrote … demo_manuscript.pdf"
python examples/demo/make_manuscript.py

# 4) run the audit — the resolver fetches the open-access references live
papertrace run examples/demo/demo_manuscript.pdf -c demo_case

# then read the results:
open demo_case/out/report.md                # or report_editor.html / report_terminal.html
```

No reference PDFs ship with this repo — retrieving them **is** the demo.
The paywalled reference [4] stays unretrieved by design: the tool reports the
claim as unverifiable instead of guessing. That register is the point.

The literature scout hits the same honesty wall on purpose: the demo paper is
fictional, so Europe PMC doesn't know it — the scan records *"paper not
identified"* in `scout.json` instead of inventing neighbours. Run
`papertrace scout` on a case built from any real paper to see the two
registers (published-since / existed-but-uncited) fill up, or pass
`--no-scout` to skip the step.

If you already have some of the PDFs, drop them into `demo_case/sources/`
before step 2 — the resolver prefers your copies over the network.
