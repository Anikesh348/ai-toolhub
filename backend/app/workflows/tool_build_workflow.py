import shutil
import re
from pathlib import Path

from app.models.status import BuildStatus, ToolStatus
from app.repositories.build_log_repository import BuildLogRepository
from app.repositories.request_repository import RequestRepository
from app.repositories.tool_repository import ToolRepository
from app.services.alert_service import AlertService
from app.services.codex_service import CodexService
from app.services.docker_service import DockerService, RuntimePlan
from app.services.port_allocator_service import PortAllocatorService
from app.services.prompt_service import PromptService
from app.services.testing_service import TestingService
from app.utils.config import Settings
from app.utils.logger import get_logger


class ToolBuildWorkflow:
    def __init__(
        self,
        settings: Settings,
        request_repository: RequestRepository,
        build_log_repository: BuildLogRepository,
        tool_repository: ToolRepository,
        prompt_service: PromptService,
        codex_service: CodexService,
        testing_service: TestingService,
        docker_service: DockerService,
        port_allocator_service: PortAllocatorService,
        alert_service: AlertService,
    ) -> None:
        self._settings = settings
        self._request_repository = request_repository
        self._build_log_repository = build_log_repository
        self._tool_repository = tool_repository
        self._prompt_service = prompt_service
        self._codex_service = codex_service
        self._testing_service = testing_service
        self._docker_service = docker_service
        self._port_allocator_service = port_allocator_service
        self._alert_service = alert_service
        self._logger = get_logger(__name__)

    def run(
        self,
        request_id: str,
        tool_name_hint: str | None,
        base_request_id: str | None = None,
        rebuild_tool_id: str | None = None,
    ) -> None:
        try:
            self._run(
                request_id=request_id,
                tool_name_hint=tool_name_hint,
                base_request_id=base_request_id,
                rebuild_tool_id=rebuild_tool_id,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Workflow failed for request %s", request_id)
            tool_for_request = self._tool_repository.get_by_request_ids([request_id]).get(request_id)
            if tool_for_request:
                self._tool_repository.update_status(tool_for_request["toolId"], ToolStatus.FAILED, clear_runtime=True)
            self._request_repository.update_status(request_id, BuildStatus.FAILED, error=str(exc))
            self._build_log_repository.add_log(request_id, "workflow", f"Unhandled error: {exc}")
            self._alert_service.send_build_failed_alert(request_id=request_id, reason=str(exc))

    def _run(
        self,
        request_id: str,
        tool_name_hint: str | None,
        base_request_id: str | None = None,
        rebuild_tool_id: str | None = None,
    ) -> None:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return

        rebuild_tool = self._tool_repository.get_by_id(rebuild_tool_id) if rebuild_tool_id else None
        if base_request_id:
            self._transition(request_id, BuildStatus.REFINING_PROMPT, "Preparing previous project as baseline")
            seeded = self._seed_workspace_from_base(base_request_id=base_request_id, request_id=request_id)
            self._build_log_repository.add_log(
                request_id,
                "workspace_seed",
                f"Baseline workspace {'prepared' if seeded else 'not found; continuing from empty workspace'} from {base_request_id}",
            )

        self._transition(request_id, BuildStatus.REFINING_PROMPT, "Refining prompt")
        refined_prompt = self._prompt_service.refine_prompt(request["prompt"])
        self._request_repository.set_refined_prompt(request_id, refined_prompt)

        last_failure = ""
        validated_image_tag: str | None = None
        for attempt in range(1, self._settings.max_build_attempts + 1):
            if self._request_repository.get_by_id(request_id) is None:
                return
            if attempt == 1:
                prompt = refined_prompt
                self._transition(request_id, BuildStatus.GENERATING_CODE, f"Generating code (attempt {attempt})")
            else:
                guidance = self._testing_service.build_fix_guidance(last_failure)
                feedback = (
                    f"The previous attempt failed tests.\n"
                    f"Fix all issues and keep all requirements satisfied.\n"
                    f"Error logs:\n{last_failure[-6000:]}\n\n"
                    f"Additional mandatory fixes:\n{guidance}"
                )
                prompt = f"{refined_prompt}\n\n{feedback}"
                self._transition(request_id, BuildStatus.FIXING_ERRORS, f"Fixing code (attempt {attempt})")

            generation_result = self._codex_service.run_generation(request_id=request_id, prompt=prompt)
            if self._request_repository.get_by_id(request_id) is None:
                return
            self._build_log_repository.add_log(
                request_id,
                f"generate_attempt_{attempt}",
                generation_result.logs[-12000:],
            )
            if not generation_result.success:
                last_failure = generation_result.logs
                continue

            preflight_ok, preflight_message = self._testing_service.preflight_validate(request_id=request_id)
            self._build_log_repository.add_log(request_id, f"preflight_attempt_{attempt}", preflight_message)
            if not preflight_ok:
                last_failure = preflight_message
                continue

            self._transition(request_id, BuildStatus.TESTING, f"Running tests (attempt {attempt})")
            test_result = self._testing_service.run_tests(request_id=request_id)
            self._build_log_repository.add_log(request_id, f"test_attempt_{attempt}", test_result.logs[-12000:])
            if test_result.success:
                self._transition(request_id, BuildStatus.BUILDING_IMAGE, f"Building Docker image (attempt {attempt})")
                attempt_image_tag = f"generated-tool:{request_id}-a{attempt}"
                image_build_result = self._docker_service.build_image(request_id=request_id, image_tag=attempt_image_tag)
                if self._request_repository.get_by_id(request_id) is None:
                    return
                self._build_log_repository.add_log(
                    request_id,
                    f"image_build_attempt_{attempt}",
                    image_build_result.logs[-12000:],
                )
                if not image_build_result.success:
                    last_failure = f"Docker image build failed:\n{image_build_result.logs[-4000:]}"
                    continue

                self._transition(request_id, BuildStatus.DEPLOYING, f"Runtime precheck (attempt {attempt})")
                try:
                    probe_port = self._port_allocator_service.allocate_port(reserve=False)
                except RuntimeError as exc:
                    last_failure = f"Port allocation failed during runtime precheck: {exc}"
                    continue

                probe_ok, probe_container_id, probe_error = self._docker_service.run_probe_container(
                    name=f"{request_id[:10]}-{attempt}",
                    image_tag=attempt_image_tag,
                    host_port=probe_port,
                    request_id=request_id,
                )
                if not probe_ok or not probe_container_id:
                    last_failure = f"Runtime precheck container failed: {probe_error}"
                    continue

                smoke_ok, smoke_message = self._testing_service.smoke_test(
                    probe_port,
                    host=self._settings.smoke_test_host,
                )
                probe_logs = ""
                if not smoke_ok:
                    probe_logs = self._docker_service.get_container_logs(probe_container_id)
                self._docker_service.stop_container(probe_container_id)
                self._build_log_repository.add_log(
                    request_id,
                    f"predeploy_smoke_attempt_{attempt}",
                    f"{smoke_message}\n{probe_logs[-3000:]}".strip(),
                )
                if not smoke_ok:
                    last_failure = (
                        f"Runtime precheck failed: {smoke_message}\n"
                        f"Container logs:\n{probe_logs[-4000:]}"
                    )
                    continue

                validated_image_tag = attempt_image_tag
                break
            guidance = self._testing_service.build_fix_guidance(test_result.logs)
            last_failure = f"{test_result.logs}\n\nSuggested fixes:\n{guidance}"

        if validated_image_tag is None:
            reason = f"Failed after {self._settings.max_build_attempts} attempts. Last error:\n{last_failure[-4000:]}"
            self._fail_request(request_id, reason)
            return

        if rebuild_tool is not None:
            tool = rebuild_tool
            tool_name = rebuild_tool["name"]
            self._tool_repository.prepare_rebuild(
                tool_id=tool["toolId"],
                request_id=request_id,
                docker_image=validated_image_tag,
            )
            self._build_log_repository.add_log(
                request_id,
                "rebuild",
                f"Updating existing tool `{tool['toolId']}` ({tool_name})",
            )
        else:
            tool_name, runtime_name = self._derive_tool_naming(
                tool_name_hint=tool_name_hint,
                request_prompt=request["prompt"],
            )
            tool = self._tool_repository.create(
                request_id=request_id,
                name=tool_name,
                docker_image=validated_image_tag,
                runtime_name=runtime_name,
            )
        runtime_plan = self._docker_service.inspect_runtime_plan(request_id)
        allocation_order = self._allocation_order(runtime_plan)
        self._transition(request_id, BuildStatus.DEPLOYING, "Deploying tool runtime")

        service_ports, allocation_error = self._resolve_or_allocate_service_ports(
            tool=tool,
            allocation_order=allocation_order,
            request_id=request_id,
        )
        if allocation_error:
            self._tool_repository.update_status(tool["toolId"], ToolStatus.FAILED)
            self._fail_request(request_id, allocation_error)
            return
        ui_service = runtime_plan.ui_service or allocation_order[0]
        ui_port = service_ports.get(ui_service)
        smoke_service = runtime_plan.smoke_service or ui_service
        smoke_port = service_ports.get(smoke_service)
        if ui_port is None or smoke_port is None:
            self._tool_repository.update_status(tool["toolId"], ToolStatus.FAILED)
            self._fail_request(request_id, "Unable to resolve UI/smoke service ports for deployment")
            return

        self._tool_repository.assign_ports(tool["toolId"], service_ports, ui_port=ui_port)

        deploy_ok, container_id, deploy_error = self._docker_service.run_tool_runtime(
            request_id=request_id,
            tool_id=tool["toolId"],
            runtime_name=tool.get("runtimeName") or tool["name"],
            image_tag=validated_image_tag,
            runtime_plan=runtime_plan,
            service_host_ports=service_ports,
        )
        if not deploy_ok or not container_id:
            self._tool_repository.update_status(tool["toolId"], ToolStatus.FAILED)
            self._fail_request(request_id, f"Tool deployment failed: {deploy_error}")
            return

        self._tool_repository.update_deployment(
            tool_id=tool["toolId"],
            container_id=container_id,
            port=ui_port,
            ports=service_ports,
            ui_port=ui_port,
            status=ToolStatus.DEPLOYING,
        )

        smoke_ok, smoke_message = self._testing_service.smoke_test(
            smoke_port,
            host=self._settings.smoke_test_host,
        )
        runtime_logs = ""
        if not smoke_ok:
            runtime_logs = self._docker_service.get_container_logs(container_id)
        self._build_log_repository.add_log(request_id, "smoke_test", f"{smoke_message}\n{runtime_logs[-3000:]}".strip())
        if not smoke_ok:
            self._docker_service.stop_container(container_id)
            self._tool_repository.update_status(tool["toolId"], ToolStatus.FAILED, clear_runtime=True)
            self._fail_request(
                request_id,
                f"Smoke test failed: {smoke_message}\nContainer logs:\n{runtime_logs[-4000:]}",
            )
            return

        self._tool_repository.update_status(tool["toolId"], ToolStatus.RUNNING)
        self._request_repository.update_status(request_id, BuildStatus.RUNNING, error=None)
        mapped_ports = ", ".join(f"{name}:{port}" for name, port in service_ports.items())
        self._build_log_repository.add_log(
            request_id,
            "complete",
            f"Tool running. UI port {ui_port}. Service ports [{mapped_ports}]",
        )
        self._alert_service.send_tool_deployed_alert(tool_name=tool_name, port=ui_port)

    def _resolve_or_allocate_service_ports(
        self,
        tool: dict,
        allocation_order: list[str],
        request_id: str,
    ) -> tuple[dict[str, int], str | None]:
        existing_ports: dict[str, int] = {}
        raw_ports = tool.get("ports")
        if isinstance(raw_ports, dict):
            for service_name in allocation_order:
                value = raw_ports.get(service_name)
                if value is None:
                    continue
                existing_ports[service_name] = int(value)
        if not existing_ports and tool.get("port") is not None and allocation_order:
            existing_ports[allocation_order[0]] = int(tool["port"])

        missing_services = [service for service in allocation_order if service not in existing_ports]
        if missing_services:
            try:
                allocated_ports = self._port_allocator_service.allocate_ports(
                    count=len(missing_services),
                    reserve=True,
                    tool_id=tool["toolId"],
                    request_id=request_id,
                )
            except RuntimeError as exc:
                return {}, str(exc)
            for index, service_name in enumerate(missing_services):
                existing_ports[service_name] = allocated_ports[index]

        if not existing_ports:
            return {}, "Unable to allocate runtime ports"
        return existing_ports, None

    def _seed_workspace_from_base(self, base_request_id: str, request_id: str) -> bool:
        source_root = Path(self._settings.codex_workspace_host) / base_request_id
        target_root = Path(self._settings.codex_workspace_host) / request_id
        if not source_root.exists() or not source_root.is_dir():
            return False

        target_root.mkdir(parents=True, exist_ok=True)
        ignored_names = {
            ".venv",
            ".pytest_cache",
            "__pycache__",
            "node_modules",
        }
        for entry in source_root.iterdir():
            if entry.name in ignored_names or entry.name.startswith(".mypy_cache"):
                continue
            target_entry = target_root / entry.name
            if target_entry.exists():
                if target_entry.is_dir():
                    shutil.rmtree(target_entry)
                else:
                    target_entry.unlink()
            if entry.is_dir():
                shutil.copytree(entry, target_entry)
            elif entry.is_file():
                shutil.copy2(entry, target_entry)
        return True

    def _derive_tool_naming(self, tool_name_hint: str | None, request_prompt: str) -> tuple[str, str]:
        base_name = (tool_name_hint or "").strip()
        if not base_name:
            base_name = self._infer_name_from_prompt(request_prompt)

        name = self._to_display_name(base_name)
        runtime_name = self._to_runtime_name(base_name)

        existing_tools = self._tool_repository.list_all()
        existing_display_names = {tool["name"].lower() for tool in existing_tools}
        existing_runtime_names = {str(tool.get("runtimeName") or "").lower() for tool in existing_tools}

        unique_name = name
        unique_runtime_name = runtime_name
        suffix = 2
        while unique_name.lower() in existing_display_names or unique_runtime_name.lower() in existing_runtime_names:
            unique_name = f"{name} {suffix}"
            unique_runtime_name = f"{runtime_name}-{suffix}"
            suffix += 1

        return unique_name, unique_runtime_name

    @staticmethod
    def _infer_name_from_prompt(prompt: str) -> str:
        text = re.sub(r"\s+", " ", prompt).strip()
        patterns = (
            r"Build a production-ready tool based on this request:\s*(.+?)(?:\s+Requirements:|$)",
            r"Change request:\s*(.+?)(?:\s+Requirements:|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                text = match.group(1).strip()
                break

        phrase_patterns = (
            r"\b(?:build|create|make|develop|generate|design)\s+(?:an?|the)?\s+(.+?)(?:[.!?]|$)",
            r"\b(?:need|want)\s+(?:an?|the)?\s+(.+?)(?:[.!?]|$)",
        )
        for pattern in phrase_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            text = match.group(1).strip()
            break
        tool_clause_match = re.match(
            r"^(?:an?\s+)?(?:tool|app|application|service)\s+that\s+(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if tool_clause_match:
            text = tool_clause_match.group(1).strip()
        else:
            text = re.split(r"\b(?:with|using|where|including|include)\b", text, maxsplit=1, flags=re.IGNORECASE)[
                0
            ].strip()

        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        stopwords = {
            "build",
            "a",
            "an",
            "the",
            "tool",
            "for",
            "with",
            "and",
            "to",
            "of",
            "that",
            "this",
            "app",
            "simple",
            "please",
            "include",
            "using",
            "only",
            "based",
            "request",
            "production",
            "ready",
            "workflow",
            "user",
            "need",
            "want",
            "create",
            "make",
            "develop",
            "generate",
            "design",
            "new",
            "project",
            "platform",
            "system",
            "solution",
            "into",
            "from",
            "in",
            "on",
            "at",
            "by",
            "is",
            "are",
            "be",
            "it",
            "we",
            "our",
            "your",
        }
        selected: list[str] = []
        for word in words:
            if word in stopwords or len(word) < 2:
                continue
            if word in selected:
                continue
            selected.append(word)
            if len(selected) == 4:
                break

        if not selected:
            return "task assistant"
        if len(selected) == 1:
            suffix = ToolBuildWorkflow._infer_name_suffix(words)
            if suffix and suffix != selected[0]:
                selected.append(suffix)
        if len(selected) < 2:
            selected.append("assistant")
        return " ".join(selected[:4])

    @staticmethod
    def _infer_name_suffix(words: list[str]) -> str:
        suffix_priority = (
            "dashboard",
            "tracker",
            "assistant",
            "scheduler",
            "manager",
            "analyzer",
            "generator",
            "reporter",
            "monitor",
            "notifier",
            "service",
            "api",
        )
        lowered_words = [word.lower() for word in words]
        for token in suffix_priority:
            if token in lowered_words:
                return token
        return "assistant"

    @staticmethod
    def _to_display_name(base_name: str) -> str:
        words = re.findall(r"[a-zA-Z0-9]+", base_name.lower())
        if not words:
            return "Task Assistant"
        capped: list[str] = []
        for word in words[:4]:
            if word in {"api", "ui", "crud"}:
                capped.append(word.upper())
            else:
                capped.append(word.capitalize())
        return " ".join(capped)

    @staticmethod
    def _to_runtime_name(base_name: str) -> str:
        token = re.sub(r"[^a-z0-9]+", "-", base_name.lower()).strip("-")
        token = re.sub(r"-{2,}", "-", token)
        return token[:40] if token else "generated-tool"

    def _transition(self, request_id: str, status: BuildStatus, message: str) -> None:
        self._request_repository.update_status(request_id, status, error=None)
        self._build_log_repository.add_log(request_id, "status", f"{status.value}: {message}")

    def _fail_request(self, request_id: str, reason: str) -> None:
        self._request_repository.update_status(request_id, BuildStatus.FAILED, error=reason)
        self._build_log_repository.add_log(request_id, "failed", reason[-12000:])
        self._alert_service.send_build_failed_alert(request_id=request_id, reason=reason[-4000:])

    @staticmethod
    def _allocation_order(runtime_plan: RuntimePlan) -> list[str]:
        published_services = [service.name for service in runtime_plan.published_services]
        if not published_services:
            return [runtime_plan.ui_service or "app"]

        if runtime_plan.ui_service and runtime_plan.ui_service in published_services:
            return [runtime_plan.ui_service] + [
                service_name for service_name in published_services if service_name != runtime_plan.ui_service
            ]
        return published_services
