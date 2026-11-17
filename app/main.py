"""FastAPI-сервер Süzgəc."""
from __future__ import annotations

from collections import Counter
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

MAX_TEXT_LENGTH = 5000
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="Süzgəc")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Статистика — только счётчики по категориям, никаких текстов.
_stats_lock = Lock()
_category_counts: Counter = Counter()
_totals: Counter = Counter()


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # Не отдаём назад введённый текст (в стандартном ответе FastAPI он есть).
    return JSONResponse(
        status_code=422,
        content={"detail": f"Текст должен быть от 1 до {MAX_TEXT_LENGTH} символов."},
    )


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
