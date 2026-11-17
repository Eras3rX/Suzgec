"""Автоматическая закраска документов: PDF и изображения (фото удостоверения, скриншот).

Что делаем:
  1. Достаём текст с координатами: из текстового слоя PDF (PyMuPDF) или распознаём картинку (RapidOCR, офлайн).
  2. Тот же детектор, что и для обычного текста, находит данные (FİN, телефон, IBAN, ФИО…) и гриф секретности.
  3. Найденные места закрашиваются чёрным по-настоящему: в PDF текст под зоной удаляется из файла, в картинке
     чёрные пиксели вшиваются в новое изображение (метаданные и EXIF не переносятся).
  4. На картинках дополнительно закрашиваются лица (OpenCV) и области подписи рядом со словом «İmza».
  5. Для AI остаётся только замаскированный текст документа (метки вместо значений).

Файл живёт только в памяти запроса: на диск ничего не пишется. Тяжёлые библиотеки подключаются лениво, без них
сайт работает как раньше (см. available()).
"""
from __future__ import annotations

import base64
import importlib.util
import io
import math
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from app.detector import classify
from app.masker import MaskResult, mask

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_PIXELS = 25_000_000       # защита от «бомб»: огромных картинок, которые съедают память
PREVIEW_PAGES = 3             # сколько страниц PDF показывать в демо
RENDER_ZOOM = 2.0             # 144 dpi: достаточно для распознавания и лиц
PREVIEW_SIDE = 1000           # длинная сторона превью в пикселях
PAD_PX = 3                    # запас вокруг закрашиваемой зоны (в пикселях картинки)

VISUAL_CATEGORIES = {"UZ": "Üz", "IMZA": "İmza"}
_SIGNATURE_WORDS = ("imza", "signature", "подпись", "podpis")


class RedactError(Exception):
    """Понятная пользователю ошибка (по-азербайджански). Текст документа в сообщения не попадает."""


def available() -> bool:
    return all(importlib.util.find_spec(m) for m in ("pymupdf", "PIL", "cv2", "rapidocr_onnxruntime", "numpy"))


def warm_up() -> None:
    """Загрузить модель распознавания в фоне, чтобы первый документ не ждал (первая загрузка занимает секунды)."""
    if not available():
        return

    def _run():
        try:
            from PIL import Image
            _ocr_items(Image.new("RGB", (200, 60), "white"))
            _face_cascade()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, name="redact-warmup", daemon=True).start()


# ---------- данные ----------

@dataclass
class Item:
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 (точки PDF или пиксели картинки)
    sep: str = " "                          # что идёт после элемента в тексте документа
    ocr: bool = False                       # распознано с картинки: терпимо к склеенным словам


@dataclass
class Region:
    box: tuple[float, float, float, float]
    category: str                           # категория данных либо UZ / IMZA


@dataclass
class RedactResult:
    kind: str = ""                          # "pdf" | "image"
    blocked: str | None = None              # ключ грифа, если документ заблокирован
    data: bytes = b""
    mime: str = ""
    pages: list[dict] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    masked: MaskResult | None = None
    page_count: int = 0
    elapsed_ms: float = 0.0


# ---------- ленивые тяжёлые объекты ----------

_lock = threading.Lock()
_engine = None
_cascade = None


def _ocr_engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # По умолчанию ONNX берёт все ядра, и на процессорах с десятками потоков это в 5-8 раз медленнее
            # (замер: 28 потоков 7.7 с, 4 потока 0.9 с на одну карточку). Поэтому потоков немного.
            threads = max(1, min(int(os.getenv("OCR_THREADS", "4")), os.cpu_count() or 1))
            _engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
        return _engine


def _face_cascade():
    global _cascade
    with _lock:
        if _cascade is None:
            import cv2
            _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        return _cascade


# ---------- геометрия: из позиций в тексте в прямоугольники ----------

_NARROW = set("il.,:;'|!tfjrI1 ")
_WIDE = set("mwMW@")


def _weights(text: str) -> list[float]:
    return [0.5 if c in _NARROW else 1.6 if c in _WIDE else 1.0 for c in text]


def _slice_box(item: Item, i0: int, i1: int) -> tuple[float, float, float, float]:
    """Прямоугольник символов [i0, i1] внутри элемента: пропорционально ширине символов."""
    x0, y0, x1, y1 = item.box
    n = len(item.text)
    if n == 0 or (i0 <= 0 and i1 >= n - 1):
        return item.box
    w = _weights(item.text)
    total = sum(w) or 1.0
    a = sum(w[:i0]) / total
    b = sum(w[: i1 + 1]) / total
    return (x0 + (x1 - x0) * a, y0, x0 + (x1 - x0) * b, y1)


def _build_text(pages: list[list[Item]]) -> tuple[str, list[tuple[int, int, int]]]:
    """Текст всего документа и карта «символ текста -> (страница, элемент, индекс символа или -1)»."""
    chars: list[str] = []
    owner: list[tuple[int, int, int]] = []
    for p, items in enumerate(pages):
        for k, item in enumerate(items):
            text = item.text
            for idx, ch in enumerate(text):
                chars.append(ch)
                owner.append((p, k, idx))
                # OCR часто склеивает «Soyadi:Quliyeva»: добавляем пробел после двоеточия (в карте он «ничей»)
                if item.ocr and ch == ":" and idx + 1 < len(text) and text[idx + 1] not in " \t":
                    chars.append(" ")
                    owner.append((p, k, -1))
            chars.append(item.sep or " ")
            owner.append((p, k, -1))
        chars.append("\n")
        owner.append((p, -1, -1))
    return "".join(chars), owner


def _regions_from_spans(spans, owner, pages: list[list[Item]]) -> list[list[Region]]:
    result: list[list[Region]] = [[] for _ in pages]
    for s in spans:
        per_item: dict[tuple[int, int], list[int]] = {}
        for pos in range(s.start, min(s.end, len(owner))):
            p, k, idx = owner[pos]
            if k >= 0 and idx >= 0:
                per_item.setdefault((p, k), []).append(idx)
        for (p, k), idxs in per_item.items():
            result[p].append(Region(_slice_box(pages[p][k], min(idxs), max(idxs)), s.category))
    return result


# ---------- картинки: OCR, лица, подпись ----------

def _to_png_b64(img, max_side: int | None = None) -> str:
    from PIL import Image
    if max_side and max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * ratio)), max(1, round(img.height * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _ocr_items(img) -> list[Item]:
    import numpy as np
    arr = np.array(img.convert("RGB"))[:, :, ::-1]  # RapidOCR ждёт BGR
    result, _ = _ocr_engine()(arr)
    items = []
    for box, text, _conf in result or []:
        xs, ys = [pt[0] for pt in box], [pt[1] for pt in box]
        if text.strip():
            items.append(Item(text.strip(), (min(xs), min(ys), max(xs), max(ys)), "\n", ocr=True))
    return items


def _overlap(a, b) -> float:
    """Доля площади a, которая лежит внутри b."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def _find_faces(img, text_boxes) -> list[Region]:
    import cv2
    import numpy as np
    scale = min(1.0, 1600 / max(img.size))
    small = img.convert("L")
    if scale < 1.0:
        small = small.resize((round(img.width * scale), round(img.height * scale)))
    gray = np.array(small)
    min_side = max(40, int(min(gray.shape[:2]) * 0.07))
    faces = _face_cascade().detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    regions = []
    for (x, y, w, h) in faces:
        box = (x / scale, y / scale, (x + w) / scale, (y + h) / scale)
        if sum(_overlap(box, tb) for tb in text_boxes) > 0.4:  # «лицо» посреди текста — это буквы, а не лицо
            continue
        pad = 0.2 * (box[2] - box[0])
        regions.append(Region((box[0] - pad, box[1] - pad * 1.4, box[2] + pad, box[3] + pad), "UZ"))
    return regions


def _find_signatures(img, items: list[Item]) -> list[Region]:
    """Грубая эвристика: «чернила» рядом со словом İmza / Signature, не принадлежащие другим строкам текста."""
    import cv2
    import numpy as np
    labels = [it for it in items if any(w in it.text.lower().replace("i̇", "i") for w in _SIGNATURE_WORDS)]
    if not labels:
        return []
    gray = np.array(img.convert("L"))
    H, W = gray.shape
    regions = []
    for label in labels:
        x0, y0, x1, y1 = label.box
        h = max(10.0, y1 - y0)
        rx0, rx1 = max(0, int(x0 - h)), min(W, int(x1 + 12 * h))
        ry0, ry1 = max(0, int(y0 - 1.5 * h)), min(H, int(y1 + 4 * h))
        if rx1 - rx0 < 5 or ry1 - ry0 < 5:
            continue
        crop = gray[ry0:ry1, rx0:rx1].copy()
        _, ink = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        for other in items:  # весь распознанный текст (и сама подпись-метка) — не чернила
            bx0, by0, bx1, by1 = other.box
            ink[max(0, int(by0 - 3 - ry0)):max(0, int(by1 + 3 - ry0)), max(0, int(bx0 - 3 - rx0)):max(0, int(bx1 + 3 - rx0))] = 0
        ink = cv2.dilate(ink, np.ones((5, 5), np.uint8))
        count, _, stats, _ = cv2.connectedComponentsWithStats(ink)
        crop_h, crop_w = ink.shape
        parts = []
        for i in range(1, count):
            x, y, w, hh, area = (int(v) for v in stats[i])
            if area < 120:
                continue  # крошки: остатки букв по краям распознанных строк
            if min(w, hh) < 0.35 * h and max(w, hh) > 4 * h:
                continue  # линия: рамка документа, подчёркивание, таблица
            if w > 0.9 * crop_w or hh > 0.9 * crop_h:
                continue  # занимает всю область: это рамка, а не подпись
            parts.append([x, y, x + w, y + hh, area])
        if not parts:
            continue
        # Подпись: самый крупный кусок чернил плюс всё, что лежит от него не дальше 1.5 высоты строки.
        parts.sort(key=lambda p: -p[4])
        merged = parts.pop(0)[:4]
        grew = True
        while grew:
            grew = False
            for p in parts[:]:
                gap = 1.5 * h
                if p[0] <= merged[2] + gap and p[2] >= merged[0] - gap and p[1] <= merged[3] + gap and p[3] >= merged[1] - gap:
                    merged = [min(merged[0], p[0]), min(merged[1], p[1]), max(merged[2], p[2]), max(merged[3], p[3])]
                    parts.remove(p)
                    grew = True
        regions.append(Region((rx0 + merged[0], ry0 + merged[1], rx0 + merged[2], ry0 + merged[3]), "IMZA"))
    return regions


def _padded(box, base: float) -> tuple[float, float, float, float]:
    """Зона с запасом: распознанные границы приблизительны, поэтому запас растёт с высотой строки."""
    x0, y0, x1, y1 = box
    pad = max(base, 0.2 * (y1 - y0))
    return (max(0.0, x0 - pad), max(0.0, y0 - pad), x1 + pad, y1 + pad)


def _paint(img, regions: list[Region]):
    from PIL import ImageDraw
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for r in regions:
        draw.rectangle(list(_padded(r.box, PAD_PX)), fill=(0, 0, 0))
    return out


def _norm_boxes(regions: list[Region], width: float, height: float, pad: float = 0.0) -> list[dict]:
    out = []
    for r in regions:
        x0, y0, x1, y1 = _padded(r.box, pad)
        x1, y1 = min(x1, width), min(y1, height)
        out.append({"x": round(x0 / width, 5), "y": round(y0 / height, 5),
                    "w": round((x1 - x0) / width, 5), "h": round((y1 - y0) / height, 5), "category": r.category})
    return out


# ---------- общий конвейер ----------

def _open_image(data: bytes):
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP", "BMP"):
            raise RedactError("Dəstəklənməyən şəkil formatı. PNG, JPG, WEBP və ya PDF yükləyin.")
        if img.width * img.height > MAX_PIXELS:
            raise RedactError("Şəkil çox böyükdür (25 meqapiksel limiti). Ölçüsünü kiçildin.")
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")  # EXIF-i yeni şəkilə köçürmürük
    except RedactError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RedactError("Fayl oxunmadı: şəkil zədəlidir və ya dəstəklənmir.") from exc


def _classify_ocr(text: str) -> str | None:
    """Гриф в распознанном тексте. OCR часто путает «ə» с a, o, e, 0 или теряет её («Moxfi», «Xidmoti»):
    для распознанных страниц такие написания тоже считаем грифом (лишняя блокировка лучше утечки)."""
    found = classify(text)
    if found:
        return found
    fixed = re.sub(r"(?i)\b(m)[aoö09](xfi)\b", r"\1e\2", text)
    fixed = re.sub(r"(?i)\b(xidm)[aoö09](ti)\b", r"\1e\2", fixed)
    return classify(fixed)


_CARD_LABELS = {  # подписи полей на удостоверении -> категория значения под подписью
    "soyadi": ("AD", 1), "adi": ("AD", 1), "atasininadi": ("AD", 1), "anasininadi": ("AD", 1),
    "dogulduguyervatarix": ("ADRES", 2),
}
_MRZ_NAMES = re.compile(r"[A-Z]<[A-Z]{3}([A-Z]+)<<([A-Z]+)")
_FOLD_LETTERS = str.maketrans({"ə": "e", "Ə": "e", "ı": "i", "İ": "i"})


def _letters(text: str) -> str:
    """Только латинские буквы без диакритики и регистра: «Atasının adı» -> «atasininadi»."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", text.translate(_FOLD_LETTERS))
    return "".join(c for c in plain.lower() if "a" <= c <= "z")


def _similar(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def _label_of(item: Item):
    key = _letters(item.text)
    if key in _CARD_LABELS:
        return _CARD_LABELS[key]
    if len(key) >= 6:  # OCR путает буквы («Sovadi»): сравниваем нестрого, но только длинные подписи
        for name, value in _CARD_LABELS.items():
            if len(name) >= 6 and _similar(key, name) >= 0.8:
                return value
    return None


def _layout_findings(pages_items: list[list[Item]], text: str, pos_of: dict) -> list:
    """Значения, которые детектор не узнал по виду, но которые понятны по макету удостоверения:
    строка под подписью «Soyadı / Adı / Atasının adı / Doğulduğu yer və tarix», а также имя и фамилия,
    повторённые в машиночитаемой зоне. Работает только для распознанных (OCR) страниц."""
    from app.detector import Finding
    extra: list = []

    def add(p: int, k: int, category: str, lo: int = 0, hi: int | None = None) -> None:
        item = pages_items[p][k]
        hi = len(item.text) if hi is None else hi
        if hi - lo < 2 or (p, k, lo) not in pos_of or (p, k, hi - 1) not in pos_of:
            return
        start, end = pos_of[(p, k, lo)], pos_of[(p, k, hi - 1)] + 1
        extra.append(Finding(category, start, end, text[start:end]))

    names: list[str] = []
    m = _MRZ_NAMES.search(text.upper())
    if m:
        names = [_letters(m.group(1)), _letters(m.group(2))]

    for p, items in enumerate(pages_items):
        if not any(it.ocr for it in items):
            continue
        for i, label in enumerate(items):
            spec = _label_of(label)
            if spec:
                category, count = spec
                lb = label.box
                h = max(10.0, lb[3] - lb[1])
                below = sorted((it.box[1], j) for j, it in enumerate(items)
                               if j != i and _label_of(it) is None and "<" not in it.text
                               and lb[1] + 0.4 * h <= it.box[1] <= lb[3] + 3.2 * h and abs(it.box[0] - lb[0]) <= 3 * h)
                for _, j in below[:count]:
                    add(p, j, category)
        if names:
            for k, it in enumerate(items):
                if "<" in it.text:
                    continue
                for w in re.finditer(r"\S+", it.text):
                    word = _letters(w.group())
                    if len(word) >= 4 and any(_similar(word, n) >= 0.75 for n in names if len(n) >= 4):
                        add(p, k, "AD", w.start(), w.end())
    return extra


def _finish_text(pages_items, doc_pages_regions_fn):
    """Склеить текст, проверить гриф, замаскировать. Возвращает (text, masked, per-page regions, blocked)."""
    text, owner = _build_text(pages_items)
    ocr_used = any(item.ocr for items in pages_items for item in items)
    blocked = _classify_ocr(text) if ocr_used else classify(text)
    if blocked:
        return text, None, None, blocked
    extra = []
    if ocr_used:
        pos_of = {key: pos for pos, key in enumerate(owner) if key[1] >= 0 and key[2] >= 0}
        extra = _layout_findings(pages_items, text, pos_of)
    masked = mask(text, extra)
    return text, masked, _regions_from_spans(masked.spans, owner, pages_items), None


def _count(result: RedactResult, masked: MaskResult | None, all_regions: list[list[Region]]) -> None:
    for regions in all_regions:
        for r in regions:
            if r.category in VISUAL_CATEGORIES:
                result.counts[r.category] += 1
    if masked:
        result.counts.update(masked.counts)


def redact_image(data: bytes) -> RedactResult:
    started = time.perf_counter()
    img = _open_image(data)
    items = _ocr_items(img)
    _text, masked, per_page, blocked = _finish_text([items], None)
    result = RedactResult(kind="image", page_count=1, masked=masked)
    if blocked:
        result.blocked = blocked
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result
    text_boxes = [it.box for it in items]
    regions = per_page[0] + _find_signatures(img, items) + _find_faces(img, text_boxes)
    out = _paint(img, regions)
    buf = io.BytesIO()
    out.save(buf, "PNG")  # новое изображение: метаданных исходного файла в нём нет
    result.data, result.mime = buf.getvalue(), "image/png"
    result.pages = [{"width": img.width, "height": img.height, "before": None,
                     "after": _to_png_b64(out, PREVIEW_SIDE),
                     "boxes": _norm_boxes(regions, img.width, img.height, PAD_PX)}]
    _count(result, masked, [regions])
    result.elapsed_ms = (time.perf_counter() - started) * 1000
    return result


def _render(page, zoom: float):
    import pymupdf
    from PIL import Image
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _page_zoom(page) -> float:
    return min(RENDER_ZOOM, math.sqrt(MAX_PIXELS / max(1.0, page.rect.width * page.rect.height)))


def _pdf_words(page) -> list[Item]:
    words = page.get_text("words", sort=True)
    items = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        newline = nxt is None or (nxt[5], nxt[6]) != (w[5], w[6])
        items.append(Item(w[4], (w[0], w[1], w[2], w[3]), "\n" if newline else " "))
    return items


def _strip_page_extras(page) -> None:
    """Аннотации и поля форм могут хранить данные вне текстового слоя: убираем."""
    try:
        for annot in list(page.annots() or []):
            page.delete_annot(annot)
        for widget in list(page.widgets() or []):
            page.delete_widget(widget)
    except Exception:  # noqa: BLE001
        pass


def redact_pdf(data: bytes) -> RedactResult:
    import pymupdf
    started = time.perf_counter()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise RedactError("Fayl oxunmadı: PDF zədəlidir və ya dəstəklənmir.") from exc
    try:
        if doc.needs_pass:
            raise RedactError("PDF parolla qorunur. Parolsuz variantı yükləyin.")
        if doc.page_count == 0:
            raise RedactError("PDF boşdur.")
        if doc.page_count > MAX_PAGES:
            raise RedactError(f"PDF çox səhifəlidir: maksimum {MAX_PAGES} səhifə.")

        kinds, pages_items, images = [], [], []
        for page in doc:
            words = _pdf_words(page)
            scanned = len(words) < 5 and bool(page.get_images())
            if scanned:  # сканированная страница: текстового слоя нет, распознаём картинку
                img = _render(page, _page_zoom(page))
                zoom = img.width / page.rect.width
                items = _ocr_items(img)
                kinds.append(("scan", zoom))
                images.append(img)
            else:
                items = words
                kinds.append(("text", _page_zoom(page)))
                images.append(None)
            pages_items.append(items)

        _text, masked, per_page, blocked = _finish_text(pages_items, None)
        result = RedactResult(kind="pdf", page_count=doc.page_count, masked=masked, mime="application/pdf")
        if blocked:
            result.blocked = blocked
            result.elapsed_ms = (time.perf_counter() - started) * 1000
            return result

        out = pymupdf.open()
        all_regions: list[list[Region]] = []
        for i, page in enumerate(doc):
            kind, zoom = kinds[i]
            items, regions = pages_items[i], list(per_page[i])
            width, height = page.rect.width, page.rect.height
            before = _to_png_b64(_render(page, 1.4), PREVIEW_SIDE) if i < PREVIEW_PAGES else None
            if kind == "scan":
                img = images[i]
                regions += _find_signatures(img, items) + _find_faces(img, [it.box for it in items])
                painted = _paint(img, regions)
                new = out.new_page(width=width, height=height)
                buf = io.BytesIO()
                painted.save(buf, "PNG")
                new.insert_image(new.rect, stream=buf.getvalue())
                norm_w, norm_h, offset = img.width, img.height, PAD_PX
                after_img = painted
            else:
                render = _render(page, zoom)  # для лиц и подписей ищем на отрисованной странице
                visual = _find_signatures(render, [Item(it.text, tuple(c * zoom for c in it.box)) for it in items])
                visual += _find_faces(render, [tuple(c * zoom for c in it.box) for it in items])
                regions += [Region(tuple(c / zoom for c in r.box), r.category) for r in visual]
                _strip_page_extras(page)
                for r in regions:
                    page.add_redact_annot(pymupdf.Rect(*_padded(r.box, 1.5)), fill=(0, 0, 0))
                page.apply_redactions()  # текст и пиксели картинок под зоной удаляются из файла
                out.insert_pdf(doc, from_page=i, to_page=i)
                norm_w, norm_h, offset = width, height, 1.5
                after_img = None
            if i < PREVIEW_PAGES:
                if after_img is None:
                    after_img = _render(out[-1], 1.4)
                result.pages.append({"width": norm_w, "height": norm_h, "before": before,
                                     "after": _to_png_b64(after_img, PREVIEW_SIDE),
                                     "boxes": _norm_boxes(regions, norm_w, norm_h, offset)})
            all_regions.append(regions)

        out.set_metadata({})
        try:
            out.del_xml_metadata()
        except Exception:  # noqa: BLE001
            pass
        result.data = out.tobytes(garbage=4, deflate=True, clean=True)
        out.close()
        _count(result, masked, all_regions)
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        return result
    finally:
        doc.close()


def redact_file(data: bytes) -> RedactResult:
    """Определяем тип по содержимому (а не по имени и заголовку запроса) и закрашиваем."""
    if not data:
        raise RedactError("Fayl boşdur.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise RedactError("Fayl çox böyükdür: maksimum 10 MB.")
    if data[:5] == b"%PDF-":
        return redact_pdf(data)
    return redact_image(data)
