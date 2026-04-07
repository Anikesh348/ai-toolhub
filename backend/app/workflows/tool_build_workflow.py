import json
import re
import shutil
from datetime import timedelta
from pathlib import Path

from app.models.status import BuildStatus, ToolStatus
from app.repositories.build_log_artifact_repository import BuildLogArtifactRepository
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
from app.utils.time import now_ist


class ToolBuildWorkflow:
    def __init__(
        self,
        settings: Settings,
        request_repository: RequestRepository,
        build_log_repository: BuildLogRepository,
        build_log_artifact_repository: BuildLogArtifactRepository,
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
        self._build_log_artifact_repository = build_log_artifact_repository
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
        model: str | None = None,
        prompt_override: str | None = None,
        prompt_already_refined: bool = False,
    ) -> None:
        try:
            self._run(
                request_id=request_id,
                tool_name_hint=tool_name_hint,
                base_request_id=base_request_id,
                rebuild_tool_id=rebuild_tool_id,
                model=model,
                prompt_override=prompt_override,
                prompt_already_refined=prompt_already_refined,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Workflow failed for request %s", request_id)
            request_state = self._request_repository.get_by_id(request_id)
            if request_state and request_state.get("status") == BuildStatus.STOPPED.value:
                self._build_log_repository.add_log(request_id, "workflow", "Build stop acknowledged during workflow shutdown")
                return
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
        model: str | None = None,
        prompt_override: str | None = None,
        prompt_already_refined: bool = False,
    ) -> None:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return
        if request["status"] == BuildStatus.STOPPED.value:
            self._build_log_repository.add_log(request_id, "workflow", "Build stop acknowledged before workflow execution")
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
        source_prompt = (prompt_override or "").strip() or str(request["prompt"])
        refined_prompt = source_prompt if prompt_already_refined else self._prompt_service.refine_prompt(source_prompt)
        self._request_repository.set_refined_prompt(request_id, refined_prompt)
        generation_model = (model or "").strip() or str(getattr(self._settings, "tool_builder_model", "") or "").strip() or None
        request_context_prompt = self._effective_request_context_prompt(request)

        last_failure = ""
        terminal_failure_reason: str | None = None
        validated_image_tag: str | None = None
        for attempt in range(1, self._settings.max_build_attempts + 1):
            if self._request_stopped_or_missing(request_id):
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

            generation_result = self._codex_service.run_generation(
                request_id=request_id,
                prompt=prompt,
                model=generation_model,
            )
            if self._request_stopped_or_missing(request_id):
                return
            self._build_log_repository.add_log(
                request_id,
                f"generate_attempt_{attempt}",
                generation_result.logs[-12000:],
            )
            self._store_log_artifact(
                request_id=request_id,
                step=f"generate_attempt_{attempt}",
                file_name=f"generate-attempt-{attempt}.log",
                content=generation_result.logs,
            )
            if not generation_result.success:
                generation_logs = self._codex_service.clean_cli_output(generation_result.logs)
                last_failure = generation_logs or generation_result.logs
                non_retryable_reason = self._classify_non_retryable_generation_failure(last_failure)
                if non_retryable_reason:
                    terminal_failure_reason = (
                        f"{non_retryable_reason}\n\nGeneration logs:\n{last_failure[-3500:]}"
                    )
                    self._build_log_repository.add_log(
                        request_id,
                        f"generate_attempt_{attempt}_non_retryable",
                        non_retryable_reason,
                    )
                    break
                continue

            preflight_ok, preflight_message = self._testing_service.preflight_validate(request_id=request_id)
            self._build_log_repository.add_log(request_id, f"preflight_attempt_{attempt}", preflight_message)
            self._store_log_artifact(
                request_id=request_id,
                step=f"preflight_attempt_{attempt}",
                file_name=f"preflight-attempt-{attempt}.log",
                content=preflight_message,
            )
            if not preflight_ok:
                last_failure = preflight_message
                continue

            self._transition(request_id, BuildStatus.TESTING, f"Running tests (attempt {attempt})")
            test_result = self._testing_service.run_tests(request_id=request_id)
            self._build_log_repository.add_log(request_id, f"test_attempt_{attempt}", test_result.logs[-12000:])
            self._store_log_artifact(
                request_id=request_id,
                step=f"test_attempt_{attempt}",
                file_name=f"test-attempt-{attempt}.log",
                content=test_result.logs,
            )
            if test_result.success:
                self._transition(request_id, BuildStatus.BUILDING_IMAGE, f"Building Docker image (attempt {attempt})")
                attempt_image_tag = f"generated-tool:{request_id}-a{attempt}"
                image_build_result = self._docker_service.build_image(request_id=request_id, image_tag=attempt_image_tag)
                if self._request_stopped_or_missing(request_id):
                    return
                self._build_log_repository.add_log(
                    request_id,
                    f"image_build_attempt_{attempt}",
                    image_build_result.logs[-12000:],
                )
                self._store_log_artifact(
                    request_id=request_id,
                    step=f"image_build_attempt_{attempt}",
                    file_name=f"image-build-attempt-{attempt}.log",
                    content=image_build_result.logs,
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

                probe_logs = ""
                try:
                    smoke_ok, smoke_message = self._testing_service.smoke_test(
                        probe_port,
                        host=self._settings.smoke_test_host,
                    )
                    if not smoke_ok:
                        probe_logs = self._docker_service.get_container_logs(probe_container_id)
                    self._build_log_repository.add_log(
                        request_id,
                        f"predeploy_smoke_attempt_{attempt}",
                        f"{smoke_message}\n{probe_logs[-3000:]}".strip(),
                    )
                    self._store_log_artifact(
                        request_id=request_id,
                        step=f"predeploy_smoke_attempt_{attempt}",
                        file_name=f"predeploy-smoke-attempt-{attempt}.log",
                        content=f"{smoke_message}\n\n{probe_logs}".strip(),
                    )
                    if not smoke_ok:
                        last_failure = (
                            f"Runtime precheck failed: {smoke_message}\n"
                            f"Container logs:\n{probe_logs[-4000:]}"
                        )
                        continue

                    self._transition(
                        request_id,
                        BuildStatus.VERIFYING_APIS,
                        f"Verifying backend endpoints (attempt {attempt})",
                    )
                    api_ok, api_summary, api_report = self._testing_service.verify_runtime_apis(
                        request_id=request_id,
                        host_port=probe_port,
                        host=self._settings.smoke_test_host,
                    )
                    if self._request_stopped_or_missing(request_id):
                        return
                    api_report_text = json.dumps(api_report, ensure_ascii=True)
                    self._build_log_repository.add_log(
                        request_id,
                        f"api_verify_attempt_{attempt}",
                        f"{api_summary}\n{api_report_text[-5000:]}",
                    )
                    self._store_log_artifact(
                        request_id=request_id,
                        step=f"api_verify_attempt_{attempt}",
                        file_name=f"api-verify-attempt-{attempt}.json",
                        content=json.dumps(
                            {
                                "summary": api_summary,
                                "report": api_report,
                            },
                            ensure_ascii=True,
                            indent=2,
                        ),
                        content_type="application/json; charset=utf-8",
                    )
                    if not api_ok:
                        last_failure = f"{api_summary}\n{api_report_text[-5000:]}"
                        continue

                    data_reliability_ok, data_reliability_message = self._testing_service.assess_dynamic_data_reliability(
                        request_id=request_id,
                        prompt=request_context_prompt,
                    )
                    self._build_log_repository.add_log(
                        request_id,
                        f"data_reliability_attempt_{attempt}",
                        data_reliability_message[-5000:],
                    )
                    self._store_log_artifact(
                        request_id=request_id,
                        step=f"data_reliability_attempt_{attempt}",
                        file_name=f"data-reliability-attempt-{attempt}.log",
                        content=data_reliability_message,
                    )
                    if not data_reliability_ok:
                        last_failure = data_reliability_message
                        continue

                    should_cross_check_scraping = bool(
                        getattr(self._settings, "scraping_web_verify_enabled", True)
                    ) and self._testing_service.should_cross_check_live_data(
                        request_id=request_id,
                        prompt=request_context_prompt,
                    )
                    if should_cross_check_scraping:
                        self._transition(
                            request_id,
                            BuildStatus.VERIFYING_APIS,
                            f"Cross-checking scraping output with web search (attempt {attempt})",
                        )
                        scrape_ok, scrape_message = self._verify_scraped_api_output_with_web(
                            request_id=request_id,
                            attempt=attempt,
                            request_prompt=request_context_prompt,
                            api_report=api_report,
                        )
                        if self._request_stopped_or_missing(request_id):
                            return
                        self._build_log_repository.add_log(
                            request_id,
                            f"scrape_verify_attempt_{attempt}",
                            scrape_message[-6000:],
                        )
                        self._store_log_artifact(
                            request_id=request_id,
                            step=f"scrape_verify_attempt_{attempt}",
                            file_name=f"scrape-verify-attempt-{attempt}.log",
                            content=scrape_message,
                        )
                        if not scrape_ok:
                            last_failure = scrape_message
                            continue
                finally:
                    self._docker_service.stop_container(probe_container_id)

                validated_image_tag = attempt_image_tag
                break
            guidance = self._testing_service.build_fix_guidance(test_result.logs)
            last_failure = f"{test_result.logs}\n\nSuggested fixes:\n{guidance}"

        if validated_image_tag is None:
            if self._request_stopped_or_missing(request_id):
                return
            if terminal_failure_reason:
                reason = terminal_failure_reason
            else:
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
        if self._request_stopped_or_missing(request_id):
            return

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
            monitor_ignore_until=now_ist()
            + timedelta(seconds=max(0, int(getattr(self._settings, "monitor_startup_grace_seconds", 90)))),
        )

        smoke_ok, smoke_message = self._testing_service.smoke_test(
            smoke_port,
            host=self._settings.smoke_test_host,
        )
        runtime_logs = ""
        if not smoke_ok:
            runtime_logs = self._docker_service.get_container_logs(container_id)
        self._build_log_repository.add_log(request_id, "smoke_test", f"{smoke_message}\n{runtime_logs[-3000:]}".strip())
        self._store_log_artifact(
            request_id=request_id,
            step="smoke_test",
            file_name="runtime-smoke-test.log",
            content=f"{smoke_message}\n\n{runtime_logs}".strip(),
        )
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

    @staticmethod
    def _effective_request_context_prompt(request: dict[str, object]) -> str:
        initial_prompt = str(request.get("initialPrompt") or "").strip()
        latest_prompt = str(request.get("latestPrompt") or request.get("prompt") or "").strip()
        raw_history = request.get("promptHistory")

        history_prompts: list[str] = []
        if isinstance(raw_history, list):
            for item in raw_history:
                if not isinstance(item, dict):
                    continue
                prompt = str(item.get("prompt") or "").strip()
                if prompt:
                    history_prompts.append(prompt)

        if not initial_prompt and not history_prompts:
            return latest_prompt

        parts: list[str] = []
        if initial_prompt:
            parts.append(f"Original tool request:\n{initial_prompt}")
        if history_prompts:
            recent_changes = history_prompts[1:] if len(history_prompts) > 1 else []
            if recent_changes:
                parts.append("Modification history:\n" + "\n".join(f"- {prompt}" for prompt in recent_changes[-3:]))
        if latest_prompt and latest_prompt != initial_prompt:
            parts.append(f"Latest user request:\n{latest_prompt}")

        combined = "\n\n".join(part for part in parts if part.strip()).strip()
        return combined or str(request.get("prompt") or "").strip()

    def _request_stopped_or_missing(self, request_id: str) -> bool:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return True
        if request.get("status") == BuildStatus.STOPPED.value:
            self._build_log_repository.add_log(request_id, "workflow", "Build stop acknowledged")
            return True
        return False

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
        candidates = ToolBuildWorkflow._candidate_name_phrases(prompt)
        best_terms: list[str] = []
        best_suffix: str | None = None
        best_score = float("-inf")

        for candidate in candidates:
            terms = ToolBuildWorkflow._extract_meaningful_name_terms(candidate)
            if not terms:
                continue
            suffix = ToolBuildWorkflow._infer_contextual_suffix(candidate)
            score = ToolBuildWorkflow._score_name_candidate(terms=terms, suffix=suffix)
            if score <= best_score:
                continue
            best_terms = terms
            best_suffix = suffix
            best_score = score

        if not best_terms:
            return "task assistant"

        context_terms: list[str] = []
        for term in best_terms:
            if best_suffix and term == best_suffix:
                continue
            context_terms.append(term)
            if len(context_terms) == 3:
                break

        suffix = best_suffix or ToolBuildWorkflow._infer_name_suffix(best_terms)
        if suffix and suffix not in context_terms and len(context_terms) < 4:
            context_terms.append(suffix)

        if len(context_terms) == 1:
            fallback = suffix if suffix and suffix != context_terms[0] else "assistant"
            context_terms.append(fallback)

        return " ".join(context_terms[:4])

    @staticmethod
    def _candidate_name_phrases(prompt: str) -> list[str]:
        text = re.sub(r"\s+", " ", prompt).strip()
        candidates: list[str] = []

        def add(value: str) -> None:
            cleaned = re.sub(r"\s+", " ", value).strip(" .,:;!-")
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)

        add(text)

        wrapper_patterns = (
            r"Build a production-ready tool based on this request:\s*(.+?)(?:\s+Requirements:|$)",
            r"Change request:\s*(.+?)(?:\s+Requirements:|$)",
        )
        for pattern in wrapper_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                add(match.group(1))

        phrase_patterns = (
            r"\b(?:build|create|make|develop|generate|design)\s+(?:an?|the)?\s+(.+?)(?:[.!?]|$)",
            r"\b(?:need|want)\s+(?:an?|the)?\s+(.+?)(?:[.!?]|$)",
        )
        for pattern in phrase_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                add(match.group(1))

        seed_candidates = list(candidates)
        for candidate in seed_candidates:
            tool_clause_match = re.match(
                r"^(?:an?\s+)?(?:tool|app|application|service|platform|system|website|site|something)\s+that\s+(.+)$",
                candidate,
                flags=re.IGNORECASE,
            )
            if tool_clause_match:
                add(tool_clause_match.group(1))

            for_match = re.search(r"\bfor\s+(.+)$", candidate, flags=re.IGNORECASE)
            if for_match:
                add(for_match.group(1))

            split_parts = re.split(
                r"\b(?:with|including|include|featuring|using|where)\b",
                candidate,
                maxsplit=1,
                flags=re.IGNORECASE,
            )
            if len(split_parts) == 2:
                add(split_parts[0])
                add(split_parts[1])

        return candidates

    @staticmethod
    def _extract_meaningful_name_terms(text: str) -> list[str]:
        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        stopwords = {
            "a",
            "an",
            "the",
            "all",
            "and",
            "app",
            "application",
            "api",
            "article",
            "articles",
            "are",
            "at",
            "auth",
            "authentication",
            "backend",
            "based",
            "be",
            "build",
            "built",
            "by",
            "create",
            "csv",
            "design",
            "develop",
            "feature",
            "features",
            "for",
            "frontend",
            "from",
            "full",
            "generate",
            "export",
            "exports",
            "including",
            "in",
            "integration",
            "integrations",
            "into",
            "is",
            "it",
            "lightweight",
            "login",
            "make",
            "modern",
            "need",
            "new",
            "of",
            "on",
            "only",
            "our",
            "platform",
            "please",
            "production",
            "project",
            "ready",
            "request",
            "responsive",
            "result",
            "results",
            "screen",
            "secure",
            "simple",
            "signup",
            "solution",
            "stack",
            "system",
            "test",
            "testing",
            "tests",
            "that",
            "this",
            "theme",
            "themes",
            "to",
            "today",
            "tool",
            "trend",
            "trends",
            "trending",
            "urgency",
            "using",
            "user",
            "users",
            "want",
            "we",
            "which",
            "website",
            "where",
            "widget",
            "widgets",
            "with",
            "workflow",
            "your",
            "something",
            "monthly",
            "metric",
            "metrics",
            "chart",
            "charts",
            "ability",
            "abilities",
            "city",
            "cities",
            "cron",
            "date",
            "dates",
            "does",
            "email",
            "emails",
            "file",
            "files",
            "format",
            "formats",
            "interval",
            "language",
            "languages",
            "option",
            "options",
            "pick",
            "range",
            "ranges",
            "select",
            "selected",
            "server",
        }
        action_words = {
            "analyze",
            "analyzing",
            "book",
            "booking",
            "bookings",
            "create",
            "finding",
            "find",
            "generate",
            "generating",
            "manage",
            "managing",
            "monitor",
            "monitoring",
            "schedule",
            "scheduling",
            "searches",
            "searching",
            "summaries",
            "summarize",
            "summarizes",
            "summarizer",
            "summarizing",
            "track",
            "tracking",
        }
        ignored_words = stopwords | action_words

        selected: list[str] = []
        for word in words:
            if word in ignored_words or len(word) < 2:
                continue
            if word in selected:
                continue
            selected.append(word)
        return selected

    @staticmethod
    def _score_name_candidate(terms: list[str], suffix: str | None) -> float:
        if not terms:
            return float("-inf")

        score = float(min(len(terms), 3))
        if len(terms) == 1:
            score -= 0.25
        if len(terms) > 3:
            score -= 0.15 * (len(terms) - 3)
        if suffix:
            score += 0.6
        return score

    @staticmethod
    def _infer_contextual_suffix(text: str) -> str | None:
        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        if any(token in words for token in {"analytics", "metric", "metrics", "chart", "charts", "widget", "widgets"}):
            return "dashboard"

        explicit_suffixes: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("dashboard", ("dashboard",)),
            ("marketplace", ("marketplace",)),
            ("portal", ("portal",)),
            ("api", ("api",)),
            ("tracker", ("tracker", "track", "tracking")),
            ("summarizer", ("summary", "summaries", "summarize", "summarizes", "summarizing")),
            ("monitor", ("monitor", "monitoring")),
            ("notifier", ("alert", "alerts", "notify", "notification", "notifications")),
            ("scheduler", ("scheduler", "schedule", "schedules", "scheduling")),
            ("manager", ("manager", "manage", "manages", "managing", "management")),
            ("generator", ("generator", "generate", "generating")),
            ("search", ("search", "searches", "searching", "find", "finder", "finding")),
            ("assistant", ("assistant", "copilot", "chatbot")),
        )
        for suffix, tokens in explicit_suffixes:
            if any(token in words for token in tokens):
                return suffix
        return None

    @staticmethod
    def _infer_name_suffix(words: list[str]) -> str:
        suffix_priority = (
            "dashboard",
            "tracker",
            "summarizer",
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
        request = self._request_repository.get_by_id(request_id)
        if request and request.get("status") == BuildStatus.STOPPED.value:
            self._build_log_repository.add_log(request_id, "workflow", "Skipping failure update because build was stopped")
            return
        self._request_repository.update_status(request_id, BuildStatus.FAILED, error=reason)
        self._build_log_repository.add_log(request_id, "failed", reason[-12000:])
        self._store_log_artifact(
            request_id=request_id,
            step="failed",
            file_name="failure-summary.log",
            content=reason,
        )
        self._alert_service.send_build_failed_alert(request_id=request_id, reason=reason[-4000:])

    def _store_log_artifact(
        self,
        request_id: str,
        step: str,
        file_name: str,
        content: str,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        if not content:
            return
        self._build_log_artifact_repository.add_artifact(
            request_id=request_id,
            step=step,
            file_name=file_name,
            content=content,
            content_type=content_type,
        )

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

    def _verify_scraped_api_output_with_web(
        self,
        request_id: str,
        attempt: int,
        request_prompt: str,
        api_report: dict[str, object],
    ) -> tuple[bool, str]:
        sample_terms = api_report.get("sampleTerms")
        if not isinstance(sample_terms, list):
            return True, "Scraping signals detected, but no API sample terms were available for web cross-check."
        normalized_samples = [str(item).strip() for item in sample_terms if str(item).strip()]
        if not normalized_samples:
            return True, "Scraping signals detected, but sampled API response had no textual values to cross-check."

        capped_samples = normalized_samples[:5]
        suggested_queries = self._build_scrape_verification_queries(request_prompt=request_prompt, sample_terms=capped_samples)
        verification_prompt = (
            "Use live web search to validate whether these sampled API results are plausible for the user request.\n"
            "You must actually search the web before answering.\n"
            "Prefer current sources that look authoritative for the domain.\n"
            "Compare the API sample values against the web evidence and be strict about mismatches.\n"
            "Return strict JSON only in this shape:\n"
            '{"matches": true|false, "confidence": 0.0, "reason": "short reason", '
            '"queries": ["..."], "sources": ["https://..."], "webItems": ["..."], '
            '"overlap": ["..."], "missingFromApi": ["..."], "unexpectedInApi": ["..."]}\n\n'
            f"User request:\n{request_prompt}\n\n"
            f"Suggested search queries:\n- " + "\n- ".join(suggested_queries) + "\n\n"
            f"API sample terms:\n- " + "\n- ".join(capped_samples)
        )
        timeout = int(getattr(self._settings, "scraping_web_verify_timeout_seconds", 120))
        verification = self._codex_service.run_chat(
            session_id=f"verify-{request_id[:18]}-{attempt}",
            prompt=verification_prompt,
            timeout_seconds=max(30, timeout),
        )
        if not verification.success:
            return (
                True,
                "Scraping web verification was inconclusive because Codex web search verification did not complete successfully. "
                "Allowing deployment and keeping logs for review.\n"
                f"Logs:\n{verification.logs[-3500:]}",
            )

        cleaned_output = self._codex_service.clean_cli_output(verification.logs)
        parsed = self._parse_first_json_object(cleaned_output)
        if not isinstance(parsed, dict):
            return (
                True,
                "Scraping web verification was inconclusive because the verifier did not return parsable JSON. "
                "Allowing deployment and keeping logs for review.\n"
                f"Output:\n{cleaned_output[-3000:]}",
            )

        matches = bool(parsed.get("matches"))
        reason = str(parsed.get("reason") or "No reason provided.")
        sources = self._normalize_string_list(parsed.get("sources"))
        overlap = self._normalize_string_list(parsed.get("overlap"))
        missing_from_api = self._normalize_string_list(parsed.get("missingFromApi"))
        unexpected_in_api = self._normalize_string_list(parsed.get("unexpectedInApi"))
        confidence_value = parsed.get("confidence")
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = 0.0

        if not sources:
            return (
                True,
                "Scraping web verification was inconclusive because the verifier did not provide any live web sources. "
                "Allowing deployment.",
            )
        mismatch_count = len(missing_from_api) + len(unexpected_in_api)
        strong_mismatch = (
            not matches
            and confidence >= 0.78
            and mismatch_count >= 2
            and len(overlap) == 0
        )
        weak_match = matches or len(overlap) > 0 or confidence < 0.78

        if strong_mismatch:
            return (
                False,
                "Scraping/web mismatch detected during endpoint verification.\n"
                f"Confidence: {confidence:.2f}\nReason: {reason}\n"
                f"Missing from API: {missing_from_api}\nUnexpected in API: {unexpected_in_api}\n"
                f"Sources: {sources}",
            )
        if weak_match:
            if missing_from_api or unexpected_in_api:
                return (
                    True,
                    "Scraping data cross-check passed with minor differences that were not strong enough to block deployment.\n"
                    f"Confidence: {confidence:.2f}\nReason: {reason}\n"
                    f"Overlap: {overlap}\nMissing from API: {missing_from_api}\nUnexpected in API: {unexpected_in_api}\n"
                    f"Sources: {sources}",
                )
            return True, f"Scraping data cross-check passed (confidence {confidence:.2f}): {reason}. Sources: {sources}"

        return (
            True,
            "Scraping web verification was inconclusive and did not find a strong enough mismatch to block deployment.\n"
            f"Confidence: {confidence:.2f}\nReason: {reason}\nSources: {sources}",
        )

    @staticmethod
    def _build_scrape_verification_queries(request_prompt: str, sample_terms: list[str]) -> list[str]:
        lowered = re.sub(r"\s+", " ", request_prompt.lower()).strip()
        queries: list[str] = []

        def add(value: str) -> None:
            cleaned = re.sub(r"\s+", " ", value).strip()
            if cleaned and cleaned not in queries:
                queries.append(cleaned)

        if any(token in lowered for token in ("movie", "movies", "show", "showtimes", "bookmyshow", "district")):
            cities = [city for city in ("chennai", "bangalore", "bengaluru") if city in lowered]
            for city in cities or ["india"]:
                add(f"{city} movies now bookmyshow district")
            if sample_terms:
                add(f"{sample_terms[0]} movie showtimes")
        if any(token in lowered for token in ("playstation", "ps4", "ps5", "sony", "digital game", "digital games")):
            add("site:playstation.com digital games store")
            add("site:playstation.com PS5 PS4 games sale")
            if sample_terms:
                add(f"{sample_terms[0]} site:playstation.com")

        add(request_prompt[:140])
        for term in sample_terms[:3]:
            add(term)
        return queries[:5]

    @staticmethod
    def _normalize_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = re.sub(r"\s+", " ", str(item)).strip()
            if not candidate:
                continue
            lowered = candidate.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(candidate)
        return normalized

    @staticmethod
    def _parse_first_json_object(value: str) -> dict | None:
        text = (value or "").strip()
        if not text:
            return None

        try:
            parsed_direct = json.loads(text)
            return parsed_direct if isinstance(parsed_direct, dict) else None
        except json.JSONDecodeError:
            pass

        for match in re.finditer(r"\{[\s\S]*?\}", text):
            candidate = match.group(0)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _classify_non_retryable_generation_failure(logs: str) -> str | None:
        text = (logs or "").lower()
        if not text:
            return None

        usage_limit_signals = (
            "hit your usage limit",
            "purchase more credits",
            "upgrade to pro",
            "codex/settings/usage",
        )
        if sum(1 for signal in usage_limit_signals if signal in text) >= 2:
            return (
                "Codex generation failed due account usage limits. "
                "Retrying in this run will not help; please renew/upgrade usage and rerun."
            )

        auth_signals = (
            "not logged in",
            "login required",
            "authentication failed",
            "please log in",
        )
        if any(signal in text for signal in auth_signals):
            return (
                "Codex generation failed due authentication/login state. "
                "Please restore Codex login before retrying."
            )

        return None
