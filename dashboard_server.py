import logging
import os
import json
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
        self.app.router.add_post('/api/auth/google', self.handle_google_auth)
        self.app.router.add_get('/api/status', self.handle_get_status)
        self.app.router.add_get('/api/orders', self.handle_get_orders)
        self.app.router.add_post('/api/control', self.handle_post_control)
        
        # Serve static assets
        static_dir = os.path.join(os.path.dirname(__file__), 'static')
        if os.path.exists(static_dir):
            self.app.router.add_static('/static/', path=static_dir, name='static')

    async def handle_index(self, request: web.Request) -> web.Response:
        static_index = os.path.join(os.path.dirname(__file__), 'static', 'index.html')
        if os.path.exists(static_index):
            with open(static_index, 'r', encoding='utf-8') as f:
                return web.Response(text=f.read(), content_type='text/html')
        return web.Response(text="<h1>Web Dashboard static/index.html missing</h1>", content_type='text/html')

    async def handle_google_auth(self, request: web.Request) -> web.Response:
        """Authenticates Google OAuth user email against ALLOWED_GOOGLE_EMAIL whitelist."""
        try:
            body = await request.json()
            email = body.get('email', '').strip().lower()
            
            allowed_email = config.allowed_google_email.strip().lower()

            if not allowed_email:
                # If no whitelist specified in env, allow login in dev mode & notify user
                session_token = f"session_{email}"
                self.authenticated_sessions.add(session_token)
                return web.json_response({
                    'success': True,
                    'token': session_token,
                    'email': email,
                    'message': 'Logged in (No whitelist enforced in .env)'
                })

            if email != allowed_email:
                logger.warning(f"🚨 BLOCKED UNAUTHORIZED GOOGLE LOGIN ATTEMPT: {email}")
                return web.json_response({
                    'success': False,
                    'error': f"Access Denied: Email '{email}' is not whitelisted."
                }, status=403)

            session_token = f"session_{email}"
            self.authenticated_sessions.add(session_token)
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
        if not config.allowed_google_email:
            return True  # Dev mode
        return token in self.authenticated_sessions

    async def handle_get_status(self, request: web.Request) -> web.Response:
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        meta = self.bot.latest_meta
        try:
            bal = self.bot.exchange.fetch_balance()
            usdt_free = bal.get('USDT', {}).get('free', 0.0)
            usdt_total = bal.get('USDT', {}).get('total', usdt_free)
            usdt_used = bal.get('USDT', {}).get('used', 0.0)
        except Exception as e:
            logger.error(f"Error fetching balance for dashboard: {e}")
            usdt_free = 0.0
            usdt_total = 0.0
            usdt_used = 0.0
        
        real_usdt = None
        try:
            real_bal = self.bot.exchange.fetch_real_balance()
            if real_bal and 'USDT' in real_bal:
                real_usdt = real_bal['USDT'].get('total', real_bal['USDT'].get('free', 0.0))
        except Exception:
            pass

        payload = {
            'symbol': config.symbol,
            'timeframe': config.timeframe,
            'mode': 'PAPER TRADING' if config.paper_trading else 'LIVE BYBIT',
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
            'deepseek_api_key_set': bool(config.deepseek_api_key or config.llm_api_key),
            'trading_mode': config.trading_mode,
            'trading_mode_display': config.trading_mode_display,
            'min_llm_confidence': config.min_llm_confidence,
            'active_position': self.bot.current_position
        }
        return web.json_response(payload)

    async def handle_get_orders(self, request: web.Request) -> web.Response:
        if not self._verify_session(request):
            return web.json_response({'error': 'Unauthorized'}, status=401)

        orders = self.bot.exchange.paper.trades_history if self.bot.exchange.paper else []
        return web.json_response(orders)

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
            elif action == 'toggle_mode':
                config.paper_trading = not config.paper_trading
                self.bot.exchange.is_paper = config.paper_trading
                return web.json_response({'success': True, 'paper_trading': config.paper_trading})
            elif action == 'set_execution_mode':
                mode = body.get('mode', 'paper')
                config.paper_trading = (mode.lower() == 'paper')
                self.bot.exchange.is_paper = config.paper_trading
                return web.json_response({'success': True, 'paper_trading': config.paper_trading})
            elif action == 'set_llm_key':
                key = body.get('key', '').strip()
                if key:
                    config.deepseek_api_key = key
                    config.llm_api_key = key
                    return web.json_response({'success': True, 'message': 'API Key set successfully'})
                return web.json_response({'success': False, 'error': 'Key cannot be empty'}, status=400)
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

            return web.json_response({'success': False, 'error': 'Unknown action'}, status=400)
        except Exception as e:
            return web.json_response({'success': False, 'error': str(e)}, status=400)

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        
        ports_to_try = [config.dashboard_port] + [5001, 5002, 5005, 8080, 8081, 8888]
        bound = False
        for port in ports_to_try:
            try:
                self.site = web.TCPSite(self.runner, '127.0.0.1', port)
                await self.site.start()
                config.dashboard_port = port
                logger.info(f"🌐 PRIVATE WEB DASHBOARD RUNNING AT: http://127.0.0.1:{port}")
                bound = True
                break
            except Exception as e:
                logger.warning(f"Could not bind Web Dashboard on port {port}: {e}. Trying alternative port...")

        if not bound:
            logger.error("❌ Failed to bind Web Dashboard on any fallback port.")
