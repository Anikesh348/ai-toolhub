import time
from pathlib import Path

import requests
import yaml

from app.services.docker_service import CommandResult, DockerService
from app.utils.config import Settings


class TestingService:
    def __init__(self, settings: Settings, docker_service: DockerService) -> None:
        self._settings = settings
        self._docker_service = docker_service

    def run_tests(self, request_id: str) -> CommandResult:
        shell_command = (
            "set -e; "
            "cd .; "
            "python3 -m venv .venv; "
            ". .venv/bin/activate; "
            "python -m pip install --no-cache-dir --upgrade pip; "
            "python -m pip install --no-cache-dir pytest; "
            "if [ -f requirements.txt ]; then "
            "python -m pip install --no-cache-dir -r requirements.txt; "
            "fi; "
            "if [ -f requirements-dev.txt ]; then "
            "python -m pip install --no-cache-dir -r requirements-dev.txt; "
            "fi; "
            f"{self._settings.tool_test_command}"
        )
        return self._docker_service.run_builder_container(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=self._settings.build_timeout_seconds,
        )

    def preflight_validate(self, request_id: str) -> tuple[bool, str]:
        host_job_path, _ = self._docker_service.ensure_job_workspace(request_id)
        missing_items: list[str] = []

        if not (host_job_path / "Dockerfile").exists():
            missing_items.append("Dockerfile is missing")
        compose_path = self._find_compose_file(host_job_path)
        if compose_path is None:
            missing_items.append("docker-compose file is missing (expected docker-compose.yml or docker-compose.yaml)")
        else:
            compose_valid, compose_error = self._validate_compose_yaml(compose_path)
            if not compose_valid:
                missing_items.append(compose_error)
        if not (host_job_path / "requirements.txt").exists():
            missing_items.append("requirements.txt is missing")
        if not self._has_discoverable_pytest_files(host_job_path):
            missing_items.append(
                "No discoverable pytest tests found. Add tests under tests/ with names like test_status.py."
            )

        if missing_items:
            details = "\n".join(f"- {item}" for item in missing_items)
            return False, f"Pre-test validation failed:\n{details}"
        return True, "Pre-test validation passed"

    def build_fix_guidance(self, failure_log: str) -> str:
        lowered = failure_log.lower()
        guidance: list[str] = []

        if "no tests ran" in lowered or "collected 0 items" in lowered:
            guidance.append(
                "Create at least one pytest-discoverable test file under tests/, for example tests/test_status.py."
            )
            guidance.append(
                "Ensure tests actually execute (no skipped/empty suite) and pass with `python -m pytest -q`."
            )
        if "modulenotfounderror" in lowered:
            guidance.append(
                "Fix missing imports by adding required dependencies to requirements.txt and updating imports."
            )
        if "/status" in lowered and "assert" in lowered:
            guidance.append(
                "Ensure GET /status returns exactly JSON {\"status\":\"ok\"} with HTTP 200."
            )
        if "docker-compose" in lowered and "missing" in lowered:
            guidance.append(
                "Add docker-compose.yml with valid services; for multi-service apps expose backend/frontend on separate ports."
            )
        if "yaml" in lowered and ("invalid" in lowered or "parse" in lowered):
            guidance.append(
                "Fix docker-compose YAML syntax and ensure it contains a top-level `services` mapping."
            )

        if not guidance:
            guidance.append("Address every failing test and keep all mandatory tool requirements satisfied.")
        return "\n".join(f"- {line}" for line in guidance)

    @staticmethod
    def _find_compose_file(root: Path) -> Path | None:
        compose_names = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
        for compose_name in compose_names:
            compose_path = root / compose_name
            if compose_path.exists():
                return compose_path
        return None

    @staticmethod
    def _validate_compose_yaml(compose_path: Path) -> tuple[bool, str]:
        try:
            raw = compose_path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"docker-compose file is unreadable: {exc}"

        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            return False, f"docker-compose YAML is invalid: {exc}"

        if not isinstance(parsed, dict):
            return False, "docker-compose YAML must be a mapping at top level"
        services = parsed.get("services")
        if not isinstance(services, dict) or not services:
            return False, "docker-compose YAML must define a non-empty top-level `services` mapping"

        return True, "docker-compose YAML is valid"

    @staticmethod
    def _has_discoverable_pytest_files(root: Path) -> bool:
        test_dirs = [root / "tests"]
        for test_dir in test_dirs:
            if test_dir.exists():
                for path in test_dir.rglob("*.py"):
                    if any(part.startswith(".") or part in {"__pycache__", ".venv", "node_modules"} for part in path.parts):
                        continue
                    if path.name.startswith("test_") or path.name.endswith("_test.py"):
                        return True

        for path in root.glob("test_*.py"):
            if path.is_file():
                return True
        for path in root.glob("*_test.py"):
            if path.is_file():
                return True
        return False

    def smoke_test(self, host_port: int, host: str | None = None) -> tuple[bool, str]:
        target_host = host or self._settings.smoke_test_host
        url = f"http://{target_host}:{host_port}{self._settings.smoke_test_path}"
        for _ in range(self._settings.smoke_test_retries):
            try:
                response = requests.get(url, timeout=self._settings.smoke_test_timeout_seconds)
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "ok":
                        return True, "Smoke test passed"
                    return False, f"Unexpected /status payload: {payload}"
            except requests.RequestException:
                pass
            time.sleep(self._settings.smoke_test_retry_interval_seconds)
        return False, f"Smoke test failed for {url}"
