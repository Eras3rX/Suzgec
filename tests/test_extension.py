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
