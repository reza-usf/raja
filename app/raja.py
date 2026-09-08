from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError


@dataclass
class TrainInfo:
    key: str
    owner: str = ""
    train_number: str = ""
    origin: str = ""
    destination: str = ""
    depart_time: str = ""
    arrive_time: str = ""
    capacity_text: str = ""
    capacity_count: int | None = None
    capacity_plus: bool = False
    price_text: str = ""
    train_name: str = ""
    wagon_type: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "owner": self.owner,
            "train_number": self.train_number,
            "origin": self.origin,
            "destination": self.destination,
            "depart_time": self.depart_time,
            "arrive_time": self.arrive_time,
            "capacity_text": self.capacity_text,
            "capacity_count": self.capacity_count,
            "capacity_plus": self.capacity_plus,
            "price_text": self.price_text,
            "train_name": self.train_name,
            "wagon_type": self.wagon_type,
        }


@dataclass
class SearchResult:
    available: bool
    date: str
    details: list[str]
    fingerprint: str
    debug_file: str | None = None
    trains: list[TrainInfo] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "date": self.date,
            "available": self.available,
            "trains": [t.to_dict() for t in self.trains],
        }


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
        """
        Raja renders origin/destination as ng-select controls:
          app-stations[name="fromStation"]
          app-stations[name="toStation"]

        Select explicitly from Raja's option list, matching the successful
        Playwright Codegen flow instead of only changing the text input value.
        """
        component_name = "fromStation" if origin else "toStation"
        root = page.locator(f'app-stations[name="{component_name}"]')
        await root.wait_for(state="visible")

        combo = root.get_by_role("combobox")
        await combo.click()
        await page.wait_for_timeout(350)

        # Prefer an already-visible exact option (Codegen uses exact Tehran / role option Shiraz).
        exact_text = page.get_by_text(station, exact=True)
        try:
            for i in range(min(await exact_text.count(), 10)):
                item = exact_text.nth(i)
                if await item.is_visible():
                    await item.click()
                    await page.wait_for_timeout(250)
                    return
        except Exception:
            pass

        role_option = page.get_by_role("option", name=station, exact=True)
        try:
            if await role_option.count() and await role_option.first.is_visible():
                await role_option.first.click()
                await page.wait_for_timeout(250)
                return
        except Exception:
            pass

        # Fallback: type/filter and select matching option.
        await combo.fill(station)
        await page.wait_for_timeout(500)
        role_option = page.get_by_role("option", name=station, exact=True)
        if await role_option.count():
            await role_option.first.click()
            await page.wait_for_timeout(250)
            return

        visible_options = page.locator(".ng-option:visible").filter(has_text=station)
        if await visible_options.count():
            await visible_options.first.click()
            await page.wait_for_timeout(250)
            return

        raise RuntimeError(
            f"ایستگاه «{station}» در فهرست {'مبدا' if origin else 'مقصد'} پیدا نشد."
        )

    async def _fill_date(self, page: Page, jalali_date: str):
        """
        Select Raja's date through the real NgbDatepicker UI.

        The actual DOM uses aria-labels like:
          1405-شهریور-27

        Playwright Codegen generated get_by_label("-شهریور-27") because
        get_by_label is substring-matching by default. Using exact=True with
        "-شهریور-27" was therefore wrong.
        """
        month_names = {
            1: "فروردین", 2: "اردیبهشت", 3: "خرداد", 4: "تیر",
            5: "مرداد", 6: "شهریور", 7: "مهر", 8: "آبان",
            9: "آذر", 10: "دی", 11: "بهمن", 12: "اسفند",
        }

        m = re.fullmatch(r"(\d{4})/(\d{1,2})/(\d{1,2})", jalali_date.strip())
        if not m:
            raise RuntimeError(f"فرمت تاریخ نامعتبر است: {jalali_date}")

        year, month, day = map(int, m.groups())
        month_name = month_names.get(month)
        if not month_name:
            raise RuntimeError(f"ماه شمسی نامعتبر است: {month}")

        calendar_button = page.locator(
            'app-datepicker-single[name="oneWayDatePicker"] button[title="set"]'
        )
        if not await calendar_button.count():
            calendar_button = page.locator('button.icon-calendar[title="set"]')

        await calendar_button.first.wait_for(state="visible", timeout=8_000)
        await calendar_button.first.click()

        calendar = page.locator("ngb-datepicker.dropdown-menu.show")
        await calendar.wait_for(state="visible", timeout=8_000)

        # Raja exposes month/year dropdowns in the datepicker. Selecting them
        # makes this work for future months too, not only the currently shown one.
        year_select = calendar.locator('select[aria-label="Select year"]')
        month_select = calendar.locator('select[aria-label="Select month"]')

        if await year_select.count():
            available_years = await year_select.locator("option").evaluate_all(
                "(els) => els.map(e => e.value)"
            )
            if str(year) not in available_years:
                raise RuntimeError(
                    f"سال {year} در تقویم قابل انتخاب رجا وجود ندارد."
                )
            await year_select.select_option(str(year))
            await page.wait_for_timeout(250)

        # Re-resolve after Angular re-render.
        calendar = page.locator("ngb-datepicker.dropdown-menu.show")
        month_select = calendar.locator('select[aria-label="Select month"]')
        if await month_select.count():
            available_months = await month_select.locator("option").evaluate_all(
                "(els) => els.map(e => e.value)"
            )
            if str(month) not in available_months:
                raise RuntimeError(
                    f"ماه {month_name} در تقویم قابل انتخاب رجا وجود ندارد."
                )
            await month_select.select_option(str(month))
            await page.wait_for_timeout(350)

        calendar = page.locator("ngb-datepicker.dropdown-menu.show")
        target_label = f"{year}-{month_name}-{day}"

        # Exact DOM selector from the captured Raja page.
        target = calendar.locator(
            f'div.ngb-dp-day[aria-label="{target_label}"]:not(.disabled):not(.hidden)'
        )

        try:
            await target.first.wait_for(state="visible", timeout=5_000)
        except Exception:
            debug = await self._debug_snapshot(page, "raja-calendar-date-not-found")
            raise RuntimeError(
                f"تاریخ {jalali_date} در تقویم رجا قابل انتخاب نبود. "
                f"target={target_label!r}, snapshot={debug or 'unavailable'}"
            )

        day_span = target.first.locator("span.custom-day")
        if await day_span.count():
            await day_span.click()
        else:
            await target.first.click()

        await page.wait_for_timeout(450)

        date_input = page.locator(
            'app-datepicker-single[name="oneWayDatePicker"] input[name="dp"]'
        )
        if not await date_input.count():
            date_input = page.locator('input[name="dp"]')

        value = (await date_input.first.input_value()).strip()
        if not value:
            raise RuntimeError(
                f"تاریخ {jalali_date} کلیک شد اما فیلد تاریخ رجا خالی ماند."
            )

    async def _set_passenger_type(self, page: Page, passenger_type: str):
        labels = {
            "normal": ("changeticketFamily", "مسافرین عادی"),
            "men": ("changeticketMale", "ویژه برادران"),
            "women": ("changeticketFamale", "ویژه خواهران"),
        }
        aria_label, visible_label = labels[passenger_type]

        toggle = page.locator("#dropdownTikcetType")
        await toggle.wait_for(state="visible")
        current = re.sub(r"\s+", " ", (await toggle.inner_text()).strip())

        # Raja defaults to normal passengers. If already selected, no need to reopen.
        if visible_label in current:
            return

        await toggle.click()
        await page.wait_for_timeout(150)
        radio = page.locator(f'input[aria-label="{aria_label}"]')
        await radio.wait_for(state="visible")
        # Click the label because the radio input is styled by ngbButton.
        await radio.locator("xpath=..").click()
        await page.wait_for_timeout(200)

    async def _set_passenger_count(self, page: Page, count: int):
        if not (1 <= count <= 6):
            raise RuntimeError("تعداد مسافر باید بین ۱ تا ۶ باشد.")

        toggle = page.locator("#dropdownPassenger")
        await toggle.wait_for(state="visible")

        if count == 1:
            return

        await toggle.click()
        plus = page.locator('button[title="plus-adult"]')
        await plus.wait_for(state="visible")
        for _ in range(count - 1):
            await plus.click()
            await page.wait_for_timeout(120)

        try:
            await toggle.click()
        except Exception:
            pass

    async def _click_search(self, page: Page):
        button = page.locator("#btnsearchticket")
        await button.wait_for(state="visible")
        if not await button.is_enabled():
            raise RuntimeError("دکمه جست‌وجوی رجا غیرفعال است.")
        await button.click()

    async def _collect_result(self, page: Page, jalali_date: str) -> SearchResult:
        """
        Parse Raja's actual ticket-result cards.

        A sellable result currently appears as:
          .train-result
            .owner-name
            .train-name
            .wagon-type
            .train-number
            .start-station-name / .end-station-name
            .start-day / .end-day
            .price
            .capacity-title .field-value
            button.lock-btn   -> "رزرو بلیت"

        We only report availability when a train card has a usable reservation
        button. This avoids false positives from unrelated words such as "قطار"
        or "بلیت" elsewhere on the page.
        """
        # Raja updates the result area asynchronously. Poll briefly for either
        # a ticket card or a real no-result message.
        deadline = time.monotonic() + 12
        normalized = ""

        no_result_hints = (
            "برای مسیر رفت شما در این تاریخ قطار یافت نشد",
            "در این تاریخ قطار یافت نشد",
            "قطار یافت نشد",
            "نتیجه‌ای یافت نشد",
            "نتیجه ای یافت نشد",
            "بلیت یافت نشد",
            "بلیط یافت نشد",
        )

        while time.monotonic() < deadline:
            cards = page.locator(".train-result")
            if await cards.count():
                break

            try:
                body_text = (await page.locator("body").inner_text()).strip()
                normalized = re.sub(r"\s+", " ", body_text)
                if any(hint in normalized for hint in no_result_hints):
                    fingerprint = hashlib.sha256(
                        f"none|{jalali_date}".encode()
                    ).hexdigest()
                    return SearchResult(False, jalali_date, [], fingerprint)
            except Exception:
                pass

            await page.wait_for_timeout(300)

        cards = page.locator(".train-result")
        card_count = await cards.count()

        if card_count == 0:
            # One final read before considering this an unexpected page.
            try:
                body_text = (await page.locator("body").inner_text()).strip()
                normalized = re.sub(r"\s+", " ", body_text)
                if any(hint in normalized for hint in no_result_hints):
                    fingerprint = hashlib.sha256(
                        f"none|{jalali_date}".encode()
                    ).hexdigest()
                    return SearchResult(False, jalali_date, [], fingerprint)
            except Exception:
                pass

            debug = await self._debug_snapshot(page, "raja-result-unrecognized")
            raise RuntimeError(
                "صفحه نتیجه رجا باز شد، اما نه کارت قطار و نه پیام «قطار یافت نشد» "
                f"تشخیص داده شد. snapshot={debug or 'unavailable'}"
            )

        details: list[str] = []
        stable_ids: list[str] = []
        trains: list[TrainInfo] = []

        async def text_of(root, selector: str) -> str:
            loc = root.locator(selector)
            if not await loc.count():
                return ""
            try:
                text = (await loc.first.inner_text()).strip()
                return re.sub(r"\s+", " ", text)
            except Exception:
                return ""

        for i in range(min(card_count, 30)):
            card = cards.nth(i)
            try:
                if not await card.is_visible():
                    continue
            except Exception:
                continue

            reserve = card.locator("button.lock-btn").filter(
                has_text=re.compile(r"رزرو\s*بلیت|رزرو\s*بلیط")
            )

            sellable = False
            if await reserve.count():
                try:
                    sellable = (
                        await reserve.first.is_visible()
                        and await reserve.first.is_enabled()
                    )
                except Exception:
                    sellable = False

            # A card may represent a listed train with no sellable capacity.
            # Do not alert for that case.
            if not sellable:
                continue

            owner = await text_of(card, ".owner-name")
            train_name = await text_of(card, ".train-name")
            wagon_type = await text_of(card, ".wagon-type")
            train_number_text = await text_of(card, ".train-number")
            origin = await text_of(card, ".start-station-name")
            destination = await text_of(card, ".end-station-name")
            depart_text = await text_of(card, ".start-day")
            arrive_text = await text_of(card, ".end-day")
            price = await text_of(card, ".price")
            capacity = await text_of(card, ".capacity-title .field-value")

            train_number = re.sub(r"^.*?شماره\s*قطار\s*:\s*", "", train_number_text)
            depart_match = re.search(r"(\d{1,2}:\d{2})", depart_text)
            arrive_match = re.search(r"(\d{1,2}:\d{2})", arrive_text)
            depart_time = depart_match.group(1) if depart_match else depart_text
            arrive_time = arrive_match.group(1) if arrive_match else arrive_text

            # Parse the capacity text into a machine-usable number where possible.
            # Examples observed on Raja: "20+ بلیت", "8 بلیت".
            capacity_ascii = capacity.translate(
                str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
            )
            capacity_match = re.search(r"(\d+)", capacity_ascii)
            capacity_count = int(capacity_match.group(1)) if capacity_match else None
            capacity_plus = "+" in capacity_ascii

            train_key = "|".join(
                [
                    train_number or "?",
                    owner or "?",
                    origin or "?",
                    destination or "?",
                    depart_time or "?",
                ]
            )

            trains.append(
                TrainInfo(
                    key=train_key,
                    owner=owner,
                    train_number=train_number,
                    origin=origin,
                    destination=destination,
                    depart_time=depart_time,
                    arrive_time=arrive_time,
                    capacity_text=capacity,
                    capacity_count=capacity_count,
                    capacity_plus=capacity_plus,
                    price_text=price,
                    train_name=train_name,
                    wagon_type=wagon_type,
                )
            )

            parts = []
            if owner:
                parts.append(owner)
            if train_number:
                parts.append(f"قطار {train_number}")
            if origin or destination:
                parts.append(f"{origin or '؟'} → {destination or '؟'}")
            if depart_time:
                parts.append(f"حرکت {depart_time}")
            if arrive_time:
                parts.append(f"ورود {arrive_time}")
            if capacity:
                parts.append(capacity)
            if price:
                parts.append(f"{price} ریال")
            if train_name:
                parts.append(train_name)
            elif wagon_type:
                parts.append(wagon_type)

            details.append(" | ".join(parts)[:700])

            # Fingerprint intentionally ignores capacity and price so the bot
            # does not spam on every seat-count/price refresh. If the same train
            # sells out, the watch fingerprint becomes None; if it reappears,
            # the bot alerts again.
            stable_ids.append(
                "|".join(
                    [
                        jalali_date,
                        train_number,
                        owner,
                        origin,
                        destination,
                        depart_time,
                    ]
                )
            )

        if not details:
            fingerprint = hashlib.sha256(
                f"none|{jalali_date}".encode()
            ).hexdigest()
            return SearchResult(False, jalali_date, [], fingerprint)

        material = json.dumps(
            sorted(stable_ids),
            ensure_ascii=False,
        )
        fingerprint = hashlib.sha256(material.encode()).hexdigest()
        return SearchResult(True, jalali_date, details, fingerprint, trains=trains)

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

    async def _open_search(
        self,
        page: Page,
        origin: str,
        destination: str,
        jalali_date: str,
        passengers: int,
        passenger_type: str,
    ) -> SearchResult:
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

    async def _go_next_result_day(self, page: Page, target_date: str):
        """
        Move from the current Raja result page to the following day without
        rebuilding the whole search form. This is much faster for contiguous ranges.
        """
        button = page.get_by_role("button", name="روز بعد", exact=True)
        await button.wait_for(state="visible", timeout=8_000)
        await button.click()

        header = page.locator(".ticketHedInfo .date")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                text = re.sub(r"\s+", " ", (await header.inner_text()).strip())
                if target_date in text:
                    # Give Angular's result list a short moment after the header changes.
                    await page.wait_for_timeout(450)
                    return
            except Exception:
                pass
            await page.wait_for_timeout(200)

        raise RuntimeError(
            f"پس از کلیک «روز بعد»، صفحه رجا به تاریخ {target_date} نرفت."
        )

    async def check_dates(
        self,
        origin: str,
        destination: str,
        jalali_dates: list[str],
        passengers: int,
        passenger_type: str,
    ) -> tuple[dict[str, SearchResult], dict[str, str]]:
        """
        Check a contiguous date range efficiently.

        The first date performs a normal Raja search. Later dates use the result
        page's «روز بعد» button. If that navigation fails for a date, we fall
        back to a fresh full search for that date, so one broken step does not
        lose the rest of the range.
        """
        results: dict[str, SearchResult] = {}
        errors: dict[str, str] = {}

        if not jalali_dates:
            return results, errors

        async with self._lock:
            page = await self._new_page()
            try:
                for idx, jalali_date in enumerate(jalali_dates):
                    try:
                        if idx == 0:
                            result = await self._open_search(
                                page,
                                origin,
                                destination,
                                jalali_date,
                                passengers,
                                passenger_type,
                            )
                        else:
                            try:
                                await self._go_next_result_day(page, jalali_date)
                                result = await self._collect_result(page, jalali_date)
                            except Exception:
                                # Recover from a broken next-day navigation with a
                                # completely fresh search and continue from there.
                                try:
                                    await page.context.close()
                                except Exception:
                                    pass
                                page = await self._new_page()
                                result = await self._open_search(
                                    page,
                                    origin,
                                    destination,
                                    jalali_date,
                                    passengers,
                                    passenger_type,
                                )

                        results[jalali_date] = result

                    except Exception as exc:
                        errors[jalali_date] = str(exc)
                        try:
                            await page.context.close()
                        except Exception:
                            pass
                        page = await self._new_page()

                return results, errors
            finally:
                try:
                    await page.context.close()
                except Exception:
                    pass

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
                return await self._open_search(
                    page,
                    origin,
                    destination,
                    jalali_date,
                    passengers,
                    passenger_type,
                )
            except Exception as exc:
                debug = await self._debug_snapshot(page, "raja-error")
                raise RuntimeError(
                    f"جست‌وجوی رجا ناموفق بود: {exc}. snapshot={debug or 'unavailable'}"
                ) from exc
            finally:
                await page.context.close()
