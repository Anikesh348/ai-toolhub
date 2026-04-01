import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import time
from typing import Any, Iterable, Optional
import re

import docker
import yaml
from docker.errors import DockerException, NotFound
from requests import exceptions as requests_exceptions

from app.utils.config import Settings


@dataclass
class CommandResult:
    success: bool
    exit_code: int
    logs: str


@dataclass
class CommandStreamEvent:
    type: str
    chunk: str | None = None
    result: CommandResult | None = None


@dataclass
class ServiceRuntimeSpec:
    name: str
    container_port: int | None
    is_ui: bool = False
    is_smoke: bool = False


@dataclass
class RuntimePlan:
    mode: str
    services: list[ServiceRuntimeSpec]
    ui_service: str | None
    smoke_service: str | None

    @property
    def published_services(self) -> list[ServiceRuntimeSpec]:
        return [service for service in self.services if service.container_port is not None]


class DockerService:
    _COMPOSE_VAR_PATTERN = re.compile(r"\$\{([^}:]+)(?::-(.*?))?}")

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = docker.from_env()

    def ensure_job_workspace(self, request_id: str) -> tuple[Path, str]:
        host_root = Path(self._settings.codex_workspace_host).resolve()
        host_root.mkdir(parents=True, exist_ok=True)
        host_job_path = host_root / request_id
        host_job_path.mkdir(parents=True, exist_ok=True)

        container_job_path = f"{self._settings.codex_workspace_container.rstrip('/')}/{request_id}"
        return host_job_path, container_job_path

    def run_builder_container(self, request_id: str, shell_command: str, timeout_seconds: int) -> CommandResult:
        return self.run_builder_container_with_options(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=timeout_seconds,
        )

    def run_builder_container_with_options(
        self,
        request_id: str,
        shell_command: str,
        timeout_seconds: int,
        extra_volumes: dict[str, dict[str, str]] | None = None,
        extra_environment: dict[str, str] | None = None,
        working_dir_override: str | None = None,
        tty: bool = False,
    ) -> CommandResult:
        _, container_job_path = self.ensure_job_workspace(request_id)
        container = None
        workspace_root = self._settings.codex_workspace_container.rstrip("/")
        environment: dict[str, str] = {
            # Keep Codex auth/config under mounted workspace so ephemeral builders can reuse login state
            # without any mount outside CODEX_WORKSPACE_HOST.
            "HOME": workspace_root,
            "XDG_CONFIG_HOME": f"{workspace_root}/.config",
        }
        if self._settings.openai_api_key:
            environment["OPENAI_API_KEY"] = self._settings.openai_api_key
        if extra_environment:
            environment.update({key: value for key, value in extra_environment.items() if value is not None})
        volumes: dict[str, dict[str, str]] = {
            self._settings.codex_workspace_host: {
                "bind": self._settings.codex_workspace_container,
                "mode": "rw",
            }
        }
        volumes.update(self._builder_allowed_path_mounts())
        if extra_volumes:
            volumes.update(extra_volumes)
        builder_name = self._builder_container_name(request_id)
        builder_labels = {
            "tool.builder": "true",
            "tool.request_id": request_id,
        }

        try:
            try:
                existing = self._client.containers.get(builder_name)
                existing.remove(force=True)
            except NotFound:
                pass
            container = self._client.containers.run(
                image=self._settings.codex_image_name,
                name=builder_name,
                command=["/bin/sh", "-lc", shell_command],
                detach=True,
                auto_remove=False,
                tty=tty,
                stdin_open=tty,
                working_dir=working_dir_override or container_job_path,
                volumes=volumes,
                mem_limit=self._settings.builder_memory_limit,
                nano_cpus=self._to_nano_cpus(self._settings.builder_cpu_limit),
                pids_limit=self._settings.builder_pids_limit,
                environment=environment if environment else None,
                labels=builder_labels,
            )
            try:
                wait_result = container.wait(timeout=timeout_seconds)
            except requests_exceptions.ReadTimeout:
                timeout_logs = self._safe_container_logs(container)
                return CommandResult(
                    success=False,
                    exit_code=124,
                    logs=f"{timeout_logs}\nCommand timed out after {timeout_seconds}s.".strip(),
                )
            exit_code = int(wait_result.get("StatusCode", 1))
            logs = self._safe_container_logs(container)
            return CommandResult(success=exit_code == 0, exit_code=exit_code, logs=logs)
        except requests_exceptions.RequestException as exc:
            return CommandResult(success=False, exit_code=1, logs=f"Docker request error: {exc}")
        except DockerException as exc:
            return CommandResult(success=False, exit_code=1, logs=self._format_docker_error(exc))
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass

    def run_builder_container_stream_with_options(
        self,
        request_id: str,
        shell_command: str,
        timeout_seconds: int,
        extra_volumes: dict[str, dict[str, str]] | None = None,
        extra_environment: dict[str, str] | None = None,
        working_dir_override: str | None = None,
        tty: bool = False,
    ) -> Iterable[CommandStreamEvent]:
        _, container_job_path = self.ensure_job_workspace(request_id)
        container = None
        workspace_root = self._settings.codex_workspace_container.rstrip("/")
        environment: dict[str, str] = {
            "HOME": workspace_root,
            "XDG_CONFIG_HOME": f"{workspace_root}/.config",
        }
        if self._settings.openai_api_key:
            environment["OPENAI_API_KEY"] = self._settings.openai_api_key
        if extra_environment:
            environment.update({key: value for key, value in extra_environment.items() if value is not None})
        volumes: dict[str, dict[str, str]] = {
            self._settings.codex_workspace_host: {
                "bind": self._settings.codex_workspace_container,
                "mode": "rw",
            }
        }
        volumes.update(self._builder_allowed_path_mounts())
        if extra_volumes:
            volumes.update(extra_volumes)
        builder_name = self._builder_container_name(request_id)
        builder_labels = {
            "tool.builder": "true",
            "tool.request_id": request_id,
        }

        try:
            try:
                existing = self._client.containers.get(builder_name)
                existing.remove(force=True)
            except NotFound:
                pass
            container = self._client.containers.run(
                image=self._settings.codex_image_name,
                name=builder_name,
                command=["/bin/sh", "-lc", shell_command],
                detach=True,
                auto_remove=False,
                tty=tty,
                stdin_open=tty,
                working_dir=working_dir_override or container_job_path,
                volumes=volumes,
                mem_limit=self._settings.builder_memory_limit,
                nano_cpus=self._to_nano_cpus(self._settings.builder_cpu_limit),
                pids_limit=self._settings.builder_pids_limit,
                environment=environment if environment else None,
                labels=builder_labels,
            )

            started_at = time.monotonic()
            emitted_chars = 0
            status = "created"

            while True:
                if time.monotonic() - started_at > timeout_seconds:
                    try:
                        container.kill()
                    except DockerException:
                        pass
                    logs = self._safe_container_logs(container)
                    if len(logs) > emitted_chars:
                        yield CommandStreamEvent(type="log", chunk=logs[emitted_chars:])
                    yield CommandStreamEvent(
                        type="done",
                        result=CommandResult(
                            success=False,
                            exit_code=124,
                            logs=f"{logs}\nCommand timed out after {timeout_seconds}s.".strip(),
                        ),
                    )
                    return

                logs = self._safe_container_logs(container)
                if len(logs) > emitted_chars:
                    yield CommandStreamEvent(type="log", chunk=logs[emitted_chars:])
                    emitted_chars = len(logs)

                try:
                    container.reload()
                    status = container.status
                except DockerException:
                    status = "unknown"

                if status not in {"created", "running", "restarting"}:
                    break
                time.sleep(0.2)

            wait_code = 1
            try:
                wait_result = container.wait(timeout=10)
                wait_code = int(wait_result.get("StatusCode", 1))
            except requests_exceptions.ReadTimeout:
                wait_code = 124
            except DockerException:
                wait_code = 1

            final_logs = self._safe_container_logs(container)
            if len(final_logs) > emitted_chars:
                yield CommandStreamEvent(type="log", chunk=final_logs[emitted_chars:])
            yield CommandStreamEvent(
                type="done",
                result=CommandResult(success=wait_code == 0, exit_code=wait_code, logs=final_logs),
            )
        except DockerException as exc:
            yield CommandStreamEvent(
                type="done",
                result=CommandResult(success=False, exit_code=1, logs=self._format_docker_error(exc)),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass

    def list_containers_summary(self, running_only: bool = True) -> list[dict[str, Any]]:
        try:
            containers = self._client.containers.list(all=not running_only)
        except DockerException:
            return []

        summaries: list[dict[str, Any]] = []
        for container in containers:
            try:
                container.reload()
                attrs = container.attrs
            except DockerException:
                continue

            state = attrs.get("State", {})
            health = None
            health_data = state.get("Health")
            if isinstance(health_data, dict):
                health = health_data.get("Status")
            ports = attrs.get("NetworkSettings", {}).get("Ports", {})
            published: list[str] = []
            if isinstance(ports, dict):
                for container_port, mappings in ports.items():
                    if not mappings:
                        continue
                    for mapping in mappings:
                        host_ip = mapping.get("HostIp")
                        host_port = mapping.get("HostPort")
                        if host_port:
                            published.append(f"{host_ip}:{host_port}->{container_port}")

            summaries.append(
                {
                    "id": container.id[:12],
                    "name": container.name,
                    "image": attrs.get("Config", {}).get("Image", container.image.tags[0] if container.image.tags else ""),
                    "status": container.status,
                    "health": health,
                    "ports": published,
                }
            )

        summaries.sort(key=lambda row: row["name"])
        return summaries

    def check_container_health_and_restart(self, container_name: str) -> dict[str, Any]:
        target = self._find_container_by_name(container_name)
        if target is None:
            return {"found": False, "message": f"Container `{container_name}` not found."}

        before_status = self._container_health_status(target)
        before_running = target.status == "running"
        action = "none"

        try:
            if not before_running:
                target.start()
                action = "start"
            elif before_status in {"unhealthy", "exited", "dead"}:
                target.restart()
                action = "restart"
            target.reload()
        except DockerException as exc:
            return {
                "found": True,
                "name": target.name,
                "before": before_status,
                "after": None,
                "action": action,
                "message": f"Failed to apply container action: {exc}",
                "error": True,
            }

        after_status = self._container_health_status(target)
        return {
            "found": True,
            "name": target.name,
            "before": before_status,
            "after": after_status,
            "action": action,
            "message": f"Container `{target.name}` status: {before_status} -> {after_status} (action: {action}).",
            "error": False,
        }

    def get_container_health(self, container_name: str) -> dict[str, Any]:
        target = self._find_container_by_name(container_name)
        if target is None:
            return {"found": False, "message": f"Container `{container_name}` not found."}

        status = self._container_health_status(target)
        return {
            "found": True,
            "name": target.name,
            "status": status,
            "message": f"Container `{target.name}` health/status: {status}.",
        }

    def _find_container_by_name(self, container_name: str) -> Any | None:
        needle = container_name.strip().lower()
        if not needle:
            return None
        try:
            containers = self._client.containers.list(all=True)
        except DockerException:
            return None

        exact_match = None
        partial_match = None
        for container in containers:
            name = (container.name or "").lower()
            if name == needle:
                exact_match = container
                break
            if needle in name and partial_match is None:
                partial_match = container
        return exact_match or partial_match

    @staticmethod
    def _safe_container_logs(container: Any) -> str:
        try:
            raw_logs = container.logs(stdout=True, stderr=True)
        except DockerException:
            return ""
        return raw_logs.decode("utf-8", errors="replace")

    @staticmethod
    def _format_docker_error(exc: DockerException) -> str:
        raw = str(exc)
        if "mounts denied" in raw.lower():
            guidance = (
                "Mount denied by Docker Desktop. "
                "Share the requested host path in Docker Desktop -> Settings -> Resources -> File Sharing, "
                "or restrict OPERATOR_ALLOWED_PATHS to already shared roots (for macOS usually /Users)."
            )
            return f"Docker builder error: {raw}\n\n{guidance}"
        return f"Docker builder error: {raw}"

    @staticmethod
    def _container_health_status(container: Any) -> str:
        try:
            container.reload()
        except DockerException:
            return "unknown"
        state = container.attrs.get("State", {})
        health = state.get("Health")
        if isinstance(health, dict) and health.get("Status"):
            return str(health["Status"])
        status = state.get("Status")
        return str(status) if status else "unknown"

    def build_image(self, request_id: str, image_tag: str) -> CommandResult:
        host_job_path, _ = self.ensure_job_workspace(request_id)
        build_logs: list[str] = []
        container_limits = self._build_container_limits()
        try:
            _, stream = self._client.images.build(
                path=str(host_job_path),
                tag=image_tag,
                rm=True,
                pull=False,
                container_limits=container_limits or None,
            )
            for chunk in stream:
                line = chunk.get("stream") or chunk.get("error") or ""
                if line:
                    build_logs.append(line.rstrip())
            return CommandResult(success=True, exit_code=0, logs="\n".join(build_logs))
        except DockerException as exc:
            build_logs.append(f"Image build error: {exc}")
            return CommandResult(success=False, exit_code=1, logs="\n".join(build_logs))

    def inspect_runtime_plan(self, request_id: str) -> RuntimePlan:
        host_job_path, _ = self.ensure_job_workspace(request_id)
        compose_path = self._find_compose_file(host_job_path)
        if compose_path is None:
            return RuntimePlan(
                mode="single",
                services=[ServiceRuntimeSpec(name="app", container_port=self._settings.tool_internal_port, is_ui=True, is_smoke=True)],
                ui_service="app",
                smoke_service="app",
            )

        try:
            raw_compose = compose_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(raw_compose)
        except (OSError, yaml.YAMLError):
            return RuntimePlan(
                mode="single",
                services=[ServiceRuntimeSpec(name="app", container_port=self._settings.tool_internal_port, is_ui=True, is_smoke=True)],
                ui_service="app",
                smoke_service="app",
            )

        services_section = parsed.get("services") if isinstance(parsed, dict) else None
        if not isinstance(services_section, dict) or not services_section:
            return RuntimePlan(
                mode="single",
                services=[ServiceRuntimeSpec(name="app", container_port=self._settings.tool_internal_port, is_ui=True, is_smoke=True)],
                ui_service="app",
                smoke_service="app",
            )

        specs: list[ServiceRuntimeSpec] = []
        for service_name, service_config in services_section.items():
            if not isinstance(service_name, str):
                continue
            container_port = None
            if isinstance(service_config, dict):
                container_port = self._extract_primary_container_port(service_config.get("ports"))
            specs.append(ServiceRuntimeSpec(name=service_name, container_port=container_port))

        published = [spec for spec in specs if spec.container_port is not None]
        if not published:
            specs = [ServiceRuntimeSpec(name="app", container_port=self._settings.tool_internal_port, is_ui=True, is_smoke=True)]
            return RuntimePlan(mode="single", services=specs, ui_service="app", smoke_service="app")

        ui_service = self._select_ui_service(published)
        smoke_service = self._select_smoke_service(published)
        for spec in specs:
            spec.is_ui = spec.name == ui_service
            spec.is_smoke = spec.name == smoke_service

        return RuntimePlan(mode="compose", services=specs, ui_service=ui_service, smoke_service=smoke_service)

    def run_tool_runtime(
        self,
        request_id: str,
        tool_id: str,
        runtime_name: str,
        image_tag: str,
        runtime_plan: RuntimePlan,
        service_host_ports: dict[str, int],
    ) -> tuple[bool, Optional[str], str]:
        if runtime_plan.mode == "compose":
            return self._run_compose_runtime(
                request_id=request_id,
                tool_id=tool_id,
                runtime_name=runtime_name,
                runtime_plan=runtime_plan,
                service_host_ports=service_host_ports,
            )

        ui_service = runtime_plan.ui_service or "app"
        host_port = service_host_ports.get(ui_service)
        if host_port is None and service_host_ports:
            host_port = next(iter(service_host_ports.values()))
        if host_port is None:
            return False, None, "No host port provided for single-container runtime"
        return self.run_tool_container(
            tool_id=tool_id,
            runtime_name=runtime_name,
            request_id=request_id,
            image_tag=image_tag,
            host_port=host_port,
        )

    def run_tool_container(
        self,
        tool_id: str,
        runtime_name: str,
        request_id: str,
        image_tag: str,
        host_port: int,
    ) -> tuple[bool, Optional[str], str]:
        runtime_token = self._safe_name_token(runtime_name, default="generated-tool")
        name = f"tool-{runtime_token}"
        labels = {
            "tool.runtime": "single",
            "tool.name": runtime_token,
            "tool.id": tool_id,
            "tool.request_id": request_id,
        }
        try:
            # Remove an existing container with the same name to avoid naming collisions.
            try:
                existing = self._client.containers.get(name)
                existing_labels = (existing.attrs.get("Config", {}) or {}).get("Labels", {}) or {}
                if existing_labels.get("tool.id") == tool_id:
                    existing.remove(force=True)
                else:
                    name = self._select_unique_container_name(base_name=name, tool_id=tool_id)
            except NotFound:
                pass
            container = self._client.containers.run(
                image=image_tag,
                name=name,
                detach=True,
                auto_remove=False,
                ports={f"{self._settings.tool_internal_port}/tcp": host_port},
                mem_limit=self._settings.tool_memory_limit,
                nano_cpus=self._to_nano_cpus(self._settings.tool_cpu_limit),
                restart_policy={"Name": "unless-stopped"},
                labels=labels,
            )
            return True, container.id, ""
        except DockerException as exc:
            return False, None, str(exc)

    def run_probe_container(
        self,
        name: str,
        image_tag: str,
        host_port: int,
        request_id: str | None = None,
    ) -> tuple[bool, Optional[str], str]:
        probe_name = f"probe-{name}"
        labels: dict[str, str] = {"tool.runtime": "probe"}
        if request_id:
            labels["tool.request_id"] = request_id
        try:
            try:
                existing = self._client.containers.get(probe_name)
                existing.remove(force=True)
            except NotFound:
                pass
            container = self._client.containers.run(
                image=image_tag,
                name=probe_name,
                detach=True,
                auto_remove=False,
                ports={f"{self._settings.tool_internal_port}/tcp": host_port},
                mem_limit=self._settings.tool_memory_limit,
                nano_cpus=self._to_nano_cpus(self._settings.tool_cpu_limit),
                labels=labels,
            )
            return True, container.id, ""
        except DockerException as exc:
            return False, None, str(exc)

    def get_container_logs(self, container_id: str, tail: int = 200) -> str:
        if container_id.startswith("compose:"):
            project_name = container_id.split(":", 1)[1]
            return self._get_compose_logs(project_name=project_name, tail=tail)

        try:
            container = self._client.containers.get(container_id)
            raw_logs = container.logs(stdout=True, stderr=True, tail=tail)
            return raw_logs.decode("utf-8", errors="replace")
        except DockerException:
            return ""

    def stop_container(self, container_id: str) -> None:
        if container_id.startswith("compose:"):
            project_name = container_id.split(":", 1)[1]
            self._stop_compose_project(project_name)
            return

        try:
            container = self._client.containers.get(container_id)
            container.remove(force=True)
        except NotFound:
            return
        except DockerException:
            return

    def container_running(self, container_id: str) -> bool:
        if container_id.startswith("compose:"):
            project_name = container_id.split(":", 1)[1]
            containers = self._list_compose_project_containers(project_name)
            if not containers:
                return False
            for container in containers:
                try:
                    container.reload()
                except DockerException:
                    return False
                if container.status != "running":
                    return False
            return True

        try:
            container = self._client.containers.get(container_id)
            container.reload()
            return container.status == "running"
        except DockerException:
            return False

    def resolve_running_container_id_for_tool(self, tool_id: str, runtime_name: str | None = None) -> str | None:
        try:
            containers = self._client.containers.list(all=True, filters={"label": f"tool.id={tool_id}"})
        except DockerException:
            containers = []

        if not containers and runtime_name:
            project_name = self._compose_project_name(runtime_name)
            containers = self._list_compose_project_containers(project_name)

        running_containers: list[Any] = []
        for container in containers:
            try:
                container.reload()
            except DockerException:
                continue
            if container.status == "running":
                running_containers.append(container)

        if not running_containers:
            return None

        for container in running_containers:
            labels = ((container.attrs.get("Config", {}) or {}).get("Labels", {}) or {})
            project_name = str(labels.get("tool.project") or "").strip()
            if project_name:
                return f"compose:{project_name}"

        return running_containers[0].id

    def _run_compose_runtime(
        self,
        request_id: str,
        tool_id: str,
        runtime_name: str,
        runtime_plan: RuntimePlan,
        service_host_ports: dict[str, int],
    ) -> tuple[bool, Optional[str], str]:
        host_job_path, _ = self.ensure_job_workspace(request_id)
        compose_path = self._find_compose_file(host_job_path)
        if compose_path is None:
            return False, None, "docker-compose file not found for compose runtime"

        try:
            parsed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return False, None, f"Unable to parse docker-compose file: {exc}"

        if not isinstance(parsed, dict) or not isinstance(parsed.get("services"), dict):
            return False, None, "docker-compose file must define a top-level services mapping"

        compose_services: dict[str, Any] = parsed["services"]
        service_names = [service.name for service in runtime_plan.services if service.name in compose_services]
        if not service_names:
            return False, None, "No runnable services found in docker-compose"

        project_name = self._compose_project_name(runtime_name)
        labels = {
            "tool.runtime": "compose",
            "tool.name": self._safe_name_token(runtime_name, default="generated-tool"),
            "tool.project": project_name,
            "tool.id": tool_id,
            "tool.request_id": request_id,
        }

        self._stop_compose_project(project_name)

        try:
            self._client.networks.create(project_name, driver="bridge", labels=labels)
        except DockerException as exc:
            return False, None, f"Unable to create runtime network: {exc}"

        created_container_ids: list[str] = []
        compose_env = self._compose_env(
            service_host_ports=service_host_ports,
            runtime_plan=runtime_plan,
        )

        try:
            ordered_services = self._order_services(service_names=service_names, compose_services=compose_services)
            for service_name in ordered_services:
                service_config = compose_services[service_name]
                if not isinstance(service_config, dict):
                    self._stop_compose_project(project_name)
                    return False, None, f"Service `{service_name}` must be defined as a mapping"

                image_name, image_error = self._resolve_compose_service_image(
                    runtime_name=runtime_name,
                    service_name=service_name,
                    service_config=service_config,
                    compose_dir=compose_path.parent,
                    compose_env=compose_env,
                )
                if image_error:
                    self._stop_compose_project(project_name)
                    return False, None, image_error

                container_name = self._compose_container_name(project_name=project_name, service_name=service_name)
                try:
                    existing = self._client.containers.get(container_name)
                    existing.remove(force=True)
                except NotFound:
                    pass

                runtime_spec = next((spec for spec in runtime_plan.services if spec.name == service_name), None)
                ports: dict[str, int] | None = None
                if runtime_spec and runtime_spec.container_port is not None:
                    host_port = service_host_ports.get(service_name)
                    if host_port is not None:
                        ports = {f"{runtime_spec.container_port}/tcp": host_port}

                environment = self._build_service_environment(service_config=service_config, compose_env=compose_env)

                container = self._client.containers.run(
                    image=image_name,
                    name=container_name,
                    detach=True,
                    auto_remove=False,
                    ports=ports,
                    environment=environment if environment else None,
                    command=service_config.get("command"),
                    entrypoint=service_config.get("entrypoint"),
                    working_dir=service_config.get("working_dir"),
                    labels=labels,
                    mem_limit=self._settings.tool_memory_limit,
                    nano_cpus=self._to_nano_cpus(self._settings.tool_cpu_limit),
                    restart_policy={"Name": "unless-stopped"},
                )
                runtime_network = self._client.networks.get(project_name)
                runtime_network.connect(container, aliases=[service_name])
                created_container_ids.append(container.id)
        except DockerException as exc:
            self._stop_compose_project(project_name)
            return False, None, f"Compose runtime failed: {exc}"

        if not created_container_ids:
            self._stop_compose_project(project_name)
            return False, None, "Compose runtime did not start any containers"

        return True, f"compose:{project_name}", ""

    def _resolve_compose_service_image(
        self,
        runtime_name: str,
        service_name: str,
        service_config: dict[str, Any],
        compose_dir: Path,
        compose_env: dict[str, str],
    ) -> tuple[str, str | None]:
        if "build" in service_config:
            build_config = service_config["build"]
            context_raw = "."
            dockerfile_raw = "Dockerfile"
            build_args: dict[str, str] = {}

            if isinstance(build_config, str):
                context_raw = build_config
            elif isinstance(build_config, dict):
                context_raw = str(build_config.get("context", "."))
                dockerfile_raw = str(build_config.get("dockerfile", "Dockerfile"))
                raw_build_args = build_config.get("args")
                if isinstance(raw_build_args, dict):
                    for key, value in raw_build_args.items():
                        if isinstance(key, str):
                            build_args[key] = self._interpolate_compose_value(str(value), compose_env)

            context_path = (compose_dir / self._interpolate_compose_value(context_raw, compose_env)).resolve()
            if not context_path.exists() or not context_path.is_dir():
                return "", f"Build context for service `{service_name}` does not exist: {context_path}"

            dockerfile_path = self._interpolate_compose_value(dockerfile_raw, compose_env)
            image_tag = self._compose_service_image_tag(runtime_name=runtime_name, service_name=service_name)
            try:
                _, stream = self._client.images.build(
                    path=str(context_path),
                    dockerfile=dockerfile_path,
                    tag=image_tag,
                    rm=True,
                    pull=False,
                    buildargs=build_args or None,
                    container_limits=self._build_container_limits() or None,
                )
                for _ in stream:
                    # Drain build output to complete the generator and surface failures.
                    pass
            except DockerException as exc:
                return "", f"Unable to build service `{service_name}` image: {exc}"
            return image_tag, None

        raw_image = service_config.get("image")
        if not isinstance(raw_image, str) or not raw_image.strip():
            return "", f"Service `{service_name}` must define either `build` or `image`"

        image_name = self._interpolate_compose_value(raw_image, compose_env)
        return image_name, None

    def _build_service_environment(self, service_config: dict[str, Any], compose_env: dict[str, str]) -> dict[str, str]:
        environment = service_config.get("environment")
        if environment is None:
            return {}

        resolved: dict[str, str] = {}
        if isinstance(environment, dict):
            for key, value in environment.items():
                if not isinstance(key, str):
                    continue
                if value is None:
                    resolved[key] = compose_env.get(key, "")
                else:
                    resolved[key] = self._interpolate_compose_value(str(value), compose_env)
            return resolved

        if isinstance(environment, list):
            for item in environment:
                if not isinstance(item, str) or not item.strip():
                    continue
                if "=" in item:
                    key, raw_value = item.split("=", 1)
                    resolved[key] = self._interpolate_compose_value(raw_value, compose_env)
                else:
                    resolved[item] = compose_env.get(item, "")
        return resolved

    @staticmethod
    def _find_compose_file(root: Path) -> Path | None:
        compose_names = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
        for compose_name in compose_names:
            compose_path = root / compose_name
            if compose_path.exists():
                return compose_path
        return None

    def _compose_env(self, service_host_ports: dict[str, int], runtime_plan: RuntimePlan) -> dict[str, str]:
        compose_env = {key: value for key, value in os.environ.items()}
        for key, value in self._settings.model_dump().items():
            compose_env.setdefault(key, str(value))
            compose_env.setdefault(key.upper(), str(value))

        if service_host_ports:
            compose_env["HOST_PORT"] = str(next(iter(service_host_ports.values())))

        for service_name, host_port in service_host_ports.items():
            upper_name = re.sub(r"[^a-zA-Z0-9]", "_", service_name).upper()
            compose_env[f"{upper_name}_HOST_PORT"] = str(host_port)
            compose_env[f"{upper_name}_PORT"] = str(host_port)
            if "FRONTEND" in upper_name:
                compose_env["FRONTEND_HOST_PORT"] = str(host_port)
                compose_env["FRONTEND_PORT"] = str(host_port)
            if "BACKEND" in upper_name:
                compose_env["BACKEND_HOST_PORT"] = str(host_port)
                compose_env["BACKEND_PORT"] = str(host_port)

        ui_service = runtime_plan.ui_service
        if ui_service and ui_service in service_host_ports:
            ui_port = str(service_host_ports[ui_service])
            compose_env["FRONTEND_HOST_PORT"] = ui_port
            compose_env["FRONTEND_PORT"] = ui_port
            compose_env["UI_HOST_PORT"] = ui_port
            compose_env["UI_PORT"] = ui_port

        smoke_service = runtime_plan.smoke_service
        if smoke_service and smoke_service in service_host_ports:
            smoke_port = str(service_host_ports[smoke_service])
            compose_env["BACKEND_HOST_PORT"] = smoke_port
            compose_env["BACKEND_PORT"] = smoke_port
            compose_env["API_HOST_PORT"] = smoke_port
            compose_env["API_PORT"] = smoke_port
        return compose_env

    def _interpolate_compose_value(self, value: str, compose_env: dict[str, str]) -> str:
        def replacement(match: re.Match[str]) -> str:
            variable_name = match.group(1)
            default_value = match.group(2)
            if variable_name in compose_env:
                return compose_env[variable_name]
            if default_value is not None:
                return default_value
            return ""

        return self._COMPOSE_VAR_PATTERN.sub(replacement, value)

    @staticmethod
    def _extract_primary_container_port(ports_section: Any) -> int | None:
        if not isinstance(ports_section, list):
            return None

        for entry in ports_section:
            target = DockerService._extract_target_port(entry)
            if target is not None:
                return target
        return None

    @staticmethod
    def _extract_target_port(entry: Any) -> int | None:
        if isinstance(entry, dict):
            raw_target = entry.get("target")
            if raw_target is None:
                return None
            return DockerService._parse_port_token(str(raw_target))

        if isinstance(entry, str):
            without_protocol = entry.split("/", 1)[0].strip()
            token = without_protocol.split(":")[-1].strip()
            return DockerService._parse_port_token(token)

        if isinstance(entry, int):
            return entry
        return None

    @staticmethod
    def _parse_port_token(token: str) -> int | None:
        candidate = token.strip()
        if not candidate:
            return None

        variable_match = re.fullmatch(r"\$\{[^}:]+(?::-(\d+))?}", candidate)
        if variable_match:
            default_port = variable_match.group(1)
            return int(default_port) if default_port else None

        if "-" in candidate:
            candidate = candidate.split("-", 1)[0].strip()

        return int(candidate) if candidate.isdigit() else None

    @staticmethod
    def _select_ui_service(services: list[ServiceRuntimeSpec]) -> str:
        frontend_terms = ("frontend", "ui", "web", "client", "react", "next")
        backend_terms = ("backend", "api", "server")

        for service in services:
            lowered = service.name.lower()
            if any(term in lowered for term in frontend_terms):
                return service.name

        for service in services:
            lowered = service.name.lower()
            if not any(term in lowered for term in backend_terms):
                return service.name

        return services[0].name

    def _select_smoke_service(self, services: list[ServiceRuntimeSpec]) -> str:
        backend_terms = ("backend", "api", "server")
        for service in services:
            lowered = service.name.lower()
            if any(term in lowered for term in backend_terms):
                return service.name

        for service in services:
            if service.container_port == self._settings.tool_internal_port:
                return service.name

        return services[0].name

    @staticmethod
    def _order_services(service_names: list[str], compose_services: dict[str, Any]) -> list[str]:
        graph: dict[str, set[str]] = {name: set() for name in service_names}
        for service_name in service_names:
            service_config = compose_services.get(service_name)
            if not isinstance(service_config, dict):
                continue
            depends_on = service_config.get("depends_on")
            dependencies: list[str] = []
            if isinstance(depends_on, list):
                dependencies = [dependency for dependency in depends_on if isinstance(dependency, str)]
            elif isinstance(depends_on, dict):
                dependencies = [dependency for dependency in depends_on if isinstance(dependency, str)]

            for dependency in dependencies:
                if dependency in graph:
                    graph[service_name].add(dependency)

        ordered: list[str] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(name: str) -> None:
            if name in permanent:
                return
            if name in temporary:
                return
            temporary.add(name)
            for dependency in graph.get(name, set()):
                visit(dependency)
            temporary.remove(name)
            permanent.add(name)
            ordered.append(name)

        for service_name in service_names:
            visit(service_name)

        return ordered

    def _compose_project_name(self, runtime_name: str) -> str:
        runtime_token = self._safe_name_token(runtime_name, default="generated-tool")
        return f"tool-{runtime_token}"

    @staticmethod
    def _compose_container_name(project_name: str, service_name: str) -> str:
        safe_service = re.sub(r"[^a-zA-Z0-9_.-]", "-", service_name)
        return f"{project_name}-{safe_service}"

    def _compose_service_image_tag(self, runtime_name: str, service_name: str) -> str:
        runtime_token = self._safe_name_token(runtime_name, default="generated-tool")
        safe_service = re.sub(r"[^a-zA-Z0-9_.-]", "-", service_name.lower())
        return f"generated-tool:{runtime_token}-{safe_service}"

    def _list_compose_project_containers(self, project_name: str) -> list[Any]:
        try:
            return self._client.containers.list(all=True, filters={"label": f"tool.project={project_name}"})
        except DockerException:
            return []

    def _stop_compose_project(self, project_name: str) -> None:
        containers = self._list_compose_project_containers(project_name)
        for container in containers:
            try:
                container.remove(force=True)
            except Exception:  # pylint: disable=broad-except
                continue

        try:
            network = self._client.networks.get(project_name)
            network.remove()
        except Exception:  # pylint: disable=broad-except
            pass

    def _get_compose_logs(self, project_name: str, tail: int) -> str:
        collected: list[str] = []
        containers = self._list_compose_project_containers(project_name)
        for container in containers:
            try:
                raw = container.logs(stdout=True, stderr=True, tail=tail)
            except DockerException:
                continue
            logs = raw.decode("utf-8", errors="replace").strip()
            if logs:
                collected.append(f"[{container.name}]\n{logs}")
        return "\n\n".join(collected)

    def stop_request_containers(
        self,
        request_id: str,
        tool_id: str | None = None,
        runtime_name: str | None = None,
    ) -> None:
        target_runtime_name = runtime_name or ""
        target_project_name = self._compose_project_name(target_runtime_name) if target_runtime_name else None
        builder_name = self._builder_container_name(request_id)
        probe_prefix = f"probe-{self._safe_name_token(request_id, default='request')[:10]}"
        runtime_prefix = f"tool-{self._safe_name_token(target_runtime_name, default='generated-tool')}" if target_runtime_name else ""

        candidates: dict[str, Any] = {}

        def collect(filters: dict[str, str]) -> None:
            try:
                containers = self._client.containers.list(all=True, filters=filters)
            except Exception:  # pylint: disable=broad-except
                return
            for container in containers:
                try:
                    candidates[container.id] = container
                except Exception:  # pylint: disable=broad-except
                    continue

        collect({"label": f"tool.request_id={request_id}"})
        if tool_id:
            collect({"label": f"tool.id={tool_id}"})
        collect({"name": builder_name})
        collect({"name": probe_prefix})
        if runtime_prefix:
            collect({"name": runtime_prefix})
        if target_project_name:
            collect({"label": f"tool.project={target_project_name}"})

        for container in candidates.values():
            try:
                container.remove(force=True)
            except Exception:  # pylint: disable=broad-except
                continue

        if target_project_name:
            try:
                self._stop_compose_project(target_project_name)
            except Exception:  # pylint: disable=broad-except
                pass

    @staticmethod
    def _safe_name_token(value: str, default: str = "generated-tool", max_length: int = 48) -> str:
        lowered = value.strip().lower()
        token = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        if not token:
            token = default
        token = re.sub(r"-{2,}", "-", token)
        return token[:max_length].strip("-") or default

    def _builder_container_name(self, request_id: str) -> str:
        request_token = self._safe_name_token(request_id, default="request", max_length=24)
        return f"builder-{request_token}"

    def _builder_allowed_path_mounts(self) -> dict[str, dict[str, str]]:
        mounts: dict[str, dict[str, str]] = {}
        workspace_host = self._normalize_host_path(self._settings.codex_workspace_host)
        allowed_paths = [self._normalize_host_path(path) for path in self._settings.allowed_paths]

        for allowed_path in allowed_paths:
            if allowed_path == "/":
                for supplemental_path in self._root_supplemental_mounts():
                    self._add_builder_mount(
                        mounts=mounts,
                        host_path=supplemental_path,
                        workspace_host=workspace_host,
                        require_exists=True,
                    )
                continue
            self._add_builder_mount(
                mounts=mounts,
                host_path=allowed_path,
                workspace_host=workspace_host,
                require_exists=False,
            )
        return mounts

    def _add_builder_mount(
        self,
        mounts: dict[str, dict[str, str]],
        host_path: str,
        workspace_host: str,
        require_exists: bool,
    ) -> None:
        normalized = self._normalize_host_path(host_path)
        if normalized == workspace_host:
            return
        if normalized in mounts:
            return
        if require_exists and not Path(normalized).exists():
            return
        mounts[normalized] = {
            "bind": normalized,
            "mode": "rw",
        }

    def _root_supplemental_mounts(self) -> list[str]:
        derived_roots: set[str] = set()
        candidates_for_roots = [
            self._settings.codex_workspace_host,
            *self._settings.project_paths,
        ]
        for candidate in candidates_for_roots:
            derived = self._top_level_root(candidate)
            if derived:
                derived_roots.add(derived)

        if self._is_macos_host():
            candidates = set(derived_roots)
            if not candidates:
                candidates.add("/Users")
        else:
            candidates = {"/Users", "/home", "/Volumes", "/private", "/tmp", "/var", "/etc"}
            candidates.update(derived_roots)

        return sorted(candidate for candidate in candidates if Path(candidate).exists())

    def _is_macos_host(self) -> bool:
        probe_paths = [
            self._settings.codex_workspace_host,
            *self._settings.allowed_paths,
            *self._settings.project_paths,
        ]
        normalized_paths = [self._normalize_host_path(path) for path in probe_paths if path and path.strip()]
        for normalized in normalized_paths:
            if normalized == "/Users" or normalized.startswith("/Users/"):
                return True
            if normalized == "/Volumes" or normalized.startswith("/Volumes/"):
                return True
            if normalized == "/private" or normalized.startswith("/private/"):
                return True
        return False

    @staticmethod
    def _top_level_root(path: str) -> str | None:
        normalized = DockerService._normalize_host_path(path)
        parts = PurePosixPath(normalized).parts
        if len(parts) < 2:
            return None
        return f"/{parts[1]}"

    @staticmethod
    def _normalize_host_path(path: str) -> str:
        raw = path.strip()
        if not raw:
            return "/"
        pure = PurePosixPath(raw)
        if not pure.is_absolute():
            pure = PurePosixPath("/") / pure
        return str(pure)

    def _select_unique_container_name(self, base_name: str, tool_id: str) -> str:
        try:
            existing = {container.name for container in self._client.containers.list(all=True)}
        except DockerException:
            existing = set()

        if base_name not in existing:
            return base_name

        for index in range(2, 50):
            candidate = f"{base_name}-{index}"
            if candidate in existing:
                continue
            return candidate

        return f"{base_name}-{tool_id[:6]}"

    @staticmethod
    def _to_nano_cpus(cpu_cores: float) -> int:
        return int(cpu_cores * 1_000_000_000)

    def _build_container_limits(self) -> dict[str, Any]:
        limits: dict[str, Any] = {}
        memory_limit = self._parse_memory_limit_bytes(self._settings.builder_memory_limit)
        if memory_limit is not None:
            limits["memory"] = memory_limit

        cpu_limit = max(float(self._settings.builder_cpu_limit), 0.0)
        if cpu_limit <= 0:
            return limits

        host_cpus = max(1, os.cpu_count() or 1)
        allowed_cpus = min(host_cpus, max(1, int(cpu_limit)))
        limits["cpusetcpus"] = "0" if allowed_cpus == 1 else f"0-{allowed_cpus - 1}"
        limits["cpushares"] = max(2, int(cpu_limit * 1024))
        return limits

    @staticmethod
    def _parse_memory_limit_bytes(value: str | int | float | None) -> int | None:
        if value is None:
            return None

        if isinstance(value, (int, float)):
            numeric = int(value)
            return numeric if numeric > 0 else None

        candidate = str(value).strip().lower()
        if not candidate:
            return None

        match = re.fullmatch(r"(\d+)([kmgt]?)(i?b)?", candidate)
        if not match:
            return None

        amount = int(match.group(1))
        unit = match.group(2)
        is_binary = (match.group(3) or "").startswith("i")
        multipliers = {
            "": 1,
            "k": 1024 if is_binary else 1000,
            "m": (1024 ** 2) if is_binary else (1000 ** 2),
            "g": (1024 ** 3) if is_binary else (1000 ** 3),
            "t": (1024 ** 4) if is_binary else (1000 ** 4),
        }
        return amount * multipliers[unit]
