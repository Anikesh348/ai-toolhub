import { startTransition, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  buildJobLogArtifactDownloadUrl,
  buildJobLogArtifactViewUrl,
  JobSummary,
  JobLogArtifact,
  TOOL_BACKEND_BASE_URL,
  TOOL_FRONTEND_BASE_URL,
  ToolRecord,
  createToolBuilderSessionForTool,
  deleteJob,
  fetchJobLogArtifacts,
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

function formatArtifactSize(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} B`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

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
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [toolsById, setToolsById] = useState<Record<string, ToolRecord>>({});
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<{ toolId: string; action: "start" | "stop" | "rebuild" } | null>(
    null
  );
  const [chatLoadingToolId, setChatLoadingToolId] = useState<string | null>(null);
  const [deleteLoadingJobId, setDeleteLoadingJobId] = useState<string | null>(null);
  const [logsLoadingJobId, setLogsLoadingJobId] = useState<string | null>(null);
  const [activeLogsJob, setActiveLogsJob] = useState<JobSummary | null>(null);
  const [activeLogArtifacts, setActiveLogArtifacts] = useState<JobLogArtifact[]>([]);
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

      if (ACTIVE_STATUSES.includes(job.status) || job.status === "FAILED" || job.status === "STOPPED") {
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
      navigate(`/chat?chatId=${chat.id}`);
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

  async function handleOpenLogs(job: JobSummary): Promise<void> {
    setError(null);
    setLogsLoadingJobId(job.id);
    try {
      const artifacts = await fetchJobLogArtifacts(job.id);
      setActiveLogsJob(job);
      setActiveLogArtifacts(artifacts);
    } catch (logError) {
      setError(logError instanceof Error ? logError.message : "Unable to load log artifacts");
    } finally {
      setLogsLoadingJobId(null);
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
                <th className="px-3 py-3">Logs</th>
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
                    <button
                      type="button"
                      onClick={() => void handleOpenLogs(job)}
                      disabled={logsLoadingJobId === job.id}
                      className="btn-ghost border-amber/30 bg-black/35 px-2.5 py-1 text-xs text-[color:var(--text-main)]"
                    >
                      {logsLoadingJobId === job.id ? "Loading..." : "Logs"}
                    </button>
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
                  <td colSpan={7} className="px-3 py-8 text-center text-sm text-muted">
                    No generated tools yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {activeLogsJob && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/70 px-3 py-3 backdrop-blur-sm md:items-center md:px-4 md:py-6">
          <button
            type="button"
            aria-label="Close logs panel"
            onClick={() => {
              setActiveLogsJob(null);
              setActiveLogArtifacts([]);
            }}
            className="absolute inset-0"
          />
          <div className="relative z-[71] my-auto flex w-full max-w-3xl flex-col overflow-hidden rounded-[1.75rem] border border-amber/20 bg-[#11100d] shadow-[0_28px_90px_-30px_rgba(0,0,0,0.95)] max-md:min-h-[calc(100dvh-1.5rem)] max-md:max-h-[calc(100dvh-1.5rem)] md:max-h-[calc(100dvh-3rem)]">
            <div className="shrink-0 border-b border-amber/15 bg-[radial-gradient(circle_at_top,#2a2116_0%,#16120d_52%,#0d0b09_100%)] px-4 py-4 md:px-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-amber/70">Build Log Files</p>
                  <h2 className="mt-1 text-xl font-semibold text-[color:var(--text-main)]">{toolDisplayName(activeLogsJob)}</h2>
                  <p className="mt-2 text-sm text-muted">
                    Request `{activeLogsJob.id.slice(0, 8)}`. These artifacts are stored in MongoDB and can be downloaded individually.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setActiveLogsJob(null);
                    setActiveLogArtifacts([]);
                  }}
                  className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-black/35 text-muted transition hover:border-amber/35 hover:text-[color:var(--text-main)]"
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M6 6l12 12" />
                    <path d="M18 6L6 18" />
                  </svg>
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 md:px-5 md:py-5">
              {activeLogArtifacts.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-amber/25 bg-black/25 px-4 py-5 text-sm text-muted">
                  No stored log artifacts for this job yet.
                </div>
              ) : (
                <div className="space-y-3">
                  {activeLogArtifacts.map((artifact) => (
                    <div
                      key={artifact.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber/15 bg-black/25 px-4 py-3"
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-[color:var(--text-main)]">{artifact.fileName}</p>
                        <p className="mt-1 font-[var(--font-mono)] text-[11px] text-muted">{artifact.step}</p>
                        <p className="mt-1 text-[11px] text-muted">
                          {formatArtifactSize(artifact.sizeBytes)} • {formatDate(artifact.createdAt)}
                        </p>
                      </div>
                      <a
                        href={buildJobLogArtifactViewUrl(activeLogsJob.id, artifact.id)}
                        target="_blank"
                        rel="noreferrer"
                        className="btn-ghost border-amber/35 bg-amber/10 px-3 py-2 text-sm text-amber"
                      >
                        View
                      </a>
                      <a
                        href={buildJobLogArtifactDownloadUrl(activeLogsJob.id, artifact.id)}
                        className="btn-ghost border-skyline/45 bg-skyline/10 px-3 py-2 text-sm text-skyline"
                      >
                        Download
                      </a>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
