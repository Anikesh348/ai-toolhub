# AI Tool Builder Backend

FastAPI backend that accepts a natural-language tool prompt, runs Codex in an ephemeral builder container, tests generated code, builds/deploys the tool in Docker, and tracks lifecycle state in MongoDB.

## V2 Redesign Blueprint

- Multi-mode platform redesign (Chat + Tool Builder + Pi Operator): [`docs/platform-redesign-v2.md`](docs/platform-redesign-v2.md)

## Architecture

- `backend` container: FastAPI API + workflow orchestrator.
- `codex` container: long-running Codex runtime container with workspace-only mount.
- `ui` container: Next.js + Tailwind dashboard for request submission and live tracking.
- external MongoDB Atlas: persistent storage for jobs, logs, and deployed tools.
- ephemeral builder container: started per build attempt using `CODEX_IMAGE_NAME`.
- generated tool containers: one per successful build, isolated with CPU/memory limits.

## Key Features

- Persistent chat sessions with Codex-backed responses (`/chat/*` APIs + `/chat` UI page).
- Chat model selection with configurable available-model dropdown (`CODEX_CHAT_MODELS`, `CODEX_DEFAULT_CHAT_MODEL`).
- Chat image attachments (upload image in composer, pass to Codex via `codex exec --image`).
- Operator mode supports host operations tasks (project edits, Docker summaries, health checks/restarts).
- Prompt refinement with system constraints for generated tools.
- Prompt refinement defaults generated apps/tools to include a Next.js (React) UI unless prompt explicitly asks for backend/API/CLI only.
- Retry loop for generate -> test -> fix (up to `MAX_BUILD_ATTEMPTS`).
- Generated tool contract enforces `Dockerfile`, `docker-compose.yml`/`docker-compose.yaml`, `requirements.txt`, and pytest tests.
- Preflight validates docker-compose YAML syntax and requires a top-level non-empty `services` mapping.
- Strict workspace mount policy (`CODEX_WORKSPACE_HOST` -> `CODEX_WORKSPACE_CONTAINER` only).
- Docker resource limits for builder/tool containers.
- Dynamic port allocator (`PORT_RANGE_START` - `PORT_RANGE_END`) with persistent reservation history in MongoDB to avoid reusing previously assigned ports.
- Each tool keeps a stable assigned host port; stopping/starting the same tool reuses that exact port.
- Smoke test enforcement on `GET /status` expecting `{"status":"ok"}`.
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
  app/
    page.tsx
    layout.tsx
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
3. Set your MongoDB Atlas URL (`DB_URL`) and choose a new `MONGO_COLLECTION_PREFIX`.
4. Codex image is built locally by Compose from `docker/Codex.Dockerfile`.
5. Configure Google sign-in by setting `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` in `.env`.
6. Authenticate Codex (choose one, from Profile after Google sign-in):
   - ChatGPT Pro login (no API key): run login command below once.
   - API key auth: set `OPENAI_API_KEY` in `.env`.
7. Start the stack:
   ```bash
   docker-compose up --build
   ```
8. Open UI at `http://localhost:${UI_PORT}` (default `http://localhost:3000`).
9. For lower Pi build CPU, keep `UI_NEXT_DISABLE_SWC_WORKER=1` and tune `BUILDER_CPU_LIMIT` in `.env` (for tool image builds).

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
- `GET /integrations/git/ssh/public-key`
  - ensures an operator SSH key exists and returns the public key for manual Git provider setup
- `POST /integrations/git/ssh/verify`
  - body: `{"host":"github.com","username":"git"}`
  - verifies SSH auth against the Git host and returns terminal output/status
- `GET /health`
  - backend health check

## Security Notes

- Builder containers only mount `${CODEX_WORKSPACE_HOST}` to `${CODEX_WORKSPACE_CONTAINER}`.
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
