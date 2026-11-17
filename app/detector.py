"""Детектор азербайджанских персональных данных.

detect(text) -> список Finding (категория, позиции, значение).
is_classified(text) -> True, если в тексте есть гриф «Məxfi» / «Gizli».

Форматы упрощены и будут сверены с официальными источниками.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    category: str
    start: int
    end: int
    value: str


# При пересечении находок побеждает категория, стоящая раньше в списке.
PRIORITY = ["EMAIL", "IBAN", "KART", "ID", "VOEN", "TEL", "FIN", "AD"]

# ---------- Контрольные суммы ----------


def iban_valid(iban: str) -> bool:
    """AZ IBAN: 28 символов, проверка mod-97."""
    iban = iban.replace(" ", "").upper()
    if len(iban) != 28 or not iban.startswith("AZ"):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) != 16:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ---------- Регулярные выражения ----------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IBAN = re.compile(r"(?<![A-Za-z0-9])AZ\d{2} ?[A-Z]{4}(?: ?\d{4}){5}(?![A-Za-z0-9])")
_CARD = re.compile(r"(?<!\d)(?:\d{4}[ -]?){3}\d{4}(?!\d)")
_ID = re.compile(r"(?<![A-Za-z0-9])(?:AZE\d{8}|AA\d{7})(?![A-Za-z0-9])")
# VÖEN берём только рядом со словом VÖEN; значением считается сами 10 цифр.
# Допускаем «VÖEN-i:», «VÖEN организации:» — до двух слов между словом VÖEN и цифрами.
_VOEN = re.compile(
    r"(?i:V[ÖO]EN)(?:-?\w{1,2})?[\s:№#\-]*(?:[^\W\d_]+[\s:№#\-]+){0,2}(\d{10})(?!\d)"
)
# Артикулы вида ABC1234 (буквы, затем только цифры) — не FİN: слишком похоже на коды товаров.
_SKU_LIKE = re.compile(r"[A-Z]{1,4}\d{3,6}")
_PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"\+994[\s-]?\(?\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}"
    r"|0\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}"
    r")(?!\d)"
)
# FİN: ровно 7 символов A-Z/0-9, обязательно и буква, и цифра.
_FIN = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Z0-9]{7}(?![A-Za-z0-9]))(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{7}"
)
_CLASSIFIED = re.compile(r"(?<!\w)(?:[Mm][əƏ][xX][fF][iİıI]|[Gg][iİıI][zZ][lL][iİıI])(?!\w)")

# ---------- Имена ----------

FIRST_NAMES = {
    "Elvin", "Rəşad", "Aysel", "Nigar", "Əli", "Vüsal", "Leyla", "Kamran",
    "Günel", "Tural", "Səbinə", "Orxan", "Fidan", "Murad", "Ülviyyə", "Samir",
    "Nərmin", "Rauf", "Zaur", "Aynur", "Emin", "Fərid", "Gülnar", "Həsən",
    "Hüseyn", "Cavid", "Ramil", "Ruslan", "Sevinc", "Turan", "Xədicə", "Yusif",
    "Mehriban", "Anar", "Elnur", "Nurlan", "Kənan", "İlqar", "Rəşid", "Məmməd",
    "Aygün", "Arzu", "Səbuhi", "Eldar", "Fuad", "Könül", "Lamiyə", "Nailə",
    "Pərvin", "Ramiz", "Səidə", "Tahir", "Vüqar", "Zəhra", "Elçin", "Namiq",
    "Mahir", "Rəsul", "Fərhad", "Cəmil", "Şahin", "Nicat", "Rövşən", "Vaqif",
    "Kamil", "Ələsgər", "Əhməd", "Əziz", "Bəxtiyar", "Cəlal", "Mübariz",
    "Natiq", "Oqtay", "Rafiq", "Sabir", "Teymur", "Ülvi", "Vahid", "Yaşar",
    "İlkin", "Elşən", "Ayşən", "Gülşən", "Mətanət", "Nərgiz", "Sevda", "Təranə",
    "Ülkər", "İradə", "Səadət", "Lalə", "Məryəm", "Aytən",
}
# То же для русского текста (кириллица).
FIRST_NAMES_CYR = {
    "Руслан", "Эльвин", "Айсель", "Нигяр", "Фидан", "Тураль", "Орхан", "Рашад",
    "Самир", "Лейла", "Камран", "Фарид", "Мурад", "Анар", "Эльнур", "Заур",
    "Эмин", "Расим", "Гюнель", "Айгюн", "Мехрибан", "Кямран", "Тахир",
}
# Сильные суффиксы: слово с таким окончанием считаем фамилией и без имени рядом.
STRONG_SURNAME_SUFFIXES = ("yeva", "yev", "ova", "ov")
# Слабые: слишком часто встречаются в обычных словах («Əlaqəli», «Сурков»…),
# поэтому считаем фамилией, только если рядом стоит имя или сильная фамилия.
WEAK_SURNAME_SUFFIXES = ("li", "ова", "ов", "ева", "ев")
PATRONYMIC_WORDS = {"oğlu", "qızı", "оглы", "кызы"}
MIN_SURNAME_LEN = 5

_WORD = re.compile(r"\w+")


def _find_names(text: str) -> list[Finding]:
    # 1. Помечаем каждое слово: first / strong / weak / patr — или None (не имя).
    tokens: list[tuple[int, int, str | None]] = []
    prev_kind: str | None = None
    for m in _WORD.finditer(text):
        word = m.group()
        kind: str | None = None
        if word in FIRST_NAMES or word in FIRST_NAMES_CYR:
            kind = "first"
        elif word[0].isupper() and len(word) >= MIN_SURNAME_LEN:
            if word.endswith(STRONG_SURNAME_SUFFIXES):
                kind = "strong"
            elif word.endswith(WEAK_SURNAME_SUFFIXES):
                kind = "weak"
        elif word in PATRONYMIC_WORDS and prev_kind is not None:
            kind = "patr"  # «oğlu»/«qızı» бывают только после имени
        tokens.append((m.start(), m.end(), kind))
        prev_kind = kind

    # 2. Склеиваем соседние слова-имена (через пробел) в одно ФИО.
    groups: list[tuple[int, int, set[str]]] = []
    for start, end, kind in tokens:
        if kind is None:
            continue
        if groups and start > groups[-1][1] and text[groups[-1][1]:start].strip(" \t") == "":
            g_start, _, kinds = groups[-1]
            groups[-1] = (g_start, end, kinds | {kind})
        else:
            groups.append((start, end, {kind}))

    # 3. ФИО должно содержать имя из словаря или сильную фамилию.
    return [
        Finding("AD", s, e, text[s:e])
        for s, e, kinds in groups
        if kinds & {"first", "strong"}
    ]


# ---------- Основные функции ----------


def _regex_findings(text: str) -> list[Finding]:
    found: list[Finding] = []
    for m in _EMAIL.finditer(text):
        found.append(Finding("EMAIL", m.start(), m.end(), m.group()))
    for m in _IBAN.finditer(text):
        if iban_valid(m.group()):
            found.append(Finding("IBAN", m.start(), m.end(), m.group()))
    for m in _CARD.finditer(text):
        if luhn_valid(m.group()):
            found.append(Finding("KART", m.start(), m.end(), m.group()))
    for m in _ID.finditer(text):
        found.append(Finding("ID", m.start(), m.end(), m.group()))
    for m in _VOEN.finditer(text):
        found.append(Finding("VOEN", m.start(1), m.end(1), m.group(1)))
    for m in _PHONE.finditer(text):
        found.append(Finding("TEL", m.start(), m.end(), m.group()))
    for m in _FIN.finditer(text):
        if not _SKU_LIKE.fullmatch(m.group()):
            found.append(Finding("FIN", m.start(), m.end(), m.group()))
    return found


def detect(text: str) -> list[Finding]:
    """Найти все персональные данные. Пересечения разрешаются по PRIORITY."""
    candidates = _regex_findings(text) + _find_names(text)
    candidates.sort(key=lambda f: (PRIORITY.index(f.category), -(f.end - f.start), f.start))
    accepted: list[Finding] = []
    for cand in candidates:
        if all(cand.end <= a.start or cand.start >= a.end for a in accepted):
            accepted.append(cand)
    accepted.sort(key=lambda f: f.start)
    return accepted


def is_classified(text: str) -> bool:
    """Есть ли гриф «Məxfi» / «Gizli» — такой текст блокируется целиком."""
    return _CLASSIFIED.search(text) is not None
