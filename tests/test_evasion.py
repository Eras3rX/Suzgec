"""Защита от обхода детектора: разделители, двойники-буквы, регистр, невидимые символы.

Каждый способ обхода проверяется на каждом типе данных. Только синтетические данные.
Тесты идут по правилам (detect_rules): модель здесь ни при чём.
"""
import pytest

from app.detector import detect_rules, is_classified
from app.masker import mask


def make_iban() -> str:
    bban = "NABZ" + "00000000137010001944"
    digits = "".join(str(int(c, 36)) for c in bban + "AZ00")
    return f"AZ{98 - int(digits) % 97:02d}{bban}"


IBAN = make_iban()
CARD = "4111111111111111"  # публичный тестовый номер, проходит проверку Луна
FIN = "1A2B3C4"
ID_AZE = "AZE04512367"
ID_AA = "AA3456781"
PHONE_PLUS = "+994501234567"
PHONE_ZERO = "0501234567"
EMAIL = "elvin.test@example.az"
VOEN = "1402938475"

# Кириллица, похожая на латиницу
CYR_A, CYR_E, CYR_K, CYR_M, CYR_P, CYR_C, CYR_X = "\u0410", "\u0415", "\u041a", "\u041c", "\u0420", "\u0421", "\u0445"
ZW = "\u200b"  # невидимый пробел нулевой ширины


def spaced(s: str, sep: str = " ") -> str:
    return sep.join(s)


def groups(s: str, n: int, sep: str) -> str:
    return sep.join(s[i:i + n] for i in range(0, len(s), n))


def found(text: str) -> list[tuple[str, str]]:
    return [(f.category, f.value) for f in detect_rules(text)]


def assert_found(text: str, category: str, value: str):
    assert (category, value) in found(text), f"не найдено {category}={value!r} в {text!r}; есть: {found(text)}"


# ---------- 1. Пробелы ----------

SPACES = [
    ("FIN каждый символ", f"Код {spaced(FIN)} в анкете", "FIN", spaced(FIN)),
    ("FIN с подсказкой, куски", "FİN: 1A 2B 3C4 указан", "FIN", "1A 2B 3C4"),
    ("IBAN каждый символ", f"Счёт {spaced(IBAN)} оплатить", "IBAN", spaced(IBAN)),
    ("IBAN группы по 2", f"Счёт {groups(IBAN, 2, ' ')} оплатить", "IBAN", groups(IBAN, 2, " ")),
    ("карта каждая цифра", f"Карта {spaced(CARD)} заблокирована", "KART", spaced(CARD)),
    ("карта группы по 2", f"Карта {groups(CARD, 2, ' ')} заблокирована", "KART", groups(CARD, 2, " ")),
    ("телефон +994 каждая цифра", f"Тел {spaced(PHONE_PLUS[1:])} ", "TEL", spaced(PHONE_PLUS[1:])),
    ("телефон +994 с плюсом", "Тел + 9 9 4 5 0 1 2 3 4 5 6 7 звонить", "TEL", "+ 9 9 4 5 0 1 2 3 4 5 6 7"),
    ("телефон 0XX каждая цифра", f"Тел {spaced(PHONE_ZERO)} звонить", "TEL", spaced(PHONE_ZERO)),
    ("удостоверение AZE", f"Номер {spaced(ID_AZE)} выдан", "ID", spaced(ID_AZE)),
    ("удостоверение AA", f"Номер {spaced(ID_AA)} выдан", "ID", spaced(ID_AA)),
    ("email пробелы вокруг @ и точек", "Пишите elvin.test @ example . az срочно", "EMAIL", "elvin.test @ example . az"),
    ("VÖEN каждая цифра", f"VÖEN: {spaced(VOEN)}", "VOEN", spaced(VOEN)),
]

# ---------- 2. Дефисы ----------

HYPHENS = [
    ("FIN каждый символ", f"Код {spaced(FIN, '-')} в анкете", "FIN", spaced(FIN, "-")),
    ("FIN с подсказкой", "FİN: 1A-2B-3C4 указан", "FIN", "1A-2B-3C4"),
    ("IBAN группы по 4", f"Счёт {groups(IBAN, 4, '-')} оплатить", "IBAN", groups(IBAN, 4, "-")),
    ("карта группы", f"Карта {groups(CARD, 4, '-')} заблокирована", "KART", groups(CARD, 4, "-")),
    ("карта каждая цифра", f"Карта {spaced(CARD, '-')} заблокирована", "KART", spaced(CARD, "-")),
    ("телефон +994", "Тел +994-50-123-45-67 звонить", "TEL", "+994-50-123-45-67"),
    ("телефон 0XX", "Тел 050-123-45-67 звонить", "TEL", "050-123-45-67"),
    ("удостоверение AZE", "Номер AZE-04512367 выдан", "ID", "AZE-04512367"),
    ("удостоверение AA", "Номер A-A-3456781 выдан", "ID", "A-A-3456781"),
    ("VÖEN группы", "VÖEN 1402-938-475", "VOEN", "1402-938-475"),
]

# ---------- 3. Точки ----------

DOTS = [
    ("FIN с подсказкой", "FİN: 1A.2B.3C4 указан", "FIN", "1A.2B.3C4"),
    ("IBAN группы по 4", f"Счёт {groups(IBAN, 4, '.')} оплатить", "IBAN", groups(IBAN, 4, ".")),
    ("карта группы", f"Карта {groups(CARD, 4, '.')} заблокирована", "KART", groups(CARD, 4, ".")),
    ("телефон 0XX", "Тел 050.123.45.67 звонить", "TEL", "050.123.45.67"),
    ("телефон +994", "Тел +994.50.123.45.67 звонить", "TEL", "+994.50.123.45.67"),
    ("удостоверение AZE", "Номер AZE.04512367 выдан", "ID", "AZE.04512367"),
    ("email (at)(dot)", "Пишите elvin.test(at)example(dot)az срочно", "EMAIL", "elvin.test(at)example(dot)az"),
    ("email [at][dot]", "Пишите elvin.test [at] example [dot] az срочно", "EMAIL", "elvin.test [at] example [dot] az"),
    ("VÖEN группы", "VÖEN: 1402.938.475", "VOEN", "1402.938.475"),
]

# ---------- 4. Переносы строк ----------

NEWLINES = [
    ("FIN с подсказкой, значение на новой строке", "FİN:\n1A2B3C4", "FIN", "1A2B3C4"),
    ("FIN с подсказкой, по кускам", "FİN:\n1A\n2B\n3C4", "FIN", "1A\n2B\n3C4"),
    ("FIN каждый символ", f"Код {spaced(FIN, chr(10))} в анкете", "FIN", spaced(FIN, "\n")),
    ("IBAN группы по 4", f"Счёт\n{groups(IBAN, 4, chr(10))}\nоплатить", "IBAN", groups(IBAN, 4, "\n")),
    ("карта группы", f"Карта {groups(CARD, 4, chr(10))} заблокирована", "KART", groups(CARD, 4, "\n")),
    ("телефон 0XX", "Тел 050\n123\n45\n67 звонить", "TEL", "050\n123\n45\n67"),
    ("телефон +994", "Тел +994\n50\n123\n45\n67 звонить", "TEL", "+994\n50\n123\n45\n67"),
    ("удостоверение", "Номер AZE\n04512367 выдан", "ID", "AZE\n04512367"),
    ("email", "Пишите elvin.test@\nexample.az срочно", "EMAIL", "elvin.test@\nexample.az"),
    ("VÖEN на следующей строке", "VÖEN:\n1402938475", "VOEN", "1402938475"),
]

# ---------- 5. Двойники: кириллица и греческий вместо латиницы ----------

HOMOGLYPHS = [
    ("ID: кириллические А и Е", f"Номер {CYR_A}Z{CYR_E}04512367 выдан", "ID", f"{CYR_A}Z{CYR_E}04512367"),
    ("ID: целиком кириллица АА", f"Номер {CYR_A}{CYR_A}3456781 выдан", "ID", f"{CYR_A}{CYR_A}3456781"),
    ("ID: греческие Α и Ε", "Номер \u0391Z\u039504512367 выдан", "ID", "\u0391Z\u039504512367"),
    ("FIN: кириллические К, М, Р", f"Код 7{CYR_K}3{CYR_M}9{CYR_P}Q в анкете", "FIN", f"7{CYR_K}3{CYR_M}9{CYR_P}Q"),
    ("FIN: кириллические А и С", f"Код 1{CYR_A}2B3{CYR_C}4 в анкете", "FIN", f"1{CYR_A}2B3{CYR_C}4"),
    ("IBAN: кириллическая А", f"Счёт {CYR_A}Z{IBAN[2:5]}{CYR_A}{IBAN[6:]} оплатить", "IBAN", f"{CYR_A}Z{IBAN[2:5]}{CYR_A}{IBAN[6:]}"),
    ("email: кириллическая а", f"Пишите \u0430ysel@example.az срочно", "EMAIL", "\u0430ysel@example.az"),
    ("VÖEN: кириллическая Е в слове", f"V\u00d6{CYR_E}N: {VOEN}", "VOEN", VOEN),
    ("телефон: полноширинные цифры", "Тел \uff10\uff15\uff10 \uff11\uff12\uff13 \uff14\uff15 \uff16\uff17 звонить", "TEL", "\uff10\uff15\uff10 \uff11\uff12\uff13 \uff14\uff15 \uff16\uff17"),
    ("карта: арабско-индийские цифры", "Карта \u0664\u0661\u0661\u0661 1111 1111 1111 ok", "KART", "\u0664\u0661\u0661\u0661 1111 1111 1111"),
    ("имя: кириллическая Е в латинском имени", f"Заявка {CYR_E}lvin M\u0259mm\u0259dov принята", "AD", f"{CYR_E}lvin M\u0259mm\u0259dov"),
]

# ---------- 6. Смешанный регистр ----------

MIXED_CASE = [
    ("ID строчными", "Номер aze04512367 выдан", "ID", "aze04512367"),
    ("ID Capitalized", "Номер Aze04512367 выдан", "ID", "Aze04512367"),
    ("ID AA строчными", "Номер aa3456781 выдан", "ID", "aa3456781"),
    ("IBAN строчными", f"Счёт {IBAN.lower()} оплатить", "IBAN", IBAN.lower()),
    ("IBAN вперемешку", f"Счёт Az{IBAN[2:6]}{IBAN[6:10].lower()}{IBAN[10:]} оплатить", "IBAN", f"Az{IBAN[2:6]}{IBAN[6:10].lower()}{IBAN[10:]}"),
    ("FIN строчными с подсказкой", "fİn: 1a2b3c4 указан", "FIN", "1a2b3c4"),
    ("FIN вперемешку без подсказки", "Код 1a2B3c4 в анкете", "FIN", "1a2B3c4"),
    ("FIN строчными, куски, подсказка fin", "fin 1a-2b-3c4", "FIN", "1a-2b-3c4"),
    ("email ЗАГЛАВНЫМИ", "Пишите ELVIN.TEST@EXAMPLE.AZ срочно", "EMAIL", "ELVIN.TEST@EXAMPLE.AZ"),
    ("VÖEN строчными", f"voen: {VOEN}", "VOEN", VOEN),
    ("имя ЗАГЛАВНЫМИ", "Заявка ELVIN MƏMMƏDOV принята", "AD", "ELVIN MƏMMƏDOV"),
    ("имя ASCII-заглавными без İ", "Заявка ELVIN MAMMADOV принята", "AD", "ELVIN MAMMADOV"),
    ("имя строчными (имя + фамилия)", "заявка elvin məmmədov принята", "AD", "elvin məmmədov"),
    ("имя вперемешку", "Заявка eLvIn MəMmƏdOv принята", "AD", "eLvIn MəMmƏdOv"),
    ("имя с отчеством строчными", "заявка elvin məmmədov rəşid oğlu принята", "AD", "elvin məmmədov rəşid oğlu"),
]

# ---------- 7. Невидимые символы ----------

INVISIBLE = [
    ("ID с нулевой шириной", f"Номер AZE0451{ZW}2367 выдан", "ID", f"AZE0451{ZW}2367"),
    ("FIN с нулевой шириной", f"Код 1A2B{ZW}3C4 в анкете", "FIN", f"1A2B{ZW}3C4"),
    ("телефон с мягким переносом", "Тел +994 50 123\u00ad45 67 звонить", "TEL", "+994 50 123\u00ad45 67"),
    ("карта с нулевой шириной", f"Карта 4111{ZW}1111{ZW}1111{ZW}1111 ok", "KART", f"4111{ZW}1111{ZW}1111{ZW}1111"),
    ("email с нулевой шириной", f"Пишите elvin.test@exam{ZW}ple.az срочно", "EMAIL", f"elvin.test@exam{ZW}ple.az"),
    ("имя с нулевой шириной", f"Заявка El{ZW}vin Məmmədov принята", "AD", f"El{ZW}vin Məmmədov"),
]

# ---------- 8. Комбинации ----------

CYR_a, CYR_e = "а", "е"  # строчные кириллические а, е

COMBINED = [
    # кириллица + строчные буквы + пробелы между всеми символами
    ("ID: кириллица + строчные + пробелы", f"Номер {CYR_a} z {CYR_e} 0 4 5 1 2 3 6 7 выдан", "ID",
     f"{CYR_a} z {CYR_e} 0 4 5 1 2 3 6 7"),
    # IBAN строчными, группы через дефис, с переносом строки посередине
    ("IBAN: строчные + дефисы + перенос", "Счёт az21-nabz-0000-0000-\n1370-1000-1944 оплатить", "IBAN",
     "az21-nabz-0000-0000-\n1370-1000-1944"),
    # карта с невидимым символом внутри и пробелами между цифрами
    ("карта: невидимые + пробелы", f"Карта 4 1 1 1{ZW} 1 1 1 1 1 1 1 1 1 1 1 1 ok", "KART",
     f"4 1 1 1{ZW} 1 1 1 1 1 1 1 1 1 1 1 1"),
]


def run_cases(cases):
    return [pytest.param(text, cat, value, id=name) for name, text, cat, value in cases]


@pytest.mark.parametrize("text,category,value", run_cases(SPACES))
def test_split_by_spaces(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(HYPHENS))
def test_split_by_hyphens(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(DOTS))
def test_split_by_dots(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(NEWLINES))
def test_split_by_newlines(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(HOMOGLYPHS))
def test_lookalike_letters_and_digits(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(MIXED_CASE))
def test_mixed_case(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(INVISIBLE))
def test_invisible_characters(text, category, value):
    assert_found(text, category, value)


@pytest.mark.parametrize("text,category,value", run_cases(COMBINED))
def test_combined_tricks(text, category, value):
    assert_found(text, category, value)


# ---------- Гриф «Məxfi» / «Gizli» ----------

CLASSIFIED_EVASIONS = [
    "MƏXFİ", "məxfi", "mExFi", "MəXfİ", "MEXFI", "GİZLİ", "gizli", "gIzLi", "GIZLI",
    "M ə x f i", "G-i-z-l-i", "M.ə.x.f.i", "M\nəxfi"[:0] + "G\ni\nz\nl\ni",
    f"{CYR_M}əxfi",             # кириллическая М
    f"M\u0259{CYR_X}fi",        # кириллическая х
    "G\u0456zli",               # кириллическая і
    f"M{ZW}əxfi", "Gi\u00adzli",  # невидимые символы
]


@pytest.mark.parametrize("marker", CLASSIFIED_EVASIONS)
def test_classified_marker_evasions_are_blocked(marker):
    assert is_classified(f"Daxili sənəd. {marker}. Kənara çıxarmayın.")


@pytest.mark.parametrize("text", [
    "Gizlilik siyasəti yeniləndi.", "Məxfilik müqaviləsi imzalandı.", "Müxtəlif sənədlər.",
    "Hesabat hazırdır.", "Sürətli çatdırılma.",
])
def test_similar_but_harmless_words_are_not_blocked(text):
    assert not is_classified(text)


# ---------- Позиции и маскирование при обходе ----------


def test_positions_point_to_original_text_with_invisible_characters():
    text = f"Salam {ZW}AZE0451{ZW}2367 sonra"
    [f] = [f for f in detect_rules(text) if f.category == "ID"]
    assert text[f.start:f.end] == f"AZE0451{ZW}2367" == f.value


def test_masking_removes_split_and_disguised_data():
    text = (f"Mən {CYR_E}lvin M\u0259mm\u0259dov. {spaced(FIN, '-')}, "
            f"tel 050\n123\n45\n67, kart {groups(CARD, 4, '.')}.")
    masked = mask(text).masked_text
    assert masked == "Mən [AD_1]. [FIN_1], tel [TEL_1], kart [KART_1]."


# ---------- Ложные срабатывания: защита не должна маскировать обычный текст ----------

BENIGN = [
    "Tarix 05.07.2024 12 nəfər iştirak etdi.",
    "Tarix: 01.02.2024 10 20 30",
    "1 2 3 4 5 6 7 8 9 10 11 12",
    "Sifariş 1234 5678 9012 3456 deyil, qiymət 25 AZN.",  # 16 цифр, но не проходят проверку Луна
    "Kod AZE-2024 formasında yazılır.",
    "covid19 haqqında məlumat. abc1234 kodu.",
    "1. a 2. b 3. c 4. d siyahı",
    "Mən 3 gün 5 saat 2 dəqiqə gözlədim.",
    "foo @ bar saat 5 ilə görüşək. Sonra danışarıq.",
    "anar meyvəsi və elvin",  # одиночные имена строчными — не имя
    "Əlaqəli şöbə ilə danışın. Sürətli çatdırılma.",
    "Привет, это обычный текст без данных. Спасибо!",
]


@pytest.mark.parametrize("text", BENIGN)
def test_ordinary_text_is_not_flagged(text):
    assert found(text) == []


def test_two_phones_in_a_row_are_not_swallowed_by_a_card_pattern():
    text = "050 123 45 67 050 123 45 67"
    assert [c for c, _ in found(text)] == ["TEL", "TEL"]


def test_normal_formats_still_work_after_hardening():
    assert_found(f"Hesab {IBAN}", "IBAN", IBAN)
    assert_found(f"Karta {groups(CARD, 4, ' ')}", "KART", groups(CARD, 4, " "))
    assert_found(f"Tel {PHONE_PLUS}", "TEL", PHONE_PLUS)
    assert_found(f"FİN {FIN}", "FIN", FIN)
