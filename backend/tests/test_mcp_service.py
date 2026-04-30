from datetime import datetime

from app.services.mcp_service import McpService
from app.services.chat_service import ChatService


class _MemoryMcpRepository:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def create(self, payload: dict) -> dict:
        document = {
            "id": "server-12345678",
            **payload,
            "createdAt": datetime(2026, 1, 1),
            "updatedAt": datetime(2026, 1, 1),
            "lastTestedAt": None,
            "lastStatus": "untested",
            "lastMessage": "Connection has not been tested yet.",
        }
        self.documents[document["id"]] = document
        return dict(document)

    def list_all(self) -> list[dict]:
        return list(self.documents.values())

    def list_enabled(self) -> list[dict]:
        return [document for document in self.documents.values() if document.get("enabled")]

    def get_by_id(self, server_id: str) -> dict | None:
        document = self.documents.get(server_id)
        return dict(document) if document else None

    def update(self, server_id: str, payload: dict) -> dict | None:
        existing = self.documents.get(server_id)
        if not existing:
            return None
        existing.update(payload)
        existing["updatedAt"] = datetime(2026, 1, 2)
        return dict(existing)


def test_create_docker_gateway_masks_env_and_generates_codex_setup() -> None:
    repository = _MemoryMcpRepository()
    service = McpService(repository=repository)  # type: ignore[arg-type]

    server = service.create_server(
        {
            "name": "Travel Tools",
            "transport": "docker_gateway",
            "enabled": True,
            "dockerProfile": "travel",
            "env": {"BOOKING_TOKEN": "secret-token"},
        }
    )

    assert server["envKeys"] == ["BOOKING_TOKEN"]
    assert "env" not in server
    setup_script = service.build_codex_setup_script()
    assert "codex mcp add toolhub-travel-tools-server-1" in setup_script
    assert "docker mcp gateway run --profile travel" in setup_script
    assert "--env BOOKING_TOKEN=secret-token" in setup_script


def test_streamable_http_requires_http_url() -> None:
    service = McpService(repository=_MemoryMcpRepository())  # type: ignore[arg-type]

    try:
        service.create_server({"name": "Bad HTTP", "transport": "streamable_http", "url": "ftp://example.com/mcp"})
    except ValueError as exc:
        assert "http(s) URL" in str(exc)
    else:
        raise AssertionError("Expected invalid MCP URL to fail")


def test_update_preserves_secret_when_env_value_is_blank() -> None:
    repository = _MemoryMcpRepository()
    service = McpService(repository=repository)  # type: ignore[arg-type]

    created = service.create_server(
        {
            "name": "Food Tools",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "food-mcp"],
            "env": {"FOOD_API_KEY": "original-secret"},
        }
    )
    service.update_server(
        created["id"],
        {
            "name": "Food Tools",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "food-mcp"],
            "env": {"FOOD_API_KEY": ""},
        },
    )

    setup_script = service.build_codex_setup_script()
    assert "--env FOOD_API_KEY=original-secret" in setup_script


def test_direct_zomato_suppresses_default_docker_gateway_setup() -> None:
    repository = _MemoryMcpRepository()
    repository.documents = {
        "zomato-http": {
            "id": "zomato-http",
            "name": "Zomato MCP",
            "transport": "streamable_http",
            "enabled": True,
            "url": "https://mcp-server.zomato.com/mcp",
            "env": {"ZOMATO_MCP_ACCESS_TOKEN": "zomato-token"},
            "createdAt": datetime(2026, 1, 1),
            "updatedAt": datetime(2026, 1, 1),
        },
        "docker-default": {
            "id": "docker-default",
            "name": "Docker MCP Gateway",
            "transport": "docker_gateway",
            "enabled": True,
            "dockerProfile": "default",
            "dockerServers": [],
            "env": {},
            "createdAt": datetime(2026, 1, 1),
            "updatedAt": datetime(2026, 1, 1),
        },
    }
    service = McpService(repository=repository)  # type: ignore[arg-type]

    setup_script = service.build_codex_setup_script()

    assert "https://mcp-server.zomato.com/mcp" in setup_script
    assert "--bearer-token-env-var ZOMATO_MCP_ACCESS_TOKEN" in setup_script
    assert "docker mcp gateway run" not in setup_script


def test_chat_service_extracts_mcp_oauth_authorization_event() -> None:
    service = ChatService.__new__(ChatService)
    emitted_urls: set[str] = set()

    events = service._extract_mcp_oauth_events(  # noqa: SLF001
        "Authorize `toolhub-zomato` by opening this URL in your browser:\n"
        "https://mcp-server.zomato.com/authorize?response_type=code&client_id=abc",
        emitted_urls,
    )

    assert events == [
        {
            "type": "mcp_oauth",
            "url": "https://mcp-server.zomato.com/authorize?response_type=code&client_id=abc",
            "serverName": "toolhub-zomato",
            "message": "Authorize this MCP server, then return to this chat while the request continues.",
        }
    ]
    assert service._extract_mcp_oauth_events("https://mcp-server.zomato.com/authorize?response_type=code&client_id=abc", emitted_urls) == []  # noqa: SLF001
