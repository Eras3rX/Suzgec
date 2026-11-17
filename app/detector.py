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
_VOEN = re.compile(r"(?i:V[ÖO]EN)\s*[:№#\-]?\s*(\d{10})(?!\d)")
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
}
SURNAME_SUFFIXES = ("yeva", "yev", "ova", "ov", "li")
PATRONYMIC_WORDS = {"oğlu", "qızı"}
MIN_SURNAME_LEN = 5

_WORD = re.compile(r"\w+")


def _find_names(text: str) -> list[Finding]:
    # 1. Помечаем каждое слово: это часть имени или нет.
    tokens: list[tuple[int, int, bool]] = []
    prev_is_name = False
    for m in _WORD.finditer(text):
        word = m.group()
        is_name = False
        if word in FIRST_NAMES:
            is_name = True
        elif word[0].isupper() and len(word) >= MIN_SURNAME_LEN and word.endswith(SURNAME_SUFFIXES):
            is_name = True
        elif word in PATRONYMIC_WORDS and prev_is_name:
            is_name = True
        tokens.append((m.start(), m.end(), is_name))
        prev_is_name = is_name

    # 2. Склеиваем соседние слова-имена (через пробел) в одно ФИО.
    findings: list[Finding] = []
    group_start = group_end = None
    for start, end, is_name in tokens:
        if is_name and group_start is not None and text[group_end:start].strip(" \t") == "" and start > group_end:
            group_end = end
            continue
        if group_start is not None:
            findings.append(Finding("AD", group_start, group_end, text[group_start:group_end]))
            group_start = group_end = None
        if is_name:
            group_start, group_end = start, end
    if group_start is not None:
        findings.append(Finding("AD", group_start, group_end, text[group_start:group_end]))
    # Одиночные «oğlu»/«qızı» без имени рядом не бывают: они помечаются только после имени.
    return findings


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
