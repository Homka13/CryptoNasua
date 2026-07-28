import logging
import asyncio
from typing import Optional, Callable
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import config

logger = logging.getLogger(__name__)

class TelegramInterface:
    """Provides interactive control and real-time alerts via Telegram."""
    
    def __init__(self, get_status_fn: Optional[Callable] = None, get_balance_fn: Optional[Callable] = None):
        self.token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self.app = None
        self.get_status_fn = get_status_fn
        self.get_balance_fn = get_balance_fn
        self.is_active = True

        if self.token:
            try:
                self.app = Application.builder().token(self.token).build()
                self._setup_handlers()
                logger.info("Telegram Bot initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram Bot: {e}")

    def _setup_handlers(self):
        if not self.app:
            return
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("balance", self._cmd_balance))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

    def _is_authorized(self, update: Update) -> bool:
        if not self.chat_id:
            return True  # If no chat_id locked in env, allow initial setup
        user_chat_id = str(update.effective_chat.id) if update.effective_chat else ""
        user_id = str(update.effective_user.id) if update.effective_user else ""
        if user_chat_id != str(self.chat_id) and user_id != str(self.chat_id):
            logger.warning(f"🚨 UNAUTHORIZED TELEGRAM ACCESS ATTEMPT from Chat ID {user_chat_id} (User: {user_id})")
            return False
        return True

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.message.reply_text("⛔ *Access Denied*: This trading bot is private.", parse_mode="Markdown")
            return
        keyboard = [
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("💰 Balance", callback_data="balance")
            ],
            [
                InlineKeyboardButton("🛑 Pause Bot", callback_data="pause"),
                InlineKeyboardButton("▶️ Resume Bot", callback_data="resume")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🤖 *Bybit Crypto Trading Bot ($10 Capital)*\n\n"
            f"• Mode: `{'PAPER TRADING (Dry-Run)' if config.paper_trading else 'LIVE TRADING'}`\n"
            f"• Pair: `{config.symbol}`\n"
            f"• Timeframe: `{config.timeframe}`\n"
            f"• Trade Size: `${config.trade_size_usdt}`\n\n"
            f"Use the buttons below or commands to control the bot:",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        msg = self._build_status_msg()
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        msg = self._build_balance_msg()
        if update.message:
            await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            return
        self.is_active = False
        if update.message:
            await update.message.reply_text("🛑 *Bot trading paused by user.*", parse_mode="Markdown")

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update):
            await update.callback_query.answer("Access Denied", show_alert=True)
            return
        query = update.callback_query
        await query.answer()

        if query.data == "status":
            await query.edit_message_text(self._build_status_msg(), parse_mode="Markdown")
        elif query.data == "balance":
            await query.edit_message_text(self._build_balance_msg(), parse_mode="Markdown")
        elif query.data == "pause":
            self.is_active = False
            await query.edit_message_text("🛑 *Bot trading paused.* Use /start to resume.", parse_mode="Markdown")
        elif query.data == "resume":
            self.is_active = True
            await query.edit_message_text("▶️ *Bot trading resumed.*", parse_mode="Markdown")

    def _build_status_msg(self) -> str:
        if self.get_status_fn:
            return self.get_status_fn()
        return "📊 *Status*: Bot running smoothly."

    def _build_balance_msg(self) -> str:
        if self.get_balance_fn:
            return self.get_balance_fn()
        return "💰 *Balance*: Initial $10.00 USDT"

    async def send_alert(self, text: str):
        """Sends a high-priority push alert message to the configured Telegram chat."""
        if not self.app or not self.chat_id:
            logger.info(f"[TELEGRAM ALERT LOG]: {text}")
            return
        try:
            await self.app.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Error sending Telegram alert: {e}")

    def run_polling(self):
        """Runs the Telegram bot polling in a non-blocking background task."""
        if self.app:
            asyncio.create_task(self.app.run_polling())
