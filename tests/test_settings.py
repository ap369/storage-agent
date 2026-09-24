from settings import Settings


def test_settings_reads_required_values_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("SANDBOX_ROOT", "/tmp/sandbox")
    monkeypatch.setenv("API_TOKEN", "bearer-token")

    settings = Settings(_env_file=None)

    assert settings.LLM_BASE_URL == "https://example.com/v1"
    assert settings.LLM_API_KEY == "secret-key"
    assert settings.LLM_MODEL == "gpt-4o-mini"
    assert settings.SANDBOX_ROOT == "/tmp/sandbox"
    assert settings.API_TOKEN == "bearer-token"


def test_settings_has_sensible_defaults(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("SANDBOX_ROOT", "/tmp/sandbox")
    monkeypatch.setenv("API_TOKEN", "bearer-token")

    settings = Settings(_env_file=None)

    assert settings.DB_PATH == "./data/storage_agent.db"
    assert settings.SYSTEM_PROMPT_PATH == "./config/system_prompt.md"
    assert settings.MAX_TOOL_TURNS == 20
