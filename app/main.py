"""FastAPI-сервер Süzgəc.

Приватность: тексты и таблицы меток нигде не сохраняются и не логируются.
Здесь нет print/logging с данными запроса; в ответах об ошибках текст пользователя не повторяется.
"""
from __future__ import annotations

import hmac
import logging
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import ml_detector
from app.detector import CLASSIFICATION_NAMES, classify, detect
from app.incidents import IncidentLog
from app.llm import LLMError, ai_enabled, ask_llm
from app.masker import mask, restore_tolerant
from app.ratelimit import RateLimiter

MAX_TEXT_LENGTH = 5000
MAX_BODY_BYTES = 64 * 1024  # 5000 символов даже с экранированием JSON укладываются с запасом
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
# Общий дневной потолок настоящих вызовов AI: публичная ссылка не должна сжечь баланс ключа API.
DAILY_LLM_LIMIT = int(os.getenv("DAILY_LLM_LIMIT", "300"))
RATE_LIMITED_PATHS = {"/api/check", "/api/mask", "/api/chat"}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

limiter = RateLimiter(RATE_LIMIT_PER_MINUTE, 60.0)
incidents = IncidentLog()  # журнал блокировок по грифу: время, категория, канал (без текста)
_log = logging.getLogger("suzgec")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ml_detector.start_background_load()  # модель грузится в фоне; пока она не готова, работают правила
    yield


app = FastAPI(title="Süzgəc", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Статистика — только счётчики по категориям, никаких текстов.
_stats_lock = Lock()
_category_counts: Counter = Counter()
_blocked_categories: Counter = Counter()  # сколько раз заблокирован каждый гриф
_source_counts: Counter = Counter()  # кто находил: rule / ai / rule+ai
_totals: Counter = Counter()
_daily = {"day": None, "count": 0}  # сколько раз сегодня (UTC) звали настоящий AI


def _reserve_llm_call() -> bool:
    """Занять одну из дневных попыток вызова AI."""
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


def _blocked_message(category: str) -> str:
    """Сообщение пользователю на азербайджанском. Без текста документа."""
    name = CLASSIFICATION_NAMES[category]
    return (f"«{name}» qrifi aşkarlandı. Sorğu tam bloklandı: mətn maskalanmadı və AI-yə göndərilmədi. "
            "İnzibatçıya bildiriş göndərildi.")


@app.post("/api/check")
def check(body: TextIn):
    category = classify(body.text)
    if category:
        # Текст с грифом дальше не обрабатываем: ни правила, ни модель его не видят.
        return {"blocked": True, "category": CLASSIFICATION_NAMES[category],
                "message": _blocked_message(category), "findings": []}
    return {
        "blocked": False,
        "findings": [
            {"category": f.category, "start": f.start, "end": f.end, "source": f.source} for f in detect(body.text)
        ],
    }


def _block_classified(text: str, channel: str, request: Request) -> JSONResponse | None:
    """Текст с грифом блокируется целиком: не маскируется, в AI не уходит.

    Записываем в журнал инцидентов время, категорию и канал (без текста) и отвечаем 403.
    """
    category = classify(text)
    if category is None:
        return None
    if request.headers.get("x-suzgec-client", "").lower() == "extension":
        channel = "extension"
    name = CLASSIFICATION_NAMES[category]
    with _stats_lock:
        _totals["requests"] += 1
        _totals["blocked"] += 1
        _blocked_categories[name] += 1
    incidents.add(category, name, channel)
    return JSONResponse(
        status_code=403,
        content={"detail": _blocked_message(category), "blocked": True, "category": name},
    )


@app.get("/api/config")
def config():
    """Что доступно на этом сервере. Страница прячет режим «Встроенный AI», если ключа нет."""
    return {"ai_available": ai_enabled(), "model": ml_detector.status()}


@app.post("/api/mask")
def mask_only(body: TextIn, request: Request):
    """Режим «копировать и вставить»: замаскировать текст, AI не вызывается.

    Значения персональных данных в ответ НЕ возвращаются: только замаскированный текст и позиции
    (spans). Таблицу «метка → значение» браузер собирает сам из текста, который пользователь
    уже держит у себя, и хранит только в памяти вкладки.
    """
    blocked = _block_classified(body.text, "mask", request)
    if blocked:
        return blocked
    masked = mask(body.text)
    with _stats_lock:
        _totals["requests"] += 1
        _category_counts.update(masked.counts)
        _source_counts.update(masked.source_counts)
    return {
        "masked_text": masked.masked_text,
        "spans": [
            {"label": s.label, "category": s.category, "start": s.start, "end": s.end, "source": s.source}
            for s in masked.spans
        ],
        "found": dict(masked.counts),
    }


@app.post("/api/chat")
def chat(body: TextIn, request: Request):
    blocked = _block_classified(body.text, "chat", request)
    if blocked:
        return blocked
    if not ai_enabled():
        raise HTTPException(
            status_code=503,
            detail="Встроенный AI не настроен (нет ключа API). Используйте режим «Копировать и вставить».",
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
        _source_counts.update(masked.source_counts)

    answer, info = restore_tolerant(raw_answer, masked.mapping)
    return {
        "masked_text": masked.masked_text,
        "answer": answer,
        "found": dict(masked.counts),
        "warnings": _restore_warnings(info, len(masked.mapping)),
    }


def _restore_warnings(info, total: int) -> list[str]:
    """Понятные предупреждения о том, как AI обошёлся с метками (без значений данных)."""
    warnings = []
    if info.fuzzy:
        warnings.append(f"AI изменил запись меток ({info.fuzzy} шт.): данные восстановлены по смыслу. Проверьте ответ.")
    if total and len(info.missing) == total:
        warnings.append("В ответе нет ни одной метки из вашего текста: AI убрал их или переписал ответ.")
    elif info.missing:
        warnings.append("В ответе не найдены метки: " + ", ".join(info.missing) + ". Возможно, AI удалил их или не упомянул.")
    return warnings


@app.get("/api/stats")
def stats():
    with _stats_lock:
        return {
            "categories": dict(_category_counts),
            "sources": dict(_source_counts),
            "blocked_categories": dict(_blocked_categories),
            **dict(_totals),
        }


# ---------- Панель администратора ----------
# Доступ: если задан ADMIN_TOKEN, нужен заголовок X-Admin-Token с этим значением; если не задан,
# панель открыта только с этого же компьютера (127.0.0.1). На публичном хостинге без токена она закрыта.

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def require_admin(request: Request) -> None:
    token = os.getenv("ADMIN_TOKEN", "")
    if token:
        given = request.headers.get("x-admin-token", "")
        if not hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Нужен токен администратора.")
    elif (request.client.host if request.client else "") not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Панель администратора доступна только с этого компьютера. Для удалённого доступа задайте ADMIN_TOKEN.",
        )


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/api/admin/incidents")
def admin_incidents(request: Request, since: int = 0):
    """Блокировки по грифу: время, категория, канал. Текста документов здесь нет и быть не может."""
    require_admin(request)
    return {"incidents": incidents.since(since), **incidents.summary()}
