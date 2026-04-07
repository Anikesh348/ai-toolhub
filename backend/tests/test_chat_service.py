from types import SimpleNamespace
from unittest.mock import Mock

from app.services.chat_service import ChatService
from app.utils.time import now_ist


def test_docker_summary_query_for_status_request() -> None:
    prompt = "Can you show me running docker containers summary?"
    assert ChatService._is_docker_summary_query(prompt) is True


def test_docker_summary_query_ignores_deploy_request() -> None:
    prompt = "Can you deploy the docker containers in tool-hub repo in Documents?"
    assert ChatService._is_docker_summary_query(prompt) is False


def test_docker_summary_query_ignores_spin_up_request() -> None:
    prompt = "Please spin up docker containers for tool-hub"
    assert ChatService._is_docker_summary_query(prompt) is False


def test_operator_branch_instruction_defaults_to_current_branch() -> None:
    instruction = ChatService._operator_branch_instruction("Update the login endpoint validation.")
    assert "stay on the current branch" in instruction


def test_operator_branch_instruction_detects_new_branch_request() -> None:
    instruction = ChatService._operator_branch_instruction("Please make this change on a new branch.")
    assert "explicitly requested a new branch" in instruction


def test_operator_branch_instruction_extracts_requested_branch_name() -> None:
    instruction = ChatService._operator_branch_instruction("Use branch feat/login-cleanup for this update.")
    assert "`feat/login-cleanup`" in instruction


def test_extract_requested_branch_name_from_checkout_command() -> None:
    branch_name = ChatService._extract_requested_branch_name("Run git checkout -b feat/api-timeout and fix retries.")
    assert branch_name == "feat/api-timeout"


def test_extract_requested_branch_name_returns_none_when_not_requested() -> None:
    branch_name = ChatService._extract_requested_branch_name("Fix the flaky tests in the auth module.")
    assert branch_name is None


def test_normalize_runtime_base_url_defaults_to_localhost() -> None:
    assert ChatService._normalize_runtime_base_url(None) == "http://localhost"
    assert ChatService._normalize_runtime_base_url("") == "http://localhost"


def test_normalize_runtime_base_url_adds_scheme_and_trims_slash() -> None:
    assert ChatService._normalize_runtime_base_url("example.com/") == "http://example.com"
    assert ChatService._normalize_runtime_base_url("https://tool.example.com/") == "https://tool.example.com"


def test_service_prefers_frontend_detection() -> None:
    assert ChatService._service_prefers_frontend("frontend") is True
    assert ChatService._service_prefers_frontend("backend-api") is False
    assert ChatService._service_prefers_frontend("worker") is False
    assert ChatService._service_prefers_frontend("redis") is None


def test_tool_builder_status_query_matches_status_only_prompt() -> None:
    prompt = "Share current status and port."
    assert ChatService._is_tool_builder_status_query(prompt) is True


def test_tool_builder_status_query_matches_seed_prompt_with_first_status_clause() -> None:
    prompt = (
        "Use tool id 0123456789abcdef0123456789abcdef as active context for this chat. "
        "I want follow-up changes to modify this tool in place and redeploy it. "
        "First, share current status and port."
    )
    assert ChatService._is_tool_builder_status_query(prompt) is True


def test_tool_builder_status_query_matches_polite_status_prompt() -> None:
    prompt = "Can you check current status and port for this deployed tool?"
    assert ChatService._is_tool_builder_status_query(prompt) is True


def test_tool_builder_status_query_ignores_modification_prompt_with_runtime_terms() -> None:
    prompt = "Fix the deployed tool login flow and redeploy it; it is not running correctly."
    assert ChatService._is_tool_builder_status_query(prompt) is False


def test_tool_builder_status_query_ignores_modification_prompt_that_mentions_url() -> None:
    prompt = "Update the navbar contrast and then share the URL."
    assert ChatService._is_tool_builder_status_query(prompt) is False


def test_tool_builder_information_query_matches_tool_question() -> None:
    prompt = "How does this tool store price history?"
    assert ChatService._is_tool_builder_information_query(prompt) is True


def test_tool_builder_information_query_ignores_change_request() -> None:
    prompt = "Add price history filters to the dashboard."
    assert ChatService._is_tool_builder_information_query(prompt) is False


def test_sanitize_operator_output_removes_fenced_code_by_default() -> None:
    output = (
        "Completed the fix.\n\n"
        "```python\n"
        "def noisy_dump():\n"
        "    return 'too much code'\n"
        "```\n\n"
        "Service is healthy."
    )
    sanitized = ChatService._sanitize_operator_output("Fix it and summarize", output)
    assert "noisy_dump" not in sanitized
    assert "Completed the fix." in sanitized
    assert "Service is healthy." in sanitized


def test_sanitize_operator_output_keeps_code_when_diff_requested() -> None:
    output = (
        "Applied patch.\n\n"
        "```diff\n"
        "+ print('hello')\n"
        "```\n"
    )
    sanitized = ChatService._sanitize_operator_output("Show me the diff", output)
    assert "+ print('hello')" in sanitized


def test_build_tool_modification_prompt_includes_prior_tool_context() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
    )
    tool = {
        "toolId": "tool-1",
        "name": "Demo Tool",
        "status": "RUNNING",
        "uiPort": 3010,
        "ports": {"app": 3010},
    }
    request_state = {
        "prompt": "Add CSV export for job history.",
        "initialPrompt": "Build a dashboard for monitoring background jobs.",
        "latestPrompt": "Add CSV export for job history.",
        "promptHistory": [
            {"kind": "initial", "prompt": "Build a dashboard for monitoring background jobs."},
            {"kind": "modification", "prompt": "Add CSV export for job history."},
        ],
        "refinedPrompt": "Include status board and retry controls.",
        "status": "RUNNING",
        "error": None,
    }

    prompt = service._build_tool_modification_prompt(
        user_content="Add CSV export for job history.",
        tool=tool,
        request_state=request_state,
    )

    assert "Existing tool context:" in prompt
    assert "Tool ID: tool-1" in prompt
    assert "Original tool request:" in prompt
    assert "Applied modification history:" in prompt
    assert "Latest refined requirements:" in prompt
    assert "Add CSV export for job history." in prompt
    assert "focused modification request" in prompt
    assert "Do not rewrite existing scraping/data-source logic" in prompt
    assert "default to a dark theme" in prompt
    assert "Asia/Kolkata" in prompt
    assert "TZ=Asia/Kolkata" in prompt


def test_finalize_tool_builder_request_preserves_structured_requirements() -> None:
    original_request = (
        "Build a production-ready movie alerts tool.\n\n"
        "Requirements:\n"
        "- Allow users to select city, movie, language, and format.\n"
        "- Persist alerts in MongoDB.\n"
        "- Run scheduled polling every 15 minutes.\n"
    )
    clarification_result = {
        "clarifiedRequest": "Build a movie alerts tool with MongoDB and a 15 minute polling interval."
    }

    finalized = ChatService._finalize_tool_builder_request(  # pylint: disable=protected-access
        original_request=original_request,
        clarification_result=clarification_result,
    )

    assert "Allow users to select city, movie, language, and format." in finalized
    assert "Persist alerts in MongoDB." in finalized
    assert "15 minutes" in finalized


def test_build_tool_initial_prompt_defaults_ui_to_dark_theme() -> None:
    prompt = ChatService._build_tool_initial_prompt("Build a monitoring dashboard.")
    assert "Make the UI feel modern and polished." in prompt
    assert "Default the UI to a dark theme" in prompt
    assert "Asia/Kolkata" in prompt
    assert "TZ=Asia/Kolkata" in prompt


def test_tool_builder_clarification_targets_live_movie_alert_gaps() -> None:
    clarification = ChatService._build_tool_builder_clarification(
        "Build a tool that tracks movie show availability in Chennai and Bangalore and sends alerts."
    )

    assert clarification is not None
    assert "authoritative" in clarification
    assert "alerts fire" in clarification


def test_tool_builder_initial_clarification_comes_from_codex_analysis() -> None:
    codex_service = Mock()
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs=(
            '{"needsClarification": true, "questions": ['
            '"Should alerts trigger when the price is at or below the target price, or also within a percentage above it?", '
            '"Should the polling interval be configured in minutes only, and what minimum should be allowed?"], '
            '"clarifiedRequest": "Track PlayStation digital game prices from the Sony website and alert users by email when target prices are reached."}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    message_repository = Mock()
    message_repository.list_recent_for_session.return_value = []
    service = ChatService(
        session_repository=Mock(),
        message_repository=message_repository,
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=Mock(),  # type: ignore[arg-type]
    )

    message, context = service._run_tool_builder_task(
        session_id="session-1",
        user_content=(
            "build a tool where user can search for playstation games (digital) along with the price "
            "they want to purchase it for and send mail alerts"
        ),
        selected_model="gpt-5.4-mini",
    )

    assert "price is at or below the target price" in message
    assert "polling interval" in message
    assert "BookMyShow" not in message
    assert context is not None
    assert context["phase"] == "clarification"
    codex_service.run_chat.assert_called_once()
    assert codex_service.run_chat.call_args.kwargs["model"] == "gpt-5.4-mini"


def test_tool_builder_clarification_reply_can_start_build() -> None:
    tool_builder_service = Mock()
    tool_builder_service.start_generation.return_value = {"id": "req-clarified", "status": "PENDING"}
    codex_service = Mock()
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs=(
            '{"needsClarification": false, "questions": [], '
            '"clarifiedRequest": "Build a movie alert tool for Chennai and Bangalore using BookMyShow first with District fallback, '
            'refresh live listings, alert for newly available shows only, and allow polling interval in minutes with a 15 minute minimum."}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
    )
    service._resolve_tool_builder_context = Mock(  # type: ignore[method-assign]
        return_value={
            "phase": "clarification",
            "draftPrompt": "Build a movie alert tool for Chennai and Bangalore.",
            "pendingQuestions": [
                "Which source should be treated as authoritative for current show listings: BookMyShow, District, or both with a fallback order?",
                "When should alerts fire: every polling run with matches, or only when a newly available show appears compared with the previous run?",
            ],
        }
    )

    message, context = service._run_tool_builder_task(
        session_id="session-1",
        user_content=(
            "Use BookMyShow first with District fallback, use current live listings, "
            "send alerts only for newly available shows, and configure interval in minutes with a 15 minute minimum."
        ),
        selected_model="gpt-5.4-mini",
    )

    assert "Build queued with your clarified requirements." in message
    assert context == {"requestId": "req-clarified", "phase": "building"}
    tool_builder_service.start_generation.assert_called_once()
    assert tool_builder_service.start_generation.call_args.kwargs["model"] == "gpt-5.4-mini"
    assert tool_builder_service.start_generation.call_args.kwargs["prompt_already_refined"] is True


def test_tool_builder_follow_up_only_asks_unresolved_questions_from_codex() -> None:
    codex_service = Mock()
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs=(
            '{"needsClarification": true, "questions": ['
            '"Should the polling interval be configured in minutes only, and is 5 minutes the minimum allowed interval?"], '
            '"clarifiedRequest": "Use the Sony website for PlayStation digital games and send alerts every polling run when price conditions match."}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=Mock(),  # type: ignore[arg-type]
    )
    service._resolve_tool_builder_context = Mock(  # type: ignore[method-assign]
        return_value={
            "phase": "clarification",
            "draftPrompt": "Build a tool for PlayStation digital game price alerts.",
            "pendingQuestions": [
                "Which source should be treated as authoritative for the price data?",
                "When should alerts fire?",
            ],
        }
    )

    message, _context = service._run_tool_builder_task(
        session_id="session-1",
        user_content="1. sony website 2. every time alert",
    )

    assert "polling interval" in message
    assert "authoritative" not in message
    assert "alerts fire" not in message


def test_modification_clarification_prompt_uses_original_request_and_change_history() -> None:
    prompt = ChatService._build_tool_builder_modification_clarification_analysis_prompt(  # pylint: disable=protected-access
        change_request="Add a delete watcher action.",
        tool={"name": "Movie Alerts", "status": "RUNNING"},
        request_state={
            "prompt": "Add delete watcher action.",
            "initialPrompt": "Build a movie watcher tool with email alerts.",
            "latestPrompt": "Add delete watcher action.",
            "promptHistory": [
                {"kind": "initial", "prompt": "Build a movie watcher tool with email alerts."},
                {"kind": "modification", "prompt": "Add snooze support for alerts."},
            ],
            "refinedPrompt": "Track movies, send alerts, and support snooze.",
            "status": "RUNNING",
        },
    )

    assert "Original tool request:" in prompt
    assert "Build a movie watcher tool with email alerts." in prompt
    assert "Applied modification history:" in prompt
    assert "Add snooze support for alerts." in prompt
    assert "Latest refined requirements:" in prompt


def test_modify_chat_uses_codex_clarification_before_rebuild() -> None:
    codex_service = Mock()
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs=(
            '{"needsClarification": true, "questions": ['
            '"Should deleting a watcher remove only the watch configuration, or also delete its historical alert log entries?"], '
            '"clarifiedRequest": "Add an option to delete an existing movie watcher from the tool."}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    tool_builder_service = Mock()
    tool_builder_service.get_tool.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Movie Alerts",
        "status": "RUNNING",
    }
    tool_builder_service.get_job_state.return_value = {
        "prompt": "Build a movie watcher tool.",
        "refinedPrompt": "Track movies and send alerts.",
        "status": "RUNNING",
        "error": None,
    }
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
    )
    service._resolve_tool_builder_context = Mock(  # type: ignore[method-assign]
        return_value={
            "requestId": "request-1",
            "toolId": "tool-1",
            "toolName": "Movie Alerts",
            "phase": "running",
        }
    )

    message, context = service._run_tool_builder_task(
        session_id="session-1",
        user_content="can you also provide an option to delete an added movie watcher",
    )

    assert "historical alert log entries" in message
    assert context is not None
    assert context["phase"] == "modification_clarification"
    tool_builder_service.start_generation.assert_not_called()


def test_modify_chat_clarification_reply_starts_rebuild() -> None:
    codex_service = Mock()
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs=(
            '{"needsClarification": false, "questions": [], '
            '"clarifiedRequest": "Add an option to delete an existing movie watcher while preserving historical alert logs."}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    tool_builder_service = Mock()
    tool_builder_service.start_generation.return_value = {"id": "request-1", "status": "PENDING"}
    tool_builder_service.get_job_state.return_value = {
        "prompt": "Build a movie watcher tool.",
        "refinedPrompt": "Track movies and send alerts.",
        "status": "RUNNING",
        "error": None,
    }
    tool_builder_service.get_tool.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Movie Alerts",
        "status": "RUNNING",
    }
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
    )
    service._resolve_tool_builder_context = Mock(  # type: ignore[method-assign]
        return_value={
            "phase": "modification_clarification",
            "requestId": "request-1",
            "toolId": "tool-1",
            "toolName": "Movie Alerts",
            "draftChangeRequest": "can you also provide an option to delete an added movie watcher",
            "pendingQuestions": [
                "Should deleting a watcher remove only the watch configuration, or also delete its historical alert log entries?"
            ],
        }
    )

    message, context = service._run_tool_builder_task(
        session_id="session-1",
        user_content="Preserve the historical alert logs.",
        selected_model="gpt-5.4-mini",
    )

    assert "Started applying your clarified changes" in message
    assert context == {"requestId": "request-1", "toolId": "tool-1", "toolName": "Movie Alerts", "phase": "building"}
    tool_builder_service.start_generation.assert_called_once()
    assert tool_builder_service.start_generation.call_args.kwargs["model"] == "gpt-5.4-mini"
    assert tool_builder_service.start_generation.call_args.kwargs["prompt_already_refined"] is True
    assert (
        tool_builder_service.start_generation.call_args.kwargs["prompt"]
        == "Add an option to delete an existing movie watcher while preserving historical alert logs."
    )
    assert (
        "Apply the requested change to the existing tool codebase."
        in tool_builder_service.start_generation.call_args.kwargs["workflow_prompt"]
    )


def test_modify_chat_tool_question_answers_without_starting_rebuild() -> None:
    codex_service = Mock()
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs="The tool stores price history in MongoDB collections and reads connection settings from environment variables.",
    )
    tool_builder_service = Mock()
    tool_builder_service.get_tool.return_value = {
        "toolId": "tool-1",
        "requestId": "request-1",
        "name": "Price Tracker",
        "status": "RUNNING",
        "uiPort": 3010,
        "ports": {"app": 3010},
    }
    tool_builder_service.get_job_state.return_value = {
        "prompt": "Build a price tracker tool.",
        "refinedPrompt": "Persist products and price history in MongoDB.",
        "status": "RUNNING",
        "error": None,
    }
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
    )
    service._resolve_tool_builder_context = Mock(  # type: ignore[method-assign]
        return_value={
            "requestId": "request-1",
            "toolId": "tool-1",
            "toolName": "Price Tracker",
            "phase": "running",
        }
    )

    message, context = service._run_tool_builder_task(
        session_id="session-1",
        user_content="How does this tool store price history?",
        selected_model="gpt-5.4-mini",
    )

    assert "MongoDB collections" in message
    assert context == {
        "requestId": "request-1",
        "toolId": "tool-1",
        "toolName": "Price Tracker",
        "phase": "running",
    }
    tool_builder_service.start_generation.assert_not_called()
    codex_service.run_chat.assert_called_once()
    assert codex_service.run_chat.call_args.kwargs["model"] == "gpt-5.4-mini"


def test_summarize_tool_builder_request_text_extracts_nested_change_request() -> None:
    value = (
        "You are generating a production-ready Python tool for container deployment.\n"
        "Mandatory requirements:\n- Keep tests passing.\n\n"
        "User request:\n"
        "Apply the requested change to the existing tool codebase.\n"
        "Inspect the current workspace first, then make targeted updates.\n\n"
        "Existing tool context:\n- Tool name: Price Tracker\n\n"
        "Change request:\n"
        "Add bulk actions to archive tracked products from the dashboard.\n\n"
        "Requirements:\n"
        "- Treat this as a focused modification request.\n"
    )

    summarized = ChatService._summarize_tool_builder_request_text(value)

    assert summarized == "Add bulk actions to archive tracked products from the dashboard."


class _StubSessionRepository:
    def __init__(self) -> None:
        now = now_ist()
        self._session = {
            "id": "session-1",
            "title": "Operator Session",
            "mode": "operator",
            "model": None,
            "archived": False,
            "createdAt": now,
            "updatedAt": now,
        }
        self.touched = False

    def get_by_id(self, session_id: str) -> dict | None:
        if session_id != self._session["id"]:
            return None
        return dict(self._session)

    def touch(self, session_id: str) -> None:
        if session_id == self._session["id"]:
            self.touched = True

    def update(self, session_id: str, title: str | None = None, **_: object) -> dict | None:
        if session_id != self._session["id"]:
            return None
        if title is not None:
            self._session["title"] = title
        self._session["updatedAt"] = now_ist()
        return dict(self._session)

    def list_recent(self, limit: int = 100) -> list[dict]:
        _ = limit
        return [dict(self._session)]


class _StubMessageRepository:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self._counter = 0

    def list_recent_for_session(self, session_id: str, limit: int = 20) -> list[dict]:
        _ = limit
        now = now_ist()
        if self.created:
            return [dict(item) for item in self.created]
        return [
            {
                "id": "seed-message",
                "sessionId": session_id,
                "role": "assistant",
                "content": "Seed context",
                "metadata": {},
                "createdAt": now,
            }
        ]

    def create(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        self._counter += 1
        message = {
            "id": f"m-{self._counter}",
            "sessionId": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "createdAt": now_ist(),
        }
        self.created.append(message)
        return message


class _StubCodexService:
    @staticmethod
    def is_supported_chat_model(model: str) -> bool:
        _ = model
        return True

    @staticmethod
    def default_chat_model() -> str | None:
        return None

    @staticmethod
    def run_chat(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SimpleNamespace(success=False, logs="")

    @staticmethod
    def clean_cli_output(value: str) -> str:
        return value


def test_stream_message_persists_assistant_before_final_delta_stream() -> None:
    session_repository = _StubSessionRepository()
    message_repository = _StubMessageRepository()
    service = ChatService(
        session_repository=session_repository,  # type: ignore[arg-type]
        message_repository=message_repository,  # type: ignore[arg-type]
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
    )
    service._run_operator_task = Mock(return_value="A" * 180)  # type: ignore[method-assign]

    stream = service.stream_message(session_id="session-1", content="Please check the operator run.")
    assert next(stream)["type"] == "user_message"
    assert next(stream)["type"] == "status"
    assert next(stream)["type"] == "assistant_delta"

    created_roles = [message["role"] for message in message_repository.created]
    assert created_roles == ["user", "assistant"]
    assert session_repository.touched is True


def test_stop_active_stream_stops_codex_for_session() -> None:
    session_repository = Mock()
    session_repository.get_by_id.return_value = {"id": "session-1"}
    codex_service = Mock()

    service = ChatService(
        session_repository=session_repository,
        message_repository=Mock(),
        codex_service=codex_service,
    )

    stopped, error = service.stop_active_stream("session-1")

    assert stopped is True
    assert error is None
    codex_service.stop_chat.assert_called_once_with(session_id="session-1")


def test_stop_active_stream_returns_not_found_for_unknown_session() -> None:
    session_repository = Mock()
    session_repository.get_by_id.return_value = None
    codex_service = Mock()

    service = ChatService(
        session_repository=session_repository,
        message_repository=Mock(),
        codex_service=codex_service,
    )

    stopped, error = service.stop_active_stream("missing-session")

    assert stopped is False
    assert error == "Session not found"
    codex_service.stop_chat.assert_not_called()
