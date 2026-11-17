"""FastAPI-сервер SiftGuard.

Приватность: тексты и таблицы меток нигде не сохраняются и не логируются.
Здесь нет print/logging с данными запроса; в ответах об ошибках текст пользователя не повторяется.
"""
from __future__ import annotations

import base64
import hmac
from urllib.parse import unquote
import logging
import mimetypes
import os
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore, Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import evaluation, ml_detector, redact
from app.audit import AuditLog
from app.detector import CLASSIFICATION_NAMES, classify, detect
from app.incidents import IncidentLog
from app.messages import lang_of, tr
from app.llm import LLMError, ai_enabled, ask_llm, ask_llm_document, vision_enabled
from app.masker import mask, restore_tolerant
from app.ratelimit import RateLimiter
from app.vault import TemporaryVault

MAX_TEXT_LENGTH = 5000
MAX_BODY_BYTES = 64 * 1024  # 5000 символов даже с экранированием JSON укладываются с запасом
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
# Общий дневной потолок настоящих вызовов AI: публичная ссылка не должна сжечь баланс ключа API.
DAILY_LLM_LIMIT = int(os.getenv("DAILY_LLM_LIMIT", "300"))
RATE_LIMITED_PATHS = {"/api/check", "/api/mask", "/api/chat", "/api/redact", "/api/ask-document"}
# Загрузка документов: тело запроса — сам файл (не multipart), поэтому он целиком живёт в памяти запроса
# и не попадает во временные файлы на диске.
UPLOAD_PATHS = {"/api/redact", "/api/ask-document"}
_redact_slots = BoundedSemaphore(int(os.getenv("REDACT_CONCURRENCY", "2")))  # OCR тяжёлый: не больше N сразу
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

limiter = RateLimiter(RATE_LIMIT_PER_MINUTE, 60.0)
incidents = IncidentLog()  # журнал блокировок по грифу: время, категория, канал (без текста)
audit = AuditLog()  # журнал аудита на диске: только HMAC-хеши и время, без значений
_log = logging.getLogger("siftguard")


def _audit_detected(masked, channel: str, request: Request, department: str | None) -> None:
    """Записать в журнал аудита хеши найденных значений. Сбой журнала не должен ломать работу пользователя."""
    if request.headers.get("x-siftguard-client", "").lower() == "extension":
        channel = "extension"
    # категория берётся из метки: "[FIN_1]" -> "FIN"
    items = [(label[1:-1].rsplit("_", 1)[0], value) for label, value in masked.mapping.items()]
    try:
        audit.record_detected(items, channel, _department(department))
        audit.healthy = True
    except Exception as exc:  # noqa: BLE001
        audit.healthy = False
        _log.error("audit write failed: %s", type(exc).__name__)  # только тип ошибки, без данных


def _audit_blocked(category_name: str, channel: str, department: str) -> None:
    try:
        audit.record_blocked(category_name, channel, department)
        audit.healthy = True
    except Exception as exc:  # noqa: BLE001
        audit.healthy = False
        _log.error("audit write failed: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ml_detector.start_background_load()  # модель грузится в фоне; пока она не готова, работают правила
    redact.warm_up()                     # то же для распознавания текста на картинках
    yield


mimetypes.add_type("font/woff2", ".woff2")  # на Windows этого типа может не быть в реестре

app = FastAPI(title="SiftGuard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Статистика — только счётчики по категориям, никаких текстов.
_stats_lock = Lock()
_category_counts: Counter = Counter()
_blocked_categories: Counter = Counter()  # сколько раз заблокирован каждый гриф
_source_counts: Counter = Counter()  # кто находил: rule / ai / rule+ai
_totals: Counter = Counter()
_daily = {"day": None, "count": 0}  # сколько раз сегодня (UTC) звали настоящий AI

# Отделы: только счётчики (запросы, блокировки, защищённые данные по категориям). Ни текстов, ни имён пользователей.
DEPARTMENTS = {"maliyye": "Maliyyə", "hr": "HR", "huquq": "Hüquq"}
_DEPT_ALIASES = {"maliyyə": "maliyye", "maliyye": "maliyye", "hr": "hr", "hüquq": "huquq", "huquq": "huquq"}
NO_DEPARTMENT = "none"
NO_DEPARTMENT_NAME = "Seçilməyib"
_dept_counts: dict[str, Counter] = {d: Counter() for d in [*DEPARTMENTS, NO_DEPARTMENT]}
_dept_categories: dict[str, Counter] = {d: Counter() for d in [*DEPARTMENTS, NO_DEPARTMENT]}


def _department(value: str | None) -> str:
    """Отдел из запроса: известный (по id или названию) либо «не выбран». Мусор не принимаем."""
    return _DEPT_ALIASES.get((value or "").strip().lower(), NO_DEPARTMENT)


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
    department: str | None = None  # отдел, выбранный пользователем (только для статистики; неизвестное = «не выбран»)


def _lang(request: Request) -> str:
    """Язык интерфейса пользователя из заголовка X-Lang (az по умолчанию)."""
    return lang_of(request.headers.get("x-lang"))


def _error(status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail}, headers=headers)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Защита на входе: размер тела, частота запросов, безопасные ответы об ошибках."""
    path = request.url.path
    lang = _lang(request)
    if request.method == "POST" and path in RATE_LIMITED_PATHS:
        length = request.headers.get("content-length")
        if path in UPLOAD_PATHS:
            if length and length.isdigit() and int(length) > redact.MAX_UPLOAD_BYTES:
                return _error(413, tr("file_too_big", lang))
        elif length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return _error(413, tr("body_too_big", lang, limit=MAX_TEXT_LENGTH))
        allowed, retry_after = limiter.allow(request.client.host if request.client else "unknown")
        if not allowed:
            return _error(
                429,
                tr("rate_limited", lang, seconds=retry_after),
                {"Retry-After": str(retry_after)},
            )
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        # Логируем только тип ошибки: ни сообщение, ни трассировка не должны нести текст запроса.
        _log.error("unhandled error: %s", type(exc).__name__)
        return _error(500, tr("internal_error", lang))
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/") or path in ("/", "/admin", "/haqqinda", "/sened"):
        response.headers["Cache-Control"] = "no-cache"  # браузер перепроверяет файл (ETag), а не показывает устаревший вид
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Стандартный ответ FastAPI повторяет введённый текст — здесь этого нет намеренно.
    types = {e.get("type") for e in exc.errors()}
    lang = _lang(request)
    if "string_too_long" in types:
        return _error(413, tr("text_too_long", lang, limit=MAX_TEXT_LENGTH))
    if "string_too_short" in types:
        return _error(422, tr("text_empty", lang))
    return _error(422, tr("bad_request", lang))


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/haqqinda")
def about_page():
    return FileResponse(STATIC_DIR / "about.html")


@app.get("/sened")
def document_page():
    return FileResponse(STATIC_DIR / "sened.html")


def _blocked_message(category: str, lang: str = "az") -> str:
    """Сообщение пользователю на его языке. Без текста документа."""
    return tr("blocked", lang, name=CLASSIFICATION_NAMES[category])


@app.post("/api/check")
def check(body: TextIn, request: Request):
    category = classify(body.text)
    if category:
        # Текст с грифом дальше не обрабатываем: ни правила, ни модель его не видят.
        return {"blocked": True, "category": CLASSIFICATION_NAMES[category],
                "message": _blocked_message(category, _lang(request)), "findings": []}
    return {
        "blocked": False,
        "findings": [
            {"category": f.category, "start": f.start, "end": f.end, "source": f.source} for f in detect(body.text)
        ],
    }


def _block_classified(text: str, channel: str, request: Request, department: str | None = None) -> JSONResponse | None:
    """Текст с грифом блокируется целиком: не маскируется, в AI не уходит.

    Записываем в журнал инцидентов время, категорию и канал (без текста) и отвечаем 403.
    """
    category = classify(text)
    if category is None:
        return None
    return _register_block(category, channel, request, department)


def _register_block(category: str, channel: str, request: Request, department: str | None) -> JSONResponse:
    """Занести блокировку в счётчики, журнал инцидентов и журнал аудита; вернуть ответ 403."""
    if request.headers.get("x-siftguard-client", "").lower() == "extension":
        channel = "extension"
    name = CLASSIFICATION_NAMES[category]
    dept = _department(department)
    with _stats_lock:
        _totals["requests"] += 1
        _totals["blocked"] += 1
        _blocked_categories[name] += 1
        _dept_counts[dept]["requests"] += 1
        _dept_counts[dept]["blocked"] += 1
    incidents.add(category, name, channel)
    _audit_blocked(name, channel, dept)
    return JSONResponse(
        status_code=403,
        content={"detail": _blocked_message(category, _lang(request)), "blocked": True, "category": name},
    )


@app.get("/api/config")
def config():
    """Что доступно на этом сервере. Страница прячет режим «Встроенный AI», если ключа нет."""
    return {"ai_available": ai_enabled(), "model": ml_detector.status(), "redact_available": redact.available(),
            "vision_available": ai_enabled() and vision_enabled()}


def _count_protected(masked, department: str | None) -> None:
    """Учесть успешный запрос: только числа по категориям, источникам и отделу."""
    dept = _department(department)
    with _stats_lock:
        _totals["requests"] += 1
        _category_counts.update(masked.counts)
        _source_counts.update(masked.source_counts)
        _dept_counts[dept]["requests"] += 1
        _dept_categories[dept].update(masked.counts)


@app.post("/api/mask")
def mask_only(body: TextIn, request: Request):
    """Режим «копировать и вставить»: замаскировать текст, AI не вызывается.

    Значения персональных данных в ответ НЕ возвращаются: только замаскированный текст и позиции
    (spans). Таблицу «метка → значение» браузер собирает сам из текста, который пользователь
    уже держит у себя, и хранит только в памяти вкладки.
    """
    blocked = _block_classified(body.text, "mask", request, body.department)
    if blocked:
        return blocked
    masked = mask(body.text)
    # Таблица меток здесь нужна только для журнала аудита (хеши); сразу после этого она стирается.
    with TemporaryVault(masked.mapping):
        _audit_detected(masked, "mask", request, body.department)
    _count_protected(masked, body.department)
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
    blocked = _block_classified(body.text, "chat", request, body.department)
    if blocked:
        return blocked
    if not ai_enabled():
        raise HTTPException(
            status_code=503,
            detail=tr("ai_off_paste", _lang(request)),
        )
    if not _reserve_llm_call():
        raise HTTPException(status_code=429, detail=tr("daily_limit", _lang(request)))

    masked = mask(body.text)
    # Таблица меток живёт только внутри этого блока и стирается при любом выходе из него (успех, ошибка AI).
    with TemporaryVault(masked.mapping):
        _audit_detected(masked, "chat", request, body.department)
        try:
            raw_answer = ask_llm(masked.masked_text)
        except LLMError as exc:
            raise HTTPException(status_code=502, detail=exc.localized(_lang(request))) from exc
        _count_protected(masked, body.department)
        answer, info = restore_tolerant(raw_answer, masked.mapping)
        warnings = _restore_warnings(info, len(masked.mapping))
    return {
        "masked_text": masked.masked_text,
        "raw_answer": raw_answer,  # ответ AI как он пришёл, с метками: так страница показывает «до раскрытия»
        "answer": answer,
        "found": dict(masked.counts),
        "warnings": warnings,
    }


async def _read_upload(request: Request) -> bytes:
    """Тело запроса — сам файл. Читаем в память с жёстким потолком, ничего не сохраняя на диск."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > redact.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=tr("file_too_big", _lang(request)))
        chunks.append(chunk)
    return b"".join(chunks)


async def _redact_upload(request: Request):
    """Прочитать файл из тела запроса и закрасить. Возвращает (результат, отдел); ошибки — HTTPException."""
    if not redact.available():
        raise HTTPException(status_code=503, detail=tr("redact_unavailable", _lang(request)))
    data = await _read_upload(request)
    department = request.headers.get("x-department")
    if not _redact_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail=tr("busy", _lang(request)))
    try:
        result = await run_in_threadpool(redact.redact_file, data)
    except redact.RedactError as exc:
        raise HTTPException(status_code=422, detail=exc.localized(_lang(request))) from exc
    except Exception as exc:  # noqa: BLE001
        _log.error("redact failed: %s", type(exc).__name__)  # только тип ошибки, без содержимого документа
        raise HTTPException(status_code=500, detail=tr("redact_failed", _lang(request))) from exc
    finally:
        _redact_slots.release()
        del data
    return result, department


def _finish_redaction(result, request: Request, department: str | None) -> tuple[dict, dict]:
    """Учёт закрашенного документа (аудит, счётчики) и ответ клиенту.

    Возвращает (ответ, занятые номера меток по категориям): номера нужны, чтобы метки вопроса к AI не совпали
    с метками документа.
    """
    masked = result.masked
    offsets: dict[str, int] = {}
    for label in masked.mapping:  # "[AD_3]" -> AD: 3
        cat, _, num = label[1:-1].rpartition("_")
        offsets[cat] = max(offsets.get(cat, 0), int(num))
    with TemporaryVault(masked.mapping):
        _audit_detected(masked, "redact", request, department)
    _count_protected(masked, department)
    with _stats_lock:
        _totals["redacted_files"] += 1
        _totals["faces"] += result.counts.get("UZ", 0)
        _totals["signatures"] += result.counts.get("IMZA", 0)
    payload = {
        "blocked": False,
        "kind": result.kind,
        "page_count": result.page_count,
        "elapsed_ms": round(result.elapsed_ms),
        "counts": dict(result.counts),
        "masked_text": masked.masked_text,
        "pages": result.pages,
        "file": {"mime": result.mime, "name": "siftguard-qaralanmis." + ("pdf" if result.kind == "pdf" else "png"),
                 "data": base64.b64encode(result.data).decode("ascii")},
    }
    return payload, offsets


@app.post("/api/redact")
async def redact_document(request: Request):
    """Автоматическая закраска PDF или изображения (тело запроса = файл; отдел в заголовке X-Department).

    Назад: закрашенный файл (base64), превью «до/после» с координатами зон, счётчики и замаскированный текст
    документа для AI. Исходный файл и найденные значения нигде не сохраняются.
    """
    result, department = await _redact_upload(request)
    if result.blocked:
        return _register_block(result.blocked, "redact", request, department)
    payload, _offsets = _finish_redaction(result, request, department)
    return payload


MAX_QUESTION = 400
DEFAULT_QUESTION = "Bu sənəd haqqında qısa izah ver və vacib məqamları qeyd et."


@app.post("/api/ask-document")
async def ask_document(request: Request):
    """Скинуть документ AI: закрасить, а затем задать вопрос (тело запроса = файл; вопрос в заголовке X-Question,
    закодирован через encodeURIComponent; отдел в X-Department).

    В AI уходит только закрашенное изображение (если подключённая модель умеет читать картинки) и текст документа с
    метками, либо один этот текст (PDF или модель без зрения). Вопрос тоже проходит маскирование.
    Ответ приходит вместе с обычным результатом закраски; метки документа в ответе остаются метками.
    """
    if not ai_enabled():
        raise HTTPException(status_code=503, detail=tr("ai_off_doc", _lang(request)))
    question = unquote(request.headers.get("x-question", "")).strip()[:MAX_QUESTION] or DEFAULT_QUESTION
    result, department = await _redact_upload(request)
    if result.blocked:
        return _register_block(result.blocked, "redact", request, department)
    payload, offsets = _finish_redaction(result, request, department)

    payload["ai_mode"] = None
    payload["answer"] = ""
    payload["ai_error"] = ""
    if not _reserve_llm_call():
        payload["ai_error"] = tr("daily_limit", _lang(request))
        return payload

    q = mask(question, start_after=offsets)  # вопрос тоже может содержать данные: маскируем с продолжением нумерации
    use_image = result.kind == "image" and vision_enabled()
    try:
        jpeg = await run_in_threadpool(redact.to_jpeg, result.data) if use_image else None
        with TemporaryVault(q.mapping):
            _audit_detected(q, "redact", request, department)
            raw = await run_in_threadpool(ask_llm_document, q.masked_text, result.masked.masked_text, jpeg)
            answer, _info = restore_tolerant(raw, q.mapping)
        payload["answer"] = answer
        payload["ai_mode"] = "image" if use_image else "text"
    except LLMError as exc:
        payload["ai_error"] = exc.localized(_lang(request))
    except Exception as exc:  # noqa: BLE001
        _log.error("ask-document failed: %s", type(exc).__name__)
        payload["ai_error"] = tr("doc_ai_failed", _lang(request))
    return payload


def _restore_warnings(info, total: int) -> list[str]:
    """Понятные предупреждения о том, как AI обошёлся с метками (без значений данных)."""
    warnings = []
    if info.fuzzy:
        warnings.append(f"AI etiketlərin yazılışını dəyişib ({info.fuzzy} ədəd): məlumatlar mənaya görə bərpa olundu. Cavabı yoxlayın.")
    if total and len(info.missing) == total:
        warnings.append("Cavabda mətninizdəki etiketlərin heç biri yoxdur: AI onları silib və ya cavabı yenidən yazıb.")
    elif info.missing:
        warnings.append("Cavabda bu etiketlər tapılmadı: " + ", ".join(info.missing) + ". AI onları silmiş və ya xatırlamamış ola bilər.")
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
            raise HTTPException(status_code=401, detail=tr("admin_token", _lang(request)))
    elif (request.client.host if request.client else "") not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=403,
            detail=tr("admin_local", _lang(request)),
        )


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/api/admin/incidents")
def admin_incidents(request: Request, since: int = 0):
    """Блокировки по грифу: время, категория, канал. Текста документов здесь нет и быть не может."""
    require_admin(request)
    return {"incidents": incidents.since(since), **incidents.summary()}


@app.get("/api/admin/stats")
def admin_stats(request: Request):
    """Живые счётчики для панели администратора: по категориям, блокировкам и отделам. Только числа."""
    require_admin(request)
    with _stats_lock:
        departments = []
        for dept_id, name in [*DEPARTMENTS.items(), (NO_DEPARTMENT, NO_DEPARTMENT_NAME)]:
            cats = dict(_dept_categories[dept_id])
            departments.append({
                "id": dept_id,
                "name": name,
                "requests": _dept_counts[dept_id]["requests"],
                "blocked": _dept_counts[dept_id]["blocked"],
                "protected": sum(cats.values()),
                "categories": cats,
            })
        return {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "requests": _totals["requests"],
            "blocked": _totals["blocked"],
            "categories": dict(_category_counts),
            "blocked_categories": dict(_blocked_categories),
            "sources": dict(_source_counts),
            "departments": departments,
            "documents": {"files": _totals["redacted_files"], "faces": _totals["faces"], "signatures": _totals["signatures"]},
            "vault": TemporaryVault.stats(),
        }


class ValueIn(BaseModel):
    value: str = Field(min_length=1, max_length=200)


@app.get("/api/admin/audit")
def admin_audit(request: Request, limit: int = 50):
    """Журнал аудита: состояние цепочки и последние события. Значений в журнале нет, только сокращённые HMAC."""
    require_admin(request)
    limit = max(1, min(limit, 200))
    return {
        "chain": audit.verify_chain(),
        "healthy": audit.healthy,
        "key_source": audit.key_source or ("env" if os.getenv("AUDIT_KEY") else "file"),
        "vault": TemporaryVault.stats(),
        "events": audit.recent(limit),
    }


@app.post("/api/admin/audit/check")
def admin_audit_check(body: ValueIn, request: Request):
    """Встречалось ли это значение? Считаем HMAC введённого значения и ищем в журнале; само значение не сохраняется."""
    require_admin(request)
    return audit.check_value(body.value)


_eval_lock = Lock()


@app.post("/api/admin/eval")
def admin_eval(request: Request):
    """Кнопка «100 sintetik cümlə üzrə test»: прогоняет детектор по 100 синтетическим фразам и отдаёт
    настоящие Precision, Recall и задержку. Только числа; на обычные счётчики и журнал не влияет."""
    require_admin(request)
    if not _eval_lock.acquire(blocking=False):
        return _error(409, tr("eval_running", _lang(request)))
    try:
        return evaluation.evaluate_all()
    finally:
        _eval_lock.release()
