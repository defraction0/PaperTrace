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

```bash
# 1) build the paper PDF
playwright install chromium     # once, if you haven't already
python examples/demo/make_manuscript.py

# 2) run the pipeline — the resolver fetches the open-access references live
export PAPERTRACE_EMAIL="you@example.org"   # Unpaywall requires a contact
papertrace run examples/demo/demo_manuscript.pdf -c demo_case

# 3) read the results
open demo_case/out/report.md            # or report_editor.html / report_terminal.html
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
