"use client";

import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  JobSummary,
  TOOL_BACKEND_BASE_URL,
  TOOL_FRONTEND_BASE_URL,
  ToolRecord,
  createToolBuilderSessionForTool,
  deleteJob,
  fetchJobs,
  fetchTools,
  formatDate,
  rebuildTool,
  startTool,
  stopTool,
  trimPrompt
} from "@/lib/api";
import { ACTIVE_STATUSES, STATUS_COLORS } from "@/lib/status";

const FRONTEND_SERVICE_HINTS = ["frontend", "web", "ui", "client", "dashboard", "site", "next", "vite"];
const BACKEND_SERVICE_HINTS = ["backend", "api", "server", "worker", "gateway", "graphql", "rest"];
const TERMINAL_REQUEST_STATUSES = new Set(["RUNNING", "FAILED"]);

function buildRuntimeUrl(baseUrl: string, port: number): string {
  return `${baseUrl}:${port}`;
}

function prefersFrontendService(serviceName: string): boolean | undefined {
  const lowered = serviceName.toLowerCase();
  if (BACKEND_SERVICE_HINTS.some((token) => lowered.includes(token))) {
    return false;
  }
  if (FRONTEND_SERVICE_HINTS.some((token) => lowered.includes(token))) {
    return true;
  }
  return undefined;
}

function buildServiceUrl(serviceName: string, port: number, uiPort: number | null): string {
  const preferred = prefersFrontendService(serviceName);
  if (preferred === true) {
    return buildRuntimeUrl(TOOL_FRONTEND_BASE_URL, port);
  }
  if (preferred === false) {
    return buildRuntimeUrl(TOOL_BACKEND_BASE_URL, port);
  }
  if (uiPort !== null && port === uiPort) {
    return buildRuntimeUrl(TOOL_FRONTEND_BASE_URL, port);
  }
  return buildRuntimeUrl(TOOL_BACKEND_BASE_URL, port);
}

export default function RequestsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [toolsById, setToolsById] = useState<Record<string, ToolRecord>>({});
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<{ toolId: string; action: "start" | "stop" | "rebuild" } | null>(
    null
  );
  const [chatLoadingToolId, setChatLoadingToolId] = useState<string | null>(null);
  const [deleteLoadingJobId, setDeleteLoadingJobId] = useState<string | null>(null);
  const pollingRef = useRef(false);

  const visibleJobs = useMemo(() => {
    const ordered = [...jobs].sort((lhs, rhs) => rhs.updatedAt.localeCompare(lhs.updatedAt));
    const seenToolIds = new Set<string>();
    const rows: JobSummary[] = [];

    for (const job of ordered) {
      if (job.toolId) {
        if (seenToolIds.has(job.toolId)) {
          continue;
        }
        seenToolIds.add(job.toolId);
        rows.push(job);
        continue;
      }

      if (ACTIVE_STATUSES.includes(job.status)) {
        rows.push(job);
      }
    }

    return rows;
  }, [jobs]);

  const runningCount = useMemo(() => visibleJobs.filter((job) => job.toolStatus === "RUNNING").length, [visibleJobs]);
  const hasActiveBuilds = useMemo(() => jobs.some((job) => ACTIVE_STATUSES.includes(job.status)), [jobs]);
  const pollIntervalMs = hasActiveBuilds ? 6000 : 12000;

  async function load(): Promise<void> {
    try {
      const [jobData, toolData] = await Promise.all([fetchJobs(), fetchTools()]);
      startTransition(() => {
        setJobs(jobData);
        setToolsById(
          toolData.reduce<Record<string, ToolRecord>>((acc, tool) => {
            acc[tool.toolId] = tool;
            return acc;
          }, {})
        );
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load requests");
    }
  }

  useEffect(() => {
    let mounted = true;

    const runPoll = async (): Promise<void> => {
      if (!mounted || pollingRef.current) {
        return;
      }
      pollingRef.current = true;
      try {
        await load();
      } finally {
        pollingRef.current = false;
      }
    };

    void runPoll();
    const intervalId = window.setInterval(() => {
      if (document.hidden) {
        return;
      }
      void runPoll();
    }, pollIntervalMs);

    const handleVisibilityChange = (): void => {
      if (!document.hidden) {
        void runPoll();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      mounted = false;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [pollIntervalMs]);

  async function handleStart(toolId: string): Promise<void> {
    setError(null);
    setActionLoading({ toolId, action: "start" });
    try {
      await startTool(toolId);
      await load();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Unable to start tool");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleStop(toolId: string): Promise<void> {
    setError(null);
    setActionLoading({ toolId, action: "stop" });
    try {
      await stopTool(toolId);
      await load();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Unable to stop tool");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleRebuild(toolId: string): Promise<void> {
    setError(null);
    setActionLoading({ toolId, action: "rebuild" });
    try {
      await rebuildTool(toolId);
      await load();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Unable to rebuild tool");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleOpenToolChat(toolId: string): Promise<void> {
    if (chatLoadingToolId) {
      return;
    }
    const tool = toolsById[toolId];
    if (!tool) {
      setError("Unable to open chat for this tool");
      return;
    }

    setError(null);
    setChatLoadingToolId(toolId);
    try {
      const chat = await createToolBuilderSessionForTool(tool);
      router.push(`/chat?chatId=${chat.id}`);
    } catch (chatError) {
      setError(chatError instanceof Error ? chatError.message : "Unable to open modify chat");
    } finally {
      setChatLoadingToolId(null);
    }
  }

  async function handleDeleteJob(job: JobSummary): Promise<void> {
    if (deleteLoadingJobId) {
      return;
    }
    const label = job.toolName || trimPrompt(job.prompt, 60);
    const confirmed = window.confirm(
      `Delete "${label}"?\n\nThis will remove the generated tool/job and stop associated containers.`
    );
    if (!confirmed) {
      return;
    }

    setError(null);
    setDeleteLoadingJobId(job.id);
    try {
      await deleteJob(job.id);
      await load();
    } catch (deleteJobError) {
      setError(deleteJobError instanceof Error ? deleteJobError.message : "Unable to delete tool/job");
    } finally {
      setDeleteLoadingJobId(null);
    }
  }

  function toolDisplayName(job: JobSummary): string {
    if (job.toolName) {
      return job.toolName;
    }
    return trimPrompt(job.prompt, 52);
  }

  return (
    <main className="flex flex-col gap-4 overflow-x-clip lg:h-full lg:min-h-0 lg:overflow-hidden">
      <header className="glass-strong rounded-[1.8rem] px-6 py-6 lg:shrink-0">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl">
            <p className="badge">Runtime Console</p>
            <h1 className="panel-title mt-2 text-3xl font-semibold md:text-4xl">Tools Generated</h1>
            <p className="mt-2 text-sm text-muted">Track generated tools, manage lifecycle, and open chat-driven modify chats.</p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className="stat-pill">
              Tool Jobs <strong>{visibleJobs.length}</strong>
            </span>
            <span className="stat-pill">
              Running <strong className="text-mint">{runningCount}</strong>
            </span>
            <span className="stat-pill">
              Polling <strong>{`${pollIntervalMs / 1000}s`}</strong>
            </span>
          </div>
        </div>
      </header>

      <section className="glass rounded-[1.65rem] p-4 md:p-6 lg:flex lg:min-h-0 lg:flex-col lg:overflow-hidden">
        <div className="flex items-center justify-between gap-3">
          <h2 className="panel-title text-xl font-semibold">All Generated Tools</h2>
          <p className="font-[var(--font-mono)] text-xs text-muted">{visibleJobs.length} rows</p>
        </div>
        {error && <div className="mt-4 rounded-xl border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{error}</div>}

        <div className="mt-4 overflow-x-auto rounded-2xl border border-amber/20 bg-black/40 lg:min-h-0 lg:flex-1 lg:overflow-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-[0.16em] text-muted">
                <th className="px-3 py-3">Tool Name</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Ports</th>
                <th className="px-3 py-3">Actions</th>
                <th className="px-3 py-3">Last Update</th>
                <th className="px-3 py-3">Delete</th>
              </tr>
            </thead>
            <tbody>
              {visibleJobs.map((job) => (
                <tr key={job.id} className="border-t border-amber/15 align-top">
                  <td className="px-3 py-3">
                    <p className="text-sm font-semibold text-[color:var(--text-main)]">{toolDisplayName(job)}</p>
                    <p className="mt-1 font-[var(--font-mono)] text-[11px] text-muted">Req: {job.id.slice(0, 8)}</p>
                  </td>
                  <td className="px-3 py-3">
                    <p className={`font-[var(--font-mono)] text-xs ${STATUS_COLORS[job.status]}`}>{job.status}</p>
                    <p className="mt-1 text-[11px] text-muted">Runtime: {job.toolStatus ?? "-"}</p>
                  </td>
                  <td className="px-3 py-3 text-xs text-muted">
                    {(() => {
                      const uiPort = job.uiPort ?? job.port;
                      const servicePorts = job.ports ?? {};
                      const servicePortPairs = Object.entries(servicePorts);

                      if (!uiPort && servicePortPairs.length === 0) {
                        return "-";
                      }

                      return (
                        <div className="flex flex-col gap-1">
                          {uiPort && (
                            <a
                              href={buildRuntimeUrl(TOOL_FRONTEND_BASE_URL, uiPort)}
                              target="_blank"
                              rel="noreferrer"
                              className="font-medium text-skyline underline underline-offset-2"
                            >
                              UI: {uiPort}
                            </a>
                          )}
                          {servicePortPairs.length > 0 && (
                            <div className="flex flex-wrap gap-x-2 gap-y-1 text-[11px] text-muted">
                              {servicePortPairs.map(([serviceName, servicePort]) => (
                                <a
                                  key={`${job.id}-${serviceName}-${servicePort}`}
                                  href={buildServiceUrl(serviceName, servicePort, uiPort ?? null)}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-muted underline underline-offset-2 hover:text-skyline"
                                >
                                  {serviceName}:{servicePort}
                                </a>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                  <td className="px-3 py-3">
                    {!job.toolId && (
                      <span className="text-xs text-muted">
                        {job.status === "RUNNING" ? "No runtime action" : "Building..."}
                      </span>
                    )}
                    {job.toolId && (
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => void handleOpenToolChat(job.toolId!)}
                          disabled={chatLoadingToolId === job.toolId}
                          className="btn-ghost border-skyline/45 bg-skyline/10 px-2.5 py-1 text-xs text-skyline"
                        >
                          {chatLoadingToolId === job.toolId ? "Opening..." : "Modify Chat"}
                        </button>
                        {(() => {
                          const buildInProgress = !TERMINAL_REQUEST_STATUSES.has(job.status);
                          const toolActionBusy = actionLoading?.toolId === job.toolId;
                          const stopBusy = toolActionBusy && actionLoading?.action === "stop";
                          const startBusy = toolActionBusy && actionLoading?.action === "start";
                          const rebuildBusy = toolActionBusy && actionLoading?.action === "rebuild";

                          return (
                            <>
                              {job.toolStatus === "RUNNING" ? (
                                <button
                                  type="button"
                                  onClick={() => void handleStop(job.toolId!)}
                                  disabled={toolActionBusy || buildInProgress}
                                  className="btn-ghost border-coral/35 bg-coral/10 px-2.5 py-1 text-xs text-coral disabled:opacity-50"
                                >
                                  {stopBusy ? "Stopping..." : "Stop"}
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => void handleStart(job.toolId!)}
                                  disabled={toolActionBusy || buildInProgress}
                                  className="btn-ghost border-mint/35 bg-mint/10 px-2.5 py-1 text-xs text-mint disabled:opacity-50"
                                >
                                  {startBusy ? "Starting..." : "Start"}
                                </button>
                              )}
                              <button
                                type="button"
                                onClick={() => void handleRebuild(job.toolId!)}
                                disabled={toolActionBusy || buildInProgress}
                                className="btn-ghost border-amber/35 bg-amber/10 px-2.5 py-1 text-xs text-amber disabled:opacity-50"
                                title={buildInProgress ? "A build is already in progress for this tool" : undefined}
                              >
                                {rebuildBusy ? "Rebuilding..." : "Rebuild + Start"}
                              </button>
                            </>
                          );
                        })()}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <p className="text-xs text-muted">{job.lastMessage ?? "-"}</p>
                    <p className="mt-1 text-[11px] text-muted">{formatDate(job.lastLogAt ?? job.updatedAt)}</p>
                  </td>
                  <td className="px-3 py-3">
                    <button
                      type="button"
                      onClick={() => void handleDeleteJob(job)}
                      disabled={deleteLoadingJobId === job.id}
                      className="btn-ghost border-amber/30 bg-black/35 px-2.5 py-1 text-xs text-[color:var(--text-main)]"
                    >
                      {deleteLoadingJobId === job.id ? "Deleting..." : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
              {visibleJobs.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-sm text-muted">
                    No generated tools yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
