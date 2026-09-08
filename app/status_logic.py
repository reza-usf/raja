from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ImportantEvent:
    kind: str
    date: str
    text: str


def _train_map(snapshot: dict | None) -> dict[str, dict]:
    if not snapshot:
        return {}
    return {
        t.get("key", ""): t
        for t in snapshot.get("trains", [])
        if t.get("key")
    }


def _is_definitely_low(train: dict) -> bool:
    count = train.get("capacity_count")
    plus = bool(train.get("capacity_plus"))
    # "8+ بلیت" does NOT prove capacity is below 10; only exact values do.
    return count is not None and count < 10 and not plus


def detect_important_events(
    date: str,
    previous: dict | None,
    current: dict,
) -> list[ImportantEvent]:
    """
    Notification policy:
    - unavailable -> available
    - a new train appears
    - a previously non-low train crosses to an exact capacity below 10

    Ordinary capacity changes and no-change cycles are silent dashboard edits.
    """
    events: list[ImportantEvent] = []

    prev_available = bool(previous and previous.get("available"))
    cur_available = bool(current.get("available"))

    if cur_available and not prev_available:
        events.append(
            ImportantEvent(
                "became_available",
                date,
                f"🎟 برای {date} بلیت موجود شد.",
            )
        )

    prev_trains = _train_map(previous)
    cur_trains = _train_map(current)

    # If the whole date just became available, do not also spam one event per train.
    if prev_available:
        for key, train in cur_trains.items():
            if key not in prev_trains:
                name = _short_train_name(train)
                events.append(
                    ImportantEvent(
                        "new_train",
                        date,
                        f"🆕 قطار جدید برای {date}: {name}",
                    )
                )

    for key, train in cur_trains.items():
        old = prev_trains.get(key)
        if not old:
            continue
        if _is_definitely_low(train) and not _is_definitely_low(old):
            name = _short_train_name(train)
            cap = train.get("capacity_text") or f"{train.get('capacity_count')} بلیت"
            events.append(
                ImportantEvent(
                    "capacity_low",
                    date,
                    f"⚠️ ظرفیت {name} در {date} به {cap} رسیده.",
                )
            )

    return events


def _short_train_name(train: dict) -> str:
    owner = (train.get("owner") or "").strip()
    number = (train.get("train_number") or "").strip()
    depart = (train.get("depart_time") or "").strip()
    pieces = []
    if owner:
        pieces.append(owner)
    if number:
        pieces.append(f"قطار {number}")
    if depart:
        pieces.append(f"ساعت {depart}")
    return "، ".join(pieces) or "قطار"


def render_dashboard(
    watch_id: int,
    origin: str,
    destination: str,
    passengers: int,
    passenger_type_label: str,
    dates: list[str],
    snapshots: dict[str, dict],
    errors: dict[str, str] | None = None,
    checked_at: datetime | None = None,
    scan_seconds: float | None = None,
) -> str:
    errors = errors or {}
    checked_at = checked_at or datetime.now()

    lines = [
        f"📡 وضعیت پایش #{watch_id}",
        f"🚉 {origin} → {destination}",
        f"👥 {passengers} نفر | {passenger_type_label}",
        "",
    ]

    for date in dates:
        if date in errors:
            lines.append(f"⚠️ {date} — خطا در بررسی")
            continue

        snap = snapshots.get(date)
        if not snap:
            lines.append(f"⏳ {date} — هنوز بررسی نشده")
            continue

        trains = snap.get("trains", [])
        if not snap.get("available") or not trains:
            lines.append(f"❌ {date} — بلیت موجود نیست")
            continue

        lines.append(f"✅ {date} — {len(trains)} قطار با ظرفیت")
        for train in trains[:3]:
            name = _short_train_name(train)
            cap = (train.get("capacity_text") or "ظرفیت نامشخص").strip()
            price = (train.get("price_text") or "").strip()
            tail = f" | {price} ریال" if price else ""
            lines.append(f"   • {name} | {cap}{tail}")

        if len(trains) > 3:
            lines.append(f"   • ... و {len(trains) - 3} قطار دیگر")

    stamp = checked_at.strftime("%H:%M:%S")
    footer = f"🕒 آخرین بررسی: {stamp}"
    if scan_seconds is not None:
        footer += f" | مدت اسکن: {scan_seconds:.1f} ثانیه"
    lines += ["", footer]

    text = "\n".join(lines)
    # Bale/Telegram-style text messages are commonly limited to 4096 chars.
    if len(text) > 3900:
        text = text[:3850] + "\n…\n(خروجی کوتاه شد)"
    return text
