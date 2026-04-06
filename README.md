# AI Tool Builder Backend

FastAPI backend that accepts a natural-language tool prompt, runs Codex in an ephemeral builder container, tests generated code, builds/deploys the tool in Docker, and tracks lifecycle state in MongoDB.

## V2 Redesign Blueprint

- Multi-mode platform redesign (Chat + Tool Builder + Pi Operator): [`docs/platform-redesign-v2.md`](docs/platform-redesign-v2.md)

## Architecture

- `backend` container: FastAPI API + workflow orchestrator.
- `codex` container: long-running Codex runtime container with workspace-only mount.
- `ui` container: Vite + React + Tailwind dashboard for request submission and live tracking.
- `mongo` container: self-hosted MongoDB with Docker-managed lifecycle and persistent host-mounted data directories.
- ephemeral builder container: started per build attempt using `CODEX_IMAGE_NAME`.
- generated tool containers: one per successful build, isolated with CPU/memory limits.

## Key Features

- Persistent chat sessions with Codex-backed responses (`/chat/*` APIs + `/chat` UI page).
- Chat model selection with configurable available-model dropdown (`CODEX_CHAT_MODELS`, `CODEX_DEFAULT_CHAT_MODEL`).
- Chat image attachments (upload image in composer, pass to Codex via `codex exec --image`).
- Operator mode supports host operations tasks (project edits, Docker summaries, health checks/restarts).
- Prompt refinement with system constraints for generated tools.
- Prompt refinement defaults generated apps/tools to a lightweight UI stack (server-rendered/static HTML + JS) unless prompt explicitly requests a heavier frontend framework.
- Retry loop for generate -> test -> fix (up to `MAX_BUILD_ATTEMPTS`).
- Generated tool contract enforces `Dockerfile`, `docker-compose.yml`/`docker-compose.yaml`, `requirements.txt`, and pytest tests.
- Prompt contract enforces clarify-first requirements analysis and TDD-first implementation flow.
- Preflight validates docker-compose YAML syntax and requires a top-level non-empty `services` mapping.
- Strict workspace mount policy (`CODEX_WORKSPACE_HOST` -> `CODEX_WORKSPACE_CONTAINER` only).
- Docker resource limits for builder/tool containers.
- Dynamic port allocator (`PORT_RANGE_START` - `PORT_RANGE_END`) with persistent reservation history in MongoDB to avoid reusing previously assigned ports.
- Each tool keeps a stable assigned host port; stopping/starting the same tool reuses that exact port.
- Smoke test enforcement on `GET /status` expecting `{"status":"ok"}`.
- Runtime API verification before launch (OpenAPI/static route discovery + live endpoint probes).
- Optional scraping accuracy cross-check via Codex web search before tool launch.
- Brevo alerts for deploy success, build failure, and runtime crashes.
- Background crash monitor for running tool containers.
- Live progress updates over Server-Sent Events (`GET /jobs/{jobId}/events`).
- UI navigation with dedicated pages:
  - `Build Tool`: prompt submission + active job live progress only (terminal jobs are excluded).
  - `Requests`: full request history with status and deployment metadata.

## Project Structure

```
backend/
  app/
    main.py
    api/
      tool_routes.py
      schemas.py
    services/
      prompt_service.py
      codex_service.py
      docker_service.py
      tool_builder_service.py
      port_allocator_service.py
      alert_service.py
      testing_service.py
      monitor_service.py
    models/
      tool_request.py
      tool.py
      build_log.py
      status.py
    repositories/
      request_repository.py
      tool_repository.py
      build_log_repository.py
      port_allocation_repository.py
    workflows/
      tool_build_workflow.py
    utils/
      config.py
      logger.py
docker/
  Dockerfile
  Codex.Dockerfile
  docker-compose.yml
frontend/
  src/
    main.tsx
    App.tsx
    globals.css
  Dockerfile
  package.json
scripts/
  start.sh
.env.example
```

## Setup

1. Copy env template and update values:
   ```bash
   cp .env.example .env
   ```
2. Ensure the host workspace path exists and is writable:
   ```bash
   sudo mkdir -p /srv/codex
   sudo chown -R "$USER":"$USER" /srv/codex
   ```
3. Review the self-hosted MongoDB settings in `.env`:
   - `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD`
   - `MONGO_DB_NAME`
   - `MONGO_COLLECTION_PREFIX`
   - `MONGO_DATA_DIR` and `MONGO_CONFIG_DIR` for persisted storage paths
   - By default the Compose stack injects a local MongoDB connection string into the backend, so the app no longer depends on MongoDB Atlas. You can still override `MONGO_URI` manually if you need a custom local URI.
4. Configure runtime URL bases used when opening generated tools:
   - `NEXT_PUBLIC_TOOL_FRONTEND_BASE_URL` (UI links in dashboard, fallback `http://localhost`)
   - `NEXT_PUBLIC_TOOL_BACKEND_BASE_URL` (service/backend links in dashboard, fallback `http://localhost`)
   - `TOOL_FRONTEND_BASE_URL` (tool-builder chat status UI URL, fallback `http://localhost`)
   - `TOOL_BACKEND_BASE_URL` (tool-builder chat status backend/service URLs, fallback `http://localhost`)
5. Codex image is built locally by Compose from `docker/Codex.Dockerfile`.
6. Configure Google sign-in by setting `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` in `.env`.
7. Authenticate Codex (choose one, from Profile after Google sign-in):
   - ChatGPT Pro login (no API key): run login command below once.
   - API key auth: set `OPENAI_API_KEY` in `.env`.
8. Start the stack:
   ```bash
   docker-compose up --build
   ```
9. Open UI at `http://localhost:${UI_PORT}` (default `http://localhost:3000`).
10. For lower Pi build CPU, tune `BUILDER_CPU_LIMIT` in `.env` (for tool image builds).
11. Optional reliability tuning:
   - `API_VERIFICATION_MAX_CALLS`, `API_VERIFICATION_TIMEOUT_SECONDS` (controls endpoint probe breadth/cost).
   - `SCRAPING_WEB_VERIFY_ENABLED`, `SCRAPING_WEB_VERIFY_TIMEOUT_SECONDS` (controls scrape/web cross-check behavior).

## Database Persistence

- MongoDB data is stored on the host using the `MONGO_DATA_DIR` and `MONGO_CONFIG_DIR` mounts.
- That means data survives `docker compose stop`, `docker compose down`, container recreation, and host restarts as long as those paths are preserved.
- Default paths are repo-local (`./docker-data/mongodb` and `./docker-data/mongodb-config`) so local self-hosting works out of the box.
- If you want an Immich/Jellyfin-style external storage location, set absolute paths in `.env`, for example:
  ```bash
  MONGO_DATA_DIR=/srv/ai-toolhub/mongodb
  MONGO_CONFIG_DIR=/srv/ai-toolhub/mongodb-config
  ```
- If you prefer a Docker named volume instead of host paths, replace the `mongo` service volume source in Compose with a named volume and keep the target paths the same (`/data/db` and `/data/configdb`).

## Codex Authentication (Docker)

- ChatGPT Pro (no API key):
  ```bash
  docker-compose exec codex sh -lc 'codex login'
  ```
  Complete the browser/device login flow with your ChatGPT account.
- Login state is saved under `${CODEX_WORKSPACE_CONTAINER}/.config` inside the container, which maps to `${CODEX_WORKSPACE_HOST}` on host.
- Ephemeral builder containers use `HOME=${CODEX_WORKSPACE_CONTAINER}` and `XDG_CONFIG_HOME=${CODEX_WORKSPACE_CONTAINER}/.config`, so they reuse the same login state.
- API key mode is optional; if `OPENAI_API_KEY` is set, it is injected into builder containers.

## API Endpoints

- `POST /chat/sessions`
  - creates a persistent chat session (`general`, `tool_builder`, `operator`; `pi_operator` accepted as alias)
- `GET /chat/sessions`
  - lists chat sessions ordered by latest activity
- `GET /chat/models`
  - returns available Codex chat models and default selection for the UI dropdown
- `GET /chat/sessions/{sessionId}/messages`
  - returns session message history
- `POST /chat/sessions/{sessionId}/messages`
  - appends user message and returns assistant response
- `POST /chat/sessions/{sessionId}/messages/stream`
  - streams chat events over SSE (`user_message`, `assistant_delta`, `assistant_message`, `done`)
- `POST /chat/sessions/{sessionId}/attachments`
  - uploads an image attachment for chat and returns attachment metadata/id
- `GET /chat/sessions/{sessionId}/attachments/{attachmentId}`
  - serves an uploaded chat attachment
- `POST /tools/generate`
  - body: `{"prompt":"...", "name":"optional-name"}`
  - returns: `jobId`, initial status
- `GET /jobs/{jobId}`
  - returns current status + build logs
- `GET /jobs`
  - returns all requests with status + deployment metadata (tool status, port, container id)
- `GET /jobs/{jobId}/events`
  - server-sent events stream for live job status/log updates
- `GET /tools`
  - list deployed tools
- `GET /tools/{toolId}`
  - deployed tool details
- `DELETE /tools/{toolId}`
  - stops and removes the running tool container
- `POST /tools/{toolId}/start`
  - starts (or restarts) an existing generated tool container and runs smoke validation
- `POST /tools/{toolId}/rebuild`
  - triggers a rebuild + redeploy workflow for an existing generated tool from its current workspace
- `GET /integrations/git/ssh/public-key`
  - ensures an operator SSH key exists and returns the public key for manual Git provider setup
- `POST /integrations/git/ssh/verify`
  - body: `{"host":"github.com","username":"git"}`
  - verifies SSH auth against the Git host and returns terminal output/status
- `GET /health`
  - backend health check

## Security Notes

- Builder containers always mount `${CODEX_WORKSPACE_HOST}` to `${CODEX_WORKSPACE_CONTAINER}`.
- Builder containers also mount paths from `OPERATOR_ALLOWED_PATHS` (when configured) so tool-builder and operator workflows can access the same host roots.
- The dedicated `codex` service also mounts only `${CODEX_WORKSPACE_HOST}` to `${CODEX_WORKSPACE_CONTAINER}`.
- No mount is created for `/srv/data`, so Codex cannot read it.
- Generated tools run in separate containers with hard resource limits.
- Codex CLI is executed with `--dangerously-bypass-approvals-and-sandbox` inside the already isolated builder
  container to avoid nested `bwrap` namespace failures on Docker hosts that block unprivileged user namespaces.
- Smoke tests target `${SMOKE_TEST_HOST}` (default `host.docker.internal`) so backend-in-container can validate
  host-mapped tool ports correctly.
- Operator mode host access policy:
  - `OPERATOR_ALLOWED_PATHS`: comma-separated absolute host paths allowed for operator execution.
  - `OPERATOR_DENIED_PATHS`: comma-separated absolute host paths fully blocked (wins over allow list).
  - If `OPERATOR_ALLOWED_PATHS` is empty, operator mode defaults to broad host access with deny-path overlays.
  - Git SSH integration key is stored at `${CODEX_WORKSPACE_HOST}/.ssh/id_ed25519_operator`.
  - Optional GitHub HTTPS auth bridge for operator git operations:
    - `OPERATOR_GITHUB_USERNAME` (default `x-access-token`)
    - `OPERATOR_GITHUB_TOKEN` (recommended: fine-grained PAT with repo-only permissions)

## Raspberry Pi 5 Optimization Notes

- Synchronous MongoDB and Docker SDK usage keeps runtime overhead low.
- Build/test tasks run in background threads, reducing API latency.
- Builder containers are ephemeral and removed after each attempt.
- Tight default CPU/memory limits avoid saturation on 8GB systems.
- Docker image builds inherit builder CPU/memory limits to avoid host-wide spikes during tool builds.
- UI polling auto-throttles when idle/backgrounded to reduce dashboard-side CPU churn during long builds.
