"""Журнал аудита без хранения данных.

Руководитель по информационной безопасности (CISO) должен видеть статистику и доказательства инцидентов, не
читая переписку сотрудников. Поэтому в журнал попадают НЕ значения, а только:
  - HMAC-SHA-256 значения (с секретным ключом) вместо самого значения;
  - время (UTC), категория, канал и отдел.
Ни FİN, ни телефона, ни имени в журнале нет.

Почему HMAC, а не простой SHA-256: FİN (около 78 млрд вариантов), телефон (около 1 млрд) и имена подбираются
перебором за часы. Без секретного ключа администратора по журналу ничего подобрать нельзя.

Журнал неизменяемый в смысле обнаружения подмены: каждая запись содержит хеш предыдущей, поэтому правка, вставка
или удаление записи в середине ломают цепочку, и verify_chain() показывает, где. Это обнаружение, а не запрет
записи: тот, кто может удалить файл или обрезать его хвост, цепочкой не пойман (нужна внешняя копия хеша).

«Проверка значения»: администратор вводит значение (например, FİN) и узнаёт, встречалось ли оно и когда: система
считает HMAC введённого значения и ищет его в журнале. Это хеш-обязательство, а не формальное доказательство с
нулевым разглашением.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from app.detector import _fold_char

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "audit"
LOG_NAME = "audit.jsonl"
KEY_NAME = "audit.key"
GENESIS = "0" * 64
CATEGORIES = ("AD", "FIN", "ID", "VOEN", "IBAN", "KART", "TEL", "EMAIL", "ADRES", "TARIX")
SHORT = 16  # сколько символов хеша показывать в панели


def canonical(category: str, value: str) -> str:
    """Привести значение к единому виду: разные записи одного и того же дают один хеш."""
    text = "".join(_fold_char(c) for c in unicodedata.normalize("NFKC", value))
    if category in ("FIN", "ID", "IBAN"):
        return re.sub(r"[^A-Za-z0-9]", "", text).upper()
    if category in ("KART", "VOEN"):
        return re.sub(r"\D", "", text)
    if category == "TEL":
        digits = re.sub(r"\D", "", text)
        if len(digits) == 12 and digits.startswith("994"):
            digits = digits[3:]
        elif len(digits) == 10 and digits.startswith("0"):
            digits = digits[1:]
        return digits
    if category == "EMAIL":
        return text.strip().lower()
    # ad, ünvan, tarix: без регистра (İ/I/ı = i), одинарные пробелы
    text = text.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    return re.sub(r"[\W_]+", " ", text).strip()


def _line_hash(prev: str, core: dict) -> str:
    body = json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, directory: str | Path | None = None, key: bytes | None = None):
        self.dir = Path(directory or os.getenv("AUDIT_DIR") or DEFAULT_DIR)
        self._explicit_key = key
        self._key: bytes | None = None
        self.key_source = "env" if key else ""
        self.healthy = True
        self._lock = threading.Lock()
        self._tail: tuple[int, str] | None = None  # (последний номер, последний хеш)

    @property
    def path(self) -> Path:
        return self.dir / LOG_NAME

    # ---------- ключ ----------

    def _get_key(self) -> bytes:
        if self._key is not None:
            return self._key
        if self._explicit_key:
            self._key, self.key_source = self._explicit_key, "env"
        elif os.getenv("AUDIT_KEY"):
            self._key, self.key_source = os.environ["AUDIT_KEY"].encode("utf-8"), "env"
        else:
            self.dir.mkdir(parents=True, exist_ok=True)
            key_path = self.dir / KEY_NAME
            if not key_path.exists():
                key_path.write_text(secrets.token_hex(32), encoding="utf-8")
                try:
                    os.chmod(key_path, 0o600)
                except OSError:
                    pass
            self._key, self.key_source = bytes.fromhex(key_path.read_text(encoding="utf-8").strip()), "file"
        return self._key

    def digest(self, category: str, value: str) -> str:
        message = (category + "\x1f" + canonical(category, value)).encode("utf-8")
        return hmac.new(self._get_key(), message, hashlib.sha256).hexdigest()

    # ---------- запись ----------

    def _read_tail(self) -> tuple[int, str]:
        last = (0, GENESIS)
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    last = (int(record["seq"]), str(record["hash"]))
                except (ValueError, KeyError, TypeError):
                    continue  # повреждённую строку verify_chain() покажет отдельно
        return last

    def _append(self, events: list[dict]) -> int:
        if not events:
            return 0
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            if self._tail is None:
                self._tail = self._read_tail()
            seq, prev = self._tail
            lines = []
            for event in events:
                seq += 1
                core = {"seq": seq, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
                current = _line_hash(prev, core)
                lines.append(json.dumps({**core, "prev": prev, "hash": current}, ensure_ascii=False, separators=(",", ":")))
                prev = current
            with self.path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            self._tail = (seq, prev)
            return len(lines)

    def record_detected(self, items, channel: str, department: str) -> int:
        """Записать найденные значения как (категория, значение): в журнал уходит только HMAC каждого."""
        seen, events = set(), []
        for category, value in items:
            if (category, value) in seen:
                continue
            seen.add((category, value))
            events.append({"event": "detected", "category": category, "digest": self.digest(category, value),
                           "channel": channel, "dept": department})
        return self._append(events)

    def record_blocked(self, category_name: str, channel: str, department: str) -> int:
        """Блокировка по грифу: значений нет (текст не обрабатывался), только категория грифа."""
        return self._append([{"event": "blocked", "category": category_name, "digest": None,
                              "channel": channel, "dept": department}])

    # ---------- чтение ----------

    def _records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"seq": None, "corrupt": True})
        return out

    @staticmethod
    def _public(record: dict) -> dict:
        digest = record.get("digest")
        return {"seq": record.get("seq"), "ts": record.get("ts"), "event": record.get("event"),
                "category": record.get("category"), "channel": record.get("channel"), "dept": record.get("dept"),
                "digest": (digest[:SHORT] + "…") if digest else None}

    def recent(self, limit: int = 50) -> list[dict]:
        return [self._public(r) for r in reversed(self._records()[-limit:]) if not r.get("corrupt")]

    def total(self) -> int:
        return len(self._records())

    def verify_chain(self) -> dict:
        """Проверить цепочку: номера подряд, хеш предыдущей записи, хеш каждой записи."""
        prev, count = GENESIS, 0
        for index, record in enumerate(self._records(), start=1):
            try:
                core = {k: v for k, v in record.items() if k not in ("prev", "hash")}
                ok = (record["seq"] == index and record["prev"] == prev and record["hash"] == _line_hash(prev, core))
            except (KeyError, TypeError):
                ok = False
            if not ok:
                return {"ok": False, "events": count, "broken_at": index}
            prev, count = record["hash"], count + 1
        return {"ok": True, "events": count, "broken_at": None}

    def check_value(self, value: str, limit: int = 100) -> dict:
        """Встречалось ли значение? Считаем HMAC введённого значения для каждой категории и ищем в журнале."""
        wanted = {self.digest(cat, value): cat for cat in CATEGORIES if canonical(cat, value)}
        matches = []
        for record in self._records():
            if record.get("event") == "detected" and record.get("digest") in wanted:
                matches.append({"seq": record["seq"], "ts": record["ts"], "category": record["category"],
                                "channel": record.get("channel"), "dept": record.get("dept")})
        return {"found": bool(matches), "count": len(matches), "matches": matches[-limit:]}
