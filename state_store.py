import json
import logging
import asyncio
import os
from collections import deque
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)

POSITION_STATE_FILE = os.getenv("POSITION_STATE_FILE", "position_state.json")


class BotStateStore:
    """Async-safe mutable bot state backed by an ``asyncio.Lock``.

    Holds the live trading state (active position, latest metadata, scan logs and
    AI verdicts) so the orchestrator no longer relies on ad-hoc in-memory attrs.
    Optionally persists the open position to ``position_state.json`` to survive
    bot restarts.
    """

    def __init__(self, persist_position: bool = True) -> None:
        self._lock = asyncio.Lock()
        self.persist_position = persist_position
        self.current_position: Optional[Dict[str, Any]] = None
        self.latest_meta: Dict[str, Any] = {}
        self.scan_logs: deque = deque(maxlen=50)
        self.ai_verdicts: deque = deque(maxlen=50)
        self.is_running: bool = False

        if self.persist_position:
            self._load_position()

    def _load_position(self) -> None:
        try:
            if os.path.exists(POSITION_STATE_FILE):
                with open(POSITION_STATE_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get('symbol'):
                        self.current_position = data
                        logger.info(f"🔁 Restored open position from {POSITION_STATE_FILE}: {data['symbol']}")
        except Exception as e:
            logger.error(f"Error loading position state: {e}")

    def _save_position(self) -> None:
        if not self.persist_position:
            return
        try:
            if self.current_position:
                with open(POSITION_STATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.current_position, f, ensure_ascii=False, indent=2)
            elif os.path.exists(POSITION_STATE_FILE):
                os.remove(POSITION_STATE_FILE)
        except Exception as e:
            logger.error(f"Error saving position state: {e}")

    async def get_status_snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return {
                'current_position': dict(self.current_position) if self.current_position else None,
                'latest_meta': dict(self.latest_meta),
                'scan_logs': list(self.scan_logs),
                'ai_verdicts': list(self.ai_verdicts),
                'is_running': self.is_running,
            }

    async def record_scan(self, entry: Dict[str, Any]) -> None:
        async with self._lock:
            self.scan_logs.appendleft(entry)

    async def record_verdict(self, entry: Dict[str, Any]) -> None:
        async with self._lock:
            self.ai_verdicts.appendleft(entry)

    async def update_meta(self, meta: Dict[str, Any]) -> None:
        async with self._lock:
            self.latest_meta = dict(meta)

    async def update_position(self, position: Optional[Dict[str, Any]]) -> None:
        async with self._lock:
            self.current_position = dict(position) if position else None
            self._save_position()

    async def clear_position(self) -> None:
        await self.update_position(None)

    async def set_running(self, running: bool) -> None:
        async with self._lock:
            self.is_running = running