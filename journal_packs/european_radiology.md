# Journal pack — European Radiology

Extends `prompts/review_core.md` for *European Radiology* (Springer/ESR).
Based on the journal's reviewer template and AI/Radiomics reviewing guide.
Single-blinded: reviewer anonymous to authors.

## Journal-specific structure checks

- **Abstract** ≤250 words, sections Background/Objectives · Materials and
  Methods · Results · Conclusions. Count the words.
- **Keywords**: 3–5, MeSH-compliant (spot-check via MeSH on Demand; flag only
  egregious misses).
- **Key Points**: exactly three — Question (20–25 words), Findings (20–25
  words), Clinical Relevance Statement (≤40 words). Count each. No
  speculation in key points.
- **Abbreviations**: list present; nothing used <2× remains listed; each
  defined at first use. CT/MRI/US need listing but no definition.
- Structure follows the three-paragraph introduction and IMRaD conventions in
  the core file.

## Reviewer form fields and decision rules

Produce a value for each; if genuinely borderline, propose a default and ask.

| Field | Options | Rule of thumb |
|---|---|---|
| Originality | (Partly) novel / Already known / Does not add | Any substantive contribution (method, cohort, application, question) → novel. A found-in-search paper already reporting the finding pushes down — surface it. |
| Importance | High / Medium / Low | High = unmet need, could change practice/guidelines. Medium = incremental in a studied area. |
| Substantial methodological errors? | Yes / No | Yes if any: unjustified n with negative/borderline result; inadequate reference standard; wrong test; Methods↔Results mismatch; unaddressed spectrum/verification bias; prediction model without internal validation or calibration; data leakage; missing CIs on primary outcome; p=0.000. |
| Language editing? | No–Minor / Yes-major | Major only if the science risks being misunderstood. Never line-edit. |
| Statistics expert needed? | Yes / No | Yes for mixed models, competing risks, Bayesian, propensity methods, unusual tests, or your own uncertainty. When in doubt: Yes. |
| AI/Radiomics? | Yes / No | Yes for model development/validation or applied AI products. Radiomics = engineered feature extraction (GLCM etc.); simple biomarkers don't count. Plain regression baselines don't make a paper "AI". |
| Reference standard reliable? | N/A / Ideal / Acceptable / Unreliable | Ideal = independent objective ground truth (histopath, labs, adjudicated outcomes). Acceptable = multi-reader consensus, validated NLP labels with error rates. Unreliable = single reader, same-modality-derived truth, uncharacterized NLP. |
| Compared to human/clinical model? | N/A / Yes / No | Yes needs a meaningful comparator AND a statistical comparison (DeLong, McNemar, NRI/IDI, bootstrap). Side-by-side numbers without a test: still Yes, but flag the gap. |
| Clinically relevant approach? | N/A / Yes / No | Does the prediction change management? Is there an available action? Prediction of something histopathology answers anyway → lean No. "Could personalize treatment" without naming the differing treatment → push back. |
| AI expert needed? | N/A / Yes / No | Yes for recent/complex architectures, generalization claims needing ML scrutiny, unusual training regimes, or your own uncertainty. |
| Review the revision? | Yes / No | **Always the user's call — ask.** |
| Transfer authorization, ORCID credit | — | User's personal preference — never decide. |

## Comments format

Comments to Author follow the core format with this section order: Abstract ·
Keywords · Key Points · Abbreviations · Title · Introduction · Materials and
Methods · Results · Discussion · Figures and Tables · Bibliography · Ethical
Considerations. Line numbers when the reviewer copy has them.

Confidential Comments to Editor: the four-point structure from the core file,
~250 words.

## House rules

- No decision recommendation in the author-facing box (the form says so
  explicitly).
- Bibliography per Springer style — spot-check only.
- If fundamental flaws make full review pointless, the template permits
  stopping after Methods/Results — ask the user before doing so.
