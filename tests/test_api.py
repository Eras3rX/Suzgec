"""Тесты API (синтетические данные, настоящий AI не вызывается)."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import MAX_TEXT_LENGTH, app

client = TestClient(app)

TEXT = "Mən Elvin Məmmədov, FİN 1A2B3C4, tel +994 50 123 45 67"


def test_index_page_and_script_served():
    r = client.get("/")
    assert r.status_code == 200 and "SiftGuard" in r.text
    assert client.get("/static/restore.js").status_code == 200


def test_pages_do_not_use_browser_storage():
    """Таблица меток живёт только в памяти вкладки: никакого localStorage, куки, IndexedDB."""
    pattern = re.compile(r"localStorage\.|sessionStorage\.|document\.cookie|indexedDB")
    for path in (Path("static/index.html"), Path("static/restore.js")):
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name


# ---------- /api/config ----------


def test_config_ai_unavailable_without_key():
    body = client.get("/api/config").json()
    assert body["ai_available"] is False
    assert body["model"] == "off"  # в тестах локальная модель выключена


def test_config_ai_available_with_key(ai_on):
    assert client.get("/api/config").json()["ai_available"] is True


# ---------- /api/check ----------


def test_check_returns_positions_without_values():
    r = client.post("/api/check", json={"text": TEXT})
    body = r.json()
    assert r.status_code == 200 and not body["blocked"]
    assert {f["category"] for f in body["findings"]} == {"AD", "FIN", "TEL"}
    assert all(set(f) == {"category", "start", "end", "source"} for f in body["findings"])
    assert all(f["source"] == "rule" for f in body["findings"])  # модель выключена


def test_check_flags_classified():
    assert client.post("/api/check", json={"text": "Məxfi sənəd"}).json()["blocked"] is True


# ---------- /api/mask: основной режим, ключ не нужен ----------


def test_mask_works_without_key_and_returns_no_original_values():
    r = client.post("/api/mask", json={"text": TEXT})
    body = r.json()
    assert r.status_code == 200
    assert body["masked_text"] == "Mən [AD_1], FİN [FIN_1], tel [TEL_1]"
    assert body["found"] == {"AD": 1, "FIN": 1, "TEL": 1}
    for secret in ("Elvin", "Məmmədov", "1A2B3C4", "+994 50 123 45 67"):
        assert secret not in r.text  # сервер значений не возвращает


def test_mask_spans_let_the_browser_rebuild_the_table():
    """Браузер берёт значения из своего текста по позициям: проверяем, что позиции верные."""
    spans = client.post("/api/mask", json={"text": TEXT}).json()["spans"]
    chars = list(TEXT)
    table = {s["label"]: "".join(chars[s["start"]:s["end"]]) for s in spans}
    assert table == {"[AD_1]": "Elvin Məmmədov", "[FIN_1]": "1A2B3C4", "[TEL_1]": "+994 50 123 45 67"}


def test_mask_blocks_classified_text():
    r = client.post("/api/mask", json={"text": "Gizli: FİN 1A2B3C4"})
    assert r.status_code == 403
    assert "1A2B3C4" not in r.text


def test_mask_text_without_data_returns_unchanged():
    body = client.post("/api/mask", json={"text": "Salam, necəsiniz?"}).json()
    assert body["masked_text"] == "Salam, necəsiniz?" and body["spans"] == [] and body["found"] == {}


# ---------- /api/chat: второй режим, нужен ключ ----------


def test_chat_without_key_explains_how_to_continue():
    r = client.post("/api/chat", json={"text": TEXT})
    assert r.status_code == 503
    assert "yapışdırın" in r.json()["detail"]
    assert "1A2B3C4" not in r.text


def test_chat_masks_and_restores(ai_on):
    r = client.post("/api/chat", json={"text": TEXT})
    body = r.json()
    assert r.status_code == 200
    assert "Elvin" not in body["masked_text"] and "1A2B3C4" not in body["masked_text"]
    assert "[AD_1]" in body["masked_text"] and "[FIN_1]" in body["masked_text"]
    assert "Elvin Məmmədov" in body["answer"] and "1A2B3C4" in body["answer"]
    assert body["found"] == {"AD": 1, "FIN": 1, "TEL": 1}


def test_chat_returns_the_raw_ai_answer_with_labels_for_the_page_to_reveal(ai_on):
    """Страница показывает ответ AI как он пришёл (с метками) и раскрывает его у себя, по таблице в браузере."""
    body = client.post("/api/chat", json={"text": TEXT}).json()
    assert "[AD_1]" in body["raw_answer"] and "[FIN_1]" in body["raw_answer"]
    assert "Elvin" not in body["raw_answer"] and "1A2B3C4" not in body["raw_answer"]  # значений в нём нет
    assert body["raw_answer"] != body["answer"]


def test_chat_blocks_classified_text_even_without_key():
    r = client.post("/api/chat", json={"text": "Gizli: FİN 1A2B3C4"})
    assert r.status_code == 403
    assert "1A2B3C4" not in r.text


# ---------- Ошибки ввода ----------


def test_text_too_long_gives_clear_error():
    for path in ("/api/mask", "/api/chat"):
        r = client.post(path, json={"text": "x" * (MAX_TEXT_LENGTH + 1)})
        assert r.status_code == 413
        assert "çox uzundur" in r.json()["detail"] and str(MAX_TEXT_LENGTH) in r.json()["detail"]


def test_empty_text_gives_clear_error():
    r = client.post("/api/mask", json={"text": ""})
    assert r.status_code == 422
    assert "boş ola bilməz" in r.json()["detail"]


# ---------- Статистика ----------


def test_stats_contain_only_counters():
    before = client.get("/api/stats").json()
    client.post("/api/mask", json={"text": "tel 050 123 45 67, FİN 9D1F6GH"})
    after = client.get("/api/stats").json()
    assert after["requests"] == before.get("requests", 0) + 1
    assert after["categories"]["TEL"] == before["categories"].get("TEL", 0) + 1
    assert "050 123 45 67" not in str(after)
