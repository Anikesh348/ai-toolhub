export type BuildStatus =
  | "PENDING"
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

export function isTerminalStatus(status: BuildStatus): boolean {
  return status === "RUNNING" || status === "STOPPED" || status === "FAILED";
}
