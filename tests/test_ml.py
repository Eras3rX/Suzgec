"""Тесты гибрида «правила + локальная модель».

В обычных тестах настоящая модель НЕ загружается (она тяжёлая): вместо неё подставляется объект с методом
predict(). Проверка на настоящей модели включается вручную: PII_MODEL_TESTS=1 pytest tests/test_ml.py
"""
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import ml_detector
from app.detector import detect, detect_rules, merge_findings
from app.main import app
from app.masker import mask

client = TestClient(app)


def ent(text, label, value, score=0.99, nth=0):
    """Сущность в формате модели; позиции ищем в тексте."""
    start = -1
    for _ in range(nth + 1):
        start = text.index(value, start + 1)
    return {"label": label, "text": value, "start": start, "end": start + len(value), "score": score}


@pytest.fixture
def fake_model(monkeypatch):
    """Включить «готовую» модель, которая отвечает функцией fn(text) -> список сущностей."""

    def install(fn):
        monkeypatch.setenv("PII_MODEL_ENABLED", "1")
        monkeypatch.setitem(ml_detector._state, "status", "ready")
        monkeypatch.setitem(ml_detector._state, "ner", SimpleNamespace(predict=fn))

    return install


def pairs(findings):
    return [(f.category, f.value, f.source) for f in findings]


# ---------- Перевод меток модели в наши категории ----------


def test_given_name_and_surname_become_one_name():
    t = "Salam, mən Rəşad Vəliyev."
    out = ml_detector._to_findings(t, [ent(t, "GIVENNAME", "Rəşad"), ent(t, "SURNAME", "Vəliyev")])
    assert pairs(out) == [("AD", "Rəşad Vəliyev", "ai")]


def test_address_parts_merge_into_one_address():
    t = "Ünvan: Bakı şəhəri, Nizami küçəsi 45, mənzil 12."
    ents = [ent(t, "CITY", "Bakı"), ent(t, "STREET", "Nizami küçəsi"),
            ent(t, "BUILDINGNUM", "45"), ent(t, "BUILDINGNUM", "12")]
    assert pairs(ml_detector._to_findings(t, ents)) == [("ADRES", "Bakı şəhəri, Nizami küçəsi 45, mənzil 12", "ai")]


@pytest.mark.parametrize("label,value", [("CITY", "Bakı"), ("STREET", "Nizami küçəsi"), ("BUILDINGNUM", "45")])
def test_lone_address_part_is_not_an_address(label, value):
    t = f"Mətn {value} davamı."
    assert ml_detector._to_findings(t, [ent(t, label, value)]) == []


def test_city_with_building_is_an_address():
    t = "Sumqayıt, 15"
    ents = [ent(t, "CITY", "Sumqayıt"), ent(t, "BUILDINGNUM", "15")]
    assert [f.category for f in ml_detector._to_findings(t, ents)] == ["ADRES"]


def test_street_and_city_without_a_number_is_not_an_address():
    t = "Heydər Əliyev prospekti Bakı ən uzun küçədir."
    ents = [ent(t, "STREET", "Heydər Əliyev prospekti"), ent(t, "CITY", "Bakı")]
    assert ml_detector._to_findings(t, ents) == []


def test_street_with_building_number_is_an_address():
    t = "Nizami küçəsi 45"
    ents = [ent(t, "STREET", "Nizami küçəsi"), ent(t, "BUILDINGNUM", "45")]
    assert pairs(ml_detector._to_findings(t, ents)) == [("ADRES", "Nizami küçəsi 45", "ai")]


def test_russian_common_words_are_not_names_but_surnames_are():
    t = "Рабочее время. Номер. Алиев."
    ents = [ent(t, "GIVENNAME", "время", 0.997), ent(t, "GIVENNAME", "Номер", 0.888), ent(t, "SURNAME", "Алиев", 0.886)]
    assert pairs(ml_detector._to_findings(t, ents)) == [("AD", "Алиев", "ai")]


def test_uppercase_abbreviation_is_not_a_name_unless_model_is_sure():
    t = "Переведите на IBAN или на FIDAN."
    assert ml_detector._to_findings(t, [ent(t, "GIVENNAME", "IBAN", 0.666)]) == []
    assert len(ml_detector._to_findings(t, [ent(t, "GIVENNAME", "FIDAN", 0.95)])) == 1


def test_implausible_date_is_dropped():
    t = "İclas protokolu № 45/2024 imzalanıb. Doğum 3 mart 1985."
    ents = [ent(t, "DATE", "45/2024", 0.888), ent(t, "DATE", "3 mart 1985")]
    assert pairs(ml_detector._to_findings(t, ents)) == [("TARIX", "3 mart 1985", "ai")]


def test_date_becomes_tarix_and_other_labels_are_ignored():
    t = "Doğum tarixi 12.05.1990, yaş 34, saat 10.45, tel 050 123 45 67"
    ents = [ent(t, "DATE", "12.05.1990"), ent(t, "AGE", "34"), ent(t, "TIME", "10.45"),
            ent(t, "TELEPHONENUM", "050 123 45 67")]
    assert pairs(ml_detector._to_findings(t, ents)) == [("TARIX", "12.05.1990", "ai")]


def test_low_confidence_is_dropped(monkeypatch):
    t = "Salam Aytac"
    monkeypatch.setenv("PII_MODEL_MIN_SCORE", "0.6")
    assert ml_detector._to_findings(t, [ent(t, "GIVENNAME", "Aytac", score=0.5)]) == []
    assert len(ml_detector._to_findings(t, [ent(t, "GIVENNAME", "Aytac", score=0.7)])) == 1


def test_categories_can_be_narrowed_by_env(monkeypatch):
    t = "Aytac, doğum tarixi 12.05.1990"
    ents = [ent(t, "GIVENNAME", "Aytac"), ent(t, "DATE", "12.05.1990")]
    monkeypatch.setenv("PII_MODEL_CATEGORIES", "AD,ADRES")
    assert [f.category for f in ml_detector._to_findings(t, ents)] == ["AD"]


# ---------- Объединение правил и модели ----------


def test_hybrid_combines_both_and_marks_sources(fake_model):
    t = "Müştəri Aytac Hüseynova, FİN 5ARZ8D2, ünvan Bakı şəhəri, Nizami küçəsi 45."
    fake_model(lambda text: [
        ent(text, "GIVENNAME", "Aytac"), ent(text, "SURNAME", "Hüseynova"),
        ent(text, "CITY", "Bakı"), ent(text, "STREET", "Nizami küçəsi"), ent(text, "BUILDINGNUM", "45"),
    ])
    assert pairs(detect(t)) == [
        ("AD", "Aytac Hüseynova", "rule+ai"),  # «Hüseynova» знают и правила, «Aytac» только модель
        ("FIN", "5ARZ8D2", "rule"),
        ("ADRES", "Bakı şəhəri, Nizami küçəsi 45", "ai"),
    ]
    assert pairs(detect(t, use_model=False)) == [("AD", "Hüseynova", "rule"), ("FIN", "5ARZ8D2", "rule")]


def test_model_only_name_is_found_when_rules_do_not_know_it(fake_model):
    t = "Toğrul bəy iclasa gecikdi."
    assert detect_rules(t) == []
    fake_model(lambda text: [ent(text, "GIVENNAME", "Toğrul")])
    assert pairs(detect(t)) == [("AD", "Toğrul", "ai")]


def test_rules_win_over_model_on_structured_data(fake_model):
    t = "FİN 5ARZ8D2 və tel 050 123 45 67"
    fake_model(lambda text: [ent(text, "GIVENNAME", "5ARZ8D2"), ent(text, "SURNAME", "050")])
    assert pairs(detect(t)) == [("FIN", "5ARZ8D2", "rule"), ("TEL", "050 123 45 67", "rule")]


def test_model_address_beats_rule_name_inside_it(fake_model):
    t = "Ünvan: Nigar küçəsi 12, Bakı."
    assert [f.category for f in detect_rules(t)] == ["AD"]  # правила видят в «Nigar» имя
    fake_model(lambda text: [ent(text, "STREET", "Nigar küçəsi"), ent(text, "BUILDINGNUM", "12")])
    assert pairs(detect(t)) == [("ADRES", "Nigar küçəsi 12", "ai")]


def test_merge_findings_joins_the_same_name_from_both_sources():
    t = "Elvin Məmmədov"
    ml = ml_detector._to_findings(t, [ent(t, "GIVENNAME", "Elvin"), ent(t, "SURNAME", "Məmmədov")])
    out = merge_findings(t, detect_rules(t), ml)
    assert pairs(out) == [("AD", "Elvin Məmmədov", "rule+ai")]


def test_mask_labels_and_sources_follow_hybrid(fake_model):
    t = "Müştəri Toğrul, FİN 5ARZ8D2, doğum 12.05.1990"
    fake_model(lambda text: [ent(text, "GIVENNAME", "Toğrul"), ent(text, "DATE", "12.05.1990")])
    result = mask(t)
    assert result.masked_text == "Müştəri [AD_1], FİN [FIN_1], doğum [TARIX_1]"
    assert dict(result.source_counts) == {"ai": 2, "rule": 1}
    assert result.mapping == {"[AD_1]": "Toğrul", "[FIN_1]": "5ARZ8D2", "[TARIX_1]": "12.05.1990"}


# ---------- Если модели нет или она ломается, сайт работает на правилах ----------


def test_disabled_model_changes_nothing(monkeypatch):
    monkeypatch.setenv("PII_MODEL_ENABLED", "0")
    assert ml_detector.status() == "off"
    assert ml_detector.predict("Toğrul bəy") == []
    assert ml_detector.load_blocking() == "off"


def test_missing_model_files_fall_back_to_rules(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PII_MODEL_ENABLED", "1")
    monkeypatch.setenv("PII_MODEL_PATH", str(tmp_path / "no-such-dir"))
    monkeypatch.setitem(ml_detector._state, "status", None)
    monkeypatch.setitem(ml_detector._state, "ner", None)
    caplog.set_level(logging.DEBUG)
    assert ml_detector.load_blocking() == "unavailable"
    text = "Elvin Məmmədov, FİN 5ARZ8D2"
    assert detect(text) == detect_rules(text)  # правила работают как раньше
    assert "no-such-dir" not in caplog.text  # причина — только тип ошибки, без путей и текста


def test_broken_model_files_fall_back_to_rules(monkeypatch, tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"not a model")
    (tmp_path / "pii_ner.py").write_text("raise MemoryError('out of memory')")
    monkeypatch.setenv("PII_MODEL_ENABLED", "1")
    monkeypatch.setenv("PII_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # проверим, что загрузчик принудительно включит офлайн
    monkeypatch.setitem(ml_detector._state, "status", None)
    monkeypatch.setitem(ml_detector._state, "ner", None)
    assert ml_detector.load_blocking() == "unavailable"
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert detect("FİN 5ARZ8D2") == detect_rules("FİN 5ARZ8D2")


def test_model_crash_on_a_request_falls_back_and_hides_text(fake_model, caplog):
    secret = "Toğrul Məmmədov"

    def boom(text):
        raise RuntimeError("сбой на тексте: " + text)

    fake_model(boom)
    caplog.set_level(logging.DEBUG)
    assert detect(f"Salam {secret}") == detect_rules(f"Salam {secret}")
    assert secret not in caplog.text and "RuntimeError" in caplog.text


def test_loading_model_does_not_block_requests(monkeypatch):
    """Пока модель грузится, запросы обрабатываются правилами."""
    monkeypatch.setenv("PII_MODEL_ENABLED", "1")
    monkeypatch.setitem(ml_detector._state, "status", "loading")
    monkeypatch.setitem(ml_detector._state, "ner", None)
    assert ml_detector.status() == "loading"
    assert ml_detector.predict("Toğrul bəy") == []


def test_model_module_has_no_network_calls():
    """Модуль не должен обращаться к сети: только офлайн-режим и локальная папка."""
    source = Path(ml_detector.__file__).read_text(encoding="utf-8")
    for forbidden in ("import requests", "urllib", "snapshot_download", "from_pretrained", "http://", "https://"):
        assert forbidden not in source, forbidden


# ---------- API ----------


def test_api_reports_model_status_and_sources(fake_model):
    fake_model(lambda text: [ent(text, "GIVENNAME", "Toğrul")])
    assert client.get("/api/config").json()["model"] == "ready"

    text = "Müştəri Toğrul, FİN 5ARZ8D2"
    found = client.post("/api/check", json={"text": text}).json()["findings"]
    assert [(f["category"], f["source"]) for f in found] == [("AD", "ai"), ("FIN", "rule")]

    body = client.post("/api/mask", json={"text": text}).json()
    assert [(s["label"], s["source"]) for s in body["spans"]] == [("[AD_1]", "ai"), ("[FIN_1]", "rule")]
    assert body["masked_text"] == "Müştəri [AD_1], FİN [FIN_1]"
    assert "Toğrul" not in str(body)  # значения по-прежнему не возвращаются

    stats = client.get("/api/stats").json()
    assert stats["sources"]["ai"] >= 1 and stats["sources"]["rule"] >= 1
    assert "Toğrul" not in str(stats)


# ---------- Настоящая модель (вручную) ----------

REAL = os.getenv("PII_MODEL_TESTS") == "1"


@pytest.mark.skipif(not REAL, reason="настоящая модель: PII_MODEL_TESTS=1 pytest tests/test_ml.py")
def test_real_model_finds_names_addresses_and_dates(monkeypatch):
    monkeypatch.setenv("PII_MODEL_ENABLED", "1")
    monkeypatch.setitem(ml_detector._state, "status", None)
    monkeypatch.setitem(ml_detector._state, "ner", None)
    assert ml_detector.load_blocking() == "ready"
    text = ("Salam, mən Aytac Hüseynova. Ünvan: Bakı şəhəri, Nizami küçəsi 45, mənzil 12. "
            "Doğum tarixi: 12.05.1990.")
    found = {(f.category, f.value) for f in detect(text)}
    assert ("AD", "Aytac Hüseynova") in found
    assert ("ADRES", "Bakı şəhəri, Nizami küçəsi 45, mənzil 12") in found
    assert ("TARIX", "12.05.1990") in found
    assert detect("Bu gün hava gözəldir. Bakı paytaxtdır.") == []
