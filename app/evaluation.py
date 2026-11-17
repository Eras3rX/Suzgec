"""Замер точности и скорости детектора на 100 синтетических фразах (data/eval_data.csv).

Им пользуются и команда `python tests/eval.py`, и кнопка «100 sintetik cümlə üzrə test» в панели администратора.
Все числа считаются заново при каждом запуске: заранее вписанных значений нет.

Для панели администратора наружу отдаются только числа (счётчики, точность, полнота, задержка). Тексты фраз
и найденные значения в ответ не попадают (список ошибок возвращается только по просьбе, для консоли).

Находка верна, если совпали категория и значение целиком. Исключение: адреса (ADRES) — для них достаточно
перекрытия не меньше половины (IoU >= 0.5): границы адреса у людей и модели расходятся.
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone
from pathlib import Path

from app import ml_detector
from app.detector import PRIORITY, detect

DATA = Path(__file__).resolve().parent.parent / "data" / "eval_data.csv"
LOOSE = {"ADRES"}  # для них достаточно перекрытия, а не точного совпадения границ
MODES = {"rules": "Qaydalar", "hybrid": "Qaydalar + AI"}


def load_rows() -> list[dict]:
    """Фразы набора: id, язык, текст, ожидаемые находки [(категория, значение)]."""
    raw = DATA.read_text(encoding="utf-8-sig")  # только чтение набора фраз: данные приложение не записывает
    return [
        {
            "id": row["id"],
            "lang": row["lang"],
            "text": row["text"],
            "expected": [tuple(item.split("=", 1)) for item in row["expected"].split("|") if item],
        }
        for row in csv.DictReader(io.StringIO(raw, newline=""))
    ]


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union else 0.0


def match(text: str, expected: list[tuple[str, str]], findings) -> tuple[list, list, list]:
    """Вернуть (совпало, пропущено, лишнее). Каждая находка совпадает не более чем с одним ожиданием."""
    found = [(f.category, f.value, (f.start, f.end)) for f in findings]
    used: set[int] = set()
    hit, missed = [], []
    for cat, val in expected:
        pos = text.find(val)
        span = (pos, pos + len(val))
        for i, (fcat, fval, fspan) in enumerate(found):
            if i in used or fcat != cat:
                continue
            ok = _iou(span, fspan) >= 0.5 if cat in LOOSE else fval == val
            if ok:
                used.add(i)
                hit.append((cat, val))
                break
        else:
            missed.append((cat, val))
    extra = [(fcat, fval) for i, (fcat, fval, _) in enumerate(found) if i not in used]
    return hit, missed, extra


def _rates(found: int, expected: int, false: int) -> dict:
    """Полнота (recall), точность (precision) и F1 как доли 0..1; None, если делить не на что."""
    recall = found / expected if expected else None
    precision = found / (found + false) if (found + false) else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if precision == 0 or recall == 0 else None)
    return {"recall": recall, "precision": precision, "f1": f1}


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, int(round(q * len(sorted_values) + 0.4999)) - 1))
    return sorted_values[index]


def run(mode: str, rows: list[dict] | None = None, with_errors: bool = False) -> dict:
    """Прогнать детектор по всем фразам в режиме rules (только правила) или hybrid (правила + модель)."""
    rows = rows if rows is not None else load_rows()
    stats = {c: {"expected": 0, "found": 0, "missed": 0, "false": 0} for c in PRIORITY}
    errors: list[str] = []
    latencies: list[float] = []
    clean = clean_fp = 0

    for row in rows:
        started = time.perf_counter()
        findings = detect(row["text"], use_model=False if mode == "rules" else None)
        latencies.append((time.perf_counter() - started) * 1000)

        hit, missed, extra = match(row["text"], row["expected"], findings)
        for cat, _ in row["expected"]:
            stats[cat]["expected"] += 1
        for cat, _ in hit:
            stats[cat]["found"] += 1
        for cat, val in missed:
            stats[cat]["missed"] += 1
            errors.append(f'{row["id"]}  пропущено {cat}={val}')
        for cat, val in extra:
            stats[cat]["false"] += 1
            errors.append(f'{row["id"]}  ложное {cat}={val}')
        if not row["expected"]:
            clean += 1
            clean_fp += bool(extra)

    categories = {c: {**s, **_rates(s["found"], s["expected"], s["false"])} for c, s in stats.items()}
    totals = {k: sum(s[k] for s in stats.values()) for k in ("expected", "found", "missed", "false")}
    totals.update(_rates(totals["found"], totals["expected"], totals["false"]))
    ordered = sorted(latencies)
    result = {
        "mode": mode,
        "mode_name": MODES[mode],
        "phrases": len(rows),
        "with_data": len(rows) - clean,
        "without_data": clean,
        "categories": categories,
        "totals": totals,
        "clean_false_alarms": clean_fp,
        "latency_ms": {
            "avg": sum(latencies) / len(latencies) if latencies else 0.0,
            "p95": _percentile(ordered, 0.95),
            "max": ordered[-1] if ordered else 0.0,
            "total": sum(latencies),
        },
    }
    if with_errors:
        result["errors"] = errors
    return result


def evaluate_all() -> dict:
    """Что показывает кнопка в панели администратора: режим «только правила» и, если модель готова,
    режим «правила + AI». Только числа."""
    rows = load_rows()
    runs = [run("rules", rows)]
    model = ml_detector.status()
    if model == "ready":
        runs.append(run("hybrid", rows))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phrases": len(rows),
        "with_data": sum(1 for r in rows if r["expected"]),
        "without_data": sum(1 for r in rows if not r["expected"]),
        "model": model,
        "runs": runs,
    }
