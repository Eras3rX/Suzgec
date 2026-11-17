"""FastAPI-сервер Süzgəc.

Приватность: тексты и таблицы меток нигде не сохраняются и не логируются.
Здесь нет print/logging с данными запроса; в ответах об ошибках текст пользователя не повторяется.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.detector import detect, is_classified
from app.llm import LLMError, ask_llm, is_demo
from app.masker import mask, restore
from app.ratelimit import RateLimiter

MAX_TEXT_LENGTH = 5000
MAX_BODY_BYTES = 64 * 1024  # 5000 символов даже с экранированием JSON укладываются с запасом
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
# Общий дневной потолок настоящих вызовов AI: публичная ссылка не должна сжечь баланс ключа API.
DAILY_LLM_LIMIT = int(os.getenv("DAILY_LLM_LIMIT", "300"))
RATE_LIMITED_PATHS = {"/api/check", "/api/chat"}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

limiter = RateLimiter(RATE_LIMIT_PER_MINUTE, 60.0)
_log = logging.getLogger("suzgec")

app = FastAPI(title="Süzgəc")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Статистика — только счётчики по категориям, никаких текстов.
_stats_lock = Lock()
_category_counts: Counter = Counter()
_totals: Counter = Counter()
_daily = {"day": None, "count": 0}  # сколько раз сегодня (UTC) звали настоящий AI


def _reserve_llm_call() -> bool:
    """Занять одну из дневных попыток вызова AI. В демо-режиме (без ключа) лимит не нужен."""
    if is_demo():
        return True
    today = datetime.now(timezone.utc).date()
    with _stats_lock:
        if _daily["day"] != today:
            _daily["day"], _daily["count"] = today, 0
        if _daily["count"] >= DAILY_LLM_LIMIT:
            return False
        _daily["count"] += 1
        return True


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


def _error(status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail}, headers=headers)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Защита на входе: размер тела, частота запросов, безопасные ответы об ошибках."""
    path = request.url.path
    if request.method == "POST" and path in RATE_LIMITED_PATHS:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return _error(413, f"Запрос слишком большой. Текст — не более {MAX_TEXT_LENGTH} символов.")
        allowed, retry_after = limiter.allow(request.client.host if request.client else "unknown")
        if not allowed:
            return _error(
                429,
                f"Слишком много запросов. Подождите {retry_after} сек. и попробуйте снова.",
                {"Retry-After": str(retry_after)},
            )
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        # Логируем только тип ошибки: ни сообщение, ни трассировка не должны нести текст запроса.
        _log.error("unhandled error: %s", type(exc).__name__)
        return _error(500, "Внутренняя ошибка сервера. Попробуйте ещё раз.")
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Стандартный ответ FastAPI повторяет введённый текст — здесь этого нет намеренно.
    types = {e.get("type") for e in exc.errors()}
    if "string_too_long" in types:
        return _error(413, f"Текст слишком длинный: максимум {MAX_TEXT_LENGTH} символов. "
                           "Сократите его или отправьте по частям.")
    if "string_too_short" in types:
        return _error(422, "Введите текст: он не должен быть пустым.")
    return _error(422, 'Некорректный запрос: ожидается JSON вида {"text": "..."}.')


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/check")
def check(body: TextIn):
    return {
        "blocked": is_classified(body.text),
        "findings": [{"category": f.category, "start": f.start, "end": f.end} for f in detect(body.text)],
    }


@app.post("/api/chat")
def chat(body: TextIn):
    if is_classified(body.text):
        with _stats_lock:
            _totals["requests"] += 1
            _totals["blocked"] += 1
        raise HTTPException(
            status_code=403,
            detail="Текст с грифом «Məxfi» / «Gizli» заблокирован и не отправлен в ИИ.",
        )

    if not _reserve_llm_call():
        raise HTTPException(status_code=429, detail="Дневной лимит демо исчерпан. Попробуйте завтра.")

    masked = mask(body.text)  # таблица меток живёт только внутри этого вызова
    try:
        raw_answer = ask_llm(masked.masked_text)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    with _stats_lock:
        _totals["requests"] += 1
        _category_counts.update(masked.counts)

    return {
        "masked_text": masked.masked_text,
        "answer": restore(raw_answer, masked.mapping),
        "found": dict(masked.counts),
        "demo": is_demo(),
    }


@app.get("/api/stats")
def stats():
    with _stats_lock:
        return {"categories": dict(_category_counts), **dict(_totals)}
