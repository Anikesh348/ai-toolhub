export type BuildStatus =
  | "PENDING"
  | "REFINING_PROMPT"
  | "GENERATING_CODE"
  | "TESTING"
  | "FIXING_ERRORS"
  | "BUILDING_IMAGE"
  | "DEPLOYING"
  | "RUNNING"
  | "FAILED";

export const ACTIVE_STATUSES: BuildStatus[] = [
  "PENDING",
  "REFINING_PROMPT",
  "GENERATING_CODE",
  "TESTING",
  "FIXING_ERRORS",
  "BUILDING_IMAGE",
  "DEPLOYING"
];

export const STATUS_COLORS: Record<BuildStatus, string> = {
  PENDING: "text-skyline",
  REFINING_PROMPT: "text-skyline",
  GENERATING_CODE: "text-amber",
  TESTING: "text-amber",
  FIXING_ERRORS: "text-coral",
  BUILDING_IMAGE: "text-amber",
  DEPLOYING: "text-skyline",
  RUNNING: "text-mint",
  FAILED: "text-coral"
};

export function isTerminalStatus(status: BuildStatus): boolean {
  return status === "RUNNING" || status === "FAILED";
}

