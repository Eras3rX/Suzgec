# Süzgəc

Шлюз между сотрудником и AI-ассистентом: находит азербайджанские персональные данные,
заменяет их метками ([FIN_1], [AD_1]…), отправляет в LLM только замаскированный текст
и возвращает в ответе исходные значения. Подробности — в [CLAUDE.md](CLAUDE.md).

## Запуск (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # ключ API необязателен: без него работает демо-режим
python -m pytest            # тесты
python tests/eval.py        # замер точности детектора (tests/eval_data.csv)
python -m uvicorn app.main:app --reload
```

Открыть в браузере: http://127.0.0.1:8000

## Структура

- `app/detector.py` — поиск персональных данных
- `app/masker.py` — маскирование и восстановление
- `app/llm.py` — вызов LLM (или демо-заглушка)
- `app/main.py` — API: `/api/check`, `/api/chat`, `/api/stats`
- `static/index.html` — страница демо
- `tests/` — тесты (только синтетические данные)
