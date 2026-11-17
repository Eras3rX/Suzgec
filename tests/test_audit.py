"""Журнал аудита без хранения данных и временное хранилище таблицы меток (только синтетические данные)."""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.audit import GENESIS, AuditLog, canonical
from app.vault import TemporaryVault

client = TestClient(main.app)
local = TestClient(main.app, client=("127.0.0.1", 50000))  # этот же компьютер: так открывается панель

FIN = "5ARZ8D2"
PHONE = "+994 50 123 45 67"
NAME = "Aysel Quliyeva"
TEXT = f"{NAME} üçün müraciət. FİN {FIN}, telefon {PHONE}."


def log_text(log: AuditLog) -> str:
    return log.path.read_text(encoding="utf-8") if log.path.exists() else ""


# ---------- канонизация и хеш ----------

def test_canonical_makes_variants_equal():
    assert canonical("FIN", "5arz8d2") == canonical("FIN", "5 ARZ-8D2") == "5ARZ8D2"
    assert canonical("TEL", "+994 50 123 45 67") == canonical("TEL", "050-123-45-67") == "501234567"
    assert canonical("AD", "AYSEL  QULİYEVA") == canonical("AD", "aysel quliyeva")
    assert canonical("KART", "4169 7388 5555 1234") == "4169738855551234"


def test_digest_depends_on_key_and_category():
    a = AuditLog("x", key=b"one")
    b = AuditLog("x", key=b"two")
    assert a.digest("FIN", FIN) == a.digest("FIN", "5arz8d2")
    assert a.digest("FIN", FIN) != b.digest("FIN", FIN)  # без ключа хеш не воспроизвести
    assert a.digest("FIN", FIN) != a.digest("ID", FIN)


# ---------- в журнале нет значений ----------

def test_log_never_contains_plaintext(isolated_audit):
    client.post("/api/mask", json={"text": TEXT, "department": "maliyye"})
    text = log_text(isolated_audit)
    assert text, "журнал должен быть записан"
    for secret in (FIN, "501234567", "50 123 45 67", "Aysel", "Quliyeva", NAME):
        assert secret not in text
    for line in text.splitlines():
        record = json.loads(line)
        assert set(record) == {"seq", "ts", "event", "category", "digest", "channel", "dept", "prev", "hash"}
        assert len(record["digest"]) == 64


def test_log_records_categories_channel_and_department(isolated_audit):
    client.post("/api/mask", json={"text": TEXT, "department": "hr"})
    records = [json.loads(line) for line in log_text(isolated_audit).splitlines()]
    assert {r["category"] for r in records} >= {"FIN", "TEL", "AD"}
    assert {r["channel"] for r in records} == {"mask"}
    assert {r["dept"] for r in records} == {"hr"}


def test_extension_channel(isolated_audit):
    client.post("/api/mask", json={"text": TEXT}, headers={"X-SiftGuard-Client": "extension"})
    assert {json.loads(l)["channel"] for l in log_text(isolated_audit).splitlines()} == {"extension"}


def test_text_without_data_writes_nothing(isolated_audit):
    client.post("/api/mask", json={"text": "Bu gün hava yaxşıdır."})
    assert isolated_audit.total() == 0


def test_repeated_value_in_one_request_is_one_event(isolated_audit):
    client.post("/api/mask", json={"text": f"FİN {FIN}. Təkrar: {FIN}."})
    assert isolated_audit.total() == 1


def test_classified_block_is_logged_without_digest(isolated_audit):
    r = client.post("/api/chat", json={"text": "Məxfi. " + TEXT})
    assert r.status_code == 403
    records = [json.loads(l) for l in log_text(isolated_audit).splitlines()]
    assert len(records) == 1 and records[0]["event"] == "blocked" and records[0]["digest"] is None
    assert FIN not in log_text(isolated_audit)


def test_chat_path_writes_audit(ai_on, isolated_audit):
    client.post("/api/chat", json={"text": TEXT})
    records = [json.loads(l) for l in log_text(isolated_audit).splitlines()]
    assert {r["channel"] for r in records} == {"chat"}


# ---------- цепочка ----------

def fill(log: AuditLog, n: int = 5):
    for i in range(n):
        log.record_detected([("FIN", f"5ARZ8D{i}")], "mask", "none")


def test_chain_is_valid_and_links_records(tmp_path):
    log = AuditLog(tmp_path, key=b"k")
    fill(log)
    result = log.verify_chain()
    assert result == {"ok": True, "events": 5, "broken_at": None}
    records = [json.loads(l) for l in log_text(log).splitlines()]
    assert records[0]["prev"] == GENESIS
    assert all(records[i]["prev"] == records[i - 1]["hash"] for i in range(1, 5))


def test_chain_survives_restart(tmp_path):
    fill(AuditLog(tmp_path, key=b"k"), 3)
    second = AuditLog(tmp_path, key=b"k")
    fill(second, 2)
    assert second.verify_chain() == {"ok": True, "events": 5, "broken_at": None}


def test_edit_is_detected(tmp_path):
    log = AuditLog(tmp_path, key=b"k")
    fill(log)
    lines = log_text(log).splitlines()
    record = json.loads(lines[2])
    record["category"] = "TEL"  # кто-то подменил категорию
    lines[2] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain() == {"ok": False, "events": 2, "broken_at": 3}


def test_deletion_in_the_middle_is_detected(tmp_path):
    log = AuditLog(tmp_path, key=b"k")
    fill(log)
    lines = log_text(log).splitlines()
    del lines[1]
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert log.verify_chain()["ok"] is False


def test_garbage_line_is_detected(tmp_path):
    log = AuditLog(tmp_path, key=b"k")
    fill(log, 2)
    log.path.write_text(log_text(log) + "не json\n", encoding="utf-8")
    assert log.verify_chain()["ok"] is False


# ---------- проверка значения ----------

def test_check_value_finds_event_without_storing_value(tmp_path):
    log = AuditLog(tmp_path, key=b"k")
    log.record_detected([("FIN", FIN)], "mask", "maliyye")
    before = log_text(log)
    result = log.check_value("5 arz 8d2")  # другая запись того же FİN
    assert result["found"] and result["count"] == 1
    assert result["matches"][0]["dept"] == "maliyye"
    assert log_text(log) == before  # проверка ничего не пишет
    assert not log.check_value("1ABC2DE")["found"]


def test_check_value_with_other_key_finds_nothing(tmp_path):
    AuditLog(tmp_path, key=b"k").record_detected([("FIN", FIN)], "mask", "none")
    assert not AuditLog(tmp_path, key=b"other").check_value(FIN)["found"]


# ---------- ключ ----------

def test_key_file_is_created_and_reused(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDIT_KEY", raising=False)
    first = AuditLog(tmp_path)
    d1 = first.digest("FIN", FIN)
    assert (tmp_path / "audit.key").exists()
    assert AuditLog(tmp_path).digest("FIN", FIN) == d1


def test_key_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_KEY", "from-env")
    log = AuditLog(tmp_path)
    log.digest("FIN", FIN)
    assert log.key_source == "env" and not (tmp_path / "audit.key").exists()


# ---------- временное хранилище ----------

def test_vault_wipes_on_exit():
    mapping = {"[FIN_1]": FIN}
    with TemporaryVault(mapping):
        assert mapping == {"[FIN_1]": FIN}
    assert mapping == {}


def test_vault_wipes_even_on_error():
    mapping = {"[FIN_1]": FIN}
    with pytest.raises(RuntimeError):
        with TemporaryVault(mapping):
            raise RuntimeError("AI недоступен")
    assert mapping == {}


def test_vault_counters_return_to_zero_after_requests(ai_on):
    before = TemporaryVault.stats()
    client.post("/api/mask", json={"text": TEXT})
    client.post("/api/chat", json={"text": TEXT})
    after = TemporaryVault.stats()
    assert after["active"] == 0
    assert after["wiped"] == before["wiped"] + 2


def test_vault_wiped_when_llm_fails(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    def boom(_):
        raise main.LLMError("test")

    monkeypatch.setattr(main, "ask_llm", boom)
    before = TemporaryVault.stats()
    assert client.post("/api/chat", json={"text": TEXT}).status_code == 502
    assert TemporaryVault.stats() == {"active": 0, "wiped": before["wiped"] + 1}


def test_restoration_still_works_through_vault(ai_on):
    r = client.post("/api/chat", json={"text": TEXT}).json()
    assert FIN in r["answer"] and NAME in r["answer"]  # вернулись исходные значения
    assert FIN not in r["masked_text"]


# ---------- сбой журнала не ломает работу ----------

def test_audit_failure_does_not_break_request(monkeypatch, isolated_audit):
    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(isolated_audit, "record_detected", broken)
    r = client.post("/api/mask", json={"text": TEXT})
    assert r.status_code == 200 and FIN not in r.json()["masked_text"]
    assert isolated_audit.healthy is False


# ---------- API панели ----------

def test_admin_audit_endpoint(isolated_audit):
    client.post("/api/mask", json={"text": TEXT})
    data = local.get("/api/admin/audit").json()
    assert data["chain"]["ok"] and data["chain"]["events"] >= 3
    assert data["vault"]["active"] == 0
    body = json.dumps(data, ensure_ascii=False)
    for secret in (FIN, "Aysel", "Quliyeva", "501234567"):
        assert secret not in body
    assert all(e["digest"].endswith("…") and len(e["digest"]) <= 17 for e in data["events"])  # хеш только сокращённый


def test_admin_audit_check_endpoint(isolated_audit):
    client.post("/api/mask", json={"text": TEXT, "department": "huquq"})
    found = local.post("/api/admin/audit/check", json={"value": FIN.lower()}).json()
    assert found["found"] and found["matches"][0]["dept"] == "huquq"
    assert local.post("/api/admin/audit/check", json={"value": "9ZZZ9Z9"}).json()["found"] is False


def test_audit_endpoints_need_admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    assert client.get("/api/admin/audit").status_code == 401
    assert client.post("/api/admin/audit/check", json={"value": FIN}).status_code == 401
    ok = client.get("/api/admin/audit", headers={"X-Admin-Token": "secret-token"})
    assert ok.status_code == 200


def test_audit_check_rejects_empty_and_long_value():
    assert local.post("/api/admin/audit/check", json={"value": ""}).status_code in (413, 422)
    assert local.post("/api/admin/audit/check", json={"value": "x" * 500}).status_code in (413, 422)


def test_audit_endpoints_closed_for_remote_computer():
    assert client.get("/api/admin/audit").status_code == 403
    assert client.post("/api/admin/audit/check", json={"value": FIN}).status_code == 403
