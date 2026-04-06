import time
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests
import yaml

from app.services.docker_service import CommandResult, DockerService
from app.utils.config import Settings


class TestingService:
    _OPENAPI_PATH = "/openapi.json"
    _REQUIREMENTS_ARTIFACT_CANDIDATES = (
        "docs/requirements.md",
        "requirements.md",
        "docs/spec.md",
        "docs/specification.md",
    )
    _TEST_CASE_ARTIFACT_CANDIDATES = (
        "docs/test-cases.md",
        "docs/test_cases.md",
        "docs/test-plan.md",
        "test-cases.md",
        "test-plan.md",
    )
    _SCRAPING_KEYWORDS = (
        "beautifulsoup",
        "bs4",
        "playwright",
        "selenium",
        "scrapy",
        "lxml",
        "requests_html",
        "undetected_chromedriver",
    )
    _DYNAMIC_DATA_KEYWORDS = (
        "movie",
        "movies",
        "show",
        "shows",
        "showtime",
        "showtimes",
        "availability",
        "latest",
        "today",
        "live",
        "real-time",
        "schedule",
        "price",
        "news",
        "weather",
        "stock",
    )
    _LIGHTWEIGHT_MODIFICATION_TERMS = (
        "delete",
        "remove",
        "rename",
        "edit",
        "update",
        "button",
        "toggle",
        "watcher",
        "watchers",
        "ui",
        "ux",
        "form",
        "list",
        "row",
        "item",
    )
    _LIVE_DATA_CHANGE_TERMS = (
        "scrape",
        "scraping",
        "parser",
        "parse",
        "bookmyshow",
        "district",
        "fetch",
        "crawler",
        "backend api",
        "api",
        "showtime",
        "showtimes",
        "movie dropdown",
        "live data",
        "cron logic",
        "email sending",
        "brevo",
        "source",
        "verification",
    )
    _STATIC_DATA_HINTS = ("catalog", "sample", "mock", "fixture", "seed", "dummy")
    _EXTERNAL_FETCH_PATTERNS = (
        re.compile(r"requests\.(?:get|post|request)\(\s*[\"']https?://", flags=re.IGNORECASE),
        re.compile(r"httpx\.(?:get|post|request)\(", flags=re.IGNORECASE),
        re.compile(r"aiohttp\.", flags=re.IGNORECASE),
        re.compile(r"urllib\.request\.", flags=re.IGNORECASE),
        re.compile(r"fetch\(\s*[\"']https?://", flags=re.IGNORECASE),
    )

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
        if not self._find_first_existing_path(host_job_path, self._REQUIREMENTS_ARTIFACT_CANDIDATES):
            missing_items.append(
                "Requirements summary artifact is missing (expected e.g. docs/requirements.md with assumptions and acceptance criteria)"
            )
        if not self._find_first_existing_path(host_job_path, self._TEST_CASE_ARTIFACT_CANDIDATES):
            missing_items.append(
                "Test cases artifact is missing (expected e.g. docs/test-cases.md describing core scenarios before implementation)"
            )
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
        if "requirements summary artifact is missing" in lowered:
            guidance.append(
                "Add docs/requirements.md summarizing clarified assumptions plus explicit acceptance criteria."
            )
        if "test cases artifact is missing" in lowered:
            guidance.append(
                "Add docs/test-cases.md with concrete happy-path, validation, and regression scenarios before implementation."
            )
        if "yaml" in lowered and ("invalid" in lowered or "parse" in lowered):
            guidance.append(
                "Fix docker-compose YAML syntax and ensure it contains a top-level `services` mapping."
            )
        if "api verification failed" in lowered or "endpoint checks returned server errors" in lowered:
            guidance.append(
                "Fix backend routes so runtime API checks return non-5xx responses for expected endpoints."
            )
            guidance.append(
                "If scraping/external data is used, improve extraction/parsing accuracy and handle empty/malformed upstream responses."
            )
        if "config/search consistency probe failed" in lowered:
            guidance.append(
                "Ensure POST /api/config persists changes and subsequent /api/search reads the latest saved config."
            )
        if "static dataset files only" in lowered:
            guidance.append(
                "For dynamic/live-data requests, replace static sample catalogs with real data fetching plus robust parsing."
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

    def verify_runtime_apis(
        self,
        request_id: str,
        host_port: int,
        host: str | None = None,
    ) -> tuple[bool, str, dict[str, object]]:
        target_host = host or self._settings.smoke_test_host
        base_url = f"http://{target_host}:{host_port}"
        timeout_seconds = max(1, int(getattr(self._settings, "api_verification_timeout_seconds", 4)))
        max_calls = max(1, int(getattr(self._settings, "api_verification_max_calls", 8)))
        max_samples = max(1, int(getattr(self._settings, "api_verification_max_samples", 5)))

        openapi_payload = self._fetch_openapi_payload(base_url=base_url, timeout_seconds=timeout_seconds)
        endpoints = self._discover_openapi_endpoints_from_payload(
            payload=openapi_payload,
            max_calls=max_calls,
        )
        source = "openapi" if endpoints else "workspace"
        if not endpoints:
            host_job_path, _ = self._docker_service.ensure_job_workspace(request_id)
            endpoints = self._discover_workspace_get_endpoints(host_job_path=host_job_path, max_calls=max_calls)

        if not endpoints:
            endpoints = [("GET", "/status", None)]
            source = "fallback"

        probes: list[dict[str, object]] = []
        failed_paths: list[str] = []
        sample_terms: list[str] = []

        for method, path_with_query, body in endpoints[:max_calls]:
            url = f"{base_url}{path_with_query}"
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    timeout=timeout_seconds,
                    json=body,
                )
                ok = response.status_code < 500
                probe = {
                    "method": method,
                    "path": path_with_query,
                    "statusCode": response.status_code,
                    "ok": ok,
                }
                if not ok:
                    failed_paths.append(path_with_query)
                else:
                    sample_terms.extend(self._extract_sample_terms(response=response, max_terms=max_samples))
                probes.append(probe)
            except requests.RequestException as exc:
                failed_paths.append(path_with_query)
                probes.append(
                    {
                        "method": method,
                        "path": path_with_query,
                        "statusCode": None,
                        "ok": False,
                        "error": str(exc),
                    }
                )

        stateful_ok, stateful_failures, stateful_probes = self._run_stateful_config_search_probe(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            openapi_payload=openapi_payload,
        )
        probes.extend(stateful_probes)
        failed_paths.extend(stateful_failures)

        unique_terms: list[str] = []
        seen_terms: set[str] = set()
        for token in sample_terms:
            normalized = token.lower()
            if normalized in seen_terms:
                continue
            seen_terms.add(normalized)
            unique_terms.append(token)
            if len(unique_terms) >= max_samples:
                break

        success = len(failed_paths) == 0 and len(probes) > 0 and stateful_ok
        if success:
            summary = f"API verification passed: verified {len(probes)} endpoint checks."
        else:
            summary = f"API verification failed: {len(failed_paths)} endpoint checks returned server errors or were unreachable."
        report: dict[str, object] = {
            "source": source,
            "baseUrl": base_url,
            "probes": probes,
            "failedPaths": failed_paths,
            "sampleTerms": unique_terms,
        }
        return success, summary, report

    def assess_dynamic_data_reliability(self, request_id: str, prompt: str) -> tuple[bool, str]:
        if not self._is_dynamic_data_prompt(prompt):
            return True, "Dynamic-data reliability check skipped for non-dynamic prompt."

        host_job_path, _ = self._docker_service.ensure_job_workspace(request_id)
        static_files = self._detect_static_dataset_files(host_job_path)
        has_external_fetch = self._detect_external_data_fetch(host_job_path)

        if static_files and not has_external_fetch:
            listed = ", ".join(static_files[:6])
            return (
                False,
                "Dynamic-data reliability check failed: implementation appears to use static dataset files only "
                f"({listed}) with no external data-fetching logic.",
            )
        return True, "Dynamic-data reliability check passed."

    def detect_scraping_signals(self, request_id: str) -> bool:
        host_job_path, _ = self._docker_service.ensure_job_workspace(request_id)
        return self._detect_external_data_fetch(host_job_path)

    def should_cross_check_live_data(self, request_id: str, prompt: str) -> bool:
        if not self._is_dynamic_data_prompt(prompt):
            return False
        host_job_path, _ = self._docker_service.ensure_job_workspace(request_id)
        if not self._detect_external_data_fetch(host_job_path):
            return False

        change_request = self._extract_change_request(prompt)
        if change_request and self._is_lightweight_modification_request(change_request):
            return False
        return True

    def _discover_openapi_endpoints(
        self,
        base_url: str,
        timeout_seconds: int,
        max_calls: int,
    ) -> list[tuple[str, str, dict | None]]:
        payload = self._fetch_openapi_payload(base_url=base_url, timeout_seconds=timeout_seconds)
        return self._discover_openapi_endpoints_from_payload(payload=payload, max_calls=max_calls)

    def _discover_openapi_endpoints_from_payload(
        self,
        payload: dict | None,
        max_calls: int,
    ) -> list[tuple[str, str, dict | None]]:
        if not isinstance(payload, dict):
            return []
        paths = payload.get("paths")
        if not isinstance(paths, dict):
            return []

        endpoints: list[tuple[str, str, dict | None]] = []
        for path, operations in paths.items():
            if len(endpoints) >= max_calls:
                break
            if not isinstance(path, str) or not path.startswith("/") or "{" in path:
                continue
            if not isinstance(operations, dict):
                continue
            get_op = operations.get("get")
            if not isinstance(get_op, dict):
                continue
            resolved_path = self._resolve_openapi_path_with_defaults(path=path, operation=get_op)
            if resolved_path is None:
                continue
            endpoints.append(("GET", resolved_path, None))
        return endpoints

    def _fetch_openapi_payload(self, base_url: str, timeout_seconds: int) -> dict | None:
        try:
            response = requests.get(f"{base_url}{self._OPENAPI_PATH}", timeout=timeout_seconds)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def _discover_workspace_get_endpoints(
        self,
        host_job_path: Path,
        max_calls: int,
    ) -> list[tuple[str, str, dict | None]]:
        pattern = re.compile(r"@\w+\.(?:get|route)\(\s*['\"]([^'\"]+)['\"]", flags=re.IGNORECASE)
        discovered: list[str] = []

        for file_path in host_job_path.rglob("*.py"):
            if any(part in {".venv", "node_modules", "__pycache__", ".pytest_cache"} for part in file_path.parts):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in pattern.finditer(content):
                path = match.group(1).strip()
                if not path.startswith("/") or "{" in path:
                    continue
                if path not in discovered:
                    discovered.append(path)
                if len(discovered) >= max_calls:
                    break
            if len(discovered) >= max_calls:
                break

        if "/status" not in discovered:
            discovered.insert(0, "/status")
        return [("GET", path, None) for path in discovered[:max_calls]]

    @staticmethod
    def _resolve_openapi_path_with_defaults(path: str, operation: dict) -> str | None:
        query_values: dict[str, str] = {}
        parameters = operation.get("parameters")
        if isinstance(parameters, list):
            for parameter in parameters:
                if not isinstance(parameter, dict):
                    continue
                if parameter.get("in") != "query":
                    continue
                name = str(parameter.get("name") or "").strip()
                if not name:
                    continue
                schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
                value = (
                    parameter.get("example")
                    or schema.get("example")
                    or schema.get("default")
                    or (schema.get("enum")[0] if isinstance(schema.get("enum"), list) and schema.get("enum") else None)
                )
                if value is None and parameter.get("required"):
                    return None
                if value is None:
                    continue
                query_values[name] = str(value)

        if not query_values:
            return path
        return f"{path}?{urlencode(query_values)}"

    @staticmethod
    def _extract_sample_terms(response: requests.Response, max_terms: int) -> list[str]:
        try:
            payload = response.json()
        except ValueError:
            return []

        priority_values: list[str] = []
        fallback_values: list[str] = []
        preferred_keys = {
            "movie",
            "movies",
            "game",
            "games",
            "product",
            "products",
            "title",
            "titles",
            "name",
            "edition",
            "film",
            "show",
            "shows",
            "showtime",
            "showtimes",
            "cinema",
            "theatre",
            "theater",
            "language",
            "format",
            "city",
            "platform",
            "price",
            "sale_price",
            "discounted_price",
        }

        def add_value(raw: str, prioritized: bool) -> None:
            token = re.sub(r"\s+", " ", raw).strip()
            if len(token) < 3:
                return
            target = priority_values if prioritized else fallback_values
            if token not in target:
                target.append(token[:120])

        def collect(node: object, key_hint: str | None = None) -> None:
            if len(priority_values) + len(fallback_values) >= max_terms * 3:
                return
            if isinstance(node, str):
                prioritized = bool(key_hint and key_hint.lower() in preferred_keys)
                add_value(node, prioritized=prioritized)
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    collect(value, key_hint=str(key))
                    if len(priority_values) + len(fallback_values) >= max_terms * 3:
                        return
                return
            if isinstance(node, list):
                for item in node[:8]:
                    collect(item, key_hint=key_hint)
                    if len(priority_values) + len(fallback_values) >= max_terms * 3:
                        return

        collect(payload)

        values: list[str] = []
        for candidate in priority_values + fallback_values:
            normalized = candidate.lower()
            if normalized in {item.lower() for item in values}:
                continue
            values.append(candidate)
            if len(values) >= max_terms:
                break
        return values[:max_terms]

    def _run_stateful_config_search_probe(
        self,
        base_url: str,
        timeout_seconds: int,
        openapi_payload: dict | None,
    ) -> tuple[bool, list[str], list[dict[str, object]]]:
        probes: list[dict[str, object]] = []
        failures: list[str] = []

        paths = openapi_payload.get("paths") if isinstance(openapi_payload, dict) else None
        if not isinstance(paths, dict):
            return True, failures, probes
        config_ops = paths.get("/api/config")
        search_ops = paths.get("/api/search")
        if not isinstance(config_ops, dict) or not isinstance(search_ops, dict):
            return True, failures, probes
        if not isinstance(config_ops.get("get"), dict) or not isinstance(config_ops.get("post"), dict):
            return True, failures, probes
        if not isinstance(search_ops.get("post"), dict):
            return True, failures, probes

        try:
            get_initial = requests.get(f"{base_url}/api/config", timeout=timeout_seconds)
        except requests.RequestException as exc:
            failures.append(f"/api/config (GET initial): {exc}")
            return False, failures, probes
        probes.append(
            {"method": "GET", "path": "/api/config", "statusCode": get_initial.status_code, "ok": get_initial.status_code < 500}
        )
        if get_initial.status_code >= 400:
            failures.append(f"/api/config (GET initial) returned {get_initial.status_code}")
            return False, failures, probes

        config_payload = self._extract_config_payload(get_initial)
        if not isinstance(config_payload, dict):
            return True, failures, probes
        update_payload = self._build_config_probe_payload(config_payload)
        if not update_payload:
            return True, failures, probes

        try:
            post_config = requests.post(
                f"{base_url}/api/config",
                timeout=timeout_seconds,
                json=update_payload,
            )
        except requests.RequestException as exc:
            failures.append(f"/api/config (POST): {exc}")
            return False, failures, probes
        probes.append(
            {"method": "POST", "path": "/api/config", "statusCode": post_config.status_code, "ok": post_config.status_code < 500}
        )
        if post_config.status_code >= 400:
            failures.append(f"/api/config (POST) returned {post_config.status_code}")
            return False, failures, probes

        try:
            get_after = requests.get(f"{base_url}/api/config", timeout=timeout_seconds)
        except requests.RequestException as exc:
            failures.append(f"/api/config (GET after POST): {exc}")
            return False, failures, probes
        probes.append(
            {"method": "GET", "path": "/api/config", "statusCode": get_after.status_code, "ok": get_after.status_code < 500}
        )
        if get_after.status_code >= 400:
            failures.append(f"/api/config (GET after POST) returned {get_after.status_code}")
            return False, failures, probes

        persisted_config = self._extract_config_payload(get_after)
        if isinstance(persisted_config, dict):
            for key, value in update_payload.items():
                if key not in persisted_config:
                    continue
                if persisted_config.get(key) != value:
                    failures.append(f"/api/config persistence mismatch for `{key}`")
                    break

        search_payload = {}
        try:
            post_search = requests.post(f"{base_url}/api/search", timeout=timeout_seconds, json=search_payload)
        except requests.RequestException as exc:
            failures.append(f"/api/search (POST): {exc}")
            return False, failures, probes
        probes.append(
            {"method": "POST", "path": "/api/search", "statusCode": post_search.status_code, "ok": post_search.status_code < 500}
        )
        if post_search.status_code >= 500:
            failures.append(f"/api/search (POST) returned {post_search.status_code}")
            return False, failures, probes

        try:
            search_json = post_search.json()
        except ValueError:
            search_json = {}
        criteria = search_json.get("criteria") if isinstance(search_json, dict) else None
        if isinstance(criteria, dict):
            for key, value in update_payload.items():
                if key not in criteria:
                    continue
                if criteria.get(key) != value:
                    failures.append(
                        "Config/search consistency probe failed: search criteria did not reflect persisted config updates."
                    )
                    break

        return len(failures) == 0, failures, probes

    @staticmethod
    def _extract_config_payload(response: requests.Response) -> dict | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        if isinstance(payload.get("config"), dict):
            return payload["config"]
        return payload if payload else None

    @staticmethod
    def _build_config_probe_payload(config: dict) -> dict:
        payload = dict(config)
        if "interval_minutes" in payload:
            try:
                current = int(payload["interval_minutes"])
            except (TypeError, ValueError):
                current = 30
            payload["interval_minutes"] = 31 if current == 30 else 30

        emails = payload.get("emails")
        if isinstance(emails, list):
            normalized = [str(item).strip() for item in emails if str(item).strip()]
        else:
            normalized = []
        probe_email = "probe-config-check@example.com"
        if probe_email not in normalized:
            normalized.append(probe_email)
        payload["emails"] = normalized

        date_from = payload.get("date_from")
        date_to = payload.get("date_to")
        try:
            if isinstance(date_from, str) and isinstance(date_to, str):
                start = datetime.strptime(date_from, "%Y-%m-%d").date()
                end = datetime.strptime(date_to, "%Y-%m-%d").date()
                if end < start:
                    end = start
                payload["date_from"] = start.isoformat()
                payload["date_to"] = max(end, start + timedelta(days=1)).isoformat()
        except ValueError:
            pass
        return payload

    @classmethod
    def _is_dynamic_data_prompt(cls, prompt: str) -> bool:
        lowered = re.sub(r"\s+", " ", prompt.lower()).strip()
        return any(keyword in lowered for keyword in cls._DYNAMIC_DATA_KEYWORDS)

    @staticmethod
    def _extract_change_request(prompt: str) -> str | None:
        match = re.search(
            r"Change request:\s*(.+?)(?:\n\nRequirements:|$)",
            prompt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        return re.sub(r"\s+", " ", match.group(1)).strip()

    @classmethod
    def _is_lightweight_modification_request(cls, change_request: str) -> bool:
        lowered = re.sub(r"\s+", " ", change_request.lower()).strip()
        if not lowered:
            return False
        if any(term in lowered for term in cls._LIVE_DATA_CHANGE_TERMS):
            return False
        return any(term in lowered for term in cls._LIGHTWEIGHT_MODIFICATION_TERMS)

    def _detect_static_dataset_files(self, root: Path) -> list[str]:
        candidates: list[str] = []
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if any(part in {".venv", "node_modules", "__pycache__", ".pytest_cache"} for part in file_path.parts):
                continue
            if file_path.suffix.lower() not in {".json", ".csv", ".tsv"}:
                continue
            lowered_name = file_path.name.lower()
            if not any(token in lowered_name for token in self._STATIC_DATA_HINTS):
                continue
            try:
                relative = str(file_path.relative_to(root))
            except ValueError:
                relative = str(file_path)
            candidates.append(relative)
            if len(candidates) >= 8:
                break
        return candidates

    def _detect_external_data_fetch(self, root: Path) -> bool:
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in {".py", ".js", ".ts", ".tsx", ".mjs", ".cjs"}:
                continue
            if any(part in {".venv", "node_modules", "__pycache__", ".pytest_cache"} for part in file_path.parts):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lowered = content.lower()
            if any(token in lowered for token in self._SCRAPING_KEYWORDS):
                return True
            if any(domain in lowered for domain in ("bookmyshow", "district.in", "districtbyzomato", "district by zomato")):
                return True
            if ".text" in lowered and any(token in lowered for token in ("html", "parse", "regex", "selector")):
                return True
            if any(pattern.search(content) for pattern in self._EXTERNAL_FETCH_PATTERNS):
                return True
        return False

    @staticmethod
    def _find_first_existing_path(root: Path, candidates: tuple[str, ...]) -> Path | None:
        for relative in candidates:
            candidate = root / relative
            if candidate.exists() and candidate.is_file():
                return candidate
        return None
