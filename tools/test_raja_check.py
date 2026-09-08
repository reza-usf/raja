from __future__ import annotations

import asyncio

from app.raja import RajaScraper


async def main():
    scraper = RajaScraper(
        base_url="https://www.raja.ir/",
        headless=False,
        debug_dir="data/debug",
    )

    try:
        await scraper.start()
        result = await scraper.check_date(
            origin="تهران",
            destination="شیراز",
            jalali_date="1405/06/27",
            passengers=1,
            passenger_type="normal",
        )

        print("\n========== FINAL SCRAPER RESULT ==========")
        print("available:", result.available)
        print("date:", result.date)
        print("fingerprint:", result.fingerprint)
        print("details:")
        for item in result.details:
            print(" -", item)

        if result.available:
            print("\nOK: ticket availability was detected.")
        else:
            print("\nNOT AVAILABLE: scraper did not find a sellable ticket.")

    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
