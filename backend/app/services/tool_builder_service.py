import threading
from datetime import datetime

from app.models.status import ToolStatus
from app.repositories.build_log_repository import BuildLogRepository
from app.repositories.request_repository import RequestRepository
from app.repositories.tool_repository import ToolRepository
from app.services.docker_service import DockerService, RuntimePlan
from app.services.port_allocator_service import PortAllocatorService
from app.services.testing_service import TestingService
from app.workflows.tool_build_workflow import ToolBuildWorkflow


class ToolBuilderService:
    def __init__(
        self,
        request_repository: RequestRepository,
        build_log_repository: BuildLogRepository,
        tool_repository: ToolRepository,
        docker_service: DockerService,
        testing_service: TestingService,
        port_allocator_service: PortAllocatorService,
        workflow: ToolBuildWorkflow,
    ) -> None:
        self._request_repository = request_repository
        self._build_log_repository = build_log_repository
        self._tool_repository = tool_repository
        self._docker_service = docker_service
        self._testing_service = testing_service
        self._port_allocator_service = port_allocator_service
        self._workflow = workflow

    def start_generation(
        self,
        prompt: str,
        name: str | None,
        base_request_id: str | None = None,
        rebuild_tool_id: str | None = None,
    ) -> dict:
        workflow_base_request_id = base_request_id
        request = None

        # Rebuilds should continue in the existing tool workspace (same request id)
        # so modify-chat iterations do not fan out into new workspace directories.
        if rebuild_tool_id and base_request_id:
            request = self._request_repository.prepare_for_rebuild(
                request_id=base_request_id,
                prompt=prompt,
            )
            if request is not None:
                workflow_base_request_id = None

        if request is None:
            request = self._request_repository.create(prompt=prompt)

        request_id = request["id"]
        thread = threading.Thread(
            target=self._workflow.run,
            kwargs={
                "request_id": request_id,
                "tool_name_hint": name,
                "base_request_id": workflow_base_request_id,
                "rebuild_tool_id": rebuild_tool_id,
            },
            daemon=True,
        )
        thread.start()
        return request

    def list_tools(self) -> list[dict]:
        return self._tool_repository.list_all()

    def get_tool(self, tool_id: str) -> dict | None:
        return self._tool_repository.get_by_id(tool_id)

    def get_tool_for_request(self, request_id: str) -> dict | None:
        return self._tool_repository.get_by_request_ids([request_id]).get(request_id)

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
        self._request_repository.delete(request_id)
        return True

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
            return None, f"Unable to start tool runtime: {deploy_error}"

        self._tool_repository.update_deployment(
            tool_id=tool_id,
            container_id=container_id,
            port=ui_port,
            ports=service_ports,
            ui_port=ui_port,
            status=ToolStatus.DEPLOYING,
        )

        smoke_ok, smoke_message = self._testing_service.smoke_test(smoke_port)
        if not smoke_ok:
            runtime_logs = self._docker_service.get_container_logs(container_id)
            self._docker_service.stop_container(container_id)
            self._tool_repository.update_status(tool_id, ToolStatus.FAILED, clear_runtime=True)
            self._build_log_repository.add_log(
                tool["requestId"],
                "manual_start_failed",
                f"{smoke_message}\n{runtime_logs[-3000:]}".strip(),
            )
            return None, f"Start smoke test failed: {smoke_message}"

        self._tool_repository.update_status(tool_id, ToolStatus.RUNNING)
        mapped_ports = ", ".join(f"{name}:{port}" for name, port in service_ports.items())
        self._build_log_repository.add_log(
            tool["requestId"],
            "manual_start",
            f"Tool started manually. UI port {ui_port}. Service ports [{mapped_ports}]",
        )
        return self._tool_repository.get_by_id(tool_id), None

    def get_job(self, request_id: str) -> dict | None:
        request = self._request_repository.get_by_id(request_id)
        if request is None:
            return None
        logs = self._build_log_repository.get_logs_for_request(request_id)
        return {**request, "logs": logs}

    def get_job_state(self, request_id: str) -> dict | None:
        return self._request_repository.get_by_id(request_id)

    def list_jobs(self, limit: int = 200) -> list[dict]:
        requests = self._request_repository.list_recent(limit=limit)
        request_ids = [request["id"] for request in requests]
        tools_by_request = self._tool_repository.get_by_request_ids(request_ids)
        latest_logs = self._build_log_repository.get_latest_logs_for_requests(request_ids)

        summaries: list[dict] = []
        for request in requests:
            tool = tools_by_request.get(request["id"])
            latest_log = latest_logs.get(request["id"])
            summaries.append(
                {
                    "id": request["id"],
                    "prompt": request["prompt"],
                    "status": request["status"],
                    "error": request.get("error"),
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
