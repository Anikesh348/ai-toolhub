from __future__ import annotations

import base64
import hashlib
import re
import secrets
import shlex
import subprocess
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from app.repositories.mcp_server_repository import McpServerRepository


class McpService:
    _CODEX_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
    _SUPPORTED_TRANSPORTS = {"stdio", "streamable_http", "docker_gateway"}
    _SHELL_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _ZOMATO_MCP_URL = "https://mcp-server.zomato.com/mcp"
    _ZOMATO_REGISTER_URL = "https://mcp-server.zomato.com/register"
    _ZOMATO_AUTHORIZE_URL = "https://mcp-server.zomato.com/authorize"
    _ZOMATO_TOKEN_URL = "https://mcp-server.zomato.com/token"
    _ZOMATO_SCOPE = "mcp:tools mcp:resources mcp:prompts"
    _ZOMATO_REDIRECT_URI = "https://oauth.pstmn.io/v1/callback"
    _ZOMATO_ACCESS_TOKEN_ENV = "ZOMATO_MCP_ACCESS_TOKEN"
    _ZOMATO_REFRESH_TOKEN_ENV = "ZOMATO_MCP_REFRESH_TOKEN"
    _ZOMATO_CLIENT_ID_ENV = "ZOMATO_MCP_CLIENT_ID"
    _ZOMATO_CLIENT_SECRET_ENV = "ZOMATO_MCP_CLIENT_SECRET"
    _ZOMATO_EXPIRES_AT_ENV = "ZOMATO_MCP_TOKEN_EXPIRES_AT"

    def __init__(self, repository: McpServerRepository) -> None:
        self._repository = repository
        self._pending_zomato_oauth: dict[str, dict[str, str | float]] = {}

    def list_servers(self) -> list[dict]:
        return [self._public_model(server) for server in self._repository.list_all()]

    def create_server(self, payload: dict[str, Any]) -> dict:
        normalized = self._normalize_payload(payload)
        return self._public_model(self._repository.create(normalized))

    def update_server(self, server_id: str, payload: dict[str, Any]) -> dict | None:
        existing = self._repository.get_by_id(server_id)
        if existing is None:
            return None
        merged = {**existing, **{key: value for key, value in payload.items() if value is not None}}
        if "env" in payload:
            merged["env"] = self._merge_env(existing.get("env") or {}, payload.get("env") or {})
        normalized = self._normalize_payload(merged)
        updated = self._repository.update(server_id, normalized)
        return self._public_model(updated) if updated else None

    def delete_server(self, server_id: str) -> bool:
        return self._repository.delete(server_id)

    def test_server(self, server_id: str) -> dict | None:
        server = self._repository.get_by_id(server_id)
        if server is None:
            return None

        status = "ready"
        message = "MCP server configuration is valid and will be registered when chat starts."
        if server["transport"] == "docker_gateway":
            status, message = self._test_docker_gateway(server)
        elif server["transport"] == "streamable_http":
            status, message = self._test_http_url(server.get("url") or "")
        elif not server.get("command"):
            status = "error"
            message = "Stdio MCP server needs a command."

        tested = self._repository.mark_tested(server_id, status=status, message=message)
        return self._public_model(tested) if tested else None

    def build_codex_setup_script(self) -> str:
        lines: list[str] = []
        enabled_servers = self._repository.list_enabled()
        direct_zomato_enabled = any(self._is_direct_zomato_server(server) for server in enabled_servers)
        for server in enabled_servers:
            try:
                if direct_zomato_enabled and (
                    self._is_zomato_docker_gateway(server) or self._is_default_docker_gateway(server)
                ):
                    continue
                server = self._refresh_zomato_token_if_needed(server)
                lines.extend(self._codex_setup_lines(server))
            except ValueError:
                continue
        return "\n".join(lines)

    def start_zomato_oauth(self) -> dict[str, str]:
        registration = self._register_zomato_oauth_client()
        client_id = str(registration.get("client_id") or "").strip()
        if not client_id:
            raise ValueError("Zomato did not return an OAuth client id.")

        code_verifier = self._new_pkce_verifier()
        code_challenge = self._pkce_challenge(code_verifier)
        state = secrets.token_urlsafe(24)
        client_secret = str(registration.get("client_secret") or "").strip()
        self._pending_zomato_oauth[state] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
            "created_at": time.time(),
        }

        params = {
            "response_type": "code",
            "client_id": client_id,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "redirect_uri": self._ZOMATO_REDIRECT_URI,
            "scope": self._ZOMATO_SCOPE,
        }
        return {
            "authorizationUrl": f"{self._ZOMATO_AUTHORIZE_URL}?{urlencode(params)}",
            "redirectUri": self._ZOMATO_REDIRECT_URI,
            "state": state,
            "message": (
                "Authorize Zomato, then paste the final Postman callback URL or the returned code here. "
                "This avoids Zomato's localhost redirect whitelist issue."
            ),
        }

    def complete_zomato_oauth(self, payload: dict[str, Any]) -> dict[str, Any]:
        code, state = self._extract_oauth_code_and_state(payload)
        if not code:
            raise ValueError("Paste the full callback URL from the browser, or paste the OAuth code.")
        if not state:
            raise ValueError("OAuth state is missing. Please restart the Zomato connection flow.")

        pending = self._pending_zomato_oauth.pop(state, None)
        if not pending:
            raise ValueError("This Zomato authorization flow expired. Please start it again.")

        token_payload = self._exchange_zomato_code_for_token(
            code=code,
            client_id=str(pending["client_id"]),
            client_secret=str(pending.get("client_secret") or ""),
            code_verifier=str(pending["code_verifier"]),
        )
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Zomato did not return an access token.")

        server = self._upsert_zomato_server(
            access_token=access_token,
            refresh_token=str(token_payload.get("refresh_token") or "").strip(),
            client_id=str(pending["client_id"]),
            client_secret=str(pending.get("client_secret") or ""),
            expires_in=token_payload.get("expires_in"),
        )
        return {
            "message": "Zomato MCP is connected. It will be available to new chat runs.",
            "server": server,
        }

    def _codex_setup_lines(self, server: dict[str, Any]) -> list[str]:
        codex_name = self._codex_name(server)
        lines = [f"codex mcp remove {shlex.quote(codex_name)} >/dev/null 2>&1 || true"]
        env = {str(key): str(value) for key, value in (server.get("env") or {}).items() if str(key).strip()}

        if server["transport"] == "streamable_http":
            url = str(server.get("url") or "").strip()
            if not url:
                raise ValueError("Streamable HTTP MCP server needs a URL.")
            bearer_env = self._select_bearer_token_env(env)
            lines.extend(self._export_env_lines(env))
            bearer_segment = f" --bearer-token-env-var {shlex.quote(bearer_env)}" if bearer_env else ""
            lines.append(f"codex mcp add {shlex.quote(codex_name)} --url {shlex.quote(url)}{bearer_segment} || true")
            return lines

        command_parts = self._command_parts(server)
        env_flags = " ".join(
            f"--env {shlex.quote(f'{key}={value}')}" for key, value in sorted(env.items())
        )
        env_segment = f" {env_flags}" if env_flags else ""
        command_segment = " ".join(shlex.quote(part) for part in command_parts)
        lines.append(f"codex mcp add {shlex.quote(codex_name)}{env_segment} -- {command_segment} || true")
        return lines

    def _command_parts(self, server: dict[str, Any]) -> list[str]:
        if server["transport"] == "docker_gateway":
            if self._is_zomato_docker_gateway(server):
                raise ValueError(
                    "Zomato OAuth rejects Docker Gateway's localhost callback. "
                    "Use the Connect Zomato flow to register the direct HTTP MCP server."
                )
            parts = ["docker", "mcp", "gateway", "run"]
            docker_profile = str(server.get("dockerProfile") or "").strip()
            docker_servers = [str(item).strip() for item in (server.get("dockerServers") or []) if str(item).strip()]
            if docker_profile:
                parts.extend(["--profile", docker_profile])
            elif docker_servers:
                for docker_server in docker_servers:
                    parts.extend(["--servers", docker_server])
            return parts

        command = str(server.get("command") or "").strip()
        if not command:
            raise ValueError("Stdio MCP server needs a command.")
        return [command, *[str(arg) for arg in (server.get("args") or [])]]

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        transport = str(payload.get("transport") or "stdio").strip()
        if transport not in self._SUPPORTED_TRANSPORTS:
            raise ValueError("Unsupported MCP transport.")

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("MCP server name is required.")

        env = payload.get("env") or {}
        if not isinstance(env, dict):
            raise ValueError("MCP env must be a key/value object.")

        normalized = {
            "name": name[:80],
            "description": (str(payload.get("description") or "").strip() or None),
            "transport": transport,
            "enabled": bool(payload.get("enabled", True)),
            "command": None,
            "args": [],
            "url": None,
            "dockerProfile": None,
            "dockerServers": [],
            "env": {
                str(key).strip(): str(value)
                for key, value in env.items()
                if str(key).strip() and value is not None
            },
        }

        if transport == "stdio":
            command = str(payload.get("command") or "").strip()
            if not command:
                raise ValueError("Stdio MCP server needs a command.")
            normalized["command"] = command[:240]
            normalized["args"] = [str(arg)[:400] for arg in (payload.get("args") or [])][:40]
        elif transport == "streamable_http":
            url = str(payload.get("url") or "").strip()
            if not self._valid_http_url(url):
                raise ValueError("Streamable HTTP MCP server needs an http(s) URL.")
            normalized["url"] = url[:1000]
        else:
            normalized["dockerProfile"] = (str(payload.get("dockerProfile") or "").strip() or None)
            normalized["dockerServers"] = [
                str(item).strip()[:120] for item in (payload.get("dockerServers") or []) if str(item).strip()
            ][:40]

        return normalized

    @staticmethod
    def _merge_env(existing_env: dict[str, Any], incoming_env: dict[str, Any]) -> dict[str, str]:
        merged = {str(key): str(value) for key, value in existing_env.items()}
        for raw_key, raw_value in incoming_env.items():
            key = str(raw_key).strip()
            if not key:
                continue
            value = str(raw_value)
            if value == "" and key in merged:
                continue
            merged[key] = value
        return merged

    def _test_docker_gateway(self, server: dict[str, Any]) -> tuple[str, str]:
        if self._is_zomato_docker_gateway(server):
            return (
                "error",
                "Zomato OAuth rejects Docker Gateway's localhost redirect. Use Connect Zomato to add it as a direct HTTP MCP server.",
            )
        command = ["docker", "mcp", "version"]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        except FileNotFoundError:
            return "error", "Docker CLI is not available in the backend runtime."
        except subprocess.TimeoutExpired:
            return "error", "Docker MCP version check timed out."

        output = "\n".join([result.stdout.strip(), result.stderr.strip()]).strip()
        if result.returncode != 0:
            return "error", output or "Docker MCP CLI is not available. Install Docker MCP Gateway or Docker Desktop MCP Toolkit."

        detail = output.splitlines()[0] if output else "Docker MCP CLI is available."
        configured = self._command_parts(server)
        return "ready", f"{detail}. Codex will register: {' '.join(configured)}"

    def _test_http_url(self, url: str) -> tuple[str, str]:
        if not self._valid_http_url(url):
            return "error", "Streamable HTTP URL is invalid."
        return "ready", "Streamable HTTP MCP URL is valid. Runtime availability is checked when chat starts."

    def _register_zomato_oauth_client(self) -> dict[str, Any]:
        payload = {
            "client_name": "AI ToolHub",
            "redirect_uris": [self._ZOMATO_REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": self._ZOMATO_SCOPE,
        }
        try:
            response = requests.post(self._ZOMATO_REGISTER_URL, json=payload, timeout=12)
        except requests.RequestException as exc:
            raise ValueError(f"Unable to register Zomato OAuth client: {exc}") from exc
        if response.status_code >= 400:
            raise ValueError(f"Unable to register Zomato OAuth client: {response.text.strip() or response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError("Zomato returned an invalid OAuth registration response.") from exc

    def _exchange_zomato_code_for_token(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._ZOMATO_REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        return self._post_zomato_token(data=data, client_secret=client_secret)

    def _refresh_zomato_token_if_needed(self, server: dict[str, Any]) -> dict[str, Any]:
        if str(server.get("url") or "").rstrip("/") != self._ZOMATO_MCP_URL.rstrip("/"):
            return server
        env = dict(server.get("env") or {})
        refresh_token = str(env.get(self._ZOMATO_REFRESH_TOKEN_ENV) or "").strip()
        if not refresh_token:
            return server
        try:
            expires_at = float(env.get(self._ZOMATO_EXPIRES_AT_ENV) or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at and expires_at > time.time() + 120:
            return server

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": str(env.get(self._ZOMATO_CLIENT_ID_ENV) or ""),
        }
        try:
            token_payload = self._post_zomato_token(
                data=data,
                client_secret=str(env.get(self._ZOMATO_CLIENT_SECRET_ENV) or ""),
            )
        except ValueError:
            return server

        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            return server
        updated = self._upsert_zomato_server(
            access_token=access_token,
            refresh_token=str(token_payload.get("refresh_token") or refresh_token),
            client_id=str(env.get(self._ZOMATO_CLIENT_ID_ENV) or ""),
            client_secret=str(env.get(self._ZOMATO_CLIENT_SECRET_ENV) or ""),
            expires_in=token_payload.get("expires_in"),
        )
        return self._repository.get_by_id(str(updated["id"])) or server

    def _post_zomato_token(self, *, data: dict[str, str], client_secret: str) -> dict[str, Any]:
        def post(form: dict[str, str]) -> requests.Response:
            return requests.post(
                self._ZOMATO_TOKEN_URL,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=12,
            )

        try:
            response = post(data)
            if response.status_code >= 400 and client_secret:
                response = post({**data, "client_secret": client_secret})
        except requests.RequestException as exc:
            raise ValueError(f"Unable to exchange Zomato OAuth token: {exc}") from exc
        if response.status_code >= 400:
            raise ValueError(f"Unable to exchange Zomato OAuth token: {response.text.strip() or response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError("Zomato returned an invalid token response.") from exc

    def _upsert_zomato_server(
        self,
        *,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        expires_in: Any,
    ) -> dict[str, Any]:
        env = {
            self._ZOMATO_ACCESS_TOKEN_ENV: access_token,
            self._ZOMATO_CLIENT_ID_ENV: client_id,
        }
        if refresh_token:
            env[self._ZOMATO_REFRESH_TOKEN_ENV] = refresh_token
        if client_secret:
            env[self._ZOMATO_CLIENT_SECRET_ENV] = client_secret
        try:
            expires_in_seconds = int(float(expires_in))
        except (TypeError, ValueError):
            expires_in_seconds = 3600
        env[self._ZOMATO_EXPIRES_AT_ENV] = str(int(time.time()) + max(60, expires_in_seconds))

        payload = self._normalize_payload(
            {
                "name": "Zomato MCP",
                "description": "Food discovery and ordering through Zomato's official MCP server.",
                "transport": "streamable_http",
                "enabled": True,
                "url": self._ZOMATO_MCP_URL,
                "env": env,
            }
        )
        existing = next(
            (
                server
                for server in self._repository.list_all()
                if str(server.get("url") or "").rstrip("/") == self._ZOMATO_MCP_URL.rstrip("/")
                or str(server.get("name") or "").strip().lower() == "zomato mcp"
            ),
            None,
        )
        if existing:
            updated = self._repository.update(str(existing["id"]), payload)
            server_id = str(existing["id"])
        else:
            updated = self._repository.create(payload)
            server_id = str(updated["id"])
        tested = self._repository.mark_tested(
            server_id,
            status="ready",
            message="Zomato OAuth token is stored. Codex will use it as a bearer token for the HTTP MCP server.",
        )
        return self._public_model(tested or updated)

    def _extract_oauth_code_and_state(self, payload: dict[str, Any]) -> tuple[str | None, str | None]:
        callback_url = str(payload.get("callbackUrl") or "").strip()
        code = str(payload.get("code") or "").strip() or None
        state = str(payload.get("state") or "").strip() or None
        if callback_url:
            parsed = urlsplit(callback_url)
            query = parse_qs(parsed.query)
            fragment = parse_qs(parsed.fragment)
            values = {**fragment, **query}
            code = (values.get("code") or [code])[0]
            state = (values.get("state") or [state])[0]
            if not code and "://" not in callback_url and "?" not in callback_url:
                code = callback_url
        return code, state

    def _select_bearer_token_env(self, env: dict[str, str]) -> str | None:
        candidates = [
            self._ZOMATO_ACCESS_TOKEN_ENV,
            "MCP_BEARER_TOKEN",
            "MCP_ACCESS_TOKEN",
            "ACCESS_TOKEN",
        ]
        for key in candidates:
            if env.get(key) and self._SHELL_ENV_NAME_RE.fullmatch(key):
                return key
        return None

    def _export_env_lines(self, env: dict[str, str]) -> list[str]:
        lines: list[str] = []
        for key, value in sorted(env.items()):
            if not self._SHELL_ENV_NAME_RE.fullmatch(key):
                continue
            lines.append(f"export {key}={shlex.quote(str(value))}")
        return lines

    def _is_direct_zomato_server(self, server: dict[str, Any]) -> bool:
        env = server.get("env") or {}
        return (
            server.get("transport") == "streamable_http"
            and str(server.get("url") or "").rstrip("/") == self._ZOMATO_MCP_URL.rstrip("/")
            and bool(str(env.get(self._ZOMATO_ACCESS_TOKEN_ENV) or "").strip())
        )

    def _is_zomato_docker_gateway(self, server: dict[str, Any]) -> bool:
        if server.get("transport") != "docker_gateway":
            return False
        parts = [
            str(server.get("name") or ""),
            str(server.get("description") or ""),
            str(server.get("dockerProfile") or ""),
            " ".join(str(item) for item in (server.get("dockerServers") or [])),
        ]
        return "zomato" in " ".join(parts).lower()

    @staticmethod
    def _is_default_docker_gateway(server: dict[str, Any]) -> bool:
        if server.get("transport") != "docker_gateway":
            return False
        docker_servers = [str(item).strip() for item in (server.get("dockerServers") or []) if str(item).strip()]
        docker_profile = str(server.get("dockerProfile") or "").strip().lower()
        return not docker_servers and docker_profile in {"", "default"}

    @staticmethod
    def _new_pkce_verifier() -> str:
        return secrets.token_urlsafe(64)[:128]

    @staticmethod
    def _pkce_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _valid_http_url(value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def _public_model(self, server: dict[str, Any]) -> dict:
        return {
            **{key: value for key, value in server.items() if key != "env"},
            "codexName": self._codex_name(server),
            "envKeys": sorted((server.get("env") or {}).keys()),
        }

    def _codex_name(self, server: dict[str, Any]) -> str:
        base = self._CODEX_NAME_RE.sub("-", str(server.get("name") or "mcp").strip()).strip("-").lower()
        suffix = str(server.get("id") or "")[:8]
        return f"toolhub-{base or 'mcp'}-{suffix}"
