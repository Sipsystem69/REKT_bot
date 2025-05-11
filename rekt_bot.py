# rekt_bot.py

import os
import asyncio
import json
import requests
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from dotenv import load_dotenv
import websockets

# ---- Load environment ----
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHAT_ID      = int(os.getenv("CHAT_ID"))
WEBHOOK_HOST = os.getenv("WEBHOOK_URL")      # e.g. "https://your-app.onrender.com"
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL  = WEBHOOK_HOST + WEBHOOK_PATH

# Render provides port via $PORT
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 5000))

# V5 endpoint for USDT futures
EXCHANGE_WS = "wss://stream.bybit.com/v5/public/linear"

# ---- FSM States ----
class Settings(StatesGroup):
    waiting_for_limit = State()

class ListSettings(StatesGroup):
    choosing_mode = State()

# ---- In-memory storage ----
limits     = {}  # chat_id -> float threshold in USD
list_modes = {}  # chat_id -> str mode

# ---- Bot & Dispatcher ----
bot     = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp      = Dispatcher(bot, storage=storage)

# ---- Keyboards ----
def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💲 Лимит ByBit", callback_data="set_limit"),
        InlineKeyboardButton("⚫️ Список ByBit", callback_data="set_list"),
    )
    return kb


def list_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🟡 Все токены",    callback_data="list_all"),
        InlineKeyboardButton("🟡 Без топ 20",   callback_data="list_no_top20"),
        InlineKeyboardButton("🟡 Без топ 50",   callback_data="list_no_top50"),
        InlineKeyboardButton("❌ Отмена",       callback_data="list_cancel"),
    )
    return kb

# ---- Handlers ----
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    limits[msg.chat.id]     = limits.get(msg.chat.id, 100_000.0)
    list_modes[msg.chat.id] = list_modes.get(msg.chat.id, "list_all")
    await msg.answer(
        "Привет! Я сканирую ByBit на предмет ликвидаций.\n\n"
        "Выберите действие:",
        reply_markup=main_menu()
    )

@dp.callback_query_handler(lambda c: c.data == "set_limit")
async def callback_set_limit(cq: types.CallbackQuery):
    await cq.answer()
    await bot.send_message(cq.from_user.id, "Введите минимальный объём ликвидаций (в тысячах USD). Например, 15 = $15 000:")
    await Settings.waiting_for_limit.set()

@dp.message_handler(state=Settings.waiting_for_limit, content_types=types.ContentTypes.TEXT)
async def process_limit(msg: types.Message, state: FSMContext):
    text = msg.text.strip().lower().replace(",", "").replace("$", "")
    try:
        # if user ends with 'k', treat as thousands
        if text.endswith('k'):
            base = float(text[:-1])
            value = base * 1_000
        else:
            value = float(text)
            # if less than 1000, assume shorthand thousands
            if value < 1000:
                value = value * 1_000
        limits[msg.chat.id] = value
        await msg.answer(
            f"✅ Порог установлен: от ${value:,.2f}",
            reply_markup=main_menu()
        )
        await state.finish()
    except ValueError:
        await msg.answer("❌ Не похоже на число. Введите, например, 15 или 15k:")

@dp.callback_query_handler(lambda c: c.data == "set_list")
async def callback_set_list(cq: types.CallbackQuery):
    await cq.answer()
    await bot.send_message(cq.from_user.id, "Выберите режим списка ликвидаций:", reply_markup=list_menu())
    await ListSettings.choosing_mode.set()

@dp.callback_query_handler(lambda c: c.data.startswith("list_"), state=ListSettings.choosing_mode)
async def process_list_choice(cq: types.CallbackQuery, state: FSMContext):
    mode = cq.data
    await cq.answer()
    if mode == "list_cancel":
        await bot.send_message(cq.from_user.id, "❌ Отмена.", reply_markup=main_menu())
    else:
        desc = {
            "list_all":      "🟡 Режим: все токены",
            "list_no_top20": "🟡 Режим: без топ 20",
            "list_no_top50": "🟡 Режим: без топ 50",
        }[mode]
        list_modes[cq.from_user.id] = mode
        await bot.send_message(cq.from_user.id, f"✅ {desc}", reply_markup=main_menu())
    await state.finish()

# ---- Liquidation listener with retry & V5 subscription ----
async def liquidation_listener():
    # get USDT futures symbols
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/instruments-info?category=linear"
        )
        resp.raise_for_status()
        symbols = [item["symbol"] for item in resp.json()["result"]["list"]]
    except Exception as err:
        print(f"❌ Ошибка получения символов: {err}")
        symbols = []

    topics = [f"allLiquidation.{s}" for s in symbols]
    while True:
        try:
            async with websockets.connect(EXCHANGE_WS) as ws:
                await ws.send(json.dumps({"op":"subscribe","args":topics}))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    topic = data.get("topic", "")
                    if topic.startswith("allLiquidation") and data.get("data"):
                        for itm in data["data"]:
                            vol = float(itm["v"])
                            if vol < limits.get(CHAT_ID, 100_000.0):
                                continue
                            text = (
                                f"💥 Ликвидация {itm['s']}\n"
                                f"• Сторона: {itm['S']}\n"
                                f"• Объём: ${vol:,.2f}\n"
                                f"• Цена: {float(itm['p'])}\n"
                                f"• Время: {datetime.fromtimestamp(itm['T']/1000)}"
                            )
                            await bot.send_message(CHAT_ID, text)
        except Exception as e:
            print(f"WebSocket error: {e}. Reconnecting in 5s…")
            await asyncio.sleep(5)

# ---- Startup & shutdown ----
async def on_startup(dp_):
    await bot.set_webhook(WEBHOOK_URL)
    asyncio.create_task(liquidation_listener())

async def on_shutdown(dp_):
    await bot.delete_webhook()

# ---- Entry point ----
if __name__ == "__main__":
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        skip_updates=True,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
