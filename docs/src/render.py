#!/usr/bin/env python3
"""Render docs/src/hero.html to docs/hero.png at 2x (1600x800 CSS px -> 3200x1600).

Usage:  python3 docs/src/render.py
Needs:  pip install playwright && python3 -m playwright install chromium
The hero loads Archivo, Inter and JetBrains Mono from Google Fonts, so render online
for the intended look; offline it falls back to system sans and mono.
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

SRC = Path(__file__).resolve().parent
OUT = SRC.parent / "hero.png"

async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 800}, device_scale_factor=2)
        await page.goto((SRC / "hero.html").as_uri(), wait_until="networkidle")
        await page.evaluate("document.fonts.ready")
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(OUT))
        await browser.close()
    print(f"wrote {OUT}")

if __name__ == "__main__":
    asyncio.run(main())
