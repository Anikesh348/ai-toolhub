import base64
import json
import mimetypes
from pathlib import Path
import re
import shlex
from typing import Iterable
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

    def __init__(self, settings: Settings, docker_service: DockerService) -> None:
        self._settings = settings
        self._docker_service = docker_service

    def run_generation(self, request_id: str, prompt: str, model: str | None = None) -> CommandResult:
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
        return self._docker_service.run_builder_container(
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
        return self._docker_service.run_builder_container(
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

    def run_operator(
        self,
        session_id: str,
        prompt: str,
        container_cwd: str,
        extra_volumes: dict[str, dict[str, str]],
        timeout_seconds: int | None = None,
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
        return self._docker_service.run_builder_container_with_options(
            request_id=request_id,
            shell_command=shell_command,
            timeout_seconds=timeout_seconds or self._settings.operator_timeout_seconds,
            extra_volumes=extra_volumes,
            extra_environment=self._operator_git_auth_env(),
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
