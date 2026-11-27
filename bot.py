import asyncio
import os
import aiosqlite
from datetime import datetime, timedelta
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Загружаем переменные из .env
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в Environment!")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 10000))

# ВАЖНО: На Render пишем в /tmp, иначе будет ошибка прав доступа
DB_PATH = "/tmp/mesh_bot.db"
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ =================
async def init_db():
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    mesh_token TEXT
                )
            """)
            await db.commit()
            print("✅ База данных успешно инициализирована.")
    except Exception as e:
        print(f"❌ ОШИБКА БАЗЫ ДАННЫХ: {e}")

async def save_token_to_db(user_id: int, token: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO users (user_id, mesh_token) VALUES (?, ?)", (user_id, token))
        await db.commit()

async def get_token_from_db(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT mesh_token FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

# ================= АВТОМАТИЗАЦИЯ (ЗАГЛУШКИ) =================
async def parse_mesh_data(token: str, action: str, extra_data: dict = None):
    await asyncio.sleep(1) # Имитация задержки сети
    
    if action == "homework":
        return "📚 **Домашнее задание:**\n\n📖 Математика: № 5.12\n📖 Русский: Упр. 234"
    elif action == "marks":
        return "📊 **Оценки:**\n\n25.11 | Математика: **5**\n24.11 | Русский: **4**"
    elif action == "schedule":
        day = extra_data.get("day")
        return f"📅 **Расписание на {day}:**\n\n1. Математика\n2. Русский\n3. Английский"
    elif action == "changes":
        return "🔔 **Замен нет.** Все по расписанию."
    return "❓ Неизвестное действие"

# ================= КЛАВИАТУРЫ =================
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Домашнее задание", callback_data="menu_homework")],
        [InlineKeyboardButton(text="📊 Оценки", callback_data="menu_marks")],
        [InlineKeyboardButton(text="📅 Расписание", callback_data="menu_schedule")],
        [InlineKeyboardButton(text="🔔 Изменения / Замены", callback_data="menu_changes")],
        [InlineKeyboardButton(text="🚪 Выйти (удалить токен)", callback_data="logout")]
    ])

def schedule_kb():
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=day, callback_data=f"day_{day}")] for day in days
    ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]])

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])

# ================= ХЕНДЛЕРЫ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    try:
        token = await get_token_from_db(message.from_user.id)
        if token:
            await message.answer("С возвращением! Выбери действие:", reply_markup=main_menu_kb())
        else:
            await message.answer("👋 Привет! Скинь свой токен МЭШ (начинается на `eyJ...`):")
    except Exception as e:
        print(f"Ошибка в cmd_start: {e}")

@dp.message(F.text.startswith("eyJ"))
async def save_token(message: types.Message):
    try:
        await save_token_to_db(message.from_user.id, message.text.strip())
        await message.answer("✅ Токен сохранён! Выбери действие:", reply_markup=main_menu_kb())
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении: {e}")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()

@dp.callback_query(F.data == "logout")
async def logout(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.message.edit_text("🚪 Токен удалён. Напиши /start.")
    await callback.answer()

@dp.callback_query(F.data == "menu_homework")
async def show_homework(callback: CallbackQuery):
    token = await get_token_from_db(callback.from_user.id)
    await callback.message.edit_text("⏳ Запрашиваю домашку...")
    result = await parse_mesh_data(token, "homework")
    await callback.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "menu_marks")
async def show_marks(callback: CallbackQuery):
    token = await get_token_from_db(callback.from_user.id)
    await callback.message.edit_text("⏳ Запрашиваю оценки...")
    result = await parse_mesh_data(token, "marks")
    await callback.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "menu_schedule")
async def ask_day(callback: CallbackQuery):
    await callback.message.edit_text("Выбери день недели:", reply_markup=schedule_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith("day_"))
async def show_schedule(callback: CallbackQuery):
    day = callback.data.split("_")[1]
    token = await get_token_from_db(callback.from_user.id)
    await callback.message.edit_text(f"⏳ Запрашиваю расписание на {day}...")
    result = await parse_mesh_data(token, "schedule", extra_data={"day": day})
    await callback.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "menu_changes")
async def show_changes(callback: CallbackQuery):
    token = await get_token_from_db(callback.from_user.id)
    await callback.message.edit_text("⏳ Проверяю замены...")
    result = await parse_mesh_data(token, "changes")
    await callback.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()

# ================= ЗАПУСК =================
async def on_startup(app: web.Application):
    print("⏳ Запуск бота...")
    await init_db()
    if WEBHOOK_HOST:
        await bot.set_webhook(url=WEBHOOK_URL)
        print(f"✅ Webhook установлен: {WEBHOOK_URL}")
    else:
        print("❌ WEBHOOK_HOST не задан!")

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    
    async def health(request):
        return web.Response(text="OK")
    app.router.add_get("/", health)
    
    setup_application(app, dp, bot=bot)
    print(f"🚀 Сервер запускается на порту {PORT}...")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
