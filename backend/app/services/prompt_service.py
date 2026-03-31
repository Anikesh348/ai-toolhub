class PromptService:
    def refine_prompt(self, prompt: str) -> str:
        requirements = """
You are generating a production-ready Python tool for container deployment.
Mandatory requirements:
- Use Python 3.11.
- Expose an HTTP endpoint `GET /status` returning JSON: {"status":"ok"}.
- Listen on port 3000 inside the container.
- Include complete source code, tests, a Dockerfile, and docker-compose file (`docker-compose.yml` or `docker-compose.yaml`).
- In docker-compose, every externally reachable service must declare a `ports` mapping (one per service).
- Prefer env-var-based host mapping (for example `${FRONTEND_HOST_PORT:-3001}:3000`) and avoid hardcoded host ports.
- Do not hardcode host port 3000/3001 for runtime routing; use host-port env vars so the platform can inject unique ports.
- Include `requirements.txt`.
- Add automated tests runnable with `pytest -q`.
- Place tests in `tests/` with discoverable names like `test_status.py`.
- Include at least one passing test that validates `GET /status` returns `{"status":"ok"}`.
- Keep dependencies minimal and CPU/memory efficient.
- Ensure logs are meaningful and non-verbose.
- Avoid writing outside the current project directory.
""".strip()
        if self._should_require_web_ui(prompt):
            if self._should_use_node_frontend(prompt):
                requirements = (
                    f"{requirements}\n"
                    "UI requirements:\n"
                    "- Build a user-facing web UI.\n"
                    "- User explicitly asked for a JS framework; honor that request.\n"
                    "- Keep Docker build/runtime lightweight with multi-stage builds and minimal production dependencies.\n"
                    "- Keep backend service in root and preserve `GET /status` on port 3000 for platform smoke tests.\n"
                    "- In `docker-compose.yml`, include required service ports and valid YAML structure.\n"
                    "- Frontend should call backend APIs and render meaningful UI for the requested tool.\n"
                )
            else:
                requirements = (
                    f"{requirements}\n"
                    "UI requirements:\n"
                    "- Build a user-facing web UI.\n"
                    "- Use a lightweight frontend approach (static HTML/CSS/vanilla JS) without Node build tooling.\n"
                    "- Default to a single Python service for UI + API when practical to reduce Docker build time.\n"
                    "- Serve UI from the Python app (or equivalent lightweight setup) while keeping `GET /status` on port 3000.\n"
                    "- Include docker-compose with required service ports and valid YAML structure.\n"
                    "- UI should call backend APIs and render meaningful output for the requested tool.\n"
                )

        return f"{requirements}\n\nUser request:\n{prompt.strip()}"

    @staticmethod
    def _should_require_web_ui(prompt: str) -> bool:
        lowered = prompt.lower()
        ui_opt_out_terms = (
            "backend only",
            "api only",
            "no ui",
            "without ui",
            "cli only",
            "command line only",
            "terminal only",
            "headless",
        )
        return not any(term in lowered for term in ui_opt_out_terms)

    @staticmethod
    def _should_use_node_frontend(prompt: str) -> bool:
        if PromptService._should_force_lightweight_ui(prompt):
            return False

        lowered = prompt.lower()
        explicit_framework_terms = (
            "next.js",
            "nextjs",
            "react",
            "vite",
            "vue",
            "nuxt",
            "svelte",
            "angular",
            "frontend framework",
            "spa",
        )
        return any(term in lowered for term in explicit_framework_terms)

    @staticmethod
    def _should_force_lightweight_ui(prompt: str) -> bool:
        lowered = prompt.lower()
        lightweight_terms = (
            "no node",
            "without node",
            "no next",
            "no nextjs",
            "no react",
            "no vite",
            "vanilla js",
            "plain html",
            "static html",
            "single fastapi service",
            "lightweight ui",
        )
        return any(term in lowered for term in lightweight_terms)
