"""Замер точности и скорости детектора на data/eval_data.csv (100 синтетических фраз) в двух режимах.

Запуск из корня проекта:
    .venv\\Scripts\\python.exe tests/eval.py                # оба режима и таблица сравнения
    .venv\\Scripts\\python.exe tests/eval.py --mode rules   # только правила
    .venv\\Scripts\\python.exe tests/eval.py --mode hybrid  # правила + локальная модель
    .venv\\Scripts\\python.exe tests/eval.py --no-errors    # без списка ошибок

Вся логика замера в app/evaluation.py: её же запускает кнопка «100 sintetik cümlə üzrə test» в панели
администратора. Здесь только печать таблиц.

Режимы:
  rules  — только правила (регулярные выражения, контрольные суммы, словарь имён);
  hybrid — правила + локальная модель (нужна скачанная модель, см. README).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PII_MODEL_ENABLED", "1")

from app import evaluation, ml_detector  # noqa: E402
from app.detector import PRIORITY  # noqa: E402

MODE_TITLES = {"rules": "Правила", "hybrid": "Правила + AI"}


def pct(value) -> str:
    return f"{value:.0%}" if value is not None else "—"


def print_single(result: dict) -> None:
    header = f'{"Категория":<10}{"Ожидалось":>10}{"Найдено":>9}{"Пропущено":>11}{"Ложных":>8}{"Recall":>9}{"Precision":>11}'
    print(f'\n=== {MODE_TITLES[result["mode"]]} ===')
    print(header)
    print("-" * len(header))
    for cat in PRIORITY:
        s = result["categories"][cat]
        print(f'{cat:<10}{s["expected"]:>10}{s["found"]:>9}{s["missed"]:>11}{s["false"]:>8}{pct(s["recall"]):>9}{pct(s["precision"]):>11}')
    print("-" * len(header))
    t = result["totals"]
    print(f'{"ИТОГО":<10}{t["expected"]:>10}{t["found"]:>9}{t["missed"]:>11}{t["false"]:>8}{pct(t["recall"]):>9}{pct(t["precision"]):>11}')
    lat = result["latency_ms"]
    print(f'Фраз: {result["phrases"]}, без данных: {result["without_data"]}, из них с ложными срабатываниями: '
          f'{result["clean_false_alarms"]}. Задержка: средняя {lat["avg"]:.2f} мс, p95 {lat["p95"]:.2f} мс, макс {lat["max"]:.2f} мс')


def print_compare(a: dict, b: dict) -> None:
    print("\n=== Сравнение: только правила и правила + AI-модель ===")
    head1 = f'{"":<10}{"":>10}  {"Правила":^38}  {"Правила + AI":^38}'
    head2 = (f'{"Категория":<10}{"Ожидалось":>10}  '
             + f'{"найд.":>6}{"проп.":>6}{"лож.":>6}{"Recall":>9}{"Prec.":>8}  ' * 2)
    print(head1)
    print(head2)
    print("-" * len(head2))

    def cells(s):
        return f'{s["found"]:>6}{s["missed"]:>6}{s["false"]:>6}{pct(s["recall"]):>9}{pct(s["precision"]):>8}  '

    for cat in PRIORITY:
        print(f'{cat:<10}{a["categories"][cat]["expected"]:>10}  {cells(a["categories"][cat])}{cells(b["categories"][cat])}')
    print("-" * len(head2))
    print(f'{"ИТОГО":<10}{a["totals"]["expected"]:>10}  {cells(a["totals"])}{cells(b["totals"])}')
    print(f'\nФраз без данных с ложными срабатываниями: правила {a["clean_false_alarms"]} из {a["without_data"]}, '
          f'правила + AI {b["clean_false_alarms"]} из {b["without_data"]}')
    print(f'Средняя задержка на фразу: правила {a["latency_ms"]["avg"]:.2f} мс, правила + AI {b["latency_ms"]["avg"]:.2f} мс')


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
        results[mode] = evaluation.run(mode, with_errors=True)

    if len(results) == 2:
        print_compare(results["rules"], results["hybrid"])
    else:
        for result in results.values():
            print_single(result)

    if not args.no_errors:
        for mode, result in results.items():
            if result["errors"]:
                print(f"\nОшибки ({MODE_TITLES[mode]}):")
                for line in result["errors"]:
                    print("  " + line)


if __name__ == "__main__":
    main()
