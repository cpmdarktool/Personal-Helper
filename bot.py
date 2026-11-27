import asyncio
import os
import aiosqlite
from datetime import datetime, timedelta
from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Импортируем клиент для дневника
from schoolmospy import StudentClient

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден в Environment!")

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 10000))
DB_PATH = "/tmp/mesh_bot.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AuthStates(StatesGroup):
    waiting_for_token = State()

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

async def delete_token_from_db(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()

# ================= КЛАВИАТУРЫ =================
def start_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Войти в аккаунт", callback_data="auth_start")],
        [InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")]
    ])

def auth_instruction_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Войти на сайт МЭШ", url="https://authedu.mosreg.ru/")],
        [InlineKeyboardButton(text="2️⃣ Получить токен", url="https://authedu.mosreg.ru/v2/token/refresh?roleId=1&subsystem=2")],
        [InlineKeyboardButton(text="✅ Я получил токен", callback_data="auth_wait_token")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")]
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_start")]
    ])

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Домашнее задание", callback_data="menu_homework")],
        [InlineKeyboardButton(text="📊 Оценки", callback_data="menu_marks")],
        [InlineKeyboardButton(text="📅 Расписание", callback_data="menu_schedule")],
        [InlineKeyboardButton(text="🔔 Изменения / Замены", callback_data="menu_changes")],
        [InlineKeyboardButton(text="🚪 Выйти из аккаунта", callback_data="logout")]
    ])

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])

def schedule_kb():
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=day, callback_data=f"day_{day}")] for day in days
    ] + [[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]])

# ================= АВТОМАТИЗАЦИЯ (РЕАЛЬНЫЕ ЗАПРОСЫ) =================
async def parse_mesh_data(token: str, action: str, extra_data: dict = None):
    try:
        # Создаем клиент с токеном пользователя
        client = StudentClient(token=token)
        
        # Получаем текущую дату и неделю назад (для запроса оценок и домашки)
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        if action == "marks":
            marks = await client.marks.get(from_date=week_ago, to_date=today)
            await client.close()
            
            if not marks:
                return "📊 **Оценок за последнюю неделю нет.**"
            
            # Формируем красивый текст из полученного списка
            text = "📊 **Твои оценки:**\n\n"
            for mark in marks:
                # В разных версиях API поля могут называться по-разному
                # Поэтому используем безопасный вывод (если ключа нет - будет пусто)
                date = mark.get('date', 'Дата неизвестна')
                subject = mark.get('subject_name', 'Предмет')
                value = mark.get('mark', '?')
                text += f"📅 `{date}` | {subject}: **{value}**\n"
            return text

        elif action == "homework":
            hw = await client.homeworks.get(from_date=week_ago, to_date=today)
            await client.close()
            
            if not hw:
                return "📚 **Домашнего задания нет.**"
            
            text = "📚 **Домашнее задание:**\n\n"
            for task in hw:
                subject = task.get('subject_name', 'Предмет')
                desc = task.get('description', 'Без описания')
                text += f"📖 **{subject}**:\n{desc}\n\n"
            return text

        elif action == "schedule":
            day = extra_data.get("day")
            # Ищем ближайшую дату для этого дня недели
            target_date = datetime.now().strftime("%Y-%m-%d") # Упрощенно - на сегодня
            lessons = await client.events.get(from_date=target_date, to_date=target_date)
            await client.close()
            
            if not lessons:
                return f"📅 **На {day} уроков нет.**"
            
            text = f"📅 **Расписание на {day}:**\n\n"
            for i, lesson in enumerate(lessons, 1):
                subject = lesson.get('subject_name', 'Урок')
                room = lesson.get('room', '')
                text += f"{i}. {subject} ({room})\n"
            return text

        elif action == "changes":
            return "🔔 **Пока функция в разработке.**"

    except Exception as e:
        # Если библиотека не поддерживает mosreg или токен не подходит, мы увидим ошибку
        return f"❌ **Ошибка при запросе к дневнику:**\n\n`{str(e)}`"

    return "❓ Неизвестное действие"

# ================= ХЕНДЛЕРЫ =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    token = await get_token_from_db(message.from_user.id)
    
    text = "👋 Привет! Я твой личный помощник для школьного дневника.\n\nВыбери действие:"
    
    if token:
        await message.answer(text, reply_markup=main_menu_kb())
    else:
        await message.answer(text, reply_markup=start_kb())

@dp.callback_query(F.data == "about")
async def about_bot(callback: CallbackQuery):
    text = "🤖 **О боте:**\nЭтот бот создан для удобного просмотра школьного дневника. Он не передает твои данные третьим лицам."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    token = await get_token_from_db(callback.from_user.id)
    if token:
        await callback.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    else:
        await callback.message.edit_text("👋 Главное меню:", reply_markup=start_kb())
    await callback.answer()

@dp.callback_query(F.data == "auth_start")
async def auth_start(callback: CallbackQuery):
    text = (
        "🔐 **Инструкция по входу:**\n\n"
        "1. Нажми кнопку **«1️⃣ Войти на сайт МЭШ»** и авторизуйся через Госуслуги.\n"
        "2. Вернись в этот чат и нажми **«2️⃣ Получить токен»**.\n"
        "3. На открывшейся странице найди длинный текст, который начинается с `eyJ...`.\n"
        "4. Скопируй **только этот текст** (без кавычек) и отправь его сюда.\n\n"
        "⚠️ _Если страницы не открываются, выключи VPN._"
    )
    await callback.message.edit_text(text, reply_markup=auth_instruction_kb(), parse_mode="Markdown", disable_web_page_preview=True)
    await callback.answer()

@dp.callback_query(F.data == "auth_wait_token")
async def auth_wait_token(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AuthStates.waiting_for_token)
    await callback.message.edit_text(
        "📝 Отправь мне свой токен сообщением.\n\n"
        "Он выглядит как длинная строка, начинающаяся на `eyJ...`.",
        reply_markup=cancel_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(AuthStates.waiting_for_token)
async def process_token(message: types.Message, state: FSMContext):
    token = message.text.strip()
    
    if not token.startswith("eyJ") or len(token) < 100:
        await message.answer(
            "❌ **Ошибка!** Это не похоже на токен.\n"
            "Токен должен начинаться с `eyJ...` и быть длинным. Попробуй еще раз.",
            reply_markup=cancel_kb(),
            parse_mode="Markdown"
        )
        return

    try:
        await save_token_to_db(message.from_user.id, token)
        await state.clear()
        await message.answer(
            "✅ **Успешно!** Токен сохранен. Теперь ты можешь пользоваться ботом.",
            reply_markup=main_menu_kb(),
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка базы данных: {e}", reply_markup=cancel_kb())

@dp.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()

@dp.callback_query(F.data == "logout")
async def logout(callback: CallbackQuery):
    await delete_token_from_db(callback.from_user.id)
    await callback.message.edit_text(
        "🚪 Ты вышел из аккаунта. Токен удален.\nНапиши /start, чтобы начать заново.",
        reply_markup=start_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_homework")
async def show_homework(callback: CallbackQuery):
    token = await get_token_from_db(callback.from_user.id)
    if not token:
        return await callback.answer("Сначала войди в аккаунт!", show_alert=True)
    await callback.message.edit_text("⏳ Запрашиваю домашку...")
    result = await parse_mesh_data(token, "homework")
    await callback.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "menu_marks")
async def show_marks(callback: CallbackQuery):
    token = await get_token_from_db(callback.from_user.id)
    if not token:
        return await callback.answer("Сначала войди в аккаунт!", show_alert=True)
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
    if not token:
        return await callback.answer("Сначала войди в аккаунт!", show_alert=True)
    await callback.message.edit_text(f"⏳ Запрашиваю расписание на {day}...")
    result = await parse_mesh_data(token, "schedule", extra_data={"day": day})
    await callback.message.edit_text(result, reply_markup=back_kb(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "menu_changes")
async def show_changes(callback: CallbackQuery):
    token = await get_token_from_db(callback.from_user.id)
    if not token:
        return await callback.answer("Сначала войди в аккаунт!", show_alert=True)
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
