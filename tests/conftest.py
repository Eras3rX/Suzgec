import pytest

import app.main as main
from app.audit import AuditLog


@pytest.fixture(autouse=True)
def isolated_audit(monkeypatch, tmp_path_factory):
    """Тесты пишут журнал аудита во временную папку с тестовым ключом, а не в data/audit."""
    log = AuditLog(tmp_path_factory.mktemp("audit"), key=b"test-audit-key")
    monkeypatch.setattr(main, "audit", log)
    return log


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch):
    """Тесты никогда не должны использовать настоящий ключ из .env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("PII_MODEL_ENABLED", "0")  # тяжёлую модель в обычных тестах не грузим
    main.limiter.reset()
    yield
    main.limiter.reset()


@pytest.fixture
def ai_on(monkeypatch):
    """Встроенный AI включён: ключ «есть», а вместо сети — заглушка, которая возвращает то, что получила."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(main, "ask_llm", lambda masked: "Ответ AI: " + masked)
