"""Грифы секретности: распознавание, полная блокировка, журнал инцидентов, панель администратора.

Только синтетические данные. Секретная строка SECRET_DOC в тестах играет роль «текста документа»:
нигде, кроме запроса, она появляться не должна.
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.detector import CLASSIFICATION_NAMES, classify, is_classified
from app.incidents import MAX_INCIDENTS, IncidentLog
from app.main import app

client = TestClient(app)                                        # чужой компьютер для панели администратора
local = TestClient(app, client=("127.0.0.1", 50000))            # этот же компьютер

SECRET_DOC = "ZZ-SECRET-DOC-7781"
ZW = "​"
CYR_M, CYR_E, CYR_X, CYR_I = "М", "е", "х", "і"


@pytest.fixture(autouse=True)
def clean_state():
    main.incidents.clear()
    yield
    main.incidents.clear()


# ---------- 1. Распознавание: каждый гриф × каждый способ написания ----------

VARIANTS = {
    "tam_mexfi": [
        "Tam məxfi", "TAM MƏXFİ", "tam məxfi", "tAm MəXfİ", "Tam mexfi", "TAM MEXFI", "tam mexfi",
        "T a m  m ə x f i", "Tam-məxfi", "Tam\nməxfi", "Tam   məxfi", "T.a.m. m.ə.x.f.i.",
        f"Tam M{ZW}əxfi", f"Tam {CYR_M}əxfi", f"Tam m{CYR_E}{CYR_X}fi",
    ],
    "mexfi": [
        "Məxfi", "MƏXFİ", "məxfi", "mExFi", "MəXfİ", "mexfi", "MEXFI", "Mexfi",
        "M ə x f i", "M-ə-x-f-i", "M.ə.x.f.i", "M\nəxfi", f"M{ZW}əxfi", f"{CYR_M}əxfi", f"Mə{CYR_X}fi",
    ],
    "gizli": [
        "Gizli", "GİZLİ", "gizli", "gIzLi", "GIZLI", "Gizli".swapcase(), "G i z l i", "G-i-z-l-i",
        "G.i.z.l.i", "G\ni\nz\nl\ni", f"G{CYR_I}zli", "Gi­zli",
    ],
    "xidmeti": [
        "Xidməti istifadə üçün", "XİDMƏTİ İSTİFADƏ ÜÇÜN", "xidməti istifadə üçün", "Xidmeti istifade ucun",
        "XIDMETI ISTIFADE UCUN", "xidmeti istifade ucun", "xIdMəTi İsTiFaDə üÇüN",
        "X i d m ə t i  i s t i f a d ə  ü ç ü n", "Xidməti-istifadə-üçün", "Xidməti\nistifadə\nüçün",
        "Xidməti   istifadə   üçün",
        # сокращение «XİÜ»
        "XİÜ", "XIU", "xiü", "XiÜ", "X İ Ü", "X.İ.Ü.", "X-I-U", f"X{ZW}İÜ",
    ],
}
ALL_VARIANTS = [(key, v) for key, variants in VARIANTS.items() for v in variants]


@pytest.mark.parametrize("key,marker", ALL_VARIANTS, ids=[f"{k}:{v!r}" for k, v in ALL_VARIANTS])
def test_marker_is_recognised_in_any_spelling(key, marker):
    assert classify(f"Daxili sənəd. {marker}. Kənara çıxarmayın.") == key
    assert is_classified(f"{marker}")


def test_every_listed_marker_is_covered():
    assert set(VARIANTS) == set(CLASSIFICATION_NAMES)
    assert CLASSIFICATION_NAMES == {
        "tam_mexfi": "Tam məxfi", "mexfi": "Məxfi", "gizli": "Gizli", "xidmeti": "Xidməti istifadə üçün",
    }


def test_most_severe_marker_wins_when_several_are_present():
    assert classify("Gizli. Məxfi. Xidməti istifadə üçün.") == "mexfi"
    assert classify("Xidməti istifadə üçün. Tam məxfi.") == "tam_mexfi"
    assert classify("XİÜ və gizli") == "gizli"


@pytest.mark.parametrize("text", [
    "Gizlilik siyasəti yeniləndi.", "Məxfilik müqaviləsi imzalandı.", "Müxtəlif sənədlər.", "Hesabat hazırdır.",
    "Tam şəkildə hazırdır.", "Xidməti məlumat və istifadə qaydaları.", "Üçün istifadə qaydası.",
    "Xiu Li ilə görüşdük.",        # китайское имя: «Xiu», одна заглавная, без «ü»
    "xiu meyvəsi",                 # строчное «xiu» без диакритики грифом не считаем
    "Привет, это обычный текст без грифа.",
])
def test_similar_but_harmless_text_is_not_a_marker(text):
    assert classify(text) is None


def test_marker_part_of_a_longer_word_is_not_a_marker():
    assert classify("Məxfiləşdirmə qaydaları") is None
    assert classify("Gizlicə gəldi") is None


# ---------- 2. Блокировка целиком: не маскируем, в AI не отправляем ----------

DOC = f"Məxfi sənəd. Əməkdaş Elvin Məmmədov, FİN 5ARZ8D2, {SECRET_DOC}."


@pytest.fixture
def boom(monkeypatch):
    """Если для текста с грифом вызвать маскирование, поиск данных или AI, тест упадёт."""
    def fail(*a, **k):
        raise AssertionError("текст с грифом не должен обрабатываться")
    monkeypatch.setattr(main, "mask", fail)
    monkeypatch.setattr(main, "detect", fail)
    monkeypatch.setattr(main, "ask_llm", fail)


@pytest.mark.parametrize("path", ["/api/mask", "/api/chat"])
@pytest.mark.parametrize("marker,name", [
    ("Məxfi", "Məxfi"), ("TAM MEXFI", "Tam məxfi"), ("g i z l i", "Gizli"),
    ("xidmeti istifade ucun", "Xidməti istifadə üçün"), ("XİÜ", "Xidməti istifadə üçün"),
])
def test_classified_request_is_blocked_completely(path, marker, name, ai_on, boom):
    stats_before = client.get("/api/stats").json()
    r = client.post(path, json={"text": f"{marker}: Elvin Məmmədov, FİN 5ARZ8D2, {SECRET_DOC}"})
    body = r.json()
    assert r.status_code == 403
    assert body["blocked"] is True and body["category"] == name
    assert "masked_text" not in body and "answer" not in body and "spans" not in body  # не маскировали
    assert SECRET_DOC not in r.text and "Elvin" not in r.text
    stats = client.get("/api/stats").json()
    assert stats["categories"] == stats_before["categories"]                          # данные не считались
    assert stats["blocked"] == stats_before.get("blocked", 0) + 1
    assert stats["blocked_categories"][name] == stats_before.get("blocked_categories", {}).get(name, 0) + 1


def test_blocking_message_is_in_azerbaijani_and_names_the_marker(boom):
    r = client.post("/api/mask", json={"text": "Tam məxfi: " + SECRET_DOC})
    detail = r.json()["detail"]
    assert "«Tam məxfi» qrifi aşkarlandı" in detail
    assert "bloklandı" in detail and "maskalanmadı" in detail and "AI-yə göndərilmədi" in detail
    assert "İnzibatçıya bildiriş göndərildi" in detail
    assert SECRET_DOC not in detail


def test_check_endpoint_does_not_process_classified_text(boom):
    body = client.post("/api/check", json={"text": DOC}).json()
    assert body["blocked"] is True and body["category"] == "Məxfi" and body["findings"] == []
    assert "qrifi aşkarlandı" in body["message"] and SECRET_DOC not in json.dumps(body)


def test_unclassified_text_is_not_blocked_and_leaves_no_incident():
    r = client.post("/api/mask", json={"text": "Elvin Məmmədov, FİN 5ARZ8D2"})
    assert r.status_code == 200
    assert local.get("/api/admin/incidents").json()["incidents"] == []


def test_page_shows_the_message_in_azerbaijani():
    html = client.get("/").text
    assert "Sorğu bloklandı" in html and "blockedBox(" in html


# ---------- 3. Журнал инцидентов и панель администратора ----------


def incidents_json():
    r = local.get("/api/admin/incidents")
    assert r.status_code == 200
    return r.json()


def test_incident_has_time_category_and_channel_but_no_text(boom, caplog):
    caplog.set_level(logging.DEBUG)
    before = datetime.now(timezone.utc) - timedelta(seconds=2)
    client.post("/api/mask", json={"text": DOC})
    data = incidents_json()
    assert len(data["incidents"]) == 1
    item = data["incidents"][0]
    assert set(item) == {"id", "time", "severity", "category", "category_name", "channel"}
    assert item["severity"] == "critical"
    assert item["category_name"] == "Məxfi" and item["channel"] == "mask"
    when = datetime.fromisoformat(item["time"])
    assert before <= when <= datetime.now(timezone.utc) + timedelta(seconds=2)
    for payload in (json.dumps(data), caplog.text, json.dumps(client.get("/api/stats").json())):
        assert SECRET_DOC not in payload and "Elvin" not in payload and "5ARZ8D2" not in payload


def test_channels_are_distinguished(ai_on, boom):
    client.post("/api/mask", json={"text": DOC})
    client.post("/api/chat", json={"text": DOC})
    client.post("/api/mask", json={"text": DOC}, headers={"X-Suzgec-Client": "extension"})
    client.post("/api/mask", json={"text": DOC}, headers={"X-Suzgec-Client": "evil<script>"})  # мусор игнорируем
    channels = [i["channel"] for i in reversed(incidents_json()["incidents"])]
    assert channels == ["mask", "chat", "extension", "mask"]


def test_incidents_are_numbered_newest_first_and_since_filters(boom):
    for text in ("Gizli a", "Məxfi b", "Tam məxfi c"):
        client.post("/api/mask", json={"text": text})
    data = incidents_json()
    assert [i["category_name"] for i in data["incidents"]] == ["Tam məxfi", "Məxfi", "Gizli"]
    assert [i["id"] for i in data["incidents"]] == [3, 2, 1] and data["last_id"] == 3 and data["total"] == 3
    assert data["by_category"] == {"Gizli": 1, "Məxfi": 1, "Tam məxfi": 1}
    newer = local.get("/api/admin/incidents?since=2").json()
    assert [i["id"] for i in newer["incidents"]] == [3]
    assert local.get("/api/admin/incidents?since=3").json()["incidents"] == []


def test_log_is_bounded_and_holds_no_text():
    log = IncidentLog()
    for _ in range(MAX_INCIDENTS + 50):
        log.add("gizli", "Gizli", "mask")
    items = log.since(0)
    assert len(items) == MAX_INCIDENTS and items[0]["id"] == MAX_INCIDENTS + 50
    assert log.summary()["total"] == MAX_INCIDENTS + 50  # счётчик помнит все, журнал — последние
    assert log.since(0)[0].keys() == {"id", "time", "severity", "category", "category_name", "channel"}


def test_admin_api_is_closed_to_other_computers_by_default(boom):
    client.post("/api/mask", json={"text": DOC})
    r = client.get("/api/admin/incidents")  # «чужой» клиент
    assert r.status_code == 403 and "ADMIN_TOKEN" in r.json()["detail"]
    assert "incidents" not in r.json()


def test_admin_token_protects_the_api(monkeypatch, boom):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret-token")
    client.post("/api/mask", json={"text": DOC})
    assert client.get("/api/admin/incidents").status_code == 401
    assert client.get("/api/admin/incidents", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert local.get("/api/admin/incidents").status_code == 401  # даже с этого компьютера без токена нельзя
    ok = client.get("/api/admin/incidents", headers={"X-Admin-Token": "s3cret-token"})
    assert ok.status_code == 200 and len(ok.json()["incidents"]) == 1


def test_admin_page_is_served_with_the_critical_alert():
    r = client.get("/admin")
    assert r.status_code == 200
    html = r.text
    assert "КРИТИЧНО" in html and 'class="alert"' in html and "#b42318" in html  # красное критическое уведомление
    assert "/api/admin/incidents" in html


def test_admin_page_shows_time_category_channel_and_never_renders_html():
    html = (main.STATIC_DIR / "admin.html").read_text(encoding="utf-8")
    for needle in ("Время", "Гриф", "Канал", "category_name", "toLocaleString"):
        assert needle in html
    assert "innerHTML" not in html and "localStorage" not in html and "document.write" not in html
