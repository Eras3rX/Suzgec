# Сборка для Hugging Face Spaces (тип Space: Docker). Забирает код проекта из публичного репозитория GitHub,
# ставит зависимости (включая распознавание документов) и запускает сайт на порту 7860.
FROM python:3.11-slim

# libgl1 и libglib2.0-0: нужны OpenCV (поиск лиц); git: чтобы забрать код
RUN apt-get update \
    && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face запускает контейнер от пользователя 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user/app

# Эта строка меняет слой, когда в репозитории появляется новый коммит: пересборка берёт свежий код
ADD https://api.github.com/repos/Eras3rX/Suzgec/git/refs/heads/main /tmp/repo-version
RUN git clone --depth 1 https://github.com/Eras3rX/Suzgec.git .

RUN pip install --no-cache-dir -r requirements.txt -r requirements-vision.txt

# Локальная модель (PyTorch) в Space не ставится: работают правила. Ключи AI и ADMIN_TOKEN задаются в Settings -> Secrets.
ENV PII_MODEL_ENABLED=0 OCR_THREADS=2

EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips=*"]
