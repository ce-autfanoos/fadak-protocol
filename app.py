import os
import re
from flask import Flask, request
import telebot
from telebot import types
from supabase import create_client, Client

# ==========================================
# خواندن متغیرهای محیطی از Render
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
COMPETITION_ACTIVE = os.environ.get("COMPETITION_ACTIVE", "false").lower() == "true"

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# ==========================================
# توابع کمکی و دیتابیس
# ==========================================
def to_english_digits(text: str) -> str:
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    english = "0123456789"
    trans_p = str.maketrans(persian, english)
    trans_a = str.maketrans(arabic, english)
    return text.translate(trans_p).translate(trans_a).strip()

def normalize_phone(phone: str) -> str:
    """استانداردسازی شماره موبایل به فرمت 09xxxxxxxxx"""
    phone = to_english_digits(phone)
    phone = re.sub(r"[^\d+]", "", phone)
    if phone.startswith("+98"):
        phone = "0" + phone[3:]
    elif phone.startswith("98") and len(phone) == 12:
        phone = "0" + phone[2:]
    elif phone.startswith("9") and len(phone) == 10:
        phone = "0" + phone
    return phone

def get_user(user_id: int):
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

def create_user(user_id: int, username: str):
    data = {
        "user_id": user_id,
        "username": username or "NoUsername",
        "reg_step": "WAIT_NAME",
        "current_stage": 1,
        "score": 0
    }
    res = supabase.table("users").insert(data).execute()
    return res.data[0] if res.data else data

def update_user(user_id: int, **kwargs):
    supabase.table("users").update(kwargs).eq("user_id", user_id).execute()

def is_field_taken(field_name: str, value: str) -> bool:
    res = supabase.table("users").select("user_id").eq(field_name, value).execute()
    return len(res.data) > 0

# ==========================================
# کیبوردها و پیام‌ها
# ==========================================
PRE_LAUNCH_TEXT = (
    "🟢 [SYSTEM ONLINE] \n"
    "⚪️ [اتصال امن برقرار شد]\n"
    ".\n"
    "🔴 به «پروتکل فدک» خوش آمدید.\n"
    "- تیم واکنش سریع، ورود شما را تأیید می‌کند. در حال حاضر، سرورهای آرشیو مرکزی در حال بازیابی و رمزگشایی اطلاعات هستند.\n\n"
    "⏳ وضعیت فعلی: در حال آماده‌سازی سند حقیقت...\n"
    "- لطفا منتظر بمانید تا اطلاع‌رسانی‌های لازم برای آغاز عملیات(شروع مسابقه) از طریق همین ربات به شما ارسال شود. به محض فعال شدن ترمینال، دستورات را از همین‌جا دریافت خواهید کرد.\n\n"
    "<tg-spoiler>🛡 آماده باشید. عملیات به زودی آغاز می‌شود!</tg-spoiler>\n\n"
    "اطلاع رسانی ها در :\n"
    "<a href='https://t.me/aut_fanoos'>فانوس</a>\n"
    "<a href='https://t.me/pooyesh_aut'>پویش</a>"
)

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🚀 ورود به عملیات (شروع مسابقه)"),
        types.KeyboardButton("👤 مشخصات من")
    )
    return markup

def get_phone_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    btn = types.KeyboardButton("📱 اشتراک‌گذاری سریع شماره تماس", request_contact=True)
    markup.add(btn)
    return markup

# ==========================================
# مسیرهای وب‌سرور Flask
# ==========================================
@app.route("/")
@app.route("/ping")
def ping():
    return "FADAK PROTOCOL BOT - ONLINE", 200

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.headers.get("content-type") == "application/json":
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return "OK", 200
    return "Forbidden", 403

@app.route("/set_webhook")
def set_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL env variable is not set!", 400
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook/{BOT_TOKEN}")
    return "✅ Webhook successfully connected!", 200

# ==========================================
# هندلرهای تلگرام (ثبت‌نام ۳ مرحله‌ای)
# ==========================================
@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    bot.send_message(
        message.chat.id,
        PRE_LAUNCH_TEXT,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    
    user = get_user(user_id)
    if not user:
        create_user(user_id, username)
        bot.send_message(
            message.chat.id,
            "🔐 <b>[احراز هویت اپراتور - گام ۱ از ۳]</b>\n"
            "برای ثبت در سیستم واکنش سریع و اهدای جوایز، لطفاً <b>نام و نام خانوادگی</b> کامل خود را به فارسی ارسال کنید:",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif user.get("reg_step") == "WAIT_NAME":
        bot.send_message(
            message.chat.id,
            "🔐 <b>[گام ۱ از ۳]</b> لطفاً <b>نام و نام خانوادگی</b> کامل خود را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif user.get("reg_step") == "WAIT_STUDENT_ID":
        bot.send_message(
            message.chat.id,
            "🎓 <b>[گام ۲ از ۳]</b> لطفاً <b>شماره دانشجویی</b> خود را وارد کنید:",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif user.get("reg_step") == "WAIT_PHONE":
        bot.send_message(
            message.chat.id,
            "📞 <b>[گام ۳ از ۳]</b> لطفاً <b>شماره موبایل</b> خود را (جهت هماهنگی اهدای جوایز) تایپ کنید یا روی دکمه زیر بزنید:",
            parse_mode="HTML",
            reply_markup=get_phone_keyboard()
        )
    else:
        bot.send_message(
            message.chat.id,
            f"✅ <b>اپراتور «{user.get('full_name')}» شناسایی شد.</b>\n"
            f"🎓 شماره دانشجویی: <code>{user.get('student_id')}</code>\n"
            f"📞 شماره تماس: <code>{user.get('phone_number')}</code>\n\n"
            "از منوی زیر می‌توانید وضعیت عملیات را بررسی کنید:",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )

def process_phone_submission(message, raw_phone: str):
    user_id = message.from_user.id
    user = get_user(user_id)
    phone = normalize_phone(raw_phone)
    
    if not re.match(r"^09\d{9}$", phone):
        bot.send_message(
            message.chat.id,
            "⚠️ شماره موبایل واردشده معتبر نیست! لطفاً شماره را به صورت ۱۱ رقمی (مثلاً <code>09123456789</code>) وارد کنید یا از دکمه پایین صفحه استفاده نمایید:",
            parse_mode="HTML",
            reply_markup=get_phone_keyboard()
        )
        return
        
    if is_field_taken("phone_number", phone):
        bot.send_message(
            message.chat.id,
            "⛔️ <b>[SECURITY ALERT: DUPLICATE PHONE]</b>\n"
            "این شماره موبایل قبلاً در سیستم ثبت شده است! لطفاً شماره موبایل متعلق به خودتان را وارد کنید:",
            parse_mode="HTML",
            reply_markup=get_phone_keyboard()
        )
        return
        
    update_user(user_id, phone_number=phone, reg_step="COMPLETED")
    bot.send_message(
        message.chat.id,
        "🟢 <b>[IDENTITY VERIFIED | هویت شما تایید شد]</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 نام اپراتور: <b>{user.get('full_name')}</b>\n"
        f"🎓 شماره دانشجویی: <code>{user.get('student_id')}</code>\n"
        f"📞 شماره تماس: <code>{phone}</code>\n\n"
        "ثبت‌نام شما در ترمینال «پروتکل فدک» تکمیل شد. به محض آغاز رسمی مسابقه، از طریق دکمه زیر می‌توانید وارد مرحله اول شوید.",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )

# هندلر مخصوص زمانی که کاربر روی دکمه «اشتراک‌گذاری سریع شماره تماس» می‌زند
@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    user = get_user(message.from_user.id)
    if not user or user.get("reg_step") != "WAIT_PHONE":
        return
    if message.contact and message.contact.phone_number:
        process_phone_submission(message, message.contact.phone_number)

@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    user_id = message.from_user.id
    text = message.text.strip()
    user = get_user(user_id)
    
    if not user:
        handle_start(message)
        return
        
    reg_step = user.get("reg_step")
    
    # گام ۱: نام و نام خانوادگی
    if reg_step == "WAIT_NAME":
        if len(text) < 4 or text.isdigit():
            bot.send_message(
                message.chat.id,
                "⚠️ نام واردشده معتبر نیست. لطفاً نام و نام خانوادگی واقعی خود را وارد کنید:"
            )
            return
            
        update_user(user_id, full_name=text, reg_step="WAIT_STUDENT_ID")
        bot.send_message(
            message.chat.id,
            f"✅ نام شما (<b>{text}</b>) ثبت شد.\n\n"
            "🎓 <b>[احراز هویت اپراتور - گام ۲ از ۳]</b>\n"
            "لطفاً <b>شماره دانشجویی</b> خود را ارسال کنید:\n"
            "<i>(توجه: هر شماره دانشجویی فقط یک‌بار قابل ثبت است)</i>",
            parse_mode="HTML"
        )
        return

    # گام ۲: شماره دانشجویی
    if reg_step == "WAIT_STUDENT_ID":
        st_id = to_english_digits(text)
        
        if not st_id.isdigit() or not (6 <= len(st_id) <= 12):
            bot.send_message(
                message.chat.id,
                "⚠️ فرمت شماره دانشجویی نامعتبر است! لطفاً فقط عدد شماره دانشجویی خود را ارسال کنید:"
            )
            return
            
        if is_field_taken("student_id", st_id):
            bot.send_message(
                message.chat.id,
                "⛔️ <b>[SECURITY ALERT: DUPLICATE ID]</b>\n"
                "این شماره دانشجویی قبلاً با یک اکانت دیگر ثبت شده است! لطفاً شماره دانشجویی صحیح خود را وارد کنید:",
                parse_mode="HTML"
            )
            return
            
        update_user(user_id, student_id=st_id, reg_step="WAIT_PHONE")
        bot.send_message(
            message.chat.id,
            f"✅ شماره دانشجویی <code>{st_id}</code> ثبت شد.\n\n"
            "📞 <b>[احراز هویت اپراتور - گام ۳ از ۳]</b>\n"
            "لطفاً جهت هماهنگی اهدای جوایز، <b>شماره موبایل</b> خود را تایپ کنید (مثلاً <code>09123456789</code>) یا روی دکمه زیر بزنید:",
            parse_mode="HTML",
            reply_markup=get_phone_keyboard()
        )
        return

    # گام ۳: شماره موبایل (اگر کاربر دستی تایپ کرد)
    if reg_step == "WAIT_PHONE":
        process_phone_submission(message, text)
        return

    # منوی اصلی پس از تکمیل ثبت‌نام
    if text == "👤 مشخصات من":
        bot.send_message(
            message.chat.id,
            f"👤 <b>اپراتور:</b> {user.get('full_name')}\n"
            f"🎓 <b>شماره دانشجویی:</b> <code>{user.get('student_id')}</code>\n"
            f"📞 <b>شماره تماس:</b> <code>{user.get('phone_number')}</code>\n"
            f"📍 <b>مرحله فعلی:</b> {user.get('current_stage')} از ۵\n"
            f"🏆 <b>امتیاز:</b> {user.get('score')}",
            parse_mode="HTML"
        )
        return
        
    if text == "🚀 ورود به عملیات (شروع مسابقه)":
        if not COMPETITION_ACTIVE:
            bot.send_message(
                message.chat.id,
                "⏳ <b>[TERMINAL LOCKED]</b>\n"
                "عملیات هنوز به صورت رسمی آغاز نشده است! هویت شما در سیستم ثبت شده و به محض باز شدن لایه اول، اطلاع‌رسانی خواهد شد.",
                parse_mode="HTML"
            )
        else:
            bot.send_message(
                message.chat.id,
                "🟢 ترمینال فعال است! (آماده اتصال به مرحله ۱...)"
            )
        return

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
