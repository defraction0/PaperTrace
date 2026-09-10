# Draft issue: a paired evaluation benchmark for the claim checker

**Status: considered and declined, 2026-09-05 — see
[`docs/adr/0001-no-gold-benchmark.md`](../docs/adr/0001-no-gold-benchmark.md).**
Not posted as an issue. The blocker is not the design below but its labelling
precondition: `DESIGN.md` requires ≥ 2 labellers who did not write the prompts,
and there is one maintainer, who wrote them. Kept in the repository unchanged
because it is the plan that would be executed if that ever changes — the text
from here down is the original draft and still reflects what should be built.

---

## Title

Build a paired evaluation benchmark for the claim checker

## Body

PaperTrace's software tests prove its plumbing works — ingest, retrieval,
coverage arithmetic, crop placement, report rendering, all offline. They say
nothing about the only question users actually ask: **are the verdicts right?**

That is an empirical property of a model, and it needs measurement against human
labels. The protocol is specified in [`evals/DESIGN.md`](DESIGN.md) and an
offline scoring harness already exists in [`evals/`](.). What is missing is the
data.

### Why *paired* cases specifically

The shipped demonstration set has five cases from a synthetic manuscript whose
defects were planted by this project's maintainer. Every planted defect exists
only in its **altered** form. There is no faithful sibling — no sentence that
states the same fact from the same source *correctly*.

That gap matters more than the set's size. Without pairs there is no way to
distinguish a checker that **reads the source** from one that **pattern-matches
plausibility**: a model with a bias toward "contradicted" scores well on a set
made entirely of contradictions. A pair makes that bias visible immediately, via
`pair_collapse_rate` — the fraction of pairs where both members received the
same verdict.

### Proposed shape

A generator producing a manuscript that cites real open-access papers and, for
each fact, emits **both** a faithful and an altered sentence citing the same
reference — for example "an external validation AUC of 0.77" alongside "an
external validation AUC of 0.94", both citing the same paper.

Alteration kinds are already enumerated in the gold schema:
`numeric_substitution`, `magnitude_inflation`, `population_swap`,
`direction_flip`, `strength_upgrade`, `attribution_swap`.

### Acceptance criteria

- [ ] ≥ 40 cases, ≥ 15 of them in faithful/altered pairs.
- [ ] All four gold verdicts represented, including **`partial`** — the current
      set has none, and that boundary dominates macro-F1.
- [ ] At least three `not_retrieved` cases resting on genuinely paywalled DOIs,
      each with `expected_ref_status` recorded so availability drift invalidates
      them instead of silently corrupting `missed_gap_rate`.
- [ ] At least three multi-reference claims, labelled honestly: batch mode
      judges against the first available source only, so the tool structurally
      cannot resolve these.
- [ ] Every case validates against `schemas/eval_gold.schema.json`, and every
      `gold_anchor_phrases` entry is a substring of its `decisive_passage`.
- [ ] Labelled by **≥ 2 people who did not write the prompts**, with initial
      labels and adjudication recorded per case, and inter-labeller agreement
      reported as a **ceiling** on the achievable score.
- [ ] Unresolvable cases kept with `gold_verdict: null`, not deleted.
- [ ] Sources are open-access and redistributable, or retrievable by DOI at run
      time — no publisher PDFs in the repo.
- [ ] `kind: "benchmark"` set only once the labelling policy in `DESIGN.md` is
      genuinely satisfied. Until then it stays `"demonstration"`.

### Open questions — please argue with these

1. **Synthetic or real manuscripts?** Synthetic gives clean labels and no
   copyright problem, but the prose is unrepresentative and the tool's authors
   constructed it. Real published papers are representative but their claims are
   ambiguous and labelling them is slow. A mix, and in what ratio?
2. **Is pair collapse the right primary signal**, or does scoring both members
   of a pair double-count a single underlying judgement?
3. **The supported/partial rubric** in `DESIGN.md` is a first draft. Does it
   survive contact with real claims, or does it need the ordinal treatment
   (weighted κ, partial credit for adjacent classes)?
4. **How should figure-only evidence be labelled**, given that cited sources are
   ingested as flat text in batch mode and their figures are invisible to the
   judge?
5. **Alignment risk.** Gold cases are matched to predictions by fuzzy text
   similarity, so aligner errors are indistinguishable from model errors. Is
   human-authored `match_keys` enough, or should gold claims be verbatim
   manuscript sentences with the aligner made strict?

### Explicitly out of scope

Establishing clinical or research validity. A first benchmark measures whether
this tool does what it says on a defined set of cases. Nothing more follows from
it, and the report is built to keep saying so.
