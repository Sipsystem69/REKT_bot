# rekt_bot.py

import os
import asyncio
import json
import requests
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.utils.executor import start_webhook
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

# V5 endpoint для USDT-фьючерсов
EXCHANGE_WS = "wss://stream.bybit.com/v5/public/linear"

# ---- FSM States ----
class Settings(StatesGroup):
    waiting_for_limit = State()

class ListSettings(StatesGroup):
    choosing_mode = State()

# ---- In-memory storage ----
limits     = {}  # chat_id -> float threshold
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
        InlineKeyboardButton("🟡 Все токены",  callback_data="list_all"),
        InlineKeyboardButton("🟡 Без топ 20", callback_data="list_no_top20"),
        InlineKeyboardButton("🟡 Без топ 50", callback_data="list_no_top50"),
        InlineKeyboardButton("❌ Отмена",     callback_data="list_cancel"),
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
    await bot.send_message(cq.from_user.id, "Введите минимальный объём ликвидаций (USD):")
    await Settings.waiting_for_limit.set()

@dp.message_handler(state=Settings.waiting_for_limit, content_types=types.ContentTypes.TEXT)
async def process_limit(msg: types.Message, state: FSMContext):
    text = msg.text.replace(",", "").replace("$", "")
    try:
        value = float(text)
        limits[msg.chat.id] = value
        await msg.answer(f"✅ Порог установлен: от ${value:,.2f}", reply_markup=main_menu())
        await state.finish()
    except ValueError:
        await msg.answer("❌ Не похоже на число. Попробуйте ещё раз:")

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
    # 1) Получаем список всех фьючерсных символов USDT (category=linear)
    try:
        r = requests.get("https://api.bybit.com/v5/market/instruments-info?category=linear")
        r.raise_for_status()
        insts = r.json()["result"]["list"]
        symbols = [inst["symbol"] for inst in insts]
    except Exception as e:
        print(f"❌ Не удалось получить список символов: {e}")
        symbols = []

    topics = [f"allLiquidation.{sym}" for sym in symbols]
    while True:
        try:
            async with websockets.connect(EXCHANGE_WS) as ws:
                # 2) Подписываемся на все каналы allLiquidation.{symbol}
                await ws.send(json.dumps({"op": "subscribe", "args": topics}))

                # 3) Слушаем и фильтруем по порогу
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    topic = data.get("topic", "")
                    if topic.startswith("allLiquidation") and "data" in data:
                        for item in data["data"]:
                            vol   = float(item["v"])
                            if vol < limits.get(CHAT_ID, 100_000.0):
                                continue
                            sym   = item["s"]
                            side  = item["S"]
                            price = float(item["p"])
                            ts    = datetime.fromtimestamp(item["T"]/1000).strftime("%Y-%m-%d %H:%M:%S")
                            text = (
                                f"💥 Ликвидация {sym}\n"
                                f"• Сторона: {side}\n"
                                f"• Объём: ${vol:,.2f}\n"
                                f"• Цена: {price}\n"
                                f"• Время: {ts}"
                            )
                            await bot.send_message(CHAT_ID, text)
        except Exception as e:
            print(f"WebSocket error: {e}. Reconnecting in 5s…")
            await asyncio.sleep(5)

# ---- Webhook setup ----
async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    # фоновый таск
    asyncio.create_task(liquidation_listener())

async def on_shutdown(dp):
    await bot.delete_webhook()

# ---- Entry point ----
if __name__ == "__main__":
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        skip_updates=True,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
