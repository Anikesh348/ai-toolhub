import json
from functools import lru_cache
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CODEX_CHAT_MODELS = (
    "gpt-5.4,"
    "gpt-5.4-mini,"
    "gpt-5.3-codex,"
    "gpt-5.2-codex,"
    "gpt-5.2,"
    "gpt-5.1-codex-max,"
    "gpt-5.1-codex-mini"
)


def parse_cors_allowed_origins(value: str) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()

    for raw_origin in value.split(","):
        candidate = raw_origin.strip()
        if not candidate:
            continue

        # Accept "host:port" entries by expanding them to both common schemes.
        candidates = [candidate]
        if "://" not in candidate:
            candidates = [f"http://{candidate}", f"https://{candidate}"]

        for candidate_with_scheme in candidates:
            parsed = urlsplit(candidate_with_scheme)
            if not parsed.scheme or not parsed.netloc:
                continue

            normalized_origin = f"{parsed.scheme}://{parsed.netloc}"
            if normalized_origin in seen:
                continue
            seen.add(normalized_origin)
            origins.append(normalized_origin)

    return origins


def dedupe_model_slugs(values: list[str]) -> list[str]:
    models: list[str] = []
    seen: set[str] = set()
    for raw in values:
        candidate = str(raw).strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        models.append(candidate)
    return models


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="AI Tool Builder Backend", alias="APP_NAME")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ALLOWED_ORIGINS",
    )

    mongo_uri: str = Field(
        default="mongodb://toolhub:toolhub-dev-password@mongo:27017/ai-toolhub?authSource=admin",
        validation_alias=AliasChoices("MONGO_URI", "DB_URL"),
    )
    mongo_db_name: str = Field(default="ai-toolhub", alias="MONGO_DB_NAME")
    mongo_collection_prefix: str = Field(default="tool_builder_v2", alias="MONGO_COLLECTION_PREFIX")

    codex_workspace_host: str = Field(alias="CODEX_WORKSPACE_HOST")
    codex_workspace_container: str = Field(alias="CODEX_WORKSPACE_CONTAINER")
    codex_image_name: str = Field(alias="CODEX_IMAGE_NAME")
    codex_command_template: str = Field(
        default=(
            "cd {job_dir} && codex exec --skip-git-repo-check "
            "--dangerously-bypass-approvals-and-sandbox \"$(cat {prompt_file})\""
        ),
        alias="CODEX_COMMAND_TEMPLATE",
    )
    codex_chat_models: str = Field(default=DEFAULT_CODEX_CHAT_MODELS, alias="CODEX_CHAT_MODELS")
    codex_default_chat_model: str | None = Field(default=None, alias="CODEX_DEFAULT_CHAT_MODEL")
    codex_tool_builder_model: str | None = Field(default=None, alias="CODEX_TOOL_BUILDER_MODEL")
    codex_models_cache_path: str | None = Field(default="~/.codex/models_cache.json", alias="CODEX_MODELS_CACHE_PATH")
    codex_config_path: str | None = Field(default="~/.codex/config.toml", alias="CODEX_CONFIG_PATH")

    port_range_start: int = Field(default=3001, alias="PORT_RANGE_START")
    port_range_end: int = Field(default=3999, alias="PORT_RANGE_END")

    builder_cpu_limit: float = Field(default=1.0, alias="BUILDER_CPU_LIMIT")
    builder_memory_limit: str = Field(default="512m", alias="BUILDER_MEMORY_LIMIT")
    builder_pids_limit: int = Field(default=100, alias="BUILDER_PIDS_LIMIT")

    tool_cpu_limit: float = Field(default=0.5, alias="TOOL_CPU_LIMIT")
    tool_memory_limit: str = Field(default="256m", alias="TOOL_MEMORY_LIMIT")
    tool_internal_port: int = Field(default=3000, alias="TOOL_INTERNAL_PORT")

    brevo_api_key: str | None = Field(default=None, alias="BREVO_API_KEY")
    brevo_sender_email: str | None = Field(default=None, alias="BREVO_SENDER_EMAIL")
    alert_recipient_email: str | None = Field(default=None, alias="ALERT_RECIPIENT_EMAIL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    max_build_attempts: int = Field(default=3, alias="MAX_BUILD_ATTEMPTS")
    build_timeout_seconds: int = Field(default=900, alias="BUILD_TIMEOUT_SECONDS")
    tool_test_command: str = Field(default="python -m pytest -q", alias="TOOL_TEST_COMMAND")

    smoke_test_path: str = Field(default="/status", alias="SMOKE_TEST_PATH")
    smoke_test_host: str = Field(default="host.docker.internal", alias="SMOKE_TEST_HOST")
    smoke_test_timeout_seconds: int = Field(default=5, alias="SMOKE_TEST_TIMEOUT_SECONDS")
    smoke_test_retries: int = Field(default=20, alias="SMOKE_TEST_RETRIES")
    smoke_test_retry_interval_seconds: int = Field(default=2, alias="SMOKE_TEST_RETRY_INTERVAL_SECONDS")
    api_verification_timeout_seconds: int = Field(default=4, alias="API_VERIFICATION_TIMEOUT_SECONDS")
    api_verification_max_calls: int = Field(default=8, alias="API_VERIFICATION_MAX_CALLS")
    api_verification_max_samples: int = Field(default=5, alias="API_VERIFICATION_MAX_SAMPLES")
    scraping_web_verify_timeout_seconds: int = Field(default=120, alias="SCRAPING_WEB_VERIFY_TIMEOUT_SECONDS")
    scraping_web_verify_enabled: bool = Field(default=True, alias="SCRAPING_WEB_VERIFY_ENABLED")

    monitor_poll_interval_seconds: int = Field(default=20, alias="MONITOR_POLL_INTERVAL_SECONDS")
    monitor_startup_grace_seconds: int = Field(default=90, alias="MONITOR_STARTUP_GRACE_SECONDS")

    tool_frontend_base_url: str = Field(default="http://localhost", alias="TOOL_FRONTEND_BASE_URL")
    tool_backend_base_url: str = Field(default="http://localhost", alias="TOOL_BACKEND_BASE_URL")
    usd_inr_rate_api_url: str = Field(default="https://open.er-api.com/v6/latest/USD", alias="USD_INR_RATE_API_URL")
    usd_inr_rate_timeout_seconds: float = Field(default=4.0, alias="USD_INR_RATE_TIMEOUT_SECONDS")
    usd_inr_rate_cache_ttl_seconds: int = Field(default=1800, alias="USD_INR_RATE_CACHE_TTL_SECONDS")
    usd_inr_rate_fallback: float = Field(default=83.0, alias="USD_INR_RATE_FALLBACK")
    instagram_browser_image: str = Field(
        default="lscr.io/linuxserver/chromium:latest",
        alias="INSTAGRAM_BROWSER_IMAGE",
    )
    instagram_browser_container_name: str = Field(
        default="toolhub-instagram-browser",
        alias="INSTAGRAM_BROWSER_CONTAINER_NAME",
    )
    instagram_browser_port_start: int = Field(default=4100, alias="INSTAGRAM_BROWSER_PORT_START")
    instagram_browser_port_end: int = Field(default=4199, alias="INSTAGRAM_BROWSER_PORT_END")
    instagram_browser_memory_limit: str = Field(default="768m", alias="INSTAGRAM_BROWSER_MEMORY_LIMIT")
    instagram_browser_cpu_limit: float = Field(default=1.0, alias="INSTAGRAM_BROWSER_CPU_LIMIT")
    instagram_browser_timezone: str = Field(default="Asia/Kolkata", alias="INSTAGRAM_BROWSER_TIMEZONE")
    instagram_browser_profile_host_dir: str | None = Field(default=None, alias="INSTAGRAM_BROWSER_PROFILE_HOST_DIR")
    youtube_data_api_key: str | None = Field(default=None, alias="YOUTUBE_DATA_API_KEY")
    youtube_shorts_queries: str = Field(
        default=(
            "Nature::breathtaking nature views,"
            "Travel::hidden travel gems,"
            "Food::street food shorts,"
            "Tech::latest tech hacks,"
            "Science::mind blowing science facts,"
            "Art::creative art process,"
            "Animals::cute animals daily,"
            "Fitness::quick fitness tips,"
            "Comedy::funny clips compilation,"
            "Music::music performance clips"
        ),
        alias="YOUTUBE_SHORTS_QUERIES",
    )
    youtube_shorts_preferred_categories: str = Field(
        default="Tech,Food,Travel",
        alias="YOUTUBE_SHORTS_PREFERRED_CATEGORIES",
    )
    youtube_shorts_category_boost_factor: int = Field(
        default=3,
        alias="YOUTUBE_SHORTS_CATEGORY_BOOST_FACTOR",
    )
    youtube_shorts_region_code: str | None = Field(default=None, alias="YOUTUBE_SHORTS_REGION_CODE")

    operator_denied_paths: str = Field(default="", alias="OPERATOR_DENIED_PATHS")
    operator_allowed_paths: str = Field(default="", alias="OPERATOR_ALLOWED_PATHS")
    operator_project_paths: str = Field(default="", alias="OPERATOR_PROJECT_PATHS")
    operator_timeout_seconds: int = Field(default=1800, alias="OPERATOR_TIMEOUT_SECONDS")
    operator_github_token: str | None = Field(default=None, alias="OPERATOR_GITHUB_TOKEN")
    operator_github_username: str = Field(default="x-access-token", alias="OPERATOR_GITHUB_USERNAME")

    @property
    def cors_origins(self) -> list[str]:
        return parse_cors_allowed_origins(self.cors_allowed_origins)

    @property
    def denied_paths(self) -> list[str]:
        return [path.strip() for path in self.operator_denied_paths.split(",") if path.strip()]

    @property
    def allowed_paths(self) -> list[str]:
        return [path.strip() for path in self.operator_allowed_paths.split(",") if path.strip()]

    @property
    def project_paths(self) -> list[str]:
        return [path.strip() for path in self.operator_project_paths.split(",") if path.strip()]

    def _configured_chat_models(self) -> list[str]:
        return dedupe_model_slugs(self.codex_chat_models.split(","))

    def _desktop_visible_chat_models(self) -> list[str]:
        raw_path = (self.codex_models_cache_path or "").strip()
        if not raw_path:
            return []

        cache_path = Path(raw_path).expanduser()
        if not cache_path.exists() or not cache_path.is_file():
            return []

        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return []

        ordered_slugs: list[tuple[int, str]] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            visibility = str(item.get("visibility") or "").strip().lower()
            if not slug or visibility != "list" or item.get("supported_in_api") is False:
                continue
            try:
                priority = int(item.get("priority"))
            except (TypeError, ValueError):
                priority = 9999
            ordered_slugs.append((priority, slug))

        ordered_slugs.sort(key=lambda item: (item[0], item[1]))
        return dedupe_model_slugs([slug for _, slug in ordered_slugs])

    def _desktop_default_chat_model(self) -> str | None:
        raw_path = (self.codex_config_path or "").strip()
        if not raw_path:
            return None

        config_path = Path(raw_path).expanduser()
        if not config_path.exists() or not config_path.is_file():
            return None

        try:
            with config_path.open("rb") as handle:
                payload = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None

        candidate = str(payload.get("model") or "").strip()
        return candidate or None

    @property
    def available_chat_models(self) -> list[str]:
        return dedupe_model_slugs([
            *self._desktop_visible_chat_models(),
            *self._configured_chat_models(),
        ])

    @property
    def default_chat_model(self) -> str | None:
        available = self.available_chat_models
        configured = (self.codex_default_chat_model or "").strip()
        if configured and configured in available:
            return configured
        desktop_default = self._desktop_default_chat_model()
        if desktop_default and desktop_default in available:
            return desktop_default
        return available[0] if available else None

    @property
    def tool_builder_model(self) -> str | None:
        configured = (self.codex_tool_builder_model or "").strip()
        if configured:
            return configured

        desktop_codex_models = [model for model in self._desktop_visible_chat_models() if "codex" in model]
        if desktop_codex_models:
            return desktop_codex_models[0]

        available_codex_models = [model for model in self.available_chat_models if "codex" in model]
        if available_codex_models:
            return available_codex_models[0]

        return self.default_chat_model

    @property
    def youtube_shorts_region_code_normalized(self) -> str | None:
        candidate = (self.youtube_shorts_region_code or "").strip().upper()
        if len(candidate) != 2 or not candidate.isalpha():
            return None
        return candidate


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
