"""Три языка интерфейса (az, ru, en): словарь полный, страницы подключены, сообщения сервера переведены."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.messages import LANGS, MESSAGES, lang_of, tr

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = ["index.html", "admin.html", "about.html", "sened.html"]
client = TestClient(main.app)


def load_dict() -> dict:
    text = (STATIC / "i18n-dict.js").read_text(encoding="utf-8")
    body = text[text.index("window.SG_DICT = ") + len("window.SG_DICT = "):].rstrip().rstrip(";")
    return json.loads(body)


DICT = load_dict()

# Названия и обозначения, которые одинаковы на всех языках
UNTRANSLATED = {"Sift", "Guard", "SiftGuard", "HR", "[AD_1]", "[FIN_1]", "SiftGuard · OYU CyberAI Hackathon 2026", "IBAN",
                "FİN", "Precision", "Recall", "Token", "AZ", "RU", "EN"}
LETTERS = re.compile(r"[A-Za-zƏəİıÖöÜüĞğŞşÇç]{2}")


class Collect(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.items = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in ("meta", "br", "img", "input", "link"):
            self.stack.append((tag, "data-i18n-rich" in a, "data-no-i18n" in a))
        for name in ("title", "placeholder", "aria-label", "alt"):
            if a.get(name):
                self.items.append((name, re.sub(r"\s+", " ", a[name]).strip()))
        if tag == "meta" and a.get("name") == "description":
            self.items.append(("meta", a["content"]))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if any(t in ("script", "style", "noscript", "textarea") or rich or skip for t, rich, skip in self.stack):
            return
        s = re.sub(r"\s+", " ", data).strip()
        if s and LETTERS.search(s):
            self.items.append(("text", s))


def page_strings(name: str) -> list[tuple[str, str]]:
    p = Collect()
    p.feed((STATIC / name).read_text(encoding="utf-8"))
    return p.items


def placeholders(s: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", s))


# ---------- словарь ----------

def test_dictionary_has_both_languages_with_the_same_keys():
    assert set(DICT) == {"ru", "en"}
    assert set(DICT["ru"]) == set(DICT["en"])


def test_no_empty_translations_and_placeholders_match():
    for lang in ("ru", "en"):
        for key, value in DICT[lang].items():
            assert value.strip(), (lang, key)
            if not key.startswith("#"):
                assert placeholders(key) == placeholders(value), (lang, key, value)


def test_translations_are_really_translated():
    # перевод не должен совпадать с азербайджанским ключом (кроме общих обозначений)
    same = [k for k, v in DICT["ru"].items() if k == v and k not in UNTRANSLATED and not k.startswith("#")]
    assert same == [], same


def test_russian_is_cyrillic_and_english_is_not():
    cyr = re.compile(r"[А-Яа-яЁё]")
    for key, value in DICT["ru"].items():
        if key not in UNTRANSLATED and not key.startswith("#") and LETTERS.search(value):
            assert cyr.search(value), ("ru", key, value)
    for key, value in DICT["en"].items():
        assert not cyr.search(value), ("en", key, value)


# ---------- страницы ----------

@pytest.mark.parametrize("name", PAGES)
def test_every_visible_text_on_the_page_has_a_translation(name):
    missing = sorted({s for kind, s in page_strings(name) if s not in DICT["ru"] and s not in UNTRANSLATED})
    assert missing == [], missing


@pytest.mark.parametrize("name", PAGES)
def test_page_has_the_language_switcher_and_scripts(name):
    html = (STATIC / name).read_text(encoding="utf-8")
    assert 'id="lang-switch"' in html
    assert html.index("i18n-dict.js") < html.index("i18n.js") < html.index("<script>") if "<script>" in html else True
    assert '<script src="/static/i18n.js"></script>' in html


def test_rich_paragraphs_have_translations():
    html = (STATIC / "about.html").read_text(encoding="utf-8")
    ids = re.findall(r'data-i18n-rich="([\w.]+)"', html)
    assert ids
    for rid in ids:
        assert "#" + rid in DICT["ru"] and "#" + rid in DICT["en"], rid


def script_keys(name: str) -> set[str]:
    """Фразы, которые скрипт страницы берёт из словаря: t("…") и SG.setText(…, "…") (в том числе условные)."""
    text = (STATIC / name).read_text(encoding="utf-8")
    lit = r'"((?:[^"\\\n]|\\.)*)"'
    keys = set(re.findall(r"(?<![\w.])t\(\s*" + lit, text))
    for call in re.findall(r"SG\.setText\((.*?)\);", text, flags=re.S):
        keys.update(re.findall(lit, call))
    for call in re.findall(r"(?<![\w.])t\((.*?)\)\s*[;,)]", text, flags=re.S):
        keys.update(re.findall(r"\?\s*" + lit + r"\s*:\s*" + lit, call)[0] if re.findall(r"\?\s*" + lit + r"\s*:\s*" + lit, call) else ())
    # условные варианты внутри t(cond ? "A" : "B")
    for a, b in re.findall(r'\?\s*' + lit + r'\s*:\s*' + lit, text):
        keys.update((a, b))
    return {k.encode().decode("unicode_escape") if "\\" in k else k for k in keys}


@pytest.mark.parametrize("name", ["index.html", "sened.html", "admin.html"])
def test_phrases_used_by_page_scripts_are_in_the_dictionary(name):
    # dom-идентификаторы и технические строки вида "#id", "px", "div" словарю не нужны
    candidates = {k for k in script_keys(name) if LETTERS.search(k) and " " in k or k in DICT["ru"]}
    missing = sorted(k for k in candidates if k not in DICT["ru"] and not re.fullmatch(r"[\w\-#.\[\]=:% ,/()+*|<>\\]+", k))
    assert missing == [], missing


# ---------- сообщения сервера ----------

def test_every_server_message_has_all_three_languages():
    for key, entry in MESSAGES.items():
        assert set(entry) == set(LANGS), key
        base = placeholders(entry["az"])
        for lang in LANGS:
            assert entry[lang].strip(), (key, lang)
            assert placeholders(entry[lang]) == base, (key, lang)
    cyr = re.compile(r"[А-Яа-яЁё]")
    assert all(cyr.search(entry["ru"]) for entry in MESSAGES.values())
    assert not any(cyr.search(entry["en"]) for entry in MESSAGES.values())


def test_lang_header_parsing():
    assert lang_of("ru") == "ru" and lang_of("EN") == "en" and lang_of("az") == "az"
    assert lang_of("ru-RU") == "ru" and lang_of(None) == "az" and lang_of("de") == "az" and lang_of("") == "az"


def test_tr_falls_back_to_the_text_itself():
    assert tr("not a key", "ru") == "not a key"
    assert tr("blocked", "en", name="Məxfi").startswith("Classification marking “Məxfi”")


@pytest.mark.parametrize("lang,fragment", [("az", "Mətn daxil edin"), ("ru", "Введите текст"), ("en", "Enter some text")])
def test_validation_errors_follow_the_language_header(lang, fragment):
    r = client.post("/api/mask", json={"text": ""}, headers={"X-Lang": lang})
    assert r.status_code == 422 and fragment in r.json()["detail"]


def test_default_language_is_azerbaijani_without_header():
    assert "Mətn daxil edin" in client.post("/api/mask", json={"text": ""}).json()["detail"]


@pytest.mark.parametrize("lang,fragment", [("ru", "заблокирован"), ("en", "blocked entirely"), ("az", "bloklandı")])
def test_classified_block_message_is_localized(lang, fragment):
    r = client.post("/api/mask", json={"text": "Məxfi sənəd"}, headers={"X-Lang": lang})
    assert r.status_code == 403 and fragment in r.json()["detail"]
    assert "Məxfi" in r.json()["detail"]  # название грифа не переводится


def test_too_long_text_message_is_localized():
    r = client.post("/api/mask", json={"text": "x" * 6000}, headers={"X-Lang": "en"})
    assert r.status_code == 413 and "too long" in r.json()["detail"]


def test_admin_errors_are_localized(monkeypatch):
    r = client.get("/api/admin/stats", headers={"X-Lang": "ru"})  # клиент не локальный: доступ закрыт
    assert r.status_code == 403 and "компьютера" in r.json()["detail"]
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    assert "администратора" in client.get("/api/admin/stats", headers={"X-Lang": "ru"}).json()["detail"]


def test_ai_errors_are_localized(ai_on, monkeypatch):
    import app.llm as llm

    def boom(_):
        raise llm.LLMError("ai_unavailable")

    monkeypatch.setattr(main, "ask_llm", boom)
    r = client.post("/api/chat", json={"text": "salam"}, headers={"X-Lang": "ru"})
    assert r.status_code == 502 and "недоступен" in r.json()["detail"]
    r = client.post("/api/chat", json={"text": "salam"}, headers={"X-Lang": "en"})
    assert "unavailable" in r.json()["detail"]


def test_i18n_script_does_not_use_browser_storage():
    js = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert not re.search(r"localStorage\.|sessionStorage\.|document\.cookie|indexedDB|innerHTML", js)
