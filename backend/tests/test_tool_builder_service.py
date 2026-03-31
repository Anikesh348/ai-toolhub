from types import SimpleNamespace
from unittest.mock import Mock, patch

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
