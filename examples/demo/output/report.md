# Fact-Check Report

Checker: `claude -p · claude-opus-5` · PaperTrace · 2026-09-19
Manuscript: `demo_manuscript.pdf` · Sources: `3 / 4` cited references available

**Claims:** 4 | ✅ **Supported:** 1 | ⚠️ **Partial:** 0 | ❌ **Contradicted:** 2 | ⊘ **Not retrieved:** 1
**Citation coverage:** 5/5 citation occurrences reached by an extracted claim, across 4 labels — 0 unaddressed, 0 uncertain. Coverage counts places an extracted claim *reached*, not sources that were read.

> Ingest `converter: docling 2.118.1` — layout-aware.

> ⚠️ How to read that figure: attribution is the context the extractor named. Extraction is shown every place this paper cites something and returns which of them each claim came from, so the pointer is no longer a text comparison — but naming it is still a model step, and a claim can be placed on the wrong sentence. A claim that names no place at all leaves that reference's remaining places counted as NOT covered, never as covered, so the figure understates coverage there. And detection still reads bracketed numeric markers only — a citation style it cannot see contributes no occurrences at all, which makes this ratio look better than reality, not worse.

> On this run two readings of the reference list agree on every cited label (parsed, pymupdf). That is corroboration, not confirmation, and it does not make the numbering verified: these are readings of one printed page, so a reference its layout destroyed is one they can all have missed in the same way. Any label they disagreed about is listed separately, and no verdict was printed for it.

> Here the reference list was also read by a model (claude-opus-5), shown two extractions of the same printed bibliography and asked what numbered list it carries; every value it proposed was found in the printed text. A model agreeing with a parse is a second reading, not confirmation: it read the same document, so a reference the layout destroyed is one it may also have missed.

---

## Claim 1: "A deep learning model on frontal chest radiographs detected type 2 diabetes with an external validation AUC of 0.94."

**Status:** ❌ CONTRADICTED
**Location:** Background · cites [1]

> Deep learning applied to frontal chest radiographs detected type 2 diabetes with an external validation AUC of 0.94 [1].

<br>

### ❌ CONTRADICTED — `pyrros-2023` (cited as [1])

- **Source:** Page 4 `(block_0050)` 
> The source reports external validation at a distinct institution with a ROC AUC of 0.77, not 0.94; the highest AUC reported anywhere is 0.89 (BMI<25 subgroup) in the internal prospective cohort.

![evidence](evidence/claim_01_pyrros-2023_p4.png)
*red box = matched text*
## Claim 3: "UK Biobank recruited approximately 500,000 adults aged 40-69 years, and its imaging enhancement targets 100,000 participants."

**Status:** ✅ SUPPORTED — *most adverse of 2 cited sources*
**Location:** Population imaging · cites [2, 3]

> Dedicated cohorts complement such opportunistic reuse: the UK Biobank cohort profile describes recruitment of approximately 500,000 adults aged 40-69 years [2], and its imaging enhancement targets 100,000 participants [3].

> **2 cited sources checked for this claim: 2 fully support it.**

<br>

### ✅ SUPPORTED — `sudlow-2015` (cited as [2])

- **Source:** Page 1 `(block_0018)` 
> The source states UK Biobank has "over 500,000 participants aged 40-69 years when recruited in 2006-2010", and elsewhere (block_0038, p.3, and Table 3, block_0054, p.5) specifies multimodal imaging in a subset of 100,000 participants.

![evidence](evidence/claim_03_sudlow-2015_p1.png)
*⚠️ no anchor phrase could be boxed — none was found inside the region this crop shows, so the crop is shown for context only.*
<br>

### ✅ SUPPORTED — `littlejohns-2020` (cited as [3])

- **Source:** Page 1 `(block_0008)` 
> The source states UK Biobank is "a population-based cohort of half a million participants aged 40-69 years" and that the imaging enhancement aims to image 100,000 of the existing 500,000 participants.

![evidence](evidence/claim_03_littlejohns-2020_p1.png)
*red box = matched text*
## Claim 4: "Nearly one in five confirmed UK Biobank participants had not attended an imaging assessment centre."

**Status:** ❌ CONTRADICTED
**Location:** Population imaging · cites [3]

> Attendance logistics remain a bottleneck, however - nearly one in five confirmed participants had not attended an imaging assessment centre [3].

<br>

### ❌ CONTRADICTED — `littlejohns-2020` (cited as [3])

- **Source:** Page 3 `(block_0023)` 
> The source reports that of those eligible who booked an appointment, "97% have attended an imaging assessment centre" — i.e. about 3%, not nearly 20%, had not attended.

![evidence](evidence/claim_04_littlejohns-2020_p3.png)
*the passage opens here and crosses a column or page break — 2 images below show all of it*

![evidence, continued](evidence/claim_04_littlejohns-2020_p3_cont2.png)
*red box = matched text*
---

## Not verified — source not retrieved, or check failed (1 of 4)

Either the cited PDF could not be obtained, or the source was available but
the check step failed (see each note).
Reported as such — never filled in from memory.

- **Background** (1):
  - ⊘ NOT RETRIEVED · [4] Regulatory clearances of AI systems for clinical imaging are accelerating. — *cited source not available (paywalled)*

## Assertions without citation (1) — your judgement required

Statements that would normally carry a reference but don't. Not verified —
flagged for you to weigh.

- **[U1]** Routine imaging archives are among the largest untapped screening resources in medicine. *(Background)*

## Literature scout — what the reference list doesn't know


> ⚠️ Scout scan incomplete: paper not identified in Europe PMC — pass --doi to pin it (title heuristics can miss)

*Search-based (Europe PMC) — absence from these lists
proves nothing, and presence is a candidate for your judgement, not an accusation.*

## Retrieval manifest

| # | Reference | Status | Via | Note |
|---|-----------|--------|-----|------|
| 1 | Pyrros A, Borstelmann SM, Mantravadi R, et al (2023) Opportunistic detection of … | retrieved | unpaywall | open-access copy via Unpaywall · title check: 11/11 reference tokens on its first page |
| 2 | Sudlow C, Gallacher J, Allen N, et al (2015) UK Biobank: An Open Access Resource… | retrieved | unpaywall | open-access copy via Unpaywall · title check: 12/12 reference tokens on its first page |
| 3 | Littlejohns TJ, Holliday J, Gibson LM, et al (2020) The UK Biobank imaging enhan… | retrieved | unpaywall | open-access copy via Unpaywall · title check: 12/12 reference tokens on its first page |
| 4 | Rajpurkar P, Lungren MP (2023) The Current and Future State of AI Interpretation… | paywalled | — | DOI resolved but no legal open-access copy found |

---

*Generated by [PaperTrace](https://github.com/defraction0/PaperTrace).
Evidence images are pages of the cited sources. The judgement is yours —
verify the flagged items before you rely on them.*