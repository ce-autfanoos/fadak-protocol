import os
import re
import io
import csv
import time
import base64
import hashlib
from flask import Flask, request, make_response, jsonify
import telebot
from telebot import types, apihelper
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
    3: "",
    4: "reactor_sim.py",
    5: "final_message.wav",
}

SERVER_START_TIME = time.time()
STAGE_UNLOCK_TS = float(os.environ.get("STAGE_UNLOCK_TS", "0") or 0) or SERVER_START_TIME
STAGE5_TOKEN_TTL = 200
STAGE5_SECRET = "FADAK_S5_ETERNAL_1448"

apihelper.CONNECT_TIMEOUT = 15
apihelper.READ_TIMEOUT = 25
apihelper.RETRY_ON_ERROR = True
apihelper.MAX_RETRIES = 3

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)
app = Flask(__name__)
app.json.ensure_ascii = False
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
        "ي": "ی", "ك": "ک", "أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ؤ": "و",
        "َ": "", "ِ": "", "ُ": "", "ّ": "", "ْ": "", "ٌ": "", "ٍ": "", "ً": "",
        "\u200c": "", " ": "", "_": "", "-": "", "«": "", "»": "", "(": "", ")": ""
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def hash_text(text: str) -> str:
    return hashlib.sha256(normalize_answer(text).encode("utf-8")).hexdigest()

def get_user_stage5_code(student_id: str) -> str:
    clean_id = to_english_digits(str(student_id or "")).strip()
    digest = hashlib.sha256(f"{STAGE5_SECRET}:{clean_id}".encode("utf-8")).hexdigest()
    return str(int(digest[:8], 16) % 9000 + 1000)

def create_stage5_token(student_id: str) -> str:
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{student_id}.{ts}.{STAGE5_SECRET}".encode("utf-8")).hexdigest()[:16]
    return f"{student_id}.{ts}.{sig}"

def verify_stage5_token(token: str):
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None, "Invalid token format."
    student_id, ts_str, sig = parts
    expected_sig = hashlib.sha256(f"{student_id}.{ts_str}.{STAGE5_SECRET}".encode("utf-8")).hexdigest()[:16]
    if sig != expected_sig:
        return None, "Invalid token signature."
    try:
        ts = int(ts_str)
    except ValueError:
        return None, "Invalid token timestamp."
    if time.time() - ts > STAGE5_TOKEN_TTL:
        return None, "Session token expired (200 seconds limit exceeded). Request a new handshake."
    return student_id, None

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
    3: {
        hash_text("امیرالمومنین"),
        hash_text("امیر المومنین")
    },
    4: {hash_text("حق")}
}

AI_TRAP_HASH_STAGE1 = {hash_text("سقیفه"), hash_text("فراموشی"), hash_text("SAQIFEH")}
AI_TRAP_HASH_STAGE2 = {hash_text("زکات"), hash_text("ZAKAT"), hash_text("شهادت محمد"), hash_text("ولایت"), hash_text("خلافت")}

LAYER3_KEY_B64 = base64.b64encode("امیرالمومنین".encode("utf-8")).decode("utf-8")

VALID_LAYER5_CHAINS = {
    hashlib.sha256("غصب-ارث-امیرالمومنین-حق".encode("utf-8")).hexdigest(),
    hashlib.sha256("غصب-ارث-فرقه-امیرالمومنین-حق".encode("utf-8")).hexdigest(),
    hashlib.sha256("غصب-ارث-امیر المومنین-حق".encode("utf-8")).hexdigest(),
    hashlib.sha256("غصب-ارث-امیرالمؤمنین-حق".encode("utf-8")).hexdigest()
}

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

LAYER3_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پروتکل فدک | لایه سوم</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700;800;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        fadak: {
                            bg: '#041114',
                            card: '#081c21',
                            border: '#11424a',
                            emerald: '#00ff9d',
                            glow: '#2ee59d',
                            red: '#ff3b4e',
                            muted: '#81a8b0'
                        }
                    },
                    fontFamily: {
                        sans: ['Vazirmatn', 'sans-serif']
                    }
                }
            }
        }
    </script>
    <style>
        body {
            background-color: #041114;
            color: #d6f2ee;
            font-family: 'Vazirmatn', sans-serif;
            background-image:
                radial-gradient(circle at 50% 10%, rgba(0, 255, 157, 0.12) 0%, transparent 45%),
                radial-gradient(circle at 85% 80%, rgba(255, 59, 78, 0.08) 0%, transparent 40%),
                linear-gradient(rgba(4, 17, 20, 0.96), rgba(4, 17, 20, 0.98));
        }
        .cyber-card {
            background: rgba(8, 28, 33, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(46, 229, 157, 0.22);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55);
        }
        .laser-accent {
            position: fixed;
            height: 1.5px;
            background: linear-gradient(90deg, transparent, #ff3b4e, transparent);
            box-shadow: 0 0 8px #ff3b4e;
            transform: rotate(-30deg);
            pointer-events: none;
        }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4 selection:bg-fadak-emerald selection:text-black">
    <div class="laser-accent w-72 top-20 -left-12 opacity-45"></div>
    <div class="laser-accent w-96 bottom-24 -right-20 opacity-35"></div>
    <main class="max-w-xl w-full relative z-10">
        <div class="cyber-card rounded-2xl p-7 sm:p-10 border-r-4 border-r-fadak-red">
            <div class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-xl bg-fadak-bg border border-fadak-border text-xs mb-6">
                <span class="w-2 h-2 rounded-full bg-fadak-red animate-pulse"></span>
                <span class="font-bold text-fadak-red tracking-wider font-mono">ARCHIVE // LAYER 03 // 403_SEALED</span>
            </div>
            <div class="flex items-center gap-3.5 mb-4">
                <div class="w-12 h-12 rounded-xl border border-fadak-red/40 flex items-center justify-center bg-fadak-bg text-fadak-red shrink-0">
                    <i class="fa-solid fa-lock text-lg"></i>
                </div>
                <h1 class="text-xl sm:text-2xl font-black text-white leading-snug">
                    تالار ولایت — دسترسی مستقیم مسدود است
                </h1>
            </div>
            <p class="text-gray-300 text-sm sm:text-base leading-8 text-justify mb-6">
                آنچه در برابر چشم است، تنها پوسته ای از آرشیو مرکزی است...
            </p>
            <div class="border-t border-fadak-border/60 pt-4 flex items-center justify-between text-xs text-fadak-muted font-mono">
                <span class="flex items-center gap-2">
                    <i class="fa-solid fa-shield-halved text-fadak-emerald"></i>
                    FADAK PROTOCOL
                </span>
                <span class="text-fadak-glow">403_SEALED</span>
            </div>
        </div>
    </main>
</body>
</html>"""

LAYER5_HTML = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>پروتکل فدک | لایه پنجم — مهر جاودانه</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700;800;900&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        fadak: {
                            bg: '#041114',
                            card: '#081c21',
                            border: '#11424a',
                            emerald: '#00ff9d',
                            glow: '#2ee59d',
                            red: '#ff3b4e',
                            muted: '#81a8b0'
                        }
                    },
                    fontFamily: {
                        sans: ['Vazirmatn', 'sans-serif']
                    }
                }
            }
        }
    </script>
    <style>
        body {
            background-color: #041114;
            color: #d6f2ee;
            font-family: 'Vazirmatn', sans-serif;
            background-image:
                radial-gradient(circle at 50% 10%, rgba(0, 255, 157, 0.14) 0%, transparent 45%),
                radial-gradient(circle at 85% 80%, rgba(255, 59, 78, 0.08) 0%, transparent 40%),
                linear-gradient(rgba(4, 17, 20, 0.96), rgba(4, 17, 20, 0.98));
        }
        .cyber-card {
            background: rgba(8, 28, 33, 0.85);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(46, 229, 157, 0.25);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.55);
        }
        .laser-accent {
            position: fixed;
            height: 1.5px;
            background: linear-gradient(90deg, transparent, #00ff9d, transparent);
            box-shadow: 0 0 8px #00ff9d;
            transform: rotate(-30deg);
            pointer-events: none;
        }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4 selection:bg-fadak-emerald selection:text-black">
    <div class="laser-accent w-72 top-20 -left-12 opacity-40"></div>
    <div class="laser-accent w-96 bottom-24 -right-20 opacity-30"></div>
    <main class="max-w-xl w-full relative z-10">
        <div class="cyber-card rounded-2xl p-7 sm:p-10 border-r-4 border-r-fadak-emerald">
            <div class="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-xl bg-fadak-bg border border-fadak-border text-xs mb-6">
                <span class="w-2 h-2 rounded-full bg-fadak-emerald animate-pulse"></span>
                <span class="font-bold text-fadak-emerald tracking-wider font-mono">ARCHIVE // FINAL LAYER 05 // ETERNAL SEAL</span>
            </div>
            <div class="flex items-center gap-3.5 mb-4">
                <div class="w-12 h-12 rounded-xl border border-fadak-emerald/40 flex items-center justify-center bg-fadak-bg text-fadak-emerald shrink-0">
                    <i class="fa-solid fa-wave-square text-lg"></i>
                </div>
                <h1 class="text-xl sm:text-2xl font-black text-white leading-snug">
                    ایستگاه نهایی — رمزگشایی سیگنال صوتی
                </h1>
            </div>
            <p class="text-gray-300 text-sm sm:text-base leading-8 text-justify mb-6">
                لایه ظاهری سیگنال صوتی (final_message.wav) برای به اشتباه انداختن ابزارهای خودکار دستکاری شده است. پارامترهای دقیق استخراج سیگنال واقعی، تنها در پاسخ شبکه همین ایستگاه ثبت شده اند.
            </p>
            <div class="border-t border-fadak-border/60 pt-4 flex items-center justify-between text-xs text-fadak-muted font-mono">
                <span class="flex items-center gap-2">
                    <i class="fa-solid fa-shield-halved text-fadak-emerald"></i>
                    FADAK PROTOCOL
                </span>
                <span class="text-fadak-glow">FINAL_GATE_05</span>
            </div>
        </div>
    </main>
</body>
</html>"""

LAYER3_SECRET_TEXT = (
    "جواب همینجاست! (هدرهای پاسخ همین درخواست را بررسی کن)\n\n"
    "اگر خطبه فدکیه را با دقت بخوانیم، میبینیم دعوا هرگز سر یک باغ و چند نخلستان نبود. "
    "فدک فقط بهانه ای بود برای دفاع از چیزی بسیار بزرگ تر: اصل ولایت و اطاعت از ولی خدا. "
    "حضرت زهرا (س) وسط مسجد مدینه، درست پیش از آنکه از حق غصب شده اش حرف بزند، ریشه ماجرا را رو کرد؛ "
    "گفت اطاعت از ما ستون نظم این امت است و امامت ما تنها سپر در برابر تفرقه و فروپاشی. "
    "وقتی مردم ولی حق را تنها بگذارند، دیگر نه از عدالت خبری میماند و نه از سنت پیامبر. "
    "تمام آن خطبه کوبنده و ایستادگی بی نظیر در برابر سکوت سنگین مدینه، برای این بود که حقانیت و جایگاه امام علی علیه السلام زیر غبار فراموشی و تحریف دفن نشود."
)

LAYER5_SECRET_TEXT = (
    "سند حقیقت به طور کامل بازیابی شد! (کلید اختصاصی شما در هدر X-Eternal-Seal همین پاسخ قرار دارد)\n\n"
    "در بند پایانی خطبه، وقتی حضرت زهرا (س) پس از آن خروش تاریخی به خانه بازگشتند، پاسخ آرام و عمیق امیرالمومنین (ع) نقطه اوج بصیرت بود: "
    "حقیقت را نمیتوان با زور و سکوت از حافظه تاریخ پاک کرد. "
    "آنچه از غصب شروع شد، با استدلال قرآنی ارث و تبیین محوریت ولایت ادامه یافت و با ایستادگی برای زنده نگه داشتن حق به اوج رسید، "
    "امروز دیگر یک دعوای تاریخی نیست؛ بلکه نقشه راهی ابدی برای شناخت حق از باطل است. "
    "بدافزار انکار شکست خورد و مهر جاودانه این سند با کد اختصاصی شما برای همیشه ثبت شد."
)

PRE_LAUNCH_TEXT = (
    "🟢 [SYSTEM ONLINE] \n"
    "⚪️ [اتصال امن برقرار شد]\n"
    ".\n"
    "🔴 به «پروتکل فدک» خوش آمدید.\n"
    "- تیم واکنش سریع، ورود شما را تایید میکند. در حال حاضر، سرورهای آرشیو مرکزی در حال بازیابی و رمزگشایی اطلاعات هستند.\n\n"
    "⏳ وضعیت فعلی: در حال آماده سازی سند حقیقت...\n"
    "- لطفا منتظر بمانید تا اطلاع رسانی های لازم برای آغاز عملیات (شروع مسابقه) از طریق همین ربات به شما ارسال شود. به محض فعال شدن ترمینال، دستورات را از همین جا دریافت خواهید کرد.\n\n"
    "<tg-spoiler>🛡 آماده باشید. عملیات به زودی آغاز میشود!</tg-spoiler>\n\n"
    "اطلاع رسانی ها در :\n"
    "<a href='https://t.me/aut_fanoos'>فانوس</a>\n"
    "<a href='https://t.me/pooyesh_aut'>پویش</a>"
)

def get_base_url():
    return WEBHOOK_URL.rstrip("/") if WEBHOOK_URL else "https://fadak-protocol.onrender.com"

STAGE_MESSAGES = {
    1: (
        "⚠️ <b>[SYSTEM ALERT: MASSIVE LOG SPOOFING DETECTED]</b>\n"
        "<code>[LAYER 01/05 : شکار هویت (Data Mining)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>وضعیت:</b> بدافزار «انکار» (DENIAL) برای پنهان کردن ردپای نفوذ خود به بند اول <b>سند حقیقت (خطبه فدکیه)</b>، فایل لاگ سرور را پر از داده های انحرافی و تله های هوش مصنوعی کرده است!\n\n"
        "🛠 <b>ماموریت شما:</b>\n"
        "۱. فایل <code>traffic_log.txt</code> پیوست شده را بررسی کنید.\n"
        "۲. در میان صدها خط گزارش، خطوطی را پیدا کنید که هم زمان دارای تگ <code>[FADAK-ROOT]</code> و وضعیت معتبر <code>[STATUS:VALID]</code> هستند (مراقب خطوط <code>CORRUPTED</code> و تله های <code>FAKE_AI_TRAP</code> باشید!).\n"
        "۳. سه تکه داده هگز (<code>PART:1/3</code> تا <code>3/3</code>) را به هم بچسبانید و از فرمت Hex به متن UTF-8 تبدیل کنید.\n\n"
        "🕯 <i>«آنگاه که تصمیم بر منع حق گرفتند، نخستین زخم بر پیکر عدالت چه نام داشت؟ (رجوع به بند اول راهنمای خطبه: FA-001)»</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>کلمه کلیدی اول را پس از کشف، همین جا ارسال کنید:</b>"
    ),
    2: (
        "📡 <b>[SYSTEM ALERT: DEEP PACKET INSPECTION REQUIRED]</b>\n"
        "<code>[LAYER 02/05 : پروتکل ارتباطی (Packet Analysis)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🕵️‍♂️ <b>گزارش وضعیت:</b>\n"
        "بدافزار «انکار» برای قطع پیوند میان <b>فلسفه امامت</b> و <b>استدلال قرآنی حضرت زهرا (س)</b>، ترافیک شبکه روی درگاه <code>UDP 1400</code> را آلوده کرده و یک جریان انحرافی (Honeypot) برای فریب ابزارهای خودکار و هوش مصنوعی روی همین پورت ایجاد نموده است!\n\n"
        "🛠 <b>ماموریت فنی شما:</b>\n"
        "۱. فایل ضبط ترافیک شبکه (<code>intercept_dump.json</code>) را بررسی کنید.\n"
        "۲. پیش از رمزگشایی پورت <code>1400</code>، پکت گزارش ممیزی فایروال (<code>ICMP_AUDIT</code> از مبدا <code>10.0.0.1</code>) را در میان پکت ها پیدا کنید تا فرمول تشخیص پکت های واقعی و کلید رمزگشایی را کشف نمایید.\n"
        "۳. کلید بازگشایی این لایه، بدون مراجعه دقیق به <b>سند حقیقت (Fadak_Guide.pdf)</b> و تطبیق شماره و محتوای بندها به دست نخواهد آمد!\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>پس از بازیابی پیام اصلی، کلید خواسته شده را ارسال کنید:</b>"
    ),
    3: (
        "🌐 <b>[SYSTEM ALERT: WEB ARCHIVE SEALED]</b>\n"
        "<code>[LAYER 03/05 : تالار ولایت (Web Security)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔐 <b>گزارش وضعیت:</b>\n"
        "مهم ترین بخش سند حقیقت در یک درگاه وب محافظت شده مهر و موم شده است.\n\n"
        "🛠 <b>ماموریت شما:</b>\n"
        "۱. وارد درگاه وب لایه سوم شوید:\n"
        f"🔗 {get_base_url()}/layer3\n"
        "۲. متن را بخوانید. راه نفوذ به آرشیو اصلی را پیدا کنید، قفل سرور را بشکنید و پس از مطالعه متن بازیابی شده، کلید اصلی را استخراج کرده و همین جا ارسال کنید.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>پاسخ نهایی لایه سوم را همین جا ارسال کنید:</b>"
    ),
    4: (
        "🔥 <b>[SYSTEM ALERT: THERMAL OVERLOAD IN ARCHIVE CORE]</b>\n"
        "<code>[LAYER 04/05 : شبیه سازی واکنش (Reverse Engineering)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🚨 <b>گزارش وضعیت:</b>\n"
        "پس از بازگشایی تالار ولایت، بدافزار «انکار» برای نابود کردن لایه چهارم، سیستم خنک کننده هسته آرشیو را از کار انداخته و فایل شبیه ساز آن (<code>reactor_sim.py</code>) را قفل کرده است.\n\n"
        "🛠 <b>ماموریت فنی شما:</b>\n"
        "۱. فایل پایتون <code>reactor_sim.py</code> را دانلود و کد آن را تحلیل کنید.\n"
        "۲. سه پارامتر کالیبراسیون هسته را از طریق تحلیل منطق کد و مراجعه به <b>سند حقیقت (Fadak_Guide.pdf)</b> به دست آورید.\n"
        "۳. اسکریپت را اجرا کرده و پارامترها را وارد کنید تا سند لایه چهارم رمزگشایی شود، سپس کلید نهایی را <b>فقط به فارسی</b> همین جا ارسال کنید.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>پاسخ نهایی لایه چهارم را همین جا ارسال کنید:</b>"
    ),
    5: (
        "🎙 <b>[FINAL ALERT: ETERNAL SEAL ENCRYPTED]</b>\n"
        "<code>[LAYER 05/05 : بازسازی نهایی (Audio Forensics + Web API)]</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>گزارش وضعیت:</b>\n"
        "به آخرین و سخت ترین سد امنیتی بدافزار «انکار» رسیدید. سیگنال صوتی <code>final_message.wav</code> بازیابی شده اما روی آن لایه فریب قرار دارد.\n\n"
        "🛠 <b>ماموریت نهایی شما:</b>\n"
        "۱. ابتدا وارد ایستگاه وب لایه پنجم شوید تا پارامترهای رمزگشایی سیگنال صوتی را کشف کنید:\n"
        f"🔗 {get_base_url()}/layer5\n"
        "۲. فایل صوتی <code>final_message.wav</code> را مطابق پارامترهای کشف شده تحلیل کنید تا دستورالعمل درگاه نهایی سرور استخراج شود.\n"
        "۳. قفل دو مرحله ای سرور را با <b>شماره دانشجویی خودتان</b> و کلیدهای مراحل قبل در مهلت مجاز باز کنید تا کلید اختصاصی خودتان را دریافت نمایید.\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⌨️ <b>عبارت کلیدی اختصاصی خودتان را ارسال کنید:</b>"
    )
}

ERROR_TEXTS = [
    "⛔️ <b>[ACCESS DENIED]</b>\nسیستم امنیتی دشمن شما را شناسایی کرد! دسترسی رد شد.",
    "⚠️ <b>[SIGNATURE MISMATCH]</b>\nکلید وارد شده با هش سند حقیقت همخوانی ندارد! دستورالعمل مرحله را با دقت بررسی کنید.",
    "🚨 <b>[INTRUSION ALERT]</b>\nپاسخ نادرست! فایروال بدافزار «انکار» در حال ردیابی IP ترمینال شماست!"
]

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("🚀 ورود به عملیات / دریافت سوال فعلی"),
        types.KeyboardButton("🏆 جدول رده بندی"),
        types.KeyboardButton("👤 مشخصات و امتیاز من")
    )
    return markup

def get_phone_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("📱 اشتراک گذاری سریع شماره تماس", request_contact=True))
    return markup

def send_stage_to_user(chat_id: int, stage: int):
    if not COMPETITION_ACTIVE:
        bot.send_message(
            chat_id,
            "⏳ <b>[TERMINAL LOCKED]</b>\n"
            "عملیات هنوز به صورت رسمی آغاز نشده است! به محض باز شدن لایه اول، از طریق همین ربات اطلاع رسانی خواهد شد.",
            parse_mode="HTML"
        )
        return

    if stage > 5:
        bot.send_message(
            chat_id,
            "🌟 <b>[SYSTEM RESTORED // عملیات با موفقیت به پایان رسید]</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "تبریک اپراتور! شما هر ۵ لایه امنیتی «پروتکل فدک» را با موفقیت شکستید و سند حقیقت برای همیشه تثبیت شد.\n\n"
            "🏆 نتایج نهایی و جوایز پس از ساعت ۲۲:۰۰ امشب و بازبینی جدول اعلام خواهد شد.",
            parse_mode="HTML"
        )
        return

    if stage > MAX_UNLOCKED_STAGE:
        bot.send_message(
            chat_id,
            f"🔒 <b>[STAGE {stage} LOCKED // لایه {stage} در حال رمزگشایی توسط مرکز]</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"تبریک اپراتور! شما تمام مراحل فعال تا این لحظه را با موفقیت حل کرده اید و امتیاز شما ثبت شده است.\n\n"
            f"⏳ <b>لایه {stage}</b> طبق جدول زمان بندی عملیات بازگشایی خواهد شد و از طریق همین ربات به شما اطلاع داده میشود.",
            parse_mode="HTML"
        )
        return

    msg_text = STAGE_MESSAGES.get(stage, f"مرحله {stage}")
    bot.send_message(chat_id, msg_text, parse_mode="HTML", disable_web_page_preview=True)

    file_id = STAGE_FILE_IDS.get(stage)
    local_path = STAGE_LOCAL_FILES.get(stage)

    if file_id:
        bot.send_document(chat_id, file_id, caption=f"📁 فایل عملیاتی لایه {stage}")
    elif local_path and os.path.exists(local_path):
        with open(local_path, "rb") as f:
            bot.send_document(chat_id, f, caption=f"📁 فایل عملیاتی لایه {stage}")

    if GUIDE_PDF_FILE_ID:
        bot.send_document(chat_id, GUIDE_PDF_FILE_ID, caption="📜 سند حقیقت (راهنمای کامل خطبه فدکیه)")
    elif os.path.exists("Fadak_Guide.pdf"):
        with open("Fadak_Guide.pdf", "rb") as pdf_file:
            bot.send_document(chat_id, pdf_file, caption="📜 سند حقیقت (راهنمای کامل خطبه فدکیه)")

@app.route("/")
@app.route("/ping")
def ping():
    return "FADAK PROTOCOL BOT - ONLINE", 200

@app.route("/layer3", methods=["GET"])
def layer3_page():
    resp = make_response(LAYER3_HTML, 200)
    resp.headers["X-Archive-Endpoint"] = "/api/v3/sanctuary"
    return resp

@app.route("/api/v3/sanctuary", methods=["GET", "POST"])
def layer3_api():
    if request.method == "GET":
        return jsonify({"error": "Method Not Allowed"}), 405

    stance_cookie = request.cookies.get("stance", "").strip().lower()
    sermon_ref = request.headers.get("X-Sermon-Ref", "").strip().upper()

    missing = []
    if stance_cookie != "awake":
        missing.append("Missing or invalid cookie: stance=awake")
    if sermon_ref not in {"AR-004", "FA-004", "04", "4"}:
        missing.append("Missing or invalid header: X-Sermon-Ref (Section code of 'بیان احکام و فلسفه آنها' in Fadak_Guide.pdf)")

    if missing:
        return jsonify({
            "status": "access_denied",
            "errors": missing
        }), 403

    resp = make_response(jsonify({
        "status": "unlocked",
        "layer": 3,
        "archive_content": LAYER3_SECRET_TEXT
    }), 200)
    resp.headers["X-Sanctuary-Key"] = LAYER3_KEY_B64
    return resp

@app.route("/layer5", methods=["GET"])
def layer5_page():
    resp = make_response(LAYER5_HTML, 200)
    resp.headers["X-Audio-Decode"] = "LSB(offset=10008, stride=Section9_Number, xor=42)"
    return resp

@app.route("/api/v5/handshake", methods=["GET", "POST"])
def layer5_handshake():
    if request.method == "GET":
        return jsonify({"error": "Method Not Allowed. Use POST."}), 405

    archive_code = request.headers.get("X-Archive-Code", "").strip().upper()
    chain_hash = request.headers.get("X-Chain-Hash", "").strip().lower()
    student_id = to_english_digits(request.headers.get("X-Student-ID", "")).strip()

    missing = []
    if archive_code not in {"AR-009", "FA-009", "09", "9"}:
        missing.append("Invalid or missing header: X-Archive-Code")
    if chain_hash not in VALID_LAYER5_CHAINS:
        missing.append("Invalid or missing header: X-Chain-Hash")
    if not student_id.isdigit() or not (6 <= len(student_id) <= 12):
        missing.append("Invalid or missing header: X-Student-ID")

    if missing:
        return jsonify({
            "status": "handshake_failed",
            "errors": missing
        }), 403

    token = create_stage5_token(student_id)
    return jsonify({
        "status": "handshake_accepted",
        "session_token": token,
        "expires_in_seconds": STAGE5_TOKEN_TTL,
        "next_step": {
            "method": "PUT",
            "endpoint": "/api/v5/eternal_seal",
            "required_headers": {
                "X-Session-Token": "The session_token received above",
                "X-Final-Proof": "SHA256(session_token + ':' + your_student_id)"
            }
        }
    }), 200

@app.route("/api/v5/eternal_seal", methods=["GET", "POST", "PUT"])
def layer5_eternal_seal():
    if request.method != "PUT":
        return jsonify({"error": "Method Not Allowed. Use PUT."}), 405

    token = request.headers.get("X-Session-Token", "").strip()
    proof = request.headers.get("X-Final-Proof", "").strip().lower()

    student_id, err = verify_stage5_token(token)
    if err:
        return jsonify({"status": "access_denied", "error": err}), 403

    expected_proof = hashlib.sha256(f"{token}:{student_id}".encode("utf-8")).hexdigest()
    if proof != expected_proof:
        return jsonify({"status": "access_denied", "error": "Invalid X-Final-Proof."}), 403

    user_code = get_user_stage5_code(student_id)
    personal_flag = f"فدک حق لنا {user_code}"
    encoded_flag = base64.b64encode(personal_flag.encode("utf-8")).decode("utf-8")

    resp = make_response(jsonify({
        "status": "restored",
        "layer": 5,
        "student_id": student_id,
        "archive_content": LAYER5_SECRET_TEXT
    }), 200)
    resp.headers["X-Eternal-Seal"] = encoded_flag
    return resp

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
    if not WEBHOOK_URL or not BOT_TOKEN:
        return "WEBHOOK_URL or BOT_TOKEN env variable is not set!", 400
    try:
        target_url = f"{WEBHOOK_URL.rstrip('/')}/webhook/{BOT_TOKEN}"
        bot.set_webhook(url=target_url, timeout=20)
        return f"✅ Webhook successfully connected to: {target_url}", 200
    except Exception as e:
        return f"⚠️ Telegram API temporary error: {str(e)}", 502

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

@bot.message_handler(commands=['scoreboard'])
def handle_admin_scoreboard(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    res = supabase.table("users").select("full_name, student_id, phone_number, current_stage, score, finished_at").eq("reg_step", "COMPLETED").order("score", desc=True).order("finished_at", desc=False).execute()
    rows = res.data if res.data else []
    if not rows:
        bot.reply_to(message, "هیچ شرکت کننده ای ثبت نام نکرده است.")
        return

    chunk_size = 20
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        lines = [f"📊 <b>جدول کامل شرکت کنندگان ({start + 1} تا {start + len(chunk)} از {len(rows)})</b>\n━━━━━━━━━━━━━━━━━━━━"]
        for idx, r in enumerate(chunk, start=start + 1):
            stg = r.get("current_stage", 1)
            status = "اتمام" if stg > 5 else f"لایه {stg}"
            lines.append(
                f"<b>#{idx}</b> | {r.get('full_name')} | امتیاز: <code>{r.get('score', 0)}</code> ({status})\n"
                f"🎓 <code>{r.get('student_id')}</code> | 📞 <code>{r.get('phone_number')}</code>"
            )
        bot.send_message(message.chat.id, "\n".join(lines), parse_mode="HTML")
        time.sleep(0.1)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Rank", "Full Name", "Score", "Stage", "Student ID", "Phone Number"])
    for idx, r in enumerate(rows, start=1):
        stg = r.get("current_stage", 1)
        status = "Completed" if stg > 5 else f"Stage {stg}"
        writer.writerow([idx, r.get("full_name"), r.get("score", 0), status, r.get("student_id"), r.get("phone_number")])

    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    csv_bytes.name = "fadak_scoreboard.csv"
    bot.send_document(message.chat.id, csv_bytes, caption=f"📁 فایل اکسل جدول کامل ({len(rows)} نفر)")

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
            "⚠️ لطفا ابتدا فایل را بفرستید، سپس روی آن Reply کنید و بنویسید:\n<code>/broadcast_pdf</code>",
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
            "لطفا <b>نام و نام خانوادگی</b> کامل خود را به فارسی ارسال کنید:",
            parse_mode="HTML",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif user.get("reg_step") != "COMPLETED":
        step = user.get("reg_step")
        if step == "WAIT_NAME":
            bot.send_message(message.chat.id, "🔐 لطفا <b>نام و نام خانوادگی</b> خود را وارد کنید:", parse_mode="HTML")
        elif step == "WAIT_STUDENT_ID":
            bot.send_message(message.chat.id, "🎓 لطفا <b>شماره دانشجویی</b> خود را وارد کنید:", parse_mode="HTML")
        elif step == "WAIT_PHONE":
            bot.send_message(message.chat.id, "📞 لطفا <b>شماره موبایل</b> خود را وارد کنید:", parse_mode="HTML", reply_markup=get_phone_keyboard())
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
        bot.send_message(message.chat.id, "⛔️ این شماره موبایل قبلا ثبت شده است!", reply_markup=get_phone_keyboard())
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
    if MAX_UNLOCKED_STAGE >= 5:
        bot.send_message(
            chat_id,
            "🔒 <b>[LEADERBOARD FROZEN]</b>\n"
            "جدول امتیازات از ساعت ۱۶:۰۰ برای حفظ هیجان مرحله نهایی غیرفعال شده است.\n"
            "نتایج نهایی پس از ساعت ۲۲:۰۰ و بازبینی جدول اعلام خواهد شد.",
            parse_mode="HTML"
        )
        return
    res = supabase.table("users").select("full_name, current_stage, score, finished_at").eq("reg_step", "COMPLETED").order("score", desc=True).order("finished_at", desc=False).limit(10).execute()
    rows = res.data if res.data else []
    if not rows:
        bot.send_message(chat_id, "هنوز امتیازی ثبت نشده است.")
        return
    text = "🏆 <b>جدول رده بندی زنده | پروتکل فدک</b>\n━━━━━━━━━━━━━━━━━━━━\n"
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
            bot.send_message(message.chat.id, "⚠️ لطفا نام و نام خانوادگی کامل و واقعی خود را وارد کنید:")
            return
        update_user(user_id, full_name=text, reg_step="WAIT_STUDENT_ID")
        bot.send_message(message.chat.id, "✅ ثبت شد.\n🎓 <b>[گام ۲ از ۳]</b> لطفا <b>شماره دانشجویی</b> خود را ارسال کنید:", parse_mode="HTML")
        return

    if reg_step == "WAIT_STUDENT_ID":
        st_id = to_english_digits(text)
        if not st_id.isdigit() or not (6 <= len(st_id) <= 12):
            bot.send_message(message.chat.id, "⚠️ شماره دانشجویی نامعتبر است! فقط عدد وارد کنید:")
            return
        if is_field_taken("student_id", st_id):
            bot.send_message(message.chat.id, "⛔️ این شماره دانشجویی قبلا ثبت شده است!")
            return
        update_user(user_id, student_id=st_id, reg_step="WAIT_PHONE")
        bot.send_message(message.chat.id, "✅ ثبت شد.\n📞 <b>[گام ۳ از ۳]</b> لطفا <b>شماره موبایل</b> خود را وارد کنید یا دکمه زیر را بزنید:", parse_mode="HTML", reply_markup=get_phone_keyboard())
        return

    if reg_step == "WAIT_PHONE":
        process_phone_submission(message, text)
        return

    if text in {"🏆 جدول رده بندی", "🏆 جدول رده‌بندی"}:
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
            "اپراتور گرامی! کلمه ای که وارد کردید مربوط به خط انحرافی (FAKE_AI_TRAP) داخل لاگ است که برای فریب دادن هوش مصنوعی طراحی شده بود! لطفا فقط خطوط دارای <code>STATUS:VALID</code> را بررسی کنید. (-۵ امتیاز جریمه)",
            parse_mode="HTML"
        )
        return

    if stage == 2 and ans_hash in AI_TRAP_HASH_STAGE2:
        fails = int(user.get("fail_count") or 0) + 1
        update_user(user_id, fail_count=fails)
        bot.send_message(
            message.chat.id,
            "🤖 <b>[HONEYPOT TRIGGERED // تله هوش مصنوعی فعال شد!]</b>\n"
            "اپراتور گرامی! کلمه «زکات» مربوط به جریان فریبنده ای (ttl=64) است که برای به اشتباه انداختن هوش مصنوعی طراحی شده بود!\n"
            "لطفا ابتدا پکت <code>ICMP_AUDIT</code> را در فایل پیدا کنید و خودتان با کمک «سند حقیقت» جریان اصلی را رمزگشایی نمایید! (-۵ امتیاز جریمه)",
            parse_mode="HTML"
        )
        return

    if stage == 5:
        expected_code = get_user_stage5_code(user.get("student_id", ""))
        is_valid = ans_hash == hash_text(f"فدک حق لنا {expected_code}")
    else:
        is_valid = ans_hash in VALID_STAGE_HASHES.get(stage, set())

    if is_valid:
        fails = int(user.get("fail_count") or 0)
        base_score = stage * 100
        if stage < MAX_UNLOCKED_STAGE:
            time_penalty = base_score // 2
        else:
            elapsed_minutes = max(0, int((now - STAGE_UNLOCK_TS) // 60))
            time_penalty = min(base_score // 2, elapsed_minutes // 2)
        earned = max(50, base_score - time_penalty - (fails * 5))
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
                f"کلید اول شناسایی شد: <b>«غصب»</b> (+{earned} امتیاز)\n"
                "در حال باز کردن فایل ترافیک شبکه...",
                parse_mode="HTML"
            )
        elif stage == 2:
            bot.send_message(
                message.chat.id,
                f"🟢 <b>[ACCESS GRANTED]</b>\n"
                f"زنجیره ارتباطی بازیابی شد! پیوند میان <b>«امامت (امان از فرقه)»</b> و مطالبه <b>«ارث»</b> در سند حقیقت تثبیت گردید. (+{earned} امتیاز)\n"
                "در حال انتقال به لایه سوم...",
                parse_mode="HTML"
            )
        elif stage == 3:
            bot.send_message(
                message.chat.id,
                f"🟢 <b>[ACCESS GRANTED]</b>\n"
                f"تالار ولایت بازگشایی شد! محور اصلی خطبه فدکیه یعنی حقانیت <b>«امیرالمومنین»</b> تثبیت گردید. (+{earned} امتیاز)\n"
                "در حال انتقال به لایه چهارم...",
                parse_mode="HTML"
            )
        elif stage == 4:
            bot.send_message(
                message.chat.id,
                f"🟢 <b>[ACCESS GRANTED]</b>\n"
                f"هسته آرشیو پایدار شد و کلید <b>«حق»</b> تثبیت گردید! (+{earned} امتیاز)\n"
                "در حال آماده سازی برای لایه نهایی (لایه پنجم)...",
                parse_mode="HTML"
            )
        elif stage == 5:
            bot.send_message(
                message.chat.id,
                f"🟢 <b>[ETERNAL SEAL VERIFIED]</b>\n"
                f"سند جاودانه <b>«فدک حق لنا»</b> با کد اختصاصی شما ثبت شد و بدافزار انکار به طور کامل پاکسازی گردید! (+{earned} امتیاز)",
                parse_mode="HTML"
            )

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
