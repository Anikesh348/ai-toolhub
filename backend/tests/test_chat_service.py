from datetime import datetime, timezone
from unittest.mock import Mock

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


class _StubSessionRepository:
    def __init__(self) -> None:
        now = datetime.now(tz=timezone.utc)
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
        self._session["updatedAt"] = datetime.now(tz=timezone.utc)
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
        now = datetime.now(tz=timezone.utc)
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
            "createdAt": datetime.now(tz=timezone.utc),
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
