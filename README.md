# بازوی بله برای پایش ظرفیت بلیت قطار رجا

این نسخه برای اجرا روی VPS ایران طراحی شده است. ارتباط پیام‌رسان از **بله** انجام می‌شود و بررسی ظرفیت
با Playwright روی `raja.ir` انجام می‌شود.

این پروژه **خرید یا رزرو خودکار انجام نمی‌دهد**؛ فقط ظرفیت را بررسی می‌کند و در بله هشدار می‌دهد.

## چرا بله؟

VPS ایران ممکن است به Telegram Bot API دسترسی نداشته باشد، اما API بازوی بله از مسیر زیر در دسترس است:

```text
https://tapi.bale.ai/bot<TOKEN>/METHOD_NAME
```

پروژه همچنان از `python-telegram-bot` استفاده می‌کند، چون API بله بر پایه Telegram Bot API است؛
Base URL کتابخانه به API بله تغییر داده شده است.

## امکانات

- مبدا و مقصد
- بازه تاریخ شمسی
- تعداد مسافر ۱ تا ۶
- نوع مسافر:
  - مسافرین عادی
  - ویژه برادران
  - ویژه خواهران
- SQLite برای نگهداری پایش‌ها
- فاصله بررسی قابل تنظیم، حداقل ۶۰ ثانیه
- جلوگیری از هشدار تکراری
- Docker و اجرای خودکار بعد از reboot
- ذخیره screenshot/HTML هنگام تغییر احتمالی سایت رجا

## فایل `.env`

از روی نمونه بساز:

```bash
cp .env.example .env
nano .env
```

محتوا:

```env
BALE_BOT_TOKEN=توکن_بازوی_بله
CHECK_INTERVAL_SECONDS=60
RAJA_HEADLESS=true
RAJA_BASE_URL=https://www.raja.ir/
DATABASE_PATH=data/bot.sqlite3
DEBUG_DIR=data/debug
```

توکن را در GitHub قرار نده.

## تست توکن بله

```bash
read -s BALE_TOKEN
```

توکن را وارد کن و سپس:

```bash
curl -s "https://tapi.bale.ai/bot${BALE_TOKEN}/getMe"
```

خروجی باید `ok:true` داشته باشد.

سپس:

```bash
unset BALE_TOKEN
```

## اجرا

داخل پوشه پروژه:

```bash
docker compose up -d --build
```

وضعیت:

```bash
docker compose ps
```

لاگ آخر:

```bash
docker compose logs --tail=100
```

اگر کانتینر `Up` باشد، در بله وارد بازوی خودت شو و `/start` بفرست.

## به‌روزرسانی از GitHub

اگر این پروژه را روی GitHub گذاشته‌ای و روی VPS clone کرده‌ای:

```bash
cd /root/raja_bale_alert_bot
git pull
docker compose up -d --build
```

اگر پوشه یا نام repository متفاوت است، مسیر خودت را جایگزین کن.

## نکته مهم درباره نرخ بررسی

بازه ۸ تا ۲۰ شهریور ۱۳ روز است. بررسی تک‌تک روزها در هر دور می‌تواند تعداد درخواست زیادی ایجاد کند.
حداقل فاصله برنامه ۶۰ ثانیه است، اما بهتر است پایش دائمی را با نرخ معقول انجام دهی و در صورت امکان
درخواست‌های غیرضروری مرورگر (تصاویر، فونت و...) را محدود کنی.

## عیب‌یابی رجا

اگر ظاهر سایت رجا تغییر کند، snapshotها در این مسیر ذخیره می‌شوند:

```text
data/debug/
```

برای مشاهده DOM و selectorهای صفحه:

```bash
RAJA_HEADLESS=false python tools/diagnose_raja.py
```

روی VPS بدون محیط گرافیکی، بهتر است HTML/screenshot تولیدشده را بررسی کنی.
