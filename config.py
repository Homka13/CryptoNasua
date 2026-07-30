import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

@dataclass
class TradingConfig:
    # Exchange & Authentication
    exchange_name: str = os.getenv("EXCHANGE", "bybit").lower()  # 'bybit' or 'binance'
    active_exchange: str = os.getenv("EXCHANGE", "bybit").lower()
    bybit_api_key: str = os.getenv("BYBIT_API_KEY", os.getenv("EXCHANGE_API_KEY", ""))
    bybit_api_secret: str = os.getenv("BYBIT_API_SECRET", os.getenv("EXCHANGE_API_SECRET", ""))
    binance_api_key: str = os.getenv("BINANCE_API_KEY", "")
    binance_api_secret: str = os.getenv("BINANCE_API_SECRET", "")
    binance_testnet: bool = os.getenv("BINANCE_TESTNET", "false").lower() == "true"
    bybit_private_key_path: str = os.getenv("BYBIT_API_PRIVATE_KEY_PATH", "")
    testnet: bool = os.getenv("TESTNET", "false").lower() == "true"
    paper_trading: bool = os.getenv("PAPER_TRADING", "false").lower() == "true"

    # Telegram Security
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Web Dashboard & Security Whitelist
    allowed_google_email: str = os.getenv("ALLOWED_GOOGLE_EMAIL", "")
    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
    jwt_secret: str = os.getenv("JWT_SECRET", "super-secret-crypto-bot-key-2026")
    dashboard_port: int = int(os.getenv("DASHBOARD_PORT", "5001"))

    # System & 24/7 Anti-Sleep Settings
    prevent_sleep: bool = os.getenv("PREVENT_SLEEP", "true").lower() == "true"

    # LLM Confirmation Filter (Gemini / OpenAI / DeepSeek / OpenRouter)
    use_llm_confirmation: bool = os.getenv("USE_LLM_CONFIRMATION", "true").lower() == "true"
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_provider: str = os.getenv("LLM_PROVIDER", "gemini").lower()  # gemini, openai, deepseek
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", os.getenv("LLM_API_KEY", ""))
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    trading_mode: str = os.getenv("TRADING_MODE", "chill").lower()  # chill (sniper 90%) or hunt (aggressor 60%)

    @property
    def min_llm_confidence(self) -> float:
        return 0.90 if self.trading_mode == "chill" else 0.60

    @property
    def trading_mode_display(self) -> str:
        if self.trading_mode == "chill":
            return "🦝 CHILL (Sniper - 90%+ confidence)"
        return "🔥 HUNT (Aggressor - 60%+ confidence)"

    # Capital & Market Settings
    symbol: str = os.getenv("SYMBOL", "SOL/USDT")
    use_dynamic_market_screener: bool = os.getenv("USE_DYNAMIC_SCREENER", "true").lower() == "true"
    min_screener_volume_usdt: float = 5000000.0  # Min 5 Million USDT 24h volume
    screener_top_limit: int = 25                 # Select Top 25 most volatile high-volume pairs
    rejected_cooldown_minutes: int = 15          # Cooldown period for coins rejected by LLM to prevent log spam
    trading_pairs: list = field(default_factory=lambda: [
        "WIF/USDT", "PEPE/USDT", "BONK/USDT", "FLOKI/USDT", 
        "SUI/USDT", "NEAR/USDT", "FET/USDT", "SOL/USDT", "DOGE/USDT"
    ])
    multi_pair_scan: bool = os.getenv("MULTI_PAIR_SCAN", "true").lower() == "true"
    timeframe: str = os.getenv("TIMEFRAME", "15m")
    initial_capital: float = float(os.getenv("INITIAL_CAPITAL", "10.0"))
    trade_size_usdt: float = float(os.getenv("TRADE_SIZE_USDT", "5.50"))
    max_active_orders: int = 3
    min_order_usdt: float = 5.50  # 5.50 USDT safety buffer to clear Bybit/Binance MIN_NOTIONAL ($5.00) rules

    # Risk Management
    max_trade_pct: float = 0.45   # 45% compound trade allocation
    max_daily_drawdown: float = 0.10 # Max daily drawdown
    stop_loss_pct: float = 0.02   # 2.0% Stop Loss
    take_profit_pct: float = 0.035 # 3.5% Take Profit
    max_daily_loss_pct: float = 0.10 # Stop trading if 10% lost in 24h

    # Strategy Parameters (Micro-Grid + RSI / EMA)
    rsi_period: int = 14
    rsi_oversold: float = 40.0
    rsi_overbought: float = 70.0
    ema_fast: int = 20
    ema_slow: int = 50
    grid_levels: int = 3
    grid_step_pct: float = 0.01  # 1% price step between grid levels

    # Adaptive Position Management (Stale / Trend-Reversal Emergency Exits)
    use_position_health_check: bool = os.getenv("USE_POSITION_HEALTH_CHECK", "true").lower() == "true"
    health_min_hold_minutes: float = 2.0      # Grace period before emergency exits may fire
    health_stale_hours: float = 4.0            # Flat-position timeout
    health_stale_pnl_pct: float = 0.5          # |PnL| under this counts as "no movement"
    health_rsi_overheat: float = 75.0          # Take profit early above this RSI
    emergency_exit_cooldown_minutes: int = 30  # Re-entry block after an emergency exit

    # Quant Execution Algorithms (Anti-Slippage & Smart Slicing)
    use_limit_offset: bool = True
    limit_offset_pct: float = 0.0015   # 0.15% limit price offset tolerance
    max_slippage_pct: float = 0.0020   # 0.20% max allowed orderbook VWAP slippage
    use_iceberg: bool = True
    iceberg_slices: int = 3            # Slice order into 3 equal parts
    def save_persisted_config(self):
        try:
            os.makedirs("data", exist_ok=True)
            data = {
                "deepseek_api_key": self.deepseek_api_key,
                "llm_api_key": self.llm_api_key,
                "use_llm_confirmation": self.use_llm_confirmation,
                "trading_mode": self.trading_mode
            }
            with open("data/config_cache.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            pass

    def load_persisted_config(self):
        try:
            if os.path.exists("data/config_cache.json"):
                with open("data/config_cache.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("deepseek_api_key"):
                        self.deepseek_api_key = data["deepseek_api_key"]
                        self.llm_api_key = data.get("llm_api_key", data["deepseek_api_key"])
                    if "use_llm_confirmation" in data:
                        self.use_llm_confirmation = data["use_llm_confirmation"]
                    if "trading_mode" in data:
                        self.trading_mode = data["trading_mode"]
        except Exception as e:
            pass

config = TradingConfig()
config.load_persisted_config()
