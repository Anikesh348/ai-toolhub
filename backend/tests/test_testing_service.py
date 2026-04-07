from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.testing_service import TestingService


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _build_service(tmp_path: Path) -> tuple[TestingService, Mock]:
    docker_service = Mock()
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")
    settings = SimpleNamespace(
        smoke_test_host="127.0.0.1",
        smoke_test_timeout_seconds=2,
        api_verification_timeout_seconds=2,
        api_verification_max_calls=8,
        api_verification_max_samples=5,
    )
    return TestingService(settings=settings, docker_service=docker_service), docker_service


def test_verify_runtime_apis_uses_openapi_and_passes(monkeypatch, tmp_path: Path) -> None:
    service, _ = _build_service(tmp_path=tmp_path)
    openapi_payload = {
        "paths": {
            "/status": {"get": {}},
            "/shows": {
                "get": {
                    "parameters": [
                        {"name": "city", "in": "query", "required": True, "schema": {"default": "chennai"}}
                    ]
                }
            },
        }
    }

    def fake_get(url: str, timeout: int):  # type: ignore[no-untyped-def]
        assert timeout == 2
        assert url.endswith("/openapi.json")
        return _Response(200, openapi_payload)

    def fake_request(method: str, url: str, timeout: int, json=None):  # type: ignore[no-untyped-def]
        _ = timeout, json
        if method == "GET" and url.endswith("/status"):
            return _Response(200, {"status": "ok"})
        if method == "GET" and "/shows" in url:
            return _Response(200, {"items": [{"movie": "Demo Film"}]})
        return _Response(404, {"error": "not-found"})

    monkeypatch.setattr("app.services.testing_service.requests.get", fake_get)
    monkeypatch.setattr("app.services.testing_service.requests.request", fake_request)

    ok, summary, report = service.verify_runtime_apis(request_id="req-1", host_port=3123, host="127.0.0.1")

    assert ok is True
    assert "verified" in summary.lower()
    assert report["source"] == "openapi"
    assert report["probes"]


def test_verify_runtime_apis_fails_when_endpoint_returns_server_error(monkeypatch, tmp_path: Path) -> None:
    service, _ = _build_service(tmp_path=tmp_path)
    openapi_payload = {"paths": {"/movies": {"get": {}}}}

    monkeypatch.setattr("app.services.testing_service.requests.get", lambda *_args, **_kwargs: _Response(200, openapi_payload))
    monkeypatch.setattr(
        "app.services.testing_service.requests.request",
        lambda *_args, **_kwargs: _Response(502, {"error": "bad gateway"}),
    )

    ok, summary, report = service.verify_runtime_apis(request_id="req-2", host_port=3124, host="127.0.0.1")

    assert ok is False
    assert "server errors" in summary.lower()
    assert report["failedPaths"]
    assert "/movies" in report["failedPaths"][0]


def test_detect_scraping_signals(tmp_path: Path) -> None:
    service, docker_service = _build_service(tmp_path=tmp_path)
    (tmp_path / "app.py").write_text("from bs4 import BeautifulSoup\n", encoding="utf-8")
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")

    assert service.detect_scraping_signals(request_id="req-3") is True


def test_should_cross_check_live_data_for_external_fetch_prompt(tmp_path: Path) -> None:
    service, docker_service = _build_service(tmp_path=tmp_path)
    (tmp_path / "app.py").write_text(
        "import requests\nrequests.get('https://in.bookmyshow.com/explore/movies-chennai')\n",
        encoding="utf-8",
    )
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")

    assert service.should_cross_check_live_data(
        request_id="req-3b",
        prompt="Track current movie show availability and send alerts",
    ) is True


def test_should_skip_live_data_cross_check_for_simple_modify_request(tmp_path: Path) -> None:
    service, docker_service = _build_service(tmp_path=tmp_path)
    (tmp_path / "app.py").write_text(
        "import requests\nrequests.get('https://in.bookmyshow.com/explore/movies-chennai')\n",
        encoding="utf-8",
    )
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")

    prompt = (
        "Apply the requested change to the existing tool codebase.\n\n"
        "Change request:\n"
        "can you also provide an option to delete an added movie watcher\n\n"
        "Requirements:\n"
        "- Preserve existing working behavior.\n"
    )

    assert service.should_cross_check_live_data(
        request_id="req-3c",
        prompt=prompt,
    ) is False


def test_verify_runtime_apis_fails_when_config_search_consistency_breaks(monkeypatch, tmp_path: Path) -> None:
    service, _ = _build_service(tmp_path=tmp_path)
    openapi_payload = {
        "paths": {
            "/status": {"get": {}},
            "/api/config": {"get": {}, "post": {}},
            "/api/search": {"post": {}},
        }
    }
    baseline_config = {
        "city": "chennai",
        "movie": "Interstellar",
        "language": "English",
        "format": "IMAX 2D",
        "date_from": "2026-04-10",
        "date_to": "2026-04-11",
        "emails": [],
        "interval_minutes": 60,
    }

    def fake_get(url: str, timeout: int):  # type: ignore[no-untyped-def]
        _ = timeout
        if url.endswith("/openapi.json"):
            return _Response(200, openapi_payload)
        if url.endswith("/status"):
            return _Response(200, {"status": "ok"})
        if url.endswith("/api/config"):
            return _Response(200, {"config": baseline_config})
        return _Response(404, {"error": "not-found"})

    def fake_post(url: str, timeout: int, json=None):  # type: ignore[no-untyped-def]
        _ = timeout, json
        if url.endswith("/api/config"):
            return _Response(200, {"config": baseline_config})
        if url.endswith("/api/search"):
            # Intentionally stale criteria to prove consistency checks trigger failure.
            return _Response(200, {"criteria": baseline_config, "match_count": 0, "matches": []})
        return _Response(404, {"error": "not-found"})

    def fake_request(method: str, url: str, timeout: int, json=None):  # type: ignore[no-untyped-def]
        _ = json
        if method == "GET":
            return fake_get(url=url, timeout=timeout)
        return _Response(404, {"error": "not-found"})

    monkeypatch.setattr("app.services.testing_service.requests.get", fake_get)
    monkeypatch.setattr("app.services.testing_service.requests.post", fake_post)
    monkeypatch.setattr("app.services.testing_service.requests.request", fake_request)

    ok, summary, report = service.verify_runtime_apis(request_id="req-4", host_port=3125, host="127.0.0.1")

    assert ok is False
    assert "failed" in summary.lower()
    assert any("consistency" in item.lower() for item in report["failedPaths"])


def test_assess_dynamic_data_reliability_flags_static_only_implementation(tmp_path: Path) -> None:
    service, docker_service = _build_service(tmp_path=tmp_path)
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "catalog.json").write_text("[]", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")

    ok, message = service.assess_dynamic_data_reliability(
        request_id="req-5",
        prompt="Find movie show availability in date range and send alerts",
    )

    assert ok is False
    assert "static dataset files only" in message


def test_assess_dynamic_data_reliability_allows_dynamic_prompt_when_external_fetch_exists(tmp_path: Path) -> None:
    service, docker_service = _build_service(tmp_path=tmp_path)
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "catalog.json").write_text("[]", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(
        "import requests\n\nrequests.get('https://example.com')\n",
        encoding="utf-8",
    )
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")

    ok, message = service.assess_dynamic_data_reliability(
        request_id="req-6",
        prompt="Find movie show availability in date range and send alerts",
    )

    assert ok is True
    assert "passed" in message.lower()


def test_preflight_validate_requires_requirement_and_test_case_artifacts(tmp_path: Path) -> None:
    service, docker_service = _build_service(tmp_path=tmp_path)
    docker_service.ensure_job_workspace.return_value = (tmp_path, "/workspace/job")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services:\n  app:\n    image: demo\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\npytest\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_status.py").write_text("def test_status():\n    assert True\n", encoding="utf-8")

    ok, message = service.preflight_validate(request_id="req-artifacts")

    assert ok is False
    assert "Requirements summary artifact is missing" in message
    assert "Test cases artifact is missing" in message


def test_build_fix_guidance_handles_mongo_startup_crash() -> None:
    service, _docker_service = _build_service(tmp_path=Path("/tmp"))

    guidance = service.build_fix_guidance(
        "Smoke test failed.\nMongoServerSelectionError: Server selection timed out after 5000 ms\nconnect ECONNREFUSED"
    )

    assert "Do not connect to Mongo synchronously" in guidance
    assert "bounded retry or lazy database initialization" in guidance
    assert "`MONGO_URI` and `MONGO_DB_NAME`" in guidance
