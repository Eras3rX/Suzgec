"""Закраска PDF и изображений (только синтетические документы, никаких реальных людей)."""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import redact

pytestmark = pytest.mark.skipif(not redact.available(), reason="библиотеки для документов не установлены (requirements-vision.txt)")

pymupdf = pytest.importorskip("pymupdf")
PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

client = TestClient(main.app)
local = TestClient(main.app, client=("127.0.0.1", 50000))

FIN = "5ARZ8D2"
PHONE_DIGITS = "501234567"
NAME = "Quliyeva"
CARD_LINES = ["Soyadi: Quliyeva", "Adi: Aysel", "FIN: 5ARZ8D2", "Telefon: +994 50 123 45 67", "Seriya: AZE12345678", "Imza:"]


# ---------- синтетические документы ----------

def make_pdf(lines, pages=1, metadata=None) -> bytes:
    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + 20 * i), line, fontsize=11)
    if metadata:
        doc.set_metadata(metadata)
    return doc.tobytes()


def make_card(lines=CARD_LINES, signature=True, size=(900, 520), frame=False) -> bytes:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    if frame:
        draw.rectangle([10, 10, size[0] - 11, size[1] - 11], outline="black", width=3)
    font = ImageFont.load_default(size=32)
    for i, text in enumerate(lines):
        draw.text((40, 30 + i * 56), text, fill="black", font=font)
    if signature:
        draw.line([(170, 400), (210, 360), (250, 420), (300, 350), (340, 410), (410, 380)], fill="black", width=5)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def pdf_text(data: bytes) -> str:
    return "\n".join(page.get_text() for page in pymupdf.open(stream=data, filetype="pdf"))


@pytest.fixture(scope="module", autouse=True)
def warm_ocr():
    redact._ocr_items(Image.new("RGB", (100, 40), "white"))


# ---------- PDF с текстовым слоем ----------

def test_pdf_values_are_removed_from_the_file():
    result = redact.redact_file(make_pdf([f"Aysel Quliyeva, FIN {FIN}, telefon +994 50 123 45 67.", "IBAN AZ21NABZ00000000137010001944."]))
    text = pdf_text(result.data)
    for secret in (FIN, NAME, "Aysel", "123 45 67", "NABZ"):
        assert secret not in text, secret
    assert {"AD", "FIN", "TEL", "IBAN"} <= set(result.counts)
    assert result.kind == "pdf" and result.mime == "application/pdf"


def test_pdf_leaves_harmless_text_alone():
    result = redact.redact_file(make_pdf(["Bu gun hava yaxsidir.", f"FIN {FIN}."]))
    text = pdf_text(result.data)
    assert "hava" in text and FIN not in text


def test_pdf_masked_text_has_labels_not_values():
    result = redact.redact_file(make_pdf([f"Aysel Quliyeva, FIN {FIN}."]))
    assert "[FIN_1]" in result.masked.masked_text and FIN not in result.masked.masked_text


def test_pdf_without_data_is_unchanged_in_text():
    result = redact.redact_file(make_pdf(["Bu gun hava yaxsidir."]))
    assert not result.counts and "hava" in pdf_text(result.data)


def test_pdf_metadata_is_stripped():
    meta = {"author": "Aysel Quliyeva", "title": f"FIN {FIN}", "subject": "gizli", "keywords": "x"}
    result = redact.redact_file(make_pdf([f"FIN {FIN}"], metadata=meta))
    out_meta = pymupdf.open(stream=result.data, filetype="pdf").metadata
    assert not any((out_meta.get(k) or "") for k in ("author", "title", "subject", "keywords"))
    assert b"Quliyeva" not in result.data and FIN.encode() not in result.data


def test_pdf_multiple_pages_share_labels():
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 100), f"FIN {FIN}", fontsize=11)
    result = redact.redact_file(doc.tobytes())
    assert result.masked.masked_text.count("[FIN_1]") == 2 and "[FIN_2]" not in result.masked.masked_text
    assert FIN not in pdf_text(result.data) and result.page_count == 2


def test_pdf_classified_document_is_blocked_whole():
    result = redact.redact_file(make_pdf(["Mexfi.", f"Aysel Quliyeva FIN {FIN}"]))
    assert result.blocked and result.data == b"" and result.masked is None


def test_pdf_preview_boxes_are_inside_the_page():
    result = redact.redact_file(make_pdf([f"FIN {FIN}"]))
    page = result.pages[0]
    assert page["before"] and page["after"] and page["boxes"]
    for b in page["boxes"]:
        assert 0 <= b["x"] <= 1 and 0 <= b["y"] <= 1 and b["w"] > 0 and b["h"] > 0 and b["x"] + b["w"] <= 1.001


def test_pdf_errors_are_clear():
    with pytest.raises(redact.RedactError):
        redact.redact_file(b"%PDF-1.4 not really a pdf")
    with pytest.raises(redact.RedactError):
        redact.redact_file(make_pdf(["x"], pages=redact.MAX_PAGES + 1))
    doc = pymupdf.open(); doc.new_page()
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="1", owner_pw="2")
    with pytest.raises(redact.RedactError):
        redact.redact_file(locked)


def test_scanned_pdf_page_is_recognized_and_painted():
    """PDF без текстового слоя: страница — картинка. Распознаём, закрашиваем, отдаём картинкой."""
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=520)
    page.insert_image(page.rect, stream=make_card())
    result = redact.redact_file(doc.tobytes())
    assert {"FIN", "TEL"} <= set(result.counts)
    assert pdf_text(result.data).strip() == ""  # текстового слоя нет, значений в виде текста тоже


# ---------- изображения ----------

def test_image_values_are_not_readable_after_redaction():
    result = redact.redact_file(make_card())
    assert {"FIN", "TEL", "AD"} <= set(result.counts)
    again = redact._ocr_items(Image.open(io.BytesIO(result.data)))
    joined = " ".join(item.text for item in again)
    for secret in (FIN, PHONE_DIGITS, NAME):
        assert secret not in joined.replace(" ", ""), secret


def test_image_labels_stay_and_pixels_are_black():
    result = redact.redact_file(make_card())
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert out.size == (900, 520)
    boxes = result.pages[0]["boxes"]
    assert boxes
    for b in boxes:  # центр каждой зоны чёрный
        cx, cy = int((b["x"] + b["w"] / 2) * out.width), int((b["y"] + b["h"] / 2) * out.height)
        assert out.getpixel((cx, cy)) == (0, 0, 0), b


def test_image_signature_area_is_painted():
    result = redact.redact_file(make_card())
    assert result.counts.get("IMZA") == 1
    sig = [b for b in result.pages[0]["boxes"] if b["category"] == "IMZA"][0]
    assert sig["y"] > 0.55  # под словом «Imza», где нарисована подпись


def test_image_signature_zone_is_tight_and_frame_is_kept():
    """Рамка документа и соседние строки — не подпись: закрашивается только сама подпись."""
    result = redact.redact_file(make_card(frame=True))
    sig = [b for b in result.pages[0]["boxes"] if b["category"] == "IMZA"][0]
    assert sig["w"] < 0.55 and sig["h"] < 0.3, sig
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert out.getpixel((11, 300)) == (0, 0, 0)  # линия рамки на месте (чёрная и так), но зона вокруг неё не залита
    assert out.getpixel((60, 480)) == (255, 255, 255)  # пустое место внизу карточки не закрашено


LAYOUT_LINES = ["Seriya AZE N 12345678", "Soyadi", "QULIYEVA", "Adi", "AYSEL", "Atasinin adi", "RAMIZ OGLU",
                "I<AZEQULIYEVA<<AYSEL<<<<<<<<<<<<<<<", "12345678<3AZE9001019F25010125NFMSGB0"]


def test_id_card_layout_series_mrz_and_values_under_labels():
    """Как на настоящем удостоверении: серия через знак номера, значения под подписями, MRZ внизу."""
    result = redact.redact_file(make_card(lines=LAYOUT_LINES, signature=False, size=(1000, 640)))
    masked = result.masked.masked_text
    for secret in ("12345678", "QULIYEVA", "AYSEL", "RAMIZ", "MSGB0", "<<"):
        assert secret not in masked, (secret, masked)
    assert result.counts.get("ID", 0) >= 3 and result.counts.get("AD", 0) >= 2
    again = redact._ocr_items(Image.open(io.BytesIO(result.data)))
    joined = "".join(item.text for item in again)
    for secret in ("QULIYEVA", "AYSEL", "<<"):
        assert secret not in joined, secret


BILINGUAL_LINES = ["SOYADI / SURNAME", "DAVIS", "ADI / GIVEN NAME", "RACHEL", "ATASININ ADI / PATRONYMIC", "MEHEMMED QIZI",
                   "DOGULDUGU TARIX / DATE OF BIRTH", "05.04.1991", "VESIQENIN NOMRESI / CARD NO", "TC1234567"]


def test_bilingual_labels_names_dates_and_card_number():
    """Фото нового образца: подписи на двух языках, латинские имена, номер карточки «TC1234567»."""
    result = redact.redact_file(make_card(lines=BILINGUAL_LINES, signature=False, size=(1000, 700)))
    masked = result.masked.masked_text
    for secret in ("DAVIS", "RACHEL", "MEHEMMED", "05.04.1991", "TC1234567"):
        assert secret not in masked, (secret, masked)
    assert {"AD", "TARIX", "ID"} <= set(result.counts)


def test_patronymic_line_is_a_name_even_without_label():
    masked = redact.redact_file(make_card(lines=["Bu gun hava yaxsidir.", "MEHEMMED QIZI"], signature=False)).masked.masked_text
    assert "MEHEMMED" not in masked and "hava" in masked  # обычная строка рядом не закрашена


def test_word_matching_tolerates_ocr_mistakes():
    assert redact._contains_word("ninimzasiholierssioatuee", "imza", 0.99)
    assert redact._contains_word("ninaolpatronim", "patronim")
    assert redact._contains_word("hodlerssignature", "signature")
    assert not redact._contains_word("telefon", "surname")


def test_signature_is_found_when_label_is_garbled_at_native_scale(monkeypatch):
    """Метка «İmza» на мелком фото читается нестабильно: если на исходном масштабе её нет, пробуем другие."""
    img = Image.open(io.BytesIO(make_card(lines=["SOYADI / SURNAME", "DAVIS", "Imza:"])))
    first = [it for it in redact._ocr_items(img) if "imza" not in redact._letters(it.text)]  # «прочитали» без метки
    boxes = redact._signature_label_boxes(img, first)
    assert boxes  # метка найдена на другом масштабе
    x0, y0, x1, y1 = boxes[0]
    assert 100 < y0 < 250  # там, где она нарисована (третья строка), а не где-то ещё


def test_extra_ocr_passes_only_for_id_like_pages():
    img = Image.open(io.BytesIO(make_card(lines=["Bu gun hava yaxsidir."], signature=False)))
    items = redact._ocr_items(img)
    calls = []
    real = redact._ocr_items
    redact._ocr_items = lambda im: calls.append(im.size) or real(im)
    try:
        assert redact._signature_label_boxes(img, items) == []
    finally:
        redact._ocr_items = real
    assert calls == []  # обычная страница: дополнительных проходов OCR нет


def test_names_repeated_from_mrz_are_found_even_if_label_is_missing():
    # подписи «Adi» нет, но имя повторено в MRZ: находим по совпадению (нестрогому: OCR ошибается в буквах)
    lines = ["QULIYEVA", "AYSEL", "I<AZEQULIYEVA<<AYSEL<<<<<<<<<<<<<<<"]
    masked = redact.redact_file(make_card(lines=lines, signature=False)).masked.masked_text
    assert "QULIYEVA" not in masked and "AYSEL" not in masked


def test_label_matching_is_fuzzy_but_not_loose():
    item = lambda text: redact.Item(text, (0, 0, 10, 10), "\n", ocr=True)  # noqa: E731
    assert redact._label_of(item("Sovadi")) is not None        # OCR: y -> v
    assert redact._label_of(item("Atasinin adi")) is not None
    assert redact._label_of(item("Dogulduguyervatarix")) is not None
    assert redact._label_of(item("Telefon")) is None
    assert redact._label_of(item("Soyad nomresi 123")) is None


def test_image_without_signature_word_is_not_painted():
    result = redact.redact_file(make_card(lines=["Bu gun hava yaxsidir."], signature=True))
    assert not result.counts.get("IMZA")


def test_image_exif_is_not_carried_over():
    img = Image.open(io.BytesIO(make_card()))
    exif = Image.Exif(); exif[0x010E] = "Aysel Quliyeva FIN 5ARZ8D2"; exif[0x013B] = "Aysel"
    buf = io.BytesIO(); img.convert("RGB").save(buf, "JPEG", exif=exif.tobytes())
    result = redact.redact_file(buf.getvalue())
    assert len(Image.open(io.BytesIO(result.data)).getexif()) == 0
    assert b"Quliyeva" not in result.data and b"5ARZ8D2" not in result.data


def test_image_face_region_is_painted(monkeypatch):
    class FakeCascade:
        def detectMultiScale(self, *_a, **_k):
            return [(600, 150, 120, 150)]

    monkeypatch.setattr(redact, "_face_cascade", lambda: FakeCascade())
    result = redact.redact_file(make_card(lines=["Bu gun hava yaxsidir."], signature=False))
    assert result.counts.get("UZ") == 1
    out = Image.open(io.BytesIO(result.data)).convert("RGB")
    assert out.getpixel((660, 225)) == (0, 0, 0)


def test_image_text_that_looks_like_a_face_is_ignored(monkeypatch):
    class FakeCascade:
        def detectMultiScale(self, *_a, **_k):
            return [(30, 20, 400, 60)]  # поверх строки текста

    monkeypatch.setattr(redact, "_face_cascade", lambda: FakeCascade())
    result = redact.redact_file(make_card(lines=["Bu gun hava yaxsidir."], signature=False))
    assert not result.counts.get("UZ")


@pytest.mark.parametrize("marker", ["Mexfi", "Moxfi", "Maxfi", "Tam moxfi", "Gizli"])
def test_image_classified_is_blocked_even_when_ocr_misreads_schwa(marker):
    # OCR читает «ə» как e, o или a: любой из вариантов должен блокировать документ
    result = redact.redact_file(make_card(lines=[marker + " sened", f"FIN {FIN}"], signature=False))
    assert result.blocked and result.data == b""


def test_image_word_that_only_contains_marker_is_not_blocked():
    result = redact.redact_file(make_card(lines=["Gizlilik siyaseti", f"FIN {FIN}"], signature=False))
    assert result.blocked is None


def test_image_errors_are_clear():
    with pytest.raises(redact.RedactError):
        redact.redact_file(b"this is not an image")
    with pytest.raises(redact.RedactError):
        redact.redact_file(b"")
    huge = Image.new("L", (6000, 6000), 255)  # 36 Мпикс > лимита 25 Мпикс, но PNG маленький
    buf = io.BytesIO(); huge.save(buf, "PNG")
    with pytest.raises(redact.RedactError):
        redact.redact_file(buf.getvalue())


def test_gif_is_not_accepted():
    buf = io.BytesIO(); Image.new("RGB", (50, 50), "white").save(buf, "GIF")
    with pytest.raises(redact.RedactError):
        redact.redact_file(buf.getvalue())


def test_type_is_detected_by_content_not_by_name():
    # браузер может прислать что угодно в заголовках: решает содержимое
    assert redact.redact_file(make_pdf([f"FIN {FIN}"])).kind == "pdf"
    assert redact.redact_file(make_card(lines=["Bu gun hava"], signature=False)).kind == "image"


# ---------- API ----------

def post(data: bytes, **headers):
    return local.post("/api/redact", content=data, headers={"Content-Type": "application/octet-stream", **headers})


def test_api_pdf_roundtrip_and_no_values_in_json():
    r = post(make_pdf([f"Aysel Quliyeva, FIN {FIN}, telefon +994 50 123 45 67."]), **{"X-Department": "maliyye"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "pdf" and body["blocked"] is False
    out = base64.b64decode(body["file"]["data"])
    assert out[:5] == b"%PDF-" and FIN not in pdf_text(out)
    # в «masked_text» нет значений; превью «до» принадлежит самому пользователю и в тест не входит
    assert FIN not in json.dumps({k: v for k, v in body.items() if k != "pages"})
    assert "[FIN_1]" in body["masked_text"]


def test_api_image_result_is_png():
    body = post(make_card()).json()
    assert body["kind"] == "image" and body["file"]["name"].endswith(".png")
    assert base64.b64decode(body["file"]["data"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert body["pages"][0]["before"] is None  # исходная картинка уже у пользователя


def test_api_counts_stats_and_department():
    before = local.get("/api/admin/stats").json()
    post(make_pdf([f"FIN {FIN}"]), **{"X-Department": "hr"})
    after = local.get("/api/admin/stats").json()
    assert after["documents"]["files"] == before["documents"]["files"] + 1
    assert after["categories"].get("FIN", 0) == before["categories"].get("FIN", 0) + 1
    hr = {d["id"]: d for d in after["departments"]}["hr"]
    assert hr["requests"] >= 1


def test_api_audit_log_has_hashes_only(isolated_audit):
    post(make_pdf([f"Aysel Quliyeva FIN {FIN}"]))
    text = isolated_audit.path.read_text(encoding="utf-8")
    assert '"channel":"redact"' in text
    assert FIN not in text and NAME not in text


def test_api_classified_document_is_blocked_and_reported():
    r = post(make_pdf(["Tam mexfi", f"FIN {FIN}"]))
    assert r.status_code == 403 and r.json()["blocked"] is True
    inc = local.get("/api/admin/incidents").json()["incidents"]
    assert any(i["channel"] == "redact" for i in inc)  # журнал общий для всех тестов: ищем нашу запись
    assert FIN not in json.dumps(inc)


def test_api_bad_files_get_clear_errors():
    assert post(b"garbage").status_code == 422
    assert post(b"").status_code == 422


def test_api_oversized_upload_is_rejected():
    assert post(b"0" * (redact.MAX_UPLOAD_BYTES + 1)).status_code == 413


def test_api_busy_returns_429(monkeypatch):
    from threading import BoundedSemaphore
    monkeypatch.setattr(main, "_redact_slots", BoundedSemaphore(1))
    main._redact_slots.acquire()
    assert post(make_pdf(["x"])).status_code == 429


def test_api_unavailable_returns_503(monkeypatch):
    monkeypatch.setattr(redact, "available", lambda: False)
    assert post(make_pdf(["x"])).status_code == 503


def test_api_nothing_logged_and_no_files_written(caplog, capfd, tmp_path, monkeypatch):
    import logging
    caplog.set_level(logging.DEBUG)
    monkeypatch.chdir(tmp_path)
    post(make_pdf([f"Aysel Quliyeva FIN {FIN}"]))
    post(make_card())
    post(b"garbage")
    out = capfd.readouterr()
    assert FIN not in caplog.text + out.out + out.err and NAME not in caplog.text + out.out + out.err
    assert list(tmp_path.iterdir()) == []


def test_api_rate_limit_applies_to_uploads():
    main.limiter.reset()
    codes = [client.post("/api/redact", content=b"x", headers={"Content-Type": "application/octet-stream"}).status_code
             for _ in range(main.RATE_LIMIT_PER_MINUTE + 3)]
    assert 429 in codes


def test_config_and_page():
    assert client.get("/api/config").json()["redact_available"] is True
    page = client.get("/sened")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "/api/redact" in page.text
