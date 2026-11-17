"""Тесты маскирования и восстановления (синтетические данные)."""
from app.masker import mask, restore


def test_mask_replaces_values_with_labels():
    text = "Elvin Məmmədov, FİN 1A2B3C4, tel +994 50 123 45 67"
    result = mask(text)
    assert "1A2B3C4" not in result.masked_text
    assert "Məmmədov" not in result.masked_text
    assert "[AD_1]" in result.masked_text
    assert "[FIN_1]" in result.masked_text
    assert "[TEL_1]" in result.masked_text


def test_roundtrip_restores_original():
    text = "Elvin Məmmədov, FİN 1A2B3C4, mail test.user@example.com"
    result = mask(text)
    assert restore(result.masked_text, result.mapping) == text


def test_same_value_gets_same_label():
    result = mask("FİN 1A2B3C4 ... təkrar 1A2B3C4")
    assert result.masked_text.count("[FIN_1]") == 2
    assert "[FIN_2]" not in result.masked_text
    assert result.counts["FIN"] == 2


def test_different_values_get_different_labels():
    result = mask("1A2B3C4 və 5D6E7F8")
    assert "[FIN_1]" in result.masked_text and "[FIN_2]" in result.masked_text


def test_restore_keeps_unknown_labels():
    assert restore("Salam [AD_9]", {}) == "Salam [AD_9]"


def test_restore_in_llm_answer():
    result = mask("Mənim adım Aysel Əliyeva")
    answer = "Salam, [AD_1]! Sizə necə kömək edim?"
    assert restore(answer, result.mapping) == "Salam, Aysel Əliyeva! Sizə necə kömək edim?"


def test_spans_point_to_original_values():
    text = "Elvin Məmmədov, FİN 1A2B3C4, yenə 1A2B3C4"
    result = mask(text)
    assert [(s.label, text[s.start:s.end]) for s in result.spans] == [
        ("[AD_1]", "Elvin Məmmədov"),
        ("[FIN_1]", "1A2B3C4"),
        ("[FIN_1]", "1A2B3C4"),
    ]
    assert all(result.mapping[s.label] == text[s.start:s.end] for s in result.spans)
