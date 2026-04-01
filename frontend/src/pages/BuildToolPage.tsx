import { FormEvent, startTransition, useEffect, useMemo, useRef, useState } from "react";

import {
  API_BASE_URL,
  JobDetail,
  JobEvent,
  JobSummary,
  fetchJobDetail,
  fetchJobs,
  formatDate,
  mergeLogs,
  submitTool,
  trimPrompt
} from "@/lib/api";
import { STATUS_COLORS, isTerminalStatus } from "@/lib/status";

export default function BuildToolPage() {
  const [prompt, setPrompt] = useState("");
  const [toolName, setToolName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uiError, setUiError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<JobDetail | null>(null);
  const jobsPollingRef = useRef(false);

  const activeJobs = useMemo(() => jobs.filter((job) => !isTerminalStatus(job.status)), [jobs]);
  const activeSummary = useMemo(() => activeJobs.find((job) => job.id === activeJobId) ?? null, [activeJobs, activeJobId]);

  async function loadJobs(): Promise<void> {
    try {
      const data = await fetchJobs();
      startTransition(() => setJobs(data));
      setActiveJobId((current) => {
        if (current && data.some((job) => job.id === current && !isTerminalStatus(job.status))) {
          return current;
        }
        const firstActive = data.find((job) => !isTerminalStatus(job.status));
        return firstActive ? firstActive.id : null;
      });
    } catch (error) {
      setUiError(error instanceof Error ? error.message : "Unable to load jobs");
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setUiError(null);
    setSubmitting(true);
    try {
      const payload = await submitTool(prompt, toolName);
      setPrompt("");
      setToolName("");
      setActiveJobId(payload.jobId);
      await loadJobs();
      const detail = await fetchJobDetail(payload.jobId);
      setActiveJob(detail);
    } catch (error) {
      setUiError(error instanceof Error ? error.message : "Failed to submit request");
    } finally {
      setSubmitting(false);
    }
  }

  useEffect(() => {
    let mounted = true;
    const pollIntervalMs = activeJobId ? 5000 : 12000;

    const runPoll = async (): Promise<void> => {
      if (!mounted || jobsPollingRef.current) {
        return;
      }

      jobsPollingRef.current = true;
      try {
        await loadJobs();
      } finally {
        jobsPollingRef.current = false;
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
  }, [activeJobId]);

  useEffect(() => {
    if (!activeJobId) {
      setActiveJob(null);
      return;
    }
    let mounted = true;
    void fetchJobDetail(activeJobId)
      .then((detail) => {
        if (mounted) {
          setActiveJob(detail);
        }
      })
      .catch((error: unknown) => {
        if (mounted) {
          setUiError(error instanceof Error ? error.message : "Unable to fetch job details");
        }
      });

    const eventSource = new EventSource(`${API_BASE_URL}/jobs/${activeJobId}/events`);
    eventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as JobEvent;
        startTransition(() => {
          setActiveJob((current) => {
            if (!current || current.id !== payload.jobId) {
              return current;
            }

            const mergedLogs = mergeLogs(current.logs, payload.logs);
            if (
              current.status === payload.status
              && current.error === payload.error
              && current.updatedAt === payload.updatedAt
              && mergedLogs === current.logs
            ) {
              return current;
            }

            return {
              ...current,
              status: payload.status,
              error: payload.error,
              updatedAt: payload.updatedAt,
              logs: mergedLogs
            };
          });
        });
        if (isTerminalStatus(payload.status)) {
          void loadJobs();
        }
      } catch {
        return;
      }
    };
    eventSource.onerror = () => {
      eventSource.close();
    };

    return () => {
      mounted = false;
      eventSource.close();
    };
  }, [activeJobId]);

  return (
    <main className="flex flex-col gap-4 overflow-x-clip lg:h-full lg:min-h-0 lg:overflow-hidden">
      <header className="glass-strong rounded-[1.8rem] px-6 py-6 lg:shrink-0">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-2xl">
            <p className="badge">Build Pipeline</p>
            <h1 className="panel-title mt-2 text-3xl font-semibold md:text-4xl">Tool Builder</h1>
            <p className="mt-2 text-sm text-muted">Turn product prompts into tested, deployable apps with live build telemetry.</p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className="stat-pill">
              Active <strong>{activeJobs.length}</strong>
            </span>
            <span className="stat-pill">
              Total Jobs <strong>{jobs.length}</strong>
            </span>
            <span className="stat-pill">
              Status <strong className={activeSummary ? STATUS_COLORS[activeSummary.status] : "text-muted"}>{activeSummary ? activeSummary.status : "Idle"}</strong>
            </span>
          </div>
        </div>
      </header>

      <section className="grid gap-4 lg:min-h-0 lg:flex-1 lg:overflow-hidden xl:grid-cols-[430px_1fr]">
        <article className="glass rounded-[1.65rem] p-5 md:p-6 lg:min-h-0 lg:overflow-y-auto">
          <h2 className="panel-title text-xl font-semibold">New Tool Request</h2>
          <p className="mt-1 text-xs text-muted">Describe what you want to build and any requirements we should enforce.</p>
          <form className="mt-4 space-y-4" onSubmit={handleSubmit}>
            <label className="block">
              <span className="mb-1 block text-sm text-muted">Tool Name (optional)</span>
              <input
                value={toolName}
                onChange={(event) => setToolName(event.target.value)}
                className="field px-3 py-2 text-sm"
                placeholder="Customer Insight Dashboard"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm text-muted">Prompt</span>
              <textarea
                required
                minLength={10}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={11}
                className="field min-h-[220px] resize-y px-3 py-2 text-sm"
                placeholder="Create a full-stack app with analytics widgets, API integration, auth, and tests."
              />
            </label>
            <button type="submit" disabled={submitting} className="btn-primary w-full px-4 py-2.5 text-sm">
              {submitting ? "Submitting..." : "Generate Tool"}
            </button>
          </form>
          {uiError && <div className="mt-4 rounded-xl border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{uiError}</div>}
        </article>

        <article className="glass-strong rounded-[1.65rem] p-5 md:p-6 lg:flex lg:min-h-0 lg:flex-col lg:overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="panel-title text-xl font-semibold">Live Progress</h2>
            <span className="badge">SSE</span>
          </div>

          {activeJobs.length > 0 && (
            <div className="mt-4 flex max-h-28 flex-wrap gap-2 overflow-y-auto">
              {activeJobs.map((job) => (
                <button
                  key={job.id}
                  type="button"
                  onClick={() => setActiveJobId(job.id)}
                  className={`rounded-full border px-3 py-1.5 text-left text-xs transition ${
                    activeJobId === job.id
                      ? "border-skyline/55 bg-skyline/15 text-[color:var(--text-main)]"
                      : "border-amber/20 bg-black/35 text-muted hover:border-skyline/45 hover:bg-black/55"
                  }`}
                >
                  <span className="font-[var(--font-mono)] text-[11px] text-skyline">{job.id.slice(0, 8)}</span>
                  <span className="ml-2">{trimPrompt(job.prompt, 75)}</span>
                </button>
              ))}
            </div>
          )}

          {!activeJob && (
            <div className="mt-4 rounded-2xl border border-dashed border-amber/30 bg-black/35 px-4 py-4 text-sm text-muted">
              No active build right now. Start a new request to watch real-time logs.
            </div>
          )}

          {activeJob && (
            <div className="mt-4 space-y-4 lg:min-h-0 lg:flex-1 lg:overflow-hidden">
              <div className="rounded-2xl border border-amber/20 bg-black/40 p-4">
                <p className="font-[var(--font-mono)] text-xs text-muted">Prompt</p>
                <p className="mt-1 text-sm text-[color:var(--text-main)]">{activeJob.prompt}</p>
                {activeJob.error && (
                  <p className="mt-3 rounded-lg border border-coral/35 bg-coral/10 px-2 py-1 text-xs text-coral">{activeJob.error}</p>
                )}
              </div>
              <div className="rounded-2xl border border-amber/20 bg-black/35 p-3 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
                <p className="mb-2 font-[var(--font-mono)] text-xs text-muted">Build Logs</p>
                <div className="max-h-[26rem] space-y-2 overflow-y-auto pr-1 lg:max-h-none lg:min-h-0 lg:flex-1">
                  {activeJob.logs.length === 0 && <p className="text-xs text-muted">No logs yet.</p>}
                  {activeJob.logs.map((log) => (
                    <div key={`${log.id}:${log.timestamp}:${log.step}`} className="rounded-xl border border-amber/20 bg-black/50 px-3 py-2.5">
                      <p className="font-[var(--font-mono)] text-[11px] text-skyline">{log.step}</p>
                      <p className="mt-1 whitespace-pre-wrap text-xs text-[color:var(--text-main)]">{log.message}</p>
                      <p className="mt-1 text-[10px] text-muted">{formatDate(log.timestamp)}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </article>
      </section>
    </main>
  );
}
