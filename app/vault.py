"""Временное хранилище таблицы меток на время одной операции.

Для восстановления имён в ответе значения нужны в открытом виде: хеш обратить нельзя. Поэтому таблица
«метка -> значение» живёт только в памяти и только пока выполняется операция. Выход из блока `with`
(успешный, с ошибкой AI или с любым другим исключением) стирает её: значения затираются, словарь очищается.

Честная оговорка: в Python строки неизменяемы, поэтому «стирание» означает, что мы убираем все свои ссылки на
значения и затираем записи словаря. Это не гарантированная очистка оперативной памяти на уровне системы.
"""
from __future__ import annotations

from threading import Lock


class TemporaryVault:
    _lock = Lock()
    _active = 0
    _closed = 0

    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping  # тот же словарь, что и у вызывающего: стираем его на месте

    def __enter__(self) -> "TemporaryVault":
        with TemporaryVault._lock:
            TemporaryVault._active += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        for key in list(self.mapping):
            self.mapping[key] = ""  # сначала затираем значения
        self.mapping.clear()
        with TemporaryVault._lock:
            TemporaryVault._active -= 1
            TemporaryVault._closed += 1
        return False  # исключения не подавляем

    @classmethod
    def stats(cls) -> dict:
        """Сколько таблиц хранится сейчас (должно быть 0) и сколько уже стёрто."""
        with cls._lock:
            return {"active": cls._active, "wiped": cls._closed}
