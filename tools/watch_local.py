from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from app.raja import RajaScraper
from app.status_logic import detect_important_events, render_dashboard

ORIGIN = "تهران"
DESTINATION = "شیراز"
DATES = [
    "1405/06/27",
    "1405/06/28",
    "1405/06/29",
    "1405/06/30",
    "1405/06/31",
]
PASSENGERS = 1
PASSENGER_TYPE = "normal"
PASSENGER_TYPE_LABEL = "مسافرین عادی"
INTERVAL_SECONDS = 60

STATE_FILE = Path("data/local-watch-v6-state.json")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def main():
    scraper = RajaScraper(
        base_url="https://www.raja.ir/",
        headless=False,
        debug_dir="data/debug",
    )
    previous = load_state()

    print("پایش محلی شروع شد. برای توقف Ctrl+C بزن.")
    print("هر دور از شروع دور قبلی تقریباً 60 ثانیه فاصله دارد.\n")

    try:
        await scraper.start()

        while True:
            cycle_start = time.monotonic()
            print("=" * 70)
            print("شروع دور:", datetime.now().strftime("%H:%M:%S"))

            results, errors = await scraper.check_dates(
                origin=ORIGIN,
                destination=DESTINATION,
                jalali_dates=DATES,
                passengers=PASSENGERS,
                passenger_type=PASSENGER_TYPE,
            )

            current = {
                date: result.snapshot()
                for date, result in results.items()
            }

            events = []
            for date, snap in current.items():
                events.extend(
                    detect_important_events(
                        date,
                        previous.get(date),
                        snap,
                    )
                )

            elapsed = time.monotonic() - cycle_start
            print()
            print(
                render_dashboard(
                    watch_id=1,
                    origin=ORIGIN,
                    destination=DESTINATION,
                    passengers=PASSENGERS,
                    passenger_type_label=PASSENGER_TYPE_LABEL,
                    dates=DATES,
                    snapshots=current,
                    errors=errors,
                    checked_at=datetime.now(),
                    scan_seconds=elapsed,
                )
            )

            if events:
                print("\n🔔 رویدادهایی که در نسخه بله باید Notification بدهند:")
                for event in events:
                    print(" -", event.text)
            else:
                print("\n🔕 تغییر مهمی نیست؛ در نسخه بله فقط Dashboard ادیت می‌شود.")

            # Preserve the last good snapshot for dates that temporarily error.
            merged = dict(previous)
            merged.update(current)
            previous = merged
            save_state(previous)

            sleep_for = max(0.0, INTERVAL_SECONDS - elapsed)
            print(f"\nدور بعدی حدود {sleep_for:.1f} ثانیه دیگر شروع می‌شود.")
            await asyncio.sleep(sleep_for)

    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
