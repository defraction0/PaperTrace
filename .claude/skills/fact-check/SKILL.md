---
name: fact-check
description: Verify one claim of a paper against the actual text of its cited source — locate the passage, judge support, record a page-level anchor, and produce a red-box evidence crop. Use for every citation-backed claim during a paper audit or manuscript review, or standalone when the user asks to check a specific claim against a source.
---

# Fact-check one claim against its cited source

Input: a claim (verbatim or tightly paraphrased), where it appears in the
manuscript, and the citation label(s) it carries. Context: a `case/` folder
with `refs_manifest.json` and per-source ingests under `case/ingest/<slug>/`.

## Procedure

1. **Locate the source.** Look up the citation label in `refs_manifest.json`.
   - Status `retrieved`/`provided` → proceed.
   - Anything else → verdict **`not_retrieved`**, note the manifest reason,
     stop. Do not reconstruct the source from memory — that is the one
     forbidden move. Your training knowledge of a paper is NOT evidence; only
     the PDF on disk is.

2. **Ingest on demand.** If `case/ingest/<slug>/` doesn't exist yet:
   `papertrace ingest <pdf> -o case/ingest/<slug>`

3. **Read for the claim.** Search the source's `clean.md` for the claim's
   subject (numbers, named methods, populations). Read the matching blocks in
   `annotated.md` — they carry `block_NNNN, page N` markers. Read enough
   context to judge fairly; a sentence ripped from a limitations paragraph is
   not support.

4. **Verdict** — exactly one of:
   - `supported` — the source states what the manuscript attributes to it.
   - `partial` — kernel is there but scope/strength/object differs (wrong
     paper cited for a true fact, mechanism inferred not shown, grouped refs
     where only some carry the claim). Say precisely what diverges.
   - `contradicted` — the source says otherwise. Quote the source's actual
     figure/statement in the note.
   - `not_retrieved` — no source on disk (from step 1).

5. **Anchor it.** Record: source slug, page, the `block_NNNN` id you read,
   and 1–3 short *verbatim* phrases from that block (`anchor_phrases`) that a
   text search will find — numbers and distinctive strings beat common words.
   Beware of a phrase that also occurs elsewhere on the page (a CI bound, a
   running head): pick phrases unique within the block.

6. **Crop the evidence.**
   `papertrace highlight case/ --claim <id>` (or the library call) →
   writes `case/out/evidence/claim_<id>_<slug>_p<page>.png` with red boxes on
   the anchor phrases. If zero boxes were drawn, your anchor phrases don't
   match the page text — fix them (ligatures, hyphenation) rather than
   shipping an unmarked crop.

7. **Record.** Append to `case/out/results.json` (schema:
   `schemas/results.schema.json`): id, claim, location, refs, verdict, note
   (≤2 sentences, the *why*), anchors, evidence_image.

## Judgement notes

- A claim citing several references is judged against **all** of them;
  support from two refs doesn't cover a claim attributed to five.
- Numbers must match the source's numbers, not be merely compatible.
  "91%" against a source saying 90.9% → supported (state the rounding);
  against 76% → contradicted.
- Secondary citations ("A, citing B") are judged against A — note when A
  itself only cites B for the figure.
- When your reading and the manuscript's could both be defensible, verdict
  `partial` and surface the disagreement to the user rather than ruling.
