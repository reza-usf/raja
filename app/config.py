from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    check_interval_seconds: int
    raja_headless: bool
    raja_base_url: str
    database_path: str
    debug_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

        interval = max(60, int(os.getenv("CHECK_INTERVAL_SECONDS", "60")))
        headless = os.getenv("RAJA_HEADLESS", "true").lower() in {"1", "true", "yes", "on"}

        return cls(
            telegram_bot_token=token,
            check_interval_seconds=interval,
            raja_headless=headless,
            raja_base_url=os.getenv("RAJA_BASE_URL", "https://www.raja.ir/").strip(),
            database_path=os.getenv("DATABASE_PATH", "data/bot.sqlite3").strip(),
            debug_dir=os.getenv("DEBUG_DIR", "data/debug").strip(),
        )
