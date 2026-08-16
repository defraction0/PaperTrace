#!/usr/bin/env python3
"""Generate the GitHub social-preview card (1280x640).

Composes a self-contained HTML card — logo, the one-line question the tool
answers, and a single claim-vs-evidence example — and screenshots it with
playwright at exactly GitHub's recommended size.

Output: docs/social_preview.png (upload manually: repo Settings → Social preview).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "templates" / "assets"
LOGO = ROOT / "assets" / "logo.png"
OUT_HTML = ROOT / "docs" / "social_preview.html"
OUT_PNG = ROOT / "docs" / "social_preview.png"

# palette — same slate/paper/red family as scripts/make_logo.py
BG = "#1b2733"
BG_HI = "#2c3e50"
PAPER = "#f5f2ea"
INK = "#8a94a6"
RED = "#e5484d"
GREEN = "#3dd68c"

HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@font-face {{ font-family: Sans; src: url("{(FONTS / 'InstrumentSans-Regular.ttf').as_uri()}"); }}
@font-face {{ font-family: Sans; font-weight: bold; src: url("{(FONTS / 'InstrumentSans-Bold.ttf').as_uri()}"); }}
@font-face {{ font-family: Mono; src: url("{(FONTS / 'JetBrainsMono-Regular.ttf').as_uri()}"); }}
@font-face {{ font-family: Mono; font-weight: bold; src: url("{(FONTS / 'JetBrainsMono-Bold.ttf').as_uri()}"); }}
* {{ margin: 0; box-sizing: border-box; }}
body {{
  width: 1280px; height: 640px; overflow: hidden;
  background: linear-gradient(135deg, {BG_HI} 0%, {BG} 55%);
  font-family: Sans, sans-serif; color: {PAPER};
  display: flex; align-items: center; gap: 56px; padding: 0 72px;
}}
.left {{ flex: 0 0 460px; }}
.left img {{ width: 148px; image-rendering: pixelated; margin-bottom: 28px; }}
h1 {{ font-family: Mono, monospace; font-size: 54px; letter-spacing: -1px; }}
.q {{ font-size: 33px; font-weight: bold; line-height: 1.25; margin-top: 18px; }}
.sub {{ font-size: 21px; color: {INK}; margin-top: 18px; line-height: 1.45; }}
.card {{
  flex: 1; background: #22303f; border: 1px solid #3a4a5c; border-radius: 14px;
  padding: 34px 38px; font-size: 22px; line-height: 1.5;
  box-shadow: 0 18px 60px rgba(0,0,0,.35);
}}
.lbl {{ font-family: Mono, monospace; font-size: 16px; color: {INK};
        text-transform: uppercase; letter-spacing: 2px; }}
.claim {{ margin: 10px 0 24px; }}
.claim b {{ color: {RED}; }}
.chip {{ display: inline-block; font-family: Mono, monospace; font-weight: bold;
         font-size: 19px; color: {RED}; border: 2px solid {RED}; border-radius: 7px;
         padding: 5px 14px; margin-bottom: 24px; }}
.chip.ok {{ color: {GREEN}; border-color: {GREEN}; }}
.evidence {{
  background: {PAPER}; color: #2a2a2a; border: 3px solid {RED}; border-radius: 4px;
  font-size: 21px; padding: 18px 22px; line-height: 1.45;
}}
.evidence b {{ background: rgba(229,72,77,.18); outline: 2px solid {RED}; padding: 1px 4px; }}
.src {{ font-family: Mono, monospace; font-size: 16px; color: {INK}; margin-top: 14px; }}
</style></head><body>
  <div class="left">
    <img src="{LOGO.as_uri()}" alt="">
    <h1>PaperTrace</h1>
    <div class="q">Do the citations support the claim?</div>
    <div class="sub">Claims checked against the cited pages ·
    page-level evidence · gaps reported, never guessed</div>
  </div>
  <div class="card">
    <div class="lbl">the paper says</div>
    <div class="claim">“…reported an AUC of <b>0.94</b> for opportunistic
    diabetes detection from chest radiographs [1].”</div>
    <span class="chip">✗ CONTRADICTED</span>
    <div class="lbl">the cited source actually says</div>
    <div class="evidence" style="margin-top:10px">“External validation at a
    distinct institution yielded a <b>ROC AUC of 0.77</b>, with 5% of patients
    subsequently diagnosed with T2D.”</div>
    <div class="src">pyrros-2023 · Nat Commun 14:4039 · retrieved via Unpaywall</div>
  </div>
</body></html>
"""


def main() -> None:
    OUT_HTML.parent.mkdir(exist_ok=True)
    OUT_HTML.write_text(HTML)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 640}, device_scale_factor=1)
        page.goto(OUT_HTML.resolve().as_uri())
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT_PNG))
        browser.close()
    OUT_HTML.unlink()  # the PNG is the artifact; the HTML is scaffolding
    print(f"wrote {OUT_PNG} (1280x640) — upload: repo Settings → Social preview")


if __name__ == "__main__":
    main()
