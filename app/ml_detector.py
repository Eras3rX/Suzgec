"""Локальная модель для поиска имён, адресов и дат (LocalDoc/pii-ner-azerbaijani-v4).

Важно:
- Модель работает только на этом сервере. Работа с сетью отключена принудительно (офлайн-режим
  Hugging Face), текст никуда не отправляется. Веса лежат в локальной папке, скачанной заранее.
- Модель необязательна. Если она выключена, не скачана, нет PyTorch или не хватило памяти, модуль
  молча (без текста запроса в логах) сообщает «нет модели», и работают только правила.
- Загрузка идёт в фоновом потоке: пока модель не готова, запросы обрабатываются правилами.
- Лицензия модели CC BY-NC 4.0 (некоммерческая). См. README.

Переменные окружения:
  PII_MODEL_ENABLED     1 (по умолчанию) или 0 — выключатель модели
  PII_MODEL_PATH        папка с моделью (по умолчанию models/pii-ner-azerbaijani-v4)
  PII_MODEL_MIN_SCORE   минимальная уверенность модели, 0..1 (по умолчанию 0.6)
  PII_MODEL_CATEGORIES  какие категории брать у модели (по умолчанию AD,ADRES,TARIX);
                        например AD,ADRES — без дат, если они мешают
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import threading
from pathlib import Path

from dotenv import load_dotenv

from app.detector import SOURCE_AI, Finding

load_dotenv()

_log = logging.getLogger("siftguard.ml")

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "pii-ner-azerbaijani-v4"

# Метки модели -> наши категории. Остальные метки (время, возраст, номера документов и т.д.) не берём:
# структурированные данные надёжнее ловят правила.
NAME_LABELS = {"GIVENNAME", "SURNAME"}
ADDRESS_LABELS = {"CITY", "STREET", "BUILDINGNUM", "ZIPCODE"}
DATE_LABELS = {"DATE"}
# Части адреса склеиваем в один фрагмент, если между ними не больше стольких символов без точки и перевода строки.
MAX_ADDRESS_GAP = 14

_lock = threading.Lock()  # PyTorch-вызовы по одному
_state: dict = {"status": None, "ner": None}  # status: None | "loading" | "ready" | "unavailable"


def enabled() -> bool:
    return os.getenv("PII_MODEL_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off", "")


def model_dir() -> Path:
    return Path(os.getenv("PII_MODEL_PATH") or DEFAULT_MODEL_DIR)


def min_score() -> float:
    try:
        return float(os.getenv("PII_MODEL_MIN_SCORE", "0.6"))
    except ValueError:
        return 0.6


def categories() -> set[str]:
    raw = os.getenv("PII_MODEL_CATEGORIES", "AD,ADRES,TARIX")
    return {c.strip().upper() for c in raw.split(",") if c.strip()} & {"AD", "ADRES", "TARIX"}


def status() -> str:
    """off | loading | ready | unavailable"""
    if not enabled():
        return "off"
    return _state["status"] or "loading"  # ещё не начинали = вот-вот начнём


def _load() -> None:
    try:
        path = model_dir()
        if not (path / "model.safetensors").is_file() or not (path / "pii_ner.py").is_file():
            raise FileNotFoundError("model files not found")
        # Никакой сети: ни скачиваний, ни телеметрии.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        # Файл pii_ner.py загружаем по точному пути (папку модели в sys.path не добавляем).
        spec = importlib.util.spec_from_file_location("pii_ner_local", path / "pii_ner.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ner = module.PiiNer(path, device="cpu")
        ner.predict("Salam")  # прогрев
        _state["ner"] = ner
        _state["status"] = "ready"
    except BaseException as exc:  # noqa: BLE001 — любая причина = работаем без модели
        _state["ner"] = None
        _state["status"] = "unavailable"
        _log.warning("PII model unavailable, using rules only: %s", type(exc).__name__)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise


def start_background_load() -> None:
    """Запустить загрузку модели в фоне (один раз). Сайт при этом не ждёт."""
    with _lock:
        if not enabled() or _state["status"] is not None:
            return
        _state["status"] = "loading"
    threading.Thread(target=_guarded_load, name="pii-model-load", daemon=True).start()


def _guarded_load() -> None:
    with _lock:
        _load()


def load_blocking() -> str:
    """Загрузить модель и подождать (для eval и скриптов). Возвращает статус."""
    if not enabled():
        return "off"
    with _lock:
        if _state["status"] in (None, "loading"):
            _state["status"] = "loading"
            _load()
    return status()


# Фильтры от типичных ошибок модели. Она обучена на азербайджанском (латиница) и на русском тексте
# принимает за имя обычные слова («время», «Номер») с уверенностью 0.9+, а аббревиатуры («IBAN») за имена.
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-zƏəİıÖöÜüŞşÇçĞğ]")
_CYR_SURNAME_ENDINGS = ("ов", "ова", "ев", "ева", "ин", "ина", "ян", "оглы", "кызы")
_CAPS_NAME_MIN_SCORE = 0.9  # заглавными писать имена не принято: берём только очень уверенные
_MONTHS = re.compile(
    r"yanvar|fevral|mart|aprel|may|iyun|iyul|avqust|sentyabr|oktyabr|noyabr|dekabr|"
    r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр",
    re.IGNORECASE,
)
_DATE_NUMERIC = re.compile(r"\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}")


def _name_plausible(value: str, score: float) -> bool:
    if value.isupper() and score < _CAPS_NAME_MIN_SCORE:
        return False
    if _CYRILLIC.search(value) and not _LATIN.search(value):
        # Русский текст: слово принимаем как имя, только если оно похоже на фамилию.
        return any(w.lower().endswith(_CYR_SURNAME_ENDINGS) and len(w) >= 4 for w in value.split())
    return True


def _date_plausible(value: str) -> bool:
    """Настоящая дата: 12.05.1990, 1990-05-12, «3 mart 1985». Не «45/2024» из номера протокола."""
    return bool(_DATE_NUMERIC.search(value) or _MONTHS.search(value))


def _to_findings(text: str, entities: list[dict]) -> list[Finding]:
    threshold = min_score()
    names, addresses, dates = [], [], []
    for e in sorted(entities, key=lambda x: x["start"]):
        if e["score"] < threshold or e["start"] >= e["end"]:
            continue
        label = e["label"]
        if label in NAME_LABELS:
            if _name_plausible(text[e["start"]:e["end"]], e["score"]):
                names.append(e)
        elif label in ADDRESS_LABELS:
            addresses.append(e)
        elif label in DATE_LABELS:
            if _date_plausible(text[e["start"]:e["end"]]):
                dates.append(e)

    findings: list[Finding] = []

    # Имя + фамилия, идущие подряд, — один фрагмент.
    for group in _group(text, names, gap=lambda g: g.strip(" \t") == ""):
        findings.append(_finding("AD", text, group))

    # Части адреса (город, улица, дом, индекс) — один фрагмент. Адресом считаем, только если в нём
    # есть номер дома или индекс и ещё хотя бы один элемент (город или улица): одинокий город
    # («Bakı paytaxtdır»), улица-достопримечательность или «45 nəfər» — ещё не адрес человека.
    for group in _group(text, addresses, gap=lambda g: len(g) <= MAX_ADDRESS_GAP and not re.search(r"[.!?\n]", g)):
        labels = {e["label"] for e in group}
        if len(labels) >= 2 and labels & {"BUILDINGNUM", "ZIPCODE"}:
            findings.append(_finding("ADRES", text, group))

    for e in dates:
        findings.append(_finding("TARIX", text, [e]))
    wanted = categories()
    return [f for f in findings if f.category in wanted]


def _group(text: str, entities: list[dict], gap) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for e in entities:
        if groups and e["start"] >= groups[-1][-1]["end"] and gap(text[groups[-1][-1]["end"]:e["start"]]):
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


def _finding(category: str, text: str, group: list[dict]) -> Finding:
    start, end = group[0]["start"], group[-1]["end"]
    return Finding(category, start, end, text[start:end], SOURCE_AI)


def predict(text: str) -> list[Finding]:
    """Находки модели в нашем формате. Если модель недоступна — пустой список (работают правила)."""
    if status() != "ready":
        return []
    try:
        with _lock:
            entities = _state["ner"].predict(text)
        return _to_findings(text, entities)
    except Exception as exc:  # noqa: BLE001
        _log.warning("PII model failed on a request, using rules only: %s", type(exc).__name__)
        return []
