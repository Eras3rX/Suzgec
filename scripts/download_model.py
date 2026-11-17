"""Скачать локальную модель LocalDoc/pii-ner-azerbaijani-v4 в models/pii-ner-azerbaijani-v4.

    .venv\\Scripts\\python.exe scripts/download_model.py

- Версия модели зафиксирована (REVISION): скачивается ровно то, что мы проверяли.
- Веса сверяются по SHA-256, остальные файлы по размеру. При несовпадении файл удаляется, скрипт падает.
- Лицензия модели: CC BY-NC 4.0 (только некоммерческое использование, нужно указывать авторов).
  Модель в репозиторий не коммитится (папка models/ в .gitignore).
- Это единственное место проекта, которое ходит в сеть. Сервер модель не скачивает.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "LocalDoc/pii-ner-azerbaijani-v4"
REVISION = "6e1d85c7ad1767720611b00d18a6b8a0628b051a"
TARGET = Path(__file__).resolve().parent.parent / "models" / "pii-ner-azerbaijani-v4"

# имя файла -> (размер в байтах, SHA-256 или None)
FILES = {
    "config.json": (3441, None),
    "inference_config.json": (94, None),
    "lexicon.json": (17808, None),
    "pii_ner.py": (16620, None),
    "tokenizer.json": (9587273, None),
    "tokenizer_config.json": (574, None),
    "trim_rules.json": (761, None),
    "trim_rules_all.json": (1344, None),
    "README.md": (9362, None),
    "model.safetensors": (279624676, "75dd8f73996ab5f9c3ccc4abeed4cebf327663f239480dd6a18779c71fe7183c"),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ok(path: Path, size: int, digest: str | None) -> bool:
    return path.is_file() and path.stat().st_size == size and (digest is None or sha256(path) == digest)


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    total_mb = sum(size for size, _ in FILES.values()) / 1e6
    print(f"Модель {REPO}, версия {REVISION[:8]}, ~{total_mb:.0f} МБ, лицензия CC BY-NC 4.0")
    for name, (size, digest) in FILES.items():
        dest = TARGET / name
        if is_ok(dest, size, digest):
            print(f"  уже есть: {name}")
            continue
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        print(f"  скачиваю: {name} ({size / 1e6:.1f} МБ)")
        urllib.request.urlretrieve(url, dest)
        if not is_ok(dest, size, digest):
            dest.unlink(missing_ok=True)
            print(f"ОШИБКА: файл {name} не прошёл проверку размера/хеша, удалён.", file=sys.stderr)
            return 1
    (TARGET / "REVISION.txt").write_text(REVISION + "\n", encoding="utf-8")
    print(f"Готово: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
