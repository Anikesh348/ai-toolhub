import base64
from datetime import datetime, timezone
import json
import mimetypes
import posixpath
from pathlib import Path
from pathlib import PurePosixPath
import re
import requests
import shlex
import time
from typing import Any, Iterable
from uuid import uuid4

from app.services.docker_service import CommandResult, CommandStreamEvent, DockerService
from app.utils.config import Settings


class CodexService:
    _ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    _LOGGED_IN_RE = re.compile(r"\blogged in\b", flags=re.IGNORECASE)
    _LOGGED_OUT_RE = re.compile(r"\b(?:not logged in|logged out)\b", flags=re.IGNORECASE)
    _PROVIDER_RE = re.compile(r"\b(?:using|with)\s+(.+)$", flags=re.IGNORECASE)
    _SSH_TARGET_RE = re.compile(r"^[a-zA-Z0-9._-]+$")
    _CHAT_ATTACHMENT_ID_RE = re.compile(r"^[a-f0-9]{32}-[A-Za-z0-9._-]{1,180}$")
    _TRANSIENT_TRANSPORT_MARKERS = (
        "failed to connect to websocket",
        "responses_websocket",
        "broken pipe (os error 32)",
        "io error: broken pipe",
        "error: reconnecting",
        "connection reset by peer",
        "connection closed before message completed",
        "error sending request for url",
    )
    _TRANSIENT_DOCKER_MARKERS = (
        "docker request error",
        "read timed out",
        "unixhttpconnectionpool",
        "connection aborted",
        "connection reset by peer",
        "temporarily unavailable",
        "httpconnectionpool",
        "protocol error",
    )
    _USAGE_ENDPOINTS = (
        "https://chatgpt.com/backend-api/wham/usage",
        "https://chat.openai.com/backend-api/wham/usage",
    )
    _LOCAL_IMAGE_EXTENSIONS = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }
    _MAX_GENERATED_IMAGE_BYTES = 20 * 1024 * 1024

    def __init__(self, settings: Settings, docker_service: DockerService) -> None:
        self._settings = settings
        self._docker_service = docker_service

    def run_generation(
        self,
        request_id: str,
        prompt: str,
        model: str | None = None,
        image_paths: list[str] | None = None,
    ) -> CommandResult:
        host_job_path, container_job_path = self._docker_service.ensure_job_workspace(request_id)
        prompt_file_host = host_job_path / "prompt.txt"
        prompt_file_host.write_text(prompt, encoding="utf-8")
        prompt_file_container = f"{container_job_path}/prompt.txt"

        shell_command = self._settings.codex_command_template.format(
            job_dir=container_job_path,
            prompt_file=prompt_file_container,
            workspace=self._settings.codex_workspace_container,
            request_id=request_id,
        )
        shell_command = self._apply_chat_model(shell_command=shell_command, model=model)
        shell_command = self._apply_chat_images(shell_command=shell_command, image_paths=image_paths)
        return self._run_builder_container_with_retries(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=self._settings.build_timeout_seconds,
        )

    def readme_exists(self, request_id: str) -> bool:
        host_job_path = Path(self._settings.codex_workspace_host) / request_id
        return (host_job_path / "README.md").exists()

    def run_chat(
        self,
        session_id: str,
        prompt: str,
        model: str | None = None,
        image_paths: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        request_id = f"chat-{session_id}"
        host_job_path, container_job_path = self._docker_service.ensure_job_workspace(request_id)
        prompt_file_host = host_job_path / "chat_prompt.txt"
        prompt_file_host.write_text(prompt, encoding="utf-8")
        prompt_file_container = f"{container_job_path}/chat_prompt.txt"

        shell_command = self._settings.codex_command_template.format(
            job_dir=container_job_path,
            prompt_file=prompt_file_container,
            workspace=self._settings.codex_workspace_container,
            request_id=request_id,
        )
        shell_command = self._apply_chat_model(shell_command=shell_command, model=model)
        shell_command = self._apply_chat_images(shell_command=shell_command, image_paths=image_paths)
        return self._run_builder_container_with_retries(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=timeout_seconds or self._settings.build_timeout_seconds,
        )

    def stream_chat(
        self,
        session_id: str,
        prompt: str,
        model: str | None = None,
        image_paths: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> Iterable[CommandStreamEvent]:
        request_id = f"chat-{session_id}"
        host_job_path, container_job_path = self._docker_service.ensure_job_workspace(request_id)
        prompt_file_host = host_job_path / "chat_prompt.txt"
        prompt_file_host.write_text(prompt, encoding="utf-8")
        prompt_file_container = f"{container_job_path}/chat_prompt.txt"

        shell_command = self._settings.codex_command_template.format(
            job_dir=container_job_path,
            prompt_file=prompt_file_container,
            workspace=self._settings.codex_workspace_container,
            request_id=request_id,
        )
        shell_command = self._apply_chat_model(shell_command=shell_command, model=model)
        shell_command = self._apply_chat_images(shell_command=shell_command, image_paths=image_paths)
        return self._docker_service.run_builder_container_stream_with_options(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=timeout_seconds or self._settings.build_timeout_seconds,
        )

    def stop_chat(self, session_id: str) -> None:
        request_id = f"chat-{session_id}"
        self._docker_service.stop_request_containers(request_id=request_id)

    def list_chat_models(self) -> list[str]:
        return self._settings.available_chat_models

    def default_chat_model(self) -> str | None:
        return self._settings.default_chat_model

    def is_supported_chat_model(self, model: str) -> bool:
        return model in self.list_chat_models()

    def save_chat_attachment(
        self,
        session_id: str,
        file_name: str,
        content_type: str | None,
        data: bytes,
    ) -> dict[str, object]:
        request_id = f"chat-{session_id}"
        host_job_path, container_job_path = self._docker_service.ensure_job_workspace(request_id)
        attachment_dir = host_job_path / "attachments"
        attachment_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._sanitize_attachment_filename(file_name)
        attachment_id = f"{uuid4().hex}-{safe_name}"
        attachment_host_path = attachment_dir / attachment_id
        attachment_host_path.write_bytes(data)

        metadata_path = attachment_dir / f"{attachment_id}.json"
        metadata = {
            "fileName": file_name,
            "contentType": content_type or "",
            "size": len(data),
        }
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        resolved = self.get_chat_attachment(session_id=session_id, attachment_id=attachment_id)
        if not resolved:
            raise RuntimeError("Attachment write succeeded but attachment metadata could not be resolved.")
        return resolved

    def get_chat_attachment(self, session_id: str, attachment_id: str) -> dict[str, object] | None:
        if not self._CHAT_ATTACHMENT_ID_RE.fullmatch((attachment_id or "").strip()):
            return None

        request_id = f"chat-{session_id}"
        host_job_path, container_job_path = self._docker_service.ensure_job_workspace(request_id)
        attachment_dir = (host_job_path / "attachments").resolve()
        attachment_host_path = (attachment_dir / attachment_id).resolve()
        if attachment_host_path.parent != attachment_dir:
            return None
        if not attachment_host_path.exists() or not attachment_host_path.is_file():
            return None

        metadata_path = attachment_dir / f"{attachment_id}.json"
        metadata: dict[str, object] = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {}

        guessed_type = mimetypes.guess_type(str(attachment_host_path))[0]
        content_type = str(metadata.get("contentType") or guessed_type or "application/octet-stream")
        file_name = str(metadata.get("fileName") or attachment_id.split("-", 1)[-1] or attachment_id)
        size = int(metadata.get("size") or attachment_host_path.stat().st_size)
        container_path = f"{container_job_path}/attachments/{attachment_id}"

        return {
            "id": attachment_id,
            "fileName": file_name,
            "contentType": content_type,
            "size": size,
            "containerPath": container_path,
            "hostPath": str(attachment_host_path),
        }

    def generate_chat_image(self, session_id: str, prompt: str) -> tuple[dict[str, object] | None, str | None]:
        api_key = str(getattr(self._settings, "openai_api_key", "") or "").strip()
        if not api_key:
            return None, "Image generation is unavailable because OPENAI_API_KEY is not configured."

        cleaned_prompt = str(prompt or "").strip()
        if not cleaned_prompt:
            return None, "Image prompt is empty."

        model = str(getattr(self._settings, "openai_image_model", "") or "").strip() or "gpt-image-1"
        size = str(getattr(self._settings, "openai_image_size", "") or "").strip() or "1024x1024"
        quality = str(getattr(self._settings, "openai_image_quality", "") or "").strip() or "high"
        timeout_seconds = max(10, int(getattr(self._settings, "openai_image_timeout_seconds", 60) or 60))
        base_url = str(getattr(self._settings, "openai_image_api_base", "") or "").strip() or "https://api.openai.com/v1"
        endpoint = f"{base_url.rstrip('/')}/images/generations"

        payload: dict[str, Any] = {
            "model": model,
            "prompt": cleaned_prompt,
            "size": size,
            "quality": quality,
            "response_format": "b64_json",
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
        except requests.RequestException as exc:
            return None, f"Image generation request failed: {exc}"

        if response.status_code >= 400:
            response_text = (response.text or "").strip()
            if len(response_text) > 280:
                response_text = f"{response_text[:280].rstrip()}..."
            detail = response_text or "No error details were returned."
            return None, f"Image generation failed (HTTP {response.status_code}): {detail}"

        try:
            body = response.json()
        except ValueError:
            return None, "Image generation response was not valid JSON."

        image_bytes, content_type, file_name = self._extract_generated_image_bytes(body)
        if not image_bytes:
            return None, "Image generation did not return image bytes."

        if len(image_bytes) > self._MAX_GENERATED_IMAGE_BYTES:
            return None, "Generated image is too large to store."

        try:
            saved = self.save_chat_attachment(
                session_id=session_id,
                file_name=file_name,
                content_type=content_type,
                data=image_bytes,
            )
        except RuntimeError as exc:
            return None, str(exc)
        return saved, None

    def _extract_generated_image_bytes(self, payload: Any) -> tuple[bytes | None, str, str]:
        if not isinstance(payload, dict):
            return None, "image/png", "generated-image.png"

        items = payload.get("data")
        if not isinstance(items, list):
            return None, "image/png", "generated-image.png"

        for item in items:
            if not isinstance(item, dict):
                continue
            b64_value = item.get("b64_json")
            if isinstance(b64_value, str) and b64_value.strip():
                try:
                    decoded = base64.b64decode(b64_value)
                except ValueError:
                    continue
                if decoded:
                    return decoded, "image/png", "generated-image.png"
            url_value = item.get("url")
            if isinstance(url_value, str) and url_value.strip():
                downloaded = self._download_generated_image(url_value.strip())
                if downloaded is not None:
                    return downloaded

        return None, "image/png", "generated-image.png"

    def _download_generated_image(self, url: str) -> tuple[bytes, str, str] | None:
        try:
            response = requests.get(url, timeout=20)
        except requests.RequestException:
            return None
        if response.status_code >= 400:
            return None

        content = response.content or b""
        if not content:
            return None

        content_type = str(response.headers.get("Content-Type") or "image/png").split(";", 1)[0].strip() or "image/png"
        extension = ".png"
        guessed_extension = mimetypes.guess_extension(content_type) or ""
        if guessed_extension:
            extension = guessed_extension
        file_name = f"generated-image{extension}"
        return content, content_type, file_name

    def materialize_chat_generated_image(self, session_id: str, image_reference: str) -> dict[str, object] | None:
        host_path = self._resolve_chat_generated_image_host_path(session_id=session_id, image_reference=image_reference)
        if host_path is None:
            return None
        if not host_path.exists() or not host_path.is_file():
            return None

        try:
            size = host_path.stat().st_size
        except OSError:
            return None
        if size <= 0 or size > self._MAX_GENERATED_IMAGE_BYTES:
            return None

        guessed_type = (mimetypes.guess_type(str(host_path))[0] or "").lower()
        content_type = guessed_type if guessed_type.startswith("image/") else self._LOCAL_IMAGE_EXTENSIONS.get(host_path.suffix.lower(), "")
        if not content_type.startswith("image/"):
            return None

        try:
            data = host_path.read_bytes()
        except OSError:
            return None
        if not data:
            return None

        try:
            return self.save_chat_attachment(
                session_id=session_id,
                file_name=host_path.name,
                content_type=content_type,
                data=data,
            )
        except RuntimeError:
            return None

    def _resolve_chat_generated_image_host_path(self, session_id: str, image_reference: str) -> Path | None:
        cleaned = str(image_reference or "").strip().strip("<>").strip("'\"")
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if lowered.startswith(("http://", "https://", "data:", "blob:")):
            return None
        if lowered.startswith("/chat/sessions/"):
            return None

        request_id = f"chat-{session_id}"
        host_job_path, container_job_path = self._docker_service.ensure_job_workspace(request_id)
        workspace_host_root = Path(self._settings.codex_workspace_host).resolve()
        workspace_container_root = PurePosixPath(self._settings.codex_workspace_container.rstrip("/") or "/")
        container_job_root = PurePosixPath(container_job_path)

        # Keep only the path segment if references include query/fragment suffixes.
        ref_path = cleaned.split("?", 1)[0].split("#", 1)[0].strip()
        if not ref_path:
            return None

        def _within_root(path: Path, root: Path) -> bool:
            try:
                path.resolve().relative_to(root.resolve())
                return True
            except ValueError:
                return False

        if ref_path.startswith("/"):
            normalized_container_path = PurePosixPath(posixpath.normpath(ref_path))
            try:
                relative = normalized_container_path.relative_to(workspace_container_root)
            except ValueError:
                host_absolute = Path(ref_path).resolve()
                return host_absolute if _within_root(host_absolute, workspace_host_root) else None
            mapped = (workspace_host_root / Path(*relative.parts)).resolve()
            return mapped if _within_root(mapped, workspace_host_root) else None

        normalized_relative = posixpath.normpath(ref_path).lstrip("/")
        if not normalized_relative or normalized_relative.startswith(".."):
            return None

        if "/" in normalized_relative:
            normalized_container_path = PurePosixPath("/") / container_job_root / PurePosixPath(normalized_relative)
            try:
                relative = normalized_container_path.relative_to(workspace_container_root)
            except ValueError:
                return None
            mapped = (workspace_host_root / Path(*relative.parts)).resolve()
            return mapped if _within_root(mapped, workspace_host_root) else None

        mapped = (host_job_path / normalized_relative).resolve()
        return mapped if _within_root(mapped, workspace_host_root) else None

    def run_operator(
        self,
        session_id: str,
        prompt: str,
        container_cwd: str,
        extra_volumes: dict[str, dict[str, str]],
        timeout_seconds: int | None = None,
        model: str | None = None,
    ) -> CommandResult:
        request_id = f"chat-{session_id}"
        host_job_path, _ = self._docker_service.ensure_job_workspace(request_id)
        prompt_file_host = host_job_path / "operator_prompt.txt"
        prompt_file_host.write_text(prompt, encoding="utf-8")
        prompt_file_container = f"{self._settings.codex_workspace_container.rstrip('/')}/{request_id}/operator_prompt.txt"

        shell_command = (
            f"cd {container_cwd} && "
            "codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox "
            f"\"$(cat {prompt_file_container})\""
        )
        shell_command = self._apply_chat_model(shell_command=shell_command, model=model)
        return self._run_builder_container_with_retries(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=timeout_seconds or self._settings.operator_timeout_seconds,
            extra_volumes=extra_volumes,
            extra_environment=self._operator_runtime_env(),
        )

    def get_login_status(self) -> dict[str, object]:
        result = self._docker_service.run_builder_container(
            request_id="codex-auth-status",
            shell_command="codex login status",
            timeout_seconds=30,
        )
        message = self.clean_cli_output(result.logs)
        logged_in, provider = self._parse_login_state(message, command_success=result.success)
        return {
            "loggedIn": logged_in,
            "provider": provider,
            "message": message or "Unable to determine Codex login status.",
            "exitCode": result.exit_code,
        }

    def stream_login_device_auth(self) -> Iterable[CommandStreamEvent]:
        # Avoid pseudo-TTY/script wrapping here: Docker SDK log polling can stall on
        # TTY-attached sessions and prevent SSE chunks from reaching the UI.
        shell_command = "codex login --device-auth"
        return self._docker_service.run_builder_container_stream_with_options(
            request_id="codex-auth-login",
            shell_command=shell_command,
            timeout_seconds=20 * 60,
        )

    def logout(self) -> dict[str, object]:
        result = self._docker_service.run_builder_container(
            request_id="codex-auth-logout",
            shell_command="codex logout || codex login logout",
            timeout_seconds=30,
        )
        logout_message = self.clean_cli_output(result.logs)
        status_after_logout = self.get_login_status()
        logged_in = bool(status_after_logout["loggedIn"])
        provider = status_after_logout["provider"]

        if logout_message:
            message = logout_message
        elif not logged_in:
            message = "Codex session signed out."
        else:
            message = "Codex logout command finished, but session still appears active."
        return {
            "loggedIn": logged_in,
            "provider": provider,
            "message": message,
            "exitCode": result.exit_code,
        }

    def get_usage_status(self) -> dict[str, object]:
        payload, load_error = self._load_codex_auth_payload()
        if payload is None:
            return self._build_usage_unavailable(
                message=load_error or "Codex auth state is unavailable.",
                auth_mode=None,
            )

        auth_mode = str(payload.get("auth_mode") or "").strip().lower() or None
        if auth_mode != "chatgpt":
            return self._build_usage_unavailable(
                message=(
                    "Codex usage metrics are available only for ChatGPT-authenticated sessions. "
                    "Sign in with ChatGPT to load usage."
                ),
                auth_mode=auth_mode,
            )

        tokens = payload.get("tokens")
        token_payload = tokens if isinstance(tokens, dict) else {}
        access_token = str(token_payload.get("access_token") or "").strip()
        account_id = str(token_payload.get("account_id") or "").strip() or None
        if not access_token:
            return self._build_usage_unavailable(
                message="ChatGPT access token is missing from Codex auth state. Please re-authenticate.",
                auth_mode=auth_mode,
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "codex-cli",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id

        last_error: str | None = None
        for endpoint in self._USAGE_ENDPOINTS:
            try:
                response = requests.get(endpoint, headers=headers, timeout=12)
            except requests.RequestException as exc:
                last_error = f"Unable to reach Codex usage endpoint: {exc}"
                continue

            if response.status_code >= 400:
                last_error = (
                    f"Codex usage endpoint returned HTTP {response.status_code}. "
                    "Please confirm Codex ChatGPT login and try again."
                )
                continue

            try:
                usage_payload = response.json()
            except ValueError:
                last_error = "Codex usage endpoint returned invalid JSON."
                continue

            if not isinstance(usage_payload, dict):
                last_error = "Codex usage endpoint returned an unexpected payload."
                continue

            return self._map_usage_payload(
                payload=usage_payload,
                auth_mode=auth_mode,
                endpoint=endpoint,
            )

        return self._build_usage_unavailable(
            message=last_error or "Unable to fetch Codex usage right now.",
            auth_mode=auth_mode,
        )

    def get_git_ssh_public_key(self) -> dict[str, object]:
        generated = self._ensure_operator_ssh_key_pair()
        public_key_path = self._operator_ssh_public_key_host_path()
        if not public_key_path.exists():
            raise RuntimeError("SSH public key could not be generated. Please retry.")

        public_key = public_key_path.read_text(encoding="utf-8").strip()
        if not public_key:
            raise RuntimeError("SSH public key file is empty. Please regenerate the key.")

        fingerprint = self._read_operator_ssh_fingerprint()
        message = (
            "A new SSH key pair was created for Git integration."
            if generated
            else "Using existing SSH key pair for Git integration."
        )
        return {
            "publicKey": public_key,
            "fingerprint": fingerprint,
            "keyPath": str(public_key_path),
            "generated": generated,
            "message": message,
        }

    def verify_git_ssh_connection(self, host: str = "github.com", username: str = "git") -> dict[str, object]:
        sanitized_host = self._sanitize_ssh_target(host, label="host")
        sanitized_username = self._sanitize_ssh_target(username, label="username")
        self._ensure_operator_ssh_key_pair()

        target = f"{sanitized_username}@{sanitized_host}"
        shell_command = (
            "set +e; "
            'mkdir -p "$HOME/.ssh"; '
            'chmod 700 "$HOME/.ssh"; '
            'OUTPUT=$(ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 '
            '-i "$HOME/.ssh/id_ed25519_operator" '
            f"{target} 2>&1); "
            "STATUS=$?; "
            'printf "%s\\n" "$OUTPUT"; '
            'printf "__SSH_EXIT_CODE__:%s\\n" "$STATUS"; '
            "exit 0"
        )

        command_result = self._docker_service.run_builder_container(
            request_id="git-ssh-verify",
            shell_command=shell_command,
            timeout_seconds=45,
        )
        cleaned_logs = self.clean_cli_output(command_result.logs)
        exit_code = self._extract_ssh_exit_code(cleaned_logs)
        logs_without_marker = self._strip_ssh_exit_marker(cleaned_logs)
        connected = self._is_git_ssh_connected(logs_without_marker, exit_code)
        message = self._build_git_ssh_message(
            connected=connected,
            host=sanitized_host,
            username=sanitized_username,
            logs=logs_without_marker,
        )

        return {
            "connected": connected,
            "host": sanitized_host,
            "username": sanitized_username,
            "exitCode": exit_code,
            "message": message,
            "logs": logs_without_marker,
        }

    def _operator_git_auth_env(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        config_entries: list[tuple[str, str]] = []

        workspace_root = self._settings.codex_workspace_container.rstrip("/")
        ssh_key_host = Path(self._settings.codex_workspace_host) / ".ssh" / "id_ed25519_operator"
        if ssh_key_host.exists():
            ssh_key_container = f"{workspace_root}/.ssh/id_ed25519_operator"
            known_hosts_container = f"{workspace_root}/.ssh/known_hosts"
            environment["GIT_SSH_COMMAND"] = (
                f"ssh -i {ssh_key_container} -o IdentitiesOnly=yes "
                f"-o UserKnownHostsFile={known_hosts_container} -o StrictHostKeyChecking=accept-new"
            )
            # Rewrite HTTPS GitHub remotes to SSH for this process tree.
            config_entries.append(("url.git@github.com:.insteadof", "https://github.com/"))

        token = (self._settings.operator_github_token or "").strip()
        if token:
            username = (self._settings.operator_github_username or "x-access-token").strip() or "x-access-token"
            encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
            # HTTPS fallback when PAT is configured.
            config_entries.append(("http.https://github.com/.extraheader", f"AUTHORIZATION: basic {encoded}"))

        if config_entries:
            environment["GIT_CONFIG_COUNT"] = str(len(config_entries))
            for index, (key, value) in enumerate(config_entries):
                environment[f"GIT_CONFIG_KEY_{index}"] = key
                environment[f"GIT_CONFIG_VALUE_{index}"] = value

        return environment

    def _operator_runtime_env(self) -> dict[str, str]:
        environment = self._operator_git_auth_env()
        # Keep package managers non-interactive inside operator runs.
        environment["DEBIAN_FRONTEND"] = "noninteractive"

        sudo_password = str(getattr(self._settings, "operator_sudo_password", "") or "").strip()
        if sudo_password:
            # Optional secret for non-interactive sudo flows during remote/server package installs.
            environment["OPERATOR_SUDO_PASSWORD"] = sudo_password

        return environment

    def _load_codex_auth_payload(self) -> tuple[dict[str, Any] | None, str | None]:
        auth_path = self._codex_auth_file_path()
        if not auth_path.exists():
            return None, "Codex auth file was not found. Sign in to Codex first."

        try:
            raw_payload = json.loads(auth_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, "Unable to read Codex auth file."

        if not isinstance(raw_payload, dict):
            return None, "Codex auth file has an unexpected format."
        return raw_payload, None

    def _codex_auth_file_path(self) -> Path:
        return Path(self._settings.codex_workspace_host) / ".codex" / "auth.json"

    @staticmethod
    def _build_usage_unavailable(message: str, auth_mode: str | None) -> dict[str, object]:
        return {
            "available": False,
            "authMode": auth_mode,
            "endpoint": None,
            "planType": None,
            "message": message,
            "fetchedAt": None,
            "rateLimit": None,
            "codeReviewRateLimit": None,
            "additionalRateLimits": [],
            "credits": None,
            "spendControlReached": None,
        }

    @classmethod
    def _map_usage_payload(cls, payload: dict[str, Any], auth_mode: str | None, endpoint: str) -> dict[str, object]:
        additional_limits_raw = payload.get("additional_rate_limits")
        additional_limits: list[dict[str, object]] = []
        if isinstance(additional_limits_raw, list):
            for item in additional_limits_raw:
                if not isinstance(item, dict):
                    continue
                additional_limits.append({
                    "limitName": item.get("limit_name"),
                    "meteredFeature": item.get("metered_feature"),
                    "rateLimit": cls._map_rate_limit_details(item.get("rate_limit")),
                })

        credits_raw = payload.get("credits")
        credits: dict[str, object] | None = None
        if isinstance(credits_raw, dict):
            balance_raw = credits_raw.get("balance")
            balance = str(balance_raw) if balance_raw is not None else None
            credits = {
                "hasCredits": bool(credits_raw.get("has_credits")),
                "unlimited": bool(credits_raw.get("unlimited")),
                "balance": balance,
                "overageLimitReached": (
                    bool(credits_raw.get("overage_limit_reached"))
                    if "overage_limit_reached" in credits_raw
                    else None
                ),
            }

        spend_control = payload.get("spend_control")
        spend_control_reached: bool | None = None
        if isinstance(spend_control, dict) and "reached" in spend_control:
            spend_control_reached = bool(spend_control.get("reached"))

        return {
            "available": True,
            "authMode": auth_mode,
            "endpoint": endpoint,
            "planType": str(payload.get("plan_type") or "").strip() or None,
            "message": "Codex usage loaded from your ChatGPT account.",
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "rateLimit": cls._map_rate_limit_details(payload.get("rate_limit")),
            "codeReviewRateLimit": cls._map_rate_limit_details(payload.get("code_review_rate_limit")),
            "additionalRateLimits": additional_limits,
            "credits": credits,
            "spendControlReached": spend_control_reached,
        }

    @classmethod
    def _map_rate_limit_details(cls, value: Any) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None
        return {
            "allowed": bool(value.get("allowed")),
            "limitReached": bool(value.get("limit_reached")),
            "primaryWindow": cls._map_rate_limit_window(value.get("primary_window")),
            "secondaryWindow": cls._map_rate_limit_window(value.get("secondary_window")),
        }

    @classmethod
    def _map_rate_limit_window(cls, value: Any) -> dict[str, object] | None:
        if not isinstance(value, dict):
            return None

        used_percent_raw = value.get("used_percent")
        limit_window_seconds_raw = value.get("limit_window_seconds")
        reset_after_seconds_raw = value.get("reset_after_seconds")
        reset_at_raw = value.get("reset_at")

        used_percent: float | None = None
        if isinstance(used_percent_raw, (int, float)):
            used_percent = float(used_percent_raw)

        limit_window_seconds: int | None = None
        if isinstance(limit_window_seconds_raw, (int, float)):
            limit_window_seconds = int(limit_window_seconds_raw)

        reset_after_seconds: int | None = None
        if isinstance(reset_after_seconds_raw, (int, float)):
            reset_after_seconds = int(reset_after_seconds_raw)

        reset_at_epoch: int | None = None
        if isinstance(reset_at_raw, (int, float)):
            reset_at_epoch = int(reset_at_raw)

        return {
            "usedPercent": used_percent,
            "limitWindowSeconds": limit_window_seconds,
            "windowMinutes": cls._window_minutes_from_seconds(limit_window_seconds),
            "resetAfterSeconds": reset_after_seconds,
            "resetAtEpoch": reset_at_epoch,
            "resetAt": cls._epoch_seconds_to_iso_utc(reset_at_epoch),
        }

    @staticmethod
    def _window_minutes_from_seconds(seconds: int | None) -> int | None:
        if seconds is None or seconds <= 0:
            return None
        return (seconds + 59) // 60

    @staticmethod
    def _epoch_seconds_to_iso_utc(value: int | None) -> str | None:
        if value is None or value <= 0:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()

    @staticmethod
    def _apply_chat_model(shell_command: str, model: str | None) -> str:
        cleaned = (model or "").strip()
        if not cleaned:
            return shell_command
        if "--model" in shell_command:
            return shell_command

        model_flag = f" --model {shlex.quote(cleaned)}"
        return re.sub(r"\bcodex\s+exec\b", lambda match: f"{match.group(0)}{model_flag}", shell_command, count=1)

    @staticmethod
    def _apply_chat_images(shell_command: str, image_paths: list[str] | None) -> str:
        if not image_paths:
            return shell_command
        cleaned = [path.strip() for path in image_paths if path and path.strip()]
        if not cleaned:
            return shell_command
        image_flags = "".join(f" --image {shlex.quote(path)}" for path in cleaned)
        return re.sub(r"\bcodex\s+exec\b", lambda match: f"{match.group(0)}{image_flags}", shell_command, count=1)

    def _run_builder_container_with_retries(
        self,
        request_id: str,
        shell_command: str,
        timeout_seconds: int,
        extra_volumes: dict[str, dict[str, str]] | None = None,
        extra_environment: dict[str, str] | None = None,
        working_dir_override: str | None = None,
        tty: bool = False,
    ) -> CommandResult:
        configured_retries = max(0, int(getattr(self._settings, "codex_transient_retries", 0)))
        retry_delay_seconds = max(0.0, float(getattr(self._settings, "codex_transient_retry_delay_seconds", 0.0)))
        max_attempts = configured_retries + 1

        transient_attempt_logs: list[str] = []
        last_result: CommandResult | None = None

        for attempt in range(1, max_attempts + 1):
            result = self._docker_service.run_builder_container_with_options(
                request_id=request_id,
                shell_command=shell_command,
                timeout_seconds=timeout_seconds,
                extra_volumes=extra_volumes,
                extra_environment=extra_environment,
                working_dir_override=working_dir_override,
                tty=tty,
            )
            last_result = result

            if result.success:
                if not transient_attempt_logs:
                    return result

                combined_logs = "\n\n".join([
                    *transient_attempt_logs,
                    f"Recovered after transient Codex runtime issue on attempt {attempt}/{max_attempts}.",
                    result.logs.strip(),
                ]).strip()
                return CommandResult(success=True, exit_code=result.exit_code, logs=combined_logs)

            if not self._is_transient_retryable_failure(result.logs):
                if not transient_attempt_logs:
                    return result

                combined_logs = "\n\n".join([
                    *transient_attempt_logs,
                    result.logs.strip(),
                ]).strip()
                return CommandResult(success=False, exit_code=result.exit_code, logs=combined_logs)

            transient_attempt_logs.append(
                f"[Codex transient runtime failure attempt {attempt}/{max_attempts}]\n"
                f"{(result.logs or '').strip() or '(no logs)'}"
            )
            if attempt < max_attempts and retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds * attempt)

        if last_result is None:
            return CommandResult(
                success=False,
                exit_code=1,
                logs="Codex command failed before producing output.",
            )

        combined_failure_logs = "\n\n".join([
            *transient_attempt_logs,
            f"Transient Codex runtime issue persisted after {max_attempts} attempts.",
            (last_result.logs or "").strip(),
        ]).strip()
        return CommandResult(success=False, exit_code=last_result.exit_code, logs=combined_failure_logs)

    @classmethod
    def _is_transient_transport_failure(cls, logs: str) -> bool:
        lowered = (logs or "").lower()
        if not lowered:
            return False
        return any(marker in lowered for marker in cls._TRANSIENT_TRANSPORT_MARKERS)

    @classmethod
    def _is_transient_retryable_failure(cls, logs: str) -> bool:
        lowered = (logs or "").lower()
        if not lowered:
            return False
        if cls._is_transient_transport_failure(lowered):
            return True
        if "docker request error" not in lowered:
            return False
        return any(marker in lowered for marker in cls._TRANSIENT_DOCKER_MARKERS)

    @staticmethod
    def _sanitize_attachment_filename(file_name: str) -> str:
        candidate = Path(file_name or "image").name
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-.")
        if not cleaned:
            cleaned = "image"
        if len(cleaned) > 180:
            cleaned = cleaned[:180]
        return cleaned

    @classmethod
    def clean_cli_output(cls, value: str) -> str:
        without_ansi = cls._ANSI_ESCAPE_RE.sub("", value)
        cleaned_lines = [
            line.rstrip()
            for line in without_ansi.replace("\r", "\n").splitlines()
            if line.strip() and not line.startswith("Session terminated, killing shell")
        ]
        return "\n".join(cleaned_lines).strip()

    @classmethod
    def clean_cli_stream_chunk(cls, value: str) -> str:
        without_ansi = cls._ANSI_ESCAPE_RE.sub("", value)
        normalized = without_ansi.replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("Session terminated, killing shell...", "")

    @classmethod
    def _parse_login_state(cls, message: str, command_success: bool) -> tuple[bool, str | None]:
        if not message:
            return command_success, None

        lines = [line.strip() for line in message.splitlines() if line.strip()]

        explicit_logged_out = any(cls._LOGGED_OUT_RE.search(line) for line in lines)
        if explicit_logged_out:
            return False, None

        provider = cls._extract_provider(lines)

        # Trust successful `codex login status` exits unless we explicitly detect logged-out output.
        # This keeps auth sticky even if CLI wording changes across releases.
        if command_success:
            return True, provider

        logged_in_line = next((line for line in lines if cls._LOGGED_IN_RE.search(line)), None)
        if logged_in_line:
            provider = provider or cls._extract_provider([logged_in_line])
            return True, provider

        return False, None

    @classmethod
    def _extract_provider(cls, lines: list[str]) -> str | None:
        for line in lines:
            lowered = line.lower()
            if "chatgpt" in lowered:
                return "ChatGPT"
            if "api key" in lowered or "api-key" in lowered:
                return "API key"

            provider_match = cls._PROVIDER_RE.search(line)
            if provider_match:
                value = provider_match.group(1).strip().rstrip(".")
                if value:
                    return value
        return None

    def _ensure_operator_ssh_key_pair(self) -> bool:
        private_key_path = self._operator_ssh_private_key_host_path()
        public_key_path = self._operator_ssh_public_key_host_path()
        already_exists = private_key_path.exists() and public_key_path.exists()

        shell_command = (
            "set -e; "
            'mkdir -p "$HOME/.ssh"; '
            'chmod 700 "$HOME/.ssh"; '
            'if [ ! -f "$HOME/.ssh/id_ed25519_operator" ]; then '
            'ssh-keygen -q -t ed25519 -f "$HOME/.ssh/id_ed25519_operator" -N "" -C "toolhub-operator@local"; '
            "fi; "
            'if [ ! -f "$HOME/.ssh/id_ed25519_operator.pub" ]; then '
            'ssh-keygen -y -f "$HOME/.ssh/id_ed25519_operator" > "$HOME/.ssh/id_ed25519_operator.pub"; '
            "fi; "
            'chmod 600 "$HOME/.ssh/id_ed25519_operator"; '
            'chmod 644 "$HOME/.ssh/id_ed25519_operator.pub"'
        )
        result = self._docker_service.run_builder_container(
            request_id="git-ssh-keygen",
            shell_command=shell_command,
            timeout_seconds=45,
        )
        if not result.success:
            cleaned = self.clean_cli_output(result.logs)
            raise RuntimeError(cleaned or "Unable to prepare SSH key for Git integration.")

        if not (private_key_path.exists() and public_key_path.exists()):
            raise RuntimeError("SSH key generation finished but key files were not found in workspace.")
        return not already_exists

    def _read_operator_ssh_fingerprint(self) -> str | None:
        result = self._docker_service.run_builder_container(
            request_id="git-ssh-fingerprint",
            shell_command='ssh-keygen -lf "$HOME/.ssh/id_ed25519_operator.pub"',
            timeout_seconds=20,
        )
        if not result.success:
            return None
        cleaned = self.clean_cli_output(result.logs)
        return cleaned.splitlines()[0] if cleaned else None

    @staticmethod
    def _extract_ssh_exit_code(logs: str) -> int:
        match = re.search(r"__SSH_EXIT_CODE__:(\d+)", logs)
        if match:
            return int(match.group(1))
        return 1

    @staticmethod
    def _strip_ssh_exit_marker(logs: str) -> str:
        return re.sub(r"\n?__SSH_EXIT_CODE__:\d+\s*$", "", logs).strip()

    @classmethod
    def _is_git_ssh_connected(cls, logs: str, exit_code: int) -> bool:
        lowered = logs.lower()
        failure_markers = (
            "permission denied",
            "authentication failed",
            "could not resolve hostname",
            "name or service not known",
            "connection timed out",
            "connection refused",
            "no route to host",
        )
        if any(marker in lowered for marker in failure_markers):
            return False

        success_markers = (
            "successfully authenticated",
            "you've successfully authenticated",
            "welcome to github",
            "welcome to gitlab",
            "shell access is not provided",
            "authenticated via ssh key",
        )
        if any(marker in lowered for marker in success_markers):
            return True

        return exit_code == 0

    @staticmethod
    def _build_git_ssh_message(connected: bool, host: str, username: str, logs: str) -> str:
        if connected:
            return f"SSH authentication to {username}@{host} looks successful."

        lowered = logs.lower()
        if "permission denied" in lowered:
            return (
                f"SSH key is not authorized yet for {username}@{host}. "
                "Add the public key to your Git provider and verify again."
            )
        if "could not resolve hostname" in lowered or "name or service not known" in lowered:
            return f"Unable to resolve host `{host}`. Check the Git host and retry."
        if "connection timed out" in lowered or "connection refused" in lowered:
            return f"Connection to `{host}` timed out or was refused. Please retry."
        return f"SSH authentication to {username}@{host} could not be verified."

    @classmethod
    def _sanitize_ssh_target(cls, value: str, label: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"Git SSH {label} is required.")
        if not cls._SSH_TARGET_RE.fullmatch(cleaned):
            raise ValueError(f"Git SSH {label} contains unsupported characters.")
        return cleaned

    def _operator_ssh_private_key_host_path(self) -> Path:
        return Path(self._settings.codex_workspace_host) / ".ssh" / "id_ed25519_operator"

    def _operator_ssh_public_key_host_path(self) -> Path:
        return Path(self._settings.codex_workspace_host) / ".ssh" / "id_ed25519_operator.pub"
