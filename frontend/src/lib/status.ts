export type BuildStatus =
  | "PENDING"
  | "VISIONARY_REFINING"
  | "BLUEPRINT_DESIGNING"
  | "CRAFTSMAN_IMPLEMENTING"
  | "GUARDIAN_VALIDATING"
  | "SHIPMASTER_DEPLOYING"
  | "REFINING_PROMPT"
  | "GENERATING_CODE"
  | "TESTING"
  | "FIXING_ERRORS"
  | "BUILDING_IMAGE"
  | "DEPLOYING"
  | "VERIFYING_APIS"
  | "RUNNING"
  | "STOPPED"
  | "FAILED";

export const ACTIVE_STATUSES: BuildStatus[] = [
  "PENDING",
  "VISIONARY_REFINING",
  "BLUEPRINT_DESIGNING",
  "CRAFTSMAN_IMPLEMENTING",
  "GUARDIAN_VALIDATING",
  "SHIPMASTER_DEPLOYING",
  "REFINING_PROMPT",
  "GENERATING_CODE",
  "TESTING",
  "FIXING_ERRORS",
  "BUILDING_IMAGE",
  "DEPLOYING",
  "VERIFYING_APIS"
];

export const STATUS_COLORS: Record<BuildStatus, string> = {
  PENDING: "text-skyline",
  VISIONARY_REFINING: "text-skyline",
  BLUEPRINT_DESIGNING: "text-skyline",
  CRAFTSMAN_IMPLEMENTING: "text-amber",
  GUARDIAN_VALIDATING: "text-amber",
  SHIPMASTER_DEPLOYING: "text-skyline",
  REFINING_PROMPT: "text-skyline",
  GENERATING_CODE: "text-amber",
  TESTING: "text-amber",
  FIXING_ERRORS: "text-coral",
  BUILDING_IMAGE: "text-amber",
  DEPLOYING: "text-skyline",
  VERIFYING_APIS: "text-skyline",
  RUNNING: "text-mint",
  STOPPED: "text-muted",
  FAILED: "text-coral"
};

export type BuildStageInfo = {
  stage: string;
  agent: string;
  message: string;
};

export const STATUS_STAGE_INFO: Record<BuildStatus, BuildStageInfo> = {
  PENDING: {
    stage: "Queued",
    agent: "Orchestrator",
    message: "Preparing build queue..."
  },
  VISIONARY_REFINING: {
    stage: "Requirements",
    agent: "🧠 Visionary",
    message: "🧠 Visionary is refining requirements..."
  },
  BLUEPRINT_DESIGNING: {
    stage: "Architecture",
    agent: "🏗️ Blueprint",
    message: "🏗️ Blueprint is designing architecture..."
  },
  CRAFTSMAN_IMPLEMENTING: {
    stage: "Implementation",
    agent: "👨‍💻 Craftsman",
    message: "👨‍💻 Craftsman is implementing the solution..."
  },
  GUARDIAN_VALIDATING: {
    stage: "Validation",
    agent: "🧪 Guardian",
    message: "🧪 Guardian is validating quality gates..."
  },
  SHIPMASTER_DEPLOYING: {
    stage: "Deployment",
    agent: "⚙️ Shipmaster",
    message: "⚙️ Shipmaster is preparing deployment..."
  },
  REFINING_PROMPT: {
    stage: "Requirements",
    agent: "🧠 Visionary",
    message: "🧠 Visionary is refining requirements..."
  },
  GENERATING_CODE: {
    stage: "Implementation",
    agent: "👨‍💻 Craftsman",
    message: "👨‍💻 Craftsman is implementing the solution..."
  },
  TESTING: {
    stage: "Validation",
    agent: "🧪 Guardian",
    message: "🧪 Guardian is validating quality gates..."
  },
  FIXING_ERRORS: {
    stage: "Remediation",
    agent: "👨‍💻 Craftsman",
    message: "👨‍💻 Craftsman is fixing QA findings..."
  },
  BUILDING_IMAGE: {
    stage: "Packaging",
    agent: "⚙️ Shipmaster",
    message: "⚙️ Shipmaster is building deployment artifacts..."
  },
  DEPLOYING: {
    stage: "Deployment",
    agent: "⚙️ Shipmaster",
    message: "⚙️ Shipmaster is deploying runtime..."
  },
  VERIFYING_APIS: {
    stage: "Validation",
    agent: "🧪 Guardian",
    message: "🧪 Guardian is verifying runtime APIs..."
  },
  RUNNING: {
    stage: "Live",
    agent: "Platform",
    message: "Tool is live and healthy."
  },
  STOPPED: {
    stage: "Stopped",
    agent: "Platform",
    message: "Build stopped."
  },
  FAILED: {
    stage: "Failed",
    agent: "Platform",
    message: "Build failed."
  }
};

export function isTerminalStatus(status: BuildStatus): boolean {
  return status === "RUNNING" || status === "STOPPED" || status === "FAILED";
}
