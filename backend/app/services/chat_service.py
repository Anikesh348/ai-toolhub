import json
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from app.repositories.chat_message_repository import ChatMessageRepository
from app.repositories.chat_session_repository import ChatSessionRepository
from app.services.codex_service import CodexService
from app.services.docker_service import DockerService
from app.services.operator_access_service import OperatorAccessService
from app.services.system_context_service import SystemContextService
from app.services.tool_builder_service import ToolBuilderService
from app.utils.logger import get_logger

CHAT_MODES = {"general", "tool_builder", "operator", "pi_operator"}
MAX_IMAGE_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_ATTACHMENTS = 4


class ChatService:
    _FRONTEND_SERVICE_HINTS = ("frontend", "web", "ui", "client", "dashboard", "site", "next", "vite")
    _BACKEND_SERVICE_HINTS = ("backend", "api", "server", "worker", "gateway", "graphql", "rest")

    def __init__(
        self,
        session_repository: ChatSessionRepository,
        message_repository: ChatMessageRepository,
        codex_service: CodexService,
        docker_service: DockerService | None = None,
        operator_access_service: OperatorAccessService | None = None,
        system_context_service: SystemContextService | None = None,
        tool_builder_service: ToolBuilderService | None = None,
        tool_frontend_base_url: str = "http://localhost",
        tool_backend_base_url: str = "http://localhost",
    ) -> None:
        self._session_repository = session_repository
        self._message_repository = message_repository
        self._codex_service = codex_service
        self._docker_service = docker_service
        self._operator_access_service = operator_access_service
        self._system_context_service = system_context_service
        self._tool_builder_service = tool_builder_service
        self._tool_frontend_base_url = self._normalize_runtime_base_url(tool_frontend_base_url)
        self._tool_backend_base_url = self._normalize_runtime_base_url(tool_backend_base_url)
        self._logger = get_logger(__name__)
        self._cancelled_stream_sessions: set[str] = set()
        self._cancelled_stream_lock = threading.Lock()

    def create_session(self, title: str | None, mode: str, model: str | None = None) -> dict:
        normalized_mode = self._normalize_mode(mode)
        normalized_model = self._resolve_model(model=model, fallback_to_default=True)
        cleaned_title = (title or "").strip()
        session_title = cleaned_title if cleaned_title else "New Chat"
        return self._session_repository.create(
            title=session_title,
            mode=normalized_mode,
            model=normalized_model,
        )

    def list_sessions(self, limit: int = 100) -> list[dict]:
        return self._session_repository.list_recent(limit=limit)

    def get_session(self, session_id: str) -> dict | None:
        return self._session_repository.get_by_id(session_id)

    def list_messages(self, session_id: str, limit: int = 500) -> list[dict]:
        return self._message_repository.list_for_session(session_id=session_id, limit=limit)

    def send_message(
        self,
        session_id: str,
        content: str,
        model: str | None = None,
        attachment_ids: list[str] | None = None,
    ) -> tuple[dict | None, dict | None, str | None]:
        session = self._session_repository.get_by_id(session_id)
        if session is None:
            return None, None, "Session not found"

        attachments, attachment_error = self._resolve_message_attachments(
            session_id=session_id,
            attachment_ids=attachment_ids or [],
        )
        if attachment_error:
            return None, None, attachment_error

        existing_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=1)
        user_metadata: dict[str, Any] = {}
        if attachments:
            user_metadata["attachments"] = attachments
        user_message = self._message_repository.create(
            session_id=session_id,
            role="user",
            content=content.strip(),
            metadata=user_metadata if user_metadata else None,
        )
        if not existing_messages:
            session = self._auto_name_session(session, content)

        recent_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=18)
        selected_model = (
            self._resolve_model(model=model, fallback_to_default=False)
            or self._resolve_model(model=session.get("model"), fallback_to_default=False)
            or self._resolve_model(model=None, fallback_to_default=True)
        )
        image_paths = [str(item.get("containerPath")) for item in attachments if item.get("containerPath")]
        prompt = self._build_chat_prompt(
            mode=self._normalize_mode(session["mode"]),
            messages=recent_messages,
        )
        quick_answer = self._try_direct_operator_answer(
            mode=self._normalize_mode(session["mode"]),
            user_content=content,
        )
        assistant_metadata: dict[str, Any] = {}
        if quick_answer is not None:
            assistant_text = quick_answer
            success = True
            exit_code = 0
        elif self._normalize_mode(session["mode"]) == "operator":
            assistant_text = self._run_operator_task(session_id=session_id, user_content=content)
            success = True
            exit_code = 0
        elif self._normalize_mode(session["mode"]) == "tool_builder":
            assistant_text, tool_builder_context = self._run_tool_builder_task(
                session_id=session_id,
                user_content=content,
                selected_model=selected_model,
            )
            success = True
            exit_code = 0
            if tool_builder_context:
                assistant_metadata["toolBuilder"] = tool_builder_context
        else:
            completion = self._codex_service.run_chat(
                session_id=session_id,
                prompt=prompt,
                model=selected_model,
                image_paths=image_paths,
            )
            assistant_text = self._build_assistant_text(completion.logs, completion.success)
            success = completion.success
            exit_code = completion.exit_code
            if not completion.success:
                self._logger.warning(
                    "Chat completion failed for session %s: exit=%s",
                    session_id,
                    completion.exit_code,
                )

        assistant_metadata["success"] = success
        assistant_metadata["exitCode"] = exit_code
        assistant_message = self._message_repository.create(
            session_id=session_id,
            role="assistant",
            content=assistant_text,
            metadata=assistant_metadata,
        )
        self._session_repository.touch(session_id)
        return user_message, assistant_message, None

    def stream_message(
        self,
        session_id: str,
        content: str,
        model: str | None = None,
        attachment_ids: list[str] | None = None,
    ) -> Iterable[dict[str, Any]]:
        self._clear_stream_cancelled(session_id)
        session = self._session_repository.get_by_id(session_id)
        if session is None:
            yield {"type": "error", "error": "Session not found"}
            return

        user_content = content.strip()
        if not user_content:
            yield {"type": "error", "error": "Message content cannot be empty"}
            return

        attachments, attachment_error = self._resolve_message_attachments(
            session_id=session_id,
            attachment_ids=attachment_ids or [],
        )
        if attachment_error:
            yield {"type": "error", "error": attachment_error}
            return

        existing_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=1)
        user_metadata: dict[str, Any] = {}
        if attachments:
            user_metadata["attachments"] = attachments
        user_message = self._message_repository.create(
            session_id=session_id,
            role="user",
            content=user_content,
            metadata=user_metadata if user_metadata else None,
        )
        if not existing_messages:
            session = self._auto_name_session(session, user_content)

        yield {"type": "user_message", "session": session, "message": user_message}
        yield {"type": "status", "status": "thinking"}

        recent_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=18)
        selected_model = (
            self._resolve_model(model=model, fallback_to_default=False)
            or self._resolve_model(model=session.get("model"), fallback_to_default=False)
            or self._resolve_model(model=None, fallback_to_default=True)
        )
        image_paths = [str(item.get("containerPath")) for item in attachments if item.get("containerPath")]
        prompt = self._build_chat_prompt(
            mode=self._normalize_mode(session["mode"]),
            messages=recent_messages,
        )
        quick_answer = self._try_direct_operator_answer(
            mode=self._normalize_mode(session["mode"]),
            user_content=user_content,
        )
        streamed_assistant = ""
        stream_status = "thinking"
        assistant_metadata: dict[str, Any] = {}
        if quick_answer is not None:
            assistant_text = quick_answer
            success = True
            exit_code = 0
        elif self._normalize_mode(session["mode"]) == "operator":
            assistant_text = self._run_operator_task(session_id=session_id, user_content=user_content)
            success = True
            exit_code = 0
        elif self._normalize_mode(session["mode"]) == "tool_builder":
            assistant_text, tool_builder_context = self._run_tool_builder_task(
                session_id=session_id,
                user_content=user_content,
                selected_model=selected_model,
            )
            success = True
            exit_code = 0
            if tool_builder_context:
                assistant_metadata["toolBuilder"] = tool_builder_context
        else:
            completion = None
            raw_logs = ""
            for stream_event in self._codex_service.stream_chat(
                session_id=session_id,
                prompt=prompt,
                model=selected_model,
                image_paths=image_paths,
            ):
                if stream_event.type == "log" and stream_event.chunk:
                    raw_logs = f"{raw_logs}{stream_event.chunk}"
                    if len(raw_logs) > 24000:
                        raw_logs = raw_logs[-24000:]
                    detected_status = self._detect_stream_status(stream_event.chunk)
                    if detected_status and detected_status != stream_status:
                        stream_status = detected_status
                        yield {"type": "status", "status": stream_status}
                    parsed_partial = self._sanitize_assistant_output(self._extract_assistant_text(raw_logs))
                    delta = self._incremental_delta(streamed_assistant, parsed_partial)
                    if delta:
                        if stream_status != "thinking":
                            stream_status = "thinking"
                            yield {"type": "status", "status": stream_status}
                        streamed_assistant = parsed_partial
                        yield {"type": "assistant_delta", "delta": delta}
                    continue
                if stream_event.type == "done":
                    completion = stream_event.result

            if completion is None:
                assistant_text = "I could not generate a response right now. Please retry."
                success = False
                exit_code = 1
            else:
                assistant_text = self._build_assistant_text(completion.logs, completion.success)
                success = completion.success
                exit_code = completion.exit_code
            stream_cancelled = self._consume_stream_cancelled(session_id)
            if stream_cancelled:
                if not assistant_text.strip():
                    assistant_text = "Generation stopped."
                success = False
                exit_code = 130
                assistant_metadata["stopped"] = True
            if completion is not None and not completion.success:
                self._logger.warning(
                    "Chat stream completion failed for session %s: exit=%s",
                    session_id,
                    completion.exit_code,
                )

        assistant_metadata["success"] = success
        assistant_metadata["exitCode"] = exit_code
        assistant_message = self._message_repository.create(
            session_id=session_id,
            role="assistant",
            content=assistant_text,
            metadata=assistant_metadata,
        )
        self._session_repository.touch(session_id)
        updated_session = self._session_repository.get_by_id(session_id)
        if updated_session is not None:
            session = updated_session

        remaining = assistant_text
        if streamed_assistant and assistant_text.startswith(streamed_assistant):
            remaining = assistant_text[len(streamed_assistant) :]
        for chunk in self._chunk_text(remaining):
            yield {"type": "assistant_delta", "delta": chunk}

        yield {"type": "assistant_message", "session": session, "message": assistant_message}
        yield {"type": "done"}

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        update_model: bool = False,
        archived: bool | None = None,
    ) -> dict | None:
        normalized_mode = self._normalize_mode(mode) if mode is not None else None
        normalized_title = title.strip() if isinstance(title, str) else None
        normalized_model = self._resolve_model(model=model, fallback_to_default=True) if update_model else None
        return self._session_repository.update(
            session_id=session_id,
            title=normalized_title if normalized_title else None,
            mode=normalized_mode,
            model=normalized_model if update_model else None,
            archived=archived,
        )

    def list_models(self) -> dict[str, Any]:
        return {
            "models": self._codex_service.list_chat_models(),
            "defaultModel": self._codex_service.default_chat_model(),
        }

    def create_image_attachment(
        self,
        session_id: str,
        file_name: str,
        content_type: str | None,
        data: bytes,
    ) -> tuple[dict[str, Any] | None, str | None]:
        session = self._session_repository.get_by_id(session_id)
        if session is None:
            return None, "Session not found"

        if not data:
            return None, "Attachment is empty."
        if len(data) > MAX_IMAGE_ATTACHMENT_BYTES:
            return None, f"Image is too large. Maximum size is {MAX_IMAGE_ATTACHMENT_BYTES // (1024 * 1024)}MB."
        normalized_type = (content_type or "").lower()
        if not normalized_type.startswith("image/"):
            guessed_type = (mimetypes.guess_type(file_name)[0] or "").lower()
            if not guessed_type.startswith("image/"):
                return None, "Only image attachments are supported."
            content_type = guessed_type

        try:
            saved = self._codex_service.save_chat_attachment(
                session_id=session_id,
                file_name=file_name,
                content_type=content_type,
                data=data,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Unable to save chat attachment for session %s", session_id)
            return None, str(exc)
        if not saved:
            return None, "Unable to save attachment."
        attachment = dict(saved)
        attachment["url"] = f"/chat/sessions/{session_id}/attachments/{attachment['id']}"
        return attachment, None

    def get_image_attachment(self, session_id: str, attachment_id: str) -> dict[str, Any] | None:
        attachment = self._codex_service.get_chat_attachment(session_id=session_id, attachment_id=attachment_id)
        if not attachment:
            return None
        enriched = dict(attachment)
        enriched["url"] = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
        return enriched

    def stop_active_stream(self, session_id: str) -> tuple[bool, str | None]:
        session = self._session_repository.get_by_id(session_id)
        if session is None:
            return False, "Session not found"

        self._mark_stream_cancelled(session_id)
        self._codex_service.stop_chat(session_id=session_id)
        return True, None

    def delete_session(self, session_id: str) -> bool:
        session = self._session_repository.get_by_id(session_id)
        if session is None:
            return False
        self._message_repository.delete_for_session(session_id=session_id)
        return self._session_repository.delete(session_id=session_id)

    def _mark_stream_cancelled(self, session_id: str) -> None:
        with self._cancelled_stream_lock:
            self._cancelled_stream_sessions.add(session_id)

    def _clear_stream_cancelled(self, session_id: str) -> None:
        with self._cancelled_stream_lock:
            self._cancelled_stream_sessions.discard(session_id)

    def _consume_stream_cancelled(self, session_id: str) -> bool:
        with self._cancelled_stream_lock:
            if session_id in self._cancelled_stream_sessions:
                self._cancelled_stream_sessions.remove(session_id)
                return True
        return False

    def _build_chat_prompt(self, mode: str, messages: list[dict[str, Any]]) -> str:
        normalized_mode = self._normalize_mode(mode)
        system_prompt = self._system_prompt_for_mode(normalized_mode)
        lines = [system_prompt, "", "Conversation:"]
        if normalized_mode == "operator" and self._system_context_service is not None:
            lines = [
                system_prompt,
                "",
                self._system_context_service.format_for_prompt(),
                "",
                "Conversation:",
            ]
        for message in messages:
            role = message["role"].upper()
            # Keep history entries on one serialized line so echoed prompts
            # do not inject multiline prior responses into incremental parsing.
            content_with_context = str(message["content"])
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            if isinstance(metadata, dict):
                attachment_context = self._attachment_prompt_context(metadata)
                if attachment_context:
                    if content_with_context:
                        content_with_context = f"{content_with_context}\n\n{attachment_context}"
                    else:
                        content_with_context = attachment_context
            content = re.sub(r"\s+", " ", content_with_context).strip()
            serialized_content = json.dumps(content, ensure_ascii=False)
            lines.append(f"{role}: {serialized_content}")
        lines.append("")
        lines.append("Respond as ASSISTANT in clear Markdown. Keep answers concise unless asked for details.")
        lines.append("Return only the final answer. Do not include tool logs, search traces, or intermediate status narration.")
        return "\n".join(lines)

    def _build_assistant_text(self, raw_logs: str, success: bool) -> str:
        parsed = self._extract_assistant_text(raw_logs)
        if parsed:
            return self._sanitize_assistant_output(parsed)
        if success:
            return "No response was produced."
        return "I could not generate a response right now. Please retry."

    @staticmethod
    def _sanitize_assistant_output(text: str) -> str:
        if not text:
            return ""

        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        filtered_lines: list[str] = []
        for line in normalized.split("\n"):
            stripped = line.strip()
            if not stripped:
                if filtered_lines and filtered_lines[-1] != "":
                    filtered_lines.append("")
                continue
            if ChatService._is_prompt_echo_line(stripped):
                continue
            lowered = stripped.lower()
            if lowered.startswith("assistant:"):
                assistant_value = stripped.split(":", 1)[1].lstrip()
                if not assistant_value:
                    continue
                # Serialized history prompt echoes are quoted/json-like. Skip them.
                if (
                    (assistant_value.startswith('"') and assistant_value.endswith('"'))
                    or (assistant_value.startswith("'") and assistant_value.endswith("'"))
                    or (assistant_value.startswith("{") and assistant_value.endswith("}"))
                    or (assistant_value.startswith("[") and assistant_value.endswith("]"))
                ):
                    continue
                stripped = assistant_value
                lowered = stripped.lower()
            if lowered.startswith("user:"):
                continue
            filtered_lines.append(stripped)

        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(filtered_lines).strip())
        blocks = [block.strip() for block in cleaned.split("\n\n") if block.strip()]
        if not blocks:
            return cleaned

        deduped_blocks: list[str] = []
        seen_block_keys: set[str] = set()
        for block in blocks:
            block_key = re.sub(r"\s+", " ", block).strip().lower()
            if not block_key:
                continue
            if deduped_blocks and deduped_blocks[-1] == block:
                continue
            if block_key in seen_block_keys:
                continue
            deduped_blocks.append(block)
            seen_block_keys.add(block_key)

        count = len(deduped_blocks)
        if count > 1 and count % 2 == 0:
            midpoint = count // 2
            lhs = [re.sub(r"\s+", " ", block).strip().lower() for block in deduped_blocks[:midpoint]]
            rhs = [re.sub(r"\s+", " ", block).strip().lower() for block in deduped_blocks[midpoint:]]
            if lhs == rhs:
                deduped_blocks = deduped_blocks[:midpoint]

        return "\n\n".join(deduped_blocks).strip()

    @staticmethod
    def _is_prompt_echo_line(stripped: str) -> bool:
        lowered = stripped.lower()
        return (
            lowered.startswith("you are a helpful engineering assistant for a self-hosted ai tool platform")
            or lowered.startswith("respond as assistant in clear markdown")
            or lowered.startswith("return only the final answer")
            or lowered == "conversation:"
            or lowered.startswith("conversation:")
        )

    @staticmethod
    def _is_docker_summary_query(user_content: str) -> bool:
        lowered = user_content.lower()
        if "docker" not in lowered:
            return False

        has_summary_marker = any(
            keyword in lowered
            for keyword in (
                "running",
                "containers",
                "summary",
                "list",
                "show",
                "status",
                "what is running",
            )
        )
        if not has_summary_marker:
            return False

        action_pattern = re.compile(
            r"\b("
            r"deploy|redeploy|rebuild|build|spin\s+up|start(?:\s+up)?|launch|run|"
            r"bring\s+up|restart|stop|shutdown|remove|delete|create|"
            r"compose\s+up|up\s+-d|docker\s+up"
            r")\b"
        )
        return action_pattern.search(lowered) is None

    def _try_direct_operator_answer(self, mode: str, user_content: str) -> str | None:
        if mode != "operator" or self._system_context_service is None:
            if mode != "operator":
                return None

        lowered = user_content.lower()

        # Docker runtime summaries
        if self._docker_service is not None and self._is_docker_summary_query(user_content):
            containers = self._docker_service.list_containers_summary(running_only=True)
            if not containers:
                return "No running Docker containers found."

            lines = [f"Running Docker containers: {len(containers)}", ""]
            for item in containers[:25]:
                health = item.get("health") or "n/a"
                ports = ", ".join(item.get("ports") or []) or "no published ports"
                lines.append(
                    f"- {item['name']} ({item['image']})\n"
                    f"  status: {item['status']} | health: {health}\n"
                    f"  ports: {ports}"
                )
            if len(containers) > 25:
                lines.append(f"\n...and {len(containers) - 25} more containers.")
            return "\n".join(lines)

        # Health check / restart actions
        if self._docker_service is not None:
            health_match = re.search(r"check if ([a-zA-Z0-9_.-]+) container is healthy", lowered)
            if health_match:
                target_name = health_match.group(1)
                should_restart = "restart" in lowered
                if should_restart:
                    result = self._docker_service.check_container_health_and_restart(target_name)
                else:
                    result = self._docker_service.get_container_health(target_name)
                return str(result.get("message", "Unable to check container health."))

        if self._system_context_service is None:
            return None
        if not any(keyword in lowered for keyword in ("battery", "ram", "memory", "cpu", "disk", "storage", "uptime")):
            return None

        snapshot = self._system_context_service.snapshot()
        cpu = snapshot["cpu"]
        memory = snapshot["memory"]
        disk = snapshot["disk"]
        battery = snapshot["battery"]

        details: list[str] = []
        if "battery" in lowered:
            if battery is None:
                details.append("Battery: unavailable in current runtime context.")
            else:
                battery_state = "plugged in" if battery["isPlugged"] else "on battery"
                details.append(f"Battery: {battery['percent']}% ({battery_state}).")
        if "ram" in lowered or "memory" in lowered:
            details.append(
                f"RAM: {memory['percent']}% used ({self._bytes_to_gb(memory['usedBytes'])}GB / {self._bytes_to_gb(memory['totalBytes'])}GB)."
            )
        if "cpu" in lowered:
            details.append(f"CPU: {cpu['percent']}% usage right now.")
        if "disk" in lowered or "storage" in lowered:
            details.append(
                f"Disk (/): {disk['percent']}% used ({self._bytes_to_gb(disk['usedBytes'])}GB / {self._bytes_to_gb(disk['totalBytes'])}GB)."
            )
        if "uptime" in lowered:
            details.append(f"Uptime: {self._format_uptime(int(snapshot['uptimeSeconds']))}.")

        if snapshot.get("isContainer"):
            details.append("Note: this agent is running in a container, so host-only signals may be limited.")

        return "\n".join(details) if details else None

    def _run_operator_task(self, session_id: str, user_content: str) -> str:
        if self._operator_access_service is None:
            return "Operator policy is not configured. Please set OPERATOR_ALLOWED_PATHS / OPERATOR_DENIED_PATHS."

        session = self._session_repository.get_by_id(session_id)
        project_hint = self._extract_project_hint(user_content)
        host_cwd = self._operator_access_service.resolve_project_path(project_hint)
        if host_cwd is None:
            host_cwd = self._operator_access_service.normalize_cwd(None)
        container_cwd = self._operator_access_service.to_container_path(host_cwd)
        mounts = self._operator_access_service.build_mounts(primary_path=host_cwd)
        if Path("/var/run/docker.sock").exists():
            mounts["/var/run/docker.sock"] = {
                "bind": "/var/run/docker.sock",
                "mode": "rw",
            }
        branch_instruction = self._operator_branch_instruction(user_content=user_content)
        recent_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=24)
        conversation_context = self._format_operator_conversation_context(recent_messages)
        cross_session_context = self._recent_operator_session_context(
            current_session_id=session_id,
            current_session_messages=recent_messages,
        )
        extra_context_block = ""
        if cross_session_context:
            extra_context_block = (
                "Recent operator context from previous session (use only if relevant):\n"
                f"{cross_session_context}\n\n"
            )

        operator_prompt = (
            "You are operating as a software engineering operator on a host-mounted filesystem.\n"
            f"{self._operator_access_service.policy_summary()}\n\n"
            f"Session title: {(session or {}).get('title', 'Operator Session')}\n"
            f"Session mode: {(session or {}).get('mode', 'operator')}\n\n"
            "Conversation context (older to newer):\n"
            f"{conversation_context}\n\n"
            f"{extra_context_block}"
            f"Current user task:\n{user_content}\n\n"
            "Execution requirements:\n"
            f"- Work under: {container_cwd}\n"
            f"{branch_instruction}\n"
            "- For code changes, implement the requested update and run tests/lint/build checks only when needed for confidence.\n"
            "- If this is non-code ops query, provide concise factual summary.\n"
            "- For container operations, try `docker compose` first and fall back to `docker-compose` if needed.\n"
            "- Keep response crisp and readable (3-6 short lines by default).\n"
            "- Include verification details, branch names, modified file lists, and environment diagnostics only when the user explicitly asks.\n"
            "- Do not paste full file contents, full diffs, or long code/config blocks unless the user explicitly asks for code/log output.\n"
            "- Never access denied paths.\n"
            "- Resolve follow-up references (for example: 'that repo', 'continue', 'as discussed') using conversation context.\n"
            "- If context is still ambiguous, state the ambiguity briefly and proceed with the most likely interpretation.\n"
        )

        try:
            completion = self._codex_service.run_operator(
                session_id=session_id,
                prompt=operator_prompt,
                container_cwd=container_cwd,
                extra_volumes=mounts,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Operator task execution failed for session %s", session_id)
            return f"Operator task failed to start: {exc}"

        output = self._build_assistant_text(completion.logs, completion.success)
        output = self._sanitize_operator_output(user_content=user_content, output=output)
        if not completion.success:
            return f"{output}\n\n(Operator execution exited with code {completion.exit_code})"
        return output

    @staticmethod
    def _sanitize_operator_output(user_content: str, output: str) -> str:
        cleaned = output.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            return ""

        lowered_request = user_content.lower()
        requested_details = ChatService._operator_requested_verbose_output(lowered_request)
        requested_code = ChatService._operator_requested_code_output(lowered_request)
        if requested_details:
            return cleaned

        filtered_lines: list[str] = []
        skip_verification_block = False
        for line in cleaned.split("\n"):
            stripped = line.strip()
            lowered = stripped.lower()

            if lowered in {"verification", "verification:"}:
                skip_verification_block = True
                continue
            if skip_verification_block:
                if not stripped:
                    skip_verification_block = False
                continue

            if lowered.startswith("if you want, i can"):
                continue
            filtered_lines.append(line.rstrip())

        normalized = "\n".join(filtered_lines).strip()
        if not requested_code:
            normalized = ChatService._strip_fenced_code_blocks(normalized)
            normalized = ChatService._strip_diff_noise_lines(normalized)
            if not normalized:
                return "I omitted verbose code output. Ask for `diff` or `logs` if you want full details."
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized

    @staticmethod
    def _operator_requested_verbose_output(lowered_request: str) -> bool:
        verbose_markers = (
            "verify",
            "verification",
            "logs",
            "log output",
            "details",
            "full output",
            "raw output",
            "command output",
            "debug output",
        )
        return any(token in lowered_request for token in verbose_markers)

    @staticmethod
    def _operator_requested_code_output(lowered_request: str) -> bool:
        code_markers = (
            "code",
            "snippet",
            "diff",
            "patch",
            "file contents",
            "show file",
            "show the file",
            "source",
            "implementation",
        )
        return any(token in lowered_request for token in code_markers)

    @staticmethod
    def _strip_fenced_code_blocks(value: str) -> str:
        if "```" not in value:
            return value.strip()

        lines = value.split("\n")
        kept: list[str] = []
        in_fence = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _strip_diff_noise_lines(value: str) -> str:
        lines: list[str] = []
        for raw_line in value.split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            if re.match(r"^(diff --git|index [0-9a-f]+\.\.[0-9a-f]+|--- |\+\+\+ |@@ )", stripped):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _operator_branch_instruction(user_content: str) -> str:
        requested_branch = ChatService._extract_requested_branch_name(user_content=user_content)
        if requested_branch:
            return (
                "- For code-change tasks in a git repository, the user explicitly requested branch work: "
                f"switch to `{requested_branch}` (create it only if it does not exist), then implement the change."
            )

        if ChatService._user_requested_new_branch(user_content=user_content):
            return (
                "- For code-change tasks in a git repository, the user explicitly requested a new branch: "
                "create a sensible branch name, switch to it, then implement the change."
            )

        return (
            "- For code-change tasks in a git repository, stay on the current branch by default. "
            "Create or switch branches only when the user explicitly asks."
        )

    @staticmethod
    def _user_requested_new_branch(user_content: str) -> bool:
        lowered = user_content.lower()
        branch_request_patterns = (
            r"\bnew branch\b",
            r"\bseparate branch\b",
            r"\bcreate (?:a )?(?:new )?branch\b",
            r"\bcheckout\s+-b\b",
            r"\bcheck out (?:a )?(?:new )?branch\b",
            r"\bswitch to (?:a )?(?:new )?branch\b",
            r"\bwork on (?:a )?(?:new|separate) branch\b",
            r"\buse (?:a )?(?:new|separate) branch\b",
        )
        return any(re.search(pattern, lowered) for pattern in branch_request_patterns)

    @staticmethod
    def _extract_requested_branch_name(user_content: str) -> str | None:
        branch_name_patterns = (
            r"\bgit checkout -b\s+([A-Za-z0-9._/-]+)",
            r"\bcheckout -b\s+([A-Za-z0-9._/-]+)",
            r"\b(?:create|use|switch to|checkout)\s+(?:the\s+)?branch\s+([A-Za-z0-9._/-]+)",
            r"\bon\s+branch\s+([A-Za-z0-9._/-]+)",
            r"\bbranch\s+(?:named|called)\s+([A-Za-z0-9._/-]+)",
        )
        for pattern in branch_name_patterns:
            match = re.search(pattern, user_content, flags=re.IGNORECASE)
            if not match:
                continue
            branch_name = match.group(1).strip().strip("`'\".,;:()[]{}")
            if branch_name:
                return branch_name
        return None

    def _run_tool_builder_task(
        self,
        session_id: str,
        user_content: str,
        selected_model: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        if self._tool_builder_service is None:
            return "Tool builder workflow is not configured yet. Please check backend startup dependencies.", None

        context = self._resolve_tool_builder_context(session_id)
        if context and context.get("phase") == "modification_clarification":
            base_request_id = str(context.get("requestId") or "").strip()
            rebuild_tool_id = str(context.get("toolId") or "").strip() or None
            tool_name = str(context.get("toolName") or "").strip() or None
            draft_change_request = str(context.get("draftChangeRequest") or "").strip()
            request_state = self._tool_builder_service.get_job_state(base_request_id) if base_request_id else None
            tool = self._tool_builder_service.get_tool(rebuild_tool_id) if rebuild_tool_id else None
            if tool is None and base_request_id:
                tool = self._tool_builder_service.get_tool_for_request(request_id=base_request_id)

            if draft_change_request and base_request_id:
                combined_change_request = (
                    f"{draft_change_request}\n\n"
                    "Additional clarification from user:\n"
                    f"{user_content.strip()}"
                )
                clarification, clarification_result = self._tool_builder_modification_clarification_from_codex(
                    session_id=session_id,
                    change_request=combined_change_request,
                    tool=tool,
                    request_state=request_state,
                    prior_questions=context.get("pendingQuestions"),
                    model=selected_model,
                )
                if clarification:
                    return clarification, {
                        "phase": "modification_clarification",
                        "requestId": base_request_id,
                        "toolId": rebuild_tool_id,
                        "toolName": tool_name,
                        "draftChangeRequest": str(
                            clarification_result.get("clarifiedRequest") if isinstance(clarification_result, dict) else combined_change_request
                        )
                        or combined_change_request,
                        "pendingQuestions": clarification_result.get("questions", []) if isinstance(clarification_result, dict) else [],
                    }

                finalized_change_request = (
                    str(clarification_result.get("clarifiedRequest") or "").strip()
                    if isinstance(clarification_result, dict)
                    else ""
                ) or combined_change_request
                modification_prompt = self._build_tool_modification_prompt(
                    user_content=finalized_change_request,
                    tool=tool,
                    request_state=request_state,
                )
                job = self._tool_builder_service.start_generation(
                    prompt=finalized_change_request,
                    name=tool_name,
                    base_request_id=base_request_id,
                    rebuild_tool_id=rebuild_tool_id,
                    model=selected_model,
                    workflow_prompt=modification_prompt,
                )
                next_context = {
                    "requestId": job["id"],
                    "toolId": rebuild_tool_id,
                    "toolName": tool_name,
                    "phase": "building",
                }
                message = (
                    "Started applying your clarified changes to the existing tool.\n\n"
                    f"- Request ID: `{job['id']}`\n"
                    f"- Tool ID: `{rebuild_tool_id or 'will be resolved after build starts'}`\n"
                    "- I will rebuild and redeploy the tool container once tests and verification pass.\n"
                    "- Ask `status` anytime in this chat to get the latest state and port."
                )
                return message, next_context

        if context and context.get("phase") == "clarification":
            draft_prompt = str(context.get("draftPrompt") or "").strip()
            if draft_prompt:
                combined_request = (
                    f"{draft_prompt}\n\n"
                    "Clarifications from user:\n"
                    f"{user_content.strip()}"
                )
                clarification, clarification_result = self._tool_builder_clarification_from_codex(
                    session_id=session_id,
                    request_text=combined_request,
                    prior_questions=context.get("pendingQuestions"),
                    model=selected_model,
                )
                if clarification:
                    next_context = {
                        "phase": "clarification",
                        "draftPrompt": str(
                            clarification_result.get("clarifiedRequest") if isinstance(clarification_result, dict) else combined_request
                        )
                        or combined_request,
                        "pendingQuestions": clarification_result.get("questions", []) if isinstance(clarification_result, dict) else [],
                    }
                    return clarification, next_context

                finalized_request = (
                    str(clarification_result.get("clarifiedRequest") or "").strip()
                    if isinstance(clarification_result, dict)
                    else ""
                ) or combined_request
                initial_prompt = self._build_tool_initial_prompt(finalized_request)
                job = self._tool_builder_service.start_generation(
                    prompt=finalized_request,
                    name=None,
                    model=selected_model,
                    workflow_prompt=initial_prompt,
                )
                next_context = {"requestId": job["id"], "phase": "building"}
                message = (
                    "Build queued with your clarified requirements.\n\n"
                    f"- Request ID: `{job['id']}`\n"
                    "- Next I’ll turn this into acceptance criteria, write tests first, implement the tool, and verify runtime APIs before publish.\n"
                    "- For live scraping/data tools, I’ll only publish after backend results line up with web verification.\n"
                    "- Ask `status` in this chat anytime to check progress and runtime details."
                )
                return message, next_context

        if context and not context.get("requestId") and not context.get("toolId"):
            context = None
        explicit_tool_id = self._extract_tool_id(user_content)
        if explicit_tool_id:
            referenced_tool = self._tool_builder_service.get_tool(explicit_tool_id)
            if referenced_tool:
                context = {
                    "toolId": referenced_tool["toolId"],
                    "requestId": referenced_tool["requestId"],
                    "toolName": referenced_tool["name"],
                    "phase": "running" if referenced_tool["status"] == "RUNNING" else "building",
                }

        if context:
            request_id = str(context.get("requestId") or "").strip()
            tool_id = str(context.get("toolId") or "").strip() or None
            tool = self._tool_builder_service.get_tool(tool_id) if tool_id else None
            if tool is None and request_id:
                tool = self._tool_builder_service.get_tool_for_request(request_id=request_id)
            if tool:
                context["toolId"] = tool["toolId"]
                context["toolName"] = tool["name"]
                context["requestId"] = tool["requestId"]
                context["phase"] = "running" if tool["status"] == "RUNNING" else "building"

            if self._is_tool_builder_status_query(user_content):
                return self._tool_builder_status_message(context=context), context

            request_state = self._tool_builder_service.get_job_state(request_id) if request_id else None
            if self._is_tool_builder_information_query(user_content):
                answer = self._answer_tool_builder_question(
                    session_id=session_id,
                    user_content=user_content,
                    tool=tool,
                    request_state=request_state,
                    model=selected_model,
                )
                return answer, context

            base_request_id = str(context.get("requestId") or "").strip()
            if not base_request_id:
                return (
                    "I could not find the active tool context for this chat. "
                    "Please mention the tool id explicitly and I will continue from there.",
                    context,
                )

            rebuild_tool_id = str(context.get("toolId") or "").strip() or None
            tool_name = str(context.get("toolName") or "").strip() or None
            clarification, clarification_result = self._tool_builder_modification_clarification_from_codex(
                session_id=session_id,
                change_request=user_content,
                tool=tool,
                request_state=request_state,
                model=selected_model,
            )
            if clarification:
                draft_change_request = (
                    str(clarification_result.get("clarifiedRequest") or "").strip()
                    if isinstance(clarification_result, dict)
                    else ""
                ) or user_content.strip()
                return clarification, {
                    "phase": "modification_clarification",
                    "requestId": base_request_id,
                    "toolId": rebuild_tool_id,
                    "toolName": tool_name,
                    "draftChangeRequest": draft_change_request,
                    "pendingQuestions": clarification_result.get("questions", []) if isinstance(clarification_result, dict) else [],
                }

            finalized_change_request = (
                str(clarification_result.get("clarifiedRequest") or "").strip()
                if isinstance(clarification_result, dict)
                else ""
            ) or user_content
            modification_prompt = self._build_tool_modification_prompt(
                user_content=finalized_change_request,
                tool=tool,
                request_state=request_state,
            )
            job = self._tool_builder_service.start_generation(
                prompt=finalized_change_request,
                name=tool_name,
                base_request_id=base_request_id,
                rebuild_tool_id=rebuild_tool_id,
                model=selected_model,
                workflow_prompt=modification_prompt,
            )
            next_context = {
                "requestId": job["id"],
                "toolId": rebuild_tool_id,
                "toolName": tool_name,
                "phase": "building",
            }
            message = (
                "Started applying your changes to the existing tool.\n\n"
                f"- Request ID: `{job['id']}`\n"
                f"- Tool ID: `{rebuild_tool_id or 'will be resolved after build starts'}`\n"
                "- I will rebuild and redeploy the tool container once tests pass.\n"
                "- Ask `status` anytime in this chat to get the latest state and port."
            )
            return message, next_context

        clarification, clarification_result = self._tool_builder_clarification_from_codex(
            session_id=session_id,
            request_text=user_content,
            model=selected_model,
        )
        if clarification:
            draft_prompt = (
                str(clarification_result.get("clarifiedRequest") or "").strip()
                if isinstance(clarification_result, dict)
                else ""
            ) or user_content.strip()
            return clarification, {
                "phase": "clarification",
                "draftPrompt": draft_prompt,
                "pendingQuestions": clarification_result.get("questions", []) if isinstance(clarification_result, dict) else [],
            }

        finalized_request = (
            str(clarification_result.get("clarifiedRequest") or "").strip()
            if isinstance(clarification_result, dict)
            else ""
        ) or user_content
        initial_prompt = self._build_tool_initial_prompt(finalized_request)
        job = self._tool_builder_service.start_generation(
            prompt=finalized_request,
            name=None,
            model=selected_model,
            workflow_prompt=initial_prompt,
        )
        next_context = {"requestId": job["id"], "phase": "building"}
        message = (
            "Build queued.\n\n"
            f"- Request ID: `{job['id']}`\n"
            "- Next I’ll convert this into acceptance criteria, write tests first, implement the tool, and verify runtime behavior before deployment.\n"
            "- If the backend relies on scraping or live external data, I’ll cross-check sampled results with web verification before publish.\n"
            "- Once deployed, ask `status` in this chat to get the live port and runtime state.\n"
            "- You can keep chatting here to request further changes; I’ll rebuild from the last successful version."
        )
        return message, next_context

    def _resolve_tool_builder_context(self, session_id: str) -> dict[str, Any] | None:
        recent = self._message_repository.list_recent_for_session(session_id=session_id, limit=36)
        for message in reversed(recent):
            metadata = message.get("metadata")
            if not isinstance(metadata, dict):
                continue
            candidate = metadata.get("toolBuilder")
            if isinstance(candidate, dict):
                return dict(candidate)
        return None

    @staticmethod
    def _extract_tool_id(text: str) -> str | None:
        match = re.search(r"\b([a-f0-9]{32})\b", text.lower())
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _needs_tool_builder_clarification(user_content: str) -> bool:
        cleaned = re.sub(r"\s+", " ", user_content).strip().lower()
        token_count = len(re.findall(r"\w+", cleaned))
        if token_count < 6:
            return True
        vague_markers = (
            "build a tool",
            "build tool",
            "make a tool",
            "create a tool",
            "something like",
            "not sure",
            "whatever works",
        )
        return any(marker in cleaned for marker in vague_markers) and token_count < 20

    def _tool_builder_clarification_from_codex(
        self,
        session_id: str,
        request_text: str,
        prior_questions: object | None = None,
        model: str | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        clarification_prompt = self._build_tool_builder_clarification_analysis_prompt(
            request_text=request_text,
            prior_questions=prior_questions,
        )
        result = self._codex_service.run_chat(
            session_id=f"{session_id}-tool-clarify",
            prompt=clarification_prompt,
            model=model,
            timeout_seconds=90,
        )
        if result.success:
            cleaned = self._codex_service.clean_cli_output(result.logs)
            parsed = self._parse_first_json_object(cleaned)
            normalized = self._normalize_tool_builder_clarification_result(parsed)
            if normalized is not None:
                if normalized["needsClarification"] and normalized["questions"]:
                    return self._render_tool_builder_clarification_message(normalized["questions"]), normalized
                return None, normalized

        clarification = self._build_tool_builder_clarification(request_text)
        if clarification:
            fallback_questions = self._collect_tool_builder_ambiguities(request_text)
            return clarification, {
                "needsClarification": True,
                "questions": fallback_questions,
                "clarifiedRequest": request_text.strip(),
                "source": "fallback",
            }
        return None, {
            "needsClarification": False,
            "questions": [],
            "clarifiedRequest": request_text.strip(),
            "source": "fallback",
        }

    def _tool_builder_modification_clarification_from_codex(
        self,
        session_id: str,
        change_request: str,
        tool: dict[str, Any] | None,
        request_state: dict[str, Any] | None,
        prior_questions: object | None = None,
        model: str | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        clarification_prompt = self._build_tool_builder_modification_clarification_analysis_prompt(
            change_request=change_request,
            tool=tool,
            request_state=request_state,
            prior_questions=prior_questions,
        )
        result = self._codex_service.run_chat(
            session_id=f"{session_id}-tool-modify-clarify",
            prompt=clarification_prompt,
            model=model,
            timeout_seconds=90,
        )
        if result.success:
            cleaned = self._codex_service.clean_cli_output(result.logs)
            parsed = self._parse_first_json_object(cleaned)
            normalized = self._normalize_tool_builder_clarification_result(parsed)
            if normalized is not None:
                if normalized["needsClarification"] and normalized["questions"]:
                    return self._render_tool_builder_clarification_message(normalized["questions"]), normalized
                return None, normalized

        return None, {
            "needsClarification": False,
            "questions": [],
            "clarifiedRequest": change_request.strip(),
            "source": "fallback",
        }

    @staticmethod
    def _build_tool_builder_clarification_analysis_prompt(
        request_text: str,
        prior_questions: object | None = None,
    ) -> str:
        prior_lines: list[str] = []
        if isinstance(prior_questions, list):
            cleaned_prior = [str(item).strip() for item in prior_questions if str(item).strip()]
            if cleaned_prior:
                prior_lines.append("Previously asked clarification questions:")
                for question in cleaned_prior[:5]:
                    prior_lines.append(f"- {question}")

        prior_block = "\n".join(prior_lines).strip()
        if prior_block:
            prior_block = f"{prior_block}\n\n"

        return (
            "Analyze this tool-building request and decide if clarification is still required before implementation.\n"
            "Use the full request plus any user follow-up answers already included.\n"
            "Do not repeat questions that are already answered.\n"
            "Infer reasonable defaults when risk is low, but ask concise questions for anything that could materially change implementation.\n"
            "Prefer at most 3 questions.\n"
            "Return strict JSON only in this shape:\n"
            '{"needsClarification": true|false, "questions": ["..."], "clarifiedRequest": "...", "reason": "..."}\n\n'
            f"{prior_block}"
            f"Request and follow-ups:\n{request_text.strip()}"
        )

    @classmethod
    def _build_tool_builder_modification_clarification_analysis_prompt(
        cls,
        change_request: str,
        tool: dict[str, Any] | None,
        request_state: dict[str, Any] | None,
        prior_questions: object | None = None,
    ) -> str:
        prior_lines: list[str] = []
        if isinstance(prior_questions, list):
            cleaned_prior = [str(item).strip() for item in prior_questions if str(item).strip()]
            if cleaned_prior:
                prior_lines.append("Previously asked clarification questions:")
                for question in cleaned_prior[:5]:
                    prior_lines.append(f"- {question}")

        context_lines = ["Existing tool context:"]
        if tool:
            context_lines.append(f"- Tool name: {tool.get('name')}")
            context_lines.append(f"- Tool status: {tool.get('status')}")
        if request_state:
            prompt = cls._summarize_tool_builder_request_text(str(request_state.get("prompt") or ""), limit=1200)
            refined = cls._summarize_tool_builder_request_text(str(request_state.get("refinedPrompt") or ""), limit=1200)
            if prompt:
                context_lines.append(f"Previous request: {prompt}")
            if refined:
                context_lines.append(f"Refined requirements: {refined}")

        prior_block = "\n".join(prior_lines).strip()
        context_block = "\n".join(context_lines).strip()
        if prior_block:
            prior_block = f"{prior_block}\n\n"

        return (
            "Analyze this requested modification to an existing generated tool and decide if clarification is still required before implementation.\n"
            "Use the existing tool context, prior requirements, and the user follow-up answers already included.\n"
            "Do not repeat questions that are already answered.\n"
            "If the change is simple and low-risk, avoid asking unnecessary questions.\n"
            "Prefer at most 3 questions.\n"
            "Return strict JSON only in this shape:\n"
            '{"needsClarification": true|false, "questions": ["..."], "clarifiedRequest": "...", "reason": "..."}\n\n'
            f"{prior_block}"
            f"{context_block}\n\n"
            f"Change request and follow-ups:\n{change_request.strip()}"
        )

    @staticmethod
    def _render_tool_builder_clarification_message(questions: list[str]) -> str:
        lines = ["Before I build this, I need to lock down these details so the generated tool is accurate:"]
        for index, question in enumerate(questions[:4], start=1):
            lines.append(f"{index}. {question}")
        lines.append(
            "Once you reply, I’ll turn that into acceptance criteria, write the tests first, then build and verify the tool before publish."
        )
        return "\n".join(lines)

    @staticmethod
    def _normalize_tool_builder_clarification_result(payload: object) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        questions_raw = payload.get("questions")
        questions = []
        if isinstance(questions_raw, list):
            for item in questions_raw:
                question = str(item).strip()
                if question:
                    questions.append(question)

        clarified_request = str(payload.get("clarifiedRequest") or "").strip()
        needs_clarification = bool(payload.get("needsClarification"))
        if questions and not needs_clarification:
            needs_clarification = True
        return {
            "needsClarification": needs_clarification,
            "questions": questions[:4],
            "clarifiedRequest": clarified_request,
            "reason": str(payload.get("reason") or "").strip(),
            "source": "codex",
        }

    @staticmethod
    def _parse_first_json_object(value: str) -> dict[str, Any] | None:
        text = (value or "").strip()
        if not text:
            return None

        try:
            parsed_direct = json.loads(text)
            return parsed_direct if isinstance(parsed_direct, dict) else None
        except json.JSONDecodeError:
            pass

        for match in re.finditer(r"\{[\s\S]*?\}", text):
            candidate = match.group(0)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @classmethod
    def _build_tool_builder_clarification(cls, user_content: str) -> str | None:
        questions = cls._collect_tool_builder_ambiguities(user_content)
        if not questions:
            return None

        return cls._render_tool_builder_clarification_message(questions)

    @classmethod
    def _collect_tool_builder_ambiguities(cls, user_content: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", user_content).strip()
        lowered = cleaned.lower()
        questions: list[str] = []

        if cls._needs_tool_builder_clarification(user_content):
            questions.append("What is the exact primary user workflow from first input to final output?")

        if cls._request_mentions_live_movie_data(lowered) and not any(token in lowered for token in ("bookmyshow", "district")):
            questions.append("Which source should be treated as authoritative for current show listings: BookMyShow, District, or both with a fallback order?")

        if cls._request_mentions_live_movie_data(lowered) and not any(
            token in lowered
            for token in (
                "newly available",
                "new show",
                "first appears",
                "first available",
                "every run",
                "every match",
                "repeat alert",
                "dedupe",
            )
        ):
            questions.append("When should alerts fire: every polling run with matches, or only when a newly available show appears compared with the previous run?")

        if any(token in lowered for token in ("cron", "interval", "schedule", "poll")) and not any(
            token in lowered for token in ("minute", "minutes", "hour", "hours")
        ):
            questions.append("Should the run interval be configured in minutes, hours, or both, and what minimum interval should be allowed?")

        if "email" in lowered and not any(token in lowered for token in ("brevo", "smtp", "sendinblue")):
            questions.append("Which email provider should the generated tool integrate with for alerts?")

        if any(token in lowered for token in ("bookmyshow", "district")) and "current" not in lowered and "live" not in lowered:
            questions.append("Should the movie dropdown always refresh from live listings before the user saves the alert configuration, or is cached data acceptable?")

        deduped: list[str] = []
        seen: set[str] = set()
        for question in questions:
            normalized = question.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(question)
        return deduped

    @staticmethod
    def _request_mentions_live_movie_data(lowered: str) -> bool:
        movie_markers = ("movie", "movies", "show", "shows", "showtime", "showtimes", "theatre", "theater", "cinema")
        return any(marker in lowered for marker in movie_markers)

    @staticmethod
    def _is_tool_builder_information_query(user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return False

        if ChatService._is_tool_builder_status_query(user_content):
            return True

        action_pattern = re.compile(
            r"\b("
            r"modify|change|update|fix|add|remove|delete|implement|refactor|improve|"
            r"optimi[sz]e|rewrite|build|rebuild|redeploy|deploy|create|generate|"
            r"make|migrate|convert|replace|rename|integrate|configure|setup|set\s+up"
            r")\b"
        )
        if action_pattern.search(lowered):
            return False

        question_prefixes = (
            "what ",
            "what's ",
            "what is ",
            "how ",
            "why ",
            "when ",
            "where ",
            "which ",
            "who ",
            "is ",
            "are ",
            "do ",
            "does ",
            "did ",
            "can ",
            "could ",
            "would ",
            "should ",
            "will ",
            "explain ",
            "describe ",
            "tell me ",
            "help me understand ",
            "walk me through ",
        )
        question_phrases = (
            "how does",
            "what does",
            "how is",
            "why is",
            "where is",
            "is there",
            "does it",
            "can it",
            "which api",
            "which database",
            "what database",
            "what collections",
        )
        compact_info_markers = (
            "architecture",
            "workflow",
            "data model",
            "database",
            "mongodb",
            "collection",
            "collections",
            "api",
            "storage",
            "persist",
            "persistence",
            "how it works",
            "how this works",
        )

        word_count = len(re.findall(r"\w+", lowered))
        if "?" in lowered:
            return True
        if any(lowered.startswith(prefix) for prefix in question_prefixes):
            return True
        if any(phrase in lowered for phrase in question_phrases):
            return True
        return word_count <= 10 and any(marker in lowered for marker in compact_info_markers)

    def _answer_tool_builder_question(
        self,
        session_id: str,
        user_content: str,
        tool: dict[str, Any] | None,
        request_state: dict[str, Any] | None,
        model: str | None = None,
    ) -> str:
        prompt = self._build_tool_builder_question_answer_prompt(
            user_content=user_content,
            tool=tool,
            request_state=request_state,
        )
        result = self._codex_service.run_chat(
            session_id=f"{session_id}-tool-question",
            prompt=prompt,
            model=model,
            timeout_seconds=90,
        )
        if result.success:
            answer = self._build_assistant_text(result.logs, result.success)
            if answer.strip():
                return answer

        return (
            "I couldn’t answer that from the available tool context right now. "
            "Ask `status` for runtime details, or send a change request if you want me to rebuild the tool."
        )

    def _build_tool_builder_question_answer_prompt(
        self,
        user_content: str,
        tool: dict[str, Any] | None,
        request_state: dict[str, Any] | None,
    ) -> str:
        lines = [
            "You are answering a user's question about an existing generated software tool.",
            "Do not start a build, rebuild, or code change.",
            "Do not propose implementation changes unless the user explicitly asks for them.",
            "Answer the user's question directly using the provided tool context and requirements.",
            "If some implementation detail is not known from the provided context, say what is known and note the uncertainty briefly.",
            "Keep the answer concise and practical.",
            "",
            "Existing tool context:",
        ]

        if tool:
            lines.append(f"- Tool ID: {tool.get('toolId')}")
            lines.append(f"- Tool name: {tool.get('name')}")
            lines.append(f"- Tool status: {tool.get('status')}")
            if tool.get("uiPort"):
                lines.append(f"- UI port: {tool.get('uiPort')}")
            ports = tool.get("ports")
            if isinstance(ports, dict) and ports:
                serialized_ports = ", ".join(f"{name}:{port}" for name, port in ports.items())
                lines.append(f"- Service ports: {serialized_ports}")

        if request_state:
            prompt = self._summarize_tool_builder_request_text(str(request_state.get("prompt") or ""), limit=3000)
            refined = self._summarize_tool_builder_request_text(str(request_state.get("refinedPrompt") or ""), limit=3000)
            status = str(request_state.get("status") or "").strip()
            error = self._truncate_prompt_for_context(str(request_state.get("error") or ""), limit=1200)
            if status:
                lines.append(f"- Latest build status: {status}")
            if prompt:
                lines.append(f"\nPrevious request:\n{prompt}")
            if refined:
                lines.append(f"\nRefined requirements:\n{refined}")
            if error:
                lines.append(f"\nLatest error:\n{error}")

        lines.extend(
            [
                "",
                "User question:",
                user_content.strip(),
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _is_tool_builder_status_query(user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return False

        # Keep the modify-chat seed prompt status-only even though it may mention future changes.
        if re.search(
            r"\bfirst,?\s*(?:please\s*)?(?:show|share|check|give|tell)\b.*\b(status|state|running|port|url|link|health)\b",
            lowered,
        ):
            return True

        # Do not short-circuit rebuild requests just because they mention runtime words.
        action_pattern = re.compile(
            r"\b("
            r"modify|change|update|fix|add|remove|delete|implement|refactor|improve|"
            r"optimi[sz]e|rewrite|build|rebuild|redeploy|deploy|create|generate|"
            r"make|migrate|convert|replace|rename|integrate|configure|setup|set\s+up"
            r")\b"
        )

        status_patterns = (
            r"^status\??$",
            r"^(?:what(?:'s| is)|show|share|check|give|tell)\b.*\b(status|state|running|deployed|deployment|port|url|link|health)\b",
            r"^(?:can you|could you|please)\s+(?:show|share|check|give|tell)\b.*\b(status|state|running|deployed|deployment|port|url|link|health)\b",
            r"\bis (?:it|the tool|this) (?:running|deployed|healthy)\b",
            r"\b(current|latest)\s+(status|state|port|url|link)\b",
        )
        explicit_status_intent = any(re.search(pattern, lowered) for pattern in status_patterns)
        if explicit_status_intent and not action_pattern.search(lowered):
            return True

        status_markers = ("status", "running", "port", "url", "link", "health", "deployed", "deployment")
        word_count = len(re.findall(r"\w+", lowered))
        marker_hits = sum(1 for marker in status_markers if marker in lowered)
        if action_pattern.search(lowered):
            return False
        return marker_hits > 0 and word_count <= 5

    def _tool_builder_status_message(self, context: dict[str, Any]) -> str:
        request_id = str(context.get("requestId") or "").strip()
        tool_id = str(context.get("toolId") or "").strip() or None

        job = self._tool_builder_service.get_job_state(request_id) if request_id else None
        tool = self._tool_builder_service.get_tool(tool_id) if tool_id else None
        if tool is None and request_id:
            tool = self._tool_builder_service.get_tool_for_request(request_id=request_id)

        lines = ["Tool Builder status:"]
        if request_id:
            job_status = job["status"] if job else "UNKNOWN"
            lines.append(f"- Latest request: `{request_id}` ({job_status})")
            if job and job.get("error"):
                lines.append(f"- Last error: {job['error']}")
        else:
            lines.append("- Latest request: unknown")

        if tool:
            raw_ui_port = tool.get("uiPort") or tool.get("port")
            ui_port = self._as_int_port(raw_ui_port)
            lines.append(f"- Tool ID: `{tool['toolId']}`")
            lines.append(f"- Tool status: {tool['status']}")
            if ui_port:
                lines.append(f"- UI: {self._build_runtime_url(self._tool_frontend_base_url, ui_port)}")
            ports = tool.get("ports") or {}
            if isinstance(ports, dict) and ports:
                mapped_items: list[str] = []
                for name, port in ports.items():
                    resolved_port = self._as_int_port(port)
                    if resolved_port is None:
                        continue
                    mapped_items.append(
                        f"{name}:{resolved_port} ({self._build_service_runtime_url(name, resolved_port, ui_port)})"
                    )
                if mapped_items:
                    lines.append(f"- Service ports: {' | '.join(mapped_items)}")
        else:
            lines.append("- Tool record: not created yet (build may still be running)")

        return "\n".join(lines)

    @staticmethod
    def _build_tool_initial_prompt(user_content: str) -> str:
        return (
            "Build a production-ready tool based on this request:\n"
            f"{user_content}\n\n"
            "Requirements:\n"
            "- Clarify requirements first. Convert requirements into explicit acceptance criteria and list assumptions before implementation.\n"
            "- Save the clarified requirements in `docs/requirements.md` and save concrete test cases in `docs/test-cases.md` before implementation.\n"
            "- Implement the exact requested workflow; avoid unrelated extra features.\n"
            "- Convert the request into concrete acceptance criteria and satisfy each criterion in code/tests.\n"
            "- Follow TDD: write/update failing tests first, then implement backend changes until tests pass.\n"
            "- Choose a concise, domain-meaningful product name; avoid generic names based on filler words from the prompt.\n"
            "- Include a usable UI unless explicitly backend-only.\n"
            "- Make the UI feel modern and polished.\n"
            "- Default the UI to a dark theme unless the user explicitly requests another theme.\n"
            "- Default all user-facing dates, times, schedules, and cron behavior to IST using the `Asia/Kolkata` timezone unless the user explicitly requests another timezone.\n"
            "- Configure the generated app/runtime to honor `TZ=Asia/Kolkata` by default and keep frontend/backend time handling aligned with that timezone.\n"
            "- Prefer a lightweight UI stack (server-rendered/static HTML + JS) unless a heavier frontend framework is explicitly requested.\n"
            "- Include tests for the core requested behavior (not only health/status endpoints).\n"
            "- Include tests and runnable docker artifacts including docker-compose.\n"
            "- Add a lightweight mock deployment validation step for docker-compose/runtime wiring.\n"
            "- Before launch, verify backend endpoints using real API calls; if scraping/external data is involved, cross-check sampled API output with live web search evidence and fix mismatches before publish.\n"
            "- For live-data tools, do not ship placeholder or stale sample datasets as the primary source of truth.\n"
            "- Keep implementation practical and maintainable."
        )

    def _build_tool_modification_prompt(
        self,
        user_content: str,
        tool: dict[str, Any] | None,
        request_state: dict[str, Any] | None,
    ) -> str:
        tool_lines: list[str] = ["Existing tool context:"]
        if tool:
            tool_lines.append(f"- Tool ID: {tool.get('toolId')}")
            tool_lines.append(f"- Tool name: {tool.get('name')}")
            tool_lines.append(f"- Runtime status: {tool.get('status')}")
            if tool.get("uiPort"):
                tool_lines.append(f"- Current UI port: {tool.get('uiPort')}")
            ports = tool.get("ports")
            if isinstance(ports, dict) and ports:
                serialized_ports = ", ".join(f"{name}:{port}" for name, port in ports.items())
                tool_lines.append(f"- Current service ports: {serialized_ports}")
        else:
            tool_lines.append("- Tool metadata unavailable in chat context; infer from repository workspace.")

        if request_state:
            prior_prompt = self._summarize_tool_builder_request_text(str(request_state.get("prompt") or ""), limit=5000)
            refined_prompt = self._summarize_tool_builder_request_text(str(request_state.get("refinedPrompt") or ""), limit=5000)
            status = str(request_state.get("status") or "UNKNOWN")
            tool_lines.append(f"- Latest build status: {status}")
            if prior_prompt:
                tool_lines.append(f"\nPrevious tool request:\n{prior_prompt}")
            if refined_prompt:
                tool_lines.append(f"\nLatest refined requirements:\n{refined_prompt}")
            if request_state.get("error"):
                error_summary = self._truncate_prompt_for_context(str(request_state["error"]), limit=2000)
                tool_lines.append(f"\nLatest build/runtime error:\n{error_summary}")

        context_block = "\n".join(tool_lines)
        return (
            "Apply the requested change to the existing tool codebase.\n"
            "Inspect the current workspace first, then make targeted updates.\n\n"
            f"{context_block}\n\n"
            f"Change request:\n{user_content}\n\n"
            "Requirements:\n"
            "- Clarify updated requirements and assumptions before changing implementation.\n"
            "- Keep `docs/requirements.md` and `docs/test-cases.md` in sync with the requested change before implementation.\n"
            "- Implement exactly what the user asked in this change request.\n"
            "- Treat this as a focused modification request: make the smallest code change that satisfies it.\n"
            "- Preserve existing working behavior unless this request explicitly changes it.\n"
            "- Keep the UI modern and polished; default to a dark theme unless this change request explicitly asks for another theme.\n"
            "- Keep user-facing dates, times, schedules, and cron behavior on IST using the `Asia/Kolkata` timezone unless this change request explicitly asks for another timezone.\n"
            "- Preserve or add runtime timezone configuration so the tool defaults to `TZ=Asia/Kolkata`.\n"
            "- Do not rewrite existing scraping/data-source logic unless the change request explicitly requires it.\n"
            "- Keep the UI/runtime stack lightweight unless the request explicitly requires a heavier frontend framework.\n"
            "- Follow TDD: update/add tests first for the changed behavior and likely regressions, then implement backend changes.\n"
            "- Keep Docker/runtime compatibility intact, including required health/status checks.\n"
            "- Re-verify runtime APIs after the change; for scraping/live-data flows, compare sampled API results with live web evidence before publish."
        )

    @staticmethod
    def _truncate_prompt_for_context(value: str, limit: int = 5000) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit].rstrip()}\n... [truncated]"

    @classmethod
    def _summarize_tool_builder_request_text(cls, value: str, limit: int = 3000) -> str:
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return ""

        extracted = normalized
        for _ in range(4):
            inner = cls._extract_embedded_tool_builder_request(extracted)
            if not inner or inner == extracted:
                break
            extracted = inner.strip()

        return cls._truncate_prompt_for_context(extracted, limit=limit)

    @staticmethod
    def _extract_embedded_tool_builder_request(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""

        user_request_marker = "\n\nUser request:\n"
        if user_request_marker in normalized:
            return normalized.split(user_request_marker, 1)[1].strip()

        initial_prefix = "Build a production-ready tool based on this request:\n"
        if normalized.startswith(initial_prefix):
            remainder = normalized[len(initial_prefix):]
            if "\n\nRequirements:\n" in remainder:
                return remainder.split("\n\nRequirements:\n", 1)[0].strip()
            return remainder.strip()

        change_marker = "\n\nChange request:\n"
        if change_marker in normalized:
            remainder = normalized.split(change_marker, 1)[1]
            if "\n\nRequirements:\n" in remainder:
                return remainder.split("\n\nRequirements:\n", 1)[0].strip()
            return remainder.strip()

        if normalized.startswith("Change request:\n"):
            remainder = normalized[len("Change request:\n"):]
            if "\n\nRequirements:\n" in remainder:
                return remainder.split("\n\nRequirements:\n", 1)[0].strip()
            return remainder.strip()

        return normalized

    def _recent_operator_session_context(
        self,
        current_session_id: str,
        current_session_messages: list[dict[str, Any]],
    ) -> str:
        # Same-session context is primary; cross-session context is only for fresh chats.
        if len(current_session_messages) > 4:
            return ""

        sessions = self._session_repository.list_recent(limit=16)
        for session in sessions:
            if session.get("id") == current_session_id:
                continue
            if self._normalize_mode(session.get("mode")) != "operator":
                continue

            messages = self._message_repository.list_recent_for_session(session_id=session["id"], limit=8)
            formatted = self._format_operator_conversation_context(messages)
            if not formatted or formatted == "(no prior conversation context)":
                continue
            return f"Session `{session.get('title', 'Operator Session')}`:\n{formatted}"

        return ""

    @staticmethod
    def _format_operator_conversation_context(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "(no prior conversation context)"

        # Keep context bounded so operator prompts remain fast and focused.
        selected = messages[-18:]
        lines: list[str] = []
        for message in selected:
            role = str(message.get("role", "user")).upper()
            content = str(message.get("content", "")).strip()
            if not content:
                continue

            normalized = re.sub(r"\s+", " ", content)
            if len(normalized) > 900:
                normalized = f"{normalized[:900].rstrip()}..."
            lines.append(f"{role}: {normalized}")

        return "\n".join(lines) if lines else "(no prior conversation context)"

    def _auto_name_session(self, session: dict, user_content: str) -> dict:
        if session.get("title") and session["title"].strip().lower() != "new chat":
            return session
        generated_title = self._generate_session_title(user_content)
        updated = self._session_repository.update(session_id=session["id"], title=generated_title)
        return updated if updated is not None else session

    @staticmethod
    def _generate_session_title(user_content: str) -> str:
        cleaned = re.sub(r"\s+", " ", user_content).strip()
        cleaned = re.sub(r"[`*_#>\[\]\(\)]", "", cleaned)
        if not cleaned:
            return "General Chat"

        words = cleaned.split(" ")
        title = " ".join(words[:7]).strip(" .,:;!?-")
        if not title:
            return "General Chat"
        if len(title) > 56:
            title = f"{title[:53].rstrip()}..."
        return title

    @staticmethod
    def _chunk_text(text: str, size: int = 64) -> Iterable[str]:
        if not text:
            return []
        return [text[index:index + size] for index in range(0, len(text), size)]

    @staticmethod
    def _incremental_delta(previous: str, current: str) -> str:
        if not current or current == previous:
            return ""
        if previous and not current.startswith(previous):
            return ""
        return current[len(previous) :]

    @staticmethod
    def _detect_stream_status(log_chunk: str) -> str | None:
        lowered = log_chunk.lower()
        if (
            "searching the web" in lowered
            or "searched:" in lowered
            or "web search" in lowered
            or "🌐" in log_chunk
        ):
            return "web_search"
        return None

    @staticmethod
    def _normalize_mode(mode: str | None) -> str:
        if mode == "pi_operator":
            return "operator"
        if mode in {"general", "tool_builder", "operator"}:
            return mode
        return "general"

    def _resolve_model(self, model: str | None, fallback_to_default: bool) -> str | None:
        cleaned = (model or "").strip()
        if cleaned and self._codex_service.is_supported_chat_model(cleaned):
            return cleaned
        if fallback_to_default:
            return self._codex_service.default_chat_model()
        return None

    def _resolve_message_attachments(
        self,
        session_id: str,
        attachment_ids: list[str],
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not attachment_ids:
            return [], None
        if len(attachment_ids) > MAX_MESSAGE_ATTACHMENTS:
            return [], f"You can attach up to {MAX_MESSAGE_ATTACHMENTS} images per message."

        attachments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for attachment_id in attachment_ids:
            cleaned = (attachment_id or "").strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            resolved = self.get_image_attachment(session_id=session_id, attachment_id=cleaned)
            if not resolved:
                return [], f"Attachment `{cleaned}` was not found."
            attachments.append(
                {
                    "id": resolved["id"],
                    "fileName": resolved["fileName"],
                    "contentType": resolved["contentType"],
                    "size": resolved["size"],
                    "containerPath": resolved["containerPath"],
                    "url": resolved["url"],
                }
            )
        return attachments, None

    @staticmethod
    def _attachment_prompt_context(metadata: dict[str, Any]) -> str:
        raw_attachments = metadata.get("attachments")
        if not isinstance(raw_attachments, list) or not raw_attachments:
            return ""

        details: list[str] = []
        for item in raw_attachments:
            if not isinstance(item, dict):
                continue
            file_name = str(item.get("fileName") or "image")
            container_path = str(item.get("containerPath") or "")
            if container_path:
                details.append(f"{file_name} ({container_path})")
            else:
                details.append(file_name)
        if not details:
            return ""
        return "Attached image files: " + "; ".join(details)

    @staticmethod
    def _extract_project_hint(user_content: str) -> str | None:
        path_match = re.search(r"(/[\w.\-~/]+(?:/[\w.\-~]+)*)", user_content)
        if path_match:
            return path_match.group(1)

        name_match = re.search(
            r"(?:for|of|in)\s+([a-zA-Z0-9._-]+)",
            user_content,
            flags=re.IGNORECASE,
        )
        if name_match:
            return name_match.group(1)
        return None

    @staticmethod
    def _bytes_to_gb(value: int) -> str:
        return f"{(value / (1024 ** 3)):.2f}"

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    @staticmethod
    def _normalize_runtime_base_url(value: str | None) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            return "http://localhost"
        if "://" not in cleaned:
            cleaned = f"http://{cleaned}"
        return cleaned.rstrip("/")

    @staticmethod
    def _build_runtime_url(base_url: str, port: int) -> str:
        return f"{base_url}:{port}"

    @staticmethod
    def _as_int_port(value: Any) -> int | None:
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            return None
        return resolved if resolved > 0 else None

    @classmethod
    def _service_prefers_frontend(cls, service_name: str) -> bool | None:
        lowered = service_name.lower()
        if any(token in lowered for token in cls._BACKEND_SERVICE_HINTS):
            return False
        if any(token in lowered for token in cls._FRONTEND_SERVICE_HINTS):
            return True
        return None

    def _build_service_runtime_url(self, service_name: str, port: int, ui_port: int | None) -> str:
        preferred = self._service_prefers_frontend(service_name)
        if preferred is True:
            base_url = self._tool_frontend_base_url
        elif preferred is False:
            base_url = self._tool_backend_base_url
        elif ui_port is not None and port == ui_port:
            base_url = self._tool_frontend_base_url
        else:
            base_url = self._tool_backend_base_url
        return self._build_runtime_url(base_url=base_url, port=port)

    def _extract_assistant_text(self, raw_logs: str) -> str:
        normalized = raw_logs.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"\x1b\[[0-9;]*m", "", normalized)
        lines = [line.rstrip() for line in normalized.split("\n")]

        json_extracted = self._extract_from_json_events(lines)
        if json_extracted:
            return json_extracted

        codex_index = self._find_codex_marker(lines)
        if codex_index is not None:
            extracted = self._extract_after_codex(lines[codex_index + 1 :])
            if extracted:
                return extracted

        filtered_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if filtered_lines and filtered_lines[-1] != "":
                    filtered_lines.append("")
                continue
            if self._is_noise_line(stripped):
                continue
            if re.fullmatch(r"[\d,]+", stripped):
                continue
            filtered_lines.append(stripped)

        text = "\n".join(filtered_lines).strip()
        return text

    @staticmethod
    def _extract_from_json_events(lines: list[str]) -> str | None:
        latest_text: str | None = None
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("{") or not stripped.endswith("}"):
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if payload.get("type") != "item.completed":
                continue
            item = payload.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") != "agent_message":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                latest_text = text.strip()
        return latest_text

    @staticmethod
    def _is_noise_line(stripped: str) -> bool:
        lowered = stripped.lower()
        return (
            lowered == "codex"
            or lowered.startswith("openai codex")
            or stripped == "--------"
            or lowered.startswith("workdir:")
            or lowered.startswith("model:")
            or lowered.startswith("provider:")
            or lowered.startswith("approval:")
            or lowered.startswith("sandbox:")
            or lowered.startswith("reasoning effort:")
            or lowered.startswith("reasoning summaries:")
            or lowered.startswith("session id:")
            or lowered == "user"
            or lowered.startswith("user:")
            or lowered.startswith("warning:")
            or lowered.startswith("mcp startup:")
            or lowered == "tokens used"
            or lowered.startswith("tokens used")
            or stripped.startswith("🌐")
            or lowered.startswith("searching the web")
            or lowered.startswith("searched:")
            or lowered.startswith("conversation:")
            or lowered.startswith("respond as assistant")
        )

    @staticmethod
    def _find_codex_marker(lines: list[str]) -> int | None:
        for index in range(len(lines) - 1, -1, -1):
            if lines[index].strip().lower() == "codex":
                return index
        return None

    def _extract_after_codex(self, lines: list[str]) -> str:
        captured: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and self._is_noise_line(stripped):
                if stripped.lower().startswith("tokens used"):
                    break
                continue
            lowered = stripped.lower()
            if lowered == "tokens used":
                break
            if lowered.startswith("tokens used"):
                break
            if not stripped:
                if captured and captured[-1] != "":
                    captured.append("")
                continue
            captured.append(line.rstrip())

        return "\n".join(captured).strip()

    @staticmethod
    def _system_prompt_for_mode(mode: str) -> str:
        if mode == "operator":
            return (
                "You are an expert host operations assistant. "
                "Give accurate diagnostics guidance, be explicit about assumptions, "
                "and never claim actions were executed unless results are provided."
            )
        if mode == "tool_builder":
            return (
                "You are an expert software tool builder. "
                "Provide implementation-focused guidance, testing steps, and practical tradeoffs."
            )
        return (
            "You are a helpful engineering assistant for a self-hosted AI tool platform. "
            "Be precise, practical, and honest about uncertainty. "
            "Answer directly with final results and avoid narrating intermediate checks."
        )
