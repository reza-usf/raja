from __future__ import annotations

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()


async def main():
    url = os.getenv("RAJA_BASE_URL", "https://www.raja.ir/")
    out = Path(os.getenv("DEBUG_DIR", "data/debug"))
    out.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(locale="fa-IR", viewport={"width": 1440, "height": 1000})
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        print("\nVISIBLE INPUTS")
        inputs = page.locator("input:visible")
        for i in range(await inputs.count()):
            el = inputs.nth(i)
            print(
                i,
                await el.get_attribute("placeholder"),
                await el.get_attribute("name"),
                await el.get_attribute("type"),
            )

        print("\nVISIBLE BUTTONS")
        buttons = page.locator("button:visible")
        for i in range(min(await buttons.count(), 80)):
            el = buttons.nth(i)
            txt = " ".join((await el.inner_text()).split())
            if txt:
                print(i, repr(txt[:160]))

        print("\nVISIBLE SELECTS")
        selects = page.locator("select:visible")
        for i in range(await selects.count()):
            el = selects.nth(i)
            print(i, await el.locator("option").all_inner_texts())

        await page.screenshot(path=str(out / "raja-home.png"), full_page=True)
        (out / "raja-home.html").write_text(await page.content(), encoding="utf-8")
        print(f"\nSaved: {out/'raja-home.png'} and {out/'raja-home.html'}")
        print("Browser remains open for 60 seconds for manual inspection.")
        await page.wait_for_timeout(60000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
