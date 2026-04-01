import { BuildStatus } from "./status";

export type JobSummary = {
  id: string;
  prompt: string;
  status: BuildStatus;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  toolId: string | null;
  toolStatus: string | null;
  toolName: string | null;
  port: number | null;
  uiPort: number | null;
  ports: Record<string, number> | null;
  containerId: string | null;
  lastStep: string | null;
  lastMessage: string | null;
  lastLogAt: string | null;
};

export type JobLog = {
  id: string;
  requestId: string;
  step: string;
  message: string;
  timestamp: string;
};

export type JobDetail = {
  id: string;
  prompt: string;
  refinedPrompt: string | null;
  status: BuildStatus;
  error: string | null;
  createdAt: string;
  updatedAt: string;
  logs: JobLog[];
};

export type JobEvent = {
  jobId: string;
  status: BuildStatus;
  error: string | null;
  updatedAt: string;
  logs: JobLog[];
};

export type ToolRecord = {
  toolId: string;
  requestId: string;
  name: string;
  dockerImage: string;
  containerId: string | null;
  port: number | null;
  uiPort: number | null;
  ports: Record<string, number> | null;
  status: "DEPLOYING" | "RUNNING" | "FAILED" | "STOPPED";
  createdAt: string;
  updatedAt: string;
};

export type ChatMode = "general" | "tool_builder" | "operator" | "pi_operator";

export type ChatSession = {
  id: string;
  title: string;
  mode: ChatMode;
  model: string | null;
  archived: boolean;
  createdAt: string;
  updatedAt: string;
};

export type ChatMessage = {
  id: string;
  sessionId: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  metadata: Record<string, unknown>;
  createdAt: string;
};

export type SendChatMessageResponse = {
  session: ChatSession;
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
};

export type DeleteChatSessionResponse = {
  sessionId: string;
  deleted: boolean;
};

export type UpdateChatSessionPayload = {
  title?: string | null;
  mode?: ChatMode;
  model?: string | null;
  archived?: boolean;
};

export type DeleteToolResponse = {
  toolId: string;
  deleted: boolean;
};

export type DeleteJobResponse = {
  jobId: string;
  deleted: boolean;
};

export type CodexAuthStatus = {
  loggedIn: boolean;
  provider: string | null;
  message: string;
  exitCode: number;
};

export type GitSshPublicKeyStatus = {
  publicKey: string;
  fingerprint: string | null;
  keyPath: string;
  generated: boolean;
  message: string;
};

export type GitSshVerification = {
  connected: boolean;
  host: string;
  username: string;
  exitCode: number;
  message: string;
  logs: string;
};

export type CodexLoginStreamEvent =
  | { type: "log"; chunk: string }
  | { type: "done"; success: boolean; exitCode: number; logs: string };

export type ChatStreamEvent =
  | { type: "user_message"; session: ChatSession; message: ChatMessage }
  | { type: "status"; status: "thinking" | "web_search" | "ready" | string }
  | { type: "assistant_delta"; delta: string }
  | { type: "assistant_message"; session: ChatSession; message: ChatMessage }
  | { type: "done" }
  | { type: "error"; error: string };

export type ChatModelsResponse = {
  models: string[];
  defaultModel: string | null;
};

export type ChatAttachment = {
  id: string;
  fileName: string;
  contentType: string;
  size: number;
  containerPath: string;
  url: string;
};

function normalizePublicBaseUrl(value: string | undefined, fallback: string): string {
  const cleaned = (value || "").trim();
  if (!cleaned) {
    return fallback;
  }
  const withScheme = cleaned.includes("://") ? cleaned : `http://${cleaned}`;
  return withScheme.replace(/\/+$/, "");
}

const ENV = import.meta.env;

export const API_BASE_URL = ENV.NEXT_PUBLIC_API_BASE_URL ?? ENV.VITE_API_BASE_URL ?? "http://localhost:8000";
export const TOOL_FRONTEND_BASE_URL = normalizePublicBaseUrl(
  ENV.NEXT_PUBLIC_TOOL_FRONTEND_BASE_URL ?? ENV.VITE_TOOL_FRONTEND_BASE_URL,
  "http://localhost"
);
export const TOOL_BACKEND_BASE_URL = normalizePublicBaseUrl(
  ENV.NEXT_PUBLIC_TOOL_BACKEND_BASE_URL ?? ENV.VITE_TOOL_BACKEND_BASE_URL,
  "http://localhost"
);

export async function fetchJobs(): Promise<JobSummary[]> {
  const response = await fetch(`${API_BASE_URL}/jobs`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to fetch jobs: ${response.status}`);
  }
  return (await response.json()) as JobSummary[];
}

export async function fetchCodexAuthStatus(): Promise<CodexAuthStatus> {
  const response = await fetch(`${API_BASE_URL}/codex/auth/status`, { cache: "no-store" });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to fetch Codex auth status: ${response.status} ${text}`);
  }
  return (await response.json()) as CodexAuthStatus;
}

export async function logoutCodexAuth(): Promise<CodexAuthStatus> {
  const response = await fetch(`${API_BASE_URL}/codex/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to log out of Codex auth: ${response.status} ${text}`);
  }
  return (await response.json()) as CodexAuthStatus;
}

export async function fetchGitSshPublicKey(): Promise<GitSshPublicKeyStatus> {
  const response = await fetch(`${API_BASE_URL}/integrations/git/ssh/public-key`, { cache: "no-store" });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to fetch Git SSH key: ${response.status} ${text}`);
  }
  return (await response.json()) as GitSshPublicKeyStatus;
}

export async function verifyGitSshConnection(host: string, username = "git"): Promise<GitSshVerification> {
  const response = await fetch(`${API_BASE_URL}/integrations/git/ssh/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ host, username })
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to verify Git SSH: ${response.status} ${text}`);
  }
  return (await response.json()) as GitSshVerification;
}

export async function fetchTools(): Promise<ToolRecord[]> {
  const response = await fetch(`${API_BASE_URL}/tools`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to fetch tools: ${response.status}`);
  }
  return (await response.json()) as ToolRecord[];
}

export async function fetchJobDetail(jobId: string): Promise<JobDetail> {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to fetch job details: ${response.status}`);
  }
  return (await response.json()) as JobDetail;
}

export async function deleteJob(jobId: string): Promise<DeleteJobResponse> {
  const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to delete job: ${response.status} ${text}`);
  }
  return (await response.json()) as DeleteJobResponse;
}

export async function submitTool(prompt: string, name?: string): Promise<{ jobId: string; status: BuildStatus }> {
  const response = await fetch(`${API_BASE_URL}/tools/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, name: name || null })
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to create request: ${response.status} ${text}`);
  }
  return (await response.json()) as { jobId: string; status: BuildStatus };
}

export async function startTool(toolId: string): Promise<ToolRecord> {
  const response = await fetch(`${API_BASE_URL}/tools/${toolId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to start tool: ${response.status} ${text}`);
  }
  return (await response.json()) as ToolRecord;
}

export async function rebuildTool(toolId: string): Promise<{ jobId: string; status: BuildStatus }> {
  const response = await fetch(`${API_BASE_URL}/tools/${toolId}/rebuild`, {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to rebuild tool: ${response.status} ${text}`);
  }
  return (await response.json()) as { jobId: string; status: BuildStatus };
}

export async function stopTool(toolId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/tools/${toolId}/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to stop tool: ${response.status} ${text}`);
  }
}

export async function deleteTool(toolId: string): Promise<DeleteToolResponse> {
  const response = await fetch(`${API_BASE_URL}/tools/${toolId}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to delete tool: ${response.status} ${text}`);
  }
  return (await response.json()) as DeleteToolResponse;
}

export function formatDate(value: string | null): string {
  if (!value) {
    return "-";
  }
  return new Date(value).toLocaleString();
}

export function trimPrompt(prompt: string, limit = 90): string {
  return prompt.length <= limit ? prompt : `${prompt.slice(0, limit - 3)}...`;
}

export function mergeLogs(existing: JobLog[], incoming: JobLog[]): JobLog[] {
  if (incoming.length === 0) {
    return existing;
  }

  const toKey = (log: JobLog): string => `${log.id}:${log.timestamp}:${log.step}`;
  const seen = new Set<string>();
  for (const log of existing) {
    seen.add(toKey(log));
  }

  const merged = [...existing];
  let changed = false;
  let outOfOrder = false;
  let lastTimestamp = existing.length > 0 ? existing[existing.length - 1].timestamp : "";

  for (const log of incoming) {
    const key = toKey(log);
    if (seen.has(key)) {
      continue;
    }
    if (lastTimestamp && log.timestamp < lastTimestamp) {
      outOfOrder = true;
    }
    merged.push(log);
    seen.add(key);
    lastTimestamp = log.timestamp;
    changed = true;
  }

  if (!changed) {
    return existing;
  }

  if (outOfOrder) {
    merged.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  }

  return merged.slice(-250);
}

export async function fetchChatSessions(): Promise<ChatSession[]> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to fetch chats: ${response.status}`);
  }
  return (await response.json()) as ChatSession[];
}

export async function fetchChatModels(): Promise<ChatModelsResponse> {
  const response = await fetch(`${API_BASE_URL}/chat/models`, { cache: "no-store" });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to fetch chat models: ${response.status} ${text}`);
  }
  return (await response.json()) as ChatModelsResponse;
}

export async function createChatSession(title?: string, mode: ChatMode = "general", model?: string | null): Promise<ChatSession> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || null, mode, model: model || null })
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to create chat: ${response.status} ${text}`);
  }
  return (await response.json()) as ChatSession;
}

export async function deleteChatSession(sessionId: string): Promise<DeleteChatSessionResponse> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to delete chat: ${response.status} ${text}`);
  }
  return (await response.json()) as DeleteChatSessionResponse;
}

export async function updateChatSession(sessionId: string, payload: UpdateChatSessionPayload): Promise<ChatSession> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to update chat: ${response.status} ${text}`);
  }
  return (await response.json()) as ChatSession;
}

export async function fetchChatMessages(sessionId: string): Promise<ChatMessage[]> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}/messages`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to fetch chat messages: ${response.status}`);
  }
  return (await response.json()) as ChatMessage[];
}

export async function sendChatMessage(
  sessionId: string,
  content: string,
  model?: string | null,
  attachmentIds: string[] = []
): Promise<SendChatMessageResponse> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, model: model || null, attachmentIds })
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to send chat message: ${response.status} ${text}`);
  }
  return (await response.json()) as SendChatMessageResponse;
}

export async function createToolBuilderSessionForTool(tool: ToolRecord): Promise<ChatSession> {
  const existingSession = await findExistingToolBuilderSession(tool);
  if (existingSession) {
    return existingSession;
  }

  const title = `Modify ${tool.name} (${tool.toolId.slice(0, 8)})`;
  const session = await createChatSession(title, "tool_builder");
  const runtimeHint = tool.uiPort
    ? `Current UI port is ${tool.uiPort}.`
    : "Current UI port is unknown; resolve it from status.";
  const seedPrompt = (
    `Use tool id ${tool.toolId} (name: ${tool.name}, request id: ${tool.requestId}) as active context for this chat. `
    + "I want follow-up changes to modify this tool in place and redeploy it. "
    + `${runtimeHint} `
    + "First, share current status and port."
  );
  await sendChatMessage(session.id, seedPrompt);
  return session;
}

function toolBuilderMetadataFromMessage(message: ChatMessage): { toolId?: string; requestId?: string } {
  const metadata = message.metadata;
  const rawToolBuilder = metadata?.toolBuilder;
  if (!rawToolBuilder || typeof rawToolBuilder !== "object") {
    return {};
  }
  const toolBuilder = rawToolBuilder as Record<string, unknown>;
  return {
    toolId: typeof toolBuilder.toolId === "string" ? toolBuilder.toolId : undefined,
    requestId: typeof toolBuilder.requestId === "string" ? toolBuilder.requestId : undefined
  };
}

function messageReferencesTool(message: ChatMessage, tool: ToolRecord): boolean {
  const metadata = toolBuilderMetadataFromMessage(message);
  if (metadata.toolId === tool.toolId) {
    return true;
  }
  if (metadata.requestId === tool.requestId) {
    return true;
  }

  return message.content.toLowerCase().includes(tool.toolId.toLowerCase());
}

async function findExistingToolBuilderSession(tool: ToolRecord): Promise<ChatSession | null> {
  const sessions = await fetchChatSessions();
  const toolBuilderSessions = sessions
    .filter((session) => session.mode === "tool_builder")
    .sort((lhs, rhs) => new Date(rhs.updatedAt).getTime() - new Date(lhs.updatedAt).getTime());

  if (toolBuilderSessions.length === 0) {
    return null;
  }

  const titledMatch = toolBuilderSessions.find((session) => {
    const lowered = session.title.toLowerCase();
    return (
      lowered.includes(tool.toolId.toLowerCase()) ||
      lowered === `modify ${tool.name}`.toLowerCase() ||
      lowered.startsWith(`modify ${tool.name}`.toLowerCase())
    );
  });
  if (titledMatch) {
    try {
      const messages = await fetchChatMessages(titledMatch.id);
      if (messages.length === 0 || messages.some((message) => messageReferencesTool(message, tool))) {
        return titledMatch;
      }
    } catch {
      return titledMatch;
    }
  }

  for (const session of toolBuilderSessions.slice(0, 20)) {
    try {
      const messages = await fetchChatMessages(session.id);
      if (messages.some((message) => messageReferencesTool(message, tool))) {
        return session;
      }
    } catch {
      continue;
    }
  }

  return null;
}

export async function streamChatMessage(
  sessionId: string,
  content: string,
  model: string | null | undefined,
  attachmentIds: string[] | undefined,
  onEvent: (event: ChatStreamEvent) => void
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, model: model || null, attachmentIds: attachmentIds || [] })
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to stream chat message: ${response.status} ${text}`);
  }
  if (!response.body) {
    throw new Error("Streaming response body is unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushPackets = (): string[] => {
    const normalized = buffer.replace(/\r\n/g, "\n");
    const packets: string[] = [];
    let cursor = 0;

    while (true) {
      const separatorIndex = normalized.indexOf("\n\n", cursor);
      if (separatorIndex === -1) {
        break;
      }
      packets.push(normalized.slice(cursor, separatorIndex));
      cursor = separatorIndex + 2;
    }

    buffer = normalized.slice(cursor);
    return packets;
  };

  const parseDataPayload = (packet: string): string | null => {
    const dataLines = packet
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart());

    if (dataLines.length === 0) {
      return null;
    }
    return dataLines.join("\n");
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });

    for (const packet of flushPackets()) {
      const rawJson = parseDataPayload(packet);
      if (!rawJson) {
        continue;
      }
      try {
        const parsed = JSON.parse(rawJson) as ChatStreamEvent;
        onEvent(parsed);
      } catch {
        continue;
      }
    }
  }

  buffer += decoder.decode();
  for (const packet of flushPackets()) {
    const rawJson = parseDataPayload(packet);
    if (!rawJson) {
      continue;
    }
    try {
      const parsed = JSON.parse(rawJson) as ChatStreamEvent;
      onEvent(parsed);
    } catch {
      continue;
    }
  }

  const trailingPayload = parseDataPayload(buffer);
  if (trailingPayload) {
    try {
      const parsed = JSON.parse(trailingPayload) as ChatStreamEvent;
      onEvent(parsed);
    } catch {
      // Ignore partial trailing payloads.
    }
  }
}

export async function uploadChatAttachment(sessionId: string, file: File): Promise<ChatAttachment> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE_URL}/chat/sessions/${sessionId}/attachments`, {
    method: "POST",
    body: formData
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to upload attachment: ${response.status} ${text}`);
  }
  return (await response.json()) as ChatAttachment;
}

export async function streamCodexLogin(
  onEvent: (event: CodexLoginStreamEvent) => void
): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/codex/auth/login/events`, {
    method: "GET",
    cache: "no-store"
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Unable to start Codex login: ${response.status} ${text}`);
  }
  if (!response.body) {
    throw new Error("Codex login stream is unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const flushPackets = (): string[] => {
    const normalized = buffer.replace(/\r\n/g, "\n");
    const packets: string[] = [];
    let cursor = 0;

    while (true) {
      const separatorIndex = normalized.indexOf("\n\n", cursor);
      if (separatorIndex === -1) {
        break;
      }
      packets.push(normalized.slice(cursor, separatorIndex));
      cursor = separatorIndex + 2;
    }

    buffer = normalized.slice(cursor);
    return packets;
  };

  const parseDataPayload = (packet: string): string | null => {
    const dataLines = packet
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart());

    if (dataLines.length === 0) {
      return null;
    }
    return dataLines.join("\n");
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });

    for (const packet of flushPackets()) {
      const rawJson = parseDataPayload(packet);
      if (!rawJson) {
        continue;
      }
      try {
        const parsed = JSON.parse(rawJson) as CodexLoginStreamEvent;
        onEvent(parsed);
      } catch {
        continue;
      }
    }
  }

  buffer += decoder.decode();
  for (const packet of flushPackets()) {
    const rawJson = parseDataPayload(packet);
    if (!rawJson) {
      continue;
    }
    try {
      const parsed = JSON.parse(rawJson) as CodexLoginStreamEvent;
      onEvent(parsed);
    } catch {
      continue;
    }
  }

  const trailingPayload = parseDataPayload(buffer);
  if (trailingPayload) {
    try {
      const parsed = JSON.parse(trailingPayload) as CodexLoginStreamEvent;
      onEvent(parsed);
    } catch {
      // Ignore partial trailing payloads.
    }
  }
}
