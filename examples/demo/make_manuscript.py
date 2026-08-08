#!/usr/bin/env python3
"""Build the synthetic demo paper PDF.

The paper is FICTIONAL and clearly labeled as such — a mini-review that
exists only so the tool has something realistic to check. Its references are
real, published papers, and the citation errors are planted on purpose:

  [1] "external validation AUC of 0.94" — the cited paper reports 0.77   → contradicted
  [3] "nearly one in five confirmed participants had not attended" —
      the cited flow chart shows 3% not yet attended                     → contradicted
  [4] a paywalled reference carrying a headline claim                    → not retrievable
  plus one assertive sentence with no citation at all                    → uncited register

Run `papertrace run demo_manuscript.pdf` afterwards and watch it catch
all of them.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent

HTML = """<!doctype html><meta charset="utf-8">
<style>
  @page { size: A4; margin: 22mm 20mm; }
  body { font-family: Georgia, 'Times New Roman', serif; font-size: 10.5pt;
         line-height: 1.45; color: #111; }
  .banner { border: 2px solid #b00; color: #b00; text-align: center;
            font-family: Arial, sans-serif; font-weight: bold; font-size: 9pt;
            padding: 4px; margin-bottom: 14px; letter-spacing: .08em; }
  h1 { font-size: 15pt; line-height: 1.25; margin: 0 0 6px; }
  .meta { font-size: 9pt; color: #444; margin-bottom: 14px; }
  h2 { font-size: 11.5pt; margin: 14px 0 4px; }
  p { margin: 0 0 7px; text-align: justify; }
  ol.refs { font-size: 9pt; padding-left: 16px; }
  ol.refs li { margin-bottom: 4px; }
</style>

<div class="banner">SYNTHETIC DEMO PAPER — FICTIONAL MINI-REVIEW, FOR TOOL DEMONSTRATION ONLY</div>

<h1>Opportunistic Disease Detection in Routine and Population Imaging:
a Fictional Mini-Review</h1>
<p class="meta">A. Placeholder, B. Example, C. Fixture · Institute for Synthetic
Examples · Correspondence: demo@example.org · <b>This mini-review is invented;
only its references are real.</b></p>

<h2>Abstract</h2>
<p>We sketch, in wholly invented prose, how images acquired for one purpose
can be reused to look for something else. Every synthesis in this document is
fabricated for demonstration purposes; only the cited references are real.</p>

<h2>Background</h2>
<p>Routine imaging archives are among the largest untapped screening resources
in medicine. Deep learning applied to frontal chest radiographs detected
type&nbsp;2 diabetes with an external validation AUC of 0.94 [1]. Regulators are
clearing AI systems for clinical imaging use at an accelerating pace [4].</p>

<h2>Population imaging</h2>
<p>Dedicated cohorts complement such opportunistic reuse: the UK Biobank
cohort profile describes recruitment of approximately 500,000 adults aged
40–69 years [2], and its imaging enhancement targets 100,000 participants
[3]. Attendance logistics remain a bottleneck, however — nearly one in five
confirmed participants had not attended an imaging assessment centre [3].</p>

<h2>Discussion</h2>
<p>This fictional mini-review demonstrates nothing about screening — it
exists so that PaperTrace has something realistic to check. Its errors are
planted: one number contradicts the cited paper, one logistics claim
contradicts the cited flow chart, one headline rests on a reference that is
not openly accessible, and one assertive sentence carries no citation at
all.</p>

<h2>References</h2>
<ol class="refs">
<li>Pyrros A, Borstelmann SM, Mantravadi R, et al (2023) Opportunistic
detection of type 2 diabetes using deep learning from frontal chest
radiographs. Nat Commun 14:4039. doi:10.1038/s41467-023-39631-x</li>
<li>Sudlow C, Gallacher J, Allen N, et al (2015) UK Biobank: An Open Access
Resource for Identifying the Causes of a Wide Range of Complex Diseases of
Middle and Old Age. PLoS Med 12:e1001779. doi:10.1371/journal.pmed.1001779</li>
<li>Littlejohns TJ, Holliday J, Gibson LM, et al (2020) The UK Biobank imaging
enhancement of 100,000 participants: rationale, data collection, management
and future directions. Nat Commun 11:2624. doi:10.1038/s41467-020-17438-4</li>
<li>Rajpurkar P, Lungren MP (2023) The Current and Future State of AI
Interpretation of Medical Images. N Engl J Med 388:1981–1990.
doi:10.1056/NEJMra2301725</li>
</ol>
"""


def main() -> None:
    out_html = HERE / "_manuscript.html"
    out_pdf = HERE / "demo_manuscript.pdf"
    out_html.write_text(HTML)

    import glob

    from playwright.sync_api import sync_playwright

    exe = None
    hits = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    if hits:
        exe = hits[-1]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page()
        page.goto(out_html.as_uri())
        page.pdf(path=str(out_pdf), format="A4", print_background=True)
        browser.close()
    out_html.unlink()
    print(f"wrote {out_pdf}")
    print("planted: [1] contradicted · [3] contradicted · [4] not retrievable · 1 uncited")


if __name__ == "__main__":
    main()
