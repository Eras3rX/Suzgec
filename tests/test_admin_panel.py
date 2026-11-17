"""Панель администратора: живые счётчики, отделы, кнопка теста на 100 фразах. Только числа, без текстов.

Синтетические данные. Строка SECRET_DOC играет роль «текста документа»: в ответах панели её быть не должно.
"""
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import evaluation, ml_detector
from app.detector import Finding
from app.main import app

client = TestClient(app)                                   # «чужой» компьютер
local = TestClient(app, client=("127.0.0.1", 50000))       # этот же компьютер

SECRET_DOC = "ZZ-SECRET-DOC-7781"
SAMPLE = "Elvin Məmmədov, FİN 5ARZ8D2, tel +994 50 123 45 67, " + SECRET_DOC


@pytest.fixture(autouse=True)
def clean_state():
    """Обнулить счётчики отделов, журнал и категории, чтобы тесты не влияли друг на друга."""
    def reset():
        main.incidents.clear()
        main._category_counts.clear()
        main._blocked_categories.clear()
        main._source_counts.clear()
        main._totals.clear()
        for d in main._dept_counts:
            main._dept_counts[d].clear()
            main._dept_categories[d].clear()
    reset()
    yield
    reset()


def admin_stats():
    r = local.get("/api/admin/stats")
    assert r.status_code == 200
    return r.json()


def dept(stats, dept_id):
    return next(d for d in stats["departments"] if d["id"] == dept_id)


# ---------- 1. Живые счётчики по категориям ----------


def test_live_counters_grow_with_every_request_without_reloading():
    before = admin_stats()
    assert before["requests"] == 0 and before["categories"] == {} and before["blocked"] == 0
    client.post("/api/mask", json={"text": SAMPLE})
    one = admin_stats()
    assert one["categories"] == {"AD": 1, "FIN": 1, "TEL": 1} and one["requests"] == 1
    client.post("/api/mask", json={"text": "FİN 7K3M9PQ"})
    client.post("/api/mask", json={"text": "Hesab AZ21NABZ00000000137010001944, kart 4111 1111 1111 1111"})
    two = admin_stats()
    assert two["categories"]["FIN"] == 2 and two["categories"]["IBAN"] == 1 and two["categories"]["KART"] == 1
    assert two["requests"] == 3


def test_blocked_documents_are_counted_as_their_own_counter():
    client.post("/api/mask", json={"text": "Məxfi: " + SAMPLE})
    client.post("/api/mask", json={"text": "tam mexfi"})
    stats = admin_stats()
    assert stats["blocked"] == 2 and stats["blocked_categories"] == {"Məxfi": 1, "Tam məxfi": 1}
    assert stats["categories"] == {}  # данные из заблокированных документов не обрабатывались и не считались


def test_admin_stats_have_numbers_only():
    client.post("/api/mask", json={"text": SAMPLE, "department": "hr"})
    client.post("/api/mask", json={"text": "Məxfi " + SAMPLE, "department": "hr"})
    payload = json.dumps(admin_stats())
    for secret in (SECRET_DOC, "Elvin", "5ARZ8D2", "123 45 67"):
        assert secret not in payload


def test_stats_endpoint_is_closed_to_other_computers_and_needs_token_when_set(monkeypatch):
    assert client.get("/api/admin/stats").status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret-token")
    assert local.get("/api/admin/stats").status_code == 401
    assert client.get("/api/admin/stats", headers={"X-Admin-Token": "s3cret-token"}).status_code == 200


def test_public_stats_do_not_expose_departments():
    client.post("/api/mask", json={"text": SAMPLE, "department": "maliyye"})
    public = client.get("/api/stats").json()
    assert "departments" not in public and "department" not in json.dumps(public).lower()


# ---------- 2. Отделы ----------


def test_departments_are_counted_separately():
    client.post("/api/mask", json={"text": SAMPLE, "department": "maliyye"})
    client.post("/api/mask", json={"text": "FİN 5ARZ8D2", "department": "maliyye"})
    client.post("/api/mask", json={"text": "Tel +994 50 123 45 67", "department": "hr"})
    client.post("/api/mask", json={"text": "Məxfi sənəd", "department": "huquq"})
    client.post("/api/mask", json={"text": "FİN 7K3M9PQ"})  # отдел не выбран
    stats = admin_stats()
    assert [d["id"] for d in stats["departments"]] == ["maliyye", "hr", "huquq", "none"]
    assert [d["name"] for d in stats["departments"]] == ["Maliyyə", "HR", "Hüquq", "Seçilməyib"]

    maliyye = dept(stats, "maliyye")
    assert (maliyye["requests"], maliyye["protected"], maliyye["blocked"]) == (2, 4, 0)
    assert maliyye["categories"] == {"AD": 1, "FIN": 2, "TEL": 1}
    hr = dept(stats, "hr")
    assert (hr["requests"], hr["protected"], hr["blocked"]) == (1, 1, 0) and hr["categories"] == {"TEL": 1}
    huquq = dept(stats, "huquq")
    assert (huquq["requests"], huquq["protected"], huquq["blocked"]) == (1, 0, 1)
    none = dept(stats, "none")
    assert (none["requests"], none["protected"]) == (1, 1)
    # сумма по отделам = общие счётчики
    assert sum(d["requests"] for d in stats["departments"]) == stats["requests"] == 5
    assert sum(d["blocked"] for d in stats["departments"]) == stats["blocked"] == 1


@pytest.mark.parametrize("sent,expected", [
    ("Maliyyə", "maliyye"), ("maliyyə", "maliyye"), ("HR", "hr"), ("Hüquq", "huquq"),
    ("huquq", "huquq"), ("", "none"), (None, "none"), ("IT", "none"), ("<script>", "none"),
])
def test_department_names_and_garbage(sent, expected):
    client.post("/api/mask", json={"text": "FİN 5ARZ8D2", "department": sent})
    assert dept(admin_stats(), expected)["requests"] == 1


def test_overlong_department_is_just_ignored_and_never_echoed():
    r = client.post("/api/mask", json={"text": SAMPLE, "department": "x" * 100})
    assert r.status_code == 200 and "x" * 100 not in r.text
    stats = admin_stats()
    assert dept(stats, "none")["requests"] == 1 and "x" * 100 not in json.dumps(stats)


def test_department_is_counted_for_the_built_in_ai_too(ai_on):
    client.post("/api/chat", json={"text": SAMPLE, "department": "huquq"})
    assert dept(admin_stats(), "huquq")["categories"] == {"AD": 1, "FIN": 1, "TEL": 1}


def test_check_does_not_count_anything():
    client.post("/api/check", json={"text": SAMPLE, "department": "hr"})
    assert admin_stats()["requests"] == 0


def test_main_page_and_extension_send_the_department():
    page = (main.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="dept"' in page and all(v in page for v in ("Maliyyə", "HR", "Hüquq"))
    assert 'department: $("dept").value' in page
    assert 'target="_blank"' in page and "/admin" in page  # админка открывается в отдельной вкладке
    ext = main.STATIC_DIR.parent / "extension"
    assert "department" in (ext / "mask-client.js").read_text(encoding="utf-8")
    popup = (ext / "popup.html").read_text(encoding="utf-8")
    assert 'id="department"' in popup and "Maliyyə" in popup and "Hüquq" in popup


# ---------- 3. Кнопка «100 sintetik cümlə üzrə test»: настоящий замер ----------


def test_the_dataset_is_exactly_100_synthetic_phrases():
    rows = evaluation.load_rows()
    assert len(rows) == 100
    assert sum(1 for r in rows if r["expected"]) == 70 and sum(1 for r in rows if not r["expected"]) == 30
    assert len({r["id"] for r in rows}) == 100


def post_eval():
    r = local.post("/api/admin/eval")
    assert r.status_code == 200, r.text
    return r.json()


def test_eval_returns_real_metrics_for_the_rules():
    data = post_eval()
    assert data["phrases"] == 100 and data["with_data"] == 70 and data["without_data"] == 30
    assert data["model"] == "off" and [r["mode"] for r in data["runs"]] == ["rules"]  # модель в тестах выключена
    run = data["runs"][0]
    totals = run["totals"]
    assert totals["expected"] == totals["found"] + totals["missed"]
    assert 0 < totals["recall"] <= 1 and 0 < totals["precision"] <= 1 and 0 < totals["f1"] <= 1
    assert totals["precision"] == pytest.approx(totals["found"] / (totals["found"] + totals["false"]))
    assert totals["recall"] == pytest.approx(totals["found"] / totals["expected"])
    assert run["latency_ms"]["avg"] > 0 and run["latency_ms"]["max"] >= run["latency_ms"]["p95"] >= 0
    assert set(run["categories"]) >= {"FIN", "IBAN", "KART", "TEL", "AD", "ADRES", "TARIX"}


def test_numbers_are_computed_not_hard_coded(monkeypatch):
    """Подменяем детектор: пустой ответ даёт нулевую полноту, «идеальный» — 100%. Значит, цифры считаются."""
    monkeypatch.setattr(evaluation, "detect", lambda text, use_model=None: [])
    nothing = post_eval()["runs"][0]["totals"]
    assert nothing["found"] == 0 and nothing["recall"] == 0.0 and nothing["precision"] is None
    assert nothing["missed"] == nothing["expected"] > 0

    by_text = {r["text"]: r["expected"] for r in evaluation.load_rows()}

    def perfect(text, use_model=None):
        found = []
        for cat, value in by_text[text]:
            start = text.find(value)
            found.append(Finding(cat, start, start + len(value), value))
        return found

    monkeypatch.setattr(evaluation, "detect", perfect)
    best = post_eval()["runs"][0]
    assert best["totals"]["recall"] == 1.0 and best["totals"]["precision"] == 1.0 and best["totals"]["false"] == 0
    assert best["clean_false_alarms"] == 0

    def noisy(text, use_model=None):  # на каждую фразу добавляем ложную находку
        return perfect(text) + [Finding("FIN", 0, 1, text[:1])]

    monkeypatch.setattr(evaluation, "detect", noisy)
    worse = post_eval()["runs"][0]
    assert worse["totals"]["recall"] == 1.0 and worse["totals"]["precision"] < 0.6
    assert worse["clean_false_alarms"] == 30


def test_latency_is_really_measured(monkeypatch):
    def slow(text, use_model=None):
        time.sleep(0.003)
        return []

    monkeypatch.setattr(evaluation, "detect", slow)
    latency = post_eval()["runs"][0]["latency_ms"]
    assert 3.0 <= latency["avg"] < 100 and latency["max"] >= latency["avg"]
    assert latency["total"] == pytest.approx(latency["avg"] * 100, rel=0.01) and latency["total"] >= 300


def test_with_the_model_ready_both_modes_are_run(monkeypatch):
    monkeypatch.setattr(ml_detector, "status", lambda: "ready")
    modes = []

    def fake(text, use_model=None):
        modes.append(use_model)
        return []

    monkeypatch.setattr(evaluation, "detect", fake)
    data = post_eval()
    assert [r["mode"] for r in data["runs"]] == ["rules", "hybrid"] and data["model"] == "ready"
    assert [r["mode_name"] for r in data["runs"]] == ["Qaydalar", "Qaydalar + AI"]
    assert modes.count(False) == 100 and modes.count(None) == 100  # правила: use_model=False; гибрид: как настроено


def test_eval_response_contains_numbers_only():
    payload = json.dumps(post_eval(), ensure_ascii=False)
    for row in evaluation.load_rows():
        assert row["text"] not in payload
        for _, value in row["expected"]:
            assert value not in payload, value


def test_eval_does_not_touch_live_counters_or_incidents():
    client.post("/api/mask", json={"text": SAMPLE, "department": "hr"})
    before = admin_stats()
    post_eval()
    post_eval()
    after = admin_stats()
    assert {k: v for k, v in after.items() if k != "updated"} == {k: v for k, v in before.items() if k != "updated"}
    assert local.get("/api/admin/incidents").json()["incidents"] == []


def test_eval_is_admin_only_and_not_run_twice_at_once(monkeypatch):
    assert client.post("/api/admin/eval").status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret-token")
    assert local.post("/api/admin/eval").status_code == 401
    assert client.post("/api/admin/eval", headers={"X-Admin-Token": "s3cret-token"}).status_code == 200
    monkeypatch.delenv("ADMIN_TOKEN")
    assert main._eval_lock.acquire(blocking=False)
    try:
        r = local.post("/api/admin/eval")
        assert r.status_code == 409 and "artıq işləyir" in r.json()["detail"]
    finally:
        main._eval_lock.release()


# ---------- Сама страница ----------


def test_admin_page_has_tabs_button_and_empty_placeholders():
    html = client.get("/admin").text
    assert "100 sintetik cümlə üzrə test" in html
    for tab in ("Canlı say", "Şöbələr", "Test", "İnsidentlər"):
        assert tab in html
    for category in ("FİN", "IBAN", "Kart", "Telefon", "Ad", "Qrif blokları"):
        assert category in html
    assert "/api/admin/stats" in html and "/api/admin/eval" in html and "setInterval(poll, 2000)" in html
    assert "Hüquq" in html or "departments" in html


def test_admin_page_has_no_hard_coded_metrics():
    """В разметке нет заранее вписанных Precision/Recall/задержек: пока тест не запущен, везде «—»."""
    html = (main.STATIC_DIR / "admin.html").read_text(encoding="utf-8")
    without_css = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    assert not re.search(r"\d+(\.\d+)?\s*%", without_css)          # никаких «96%», «97.5%»
    assert not re.search(r"\d+(\.\d+)?\s*ms\b", without_css)        # никаких «60 ms»
    body = without_css.split('id="panel-test"')[1].split('id="panel-incidents"')[0]
    assert body.count('class="empty">—</td>') == 2                  # обе таблицы теста пустые
    assert "Hələ işə salınmayıb." in body
