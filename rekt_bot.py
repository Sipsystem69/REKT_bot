# rekt_bot.py

import os
import asyncio
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from dotenv import load_dotenv
import websockets

# ---- Load environment ----
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")      # 7306953549:...
CHAT_ID   = int(os.getenv("CHAT_ID"))   # 1487834484

# Bybit public liquidation endpoint (V2 public)
EXCHANGE_WS = "wss://stream.bybit.com/realtime_public"

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
    kb.add(
        InlineKeyboardButton("🔗 Coinglass", url="https://www.coinglass.com")
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
    await cq.message.answer(
        "Введите минимальный объём ликвидаций (USD). Например, 15000 или 15k → $15 000:"
    )
    await Settings.waiting_for_limit.set()

@dp.message_handler(state=Settings.waiting_for_limit)
async def process_limit(msg: types.Message, state: FSMContext):
    text = msg.text.replace(',', '').replace('$', '').strip().lower()
    try:
        if text.endswith('k'):
            value = float(text[:-1]) * 1_000
        else:
            num = float(text)
            value = num * 1_000 if num < 1000 else num
        limits[msg.chat.id] = value
        await msg.answer(f"✅ Порог установлен: от ${value:,.2f}", reply_markup=main_menu())
        await state.finish()
    except ValueError:
        await msg.answer("❌ Не похоже на число. Попробуйте ещё раз:")

@dp.callback_query_handler(lambda c: c.data == "set_list")
async def callback_set_list(cq: types.CallbackQuery):
    await cq.answer()
    await cq.message.answer(
        "Выберите режим списка ликвидаций:", reply_markup=list_menu()
    )
    await ListSettings.choosing_mode.set()

@dp.callback_query_handler(lambda c: c.data.startswith("list_"), state=ListSettings.choosing_mode)
async def process_list_choice(cq: types.CallbackQuery, state: FSMContext):
    await cq.answer()
    mode = cq.data
    if mode == "list_cancel":
        await cq.message.answer("❌ Отмена.", reply_markup=main_menu())
    else:
        desc_map = {
            "list_all":      "🟡 Режим: все токены",
            "list_no_top20": "🟡 Режим: без топ 20",
            "list_no_top50": "🟡 Режим: без топ 50",
        }
        list_modes[cq.from_user.id] = mode
        await cq.message.answer(f"✅ {desc_map[mode]}", reply_markup=main_menu())
    await state.finish()

# ---- WebSocket listener ----
async def liquidation_listener():
    while True:
        try:
            async with websockets.connect(EXCHANGE_WS) as ws:
                await ws.send(json.dumps({"op": "subscribe", "args": ["liquidation"]}))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    if data.get("topic") == "liquidation" and data.get("data"):
                        for itm in data["data"]:
                            vol = float(itm["qty"]) * float(itm["price"])
                            if vol < limits.get(CHAT_ID, 100_000.0):
                                continue
                            symbol = itm["symbol"]
                            url = f"https://www.coinglass.com/liquidation/{symbol}"
                            ts = datetime.fromtimestamp(itm["time"]/1000).strftime("%Y-%m-%d %H:%M:%S")
                            text = (
                                f"💥 Ликвидация <a href=\"{url}\">{symbol}</a>\n"
                                f"• Сторона: {itm['side']}\n"
                                f"• Объём: ${vol:,.2f}\n"
                                f"• Цена: {itm['price']}\n"
                                f"• Время: {ts}"
                            )
                            await bot.send_message(
                                CHAT_ID,
                                text,
                                parse_mode=types.ParseMode.HTML,
                                disable_web_page_preview=True
                            )
        except Exception as e:
            print(f"WebSocket error: {e}. Reconnecting in 5s…")
            await asyncio.sleep(5)

# ---- Entry point: Long Polling ----
if __name__ == "__main__":
    # Запуск через long polling вместо webhook (устойчивость кнопок)
    from aiogram.utils import executor as _executor
    _executor.start_polling(dp, skip_updates=False)