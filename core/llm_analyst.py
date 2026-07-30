"""AI Trade Evaluator (LLM Analyst Filter Module - DeepSeek / Gemini / OpenAI)."""

import os
import json
import logging
import asyncio
import urllib.request
from typing import Dict, Any, Tuple
from config import config

logger = logging.getLogger(__name__)

class LLMAnalyst:
    """Evaluates buy signals using DeepSeek V4/V3, Gemini 1.5, OpenRouter, or OpenAI GPT-4o-mini."""
    
    def __init__(self):
        pass

    async def evaluate_trade_signal(self, symbol: str, timeframe: str, meta: Dict[str, Any], strategy_reason: str) -> Tuple[bool, str]:
        """
        Sends technical indicators to LLM for final trade confirmation.
        Returns: (is_confirmed: bool, llm_reason: str)
        """
        if not config.use_llm_confirmation:
            return True, "LLM Confirmation disabled in config"

        if meta.get('skip_llm', False):
            return True, "⚡ FAST MATH EXECUTION (LLM bypassed for 0ms breakout speed)"

        key = (config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("LLM_API_KEY", "")).strip()
        if not key or key == "your_deepseek_api_key_here":
            return True, "No LLM API key configured (Bypassed filter)"

        provider = getattr(config, 'llm_provider', 'deepseek').lower()
        
        # Determine API Endpoint and Model dynamically
        if key.startswith("sk-or-v1-"):
            api_url = "https://openrouter.ai/api/v1/chat/completions"
            model = "deepseek/deepseek-chat"
        elif provider == "openai" or (key.startswith("sk-proj-") or key.startswith("sk-admin-")):
            api_url = "https://api.openai.com/v1/chat/completions"
            model = "gpt-4o-mini"
        else:
            api_url = "https://api.deepseek.com/chat/completions"
            model = getattr(config, 'deepseek_model', 'deepseek-chat')

        prompt = f"""You are an expert quantitative crypto trader & risk manager.
Analyze the following technical setup for pair: {symbol} on {timeframe} timeframe:
- Current Price: ${meta.get('price', 0):.4f}
- RSI (14): {meta.get('rsi', 0):.1f}
- EMA 20: ${meta.get('ema_fast', 0):.4f}
- EMA 50: ${meta.get('ema_slow', 0):.4f}
- Lower Bollinger Band (20,2): ${meta.get('bb_lower', 0):.4f}
- Market Trend: {meta.get('trend', 'UNKNOWN')}
- Recent 5 Candle Closes: {meta.get('last_5_closes', [])}
- Micro-Trend 5-Candle Slope: {meta.get('slope_5_candles_pct', 0.0):+.2f}%
- 1-Hour Trend Price Change: {meta.get('change_1h_pct', 0.0):+.2f}%
- EMA 20 Slope (1 Hour): {meta.get('ema20_slope_pct', 0.0):+.2f}%
- Last 3 Candles Consecutive RED (Falling): {meta.get('last_3_red', False)}
- Strategy Signal Reason: {strategy_reason}
- Trading Mode: {config.trading_mode_display} (Min Confidence Required: {config.min_llm_confidence}%)

Strict Rules:
1. REJECT if the micro-trend or 1-hour trend is falling (change_1h_pct < -0.20%, EMA20 slope negative, or last 3 candles RED) without a clear green reversal candle. Do NOT buy coins that are slumping or creeping down!
2. Confirm ONLY if price is rebounding off a strong support level with a green candle or forming a strong bullish breakout pattern.
3. Respond ONLY in valid JSON with format:
{{"verdict": "CONFIRM" or "REJECT", "confidence": 0-100, "reason": "Short 1-sentence Ukrainian explanation"}}"""

        # Try primary endpoint, with fallback to OpenAI / OpenRouter if 401 occurs
        endpoints_to_try = [
            (api_url, model),
            ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
            ("https://openrouter.ai/api/v1/chat/completions", "deepseek/deepseek-chat")
        ]
        # Remove duplicates while preserving order
        unique_endpoints = []
        for ep in endpoints_to_try:
            if ep not in unique_endpoints:
                unique_endpoints.append(ep)

        last_error = None
        for current_url, current_model in unique_endpoints:
            try:
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                }
                if "openrouter.ai" in current_url:
                    headers["HTTP-Referer"] = "https://crypto-trading-bot.local"
                    headers["X-Title"] = "Crypto Trading Bot"

                body = {
                    "model": current_model,
                    "messages": [
                        {"role": "system", "content": "You are a disciplined crypto risk sentinel. Respond strictly in valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2
                }

                loop = asyncio.get_event_loop()
                req = urllib.request.Request(current_url, data=json.dumps(body).encode('utf-8'), headers=headers)
                
                def _fetch():
                    with urllib.request.urlopen(req, timeout=12) as response:
                        return json.loads(response.read().decode('utf-8'))

                res = await loop.run_in_executor(None, _fetch)
                content = res['choices'][0]['message']['content'].strip()
                
                # Clean markdown codeblocks if present
                if content.startswith("```json"):
                    content = content[7:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

                data = json.loads(content)
                verdict = data.get("verdict", "REJECT").upper()
                confidence = float(data.get("confidence", 0))
                reason = data.get("reason", "No reason provided")

                min_req = config.min_llm_confidence
                provider_label = "DeepSeek" if "deepseek" in current_url else ("OpenAI" if "openai" in current_url else "OpenRouter")
                if verdict == "CONFIRM" and confidence >= min_req:
                    logger.info(f"✅ LLM Verdict ({provider_label}): CONFIRM [{config.trading_mode_display}] ({confidence}%) - {reason}")
                    return True, f"LLM Verdict ({provider_label}): CONFIRM [{config.trading_mode_display}] (Confidence: {confidence:.0f}%, Min Req: {min_req}%) - {reason}"
                else:
                    logger.warning(f"🛑 LLM Verdict ({provider_label}): REJECT [{config.trading_mode_display}] ({confidence}%) - {reason}")
                    return False, f"LLM Verdict ({provider_label}): REJECT [{config.trading_mode_display}] (Confidence: {confidence:.0f}%, Min Req: {min_req}%) - {reason}"

            except Exception as e:
                last_error = e
                logger.warning(f"Endpoint {current_url} failed: {e}. Trying fallback if available...")

        logger.error(f"Error evaluating trade signal via LLM Analyst: {last_error}")
        return True, f"LLM Analyst API Key Invalid ({last_error}). Please check your API key in Web Dashboard."

    async def evaluate_active_position_health(self, symbol: str, timeframe: str, meta: Dict[str, Any], age_minutes: float, pnl_pct: float) -> Tuple[bool, str]:
        """
        Active Position Guardian: Called when position age >= 5.0 minutes.
        Asks DeepSeek LLM whether to SELL to bank micro-profit/exit stagnation or KEEP HOLDING.
        Returns (should_close: bool, reason: str)
        """
        if not config.use_llm_confirmation:
            return False, "LLM active monitoring disabled"

        key = (config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("LLM_API_KEY", "")).strip()
        if not key or key == "your_deepseek_api_key_here":
            return False, "No LLM API key"

        provider = getattr(config, 'llm_provider', 'deepseek').lower()
        if key.startswith("sk-or-v1-"):
            api_url = "https://openrouter.ai/api/v1/chat/completions"
            model = "deepseek/deepseek-chat"
        elif provider == "openai" or (key.startswith("sk-proj-") or key.startswith("sk-admin-")):
            api_url = "https://api.openai.com/v1/chat/completions"
            model = "gpt-4o-mini"
        else:
            api_url = "https://api.deepseek.com/chat/completions"
            model = getattr(config, 'deepseek_model', 'deepseek-chat')

        prompt = f"""You are an active crypto position guardian.
The position for {symbol} has been open for {age_minutes:.1f} minutes with current PnL: {pnl_pct:+.2f}%.
Technical Indicators:
- Current Price: ${meta.get('price', 0):.4f}
- RSI (14): {meta.get('rsi', 0):.1f}
- EMA 20: ${meta.get('ema_fast', 0):.4f}
- EMA 50: ${meta.get('ema_slow', 0):.4f}
- Market Trend: {meta.get('trend', 'UNKNOWN')}

Rules:
1. On 15m candles, position needs up to 15 minutes (1 full 15m candle) to complete its movement. If age is 5-14 minutes and 15m candle structure is building bullishly, recommend HOLD.
2. If PnL is positive (>= +0.20%, covering Bybit fees) and RSI is weakening or price is consolidating after 15 minutes, recommend SELL to lock in net profit.
3. If position is open for 15+ minutes and price fails to grow above +0.20% (PnL < +0.20%), recommend SELL to free up capital immediately for hot momentum coins.
4. Respond ONLY in valid JSON with format:
{{"action": "SELL" or "HOLD", "reason": "Short 1-sentence Ukrainian explanation"}}"""

        try:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            if "openrouter.ai" in api_url:
                headers["HTTP-Referer"] = "https://crypto-trading-bot.local"
                headers["X-Title"] = "Crypto Trading Bot"

            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are an active crypto risk sentinel. Respond strictly in valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2
            }

            loop = asyncio.get_event_loop()
            req = urllib.request.Request(api_url, data=json.dumps(body).encode('utf-8'), headers=headers)
            
            def _fetch():
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read().decode('utf-8'))

            res = await loop.run_in_executor(None, _fetch)
            content = res['choices'][0]['message']['content'].strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            data = json.loads(content)
            action = data.get("action", "HOLD").upper()
            reason = data.get("reason", "DeepSeek active position monitoring")

            if action == "SELL":
                logger.info(f"🤖 DeepSeek Active Sentinel recommended SELL for {symbol}: {reason}")
                return True, f"🤖 DeepSeek Sentinel (5m Health Check): {reason}"
            return False, reason
        except Exception as e:
            logger.debug(f"DeepSeek Active Position Sentinel check error: {e}")
            return False, str(e)
