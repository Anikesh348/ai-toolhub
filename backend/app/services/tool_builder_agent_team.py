from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class VisionaryArtifact:
    problem_statement: str
    user_stories: list[str]
    acceptance_criteria: list[str]
    edge_cases: list[str]
    constraints_and_assumptions: list[str]

    def to_markdown(self) -> str:
        sections = [
            "# Visionary Output",
            "",
            "## Problem Statement",
            self.problem_statement,
            "",
            "## User Stories",
            *(f"- {item}" for item in self.user_stories),
            "",
            "## Acceptance Criteria",
            *(f"- {item}" for item in self.acceptance_criteria),
            "",
            "## Edge Cases",
            *(f"- {item}" for item in self.edge_cases),
            "",
            "## Constraints & Assumptions",
            *(f"- {item}" for item in self.constraints_and_assumptions),
        ]
        return "\n".join(sections).strip()


@dataclass(frozen=True)
class BlueprintArtifact:
    architecture_diagram: str
    component_breakdown: list[str]
    api_design: list[str]
    db_schema: list[str]
    tech_decisions: list[str]

    def to_markdown(self) -> str:
        sections = [
            "# Blueprint Output",
            "",
            "## High-Level Architecture Diagram (Textual)",
            self.architecture_diagram,
            "",
            "## Component Breakdown",
            *(f"- {item}" for item in self.component_breakdown),
            "",
            "## API Design",
            *(f"- {item}" for item in self.api_design),
            "",
            "## DB Schema",
            *(f"- {item}" for item in self.db_schema),
            "",
            "## Tech Decisions + Justifications",
            *(f"- {item}" for item in self.tech_decisions),
        ]
        return "\n".join(sections).strip()


@dataclass(frozen=True)
class GuardianArtifact:
    test_plan: list[str]
    test_cases: list[str]
    validation_results: list[str]

    def to_markdown(self) -> str:
        sections = [
            "# Guardian Output",
            "",
            "## Test Plan",
            *(f"- {item}" for item in self.test_plan),
            "",
            "## Test Cases",
            *(f"- {item}" for item in self.test_cases),
            "",
            "## Validation Results",
            *(f"- {item}" for item in self.validation_results),
        ]
        return "\n".join(sections).strip()


@dataclass(frozen=True)
class BackendEngineerArtifact:
    implementation_focus: list[str]
    interface_contracts: list[str]
    reliability_contracts: list[str]

    def to_markdown(self) -> str:
        sections = [
            "# Backend Engineer Output",
            "",
            "## Implementation Focus",
            *(f"- {item}" for item in self.implementation_focus),
            "",
            "## Interface Contracts",
            *(f"- {item}" for item in self.interface_contracts),
            "",
            "## Reliability Contracts",
            *(f"- {item}" for item in self.reliability_contracts),
        ]
        return "\n".join(sections).strip()


@dataclass(frozen=True)
class FrontendEngineerArtifact:
    ui_system_design: list[str]
    interaction_contracts: list[str]
    animation_contracts: list[str]
    quality_gates: list[str]

    def to_markdown(self) -> str:
        sections = [
            "# Frontend Engineer Output",
            "",
            "## UI System Design",
            *(f"- {item}" for item in self.ui_system_design),
            "",
            "## Interaction Contracts",
            *(f"- {item}" for item in self.interaction_contracts),
            "",
            "## Animation Contracts",
            *(f"- {item}" for item in self.animation_contracts),
            "",
            "## Quality Gates",
            *(f"- {item}" for item in self.quality_gates),
        ]
        return "\n".join(sections).strip()


@dataclass(frozen=True)
class ShipmasterArtifact:
    deployment_plan: list[str]
    env_variables: list[str]
    runtime_checks: list[str]

    def to_markdown(self) -> str:
        sections = [
            "# Shipmaster Output",
            "",
            "## Deployment Plan",
            *(f"- {item}" for item in self.deployment_plan),
            "",
            "## Environment Variables",
            *(f"- {item}" for item in self.env_variables),
            "",
            "## Runtime Checks",
            *(f"- {item}" for item in self.runtime_checks),
        ]
        return "\n".join(sections).strip()


@dataclass(frozen=True)
class AgentTeamPackage:
    visionary: VisionaryArtifact
    blueprint: BlueprintArtifact
    backend_engineer: BackendEngineerArtifact
    frontend_engineer: FrontendEngineerArtifact | None
    guardian: GuardianArtifact
    shipmaster: ShipmasterArtifact
    craftsman_prompt: str


class VisionaryAgent:
    nickname = "Visionary"

    def run(self, request_prompt: str) -> VisionaryArtifact:
        requires_ui = _requires_ui(request_prompt)
        requirements = self._extract_requirements(request_prompt)
        problem_statement = self._build_problem_statement(request_prompt, requirements)
        user_stories = self._build_user_stories(requirements)
        acceptance_criteria = self._build_acceptance_criteria(requirements, requires_ui=requires_ui)
        edge_cases = self._build_edge_cases(request_prompt, requires_ui=requires_ui)
        constraints = self._build_constraints_and_assumptions(request_prompt)
        return VisionaryArtifact(
            problem_statement=problem_statement,
            user_stories=user_stories,
            acceptance_criteria=acceptance_criteria,
            edge_cases=edge_cases,
            constraints_and_assumptions=constraints,
        )

    def _extract_requirements(self, prompt: str) -> list[str]:
        cleaned = _normalize_text(prompt)
        lines = [line.strip() for line in cleaned.split("\n") if line.strip()]

        extracted: list[str] = []
        for line in lines:
            candidate = _BULLET_RE.sub("", line).strip()
            lowered = candidate.lower()
            if not candidate:
                continue
            if lowered.endswith(":"):
                continue
            if lowered.startswith(("requirements", "user request", "change request", "clarifications")):
                continue
            if lowered.startswith("build a production-ready tool"):
                continue
            if lowered.startswith("apply the requested change"):
                continue
            if len(candidate) < 8:
                continue
            if line != candidate and candidate not in extracted:
                extracted.append(candidate.rstrip("."))

        if extracted:
            return extracted[:12]

        sentences = []
        for sentence in _SENTENCE_RE.split(cleaned):
            candidate = _normalize_inline(sentence)
            if len(candidate) < 12:
                continue
            lowered = candidate.lower()
            if lowered.startswith(("requirements", "user request", "build a production-ready tool")):
                continue
            sentences.append(candidate.rstrip("."))
        return sentences[:10]

    @staticmethod
    def _build_problem_statement(prompt: str, requirements: list[str]) -> str:
        base = requirements[0] if requirements else _normalize_inline(prompt)
        if not base:
            base = "Deliver a production-grade tool that satisfies the requested workflow end-to-end."
        return (
            "Design and deliver a production-grade tool that fulfills the user's requested workflow with clear "
            f"requirements, reliable runtime behavior, and maintainable architecture. Core intent: {base.rstrip('.')}."
        )

    @staticmethod
    def _build_user_stories(requirements: list[str]) -> list[str]:
        stories: list[str] = []
        if not requirements:
            return ["As a user, I want a reliable tool workflow from input to final validated output."]

        for item in requirements[:4]:
            normalized = item[0].lower() + item[1:] if item else item
            if normalized.lower().startswith("as "):
                stories.append(normalized if normalized.endswith(".") else f"{normalized}.")
                continue
            stories.append(f"As a user, I want the system to {normalized.rstrip('.')}, so the workflow is usable and predictable.")

        return _dedupe(stories)[:5]

    @staticmethod
    def _build_acceptance_criteria(requirements: list[str], requires_ui: bool) -> list[str]:
        criteria = [
            f"System shall {item[0].lower() + item[1:].rstrip('.')}."
            for item in requirements[:8]
            if item
        ]
        criteria.extend(
            [
                'System shall expose `GET /status` and return JSON `{\"status\":\"ok\"}`.',
                "System shall include passing automated tests for health and core business behavior.",
                "System shall log meaningful operational events and handle failures gracefully.",
            ]
        )
        if requires_ui:
            criteria.extend(
                [
                    "UI shall provide a clear or reset action so users can remove results and start a fresh query.",
                    "UI shall visibly handle loading, empty-result, and error states for all network-driven actions.",
                    "UI shall keep controls keyboard accessible with explicit labels and predictable focus behavior.",
                    "UI shall be responsive and usable on mobile and desktop layouts.",
                ]
            )
        return _dedupe(criteria)

    @staticmethod
    def _build_edge_cases(prompt: str, requires_ui: bool) -> list[str]:
        lowered = prompt.lower()
        edge_cases = [
            "Invalid or incomplete user input payloads.",
            "External dependency downtime (network, API provider, or MongoDB transient failures).",
            "No matching results for filters or query criteria.",
            "Duplicate notifications/alerts across scheduler runs.",
        ]

        if any(token in lowered for token in ("movie", "show", "bookmyshow", "district")):
            edge_cases.append("Source catalog changes between polling cycles (new/removed listings).")
        if "cron" in lowered or "schedule" in lowered or "poll" in lowered:
            edge_cases.append("Scheduler overlaps and delayed execution under high load.")
        if requires_ui:
            edge_cases.append("Users clearing inputs expect stale results and errors to disappear immediately.")
            edge_cases.append("Slow upstream calls should not freeze the UI; loading state must remain visible.")
        return _dedupe(edge_cases)

    @staticmethod
    def _build_constraints_and_assumptions(prompt: str) -> list[str]:
        lowered = prompt.lower()
        constraints = [
            "Use shared MongoDB with tool-specific collections instead of provisioning a dedicated database by default.",
            "Default alert integration is Brevo unless the user explicitly requests another provider.",
            "Keep implementation lightweight for constrained environments and containerized deployment.",
            "Use deterministic validation gates before deployment (tests, smoke checks, API verification).",
        ]
        if "timezone" not in lowered:
            constraints.append("Assume `Asia/Kolkata` timezone defaults unless the user requests a different timezone.")
        return _dedupe(constraints)


class BlueprintAgent:
    nickname = "Blueprint"

    def run(self, request_prompt: str, visionary: VisionaryArtifact) -> BlueprintArtifact:
        _ = visionary
        requires_ui = _requires_ui(request_prompt)
        architecture = self._build_architecture_diagram(requires_ui=requires_ui)
        components = self._build_component_breakdown(requires_ui=requires_ui)
        api_design = self._build_api_design(requires_ui=requires_ui)
        db_schema = self._build_db_schema()
        tech_decisions = self._build_tech_decisions(request_prompt=request_prompt, requires_ui=requires_ui)
        return BlueprintArtifact(
            architecture_diagram=architecture,
            component_breakdown=components,
            api_design=api_design,
            db_schema=db_schema,
            tech_decisions=tech_decisions,
        )

    @staticmethod
    def _build_architecture_diagram(requires_ui: bool) -> str:
        ui_node = "[React/Lightweight UI] -> " if requires_ui else ""
        return (
            "User -> "
            f"{ui_node}[API Service] -> [Domain Services] -> [MongoDB (shared collections)]\n"
            "                                 |\n"
            "                                 +-> [Scheduler/Cron Jobs]\n"
            "                                 +-> [Brevo Alert Adapter]\n"
            "                                 +-> [Structured Logs + Health Endpoints]"
        )

    @staticmethod
    def _build_component_breakdown(requires_ui: bool) -> list[str]:
        components = [
            "API service layer: request validation, contracts, and status endpoints.",
            "Domain orchestrator: core business logic, rule evaluation, and workflow control.",
            "Persistence adapter: MongoDB repositories using tool-scoped collections (jobs, results, alerts, history).",
            "Scheduler worker: periodic polling and retry-safe execution.",
            "Notification adapter: Brevo email delivery with idempotent send safeguards.",
            "Observability: structured logs, build artifacts, and runtime diagnostics.",
        ]
        if requires_ui:
            components.insert(0, "Frontend app: user configuration, status dashboards, and action triggers.")
            components.insert(1, "UX state layer: loading, empty, error, and clear/reset interaction handling.")
        return components

    @staticmethod
    def _build_api_design(requires_ui: bool) -> list[str]:
        endpoints = [
            "GET /status -> health contract for smoke and runtime probes.",
            "GET /api/config -> fetch current tool configuration.",
            "PUT /api/config -> upsert validated configuration and alert settings.",
            "POST /api/jobs/run -> trigger immediate execution for deterministic validation.",
            "GET /api/results -> return latest computed results with optional filters.",
            "GET /api/history -> execution history (run id, timestamp, status, summary).",
            "POST /api/alerts/test -> validate Brevo integration with a safe test notification.",
        ]
        if requires_ui:
            endpoints.append("GET / -> serve UI application shell and static assets.")
        return endpoints

    @staticmethod
    def _build_db_schema() -> list[str]:
        return [
            "Collection `tool_jobs`: schedule metadata, job state, lock/version fields for safe retries.",
            "Collection `tool_results`: normalized result payloads and calculated summaries per run.",
            "Collection `tool_alerts`: user alert rules, channels, dedupe keys, and delivery state.",
            "Collection `tool_history`: immutable run history with diagnostics and execution duration.",
            "Collection `tool_config`: singleton tool settings (filters, timezone, provider configuration).",
        ]

    @staticmethod
    def _build_tech_decisions(request_prompt: str, requires_ui: bool) -> list[str]:
        lowered = request_prompt.lower()
        backend_choice = (
            "Backend: Python 3.11 service for compatibility with existing build/test/deploy contracts and smoke probes."
        )
        vertx_note = (
            "Vert.x note: Java Vert.x remains a preferred target for future templates, but current platform checks "
            "(pytest + Python runtime contracts) require Python-first generation for runnable output."
        )
        frontend_choice = (
            "Frontend: React only when UI complexity justifies it; otherwise lightweight static/embedded UI for low memory footprint."
            if requires_ui
            else "Frontend: no mandatory UI layer (API-first) when the request is backend-only."
        )
        db_choice = (
            "Database strategy: shared MongoDB with isolated collections per tool to reduce operational overhead "
            "while keeping logical isolation."
        )
        scheduler_choice = "Scheduling: cron-driven worker with idempotent runs and bounded retries."
        integration_choice = "Alerts: Brevo by default, overridable only via explicit user instruction."

        decisions = [backend_choice, vertx_note, frontend_choice, db_choice, scheduler_choice, integration_choice]
        if requires_ui:
            decisions.append(
                "UX baseline: include clear/reset interactions, loading/empty/error feedback states, and keyboard-accessible controls."
            )
        if any(token in lowered for token in ("raspberry", "pi", "lightweight")):
            decisions.append("Runtime footprint optimization: minimal dependencies, small container layers, and constrained memory defaults.")
        return decisions


class BackendEngineerAgent:
    nickname = "Atlas"

    def run(
        self,
        request_prompt: str,
        visionary: VisionaryArtifact,
        blueprint: BlueprintArtifact,
    ) -> BackendEngineerArtifact:
        _ = visionary
        _ = blueprint
        complex_backend = _is_complex_backend_request(request_prompt)
        implementation_focus = [
            "Own API handlers, domain services, repositories, and scheduler workers with clear module boundaries.",
            "Enforce strict request/response validation and deterministic error contracts (4xx for client errors, 5xx for server faults).",
            "Persist jobs, results, alerts, and history in shared MongoDB collections with predictable indexes.",
            "Implement Brevo adapter and alert dedupe keys so repeated scheduler runs avoid duplicate sends.",
        ]
        if complex_backend:
            implementation_focus.append(
                "Use explicit orchestration boundaries for concurrent workflows (locks/versioning/idempotency) to prevent race conditions."
            )

        interface_contracts = [
            "Keep `GET /status` stable as runtime health contract with exact JSON `{\\\"status\\\":\\\"ok\\\"}`.",
            "Define API payload schemas in one place and keep handlers, tests, and docs aligned.",
            "Return machine-readable error bodies with stable `code`/`message` structure for frontend consumption.",
        ]
        if complex_backend:
            interface_contracts.append(
                "For long-running operations, expose progress/state endpoints rather than blocking synchronous calls."
            )

        reliability_contracts = [
            "Guard external dependencies with timeouts and bounded retries.",
            "Do not crash startup on transient Mongo unavailability; keep status endpoint alive during recovery.",
            "Emit structured logs for each critical workflow phase (input validation, fetch, persist, notify).",
            "Provide deterministic test fixtures for core domain logic and failure paths.",
        ]
        if complex_backend:
            reliability_contracts.append(
                "Add regression tests for concurrency, idempotency, and partial-failure recovery paths."
            )

        return BackendEngineerArtifact(
            implementation_focus=implementation_focus,
            interface_contracts=interface_contracts,
            reliability_contracts=reliability_contracts,
        )


class FrontendEngineerAgent:
    nickname = "Prism"

    def run(
        self,
        request_prompt: str,
        visionary: VisionaryArtifact,
        blueprint: BlueprintArtifact,
    ) -> FrontendEngineerArtifact:
        _ = visionary
        _ = blueprint
        complex_frontend = _is_complex_frontend_request(request_prompt)
        game_like_ui = _is_game_like_ui_request(request_prompt)

        ui_system_design = [
            "Design UI as composable feature modules (shell, controls, visualization surface, feedback states).",
            "Separate domain state from animation state so transitions remain smooth and deterministic.",
            "Use responsive layout primitives for mobile and desktop without duplicating component logic.",
            "Keep typography, spacing, and contrast intentional so interface reads as production quality, not scaffolding.",
        ]

        interaction_contracts = [
            "Implement clear/reset interactions that fully reset inputs, derived state, and stale result panels.",
            "Disable duplicate submits while network actions are in flight and show explicit progress feedback.",
            "Support pointer, keyboard, and touch interactions for all primary controls.",
            "Provide explicit empty and error states with recovery guidance.",
        ]

        animation_contracts = [
            "Use purposeful motion for state transitions (entry, update, reveal) instead of decorative micro-animations.",
            "Drive animation with deterministic state transitions and stable timing/easing contracts.",
            "Target smooth interaction under typical load; avoid layout thrash during animated updates.",
            "Provide reduced-motion-safe behavior for accessibility preferences.",
        ]
        if complex_frontend:
            animation_contracts.append(
                "For high-motion surfaces, isolate animation loops with requestAnimationFrame/spring systems rather than ad-hoc timers."
            )
        if game_like_ui:
            animation_contracts.extend(
                [
                    "Model gauge/dial/guess-meter motion as a state machine with drag phase, release phase, and settle phase.",
                    "Support precise meter movement with pointer drag + keyboard nudging and smooth snap/reveal transitions.",
                ]
            )

        quality_gates = [
            "No placeholder visual states for critical interactions; all primary controls must be wired to real handlers.",
            "Interactive components must stay usable on both desktop and mobile viewport widths.",
            "Animation and interaction code must include tests for key state transitions where feasible.",
        ]
        if game_like_ui:
            quality_gates.append(
                "Game interaction primitives (meter movement, reveal, round reset) must be deterministic and test-covered."
            )

        return FrontendEngineerArtifact(
            ui_system_design=ui_system_design,
            interaction_contracts=interaction_contracts,
            animation_contracts=animation_contracts,
            quality_gates=quality_gates,
        )


class GuardianAgent:
    nickname = "Guardian"

    def run(self, visionary: VisionaryArtifact, blueprint: BlueprintArtifact) -> GuardianArtifact:
        requires_ui = any("frontend app" in item.lower() for item in blueprint.component_breakdown)
        test_plan = [
            "Validate all acceptance criteria from Visionary before deploy approval.",
            "Run preflight checks for Docker artifacts, test assets, and status route contract.",
            "Run automated tests (`python -m pytest -q`) with failure triage notes.",
            "Run runtime smoke checks and API verification against live containerized service.",
            "Run data reliability checks for dynamic/live-data workflows.",
        ]
        test_cases = [
            "Happy path: valid configuration produces successful result payload and persisted history.",
            "Validation path: malformed input returns deterministic 4xx errors with actionable messages.",
            "Resilience path: transient dependency outage does not crash service and preserves health endpoint.",
            "Scheduler path: repeated runs dedupe alerts and avoid duplicate notifications.",
            "Regression path: `/status` remains stable while feature endpoints evolve.",
        ]

        if any("alert" in criterion.lower() for criterion in visionary.acceptance_criteria):
            test_cases.append("Alert path: Brevo send failures are retried or surfaced without data loss.")
        if requires_ui:
            test_cases.extend(
                [
                    "UX clear/reset path: clear action removes stale results and returns the interface to initial state.",
                    "UX feedback path: loading spinner/message appears during fetch; empty and error states are human-readable.",
                ]
            )

        validation_results = [
            "Pre-implementation baseline: pending (tests execute after Craftsman output is generated).",
            "Release gate: ship only when tests, smoke checks, API verification, and data reliability checks pass.",
            "Failure policy: on QA failure, return findings to Craftsman and retry within configured attempt limits.",
        ]
        return GuardianArtifact(test_plan=test_plan, test_cases=test_cases, validation_results=validation_results)


class ShipmasterAgent:
    nickname = "Shipmaster"

    def run(self) -> ShipmasterArtifact:
        deployment_plan = [
            "Allocate free host ports before runtime container startup.",
            "Build and tag image with request-scoped identifier for traceability.",
            "Run runtime precheck container, then smoke test and API verification before final deploy.",
            "Start production runtime with assigned ports and persisted metadata.",
            "Store deployment logs/artifacts and expose runtime status in the UI.",
            "Keep restart-safe setup by persisting tool metadata and port allocations in MongoDB.",
        ]
        env_variables = [
            "MONGO_URI",
            "MONGO_DB_NAME",
            "BREVO_API_KEY",
            "BREVO_SENDER_EMAIL",
            "TZ (default `Asia/Kolkata` unless explicitly overridden)",
        ]
        runtime_checks = [
            "Container starts successfully with declared port mappings.",
            "`GET /status` returns `{\"status\":\"ok\"}` from allocated runtime host port.",
            "Runtime logs are fetchable for diagnostics.",
            "Tool status persisted as RUNNING only after smoke and API checks pass.",
        ]
        return ShipmasterArtifact(
            deployment_plan=deployment_plan,
            env_variables=env_variables,
            runtime_checks=runtime_checks,
        )


class CraftsmanAgent:
    nickname = "Craftsman"

    def build_prompt(
        self,
        refined_prompt: str,
        request_context_prompt: str,
        visionary: VisionaryArtifact,
        blueprint: BlueprintArtifact,
        backend_engineer: BackendEngineerArtifact,
        frontend_engineer: FrontendEngineerArtifact | None,
        guardian: GuardianArtifact,
        shipmaster: ShipmasterArtifact,
        requires_ui: bool,
    ) -> str:
        ux_baseline_section: list[str] = []
        if requires_ui:
            ux_baseline_section = [
                "",
                "UX baseline requirements (mandatory for web UI tools):",
                "- Include clear/reset controls for search/filter workflows so users can quickly start over.",
                "- Implement loading, empty-result, and error states with clear feedback and retry guidance.",
                "- Prevent duplicate submissions while requests are in flight.",
                "- Ensure keyboard accessibility, visible labels, and mobile-friendly responsive layout.",
            ]

        sections = [
            "You are the Senior Engineer agent \"Craftsman\" operating in a staged multi-agent pipeline.",
            "Execute the handoff in strict order: Visionary -> Blueprint -> Backend Engineer -> Frontend Engineer (when UI exists) -> Craftsman -> Guardian -> Shipmaster.",
            "Do not skip or weaken any upstream requirements.",
            "",
            "Craftsman mandate:",
            "- Write production-grade, runnable code with clean modular architecture.",
            "- No hacks, no placeholder logic, and no fake data unless explicitly requested.",
            "- Validate inputs, handle errors robustly, and log meaningful events.",
            "- Keep dependencies lean and suitable for lightweight environments.",
            "- Reuse shared MongoDB and Brevo integration defaults unless the user explicitly overrides.",
            "",
            "Specialized implementation pod protocol:",
            "- Backend Engineer \"Atlas\" owns API/domain/persistence/scheduler contracts.",
            "- Frontend Engineer \"Prism\" owns component architecture, interaction quality, and animation system design.",
            "- Craftsman integrates both slices, resolves interfaces, and ensures production readiness end-to-end.",
            "",
            "Visionary handoff:",
            visionary.to_markdown(),
            "",
            "Blueprint handoff:",
            blueprint.to_markdown(),
            "",
            "Backend Engineer handoff:",
            backend_engineer.to_markdown(),
            "",
            (
                "Frontend Engineer handoff:\n"
                f"{frontend_engineer.to_markdown()}"
                if frontend_engineer is not None
                else "Frontend Engineer handoff:\n- UI is not required for this request; skip frontend implementation pod."
            ),
            "",
            "Guardian handoff:",
            guardian.to_markdown(),
            *ux_baseline_section,
            "",
            "Shipmaster handoff:",
            shipmaster.to_markdown(),
            "",
            "Context prompt:",
            request_context_prompt.strip(),
            "",
            "Platform requirements and constraints:",
            refined_prompt.strip(),
            "",
            "Implementation deliverables:",
            "- Source code, tests, Dockerfile, and docker-compose runtime artifacts.",
            "- `docs/requirements.md` and `docs/test-cases.md` aligned to Visionary + Guardian outputs.",
            "- Preserve `/status` contract and keep runtime deployable via container workflow.",
            "- Ensure QA gates can pass without manual patching.",
        ]
        return "\n".join(part for part in sections if part is not None).strip()


class ToolBuilderAgentTeam:
    def __init__(self) -> None:
        self._visionary = VisionaryAgent()
        self._blueprint = BlueprintAgent()
        self._backend_engineer = BackendEngineerAgent()
        self._frontend_engineer = FrontendEngineerAgent()
        self._craftsman = CraftsmanAgent()
        self._guardian = GuardianAgent()
        self._shipmaster = ShipmasterAgent()

    def prepare(
        self,
        request_prompt: str,
        refined_prompt: str,
        request_context_prompt: str,
    ) -> AgentTeamPackage:
        normalized_request = _normalize_text(request_prompt)
        context_prompt = _normalize_text(request_context_prompt) or normalized_request
        requires_ui = _requires_ui(normalized_request)

        visionary = self._visionary.run(normalized_request)
        blueprint = self._blueprint.run(request_prompt=normalized_request, visionary=visionary)
        backend_engineer = self._backend_engineer.run(
            request_prompt=normalized_request,
            visionary=visionary,
            blueprint=blueprint,
        )
        frontend_engineer = (
            self._frontend_engineer.run(
                request_prompt=normalized_request,
                visionary=visionary,
                blueprint=blueprint,
            )
            if requires_ui
            else None
        )
        guardian = self._guardian.run(visionary=visionary, blueprint=blueprint)
        shipmaster = self._shipmaster.run()
        craftsman_prompt = self._craftsman.build_prompt(
            refined_prompt=refined_prompt,
            request_context_prompt=context_prompt,
            visionary=visionary,
            blueprint=blueprint,
            backend_engineer=backend_engineer,
            frontend_engineer=frontend_engineer,
            guardian=guardian,
            shipmaster=shipmaster,
            requires_ui=requires_ui,
        )

        return AgentTeamPackage(
            visionary=visionary,
            blueprint=blueprint,
            backend_engineer=backend_engineer,
            frontend_engineer=frontend_engineer,
            guardian=guardian,
            shipmaster=shipmaster,
            craftsman_prompt=craftsman_prompt,
        )


def _normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_inline(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _dedupe(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        candidate = _normalize_inline(str(item))
        if not candidate:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _requires_ui(prompt: str) -> bool:
    lowered = prompt.lower()
    ui_opt_out_terms = (
        "backend only",
        "api only",
        "no ui",
        "without ui",
        "cli only",
        "headless",
    )
    return not any(term in lowered for term in ui_opt_out_terms)


def _is_complex_frontend_request(prompt: str) -> bool:
    lowered = prompt.lower()
    if not _requires_ui(prompt):
        return False
    complexity_markers = (
        "animation",
        "animated",
        "game",
        "interactive",
        "drag",
        "gesture",
        "meter",
        "dial",
        "canvas",
        "real-time",
        "realtime",
        "multiplayer",
        "physics",
        "transition",
        "reveal",
        "framer",
        "motion",
    )
    score = sum(1 for marker in complexity_markers if marker in lowered)
    return score >= 2


def _is_game_like_ui_request(prompt: str) -> bool:
    lowered = prompt.lower()
    game_markers = (
        "wavelength",
        "guess meter",
        "dial",
        "gauge",
        "party game",
        "gameplay",
    )
    return any(marker in lowered for marker in game_markers)


def _is_complex_backend_request(prompt: str) -> bool:
    lowered = prompt.lower()
    backend_markers = (
        "websocket",
        "stream",
        "queue",
        "worker",
        "scheduler",
        "cron",
        "concurrency",
        "idempot",
        "retry",
        "rate limit",
        "transaction",
        "multiplayer",
        "real-time",
        "realtime",
        "orchestrat",
    )
    score = sum(1 for marker in backend_markers if marker in lowered)
    return score >= 2
