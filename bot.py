import os
import sys
import logging
import asyncio
from typing import Dict, List, Set
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatAction, ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("StarsBot")

# Google SDK zaxirasi
gemini_sdk_model = None
try:
    import google.generativeai as genai
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_sdk_model = genai.GenerativeModel("gemini-3.8-flash")
except Exception as e:
    logger.warning(f"SDK ogohlantirish: {e}")

user_histories: Dict[int, List[dict]] = {}

def is_owner(user_id: int) -> bool:
    global admin_ids
    if not admin_ids:
        return True
    return user_id in admin_ids

http_session: aiohttp.ClientSession = None

async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session

async def ask_gemini_direct(user_id: int, user_message: str) -> str:
    session = await get_http_session()
    
    if user_id not in user_histories:
        user_histories[user_id] = []
    history = user_histories[user_id]

    contents = []
    for item in history[-6:]:
        contents.append({
            "role": item["role"],
            "parts": [{"text": item["text"]}]
        })
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{
                "text": (
                    "Siz o'ta tezkor, lo'nda va aniq AI yordamchisiz. "
                    "Savolga darhol to'g'ridan-to'g'ri, lo'nda va aniq javob bering. "
                    "Ortiqcha kirish va salom-aliksiz javob yozing. "
                    "Oldingi savol-javoblar kontekstini doimo inobatga oling."
                )
            }]
        },
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 800
        }
    }

    # x-goog-api-key sarlavhasi orqali ultra-tezkor REST so'rovi
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"

    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                ai_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                history.append({"role": "user", "text": user_message})
                history.append({"role": "model", "text": ai_text})
                user_histories[user_id] = history[-6:]
                return ai_text
            else:
                logger.warning(f"REST API: {resp.status}, zaxira SDK ga o'tilmoqda")
    except Exception as e:
        logger.warning(f"REST xatosi: {e}, zaxira SDK ga o'tilmoqda")

    # ZAXIRA: Rasmiy Google SDK
    if gemini_sdk_model:
        try:
            chat_context = "\n".join([f"{h['role']}: {h['text']}" for h in history[-4:]])
            prompt = f"{chat_context}\nuser: {user_message}\nJavobni lo'nda va aniq bering:" if chat_context else user_message
            resp = await asyncio.to_thread(gemini_sdk_model.generate_content, prompt)
            ai_text = resp.text.strip()
            history.append({"role": "user", "text": user_message})
            history.append({"role": "model", "text": ai_text})
            user_histories[user_id] = history[-6:]
            return ai_text
        except Exception as sdk_err:
            logger.error(f"SDK xatosi: {sdk_err}")

    return "⚠️ Server band, 2 soniyadan so'ng qayta yozing."

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

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    await message.answer("⚡ Bot tezkor 24/7 rejimda faol! Savolingizni yozing:")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    user_histories[message.from_user.id] = []
    await message.answer("🧹 Xotira tozalandi.")

@dp.message(F.text)
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    if not is_owner(user_id):
        return

    asyncio.create_task(bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING))
    reply_text = await ask_gemini_direct(user_id, message.text.strip())
    await send_split_message(message, reply_text)

async def handle_health_check(request):
    return web.Response(text="Bot is running ultra-fast 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health-check server {port}-portda ishlamoqda.")

async def keep_alive_loop():
    external_url = os.getenv("RENDER_EXTERNAL_URL") or "https://my-stars-bot-v9kl.onrender.com"
    health_url = f"{external_url}/health"
    await asyncio.sleep(15)
    
    while True:
        try:
            session = await get_http_session()
            async with session.get(health_url, timeout=5) as resp:
                logger.info(f"Keep-alive ping: {resp.status}")
        except Exception:
            pass
        await asyncio.sleep(420)

async def main():
    logger.info(f"Ultra-tezkor bot ishga tushmoqda. Adminlar: {admin_ids}")
    await start_web_server()
    asyncio.create_task(keep_alive_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
