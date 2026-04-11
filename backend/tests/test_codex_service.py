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
        del request_id, shell_command, timeout_seconds, extra_volumes, extra_environment, working_dir_override, tty
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
    )


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
