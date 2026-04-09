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
