"""Проверки расширения для Chrome (extension/): манифест, права, приватность.

Поведение вставки проверяется в браузере: tests/js/extension.test.html (см. README).
"""
import json
import re
import struct
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.main import app

EXT = Path(__file__).resolve().parent.parent / "extension"
MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
SOURCES = [p for p in EXT.rglob("*") if p.suffix in (".js", ".html") and p.is_file()]
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}


def test_manifest_is_v3_and_has_the_files_it_references():
    assert MANIFEST["manifest_version"] == 3
    assert (EXT / MANIFEST["background"]["service_worker"]).is_file()
    assert (EXT / MANIFEST["action"]["default_popup"]).is_file()
    for script in MANIFEST["content_scripts"]:
        for js in script["js"]:
            assert (EXT / js).is_file(), js


def test_extension_works_only_on_chatgpt():
    patterns = [m for s in MANIFEST["content_scripts"] for m in s["matches"]]
    assert patterns and all(urlparse(p.replace("*", "x")).hostname in ("chatgpt.com", "chat.openai.com") for p in patterns)
    assert all(p.startswith("https://") for p in patterns)


def test_permissions_are_minimal_and_text_goes_only_to_localhost():
    assert MANIFEST["permissions"] == ["storage"]  # без tabs, cookies, history, clipboardRead и т.п.
    hosts = MANIFEST["host_permissions"]
    assert hosts and all(urlparse(h.replace("*", "x")).hostname in LOOPBACK for h in hosts), hosts
    assert "optional_permissions" not in MANIFEST and "optional_host_permissions" not in MANIFEST
    assert "externally_connectable" not in MANIFEST  # другие сайты и расширения писать нам не могут


def test_icons_are_valid_pngs_of_the_declared_size():
    for size, rel in MANIFEST["icons"].items():
        data = (EXT / rel).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", rel
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (int(size), int(size)), rel


def test_no_remote_addresses_in_extension_code():
    """В коде расширения могут быть только адреса локального сервера."""
    for path in SOURCES:
        for url in re.findall(r"https?://[^\s\"'`)<>]+", path.read_text(encoding="utf-8")):
            host = urlparse(url).hostname or ""
            assert host in LOOPBACK or url.startswith("http://[::1]"), f"{path.name}: {url}"


def test_no_logging_eval_or_unsafe_html_in_extension_code():
    forbidden = re.compile(r"console\.|\beval\(|new Function|innerHTML|outerHTML|document\.write|localStorage|sessionStorage|indexedDB")
    for path in SOURCES:
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name


def test_background_accepts_messages_only_from_the_extension_itself():
    assert "sender.id !== chrome.runtime.id" in (EXT / "background.js").read_text(encoding="utf-8")


def test_content_script_stops_interfering_when_the_extension_dies():
    """Сайт должен работать как раньше, даже если расширение обновили или удалили."""
    js = (EXT / "content.js").read_text(encoding="utf-8")
    assert 'removeEventListener("paste"' in js and "extensionAlive" in js


def test_server_response_has_the_fields_the_extension_reads():
    body = TestClient(app).post("/api/mask", json={"text": "FİN 5ARZ8D2"}).json()
    assert body["masked_text"] == "FİN [FIN_1]"
    assert len(body["spans"]) == 1
    assert "5ARZ8D2" not in json.dumps(body)  # сервер значений не возвращает


def test_content_script_covers_every_way_a_file_gets_into_the_chat():
    js = (EXT / "content.js").read_text(encoding="utf-8")
    for event in ('addEventListener("paste"', 'addEventListener("change"', 'addEventListener("drop"'):
        assert event in js, event
    for removed in ('removeEventListener("change"', 'removeEventListener("drop"'):  # и перестаёт вмешиваться при гибели
        assert removed in js, removed
    assert "qaralanmis" in js and "file_classified" in js


def test_file_requests_go_to_localhost_as_raw_body_with_the_extension_channel():
    client_js = (EXT / "mask-client.js").read_text(encoding="utf-8")
    assert '"/api/redact"' in client_js and "application/octet-stream" in client_js
    assert "isLoopback(server)" in client_js.split("async function redactFile")[1]  # на чужой адрес файл не уйдёт
    assert 'type === "redact"' in (EXT / "background.js").read_text(encoding="utf-8")


def test_server_marks_documents_from_the_extension_as_extension_channel(isolated_audit):
    import io

    import pytest
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open(); doc.new_page().insert_text((72, 100), "FIN 5ARZ8D2", fontsize=11)
    local = TestClient(app, client=("127.0.0.1", 50000))
    r = local.post("/api/redact", content=doc.tobytes(),
                   headers={"Content-Type": "application/octet-stream", "X-SiftGuard-Client": "extension"})
    assert r.status_code == 200
    assert '"channel":"extension"' in isolated_audit.path.read_text(encoding="utf-8")
    secret = pymupdf.open()
    secret.new_page().insert_text((72, 100), "Mexfi sened", fontsize=11)
    blocked = local.post("/api/redact", content=secret.tobytes(),
                         headers={"Content-Type": "application/octet-stream", "X-SiftGuard-Client": "extension"})
    assert blocked.status_code == 403
    assert any(i["channel"] == "extension" for i in local.get("/api/admin/incidents").json()["incidents"])


def test_extension_tells_the_server_which_channel_it_is_without_sending_text():
    """Заголовок X-SiftGuard-Client нужен, чтобы в панели администратора было видно канал «расширение»."""
    client_js = (EXT / "mask-client.js").read_text(encoding="utf-8")
    assert '"X-SiftGuard-Client": "extension"' in client_js
    content_js = (EXT / "content.js").read_text(encoding="utf-8")
    assert "qrifli mətn bloklandı" in content_js and "İnzibatçıya bildiriş göndərildi" in content_js
