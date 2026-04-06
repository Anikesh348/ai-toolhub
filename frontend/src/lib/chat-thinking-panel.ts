export type ThinkingPanelMode = "none" | "insta" | "knowledge";

export const THINKING_PANEL_MODE_STORAGE_KEY = "toolhub.chat.thinkingPanelMode";

export function normalizeThinkingPanelMode(value: unknown): ThinkingPanelMode {
  if (value === "insta") {
    return "insta";
  }
  if (value === "knowledge") {
    return "knowledge";
  }
  return "none";
}

export function loadThinkingPanelMode(): ThinkingPanelMode {
  if (typeof window === "undefined") {
    return "none";
  }
  try {
    return normalizeThinkingPanelMode(
      window.localStorage.getItem(THINKING_PANEL_MODE_STORAGE_KEY),
    );
  } catch {
    return "none";
  }
}

export function saveThinkingPanelMode(mode: ThinkingPanelMode): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(THINKING_PANEL_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore local storage write failures.
  }
}
