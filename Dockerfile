# Универсальная сборка для хостингов с Docker (Railway, Koyeb, Fly.io, Google Cloud Run, Render и др.).
# Порт берётся из переменной PORT (по умолчанию 8000). Ключи AI и ADMIN_TOKEN задайте в настройках хостинга.
FROM python:3.11-slim

# libgl1 и libglib2.0-0 нужны OpenCV (поиск лиц на документах)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-vision.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-vision.txt
COPY . .

# Локальная модель (PyTorch) не ставится: работают правила
ENV PII_MODEL_ENABLED=0 PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
