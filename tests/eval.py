"""Замер точности детектора на tests/eval_data.csv.

Запуск из корня проекта:
    .venv\\Scripts\\python.exe tests/eval.py

Находка считается верной, только если совпали категория И значение целиком
(частично найденное ФИО — это и пропуск, и ложное срабатывание).
Файл eval_data.csv: id, lang, text, expected, где expected — список
"КАТЕГОРИЯ=значение" через "|" (пусто — в тексте нет персональных данных).
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.detector import PRIORITY, detect  # noqa: E402

DATA = Path(__file__).with_name("eval_data.csv")


def load_rows():
    with DATA.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            expected = {tuple(item.split("=", 1)) for item in row["expected"].split("|") if item}
            yield row, expected


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    stats = {c: {"expected": 0, "found": 0, "missed": 0, "false": 0} for c in PRIORITY}
    misses: list[str] = []
    false_alarms: list[str] = []
    rows = clean_rows = clean_rows_with_fp = 0

    for row, expected in load_rows():
        rows += 1
        found = {(f.category, f.value) for f in detect(row["text"])}
        for cat, _ in expected:
            stats[cat]["expected"] += 1
        for cat, val in expected & found:
            stats[cat]["found"] += 1
        for cat, val in sorted(expected - found):
            stats[cat]["missed"] += 1
            misses.append(f'{row["id"]}  пропущено {cat}={val}')
        for cat, val in sorted(found - expected):
            stats[cat]["false"] += 1
            false_alarms.append(f'{row["id"]}  ложное {cat}={val}')
        if not expected:
            clean_rows += 1
            clean_rows_with_fp += bool(found)

    header = f'{"Категория":<10}{"Ожидалось":>10}{"Найдено":>9}{"Пропущено":>11}{"Ложных":>8}{"Recall":>9}{"Precision":>11}'
    print(header)
    print("-" * len(header))
    total = {"expected": 0, "found": 0, "missed": 0, "false": 0}
    for cat in PRIORITY:
        s = stats[cat]
        for k in total:
            total[k] += s[k]
        print(_line(cat, s))
    print("-" * len(header))
    print(_line("ИТОГО", total))
    print(f"\nФраз всего: {rows}, из них без персональных данных: {clean_rows}, "
          f"с ложными срабатываниями: {clean_rows_with_fp}")

    if misses or false_alarms:
        print("\nОшибки:")
        for line in misses + false_alarms:
            print("  " + line)


def _line(name: str, s: dict) -> str:
    recall = f'{s["found"] / s["expected"]:.0%}' if s["expected"] else "—"
    found_total = s["found"] + s["false"]
    precision = f'{s["found"] / found_total:.0%}' if found_total else "—"
    return f'{name:<10}{s["expected"]:>10}{s["found"]:>9}{s["missed"]:>11}{s["false"]:>8}{recall:>9}{precision:>11}'


if __name__ == "__main__":
    main()
