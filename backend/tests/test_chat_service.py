from types import SimpleNamespace
from unittest.mock import Mock, patch

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


def test_docker_summary_query_ignores_debug_request() -> None:
    prompt = "Show me docker status and debug why toolhub-api keeps restarting"
    assert ChatService._is_docker_summary_query(prompt) is False


def test_explicit_system_resource_query_ignores_diagnostic_prompt() -> None:
    prompt = "memory leak issue in worker service"
    assert ChatService._is_explicit_system_resource_query(prompt) is False


def test_explicit_system_resource_query_accepts_status_prompt() -> None:
    prompt = "show current cpu and memory usage"
    assert ChatService._is_explicit_system_resource_query(prompt) is True


def test_operator_quick_reply_disabled_even_for_simple_operator_prompts() -> None:
    assert ChatService._should_use_operator_quick_reply("operator", "show cpu usage") is False


def test_should_capture_execution_logs_includes_tool_builder_mode() -> None:
    assert ChatService._should_capture_execution_logs("tool_builder") is True  # pylint: disable=protected-access


def test_operator_task_complexity_classifies_debug_request_as_complex() -> None:
    prompt = "debug failing deployment pipeline and inspect logs from previous run"
    assert ChatService._operator_task_complexity(prompt) == "complex"


def test_operator_model_for_simple_task_prefers_fast_model() -> None:
    codex_service = Mock()
    codex_service.default_chat_model.return_value = "gpt-5.4"
    codex_service.list_chat_models.return_value = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"]
    codex_service.is_supported_chat_model.return_value = True
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
    )

    selected = service._operator_model_for_task(  # pylint: disable=protected-access
        selected_model="gpt-5.4",
        user_content="summarize disk usage in this repo",
    )

    assert selected == "gpt-5.4-mini"


def test_operator_model_for_action_task_keeps_selected_model() -> None:
    codex_service = Mock()
    codex_service.default_chat_model.return_value = "gpt-5.4"
    codex_service.list_chat_models.return_value = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"]
    codex_service.is_supported_chat_model.return_value = True
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=codex_service,  # type: ignore[arg-type]
    )

    selected = service._operator_model_for_task(  # pylint: disable=protected-access
        selected_model="gpt-5.4",
        user_content="fix memory leak in api service",
    )

    assert selected == "gpt-5.4"


def test_operator_timeout_seconds_shortens_simple_tasks() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
    )

    timeout = service._operator_timeout_seconds_for_task("show current disk usage")  # pylint: disable=protected-access
    assert timeout == 480


def test_operator_timeout_seconds_keeps_debug_tasks_high() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
    )

    timeout = service._operator_timeout_seconds_for_task(  # pylint: disable=protected-access
        "debug and troubleshoot failing deployment pipeline"
    )
    assert timeout == 1800


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


def test_try_direct_operator_answer_rejects_diagnostic_memory_prompt() -> None:
    system_context_service = Mock()
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        system_context_service=system_context_service,  # type: ignore[arg-type]
    )

    answer = service._try_direct_operator_answer("operator", "memory leak issue in backend worker")  # pylint: disable=protected-access

    assert answer is None
    system_context_service.snapshot.assert_not_called()


def test_try_direct_operator_answer_rejects_health_check_with_restart_action() -> None:
    docker_service = Mock()
    system_context_service = Mock()
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        system_context_service=system_context_service,  # type: ignore[arg-type]
    )

    answer = service._try_direct_operator_answer(  # pylint: disable=protected-access
        "operator",
        "check if api container is healthy and restart it if needed",
    )

    assert answer is None
    docker_service.get_container_health.assert_not_called()


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


def test_build_tool_initial_prompt_requires_python_pytest_and_mongo_contract() -> None:
    prompt = ChatService._build_tool_initial_prompt("Build a movie alert tool.")

    assert "Use Python 3.11." in prompt
    assert "Include `requirements.txt`." in prompt
    assert "python -m pytest -q" in prompt
    assert "Place tests in `tests/`" in prompt
    assert "`MONGO_URI` and `MONGO_DB_NAME`" in prompt
    assert "do not crash the HTTP server" in prompt


def test_build_tool_modification_prompt_preserves_runtime_contracts() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
    )

    prompt = service._build_tool_modification_prompt(
        user_content="Add delete support.",
        tool=None,
        request_state=None,
    )

    assert "requirements.txt" in prompt
    assert "python -m pytest -q" in prompt
    assert "`MONGO_URI` and `MONGO_DB_NAME`" in prompt
    assert "Do not let synchronous Mongo startup failures crash the HTTP server" in prompt


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


def test_tool_builder_with_image_attachment_forwards_visual_context_to_generation() -> None:
    codex_service = Mock()
    codex_service.run_chat.side_effect = [
        SimpleNamespace(
            success=True,
            logs=(
                "assistant: 1. Visual Direction\n"
                "- Use a compact dashboard layout with strong card hierarchy.\n"
                "2. Layout Structure\n"
                "- Keep two-column desktop layout and stacked mobile flow."
            ),
        ),
        SimpleNamespace(
            success=True,
            logs='{"needsClarification": false, "questions": [], "clarifiedRequest": ""}',
        ),
    ]
    codex_service.clean_cli_output.side_effect = lambda value: value
    tool_builder_service = Mock()
    tool_builder_service.start_generation.return_value = {"id": "req-image", "status": "PENDING"}
    message_repository = Mock()
    message_repository.list_recent_for_session.return_value = []

    service = ChatService(
        session_repository=Mock(),
        message_repository=message_repository,
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
    )

    message, context = service._run_tool_builder_task(
        session_id="session-1",
        user_content="Build a project management dashboard and match the attached screenshot style.",
        selected_model="gpt-5.4-mini",
        attachments=[
            {
                "id": "att-1",
                "fileName": "reference-ui.png",
                "contentType": "image/png",
                "size": 1024,
                "containerPath": "/workspace/chat-session/attachments/reference-ui.png",
                "url": "/chat/sessions/session-1/attachments/att-1",
            }
        ],
    )

    assert "Build queued." in message
    assert context == {"requestId": "req-image", "phase": "building"}
    assert codex_service.run_chat.call_count == 2

    visual_call = codex_service.run_chat.call_args_list[0].kwargs
    assert visual_call["session_id"] == "session-1-tool-visual-brief"
    assert visual_call["image_paths"] == ["/workspace/chat-session/attachments/reference-ui.png"]

    clarification_call = codex_service.run_chat.call_args_list[1].kwargs
    assert clarification_call["session_id"] == "session-1-tool-clarify"
    assert clarification_call["image_paths"] == ["/workspace/chat-session/attachments/reference-ui.png"]

    starter_kwargs = tool_builder_service.start_generation.call_args.kwargs
    assert starter_kwargs["image_paths"] == ["/workspace/chat-session/attachments/reference-ui.png"]
    assert "Visual reference requirements" in starter_kwargs["prompt"]


def test_send_message_for_tool_builder_forwards_resolved_attachments_to_handler() -> None:
    session_repository = Mock()
    session_repository.get_by_id.return_value = {
        "id": "session-1",
        "title": "Tool Builder Session",
        "mode": "tool_builder",
        "model": "gpt-5.4",
    }
    message_repository = Mock()
    message_repository.list_recent_for_session.side_effect = [[], []]
    message_repository.create.side_effect = [
        {
            "id": "user-1",
            "sessionId": "session-1",
            "role": "user",
            "content": "Please use the attached screenshot.",
            "metadata": {},
            "createdAt": now_ist(),
        },
        {
            "id": "assistant-1",
            "sessionId": "session-1",
            "role": "assistant",
            "content": "Build queued.",
            "metadata": {},
            "createdAt": now_ist(),
        },
    ]
    codex_service = Mock()
    codex_service.is_supported_chat_model.return_value = True
    codex_service.default_chat_model.return_value = "gpt-5.4"
    codex_service.run_chat.return_value = SimpleNamespace(success=True, logs="assistant: ok", exit_code=0)

    service = ChatService(
        session_repository=session_repository,
        message_repository=message_repository,
        codex_service=codex_service,  # type: ignore[arg-type]
        tool_builder_service=Mock(),  # type: ignore[arg-type]
    )
    service._resolve_message_attachments = Mock(  # type: ignore[method-assign]
        return_value=(
            [
                {
                    "id": "att-1",
                    "fileName": "ux.png",
                    "contentType": "image/png",
                    "size": 100,
                    "containerPath": "/workspace/chat-session/attachments/ux.png",
                    "url": "/chat/sessions/session-1/attachments/att-1",
                }
            ],
            None,
        )
    )
    service._run_tool_builder_task = Mock(  # type: ignore[method-assign]
        return_value=("Build queued.", {"requestId": "req-1", "phase": "building"})
    )

    _user_message, _assistant_message, error = service.send_message(
        session_id="session-1",
        content="Please use the attached screenshot.",
        attachment_ids=["att-1"],
    )

    assert error is None
    service._run_tool_builder_task.assert_called_once()  # type: ignore[attr-defined]
    forwarded = service._run_tool_builder_task.call_args.kwargs  # type: ignore[attr-defined]
    assert forwarded["attachments"] == [
        {
            "id": "att-1",
            "fileName": "ux.png",
            "contentType": "image/png",
            "size": 100,
            "containerPath": "/workspace/chat-session/attachments/ux.png",
            "url": "/chat/sessions/session-1/attachments/att-1",
        }
    ]


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
    service._run_operator_task = Mock(  # type: ignore[method-assign]
        return_value={"assistantText": "A" * 180, "rawLogs": "raw", "success": True, "exitCode": 0}
    )

    stream = service.stream_message(session_id="session-1", content="Please check the operator run.")
    assert next(stream)["type"] == "user_message"
    assert next(stream)["type"] == "status"
    assert next(stream)["type"] == "assistant_delta"

    created_roles = [message["role"] for message in message_repository.created]
    assert created_roles == ["user", "assistant"]
    assert session_repository.touched is True


def test_send_message_records_execution_log_for_general_chat() -> None:
    session_repository = Mock()
    session_repository.get_by_id.return_value = {
        "id": "session-1",
        "title": "General Session",
        "mode": "general",
        "model": "gpt-5.4",
    }
    message_repository = Mock()
    message_repository.list_recent_for_session.side_effect = [[], []]
    message_repository.create.side_effect = [
        {
            "id": "user-1",
            "sessionId": "session-1",
            "role": "user",
            "content": "How is the deployment?",
            "metadata": {},
            "createdAt": now_ist(),
        },
        {
            "id": "assistant-1",
            "sessionId": "session-1",
            "role": "assistant",
            "content": "Deployment looks healthy.",
            "metadata": {},
            "createdAt": now_ist(),
        },
    ]
    codex_service = Mock()
    codex_service.is_supported_chat_model.return_value = True
    codex_service.default_chat_model.return_value = "gpt-5.4"
    codex_service.run_chat.return_value = SimpleNamespace(
        success=True,
        logs="assistant: Deployment looks healthy.",
        exit_code=0,
    )
    chat_execution_log_repository = Mock()

    service = ChatService(
        session_repository=session_repository,
        message_repository=message_repository,
        codex_service=codex_service,  # type: ignore[arg-type]
        chat_execution_log_repository=chat_execution_log_repository,  # type: ignore[arg-type]
    )

    user_message, assistant_message, error = service.send_message(
        session_id="session-1",
        content="How is the deployment?",
    )

    assert error is None
    assert user_message is not None
    assert assistant_message is not None
    chat_execution_log_repository.create.assert_called_once()
    payload = chat_execution_log_repository.create.call_args.kwargs
    assert payload["session_id"] == "session-1"
    assert payload["mode"] == "general"
    assert payload["user_message_id"] == "user-1"
    assert payload["assistant_message_id"] == "assistant-1"
    assert payload["quick_path"] is False
    assert payload["success"] is True
    assert payload["total_tokens"] > 0
    assert payload["token_source"] in {"parsed", "estimated", "mixed"}


def test_send_message_records_execution_log_for_operator_without_quick_path() -> None:
    session_repository = Mock()
    session_repository.get_by_id.return_value = {
        "id": "session-1",
        "title": "Operator Session",
        "mode": "operator",
        "model": "gpt-5.4",
    }
    message_repository = Mock()
    message_repository.list_recent_for_session.side_effect = [[], []]
    message_repository.create.side_effect = [
        {
            "id": "user-1",
            "sessionId": "session-1",
            "role": "user",
            "content": "show cpu usage",
            "metadata": {},
            "createdAt": now_ist(),
        },
        {
            "id": "assistant-1",
            "sessionId": "session-1",
            "role": "assistant",
            "content": "CPU: 23.5% usage right now.",
            "metadata": {},
            "createdAt": now_ist(),
        },
    ]
    codex_service = Mock()
    codex_service.is_supported_chat_model.return_value = True
    codex_service.default_chat_model.return_value = "gpt-5.4"
    chat_execution_log_repository = Mock()
    system_context_service = Mock()
    system_context_service.format_for_prompt.return_value = "Runtime telemetry snapshot."
    system_context_service.snapshot.return_value = {
        "cpu": {"percent": 23.5},
        "memory": {"percent": 49.0, "usedBytes": 4_000_000_000, "totalBytes": 8_000_000_000},
        "disk": {"percent": 70.0, "usedBytes": 10_000_000_000, "totalBytes": 20_000_000_000},
        "battery": None,
        "uptimeSeconds": 1200,
        "isContainer": True,
    }

    service = ChatService(
        session_repository=session_repository,
        message_repository=message_repository,
        codex_service=codex_service,  # type: ignore[arg-type]
        system_context_service=system_context_service,  # type: ignore[arg-type]
        chat_execution_log_repository=chat_execution_log_repository,  # type: ignore[arg-type]
    )

    _user_message, _assistant_message, error = service.send_message(
        session_id="session-1",
        content="show cpu usage",
    )

    assert error is None
    chat_execution_log_repository.create.assert_called_once()
    payload = chat_execution_log_repository.create.call_args.kwargs
    assert payload["session_id"] == "session-1"
    assert payload["mode"] == "operator"
    assert payload["quick_path"] is False
    assert payload["total_tokens"] > 0


def test_send_message_records_execution_log_when_chat_execution_raises() -> None:
    session_repository = _StubSessionRepository()
    session_repository._session["mode"] = "general"  # pylint: disable=protected-access
    session_repository._session["model"] = "gpt-5.4"  # pylint: disable=protected-access
    message_repository = _StubMessageRepository()
    codex_service = Mock()
    codex_service.is_supported_chat_model.return_value = True
    codex_service.default_chat_model.return_value = "gpt-5.4"
    codex_service.run_chat.side_effect = RuntimeError("codex crashed while generating")
    chat_execution_log_repository = Mock()

    service = ChatService(
        session_repository=session_repository,  # type: ignore[arg-type]
        message_repository=message_repository,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        chat_execution_log_repository=chat_execution_log_repository,  # type: ignore[arg-type]
    )

    _user_message, assistant_message, error = service.send_message(
        session_id="session-1",
        content="Summarize service health.",
    )

    assert error is None
    assert assistant_message is not None
    assert "could not generate a response" in assistant_message["content"].lower()
    chat_execution_log_repository.create.assert_called_once()
    payload = chat_execution_log_repository.create.call_args.kwargs
    assert payload["success"] is False
    assert payload["exit_code"] == 1
    assert "codex crashed while generating" in payload["raw_logs"]


def test_stream_message_records_execution_log_when_stream_execution_raises() -> None:
    session_repository = _StubSessionRepository()
    session_repository._session["mode"] = "general"  # pylint: disable=protected-access
    session_repository._session["model"] = "gpt-5.4"  # pylint: disable=protected-access
    message_repository = _StubMessageRepository()
    codex_service = Mock()
    codex_service.is_supported_chat_model.return_value = True
    codex_service.default_chat_model.return_value = "gpt-5.4"

    def _broken_stream(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        if False:
            yield None
        raise RuntimeError("stream transport crashed")

    codex_service.stream_chat.side_effect = _broken_stream
    chat_execution_log_repository = Mock()

    service = ChatService(
        session_repository=session_repository,  # type: ignore[arg-type]
        message_repository=message_repository,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        chat_execution_log_repository=chat_execution_log_repository,  # type: ignore[arg-type]
    )

    events = list(service.stream_message(session_id="session-1", content="Summarize recent outages."))

    assert events[0]["type"] == "user_message"
    assert events[-1]["type"] == "done"
    assistant_event = next((event for event in events if event["type"] == "assistant_message"), None)
    assert assistant_event is not None
    assert "could not generate a response" in str(assistant_event["message"]["content"]).lower()
    chat_execution_log_repository.create.assert_called_once()
    payload = chat_execution_log_repository.create.call_args.kwargs
    assert payload["success"] is False
    assert payload["exit_code"] == 1
    assert "stream transport crashed" in payload["raw_logs"]


def test_run_operator_task_includes_raw_logs_when_operator_execution_raises() -> None:
    session_repository = Mock()
    session_repository.get_by_id.return_value = {
        "id": "session-1",
        "title": "Operator Session",
        "mode": "operator",
        "model": "gpt-5.4",
    }
    message_repository = Mock()
    message_repository.list_recent_for_session.return_value = []
    codex_service = Mock()
    codex_service.run_operator.side_effect = RuntimeError("operator runtime crashed")
    codex_service.default_chat_model.return_value = "gpt-5.4"
    codex_service.list_chat_models.return_value = ["gpt-5.4"]
    codex_service.is_supported_chat_model.return_value = True
    operator_access_service = Mock()
    operator_access_service.resolve_project_path.return_value = "/tmp"
    operator_access_service.normalize_cwd.return_value = "/tmp"
    operator_access_service.to_container_path.return_value = "/workspace/tmp"
    operator_access_service.build_mounts.return_value = {}
    operator_access_service.policy_summary.return_value = "Allowed paths: /tmp"

    service = ChatService(
        session_repository=session_repository,
        message_repository=message_repository,
        codex_service=codex_service,  # type: ignore[arg-type]
        operator_access_service=operator_access_service,  # type: ignore[arg-type]
    )

    result = service._run_operator_task(  # pylint: disable=protected-access
        session_id="session-1",
        user_content="check docker logs",
        selected_model="gpt-5.4",
    )

    assert result["success"] is False
    assert result["exitCode"] == 1
    assert "operator runtime crashed" in str(result["assistantText"])
    assert "operator runtime crashed" in str(result["rawLogs"])


def test_parse_token_usage_from_logs_parses_inline_input_output() -> None:
    logs = "assistant: done\nTokens used: 1,200 input, 345 output"
    usage = ChatService._parse_token_usage_from_logs(logs)  # pylint: disable=protected-access

    assert usage["promptTokens"] == 1200
    assert usage["completionTokens"] == 345
    assert usage["totalTokens"] == 1545


def test_parse_token_usage_from_logs_parses_labeled_token_lines() -> None:
    logs = (
        "assistant: completed\n"
        "Prompt tokens: 900\n"
        "Completion tokens: 150\n"
    )
    usage = ChatService._parse_token_usage_from_logs(logs)  # pylint: disable=protected-access

    assert usage["promptTokens"] == 900
    assert usage["completionTokens"] == 150
    assert usage["totalTokens"] == 1050


def test_derive_token_usage_estimates_when_logs_do_not_include_usage() -> None:
    usage = ChatService._derive_token_usage(  # pylint: disable=protected-access
        raw_logs="assistant: No explicit token usage line",
        user_content="Summarize the deployment status for backend and frontend services.",
        assistant_content="Deployment looks healthy across both services.",
    )

    assert usage["tokenSource"] == "estimated"
    assert usage["promptTokens"] is not None
    assert usage["completionTokens"] is not None
    assert usage["totalTokens"] > 0


def test_usage_cost_estimate_uses_live_usd_inr_rate_from_api() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        usd_inr_rate_api_url="https://open.er-api.com/v6/latest/USD",
        usd_inr_rate_timeout_seconds=2,
        usd_inr_rate_cache_ttl_seconds=1800,
        usd_inr_rate_fallback=83,
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"rates": {"INR": 86.25}}

    with patch("app.services.chat_service.requests.get", return_value=response) as get_mock:
        estimate = service._usage_cost_estimate(total_tokens=1_000_000)  # pylint: disable=protected-access

    assert estimate["usd"] == 2.0
    assert estimate["usdToInrRate"] == 86.25
    assert estimate["inr"] == 172.5
    assert estimate["usdToInrSource"] == "live_api"
    assert estimate["usdToInrLive"] is True
    assert estimate["usdToInrUpdatedAt"] is not None
    assert "live USD/INR" in estimate["note"]
    get_mock.assert_called_once()


def test_usage_cost_estimate_reuses_cached_rate_within_ttl() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        usd_inr_rate_api_url="https://open.er-api.com/v6/latest/USD",
        usd_inr_rate_timeout_seconds=2,
        usd_inr_rate_cache_ttl_seconds=1800,
        usd_inr_rate_fallback=83,
    )
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"conversion_rates": {"INR": 85.0}}

    with patch("app.services.chat_service.requests.get", return_value=response) as get_mock:
        first = service._usage_cost_estimate(total_tokens=100_000)  # pylint: disable=protected-access
        second = service._usage_cost_estimate(total_tokens=100_000)  # pylint: disable=protected-access

    assert first["usdToInrSource"] == "live_api"
    assert second["usdToInrSource"] == "live_cache"
    assert second["usdToInrRate"] == 85.0
    get_mock.assert_called_once()


def test_usage_cost_estimate_falls_back_when_fx_api_unavailable() -> None:
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        usd_inr_rate_api_url="https://open.er-api.com/v6/latest/USD",
        usd_inr_rate_timeout_seconds=2,
        usd_inr_rate_cache_ttl_seconds=1800,
        usd_inr_rate_fallback=83,
    )

    with patch("app.services.chat_service.requests.get", side_effect=RuntimeError("network down")) as get_mock:
        estimate = service._usage_cost_estimate(total_tokens=1_000_000)  # pylint: disable=protected-access

    assert estimate["usdToInrRate"] == 83.0
    assert estimate["usdToInrSource"] == "fallback_default"
    assert estimate["usdToInrLive"] is False
    assert "fallback USD/INR" in estimate["note"]
    get_mock.assert_called_once()


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


def test_summarize_usage_includes_tool_builder_runtime_usage() -> None:
    chat_execution_log_repository = Mock()
    chat_execution_log_repository.summarize_usage.return_value = {
        "totals": {
            "requests": 2,
            "promptTokens": 10,
            "completionTokens": 20,
            "totalTokens": 30,
            "parsedCount": 1,
            "estimatedCount": 1,
            "mixedCount": 0,
        },
        "modes": [
            {
                "mode": "general",
                "requests": 2,
                "promptTokens": 10,
                "completionTokens": 20,
                "totalTokens": 30,
                "parsedCount": 1,
                "estimatedCount": 1,
                "mixedCount": 0,
            }
        ],
    }
    tool_builder_service = Mock()
    tool_builder_service.summarize_token_usage.return_value = {
        "requestCount": 3,
        "promptTokens": 100,
        "completionTokens": 200,
        "totalTokens": 300,
        "parsedCount": 2,
        "estimatedCount": 1,
        "mixedCount": 0,
    }
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
        chat_execution_log_repository=chat_execution_log_repository,  # type: ignore[arg-type]
    )

    summary = service.summarize_usage(modes=["general", "tool_builder"])

    assert summary["requestCount"] == 5
    assert summary["promptTokens"] == 110
    assert summary["completionTokens"] == 220
    assert summary["totalTokens"] == 330
    assert summary["parsedCount"] == 3
    assert summary["estimatedCount"] == 2
    assert summary["mixedCount"] == 0
    tool_builder_row = next((row for row in summary["modes"] if row["mode"] == "tool_builder"), None)
    assert tool_builder_row is not None
    assert tool_builder_row["requestCount"] == 3
    assert tool_builder_row["totalTokens"] == 300
    tool_builder_service.summarize_token_usage.assert_called_once()


def test_summarize_usage_does_not_include_tool_builder_runtime_usage_for_session_filters() -> None:
    chat_execution_log_repository = Mock()
    chat_execution_log_repository.summarize_usage.return_value = {
        "totals": {
            "requests": 1,
            "promptTokens": 12,
            "completionTokens": 18,
            "totalTokens": 30,
            "parsedCount": 0,
            "estimatedCount": 1,
            "mixedCount": 0,
        },
        "modes": [
            {
                "mode": "tool_builder",
                "requests": 1,
                "promptTokens": 12,
                "completionTokens": 18,
                "totalTokens": 30,
                "parsedCount": 0,
                "estimatedCount": 1,
                "mixedCount": 0,
            }
        ],
    }
    tool_builder_service = Mock()
    service = ChatService(
        session_repository=Mock(),
        message_repository=Mock(),
        codex_service=_StubCodexService(),  # type: ignore[arg-type]
        tool_builder_service=tool_builder_service,  # type: ignore[arg-type]
        chat_execution_log_repository=chat_execution_log_repository,  # type: ignore[arg-type]
    )

    summary = service.summarize_usage(session_id="session-1", modes=["tool_builder"])

    assert summary["requestCount"] == 1
    assert summary["totalTokens"] == 30
    tool_builder_service.summarize_token_usage.assert_not_called()
