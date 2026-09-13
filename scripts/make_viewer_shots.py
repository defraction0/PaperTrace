"""Render docs/viewer_summary.png and docs/viewer_detail.png from a case's viewer.

Screenshots, not hand-made mockups: every pixel comes from a real
`report_viewer.html` written by `papertrace report -f viewer`, opened in the
same headless Chromium `render.html_to_png` uses. Re-run this rather than
editing the PNGs when the viewer changes.

    python scripts/make_viewer_shots.py <case>/out/report_viewer.html

The detail shot opens the first contradicted claim that has an evidence crop,
else the first claim with one — the crop is what the picture is for. Needs
`pip install -e ".[png]"` and a one-time `playwright install chromium`.
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from papertrace.render import _chromium_path  # noqa: E402

WIDTH, HEIGHT = 1280, 800

PICK = """
() => {
  const d = PaperTraceLogic.buildModel(JSON.parse(document.getElementById('pt-data').textContent));
  const withCrop = d.claims.filter(c => c.judgements.some(j => j.images.length));
  const pick = withCrop.find(c => c.verdict === 'contradicted') || withCrop[0];
  return pick ? pick.key : null;
}
"""


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit(__doc__)
    html = Path(argv[1]).resolve()
    if not html.exists():
        raise SystemExit(f"not found: {html}")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # noqa: BLE001
        raise SystemExit('playwright is missing — pip install -e ".[png]" && playwright install chromium') from e

    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    exe = _chromium_path() or (sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")) or [None])[-1]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
        page.goto(html.as_uri())
        page.wait_for_timeout(600)
        # dark, like the other screenshots in the README
        if page.inner_text("#pt-theme").strip() == "Dark":
            page.click("#pt-theme")
            page.wait_for_timeout(200)
        page.screenshot(path=str(out / "viewer_summary.png"))

        key = page.evaluate(PICK)
        if key is None:
            raise SystemExit("no claim in this case carries an evidence crop — nothing to show")
        page.click('[data-act="tab"][data-tab="claims"]')
        page.wait_for_timeout(150)
        page.click(f'[data-act="select"][data-key="{key}"]')
        page.wait_for_timeout(900)  # the smooth scroll to the sentence
        page.screenshot(path=str(out / "viewer_detail.png"))
        browser.close()
    print(f"wrote {out / 'viewer_summary.png'} and {out / 'viewer_detail.png'} (claim {key})")


if __name__ == "__main__":
    main(sys.argv)
