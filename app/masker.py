"""Маскирование и восстановление.

Таблица меток (mapping) живёт только в памяти одного запроса:
её нигде нельзя сохранять и логировать.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.detector import detect

_LABEL = re.compile(r"\[[A-Z]+_\d+\]")


@dataclass
class MaskResult:
    masked_text: str
    mapping: dict[str, str] = field(default_factory=dict)  # "[FIN_1]" -> исходное значение
    counts: Counter = field(default_factory=Counter)  # категория -> сколько найдено


def mask(text: str) -> MaskResult:
    result = MaskResult(masked_text="")
    value_to_label: dict[tuple[str, str], str] = {}
    per_category: Counter = Counter()
    parts: list[str] = []
    pos = 0
    for f in detect(text):
        key = (f.category, f.value)
        if key not in value_to_label:  # одинаковое значение — одинаковая метка
            per_category[f.category] += 1
            label = f"[{f.category}_{per_category[f.category]}]"
            value_to_label[key] = label
            result.mapping[label] = f.value
        result.counts[f.category] += 1
        parts.append(text[pos:f.start])
        parts.append(value_to_label[key])
        pos = f.end
    parts.append(text[pos:])
    result.masked_text = "".join(parts)
    return result


def restore(text: str, mapping: dict[str, str]) -> str:
    """Вернуть исходные значения вместо меток. Неизвестные метки остаются как есть."""
    return _LABEL.sub(lambda m: mapping.get(m.group(), m.group()), text)
