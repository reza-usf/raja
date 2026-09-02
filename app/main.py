from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import Settings
from .date_utils import current_jalali_year, format_jalali, iter_jalali_days, parse_jalali_date
from .raja import RajaScraper
from .storage import Storage, Watch


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("raja-bot")

ORIGIN, DESTINATION, DATE_FROM, DATE_TO, PASSENGERS, PASSENGER_TYPE, CONFIRM = range(7)

TYPE_LABELS = {
    "normal": "مسافرین عادی",
    "men": "ویژه برادران",
    "women": "ویژه خواهران",
}


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ پایش جدید", callback_data="new")],
        [InlineKeyboardButton("📋 پایش‌های من", callback_data="list")],
        [InlineKeyboardButton("⏹ توقف همه", callback_data="stop_all")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "سلام. این بات ظرفیت بلیت قطار رجا را فقط پایش می‌کند و خرید خودکار انجام نمی‌دهد.\n\n"
        "برای ساخت پایش جدید، مبدا، مقصد، بازه تاریخ شمسی، تعداد مسافر و نوع مسافر را می‌گیریم."
    )
    await update.message.reply_text(text, reply_markup=main_menu())


async def new_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["draft"] = {}
    await q.message.reply_text("مبدا را بنویس؛ مثلاً: تهران")
    return ORIGIN


async def set_origin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"]["origin"] = update.message.text.strip()
    await update.message.reply_text("مقصد را بنویس؛ مثلاً: شیراز")
    return DESTINATION


async def set_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"]["destination"] = update.message.text.strip()
    y = current_jalali_year()
    await update.message.reply_text(f"شروع بازه را وارد کن؛ مثل {y}/06/08 یا «8 شهریور {y}».")
    return DATE_FROM


async def set_date_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        d = parse_jalali_date(update.message.text)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return DATE_FROM
    context.user_data["draft"]["date_from"] = format_jalali(d)
    await update.message.reply_text(f"پایان بازه را وارد کن؛ مثل {d.year}/06/20 یا «20 شهریور {d.year}».")
    return DATE_TO


async def set_date_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        d = parse_jalali_date(update.message.text)
        start = parse_jalali_date(context.user_data["draft"]["date_from"])
        if d < start:
            raise ValueError("تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.")
        span = (d.togregorian() - start.togregorian()).days + 1
        if span > 31:
            raise ValueError("برای جلوگیری از فشار زیاد به سایت، هر پایش حداکثر ۳۱ روز باشد.")
    except ValueError as e:
        await update.message.reply_text(str(e))
        return DATE_TO
    context.user_data["draft"]["date_to"] = format_jalali(d)
    await update.message.reply_text("تعداد مسافر را بفرست (۱ تا ۶).")
    return PASSENGERS


async def set_passengers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    text = update.message.text.translate(table).strip()
    if not text.isdigit() or not (1 <= int(text) <= 6):
        await update.message.reply_text("یک عدد بین ۱ تا ۶ بفرست.")
        return PASSENGERS
    context.user_data["draft"]["passengers"] = int(text)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("مسافرین عادی", callback_data="type:normal")],
        [InlineKeyboardButton("ویژه برادران", callback_data="type:men")],
        [InlineKeyboardButton("ویژه خواهران", callback_data="type:women")],
    ])
    await update.message.reply_text("نوع مسافر را انتخاب کن:", reply_markup=kb)
    return PASSENGER_TYPE


async def set_passenger_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ptype = q.data.split(":", 1)[1]
    context.user_data["draft"]["passenger_type"] = ptype
    d = context.user_data["draft"]
    interval = context.application.bot_data["settings"].check_interval_seconds
    text = (
        "این پایش ساخته شود؟\n\n"
        f"🚉 {d['origin']} → {d['destination']}\n"
        f"📅 {d['date_from']} تا {d['date_to']}\n"
        f"👥 {d['passengers']} نفر\n"
        f"🎫 {TYPE_LABELS[ptype]}\n"
        f"⏱ هر {interval} ثانیه"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله", callback_data="confirm:yes"),
        InlineKeyboardButton("❌ لغو", callback_data="confirm:no"),
    ]])
    await q.message.reply_text(text, reply_markup=kb)
    return CONFIRM


async def confirm_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "confirm:no":
        await q.message.reply_text("لغو شد.", reply_markup=main_menu())
        return ConversationHandler.END

    storage: Storage = context.application.bot_data["storage"]
    settings: Settings = context.application.bot_data["settings"]
    d = context.user_data["draft"]
    watch_id = storage.add_watch(
        chat_id=q.message.chat_id,
        origin=d["origin"],
        destination=d["destination"],
        date_from=d["date_from"],
        date_to=d["date_to"],
        passengers=d["passengers"],
        passenger_type=d["passenger_type"],
        interval_seconds=settings.check_interval_seconds,
    )
    await q.message.reply_text(
        f"✅ پایش #{watch_id} فعال شد. اگر ظرفیت تازه‌ای دیده شود همین‌جا خبر می‌دهم.",
        reply_markup=main_menu(),
    )
    context.user_data.pop("draft", None)
    return ConversationHandler.END


async def list_watches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    storage: Storage = context.application.bot_data["storage"]
    watches = storage.list_active(q.message.chat_id)
    if not watches:
        await q.message.reply_text("پایش فعالی نداری.", reply_markup=main_menu())
        return

    for w in watches:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ توقف", callback_data=f"stop:{w.id}")]])
        await q.message.reply_text(
            f"#{w.id} | {w.origin} → {w.destination}\n"
            f"📅 {w.date_from} تا {w.date_to}\n"
            f"👥 {w.passengers} نفر | {TYPE_LABELS[w.passenger_type]}\n"
            f"⏱ هر {w.interval_seconds} ثانیه",
            reply_markup=kb,
        )


async def stop_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    watch_id = int(q.data.split(":", 1)[1])
    storage: Storage = context.application.bot_data["storage"]
    ok = storage.deactivate(watch_id, q.message.chat_id)
    await q.message.reply_text("متوقف شد." if ok else "این پایش پیدا نشد.", reply_markup=main_menu())


async def stop_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    storage: Storage = context.application.bot_data["storage"]
    storage.deactivate_all(q.message.chat_id)
    await q.message.reply_text("همه پایش‌های فعال متوقف شدند.", reply_markup=main_menu())


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("draft", None)
    await update.message.reply_text("لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END


def aggregate_fingerprint(results) -> str | None:
    available = [r for r in results if r.available]
    if not available:
        return None
    material = "|".join(sorted(f"{r.date}:{r.fingerprint}" for r in available))
    return hashlib.sha256(material.encode()).hexdigest()


def format_alert(watch: Watch, results) -> str:
    found = [r for r in results if r.available]
    lines = [
        "🚨 ظرفیت بلیت پیدا شد",
        f"🚉 {watch.origin} → {watch.destination}",
        f"👥 {watch.passengers} نفر | {TYPE_LABELS[watch.passenger_type]}",
        "",
    ]
    for result in found[:8]:
        lines.append(f"📅 {result.date}")
        for detail in result.details[:3]:
            clean = re.sub(r"\s+", " ", detail).strip()
            if clean:
                lines.append(f"• {clean[:250]}")
    lines += ["", "برای خرید، سریع وارد سایت رسمی رجا شو:", "https://www.raja.ir/"]
    return "\n".join(lines)


async def check_one_watch(app: Application, watch: Watch):
    storage: Storage = app.bot_data["storage"]
    scraper: RajaScraper = app.bot_data["scraper"]
    results = []

    try:
        for day in iter_jalali_days(watch.date_from, watch.date_to):
            result = await scraper.check_date(
                origin=watch.origin,
                destination=watch.destination,
                jalali_date=format_jalali(day),
                passengers=watch.passengers,
                passenger_type=watch.passenger_type,
            )
            results.append(result)
            await asyncio.sleep(0.8 + random.random() * 0.8)

        fp = aggregate_fingerprint(results)
        if fp and fp != watch.last_fingerprint:
            await app.bot.send_message(
                chat_id=watch.chat_id,
                text=format_alert(watch, results),
                disable_web_page_preview=True,
            )

        storage.set_fingerprint(watch.id, fp)

    except Exception:
        log.exception("Watch %s failed", watch.id)
        count, last_notified = storage.record_error(watch.id)
        now = time.time()
        if count >= 3 and (not last_notified or now - last_notified > 6 * 3600):
            await app.bot.send_message(
                chat_id=watch.chat_id,
                text=(
                    f"⚠️ پایش #{watch.id} چند بار پشت‌سرهم نتوانست سایت رجا را بخواند.\n"
                    "احتمالاً ظاهر سایت تغییر کرده یا دسترسی موقتاً محدود شده است. "
                    "پایش متوقف نشده و دوباره تلاش می‌کند."
                ),
            )
            storage.mark_error_notified(watch.id)
    finally:
        storage.schedule_next(watch.id, watch.interval_seconds)


async def worker(app: Application):
    while True:
        storage: Storage = app.bot_data["storage"]
        for watch in storage.due():
            await check_one_watch(app, watch)
        await asyncio.sleep(5)


async def post_init(app: Application):
    scraper: RajaScraper = app.bot_data["scraper"]
    await scraper.start()
    app.bot_data["worker_task"] = asyncio.create_task(worker(app))
    log.info("Worker started.")


async def post_shutdown(app: Application):
    task = app.bot_data.get("worker_task")
    if task:
        task.cancel()
    scraper: RajaScraper = app.bot_data["scraper"]
    await scraper.close()


def build_app() -> Application:
    settings = Settings.from_env()
    storage = Storage(settings.database_path)
    scraper = RajaScraper(
        base_url=settings.raja_base_url,
        headless=settings.raja_headless,
        debug_dir=settings.debug_dir,
    )

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data.update(settings=settings, storage=storage, scraper=scraper)

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_watch, pattern=r"^new$")],
        states={
            ORIGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_origin)],
            DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_destination)],
            DATE_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_date_from)],
            DATE_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_date_to)],
            PASSENGERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_passengers)],
            PASSENGER_TYPE: [CallbackQueryHandler(set_passenger_type, pattern=r"^type:")],
            CONFIRM: [CallbackQueryHandler(confirm_watch, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(list_watches, pattern=r"^list$"))
    app.add_handler(CallbackQueryHandler(stop_all, pattern=r"^stop_all$"))
    app.add_handler(CallbackQueryHandler(stop_watch, pattern=r"^stop:\d+$"))
    return app


def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
