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
    """Evaluates buy signals using DeepSeek V4/V3, Gemini 1.5, or OpenAI GPT-4o-mini."""
    
    def __init__(self):
        self.api_key = config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.provider = config.llm_provider
        self.api_url = "https://api.deepseek.com/v1/chat/completions" if self.provider == "deepseek" else "https://api.openai.com/v1/chat/completions"

    async def evaluate_trade_signal(self, symbol: str, timeframe: str, meta: Dict[str, Any], strategy_reason: str) -> Tuple[bool, str]:
        """
        Sends technical indicators to LLM for final trade confirmation.
        Returns: (is_confirmed: bool, llm_reason: str)
        """
        if not config.use_llm_confirmation:
            return True, "LLM Confirmation disabled in config"

        key = config.deepseek_api_key or config.llm_api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not key or key == "your_deepseek_api_key_here":
            return True, "No LLM API key configured (Bypassed filter)"

        prompt = f"""You are an expert quantitative crypto trader & risk manager.
Analyze the following technical setup for pair: {symbol} on {timeframe} timeframe:
- Current Price: ${meta.get('price', 0):.4f}
- RSI (14): {meta.get('rsi', 0):.1f}
- EMA 20: ${meta.get('ema_fast', 0):.4f}
- EMA 50: ${meta.get('ema_slow', 0):.4f}
- Market Trend: {meta.get('trend', 'UNKNOWN')}
- Strategy Signal Reason: {strategy_reason}
- Trading Mode: {config.trading_mode_display} (Min Confidence Required: {config.min_llm_confidence}%)

Rules:
1. Reject if price is in a strong downtrend unless RSI is severely oversold (< 25) with clear reversal.
2. Confirm if RSI oversold in a bullish trend or a strong breakout pattern is forming.
3. Respond ONLY in valid JSON with format:
{{"verdict": "CONFIRM" or "REJECT", "confidence": 0-100, "reason": "Short 1-sentence Ukrainian explanation"}}"""

        try:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            body = {
                "model": "deepseek-chat" if config.llm_provider == "deepseek" else "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are a disciplined crypto risk sentinel. Respond strictly in valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2
            }

            loop = asyncio.get_event_loop()
            req = urllib.request.Request(self.api_url, data=json.dumps(body).encode('utf-8'), headers=headers)
            
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
            if verdict == "CONFIRM" and confidence >= min_req:
                logger.info(f"✅ LLM Verdict: CONFIRM [{config.trading_mode_display}] ({confidence}%) - {reason}")
                return True, f"LLM Verdict: CONFIRM [{config.trading_mode_display}] (Confidence: {confidence:.0f}%, Min Req: {min_req}%) - {reason}"
            else:
                logger.warning(f"🛑 LLM Verdict: REJECT [{config.trading_mode_display}] ({confidence}%) - {reason}")
                return False, f"LLM Verdict: REJECT [{config.trading_mode_display}] (Confidence: {confidence:.0f}%, Min Req: {min_req}%) - {reason}"

        except Exception as e:
            logger.error(f"Error evaluating trade signal via LLM Analyst: {e}")
            return True, f"LLM Analyst API Error (Fallback to Quant Signal): {e}"
