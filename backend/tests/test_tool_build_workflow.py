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
