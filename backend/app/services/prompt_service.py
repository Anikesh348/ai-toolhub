import re


class PromptService:
    def refine_prompt(self, prompt: str) -> str:
        normalized_prompt = prompt.strip()
        if self._is_platform_build_brief(normalized_prompt):
            return normalized_prompt

        requires_persistence = self._should_require_persistence(normalized_prompt)
        requires_alerts = self._should_require_alerts(normalized_prompt)
        requires_schedule = self._should_require_schedule(normalized_prompt)

        requirements = """
You are generating a production-ready Python tool for container deployment.
Mandatory requirements:
- Work like a senior operator: inspect the request carefully, preserve the user's exact intent, implement the smallest complete tool that satisfies it, then verify it end-to-end.
- Clarify requirements first: restate user requirements as explicit acceptance criteria, identify ambiguities, and resolve only low-risk gaps with explicit assumptions before coding.
- Create a short requirements summary artifact in the project (for example `docs/requirements.md`) with assumptions and acceptance criteria.
- Create a concrete test cases artifact (for example `docs/test-cases.md`) before implementation so the intended behavior is explicit.
- Preserve every explicit user requirement from the request. Do not summarize away fields, constraints, integrations, workflow steps, or must-not rules.
- Create a requirement-by-requirement checklist in `docs/requirements.md` and ensure each item is covered by code and/or tests.
- Use Python 3.11.
- Implement the user's requested workflow directly; avoid unrelated placeholder features.
- Do not add generic dashboards, schedulers, email alerts, authentication, databases, or sample catalogs unless the request needs them.
- Derive clear acceptance criteria from the user request and satisfy each in the implementation.
- Choose a concise, domain-meaningful tool name; avoid generic names derived from filler prompt text.
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
- Include at least one additional passing test that validates core requested behavior beyond `/status`.
- Write tests before backend implementation (TDD): run tests, then implement backend changes until tests pass.
- Prefer backend integration tests for critical API behavior (not only unit tests/mocks).
- verify backend endpoints with real API calls before final launch; fail the build if endpoint verification fails.
- If backend relies on scraping/external extraction, cross-check sampled API results with live web search evidence and fix mismatches before launch.
- For live-data tools, do not rely on placeholder/static catalogs as the primary data source unless the user explicitly requested mock data.
- Only publish/deploy after tests, runtime verification, and any required web cross-checks all pass.
- Include a lightweight mock deployment check (for example docker compose up/down or equivalent scripted verification) so runtime wiring is validated.
- Keep bounded retries and deterministic control flow to avoid infinite loops.
- Keep dependencies minimal and CPU/memory efficient.
- Ensure logs are meaningful and non-verbose.
- Avoid writing outside the current project directory.
- When the tool includes a UI, make it feel modern, polished, and production-ready rather than barebones.
- Choose a visual direction that fits the tool domain and user request; use a dark theme only when it fits or is requested.
- Default all user-facing dates, times, schedules, and cron behavior to IST using the `Asia/Kolkata` timezone unless the user explicitly requests a different timezone.
- Configure the generated app/runtime to honor `TZ=Asia/Kolkata` by default and keep frontend/backend time handling aligned with that timezone.
""".strip()
        if requires_persistence:
            requirements = (
                f"{requirements}\n"
                "Persistence requirements:\n"
                "- reuse the platform MongoDB instead of adding a separate database service.\n"
                "- Read Mongo connection settings from environment variables `MONGO_URI` and `MONGO_DB_NAME`.\n"
                "- Create tool-specific collections for durable state.\n"
                "- Do not rely on in-memory storage or local-only files for data that must survive container reloads or crashes.\n"
                "- If MongoDB is temporarily unavailable during startup, do not crash the HTTP server; keep `/status` available while dependencies reconnect.\n"
            )
        else:
            requirements = f"{requirements}\n- Do not add MongoDB/database persistence unless the requested workflow actually needs durable state."
        if requires_alerts:
            requirements = (
                f"{requirements}\n"
                "Alert/notification requirements:\n"
                "- Use Brevo for email alerts unless the user explicitly requested another provider.\n"
                "- Read alert provider credentials from environment variables and degrade gracefully when credentials are absent.\n"
                "- Add idempotency/dedupe safeguards so repeated checks do not spam duplicate alerts unless the user asked for repeated notifications.\n"
            )
        else:
            requirements = f"{requirements}\n- Do not add Brevo/email alert plumbing unless alerts or notifications are part of the request."
        if requires_schedule:
            requirements = (
                f"{requirements}\n"
                "Scheduling requirements:\n"
                "- Implement scheduler/polling behavior only for the requested workflows.\n"
                "- Use bounded retries, overlap protection, and clear run history for scheduled jobs.\n"
            )
        else:
            requirements = f"{requirements}\n- Do not add scheduler/cron workers unless the request includes background or repeated execution."
        if self._should_require_web_ui(prompt):
            if self._should_use_node_frontend(prompt):
                requirements = (
                    f"{requirements}\n"
                    "UI requirements:\n"
                    "- Build a user-facing web UI.\n"
                    "- User explicitly asked for a JS framework; honor that request.\n"
                    "- Use a polished domain-appropriate visual design; do not force a dark theme unless requested or clearly fitting.\n"
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
                    "- Use a polished domain-appropriate visual design; do not force a dark theme unless requested or clearly fitting.\n"
                    "- Default to a single Python service for UI + API when practical to reduce Docker build time.\n"
                    "- Serve UI from the Python app (or equivalent lightweight setup) while keeping `GET /status` on port 3000.\n"
                    "- Include docker-compose with required service ports and valid YAML structure.\n"
                    "- UI should call backend APIs and render meaningful output for the requested tool.\n"
                )

        return f"{requirements}\n\nUser request:\n{normalized_prompt}"

    @staticmethod
    def _is_platform_build_brief(prompt: str) -> bool:
        prefixes = (
            "Build a production-ready tool based on this request:\n",
            "Apply the requested change to the existing tool codebase.\n",
            "Build a production-ready tool using this structured intake brief.\n",
        )
        if any(prompt.startswith(prefix) for prefix in prefixes):
            return True
        return "docs/requirements.md" in prompt and "docs/test-cases.md" in prompt and "Requirements:\n" in prompt

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

    @staticmethod
    def _should_require_persistence(prompt: str) -> bool:
        lowered = prompt.lower()
        if PromptService._has_opt_out(
            lowered,
            ("database", "db", "mongodb", "mongo", "persistence", "persistent", "storage"),
        ):
            return False
        persistence_terms = (
            "persist",
            "persistent",
            "database",
            "db",
            "mongodb",
            "mongo",
            "save",
            "saved",
            "store",
            "history",
            "watchlist",
            "tracker",
            "alerts",
            "login",
            "account",
            "state",
            "remember",
        )
        return any(term in lowered for term in persistence_terms)

    @staticmethod
    def _should_require_alerts(prompt: str) -> bool:
        lowered = prompt.lower()
        if PromptService._has_opt_out(
            lowered,
            ("alert", "alerts", "notification", "notifications", "email", "mail", "brevo"),
        ):
            return False
        alert_terms = (
            "alert",
            "alerts",
            "notify",
            "notification",
            "email",
            "mail",
            "brevo",
            "sendinblue",
            "sms",
            "webhook",
        )
        return any(term in lowered for term in alert_terms)

    @staticmethod
    def _should_require_schedule(prompt: str) -> bool:
        lowered = prompt.lower()
        if PromptService._has_opt_out(
            lowered,
            ("scheduler", "schedule", "scheduling", "cron", "polling", "background job"),
        ):
            return False
        schedule_terms = (
            "cron",
            "schedule",
            "scheduled",
            "poll",
            "polling",
            "interval",
            "every ",
            "daily",
            "hourly",
            "reminder",
            "repeat",
            "background job",
        )
        return any(term in lowered for term in schedule_terms)

    @staticmethod
    def _has_opt_out(lowered_prompt: str, terms: tuple[str, ...]) -> bool:
        for term in terms:
            escaped = re.escape(term)
            patterns = (
                rf"\bno\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bwithout\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bdo\s+not\s+(?:use|include|add)\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bnot\s+(?:use|using|include|including|add|adding)\b[^.;\n]{{0,120}}\b{escaped}\b",
                rf"\bno\s+{escaped}\b",
                rf"\bwithout\s+{escaped}\b",
                rf"\bnot\s+(?:use|using|include|including|add|adding)\s+(?:a\s+|an\s+|any\s+)?{escaped}\b",
                rf"\bdo\s+not\s+(?:use|include|add)\s+(?:a\s+|an\s+|any\s+)?{escaped}\b",
            )
            if any(re.search(pattern, lowered_prompt) for pattern in patterns):
                return True
        return False
