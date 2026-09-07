# 🤖 CryptoNasua — Bybit Speculation Trading Bot

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Exchange](https://img.shields.io/badge/exchange-Bybit-yellow.svg)
![Trading Mode](https://img.shields.io/badge/mode-Paper%20%2F%20Live-green.svg)
![LLM Integration](https://img.shields.io/badge/AI-Kimi%20%2F%20Moonshot%20%2F%20Gemini%20%2F%20DeepSeek-purple.svg)
![Tests](https://img.shields.io/badge/tests-37%20passed-brightgreen.svg)
![License](https://img.shields.io/badge/license-MIT-brightgreen.svg)

**CryptoNasua** — це сучасний, високоадаптивний торговий бот для біржі **Bybit (Spot)**, спеціально оптимізований для торгівлі з невеликим стартовим капіталом (від **$10**). 

Бот комбінує класичні індикатори технічного аналізу (RSI, EMA 20/50), суворий ризик-менеджмент, підтвердження угод за допомогою штучного інтелекту (LLM Analyst), сповіщення в **Telegram** та зручний **Web Dashboard**.

---

## 🌟 Основні Можливості

- 💵 **Оптимізація під малий капітал ($10+)**: Автоматичний розрахунок ордерів з урахуванням мінімальних лімітів Bybit.
- 🧪 **Paper Trading (Dry-Run)**: Вбудована симуляція торгівлі в режимі реального часу без ризику втрати коштів.
- 📈 **Гібридна стратегія (RSI + EMA)**: Вхід у позицію при перепроданості (RSI < 40) та підтвердженні висхідного тренду (EMA 20 > EMA 50).
- 🛡 **Динамічний Ризик-Менеджмент**: Автоматичне виставлення Stop-Loss (-2.0%) та Take-Profit (+3.5%), захист від переторгівлі.
- 🧠 **LLM AI Analyst**: Додатковий фільтр угод на базі **Kimi (Moonshot)**, **Gemini**, **DeepSeek** або **OpenAI** для виявлення хибних пробоїв ("bull-traps").
- 📱 **Інтерактивний Telegram Бот**: Миттєві сповіщення про відкриття/закриття угод та команди керування (`/status`, `/balance`, `/stop`).
- 🌐 **Web Dashboard**: Асинхронний веб-інтерфейс для моніторингу статусу бота з можливістю авторизації через Google OAuth.
- 📉 **Backtesting Engine**: Модуль для тестування стратегії на історичних свічках перед запуском у реальному часі.

---

## 📂 Структура Проекту

```
CryptoNasua/
├── main.py                # Головний оркестратор з Dependency Injection та асинхронним циклом
├── config.py              # Завантаження та валідація конфігурації (.env)
├── strategy.py            # Логіка технічного аналізу (RSI, EMA, TP/SL) — SignalResult dataclass
├── exchange_service.py    # Async CCXT (async_support) + BaseExchangeService ABC,
│                          #   BybitLiveExchange / BybitPaperExchange ізольовано
├── state_store.py         # Потокобезпечний BotStateStore (asyncio.Lock) + авто-збереження позиції
├── risk_manager.py        # Контроль просідання, розміру позицій, Stop-Loss/Trailing Stop
├── llm_analyst.py         # Аналітичний модуль ШІ (Kimi/Moonshot, Gemini, DeepSeek, OpenAI)
├── telegram_bot.py        # Телеграм-бот для сповіщень та інтерфейсу
├── dashboard_server.py    # Асинхронний Web Dashboard сервер (aiohttp)
├── backtest.py            # Модуль тестування на історичних даних
├── tests/                 # Автоматизовані тести (pytest + pytest-asyncio)
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

# Фільтр угод через ШІ (Опціонально: kimi / moonshot / deepseek / gemini / openai)
USE_LLM_CONFIRMATION=false
LLM_PROVIDER=deepseek
LLM_API_KEY=ваш_gemini_or_openai_api_key
DEEPSEEK_API_KEY=ваш_deepseek_api_key
DEEPSEEK_MODEL=deepseek-chat

# Moonshot AI (Kimi) — OpenAI-сумісний API
MOONSHOT_API_KEY=ваш_moonshot_api_key
MOONSHOT_MODEL=kimi-k1.5
MOONSHOT_BASE_URL=https://api.moonshot.cn/v1

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

### 4. Запуск тестів
```bash
pytest -v
```

---

## ⚙️ Асинхронна архітектура (Refactor)

Вся біржа працює на **чистому asyncio**: міграція з синхронного `ccxt.bybit()` на
`ccxt.async_support.bybit()` усунула блокуючі виклики всередині event loop.

- `exchange_service.BaseExchangeService` — абстрактний інтерфейс усіх біржових операцій
  (усі методи є `async` корутинами).
- `BybitLiveExchange` — реальна торгівля Bybit Spot (public + private CCXT клієнти).
- `BybitPaperExchange` — ізольована симуляція, яка повністю відділена від live-логіки
  (використовує live-потік лише для read-only ринкових даних).
- `close()` — коректне закриття CCXT/aiohttp сесій при завершенні роботи.
- `state_store.BotStateStore` — асинхронно-безпечний стан бота через `asyncio.Lock`
  з опційним збереженням відкритої позиції у `position_state.json` (переживає рестарт).
- `main.TradingBot` приймає залежності через `__init__` (Dependency Injection) та
  реєструє обробники `SIGINT`/`SIGTERM` для graceful shutdown.

---

## 🤖 Інтеграція з ШІ (LLM Analyst)

При ввімкненні прапорця `USE_LLM_CONFIRMATION=true` бот перед здійсненням купівлі
передає параметри свічки та індикаторів модельному аналітику (**Kimi / Moonshot**,
**Gemini**, **DeepSeek** або **OpenAI**). ШІ аналізує ринковий контекст і дає вердикт
(`CONFIRM` або `REJECT`), запобігаючи входу у сумнівні позиції.

Вердикти парсяться через строго типізовану модель `pydantic.BaseModel`
(`TradeVerdict`) з надійним вилученням JSON з markdown-блоків та експоненційним
backoff + jitter для повторних спроб при мережевих помилках або HTTP 429/5xx.

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
