# Tool Builder Multi-Agent Architecture

This platform now runs Tool Builder as an explicit multi-agent pipeline:

1. Visionary (Product Owner)
2. Blueprint (Solution Architect)
3. Atlas (Dedicated Backend Engineer)
4. Prism (Dedicated Frontend Engineer, UI/Animation specialist when UI exists)
5. Craftsman (Senior Engineer Integrator)
6. Guardian (QA Engineer)
7. Shipmaster (DevOps Engineer)

## Pipeline Order

The workflow is executed in strict order and tracked as build status transitions:

- `VISIONARY_REFINING`
- `BLUEPRINT_DESIGNING`
- `CRAFTSMAN_IMPLEMENTING`
- `GUARDIAN_VALIDATING`
- `SHIPMASTER_DEPLOYING`

After validation and deployment checks pass, status moves to `RUNNING`.
On failure, status moves to `FAILED` and QA findings are fed back into Craftsman retries (bounded by `MAX_BUILD_ATTEMPTS`).

## Agent Artifacts

Each agent produces a structured artifact and hands it downstream:

- Visionary: problem statement, user stories, acceptance criteria, edge cases, constraints/assumptions.
- Blueprint: architecture diagram, component map, API design, Mongo schema, tech decisions.
- Atlas (Backend Engineer): API/domain/persistence/reliability contracts for implementation.
- Prism (Frontend Engineer): UI architecture, interaction design, and animation quality contracts.
- Guardian: test plan, test cases, release validation gates.
- Shipmaster: deployment plan, env contract, runtime verification checks.
- Craftsman: receives all artifacts and platform constraints, then integrates implementation.

Artifacts are stored as build log artifacts for traceability:

- `visionary-output.md`
- `blueprint-output.md`
- `backend-engineer-output.md`
- `frontend-engineer-output.md` (when UI is present)
- `guardian-output.md`
- `shipmaster-output.md`
- `craftsman-input.md`

## Data and Integration Defaults

The pipeline defaults to:

- Shared MongoDB with tool-scoped collections.
- Brevo as alert provider unless user explicitly overrides.
- Lightweight runtime constraints for containerized deployments.

## UI Behavior

The Build Tool UI now surfaces:

- current stage,
- active agent,
- human-readable stage message (for example: `🧠 Visionary is refining requirements...`).

This stage metadata is derived from build status and shown live during SSE updates.

## UX Baseline

For tools that include a web UI, agent handoffs now enforce baseline UX quality:

- clear/reset interactions for search/filter workflows,
- explicit loading, empty-result, and error states,
- keyboard-accessible controls with visible labels,
- responsive behavior for desktop and mobile.
- dedicated animation contracts for complex interactive UI surfaces.

For complex UI/game-like requests (for example gauge/dial interactions), Prism adds explicit contracts for:

- state-machine-driven interaction phases,
- smooth meter/dial movement and reveal transitions,
- pointer + keyboard support with reduced-motion-safe behavior.

Guardian test artifacts include UX checks so these behaviors are validated before deployment.

## Operator Mode Extension

Operator mode prompts now run with the same team protocol framing:

- Visionary -> Blueprint -> Backend Engineer -> Frontend Engineer (when UI exists) -> Craftsman -> Guardian -> Shipmaster

This improves decision quality for filesystem operations, code changes, diagnostics, and container management tasks.
