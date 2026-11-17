"""Детектор азербайджанских персональных данных (гибрид: правила + локальная модель).

detect(text) -> список Finding (категория, позиции, значение, источник).
  Правила (регулярные выражения, контрольные суммы, словарь имён) находят FİN, IBAN, карты, телефоны…
  Локальная модель (app/ml_detector.py, если включена и загружена) добавляет имена, адреса, даты.
  Если модели нет, она отключена или не загрузилась, работают только правила.
detect_rules(text) -> только правила.
is_classified(text) -> True, если в тексте есть гриф «Məxfi» / «Gizli».

Защита от обхода (правила смотрят не на «как написано», а на «что это»):
  - невидимые символы (нулевой ширины, мягкий перенос) удаляются, позиции находок считаются по исходному тексту;
  - похожие буквы кириллицы и греческого (А, В, С, Е, Н, К, М, О, Р, Т, Х…), «широкие» буквы и цифры
    других алфавитов приводятся к латинице и обычным цифрам (длина текста при этом не меняется);
  - данные, разбитые пробелами, дефисами, точками, переносами строк, находятся с проверкой контрольной суммы
    (IBAN, карта) или формата (удостоверение, телефон, email, VÖEN);
  - регистр не важен (aze…, MƏXFİ, ELVIN MƏMMƏDOV). Исключение: FİN без контрольной суммы в нижнем
    регистре, с пробелами или точками принимается только по подсказке («FİN: …») или по очень характерному виду.
Находки из «ослабленных» шаблонов уступают обычным при пересечении.

Форматы упрощены и будут сверены с официальными источниками.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


SOURCE_RULE = "rule"
SOURCE_AI = "ai"
SOURCE_BOTH = "rule+ai"


@dataclass(frozen=True)
class Finding:
    category: str
    start: int
    end: int
    value: str
    source: str = SOURCE_RULE  # кто нашёл: "rule" (правило), "ai" (модель) или "rule+ai" (оба)


# При пересечении находок побеждает категория, стоящая раньше в списке.
# ADRES и TARIX находит только модель; правила их не знают.
PRIORITY = ["EMAIL", "IBAN", "KART", "ID", "VOEN", "TEL", "FIN", "AD", "ADRES", "TARIX"]
# Категории, в которых главная — модель. Остальные (структурированные) всегда решают правила.
MODEL_CATEGORIES = {"AD", "ADRES", "TARIX"}
_WEAK_PENALTY = 100  # «ослабленные» шаблоны (обход) уступают обычным

# ---------- Контрольные суммы ----------


def iban_valid(iban: str) -> bool:
    """AZ IBAN: 28 символов, проверка mod-97 (разделители игнорируются)."""
    iban = re.sub(r"[^A-Za-z0-9]", "", iban).upper()
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


# ---------- Нормализация (защита от обхода) ----------

# Буквы других алфавитов, неотличимые на глаз от латинских.
_HOMOGLYPHS = str.maketrans({
    # кириллица
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P",
    "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J", "Ѕ": "S", "Ү": "Y",
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y", "і": "i", "ј": "j",
    "ѕ": "s", "һ": "h", "ԁ": "d", "ӏ": "l",
    "Ә": "Ə", "ә": "ə",
    # греческие
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ο": "o", "ν": "v",
})


def _fold_char(ch: str) -> str:
    """Один символ -> один символ: широкие буквы/цифры -> обычные, двойники -> латиница."""
    if ord(ch) < 128:
        return ch
    nk = unicodedata.normalize("NFKC", ch)
    if len(nk) == 1:
        ch = nk
    if ch.isdigit() and not ("0" <= ch <= "9"):
        try:
            return str(unicodedata.digit(ch))
        except ValueError:
            pass
    return ch.translate(_HOMOGLYPHS)


def _normalize(text: str) -> tuple[str, str, list[int]]:
    """Вернуть (plain, folded, idx).

    plain  — текст без невидимых символов (нулевой ширины, мягкий перенос и т.п.);
    folded — то же, но двойники-буквы и цифры других алфавитов заменены латинскими/обычными (длина та же);
    idx[i] — позиция i-го символа normalized-текста в исходном тексте (idx[-1] == len(text)).
    """
    plain, folded, idx = [], [], []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":  # невидимые символы форматирования
            continue
        plain.append(ch)
        folded.append(_fold_char(ch))
        idx.append(i)
    idx.append(len(text))
    return "".join(plain), "".join(folded), idx


# ---------- Регулярные выражения ----------

# Разделители между символами одного значения: пробелы, переносы строк, точки, дефисы…
_G = r"[\s.\-_·•–—]{0,3}"    # от 0 до 3 разделителей
_G1 = r"[\s.\-_·•–—]{1,3}"   # хотя бы один
_GP = r"[\s\-_·•–—()]{0,3}"  # для телефонов: без точек (иначе дата 05.07.2024 похожа на номер)


def _spaced(parts: list[str], gap: str = _G) -> str:
    return gap.join(parts)


# Обычные (строгие) шаблоны
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

# Ослабленные шаблоны: те же данные, но разбитые разделителями или в другом регистре.
_IBAN_T = re.compile(
    r"(?<![A-Za-z0-9])" + _spaced(["A", "Z"] + [r"\d"] * 2 + ["[A-Z]"] * 4 + [r"\d"] * 20) + r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CARD_T = re.compile(
    r"(?<!\d)(?<!\d[\s.\-_])\d(?:" + _G + r"\d){15}(?!\d)(?!" + _G + r"\d)"
)
_ID_T = re.compile(
    r"(?<![A-Za-z0-9])(?:" + _spaced(["A", "Z", "E"] + [r"\d"] * 8) + "|" + _spaced(["A", "A"] + [r"\d"] * 7)
    + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_VOEN_T = re.compile(
    r"(?i:V[ÖO]EN)(?:-?\w{1,2})?[\s:№#\-]*(?:[^\W\d_]+[\s:№#\-]+){0,2}(\d(?:" + _G + r"\d){9})(?!\d)"
)
# «FİN: 1a-2b-3c4» — подсказка-слово перед значением (регистр не важен, допускается «ФИН»).
_FIN_CTX = re.compile(
    r"(?<![A-Za-z0-9])(?:F[İIıi]N|ФИН|ФИH)(?![A-Za-z0-9])[\s:№#.\-]{0,4}"
    r"(?P<v>[A-Za-z0-9](?:" + _G + r"[A-Za-z0-9]){6})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# Слитно, но не заглавными: 1a2B3c4 (без подсказки берём только если буквы и цифры часто чередуются).
_FIN_LOOSE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]{7}(?![A-Za-z0-9]))(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{7}"
)
# Все 7 символов разнесены разделителями: 1 A 2 B 3 C 4.
# Точки здесь не считаем разделителем: «1. a 2. b 3. c 4.» — это нумерованный список, а не FİN.
_GS1 = r"[\s\-_·•–—]{1,3}"
_FIN_SPLIT = re.compile(
    r"(?<![A-Za-z0-9])(?<![A-Za-z0-9][\s\-_·•–—])[A-Za-z0-9](?:" + _GS1 + r"[A-Za-z0-9]){6}"
    r"(?![A-Za-z0-9])(?!" + _GS1 + r"[A-Za-z0-9])"
)
_TLDS = "az|com|net|org|ru|info|edu|gov|io|co|tr|uk|de|biz|me|mail"
_AT = r"(?:@|\(\s*at\s*\)|\[\s*at\s*\])"
_DOT = r"(?:\.|\(\s*dot\s*\)|\[\s*dot\s*\])"
# elvin.test @ example . az, elvin(at)example(dot)az
_EMAIL_T = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9_%+\-]+(?:\.[A-Za-z0-9_%+\-]+)*\s*" + _AT + r"\s*"
    r"[A-Za-z0-9\-]+(?:\s*" + _DOT + r"\s*[A-Za-z0-9\-]+)*?\s*" + _DOT + r"\s*(?:" + _TLDS + r")(?![A-Za-z])",
    re.IGNORECASE,
)
_PH_PLUS = re.compile(
    r"(?<![\w+])\+" + _GP + _spaced(["9", "9", "4"] + [r"\d"] * 9, _GP) + r"(?!\d)(?!" + _GP + r"\d)"
)
_PH_ZERO = re.compile(
    r"(?<![\w+])(?<!\d[\s\-_])0(?:" + _GP + r"\d){9}(?!\d)(?!" + _GP + r"\d)"
)
# «994 50 123 45 67» без плюса: принимаем, только если код оператора настоящий.
_PH_994 = re.compile(
    r"(?<![\w+])(?<!\d[\s\-_])" + _spaced(["9", "9", "4"] + [r"\d"] * 9, _GP) + r"(?!\d)(?!" + _GP + r"\d)"
)
_PH_DOT_PLUS = re.compile(r"(?<![\w+.])\+994\.\d{2}\.\d{3}\.\d{2}\.\d{2}(?![\d.])")
_PH_DOT_ZERO = re.compile(r"(?<![\w+.])0\d{2}\.\d{3}\.\d{2}\.\d{2}(?![\d.])")
# Коды мобильных операторов и Баку; для номеров без «+994» чужие коды не принимаем.
_PHONE_CODES = {"10", "12", "50", "51", "55", "60", "70", "77", "99"}


def _cleaned(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s)


def _transitions(s: str) -> int:
    """Сколько раз в строке буква сменяется цифрой и наоборот."""
    return sum(1 for a, b in zip(s, s[1:]) if a.isdigit() != b.isdigit())


def _fin_shape_ok(token: str) -> bool:
    return len(token) == 7 and any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


# ---------- Гриф «Məxfi» / «Gizli» ----------

_CLS_FOLD = str.maketrans({"ə": "e", "Ə": "e", "ı": "i", "İ": "i", "I": "i"})


def _word_re(word: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + _G.join(word) + r"(?![a-z0-9])")


_CLASSIFIED = [_word_re("mexfi"), _word_re("gizli")]


def is_classified(text: str) -> bool:
    """Есть ли гриф «Məxfi» / «Gizli» — такой текст блокируется целиком.

    Регистр, двойники-буквы кириллицы, невидимые символы и разделители между буквами не мешают.
    """
    _, folded, _ = _normalize(text)
    f = folded.translate(_CLS_FOLD).lower()
    return any(p.search(f) for p in _CLASSIFIED)


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
_LATIN_LETTER = re.compile(r"[A-Za-zƏəİıÖöÜüŞşÇçĞğ]")
_OTHER_ALPHABET = re.compile(r"[Ͱ-ϿЀ-ԯ]")  # греческий и кириллица


def _az_key(word: str) -> str:
    """Ключ для сравнения имён: регистр и вариант «I/İ/ı» не важны (ELVIN = Elvin = elvin)."""
    return word.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()


_FIRST_KEYS = {_az_key(n) for n in FIRST_NAMES | FIRST_NAMES_CYR}
_PATRONYMIC_KEYS = {_az_key(w) for w in PATRONYMIC_WORDS}


def _fold_mixed(word: str) -> str:
    """Слово, где латинские буквы смешаны с кириллическими/греческими («Еlvin»), приводим к латинице."""
    if _LATIN_LETTER.search(word) and _OTHER_ALPHABET.search(word):
        return "".join(_fold_char(c) for c in word)
    return word


# Слова, которые стоят рядом с фамилией, но именем не являются: должности, обращения, части названий.
_NOT_NAME_WORDS = {
    _az_key(w)
    for w in (
        "Cənab", "Xanım", "Bəy", "Prezident", "Direktor", "Müəllim", "Doktor", "Professor", "Prof", "Akademik",
        "General", "Polkovnik", "Mühəndis", "Rəis", "Müdir", "Nazir", "Prospekti", "Küçəsi", "Meydanı",
        "Universiteti", "İnstitutu", "Şirkəti", "Məktəbi", "Господин", "Госпожа", "Директор", "Президент",
        "Профессор", "Доктор", "Академик", "Генерал", "Инженер", "Министр", "Улица", "Проспект",
        "Университет", "Институт", "Компания", "Школа", "Мистер", "Миссис",
    )
}
# Кириллические окончания фамилий: для них «подсказкой» служит заглавное слово слева (имя).
_CYR_SURNAME_ENDINGS = ("ов", "ова", "ев", "ева")


def _name_like(word: str) -> bool:
    """Похоже на имя: буквы, с заглавной, не аббревиатура."""
    return len(word) >= 2 and word.isalpha() and word[0].isupper() and not word.isupper()


def _sentence_start(text: str, pos: int) -> bool:
    """Слово в позиции pos стоит в начале предложения? (после двоеточия — нет: там часто начинается список)"""
    j = pos - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j < 0 or text[j] in ".!?…\n\r"


def _find_names(text: str) -> list[tuple[int, int]]:
    """Позиции ФИО (по словарю имён, суффиксам фамилий и соседству имени с фамилией)."""
    # 1. Помечаем каждое слово: first / strong / weak / patr (и lc — «в нижнем регистре») или None.
    toks: list[list] = []  # [начало, конец, слово, ключ, вид]
    prev_kind: str | None = None
    for m in _WORD.finditer(text):
        word = _fold_mixed(m.group())
        key = _az_key(word)
        cased = any(ch.isupper() for ch in word)  # есть заглавные: Elvin, ELVIN, eLvin
        kind: str | None = None
        if key in _FIRST_KEYS:
            kind = "first" if cased else "first_lc"
        elif len(word) >= MIN_SURNAME_LEN:
            if key.endswith(STRONG_SURNAME_SUFFIXES):
                kind = "strong" if cased else "strong_lc"
            elif cased and key.endswith(WEAK_SURNAME_SUFFIXES):
                kind = "weak"
        if kind is None and key in _PATRONYMIC_KEYS and prev_kind is not None:
            kind = "patr"  # «oğlu»/«qızı» бывают только после имени
        toks.append([m.start(), m.end(), word, key, kind])
        prev_kind = kind

    # 1б. Незнакомое имя прямо перед фамилией: «Али Алиев», «Maqa Əliyev». Слово слева с заглавной,
    #     не в начале предложения и не должность/обращение — считаем именем. Это же «включает»
    #     кириллическую фамилию на -ов/-ев, которой иначе нужно было бы имя из словаря.
    for i, t in enumerate(toks):
        if t[4] not in ("strong", "weak") or i == 0:
            continue
        c = toks[i - 1]
        if c[4] is not None or not _name_like(c[2]) or c[3] in _NOT_NAME_WORDS:
            continue
        if c[1] >= t[0] or text[c[1]:t[0]].strip(" \t") != "" or _sentence_start(text, c[0]):
            continue
        c[4] = "guess"
        if t[4] == "weak" and t[3].endswith(_CYR_SURNAME_ENDINGS):
            t[4] = "strong_ctx"

    # 2. Склеиваем соседние слова-имена (через пробел) в одно ФИО.
    groups: list[tuple[int, int, set[str]]] = []
    for start, end, _word, _key, kind in toks:
        if kind is None:
            continue
        if groups and start > groups[-1][1] and text[groups[-1][1]:start].strip(" \t") == "":
            g_start, _, kinds = groups[-1]
            groups[-1] = (g_start, end, kinds | {kind})
        else:
            groups.append((start, end, {kind}))

    # 3. ФИО должно содержать имя с заглавной буквой или фамилию. Всё в нижнем регистре
    #    принимаем только парой «имя + фамилия» (иначе обычные слова вроде «anar» — гранат).
    return [
        (s, e)
        for s, e, kinds in groups
        if kinds & {"first", "strong", "strong_ctx"} or {"first_lc", "strong_lc"} <= kinds
    ]


# ---------- Основные функции ----------

_Hit = tuple[str, int, int, bool]  # (категория, начало, конец, «ослабленный» шаблон?)


def _regex_hits(t: str) -> list[_Hit]:
    """Все находки шаблонов в нормализованном тексте t."""
    hits: list[_Hit] = []

    for m in _EMAIL.finditer(t):
        hits.append(("EMAIL", m.start(), m.end(), False))
    for m in _EMAIL_T.finditer(t):
        hits.append(("EMAIL", m.start(), m.end(), True))

    for m in _IBAN.finditer(t):
        if iban_valid(m.group()):
            hits.append(("IBAN", m.start(), m.end(), False))
    for m in _IBAN_T.finditer(t):
        if iban_valid(m.group()):
            hits.append(("IBAN", m.start(), m.end(), True))

    for m in _CARD.finditer(t):
        if luhn_valid(m.group()):
            hits.append(("KART", m.start(), m.end(), False))
    for m in _CARD_T.finditer(t):
        if luhn_valid(m.group()):
            hits.append(("KART", m.start(), m.end(), True))

    for m in _ID.finditer(t):
        hits.append(("ID", m.start(), m.end(), False))
    for m in _ID_T.finditer(t):
        hits.append(("ID", m.start(), m.end(), True))

    for m in _VOEN.finditer(t):
        hits.append(("VOEN", m.start(1), m.end(1), False))
    for m in _VOEN_T.finditer(t):
        hits.append(("VOEN", m.start(1), m.end(1), True))

    for m in _PHONE.finditer(t):
        hits.append(("TEL", m.start(), m.end(), False))
    for m in _PH_PLUS.finditer(t):
        hits.append(("TEL", m.start(), m.end(), True))
    for m in _PH_DOT_PLUS.finditer(t):
        hits.append(("TEL", m.start(), m.end(), True))
    for m in _PH_994.finditer(t):
        if re.sub(r"\D", "", m.group())[3:5] in _PHONE_CODES:
            hits.append(("TEL", m.start(), m.end(), True))
    for pattern in (_PH_ZERO, _PH_DOT_ZERO):
        for m in pattern.finditer(t):
            digits = re.sub(r"\D", "", m.group())
            if len(digits) == 10 and digits[1:3] in _PHONE_CODES:
                hits.append(("TEL", m.start(), m.end(), True))

    for m in _FIN.finditer(t):
        if not _SKU_LIKE.fullmatch(m.group()):
            hits.append(("FIN", m.start(), m.end(), False))
    for m in _FIN_CTX.finditer(t):
        if _fin_shape_ok(_cleaned(m.group("v"))):
            hits.append(("FIN", m.start("v"), m.end("v"), True))
    for m in _FIN_LOOSE.finditer(t):
        token = m.group()
        if _transitions(token) >= 3 and not token.isupper():
            hits.append(("FIN", m.start(), m.end(), True))
    for m in _FIN_SPLIT.finditer(t):
        token = _cleaned(m.group())
        if _fin_shape_ok(token) and _transitions(token) >= 3:
            hits.append(("FIN", m.start(), m.end(), True))
    return hits


def detect_rules(text: str) -> list[Finding]:
    """Только правила. Пересечения разрешаются по PRIORITY; «ослабленные» шаблоны уступают обычным."""
    plain, folded, idx = _normalize(text)
    hits = _regex_hits(folded) + [("AD", s, e, False) for s, e in _find_names(plain)]
    hits.sort(key=lambda h: (PRIORITY.index(h[0]) + (_WEAK_PENALTY if h[3] else 0), -(h[2] - h[1]), h[1]))
    accepted: list[_Hit] = []
    for hit in hits:
        if all(hit[2] <= a[1] or hit[1] >= a[2] for a in accepted):
            accepted.append(hit)
    accepted.sort(key=lambda h: h[1])
    findings = []
    for category, s, e, _ in accepted:
        start, end = idx[s], idx[e - 1] + 1  # обратно в позиции исходного текста
        findings.append(Finding(category, start, end, text[start:end]))
    return findings


# ---------- Гибрид: правила + модель ----------


def _overlaps(a: Finding, b: Finding) -> bool:
    return a.start < b.end and b.start < a.end


def _merge_same_category(items: list[Finding], text: str) -> list[Finding]:
    """Склеить пересекающиеся находки одной категории (для имён ещё и соседние через пробел)."""
    out: list[Finding] = []
    for f in sorted(items, key=lambda x: (x.category, x.start, x.end)):
        last = out[-1] if out else None
        touching = (
            last is not None
            and f.category == "AD"
            and last.category == "AD"
            and f.start >= last.end
            and text[last.end:f.start].strip(" \t") == ""
        )
        if last is not None and last.category == f.category and (f.start < last.end or touching):
            start, end = min(last.start, f.start), max(last.end, f.end)
            source = last.source if last.source == f.source else SOURCE_BOTH
            out[-1] = Finding(f.category, start, end, text[start:end], source)
        else:
            out.append(f)
    return out


def merge_findings(text: str, rule: list[Finding], ml: list[Finding]) -> list[Finding]:
    """Объединить результаты правил и модели и убрать пересечения.

    1. Структурированные данные (FİN, IBAN, карта…) всегда решают правила: модель с ними не спорит.
    2. Имена, адреса, даты: модель и правила дополняют друг друга. Одинаковые или пересекающиеся
       находки склеиваются (источник «rule+ai»). Если правило-имя пересекается с адресом или датой
       от модели, побеждает модель: она видит контекст.
    3. Оставшиеся пересечения разных категорий решаются по PRIORITY (длиннее — важнее).
    """
    structured = [f for f in rule if f.category not in MODEL_CATEGORIES]
    ml_kept = [m for m in ml if not any(_overlaps(m, s) for s in structured)]
    rule_names = [
        r for r in rule
        if r.category in MODEL_CATEGORIES
        and not any(_overlaps(r, m) and m.category != r.category for m in ml_kept)
    ]
    candidates = structured + _merge_same_category(ml_kept + rule_names, text)

    candidates.sort(key=lambda f: (PRIORITY.index(f.category), -(f.end - f.start), f.start))
    accepted: list[Finding] = []
    for cand in candidates:
        if all(cand.end <= a.start or cand.start >= a.end for a in accepted):
            accepted.append(cand)
    accepted.sort(key=lambda f: f.start)
    return accepted


def detect(text: str, use_model: bool | None = None) -> list[Finding]:
    """Все персональные данные: правила + локальная модель (если доступна).

    use_model: None — как настроено (по умолчанию модель включена, если загружена);
               False — только правила (например, для сравнения в eval).
    """
    rule = detect_rules(text)
    if use_model is False:
        return rule
    from app import ml_detector  # импорт здесь: модуль не тянет torch, пока модель не включена

    ml = ml_detector.predict(text)
    return merge_findings(text, rule, ml) if ml else rule
