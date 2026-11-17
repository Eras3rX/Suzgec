"""Замер точности детектора на tests/eval_data.csv в двух режимах.

Запуск из корня проекта:
    .venv\\Scripts\\python.exe tests/eval.py                # оба режима и таблица сравнения
    .venv\\Scripts\\python.exe tests/eval.py --mode rules   # только правила
    .venv\\Scripts\\python.exe tests/eval.py --mode hybrid  # правила + локальная модель
    .venv\\Scripts\\python.exe tests/eval.py --no-errors    # без списка ошибок

Режимы:
  rules  — только правила (регулярные выражения, контрольные суммы, словарь имён);
  hybrid — правила + локальная модель (нужна скачанная модель, см. README).

Находка верна, если совпали категория и значение целиком (частично найденное ФИО — это и пропуск,
и ложное срабатывание). Исключение — адреса (ADRES): границы адреса у людей и модели расходятся,
поэтому для них достаточно перекрытия не меньше половины (IoU >= 0.5).

Файл eval_data.csv: id, lang, text, expected, где expected — список "КАТЕГОРИЯ=значение" через "|"
(пусто — в тексте нет персональных данных).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PII_MODEL_ENABLED", "1")

from app import ml_detector  # noqa: E402
from app.detector import PRIORITY, detect  # noqa: E402

DATA = Path(__file__).with_name("eval_data.csv")
LOOSE = {"ADRES"}  # для них достаточно перекрытия, а не точного совпадения границ
MODES = {"rules": "Правила", "hybrid": "Правила + AI"}


def load_rows():
    with DATA.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            expected = [tuple(item.split("=", 1)) for item in row["expected"].split("|") if item]
            yield row, expected


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union else 0.0


def match(text: str, expected: list[tuple[str, str]], findings):
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


def run(mode: str) -> dict:
    stats = {c: {"expected": 0, "found": 0, "missed": 0, "false": 0} for c in PRIORITY}
    errors: list[str] = []
    clean = clean_fp = rows = 0
    for row, expected in load_rows():
        rows += 1
        findings = detect(row["text"], use_model=False if mode == "rules" else None)
        hit, missed, extra = match(row["text"], expected, findings)
        for cat, _ in expected:
            stats[cat]["expected"] += 1
        for cat, _ in hit:
            stats[cat]["found"] += 1
        for cat, val in missed:
            stats[cat]["missed"] += 1
            errors.append(f'{row["id"]}  пропущено {cat}={val}')
        for cat, val in extra:
            stats[cat]["false"] += 1
            errors.append(f'{row["id"]}  ложное {cat}={val}')
        if not expected:
            clean += 1
            clean_fp += bool(extra)
    return {"stats": stats, "errors": errors, "rows": rows, "clean": clean, "clean_fp": clean_fp}


def _totals(stats: dict) -> dict:
    total = {"expected": 0, "found": 0, "missed": 0, "false": 0}
    for s in stats.values():
        for k in total:
            total[k] += s[k]
    return total


def _rates(s: dict) -> tuple[str, str]:
    recall = f'{s["found"] / s["expected"]:.0%}' if s["expected"] else "—"
    found_total = s["found"] + s["false"]
    precision = f'{s["found"] / found_total:.0%}' if found_total else "—"
    return recall, precision


def print_single(name: str, result: dict) -> None:
    header = f'{"Категория":<10}{"Ожидалось":>10}{"Найдено":>9}{"Пропущено":>11}{"Ложных":>8}{"Recall":>9}{"Precision":>11}'
    print(f"\n=== {name} ===")
    print(header)
    print("-" * len(header))
    for cat in PRIORITY:
        s = result["stats"][cat]
        r, p = _rates(s)
        print(f'{cat:<10}{s["expected"]:>10}{s["found"]:>9}{s["missed"]:>11}{s["false"]:>8}{r:>9}{p:>11}')
    print("-" * len(header))
    t = _totals(result["stats"])
    r, p = _rates(t)
    print(f'{"ИТОГО":<10}{t["expected"]:>10}{t["found"]:>9}{t["missed"]:>11}{t["false"]:>8}{r:>9}{p:>11}')
    print(f'Фраз: {result["rows"]}, без данных: {result["clean"]}, из них с ложными срабатываниями: {result["clean_fp"]}')


def print_compare(a: dict, b: dict) -> None:
    print("\n=== Сравнение: только правила и правила + AI-модель ===")
    head1 = f'{"":<10}{"":>10}  {"Правила":^38}  {"Правила + AI":^38}'
    head2 = (f'{"Категория":<10}{"Ожидалось":>10}  '
             + f'{"найд.":>6}{"проп.":>6}{"лож.":>6}{"Recall":>9}{"Prec.":>8}  ' * 2)
    print(head1)
    print(head2)
    print("-" * len(head2))

    def cells(s):
        r, p = _rates(s)
        return f'{s["found"]:>6}{s["missed"]:>6}{s["false"]:>6}{r:>9}{p:>8}  '

    for cat in PRIORITY:
        sa, sb = a["stats"][cat], b["stats"][cat]
        print(f'{cat:<10}{sa["expected"]:>10}  {cells(sa)}{cells(sb)}')
    print("-" * len(head2))
    ta, tb = _totals(a["stats"]), _totals(b["stats"])
    print(f'{"ИТОГО":<10}{ta["expected"]:>10}  {cells(ta)}{cells(tb)}')
    print(f'\nФраз без данных с ложными срабатываниями: правила {a["clean_fp"]} из {a["clean"]}, '
          f'правила + AI {b["clean_fp"]} из {b["clean"]}')


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["rules", "hybrid", "both"], default="both")
    parser.add_argument("--no-errors", action="store_true", help="не печатать список ошибок")
    args = parser.parse_args()

    modes = ["rules", "hybrid"] if args.mode == "both" else [args.mode]
    results: dict[str, dict] = {}
    for mode in modes:
        if mode == "hybrid":
            status = ml_detector.load_blocking()
            if status != "ready":
                print(f"\nМодель недоступна (статус: {status}): режим «Правила + AI» пропущен. "
                      "Скачайте модель (см. README) и проверьте PII_MODEL_ENABLED / PII_MODEL_PATH.")
                continue
        results[mode] = run(mode)

    if len(results) == 2:
        print_compare(results["rules"], results["hybrid"])
    else:
        for mode, result in results.items():
            print_single(MODES[mode], result)

    if not args.no_errors:
        for mode, result in results.items():
            if result["errors"]:
                print(f"\nОшибки ({MODES[mode]}):")
                for line in result["errors"]:
                    print("  " + line)


if __name__ == "__main__":
    main()
