import threading
import time
from datetime import datetime, timedelta

from app.models.status import BuildStatus, ToolStatus
from app.repositories.request_repository import RequestRepository
from app.repositories.tool_repository import ToolRepository
from app.services.alert_service import AlertService
from app.services.docker_service import DockerService
from app.utils.config import Settings
from app.utils.logger import get_logger
from app.utils.time import now_ist


class ToolMonitorService:
    def __init__(
        self,
        settings: Settings,
        tool_repository: ToolRepository,
        request_repository: RequestRepository,
        docker_service: DockerService,
        alert_service: AlertService,
    ) -> None:
        self._settings = settings
        self._tool_repository = tool_repository
        self._request_repository = request_repository
        self._docker_service = docker_service
        self._alert_service = alert_service
        self._logger = get_logger(__name__)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _monitor_grace_until(self) -> datetime:
        seconds = max(0, int(getattr(self._settings, "monitor_startup_grace_seconds", 90)))
        return now_ist() + timedelta(seconds=seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._check_running_tools()
            self._stop_event.wait(self._settings.monitor_poll_interval_seconds)

    def _check_running_tools(self) -> None:
        tools = self._tool_repository.get_running_without_crash_alert()
        for tool in tools:
            container_id = str(tool.get("containerId") or "").strip()
            if not container_id:
                recovered_container_id = self._docker_service.resolve_running_container_id_for_tool(
                    tool_id=tool["toolId"],
                    runtime_name=tool.get("runtimeName"),
                )
                if recovered_container_id:
                    self._tool_repository.update_deployment(
                        tool_id=tool["toolId"],
                        container_id=recovered_container_id,
                        port=int(tool.get("uiPort") or tool.get("port") or next(iter(tool.get("ports", {}).values()), 0)),
                        ports={name: int(port) for name, port in (tool.get("ports") or {}).items()},
                        ui_port=int(tool.get("uiPort") or tool.get("port") or next(iter(tool.get("ports", {}).values()), 0)),
                        status=ToolStatus.RUNNING,
                        monitor_ignore_until=self._monitor_grace_until(),
                    )
                continue
            is_running = self._docker_service.container_running(container_id)
            if is_running:
                continue
            recovered_container_id = self._docker_service.resolve_running_container_id_for_tool(
                tool_id=tool["toolId"],
                runtime_name=tool.get("runtimeName"),
            )
            if recovered_container_id and recovered_container_id != container_id:
                self._tool_repository.update_deployment(
                    tool_id=tool["toolId"],
                    container_id=recovered_container_id,
                    port=int(tool.get("uiPort") or tool.get("port") or next(iter(tool.get("ports", {}).values()), 0)),
                    ports={name: int(port) for name, port in (tool.get("ports") or {}).items()},
                    ui_port=int(tool.get("uiPort") or tool.get("port") or next(iter(tool.get("ports", {}).values()), 0)),
                    status=ToolStatus.RUNNING,
                    monitor_ignore_until=self._monitor_grace_until(),
                )
                continue
            self._logger.warning("Detected crashed tool: %s", tool["toolId"])
            self._tool_repository.update_status(
                tool["toolId"],
                ToolStatus.FAILED,
                crash_alert_sent=True,
                clear_runtime=True,
            )
            self._request_repository.update_status(
                request_id=tool["requestId"],
                status=BuildStatus.FAILED,
                error="Tool container exited unexpectedly",
            )
            self._alert_service.send_tool_crashed_alert(tool["name"], container_id)
