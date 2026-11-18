import os
import re
import time
import hashlib
from flask import Flask, request
import telebot
from telebot import types
from supabase import create_client, Client

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

COMPETITION_ACTIVE = os.environ.get("COMPETITION_ACTIVE", "false").lower() == "true"
MAX_UNLOCKED_STAGE = int(os.environ.get("MAX_UNLOCKED_STAGE", "1"))
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

GUIDE_PDF_FILE_ID = os.environ.get("GUIDE_PDF_FILE_ID", "")

STAGE_FILE_IDS = {
    1: os.environ.get("STAGE1_FILE_ID", ""),
    2: os.environ.get("STAGE2_FILE_ID", ""),
    3: os.environ.get("STAGE3_FILE_ID", ""),
    4: os.environ.get("STAGE4_FILE_ID", ""),
    5: os.environ.get("STAGE5_FILE_ID", ""),
}

STAGE_LOCAL_FILES = {
    1: "traffic_log.txt",
    2: "intercept_dump.json",
    3: "encrypted_fadak.txt",
    4: "reactor_sim.py",
    5: "final_message.wav",
}

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

RATE_LIMIT_MAX = 3
LOCKOUT_SECONDS = 15

def to_english_digits(text: str) -> str:
    persian = "۰۱۲۳۴۵۶۷۸۹"
    arabic = "٠١٢٣٤٥٦٧٨٩"
    english = "0123456789"
    return text.translate(str.maketrans(persian, english)).translate(str.maketrans(arabic, english)).strip()

def normalize_answer(text: str) -> str:
    if not text:
        return ""
    text = to_english_digits(text).upper()
    replacements = {
        "ي": "ی", "ك": "ک", "أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه",
        "َ": "", "ِ": "", "ُ": "", "ّ": "", "ْ": "", "ٌ": "", "ٍ": "", "ً": "",
        "\u200c": "", " ": "", "_": "", "-": "", "«": "", "»": ""
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def hash_text(text: str) -> str:
    return hashlib.sha256(normalize_answer(text).encode("utf-8")).hexdigest()

VALID_STAGE_HASHES = {
    1: {hash_text("غصب"), hash_text("GHASB")},
    2: {
        hash_text("ارث فرقه"),
        hash_text("ارث الفرقه"),
        hash_text("ارث تفرقه"),
        hash_text("فرقه ارث"),
        hash_text("ارث"),
        hash_text("ERS")
    },
    3: {hash_text("علی"), hash_text("ALI")},
    4: {hash_text("حق"), hash_text("HAQ")},
    5: {hash_text("فدک حق لنا"), hash_text("FADAKHAQLANA")}
}

AI_TRAP_HASH_STAGE1 = {hash_text("سقیفه"), hash_text("فراموشی"), hash_text("SAQIFEH")}
AI_TRAP_HASH_STAGE2 = {hash_text("زکات"), hash_text("ZAKAT"), hash_text("شهادت محمد"), hash_text("ولایت"), hash_text("خلافت")}

def normalize_phone(phone: str) -> str:
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

STAGE_MESSAGES = {
    1: (
        "⚠️ <b>[SYSTEM ALERT: MASSIVE LOG SPOOFING DETECTED]</b>\n"
        "<code>[LAYER 01/05 : شکار هویت (Data Mining)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>وضعیت:</b> بدافزار «انکار» (DENIAL) برای پنهان کردن ردپای نفوذ خود به بند اول <b>سند حقیقت (خطبه فدکیه)</b>، فایل لاگ سرور را پر از داده‌های انحرافی و تله‌های هوش مصنوعی کرده است!\n\n"
        "🛠 <b>ماموریت شما:</b>\n"
        "۱. فایل <code>traffic_log.txt</code> پیوست‌شده را بررسی کنید.\n"
        "۲. در میان صدها خط گزارش، خطوطی را پیدا کنید که هم‌زمان دارای تگ <code>[FADAK-ROOT]</code> و وضعیت معتبر <code>[STATUS:VALID]</code> هستند (مراقب خطوط <code>CORRUPTED</code> و تله‌های <code>FAKE_AI_TRAP</code> باشید!).\n"
        "۳. سه تکه داده هگز (<code>PART:1/3</code> تا <code>3/3</code>) را به هم بچسبانید و از فرمت Hex به متن UTF-8 تبدیل کنید.\n\n"
        "🕯 <i>«آنگاه که تصمیم بر منع حق گرفتند، نخستین زخم بر پیکر عدالت چه نام داشت؟ (رجوع به بند اول راهنمای خطبه: FA-001)»</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>کلمه کلیدی اول را پس از کشف، همین‌جا ارسال کنید:</b>"
    ),
    2: (
        "📡 <b>[SYSTEM ALERT: DEEP PACKET INSPECTION REQUIRED]</b>\n"
        "<code>[LAYER 02/05 : پروتکل ارتباطی (Packet Analysis)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🕵️‍♂️ <b>گزارش وضعیت:</b>\n"
        "بدافزار «انکار» برای قطع پیوند میان <b>فلسفه امامت</b> و <b>استدلال قرآنی حضرت زهرا (س)</b>، ترافیک شبکه روی درگاه <code>UDP 1400</code> را آلوده کرده و یک جریان انحرافی (Honeypot) برای فریب ابزارهای خودکار و هوش مصنوعی روی همین پورت ایجاد نموده است!\n\n"
        "🛠 <b>ماموریت فنی شما:</b>\n"
        "۱. فایل ضبط ترافیک شبکه (<code>intercept_dump.json</code>) را بررسی کنید.\n"
        "۲. پیش از رمزگشایی پورت <code>1400</code>، پکت گزارش ممیزی فایروال (<code>ICMP_AUDIT</code> از مبدا <code>10.0.0.1</code>) را در میان پکت‌ها پیدا کنید تا فرمول تشخیص پکت‌های واقعی و کلید رمزگشایی را کشف نمایید.\n"
        "۳. کلید بازگشایی این لایه، بدون مراجعه دقیق به <b>سند حقیقت (Fadak_Guide.pdf)</b> و تطبیق شماره و محتوای بندها به دست نخواهد آمد!\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>پس از بازیابی پیام اصلی، کلید خواسته شده را ارسال کنید:</b>"
    ),
    3: "🔐 <b>[LAYER 03/05 : رمزنگاری کهن]</b>\nدر حال بارگذاری متن رمزنگاری‌شده...",
    4: "🔥 <b>[LAYER 04/05 : شبیه‌سازی واکنش]</b>\nهشدار حرارتی اتاق سرور فعال شد...",
    5: "🎙 <b>[LAYER 05/05 : بازسازی نهایی]</b>\nسیگنال صوتی نهایی دریافت شد..."
}

ERROR_TEXTS = [
    "⛔️ <b>[ACCESS DENIED]</b>\nسیستم امنیتی دشمن شما را شناسایی کرد! دسترسی رد شد. (مراقب تله‌های انحرافی داخل فایل باشید!)",
    "⚠️ <b>[SIGNATURE MISMATCH]</b>\nکلید واردشده با هشِ سند حقیقت همخوانی ندارد! دستورالعمل داخل فایل را با دقت بررسی کنید.",
    "🚨 <b>[INTRUSION ALERT]</b>\nپاسخ نادرست! فایروال بدافزار «انکار» در حال ردیابی IP ترمینال شماست!"
]

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🚀 ورود به عملیات / دریافت سوال فعلی"),
        types.KeyboardButton("🏆 جدول رده‌بندی"),
        types.KeyboardButton("👤 مشخصات و امتیاز من")
    )
    return markup

def get_phone_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("📱 اشتراک‌گذاری سریع شماره تماس", request_contact=True))
    return markup

def send_stage_to_user(chat_id: int, stage: int):
    if not COMPETITION_ACTIVE:
        bot.send_message(
            chat_id,
            "⏳ <b>[TERMINAL LOCKED]</b>\n"
            "عملیات هنوز به صورت رسمی آغاز نشده است! به محض باز شدن لایه اول، از طریق همین ربات اطلاع‌رسانی خواهد شد.",
            parse_mode="HTML"
        )
        return

    if stage > 5:
        bot.send_message(
            chat_id,
            "🌟 <b>[SYSTEM RESTORED]</b>\nشما هر ۵ لایه امنیتی «پروتکل فدک» را با موفقیت به پایان رسانده‌اید!",
            parse_mode="HTML"
        )
        return

    if stage > MAX_UNLOCKED_STAGE:
        bot.send_message(
            chat_id,
            f"🔒 <b>[STAGE {stage} LOCKED // لایه {stage} در حال رمزگشایی توسط مرکز]</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"تبریک اپراتور! شما تمام مراحل فعال تا این لحظه را با موفقیت حل کرده‌اید و امتیاز شما ثبت شده است.\n\n"
            f"⏳ <b>لایه {stage}</b> طبق جدول زمان‌بندی عملیات بازگشایی خواهد شد و از طریق همین ربات به شما اطلاع داده می‌شود.",
            parse_mode="HTML"
        )
        return

    msg_text = STAGE_MESSAGES.get(stage, f"مرحله {stage}")
    bot.send_message(chat_id, msg_text, parse_mode="HTML")

    file_id = STAGE_FILE_IDS.get(stage)
    local_path = STAGE_LOCAL_FILES.get(stage)
    
    if file_id:
        bot.send_document(chat_id, file_id, caption=f"📁 فایل عملیاتی لایه {stage}")
    elif local_path and os.path.exists(local_path):
        with open(local_path, "rb") as f:
            bot.send_document(chat_id, f, caption=f"📁 فایل عملیاتی لایه {stage}")

    if stage == 1:
        if GUIDE_PDF_FILE_ID:
            bot.send_document(chat_id, GUIDE_PDF_FILE_ID, caption="📜 سند حقیقت (راهنمای کامل خطبه فدکیه)")
        elif os.path.exists("Fadak_Guide.pdf"):
            with open("Fadak_Guide.pdf", "rb") as pdf_file:
                bot.send_document(chat_id, pdf_file, caption="📜 سند حقیقت (راهنمای کامل خطبه فدکیه)")

@app.route("/")
@app.route("/ping")
def ping():
    return "FADAK PROTOCOL BOT - ONLINE", 200

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.headers.get("content-type") == "application/json":
        try:
            json_string = request.get_data().decode("utf-8")
            update = telebot.types.Update.de_json(json_string)
            bot.process_new_updates([update])
        except Exception as e:
            print(f"❌ Webhook Error: {e}")
        return "OK", 200
    return "Forbidden", 403

@app.route("/set_webhook")
def set_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL env variable is not set!", 400
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL.rstrip('/')}/webhook/{BOT_TOKEN}")
    return "✅ Webhook successfully connected!", 200

@bot.message_handler(content_types=['document', 'audio', 'voice'], func=lambda m: m.from_user.id in ADMIN_IDS)
def admin_get_file_id(message):
    fid = None
    fname = "file"
    if message.document:
        fid = message.document.file_id
        fname = message.document.file_name
    elif message.audio:
        fid = message.audio.file_id
    if fid:
        bot.reply_to(
            message,
            f"📎 <b>File Name:</b> <code>{fname}</code>\n"
            f"🔑 <b>TELEGRAM FILE_ID:</b>\n<code>{fid}</code>",
            parse_mode="HTML"
        )

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ متن پیام را جلوی `/broadcast` بنویسید.")
        return
    broadcast_text = parts[1]
    res = supabase.table("users").select("user_id").execute()
    users = res.data if res.data else []
    status_msg = bot.reply_to(message, f"⏳ در حال ارسال به {len(users)} نفر...")
    ok, fail = 0, 0
    for u in users:
        try:
            bot.send_message(u.get("user_id"), broadcast_text, parse_mode="HTML", disable_web_page_preview=True)
            ok += 1
            time.sleep(0.05)
        except Exception:
            fail += 1
    bot.edit_message_text(f"✅ ارسال تمام شد!\nموفق: {ok} | ناموفق: {fail}", message.chat.id, status_msg.message_id)

@bot.message_handler(commands=['broadcast_pdf'])
def handle_broadcast_pdf(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        bot.reply_to(
            message,
            "⚠️ لطفاً ابتدا فایل را بفرستید، سپس روی آن Reply کنید و بنویسید:\n<code>/broadcast_pdf</code>",
            parse_mode="HTML"
        )
        return
    file_id = message.reply_to_message.document.file_id
    parts = message.text.split(maxsplit=1)
    caption_text = parts[1] if len(parts) > 1 else (
        "📜 <b>سند حقیقت | راهنمای کامل خطبه فدکیه</b>\n"
        "این فایل مرجع اصلی شما در طول مراحل عملیات «پروتکل فدک» خواهد بود. آن را ذخیره و مطالعه کنید."
    )
    res = supabase.table("users").select("user_id").execute()
    users = res.data if res.data else []
    status_msg = bot.reply_to(message, f"⏳ در حال ارسال فایل به {len(users)} کاربر...")
    ok, fail = 0, 0
    for u in users:
        try:
            bot.send_document(u.get("user_id"), file_id, caption=caption_text, parse_mode="HTML")
            ok += 1
            time.sleep(0.05)
        except Exception:
            fail += 1
    bot.edit_message_text(f"✅ ارسال فایل تمام شد!\nموفق: {ok} | ناموفق: {fail}", message.chat.id, status_msg.message_id)

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    username = message.from_user.username
    bot.send_message(message.chat.id, PRE_LAUNCH_TEXT, parse_mode="HTML", disable_web_page_preview=True)
    
    user = get_user(user_id)
    if not user:
        create_user(user_id, username)
        bot.send_message(
            message.chat.id,
            "🔐 <b>[احراز هویت اپراتور - گام ۱ از ۳]</b>\n"
            "لطفاً <b>نام و نام خانوادگی</b> کامل خود را به فارسی ارسال کنید:",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif user.get("reg_step") != "COMPLETED":
        step = user.get("reg_step")
        if step == "WAIT_NAME":
            bot.send_message(message.chat.id, "🔐 لطفاً <b>نام و نام خانوادگی</b> خود را وارد کنید:", parse_mode="HTML")
        elif step == "WAIT_STUDENT_ID":
            bot.send_message(message.chat.id, "🎓 لطفاً <b>شماره دانشجویی</b> خود را وارد کنید:", parse_mode="HTML")
        elif step == "WAIT_PHONE":
            bot.send_message(message.chat.id, "📞 لطفاً <b>شماره موبایل</b> خود را وارد کنید:", parse_mode="HTML", reply_markup=get_phone_keyboard())
    else:
        bot.send_message(
            message.chat.id,
            f"✅ <b>اپراتور «{user.get('full_name')}» متصل شد.</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )
        if COMPETITION_ACTIVE:
            send_stage_to_user(message.chat.id, user.get("current_stage", 1))

def process_phone_submission(message, raw_phone: str):
    user_id = message.from_user.id
    user = get_user(user_id)
    phone = normalize_phone(raw_phone)
    
    if not re.match(r"^09\d{9}$", phone):
        bot.send_message(message.chat.id, "⚠️ شماره موبایل معتبر نیست! به صورت ۱۱ رقمی (09...) وارد کنید:", reply_markup=get_phone_keyboard())
        return
    if is_field_taken("phone_number", phone):
        bot.send_message(message.chat.id, "⛔️ این شماره موبایل قبلاً ثبت شده است!", reply_markup=get_phone_keyboard())
        return
        
    update_user(user_id, phone_number=phone, reg_step="COMPLETED")
    bot.send_message(
        message.chat.id,
        "🟢 <b>[IDENTITY VERIFIED | هویت شما تایید شد]</b>\n"
        f"👤 اپراتور: <b>{user.get('full_name')}</b>\n"
        f"🎓 شماره دانشجویی: <code>{user.get('student_id')}</code>\n"
        f"📞 شماره تماس: <code>{phone}</code>",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )
    if COMPETITION_ACTIVE:
        send_stage_to_user(message.chat.id, 1)

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    user = get_user(message.from_user.id)
    if user and user.get("reg_step") == "WAIT_PHONE" and message.contact:
        process_phone_submission(message, message.contact.phone_number)

def show_leaderboard(chat_id: int):
    res = supabase.table("users").select("full_name, current_stage, score, finished_at").eq("reg_step", "COMPLETED").order("score", desc=True).limit(10).execute()
    rows = res.data if res.data else []
    if not rows:
        bot.send_message(chat_id, "هنوز امتیازی ثبت نشده است.")
        return
    text = "🏆 <b>جدول رده‌بندی زنده | پروتکل فدک</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        m = medals[i] if i < 3 else f"<code>#{i+1}</code>"
        stg = r.get("current_stage", 1)
        status = "✅ اتمام عملیات" if stg > 5 else f"لایه {stg}"
        text += f"{m} <b>{r.get('full_name')}</b> — امتیاز: <code>{r.get('score', 0)}</code> ({status})\n"
    bot.send_message(chat_id, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: True)
def handle_messages(message):
    user_id = message.from_user.id
    text = message.text.strip()
    user = get_user(user_id)
    
    if not user:
        handle_start(message)
        return
        
    reg_step = user.get("reg_step")
    if reg_step == "WAIT_NAME":
        if len(text) < 4 or text.isdigit():
            bot.send_message(message.chat.id, "⚠️ لطفاً نام و نام خانوادگی کامل و واقعی خود را وارد کنید:")
            return
        update_user(user_id, full_name=text, reg_step="WAIT_STUDENT_ID")
        bot.send_message(message.chat.id, "✅ ثبت شد.\n🎓 <b>[گام ۲ از ۳]</b> لطفاً <b>شماره دانشجویی</b> خود را ارسال کنید:", parse_mode="HTML")
        return

    if reg_step == "WAIT_STUDENT_ID":
        st_id = to_english_digits(text)
        if not st_id.isdigit() or not (6 <= len(st_id) <= 12):
            bot.send_message(message.chat.id, "⚠️ شماره دانشجویی نامعتبر است! فقط عدد وارد کنید:")
            return
        if is_field_taken("student_id", st_id):
            bot.send_message(message.chat.id, "⛔️ این شماره دانشجویی قبلاً ثبت شده است!")
            return
        update_user(user_id, student_id=st_id, reg_step="WAIT_PHONE")
        bot.send_message(message.chat.id, "✅ ثبت شد.\n📞 <b>[گام ۳ از ۳]</b> لطفاً <b>شماره موبایل</b> خود را وارد کنید یا دکمه زیر را بزنید:", parse_mode="HTML", reply_markup=get_phone_keyboard())
        return

    if reg_step == "WAIT_PHONE":
        process_phone_submission(message, text)
        return

    if text == "🏆 جدول رده‌بندی":
        show_leaderboard(message.chat.id)
        return

    if text == "👤 مشخصات و امتیاز من":
        bot.send_message(
            message.chat.id,
            f"👤 <b>اپراتور:</b> {user.get('full_name')}\n"
            f"🎓 <b>شماره دانشجویی:</b> <code>{user.get('student_id')}</code>\n"
            f"📍 <b>مرحله فعلی:</b> {min(user.get('current_stage', 1), 5)} از ۵\n"
            f"💎 <b>امتیاز کل:</b> <code>{user.get('score', 0)}</code>",
            parse_mode="HTML"
        )
        return

    if text == "🚀 ورود به عملیات / دریافت سوال فعلی":
        send_stage_to_user(message.chat.id, user.get("current_stage", 1))
        return

    if not COMPETITION_ACTIVE:
        bot.send_message(message.chat.id, "⏳ عملیات هنوز آغاز نشده است!")
        return

    stage = user.get("current_stage", 1)
    if stage > 5 or stage > MAX_UNLOCKED_STAGE:
        send_stage_to_user(message.chat.id, stage)
        return

    now = time.time()
    locked_until = float(user.get("locked_until") or 0)
    if now < locked_until:
        rem = int(locked_until - now)
        bot.send_message(
            message.chat.id,
            f"🔒 <b>[SECURITY LOCKOUT]</b>\nبه دلیل ۳ تلاش ناموفق، ترمینال شما به مدت <b>{rem} ثانیه</b> دیگر توسط فایروال مسدود است!",
            parse_mode="HTML"
        )
        return

    ans_hash = hash_text(text)

    if stage == 1 and ans_hash in AI_TRAP_HASH_STAGE1:
        fails = int(user.get("fail_count") or 0) + 1
        update_user(user_id, fail_count=fails)
        bot.send_message(
            message.chat.id,
            "🤖 <b>[HONEYPOT TRIGGERED // تله هوش مصنوعی فعال شد!]</b>\n"
            "اپراتور گرامی! کلمه‌ای که وارد کردید مربوط به خط انحرافی (FAKE_AI_TRAP) داخل لاگ است که برای فریب دادن هوش مصنوعی طراحی شده بود! لطفاً فقط خطوط دارای <code>STATUS:VALID</code> را بررسی کنید. (-۵ امتیاز جریمه)",
            parse_mode="HTML"
        )
        return

    if stage == 2 and ans_hash in AI_TRAP_HASH_STAGE2:
        fails = int(user.get("fail_count") or 0) + 1
        update_user(user_id, fail_count=fails)
        bot.send_message(
            message.chat.id,
            "🤖 <b>[HONEYPOT TRIGGERED // تله هوش مصنوعی فعال شد!]</b>\n"
            "اپراتور گرامی! کلمه «زکات» مربوط به جریان فریبنده‌ای (ttl=64) است که برای به اشتباه انداختن هوش مصنوعی طراحی شده بود!\n"
            "لطفاً ابتدا پکت <code>ICMP_AUDIT</code> را در فایل پیدا کنید و خودتان با کمک «سند حقیقت» جریان اصلی را رمزگشایی نمایید! (-۵ امتیاز جریمه)",
            parse_mode="HTML"
        )
        return

    if ans_hash in VALID_STAGE_HASHES.get(stage, set()):
        fails = int(user.get("fail_count") or 0)
        earned = max(50, (stage * 100) - (fails * 5))
        new_score = int(user.get("score") or 0) + earned
        new_stage = stage + 1

        update_user(
            user_id,
            current_stage=new_stage,
            score=new_score,
            fail_count=0,
            locked_until=0,
            finished_at=now
        )

        if stage == 1:
            bot.send_message(
                message.chat.id,
                f"🟢 <b>[ACCESS GRANTED]</b>\n"
                f"کلید اول شناسایی شد: <b>«غَصْب»</b> (+{earned} امتیاز)\n"
                "در حال باز کردن فایل ترافیک شبکه...",
                parse_mode="HTML"
            )
        elif stage == 2:
            bot.send_message(
                message.chat.id,
                f"🟢 <b>[ACCESS GRANTED]</b>\n"
                f"زنجیره ارتباطی بازیابی شد! پیوند میان <b>«امامت (امان از فرقه)»</b> و مطالبه <b>«إرْث»</b> در سند حقیقت تثبیت گردید. (+{earned} امتیاز)\n"
                "در حال انتقال به لایه سوم...",
                parse_mode="HTML"
            )
        else:
            bot.send_message(message.chat.id, f"🟢 <b>[ACCESS GRANTED]</b>\nلایه {stage} شکسته شد! (+{earned} امتیاز)", parse_mode="HTML")

        send_stage_to_user(message.chat.id, new_stage)
    else:
        fails = int(user.get("fail_count") or 0) + 1
        if fails >= RATE_LIMIT_MAX:
            update_user(user_id, fail_count=0, locked_until=now + LOCKOUT_SECONDS)
            bot.send_message(
                message.chat.id,
                f"🚨 <b>[INTRUSION DETECTED]</b>\nشما ۳ بار پیاپی پاسخ اشتباه وارد کردید! سیستم امنیتی دشمن شما را به مدت <b>{LOCKOUT_SECONDS} ثانیه</b> مسدود کرد.",
                parse_mode="HTML"
            )
        else:
            update_user(user_id, fail_count=fails)
            err = ERROR_TEXTS[fails % len(ERROR_TEXTS)]
            bot.send_message(message.chat.id, f"{err}\n<i>(خطای {fails} از {RATE_LIMIT_MAX})</i>", parse_mode="HTML")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
