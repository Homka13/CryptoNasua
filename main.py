import asyncio
import logging
import sys
from typing import Dict, Any, Optional

from config import config
from exchange_service import ExchangeService
from strategy import HybridStrategy
from risk_manager import RiskManager
from telegram_bot import TelegramInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class TradingBot:
    """Main Orchestrator for the Bybit Crypto Trading Bot ($10 starting budget)."""

    def __init__(self):
        self.exchange = ExchangeService()
        self.strategy = HybridStrategy()
        self.risk_manager = RiskManager()
        self.current_position: Optional[Dict[str, Any]] = None
        self.latest_meta: Dict[str, Any] = {}
        
        # Initialize LLM Analyst filter
        from llm_analyst import LLMAnalyst
        self.llm_analyst = LLMAnalyst()
        
        # Initialize Telegram
        self.telegram = TelegramInterface(
            get_status_fn=self.get_bot_status_str,
            get_balance_fn=self.get_balance_str
        )

    def get_bot_status_str(self) -> str:
        meta = self.latest_meta
        price = meta.get('price', 0.0)
        rsi = meta.get('rsi', 0.0)
        trend = meta.get('trend', 'UNKNOWN')

        pos_str = "None"
        if self.current_position:
            entry = self.current_position['entry_price']
            amt = self.current_position['amount']
            pnl_pct = ((price - entry) / entry) * 100 if price > 0 else 0.0
            pos_str = f"{amt:.4f} @ ${entry:.2f} (PnL: {pnl_pct:+.2f}%)"

        return (
            f"📊 *BYBIT BOT STATUS*\n"
            f"• Mode: `{'PAPER TRADING' if config.paper_trading else 'LIVE'}`\n"
            f"• Symbol: `{config.symbol}` ({config.timeframe})\n"
            f"• Last Price: `${price:.2f}`\n"
            f"• RSI (14): `{rsi:.1f}`\n"
            f"• Trend: `{trend}`\n"
            f"• LLM Filter: `{'ENABLED (' + config.llm_provider.upper() + ')' if config.use_llm_confirmation else 'DISABLED'}`\n"
            f"• Active Position: `{pos_str}`"
        )

    def get_balance_str(self) -> str:
        try:
            bal = self.exchange.fetch_balance()
            usdt_free = bal.get('USDT', {}).get('free', 0.0)
            base_currency = config.symbol.split('/')[0]
            coin_free = bal.get(base_currency, {}).get('free', 0.0)
            return (
                f"💰 *ACCOUNT BALANCE*\n"
                f"• Available USDT: `${usdt_free:.2f}`\n"
                f"• {base_currency} Balance: `{coin_free:.4f}`"
            )
        except Exception as e:
            return f"❌ Error fetching balance: {e}"

    async def run_loop(self):
        logger.info("🚀 Starting Bybit Trading Bot loop...")
        await self.telegram.send_alert(
            f"🚀 *Trading Bot Started!*\n"
            f"Mode: `{'PAPER TRADING' if config.paper_trading else 'LIVE'}`\n"
            f"Capital: `${config.initial_capital:.2f}`\n"
            f"Pair: `{config.symbol}` ({config.timeframe})\n"
            f"LLM Filter: `{'ENABLED' if config.use_llm_confirmation else 'DISABLED'}`"
        )

        while True:
            try:
                if not self.telegram.is_active:
                    await asyncio.sleep(5)
                    continue

                # 1. Fetch candles
                df = self.exchange.fetch_ohlcv(config.symbol, config.timeframe, limit=100)
                
                # 2. Analyze Strategy
                signal, reason, meta = self.strategy.analyze(df, self.current_position)
                self.latest_meta = meta
                
                logger.info(f"[{config.symbol} ${meta.get('price', 0):.2f}] Signal: {signal} | Reason: {reason}")

                # 3. Handle Buy Signal
                if signal == 'BUY' and self.current_position is None:
                    # Query LLM Confirmation Filter if enabled
                    is_confirmed, llm_reason = await self.llm_analyst.evaluate_trade_signal(
                        config.symbol, config.timeframe, meta, reason
                    )

                    if not is_confirmed:
                        logger.warning(f"🛑 BUY signal rejected by LLM Analyst: {llm_reason}")
                        await self.telegram.send_alert(f"⚠️ *BUY Signal REJECTED by LLM*: {llm_reason}")
                    else:
                        bal = self.exchange.fetch_balance()
                        usdt_free = bal.get('USDT', {}).get('free', 0.0)
                        
                        is_allowed, amount, risk_reason = self.risk_manager.calculate_position_size(
                            usdt_free, meta['price']
                        )

                        if is_allowed:
                            logger.info(f"Executing BUY: {risk_reason}")
                            order = self.exchange.create_spot_order('buy', amount, meta['price'])
                            
                            self.current_position = {
                                'entry_price': meta['price'],
                                'amount': amount,
                                'order_id': order.get('id')
                            }
                            
                            await self.telegram.send_alert(
                                f"🟢 *BUY ORDER EXECUTED*\n"
                                f"• Pair: `{config.symbol}`\n"
                                f"• Price: `${meta['price']:.2f}`\n"
                                f"• Amount: `{amount:.4f}`\n"
                                f"• Strategy Reason: {reason}\n"
                                f"• {llm_reason}"
                            )
                        else:
                            logger.warning(f"BUY rejected by RiskManager: {risk_reason}")

                # 4. Handle Sell Signal
                elif signal == 'SELL' and self.current_position is not None:
                    amount = self.current_position['amount']
                    entry_p = self.current_position['entry_price']
                    curr_p = meta['price']
                    pnl_pct = ((curr_p - entry_p) / entry_p) * 100

                    logger.info(f"Executing SELL ({amount:.4f} coins @ ${curr_p:.2f}). Reason: {reason}")
                    self.exchange.create_spot_order('sell', amount, curr_p)
                    
                    await self.telegram.send_alert(
                        f"🔴 *SELL ORDER EXECUTED*\n"
                        f"• Pair: `{config.symbol}`\n"
                        f"• Exit Price: `${curr_p:.2f}` (Entry: `${entry_p:.2f}`)\n"
                        f"• PnL: `{pnl_pct:+.2f}%`\n"
                        f"• Reason: {reason}"
                    )
                    self.current_position = None

            except Exception as e:
                logger.error(f"Error in bot loop: {e}")
                await asyncio.sleep(10)

            # Pause between candles / scans
            await asyncio.sleep(10)

async def main():
    bot = TradingBot()
    
    # Initialize & Start Private Web Dashboard Server
    from dashboard_server import DashboardServer
    dashboard_server = DashboardServer(bot)
    asyncio.create_task(dashboard_server.start())

    await bot.run_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot manually stopped by keyboard interrupt.")
