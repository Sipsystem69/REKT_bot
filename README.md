# 💥 Rekt Bot — Telegram-бот ліквідацій Bybit

Цей бот у реальному часі підключається до WebSocket біржі Bybit та надсилає сповіщення про великі ліквідації у Telegram. Також формує сигнал зі змодельованими даними Delta, CVD, OI.

## 🚀 Можливості
- Стежить за масовими ліквідаціями
- Надсилає структуровані сигнали в Telegram
- Формат повідомлень у стилі трейдерських ботів
- Встановлення порогу $ через меню
- Готовий до доповнення реальними API для orderflow

## 🧠 Техно-стек
- Python 3.10+
- `aiogram` для Telegram
- WebSocket API Bybit
- `dotenv` для середовища

## ⚙️ Запуск

1. Клонуй репозиторій:
   ```bash
   git clone https://github.com/your-nickname/rekt_bot.git
   cd rekt_bot
