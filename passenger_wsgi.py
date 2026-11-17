"""Точка входа для хостингов с панелью cPanel («Setup Python App») и Passenger.

Они запускают WSGI-приложение, а SiftGuard написан на FastAPI (ASGI), поэтому между ними стоит переходник a2wsgi.
Для остальных хостов этот файл не нужен: они запускают `uvicorn app.main:app`.
"""
from a2wsgi import ASGIMiddleware

from app.main import app

application = ASGIMiddleware(app)
