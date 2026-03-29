# AI ToolHub V2 Redesign

## Vision
Transform the current single-purpose tool builder into a multi-mode self-hosted AI platform:

1. **Conversation Mode (ChatGPT-like)**
   - Persistent chat sessions.
   - Uses Codex CLI as the inference engine.
   - Streaming assistant responses in a chat UI.

2. **Tool Builder Mode (existing capability)**
   - Keep current build-test-deploy workflow.
   - Reuse existing request/job tracking and deployment pages.

3. **Operator Mode (new capability)**
   - Agent can work across multiple host projects.
   - Strict access policy with allowed directories and blocked directories.
   - Can inspect and change project files, report active ports, CPU/RAM/disk, and process/service state.

---

## Product Architecture

### Frontend (Next.js)
Single app with mode-based navigation:

- `Chat`
  - Session list (persistent)
  - Conversation thread
  - Composer with mode selector (`general`, `tool_builder`, `pi_operator`)
- `Build Tool`
  - Existing page, mostly unchanged
- `Requests`
  - Existing page, mostly unchanged
- `System`
  - Live Pi metrics (CPU/RAM/disk/load)
  - Active listening ports with PID/process names
  - Services/process quick status
- `Projects`
  - Allowed project directory list
  - Blocked path list
  - Access policy validation status

### Backend (FastAPI)
Split into domains:

- `tool_builder` domain (existing)
- `chat` domain (new)
- `agent` domain (new, for Pi operator tasks)
- `system` domain (new, metrics/ports/health)
- `policy` domain (new, access control and path guard)

### Execution Layer
Use Codex CLI via execution adapters:

- `CodexRunner` for conversational and agent tasks
- `ToolBuildRunner` for current builder workflow
- `ShellProbeRunner` for controlled host/system inspection commands

All execution requests pass through **PolicyGuard** before running.

---

## Security and Access Model (Critical)

Your requirement: "access all Pi projects except blocked paths".

### Policy Rules
1. **Default deny**: nothing is accessible unless explicitly allowlisted.
2. **Allowlist roots**: explicit absolute project paths only.
3. **Blocklist paths**: absolute subpaths or siblings to deny.
4. **No broad root allowlisting** (`/`, `/home`, `/srv`) in production.
5. **Canonical path resolution**: always compare `realpath` values.

### Enforcement Layers
1. **Container mount policy**
   - Mount only allowlisted directories into Codex runtime.
   - For blocked subpaths inside mounted roots, overlay bind them to an empty read-only directory.
2. **Pre-execution guard**
   - Validate requested `cwd` and file targets against policy.
3. **Runtime command guard**
   - Reject commands that reference denied paths.
   - Enforce timeout, max output size, and command audit logging.
4. **Audit trail**
   - Persist who/what/when for every agent action (prompt, command, files touched, result).

---

## Data Model (MongoDB)

Keep existing collections and add:

1. `chat_sessions`
   - `_id`, `title`, `mode`, `createdAt`, `updatedAt`, `archived`

2. `chat_messages`
   - `_id`, `sessionId`, `role` (`system|user|assistant|tool`), `content`, `metadata`, `createdAt`

3. `agent_runs`
   - `_id`, `sessionId`, `taskType`, `status`, `input`, `resultSummary`, `startedAt`, `finishedAt`, `error`

4. `access_policies`
   - `_id`, `allowedRoots[]`, `blockedPaths[]`, `updatedAt`, `updatedBy`

5. `system_snapshots` (optional if you want history)
   - `_id`, `cpu`, `memory`, `disk`, `load`, `ports[]`, `createdAt`

Indexes:
- `chat_messages(sessionId, createdAt)`
- `chat_sessions(updatedAt)`
- `agent_runs(sessionId, startedAt)`

---

## API Design

### Chat
- `POST /chat/sessions`
- `GET /chat/sessions`
- `GET /chat/sessions/{sessionId}/messages`
- `POST /chat/sessions/{sessionId}/messages` (supports stream)
- `PATCH /chat/sessions/{sessionId}` (title/mode/archive)

### Agent (Operator)
- `POST /agent/tasks`
  - examples: `analyze_project`, `edit_project`, `run_diagnostics`
- `GET /agent/tasks/{taskId}`
- `GET /agent/tasks/{taskId}/events`

### System
- `GET /system/summary` (cpu, ram, disk, load, uptime)
- `GET /system/ports` (listening ports + process mapping)
- `GET /system/processes` (top resource consumers)

### Policy
- `GET /policy`
- `PUT /policy`
- `POST /policy/validate-path`

Tool builder endpoints remain unchanged for backward compatibility.

---

## Service Design

### 1) ChatService
Responsibilities:
- Manage session lifecycle.
- Fetch rolling context window.
- Build mode-specific system prompt.
- Invoke `CodexRunner` and stream response.
- Persist user/assistant messages.

### 2) AgentService
Responsibilities:
- Handle multi-step Pi tasks.
- Decide when to call Codex vs direct probes.
- Emit progress events.
- Persist run logs and artifacts.

### 3) PolicyGuardService
Responsibilities:
- Parse and validate policy config.
- Canonical path checks.
- Expose `is_allowed(path)` and `assert_allowed(path)`.
- Build Docker mount configuration for Codex runtime.

### 4) SystemService
Responsibilities:
- CPU/RAM/disk/load snapshots.
- Port-to-process mapping.
- Optional service status checks.

Recommended dependency: `psutil` for reliable cross-platform metrics and port/process mapping.

---

## Codex Integration Strategy

### Conversation Mode
- Keep Codex as the response engine.
- For each message:
  1. load recent session context (bounded tokens/messages),
  2. prepend mode system prompt,
  3. execute Codex,
  4. stream output,
  5. persist assistant response.

### Operator Mode
- Provide Codex with:
  - policy summary (allowed + blocked paths),
  - current system snapshot,
  - project metadata.
- Require explicit action plans before destructive edits.
- Enforce guardrails in backend regardless of prompt content.

---

## UI/UX Redesign

### Left Navigation
- Chat
- Build Tool
- Requests
- Projects
- System

### Chat UX
- Session list on the left, message thread on the right.
- Session persists automatically.
- Tags/indicator for mode (`General`, `Tool Builder`, `Operator`).
- Optional "actions" panel showing tool calls, file edits, and diagnostics during Pi mode.

### System UX
- Live cards for CPU/RAM/Disk/Load/Uptime.
- Port table with filters (process name, port, protocol).
- Refresh cadence 3-5 seconds with manual refresh fallback.

---

## Deployment Topology on host

### Recommended split
- `backend` container
- `ui` container
- `codex-runtime` container (long-running)
- optional `worker` container for queued background tasks

### Why queueing helps
Use Redis + worker (RQ/Celery) for long-running agent tasks and streaming updates without blocking API workers.

---

## Backward-Compatible Migration Plan

### Phase 1: Foundation
- Introduce domain modules (`chat`, `agent`, `policy`, `system`).
- Add new Mongo collections and indexes.
- Keep existing tool builder APIs untouched.

### Phase 2: Persistent Chat
- Implement chat sessions/messages APIs.
- Add frontend Chat page with session persistence.
- Wire Codex response flow in non-destructive mode.

### Phase 3: Policy Guard + Projects
- Add allowlist/blocklist configuration APIs.
- Build policy validation and mount resolver.
- Add Projects settings UI.

### Phase 4: Pi Observability
- Add `/system/*` endpoints.
- Add System dashboard UI.

### Phase 5: Operator Agent
- Add agent tasks/events APIs.
- Add safe file-edit flows with audit logs.
- Introduce approval gates for destructive actions.

### Phase 6: Hardening
- Add auth (at least local auth + role separation).
- Add command allow/deny templates.
- Add test coverage (policy enforcement, chat persistence, agent flows).

---

## Proposed Repository Restructure

```text
backend/app/
  api/
    tool_routes.py            # existing
    chat_routes.py            # new
    agent_routes.py           # new
    system_routes.py          # new
    policy_routes.py          # new
  domains/
    tool_builder/             # existing logic moved gradually
    chat/
    agent/
    system/
    policy/
  repositories/
    ...existing
    chat_session_repository.py
    chat_message_repository.py
    agent_run_repository.py
    policy_repository.py
  services/
    codex_runner_service.py   # shared runner abstraction
    system_service.py
    policy_guard_service.py
```

---

## Non-Negotiables for Your Use Case

1. Do not rely on prompt instructions alone for path blocking.
2. Enforce path policy in runtime and mount configuration.
3. Keep persistent conversation state in DB, not only frontend.
4. Keep tool builder backward compatible during migration.
5. Capture audit logs for all agent write operations.

---

## Suggested Immediate Next Implementation Slice

Build in this exact order to reduce risk:

1. Add `chat_sessions` + `chat_messages` collections and APIs.
2. Create Chat page in UI with session persistence.
3. Add `system/summary` and `system/ports` endpoints + dashboard cards.
4. Add policy CRUD (`allowedRoots`, `blockedPaths`) and validation endpoint.
5. Integrate Operator mode with Codex using policy guard.

This sequence gives you user-visible value early while preserving your current tool builder reliability.
