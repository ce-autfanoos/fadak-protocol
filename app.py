import os
from flask import Flask, request
import telebot

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "") # مثلا https://fadak-protocol.onrender.com

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

# مسیر اصلی و مسیر پینگ برای UptimeRobot (بیدار نگه داشتن ۲۴ ساعته ربات)
@app.route("/")
@app.route("/ping")
def ping():
    return "FADAK PROTOCOL BOT - ONLINE", 200

# دریافت پیام‌های تلگرام از طریق Webhook
@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    return "Forbidden", 403

# تنظیم خودکار وبهوک با یک بار باز کردن این آدرس در مرورگر
@app.route("/set_webhook")
def set_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL env variable is not set!", 400
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook/{BOT_TOKEN}")
    return "✅ Webhook successfully connected!", 200

# پیام خوش‌آمدگویی پیش از شروع مسابقه
@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "🟢 [SYSTEM ONLINE] \n"
        "⚪️ [اتصال امن برقرار شد]\n"
        ".\n"
        "🔴 به «پروتکل فدک» خوش آمدید.\n"
        "- تیم واکنش سریع، ورود شما را تأیید می‌کند. در حال حاضر، سرورهای آرشیو مرکزی در حال بازیابی و رمزگشایی اطلاعات هستند.\n\n\n"
        "⏳ وضعیت فعلی: در حال آماده‌سازی سند حقیقت...\n"
        "- لطفا منتظر بمانید تا اطلاع‌رسانی‌های لازم برای آغاز عملیات(شروع مسابقه) از طریق همین ربات به شما ارسال شود. به محض فعال شدن ترمینال، دستورات را از همین‌جا دریافت خواهید کرد.\n\n\n"
        "<tg-spoiler>🛡 آماده باشید. عملیات به زودی آغاز می‌شود!</tg-spoiler>\n\n"
        "اطلاع رسانی ها در :\n"
        "<a href='https://t.me/aut_fanoos'>فانوس</a>\n"
        "<a href='https://t.me/pooyesh_aut'>پویش</a>"
    )
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
