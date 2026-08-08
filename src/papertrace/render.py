"""Optional HTML → PNG rendering of the styled reports (needs Playwright + Chromium).

The HTML reports are self-contained and viewable in any browser; the PNG step
only exists for sharing. Failure here is soft — the pipeline reports it and
moves on.
"""

from __future__ import annotations

import glob
from pathlib import Path

WIDTH = 1200


def _chromium_path() -> str | None:
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def html_to_png(html_path: Path, png_path: Path, width: int = WIDTH) -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("· PNG skipped (playwright not installed — `pip install papertrace[png]`)")
        return False

    try:
        with sync_playwright() as p:
            exe = _chromium_path()
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 1500}, device_scale_factor=2)
            page.goto(html_path.resolve().as_uri())
            page.wait_for_timeout(600)
            h = round(
                page.evaluate(
                    "() => Math.max(...[...document.body.children]"
                    ".map(c => c.getBoundingClientRect().bottom))"
                )
            )
            page.set_viewport_size({"width": width, "height": h})
            page.wait_for_timeout(150)
            page.screenshot(path=str(png_path), clip={"x": 0, "y": 0, "width": width, "height": h})
            browser.close()
        return True
    except Exception as e:  # noqa: BLE001 — soft failure by design
        print(f"· PNG skipped ({e})")
        return False
