from app.services.prompt_service import PromptService


def test_refine_prompt_defaults_to_lightweight_ui() -> None:
    refined = PromptService().refine_prompt("Build a task tracker with a dashboard.")
    assert "Use a lightweight frontend approach" in refined
    assert "Use Next.js (React + TypeScript) for the frontend." not in refined
    assert "Derive clear acceptance criteria from the user request" in refined
    assert "validates core requested behavior beyond `/status`" in refined


def test_refine_prompt_skips_ui_for_backend_only_prompt() -> None:
    refined = PromptService().refine_prompt("Build a backend only API for notifications.")
    assert "UI requirements:" not in refined


def test_refine_prompt_allows_explicit_js_framework_requests() -> None:
    refined = PromptService().refine_prompt("Build a tool with React frontend and Python backend.")
    assert "User explicitly asked for a JS framework; honor that request." in refined
    assert "Use a lightweight frontend approach" not in refined


def test_refine_prompt_forces_lightweight_when_no_node_is_requested() -> None:
    refined = PromptService().refine_prompt("Build a React dashboard tool, but no node build tooling.")
    assert "Use a lightweight frontend approach" in refined
    assert "User explicitly asked for a JS framework; honor that request." not in refined


def test_refine_prompt_requires_clarification_and_tdd_flow() -> None:
    refined = PromptService().refine_prompt("Build a movie alert tool for me.")

    assert "Clarify requirements first" in refined
    assert "docs/test-cases.md" in refined
    assert "Write tests before backend implementation" in refined
    assert "run tests, then implement backend changes until tests pass" in refined


def test_refine_prompt_requires_api_verification_and_scraping_cross_check() -> None:
    refined = PromptService().refine_prompt("Build a scraping based movie listings checker.")

    assert "verify backend endpoints with real API calls" in refined
    assert "cross-check sampled API results with live web search evidence" in refined
    assert "Only publish/deploy after tests, runtime verification, and any required web cross-checks all pass" in refined
    assert "bounded retries" in refined


def test_refine_prompt_requires_shared_mongo_for_persistent_tools() -> None:
    refined = PromptService().refine_prompt("Build a price tracking tool with alerts and price history.")

    assert "reuse the platform MongoDB" in refined
    assert "`MONGO_URI` and `MONGO_DB_NAME`" in refined
    assert "Create tool-specific collections" in refined
    assert "Use Brevo for email alerts" in refined


def test_refine_prompt_avoids_unrelated_platform_integrations_for_simple_tools() -> None:
    refined = PromptService().refine_prompt(
        "Build a simple unit converter with a clean web UI and no database, alerts, scheduler, or external API."
    )

    assert "Do not add MongoDB/database persistence" in refined
    assert "Do not add Brevo/email alert plumbing" in refined
    assert "Do not add scheduler/cron workers" in refined
    assert "reuse the platform MongoDB" not in refined
    assert "Use Brevo for email alerts" not in refined


def test_refine_prompt_handles_do_not_add_comma_list_opt_outs() -> None:
    refined = PromptService().refine_prompt(
        "Build a habit tracker with MongoDB. Do not add email alerts, external APIs, authentication, or scheduler jobs."
    )

    assert "reuse the platform MongoDB" in refined
    assert "Do not add Brevo/email alert plumbing" in refined
    assert "Do not add scheduler/cron workers" in refined
    assert "Use Brevo for email alerts" not in refined
    assert "Implement scheduler/polling behavior" not in refined


def test_refine_prompt_defaults_ui_to_modern_domain_appropriate_theme() -> None:
    refined = PromptService().refine_prompt("Build a dashboard for monitoring background jobs.")

    assert "modern, polished, and production-ready" in refined
    assert "Choose a visual direction that fits the tool domain" in refined


def test_refine_prompt_defaults_timezone_to_ist() -> None:
    refined = PromptService().refine_prompt("Build a booking dashboard with daily reports and reminders.")

    assert "Asia/Kolkata" in refined
    assert "Default all user-facing dates, times, schedules, and cron behavior to IST" in refined
    assert "TZ=Asia/Kolkata" in refined


def test_refine_prompt_keeps_platform_build_brief_intact() -> None:
    prompt = (
        "Build a production-ready tool based on this request:\n"
        "Build a dashboard for monitoring jobs.\n\n"
        "Requirements:\n"
        "- Keep `docs/requirements.md` and `docs/test-cases.md` in sync.\n"
    )

    refined = PromptService().refine_prompt(prompt)

    assert refined == prompt.strip()
