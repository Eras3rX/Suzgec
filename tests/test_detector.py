"""Тесты детектора. Только синтетические данные — никаких реальных людей."""
from app.detector import detect, is_classified, iban_valid, luhn_valid


def make_iban(bank: str = "NABZ", account: str = "00000000137010001944") -> str:
    """Собрать синтетический AZ IBAN с правильной контрольной суммой."""
    bban = bank + account
    digits = "".join(str(int(c, 36)) for c in bban + "AZ00")
    check = 98 - int(digits) % 97
    return f"AZ{check:02d}{bban}"


def cats(text: str) -> list[str]:
    return [f.category for f in detect(text)]


def values(text: str, category: str) -> list[str]:
    return [f.value for f in detect(text) if f.category == category]


def test_fin_found():
    assert values("FİN: 1A2B3C4 указан в анкете", "FIN") == ["1A2B3C4"]


def test_fin_ignores_plain_words_and_numbers():
    assert cats("ALMASTA 1234567 abc1234") == []


def test_id_card_both_formats():
    assert values("AZE12345678 и AA1234567", "ID") == ["AZE12345678", "AA1234567"]


def test_voen_only_with_context():
    assert values("VÖEN: 1234567890", "VOEN") == ["1234567890"]
    assert values("VOEN 1234567890", "VOEN") == ["1234567890"]
    assert "VOEN" not in cats("номер заказа 1234567890")


def test_iban_valid_checksum():
    iban = make_iban()
    assert iban_valid(iban)
    assert values(f"Hesab: {iban}", "IBAN") == [iban]


def test_iban_with_spaces():
    iban = make_iban()
    spaced = " ".join(iban[i : i + 4] for i in range(0, 28, 4))
    assert values(f"IBAN {spaced}.", "IBAN") == [spaced]


def test_iban_wrong_checksum_rejected():
    iban = make_iban()
    broken = iban[:2] + ("00" if iban[2:4] != "00" else "01") + iban[4:]
    assert not iban_valid(broken)
    assert "IBAN" not in cats(broken)


def test_card_luhn():
    assert luhn_valid("4111 1111 1111 1111")
    assert values("Karta: 4111 1111 1111 1111", "KART") == ["4111 1111 1111 1111"]
    assert "KART" not in cats("Karta: 4111 1111 1111 1112")


def test_phones():
    assert values("+994 50 123 45 67", "TEL") == ["+994 50 123 45 67"]
    assert values("zəng edin: 050 123 45 67", "TEL") == ["050 123 45 67"]
    assert values("+994501234567", "TEL") == ["+994501234567"]


def test_email():
    assert values("yazın test.user@example.com ünvanına", "EMAIL") == ["test.user@example.com"]


def test_names_with_surname_suffix():
    assert values("Müəllim Elvin Məmmədov gəldi", "AD") == ["Elvin Məmmədov"]
    assert values("Nigar Əliyeva və Samir Hüseynli", "AD") == ["Nigar Əliyeva", "Samir Hüseynli"]


def test_name_with_patronymic():
    assert values("Elvin Məmmədov Rəşid oğlu", "AD") == ["Elvin Məmmədov Rəşid oğlu"]
    assert values("Aysel Əliyeva Rəşad qızı", "AD") == ["Aysel Əliyeva Rəşad qızı"]


def test_plain_text_has_no_findings():
    assert detect("Bu gün hava yaxşıdır, iclas saat üçdədir.") == []


def test_classified_marker():
    assert is_classified("Bu sənəd Məxfi saxlanılmalıdır")
    assert is_classified("GİZLİ — yalnız daxili istifadə üçün")
    assert not is_classified("Bu açıq məlumatdır")


def test_positions_match_values():
    text = "Mail: test.user@example.com, tel 050 123 45 67"
    for f in detect(text):
        assert text[f.start : f.end] == f.value


def test_overlap_iban_wins_over_digits():
    iban = make_iban()
    result = detect(f"Hesab {iban}")
    assert [f.category for f in result] == ["IBAN"]


# ---------- Регрессии по замеру точности (tests/eval.py) ----------


def test_voen_with_suffix_or_words_before_number():
    assert values("Şirkətin VÖEN-i: 1402938475 -dir.", "VOEN") == ["1402938475"]
    assert values("VÖEN организации: 1700456231.", "VOEN") == ["1700456231"]


def test_sku_like_code_is_not_fin():
    assert "FIN" not in cats("Артикул ABC1234 есть на складе.")


def test_weak_surname_suffix_alone_is_not_a_name():
    assert "AD" not in cats("Əlaqəli şöbə ilə danışın.")
    assert "AD" not in cats("Sürətli çatdırılma iki gün çəkir.")


def test_cyrillic_name():
    assert values("Заявление подал Руслан Гасанов.", "AD") == ["Руслан Гасанов"]
