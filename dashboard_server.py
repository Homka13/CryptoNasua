import logging
import os
import json
import time
import asyncio
from aiohttp import web
from typing import Dict, Any, Callable, Optional
from config import config

logger = logging.getLogger(__name__)

class DashboardServer:
    """Async Web Server for private Dashboard UI with Google Email Whitelisting."""

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.app = web.Application()
        self.runner = None
        self.site = None
        self.authenticated_sessions = set()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/favicon.ico', self.handle_favicon)
        self.app.router.add_post('/api/auth/google', self.handle_google_auth)
        self.app.router.add_post('/api/auth/register', self.handle_register)
        self.app.router.add_post('/api/auth/login', self.handle_password_login)
        self.app.router.add_get('/api/status', self.handle_get_status)
        self.app.router.add_get('/api/orders', self.handle_get_orders)
        self.app.router.add_get('/api/history', self.handle_get_history)
        self.app.router.add_get('/api/export/csv', self.handle_export_csv)
        self.app.router.add_get('/api/klines', self.handle_get_klines)
        self.app.router.add_post('/api/control', self.handle_post_control)
        
        # Serve static assets
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        if os.path.exists(static_dir):
            self.app.router.add_static('/static/', path=static_dir, name='static')

    async def handle_favicon(self, request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def handle_index(self, request: web.Request) -> web.Response:
        static_index = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
        if os.path.exists(static_index):
            with open(static_index, 'r', encoding='utf-8') as f:
                return web.Response(text=f.read(), content_type='text/html')
        return web.Response(text="<h1>Web Dashboard static/index.html missing</h1>", content_type='text/html')

    async def handle_register(self, request: web.Request) -> web.Response:
        """Handles new user account registration."""
        try:
            body = await request.json()
            email = body.get('email', '').strip().lower()
            password = body.get('password', '')

            from user_manager import user_manager
            success, msg, token = user_manager.register_user(email, password)
            if not success:
                return web.json_response({'success': False, 'error': msg}, status=400)

            session_token = token or f"session_{email}"
            self.authenticated_sessions.add(session_token)
            if self.bot:
                self.bot.trading_active = True

            return web.json_response({
                'success': True,
                'token': session_token,
                'email': email,
                'message': msg
            })
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)

    async def handle_password_login(self, request: web.Request) -> web.Response:
        """Handles password-based user authentication."""
        try:
            body = await request.json()
            email = body.get('email', '').strip().lower()
            password = body.get('password', '')

            from user_manager import user_manager
            success, msg, user, token = user_manager.authenticate_user(email, password)
            if not success:
                return web.json_response({'success': False, 'error': msg}, status=401)

            session_token = token or f"session_{email}"
            self.authenticated_sessions.add(session_token)
            if self.bot:
                self.bot.trading_active = True

            return web.json_response({
                'success': True,
                'token': session_token,
                'email': email,
                'message': msg
            })
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)

    async def handle_google_auth(self, request: web.Request) -> web.Response:
        """Authenticates Google OAuth user email against ALLOWED_GOOGLE_EMAIL whitelist."""
        try:
            body = await request.json()
            email = body.get('email', '').strip().lower()

            import re
            email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if not re.match(email_regex, email):
                return web.json_response({
                    'success': False,
                    'error': "Некоректний формат email адреси. Будь ласка, введіть дійсний Email."
                }, status=400)
            
            allowed_email = config.allowed_google_email.strip().lower()

            if allowed_email and email != allowed_email:
                logger.warning(f"🚨 BLOCKED UNAUTHORIZED GOOGLE LOGIN ATTEMPT: {email}")
                return web.json_response({
                    'success': False,
                    'error': f"Доступ заборонено: Email '{email}' відсутній у списку дозволених."
                }, status=403)

            session_token = f"session_{email}"
            self.authenticated_sessions.add(session_token)
            if self.bot:
                self.bot.trading_active = True
            logger.info(f"🟢 AUTHORIZED GOOGLE LOGIN SUCCESSFUL: {email}")
            
            return web.json_response({
                'success': True,
                'token': session_token,
                'email': email,
                'message': 'Google Authentication Successful'
            })
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)

    def _verify_session(self, request: web.Request) -> bool:
        auth_header = request.headers.get('Authorization', '')
        token = auth_header.replace('Bearer ', '').strip()
        if not token or token == 'null' or token == 'undefined':
            return False
        from user_manager import user_manager
        return user_manager.verify_session(token) or (token in self.authenticated_sessions)

    async def handle_get_status(self, request: web.Request) -> web.Response:
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        meta = getattr(self.bot, 'latest_meta', {}) or {}
        now = time.time()
        if not hasattr(self, '_cached_bal_time') or (now - getattr(self, '_cached_bal_time', 0) > 10.0):
            try:
                self._cached_bal = self.bot.exchange.fetch_balance()
                if hasattr(self.bot.exchange, 'fetch_real_balance'):
                    self._cached_real_bal = self.bot.exchange.fetch_real_balance()
                else:
                    self._cached_real_bal = {}
                self._cached_bal_time = now
            except Exception as e:
                logger.error(f"Error fetching balance for dashboard: {e}")

        bal = getattr(self, '_cached_bal', {}) or {}
        usdt_free = bal.get('USDT', {}).get('free', 0.0)
        usdt_total = bal.get('USDT', {}).get('total', usdt_free)
        usdt_used = bal.get('USDT', {}).get('used', 0.0)

        real_usdt = None
        real_bal = getattr(self, '_cached_real_bal', {}) or {}
        if real_bal and 'USDT' in real_bal:
            real_usdt = real_bal['USDT'].get('total', real_bal['USDT'].get('free', 0.0))

        # Build enriched active positions payload with live meta
        active_positions_payload = []
        for pos in getattr(self.bot, 'active_positions', []):
            pos_payload = dict(pos)
            pos_sym = pos_payload.get('symbol')
            pos_metas = getattr(self.bot, 'active_position_metas', {})
            pos_meta = pos_metas.get(pos_sym, {})
            if pos_meta:
                pos_payload['current_price'] = pos_meta.get('price', pos_payload.get('entry_price'))
                pos_payload['rsi'] = pos_meta.get('rsi', 50.0)
                pos_payload['trend'] = pos_meta.get('trend', 'UNKNOWN')
                pos_payload['ema_fast'] = pos_meta.get('ema_fast', 0.0)
                pos_payload['ema_slow'] = pos_meta.get('ema_slow', 0.0)
            active_positions_payload.append(pos_payload)

        # Backward compat: also send the first position as active_position
        active_pos_payload = active_positions_payload[0] if active_positions_payload else None

        # Full wallet breakdown (incl. dust below the exchange's sellable minimum),
        # so the UI can show everything and flag what's actually tradable.
        wallet_holdings = []
        tracked_symbols = {p.get('symbol') for p in active_positions_payload}
        try:
            raw_list = bal.get('info', {}).get('result', {}).get('list', [])
            raw_coins = raw_list[0].get('coin', []) if raw_list else []
            for c in raw_coins:
                coin = c.get('coin')
                if not coin or coin.upper() in ('USDT', 'USDC', 'USD'):
                    continue
                amount = float(c.get('walletBalance', 0) or 0)
                usd_value = float(c.get('usdValue', 0) or 0)
                if amount <= 0:
                    continue
                symbol = f"{coin}/USDT"
                wallet_holdings.append({
                    'coin': coin,
                    'symbol': symbol,
                    'amount': amount,
                    'usd_value': usd_value,
                    'tradable': usd_value >= 5.0,
                    'is_position': symbol in tracked_symbols
                })
            wallet_holdings.sort(key=lambda h: h['usd_value'], reverse=True)
        except Exception as e:
            logger.debug(f"Could not build wallet_holdings breakdown: {e}")

        active_ex = getattr(config, 'active_exchange', getattr(config, 'exchange_name', 'bybit')).upper()
        payload = {
            'symbol': meta.get('symbol', config.symbol),
            'timeframe': config.timeframe,
            'active_exchange': active_ex,
            'mode': 'PAPER TRADING' if config.paper_trading else f'LIVE {active_ex}',
            'paper_trading': config.paper_trading,
            'is_active': self.bot.telegram.is_active,
            'current_price': meta.get('price', 0.0),
            'rsi': meta.get('rsi', 0.0),
            'ema_fast': meta.get('ema_fast', 0.0),
            'ema_slow': meta.get('ema_slow', 0.0),
            'trend': meta.get('trend', 'UNKNOWN'),
            'usdt_balance': usdt_free,
            'usdt_total': usdt_total,
            'usdt_used': usdt_used,
            'real_usdt_balance': real_usdt,
            'initial_capital': config.initial_capital,
            'llm_enabled': config.use_llm_confirmation,
            'llm_provider': config.llm_provider.upper(),
            'llm_key_set': bool(config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")) and (config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")) != "your_deepseek_api_key_here",
            'llm_key_masked': f"{(config.deepseek_api_key or config.llm_api_key or os.getenv('DEEPSEEK_API_KEY', ''))[:6]}...{(config.deepseek_api_key or config.llm_api_key or os.getenv('DEEPSEEK_API_KEY', ''))[-4:]}" if (config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")) and len(config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")) > 8 and (config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")) != "your_deepseek_api_key_here" else None,
            'trading_mode': config.trading_mode,
            'trading_mode_display': config.trading_mode_display,
            'min_llm_confidence': config.min_llm_confidence,
            'prevent_sleep': getattr(config, 'prevent_sleep', True),
            'monitor_only': getattr(config, 'monitor_only', False),
            'market_regime': getattr(self.bot, 'latest_market_regime', {'mode': 'HUNT', 'avg_rsi': 50.0, 'is_overheated': False}),
            'active_positions': active_positions_payload,
            'active_position': active_pos_payload,
            'wallet_holdings': wallet_holdings,
            'max_concurrent_positions': getattr(self.bot, 'max_concurrent_positions', 3),
            'scan_logs': list(getattr(self.bot, 'scan_logs', [])),
            'ai_verdicts': list(getattr(self.bot, 'ai_verdicts', []))
        }
        return web.json_response(payload)

    async def handle_get_orders(self, request: web.Request) -> web.Response:
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        # Trade actions are the primary data — every BUY/SELL execution
        trade_actions = list(getattr(self.bot, 'trade_actions', []))
        # Also include AI verdicts for transparency
        verdicts = list(getattr(self.bot, 'ai_verdicts', []))
        # Fallback: legacy closed_orders from exchange adapter
        legacy_orders = getattr(self.bot.exchange, 'closed_orders', []) if self.bot.exchange else []

        # Combine: trade actions first, then verdicts, then legacy orders
        seen_ids = set()
        all_items = []
        for item in trade_actions + verdicts + legacy_orders:
            uid = f"{item.get('symbol','')}|{item.get('side','')}|{item.get('timestamp',0)}"
            if uid not in seen_ids:
                seen_ids.add(uid)
                all_items.append(item)

        all_items.sort(key=lambda x: x.get('timestamp', 0), reverse=True)

        return web.json_response({
            'trade_actions': trade_actions,
            'ai_verdicts': verdicts,
            'order_history': all_items
        })

    async def handle_get_history(self, request: web.Request) -> web.Response:
        """Returns clean chronological list of completed BUY/SELL trade actions."""
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        raw_actions = list(getattr(self.bot, 'trade_actions', []))
        trade_actions = [t for t in raw_actions if t.get('status') == 'FILLED']
        return web.json_response({
            'success': True,
            'total_trades': len(trade_actions),
            'history': trade_actions
        })

    async def handle_export_csv(self, request: web.Request) -> web.Response:
        """Exports trade actions history as a CSV file."""
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        raw_actions = list(getattr(self.bot, 'trade_actions', []))
        trade_actions = [t for t in raw_actions if t.get('status') == 'FILLED']
        csv_lines = ["Time,Symbol,Side,Amount,Price,EntryPrice,PnL_Pct,PnL_USDT,Status,Reason\n"]
        for t in trade_actions:
            line = f"{t.get('time','')},{t.get('symbol','')},{t.get('side','')},{t.get('amount',0)},{t.get('price',0)},{t.get('entry_price',0)},{t.get('pnl_pct',0)},{t.get('pnl_usdt',0)},{t.get('status','')},\"{t.get('reason','')}\"\n"
            csv_lines.append(line)

        csv_text = "".join(csv_lines)
        return web.Response(
            text=csv_text,
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="trade_history.csv"'}
        )

    async def handle_get_klines(self, request: web.Request) -> web.Response:
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        symbol = request.query.get('symbol', 'SHIB/USDT')
        if symbol == 'AUTO' or not symbol:
            symbol = 'SHIB/USDT'

        try:
            klines = self.bot.exchange.fetch_ohlcv(symbol, timeframe=config.timeframe, limit=50)
            formatted = []
            for k in klines:
                formatted.append({
                    'time': k[0],
                    'open': k[1],
                    'high': k[2],
                    'low': k[3],
                    'close': k[4],
                    'volume': k[5]
                })
            return web.json_response({'symbol': symbol, 'klines': formatted})
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return web.json_response({'symbol': symbol, 'klines': [], 'error': str(e)})

    async def handle_post_control(self, request: web.Request) -> web.Response:
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        try:
            body = await request.json()
            action = body.get('action')
            
            if action == 'pause':
                self.bot.telegram.is_active = False
                return web.json_response({'success': True, 'is_active': False})
            elif action == 'resume':
                self.bot.telegram.is_active = True
                return web.json_response({'success': True, 'is_active': True})
            elif action == 'toggle_prevent_sleep':
                config.prevent_sleep = not getattr(config, 'prevent_sleep', True)
                from main import set_prevent_sleep
                set_prevent_sleep(config.prevent_sleep)
                return web.json_response({'success': True, 'prevent_sleep': config.prevent_sleep})
            elif action == 'toggle_mode':
                config.paper_trading = not config.paper_trading
                from exchanges.exchange_factory import ExchangeFactory
                self.bot.exchange = ExchangeFactory.create_adapter()
                logger.info(f"🌐 Execution Mode Toggled via Dashboard: {'PAPER TRADING ($10)' if config.paper_trading else 'LIVE CEX REAL'}")
                return web.json_response({'success': True, 'paper_trading': config.paper_trading})
            elif action == 'close_position':
                target_symbol = body.get('symbol')
                positions = getattr(self.bot, 'active_positions', [])
                if target_symbol:
                    pos_to_close = None
                    for p in positions:
                        if p.get('symbol') == target_symbol:
                            pos_to_close = p
                            break
                    if pos_to_close:
                        logger.info(f"🔴 Manual close requested via Web Dashboard for {target_symbol}")
                        ok, message = await self.bot.close_position_market(
                            target_symbol, '🔴 Вручну через Dashboard'
                        )
                        if not ok:
                            return web.json_response({'success': False, 'error': message}, status=400)
                        return web.json_response({'success': True, 'message': message})
                    return web.json_response({'success': False, 'error': f'No active position found for {target_symbol}'}, status=400)
                else:
                    if positions:
                        logger.info(f"🔴 Manual close-all requested via Web Dashboard ({len(positions)} positions)")
                        sold, failed = [], []
                        for sym in [p.get('symbol', '') for p in list(positions)]:
                            ok, message = await self.bot.close_position_market(
                                sym, '🔴 Вручну через Dashboard (всі)'
                            )
                            (sold if ok else failed).append(sym if ok else message)
                        if failed and not sold:
                            return web.json_response({'success': False, 'error': '; '.join(failed)}, status=400)
                        msg = f'Sold {len(sold)} position(s)'
                        if failed:
                            msg += f'; {len(failed)} failed: ' + '; '.join(failed)
                        return web.json_response({'success': True, 'message': msg})
                    return web.json_response({'success': False, 'error': 'No active positions to close'}, status=400)
            elif action == 'toggle_monitor_only':
                config.monitor_only = not getattr(config, 'monitor_only', False)
                config.save_persisted_config()
                logger.info(
                    f"👁 Monitor-Only Mode {'ENABLED (no new entries)' if config.monitor_only else 'DISABLED (entries allowed)'} via Dashboard"
                )
                return web.json_response({'success': True, 'monitor_only': config.monitor_only})
            elif action == 'convert_dust':
                logger.info("♻️ Dust conversion requested via Web Dashboard")
                result = await asyncio.get_event_loop().run_in_executor(None, self.bot.convert_dust_to_usdt)
                if result.get('error'):
                    return web.json_response({'success': False, 'error': result['error']}, status=400)
                return web.json_response({
                    'success': True,
                    'converted': result['converted'],
                    'skipped': result['skipped'],
                })
            elif action == 'set_exchange':
                ex_name = body.get('exchange', 'bybit').lower()
                if ex_name in ('bybit', 'binance'):
                    config.active_exchange = ex_name
                    config.exchange_name = ex_name
                    from exchanges.exchange_factory import ExchangeFactory
                    self.bot.exchange = ExchangeFactory.create_adapter()
                    logger.info(f"🌐 Active Exchange Switched via Web Dashboard UI to: {ex_name.upper()}")
                    return web.json_response({'success': True, 'exchange': ex_name})
                return web.json_response({'success': False, 'error': 'Invalid exchange choice'}, status=400)
            elif action == 'set_execution_mode':
                mode = body.get('mode', 'paper')
                config.paper_trading = (mode.lower() == 'paper')
                from exchanges.exchange_factory import ExchangeFactory
                self.bot.exchange = ExchangeFactory.create_adapter()
                logger.info(f"🌐 Execution Mode Switched via Dashboard: {'PAPER TRADING' if config.paper_trading else 'LIVE CEX REAL'}")
                return web.json_response({'success': True, 'paper_trading': config.paper_trading})
            elif action == 'toggle_llm':
                config.use_llm_confirmation = not config.use_llm_confirmation
                logger.info(f"🌐 LLM Confirmation Filter Toggled via Dashboard: {'ENABLED' if config.use_llm_confirmation else 'DISABLED'}")
                return web.json_response({'success': True, 'llm_enabled': config.use_llm_confirmation})
            elif action == 'set_trading_mode':
                new_mode = body.get('mode', 'chill').lower()
                if new_mode in ('chill', 'hunt'):
                    config.trading_mode = new_mode
                    asyncio.create_task(self.bot.telegram.send_alert(
                        f"🌐 *Web Dashboard updated Trading Style Mode*:\n`{config.trading_mode_display}`"
                    ))
                    return web.json_response({'success': True, 'trading_mode': config.trading_mode, 'display': config.trading_mode_display})
                return web.json_response({'success': False, 'error': 'Invalid trading mode'}, status=400)
            elif action == 'toggle_trading_mode':
                config.trading_mode = 'hunt' if config.trading_mode == 'chill' else 'chill'
                asyncio.create_task(self.bot.telegram.send_alert(
                    f"🌐 *Web Dashboard toggled Trading Style Mode*:\n`{config.trading_mode_display}`"
                ))
                return web.json_response({'success': True, 'trading_mode': config.trading_mode, 'display': config.trading_mode_display})
            elif action == 'change_symbol':
                new_symbol = body.get('symbol', 'SOL/USDT')
                config.symbol = new_symbol
                return web.json_response({'success': True, 'symbol': new_symbol})
            elif action == 'set_llm_provider':
                new_provider = body.get('provider', 'gemini').lower()
                if new_provider in ('gemini', 'openai', 'deepseek'):
                    config.llm_provider = new_provider
                    asyncio.create_task(self.bot.telegram.send_alert(
                        f"🤖 *LLM Sentinel Provider Switched*: `{config.llm_provider.upper()}`"
                    ))
                    return web.json_response({'success': True, 'llm_provider': config.llm_provider.upper()})
                return web.json_response({'success': False, 'error': 'Invalid provider'}, status=400)
            elif action == 'set_llm_key':
                new_key = body.get('key', '').strip()
                if new_key:
                    config.deepseek_api_key = new_key
                    config.llm_api_key = new_key
                    config.use_llm_confirmation = True
                    config.save_persisted_config()
                    try:
                        import os
                        env_path = os.path.join(os.path.dirname(__file__), '.env')
                        lines = []
                        if os.path.exists(env_path):
                            with open(env_path, 'r', encoding='utf-8') as f:
                                lines = f.readlines()
                        
                        has_ds = False
                        has_llm = False
                        new_lines = []
                        for line in lines:
                            if line.startswith('DEEPSEEK_API_KEY='):
                                new_lines.append(f'DEEPSEEK_API_KEY={new_key}\n')
                                has_ds = True
                            elif line.startswith('LLM_API_KEY='):
                                new_lines.append(f'LLM_API_KEY={new_key}\n')
                                has_llm = True
                            else:
                                new_lines.append(line)
                        if not has_ds:
                            new_lines.append(f'DEEPSEEK_API_KEY={new_key}\n')
                        if not has_llm:
                            new_lines.append(f'LLM_API_KEY={new_key}\n')
                        
                        with open(env_path, 'w', encoding='utf-8') as f:
                            f.writelines(new_lines)
                    except Exception as env_err:
                        logger.error(f"Error saving key to .env: {env_err}")
                    masked = f"{new_key[:6]}...{new_key[-4:]}" if len(new_key) > 8 else "set"
                    return web.json_response({'success': True, 'message': 'API Key set successfully', 'key_masked': masked})
                return web.json_response({'success': False, 'error': 'Key cannot be empty'}, status=400)

            return web.json_response({'success': False, 'error': 'Unknown action'}, status=400)
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        
        ports_to_try = [5001, 5002, 5005, 8080, 8081, 8888, 5000]
        bound = False
        for port in ports_to_try:
            try:
                self.site = web.TCPSite(self.runner, '127.0.0.1', port)
                await self.site.start()
                config.dashboard_port = port
                logger.info(f"🌐 PRIVATE WEB DASHBOARD RUNNING AT: http://127.0.0.1:{port} (Логін: yuhim1308@gmail.com / Пароль: admin)")
                bound = True
                break
            except Exception as e:
                logger.warning(f"Could not bind Web Dashboard on port {port}: {e}. Trying alternative port...")

        if not bound:
            logger.error("❌ Failed to bind Web Dashboard on any fallback port.")
