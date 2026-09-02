from __future__ import annotations

import re
from datetime import date, timedelta
import jdatetime

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ASCII_DIGITS = "0123456789"

_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4,
    "مرداد": 5, "شهریور": 6, "مهر": 7, "آبان": 8,
    "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}


def normalize_digits(value: str) -> str:
    table = str.maketrans(PERSIAN_DIGITS + ARABIC_DIGITS, ASCII_DIGITS + ASCII_DIGITS)
    return value.translate(table)


def current_jalali_year() -> int:
    return jdatetime.date.fromgregorian(date=date.today()).year


def parse_jalali_date(text: str, default_year: int | None = None) -> jdatetime.date:
    # Accepts 1405/06/08, 1405-6-8, "8 شهریور", "8 شهریور 1405".
    s = normalize_digits(text.strip()).replace("ي", "ی").replace("ك", "ک")

    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
        return jdatetime.date(y, mo, d)

    m = re.fullmatch(r"(\d{1,2})\s+([آ-ی]+)(?:\s+(\d{4}))?", s)
    if m:
        d = int(m.group(1))
        month_name = m.group(2)
        y = int(m.group(3)) if m.group(3) else (default_year or current_jalali_year())
        if month_name not in _MONTHS:
            raise ValueError("نام ماه شمسی شناخته نشد.")
        return jdatetime.date(y, _MONTHS[month_name], d)

    raise ValueError("تاریخ را مثل 1405/06/08 یا «8 شهریور 1405» وارد کنید.")


def format_jalali(d: jdatetime.date) -> str:
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"


def iter_jalali_days(start: str, end: str):
    s = parse_jalali_date(start)
    e = parse_jalali_date(end)
    if e < s:
        raise ValueError("تاریخ پایان قبل از تاریخ شروع است.")
    current = s
    one_day = timedelta(days=1)
    while current <= e:
        yield current
        current += one_day
