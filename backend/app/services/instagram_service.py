from __future__ import annotations

import socket
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import docker
import requests
from docker.errors import DockerException, ImageNotFound, NotFound

from app.utils.config import Settings


class InstagramService:
    _BROWSER_INTERNAL_PORT = 3000
    _INSTAGRAM_LOGIN_URL = "https://www.instagram.com/accounts/login/"
    _INSTAGRAM_REELS_URL = "https://www.instagram.com/reels/"
    _CHROME_UNIX_EPOCH_OFFSET_SECONDS = 11_644_473_600
    _DEFAULT_CONTAINER_NAME = "toolhub-instagram-browser"
    _LEGACY_CONTAINER_NAMES = ("toolhub-instagram-mini-browser",)

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._docker_client = docker.from_env()
        self._reels_ensured_container_ids: set[str] = set()

    def get_browser_session(self, viewer_base_url: str | None = None) -> dict[str, object]:
        container_name = self._resolved_container_name()
        image = self._resolved_image()
        authenticated = self._is_instagram_authenticated()

        try:
            container = self._docker_client.containers.get(container_name)
        except NotFound:
            return {
                "running": False,
                "authenticated": authenticated,
                "requiresLogin": not authenticated,
                "containerName": container_name,
                "containerId": None,
                "hostPort": None,
                "viewerUrl": None,
                "image": image,
                "message": "Visit Profile to open Instagram login and connect your session.",
            }
        except DockerException as exc:
            raise RuntimeError(f"Unable to inspect Instagram browser container: {exc}") from exc

        try:
            container.reload()
        except DockerException:
            pass

        running = container.status == "running"
        host_port = self._extract_browser_host_port(container.attrs)
        viewer_url = (
            self._build_browser_viewer_url(host_port=host_port, viewer_base_url=viewer_base_url)
            if host_port is not None
            else None
        )

        if running and host_port is not None:
            try:
                self._normalize_keyboard_state(container)
            except RuntimeError:
                pass

        if running and host_port is not None and authenticated:
            try:
                self._ensure_reels_loaded_once(container)
            except RuntimeError:
                pass

        if running and host_port is not None and authenticated:
            message = "Instagram is connected. Reels are ready in the right panel."
        elif running and host_port is not None:
            message = "Instagram browser is running. Sign in from Profile to view content."
        elif running:
            message = "Instagram browser is running, but its port mapping was not found."
        else:
            message = f"Instagram browser is {container.status}."

        return {
            "running": running and host_port is not None,
            "authenticated": authenticated,
            "requiresLogin": not authenticated,
            "containerName": container_name,
            "containerId": container.id,
            "hostPort": host_port,
            "viewerUrl": viewer_url,
            "image": image,
            "message": message,
        }

    def start_browser_session(
        self,
        viewer_base_url: str | None = None,
        force_restart: bool = False,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
    ) -> dict[str, object]:
        if self._settings.instagram_browser_port_start > self._settings.instagram_browser_port_end:
            raise ValueError("Invalid Instagram browser port range. Check INSTAGRAM_BROWSER_PORT_START and END.")

        container_name = self._resolved_container_name()
        image = self._resolved_image()
        self._remove_legacy_browser_containers(except_name=container_name)

        existing = self._get_browser_container()
        if existing is not None:
            try:
                existing.reload()
            except DockerException:
                pass

            if force_restart:
                self._remove_container(existing)
                existing = None
            else:
                if existing.status != "running":
                    try:
                        existing.start()
                        self._wait_for_running(existing, timeout_seconds=18)
                    except DockerException as exc:
                        self._remove_container(existing)
                        existing = None
                        raise RuntimeError(f"Unable to start existing Instagram browser container: {exc}") from exc
                    except RuntimeError:
                        self._remove_container(existing)
                        existing = None
                if existing is not None:
                    existing_port = self._extract_browser_host_port(existing.attrs)
                    if existing.status == "running" and existing_port is not None:
                        if self._wait_for_browser_ready(existing, existing_port, timeout_seconds=30):
                            return self.get_browser_session(viewer_base_url=viewer_base_url)
                    self._remove_container(existing)

        profile_dir = self._instagram_browser_profile_dir()
        self._ensure_browser_image(image)
        browser_width, browser_height = self._normalized_browser_dimensions(
            viewport_width=viewport_width,
            viewport_height=viewport_height,
        )
        browser_env = {
            "TZ": self._settings.instagram_browser_timezone,
            "CHROME_CLI": self._INSTAGRAM_LOGIN_URL,
            "TITLE": "Instagram",
            "START_DOCKER": "false",
            "SELKIES_MANUAL_WIDTH": str(browser_width),
            "SELKIES_MANUAL_HEIGHT": str(browser_height),
            "SELKIES_USE_CSS_SCALING": "true",
            "SELKIES_UI_SHOW_SIDEBAR": "false",
            "SELKIES_UI_SHOW_CORE_BUTTONS": "false",
            "SELKIES_UI_SIDEBAR_SHOW_KEYBOARD_BUTTON": "false",
            "SELKIES_UI_SIDEBAR_SHOW_TRACKPAD": "false",
            "SELKIES_UI_SIDEBAR_SHOW_SOFT_BUTTONS": "false",
            "NO_DECOR": "true",
        }
        browser_labels = {
            "tool.integration": "instagram-browser",
            "tool.service": "chromium-session",
        }

        last_error: RuntimeError | None = None
        for host_port in range(
            self._settings.instagram_browser_port_start,
            self._settings.instagram_browser_port_end + 1,
        ):
            if self._is_tcp_port_open("127.0.0.1", host_port):
                continue

            try:
                container = self._docker_client.containers.run(
                    image=image,
                    name=container_name,
                    detach=True,
                    auto_remove=False,
                    ports={f"{self._BROWSER_INTERNAL_PORT}/tcp": host_port},
                    mem_limit=self._settings.instagram_browser_memory_limit,
                    nano_cpus=self._to_nano_cpus(self._settings.instagram_browser_cpu_limit),
                    shm_size="1g",
                    restart_policy={"Name": "unless-stopped"},
                    volumes={str(profile_dir): {"bind": "/config", "mode": "rw"}},
                    environment=browser_env,
                    labels=browser_labels,
                )
                self._wait_for_running(container, timeout_seconds=18)
                if not self._wait_for_browser_ready(container=container, host_port=host_port, timeout_seconds=45):
                    self._remove_container(container)
                    continue
                return self.get_browser_session(viewer_base_url=viewer_base_url)
            except DockerException as exc:
                error_text = str(exc).lower()
                if "port is already allocated" in error_text or "address already in use" in error_text:
                    continue
                if "container name" in error_text and "already in use" in error_text:
                    adopted = self._adopt_existing_browser_session(viewer_base_url=viewer_base_url)
                    if adopted is not None:
                        return adopted
                last_error = RuntimeError(f"Unable to start Instagram browser container: {exc}")
                break

        if last_error:
            raise last_error
        raise RuntimeError(
            "No free port available for Instagram browser. "
            "Adjust INSTAGRAM_BROWSER_PORT_START/END and try again."
        )

    def stop_browser_session(self, viewer_base_url: str | None = None) -> dict[str, object]:
        container = self._get_browser_container()
        if container is not None:
            self._remove_container(container)

        self._remove_legacy_browser_containers(except_name=None)

        session = self.get_browser_session(viewer_base_url=viewer_base_url)
        session["message"] = "Instagram browser stopped."
        return session

    def control_reels_scroll(self, action: str) -> dict[str, object]:
        normalized_action = (action or "").strip().lower()
        key_mapping = {
            "swipe_up": "Next",
            "swipe_down": "Prior",
        }
        if normalized_action not in key_mapping:
            raise ValueError("Unsupported reels action. Use 'swipe_up' or 'swipe_down'.")

        session = self.get_browser_session()
        if not bool(session.get("running")):
            raise RuntimeError("Instagram browser is not running. Connect it from Profile first.")
        if not bool(session.get("authenticated")):
            raise RuntimeError("Instagram is not signed in yet. Sign in from Profile first.")

        container = self._get_browser_container()
        if container is None:
            raise RuntimeError("Instagram browser container is unavailable.")

        try:
            self._normalize_keyboard_state(container)
        except RuntimeError:
            pass

        key = key_mapping[normalized_action]
        command = (
            "export DISPLAY=:1; "
            "WINDOW_ID=$(xdotool search --onlyvisible --name 'Chromium' | head -n 1 || true); "
            "if [ -n \"$WINDOW_ID\" ]; then xdotool windowactivate \"$WINDOW_ID\"; fi; "
            f"xdotool key --clearmodifiers {key}"
        )
        try:
            exec_result = container.exec_run(
                cmd=["sh", "-lc", command],
                stdout=True,
                stderr=True,
            )
        except DockerException as exc:
            raise RuntimeError(f"Unable to control Instagram reels scroll: {exc}") from exc

        if int(exec_result.exit_code or 0) != 0:
            output = exec_result.output.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"Unable to control Instagram reels scroll. {output}".strip())

        return {
            "ok": True,
            "action": normalized_action,
            "message": "Reels scrolled.",
        }

    def _ensure_browser_image(self, image: str) -> None:
        normalized_image = (image or "").strip()
        if not normalized_image:
            raise RuntimeError("Instagram browser image is not configured.")

        try:
            self._docker_client.images.get(normalized_image)
            return
        except ImageNotFound:
            pass
        except DockerException as exc:
            raise RuntimeError(f"Unable to inspect Instagram browser image: {exc}") from exc

        try:
            self._docker_client.images.pull(normalized_image)
        except DockerException as exc:
            raise RuntimeError(f"Unable to pull Instagram browser image ({normalized_image}): {exc}") from exc

    def _resolved_container_name(self) -> str:
        return self._settings.instagram_browser_container_name.strip() or self._DEFAULT_CONTAINER_NAME

    def _resolved_image(self) -> str:
        return self._settings.instagram_browser_image.strip()

    def _get_browser_container(self) -> Any | None:
        container_name = self._resolved_container_name()
        try:
            return self._docker_client.containers.get(container_name)
        except NotFound:
            return None
        except DockerException as exc:
            raise RuntimeError(f"Unable to access Instagram browser container: {exc}") from exc

    def _remove_legacy_browser_containers(self, except_name: str | None) -> None:
        names = set(self._LEGACY_CONTAINER_NAMES)
        if except_name:
            names.discard(except_name)

        for name in names:
            try:
                container = self._docker_client.containers.get(name)
            except NotFound:
                continue
            except DockerException as exc:
                raise RuntimeError(f"Unable to inspect legacy Instagram browser container ({name}): {exc}") from exc

            try:
                container.remove(force=True)
            except NotFound:
                continue
            except DockerException as exc:
                raise RuntimeError(f"Unable to remove legacy Instagram browser container ({name}): {exc}") from exc

    def _remove_container(self, container: Any) -> None:
        container_id = str(getattr(container, "id", "") or "")
        try:
            container.remove(force=True)
        except NotFound:
            return
        except DockerException as exc:
            raise RuntimeError(f"Unable to remove Instagram browser container: {exc}") from exc
        finally:
            if container_id:
                self._reels_ensured_container_ids.discard(container_id)

    def _wait_for_running(self, container: Any, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                container.reload()
            except DockerException:
                time.sleep(0.25)
                continue
            if container.status == "running":
                return
            if container.status in {"exited", "dead"}:
                break
            time.sleep(0.25)
        raise RuntimeError("Instagram browser container failed to enter running state.")

    def _wait_for_browser_ready(self, container: Any, host_port: int, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                container.reload()
            except DockerException:
                time.sleep(0.35)
                continue

            if container.status in {"exited", "dead"}:
                return False

            health = self._container_health_status(container)
            if health == "healthy":
                return True

            if self._wait_for_browser_http_ready(host_port=host_port, timeout_seconds=1):
                return True

            time.sleep(0.35)
        return False

    @staticmethod
    def _container_health_status(container: Any) -> str | None:
        state = container.attrs.get("State", {})
        if not isinstance(state, dict):
            return None
        health = state.get("Health")
        if not isinstance(health, dict):
            return None
        status = health.get("Status")
        return str(status).strip().lower() if status else None

    @staticmethod
    def _wait_for_browser_http_ready(host_port: int, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        probe_hosts = ("host.docker.internal", "127.0.0.1", "localhost")
        while time.monotonic() < deadline:
            for host in probe_hosts:
                target = f"http://{host}:{host_port}/"
                try:
                    response = requests.get(target, timeout=0.8, allow_redirects=True)
                    if response.status_code < 500:
                        return True
                except requests.RequestException:
                    continue
            time.sleep(0.2)
        return False

    def _adopt_existing_browser_session(self, viewer_base_url: str | None = None) -> dict[str, object] | None:
        container = self._get_browser_container()
        if container is None:
            return None

        try:
            container.reload()
        except DockerException:
            pass

        if container.status != "running":
            try:
                container.start()
            except DockerException:
                return None
            try:
                self._wait_for_running(container, timeout_seconds=10)
            except RuntimeError:
                return None

        host_port = self._extract_browser_host_port(container.attrs)
        if host_port is not None:
            self._wait_for_browser_ready(container=container, host_port=host_port, timeout_seconds=16)
        session = self.get_browser_session(viewer_base_url=viewer_base_url)
        return session if bool(session.get("running")) else None

    def _extract_browser_host_port(self, container_attrs: dict[str, Any]) -> int | None:
        ports = container_attrs.get("NetworkSettings", {}).get("Ports", {})
        if not isinstance(ports, dict):
            return None
        key = f"{self._BROWSER_INTERNAL_PORT}/tcp"
        port_mappings = ports.get(key)
        if not isinstance(port_mappings, list) or not port_mappings:
            return None
        mapping = port_mappings[0]
        if not isinstance(mapping, dict):
            return None
        try:
            parsed_port = int(mapping.get("HostPort"))
            return parsed_port if parsed_port > 0 else None
        except (TypeError, ValueError):
            return None

    def _build_browser_viewer_url(self, host_port: int, viewer_base_url: str | None = None) -> str:
        selected_base = (viewer_base_url or "http://localhost").strip()
        parsed = urlsplit(selected_base if "://" in selected_base else f"http://{selected_base}")
        scheme = parsed.scheme or "http"
        hostname = parsed.hostname or "localhost"
        if hostname in {"0.0.0.0", "::"}:
            hostname = "localhost"
        return f"{scheme}://{hostname}:{host_port}/"

    def _ensure_reels_loaded_once(self, container: Any) -> None:
        container_id = str(getattr(container, "id", "") or "")
        if not container_id:
            return
        if container_id in self._reels_ensured_container_ids:
            return
        self._open_reels_in_browser(container)
        self._reels_ensured_container_ids.add(container_id)

    def _normalize_keyboard_state(self, container: Any) -> None:
        command = (
            "export DISPLAY=:1; "
            "if command -v setxkbmap >/dev/null 2>&1; then setxkbmap us -option caps:none; fi; "
            "if command -v xdotool >/dev/null 2>&1; then "
            "xdotool keyup Shift_L Shift_R Control_L Control_R Alt_L Alt_R Super_L Super_R "
            "ISO_Level3_Shift Caps_Lock "
            ">/dev/null 2>&1 || true; "
            "for attempt in 1 2 3; do "
            "if xset q 2>/dev/null | grep -Eqi 'Caps Lock:[[:space:]]+on'; then "
            "xdotool key Caps_Lock >/dev/null 2>&1 || true; "
            "sleep 0.05; "
            "else break; fi; "
            "done; "
            "fi"
        )
        try:
            result = container.exec_run(cmd=["sh", "-lc", command], stdout=True, stderr=True)
        except DockerException as exc:
            raise RuntimeError(f"Unable to normalize Instagram browser keyboard state: {exc}") from exc
        if int(result.exit_code or 0) != 0:
            output = result.output.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"Unable to normalize Instagram browser keyboard state. {output}".strip())

    def _open_reels_in_browser(self, container: Any) -> None:
        self._normalize_keyboard_state(container)
        command = (
            "export DISPLAY=:1; "
            "WINDOW_ID=$(xdotool search --onlyvisible --name 'Chromium' | head -n 1 || true); "
            "if [ -z \"$WINDOW_ID\" ]; then WINDOW_ID=$(xdotool getactivewindow 2>/dev/null || true); fi; "
            "if [ -z \"$WINDOW_ID\" ]; then exit 1; fi; "
            "xdotool windowactivate \"$WINDOW_ID\"; "
            "xdotool windowmove \"$WINDOW_ID\" 0 0 >/dev/null 2>&1 || true; "
            "xdotool windowsize \"$WINDOW_ID\" 100% 100% >/dev/null 2>&1 || true; "
            "xdotool key --window \"$WINDOW_ID\" --clearmodifiers ctrl+l; "
            f"xdotool type --window \"$WINDOW_ID\" --delay 0 '{self._INSTAGRAM_REELS_URL}'; "
            "xdotool key --window \"$WINDOW_ID\" Return"
        )
        try:
            result = container.exec_run(cmd=["sh", "-lc", command], stdout=True, stderr=True)
        except DockerException as exc:
            raise RuntimeError(f"Unable to open Instagram reels in browser: {exc}") from exc
        if int(result.exit_code or 0) != 0:
            output = result.output.decode("utf-8", errors="ignore").strip()
            raise RuntimeError(f"Unable to open Instagram reels in browser. {output}".strip())

    def _instagram_browser_profile_dir(self) -> Path:
        configured = (self._settings.instagram_browser_profile_host_dir or "").strip()
        if configured:
            profile_dir = Path(configured).expanduser().resolve()
        else:
            workspace = Path(self._settings.codex_workspace_host).expanduser().resolve()
            profile_dir = workspace / ".toolhub" / "instagram-browser-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir

    def _is_instagram_authenticated(self) -> bool:
        cookie_db_paths = self._instagram_cookie_db_paths()
        now_utc = self._chrome_utc_microseconds_now()

        for cookie_db_path in cookie_db_paths:
            if not cookie_db_path.exists():
                continue

            try:
                with sqlite3.connect(
                    f"file:{cookie_db_path}?mode=ro",
                    uri=True,
                    timeout=0.2,
                ) as connection:
                    connection.execute("PRAGMA query_only = ON")
                    row = connection.execute(
                        """
                        SELECT 1
                        FROM cookies
                        WHERE host_key LIKE '%instagram.com'
                          AND name = 'sessionid'
                          AND (expires_utc = 0 OR expires_utc > ?)
                        LIMIT 1
                        """,
                        (now_utc,),
                    ).fetchone()
                    if row is not None:
                        return True
            except sqlite3.Error:
                continue

        return False

    def _instagram_cookie_db_paths(self) -> list[Path]:
        profile_dir = self._instagram_browser_profile_dir()
        candidate_paths: list[Path] = [
            profile_dir / ".config" / "chromium" / "Default" / "Cookies",
            profile_dir / ".config" / "chromium" / "Default" / "Network" / "Cookies",
            profile_dir / "chromium-profile" / "Default" / "Cookies",
            profile_dir / "chromium-profile" / "Default" / "Network" / "Cookies",
        ]

        for chromium_root in self._system_chromium_profile_roots():
            for profile_path in self._iter_chromium_profile_paths(chromium_root):
                candidate_paths.extend(
                    [
                        profile_path / "Cookies",
                        profile_path / "Network" / "Cookies",
                    ]
                )

        deduped_paths: list[Path] = []
        seen: set[str] = set()
        for path in candidate_paths:
            normalized = str(path.expanduser())
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped_paths.append(path)
        return deduped_paths

    def _system_chromium_profile_roots(self) -> list[Path]:
        homes: list[Path] = []
        seen_homes: set[str] = set()

        for candidate in [Path.home(), self._workspace_owner_home()]:
            if candidate is None:
                continue
            normalized = str(candidate.expanduser())
            if normalized in seen_homes:
                continue
            seen_homes.add(normalized)
            homes.append(candidate)

        roots: list[Path] = []
        seen_roots: set[str] = set()
        for home in homes:
            for root in [
                home / ".config" / "chromium",
                home / ".config" / "google-chrome",
                home / "snap" / "chromium" / "common" / "chromium",
                home / "Library" / "Application Support" / "Chromium",
                home / "Library" / "Application Support" / "Google" / "Chrome",
            ]:
                normalized = str(root.expanduser())
                if normalized in seen_roots:
                    continue
                seen_roots.add(normalized)
                roots.append(root)
        return roots

    def _workspace_owner_home(self) -> Path | None:
        workspace = Path(self._settings.codex_workspace_host).expanduser()
        parts = workspace.parts

        for base in ("Users", "home"):
            if base not in parts:
                continue
            index = parts.index(base)
            if len(parts) <= index + 1:
                continue
            return Path(*parts[: index + 2])

        return None

    @staticmethod
    def _iter_chromium_profile_paths(browser_root: Path) -> list[Path]:
        if not browser_root.exists():
            return []

        profile_paths: list[Path] = []
        default_profile = browser_root / "Default"
        if default_profile.exists():
            profile_paths.append(default_profile)

        try:
            for child in browser_root.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if name.startswith("Profile ") or name == "Guest Profile":
                    profile_paths.append(child)
        except OSError:
            return profile_paths

        return profile_paths

    @classmethod
    def _chrome_utc_microseconds_now(cls) -> int:
        return int((time.time() + cls._CHROME_UNIX_EPOCH_OFFSET_SECONDS) * 1_000_000)

    @staticmethod
    def _is_tcp_port_open(host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.15)
            return sock.connect_ex((host, port)) == 0

    @staticmethod
    def _to_nano_cpus(cpu_limit: float) -> int | None:
        if cpu_limit <= 0:
            return None
        return int(cpu_limit * 1_000_000_000)

    @staticmethod
    def _normalized_browser_dimensions(
        viewport_width: int | None,
        viewport_height: int | None,
    ) -> tuple[int, int]:
        default_width = 1440
        default_height = 900

        if viewport_width is None or viewport_height is None:
            return default_width, default_height

        width = max(960, min(2560, int(viewport_width)))
        height = max(720, min(1600, int(viewport_height)))
        return width, height
