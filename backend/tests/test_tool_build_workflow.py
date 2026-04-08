from types import SimpleNamespace
from unittest.mock import Mock

from app.models.status import BuildStatus
from app.services.docker_service import CommandResult
from app.workflows.tool_build_workflow import ToolBuildWorkflow


def test_infer_name_from_prompt_keeps_name_meaningful_and_short() -> None:
    prompt = (
        "Build a production-ready tool based on this request: "
        "Build an expense tracker dashboard with monthly trend charts and CSV export. "
        "Requirements: keep it lightweight."
    )
    inferred = ToolBuildWorkflow._infer_name_from_prompt(prompt)  # pylint: disable=protected-access
    words = inferred.split()

    assert 2 <= len(words) <= 4
    assert "expense" in inferred
    assert "tracker" in inferred


def test_infer_name_from_prompt_falls_back_to_two_words() -> None:
    inferred = ToolBuildWorkflow._infer_name_from_prompt("Build this.")  # pylint: disable=protected-access
    assert inferred == "task assistant"


def test_to_display_name_limits_words_and_preserves_api_token() -> None:
    display = ToolBuildWorkflow._to_display_name("sales report api generator service")  # pylint: disable=protected-access
    assert display == "Sales Report API Generator"


def test_infer_name_from_tool_that_phrase_keeps_meaningful_keywords() -> None:
    prompt = "build a tool that searches for all trending tech news articles from today and shows it on the screen."
    inferred = ToolBuildWorkflow._infer_name_from_prompt(prompt)  # pylint: disable=protected-access
    words = inferred.split()

    assert 2 <= len(words) <= 4
    assert "tech" in inferred
    assert "news" in inferred


def test_infer_name_from_prompt_prefers_product_context_over_build_details() -> None:
    prompt = "Create a full-stack app with analytics widgets, API integration, auth, and tests."
    inferred = ToolBuildWorkflow._infer_name_from_prompt(prompt)  # pylint: disable=protected-access

    assert inferred == "analytics dashboard"


def test_infer_name_from_prompt_uses_domain_and_intent_together() -> None:
    prompt = "Make something that summarizes customer support tickets by theme and urgency."
    inferred = ToolBuildWorkflow._infer_name_from_prompt(prompt)  # pylint: disable=protected-access

    assert inferred == "customer support tickets summarizer"


def test_infer_name_from_prompt_ignores_filler_words_in_enumerated_request() -> None:
    prompt = (
        "i need a tool which does this. "
        "1. ability to pick cities, movies, language, format of the movie, date range. "
        "2. send email alerts when matching shows exist."
    )
    inferred = ToolBuildWorkflow._infer_name_from_prompt(prompt)  # pylint: disable=protected-access

    assert "which" not in inferred
    assert "does" not in inferred
    assert "movies" in inferred


def test_effective_request_context_prompt_preserves_original_request_and_changes() -> None:
    prompt = ToolBuildWorkflow._effective_request_context_prompt(  # pylint: disable=protected-access
        {
            "prompt": "Add delete watcher action.",
            "initialPrompt": "Build a movie watcher tool with email alerts.",
            "latestPrompt": "Add delete watcher action.",
            "promptHistory": [
                {"kind": "initial", "prompt": "Build a movie watcher tool with email alerts."},
                {"kind": "modification", "prompt": "Add snooze support for alerts."},
                {"kind": "modification", "prompt": "Add delete watcher action."},
            ],
        }
    )

    assert "Original tool request:" in prompt
    assert "Build a movie watcher tool with email alerts." in prompt
    assert "Modification history:" in prompt
    assert "Add snooze support for alerts." in prompt
    assert "Latest user request:" in prompt


def test_workflow_records_agent_handoff_artifacts() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
        tool_builder_model="gpt-5.3-codex",
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-agents",
        "prompt": "Build a movie alert tool",
        "status": BuildStatus.PENDING.value,
    }
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(success=False, exit_code=1, logs="generation failed")
    codex_service.clean_cli_output.return_value = "generation failed"
    build_log_artifact_repository = Mock()

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=build_log_artifact_repository,  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    workflow._run(request_id="req-agents", tool_name_hint=None)  # pylint: disable=protected-access

    recorded_steps = [call.kwargs["step"] for call in build_log_artifact_repository.add_artifact.call_args_list]
    assert "agent_visionary" in recorded_steps
    assert "agent_blueprint" in recorded_steps
    assert "agent_backend_engineer" in recorded_steps
    assert "agent_frontend_engineer" in recorded_steps
    assert "agent_guardian" in recorded_steps
    assert "agent_shipmaster" in recorded_steps
    assert "agent_craftsman_input" in recorded_steps


def test_workflow_uses_default_tool_builder_model_for_generation() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
        tool_builder_model="gpt-5.3-codex",
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-model",
        "prompt": "Build a price tracker tool",
        "status": BuildStatus.PENDING.value,
    }
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(success=False, exit_code=1, logs="generation failed")
    codex_service.clean_cli_output.return_value = "generation failed"
    build_log_artifact_repository = Mock()

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=build_log_artifact_repository,  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    workflow._run(request_id="req-model", tool_name_hint=None)  # pylint: disable=protected-access

    assert codex_service.run_generation.call_args.kwargs["model"] == "gpt-5.3-codex"
    build_log_artifact_repository.add_artifact.assert_any_call(
        request_id="req-model",
        step="generate_attempt_1",
        file_name="generate-attempt-1.log",
        content="generation failed",
        content_type="text/plain; charset=utf-8",
    )


def test_workflow_forwards_image_paths_to_generation_attempts() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
        tool_builder_model="gpt-5.3-codex",
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-images",
        "prompt": "Build a dashboard",
        "status": BuildStatus.PENDING.value,
    }
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(success=False, exit_code=1, logs="generation failed")
    codex_service.clean_cli_output.return_value = "generation failed"

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    workflow._run(  # pylint: disable=protected-access
        request_id="req-images",
        tool_name_hint=None,
        image_paths=["/workspace/chat-session/attachments/reference-ui.png"],
    )

    assert codex_service.run_generation.call_args.kwargs["image_paths"] == [
        "/workspace/chat-session/attachments/reference-ui.png"
    ]


def test_workflow_tracks_generation_token_usage_from_logs() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
        tool_builder_model="gpt-5.3-codex",
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-token-usage",
        "prompt": "Build a notes app",
        "status": BuildStatus.PENDING.value,
    }
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(
        success=False,
        exit_code=1,
        logs="assistant: done\nTokens used: 1,200 input, 345 output",
    )
    codex_service.clean_cli_output.return_value = "assistant: done\nTokens used: 1,200 input, 345 output"

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    workflow._run(request_id="req-token-usage", tool_name_hint=None)  # pylint: disable=protected-access

    request_repository.increment_token_usage.assert_called_once_with(
        request_id="req-token-usage",
        prompt_tokens=1200,
        completion_tokens=345,
        total_tokens=1545,
        token_source="parsed",
    )


def test_workflow_skips_prompt_refinement_when_prompt_is_already_refined() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
        tool_builder_model="gpt-5.3-codex",
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-refined",
        "prompt": "Build a price tracker tool",
        "status": BuildStatus.PENDING.value,
    }
    prompt_service = Mock()
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(success=False, exit_code=1, logs="generation failed")
    codex_service.clean_cli_output.return_value = "generation failed"

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    workflow._run(  # pylint: disable=protected-access
        request_id="req-refined",
        tool_name_hint=None,
        prompt_override="Build a production-ready tool based on this request:\nBuild a price tracker tool",
        prompt_already_refined=True,
    )

    prompt_service.refine_prompt.assert_not_called()
    request_repository.set_refined_prompt.assert_called_once_with(
        "req-refined",
        "Build a production-ready tool based on this request:\nBuild a price tracker tool",
    )


def test_workflow_fails_when_api_verification_fails() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-1",
        "prompt": "Build movie show search API",
        "status": BuildStatus.PENDING.value,
    }
    build_log_repository = Mock()
    tool_repository = Mock()
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(success=True, exit_code=0, logs="generated")
    testing_service = Mock()
    testing_service.preflight_validate.return_value = (True, "ok")
    testing_service.run_tests.return_value = CommandResult(success=True, exit_code=0, logs="tests passed")
    testing_service.smoke_test.return_value = (True, "Smoke test passed")
    testing_service.verify_runtime_apis.return_value = (
        False,
        "API verification failed: /movies returned server errors",
        {"failedPaths": ["/movies"]},
    )
    testing_service.should_cross_check_live_data.return_value = False
    docker_service = Mock()
    docker_service.build_image.return_value = CommandResult(success=True, exit_code=0, logs="image built")
    docker_service.run_probe_container.return_value = (True, "probe-1", "")
    docker_service.get_container_logs.return_value = "Traceback: api rules failed"
    port_allocator_service = Mock()
    port_allocator_service.allocate_port.return_value = 3456
    alert_service = Mock()

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=testing_service,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        port_allocator_service=port_allocator_service,  # type: ignore[arg-type]
        alert_service=alert_service,  # type: ignore[arg-type]
    )

    workflow._run(request_id="req-1", tool_name_hint=None)  # pylint: disable=protected-access

    testing_service.verify_runtime_apis.assert_called_once_with(
        request_id="req-1",
        host_port=3456,
        host="127.0.0.1",
    )
    docker_service.get_container_logs.assert_called_once_with("probe-1")
    docker_service.stop_container.assert_called_once_with("probe-1")
    tool_repository.create.assert_not_called()
    assert any(
        call.args[1] == BuildStatus.FAILED
        for call in request_repository.update_status.call_args_list
    )


def test_workflow_fails_when_dynamic_data_reliability_check_fails() -> None:
    settings = SimpleNamespace(
        max_build_attempts=1,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-2",
        "prompt": "Build movie show search API",
        "status": BuildStatus.PENDING.value,
    }
    build_log_repository = Mock()
    tool_repository = Mock()
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    codex_service.run_generation.return_value = CommandResult(success=True, exit_code=0, logs="generated")
    testing_service = Mock()
    testing_service.preflight_validate.return_value = (True, "ok")
    testing_service.run_tests.return_value = CommandResult(success=True, exit_code=0, logs="tests passed")
    testing_service.smoke_test.return_value = (True, "Smoke test passed")
    testing_service.verify_runtime_apis.return_value = (
        True,
        "API verification passed",
        {"failedPaths": []},
    )
    testing_service.assess_dynamic_data_reliability.return_value = (
        False,
        "Dynamic-data reliability check failed: static dataset files only.",
    )
    testing_service.should_cross_check_live_data.return_value = False
    docker_service = Mock()
    docker_service.build_image.return_value = CommandResult(success=True, exit_code=0, logs="image built")
    docker_service.run_probe_container.return_value = (True, "probe-2", "")
    port_allocator_service = Mock()
    port_allocator_service.allocate_port.return_value = 3550
    alert_service = Mock()

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=testing_service,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        port_allocator_service=port_allocator_service,  # type: ignore[arg-type]
        alert_service=alert_service,  # type: ignore[arg-type]
    )

    workflow._run(request_id="req-2", tool_name_hint=None)  # pylint: disable=protected-access

    testing_service.assess_dynamic_data_reliability.assert_called_once_with(
        request_id="req-2",
        prompt="Build movie show search API",
    )
    tool_repository.create.assert_not_called()
    assert any(call.args[1] == BuildStatus.FAILED for call in request_repository.update_status.call_args_list)


def test_workflow_fails_fast_on_non_retryable_generation_error() -> None:
    settings = SimpleNamespace(
        max_build_attempts=3,
        smoke_test_host="127.0.0.1",
        scraping_web_verify_timeout_seconds=30,
    )
    request_repository = Mock()
    request_repository.get_by_id.return_value = {
        "id": "req-limit",
        "prompt": "Build movie show search API",
        "status": BuildStatus.PENDING.value,
    }
    build_log_repository = Mock()
    tool_repository = Mock()
    prompt_service = Mock()
    prompt_service.refine_prompt.return_value = "refined prompt"
    codex_service = Mock()
    quota_error_logs = (
        "ERROR: You've hit your usage limit. Upgrade to Pro and purchase more credits.\n"
        "Visit https://chatgpt.com/codex/settings/usage."
    )
    codex_service.run_generation.return_value = CommandResult(success=False, exit_code=1, logs=quota_error_logs)
    codex_service.clean_cli_output.return_value = quota_error_logs
    testing_service = Mock()
    docker_service = Mock()
    port_allocator_service = Mock()
    alert_service = Mock()

    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=request_repository,  # type: ignore[arg-type]
        build_log_repository=build_log_repository,  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        prompt_service=prompt_service,  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=testing_service,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
        port_allocator_service=port_allocator_service,  # type: ignore[arg-type]
        alert_service=alert_service,  # type: ignore[arg-type]
    )

    workflow._run(request_id="req-limit", tool_name_hint=None)  # pylint: disable=protected-access

    assert codex_service.run_generation.call_count == 1
    testing_service.preflight_validate.assert_not_called()
    failed_calls = [
        call
        for call in request_repository.update_status.call_args_list
        if len(call.args) >= 2 and call.args[1] == BuildStatus.FAILED
    ]
    assert failed_calls
    failure_reason = failed_calls[-1].kwargs.get("error", "")
    assert "usage limits" in failure_reason.lower()


def test_scrape_verification_allows_inconclusive_web_result() -> None:
    settings = SimpleNamespace(scraping_web_verify_timeout_seconds=30)
    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=Mock(),  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=Mock(),  # type: ignore[arg-type]
        codex_service=Mock(),  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )
    workflow._codex_service.run_chat.return_value = CommandResult(success=False, exit_code=1, logs="timed out")  # type: ignore[attr-defined] # pylint: disable=protected-access

    ok, message = workflow._verify_scraped_api_output_with_web(  # pylint: disable=protected-access
        request_id="req-verify",
        attempt=1,
        request_prompt="Track PlayStation game prices from Sony",
        api_report={"sampleTerms": ["The Last of Us Part I", "Rs 2,999"]},
    )

    assert ok is True
    assert "inconclusive" in message.lower()
    assert "allowing deployment" in message.lower()


def test_scrape_verification_fails_only_on_strong_mismatch() -> None:
    settings = SimpleNamespace(scraping_web_verify_timeout_seconds=30)
    codex_service = Mock()
    codex_service.run_chat.return_value = CommandResult(
        success=True,
        exit_code=0,
        logs=(
            '{"matches": false, "confidence": 0.91, "reason": "API titles do not appear on Sony store pages.", '
            '"sources": ["https://store.playstation.com/en-in/pages/latest"], '
            '"overlap": [], "missingFromApi": ["Marvels Spider-Man 2"], "unexpectedInApi": ["Fake Game 1", "Fake Game 2"]}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=Mock(),  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=Mock(),  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    ok, message = workflow._verify_scraped_api_output_with_web(  # pylint: disable=protected-access
        request_id="req-verify",
        attempt=1,
        request_prompt="Track PlayStation game prices from Sony",
        api_report={"sampleTerms": ["Fake Game 1", "Fake Game 2"]},
    )

    assert ok is False
    assert "mismatch" in message.lower()


def test_scrape_verification_allows_minor_differences() -> None:
    settings = SimpleNamespace(scraping_web_verify_timeout_seconds=30)
    codex_service = Mock()
    codex_service.run_chat.return_value = CommandResult(
        success=True,
        exit_code=0,
        logs=(
            '{"matches": false, "confidence": 0.61, "reason": "Most titles line up but one price variant differs.", '
            '"sources": ["https://store.playstation.com/en-in/pages/latest"], '
            '"overlap": ["Ghost of Tsushima Director\'s Cut"], '
            '"missingFromApi": ["Ghost of Tsushima"], "unexpectedInApi": []}'
        ),
    )
    codex_service.clean_cli_output.side_effect = lambda value: value
    workflow = ToolBuildWorkflow(
        settings=settings,  # type: ignore[arg-type]
        request_repository=Mock(),  # type: ignore[arg-type]
        build_log_repository=Mock(),  # type: ignore[arg-type]
        build_log_artifact_repository=Mock(),  # type: ignore[arg-type]
        tool_repository=Mock(),  # type: ignore[arg-type]
        prompt_service=Mock(),  # type: ignore[arg-type]
        codex_service=codex_service,  # type: ignore[arg-type]
        testing_service=Mock(),  # type: ignore[arg-type]
        docker_service=Mock(),  # type: ignore[arg-type]
        port_allocator_service=Mock(),  # type: ignore[arg-type]
        alert_service=Mock(),  # type: ignore[arg-type]
    )

    ok, message = workflow._verify_scraped_api_output_with_web(  # pylint: disable=protected-access
        request_id="req-verify",
        attempt=1,
        request_prompt="Track PlayStation game prices from Sony",
        api_report={"sampleTerms": ["Ghost of Tsushima Director's Cut"]},
    )

    assert ok is True
    assert "minor differences" in message.lower()
