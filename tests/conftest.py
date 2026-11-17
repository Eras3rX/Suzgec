import pytest

import app.main as main


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch):
    """Тесты никогда не должны использовать настоящий ключ из .env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    main.limiter.reset()
    yield
    main.limiter.reset()


@pytest.fixture
def ai_on(monkeypatch):
    """Встроенный AI включён: ключ «есть», а вместо сети — заглушка, которая возвращает то, что получила."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(main, "ask_llm", lambda masked: "Ответ AI: " + masked)
