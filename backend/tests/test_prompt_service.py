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
