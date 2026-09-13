"""Render docs/wizard.png — the guided flow, as a terminal window.

A screenshot, not an asciinema cast: the README is also the PyPI long
description, and PyPI renders neither embedded players nor repo-relative paths.
A committed PNG behind an absolute raw URL works in both places.

Reuses `papertrace.render.html_to_png` — the same playwright path that already
produces `docs/report_terminal.png` — so this adds no dependency and inherits
the palette and fonts of the real terminal report.

    python scripts/make_wizard_shot.py

Needs `pip install -e ".[png]"` and a one-time `playwright install chromium`.

The transcript is hand-laid-out rather than captured, but every number in it
comes from `wizard.workload()` on a real paper — see the comment above `BODY`.
An illustrative screenshot carrying invented counts would be the same class of
claim this project refuses to make anywhere else.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from papertrace.render import html_to_png  # noqa: E402

FONTS = ROOT / "src" / "papertrace" / "templates" / "assets"

# One illustrative session. Wording tracks src/papertrace/wizard.py; when the
# prompts change, re-run this script rather than editing the PNG.
#
# The numbers are real: 13 pages, 20 distinct references, 16 citation places,
# 5 of them multi-source, 25 model calls — all produced by
# `wizard.workload()` on the paper named below. Nothing here is invented.
BODY = """
<div class="cmd"><span class="pr">❯</span> <span class="tool">papertrace</span></div>

<div class="ln"><b class="white">  ┌──────────────────────────────────┐</b></div>
<div class="ln"><b class="white">  │  ━━━ <span class="red">●─┐</span>                         │</b></div>
<div class="ln"><b class="white">  │  <span class="dim">━━</span>    <span class="red">└─┐</span>   PaperTrace          │</b></div>
<div class="ln"><b class="white">  │  <span class="dim">━</span>  <span class="red">┌────┴┐</span>  claims traced back  │</b></div>
<div class="ln"><b class="white">  │     <span class="red">│</span> ━━  <span class="red">│</span>  to their sources    │</b></div>
<div class="ln"><b class="white">  │     <span class="red">└─────┘</span>                      │</b></div>
<div class="ln"><b class="white">  └──────────────────────────────────┘</b></div>
<div class="ln"><span class="dim">guided audit — one question at a time</span></div>
<div class="sp"></div>
<div class="ln"><b class="white">Checking your setup</b></div>
<div class="ln">  <span class="green">✓</span> claude CLI <span class="dim">(the checker runs on `claude -p`)</span></div>
<div class="ln">  <span class="green">✓</span> layout-aware ingest</div>
<div class="ln"><span class="dimmer">      docling found — tables and figures are read as structure</span></div>
<div class="ln">  <span class="amber">○</span> PNG export of the reports</div>
<div class="ln"><span class="dimmer">      fix: pip install 'papertrace[png]' &amp;&amp; playwright install chromium</span></div>
<div class="sp"></div>
<div class="ln"><b class="white">Which paper should I check?</b></div>
<div class="ln">  path: <span class="cyan">~/Downloads/s00330-026-12773-4.pdf</span></div>
<div class="ln">  <span class="green">✓</span> 13 pages · 20 distinct references cited · 16 citation places</div>
<div class="sp"></div>
<div class="ln"><b class="white">Where should I keep this audit?</b></div>
<div class="ln">  folder (<span class="dim">s00330-026-12773-4</span>): <span class="dimmer">⏎</span></div>
<div class="sp"></div>
<div class="ln">  I found a DOI on the first page: <span class="cyan">10.1007/s00330-026-12773-4</span></div>
<div class="ln">  Is that this paper's own DOI? <span class="dim">[y/n] (y):</span> <span class="dimmer">⏎</span></div>
<div class="sp"></div>
<div class="ln"><b class="white">Contact email</b> <span class="dim">— Unpaywall requires one to look up open-access copies.</span></div>
<div class="ln">  email: <span class="cyan">you@example.org</span></div>
<div class="ln">  Remember it for next time? <span class="dim">[y/n] (y):</span> <span class="dimmer">⏎</span></div>
<div class="sp"></div>
<div class="ln"><b class="white">Ready.</b></div>
<div class="ln">  This makes live requests to Crossref, Unpaywall and Europe PMC, and up to</div>
<div class="ln">  <b class="white">25</b> model calls through `claude -p`.</div>
<div class="ln"><span class="dimmer">  5 of 16 citation places cite several sources, and each cited source is</span></div>
<div class="ln"><span class="dimmer">  judged separately.</span></div>
<div class="ln"><span class="dimmer">  That costs money and takes a few minutes.</span></div>
<div class="ln">  Start the audit? <span class="dim">[y/n] (n):</span> <span class="white">y</span></div>
<div class="sp"></div>
<div class="ln"><span class="dimmer">Same thing as one command, for next time:</span></div>
<div class="ln">  <span class="cyan">papertrace run ~/Downloads/s00330-026-12773-4.pdf \\</span></div>
<div class="ln">  <span class="cyan">  -c s00330-026-12773-4 --doi 10.1007/s00330-026-12773-4</span></div>
"""

HTML = """<!doctype html><meta charset="utf-8"><style>
  @font-face {{ font-family:"JB"; src:url("assets/JetBrainsMono-Regular.ttf") format("truetype"); font-weight:400; }}
  @font-face {{ font-family:"JB"; src:url("assets/JetBrainsMono-Bold.ttf") format("truetype"); font-weight:700; }}
  :root {{
    --bg:#0d1117; --bar:#161b22; --edge:#30363d; --fg:#c9d1d9; --dim:#6e7681; --dimmer:#484f58;
    --green:#3fb950; --amber:#d29922; --cyan:#39c5cf; --mag:#bc8cff; --white:#f0f6fc;
    --red:#f85149;
  }}
  * {{ box-sizing:border-box; margin:0; }}
  html,body {{ background:#010409; }}
  body {{ width:1200px; font-family:"JB","DejaVu Sans Mono",monospace; color:var(--fg);
          -webkit-font-smoothing:antialiased; }}
  .term {{ background:var(--bg); padding-bottom:26px; }}
  .bar {{ background:var(--bar); border-bottom:1px solid var(--edge); height:42px;
          display:flex; align-items:center; padding:0 16px; gap:14px; }}
  .lights {{ display:flex; gap:8px; }}
  .lights span {{ width:12px; height:12px; border-radius:50%; display:block; }}
  .lights .r {{ background:#ff5f56; }} .lights .y {{ background:#ffbd2e; }}
  .lights .g {{ background:#27c93f; }}
  .bar .ttl {{ flex:1; text-align:center; font-size:13px; color:var(--dim); letter-spacing:.02em; }}
  .body {{ padding:20px 26px 0; font-size:15px; line-height:1.62; }}
  .ln {{ white-space:pre-wrap; }}
  .sp {{ height:13px; }}
  .cmd {{ margin-bottom:14px; font-size:15px; }}
  .cmd .pr {{ color:var(--green); font-weight:700; }}
  .cmd .tool {{ color:var(--mag); }}
  .green {{ color:var(--green); }} .amber {{ color:var(--amber); }}
  .cyan {{ color:var(--cyan); }} .dim {{ color:var(--dim); }}
  .dimmer {{ color:var(--dimmer); }} .white {{ color:var(--white); }}
  .red {{ color:var(--red); }}
</style>
<div class="term">
  <div class="bar">
    <div class="lights"><span class="r"></span><span class="y"></span><span class="g"></span></div>
    <div class="ttl">papertrace — guided audit</div>
  </div>
  <div class="body">{body}</div>
</div>
"""


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "docs"
    out_dir.mkdir(exist_ok=True)
    # the fonts are loaded relative to the HTML, so they have to sit beside it
    tmp_assets = out_dir / "assets"
    tmp_assets.mkdir(exist_ok=True)
    for name in ("JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf"):
        shutil.copy(FONTS / name, tmp_assets / name)

    html = out_dir / "_wizard.html"
    html.write_text(HTML.format(body=BODY), encoding="utf-8")
    png = out_dir / "wizard.png"
    ok = html_to_png(html, png)
    html.unlink()
    shutil.rmtree(tmp_assets)
    if not ok:
        raise SystemExit(
            "PNG not written — playwright is missing.\n"
            "    pip install -e \".[png]\" && playwright install chromium"
        )
    print(f"wrote {png}")


if __name__ == "__main__":
    main()
