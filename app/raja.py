from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError


@dataclass
class SearchResult:
    available: bool
    date: str
    details: list[str]
    fingerprint: str
    debug_file: str | None = None


class RajaScraper:
    NEGATIVE_HINTS = (
        "قطاری یافت نشد",
        "نتیجه‌ای یافت نشد",
        "بلیطی یافت نشد",
        "بلیت یافت نشد",
        "ظرفیت تکمیل",
        "تکمیل ظرفیت",
        "موجود نیست",
        "ظرفیتی وجود ندارد",
    )

    POSITIVE_ACTION_HINTS = (
        "انتخاب",
        "رزرو",
        "خرید بلیت",
        "خرید بلیط",
    )

    def __init__(self, base_url: str, headless: bool, debug_dir: str):
        self.base_url = base_url
        self.headless = headless
        self.debug_dir = Path(debug_dir)
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        self._pw = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self):
        if self._browser:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-dev-shm-usage"],
        )

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def _new_page(self) -> Page:
        await self.start()
        context = await self._browser.new_context(
            locale="fa-IR",
            timezone_id="Asia/Tehran",
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        page.set_default_timeout(12_000)
        return page

    async def _first_visible(self, page: Page, selectors: Iterable[str]):
        for sel in selectors:
            loc = page.locator(sel)
            try:
                count = await loc.count()
            except Exception:
                continue
            for i in range(min(count, 6)):
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        return item
                except Exception:
                    pass
        return None

    async def _fill_station(self, page: Page, station: str, origin: bool):
        keys = ("مبدا", "مبدأ") if origin else ("مقصد",)
        semantic_selectors = []
        for key in keys:
            semantic_selectors += [
                f'input[placeholder*="{key}"]',
                f'input[aria-label*="{key}"]',
            ]

        locator = await self._first_visible(page, semantic_selectors)
        if locator is None:
            text_inputs = page.locator('input[type="text"]:visible')
            if await text_inputs.count() >= 2:
                locator = text_inputs.nth(0 if origin else 1)
        if locator is None:
            raise RuntimeError("فیلد مبدا/مقصد در صفحه رجا پیدا نشد.")

        await locator.click()
        await locator.fill(station)
        await page.wait_for_timeout(700)

        candidates = [
            page.get_by_text(station, exact=True),
            page.locator(f'[role="option"]:has-text("{station}")'),
            page.locator(f'li:has-text("{station}")'),
            page.locator(f'.dropdown-item:has-text("{station}")'),
            page.locator(f'.mat-option-text:has-text("{station}")'),
        ]
        for cand in candidates:
            try:
                count = await cand.count()
                for i in range(min(count, 5)):
                    item = cand.nth(i)
                    if await item.is_visible():
                        await item.click()
                        return
            except Exception:
                continue

        await locator.press("Tab")

    async def _fill_date(self, page: Page, jalali_date: str):
        date_selectors = [
            'input[placeholder*="تاریخ"]',
            'input[aria-label*="تاریخ"]',
            'input[name*="date" i]',
            'input[type="date"]',
        ]
        locator = await self._first_visible(page, date_selectors)
        if locator is None:
            raise RuntimeError("فیلد تاریخ در صفحه رجا پیدا نشد.")

        try:
            await locator.click()
            await locator.fill(jalali_date)
            await locator.press("Tab")
            return
        except Exception:
            pass

        await locator.evaluate(
            """(el, value) => {
                const setter = Object.getOwnPropertyDescriptor(
                  HTMLInputElement.prototype, 'value'
                )?.set;
                if (setter) setter.call(el, value); else el.value = value;
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
                el.blur();
            }""",
            jalali_date,
        )

    async def _set_passenger_type(self, page: Page, passenger_type: str):
        text_map = {
            "normal": ("مسافرین عادی", "مسافران عادی", "عادی"),
            "men": ("ویژه برادران", "برادران"),
            "women": ("ویژه خواهران", "خواهران"),
        }
        for text in text_map[passenger_type]:
            loc = page.get_by_text(text, exact=False)
            try:
                count = await loc.count()
                for i in range(min(count, 6)):
                    item = loc.nth(i)
                    if await item.is_visible():
                        await item.click()
                        return
            except Exception:
                pass
        raise RuntimeError("گزینه نوع مسافر در صفحه رجا پیدا نشد.")

    async def _set_passenger_count(self, page: Page, count: int):
        selects = page.locator("select:visible")
        for i in range(await selects.count()):
            sel = selects.nth(i)
            try:
                options = await sel.locator("option").all_inner_texts()
                for op in options:
                    if re.sub(r"\D", "", op) == str(count):
                        await sel.select_option(label=op)
                        return
            except Exception:
                pass

        opened = False
        for text in ("مسافر", "تعداد مسافر", "نفر"):
            loc = page.get_by_text(text, exact=False)
            try:
                count_loc = await loc.count()
                for i in range(min(count_loc, 8)):
                    item = loc.nth(i)
                    if await item.is_visible():
                        await item.click()
                        opened = True
                        break
            except Exception:
                pass
            if opened:
                break

        if count == 1:
            return

        plus_candidates = [
            'button:has-text("+")',
            '[aria-label*="افزایش"]',
            'button[aria-label*="plus" i]',
            '.plus',
            '[class*="plus"]',
        ]
        plus = await self._first_visible(page, plus_candidates)
        if plus is None:
            raise RuntimeError("کنترل تعداد مسافر در صفحه رجا پیدا نشد.")
        for _ in range(count - 1):
            await plus.click()
            await page.wait_for_timeout(150)

    async def _click_search(self, page: Page):
        candidates = [
            page.get_by_role("button", name=re.compile(r"جستجو|جست‌وجو|جست وجو")),
            page.locator('button:has-text("جستجو")'),
            page.locator('button:has-text("جست‌وجو")'),
            page.locator('input[type="submit"]'),
        ]
        for cand in candidates:
            try:
                count = await cand.count()
                for i in range(min(count, 6)):
                    item = cand.nth(i)
                    if await item.is_visible() and await item.is_enabled():
                        await item.click()
                        return
            except Exception:
                pass
        raise RuntimeError("دکمه جست‌وجو در صفحه رجا پیدا نشد.")

    async def _collect_result(self, page: Page, jalali_date: str) -> SearchResult:
        await page.wait_for_timeout(1800)
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            pass

        body_text = (await page.locator("body").inner_text()).strip()
        normalized = re.sub(r"\s+", " ", body_text)

        if any(hint in normalized for hint in self.NEGATIVE_HINTS):
            fingerprint = hashlib.sha256(f"none|{jalali_date}".encode()).hexdigest()
            return SearchResult(False, jalali_date, [], fingerprint)

        details = []
        for selector in (
            'button:has-text("انتخاب")',
            'button:has-text("رزرو")',
            'a:has-text("انتخاب")',
            'a:has-text("رزرو")',
            '[class*="train"]',
            '[class*="ticket"]',
        ):
            loc = page.locator(selector)
            try:
                count = min(await loc.count(), 12)
                for i in range(count):
                    item = loc.nth(i)
                    if not await item.is_visible():
                        continue
                    text = re.sub(r"\s+", " ", (await item.inner_text()).strip())
                    if text and text not in details and len(text) > 2:
                        details.append(text[:500])
                    if len(details) >= 5:
                        break
            except Exception:
                pass
            if len(details) >= 5:
                break

        positive_action = any(hint in normalized for hint in self.POSITIVE_ACTION_HINTS)
        train_clues = sum(
            token in normalized
            for token in ("شماره قطار", "ساعت حرکت", "ظرفیت", "کوپه", "سالن", "تومان", "ریال")
        )

        available = bool(details) or (positive_action and train_clues >= 2)
        material = json.dumps(
            {"available": available, "date": jalali_date, "details": details},
            ensure_ascii=False,
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(material.encode()).hexdigest()
        return SearchResult(available, jalali_date, details, fingerprint)

    async def _debug_snapshot(self, page: Page, prefix: str) -> str | None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = self.debug_dir / f"{prefix}-{stamp}"
        try:
            await page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
            html = await page.content()
            base.with_suffix(".html").write_text(html, encoding="utf-8")
            return str(base)
        except Exception:
            return None

    async def check_date(
        self,
        origin: str,
        destination: str,
        jalali_date: str,
        passengers: int,
        passenger_type: str,
    ) -> SearchResult:
        async with self._lock:
            page = await self._new_page()
            try:
                await page.goto(self.base_url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1200)

                try:
                    tab = page.get_by_text(re.compile(r"بلیت قطار|بلیط قطار")).first
                    if await tab.is_visible():
                        await tab.click()
                        await page.wait_for_timeout(300)
                except Exception:
                    pass

                await self._fill_station(page, origin, origin=True)
                await self._fill_station(page, destination, origin=False)
                await self._fill_date(page, jalali_date)
                await self._set_passenger_count(page, passengers)
                await self._set_passenger_type(page, passenger_type)
                await self._click_search(page)
                return await self._collect_result(page, jalali_date)
            except Exception as exc:
                debug = await self._debug_snapshot(page, "raja-error")
                raise RuntimeError(
                    f"جست‌وجوی رجا ناموفق بود: {exc}. snapshot={debug or 'unavailable'}"
                ) from exc
            finally:
                await page.context.close()
