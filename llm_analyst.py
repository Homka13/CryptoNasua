import logging
import json
import aiohttp
from typing import Dict, Any, Tuple
from config import config

logger = logging.getLogger(__name__)

class LLMAnalyst:
    """Uses LLM API (Gemini / OpenAI / OpenRouter) to evaluate candidate trading setups."""

    def __init__(self):
        self.enabled = config.use_llm_confirmation
        self.api_key = config.llm_api_key
        self.provider = config.llm_provider.lower()

    async def evaluate_trade_signal(self, symbol: str, timeframe: str, metadata: Dict[str, Any], initial_reason: str) -> Tuple[bool, str]:
        """
        Queries LLM API to confirm or reject a technical BUY signal.
        Returns: (is_confirmed: bool, llm_explanation: str)
        """
        if not self.enabled or not self.api_key:
            return True, "LLM filter disabled (Defaulting to technical signal)"

        prompt = f"""You are a senior quantitative crypto trader protecting a small $10 capital account.
A technical trading system triggered a BUY candidate signal for {symbol} on {timeframe} timeframe.

Technical Context:
- Signal Trigger: {initial_reason}
- Operating Mode: {config.trading_mode_display}
- Current Price: ${metadata.get('price', 0)}
- RSI (14): {metadata.get('rsi', 0):.2f}
- Fast EMA (20): ${metadata.get('ema_fast', 0)}
- Slow EMA (50): ${metadata.get('ema_slow', 0)}
- Trend Context: {metadata.get('trend', 'UNKNOWN')}

Task: Analyze if this entry carries high risk of a bull-trap or breakdown.
Respond strictly in valid JSON format:
{{
  "decision": "CONFIRM" or "REJECT",
  "confidence": 0.0 to 1.0,
  "reason": "Short 1-sentence explanation"
}}
"""

        try:
            if self.provider == "gemini":
                return await self._query_gemini(prompt)
            else:
                return await self._query_openai(prompt)
        except Exception as e:
            logger.error(f"LLM Analyst query failed: {e}. Falling back to technical signal.")
            return True, f"LLM query error fallback: {e}"

    async def _query_gemini(self, prompt: str) -> Tuple[bool, str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Gemini API returned status {response.status}: {text[:100]}")
                
                data = await response.json()
                content_text = data['candidates'][0]['content']['parts'][0]['text']
                return self._parse_json_verdict(content_text)

    async def _query_openai(self, prompt: str) -> Tuple[bool, str]:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"OpenAI API status {response.status}: {text[:100]}")

                data = await response.json()
                content_text = data['choices'][0]['message']['content']
                return self._parse_json_verdict(content_text)

    def _parse_json_verdict(self, raw_response: str) -> Tuple[bool, str]:
        cleaned = raw_response.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        parsed = json.loads(cleaned)
        decision = parsed.get("decision", "CONFIRM").upper()
        reason = parsed.get("reason", "No reason provided")
        confidence = float(parsed.get("confidence", 1.0))
        min_required = config.min_llm_confidence

        if decision == "CONFIRM" and confidence < min_required:
            is_confirmed = False
            explanation = (
                f"LLM Verdict: REJECTED by [{config.trading_mode_display}] "
                f"(Confidence {confidence*100:.0f}% < required {min_required*100:.0f}%) - {reason}"
            )
        else:
            is_confirmed = (decision == "CONFIRM")
            explanation = (
                f"LLM Verdict: {decision} [{config.trading_mode.upper()} mode] "
                f"(Confidence: {confidence*100:.0f}%, Min Req: {min_required*100:.0f}%) - {reason}"
            )
        
        logger.info(f"🧠 [LLM ANALYST]: {explanation}")
        return is_confirmed, explanation
