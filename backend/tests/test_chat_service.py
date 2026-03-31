from app.services.chat_service import ChatService


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
