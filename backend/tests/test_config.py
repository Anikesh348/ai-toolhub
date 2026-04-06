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


def test_settings_use_desktop_model_cache_for_chat_and_tool_builder_defaults(tmp_path) -> None:
    models_cache_path = tmp_path / "models_cache.json"
    models_cache_path.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "gpt-5.4", "visibility": "list", "priority": 1, "supported_in_api": True},
                    {"slug": "gpt-5.3-codex", "visibility": "list", "priority": 5, "supported_in_api": True},
                    {"slug": "gpt-5", "visibility": "hide", "priority": 16, "supported_in_api": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "gpt-5.3-codex"\n', encoding="utf-8")

    settings = Settings(
        **_settings_kwargs(
            CODEX_MODELS_CACHE_PATH=str(models_cache_path),
            CODEX_CONFIG_PATH=str(config_path),
            CODEX_CHAT_MODELS="custom-model,gpt-5.4",
        )
    )

    assert settings.available_chat_models == ["gpt-5.4", "gpt-5.3-codex", "custom-model"]
    assert settings.default_chat_model == "gpt-5.3-codex"
    assert settings.tool_builder_model == "gpt-5.3-codex"


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
