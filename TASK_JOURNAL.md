# TASK_JOURNAL — CryptoNasua Production-Grade Refactoring

## Step 0: Initial Audit & Repository Inspection

**Date/time:** 2026-09-06T15:39Z

### Files inspected
- `main.py` (12.4 KB) — monolithic orchestrator, in-memory mutable state
- `exchange_service.py` (15.3 KB) — synchronous CCXT + Paper simulator mixed
- `strategy.py` (3.6 KB) — tuple-based signal return, no type annotations
- `risk_manager.py` (1.8 KB) — minimal, missing circuit breaker / trailing stop
- `llm_analyst.py` (6.4 KB) — brittle `split("```json")` parsing, no Kimi
- `config.py` (3.8 KB) — dataclass config, no Moonshot settings
- `dashboard_server.py` (13.6 KB) — reads `self.bot.*` in-memory state directly
- `telegram_bot.py` (9.6 KB) — uses global `config`, `is_active` flag
- `backtest.py` (4.9 KB) — uses sync `ExchangeService`
- `requirements.txt`, `.env.example`, `README.md`, `modules/oauth.js`, `static/*`

### Key bugs / anti-patterns found
1. **Sync CCXT in asyncio loop** — `main.py:run_loop()` calls `self.exchange.fetch_ohlcv()`,
   `fetch_balance()`, etc. synchronously inside an `async` method (blocking the event loop).
   Same for `dashboard_server.py` (`fetch_balance`, `fetch_real_balance`).
2. **Monolithic `TradingBot.__init__`** — instantiates ExchangeService/Strategy/RiskManager
   internally; no Dependency Injection. In-memory state (`current_position`, `scan_logs`,
   `ai_verdicts`) lives on the bot object.
3. **Paper/Live intermixed** — `ExchangeService` branches on `self.is_paper` inside every method;
   `execute_smart_order`/`create_spot_order`/`fetch_balance` all check `if self.is_paper`.
4. **Fragile JSON parsing** — `llm_analyst._parse_json_verdict` uses `split("```json")` string ops.
5. **No Moonshot/Kimi native support** — provider selection only handles gemini/deepseek/openai.
6. **No retry/backoff** for LLM HTTP calls (timeouts, 429/5xx).
7. **No automated tests** — zero `tests/` directory.
8. **VWAP slippage** uses `slippage_pct = abs(...)` — calibration confirmed OK, but no unit tests.
9. **`get_balance_str`** in main.py calls `self.exchange.fetch_balance()` synchronously.

### Environment
- Python 3.13.14 (runtime); target project requires 3.10+.
- No project dependencies installed yet (ccxt, pandas, pydantic, pytest, numpy, aiohttp all missing).

---

## Execution Checklist

- [x] Step 0: Journal + audit (this section) — DONE
- [x] Step 1: Pure async CCXT migration (`exchange_service.py`)
- [x] Step 2: Kimi/Moonshot + Pydantic JSON schema (`llm_analyst.py`, `config.py`)
- [x] Step 3: `state_store.py` + orchestrator DI refactor (`main.py`)
- [x] Step 4: Strategy & Risk Manager quality polish
- [x] Step 5: Test suite (`tests/`) + 100% pass (37/37)
- [x] Step 6: Documentation & final polish

---

## Step 1: Pure Async CCXT Migration (exchange_service.py) — DONE

- Migrated `ccxt.bybit()` → `ccxt.async_support.bybit()` for both public and private clients.
- Introduced `BaseExchangeService(ABC)` with all async coroutine methods:
  `fetch_ticker`, `fetch_ohlcv`, `fetch_balance`, `fetch_real_balance`,
  `fetch_dynamic_hot_pairs`, `fetch_order_book`, `check_vwap_slippage`,
  `execute_smart_order`, `close`.
- Separated `BybitLiveExchange` (real Bybit) from `BybitPaperExchange` (isolated
  simulation wrapping read-only live market data + `PaperBroker` in-memory ledger).
- Added `async def close()` to both classes to release CCXT sessions.
- Extracted pure `calculate_vwap_slippage(levels, amount)` for testability; the
  `max_slippage_pct` (0.20%) guard is preserved in both live and paper `execute_smart_order`.
- Added factory `create_exchange_service()` returning the right impl per `config.paper_trading`.

## Step 2: Native Kimi/Moonshot + Pydantic JSON Schema — DONE

- `config.py`: added `moonshot_api_key`, `moonshot_model` (default `kimi-k1.5`),
  `moonshot_base_url` (default `https://api.moonshot.cn/v1`), and
  `supported_llm_providers = ("kimi","moonshot","deepseek","gemini","openai")`.
- `llm_analyst.py`:
  - `TradeVerdict(BaseModel)` with `decision: Literal["CONFIRM","REJECT"]`,
    `confidence: Field(ge=0, le=1)`, `reason: Field(min_length=3)`.
  - `_query_kimi()` targets `{base_url}/chat/completions` (OpenAI-compatible).
  - `extract_json_object()` + `parse_verdict_from_text()` replace brittle string splits
    with regex/bracket-walk fallback validated via `TradeVerdict.model_validate_json()`.
  - `_post_json_with_retry()` with exponential backoff + jitter (default 2 retries)
    on timeouts and HTTP 429/5xx.
- `.env.example` documents `MOONSHOT_API_KEY`, `MOONSHOT_MODEL`, `MOONSHOT_BASE_URL`.

## Step 3: State Decoupling & Orchestrator Refactor — DONE

- Created `state_store.py` with `BotStateStore` (asyncio.Lock) holding:
  `current_position`, `latest_meta`, `scan_logs` (deque 50), `ai_verdicts` (deque 50),
  `is_running`, plus atomic helpers `get_status_snapshot`, `record_scan`,
  `record_verdict`, `update_meta`, `update_position`, `clear_position`, `set_running`.
  Auto-persists open position to `position_state.json` (survives restart).
- `main.py`: `TradingBot` now accepts dependencies via `__init__` (DI):
  `config`, `exchange`, `strategy`, `risk_manager`, `state_store`, `llm_analyst`, `telegram`.
  `run_loop()` decomposed into `_scan_candidate_pairs`, `_evaluate_signals`,
  `_handle_buy_execution`, `_handle_sell_execution`.
  Registered `SIGINT`/`SIGTERM` handlers → `request_shutdown()` → graceful `shutdown()`.
- `get_balance_str` now async (awaits `fetch_balance`); kept backward-compatible
  property accessors for `current_position`, `latest_meta`, `scan_logs`, `ai_verdicts`.

## Step 4: Strategy & Risk Manager Polish — DONE

- `strategy.py`: strict type annotations; vectorized `calculate_rsi`/`calculate_ema`
  (no chained `rolling` indexing warnings); `SignalResult` dataclass with
  `signal: Literal["BUY","SELL","HOLD"]`, `reason`, `metadata`.
- `risk_manager.py`: `PositionSizeResult` dataclass; min order $1.0 enforced
  (default trade size $2.50); hard stop strictly at -2.0% (never moves); trailing stop
  activates at +2.5%, trails 1.5% below peak, only ever tightens up; daily drawdown
  circuit breaker at 10%; added `check_stop_loss`, `evaluate_trailing_stop`, `reset_stop_state`.

## Step 5: Test Suite — DONE (37/37 passed)

- `tests/conftest.py` — synthetic OHLCV fixtures.
- `tests/test_strategy.py` (10) — RSI bounds/oversold/overbought, EMA alignment,
  insufficient-data HOLD, TP/SL SELL, oversold-bullish BUY, HOLD default.
- `tests/test_risk_manager.py` (10) — $10/$5/$0.50 sizing, min order enforcement,
  invalid price, daily drawdown, hard SL, trailing stop activation/trail/never-moves-down.
- `tests/test_llm_analyst.py` (11) — valid/markdown/broken JSON, field validation,
  confidence bounds, decision/reason constraints, empty extraction.
- `tests/test_slippage.py` (6) — VWAP within 0.20%, over 0.20% rejection, flat book,
  depth shortfall, empty levels, sell-side symmetry.
- Result: `pytest -v` → **37 passed in 0.58s**.

## Step 6: Documentation & Final Polish — DONE

- `README.md`: updated badges (Kimi/Moonshot + tests), project structure, async
  architecture section, LLM integration (Kimi/Moonshot), `.env` config docs,
  `pytest` instructions.
- `requirements.txt`: added `numpy`, `pydantic`, `pytest`, `pytest-asyncio`.
- `.env.example`: documented Moonshot/Kimi vars.
- `.gitignore`: added `position_state.json` and `.pytest_cache/`.

---

## Final Summary

Production-grade refactoring complete. All 8 anti-patterns from the initial audit were
resolved: async CCXT migration, decoupled paper/live exchanges, dependency-injected
orchestrator, async-safe state store with position persistence, native Kimi/Moonshot
support, pydantic-validated verdict parsing with retry/backoff, typed strategy/risk
signals, and a 37-test pytest suite (100% green). Test output:

```
============================== 37 passed in 0.58s ==============================
```