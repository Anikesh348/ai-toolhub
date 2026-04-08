from app.services.tool_builder_agent_team import ToolBuilderAgentTeam


def test_agent_team_prepare_emits_structured_outputs() -> None:
    package = ToolBuilderAgentTeam().prepare(
        request_prompt=(
            "Build a movie alert tool that checks show availability in Chennai and sends email alerts. "
            "Include scheduler support and persistence."
        ),
        refined_prompt="Refined prompt with platform constraints.",
        request_context_prompt="Build a movie alert tool for Chennai.",
    )

    assert package.visionary.problem_statement
    assert package.visionary.user_stories
    assert package.visionary.acceptance_criteria
    assert package.blueprint.architecture_diagram
    assert package.blueprint.api_design
    assert package.backend_engineer.implementation_focus
    assert package.guardian.test_plan
    assert package.shipmaster.deployment_plan
    assert "Visionary -> Blueprint -> Backend Engineer" in package.craftsman_prompt
    assert "Frontend Engineer handoff" in package.craftsman_prompt


def test_agent_team_defaults_include_shared_mongo_and_brevo() -> None:
    package = ToolBuilderAgentTeam().prepare(
        request_prompt="Build a price tracker with alert notifications.",
        refined_prompt="Refined prompt with platform constraints.",
        request_context_prompt="Build a price tracker with alert notifications.",
    )

    combined_constraints = "\n".join(package.visionary.constraints_and_assumptions)
    combined_decisions = "\n".join(package.blueprint.tech_decisions)

    assert "shared MongoDB" in combined_constraints
    assert "Brevo" in combined_constraints
    assert "shared MongoDB" in combined_decisions
    assert "Brevo" in combined_decisions


def test_agent_team_respects_backend_only_requests() -> None:
    package = ToolBuilderAgentTeam().prepare(
        request_prompt="Build a backend only API for rule management and scheduling.",
        refined_prompt="Refined prompt with platform constraints.",
        request_context_prompt="Build a backend only API for rule management and scheduling.",
    )

    assert all("Frontend app" not in item for item in package.blueprint.component_breakdown)
    assert any("backend-only" in item.lower() for item in package.blueprint.tech_decisions)
    assert package.frontend_engineer is None
    assert "UX baseline requirements" not in package.craftsman_prompt


def test_agent_team_ui_requests_include_ux_baseline_contracts() -> None:
    package = ToolBuilderAgentTeam().prepare(
        request_prompt="Build a weather web app with city search.",
        refined_prompt="Refined prompt with platform constraints.",
        request_context_prompt="Build a weather web app with city search.",
    )

    visionary_text = "\n".join(package.visionary.acceptance_criteria)
    blueprint_text = "\n".join(package.blueprint.tech_decisions)
    guardian_text = "\n".join(package.guardian.test_cases)
    frontend_text = package.frontend_engineer.to_markdown() if package.frontend_engineer else ""

    assert "clear or reset action" in visionary_text
    assert "loading, empty-result, and error states" in visionary_text
    assert "UX baseline" in blueprint_text
    assert "UX clear/reset path" in guardian_text
    assert "Animation Contracts" in frontend_text
    assert "UX baseline requirements" in package.craftsman_prompt


def test_agent_team_complex_game_ui_requests_include_motion_specific_frontend_contracts() -> None:
    package = ToolBuilderAgentTeam().prepare(
        request_prompt=(
            "Build an online wavelength-style party game with a guess meter, animated dial reveals, and drag controls."
        ),
        refined_prompt="Refined prompt with platform constraints.",
        request_context_prompt="Build an online wavelength-style game with rich interactive animations.",
    )

    assert package.frontend_engineer is not None
    frontend_text = package.frontend_engineer.to_markdown().lower()
    assert "guess-meter motion" in frontend_text
    assert "drag phase" in frontend_text
    assert "requestanimationframe" in frontend_text or "spring systems" in frontend_text
    assert "frontend engineer handoff" in package.craftsman_prompt.lower()
