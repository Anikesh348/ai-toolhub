from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from app.models.status import BuildStatus, ToolStatus
from app.services.docker_service import DockerService
from app.services.tool_builder_service import ToolBuilderService


def _build_service(request_repository: object) -> ToolBuilderService:
    return ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),
        tool_repository=Mock(),
        docker_service=Mock(),
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )


def test_start_generation_reuses_existing_request_for_rebuild() -> None:
    request_repository = Mock()
    request_repository.prepare_for_rebuild.return_value = {
        "id": "existing-request-id",
        "status": "PENDING",
    }
    request_repository.create.return_value = {
        "id": "new-request-id",
        "status": "PENDING",
    }
    service = _build_service(request_repository=request_repository)

    with patch("app.services.tool_builder_service.threading.Thread") as thread_cls:
        thread_instance = Mock()
        thread_cls.return_value = thread_instance

        job = service.start_generation(
            prompt="Apply dark mode updates",
            name="demo tool",
            base_request_id="existing-request-id",
            rebuild_tool_id="tool-1",
            model="gpt-5.4-mini",
        )

    assert job["id"] == "existing-request-id"
    request_repository.prepare_for_rebuild.assert_called_once_with(
        request_id="existing-request-id",
        prompt="Apply dark mode updates",
    )
    request_repository.create.assert_not_called()
    thread_kwargs = thread_cls.call_args.kwargs["kwargs"]
    assert thread_kwargs["request_id"] == "existing-request-id"
    assert thread_kwargs["base_request_id"] is None
    assert thread_kwargs["model"] == "gpt-5.4-mini"
    thread_instance.start.assert_called_once()


def test_start_generation_falls_back_to_new_request_when_rebuild_seed_missing() -> None:
    request_repository = Mock()
    request_repository.prepare_for_rebuild.return_value = None
    request_repository.create.return_value = {
        "id": "new-request-id",
        "status": "PENDING",
    }
    service = _build_service(request_repository=request_repository)

    with patch("app.services.tool_builder_service.threading.Thread") as thread_cls:
        thread_instance = Mock()
        thread_cls.return_value = thread_instance

        job = service.start_generation(
            prompt="Apply dashboard updates",
            name="demo tool",
            base_request_id="old-request-id",
            rebuild_tool_id="tool-1",
        )

    assert job["id"] == "new-request-id"
    request_repository.prepare_for_rebuild.assert_called_once_with(
        request_id="old-request-id",
        prompt="Apply dashboard updates",
    )
    request_repository.create.assert_called_once_with(prompt="Apply dashboard updates")
    thread_kwargs = thread_cls.call_args.kwargs["kwargs"]
    assert thread_kwargs["request_id"] == "new-request-id"
    assert thread_kwargs["base_request_id"] == "old-request-id"
    assert thread_kwargs["model"] is None
    thread_instance.start.assert_called_once()


def test_builder_mounts_include_operator_allowed_paths(tmp_path) -> None:
    workspace_path = tmp_path / "workspace"
    allowed_path = tmp_path / "allowed"
    workspace_path.mkdir()
    allowed_path.mkdir()

    settings = SimpleNamespace(
        codex_workspace_host=str(workspace_path),
        allowed_paths=[str(allowed_path)],
        project_paths=[],
    )

    with patch("app.services.docker_service.docker.from_env", return_value=Mock()):
        service = DockerService(settings=settings)  # type: ignore[arg-type]

    mounts = service._builder_allowed_path_mounts()  # pylint: disable=protected-access
    assert str(allowed_path) in mounts
    assert mounts[str(allowed_path)] == {
        "bind": str(allowed_path),
        "mode": "rw",
    }


def test_rebuild_tool_starts_redeploy_workflow() -> None:
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "request-1",
        "prompt": "Build a lightweight dashboard tool.",
        "status": "RUNNING",
    }
    build_log_repository = Mock()
    tool_repository = Mock()
    tool_repository.get_by_id.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Demo Tool",
    }

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=Mock(),
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    with patch.object(service, "start_generation", return_value={"id": "request-1", "status": "PENDING"}) as starter:
        job, error = service.rebuild_tool("tool-1")

    assert error is None
    assert job == {"id": "request-1", "status": "PENDING"}
    starter.assert_called_once()
    called_kwargs = starter.call_args.kwargs
    assert called_kwargs["name"] == "Demo Tool"
    assert called_kwargs["base_request_id"] == "request-1"
    assert called_kwargs["rebuild_tool_id"] == "tool-1"
    assert "No feature changes are requested." in called_kwargs["prompt"]
    build_log_repository.add_log.assert_called_once()


def test_rebuild_tool_rejects_when_build_already_running() -> None:
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "request-1",
        "prompt": "Build a lightweight dashboard tool.",
        "status": "TESTING",
    }
    tool_repository = Mock()
    tool_repository.get_by_id.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Demo Tool",
    }
    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=Mock(),
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    job, error = service.rebuild_tool("tool-1")
    assert job is None
    assert error == "A build is already running for this tool"


def test_stop_job_marks_build_stopped_and_keeps_record() -> None:
    request_repository = Mock()
    request_repository.get_by_id.side_effect = [
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.TESTING.value,
            "error": None,
        },
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.STOPPED.value,
            "error": "Build stopped by user",
        },
    ]
    build_log_repository = Mock()
    tool_repository = Mock()
    docker_service = Mock()

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    job, error = service.stop_job("request-1")

    assert error is None
    assert job == {
        "id": "request-1",
        "prompt": "Build dashboard",
        "status": BuildStatus.STOPPED.value,
        "error": "Build stopped by user",
    }
    docker_service.stop_build_containers.assert_called_once_with(request_id="request-1")
    request_repository.update_status.assert_called_once_with(
        request_id="request-1",
        status=BuildStatus.STOPPED,
        error="Build stopped by user",
    )
    build_log_repository.add_log.assert_called_once()


def test_stop_job_does_not_stop_existing_tool_runtime_for_rebuild() -> None:
    request_repository = Mock()
    request_repository.get_by_id.side_effect = [
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.GENERATING_CODE.value,
            "error": None,
        },
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.STOPPED.value,
            "error": "Build stopped by user",
        },
    ]
    docker_service = Mock()

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),
        tool_repository=Mock(),
        docker_service=docker_service,  # type: ignore[arg-type]
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    _job, _error = service.stop_job("request-1")

    docker_service.stop_build_containers.assert_called_once_with(request_id="request-1")
    docker_service.stop_request_containers.assert_not_called()


def test_list_jobs_recovers_running_state_when_container_is_alive() -> None:
    request_repository = Mock()
    request_repository.list_recent.return_value = [
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.FAILED.value,
            "error": "Smoke test failed",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    ]
    build_log_repository = Mock()
    build_log_repository.get_latest_logs_for_requests.return_value = {}
    tool_repository = Mock()
    tool_repository.get_by_request_ids.return_value = {
        "request-1": {
            "toolId": "tool-1",
            "requestId": "request-1",
            "name": "Demo Dashboard",
            "status": ToolStatus.FAILED.value,
            "containerId": "container-1",
            "port": 3010,
            "uiPort": 3010,
            "ports": {"app": 3010},
            "dockerImage": "generated-tool:test",
            "runtimeName": "demo-dashboard",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    }
    tool_repository.get_by_id.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Demo Dashboard",
        "status": ToolStatus.RUNNING.value,
        "containerId": "container-1",
        "port": 3010,
        "uiPort": 3010,
        "ports": {"app": 3010},
        "dockerImage": "generated-tool:test",
        "runtimeName": "demo-dashboard",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    docker_service = Mock()
    docker_service.container_running.return_value = True

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    jobs = service.list_jobs()

    assert jobs[0]["status"] == BuildStatus.RUNNING.value
    assert jobs[0]["toolStatus"] == ToolStatus.RUNNING.value
    assert jobs[0]["error"] is None
    tool_repository.update_status.assert_called_once_with(
        "tool-1",
        ToolStatus.RUNNING,
        crash_alert_sent=False,
    )
    request_repository.update_status.assert_called_once_with(
        request_id="request-1",
        status=BuildStatus.RUNNING,
        error=None,
    )


def test_list_jobs_marks_running_tool_failed_when_container_stops() -> None:
    request_repository = Mock()
    request_repository.list_recent.return_value = [
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.RUNNING.value,
            "error": None,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    ]
    build_log_repository = Mock()
    build_log_repository.get_latest_logs_for_requests.return_value = {}
    tool_repository = Mock()
    tool_repository.get_by_request_ids.return_value = {
        "request-1": {
            "toolId": "tool-1",
            "requestId": "request-1",
            "name": "Demo Dashboard",
            "status": ToolStatus.RUNNING.value,
            "containerId": "container-1",
            "port": 3010,
            "uiPort": 3010,
            "ports": {"app": 3010},
            "dockerImage": "generated-tool:test",
            "runtimeName": "demo-dashboard",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    }
    tool_repository.get_by_id.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Demo Dashboard",
        "status": ToolStatus.FAILED.value,
        "containerId": None,
        "port": 3010,
        "uiPort": 3010,
        "ports": {"app": 3010},
        "dockerImage": "generated-tool:test",
        "runtimeName": "demo-dashboard",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    docker_service = Mock()
    docker_service.container_running.return_value = False

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    jobs = service.list_jobs()

    assert jobs[0]["status"] == BuildStatus.FAILED.value
    assert jobs[0]["toolStatus"] == ToolStatus.FAILED.value
    tool_repository.update_status.assert_called_once_with(
        "tool-1",
        ToolStatus.FAILED,
        crash_alert_sent=False,
        clear_runtime=True,
    )
    request_repository.update_status.assert_called_once_with(
        request_id="request-1",
        status=BuildStatus.FAILED,
        error="Tool container is not running",
    )


def test_list_jobs_recovers_when_container_id_missing_but_runtime_is_alive() -> None:
    request_repository = Mock()
    request_repository.list_recent.return_value = [
        {
            "id": "request-1",
            "prompt": "Build dashboard",
            "status": BuildStatus.FAILED.value,
            "error": "Tool container exited unexpectedly",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    ]
    build_log_repository = Mock()
    build_log_repository.get_latest_logs_for_requests.return_value = {}
    tool_repository = Mock()
    tool_repository.get_by_request_ids.return_value = {
        "request-1": {
            "toolId": "tool-1",
            "requestId": "request-1",
            "name": "Demo Dashboard",
            "runtimeName": "demo-dashboard",
            "status": ToolStatus.FAILED.value,
            "containerId": None,
            "port": 3010,
            "uiPort": 3010,
            "ports": {"app": 3010},
            "dockerImage": "generated-tool:test",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    }
    tool_repository.get_by_id.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Demo Dashboard",
        "runtimeName": "demo-dashboard",
        "status": ToolStatus.RUNNING.value,
        "containerId": "compose:tool-demo-dashboard",
        "port": 3010,
        "uiPort": 3010,
        "ports": {"app": 3010},
        "dockerImage": "generated-tool:test",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    docker_service = Mock()
    docker_service.resolve_running_container_id_for_tool.return_value = "compose:tool-demo-dashboard"
    docker_service.container_running.return_value = True

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    jobs = service.list_jobs()

    assert jobs[0]["status"] == BuildStatus.RUNNING.value
    assert jobs[0]["toolStatus"] == ToolStatus.RUNNING.value
    assert jobs[0]["error"] is None
    tool_repository.update_deployment.assert_called_once_with(
        tool_id="tool-1",
        container_id="compose:tool-demo-dashboard",
        port=3010,
        ports={"app": 3010},
        ui_port=3010,
        status=ToolStatus.RUNNING,
        monitor_ignore_until=ANY,
    )
    request_repository.update_status.assert_called_once_with(
        request_id="request-1",
        status=BuildStatus.RUNNING,
        error=None,
    )


def test_get_job_reconciles_failed_request_when_tool_runtime_is_alive() -> None:
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "request-1",
        "prompt": "Build dashboard",
        "status": BuildStatus.FAILED.value,
        "error": "Tool container exited unexpectedly",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    build_log_repository = Mock()
    build_log_repository.get_logs_for_request.return_value = []
    tool_repository = Mock()
    tool_repository.get_by_request_ids.return_value = {
        "request-1": {
            "toolId": "tool-1",
            "requestId": "request-1",
            "name": "Demo Dashboard",
            "runtimeName": "demo-dashboard",
            "status": ToolStatus.FAILED.value,
            "containerId": "compose:tool-demo-dashboard",
            "port": 3010,
            "uiPort": 3010,
            "ports": {"app": 3010},
            "dockerImage": "generated-tool:test",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
    }
    tool_repository.get_by_id.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Demo Dashboard",
        "runtimeName": "demo-dashboard",
        "status": ToolStatus.RUNNING.value,
        "containerId": "compose:tool-demo-dashboard",
        "port": 3010,
        "uiPort": 3010,
        "ports": {"app": 3010},
        "dockerImage": "generated-tool:test",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    docker_service = Mock()
    docker_service.container_running.return_value = True

    service = ToolBuilderService(
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        testing_service=Mock(),
        port_allocator_service=Mock(),
        workflow=Mock(),
    )

    job = service.get_job("request-1")

    assert job is not None
    assert job["status"] == BuildStatus.RUNNING.value
    assert job["error"] is None
    request_repository.update_status.assert_called_once_with(
        request_id="request-1",
        status=BuildStatus.RUNNING,
        error=None,
    )
