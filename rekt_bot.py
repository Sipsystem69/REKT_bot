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
WEBHOOK_HOST = os.getenv("WEBHOOK_URL")      # https://your-app.onrender.com
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL  = WEBHOOK_HOST + WEBHOOK_PATH

# Render port
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 5000))

# Bybit public liquidation endpoint
EXCHANGE_WS = "wss://stream.bybit.com/realtime_public"

# ---- FSM States ----
class Settings(StatesGroup):
    waiting_for_limit = State()

class ListSettings(StatesGroup):
    choosing_mode = State()

# ---- In-memory storage ----
limits     = {}
list_modes = {}

# ---- Bot init ----
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(bot, storage=MemoryStorage())

# ---- Keyboards ----
def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💲 Лимит ByBit", callback_data="set_limit"),
        InlineKeyboardButton("⚫️ Список ByBit", callback_data="set_list"),
    )
    return kb

# ---- Handlers ----
@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    limits[msg.chat.id]     = limits.get(msg.chat.id, 100_000.0)
    list_modes[msg.chat.id] = list_modes.get(msg.chat.id, "list_all")
    await msg.answer(
        "Привет! Я сканирую ByBit на предмет ликвидаций.\n\n" +
        "Выберите действие:",
        reply_markup=main_menu()
    )

@dp.callback_query_handler(lambda c: c.data == "set_limit")
async def callback_set_limit(cq: types.CallbackQuery):
    await cq.answer()
    await bot.send_message(cq.from_user.id,
        "Введите минимальный объём ликвидаций (USD):")
    await Settings.waiting_for_limit.set()

@dp.message_handler(state=Settings.waiting_for_limit)
async def process_limit(msg: types.Message, state: FSMContext):
    text = msg.text.replace(',', '').replace('$', '').strip()
    try:
        value = float(text)
        limits[msg.chat.id] = value
        await msg.answer(f"✅ Порог установлен: от ${value:,.2f}", reply_markup=main_menu())
        await state.finish()
    except:
        await msg.answer("❌ Не похоже на число. Попробуйте ещё раз:")

@dp.callback_query_handler(lambda c: c.data == "set_list")
async def callback_set_list(cq: types.CallbackQuery):
    await cq.answer()
    # omitted for brevity
    await cq.answer()  # placeholder

# ---- WebSocket listener ----
async def liquidation_listener():
    while True:
        try:
            async with websockets.connect(EXCHANGE_WS) as ws:
                await ws.send(json.dumps({"op":"subscribe","args":["liquidation"]}))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("topic") == "liquidation":
                        for item in data.get("data", []):
                            vol = float(item['qty']) * float(item['price'])
                            if vol < limits.get(CHAT_ID, 100_000.0):
                                continue
                            symbol = item['symbol']
                            # Coinglass link per instrument
                            cg_url = f"https://www.coinglass.com/liquidation/{symbol}"
                            ts = datetime.fromtimestamp(item['time'] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                            text = (
                                f"💥 Ликвидация <a href=\"{cg_url}\">{symbol}</a>\n"
                                f"• Сторона: {item['side']}\n"
                                f"• Объём: ${vol:,.2f}\n"
                                f"• Цена: {item['price']}\n"
                                f"• Время: {ts}"
                            )
                            await bot.send_message(
                                CHAT_ID, text,
                                parse_mode=types.ParseMode.HTML,
                                disable_web_page_preview=True
                            )
        except Exception as e:
            print(f"WS error: {e}, reconnect in 5s")
            await asyncio.sleep(5)

# ---- Startup/shutdown ----
async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    asyncio.create_task(liquidation_listener())

async def on_shutdown(dp):
    await bot.delete_webhook()

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