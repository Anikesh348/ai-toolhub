import json
import mimetypes
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote_plus, urlsplit, urlunsplit

import requests
from app.repositories.chat_execution_log_repository import ChatExecutionLogRepository
from app.repositories.chat_message_repository import ChatMessageRepository
from app.repositories.chat_session_repository import ChatSessionRepository
from app.services.browser_screenshot_service import BrowserScreenshotService
from app.services.codex_service import CodexService
from app.services.docker_service import DockerService
from app.services.memory_service import MemoryService
from app.services.operator_access_service import OperatorAccessService
from app.services.system_context_service import SystemContextService
from app.services.tool_builder_service import ToolBuilderService
from app.utils.logger import get_logger
from app.utils.time import now_ist

CHAT_MODES = {"general", "tool_builder", "operator", "pi_operator"}
MAX_IMAGE_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_ATTACHMENTS = 4


class ChatService:
    _FRONTEND_SERVICE_HINTS = ("frontend", "web", "ui", "client", "dashboard", "site", "next", "vite")
    _BACKEND_SERVICE_HINTS = ("backend", "api", "server", "worker", "gateway", "graphql", "rest")
    _FAST_OPERATOR_MODEL_CANDIDATES = ("gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2-codex", "gpt-5.1-codex-mini")
    _CHAT_EXECUTION_LOG_LIMIT = 200_000
    _TOKEN_SOURCE_PARSED = "parsed"
    _TOKEN_SOURCE_ESTIMATED = "estimated"
    _TOKEN_SOURCE_MIXED = "mixed"
    _ESTIMATED_USD_PER_1M_TOKENS = 2.0
    _ESTIMATED_USD_TO_INR_RATE = 83.0
    _USD_INR_SOURCE_LIVE_API = "live_api"
    _USD_INR_SOURCE_LIVE_CACHE = "live_cache"
    _USD_INR_SOURCE_CACHE_STALE = "cache_stale"
    _USD_INR_SOURCE_FALLBACK_DEFAULT = "fallback_default"
    _MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\n]+)\)")
    _GENERAL_IMAGE_ACTION_MARKERS = (
        "generate",
        "create",
        "make",
        "draw",
        "render",
        "illustrate",
        "paint",
    )
    _GENERAL_IMAGE_TARGET_MARKERS = (
        "image",
        "photo",
        "picture",
        "portrait",
        "illustration",
        "artwork",
        "wallpaper",
    )
    _GENERAL_SCREENSHOT_ACTION_MARKERS = (
        "screenshot",
        "screen shot",
        "snapshot",
        "capture",
    )
    _GENERAL_SCREENSHOT_TARGET_MARKERS = (
        "website",
        "web site",
        "webpage",
        "web page",
        "url",
        "page",
        "site",
        "link",
    )
    _GENERAL_BROWSER_RECORDING_ACTION_MARKERS = (
        "record",
        "recording",
        "screen recording",
        "capture video",
    )
    _GENERAL_BROWSER_RECORDING_TARGET_MARKERS = (
        "video",
        "browser session",
        "screen recording",
        "recording",
    )
    _BROWSER_RECORDING_DURATION_RE = re.compile(
        r"\b(\d{1,3})\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
        flags=re.IGNORECASE,
    )
    _EXPLICIT_URL_RE = re.compile(r"(https?://[^\s<>()\"']+)", flags=re.IGNORECASE)
    _DOMAIN_URL_RE = re.compile(
        r"(?<!@)\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?:/[^\s<>()\"']*)?)",
        flags=re.IGNORECASE,
    )
    _SCREENSHOT_DIMENSION_RE = re.compile(r"\b(\d{3,4})\s*[xX]\s*(\d{3,4})\b")
    _SCREENSHOT_SS_TOKEN_RE = re.compile(r"\bss\b", flags=re.IGNORECASE)
    _SCREENSHOT_SS_REQUEST_RE = re.compile(
        r"\b(?:give|send|show|take|get|capture|grab|provide|return|share)\s+"
        r"(?:me\s+)?(?:the\s+|a\s+|an\s+)?ss\b"
        r"|\bss\b\s+(?:of|for|from)\b",
        flags=re.IGNORECASE,
    )
    _SCREENSHOT_SITE_SEARCH_PATTERNS = (
        (
            re.compile(
                r"\bsearch(?:\s+for)?\s+((?:www\.)?(?:amazon|flipkart|google|youtube|brave)(?:\.com)?)"
                r"\s+(?:for|about)\s+(.+)",
                flags=re.IGNORECASE,
            ),
            "site_query",
        ),
        (
            re.compile(
                r"\bsearch(?:\s+for)?\s+(.+?)\s+on\s+((?:www\.)?(?:amazon|flipkart|google|youtube|brave)(?:\.com)?)"
                r"(?:\s+(?:image|images)\s+search)?\b",
                flags=re.IGNORECASE,
            ),
            "query_site",
        ),
        (
            re.compile(
                r"\bon\s+((?:www\.)?(?:amazon|flipkart|google|youtube|brave)(?:\.com)?)"
                r"(?:\s+(?:image|images)\s+search)?\s+search(?:\s+for)?\s+(.+)",
                flags=re.IGNORECASE,
            ),
            "site_query",
        ),
    )
    _SCREENSHOT_SITE_SEARCH_BASE_URLS = {
        "amazon": "https://www.amazon.com/s?k={query}",
        "flipkart": "https://www.flipkart.com/search?q={query}",
        "google": "https://www.google.com/search?q={query}",
        "youtube": "https://www.youtube.com/results?search_query={query}",
        "brave": "https://search.brave.com/search?q={query}",
    }
    _SCREENSHOT_SITE_IMAGE_SEARCH_BASE_URLS = {
        "google": "https://www.google.com/search?tbm=isch&q={query}",
        "brave": "https://search.brave.com/images?q={query}",
    }
    _SCREENSHOT_SITE_HOME_URLS = {
        "amazon": "https://www.amazon.com/",
        "flipkart": "https://www.flipkart.com/",
        "google": "https://www.google.com/",
        "youtube": "https://www.youtube.com/",
        "brave": "https://search.brave.com/",
    }

    def __init__(
        self,
        session_repository: ChatSessionRepository,
        message_repository: ChatMessageRepository,
        codex_service: CodexService,
        browser_screenshot_service: BrowserScreenshotService | None = None,
        docker_service: DockerService | None = None,
        operator_access_service: OperatorAccessService | None = None,
        system_context_service: SystemContextService | None = None,
        tool_builder_service: ToolBuilderService | None = None,
        chat_execution_log_repository: ChatExecutionLogRepository | None = None,
        tool_frontend_base_url: str = "http://localhost",
        tool_backend_base_url: str = "http://localhost",
        memory_service: MemoryService | None = None,
        usd_inr_rate_api_url: str = "https://open.er-api.com/v6/latest/USD",
        usd_inr_rate_timeout_seconds: float = 4.0,
        usd_inr_rate_cache_ttl_seconds: int = 1800,
        usd_inr_rate_fallback: float = _ESTIMATED_USD_TO_INR_RATE,
    ) -> None:
        self._session_repository = session_repository
        self._message_repository = message_repository
        self._codex_service = codex_service
        self._browser_screenshot_service = browser_screenshot_service
        self._docker_service = docker_service
        self._operator_access_service = operator_access_service
        self._system_context_service = system_context_service
        self._tool_builder_service = tool_builder_service
        self._chat_execution_log_repository = chat_execution_log_repository
        self._memory_service = memory_service
        self._tool_frontend_base_url = self._normalize_runtime_base_url(tool_frontend_base_url)
        self._tool_backend_base_url = self._normalize_runtime_base_url(tool_backend_base_url)
        self._usd_inr_rate_api_url = (usd_inr_rate_api_url or "").strip()
        self._usd_inr_rate_timeout_seconds = max(float(usd_inr_rate_timeout_seconds or 0), 0.5)
        self._usd_inr_rate_cache_ttl_seconds = max(int(usd_inr_rate_cache_ttl_seconds or 0), 30)
        self._usd_inr_rate_fallback = max(float(usd_inr_rate_fallback or self._ESTIMATED_USD_TO_INR_RATE), 0.01)
        self._logger = get_logger(__name__)
        self._cancelled_stream_sessions: set[str] = set()
        self._cancelled_stream_lock = threading.Lock()
        self._usd_inr_rate_lock = threading.Lock()
        self._usd_inr_rate_cached_value: float | None = None
        self._usd_inr_rate_cached_at: datetime | None = None

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

        normalized_mode = self._normalize_mode(session["mode"])
        self._update_memory_from_user_message(content=content, mode=normalized_mode)
        recent_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=18)
        selected_model = (
            self._resolve_model(model=model, fallback_to_default=False)
            or self._resolve_model(model=session.get("model"), fallback_to_default=False)
            or self._resolve_model(model=None, fallback_to_default=True)
        )
        image_paths = [str(item.get("containerPath")) for item in attachments if item.get("containerPath")]
        prompt = self._build_chat_prompt(
            mode=normalized_mode,
            messages=recent_messages,
            memory_context=self._memory_prompt_context(),
        )
        quick_answer = None
        if self._should_use_operator_quick_reply(mode=normalized_mode, user_content=content):
            quick_answer = self._try_direct_operator_answer(
                mode=normalized_mode,
                user_content=content,
            )
        assistant_metadata: dict[str, Any] = {}
        raw_execution_logs = ""
        quick_path = quick_answer is not None
        try:
            if quick_answer is not None:
                assistant_text = quick_answer
                success = True
                exit_code = 0
            elif self._should_route_to_browser_recording(mode=normalized_mode, user_content=content):
                recording_result = self._run_general_browser_recording_task(
                    session_id=session_id,
                    user_content=content,
                )
                assistant_text = str(recording_result.get("assistantText") or "")
                success = bool(recording_result.get("success", False))
                exit_code = int(recording_result.get("exitCode", 1))
                raw_execution_logs = str(recording_result.get("rawLogs") or "")
            elif self._should_route_to_browser_screenshot(mode=normalized_mode, user_content=content):
                screenshot_result = self._run_general_browser_screenshot_task(
                    session_id=session_id,
                    user_content=content,
                )
                assistant_text = str(screenshot_result.get("assistantText") or "")
                success = bool(screenshot_result.get("success", False))
                exit_code = int(screenshot_result.get("exitCode", 1))
                raw_execution_logs = str(screenshot_result.get("rawLogs") or "")
            elif normalized_mode == "general" and self._is_general_image_generation_request(content):
                image_result = self._run_general_image_task(
                    session_id=session_id,
                    user_content=content,
                    selected_model=selected_model,
                )
                assistant_text = str(image_result.get("assistantText") or "")
                success = bool(image_result.get("success", False))
                exit_code = int(image_result.get("exitCode", 1))
                raw_execution_logs = str(image_result.get("rawLogs") or "")
            elif normalized_mode == "operator":
                operator_result = self._run_operator_task(
                    session_id=session_id,
                    user_content=content,
                    selected_model=selected_model,
                )
                assistant_text = str(operator_result.get("assistantText") or "")
                success = bool(operator_result.get("success", False))
                exit_code = int(operator_result.get("exitCode", 1))
                raw_execution_logs = str(operator_result.get("rawLogs") or "")
            elif normalized_mode == "tool_builder":
                assistant_text, tool_builder_context = self._run_tool_builder_task(
                    session_id=session_id,
                    user_content=content,
                    selected_model=selected_model,
                    attachments=attachments,
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
                raw_execution_logs = completion.logs
                if not completion.success:
                    self._logger.warning(
                        "Chat completion failed for session %s: exit=%s",
                        session_id,
                        completion.exit_code,
                    )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Chat execution failed for session %s", session_id)
            assistant_text = "I could not generate a response right now. Please retry."
            success = False
            exit_code = 1
            raw_execution_logs = self._exception_execution_logs("Chat execution failed", exc)

        assistant_text, _generated_attachments = self._hydrate_assistant_generated_images(
            session_id=session_id,
            assistant_text=assistant_text,
        )
        assistant_metadata["success"] = success
        assistant_metadata["exitCode"] = exit_code
        assistant_message = self._message_repository.create(
            session_id=session_id,
            role="assistant",
            content=assistant_text,
            metadata=assistant_metadata,
        )
        self._record_chat_execution_log(
            session_id=session_id,
            mode=normalized_mode,
            model=selected_model,
            user_message_id=str(user_message.get("id") or ""),
            assistant_message_id=str(assistant_message.get("id") or ""),
            user_content=content.strip(),
            assistant_content=assistant_text,
            raw_logs=raw_execution_logs,
            success=success,
            exit_code=exit_code,
            quick_path=quick_path,
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

        normalized_mode = self._normalize_mode(session["mode"])
        self._update_memory_from_user_message(content=user_content, mode=normalized_mode)
        recent_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=18)
        selected_model = (
            self._resolve_model(model=model, fallback_to_default=False)
            or self._resolve_model(model=session.get("model"), fallback_to_default=False)
            or self._resolve_model(model=None, fallback_to_default=True)
        )
        image_paths = [str(item.get("containerPath")) for item in attachments if item.get("containerPath")]
        prompt = self._build_chat_prompt(
            mode=normalized_mode,
            messages=recent_messages,
            memory_context=self._memory_prompt_context(),
        )
        quick_answer = None
        if self._should_use_operator_quick_reply(mode=normalized_mode, user_content=user_content):
            quick_answer = self._try_direct_operator_answer(
                mode=normalized_mode,
                user_content=user_content,
            )
        streamed_assistant = ""
        stream_status = "thinking"
        assistant_metadata: dict[str, Any] = {}
        raw_execution_logs = ""
        quick_path = quick_answer is not None
        raw_stream_logs = ""
        try:
            if quick_answer is not None:
                assistant_text = quick_answer
                success = True
                exit_code = 0
            elif self._should_route_to_browser_recording(mode=normalized_mode, user_content=user_content):
                recording_result = self._run_general_browser_recording_task(
                    session_id=session_id,
                    user_content=user_content,
                )
                assistant_text = str(recording_result.get("assistantText") or "")
                success = bool(recording_result.get("success", False))
                exit_code = int(recording_result.get("exitCode", 1))
                raw_execution_logs = str(recording_result.get("rawLogs") or "")
            elif self._should_route_to_browser_screenshot(mode=normalized_mode, user_content=user_content):
                screenshot_result = self._run_general_browser_screenshot_task(
                    session_id=session_id,
                    user_content=user_content,
                )
                assistant_text = str(screenshot_result.get("assistantText") or "")
                success = bool(screenshot_result.get("success", False))
                exit_code = int(screenshot_result.get("exitCode", 1))
                raw_execution_logs = str(screenshot_result.get("rawLogs") or "")
            elif normalized_mode == "general" and self._is_general_image_generation_request(user_content):
                image_result = self._run_general_image_task(
                    session_id=session_id,
                    user_content=user_content,
                    selected_model=selected_model,
                )
                assistant_text = str(image_result.get("assistantText") or "")
                success = bool(image_result.get("success", False))
                exit_code = int(image_result.get("exitCode", 1))
                raw_execution_logs = str(image_result.get("rawLogs") or "")
            elif normalized_mode == "operator":
                operator_result = self._run_operator_task(
                    session_id=session_id,
                    user_content=user_content,
                    selected_model=selected_model,
                )
                assistant_text = str(operator_result.get("assistantText") or "")
                success = bool(operator_result.get("success", False))
                exit_code = int(operator_result.get("exitCode", 1))
                raw_execution_logs = str(operator_result.get("rawLogs") or "")
            elif normalized_mode == "tool_builder":
                assistant_text, tool_builder_context = self._run_tool_builder_task(
                    session_id=session_id,
                    user_content=user_content,
                    selected_model=selected_model,
                    attachments=attachments,
                )
                success = True
                exit_code = 0
                if tool_builder_context:
                    assistant_metadata["toolBuilder"] = tool_builder_context
            else:
                completion = None
                for stream_event in self._codex_service.stream_chat(
                    session_id=session_id,
                    prompt=prompt,
                    model=selected_model,
                    image_paths=image_paths,
                ):
                    if stream_event.type == "log" and stream_event.chunk:
                        raw_stream_logs = f"{raw_stream_logs}{stream_event.chunk}"
                        if len(raw_stream_logs) > 24000:
                            raw_stream_logs = raw_stream_logs[-24000:]
                        detected_status = self._detect_stream_status(stream_event.chunk)
                        if detected_status and detected_status != stream_status:
                            stream_status = detected_status
                            yield {"type": "status", "status": stream_status}
                        parsed_partial = self._sanitize_assistant_output(self._extract_assistant_text(raw_stream_logs))
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
                    raw_execution_logs = raw_stream_logs
                else:
                    assistant_text = self._build_assistant_text(completion.logs, completion.success)
                    success = completion.success
                    exit_code = completion.exit_code
                    raw_execution_logs = completion.logs
                stream_cancelled = self._consume_stream_cancelled(session_id)
                if stream_cancelled:
                    if not assistant_text.strip():
                        assistant_text = "Generation stopped."
                    success = False
                    exit_code = 130
                    assistant_metadata["stopped"] = True
                    if raw_execution_logs:
                        raw_execution_logs = f"{raw_execution_logs}\n\n[stream cancelled by user]"
                    else:
                        raw_execution_logs = "[stream cancelled by user]"
                if completion is not None and not completion.success:
                    self._logger.warning(
                        "Chat stream completion failed for session %s: exit=%s",
                        session_id,
                        completion.exit_code,
                    )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Chat stream execution failed for session %s", session_id)
            assistant_text = "I could not generate a response right now. Please retry."
            success = False
            exit_code = 1
            failure_logs = self._exception_execution_logs("Chat stream execution failed", exc)
            if raw_stream_logs:
                raw_execution_logs = f"{raw_stream_logs}\n\n{failure_logs}"
            else:
                raw_execution_logs = failure_logs

        assistant_text, _generated_attachments = self._hydrate_assistant_generated_images(
            session_id=session_id,
            assistant_text=assistant_text,
        )
        assistant_metadata["success"] = success
        assistant_metadata["exitCode"] = exit_code
        assistant_message = self._message_repository.create(
            session_id=session_id,
            role="assistant",
            content=assistant_text,
            metadata=assistant_metadata,
        )
        self._record_chat_execution_log(
            session_id=session_id,
            mode=normalized_mode,
            model=selected_model,
            user_message_id=str(user_message.get("id") or ""),
            assistant_message_id=str(assistant_message.get("id") or ""),
            user_content=user_content,
            assistant_content=assistant_text,
            raw_logs=raw_execution_logs,
            success=success,
            exit_code=exit_code,
            quick_path=quick_path,
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

    def list_execution_logs(
        self,
        limit: int = 200,
        session_id: str | None = None,
        modes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self._chat_execution_log_repository is None:
            return []

        normalized_modes = [
            normalized
            for normalized in {
                self._normalize_mode(mode)
                for mode in (modes or [])
                if (mode or "").strip()
            }
            if normalized in {"general", "operator", "tool_builder"}
        ]
        return self._chat_execution_log_repository.list_recent(
            limit=max(int(limit), 1),
            session_id=(session_id or "").strip() or None,
            modes=normalized_modes or None,
        )

    def summarize_usage(
        self,
        session_id: str | None = None,
        modes: list[str] | None = None,
    ) -> dict[str, Any]:
        if self._chat_execution_log_repository is None:
            return {
                "requestCount": 0,
                "promptTokens": 0,
                "completionTokens": 0,
                "totalTokens": 0,
                "parsedCount": 0,
                "estimatedCount": 0,
                "mixedCount": 0,
                "modes": [],
                "costEstimate": self._usage_cost_estimate(total_tokens=0),
            }

        normalized_modes = [
            normalized
            for normalized in {
                self._normalize_mode(mode)
                for mode in (modes or [])
                if (mode or "").strip()
            }
            if normalized in {"general", "operator", "tool_builder"}
        ]
        summary = self._chat_execution_log_repository.summarize_usage(
            session_id=(session_id or "").strip() or None,
            modes=normalized_modes or None,
        )
        totals = summary.get("totals") if isinstance(summary, dict) else None
        if not isinstance(totals, dict):
            totals = {}
        mode_rows = summary.get("modes") if isinstance(summary, dict) else []
        normalized_rows: list[dict[str, Any]] = []
        if isinstance(mode_rows, list):
            for raw_row in mode_rows:
                if not isinstance(raw_row, dict):
                    continue
                normalized_rows.append(
                    {
                        "mode": self._normalize_mode(str(raw_row.get("mode") or "general")),
                        "requestCount": int(raw_row.get("requests") or 0),
                        "promptTokens": int(raw_row.get("promptTokens") or 0),
                        "completionTokens": int(raw_row.get("completionTokens") or 0),
                        "totalTokens": int(raw_row.get("totalTokens") or 0),
                        "parsedCount": int(raw_row.get("parsedCount") or 0),
                        "estimatedCount": int(raw_row.get("estimatedCount") or 0),
                        "mixedCount": int(raw_row.get("mixedCount") or 0),
                    }
                )

        include_tool_builder_runtime_usage = (
            not (session_id or "").strip()
            and (not normalized_modes or "tool_builder" in normalized_modes)
        )
        if include_tool_builder_runtime_usage:
            runtime_usage = self._summarize_tool_builder_runtime_usage()
            if runtime_usage["requestCount"] > 0 or runtime_usage["totalTokens"] > 0:
                tool_builder_row = next((row for row in normalized_rows if row.get("mode") == "tool_builder"), None)
                if tool_builder_row is None:
                    tool_builder_row = {
                        "mode": "tool_builder",
                        "requestCount": 0,
                        "promptTokens": 0,
                        "completionTokens": 0,
                        "totalTokens": 0,
                        "parsedCount": 0,
                        "estimatedCount": 0,
                        "mixedCount": 0,
                    }
                    normalized_rows.append(tool_builder_row)

                for key in (
                    "requestCount",
                    "promptTokens",
                    "completionTokens",
                    "totalTokens",
                    "parsedCount",
                    "estimatedCount",
                    "mixedCount",
                ):
                    value = int(runtime_usage.get(key) or 0)
                    tool_builder_row[key] += value
                    total_key = "requests" if key == "requestCount" else key
                    totals[total_key] = int(totals.get(total_key) or 0) + value

        request_count = int(totals.get("requests") or 0)
        prompt_tokens = int(totals.get("promptTokens") or 0)
        completion_tokens = int(totals.get("completionTokens") or 0)
        total_tokens = int(totals.get("totalTokens") or 0)
        parsed_count = int(totals.get("parsedCount") or 0)
        estimated_count = int(totals.get("estimatedCount") or 0)
        mixed_count = int(totals.get("mixedCount") or 0)

        return {
            "requestCount": request_count,
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
            "parsedCount": parsed_count,
            "estimatedCount": estimated_count,
            "mixedCount": mixed_count,
            "modes": normalized_rows,
            "costEstimate": self._usage_cost_estimate(total_tokens=total_tokens),
        }

    def _summarize_tool_builder_runtime_usage(self) -> dict[str, int]:
        empty = {
            "requestCount": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "parsedCount": 0,
            "estimatedCount": 0,
            "mixedCount": 0,
        }
        if self._tool_builder_service is None:
            return empty

        summarize = getattr(self._tool_builder_service, "summarize_token_usage", None)
        if not callable(summarize):
            return empty

        try:
            raw = summarize()
        except Exception:  # pylint: disable=broad-except
            self._logger.exception("Unable to summarize tool-builder token usage.")
            return empty
        if not isinstance(raw, dict):
            return empty

        return {
            "requestCount": int(raw.get("requestCount") or 0),
            "promptTokens": int(raw.get("promptTokens") or 0),
            "completionTokens": int(raw.get("completionTokens") or 0),
            "totalTokens": int(raw.get("totalTokens") or 0),
            "parsedCount": int(raw.get("parsedCount") or 0),
            "estimatedCount": int(raw.get("estimatedCount") or 0),
            "mixedCount": int(raw.get("mixedCount") or 0),
        }

    @staticmethod
    def _should_capture_execution_logs(mode: str) -> bool:
        return mode in {"general", "operator", "tool_builder"}

    @classmethod
    def _truncate_chat_execution_logs(cls, raw_logs: str) -> str:
        normalized = (raw_logs or "").replace("\r\n", "\n").replace("\r", "\n")
        if len(normalized) <= cls._CHAT_EXECUTION_LOG_LIMIT:
            return normalized
        truncated = normalized[-cls._CHAT_EXECUTION_LOG_LIMIT:]
        return f"[truncated to last {cls._CHAT_EXECUTION_LOG_LIMIT} chars]\n{truncated}"

    @staticmethod
    def _exception_execution_logs(context: str, exc: Exception) -> str:
        message = f"{context}: {exc}".strip()
        stack = traceback.format_exc().strip()
        if not stack:
            return message
        return f"{message}\n\n{stack}"

    def _usage_cost_estimate(self, total_tokens: int) -> dict[str, Any]:
        resolved_total = max(int(total_tokens), 0)
        quote = self._resolve_usd_inr_rate_quote()
        usd_to_inr_rate = float(quote["rate"])
        usd = round((resolved_total / 1_000_000) * self._ESTIMATED_USD_PER_1M_TOKENS, 6)
        inr = round(usd * usd_to_inr_rate, 4)
        note = self._usage_cost_note_from_quote(
            source=str(quote["source"]),
            fetched_at=quote.get("updatedAt"),
        )
        return {
            "usd": usd,
            "inr": inr,
            "usdPerMillionTokens": self._ESTIMATED_USD_PER_1M_TOKENS,
            "usdToInrRate": usd_to_inr_rate,
            "usdToInrSource": str(quote["source"]),
            "usdToInrLive": bool(quote["live"]),
            "usdToInrUpdatedAt": quote.get("updatedAt"),
            "note": note,
        }

    def _resolve_usd_inr_rate_quote(self) -> dict[str, Any]:
        now = now_ist()
        with self._usd_inr_rate_lock:
            cached_value = self._usd_inr_rate_cached_value
            cached_at = self._usd_inr_rate_cached_at

        if cached_value is not None and cached_at is not None:
            age_seconds = (now - cached_at).total_seconds()
            if age_seconds <= self._usd_inr_rate_cache_ttl_seconds:
                return {
                    "rate": cached_value,
                    "source": self._USD_INR_SOURCE_LIVE_CACHE,
                    "live": True,
                    "updatedAt": cached_at,
                }

        if self._usd_inr_rate_api_url:
            try:
                response = requests.get(
                    self._usd_inr_rate_api_url,
                    timeout=self._usd_inr_rate_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                resolved_rate = self._extract_usd_inr_rate(payload)
                if resolved_rate is not None:
                    fetched_at = now_ist()
                    with self._usd_inr_rate_lock:
                        self._usd_inr_rate_cached_value = resolved_rate
                        self._usd_inr_rate_cached_at = fetched_at
                    return {
                        "rate": resolved_rate,
                        "source": self._USD_INR_SOURCE_LIVE_API,
                        "live": True,
                        "updatedAt": fetched_at,
                    }
                self._logger.warning("USD/INR rate payload did not include a valid INR quote.")
            except Exception as exc:  # pylint: disable=broad-except
                self._logger.warning("Unable to fetch USD/INR rate: %s", exc)

        if cached_value is not None:
            return {
                "rate": cached_value,
                "source": self._USD_INR_SOURCE_CACHE_STALE,
                "live": False,
                "updatedAt": cached_at,
            }

        return {
            "rate": self._usd_inr_rate_fallback,
            "source": self._USD_INR_SOURCE_FALLBACK_DEFAULT,
            "live": False,
            "updatedAt": None,
        }

    @classmethod
    def _extract_usd_inr_rate(cls, payload: object) -> float | None:
        if not isinstance(payload, dict):
            return None

        candidate_maps = (
            payload.get("rates"),
            payload.get("conversion_rates"),
            payload.get("data"),
        )
        for candidate_map in candidate_maps:
            if not isinstance(candidate_map, dict):
                continue
            resolved = cls._parse_positive_float(candidate_map.get("INR") or candidate_map.get("inr"))
            if resolved is not None:
                return resolved

        return cls._parse_positive_float(payload.get("INR") or payload.get("inr") or payload.get("usd_inr"))

    @staticmethod
    def _parse_positive_float(value: object) -> float | None:
        try:
            resolved = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if resolved <= 0:
            return None
        return resolved

    def _usage_cost_note_from_quote(self, source: str, fetched_at: datetime | None) -> str:
        timestamp = fetched_at.strftime("%d %b %Y, %I:%M %p IST") if fetched_at else None
        if source == self._USD_INR_SOURCE_LIVE_API:
            if timestamp:
                return f"Estimated using live USD/INR from exchange API ({timestamp})."
            return "Estimated using live USD/INR from exchange API."
        if source == self._USD_INR_SOURCE_LIVE_CACHE:
            if timestamp:
                return f"Estimated using recently cached live USD/INR ({timestamp})."
            return "Estimated using recently cached live USD/INR."
        if source == self._USD_INR_SOURCE_CACHE_STALE:
            if timestamp:
                return f"Live FX refresh failed; estimated using last cached USD/INR ({timestamp})."
            return "Live FX refresh failed; estimated using last cached USD/INR."
        return f"Live FX unavailable; estimated using fallback USD/INR (₹{self._usd_inr_rate_fallback:.2f}/USD)."

    def _record_chat_execution_log(
        self,
        session_id: str,
        mode: str,
        model: str | None,
        user_message_id: str | None,
        assistant_message_id: str | None,
        user_content: str,
        assistant_content: str,
        raw_logs: str,
        success: bool,
        exit_code: int,
        quick_path: bool,
    ) -> None:
        if self._chat_execution_log_repository is None:
            return
        if not self._should_capture_execution_logs(mode):
            return

        sanitized_raw_logs = self._strip_codex_diagnostic_lines(raw_logs or "")
        token_usage = self._derive_token_usage(
            raw_logs=sanitized_raw_logs,
            user_content=user_content,
            assistant_content=assistant_content,
        )
        try:
            self._chat_execution_log_repository.create(
                session_id=session_id,
                mode=mode,
                model=(model or "").strip() or None,
                user_message_id=(user_message_id or "").strip() or None,
                assistant_message_id=(assistant_message_id or "").strip() or None,
                user_content=user_content.strip(),
                assistant_content=assistant_content.strip(),
                raw_logs=self._truncate_chat_execution_logs(sanitized_raw_logs),
                success=success,
                exit_code=exit_code,
                quick_path=quick_path,
                prompt_tokens=token_usage["promptTokens"],
                completion_tokens=token_usage["completionTokens"],
                total_tokens=token_usage["totalTokens"],
                token_source=token_usage["tokenSource"],
            )
        except Exception:  # pylint: disable=broad-except
            self._logger.exception("Unable to persist chat execution log for session %s", session_id)

    @classmethod
    def _derive_token_usage(
        cls,
        raw_logs: str,
        user_content: str,
        assistant_content: str,
    ) -> dict[str, Any]:
        parsed = cls._parse_token_usage_from_logs(raw_logs=raw_logs)
        prompt_tokens = parsed.get("promptTokens")
        completion_tokens = parsed.get("completionTokens")
        total_tokens = parsed.get("totalTokens")

        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens

        if total_tokens is None:
            estimated_prompt = cls._estimate_tokens_from_text(user_content)
            estimated_completion = cls._estimate_tokens_from_text(assistant_content)
            if prompt_tokens is None:
                prompt_tokens = estimated_prompt
            if completion_tokens is None:
                completion_tokens = estimated_completion
            total_tokens = max((prompt_tokens or 0) + (completion_tokens or 0), 0)
            if parsed.get("promptTokens") is None and parsed.get("completionTokens") is None:
                source = cls._TOKEN_SOURCE_ESTIMATED
            else:
                source = cls._TOKEN_SOURCE_MIXED
        else:
            source = cls._TOKEN_SOURCE_PARSED
            total_tokens = max(int(total_tokens), 0)

        return {
            "promptTokens": max(int(prompt_tokens), 0) if prompt_tokens is not None else None,
            "completionTokens": max(int(completion_tokens), 0) if completion_tokens is not None else None,
            "totalTokens": max(int(total_tokens), 0),
            "tokenSource": source,
        }

    @classmethod
    def _parse_token_usage_from_logs(cls, raw_logs: str) -> dict[str, int | None]:
        normalized = (raw_logs or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            return {"promptTokens": None, "completionTokens": None, "totalTokens": None}

        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        total_tokens: int | None = None

        inline_patterns = (
            r"tokens\s*used\s*:\s*([0-9][0-9,]*)\s*input\b[^0-9]+([0-9][0-9,]*)\s*output\b",
            r"tokens\s*used\s*:\s*input[^0-9]*([0-9][0-9,]*)[^0-9]+output[^0-9]*([0-9][0-9,]*)",
            r"tokens\s*used\s*:\s*([0-9][0-9,]*)\s*prompt\b[^0-9]+([0-9][0-9,]*)\s*(?:output|completion)\b",
        )
        for pattern in inline_patterns:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if not match:
                continue
            prompt_tokens = cls._parse_token_count(match.group(1))
            completion_tokens = cls._parse_token_count(match.group(2))
            if prompt_tokens is not None and completion_tokens is not None:
                break

        if prompt_tokens is None:
            prompt_tokens = cls._first_token_match(
                normalized,
                patterns=(
                    r"\b(?:prompt|input)\s*tokens?\s*[:=]\s*([0-9][0-9,]*)",
                    r"\binput\s*[:=]\s*([0-9][0-9,]*)\s*tokens?\b",
                    r"\btokens\s*used\s*:[^\n]*?([0-9][0-9,]*)\s*input\b",
                ),
            )
        if completion_tokens is None:
            completion_tokens = cls._first_token_match(
                normalized,
                patterns=(
                    r"\b(?:completion|output)\s*tokens?\s*[:=]\s*([0-9][0-9,]*)",
                    r"\boutput\s*[:=]\s*([0-9][0-9,]*)\s*tokens?\b",
                    r"\btokens\s*used\s*:[^\n]*?([0-9][0-9,]*)\s*output\b",
                ),
            )

        total_tokens = cls._first_token_match(
            normalized,
            patterns=(
                r"\btotal\s*tokens?\s*[:=]\s*([0-9][0-9,]*)",
                r"\btokens\s*used\s*[:=]\s*([0-9][0-9,]*)\s*(?:$|\n)",
                r"\btokens\s*used\s*\n\s*([0-9][0-9,]*)\b",
            ),
        )
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens

        return {
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
        }

    @classmethod
    def _first_token_match(cls, value: str, patterns: tuple[str, ...]) -> int | None:
        for pattern in patterns:
            match = re.search(pattern, value, flags=re.IGNORECASE)
            if not match:
                continue
            token_count = cls._parse_token_count(match.group(1))
            if token_count is not None:
                return token_count
        return None

    @staticmethod
    def _parse_token_count(value: str | None) -> int | None:
        cleaned = re.sub(r"[^\d]", "", str(value or ""))
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _estimate_tokens_from_text(value: str) -> int:
        cleaned = re.sub(r"\s+", " ", value or "").strip()
        if not cleaned:
            return 0
        words = len(re.findall(r"\w+", cleaned))
        char_estimate = max(1, round(len(cleaned) / 4))
        word_estimate = max(1, round(words * 1.3))
        return max(char_estimate, word_estimate)

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

    def _hydrate_assistant_generated_images(
        self,
        session_id: str,
        assistant_text: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        if not assistant_text or "![" not in assistant_text:
            return assistant_text, []

        materialize = getattr(self._codex_service, "materialize_chat_generated_image", None)
        if not callable(materialize):
            return assistant_text, []

        cached_url_by_reference: dict[str, str | None] = {}
        generated_attachments: list[dict[str, Any]] = []
        generated_attachment_ids: set[str] = set()

        def _rewrite(match: re.Match[str]) -> str:
            alt_text = match.group(1)
            destination = match.group(2).strip()
            if not destination:
                return match.group(0)

            image_reference = destination.split(maxsplit=1)[0].strip().strip("<>").strip("'\"")
            if not image_reference:
                return match.group(0)

            if image_reference not in cached_url_by_reference:
                resolved_attachment = materialize(session_id=session_id, image_reference=image_reference)
                if not isinstance(resolved_attachment, dict):
                    cached_url_by_reference[image_reference] = None
                else:
                    attachment = dict(resolved_attachment)
                    attachment_id = str(attachment.get("id") or "").strip()
                    if not attachment_id:
                        cached_url_by_reference[image_reference] = None
                    else:
                        url = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
                        attachment["url"] = url
                        cached_url_by_reference[image_reference] = url
                        if attachment_id not in generated_attachment_ids:
                            generated_attachment_ids.add(attachment_id)
                            generated_attachments.append(attachment)

            rewritten_url = cached_url_by_reference.get(image_reference)
            if not rewritten_url:
                return match.group(0)
            return f"![{alt_text}]({rewritten_url})"

        rewritten_text = self._MARKDOWN_IMAGE_RE.sub(_rewrite, assistant_text)
        return rewritten_text, generated_attachments

    def get_image_attachment(self, session_id: str, attachment_id: str) -> dict[str, Any] | None:
        attachment = self._codex_service.get_chat_attachment(session_id=session_id, attachment_id=attachment_id)
        if not attachment:
            return None
        enriched = dict(attachment)
        enriched["url"] = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
        return enriched

    def _run_general_image_task(
        self,
        session_id: str,
        user_content: str,
        selected_model: str | None = None,
    ) -> dict[str, Any]:
        prompt = self._general_image_prompt(user_content)

        codex_text, codex_error, codex_logs = self._generate_image_with_codex_runtime(
            session_id=session_id,
            user_content=user_content,
            prompt=prompt,
            selected_model=selected_model,
        )
        if codex_text:
            return {
                "assistantText": codex_text,
                "rawLogs": codex_logs or "image generation completed via Codex runtime",
                "success": True,
                "exitCode": 0,
            }

        generate_image = getattr(self._codex_service, "generate_chat_image", None)
        if callable(generate_image):
            attachment, error = generate_image(session_id=session_id, prompt=prompt)
            if not error and attachment:
                attachment_id = str(attachment.get("id") or "").strip()
                if attachment_id:
                    alt_text = self._image_alt_text_for_prompt(user_content)
                    image_url = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
                    return {
                        "assistantText": f"![{alt_text}]({image_url})",
                        "rawLogs": "image generation completed via OpenAI Images API fallback",
                        "success": True,
                        "exitCode": 0,
                    }
            fallback_error = str(error or "Image generation fallback failed.")
        else:
            fallback_error = "Image API fallback is unavailable in this runtime."

        primary_error = str(codex_error or "Codex runtime did not return a usable image output.")
        return {
            "assistantText": (
                "I could not generate the image right now. "
                f"{primary_error} Fallback status: {fallback_error}"
            ),
            "rawLogs": f"{primary_error}\n\n{fallback_error}",
            "success": False,
            "exitCode": 1,
        }

    def _generate_image_with_codex_runtime(
        self,
        session_id: str,
        user_content: str,
        prompt: str,
        selected_model: str | None,
    ) -> tuple[str | None, str | None, str]:
        image_model = self._best_codex_image_model(selected_model)
        runtime_prompt = self._build_codex_image_generation_prompt(user_prompt=prompt)
        logs = ""
        try:
            completion = self._codex_service.run_chat(
                session_id=f"{session_id}-image",
                prompt=runtime_prompt,
                model=image_model,
                timeout_seconds=180,
            )
            logs = str(completion.logs or "")
        except Exception as exc:  # pylint: disable=broad-except
            return None, f"Codex image runtime failed: {exc}", logs

        if not bool(getattr(completion, "success", False)):
            exit_code = int(getattr(completion, "exit_code", 1))
            return None, f"Codex image runtime did not complete successfully (exit {exit_code}).", logs

        assistant_text = self._build_assistant_text(logs, True)
        rewritten_text, _attachments = self._hydrate_assistant_generated_images(
            session_id=session_id,
            assistant_text=assistant_text,
        )
        if self._has_usable_image_markdown(rewritten_text, session_id=session_id):
            return rewritten_text, None, logs

        return None, "Codex runtime returned text but no usable image markdown output.", logs

    def _best_codex_image_model(self, selected_model: str | None) -> str | None:
        preferred = (
            "gpt-5.4",
            "gpt-5.1-codex-max",
            "gpt-5.3-codex",
            "gpt-5.2",
            "gpt-5.4-mini",
        )
        for candidate in preferred:
            if self._codex_service.is_supported_chat_model(candidate):
                return candidate
        if selected_model and self._codex_service.is_supported_chat_model(selected_model):
            return selected_model
        return self._codex_service.default_chat_model()

    @staticmethod
    def _build_codex_image_generation_prompt(user_prompt: str) -> str:
        return (
            "Generate exactly one raster image that satisfies the user request.\n"
            f"User request: {user_prompt}\n\n"
            "Rules:\n"
            "- Use Codex native image-generation capability/tooling.\n"
            "- Do not use shell commands, Python scripts, SVG generation, HTML, or placeholders.\n"
            "- Return exactly one Markdown image tag pointing to the generated image.\n"
            "- Do not include any other text."
        )

    @classmethod
    def _has_usable_image_markdown(cls, assistant_text: str, session_id: str) -> bool:
        if not assistant_text:
            return False
        for match in cls._MARKDOWN_IMAGE_RE.finditer(assistant_text):
            destination = match.group(2).strip()
            if not destination:
                continue
            image_reference = destination.split(maxsplit=1)[0].strip().strip("<>").strip("'\"")
            if not image_reference:
                continue
            lowered = image_reference.lower()
            if lowered.startswith(("http://", "https://", "data:", "blob:")):
                return True
            if image_reference.startswith(f"/chat/sessions/{session_id}/attachments/"):
                return True
        return False

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

    def _build_chat_prompt(
        self,
        mode: str,
        messages: list[dict[str, Any]],
        memory_context: str | None = None,
    ) -> str:
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
        if memory_context:
            memory_block = (
                "Saved agent memory from memory.md (use only when relevant; user-editable):\n"
                f"{memory_context.strip()}\n"
            )
            if "Conversation:" in lines:
                conversation_index = lines.index("Conversation:")
                lines[conversation_index:conversation_index] = [memory_block, ""]
            else:
                lines.extend([memory_block, ""])
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
        if normalized_mode == "general":
            lines.append(
                "If the user asks to generate or edit an image, perform the image generation/edit workflow and "
                "return the result as Markdown image output."
            )
        lines.append("Return only the final answer. Do not include tool logs, search traces, or intermediate status narration.")
        return "\n".join(lines)

    def _memory_prompt_context(self) -> str:
        if self._memory_service is None:
            return ""
        try:
            return self._memory_service.prompt_context()
        except Exception:  # pylint: disable=broad-except
            self._logger.warning("Unable to load agent memory context", exc_info=True)
            return ""

    def _update_memory_from_user_message(self, content: str, mode: str) -> None:
        if self._memory_service is None:
            return
        try:
            self._memory_service.update_from_user_message(content=content, mode=mode)
        except Exception:  # pylint: disable=broad-except
            self._logger.warning("Unable to update agent memory", exc_info=True)

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
            if ChatService._is_codex_diagnostic_line(stripped):
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
        cleaned = ChatService._collapse_consecutive_duplicate_lines(cleaned)
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
    def _collapse_consecutive_duplicate_lines(text: str) -> str:
        if not text:
            return ""

        lines = text.split("\n")
        collapsed: list[str] = []
        in_fenced_code = False
        previous_key = ""

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fenced_code = not in_fenced_code
                collapsed.append(line)
                previous_key = ""
                continue

            normalized_key = re.sub(r"\s+", " ", stripped).strip().lower()
            if not in_fenced_code and normalized_key and normalized_key == previous_key:
                continue

            collapsed.append(line)
            previous_key = normalized_key if normalized_key else ""

        return "\n".join(collapsed)

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
            r"compose\s+up|up\s+-d|docker\s+up|fix|debug|troubleshoot|investigate|repair"
            r")\b"
        )
        return action_pattern.search(lowered) is None

    @staticmethod
    def _should_use_operator_quick_reply(mode: str, user_content: str) -> bool:
        _ = (mode, user_content)
        # Force operator requests through full operator planning/execution for
        # better accuracy and consistency (no direct quick-path replies).
        return False

    @staticmethod
    def _is_operator_action_request(lowered_user_content: str) -> bool:
        action_pattern = re.compile(
            r"\b("
            r"fix|debug|troubleshoot|investigate|analy[sz]e|profile|optimi[sz]e|improve|"
            r"reduce|increase|free|clear|kill|restart|reboot|deploy|rebuild|build|run|execute|"
            r"configure|install|update|upgrade|patch|change|modify|edit|refactor|"
            r"create|delete|remove|stop|start|launch|open|close|log|logs|tail"
            r")\b"
        )
        return action_pattern.search(lowered_user_content) is not None

    @staticmethod
    def _is_explicit_system_resource_query(user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return False

        resource_terms = ("battery", "ram", "memory", "cpu", "disk", "storage", "uptime")
        if not any(term in lowered for term in resource_terms):
            return False
        if ChatService._is_operator_action_request(lowered):
            return False

        # Avoid quick-returning telemetry for diagnosis phrasing that usually needs
        # deeper operator investigation instead of a read-only snapshot.
        diagnostic_markers = ("issue", "problem", "slow", "lag", "leak", "spike", "throttl", "overheat", "error", "fail")
        if any(marker in lowered for marker in diagnostic_markers):
            return False

        question_prefixes = (
            "show",
            "check",
            "what",
            "what's",
            "what is",
            "how",
            "status",
            "current",
            "tell me",
            "give me",
            "is ",
        )
        if "?" in lowered:
            return True
        if any(lowered.startswith(prefix) for prefix in question_prefixes):
            return True

        status_markers = ("usage", "used", "free", "available", "status", "current", "temperature", "temp", "percent", "load")
        word_count = len(re.findall(r"\w+", lowered))
        if word_count <= 10 and any(marker in lowered for marker in status_markers):
            return True
        return False

    @staticmethod
    def _is_simple_operator_task(user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return False
        if ChatService._is_operator_action_request(lowered):
            return False
        if ChatService._has_operator_context_reference(lowered):
            return False
        word_count = len(re.findall(r"\w+", lowered))
        return word_count <= 18

    @staticmethod
    def _operator_task_complexity(user_content: str) -> str:
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return "standard"

        score = 0
        word_count = len(re.findall(r"\w+", lowered))

        if ChatService._is_operator_action_request(lowered):
            score += 2
        if ChatService._has_operator_context_reference(lowered):
            score += 2

        heavyweight_markers = (
            "benchmark",
            "profile",
            "migrate",
            "migration",
            "refactor",
            "rebuild",
            "deploy",
            "investigate",
            "troubleshoot",
            "debug",
            "incident",
            "regression",
            "test",
            "tests",
            "logs",
            "trace",
            "stack",
            "root cause",
        )
        score += sum(
            1
            for marker in heavyweight_markers
            if re.search(
                r"\b" + re.escape(marker).replace("\\ ", r"\s+") + r"\b",
                lowered,
            )
        )

        if word_count > 45:
            score += 2
        elif word_count > 22:
            score += 1

        if score >= 4:
            return "complex"
        if score <= 1 and ChatService._is_simple_operator_task(user_content):
            return "simple"
        return "standard"

    @staticmethod
    def _has_operator_context_reference(lowered_user_content: str) -> bool:
        reference_markers = (
            "continue",
            "as discussed",
            "as before",
            "previous",
            "last time",
            "same repo",
            "same project",
            "that repo",
            "that project",
            "earlier",
            "follow up",
            "follow-up",
        )
        return any(marker in lowered_user_content for marker in reference_markers)

    @staticmethod
    def _operator_context_limit(user_content: str) -> int:
        complexity = ChatService._operator_task_complexity(user_content)
        if complexity == "complex":
            return 24
        if complexity == "simple":
            return 10
        return 18

    @staticmethod
    def _operator_needs_cross_session_context(user_content: str, current_session_messages: list[dict[str, Any]]) -> bool:
        if len(current_session_messages) > 4:
            return False
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        return ChatService._has_operator_context_reference(lowered)

    def _try_direct_operator_answer(self, mode: str, user_content: str) -> str | None:
        if mode != "operator" or self._system_context_service is None:
            if mode != "operator":
                return None

        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return None

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
                if self._is_operator_action_request(lowered):
                    return None
                target_name = health_match.group(1)
                result = self._docker_service.get_container_health(target_name)
                return str(result.get("message", "Unable to check container health."))

        if self._system_context_service is None:
            return None
        if not self._is_explicit_system_resource_query(user_content):
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

    def _run_operator_task(
        self,
        session_id: str,
        user_content: str,
        selected_model: str | None = None,
    ) -> dict[str, Any]:
        if self._operator_access_service is None:
            return {
                "assistantText": "Operator policy is not configured. Please set OPERATOR_ALLOWED_PATHS / OPERATOR_DENIED_PATHS.",
                "rawLogs": "",
                "success": False,
                "exitCode": 1,
            }

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
        complexity = self._operator_task_complexity(user_content)
        context_limit = self._operator_context_limit(user_content=user_content)
        recent_messages = self._message_repository.list_recent_for_session(session_id=session_id, limit=context_limit)
        conversation_context = self._format_operator_conversation_context(recent_messages)
        cross_session_context = ""
        if self._operator_needs_cross_session_context(user_content=user_content, current_session_messages=recent_messages):
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
            "Operator team protocol:\n"
            "- Visionary: restate intent, assumptions, and success criteria before action.\n"
            "- Blueprint: design the minimal safe execution plan and command order.\n"
            "- Craftsman: execute file edits, commands, and container operations.\n"
            "- Guardian: validate with checks/logs and surface any risk or regression.\n"
            "- Shipmaster: finalize runtime/deployment state and report accessible endpoints.\n\n"
            "Execution requirements:\n"
            f"- Work under: {container_cwd}\n"
            f"- Task complexity: {complexity}. Calibrate planning depth and validation effort accordingly.\n"
            f"{branch_instruction}\n"
            "- For code changes, implement the requested update and run tests/lint/build checks only when needed for confidence.\n"
            "- If this is non-code ops query, provide concise factual summary.\n"
            "- For container operations, try `docker compose` first and fall back to `docker-compose` if needed.\n"
            "- Remote server operations over SSH are allowed when the user asks for them.\n"
            "- For Debian/Ubuntu package tasks, use non-interactive apt commands (`apt-get update`, `apt-get install -y`).\n"
            "- For remote apt installs, run through SSH and use `sudo -n` first to avoid interactive prompts.\n"
            "- If sudo requires a password and `OPERATOR_SUDO_PASSWORD` is present, pass it via stdin; never print secrets.\n"
            "- After package installation, verify with `dpkg -s <package>` and/or `<package> --version`.\n"
            "- Final answer must be only a concise user-facing summary: what changed, whether it was verified, and any URL/port needed to use it.\n"
            "- Keep response crisp and readable (3-6 short lines by default).\n"
            "- Do not include modified file lists, file paths, raw patches, code snippets, or environment diagnostics unless the user explicitly asks.\n"
            "- Include verification details and branch names only when the user explicitly asks.\n"
            "- Do not paste full file contents, full diffs, or long code/config blocks unless the user explicitly asks for code/log output.\n"
            "- Never access denied paths.\n"
            "- Resolve follow-up references (for example: 'that repo', 'continue', 'as discussed') using conversation context.\n"
            "- If context is still ambiguous, state the ambiguity briefly and proceed with the most likely interpretation.\n"
        )
        timeout_seconds = self._operator_timeout_seconds_for_task(user_content=user_content)
        effective_model = self._operator_model_for_task(selected_model=selected_model, user_content=user_content)

        try:
            completion = self._codex_service.run_operator(
                session_id=session_id,
                prompt=operator_prompt,
                container_cwd=container_cwd,
                extra_volumes=mounts,
                timeout_seconds=timeout_seconds,
                model=effective_model,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.exception("Operator task execution failed for session %s", session_id)
            raw_logs = self._exception_execution_logs("Operator task failed to start", exc)
            return {
                "assistantText": f"Operator task failed to start: {exc}",
                "rawLogs": raw_logs,
                "success": False,
                "exitCode": 1,
            }

        output = self._build_assistant_text(completion.logs, completion.success)
        output = self._sanitize_operator_output(user_content=user_content, output=output)
        if not completion.success:
            return {
                "assistantText": f"{output}\n\n(Operator execution exited with code {completion.exit_code})",
                "rawLogs": completion.logs,
                "success": False,
                "exitCode": int(completion.exit_code),
            }
        return {
            "assistantText": output,
            "rawLogs": completion.logs,
            "success": True,
            "exitCode": int(completion.exit_code),
        }

    def _operator_timeout_seconds_for_task(self, user_content: str) -> int:
        complexity = self._operator_task_complexity(user_content)
        if self._is_operator_package_install_request(user_content):
            # Package operations can take longer due apt metadata refresh/download time.
            return 2400
        if complexity == "simple":
            # Keep short read-only requests responsive.
            return 480
        if complexity == "complex":
            return 1800

        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if self._has_operator_context_reference(lowered):
            return 1500
        if self._is_operator_action_request(lowered):
            return 1200
        return 900

    @staticmethod
    def _is_operator_package_install_request(user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content).strip().lower()
        if not lowered:
            return False
        package_markers = (
            "apt install",
            "apt-get install",
            "sudo apt install",
            "sudo apt-get install",
            "apt upgrade",
            "apt-get upgrade",
            "apt update",
            "apt-get update",
        )
        return any(marker in lowered for marker in package_markers)

    def _operator_model_for_task(self, selected_model: str | None, user_content: str) -> str | None:
        cleaned_selected = (selected_model or "").strip() or None
        complexity = self._operator_task_complexity(user_content)
        if complexity != "simple":
            return cleaned_selected

        if cleaned_selected and "mini" in cleaned_selected.lower():
            return cleaned_selected

        default_model = self._resolve_model(model=None, fallback_to_default=True)
        if cleaned_selected and cleaned_selected != default_model:
            # Respect explicit non-default session/user selection.
            return cleaned_selected

        available_models: list[str] = []
        list_models = getattr(self._codex_service, "list_chat_models", None)
        if callable(list_models):
            try:
                available_models = [str(item).strip() for item in list_models() if str(item).strip()]
            except Exception:  # pylint: disable=broad-except
                available_models = []

        normalized_lookup = {item.lower(): item for item in available_models}
        for candidate in self._FAST_OPERATOR_MODEL_CANDIDATES:
            resolved = normalized_lookup.get(candidate.lower())
            if resolved:
                return resolved

        return cleaned_selected or default_model

    @staticmethod
    def _sanitize_operator_output(user_content: str, output: str) -> str:
        cleaned = output.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            return ""

        lowered_request = user_content.lower()
        requested_details = ChatService._operator_requested_verbose_output(lowered_request)
        requested_code = ChatService._operator_requested_code_output(lowered_request)
        cleaned = ChatService._strip_codex_diagnostic_lines(cleaned)
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
            normalized = ChatService._truncate_at_unrequested_diff(normalized)
            normalized = ChatService._strip_fenced_code_blocks(normalized)
            normalized = ChatService._strip_diff_noise_lines(normalized)
            normalized = ChatService._strip_operator_unrequested_detail_lines(normalized)
            if not normalized:
                return "I omitted verbose code output. Ask for `diff` or `logs` if you want full details."
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized

    @staticmethod
    def _operator_requested_verbose_output(lowered_request: str) -> bool:
        verbose_markers = (
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
        explicit_output_pattern = re.compile(
            r"\b(?:show|send|paste|print|return|include|display|share|give)\s+"
            r"(?:me\s+)?(?:the\s+|a\s+|an\s+)?"
            r"(?:code|snippet|diff|patch|source|implementation|file contents?|full file)\b"
        )
        if explicit_output_pattern.search(lowered_request):
            return True
        exact_markers = (
            "show me the diff",
            "show the diff",
            "give me the diff",
            "paste the diff",
            "include the diff",
            "show patch",
            "show the patch",
            "file contents",
            "full file contents",
        )
        return any(marker in lowered_request for marker in exact_markers)

    @staticmethod
    def _strip_codex_diagnostic_lines(value: str) -> str:
        normalized = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        kept: list[str] = []
        for raw_line in normalized.split("\n"):
            stripped = raw_line.strip()
            if stripped and ChatService._is_codex_diagnostic_line(stripped):
                continue
            kept.append(raw_line.rstrip())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept).strip())

    @staticmethod
    def _truncate_at_unrequested_diff(value: str) -> str:
        lines: list[str] = []
        for raw_line in value.split("\n"):
            stripped = raw_line.strip()
            if re.match(r"^(diff --git|index [0-9a-f]+\.\.[0-9a-f]+|--- |\+\+\+ |@@ )", stripped):
                break
            lines.append(raw_line.rstrip())
        return "\n".join(lines).strip()

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
    def _strip_operator_unrequested_detail_lines(value: str) -> str:
        kept: list[str] = []
        for raw_line in value.split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            lowered = stripped.lower()
            if lowered in {
                "modified files:",
                "changed files:",
                "files changed:",
                "modified:",
                "changes:",
                "diff:",
                "patch:",
            }:
                continue
            if ChatService._is_operator_unrequested_file_reference(stripped):
                continue
            if ChatService._is_operator_unrequested_code_line(stripped):
                continue
            kept.append(line)

        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept).strip())
        blocks = [block.strip() for block in cleaned.split("\n\n") if block.strip()]
        if len(blocks) <= 3:
            return cleaned
        return "\n\n".join(blocks[:3]).strip()

    @staticmethod
    def _is_operator_unrequested_file_reference(stripped: str) -> bool:
        if re.match(r"^[-*]\s+(/|~|\.\.?/|[A-Za-z0-9_.-]+/)", stripped):
            return True
        if re.match(r"^(/|~|\.\.?/)[^\s]+$", stripped):
            return True
        if re.match(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+$", stripped):
            return True
        return False

    @staticmethod
    def _is_operator_unrequested_code_line(stripped: str) -> bool:
        normalized = re.sub(r"^[+\-*•]\s*", "", stripped).strip()
        lowered = normalized.lower()
        if not normalized:
            return False
        if normalized.startswith("@"):
            return True
        if normalized.startswith(("<", "</", "{", "}", "});", "];")):
            return True
        if re.match(
            r"^(const|let|var|function|async function|class|return|if|for|while|switch|case|try|catch|import|export)\b",
            normalized,
        ):
            return True
        if re.match(r"^[A-Za-z_$][\w$]*\s*[:=]\s*.+[,;]?$", normalized):
            return True
        if re.match(r"^[.#]?[A-Za-z0-9_-]+\s*\{?$", normalized) and not normalized.endswith("."):
            return True
        if lowered.startswith(("state.", "document.", "window.", "row.", "event.", "searchinputel.", "entries")):
            return True
        code_punctuation_count = sum(normalized.count(token) for token in ("{", "}", "(", ")", ";", "=>", "`", "$", "<", ">"))
        word_count = len(re.findall(r"[A-Za-z]{2,}", normalized))
        if code_punctuation_count >= 4 and word_count <= 12:
            return True
        if stripped.startswith(("+", "-")) and code_punctuation_count >= 2:
            return True
        return False

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
        attachments: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        if self._tool_builder_service is None:
            return "Tool builder workflow is not configured yet. Please check backend startup dependencies.", None

        image_paths: list[str] = []
        seen_image_paths: set[str] = set()
        for item in attachments or []:
            if not isinstance(item, dict):
                continue
            container_path = str(item.get("containerPath") or "").strip()
            if not container_path or container_path in seen_image_paths:
                continue
            seen_image_paths.add(container_path)
            image_paths.append(container_path)
        generation_image_paths = image_paths or None
        image_context_cache: str | None = None

        def resolved_user_request() -> str:
            nonlocal image_context_cache
            if image_context_cache is None:
                image_context_cache = self._augment_tool_builder_request_with_images(
                    session_id=session_id,
                    user_content=user_content,
                    attachments=attachments or [],
                    image_paths=image_paths,
                    model=selected_model,
                )
            return image_context_cache

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
                request_payload = resolved_user_request()
                combined_change_request = (
                    f"{draft_change_request}\n\n"
                    "Additional clarification from user:\n"
                    f"{request_payload.strip()}"
                )
                clarification, clarification_result = self._tool_builder_modification_clarification_from_codex(
                    session_id=session_id,
                    change_request=combined_change_request,
                    tool=tool,
                    request_state=request_state,
                    prior_questions=context.get("pendingQuestions"),
                    model=selected_model,
                    image_paths=image_paths,
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

                finalized_change_request = self._finalize_tool_builder_request(
                    original_request=combined_change_request,
                    clarification_result=clarification_result,
                )
                condensed_history_prompt = self._condense_modification_history_prompt(
                    original_request=combined_change_request,
                    clarification_result=clarification_result,
                )
                modification_prompt = self._build_tool_modification_prompt(
                    user_content=finalized_change_request,
                    tool=tool,
                    request_state=request_state,
                )
                job = self._tool_builder_service.start_generation(
                    prompt=condensed_history_prompt,
                    name=tool_name,
                    base_request_id=base_request_id,
                    rebuild_tool_id=rebuild_tool_id,
                    model=selected_model,
                    workflow_prompt=modification_prompt,
                    prompt_already_refined=True,
                    image_paths=generation_image_paths,
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
                request_payload = resolved_user_request()
                combined_request = (
                    f"{draft_prompt}\n\n"
                    "Clarifications from user:\n"
                    f"{request_payload.strip()}"
                )
                clarification, clarification_result = self._tool_builder_clarification_from_codex(
                    session_id=session_id,
                    request_text=combined_request,
                    prior_questions=context.get("pendingQuestions"),
                    model=selected_model,
                    image_paths=image_paths,
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

                finalized_request = self._finalize_tool_builder_request(
                    original_request=combined_request,
                    clarification_result=clarification_result,
                )
                initial_prompt = self._build_tool_initial_prompt(finalized_request)
                job = self._tool_builder_service.start_generation(
                    prompt=finalized_request,
                    name=None,
                    model=selected_model,
                    workflow_prompt=initial_prompt,
                    prompt_already_refined=True,
                    image_paths=generation_image_paths,
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
            change_request = resolved_user_request()
            clarification, clarification_result = self._tool_builder_modification_clarification_from_codex(
                session_id=session_id,
                change_request=change_request,
                tool=tool,
                request_state=request_state,
                model=selected_model,
                image_paths=image_paths,
            )
            if clarification:
                draft_change_request = (
                    str(clarification_result.get("clarifiedRequest") or "").strip()
                    if isinstance(clarification_result, dict)
                    else ""
                ) or change_request.strip()
                return clarification, {
                    "phase": "modification_clarification",
                    "requestId": base_request_id,
                    "toolId": rebuild_tool_id,
                    "toolName": tool_name,
                    "draftChangeRequest": draft_change_request,
                    "pendingQuestions": clarification_result.get("questions", []) if isinstance(clarification_result, dict) else [],
                }

            finalized_change_request = self._finalize_tool_builder_request(
                original_request=change_request,
                clarification_result=clarification_result,
            )
            condensed_history_prompt = self._condense_modification_history_prompt(
                original_request=change_request,
                clarification_result=clarification_result,
            )
            modification_prompt = self._build_tool_modification_prompt(
                user_content=finalized_change_request,
                tool=tool,
                request_state=request_state,
            )
            job = self._tool_builder_service.start_generation(
                prompt=condensed_history_prompt,
                name=tool_name,
                base_request_id=base_request_id,
                rebuild_tool_id=rebuild_tool_id,
                model=selected_model,
                workflow_prompt=modification_prompt,
                prompt_already_refined=True,
                image_paths=generation_image_paths,
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

        initial_request = resolved_user_request()
        clarification, clarification_result = self._tool_builder_clarification_from_codex(
            session_id=session_id,
            request_text=initial_request,
            model=selected_model,
            image_paths=image_paths,
        )
        if clarification:
            draft_prompt = (
                str(clarification_result.get("clarifiedRequest") or "").strip()
                if isinstance(clarification_result, dict)
                else ""
            ) or initial_request.strip()
            return clarification, {
                "phase": "clarification",
                "draftPrompt": draft_prompt,
                "pendingQuestions": clarification_result.get("questions", []) if isinstance(clarification_result, dict) else [],
            }

        finalized_request = self._finalize_tool_builder_request(
            original_request=initial_request,
            clarification_result=clarification_result,
        )
        initial_prompt = self._build_tool_initial_prompt(finalized_request)
        job = self._tool_builder_service.start_generation(
            prompt=finalized_request,
            name=None,
            model=selected_model,
            workflow_prompt=initial_prompt,
            prompt_already_refined=True,
            image_paths=generation_image_paths,
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

    def _augment_tool_builder_request_with_images(
        self,
        session_id: str,
        user_content: str,
        attachments: list[dict[str, Any]],
        image_paths: list[str],
        model: str | None = None,
    ) -> str:
        base_request = user_content.strip()
        if not base_request:
            base_request = "Build or update the tool using the uploaded image as the primary UI reference."
        if not image_paths:
            return base_request

        fallback_attachment_names = [
            str(item.get("fileName") or "image").strip()
            for item in attachments
            if isinstance(item, dict)
        ]
        fallback_names = ", ".join(name for name in fallback_attachment_names if name) or "uploaded image(s)"
        fallback_summary = (
            f"Reference images: {fallback_names}. "
            "Match layout hierarchy, spacing rhythm, typography tone, color treatment, "
            "and component styling as closely as practical while keeping the requested functionality."
        )

        analysis_prompt = self._build_tool_builder_visual_analysis_prompt(
            user_content=base_request,
            attachments=attachments,
        )
        try:
            result = self._codex_service.run_chat(
                session_id=f"{session_id}-tool-visual-brief",
                prompt=analysis_prompt,
                model=model,
                image_paths=image_paths,
                timeout_seconds=120,
            )
        except Exception:  # pylint: disable=broad-except
            self._logger.exception("Failed to analyze tool-builder image references for session %s", session_id)
            summary = fallback_summary
        else:
            if result.success:
                parsed_summary = self._build_assistant_text(result.logs, result.success).strip()
                summary = parsed_summary or fallback_summary
            else:
                summary = fallback_summary

        summary = self._truncate_prompt_for_context(summary, limit=2600)
        return (
            f"{base_request}\n\n"
            "Visual reference requirements (derived from uploaded image attachments):\n"
            f"{summary}\n\n"
            "Treat these visual requirements as mandatory for UI styling and layout unless they conflict "
            "with explicit user functionality constraints."
        )

    @staticmethod
    def _build_tool_builder_visual_analysis_prompt(
        user_content: str,
        attachments: list[dict[str, Any]],
    ) -> str:
        attachment_names: list[str] = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            name = str(item.get("fileName") or "").strip()
            if name:
                attachment_names.append(name)

        lines = [
            "You are analyzing uploaded UI reference image(s) for a tool-building workflow.",
            "Extract implementation-ready visual requirements that help an engineer recreate the look and feel.",
            "Focus on concrete design signals from the image(s), not generic design advice.",
            "If a detail is uncertain, state it briefly instead of inventing specifics.",
            "Output concise Markdown with these headings exactly:",
            "1. Visual Direction",
            "2. Layout Structure",
            "3. Component Patterns",
            "4. Interaction and Motion",
            "5. Accessibility and Responsiveness",
            "6. Non-goals / Uncertain Details",
            "",
            "User request:",
            user_content.strip(),
        ]
        if attachment_names:
            lines.extend(["", "Attached files:", ", ".join(attachment_names[:6])])
        return "\n".join(lines).strip()

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
        image_paths: list[str] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        clarification_prompt = self._build_tool_builder_clarification_analysis_prompt(
            request_text=request_text,
            prior_questions=prior_questions,
        )
        if image_paths:
            clarification_prompt = (
                f"{clarification_prompt}\n\n"
                "Reference image attachments are provided. Include concrete visual/layout requirements inferred "
                "from those images inside clarifiedRequest."
            )
        result = self._codex_service.run_chat(
            session_id=f"{session_id}-tool-clarify",
            prompt=clarification_prompt,
            model=model,
            image_paths=image_paths,
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
        image_paths: list[str] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        clarification_prompt = self._build_tool_builder_modification_clarification_analysis_prompt(
            change_request=change_request,
            tool=tool,
            request_state=request_state,
            prior_questions=prior_questions,
        )
        if image_paths:
            clarification_prompt = (
                f"{clarification_prompt}\n\n"
                "Reference image attachments are provided. Preserve relevant visual/layout directions from those "
                "images inside clarifiedRequest."
            )
        result = self._codex_service.run_chat(
            session_id=f"{session_id}-tool-modify-clarify",
            prompt=clarification_prompt,
            model=model,
            image_paths=image_paths,
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
    def _tool_request_history_entries(request_state: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(request_state, dict):
            return []
        raw_history = request_state.get("promptHistory")
        entries: list[dict[str, Any]] = []
        if isinstance(raw_history, list):
            for item in raw_history:
                if not isinstance(item, dict):
                    continue
                prompt = str(item.get("prompt") or "").strip()
                if not prompt:
                    continue
                entries.append(
                    {
                        "kind": str(item.get("kind") or "unknown").strip() or "unknown",
                        "prompt": prompt,
                    }
                )
        return entries

    @classmethod
    def _tool_request_context_lines(cls, request_state: dict[str, Any] | None, limit: int = 1500) -> list[str]:
        if not isinstance(request_state, dict):
            return []

        lines: list[str] = []
        initial_prompt = cls._summarize_tool_builder_request_text(str(request_state.get("initialPrompt") or ""), limit=limit)
        latest_prompt = cls._summarize_tool_builder_request_text(
            str(request_state.get("latestPrompt") or request_state.get("prompt") or ""),
            limit=limit,
        )
        refined_prompt = cls._summarize_tool_builder_request_text(str(request_state.get("refinedPrompt") or ""), limit=limit)
        history = cls._tool_request_history_entries(request_state)
        status = str(request_state.get("status") or "").strip()
        error = cls._truncate_prompt_for_context(str(request_state.get("error") or ""), limit=min(1200, limit))

        if status:
            lines.append(f"- Latest build status: {status}")
        if initial_prompt:
            lines.append(f"\nOriginal tool request:\n{initial_prompt}")
        if history:
            recent_entries = history[1:] if len(history) > 1 else []
            if recent_entries:
                lines.append("\nApplied modification history:")
                deduped_recent: list[dict[str, str]] = []
                seen_prompts: set[str] = set()
                for entry in recent_entries:
                    prompt = cls._summarize_tool_builder_request_text(str(entry.get("prompt") or ""), limit=limit)
                    normalized = re.sub(r"\s+", " ", prompt).strip().lower()
                    if not normalized or normalized in seen_prompts:
                        continue
                    seen_prompts.add(normalized)
                    deduped_recent.append(
                        {
                            "kind": str(entry.get("kind") or "change").strip() or "change",
                            "prompt": prompt,
                        }
                    )

                for entry in deduped_recent[-4:]:
                    kind = str(entry.get("kind") or "change").strip() or "change"
                    prompt = str(entry.get("prompt") or "").strip()
                    if prompt:
                        lines.append(f"- {kind}: {prompt}")
        if latest_prompt and latest_prompt != initial_prompt:
            lines.append(f"\nLatest user request:\n{latest_prompt}")
        if refined_prompt:
            lines.append(f"\nLatest refined requirements:\n{refined_prompt}")
        if error:
            lines.append(f"\nLatest build/runtime error:\n{error}")
        return lines

    @classmethod
    def _finalize_tool_builder_request(
        cls,
        original_request: str,
        clarification_result: dict[str, Any] | None,
    ) -> str:
        original = original_request.strip()
        clarified = ""
        if isinstance(clarification_result, dict):
            clarified = str(clarification_result.get("clarifiedRequest") or "").strip()

        if not clarified:
            return original
        if clarified == original or clarified in original:
            return original

        original_norm = re.sub(r"\s+", " ", original).strip().lower()
        clarified_norm = re.sub(r"\s+", " ", clarified).strip().lower()
        if clarified_norm and clarified_norm in original_norm:
            return original

        original_has_structure = bool(re.search(r"(^|\n)\s*(?:[-*]|\d+\.)\s+", original)) or "requirements:" in original_norm
        if original_has_structure and len(clarified) < max(120, int(len(original) * 0.8)):
            return (
                f"{original}\n\n"
                "Resolved clarifications and implementation notes:\n"
                f"{clarified}"
            )

        if len(clarified) < max(80, int(len(original) * 0.65)):
            return (
                f"{original}\n\n"
                "Clarified version preserving the same requirements:\n"
                f"{clarified}"
            )
        return clarified

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
            "Do not drop, rewrite away, or weaken explicit user requirements when you produce clarifiedRequest.\n"
            "Preserve all concrete requirements, constraints, technologies, integrations, must-have fields, and must-not statements from the user's wording.\n"
            "Use clarifiedRequest to preserve the full request while appending resolved assumptions or follow-up answers, not to compress it into a shorter summary.\n"
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
        context_lines.extend(cls._tool_request_context_lines(request_state, limit=1200))

        prior_block = "\n".join(prior_lines).strip()
        context_block = "\n".join(context_lines).strip()
        if prior_block:
            prior_block = f"{prior_block}\n\n"

        return (
            "Analyze this requested modification to an existing generated tool and decide if clarification is still required before implementation.\n"
            "Use the existing tool context, prior requirements, and the user follow-up answers already included.\n"
            "Do not repeat questions that are already answered.\n"
            "Do not drop or simplify away earlier accepted tool requirements when you produce clarifiedRequest.\n"
            "Keep clarifiedRequest faithful to both the original tool brief and the new requested change.\n"
            "For clarifiedRequest, preserve the user's exact requested change and only append resolved decisions or assumptions.\n"
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
            lines.extend(self._tool_request_context_lines(request_state, limit=3000))

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
        persistence_required = ChatService._tool_request_needs_persistence(user_content)
        alerts_required = ChatService._tool_request_needs_alerts(user_content)
        schedule_required = ChatService._tool_request_needs_schedule(user_content)
        conditional_requirements: list[str] = []
        if persistence_required:
            conditional_requirements.extend(
                [
                    "- If the tool needs persistent application data, reuse the platform MongoDB instead of adding a separate database service.\n",
                    "- Read Mongo connection settings from environment variables `MONGO_URI` and `MONGO_DB_NAME`.\n",
                    "- If MongoDB is temporarily unavailable during startup, do not crash the HTTP server; use bounded retries or lazy initialization so `/status` can still respond while dependencies recover.\n",
                ]
            )
        else:
            conditional_requirements.append("- Do not add MongoDB/database persistence unless the requested workflow actually needs durable state.\n")
        if alerts_required:
            conditional_requirements.extend(
                [
                    "- Use Brevo for email alerts unless the user explicitly requested another provider.\n",
                    "- Keep alert sending idempotent so repeated runs do not spam duplicates unless explicitly requested.\n",
                ]
            )
        else:
            conditional_requirements.append("- Do not add Brevo/email alert plumbing unless alerts or notifications are part of the request.\n")
        if schedule_required:
            conditional_requirements.append("- Implement scheduled/polling jobs with bounded retries and overlap protection.\n")
        else:
            conditional_requirements.append("- Do not add scheduler/cron workers unless the request includes background or repeated execution.\n")

        return (
            "Build a production-ready tool based on this request:\n"
            f"{user_content}\n\n"
            "Requirements:\n"
            "- Work like a senior software operator: inspect the request, implement the exact workflow, run verification, and fix issues before handoff.\n"
            "- Clarify requirements first. Convert requirements into explicit acceptance criteria and list assumptions before implementation.\n"
            "- Save the clarified requirements in `docs/requirements.md` and save concrete test cases in `docs/test-cases.md` before implementation.\n"
            "- Preserve every explicit user requirement from the request. Do not summarize away fields, constraints, integrations, or must-not rules.\n"
            "- Create a checklist in `docs/requirements.md` that maps each user requirement to the code/tests that satisfy it.\n"
            "- Implement the exact requested workflow; avoid unrelated extra features.\n"
            "- Do not add generic dashboards, auth, databases, schedulers, alerts, sample catalogs, or placeholder features unless they are required by the user request.\n"
            "- Convert the request into concrete acceptance criteria and satisfy each criterion in code/tests.\n"
            "- Follow TDD: write/update failing tests first, then implement backend changes until tests pass.\n"
            "- Choose a concise, domain-meaningful product name; avoid generic names based on filler words from the prompt.\n"
            "- Use Python 3.11.\n"
            "- Include a usable UI unless explicitly backend-only.\n"
            "- Make the UI feel modern and polished.\n"
            "- Choose a visual direction that fits the tool domain and request; do not force a dark theme unless it fits or is requested.\n"
            "- Default all user-facing dates, times, schedules, and cron behavior to IST using the `Asia/Kolkata` timezone unless the user explicitly requests another timezone.\n"
            "- Configure the generated app/runtime to honor `TZ=Asia/Kolkata` by default and keep frontend/backend time handling aligned with that timezone.\n"
            "- Prefer a lightweight UI stack (server-rendered/static HTML + JS) unless a heavier frontend framework is explicitly requested.\n"
            "- Include `requirements.txt`.\n"
            "- Add automated tests runnable with `python -m pytest -q`.\n"
            "- Place tests in `tests/` with discoverable names like `test_status.py`.\n"
            "- Include at least one passing test that validates `GET /status` returns exactly `{\"status\":\"ok\"}`.\n"
            "- Include tests for the core requested behavior (not only health/status endpoints).\n"
            "- Include tests and runnable docker artifacts including docker-compose.\n"
            "- Add a lightweight mock deployment validation step for docker-compose/runtime wiring.\n"
            "- Before launch, verify backend endpoints using real API calls; if scraping/external data is involved, cross-check sampled API output with live web search evidence and fix mismatches before publish.\n"
            "- For live-data tools, do not ship placeholder or stale sample datasets as the primary source of truth.\n"
            f"{''.join(conditional_requirements)}"
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
            tool_lines.extend(self._tool_request_context_lines(request_state, limit=5000))

        context_block = "\n".join(tool_lines)
        checklist_items = self._build_modification_must_implement_checklist(user_content=user_content)
        checklist_section = ""
        if checklist_items:
            checklist_lines = "\n".join(f"- {item}" for item in checklist_items)
            checklist_section = (
                "\nCritical change checklist (must implement all):\n"
                f"{checklist_lines}\n"
            )
        return (
            "Apply the requested change to the existing tool codebase.\n"
            "Inspect the current workspace first, then make targeted updates.\n\n"
            f"{context_block}\n\n"
            f"Change request:\n{user_content}\n\n"
            f"{checklist_section}"
            "Requirements:\n"
            "- Work like a senior software operator: inspect existing files, understand current behavior, make the smallest complete change, run tests/runtime checks, and fix regressions before handoff.\n"
            "- Clarify updated requirements and assumptions before changing implementation.\n"
            "- Keep `docs/requirements.md` and `docs/test-cases.md` in sync with the requested change before implementation.\n"
            "- Preserve all previously working requirements unless this change request explicitly replaces them.\n"
            "- Add the new change to the requirements checklist and verify old requirements still hold after the edit.\n"
            "- Implement exactly what the user asked in this change request.\n"
            "- Treat this as a focused modification request: make the smallest code change that satisfies it.\n"
            "- Preserve existing working behavior unless this request explicitly changes it.\n"
            "- Keep the UI modern and polished; preserve the existing visual direction unless this change request explicitly asks for another theme.\n"
            "- Keep user-facing dates, times, schedules, and cron behavior on IST using the `Asia/Kolkata` timezone unless this change request explicitly asks for another timezone.\n"
            "- Preserve or add runtime timezone configuration so the tool defaults to `TZ=Asia/Kolkata`.\n"
            "- Preserve or add `requirements.txt` plus pytest-discoverable tests under `tests/` so `python -m pytest -q` still passes.\n"
            "- Preserve `GET /status` returning exactly `{\"status\":\"ok\"}`.\n"
            "- Preserve existing Mongo persistence if the tool already uses it; do not add a separate database service.\n"
            "- If Mongo is used, keep it wired through `MONGO_URI` and `MONGO_DB_NAME` and keep `/status` available while dependencies reconnect.\n"
            "- Do not rewrite existing scraping/data-source logic unless the change request explicitly requires it.\n"
            "- Do not introduce new databases, alert providers, schedulers, auth, or heavy frontend frameworks unless this change request explicitly requires them.\n"
            "- Keep the UI/runtime stack lightweight unless the request explicitly requires a heavier frontend framework.\n"
            "- Follow TDD: update/add tests first for the changed behavior and likely regressions, then implement backend changes.\n"
            "- Keep Docker/runtime compatibility intact, including required health/status checks.\n"
            "- Re-verify runtime APIs after the change; for scraping/live-data flows, compare sampled API results with live web evidence before publish."
        )

    @staticmethod
    def _tool_request_needs_persistence(user_content: str) -> bool:
        lowered = user_content.lower()
        if ChatService._tool_request_has_opt_out(
            lowered,
            ("database", "db", "mongodb", "mongo", "persistence", "persistent", "storage"),
        ):
            return False
        return any(
            marker in lowered
            for marker in (
                "persist",
                "persistent",
                "database",
                "db",
                "mongodb",
                "mongo",
                "save",
                "saved",
                "store",
                "history",
                "watcher",
                "watchlist",
                "tracker",
                "alert",
                "login",
                "account",
                "remember",
            )
        )

    @staticmethod
    def _tool_request_needs_alerts(user_content: str) -> bool:
        lowered = user_content.lower()
        if ChatService._tool_request_has_opt_out(
            lowered,
            ("alert", "alerts", "notification", "notifications", "email", "mail", "brevo"),
        ):
            return False
        return any(
            marker in lowered
            for marker in ("alert", "alerts", "notify", "notification", "email", "mail", "brevo", "sendinblue", "webhook")
        )

    @staticmethod
    def _tool_request_needs_schedule(user_content: str) -> bool:
        lowered = user_content.lower()
        if ChatService._tool_request_has_opt_out(
            lowered,
            ("scheduler", "schedule", "scheduling", "cron", "polling", "background job"),
        ):
            return False
        return any(
            marker in lowered
            for marker in ("cron", "schedule", "scheduled", "poll", "polling", "interval", "every ", "daily", "hourly", "background job")
        )

    @staticmethod
    def _tool_request_has_opt_out(lowered_prompt: str, terms: tuple[str, ...]) -> bool:
        for term in terms:
            escaped = re.escape(term)
            patterns = (
                rf"\bno\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bwithout\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bdo\s+not\s+(?:use|include|add)\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bnot\s+(?:use|using|include|including|add|adding)\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bno\s+{escaped}\b",
                rf"\bwithout\s+{escaped}\b",
                rf"\bnot\s+(?:use|using|include|including|add|adding)\s+(?:a\s+|an\s+|any\s+)?{escaped}\b",
                rf"\bdo\s+not\s+(?:use|include|add)\s+(?:a\s+|an\s+|any\s+)?{escaped}\b",
            )
            if any(re.search(pattern, lowered_prompt) for pattern in patterns):
                return True
        return False

    @classmethod
    def _build_modification_must_implement_checklist(cls, user_content: str) -> list[str]:
        text = cls._summarize_tool_builder_request_text(user_content, limit=6000)
        if not text:
            return []

        extracted: list[str] = []
        lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
        for line in lines:
            match = re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", line)
            candidate = (match.group(1) if match else line).strip(" .")
            lowered = candidate.lower()
            if not candidate:
                continue
            if lowered.endswith(":"):
                continue
            if lowered.startswith(("requirements", "change request", "latest user request", "modification history")):
                continue
            if lowered.startswith("continue the existing tool scope"):
                continue
            extracted.append(candidate)

        if not extracted:
            sentence = re.split(r"(?<=[.!?])\s+", text.strip())[0].strip(" .")
            if sentence:
                extracted.append(sentence)

        checklist: list[str] = []
        seen: set[str] = set()
        for item in extracted:
            normalized = re.sub(r"\s+", " ", item).strip()
            lowered = normalized.lower()
            if not normalized or lowered in seen:
                continue
            seen.add(lowered)
            checklist.append(normalized)
            if len(checklist) >= 8:
                break
        return checklist

    @classmethod
    def _condense_modification_history_prompt(
        cls,
        original_request: str,
        clarification_result: dict[str, Any] | None,
    ) -> str:
        clarified = ""
        if isinstance(clarification_result, dict):
            clarified = str(clarification_result.get("clarifiedRequest") or "").strip()

        source = clarified or original_request
        source = cls._extract_embedded_tool_builder_request(source)
        source = source or original_request

        marker_patterns = (
            r"latest user request:\s*(.+)$",
            r"new requested change(?:\s*\(preserved\))?:\s*(.+)$",
            r"change request:\s*(.+)$",
        )
        for pattern in marker_patterns:
            match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            candidate = re.sub(r"\s+", " ", match.group(1)).strip()
            if candidate:
                source = candidate
                break

        if "\n\nRequirements:\n" in source:
            source = source.split("\n\nRequirements:\n", 1)[0].strip()

        return cls._truncate_prompt_for_context(source.strip(), limit=3500)

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

        sessions = self._session_repository.list_recent(limit=8)
        for session in sessions:
            if session.get("id") == current_session_id:
                continue
            if self._normalize_mode(session.get("mode")) != "operator":
                continue

            messages = self._message_repository.list_recent_for_session(session_id=session["id"], limit=6)
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

    @classmethod
    def _is_general_browser_screenshot_request(cls, user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content or "").strip().lower()
        if not lowered:
            return False

        has_action = any(marker in lowered for marker in cls._GENERAL_SCREENSHOT_ACTION_MARKERS) or cls._is_ss_shorthand_screenshot_request(lowered)
        if not has_action:
            return False

        has_target = any(marker in lowered for marker in cls._GENERAL_SCREENSHOT_TARGET_MARKERS)
        target_url = cls._extract_screenshot_target_url(user_content)
        return bool(has_target or target_url)

    @classmethod
    def _should_route_to_browser_screenshot(cls, mode: str, user_content: str) -> bool:
        normalized_mode = cls._normalize_mode(mode)
        if normalized_mode not in {"general", "operator", "tool_builder"}:
            return False

        if not cls._is_general_browser_screenshot_request(user_content):
            return False

        # In operator/tool-builder mode, keep routing strict so normal task prompts
        # (which may include words like "capture" or "site") are not hijacked.
        if normalized_mode in {"operator", "tool_builder"}:
            lowered = re.sub(r"\s+", " ", user_content or "").strip().lower()
            has_explicit_screenshot_marker = any(
                marker in lowered for marker in ("screenshot", "screen shot", "snapshot")
            ) or cls._is_ss_shorthand_screenshot_request(lowered)
            if not has_explicit_screenshot_marker:
                return False
            if not cls._extract_screenshot_target_url(user_content):
                return False

        return True

    @classmethod
    def _is_ss_shorthand_screenshot_request(cls, user_content: str) -> bool:
        normalized = re.sub(r"\s+", " ", user_content or "").strip()
        if not normalized or not cls._SCREENSHOT_SS_TOKEN_RE.search(normalized):
            return False
        return bool(cls._SCREENSHOT_SS_REQUEST_RE.search(normalized))

    @classmethod
    def _is_general_browser_recording_request(cls, user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content or "").strip().lower()
        if not lowered:
            return False

        has_action = any(marker in lowered for marker in cls._GENERAL_BROWSER_RECORDING_ACTION_MARKERS)
        has_target_marker = any(marker in lowered for marker in cls._GENERAL_BROWSER_RECORDING_TARGET_MARKERS)
        target_url = cls._extract_screenshot_target_url(user_content)
        return bool(has_action and has_target_marker and target_url)

    @classmethod
    def _should_route_to_browser_recording(cls, mode: str, user_content: str) -> bool:
        normalized_mode = cls._normalize_mode(mode)
        if normalized_mode not in {"general", "operator", "tool_builder"}:
            return False
        if not cls._is_general_browser_recording_request(user_content):
            return False

        if normalized_mode in {"operator", "tool_builder"}:
            # Keep operator/build prompts from being intercepted unless the
            # user is clearly asking for a concrete browser recording.
            lowered = re.sub(r"\s+", " ", user_content or "").strip().lower()
            if "video" not in lowered and "recording" not in lowered and "browser session" not in lowered:
                return False
            if not cls._extract_screenshot_target_url(user_content):
                return False

        return True

    @classmethod
    def _extract_screenshot_target_url(cls, user_content: str) -> str | None:
        text = (user_content or "").strip()
        if not text:
            return None

        explicit_match = cls._EXPLICIT_URL_RE.search(text)
        if explicit_match:
            normalized = cls._normalize_screenshot_url(explicit_match.group(1))
            if normalized:
                return normalized

        # Prefer search intent inference before bare-domain fallback so
        # prompts like "search for X on google.com" resolve to a search URL.
        inferred = cls._infer_screenshot_target_url_from_text(text)
        if inferred:
            return inferred

        domain_match = cls._DOMAIN_URL_RE.search(text)
        if domain_match:
            normalized = cls._normalize_screenshot_url(domain_match.group(1))
            if normalized:
                return normalized

        return None

    @classmethod
    def _infer_screenshot_target_url_from_text(cls, text: str) -> str | None:
        lowered = re.sub(r"\s+", " ", text or "").strip().lower()
        if not lowered:
            return None

        prefer_image_search = bool(re.search(r"\b(image|images|photo|photos|pic|pics|picture|pictures)\b", lowered))

        for pattern, pattern_kind in cls._SCREENSHOT_SITE_SEARCH_PATTERNS:
            match = pattern.search(lowered)
            if not match:
                continue
            if pattern_kind == "query_site":
                raw_query = match.group(1)
                site_slug = match.group(2)
            else:
                site_slug = match.group(1)
                raw_query = match.group(2)
            site_slug = cls._normalize_screenshot_site_slug(site_slug)
            query = cls._sanitize_inferred_search_query(raw_query)
            if not query:
                continue
            base_template = cls._SCREENSHOT_SITE_SEARCH_BASE_URLS.get(site_slug)
            if prefer_image_search:
                base_template = cls._SCREENSHOT_SITE_IMAGE_SEARCH_BASE_URLS.get(site_slug, base_template)
            if not base_template:
                continue
            return base_template.format(query=quote_plus(query))

        if "amazon" in lowered:
            return cls._SCREENSHOT_SITE_HOME_URLS["amazon"]
        if "flipkart" in lowered:
            return cls._SCREENSHOT_SITE_HOME_URLS["flipkart"]
        if "youtube" in lowered:
            return cls._SCREENSHOT_SITE_HOME_URLS["youtube"]
        if "google" in lowered:
            return cls._SCREENSHOT_SITE_HOME_URLS["google"]
        if "brave" in lowered:
            return cls._SCREENSHOT_SITE_HOME_URLS["brave"]
        return None

    @staticmethod
    def _normalize_screenshot_site_slug(site_value: str) -> str:
        normalized = re.sub(r"\s+", "", site_value or "").strip().lower()
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if normalized.endswith(".com"):
            normalized = normalized[: -len(".com")]
        return normalized

    @staticmethod
    def _sanitize_inferred_search_query(raw_value: str) -> str:
        candidate = re.sub(r"\s+", " ", raw_value or "").strip()
        if not candidate:
            return ""
        candidate = re.split(r"[.!?]", candidate, maxsplit=1)[0]
        candidate = re.split(
            r"\b(?:and\s+)?(?:record|capture|screenshot|screen\s+shot|snapshot)\b",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        candidate = re.split(r"\b(?:and\s+give|give|show|with|please)\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
        candidate = candidate.strip(" \"'`.,;:!?()[]{}")
        return candidate[:120].strip()

    @staticmethod
    def _normalize_screenshot_url(raw_value: str) -> str | None:
        candidate = str(raw_value or "").strip().strip("<>").strip("'\"").strip(".,;:!?)]}")
        if not candidate:
            return None

        if "://" not in candidate:
            candidate = f"https://{candidate}"

        parsed = urlsplit(candidate)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        if not parsed.netloc:
            return None

        normalized_path = parsed.path or "/"
        return urlunsplit((scheme, parsed.netloc, normalized_path, parsed.query, ""))

    @classmethod
    def _extract_screenshot_dimensions(cls, user_content: str) -> tuple[int, int]:
        default_width = 1366
        default_height = 900
        match = cls._SCREENSHOT_DIMENSION_RE.search(user_content or "")
        if not match:
            return default_width, default_height

        try:
            width = int(match.group(1))
            height = int(match.group(2))
        except (TypeError, ValueError):
            return default_width, default_height

        width = max(640, min(3840, width))
        height = max(480, min(3840, height))
        # Keep screenshot viewport landscape even if users provide portrait dimensions.
        if height > width:
            width, height = height, width
        return width, height

    @staticmethod
    def _is_full_page_screenshot_requested(user_content: str) -> bool:
        _ = user_content
        # Force viewport screenshots so rendered outputs remain landscape.
        return False

    @classmethod
    def _extract_browser_recording_duration_seconds(cls, user_content: str) -> int:
        default_seconds = 10
        match = cls._BROWSER_RECORDING_DURATION_RE.search(user_content or "")
        if not match:
            return default_seconds

        try:
            amount = int(match.group(1))
        except (TypeError, ValueError):
            return default_seconds

        unit_text = str(match.group(2) or "").lower()
        seconds = amount * 60 if unit_text.startswith(("m", "min")) else amount
        return max(1, min(120, seconds))

    def _run_general_browser_screenshot_task(
        self,
        session_id: str,
        user_content: str,
    ) -> dict[str, Any]:
        if self._browser_screenshot_service is None:
            return {
                "assistantText": (
                    "I could not capture a website screenshot right now because the browser screenshot service "
                    "is not configured."
                ),
                "rawLogs": "Browser screenshot service is not configured in backend runtime.",
                "success": False,
                "exitCode": 1,
            }

        target_url = self._extract_screenshot_target_url(user_content)
        if not target_url:
            return {
                "assistantText": (
                    "I can capture that, but I need a valid website URL. "
                    "Please include a full link like `https://example.com`."
                ),
                "rawLogs": "Screenshot request did not include a valid URL.",
                "success": False,
                "exitCode": 1,
            }

        width, height = self._extract_screenshot_dimensions(user_content)
        full_page = self._is_full_page_screenshot_requested(user_content)
        try:
            capture = self._browser_screenshot_service.capture_screenshot(
                url=target_url,
                width=width,
                height=height,
                full_page=full_page,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "assistantText": f"I could not capture the website screenshot right now. {exc}",
                "rawLogs": f"Screenshot capture failed for {target_url}: {exc}",
                "success": False,
                "exitCode": 1,
            }

        image_bytes = capture.get("imageBytes")
        if not isinstance(image_bytes, bytes) or not image_bytes:
            return {
                "assistantText": "I could not capture a usable screenshot from that URL.",
                "rawLogs": f"Screenshot service returned invalid image payload for {target_url}.",
                "success": False,
                "exitCode": 1,
            }

        if bool(capture.get("isBlocked")):
            block_reason = str(capture.get("blockReason") or "").strip()
            message = (
                "The page loaded, but it appears to require human verification or is blocking automated browsing. "
                "I cannot bypass CAPTCHA or bot-detection systems. "
                "A safe workaround is to use an official API/export, open the page in a user-controlled browser session, "
                "or complete verification manually and retry with a permitted authenticated/profile-based flow."
            )
            if block_reason:
                message = f"{message}\n\nDetected signal: {block_reason}"
            return {
                "assistantText": message,
                "rawLogs": f"Screenshot blocked for {target_url}: {block_reason or 'bot/captcha challenge detected'}",
                "success": False,
                "exitCode": 1,
            }

        content_type = str(capture.get("contentType") or "image/png").strip() or "image/png"
        page_title = str(capture.get("pageTitle") or "").strip()
        final_url = str(capture.get("finalUrl") or target_url).strip() or target_url
        safe_title = re.sub(r"[^a-zA-Z0-9._-]+", "-", (page_title or "website")).strip("-").lower() or "website"
        file_name = f"screenshot-{safe_title}.png"

        try:
            saved = self._codex_service.save_chat_attachment(
                session_id=session_id,
                file_name=file_name,
                content_type=content_type,
                data=image_bytes,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "assistantText": f"I captured the screenshot but could not store it in chat attachments. {exc}",
                "rawLogs": f"Screenshot captured for {target_url} but attachment save failed: {exc}",
                "success": False,
                "exitCode": 1,
            }

        attachment_id = str(saved.get("id") or "").strip() if isinstance(saved, dict) else ""
        if not attachment_id:
            return {
                "assistantText": "I captured the screenshot but could not resolve the stored attachment.",
                "rawLogs": f"Attachment ID missing after screenshot capture for {target_url}.",
                "success": False,
                "exitCode": 1,
            }

        alt = page_title or f"Screenshot of {final_url}"
        image_url = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
        # Return clickable markdown so users can open screenshots in full size from chat.
        assistant_text = f"[![{alt}]({image_url})]({image_url})"
        if final_url and final_url != target_url:
            assistant_text = f"{assistant_text}\n\nCaptured from: {final_url}"

        return {
            "assistantText": assistant_text,
            "rawLogs": (
                "website screenshot captured via dedicated browser container "
                f"({width}x{height}, fullPage={str(full_page).lower()}, url={final_url})"
            ),
            "success": True,
            "exitCode": 0,
        }

    def _run_general_browser_recording_task(
        self,
        session_id: str,
        user_content: str,
    ) -> dict[str, Any]:
        if self._browser_screenshot_service is None:
            return {
                "assistantText": (
                    "I could not record a browser session right now because the browser screenshot service "
                    "is not configured."
                ),
                "rawLogs": "Browser screenshot service is not configured in backend runtime.",
                "success": False,
                "exitCode": 1,
            }

        target_url = self._extract_screenshot_target_url(user_content)
        if not target_url:
            return {
                "assistantText": (
                    "I can record that, but I need a valid website URL. "
                    "Please include a full link like `https://example.com`."
                ),
                "rawLogs": "Browser recording request did not include a valid URL.",
                "success": False,
                "exitCode": 1,
            }

        width, height = self._extract_screenshot_dimensions(user_content)
        duration_seconds = self._extract_browser_recording_duration_seconds(user_content)
        try:
            recording = self._browser_screenshot_service.record_browser_session(
                url=target_url,
                width=width,
                height=height,
                duration_seconds=duration_seconds,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "assistantText": f"I could not record the browser session right now. {exc}",
                "rawLogs": f"Browser recording failed for {target_url}: {exc}",
                "success": False,
                "exitCode": 1,
            }

        video_bytes = recording.get("videoBytes")
        if not isinstance(video_bytes, bytes) or not video_bytes:
            return {
                "assistantText": "I could not record a usable video from that URL.",
                "rawLogs": f"Browser recording service returned invalid video payload for {target_url}.",
                "success": False,
                "exitCode": 1,
            }

        if bool(recording.get("isBlocked")):
            block_reason = str(recording.get("blockReason") or "").strip()
            message = (
                "The page loaded, but it appears to require human verification or is blocking automated browsing. "
                "I cannot bypass CAPTCHA or bot-detection systems. "
                "A safe workaround is to use an official API/export, open the page in a user-controlled browser session, "
                "or complete verification manually and retry with a permitted authenticated/profile-based flow."
            )
            if block_reason:
                message = f"{message}\n\nDetected signal: {block_reason}"
            return {
                "assistantText": message,
                "rawLogs": f"Browser recording blocked for {target_url}: {block_reason or 'bot/captcha challenge detected'}",
                "success": False,
                "exitCode": 1,
            }

        content_type = str(recording.get("contentType") or "video/webm").strip() or "video/webm"
        page_title = str(recording.get("pageTitle") or "").strip()
        final_url = str(recording.get("finalUrl") or target_url).strip() or target_url
        saved_duration = int(recording.get("durationSeconds") or duration_seconds)
        safe_title = re.sub(r"[^a-zA-Z0-9._-]+", "-", (page_title or "browser-session")).strip("-").lower()
        file_name = f"browser-recording-{safe_title or 'session'}.webm"

        try:
            saved = self._codex_service.save_chat_attachment(
                session_id=session_id,
                file_name=file_name,
                content_type=content_type,
                data=video_bytes,
            )
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "assistantText": f"I recorded the browser session but could not store it in chat attachments. {exc}",
                "rawLogs": f"Browser recording captured for {target_url} but attachment save failed: {exc}",
                "success": False,
                "exitCode": 1,
            }

        attachment_id = str(saved.get("id") or "").strip() if isinstance(saved, dict) else ""
        if not attachment_id:
            return {
                "assistantText": "I recorded the browser session but could not resolve the stored attachment.",
                "rawLogs": f"Attachment ID missing after browser recording for {target_url}.",
                "success": False,
                "exitCode": 1,
            }

        video_url = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
        assistant_text = f"Recorded {saved_duration}s of the browser session: [{file_name}]({video_url})"
        if final_url and final_url != target_url:
            assistant_text = f"{assistant_text}\n\nRecorded from: {final_url}"

        return {
            "assistantText": assistant_text,
            "rawLogs": (
                "browser session recorded via dedicated browser container "
                f"({width}x{height}, durationSeconds={saved_duration}, url={final_url})"
            ),
            "success": True,
            "exitCode": 0,
        }

    @classmethod
    def _is_general_image_generation_request(cls, user_content: str) -> bool:
        lowered = re.sub(r"\s+", " ", user_content or "").strip().lower()
        if not lowered:
            return False
        if lowered.startswith(("how to ", "why ", "what is ", "what's ")):
            return False
        if "code" in lowered and "generate image" not in lowered:
            return False

        if re.match(r"^(an?\s+)?(image|photo|picture|portrait|illustration)\s+of\b", lowered):
            return True

        has_action = any(marker in lowered for marker in cls._GENERAL_IMAGE_ACTION_MARKERS)
        has_target = any(marker in lowered for marker in cls._GENERAL_IMAGE_TARGET_MARKERS)
        return has_action and has_target

    @staticmethod
    def _general_image_prompt(user_content: str) -> str:
        base_prompt = re.sub(r"\s+", " ", user_content or "").strip()
        if not base_prompt:
            return "Generate a high-quality image."

        lowered = base_prompt.lower()
        cartoon_markers = ("shin chan", "cartoon", "anime", "illustration", "comic", "sketch")
        photoreal_markers = ("realistic", "real-looking", "real looking", "photoreal", "portrait", "look like", "resemble")

        if any(marker in lowered for marker in cartoon_markers):
            return base_prompt
        if any(marker in lowered for marker in photoreal_markers):
            return (
                f"{base_prompt}. Output style: photorealistic, natural skin texture, realistic lighting, "
                "high detail, camera-real image."
            )
        return base_prompt

    @staticmethod
    def _image_alt_text_for_prompt(user_content: str) -> str:
        cleaned = re.sub(r"\s+", " ", user_content or "").strip()
        if not cleaned:
            return "Generated image"
        if len(cleaned) > 72:
            cleaned = f"{cleaned[:69].rstrip()}..."
        return cleaned

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
            if self._is_codex_diagnostic_line(stripped):
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
    def _is_codex_diagnostic_line(stripped: str) -> bool:
        lowered = stripped.lower()
        return (
            "codex_core::session" in lowered
            or "failed to record rollout items" in lowered
            or re.match(r"^\d{4}-\d{2}-\d{2}t\S+\s+(error|warn)\s+codex", lowered) is not None
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
            if stripped and self._is_codex_diagnostic_line(stripped):
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
            "You are ChatGPT, a helpful, friendly general-purpose assistant. "
            "Be natural, concise, and honest about uncertainty. "
            "For casual greetings or small talk, reply conversationally without mentioning tools or logs. "
            "Answer directly and avoid narrating intermediate checks. "
            "When users request image creation or editing, generate the image instead of only describing it."
        )
