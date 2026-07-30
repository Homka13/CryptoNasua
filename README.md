# 🤖 CryptoNasua — Bybit Speculation Trading Bot

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Exchange](https://img.shields.io/badge/exchange-Bybit-yellow.svg)
![Trading Mode](https://img.shields.io/badge/mode-Paper%20%2F%20Live-green.svg)
![LLM Integration](https://img.shields.io/badge/AI-Gemini%20%2F%20OpenAI-purple.svg)
![License](https://img.shields.io/badge/license-MIT-brightgreen.svg)

**CryptoNasua** — це сучасний, високоадаптивний торговий бот для біржі **Bybit (Spot)**, спеціально оптимізований для торгівлі з невеликим стартовим капіталом (від **$10**). 

Бот комбінує класичні індикатори технічного аналізу (RSI, EMA 20/50), суворий ризик-менеджмент, підтвердження угод за допомогою штучного інтелекту (LLM Analyst), сповіщення в **Telegram** та зручний **Web Dashboard**.

---

## 🌟 Основні Можливості

- 💵 **Оптимізація під малий капітал ($10+)**: Автоматичний розрахунок ордерів з урахуванням мінімальних лімітів Bybit.
- 🧪 **Paper Trading (Dry-Run)**: Вбудована симуляція торгівлі в режимі реального часу без ризику втрати коштів.
- 📈 **Гібридна стратегія (RSI + EMA)**: Вхід у позицію при перепроданості (RSI < 40) та підтвердженні висхідного тренду (EMA 20 > EMA 50).
- 🛡 **Динамічний Ризик-Менеджмент**: Автоматичне виставлення Stop-Loss (-2.0%) та Take-Profit (+3.5%), захист від переторгівлі.
- 🧠 **LLM AI Analyst**: Додатковий фільтр угод на базі **Google Gemini** або **OpenAI** для виявлення хибних пробоїв ("bull-traps").
- 📱 **Інтерактивний Telegram Бот**: Миттєві сповіщення про відкриття/закриття угод та команди керування (`/status`, `/balance`, `/stop`).
- 🌐 **Web Dashboard**: Асинхронний веб-інтерфейс для моніторингу статусу бота з можливістю авторизації через Google OAuth.
- 📉 **Backtesting Engine**: Модуль для тестування стратегії на історичних свічках перед запуском у реальному часі.

---

## 📂 Структура Проекту

```
CryptoNasua/
├── main.py                # Головний оркестратор та асинхронний цикл бота
├── config.py              # Завантаження та валідація конфігурації (.env)
├── core/                  # Логіка бота: strategy.py (RSI, EMA, BB), risk_manager.py, llm_analyst.py
├── exchanges/             # Адаптери бірж (Bybit, Binance, Paper Trading) через CCXT
├── telegram_bot.py        # Телеграм-бот для сповіщень та інтерфейсу
├── dashboard_server.py    # Асинхронний Web Dashboard сервер (aiohttp)
├── backtest.py            # Модуль тестування на історичних даних
├── static/                # Статичні файли (HTML/JS/CSS) для веб-дашборду
├── requirements.txt       # Залежності Python
├── .env.example           # Шаблон конфігураційних змінних
└── README.md              # Документація проекту
```

---

## 🛠 Швидкий Старт

### 1. Клонування репозиторію

```bash
git clone https://github.com/Homka13/CryptoNasua.git
cd CryptoNasua
```

### 2. Створення та активація віртуального середовища

**Windows (PowerShell):**
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Встановлення залежностей

```bash
pip install -r requirements.txt
```

---

## ⚙️ Налаштування Конфігурації (`.env`)

Створіть файл `.env` у корені проекту на основі шаблону `.env.example`:

```bash
cp .env.example .env
```

Заповніть відповідні значення в `.env`:

```ini
# Bybit API Ключі (Отримайте на Bybit -> API Management)
BYBIT_API_KEY=ваш_bybit_api_key
BYBIT_API_SECRET=ваш_bybit_api_secret

# Telegram Бот (Отримайте в @BotFather на Telegram)
TELEGRAM_BOT_TOKEN=ваш_telegram_bot_token
TELEGRAM_CHAT_ID=ваш_telegram_chat_id

# Фільтр угод через ШІ (Опціонально: Gemini / OpenAI)
USE_LLM_CONFIRMATION=false
LLM_PROVIDER=gemini
LLM_API_KEY=ваш_gemini_or_openai_api_key

# Налаштування Торгівлі
SYMBOL=SOL/USDT
TIMEFRAME=15m
INITIAL_CAPITAL=10.0
TRADE_SIZE_USDT=2.5
PAPER_TRADING=true
TESTNET=false
```

---

## 🚀 Запуск Ботів та Завдань

### 1. Тестування на історичних даних (Backtesting)
Перевірте ефективність стратегії перед запуском:
```bash
python backtest.py
```

### 2. Запуск торгівлі (Paper Trading / Симуляція)
Запустіть бота у безпечному режимі симуляції:
```bash
python main.py
```

### 3. Запуск Веб-Дашборду
Для запуску панелі моніторингу:
```bash
python dashboard_server.py
```
Після запуску дашборд буде доступний за адресою: `http://localhost:8080`

---

## 🤖 Інтеграція з ШІ (LLM Analyst)

При ввімкненні прапорця `USE_LLM_CONFIRMATION=true` бот перед здійсненням купівлі передає параметри свічки та індикаторів модельному аналітику (Google Gemini або OpenAI). ШІ аналізує ринковий контекст і дає вердикт (`CONFIRM` або `REJECT`), запобігаючи входу у сумнівні позиції.

---

## 📱 Телеграм Керування

Усі сповіщення надходять безпосередньо у ваш Telegram чат. Основні команди:
- `/status` — Перегляд поточного стану бота, цін та активної позиції.
- `/balance` — Перегляд балансу USDT та криптовалюти.
- `/stop` — Безпечна зупинка бота.

---

## ⚠️ Застереження про ризики (Risk Disclaimer)

> Торгівля криптовалютами пов'язана з високим рівнем ризику. Цей проект створено виключно для навчальних та ознайомчих цілей. Автори репозиторію не несуть відповідальності за будь-які фінансові втрати, спричинені використанням цього бота. Завжди тестуйте стратегії в режимі **Paper Trading** перед торгівлею реальними коштами!

---

## 📝 Ліцензія

Цей проект розповсюджується під ліцензією [MIT](LICENSE).
