"""Тесты API (синтетические данные, демо-режим без ключа API)."""
import pytest
from fastapi.testclient import TestClient

from app.main import MAX_TEXT_LENGTH, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # load_dotenv() мог подтянуть ключ из реального .env — для тестов он не нужен.


def test_index_page_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "Süzgəc" in r.text


def test_check_returns_positions_without_values():
    text = "Elvin Məmmədov, FİN 1A2B3C4"
    r = client.post("/api/check", json={"text": text})
    body = r.json()
    assert r.status_code == 200 and not body["blocked"]
    assert {f["category"] for f in body["findings"]} == {"AD", "FIN"}
    assert all(set(f) == {"category", "start", "end"} for f in body["findings"])


def test_check_flags_classified():
    assert client.post("/api/check", json={"text": "Məxfi sənəd"}).json()["blocked"] is True


def test_chat_masks_and_restores():
    text = "Mən Elvin Məmmədov, FİN 1A2B3C4"
    r = client.post("/api/chat", json={"text": text})
    body = r.json()
    assert r.status_code == 200 and body["demo"] is True
    assert "Elvin" not in body["masked_text"] and "1A2B3C4" not in body["masked_text"]
    assert "[AD_1]" in body["masked_text"] and "[FIN_1]" in body["masked_text"]
    assert "Elvin Məmmədov" in body["answer"] and "1A2B3C4" in body["answer"]
    assert body["found"] == {"AD": 1, "FIN": 1}


def test_chat_blocks_classified_text():
    r = client.post("/api/chat", json={"text": "Gizli: FİN 1A2B3C4"})
    assert r.status_code == 403
    assert "1A2B3C4" not in r.text


def test_text_too_long_and_empty_rejected_without_echo():
    for bad in ("", "x" * (MAX_TEXT_LENGTH + 1)):
        r = client.post("/api/chat", json={"text": bad})
        assert r.status_code == 422
        assert "1 до" in r.json()["detail"]


def test_stats_contain_only_counters():
    before = client.get("/api/stats").json()
    client.post("/api/chat", json={"text": "tel 050 123 45 67, FİN 9D1F6GH"})
    after = client.get("/api/stats").json()
    assert after["requests"] == before.get("requests", 0) + 1
    assert after["categories"]["TEL"] == before["categories"].get("TEL", 0) + 1
    assert "050 123 45 67" not in str(after)
