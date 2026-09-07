import logging
import json
import re
import random
import asyncio
from typing import Dict, Any, Tuple, Optional, Literal, List

import aiohttp
from pydantic import BaseModel, Field, ValidationError

from config import config

logger = logging.getLogger(__name__)


class TradeVerdict(BaseModel):
    """Strictly-typed LLM verdict parsed from a candidate trading setup."""

    decision: Literal["CONFIRM", "REJECT"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=3)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json_object(raw: str) -> Optional[str]:
    """Locate the first JSON object in raw text, including markdown code fences.

    Uses progressively more forgiving strategies:
      1. strip ```json / ``` fences
      2. locate a substring bounded by the first '{' and last '}'
      3. regex ``{...}`` fallback
    Returns the isolated JSON string or None.
    """
    if not raw:
        return None
    cleaned = raw.strip()

    # Handle markdown code fences (```json ... ``` or ``` ... ```).
    for fence in ("```json", "```JSON", "```"):
        if fence in cleaned:
            parts = cleaned.split(fence, 1)
            if len(parts) == 2:
                inner = parts[1].split("```", 1)[0]
                cleaned = inner.strip()
                break

    try:
        json.loads(cleaned)
        return cleaned
    except (ValueError, json.JSONDecodeError):
        pass

    # Bracket-walk: first '{' to last '}'.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except (ValueError, json.JSONDecodeError):
            pass

    m = _JSON_OBJECT_RE.search(cleaned)
    if m:
        return m.group(0)
    return None


def parse_verdict_from_text(raw: str) -> TradeVerdict:
    """Parse and validate a raw LLM response into a :class:`TradeVerdict`.

    Raises ``ValueError`` if no valid JSON object can be found/validated.
    """
    candidate = extract_json_object(raw)
    if candidate is None:
        raise ValueError("No JSON object found in LLM response")

    try:
        return TradeVerdict.model_validate_json(candidate)
    except (ValidationError, ValueError) as e:
        raise ValueError(f"Invalid TradeVerdict JSON: {e}") from e


class LLMAnalyst:
    """Uses an LLM API (Kimi/Moonshot, Gemini, OpenAI, DeepSeek) to evaluate trading setups."""

    def __init__(self):
        self.enabled = config.use_llm_confirmation

    async def evaluate_trade_signal(
        self,
        symbol: str,
        timeframe: str,
        metadata: Dict[str, Any],
        initial_reason: str,
    ) -> Tuple[bool, str]:
        """Query the LLM to confirm/reject a technical BUY signal.

        Returns ``(is_confirmed, llm_explanation)``.
        """
        provider = config.llm_provider.lower()
        api_key = self._api_key_for_provider(provider)

        if not self.enabled or not api_key:
            return True, "LLM filter disabled or API key missing (Defaulting to technical signal)"

        prompt = self._build_prompt(symbol, timeframe, metadata, initial_reason)

        try:
            if provider in ("kimi", "moonshot"):
                verdict = await self._query_kimi(prompt, api_key)
            elif provider == "gemini":
                verdict = await self._query_gemini(prompt, api_key)
            elif provider == "deepseek":
                verdict = await self._query_deepseek(prompt, api_key)
            else:
                verdict = await self._query_openai(prompt, api_key)
        except Exception as e:
            logger.error(f"LLM Analyst ({provider.upper()}) query failed: {e}. Falling back to technical signal.")
            return True, f"LLM query error fallback: {e}"

        return self._verdict_to_result(verdict)

    def _api_key_for_provider(self, provider: str) -> str:
        if provider in ("kimi", "moonshot"):
            return config.moonshot_api_key.strip()
        if provider == "deepseek":
            return (config.deepseek_api_key or config.llm_api_key).strip()
        return (config.llm_api_key or config.deepseek_api_key).strip()

    def _build_prompt(
        self,
        symbol: str,
        timeframe: str,
        metadata: Dict[str, Any],
        initial_reason: str,
    ) -> str:
        return f"""You are a senior quantitative crypto trader protecting a small $10 capital account.
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

    def _verdict_to_result(self, verdict: TradeVerdict) -> Tuple[bool, str]:
        min_required = config.min_llm_confidence
        is_confirmed = verdict.decision == "CONFIRM" and verdict.confidence >= min_required

        explanation = (
            f"LLM Verdict: {verdict.decision} [{config.trading_mode.upper()} mode] "
            f"(Confidence: {verdict.confidence*100:.0f}%, Min Req: {min_required*100:.0f}%) - {verdict.reason}"
        )
        logger.info(f"🧠 [LLM ANALYST]: {explanation}")
        return is_confirmed, explanation

    async def _query_kimi(self, prompt: str, api_key: str) -> TradeVerdict:
        """Moonshot/Kimi OpenAI-compatible chat.completions endpoint."""
        url = f"{config.moonshot_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.moonshot_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        content = await self._post_json_with_retry(url, headers, payload)
        return parse_verdict_from_text(content)

    async def _query_gemini(self, prompt: str, api_key: str) -> TradeVerdict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Gemini API returned status {response.status}: {text[:100]}")
                data = await response.json()
                content_text = data['candidates'][0]['content']['parts'][0]['text']
                return parse_verdict_from_text(content_text)

    async def _query_deepseek(self, prompt: str, api_key: str) -> TradeVerdict:
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.deepseek_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        content = await self._post_json_with_retry(url, headers, payload, total=10)
        return parse_verdict_from_text(content)

    async def _query_openai(self, prompt: str, api_key: str) -> TradeVerdict:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        content = await self._post_json_with_retry(url, headers, payload)
        return parse_verdict_from_text(content)

    async def _post_json_with_retry(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        total: float = 10,
        retries: int = 2,
    ) -> str:
        """POST with exponential backoff + jitter on timeouts and HTTP 429/5xx."""
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, headers=headers, json=payload,
                        timeout=aiohttp.ClientTimeout(total=total),
                    ) as response:
                        if response.status in (429, 500, 502, 503, 504):
                            raise Exception(f"API status {response.status}")
                        if response.status != 200:
                            text = await response.text()
                            raise Exception(f"API status {response.status}: {text[:100]}")
                        data = await response.json()
                        return data['choices'][0]['message']['content']
            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
                last_exc = e
                if attempt >= retries:
                    break
                backoff = (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(f"LLM HTTP attempt {attempt + 1} failed ({e}). Retrying in {backoff:.2f}s...")
                await asyncio.sleep(backoff)

        raise last_exc if last_exc else Exception("LLM HTTP request failed")