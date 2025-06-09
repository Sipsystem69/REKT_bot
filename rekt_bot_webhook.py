
import os
import asyncio
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.utils.executor import start_webhook
from dotenv import load_dotenv
import websockets

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://rekt-bot.onrender.com/webhook/<TOKEN>
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", default=5000))

EXCHANGE_WS = "wss://stream.bybit.com/realtime_public"

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

class Settings(StatesGroup):
    waiting_for_limit = State()

class ListSettings(StatesGroup):
    choosing_mode = State()

limits = {}
list_modes = {}

def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("💲 Лимит ByBit", callback_data="set_limit"),
        InlineKeyboardButton("⚫️ Список ByBit", callback_data="set_list"),
    )
    kb.add(InlineKeyboardButton("🔗 Coinglass", url="https://www.coinglass.com"))
    return kb

def list_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🟡 Все токени", callback_data="list_all"),
        InlineKeyboardButton("🟡 Без топ 20", callback_data="list_no_top20"),
        InlineKeyboardButton("🟡 Без топ 50", callback_data="list_no_top50"),
        InlineKeyboardButton("❌ Отмена", callback_data="list_cancel"),
    )
    return kb

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    limits[msg.chat.id] = limits.get(msg.chat.id, 100_000.0)
    list_modes[msg.chat.id] = list_modes.get(msg.chat.id, "list_all")
    await msg.answer(
        "Привіт! Я сканую Bybit на предмет ліквідацій.n\nВиберіть дію:",
        reply_markup=main_menu()
    )

@dp.callback_query_handler(lambda c: c.data == "set_limit")
async def callback_set_limit(cq: types.CallbackQuery):
    await cq.answer()
    await cq.message.answer("Введіть мінімальний обʼєм ліквідацій (USD):")
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
        await msg.answer(f"✅ Поріг встановлено: ${value:,.2f}", reply_markup=main_menu())
        await state.finish()
    except ValueError:
        await msg.answer("❌ Введено не число. Спробуйте ще раз:")

@dp.callback_query_handler(lambda c: c.data == "set_list")
async def callback_set_list(cq: types.CallbackQuery):
    await cq.answer()
    await cq.message.answer("Виберіть режим списку:", reply_markup=list_menu())
    await ListSettings.choosing_mode.set()

@dp.callback_query_handler(lambda c: c.data.startswith("list_"), state=ListSettings.choosing_mode)
async def process_list_choice(cq: types.CallbackQuery, state: FSMContext):
    await cq.answer()
    mode = cq.data
    if mode == "list_cancel":
        await cq.message.answer("❌ Скасовано.", reply_markup=main_menu())
    else:
        desc_map = {
            "list_all": "🟡 Режим: всі токени",
            "list_no_top20": "🟡 Режим: без топ 20",
            "list_no_top50": "🟡 Режим: без топ 50",
        }
        list_modes[cq.from_user.id] = mode
        await cq.message.answer(f"✅ {desc_map[mode]}", reply_markup=main_menu())
    await state.finish()

async def get_mock_orderflow(symbol: str) -> str:
    return (
        "📊 *Delta*: -12M → +4M (розворот)
"
        "📈 *CVD*: зростає після хвилі ліквідацій
"
        "📉 *OI*: впав, потім пішов у ріст"
    )

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
                            ts = datetime.fromtimestamp(itm["time"]/1000).strftime("%Y-%m-%d %H:%M:%S")
                            url = f"https://www.coinglass.com/liquidation/{symbol}"
                            side = "🔴 Long" if itm["side"] == "Sell" else "🟢 Short"
                            orderflow = await get_mock_orderflow(symbol)
                            text = (
                                f"💥 *Ліквідації на {symbol}*
"
                                f"{side} | ${vol:,.2f} @ {itm['price']}
"
                                f"🕒 {ts} UTC
"
                                f"{orderflow}

"
                                f"[Переглянути на Coinglass]({url})"
                            )
                            await bot.send_message(
                                CHAT_ID,
                                text,
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
        except Exception as e:
            print(f"WebSocket error: {e}. Reconnecting in 5s…")
            await asyncio.sleep(5)

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    asyncio.create_task(liquidation_listener())

async def on_shutdown(dp):
    await bot.delete_webhook()

if __name__ == "__main__":
    start_webhook(
        dispatcher=dp,
        webhook_path=f"/webhook/{BOT_TOKEN}",
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
