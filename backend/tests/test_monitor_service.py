from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock

from app.services.monitor_service import ToolMonitorService


def test_monitor_recovers_runtime_without_sending_crash_alert() -> None:
    settings = SimpleNamespace(monitor_poll_interval_seconds=20, monitor_startup_grace_seconds=90)
    tool_repository = Mock()
    tool_repository.get_running_without_crash_alert.return_value = [
        {
            "toolId": "tool-1",
            "requestId": "request-1",
            "name": "Movie Alerts",
            "runtimeName": "movie-alerts",
            "containerId": "stale-container",
            "port": 3010,
            "uiPort": 3010,
            "ports": {"app": 3010},
        }
    ]
    request_repository = Mock()
    docker_service = Mock()
    docker_service.container_running.return_value = False
    docker_service.resolve_running_container_id_for_tool.return_value = "compose:tool-movie-alerts"
    alert_service = Mock()

    service = ToolMonitorService(
        settings=settings,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        alert_service=alert_service,  # type: ignore[arg-type]
    )

    service._check_running_tools()  # pylint: disable=protected-access

    tool_repository.update_deployment.assert_called_once_with(
        tool_id="tool-1",
        container_id="compose:tool-movie-alerts",
        port=3010,
        ports={"app": 3010},
        ui_port=3010,
        status=ANY,
        monitor_ignore_until=ANY,
    )
    tool_repository.update_status.assert_not_called()
    request_repository.update_status.assert_not_called()
    alert_service.send_tool_crashed_alert.assert_not_called()


def test_monitor_query_skips_tools_inside_grace_window() -> None:
    future = datetime.now(tz=timezone.utc) + timedelta(seconds=60)
    collection = Mock()
    collection.find.return_value = []

    from app.repositories.tool_repository import ToolRepository

    repository = ToolRepository(collection)
    repository.get_running_without_crash_alert()

    query = collection.find.call_args.args[0]
    assert query["status"] == "RUNNING"
    assert query["crashAlertSent"] is False
    assert query["$or"][1]["monitorIgnoreUntil"]["$lte"] <= future
