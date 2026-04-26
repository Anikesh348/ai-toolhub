import base64
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.codex_service import CodexService
from app.services.docker_service import CommandResult


class _StubDockerService:
    def __init__(self, root: Path, results: list[CommandResult]) -> None:
        self._root = root
        self._results = list(results)
        self.calls: int = 0
        self.last_extra_environment: dict[str, str] | None = None
        self.last_shell_command: str | None = None

    def ensure_job_workspace(self, request_id: str) -> tuple[Path, str]:
        host_job_path = self._root / request_id
        host_job_path.mkdir(parents=True, exist_ok=True)
        return host_job_path, f"/workspace/{request_id}"

    def run_builder_container_with_options(
        self,
        request_id: str,
        shell_command: str,
        timeout_seconds: int,
        extra_volumes: dict[str, dict[str, str]] | None = None,
        extra_environment: dict[str, str] | None = None,
        working_dir_override: str | None = None,
        tty: bool = False,
    ) -> CommandResult:
        del request_id, timeout_seconds, extra_volumes, working_dir_override, tty
        self.calls += 1
        self.last_shell_command = shell_command
        self.last_extra_environment = extra_environment
        if self._results:
            return self._results.pop(0)
        return CommandResult(success=True, exit_code=0, logs="ok")

    def run_builder_container(
        self,
        request_id: str,
        shell_command: str,
        timeout_seconds: int,
    ) -> CommandResult:
        del request_id, shell_command, timeout_seconds
        self.calls += 1
        if self._results:
            return self._results.pop(0)
        return CommandResult(success=True, exit_code=0, logs="ok")


def _settings(*, retries: int, delay_seconds: float) -> SimpleNamespace:
    return SimpleNamespace(
        codex_workspace_container="/workspace",
        codex_command_template=(
            "cd {job_dir} && codex exec --skip-git-repo-check "
            '--dangerously-bypass-approvals-and-sandbox "$(cat {prompt_file})"'
        ),
        build_timeout_seconds=300,
        operator_timeout_seconds=300,
        codex_transient_retries=retries,
        codex_transient_retry_delay_seconds=delay_seconds,
        openai_api_key=None,
        codex_workspace_host="/tmp/codex-workspace",
        operator_github_token=None,
        operator_github_username="x-access-token",
        operator_sudo_password=None,
        openai_image_api_base="https://api.openai.com/v1",
        openai_image_model="gpt-image-1",
        openai_image_size="1024x1024",
        openai_image_quality="high",
        openai_image_timeout_seconds=60,
    )


def _unsigned_jwt(payload: dict[str, object]) -> str:
    header = {"alg": "none", "typ": "JWT"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).decode("ascii").rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{header_b64}.{payload_b64}."


def test_run_chat_retries_transient_transport_failure_then_succeeds(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[
            CommandResult(
                success=False,
                exit_code=1,
                logs=(
                    "failed to connect to websocket: "
                    "IO error: Broken pipe (os error 32)"
                ),
            ),
            CommandResult(success=True, exit_code=0, logs="assistant: ok"),
        ],
    )
    service = CodexService(settings=_settings(retries=2, delay_seconds=0), docker_service=docker_service)

    result = service.run_chat(session_id="session-1", prompt="hello")

    assert result.success is True
    assert result.exit_code == 0
    assert docker_service.calls == 2
    assert "Recovered after transient Codex transport issue on attempt 2/3." in result.logs
    assert "Broken pipe (os error 32)" in result.logs


def test_run_chat_uses_ephemeral_json_cli_output_for_clean_general_chat(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[CommandResult(success=True, exit_code=0, logs="assistant: ok")],
    )
    service = CodexService(settings=_settings(retries=0, delay_seconds=0), docker_service=docker_service)

    result = service.run_chat(session_id="session-1", prompt="hello")

    assert result.success is True
    assert docker_service.last_shell_command is not None
    assert "codex exec --json --color never --ephemeral" in docker_service.last_shell_command


def test_run_chat_does_not_retry_non_transient_failures(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[CommandResult(success=False, exit_code=2, logs="Permission denied")],
    )
    service = CodexService(settings=_settings(retries=3, delay_seconds=0), docker_service=docker_service)

    result = service.run_chat(session_id="session-1", prompt="hello")

    assert result.success is False
    assert result.exit_code == 2
    assert docker_service.calls == 1
    assert result.logs == "Permission denied"


def test_run_chat_reports_when_transient_retries_are_exhausted(tmp_path) -> None:
    transient_failure = CommandResult(
        success=False,
        exit_code=1,
        logs="failed to connect to websocket: IO error: Broken pipe (os error 32)",
    )
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[transient_failure, transient_failure, transient_failure],
    )
    service = CodexService(settings=_settings(retries=2, delay_seconds=0), docker_service=docker_service)

    result = service.run_chat(session_id="session-1", prompt="hello")

    assert result.success is False
    assert result.exit_code == 1
    assert docker_service.calls == 3
    assert "Transient Codex transport issue persisted after 3 attempts." in result.logs


def test_get_usage_status_returns_unavailable_for_non_chatgpt_auth(tmp_path) -> None:
    docker_service = _StubDockerService(root=tmp_path, results=[])
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    service = CodexService(settings=settings, docker_service=docker_service)

    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps({
            "auth_mode": "api_key",
            "tokens": {"access_token": "ignored"},
        }),
        encoding="utf-8",
    )

    payload = service.get_usage_status()

    assert payload["available"] is False
    assert payload["authMode"] == "api_key"
    assert "ChatGPT-authenticated sessions" in str(payload["message"])


def test_get_login_status_includes_chatgpt_email_from_id_token(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[CommandResult(success=True, exit_code=0, logs="Logged in with ChatGPT")],
    )
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    service = CodexService(settings=settings, docker_service=docker_service)

    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {
                "id_token": _unsigned_jwt({"email": "codex.user@example.com"}),
            },
        }),
        encoding="utf-8",
    )

    payload = service.get_login_status()

    assert payload["loggedIn"] is True
    assert payload["email"] == "codex.user@example.com"


def test_get_login_status_omits_email_for_non_chatgpt_auth(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[CommandResult(success=True, exit_code=0, logs="Logged in")],
    )
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    service = CodexService(settings=settings, docker_service=docker_service)

    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps({
            "auth_mode": "api_key",
            "tokens": {
                "id_token": _unsigned_jwt({"email": "hidden@example.com"}),
            },
        }),
        encoding="utf-8",
    )

    payload = service.get_login_status()

    assert payload["loggedIn"] is True
    assert payload["email"] is None


def test_get_usage_status_maps_wham_usage_payload(tmp_path, monkeypatch) -> None:
    docker_service = _StubDockerService(root=tmp_path, results=[])
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    service = CodexService(settings=settings, docker_service=docker_service)

    auth_path = tmp_path / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(
        json.dumps({
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": "token-123",
                "account_id": "acct-123",
            },
        }),
        encoding="utf-8",
    )

    sample_payload = {
        "plan_type": "plus",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {
                "used_percent": 12,
                "limit_window_seconds": 18000,
                "reset_after_seconds": 1200,
                "reset_at": 1776000000,
            },
            "secondary_window": {
                "used_percent": 5,
                "limit_window_seconds": 604800,
                "reset_after_seconds": 320000,
                "reset_at": 1776500000,
            },
        },
        "code_review_rate_limit": None,
        "additional_rate_limits": [
            {
                "limit_name": "codex_other",
                "metered_feature": "codex_other",
                "rate_limit": {
                    "allowed": True,
                    "limit_reached": False,
                    "primary_window": {
                        "used_percent": 44,
                        "limit_window_seconds": 900,
                        "reset_after_seconds": 200,
                        "reset_at": 1776000900,
                    },
                    "secondary_window": None,
                },
            }
        ],
        "credits": {
            "has_credits": True,
            "unlimited": False,
            "overage_limit_reached": False,
            "balance": "7.25",
        },
        "spend_control": {"reached": False},
    }

    captured_headers: dict[str, str] = {}

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.status_code = 200
            self.text = json.dumps(payload)
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    def _fake_get(url: str, *, headers: dict[str, str], timeout: int):
        captured_headers.update(headers)
        assert url == "https://chatgpt.com/backend-api/wham/usage"
        assert timeout == 12
        return _FakeResponse(sample_payload)

    monkeypatch.setattr("app.services.codex_service.requests.get", _fake_get)

    payload = service.get_usage_status()

    assert payload["available"] is True
    assert payload["planType"] == "plus"
    assert payload["endpoint"] == "https://chatgpt.com/backend-api/wham/usage"
    assert payload["rateLimit"]["primaryWindow"]["usedPercent"] == 12.0
    assert payload["rateLimit"]["primaryWindow"]["windowMinutes"] == 300
    assert payload["credits"]["balance"] == "7.25"
    assert len(payload["additionalRateLimits"]) == 1
    assert payload["additionalRateLimits"][0]["limitName"] == "codex_other"
    assert captured_headers["ChatGPT-Account-Id"] == "acct-123"


def test_run_operator_sets_noninteractive_apt_environment(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[CommandResult(success=True, exit_code=0, logs="done")],
    )
    service = CodexService(settings=_settings(retries=0, delay_seconds=0), docker_service=docker_service)

    service.run_operator(
        session_id="session-1",
        prompt="Install nginx",
        container_cwd="/workspace/chat-session-1",
        extra_volumes={},
        timeout_seconds=300,
    )

    assert docker_service.last_extra_environment is not None
    assert docker_service.last_extra_environment.get("DEBIAN_FRONTEND") == "noninteractive"


def test_run_operator_includes_optional_sudo_password_in_environment(tmp_path) -> None:
    docker_service = _StubDockerService(
        root=tmp_path,
        results=[CommandResult(success=True, exit_code=0, logs="done")],
    )
    settings = _settings(retries=0, delay_seconds=0)
    settings.operator_sudo_password = "super-secret"
    service = CodexService(settings=settings, docker_service=docker_service)

    service.run_operator(
        session_id="session-1",
        prompt="Install nginx",
        container_cwd="/workspace/chat-session-1",
        extra_volumes={},
        timeout_seconds=300,
    )

    assert docker_service.last_extra_environment is not None
    assert docker_service.last_extra_environment.get("OPERATOR_SUDO_PASSWORD") == "super-secret"


def test_materialize_chat_generated_image_maps_container_path_to_attachment(tmp_path) -> None:
    docker_service = _StubDockerService(root=tmp_path, results=[])
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    service = CodexService(settings=settings, docker_service=docker_service)

    generated_host_path = tmp_path / "chat-session-1" / "generated.png"
    generated_host_path.parent.mkdir(parents=True, exist_ok=True)
    generated_host_path.write_bytes(b"\x89PNG\r\n\x1a\npng-bytes")

    resolved = service.materialize_chat_generated_image(
        session_id="session-1",
        image_reference="/workspace/chat-session-1/generated.png",
    )

    assert resolved is not None
    attachment_id = str(resolved.get("id") or "")
    assert attachment_id
    attachment_host_path = tmp_path / "chat-session-1" / "attachments" / attachment_id
    assert attachment_host_path.exists()


def test_materialize_chat_generated_image_ignores_remote_urls(tmp_path) -> None:
    docker_service = _StubDockerService(root=tmp_path, results=[])
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    service = CodexService(settings=settings, docker_service=docker_service)

    resolved = service.materialize_chat_generated_image(
        session_id="session-1",
        image_reference="https://example.com/image.png",
    )

    assert resolved is None


def test_generate_chat_image_saves_attachment_from_b64_response(tmp_path, monkeypatch) -> None:
    docker_service = _StubDockerService(root=tmp_path, results=[])
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    settings.openai_api_key = "key-123"
    service = CodexService(settings=settings, docker_service=docker_service)

    png_bytes = b"\x89PNG\r\n\x1a\nfake-png"

    class _FakeResponse:
        status_code = 200
        text = '{"ok": true}'

        @staticmethod
        def json() -> dict:
            import base64

            return {"data": [{"b64_json": base64.b64encode(png_bytes).decode("ascii")}]}

    def _fake_post(url: str, *, headers: dict[str, str], json: dict[str, object], timeout: int):
        assert url == "https://api.openai.com/v1/images/generations"
        assert headers["Authorization"] == "Bearer key-123"
        assert json["model"] == "gpt-image-1"
        assert json["quality"] == "high"
        assert timeout == 60
        return _FakeResponse()

    monkeypatch.setattr("app.services.codex_service.requests.post", _fake_post)

    attachment, error = service.generate_chat_image(session_id="session-1", prompt="Generate image of mountains")

    assert error is None
    assert attachment is not None
    attachment_id = str(attachment.get("id") or "")
    assert attachment_id
    saved_path = tmp_path / "chat-session-1" / "attachments" / attachment_id
    assert saved_path.exists()


def test_generate_chat_image_requires_openai_api_key(tmp_path) -> None:
    docker_service = _StubDockerService(root=tmp_path, results=[])
    settings = _settings(retries=0, delay_seconds=0)
    settings.codex_workspace_host = str(tmp_path)
    settings.openai_api_key = None
    service = CodexService(settings=settings, docker_service=docker_service)

    attachment, error = service.generate_chat_image(session_id="session-1", prompt="Generate image of mountains")

    assert attachment is None
    assert error is not None
    assert "OPENAI_API_KEY" in error
