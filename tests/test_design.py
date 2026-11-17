"""Дизайн: правила из задания проверяются автоматически (палитра, контраст, шрифты, запреты, язык, структура)."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = {"index.html": "Əsas səhifə", "admin.html": "İdarəetmə paneli", "about.html": "Haqqında"}
CSS = (STATIC / "site.css").read_text(encoding="utf-8")
FACES = (STATIC / "fonts" / "faces.css").read_text(encoding="utf-8")
client = TestClient(app)


def page(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


# ---------- Контраст (WCAG) ----------


def luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


TOKENS = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", CSS.split("/* Категории данных")[0]))
CATEGORIES = dict(re.findall(r'\[data-cat="(\w+)"\]\s*\{\s*--cat-bg:\s*(#[0-9a-fA-F]{6})', CSS))

TEXT_PAIRS = [
    ("ink", "surface"), ("ink", "bg"), ("muted", "surface"), ("muted", "bg"), ("link", "surface"), ("link", "bg"),
    ("navy", "surface"), ("navy", "bg"), ("accent", "surface"), ("accent", "accent-bg"), ("red", "surface"),
    ("red", "red-bg"), ("amber", "amber-bg"),
]


@pytest.mark.parametrize("fg,bg", TEXT_PAIRS)
def test_text_contrast_is_at_least_aa(fg, bg):
    assert contrast(TOKENS[fg], TOKENS[bg]) >= 4.5, (fg, bg, contrast(TOKENS[fg], TOKENS[bg]))


def test_light_text_on_dark_fills_is_readable():
    assert contrast("#ffffff", TOKENS["navy"]) >= 7          # шапка, кнопки
    assert contrast("#ffffff", TOKENS["navy-hover"]) >= 7
    assert contrast("#c9d6e6", TOKENS["navy"]) >= 4.5        # подпись у логотипа
    assert contrast("#ffffff", TOKENS["red"]) >= 4.5         # красное уведомление
    assert contrast(TOKENS["red"], "#ffffff") >= 4.5         # плашка «Kritik» на красном


def test_every_category_chip_has_readable_text():
    # UZ и IMZA: зоны на изображениях (лицо, подпись), см. app/redact.py
    assert set(CATEGORIES) == {"AD", "FIN", "ID", "VOEN", "IBAN", "KART", "TEL", "EMAIL", "ADRES", "TARIX", "UZ", "IMZA"}
    for name, bg in CATEGORIES.items():
        assert contrast(TOKENS["ink"], bg) >= 7, (name, contrast(TOKENS["ink"], bg))


# ---------- Запреты ----------


@pytest.mark.parametrize("source", ["site.css", *PAGES])
def test_no_forbidden_design_patterns(source):
    text = (STATIC / source).read_text(encoding="utf-8")
    for pattern in (r"gradient\(", r"box-shadow", r"text-shadow", r"monospace", r"text-transform\s*:\s*uppercase",
                    r"[→➜➔➝➞⟶]", r"<marquee", r"@import\s+url\(['\"]?https?:"):
        assert not re.search(pattern, text, flags=re.I), f"{source}: {pattern}"


def test_corners_are_not_the_generic_rounded_cards():
    radii = [float(v) for v in re.findall(r"border-radius:\s*([\d.]+)px", CSS)]
    assert radii and max(radii) <= 4
    assert "999px" not in CSS and "50%" not in CSS.replace("flex-basis", "")


def test_red_is_used_only_for_blocking_and_critical_things():
    allowed = ("notice-block", "alert", "sev", "metric-block", ".count", "is-block", "swatch-red", "is-new")
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if re.search(r"var\(--red(-bg|-line)?\)", body):
            assert any(key in selector for key in allowed), selector.strip()


def test_accent_green_means_protected_only():
    # pane-after и facts: «после» на странице документов, то есть защищённый результат
    allowed = ("status-ready", "summary", "notice-ok", "delta", "bump", "pane-after", "facts")
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if re.search(r"var\(--accent(-bg|-line)?\)", body):
            assert any(key in selector for key in allowed), selector.strip()


# ---------- Шрифты ----------


def test_fonts_are_self_hosted_google_fonts_noto_with_all_needed_glyph_ranges():
    for family in ("Noto Sans", "Noto Serif"):
        assert f"font-family: '{family}'" in FACES
    assert "font-display: swap" in FACES
    files = re.findall(r"url\('/static/fonts/([\w.-]+)'\)", FACES)
    assert len(files) == 6
    for name in files:
        data = (STATIC / "fonts" / name).read_bytes()
        assert data[:4] == b"wOF2", name
    # ə U+0259, ğ U+011F, ş U+015F, İ U+0130 лежат в «latin-ext»; ı U+0131, ç ö ü в «latin»; кириллица в «cyrillic»
    ext_ranges = re.findall(r"latin-ext\.woff2'\) format\('woff2'\);\s*unicode-range: ([^;]+);", FACES)
    assert ext_ranges and all("U+0100-017F" in r and "U+0259" in r for r in ext_ranges)
    assert "U+0400-045F" in FACES and "U+0000-00FF" in FACES and "U+0131" in FACES
    assert (STATIC / "fonts" / "OFL-NOTICE.txt").is_file()


def test_font_stack_has_system_fallbacks():
    assert re.search(r'--sans:\s*"Noto Sans",[^;]*"Segoe UI"[^;]*system-ui[^;]*sans-serif', CSS)
    assert re.search(r'--serif:\s*"Noto Serif",[^;]*Georgia[^;]*serif', CSS)


def test_font_glyphs_for_azerbaijani_and_cyrillic_exist_in_the_files():
    ttlib = pytest.importorskip("fontTools.ttLib")  # необязательно: pip install fonttools
    need = "əƏğĞıİşŞçÇöÖüÜ" + "АБВабв" + "«»–—…№"
    for family in ("noto-sans", "noto-serif"):
        cmap = set()
        for part in ("latin", "latin-ext", "cyrillic"):
            cmap |= set(ttlib.TTFont(STATIC / "fonts" / f"{family}-{part}.woff2").getBestCmap())
        assert not [c for c in need if ord(c) not in cmap], family


def test_fonts_and_pages_are_served_by_the_app():
    r = client.get("/static/fonts/noto-sans-latin.woff2")
    assert r.status_code == 200 and r.headers["content-type"].startswith("font/woff2") and r.content[:4] == b"wOF2"
    assert client.get("/static/site.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/favicon.svg").status_code == 200
    for path in ("/", "/admin", "/haqqinda"):
        assert client.get(path).status_code == 200


# ---------- Фокус, движение, мобильные экраны ----------


def test_keyboard_focus_motion_and_mobile_rules_exist():
    assert ":focus-visible" in CSS and re.search(r":focus-visible\s*\{[^}]*outline:\s*3px", CSS)
    reduced = CSS.split("prefers-reduced-motion: reduce")[1]
    assert "animation: none" in reduced and "transition: none" in reduced and ".sieve" in reduced
    assert re.search(r"@media \(max-width: \d+px\)", CSS)
    assert "overflow-x: auto" in CSS  # широкие таблицы прокручиваются, а не ломают страницу


def test_the_sieve_animation_is_skipped_for_reduced_motion_in_the_script_too():
    js = page("index.html")
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js and "if (reduceMotion || frags.length === 0)" in js


# ---------- Структура страниц ----------


@pytest.mark.parametrize("name,current", PAGES.items())
def test_every_page_has_the_official_structure(name, current):
    html = page(name)
    assert '<html lang="az">' in html and 'name="viewport"' in html
    assert html.count("<h1") == 1
    assert 'class="skip" href="#main"' in html and 'id="main"' in html
    assert '<link rel="stylesheet" href="/static/fonts/faces.css">' in html and 'href="/static/site.css"' in html
    assert 'rel="icon" href="/static/favicon.svg"' in html
    assert '<header class="site-header">' in html and "<svg" in html and "Süzgəc" in html  # шапка, логотип-знак, название
    nav = html.split('aria-label="Əsas naviqasiya"')[1].split("</nav>")[0]
    for title in PAGES.values():
        assert title in nav
    assert nav.count('aria-current="page"') == 1 and re.search(rf'aria-current="page">{current}', nav)
    assert '<footer class="site-footer">' in html and "Məlumatların qorunması" in html


def test_navigation_links_point_to_the_three_pages():
    nav = page("index.html").split('aria-label="Əsas naviqasiya"')[1].split("</nav>")[0]
    assert 'href="/"' in nav and 'href="/admin"' in nav and 'href="/haqqinda"' in nav


def test_home_has_the_three_numbered_steps_in_azerbaijani():
    html = page("index.html")
    for title in ("Mətni daxil edin", "AI-yə köçürün", "Cavabı qaytarın"):
        assert f"<h2>{title}</h2>" in html
    assert [m for m in re.findall(r'class="step-no" aria-hidden="true">(\d)<', html)] == ["1", "2", "3"]
    assert "Süzgəcdən keçir" in html and 'class="sieve"' not in html  # «сито» создаётся скриптом, не лежит в разметке


def strip_comments(text: str) -> str:
    """Убрать комментарии разработчика (они могут быть на русском): остаётся то, что видит пользователь."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[;{}\s])//\s.*$", "", text)


def test_the_interface_is_fully_in_azerbaijani_and_self_contained():
    for name in (*PAGES, "restore.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"[Ѐ-ӿ]", strip_comments(text)), f"{name}: кириллица в интерфейсе"
        urls = [u for u in re.findall(r"https?://[^\s\"')]+", text) if "w3.org/2000/svg" not in u]
        assert not urls, f"{name}: внешние адреса {urls}"


def test_pages_and_styles_are_revalidated_so_updates_show_up():
    for path in ("/", "/admin", "/haqqinda", "/static/site.css", "/static/restore.js"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
    assert client.get("/api/stats").headers["cache-control"] == "no-store"
