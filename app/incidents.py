"""Журнал инцидентов: попытки отправить документ с грифом. Только в памяти сервера.

В журнал попадает: время, категория грифа и канал (встроенный AI, «копировать и вставить»,
расширение). Текст документа, найденные данные, адрес и имя пользователя НЕ записываются.
"""
from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
from threading import Lock

MAX_INCIDENTS = 200  # старые вытесняются: журнал не растёт бесконечно
CHANNELS = {"chat", "mask", "extension"}


class IncidentLog:
    def __init__(self, maxlen: int = MAX_INCIDENTS):
        self._items: deque[dict] = deque(maxlen=maxlen)
        self._by_category: Counter = Counter()
        self._last_id = 0
        self._lock = Lock()

    def add(self, category: str, category_name: str, channel: str) -> dict:
        """Записать инцидент. channel: chat | mask | extension."""
        with self._lock:
            self._last_id += 1
            item = {
                "id": self._last_id,
                "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "severity": "critical",
                "category": category,
                "category_name": category_name,
                "channel": channel if channel in CHANNELS else "mask",
            }
            self._items.append(item)
            self._by_category[category_name] += 1
            return item

    def since(self, last_id: int = 0) -> list[dict]:
        """Инциденты новее last_id, самые свежие первыми."""
        with self._lock:
            return [dict(i) for i in reversed(self._items) if i["id"] > last_id]

    def summary(self) -> dict:
        with self._lock:
            return {"last_id": self._last_id, "total": sum(self._by_category.values()),
                    "by_category": dict(self._by_category)}

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._by_category.clear()
            self._last_id = 0
