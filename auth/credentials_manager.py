"""Credentials & API Keys Management Module."""

import os
import json
import logging
from typing import Dict, Any, Optional
from config import config

logger = logging.getLogger(__name__)

class CredentialsManager:
    """Manages credentials for Bybit, Binance, and LLM providers."""

    @staticmethod
    def get_bybit_credentials() -> Dict[str, str]:
        key = getattr(config, 'bybit_api_key', '').strip()
        secret = getattr(config, 'bybit_api_secret', '').strip()
        return {'api_key': key if "your_" not in key.lower() else "", 'api_secret': secret}

    @staticmethod
    def get_binance_credentials() -> Dict[str, str]:
        key = getattr(config, 'binance_api_key', '').strip()
        secret = getattr(config, 'binance_api_secret', '').strip()
        return {'api_key': key if "your_" not in key.lower() else "", 'api_secret': secret}

    @staticmethod
    def mask_key(key_str: str) -> str:
        if not key_str or len(key_str) < 8 or "your_" in key_str.lower():
            return "Not Set"
        return f"{key_str[:4]}...{key_str[-4:]}"
