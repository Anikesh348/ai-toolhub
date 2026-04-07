import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docker.errors import NotFound

from app.services.docker_service import DockerService


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
