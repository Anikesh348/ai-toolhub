import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docker.errors import NotFound

from app.services.docker_service import DockerService, RuntimePlan, ServiceRuntimeSpec


def _docker_settings() -> SimpleNamespace:
    return SimpleNamespace(
        mongo_uri="mongodb://toolhub:toolhub-dev-password@mongo:27017/ai-toolhub?authSource=admin",
        mongo_db_name="ai-toolhub",
        tool_internal_port=3000,
        tool_memory_limit="256m",
        tool_cpu_limit=0.5,
        codex_workspace_host="/tmp/codex-workspace",
        allowed_paths=[],
        project_paths=[],
    )


def test_tool_runtime_environment_rewrites_local_compose_mongo_for_tool_containers() -> None:
    settings = _docker_settings()

    with patch.dict(os.environ, {"MONGO_PORT": "27018"}, clear=False):
        with patch("app.services.docker_service.docker.from_env", return_value=Mock()):
            service = DockerService(settings=settings)  # type: ignore[arg-type]

        environment = service._tool_runtime_environment()  # pylint: disable=protected-access

    assert environment == {
        "MONGO_URI": "mongodb://toolhub:toolhub-dev-password@tool-builder-mongo:27017/ai-toolhub?authSource=admin",
        "MONGO_DB_NAME": "ai-toolhub",
        "MONGO_PORT": "27018",
        "TZ": "Asia/Kolkata",
    }


def test_build_service_environment_includes_shared_mongo_defaults() -> None:
    settings = _docker_settings()

    with patch.dict(os.environ, {}, clear=True):
        with patch("app.services.docker_service.docker.from_env", return_value=Mock()):
            service = DockerService(settings=settings)  # type: ignore[arg-type]

        environment = service._build_service_environment(  # pylint: disable=protected-access
            service_config={},
            compose_env={},
        )

    assert environment["MONGO_URI"] == (
        "mongodb://toolhub:toolhub-dev-password@tool-builder-mongo:27017/ai-toolhub?authSource=admin"
    )
    assert environment["MONGO_DB_NAME"] == "ai-toolhub"
    assert environment["TZ"] == "Asia/Kolkata"


def test_run_tool_container_passes_shared_mongo_environment_to_runtime() -> None:
    settings = _docker_settings()
    docker_client = Mock()
    mongo_container = Mock()
    mongo_container.attrs = {"NetworkSettings": {"Networks": {"mongo-net": {}}}}

    def get_container(name: str):  # type: ignore[no-untyped-def]
        if name == "tool-builder-mongo":
            return mongo_container
        raise NotFound("missing")

    docker_client.containers.get.side_effect = get_container
    runtime_container = Mock(id="container-1")
    docker_client.containers.run.return_value = runtime_container

    with patch.dict(os.environ, {}, clear=True):
        with patch("app.services.docker_service.docker.from_env", return_value=docker_client):
            service = DockerService(settings=settings)  # type: ignore[arg-type]

        with patch.object(service, "_connect_container_to_shared_mongo_networks") as connect_mongo:
            ok, container_id, error = service.run_tool_container(
                tool_id="tool-1",
                runtime_name="price tracker",
                request_id="req-1",
                image_tag="generated-tool:req-1",
                host_port=3101,
            )

    assert ok is True
    assert container_id == "container-1"
    assert error == ""
    run_kwargs = docker_client.containers.run.call_args.kwargs
    assert run_kwargs["environment"]["MONGO_URI"] == (
        "mongodb://toolhub:toolhub-dev-password@tool-builder-mongo:27017/ai-toolhub?authSource=admin"
    )
    assert run_kwargs["environment"]["MONGO_DB_NAME"] == "ai-toolhub"
    assert run_kwargs["environment"]["TZ"] == "Asia/Kolkata"
    assert run_kwargs["extra_hosts"] == {"host.docker.internal": "host-gateway"}
    assert run_kwargs["network"] == "mongo-net"
    connect_mongo.assert_called_once_with(runtime_container, skip_networks={"mongo-net"})


def test_tool_runtime_environment_keeps_host_gateway_for_localhost_mongo_uri() -> None:
    settings = SimpleNamespace(
        **{
            **_docker_settings().__dict__,
            "mongo_uri": "mongodb://toolhub:toolhub-dev-password@localhost:27018/ai-toolhub?authSource=admin",
        }
    )

    with patch.dict(os.environ, {"MONGO_PORT": "27018"}, clear=False):
        with patch("app.services.docker_service.docker.from_env", return_value=Mock()):
            service = DockerService(settings=settings)  # type: ignore[arg-type]

        environment = service._tool_runtime_environment()  # pylint: disable=protected-access

    assert environment["MONGO_URI"] == (
        "mongodb://toolhub:toolhub-dev-password@host.docker.internal:27018/ai-toolhub?authSource=admin"
    )


def test_inspect_runtime_plan_uses_dockerfile_exposed_port_for_single_runtime(tmp_path: Path) -> None:
    settings = _docker_settings()
    settings.codex_workspace_host = str(tmp_path)
    settings.codex_workspace_container = "/workspace"
    request_path = tmp_path / "req-1"
    request_path.mkdir(parents=True, exist_ok=True)
    (request_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\nEXPOSE 8000\nCMD [\"python\", \"-m\", \"http.server\", \"8000\"]\n",
        encoding="utf-8",
    )

    with patch("app.services.docker_service.docker.from_env", return_value=Mock()):
        service = DockerService(settings=settings)  # type: ignore[arg-type]

    plan = service.inspect_runtime_plan("req-1")

    assert plan.mode == "single"
    assert plan.ui_service == "app"
    assert plan.smoke_service == "app"
    assert plan.services[0].container_port == 8000


def test_run_tool_runtime_passes_single_runtime_container_port() -> None:
    settings = _docker_settings()

    with patch("app.services.docker_service.docker.from_env", return_value=Mock()):
        service = DockerService(settings=settings)  # type: ignore[arg-type]

    runtime_plan = RuntimePlan(
        mode="single",
        services=[ServiceRuntimeSpec(name="app", container_port=8000, is_ui=True, is_smoke=True)],
        ui_service="app",
        smoke_service="app",
    )
    with patch.object(service, "run_tool_container", return_value=(True, "container-1", "")) as run_tool_container:
        ok, container_id, error = service.run_tool_runtime(
            request_id="req-1",
            tool_id="tool-1",
            runtime_name="movie alerts",
            image_tag="generated-tool:req-1",
            runtime_plan=runtime_plan,
            service_host_ports={"app": 3101},
        )

    assert ok is True
    assert container_id == "container-1"
    assert error == ""
    run_tool_container.assert_called_once_with(
        tool_id="tool-1",
        runtime_name="movie alerts",
        request_id="req-1",
        image_tag="generated-tool:req-1",
        host_port=3101,
        container_port=8000,
    )


def test_run_probe_container_uses_request_runtime_container_port() -> None:
    settings = _docker_settings()
    docker_client = Mock()
    docker_client.containers.get.side_effect = NotFound("missing")
    probe_container = Mock(id="probe-1")
    docker_client.containers.run.return_value = probe_container

    with patch("app.services.docker_service.docker.from_env", return_value=docker_client):
        service = DockerService(settings=settings)  # type: ignore[arg-type]

    with patch.object(service, "_single_runtime_container_port", return_value=8000):
        with patch.object(service, "_connect_container_to_shared_mongo_networks"):
            ok, container_id, error = service.run_probe_container(
                name="req-1-a1",
                image_tag="generated-tool:req-1",
                host_port=3123,
                request_id="req-1",
            )

    assert ok is True
    assert container_id == "probe-1"
    assert error == ""
    run_kwargs = docker_client.containers.run.call_args.kwargs
    assert run_kwargs["ports"] == {"8000/tcp": 3123}
