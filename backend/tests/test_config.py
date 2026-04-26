import json

from app.utils.config import Settings


def _settings_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "MONGO_URI": "mongodb://localhost:27017",
        "MONGO_DB_NAME": "toolhub-test",
        "CODEX_WORKSPACE_HOST": "/tmp/codex-workspace",
        "CODEX_WORKSPACE_CONTAINER": "/workspace",
        "CODEX_IMAGE_NAME": "codex:test",
    }
    values.update(overrides)
    return values


def test_settings_default_chat_model_prefers_latest_fallback() -> None:
    settings = Settings(
        **_settings_kwargs(),
        _env_file=None,
    )

    assert settings.default_chat_model == "gpt-5.5"


def test_settings_use_desktop_model_cache_for_chat_and_tool_builder_defaults(tmp_path) -> None:
    models_cache_path = tmp_path / "models_cache.json"
    models_cache_path.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.5", "visibility": "list", "priority": 0, "supported_in_api": True},
                    {"slug": "gpt-5.4", "visibility": "list", "priority": 1, "supported_in_api": True},
                    {"slug": "gpt-5.3-codex", "visibility": "list", "priority": 5, "supported_in_api": True},
                    {"slug": "gpt-5", "visibility": "hide", "priority": 16, "supported_in_api": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "gpt-5.5"\n', encoding="utf-8")

    settings = Settings(
        **_settings_kwargs(
            CODEX_MODELS_CACHE_PATH=str(models_cache_path),
            CODEX_CONFIG_PATH=str(config_path),
            CODEX_CHAT_MODELS="custom-model,gpt-5.4",
        )
    )

    assert settings.available_chat_models == ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "custom-model"]
    assert settings.default_chat_model == "gpt-5.5"
    assert settings.tool_builder_model == "gpt-5.5"


def test_settings_falls_back_to_workspace_codex_metadata(tmp_path) -> None:
    workspace_path = tmp_path / "workspace"
    codex_dir = workspace_path / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.6", "visibility": "list", "priority": 0, "supported_in_api": True},
                    {"slug": "gpt-5.5", "visibility": "list", "priority": 1, "supported_in_api": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    (codex_dir / "config.toml").write_text('model = "gpt-5.6"\n', encoding="utf-8")

    settings = Settings(
        **_settings_kwargs(
            CODEX_WORKSPACE_HOST=str(workspace_path),
            CODEX_MODELS_CACHE_PATH=str(tmp_path / "missing-models.json"),
            CODEX_CONFIG_PATH=str(tmp_path / "missing-config.toml"),
        ),
        _env_file=None,
    )

    assert settings.available_chat_models[:2] == ["gpt-5.6", "gpt-5.5"]
    assert settings.default_chat_model == "gpt-5.6"


def test_settings_allow_explicit_tool_builder_model_override(tmp_path) -> None:
    models_cache_path = tmp_path / "models_cache.json"
    models_cache_path.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.4", "visibility": "list", "priority": 1, "supported_in_api": True},
                    {"slug": "gpt-5.3-codex", "visibility": "list", "priority": 5, "supported_in_api": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    settings = Settings(
        **_settings_kwargs(
            CODEX_MODELS_CACHE_PATH=str(models_cache_path),
            CODEX_TOOL_BUILDER_MODEL="gpt-5.4",
        )
    )

    assert settings.tool_builder_model == "gpt-5.4"


def test_settings_default_to_local_compose_mongo() -> None:
    settings = Settings(
        CODEX_WORKSPACE_HOST="/tmp/codex-workspace",
        CODEX_WORKSPACE_CONTAINER="/workspace",
        CODEX_IMAGE_NAME="codex:test",
        _env_file=None,
    )

    assert settings.mongo_uri == "mongodb://toolhub:toolhub-dev-password@mongo:27017/ai-toolhub?authSource=admin"
    assert settings.mongo_db_name == "ai-toolhub"
    assert settings.instagram_browser_timezone == "Asia/Kolkata"
    assert settings.resolved_agent_memory_path == "/tmp/codex-workspace/memory.md"


def test_settings_allows_agent_memory_path_override() -> None:
    settings = Settings(
        **_settings_kwargs(AGENT_MEMORY_PATH="/tmp/custom-memory.md"),
        _env_file=None,
    )

    assert settings.resolved_agent_memory_path == "/tmp/custom-memory.md"
