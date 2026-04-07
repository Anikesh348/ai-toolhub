import threading
from datetime import datetime, timedelta

from app.models.status import BuildStatus, ToolStatus
from app.repositories.build_log_artifact_repository import BuildLogArtifactRepository
from app.repositories.build_log_repository import BuildLogRepository
from app.repositories.request_repository import RequestRepository
from app.repositories.tool_repository import ToolRepository
from app.services.docker_service import DockerService, RuntimePlan
from app.services.port_allocator_service import PortAllocatorService
from app.services.testing_service import TestingService
from app.utils.time import now_ist
from app.workflows.tool_build_workflow import ToolBuildWorkflow


class ToolBuilderService:
    _TERMINAL_BUILD_STATUSES = {BuildStatus.RUNNING.value, BuildStatus.STOPPED.value, BuildStatus.FAILED.value}

    def __init__(
        self,
        request_repository: RequestRepository,
        build_log_repository: BuildLogRepository,
        build_log_artifact_repository: BuildLogArtifactRepository,
        tool_repository: ToolRepository,
        docker_service: DockerService,
        testing_service: TestingService,
        port_allocator_service: PortAllocatorService,
        workflow: ToolBuildWorkflow,
    ) -> None:
        self._request_repository = request_repository
        self._build_log_repository = build_log_repository
        self._build_log_artifact_repository = build_log_artifact_repository
        self._tool_repository = tool_repository
        self._docker_service = docker_service
        self._testing_service = testing_service
        self._port_allocator_service = port_allocator_service
        self._workflow = workflow

    def _monitor_grace_seconds(self) -> int:
        settings = getattr(self._docker_service, "_settings", None)
        raw_value = getattr(settings, "monitor_startup_grace_seconds", 90)
        try:
            return max(0, int(raw_value))
        except (TypeError, ValueError):
            return 90

    def start_generation(
        self,
        prompt: str,
        name: str | None,
        base_request_id: str | None = None,
        rebuild_tool_id: str | None = None,
        model: str | None = None,
        workflow_prompt: str | None = None,
        prompt_already_refined: bool = False,
    ) -> dict:
        workflow_base_request_id = base_request_id
        request = None
        persisted_prompt = prompt
        generation_prompt = (workflow_prompt or "").strip() or prompt

        # Rebuilds should continue in the existing tool workspace (same request id)
        # so modify-chat iterations do not fan out into new workspace directories.
        if rebuild_tool_id and base_request_id:
            request = self._request_repository.prepare_for_rebuild(
                request_id=base_request_id,
                prompt=persisted_prompt,
            )
            if request is not None:
                workflow_base_request_id = None

        if request is None:
            request = self._request_repository.create(prompt=persisted_prompt)

        request_id = request["id"]
        thread = threading.Thread(
            target=self._workflow.run,
            kwargs={
                "request_id": request_id,
                "tool_name_hint": name,
                "base_request_id": workflow_base_request_id,
                "rebuild_tool_id": rebuild_tool_id,
                "model": model,
                "prompt_override": generation_prompt,
                "prompt_already_refined": prompt_already_refined,
            },
            daemon=True,
        )
        thread.start()
        return request

    def list_tools(self) -> list[dict]:
        tools = self._tool_repository.list_all()
        refreshed: list[dict] = []
        for tool in tools:
            updated_tool, _ = self._reconcile_tool_and_request_state(tool=tool, request_status=None)
            refreshed.append(updated_tool)
        return refreshed

    def get_tool(self, tool_id: str) -> dict | None:
        tool = self._tool_repository.get_by_id(tool_id)
        if tool is None:
            return None
        refreshed_tool, _ = self._reconcile_tool_and_request_state(tool=tool, request_status=None)
        return refreshed_tool

    def get_tool_for_request(self, request_id: str) -> dict | None:
        tool = self._tool_repository.get_by_request_ids([request_id]).get(request_id)
        if tool is None:
            return None
        request = self._request_repository.get_by_id(request_id)
        request_status = request["status"] if request else None
        refreshed_tool, _ = self._reconcile_tool_and_request_state(tool=tool, request_status=request_status)
        return refreshed_tool

    def stop_tool(self, tool_id: str) -> dict | None:
        tool = self._tool_repository.get_by_id(tool_id)
        if tool is None:
            return None
        if tool.get("containerId"):
            try:
                self._docker_service.stop_container(tool["containerId"])
            except Exception:  # pylint: disable=broad-except
                pass
        try:
            self._docker_service.stop_request_containers(
                request_id=tool["requestId"],
                tool_id=tool_id,
                runtime_name=tool.get("runtimeName"),
            )
        except Exception:  # pylint: disable=broad-except
            pass
        self._tool_repository.update_status(tool_id, ToolStatus.STOPPED, clear_runtime=True)
        self._build_log_repository.add_log(tool["requestId"], "manual_stop", "Tool stopped manually")
        return self._tool_repository.get_by_id(tool_id)

    def delete_tool(self, tool_id: str) -> bool:
        tool = self._tool_repository.get_by_id(tool_id)
        if tool is None:
            return False

        if tool.get("containerId"):
            try:
                self._docker_service.stop_container(tool["containerId"])
            except Exception:  # pylint: disable=broad-except
                pass
        try:
            self._docker_service.stop_request_containers(
                request_id=tool["requestId"],
                tool_id=tool_id,
                runtime_name=tool.get("runtimeName"),
            )
        except Exception:  # pylint: disable=broad-except
            pass

        deleted = self._tool_repository.delete(tool_id)
        if deleted:
            self._build_log_repository.add_log(tool["requestId"], "manual_delete", "Tool deleted manually")
        return deleted

    def delete_job(self, request_id: str) -> bool:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return False

        tool = self._tool_repository.get_by_request_id(request_id)
        tool_id = tool["toolId"] if tool else None
        runtime_name = tool.get("runtimeName") if tool else None
        if tool and tool.get("containerId"):
            try:
                self._docker_service.stop_container(tool["containerId"])
            except Exception:  # pylint: disable=broad-except
                pass
        try:
            self._docker_service.stop_request_containers(
                request_id=request_id,
                tool_id=tool_id,
                runtime_name=runtime_name,
            )
        except Exception:  # pylint: disable=broad-except
            pass

        if tool_id:
            self._tool_repository.delete(tool_id)
        self._build_log_repository.delete_for_request(request_id)
        self._build_log_artifact_repository.delete_for_request(request_id)
        self._request_repository.delete(request_id)
        return True

    def stop_job(self, request_id: str) -> tuple[dict | None, str | None]:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return None, "Job not found"
        if request["status"] in self._TERMINAL_BUILD_STATUSES:
            return request, None

        try:
            self._docker_service.stop_build_containers(request_id=request_id)
        except Exception:  # pylint: disable=broad-except
            pass

        self._request_repository.update_status(
            request_id=request_id,
            status=BuildStatus.STOPPED,
            error="Build stopped by user",
        )
        self._build_log_repository.add_log(request_id, "manual_stop_build", "Build stopped manually")
        return self._request_repository.get_by_id(request_id), None

    def start_tool(self, tool_id: str) -> tuple[dict | None, str | None]:
        tool = self._tool_repository.get_by_id(tool_id)
        if tool is None:
            return None, "Tool not found"

        if tool["status"] == ToolStatus.RUNNING.value:
            return tool, None
        if not tool.get("dockerImage"):
            return None, "Docker image not available for this tool"

        runtime_plan = self._docker_service.inspect_runtime_plan(tool["requestId"])
        service_ports, port_error = self._resolve_or_allocate_service_ports(
            tool=tool,
            runtime_plan=runtime_plan,
        )
        if port_error:
            return None, port_error

        ui_service = runtime_plan.ui_service or (next(iter(service_ports.keys()), None))
        ui_port = service_ports.get(ui_service) if ui_service else None
        smoke_service = runtime_plan.smoke_service or ui_service
        smoke_port = service_ports.get(smoke_service) if smoke_service else ui_port
        if ui_port is None or smoke_port is None:
            return None, "Unable to resolve runtime ports for this tool"

        self._tool_repository.assign_ports(tool_id=tool_id, ports=service_ports, ui_port=ui_port)

        deploy_ok, container_id, deploy_error = self._docker_service.run_tool_runtime(
            request_id=tool["requestId"],
            tool_id=tool_id,
            runtime_name=tool.get("runtimeName") or tool["name"],
            image_tag=tool["dockerImage"],
            runtime_plan=runtime_plan,
            service_host_ports=service_ports,
        )
        if not deploy_ok or not container_id:
            self._tool_repository.update_status(tool_id, ToolStatus.FAILED)
            self._request_repository.update_status(
                request_id=tool["requestId"],
                status=BuildStatus.FAILED,
                error=deploy_error or "Unable to start tool runtime",
            )
            return None, f"Unable to start tool runtime: {deploy_error}"

        self._tool_repository.update_deployment(
            tool_id=tool_id,
            container_id=container_id,
            port=ui_port,
            ports=service_ports,
            ui_port=ui_port,
            status=ToolStatus.DEPLOYING,
            monitor_ignore_until=now_ist() + timedelta(seconds=self._monitor_grace_seconds()),
        )

        smoke_ok, smoke_message = self._testing_service.smoke_test(smoke_port)
        if not smoke_ok:
            runtime_logs = self._docker_service.get_container_logs(container_id)
            self._docker_service.stop_container(container_id)
            self._tool_repository.update_status(tool_id, ToolStatus.FAILED, clear_runtime=True)
            self._request_repository.update_status(
                request_id=tool["requestId"],
                status=BuildStatus.FAILED,
                error=f"Start smoke test failed: {smoke_message}",
            )
            self._build_log_repository.add_log(
                tool["requestId"],
                "manual_start_failed",
                f"{smoke_message}\n{runtime_logs[-3000:]}".strip(),
            )
            self._store_log_artifact(
                request_id=tool["requestId"],
                step="manual_start_failed",
                file_name="manual-start-failed.log",
                content=f"{smoke_message}\n\n{runtime_logs}".strip(),
            )
            return None, f"Start smoke test failed: {smoke_message}"

        self._tool_repository.update_status(tool_id, ToolStatus.RUNNING)
        self._request_repository.update_status(
            request_id=tool["requestId"],
            status=BuildStatus.RUNNING,
            error=None,
        )
        mapped_ports = ", ".join(f"{name}:{port}" for name, port in service_ports.items())
        self._build_log_repository.add_log(
            tool["requestId"],
            "manual_start",
            f"Tool started manually. UI port {ui_port}. Service ports [{mapped_ports}]",
        )
        self._store_log_artifact(
            request_id=tool["requestId"],
            step="manual_start",
            file_name="manual-start.log",
            content=f"Tool started manually. UI port {ui_port}. Service ports [{mapped_ports}]",
        )
        return self._tool_repository.get_by_id(tool_id), None

    def rebuild_tool(self, tool_id: str) -> tuple[dict | None, str | None]:
        tool = self._tool_repository.get_by_id(tool_id)
        if tool is None:
            return None, "Tool not found"

        request = self._request_repository.get_by_id(tool["requestId"])
        if request is None:
            return None, "Associated build request not found"
        if request["status"] not in self._TERMINAL_BUILD_STATUSES:
            return None, "A build is already running for this tool"

        original_prompt = str(request.get("initialPrompt") or request.get("prompt") or "").strip()
        latest_prompt = str(request.get("latestPrompt") or request.get("prompt") or "").strip()
        if latest_prompt and latest_prompt != original_prompt:
            current_prompt = f"Original tool request:\n{original_prompt}\n\nLatest user request:\n{latest_prompt}"
        else:
            current_prompt = original_prompt
        rebuild_prompt = (
            "Rebuild and redeploy the existing tool from the current workspace.\n"
            "No feature changes are requested.\n"
            "Only make minimal fixes if required to pass tests, build, and runtime smoke checks.\n\n"
            f"Current tool context:\n{current_prompt}"
        )
        job = self.start_generation(
            prompt=rebuild_prompt,
            name=tool["name"],
            base_request_id=tool["requestId"],
            rebuild_tool_id=tool_id,
        )
        self._build_log_repository.add_log(
            tool["requestId"],
            "manual_rebuild",
            "Manual rebuild requested. Rebuilding image and redeploying runtime.",
        )
        return job, None

    def get_job(self, request_id: str) -> dict | None:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return None
        tool = self._tool_repository.get_by_request_ids([request_id]).get(request_id)
        effective_status = request["status"]
        effective_error = request.get("error")
        if tool:
            _, effective_status = self._reconcile_tool_and_request_state(
                tool=tool,
                request_status=effective_status,
            )
            if effective_status == BuildStatus.RUNNING.value:
                effective_error = None
        logs = self._build_log_repository.get_logs_for_request(request_id)
        return {**request, "status": effective_status, "error": effective_error, "logs": logs}

    def get_job_state(self, request_id: str) -> dict | None:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return None
        tool = self._tool_repository.get_by_request_ids([request_id]).get(request_id)
        effective_status = request["status"]
        effective_error = request.get("error")
        if tool:
            _, effective_status = self._reconcile_tool_and_request_state(
                tool=tool,
                request_status=effective_status,
            )
            if effective_status == BuildStatus.RUNNING.value:
                effective_error = None
        return {**request, "status": effective_status, "error": effective_error}

    def list_jobs(self, limit: int = 200) -> list[dict]:
        requests = self._request_repository.list_recent(limit=limit)
        request_ids = [request["id"] for request in requests]
        tools_by_request = self._tool_repository.get_by_request_ids(request_ids)
        latest_logs = self._build_log_repository.get_latest_logs_for_requests(request_ids)

        summaries: list[dict] = []
        for request in requests:
            tool = tools_by_request.get(request["id"])
            effective_request_status = request["status"]
            effective_request_error = request.get("error")
            if tool:
                tool, effective_request_status = self._reconcile_tool_and_request_state(
                    tool=tool,
                    request_status=effective_request_status,
                )
                if effective_request_status == BuildStatus.RUNNING.value:
                    effective_request_error = None
            latest_log = latest_logs.get(request["id"])
            summaries.append(
                {
                    "id": request["id"],
                    "prompt": request["prompt"],
                    "status": effective_request_status,
                    "error": effective_request_error,
                    "createdAt": request["createdAt"],
                    "updatedAt": request["updatedAt"],
                    "toolId": tool["toolId"] if tool else None,
                    "toolStatus": tool["status"] if tool else None,
                    "toolName": tool["name"] if tool else None,
                    "port": tool["port"] if tool else None,
                    "uiPort": tool["uiPort"] if tool else None,
                    "ports": tool["ports"] if tool else None,
                    "containerId": tool["containerId"] if tool else None,
                    "lastStep": latest_log["step"] if latest_log else None,
                    "lastMessage": latest_log["message"] if latest_log else None,
                    "lastLogAt": latest_log["timestamp"] if latest_log else None,
                }
            )
        return summaries

    def get_job_logs_after(self, request_id: str, timestamp: datetime | None, limit: int = 200) -> list[dict]:
        return self._build_log_repository.get_logs_after(request_id=request_id, timestamp=timestamp, limit=limit)

    def list_job_log_artifacts(self, request_id: str) -> list[dict]:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return []
        return self._build_log_artifact_repository.list_for_request(request_id=request_id)

    def get_job_log_artifact(self, request_id: str, artifact_id: str) -> dict | None:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return None
        return self._build_log_artifact_repository.get_by_id_for_request(
            request_id=request_id,
            artifact_id=artifact_id,
        )

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

    def _resolve_or_allocate_service_ports(self, tool: dict, runtime_plan: RuntimePlan) -> tuple[dict[str, int], str | None]:
        allocation_order = self._allocation_order(runtime_plan)
        existing_ports = self._existing_service_ports(tool=tool, allocation_order=allocation_order)

        if len(set(existing_ports.values())) != len(existing_ports):
            return {}, "Assigned service ports are duplicated; unable to start runtime safely"

        for service_name, port in existing_ports.items():
            if not self._port_allocator_service.is_port_available(port, exclude_tool_id=tool["toolId"]):
                return {}, f"Assigned port {port} for service `{service_name}` is not currently available"

        missing_services = [service for service in allocation_order if service not in existing_ports]
        if missing_services:
            try:
                allocated = self._port_allocator_service.allocate_ports(
                    count=len(missing_services),
                    reserve=True,
                    tool_id=tool["toolId"],
                    request_id=tool.get("requestId"),
                )
            except RuntimeError as exc:
                return {}, str(exc)

            for index, service_name in enumerate(missing_services):
                existing_ports[service_name] = allocated[index]

        if not existing_ports:
            return {}, "No service ports could be resolved for this tool"
        return existing_ports, None

    def _reconcile_tool_and_request_state(self, tool: dict, request_status: str | None) -> tuple[dict, str]:
        effective_request_status = request_status
        if effective_request_status is None:
            request = self._request_repository.get_by_id(tool["requestId"])
            effective_request_status = request["status"] if request else BuildStatus.FAILED.value

        container_id = str(tool.get("containerId") or "").strip()
        if not container_id:
            recovered_container_id = self._docker_service.resolve_running_container_id_for_tool(
                tool_id=tool["toolId"],
                runtime_name=tool.get("runtimeName"),
            )
            if recovered_container_id:
                existing_ports = tool.get("ports") if isinstance(tool.get("ports"), dict) else {}
                resolved_ui_port = tool.get("uiPort") or tool.get("port")
                if resolved_ui_port is None and existing_ports:
                    resolved_ui_port = next(iter(existing_ports.values()))
                try:
                    ui_port = int(resolved_ui_port) if resolved_ui_port is not None else None
                except (TypeError, ValueError):
                    ui_port = None

                if ui_port is not None and ui_port > 0:
                    self._tool_repository.update_deployment(
                        tool_id=tool["toolId"],
                        container_id=recovered_container_id,
                        port=ui_port,
                        ports={name: int(port) for name, port in existing_ports.items()},
                        ui_port=ui_port,
                        status=ToolStatus.RUNNING,
                        monitor_ignore_until=now_ist() + timedelta(seconds=self._monitor_grace_seconds()),
                    )
                else:
                    self._tool_repository.update_status(
                        tool["toolId"],
                        ToolStatus.RUNNING,
                        crash_alert_sent=False,
                    )

                refreshed = self._tool_repository.get_by_id(tool["toolId"])
                if refreshed:
                    tool = refreshed
                    container_id = str(refreshed.get("containerId") or "").strip()
                else:
                    tool = {**tool, "status": ToolStatus.RUNNING.value, "containerId": recovered_container_id}
                    container_id = recovered_container_id

                if effective_request_status == BuildStatus.FAILED.value:
                    self._request_repository.update_status(
                        request_id=tool["requestId"],
                        status=BuildStatus.RUNNING,
                        error=None,
                    )
                    effective_request_status = BuildStatus.RUNNING.value

        if not container_id:
            return tool, effective_request_status

        tool_status = str(tool.get("status") or "")
        if tool_status == ToolStatus.DEPLOYING.value and effective_request_status not in self._TERMINAL_BUILD_STATUSES:
            return tool, effective_request_status

        try:
            is_running = self._docker_service.container_running(container_id)
        except Exception:  # pylint: disable=broad-except
            return tool, effective_request_status

        if is_running:
            if tool_status != ToolStatus.RUNNING.value:
                self._tool_repository.update_status(
                    tool["toolId"],
                    ToolStatus.RUNNING,
                    crash_alert_sent=False,
                )
                refreshed = self._tool_repository.get_by_id(tool["toolId"])
                if refreshed:
                    tool = refreshed
                else:
                    tool = {**tool, "status": ToolStatus.RUNNING.value, "crashAlertSent": False}
            if effective_request_status == BuildStatus.FAILED.value:
                self._request_repository.update_status(
                    request_id=tool["requestId"],
                    status=BuildStatus.RUNNING,
                    error=None,
                )
                effective_request_status = BuildStatus.RUNNING.value
            return tool, effective_request_status

        if tool_status == ToolStatus.RUNNING.value:
            self._tool_repository.update_status(
                tool["toolId"],
                ToolStatus.FAILED,
                crash_alert_sent=False,
                clear_runtime=True,
            )
            refreshed = self._tool_repository.get_by_id(tool["toolId"])
            if refreshed:
                tool = refreshed
            else:
                tool = {**tool, "status": ToolStatus.FAILED.value, "containerId": None}
            if effective_request_status == BuildStatus.RUNNING.value:
                self._request_repository.update_status(
                    request_id=tool["requestId"],
                    status=BuildStatus.FAILED,
                    error="Tool container is not running",
                )
                effective_request_status = BuildStatus.FAILED.value
        return tool, effective_request_status

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

    @staticmethod
    def _existing_service_ports(tool: dict, allocation_order: list[str]) -> dict[str, int]:
        existing_ports: dict[str, int] = {}
        allowed_services = set(allocation_order)
        raw_ports = tool.get("ports")
        if isinstance(raw_ports, dict):
            for service_name, value in raw_ports.items():
                if not isinstance(service_name, str) or value is None:
                    continue
                if allowed_services and service_name not in allowed_services:
                    continue
                existing_ports[service_name] = int(value)

        if not existing_ports:
            port = tool.get("port")
            if port is not None:
                default_service = allocation_order[0] if allocation_order else "app"
                existing_ports[default_service] = int(port)

        return existing_ports
