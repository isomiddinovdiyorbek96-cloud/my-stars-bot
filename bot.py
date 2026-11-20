import os
import sys
import logging
import asyncio
from typing import Dict, List, Set
from dotenv import load_dotenv, set_key

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction, ParseMode
from aiogram.utils.chat_action import ChatActionSender

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Adminlar ro'yxatini yuklash
def load_admin_ids() -> Set[int]:
    raw = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")).strip()
    clean_raw = raw.replace("'", "").replace('"', "")
    ids = set()
    for item in clean_raw.split(","):
        item = item.strip()
        if item.isdigit():
            ids.add(int(item))
    return ids

admin_ids = load_admin_ids()

def save_admin_ids():
    global admin_ids
    raw_str = ",".join(str(i) for i in sorted(admin_ids))
    try:
        set_key(ENV_PATH, "ADMIN_IDS", raw_str)
        set_key(ENV_PATH, "ADMIN_ID", raw_str)
        logger.info(f"Adminlar saqlandi: {raw_str}")
    except Exception as e:
        logger.error(f"Adminlarni saqlashda xato: {e}")

ADMIN_SECRET = "777"

# Loglar
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
handlers = [logging.FileHandler(log_file, encoding="utf-8")]
if sys.stdout is not None:
    handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=handlers
)
logger = logging.getLogger("StarsBot")

# OpenAI
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import AsyncOpenAI
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logger.error(f"OpenAI yuklashda xato: {e}")

# Gemini (Tezkor Gemini 3.8 Flash)
gemini_model = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        # Tezkor javob berish uchun parametrlar
        generation_config = {
            "temperature": 0.4,
            "top_p": 0.9,
            "max_output_tokens": 1500,
        }
        gemini_model = genai.GenerativeModel("gemini-3.8-flash", generation_config=generation_config)
    except Exception as e:
        logger.error(f"Gemini yuklashda xato: {e}")

user_histories: Dict[int, List[dict]] = {}

def is_owner(user_id: int) -> bool:
    global admin_ids
    if len(admin_ids) < 3 and user_id not in admin_ids:
        admin_ids.add(user_id)
        save_admin_ids()
        return True
    return user_id in admin_ids

async def ask_ai(user_id: int, user_message: str) -> str:
    """Maksimal tezlikda aniq javob olish"""
    global openai_client, gemini_model
    
    # 1. Google Gemini (Serverda 1 soniyada javob beradi)
    if (AI_PROVIDER == "gemini" or not openai_client) and gemini_model:
        try:
            # Tez va lo'nda javob talabi
            prompt = (
                f"Savol: {user_message}\n\n"
                f"Talab: Javobni iloji boricha tez, aniq, lo'nda va to'liq qilib bering. "
                f"Ortiqcha salom-alik yoki cho'zilgan gaplarsiz, to'g'ridan-to'g'ri masalaning aniq javobini yozing."
            )
            response = await asyncio.to_thread(gemini_model.generate_content, prompt)
            ai_reply = response.text or "Javob topilmadi."
            return ai_reply.strip()
        except Exception as e:
            logger.error(f"Gemini xatosi: {e}")
            return f"⚠️ Xatolik: {str(e)[:120]}"

    # 2. OpenAI
    if openai_client:
        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Aniq, lo'nda va tezkor javob bering."},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=1500,
            )
            return (response.choices[0].message.content or "Javob yo'q.").strip()
        except Exception as e:
            return f"⚠️ OpenAI xatosi: {str(e)[:120]}"

    return "⚠️ AI kaliti kiritilmagan."

async def send_split_message(message: types.Message, text: str):
    max_len = 4000
    if len(text) <= max_len:
        try:
            await message.reply(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.reply(text)
        return

    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
    for chunk in chunks:
        try:
            await message.answer(chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await message.answer(chunk)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("admin"))
@dp.message(Command("login"))
async def cmd_login(message: types.Message):
    global admin_ids
    user_id = message.from_user.id
    parts = message.text.split()
    if len(parts) > 1 and parts[1] == ADMIN_SECRET:
        admin_ids.add(user_id)
        save_admin_ids()
        await message.answer(f"✅ Muvaffaqiyatli admin bo'ldingiz! (ID: {user_id})")
        return
    if not is_owner(user_id):
        return
    await message.answer(f"Siz adminsiz! Adminlar: {len(admin_ids)}")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return  # Begonalarga jim o'tiradi
    
    await message.answer(
        "👋 Salom! Bot 24/7 tezkor rejimda faol.\n"
        "🔒 Begonalarga mutlaqo jim turadi.\n"
        "💬 Istalgan savolingizni yozing, tezkor javob beraman!"
    )

@dp.message(F.text)
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return  # Begonalarga hech narsa demaydi!

    async with ChatActionSender(bot=bot, chat_id=message.chat.id, action=ChatAction.TYPING):
        response = await ask_ai(user_id, message.text.strip())
    
    await send_split_message(message, response)

# Render / Bulutli serverlar uchun Ping Health Check veb-serveri
async def handle_health_check(request):
    return web.Response(text="Stars Bot 24/7 ishlamoqda!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check server {port}-portda ishga tushdi.")

async def main():
    logger.info(f"Bot 24/7 ishga tushmoqda. Adminlar: {admin_ids}")
    # Render / Cloud serverlar uchun veb-serverni parallel yoqish
    await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
