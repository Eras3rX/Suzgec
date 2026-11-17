"""Сообщения сервера пользователю на трёх языках (az, ru, en).

Язык приходит в заголовке запроса X-Lang (его ставит страница по выбору пользователя); по умолчанию азербайджанский.
Текст пользователя и значения данных в сообщения не попадают: только фиксированные фразы и числа.
"""
from __future__ import annotations

LANGS = ("az", "ru", "en")
DEFAULT_LANG = "az"

MESSAGES: dict[str, dict[str, str]] = {
    "file_too_big": {
        "az": "Fayl çox böyükdür: maksimum 10 MB.",
        "ru": "Файл слишком большой: максимум 10 МБ.",
        "en": "The file is too large: 10 MB maximum.",
    },
    "body_too_big": {
        "az": "Sorğu çox böyükdür. Mətn {limit} simvoldan çox olmamalıdır.",
        "ru": "Запрос слишком большой. Текст не должен быть длиннее {limit} символов.",
        "en": "The request is too large. The text must not exceed {limit} characters.",
    },
    "rate_limited": {
        "az": "Çox sorğu göndərilib. {seconds} san. gözləyin və yenidən cəhd edin.",
        "ru": "Отправлено слишком много запросов. Подождите {seconds} с и повторите.",
        "en": "Too many requests. Wait {seconds} s and try again.",
    },
    "internal_error": {
        "az": "Serverdə daxili xəta baş verdi. Yenidən cəhd edin.",
        "ru": "На сервере произошла внутренняя ошибка. Повторите попытку.",
        "en": "An internal server error occurred. Please try again.",
    },
    "text_too_long": {
        "az": "Mətn çox uzundur: maksimum {limit} simvol. Qısaldın və ya hissələrlə göndərin.",
        "ru": "Текст слишком длинный: максимум {limit} символов. Сократите его или отправьте по частям.",
        "en": "The text is too long: {limit} characters maximum. Shorten it or send it in parts.",
    },
    "text_empty": {
        "az": "Mətn daxil edin: sahə boş ola bilməz.",
        "ru": "Введите текст: поле не может быть пустым.",
        "en": "Enter some text: the field cannot be empty.",
    },
    "bad_request": {
        "az": 'Yanlış sorğu: {"text": "..."} formatında JSON gözlənilir.',
        "ru": 'Неверный запрос: ожидается JSON вида {"text": "..."}.',
        "en": 'Invalid request: JSON like {"text": "..."} is expected.',
    },
    "blocked": {
        "az": "«{name}» qrifi aşkarlandı. Sorğu tam bloklandı: mətn maskalanmadı və AI-yə göndərilmədi. İnzibatçıya bildiriş göndərildi.",
        "ru": "Обнаружен гриф «{name}». Запрос полностью заблокирован: текст не маскировался и не отправлялся в AI. Администратору отправлено уведомление.",
        "en": "Classification marking “{name}” detected. The request was blocked entirely: the text was not masked and not sent to the AI. The administrator has been notified.",
    },
    "ai_off_paste": {
        "az": "Daxili AI quraşdırılmayıb (API açarı yoxdur). Mətni kopyalayıb istənilən AI-yə yapışdırın.",
        "ru": "Встроенный AI не настроен (нет ключа API). Скопируйте текст и вставьте его в любой AI.",
        "en": "The built-in AI is not set up (no API key). Copy the text and paste it into any AI.",
    },
    "ai_off_doc": {
        "az": "Daxili AI quraşdırılmayıb (API açarı yoxdur). Qaralanmış sənədi əl ilə AI-yə köçürün.",
        "ru": "Встроенный AI не настроен (нет ключа API). Перенесите закрашенный документ в AI вручную.",
        "en": "The built-in AI is not set up (no API key). Copy the redacted document into an AI by hand.",
    },
    "daily_limit": {
        "az": "Gündəlik demo limiti bitib. Sabah yenidən cəhd edin.",
        "ru": "Дневной демо-лимит исчерпан. Попробуйте завтра.",
        "en": "The daily demo limit has been reached. Try again tomorrow.",
    },
    "redact_unavailable": {
        "az": "Sənəd qoruması quraşdırılmayıb (requirements-vision.txt).",
        "ru": "Защита документов не установлена (requirements-vision.txt).",
        "en": "Document protection is not installed (requirements-vision.txt).",
    },
    "busy": {
        "az": "Sistem məşğuldur: sənəd emal olunur. Bir az gözləyib yenidən cəhd edin.",
        "ru": "Система занята: идёт обработка документа. Подождите немного и повторите.",
        "en": "The system is busy processing a document. Wait a moment and try again.",
    },
    "redact_failed": {
        "az": "Sənəd emal olunmadı. Başqa fayl yoxlayın.",
        "ru": "Не удалось обработать документ. Попробуйте другой файл.",
        "en": "The document could not be processed. Try another file.",
    },
    "doc_ai_failed": {
        "az": "Sənəd AI-yə göndərilə bilmədi.",
        "ru": "Не удалось отправить документ в AI.",
        "en": "The document could not be sent to the AI.",
    },
    "admin_token": {
        "az": "İnzibatçı tokeni lazımdır.",
        "ru": "Нужен токен администратора.",
        "en": "The administrator token is required.",
    },
    "admin_local": {
        "az": "İdarəetmə paneli yalnız bu kompüterdən açılır. Uzaqdan giriş üçün ADMIN_TOKEN təyin edin.",
        "ru": "Панель управления открывается только с этого компьютера. Для удалённого доступа задайте ADMIN_TOKEN.",
        "en": "The admin panel opens only from this computer. Set ADMIN_TOKEN for remote access.",
    },
    "eval_running": {
        "az": "Test artıq işləyir. Bir az gözləyin.",
        "ru": "Тест уже выполняется. Подождите немного.",
        "en": "The test is already running. Wait a moment.",
    },
    # --- AI ---
    "llm_url_missing": {
        "az": ".env faylında LLM_BASE_URL təyin edilməyib.",
        "ru": "В файле .env не задан LLM_BASE_URL.",
        "en": "LLM_BASE_URL is not set in the .env file.",
    },
    "llm_url_https": {
        "az": "LLM_BASE_URL https:// ilə başlamalıdır (yalnız yerli server üçün http:// olar).",
        "ru": "LLM_BASE_URL должен начинаться с https:// (http:// допустим только для локального сервера).",
        "en": "LLM_BASE_URL must start with https:// (http:// is allowed only for a local server).",
    },
    "llm_model_missing": {
        "az": ".env faylında LLM_MODEL təyin edilməyib.",
        "ru": "В файле .env не задан LLM_MODEL.",
        "en": "LLM_MODEL is not set in the .env file.",
    },
    "anthropic_model_missing": {
        "az": ".env faylında ANTHROPIC_MODEL dəyişəni təyin edilməyib.",
        "ru": "В файле .env не задана переменная ANTHROPIC_MODEL.",
        "en": "ANTHROPIC_MODEL is not set in the .env file.",
    },
    "llm_not_configured": {
        "az": "Daxili AI quraşdırılmayıb: API açarı təyin edilməyib.",
        "ru": "Встроенный AI не настроен: не задан ключ API.",
        "en": "The built-in AI is not set up: no API key is configured.",
    },
    "ai_unavailable": {
        "az": "AI xidməti hazırda əlçatmazdır. Bir az sonra yenidən cəhd edin.",
        "ru": "Сервис AI сейчас недоступен. Повторите чуть позже.",
        "en": "The AI service is unavailable right now. Please try again shortly.",
    },
    # --- документы ---
    "img_unsupported": {
        "az": "Dəstəklənməyən şəkil formatı. PNG, JPG, WEBP və ya PDF yükləyin.",
        "ru": "Неподдерживаемый формат изображения. Загрузите PNG, JPG, WEBP или PDF.",
        "en": "Unsupported image format. Upload a PNG, JPG, WEBP or PDF.",
    },
    "img_too_big_px": {
        "az": "Şəkil çox böyükdür (25 meqapiksel limiti). Ölçüsünü kiçildin.",
        "ru": "Изображение слишком большое (лимит 25 мегапикселей). Уменьшите его размер.",
        "en": "The image is too large (25 megapixel limit). Reduce its size.",
    },
    "img_unreadable": {
        "az": "Fayl oxunmadı: şəkil zədəlidir və ya dəstəklənmir.",
        "ru": "Файл не прочитан: изображение повреждено или не поддерживается.",
        "en": "The file could not be read: the image is damaged or unsupported.",
    },
    "pdf_unreadable": {
        "az": "Fayl oxunmadı: PDF zədəlidir və ya dəstəklənmir.",
        "ru": "Файл не прочитан: PDF повреждён или не поддерживается.",
        "en": "The file could not be read: the PDF is damaged or unsupported.",
    },
    "pdf_password": {
        "az": "PDF parolla qorunur. Parolsuz variantı yükləyin.",
        "ru": "PDF защищён паролем. Загрузите версию без пароля.",
        "en": "The PDF is password-protected. Upload a version without a password.",
    },
    "pdf_empty": {
        "az": "PDF boşdur.",
        "ru": "PDF пуст.",
        "en": "The PDF is empty.",
    },
    "pdf_pages": {
        "az": "PDF çox səhifəlidir: maksimum {limit} səhifə.",
        "ru": "В PDF слишком много страниц: максимум {limit}.",
        "en": "The PDF has too many pages: {limit} maximum.",
    },
    "file_empty": {
        "az": "Fayl boşdur.",
        "ru": "Файл пуст.",
        "en": "The file is empty.",
    },
}


def lang_of(value: str | None) -> str:
    """Язык из заголовка X-Lang; всё неизвестное означает азербайджанский."""
    value = (value or "").strip().lower()[:2]
    return value if value in LANGS else DEFAULT_LANG


def tr(key: str, lang: str = DEFAULT_LANG, **params) -> str:
    """Сообщение по ключу. Если ключа нет, возвращается сама строка (так работают и обычные тексты ошибок)."""
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry[DEFAULT_LANG]
    return text.format(**params) if params else text


class KeyedError(Exception):
    """Ошибка с ключом сообщения: на языке пользователя её текст собирает сервер (см. localized())."""

    def __init__(self, key: str, **params):
        self.key = key
        self.params = params
        super().__init__(tr(key, DEFAULT_LANG, **params))

    def localized(self, lang: str) -> str:
        return tr(self.key, lang, **self.params)
