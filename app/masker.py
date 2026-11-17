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


@dataclass(frozen=True)
class Span:
    """Где в исходном тексте стоит значение, заменённое меткой (позиции в символах Unicode)."""

    label: str
    category: str
    start: int
    end: int
    source: str = "rule"  # кто нашёл: rule / ai / rule+ai


@dataclass
class MaskResult:
    masked_text: str
    mapping: dict[str, str] = field(default_factory=dict)  # "[FIN_1]" -> исходное значение
    counts: Counter = field(default_factory=Counter)  # категория -> сколько найдено
    spans: list[Span] = field(default_factory=list)
    source_counts: Counter = field(default_factory=Counter)  # источник -> сколько найдено


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
        label = value_to_label[key]
        result.counts[f.category] += 1
        result.source_counts[f.source] += 1
        result.spans.append(Span(label, f.category, f.start, f.end, f.source))
        parts.append(text[pos:f.start])
        parts.append(label)
        pos = f.end
    parts.append(text[pos:])
    result.masked_text = "".join(parts)
    return result


@dataclass
class RestoreInfo:
    """Что случилось при восстановлении ответа AI (без самих значений)."""

    exact: int = 0  # меток в точной форме [AD_1]
    fuzzy: int = 0  # изменённых AI («Ad1», «[ad_1]», «AD-1») восстановлено по смыслу
    missing: list[str] = field(default_factory=list)  # меток из запроса, которых нет в ответе


# Любая похожая на метку запись: в скобках (допускаются пробелы, регистр, «_», «-», пробел) или «голая»
# (Ad1, AD_1, AD-1). Заменяем только то, что есть в таблице меток этого запроса: слово «abc1» не тронем.
_LOOSE_LABEL = re.compile(
    r"\[\s*([A-Za-z]+)\s*[_\- ]?\s*(\d+)\s*\]"
    r"|(?<![A-Za-z0-9])([A-Za-z]+)[_\-]?(\d+)(?![A-Za-z0-9])"
)


def restore_tolerant(text: str, mapping: dict[str, str]) -> tuple[str, RestoreInfo]:
    """Как restore(), но понимает метки, которые AI слегка исказил. Возвращает (текст, сведения)."""
    info = RestoreInfo()
    seen: set[str] = set()

    def repl(m: re.Match) -> str:
        cat = (m.group(1) or m.group(3)).upper()
        num = m.group(2) or m.group(4)
        label = f"[{cat}_{int(num)}]"
        if label not in mapping or (num != str(int(num))):  # неизвестная метка или «01»: не трогаем
            return m.group(0)
        seen.add(label)
        if m.group(0) == label:
            info.exact += 1
        else:
            info.fuzzy += 1
        return mapping[label]

    restored = _LOOSE_LABEL.sub(repl, text)
    info.missing = [label for label in mapping if label not in seen]
    return restored, info


def restore(text: str, mapping: dict[str, str]) -> str:
    """Вернуть исходные значения вместо меток. Неизвестные метки остаются как есть."""
    return _LABEL.sub(lambda m: mapping.get(m.group(), m.group()), text)
