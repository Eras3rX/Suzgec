"""Проверка подключения встроенного AI.

    python scripts/check_ai.py            проверить подключение (один маленький запрос с придуманным текстом)
    python scripts/check_ai.py --models   показать названия моделей, доступных по вашему ключу (для LLM_MODEL)

Ключ нигде не печатается.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.llm import LLMError, _base_url, _provider, ai_enabled, ask_llm  # noqa: E402

if not ai_enabled():
    print("Ключ не найден. Откройте файл .env, впишите ключ (ANTHROPIC_API_KEY или LLM_API_KEY с адресом и моделью) и запустите проверку снова.")
    raise SystemExit(1)

if "--models" in sys.argv:
    if _provider() != "openai":
        print("Список моделей нужен только для варианта LLM_*. Для Claude название модели задано в ANTHROPIC_MODEL.")
        raise SystemExit(1)
    try:
        response = httpx.get(_base_url() + "/models", headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"]}, timeout=30)
        response.raise_for_status()
        ids = sorted(m["id"] for m in response.json().get("data", []) if isinstance(m, dict) and "id" in m)
    except (LLMError, httpx.HTTPError, ValueError, KeyError) as exc:
        print("Не удалось получить список моделей:", type(exc).__name__, "(проверьте LLM_API_KEY и LLM_BASE_URL)")
        raise SystemExit(1)
    print(f"Доступно моделей: {len(ids)}")
    for model_id in ids:
        print(" ", model_id)
    raise SystemExit(0)

started = time.perf_counter()
try:
    answer = ask_llm("Ответь одним коротким предложением и обязательно повтори метку [AD_1] без изменений: поздоровайся с [AD_1].")
except LLMError as exc:
    print("Не получилось:", exc)
    raise SystemExit(1)

print(f"Подключение работает ({time.perf_counter() - started:.1f} с).")
print("Ответ AI:", answer)
print("Метка сохранена:", "да" if "[AD_1]" in answer else "нет (AI изменил метку, программа всё равно её восстановит по смыслу)")
