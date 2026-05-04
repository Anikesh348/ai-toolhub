import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  ChatExecutionLog,
  ChatUsageSummary,
  CodexUsageStatus,
  currentIstTimestamp,
  CodexAuthStatus,
  CodexLoginStreamEvent,
  fetchChatExecutionLogs,
  fetchChatUsageSummary,
  fetchCodexAuthStatus,
  fetchCodexUsageStatus,
  fetchGitSshPublicKey,
  fetchYouTubeShortSettings,
  formatDate,
  GitSshPublicKeyStatus,
  GitSshVerification,
  logoutCodexAuth,
  streamCodexLogin,
  updateYouTubeShortSettings,
  verifyGitSshConnection,
  YouTubeShortSettings,
  YouTubeShortQuerySeed
} from "@/lib/api";
import {
  loadThinkingPanelMode,
  saveThinkingPanelMode,
  ThinkingPanelMode
} from "@/lib/chat-thinking-panel";
import { useLocalAuth } from "@/lib/local-auth";

const GIT_SSH_HOST_STORAGE_KEY = "toolhub.git.ssh.host";
const GIT_SSH_USERNAME_STORAGE_KEY = "toolhub.git.ssh.username";
const GIT_SSH_VERIFICATION_STORAGE_KEY = "toolhub.git.ssh.verification";
const INTEGER_FORMATTER = new Intl.NumberFormat("en-IN");
const THINKING_PANEL_OPTIONS: Array<{
  value: ThinkingPanelMode;
  label: string;
  detail: string;
}> = [
  {
    value: "none",
    label: "None",
    detail: "Keep chat full width while the model is thinking."
  },
  {
    value: "insta",
    label: "YouTube Shorts",
    detail: "Show public YouTube Shorts in the right companion panel."
  },
  {
    value: "knowledge",
    label: "System Design Reads",
    detail: "Show rotating system design article summaries in the right companion panel."
  }
];
const MAX_SHORT_QUERY_SEEDS = 40;
type YouTubeShortDraftRow = {
  id: string;
  category: string;
  query: string;
};

function formatCount(value: number | null | undefined): string {
  return INTEGER_FORMATTER.format(Math.max(0, Math.round(value || 0)));
}

function formatUsd(value: number): string {
  if (value < 0.01) {
    return `$${value.toFixed(4)}`;
  }
  return `$${value.toFixed(2)}`;
}

function formatInr(value: number): string {
  if (value < 1) {
    return `₹${value.toFixed(2)}`;
  }
  return `₹${INTEGER_FORMATTER.format(Number(value.toFixed(0)))}`;
}

function formatPlanType(value: string | null | undefined): string {
  if (!value) {
    return "Unknown";
  }
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "0%";
  }
  return `${Math.max(0, Math.min(100, Math.round(value)))}%`;
}

export default function AccountPage() {
  const navigate = useNavigate();
  const { isAuthenticated, signOut, username } = useLocalAuth();

  const [auth, setAuth] = useState<CodexAuthStatus | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [loginInProgress, setLoginInProgress] = useState(false);
  const [logoutInProgress, setLogoutInProgress] = useState(false);
  const [authLogs, setAuthLogs] = useState("");
  const [codexUsage, setCodexUsage] = useState<CodexUsageStatus | null>(null);
  const [loadingCodexUsage, setLoadingCodexUsage] = useState(false);
  const [gitSshKey, setGitSshKey] = useState<GitSshPublicKeyStatus | null>(null);
  const [loadingGitSshKey, setLoadingGitSshKey] = useState(true);
  const [copyKeyMessage, setCopyKeyMessage] = useState<string | null>(null);
  const [gitHost, setGitHost] = useState("github.com");
  const [gitUsername, setGitUsername] = useState("git");
  const [gitVerifying, setGitVerifying] = useState(false);
  const [gitVerification, setGitVerification] = useState<GitSshVerification | null>(null);
  const [gitVerificationCheckedAt, setGitVerificationCheckedAt] = useState<string | null>(null);
  const [chatLogs, setChatLogs] = useState<ChatExecutionLog[]>([]);
  const [chatUsage, setChatUsage] = useState<ChatUsageSummary | null>(null);
  const [chatLogsLoading, setChatLogsLoading] = useState(false);
  const [activeChatLog, setActiveChatLog] = useState<ChatExecutionLog | null>(null);
  const [thinkingPanelMode, setThinkingPanelMode] = useState<ThinkingPanelMode>("none");
  const [youtubeShortSettings, setYouTubeShortSettings] = useState<YouTubeShortSettings | null>(null);
  const [youtubeShortSettingsLoading, setYouTubeShortSettingsLoading] = useState(false);
  const [youtubeShortSettingsSaving, setYouTubeShortSettingsSaving] = useState(false);
  const [youtubeShortSettingsModalOpen, setYouTubeShortSettingsModalOpen] = useState(false);
  const [youtubeShortRegionDraft, setYouTubeShortRegionDraft] = useState("");
  const [youtubeShortBoostDraft, setYouTubeShortBoostDraft] = useState(3);
  const [youtubeShortPreferredDraft, setYouTubeShortPreferredDraft] = useState("");
  const [youtubeShortQueryDraftRows, setYouTubeShortQueryDraftRows] = useState<YouTubeShortDraftRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const gitVerificationMatchesTarget = useMemo(() => {
    if (!gitVerification) {
      return false;
    }
    return (
      gitVerification.host.trim().toLowerCase() === gitHost.trim().toLowerCase()
      && gitVerification.username.trim().toLowerCase() === gitUsername.trim().toLowerCase()
    );
  }, [gitVerification, gitHost, gitUsername]);

  const codexStatusLabel = useMemo(() => {
    if (loadingAuth) {
      return "Checking";
    }
    if (auth?.loggedIn) {
      return "Connected";
    }
    return "Not connected";
  }, [loadingAuth, auth]);

  const gitConnected = useMemo(() => {
    return Boolean(gitVerificationMatchesTarget && gitVerification?.connected);
  }, [gitVerificationMatchesTarget, gitVerification]);

  const gitStatusLabel = useMemo(() => {
    if (gitVerifying) {
      return "Checking";
    }
    return gitConnected ? "Connected" : "Not connected";
  }, [gitVerifying, gitConnected]);

  const profileName = useMemo(() => {
    return username?.trim() || "ToolHub User";
  }, [username]);

  const profileSubtitle = useMemo(() => {
    return "Local credential session";
  }, []);

  const profileInitials = useMemo(() => {
    const words = profileName.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      return "TH";
    }
    if (words.length === 1) {
      return words[0].slice(0, 2).toUpperCase();
    }
    return `${words[0][0]}${words[1][0]}`.toUpperCase();
  }, [profileName]);

  const chatUsageModes = useMemo(() => {
    return [...(chatUsage?.modes ?? [])].sort((lhs, rhs) => rhs.totalTokens - lhs.totalTokens);
  }, [chatUsage]);

  const codexPrimaryWindow = useMemo(() => codexUsage?.rateLimit?.primaryWindow ?? null, [codexUsage]);
  const codexSecondaryWindow = useMemo(() => codexUsage?.rateLimit?.secondaryWindow ?? null, [codexUsage]);
  const pendingDeviceCode = useMemo(() => {
    const match = (authLogs || "").match(/\b[A-Z0-9]{4}-[A-Z0-9]{5}\b/);
    return match ? match[0] : null;
  }, [authLogs]);
  const youtubeShortQueryCount = useMemo(() => {
    return youtubeShortSettings?.queries?.length ?? 0;
  }, [youtubeShortSettings]);

  async function loadAuthStatus(): Promise<void> {
    setLoadingAuth(true);
    try {
      const status = await fetchCodexAuthStatus();
      setAuth(status);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Codex auth status");
    } finally {
      setLoadingAuth(false);
    }
  }

  async function loadCodexUsage(): Promise<void> {
    setLoadingCodexUsage(true);
    try {
      const usage = await fetchCodexUsageStatus();
      setCodexUsage(usage);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Codex usage");
    } finally {
      setLoadingCodexUsage(false);
    }
  }

  async function loadGitSshKey(): Promise<void> {
    setLoadingGitSshKey(true);
    setCopyKeyMessage(null);
    try {
      const key = await fetchGitSshPublicKey();
      setGitSshKey(key);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load Git SSH key");
    } finally {
      setLoadingGitSshKey(false);
    }
  }

  async function loadChatLogs(): Promise<void> {
    setChatLogsLoading(true);
    try {
      const [logs, usage] = await Promise.all([
        fetchChatExecutionLogs({ limit: 300, modes: ["general", "operator", "tool_builder"] }),
        fetchChatUsageSummary({ modes: ["general", "operator", "tool_builder"] })
      ]);
      setChatLogs(logs);
      setChatUsage(usage);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load chat logs");
    } finally {
      setChatLogsLoading(false);
    }
  }

  async function loadYouTubeShortSettings(): Promise<void> {
    setYouTubeShortSettingsLoading(true);
    try {
      const settings = await fetchYouTubeShortSettings();
      setYouTubeShortSettings(settings);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Unable to load YouTube Shorts settings");
    } finally {
      setYouTubeShortSettingsLoading(false);
    }
  }

  function buildYouTubeDraftRow(seed: YouTubeShortQuerySeed): YouTubeShortDraftRow {
    return {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      category: seed.category || "",
      query: seed.query || ""
    };
  }

  function openYouTubeShortSettingsModal(): void {
    const fallbackQueries = [{ category: "General", query: "interesting shorts" }];
    const sourceQueries = youtubeShortSettings?.queries?.length
      ? youtubeShortSettings.queries
      : fallbackQueries;
    setYouTubeShortRegionDraft(youtubeShortSettings?.regionCode ?? "");
    setYouTubeShortBoostDraft(
      Math.max(1, Math.min(8, Math.round(youtubeShortSettings?.categoryBoostFactor ?? 3)))
    );
    setYouTubeShortPreferredDraft((youtubeShortSettings?.preferredCategories ?? []).join(", "));
    setYouTubeShortQueryDraftRows(sourceQueries.map((seed) => buildYouTubeDraftRow(seed)));
    setYouTubeShortSettingsModalOpen(true);
  }

  function addYouTubeShortTopicRow(): void {
    setYouTubeShortQueryDraftRows((current) => {
      if (current.length >= MAX_SHORT_QUERY_SEEDS) {
        return current;
      }
      return [
        ...current,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          category: "",
          query: ""
        }
      ];
    });
  }

  function removeYouTubeShortTopicRow(rowId: string): void {
    setYouTubeShortQueryDraftRows((current) => current.filter((row) => row.id !== rowId));
  }

  async function saveYouTubeShortSettings(): Promise<void> {
    if (youtubeShortSettingsSaving) {
      return;
    }
    const normalizedQueries = youtubeShortQueryDraftRows
      .map((row) => ({
        category: (row.category || "").trim() || "General",
        query: (row.query || "").trim()
      }))
      .filter((row) => row.query.length > 0)
      .slice(0, MAX_SHORT_QUERY_SEEDS);

    if (normalizedQueries.length === 0) {
      setError("Add at least one YouTube Shorts topic query before saving.");
      return;
    }

    const normalizedPreferred = youtubeShortPreferredDraft
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    const normalizedBoost = Math.max(1, Math.min(8, Math.round(youtubeShortBoostDraft || 3)));
    const normalizedRegion = youtubeShortRegionDraft.trim().toUpperCase();

    setError(null);
    setYouTubeShortSettingsSaving(true);
    try {
      const saved = await updateYouTubeShortSettings({
        queries: normalizedQueries,
        preferredCategories: normalizedPreferred,
        categoryBoostFactor: normalizedBoost,
        regionCode: normalizedRegion || null
      });
      setYouTubeShortSettings(saved);
      setYouTubeShortSettingsModalOpen(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Unable to update YouTube Shorts settings");
    } finally {
      setYouTubeShortSettingsSaving(false);
    }
  }

  useEffect(() => {
    try {
      setThinkingPanelMode(loadThinkingPanelMode());
      const storedHost = window.localStorage.getItem(GIT_SSH_HOST_STORAGE_KEY);
      const storedUsername = window.localStorage.getItem(GIT_SSH_USERNAME_STORAGE_KEY);
      const storedVerificationRaw = window.localStorage.getItem(GIT_SSH_VERIFICATION_STORAGE_KEY);

      if (storedHost?.trim()) {
        setGitHost(storedHost);
      }
      if (storedUsername?.trim()) {
        setGitUsername(storedUsername);
      }
      if (storedVerificationRaw) {
        const parsed = JSON.parse(storedVerificationRaw) as { result?: GitSshVerification; checkedAt?: string };
        if (parsed.result) {
          setGitVerification(parsed.result);
          setGitVerificationCheckedAt(parsed.checkedAt ?? null);
        }
      }
    } catch {
      // Ignore local storage parse errors.
    }

    void loadAuthStatus();
    void loadCodexUsage();
    void loadGitSshKey();
    void loadChatLogs();
    void loadYouTubeShortSettings();
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(GIT_SSH_HOST_STORAGE_KEY, gitHost);
      window.localStorage.setItem(GIT_SSH_USERNAME_STORAGE_KEY, gitUsername);
    } catch {
      // Ignore local storage errors.
    }
  }, [gitHost, gitUsername]);

  function handleCodexLoginEvent(event: CodexLoginStreamEvent): void {
    if (event.type === "log") {
      setAuthLogs((current) => {
        const next = [current, event.chunk].filter(Boolean).join("\n");
        return next.replace(/\n{3,}/g, "\n\n");
      });
      return;
    }

    setAuthLogs(event.logs || "Codex login flow finished.");
  }

  async function handleCodexLogin(): Promise<void> {
    if (loginInProgress || logoutInProgress) {
      return;
    }

    setError(null);
    setAuthLogs("");
    setLoginInProgress(true);
    try {
      await streamCodexLogin(handleCodexLoginEvent);
      await loadAuthStatus();
      await loadCodexUsage();
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Unable to start Codex login");
    } finally {
      setLoginInProgress(false);
    }
  }

  async function handleCodexLogout(): Promise<void> {
    if (logoutInProgress || loginInProgress) {
      return;
    }

    setError(null);
    setAuthLogs("");
    setLogoutInProgress(true);
    try {
      const status = await logoutCodexAuth();
      setAuth(status);
      if (status.message) {
        setAuthLogs(status.message);
      }
      await loadAuthStatus();
      await loadCodexUsage();
    } catch (logoutError) {
      setError(logoutError instanceof Error ? logoutError.message : "Unable to log out of Codex");
    } finally {
      setLogoutInProgress(false);
    }
  }

  function handleAppSignOut(): void {
    setError(null);
    signOut();
  }

  async function handleCopyGitKey(): Promise<void> {
    if (!gitSshKey?.publicKey) {
      return;
    }
    if (!navigator?.clipboard?.writeText) {
      setCopyKeyMessage("Clipboard access is unavailable in this browser.");
      return;
    }

    try {
      await navigator.clipboard.writeText(gitSshKey.publicKey);
      setCopyKeyMessage("Public key copied.");
    } catch {
      setCopyKeyMessage("Unable to copy key automatically. Please copy manually.");
    }
  }

  async function handleVerifyGitSsh(): Promise<void> {
    if (gitVerifying) {
      return;
    }

    const normalizedHost = gitHost.trim() || "github.com";
    const normalizedUsername = gitUsername.trim() || "git";
    setGitHost(normalizedHost);
    setGitUsername(normalizedUsername);
    setError(null);
    setCopyKeyMessage(null);
    setGitVerifying(true);
    try {
      const result = await verifyGitSshConnection(normalizedHost, normalizedUsername);
      const checkedAt = currentIstTimestamp();
      setGitVerification(result);
      setGitVerificationCheckedAt(checkedAt);
      try {
        window.localStorage.setItem(
          GIT_SSH_VERIFICATION_STORAGE_KEY,
          JSON.stringify({ result, checkedAt })
        );
      } catch {
        // Ignore local storage errors.
      }
    } catch (verifyError) {
      setError(verifyError instanceof Error ? verifyError.message : "Unable to verify Git SSH connection");
    } finally {
      setGitVerifying(false);
    }
  }

  function handleThinkingPanelModeChange(mode: ThinkingPanelMode): void {
    setError(null);
    setThinkingPanelMode(mode);
    saveThinkingPanelMode(mode);
  }

  function openChatFromLog(sessionId: string): void {
    navigate(`/chat?chatId=${sessionId}`);
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-5xl flex-col gap-6 overflow-y-auto py-2 lg:py-4">
      <header className="border-b border-amber/15 pb-4">
        <p className="text-[11px] uppercase tracking-[0.2em] text-muted">Profile</p>
        <h1 className="mt-1 text-3xl font-semibold text-[color:var(--text-main)]">Account & Integrations</h1>
        <p className="mt-2 text-sm text-muted">Manage your connected services in one place.</p>

        <div className="mt-4 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-full border border-amber/30 bg-black/50 text-sm font-semibold text-amber">
            {profileInitials}
          </div>
          <div>
            <p className="text-sm font-medium text-[color:var(--text-main)]">{profileName}</p>
            <p className="text-xs text-muted">{profileSubtitle}</p>
            {auth?.email && <p className="text-xs text-muted">ChatGPT: {auth.email}</p>}
          </div>
        </div>
      </header>

      <section className="border border-amber/20 bg-black/45 p-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-[color:var(--text-main)]">Integrations</p>
          <span className="text-xs text-muted">Row view</span>
        </div>

        <div className="mt-4 space-y-3">
          <article className="border border-amber/14 bg-black/40 px-4 py-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm font-medium text-[color:var(--text-main)]">App Access</p>
                <p className="mt-1 text-xs text-muted">Username/password session for AI ToolHub.</p>
              </div>
              <div className="flex flex-wrap items-center gap-2 md:justify-end">
                <span className={`rounded-full px-3 py-1 text-xs ${isAuthenticated ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
                  {isAuthenticated ? "Connected" : "Not connected"}
                </span>
                <button
                  type="button"
                  onClick={handleAppSignOut}
                  disabled={!isAuthenticated}
                  className="btn-ghost border-coral/35 bg-coral/10 px-4 py-2 text-sm text-coral disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Sign out
                </button>
              </div>
            </div>
          </article>

          <article className="border border-amber/14 bg-black/40 px-4 py-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm font-medium text-[color:var(--text-main)]">ChatGPT</p>
                <p className="mt-1 text-xs text-muted">Codex authentication used by chat and tool workflows.</p>
                {auth?.email && <p className="mt-1 text-xs text-muted">Account email: {auth.email}</p>}
                <p className="mt-2 text-xs text-muted">{auth?.message ?? "No status message yet."}</p>
                {pendingDeviceCode && (
                  <p className="mt-2 text-xs text-amber">
                    Device code: <span className="font-[var(--font-mono)]">{pendingDeviceCode}</span>
                  </p>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2 md:justify-end">
                <span className={`rounded-full px-3 py-1 text-xs ${auth?.loggedIn ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
                  {codexStatusLabel}
                </span>
                <button
                  type="button"
                  onClick={() => void handleCodexLogin()}
                  disabled={loginInProgress || logoutInProgress}
                  className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {loginInProgress ? "Signing in..." : auth?.loggedIn ? "Reconnect" : "Sign in"}
                </button>
                <button
                  type="button"
                  onClick={() => void handleCodexLogout()}
                  disabled={logoutInProgress || loginInProgress || !auth?.loggedIn}
                  className="btn-ghost border-coral/35 bg-coral/10 px-4 py-2 text-sm text-coral disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {logoutInProgress ? "Logging out..." : "Logout"}
                </button>
              </div>
            </div>

            <div className="mt-4 overflow-hidden border-t border-amber/14 pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs uppercase tracking-[0.15em] text-muted">Codex Usage</p>
                <button
                  type="button"
                  onClick={() => void loadCodexUsage()}
                  disabled={loadingCodexUsage}
                  className="btn-ghost border-amber/30 bg-black/30 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {loadingCodexUsage ? "Refreshing..." : "Refresh Usage"}
                </button>
              </div>

              <p className="mt-2 break-words text-xs text-muted">
                {codexUsage?.message ?? "Usage data has not been loaded yet."}
              </p>

              {codexUsage?.available ? (
                <>
                  <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <article className="min-w-0 border border-amber/14 bg-black/35 px-3 py-3">
                      <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Plan</p>
                      <p className="mt-1 break-words text-lg font-semibold text-[#f7e8bf]">
                        {formatPlanType(codexUsage.planType)}
                      </p>
                      <p className="mt-1 break-words text-[11px] text-muted">
                        {codexUsage.fetchedAt ? `Fetched ${formatDate(codexUsage.fetchedAt)}` : "Awaiting refresh"}
                      </p>
                    </article>
                    <article className="min-w-0 border border-amber/14 bg-black/35 px-3 py-3">
                      <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Primary Window</p>
                      <p className="mt-1 text-lg font-semibold text-[#f7e8bf]">
                        {formatPercent(codexPrimaryWindow?.usedPercent)}
                      </p>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-black/55">
                        <div
                          className="h-full bg-amber/70"
                          style={{ width: formatPercent(codexPrimaryWindow?.usedPercent) }}
                        />
                      </div>
                      <p className="mt-1 break-words text-[11px] text-muted">
                        {codexPrimaryWindow?.resetAt
                          ? `Resets ${formatDate(codexPrimaryWindow.resetAt)}`
                          : "Reset time unavailable"}
                      </p>
                    </article>
                    <article className="min-w-0 border border-amber/14 bg-black/35 px-3 py-3">
                      <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Secondary Window</p>
                      <p className="mt-1 text-lg font-semibold text-[#f7e8bf]">
                        {formatPercent(codexSecondaryWindow?.usedPercent)}
                      </p>
                      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-black/55">
                        <div
                          className="h-full bg-skyline/70"
                          style={{ width: formatPercent(codexSecondaryWindow?.usedPercent) }}
                        />
                      </div>
                      <p className="mt-1 break-words text-[11px] text-muted">
                        {codexSecondaryWindow?.resetAt
                          ? `Resets ${formatDate(codexSecondaryWindow.resetAt)}`
                          : "Reset time unavailable"}
                      </p>
                    </article>
                    <article className="min-w-0 border border-amber/14 bg-black/35 px-3 py-3">
                      <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Credits</p>
                      <p className="mt-1 break-words text-lg font-semibold text-[#f7e8bf]">
                        {codexUsage.credits?.unlimited ? "Unlimited" : (codexUsage.credits?.balance ?? "0")}
                      </p>
                      <p className="mt-1 break-words text-[11px] text-muted">
                        {codexUsage.credits?.hasCredits ? "Credits available" : "No credits available"}
                      </p>
                    </article>
                  </div>

                  {codexUsage.additionalRateLimits.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {codexUsage.additionalRateLimits.map((item, index) => (
                        <span
                          key={`${item.limitName ?? "limit"}-${index}`}
                          className="max-w-full break-words rounded-full border border-amber/18 bg-black/30 px-3 py-1 text-[11px] text-muted"
                        >
                          {(item.limitName || item.meteredFeature || "additional").replace(/_/g, " ")}:{" "}
                          {formatPercent(item.rateLimit?.primaryWindow?.usedPercent)}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <p className="mt-2 text-xs text-muted">
                  Sign in with ChatGPT inside Codex to load account usage windows and credits.
                </p>
              )}
            </div>
          </article>

          <article className="border border-amber/14 bg-black/40 px-4 py-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm font-medium text-[color:var(--text-main)]">Git</p>
                <p className="mt-1 text-xs text-muted">SSH integration for cloning and pushing to repositories.</p>
                {!gitConnected && gitVerification && <p className="mt-2 text-xs text-muted">{gitVerification.message}</p>}
              </div>
              <div className="flex flex-wrap items-center gap-2 md:justify-end">
                <span className={`rounded-full px-3 py-1 text-xs ${gitConnected ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
                  {gitStatusLabel}
                </span>
                <button
                  type="button"
                  onClick={() => void handleVerifyGitSsh()}
                  disabled={gitVerifying}
                  className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {gitVerifying ? "Verifying..." : "Verify"}
                </button>
              </div>
            </div>

            {!gitConnected && (
              <div className="mt-4 border-t border-amber/14 pt-4">
                <p className="text-xs text-muted">
                  Add the SSH public key to your Git account, then verify from here.
                </p>

                <div className="mt-3 border border-amber/14 bg-black/35 p-3">
                  <p className="text-[11px] uppercase tracking-[0.15em] text-muted">Public Key</p>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
                    {loadingGitSshKey ? "Loading SSH key..." : (gitSshKey?.publicKey ?? "Unable to load SSH key")}
                  </pre>
                  {gitSshKey?.fingerprint && (
                    <p className="mt-2 text-[11px] text-muted">Fingerprint: {gitSshKey.fingerprint}</p>
                  )}
                  {gitSshKey?.message && <p className="mt-1 text-[11px] text-muted">{gitSshKey.message}</p>}
                </div>

                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void handleCopyGitKey()}
                    disabled={loadingGitSshKey || !gitSshKey?.publicKey}
                    className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Copy Public Key
                  </button>
                  <button
                    type="button"
                    onClick={() => void loadGitSshKey()}
                    disabled={loadingGitSshKey}
                    className="btn-ghost px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {loadingGitSshKey ? "Refreshing..." : "Refresh Key"}
                  </button>
                </div>
                {copyKeyMessage && <p className="mt-2 text-xs text-muted">{copyKeyMessage}</p>}

                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <label className="text-xs text-muted">
                    Git Host
                    <input
                      value={gitHost}
                      onChange={(event) => setGitHost(event.target.value)}
                      placeholder="github.com"
                      className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                    />
                  </label>
                  <label className="text-xs text-muted">
                    SSH Username
                    <input
                      value={gitUsername}
                      onChange={(event) => setGitUsername(event.target.value)}
                      placeholder="git"
                      className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                    />
                  </label>
                </div>
              </div>
            )}

            {!gitConnected && gitVerification && (
              <div className="mt-3 border border-amber/14 bg-black/35 p-3">
                <p className="mt-1 text-[11px] text-muted">
                  Target: {gitVerification.username}@{gitVerification.host} | Exit code: {gitVerification.exitCode}
                </p>
                {gitVerificationCheckedAt && (
                  <p className="mt-1 text-[11px] text-muted">Last checked: {formatDate(gitVerificationCheckedAt)}</p>
                )}
                <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
                  {gitVerification.logs || "No terminal output captured."}
                </pre>
              </div>
            )}
          </article>
        </div>
      </section>

      {(loginInProgress || logoutInProgress || authLogs) && (
        <section className="border border-amber/20 bg-black/45 p-4">
          <p className="mb-2 text-[11px] uppercase tracking-[0.16em] text-muted">Authentication Logs</p>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
            {authLogs || (logoutInProgress ? "Logging out..." : "Starting sign-in flow...")}
          </pre>
        </section>
      )}

      <section className="border border-amber/20 bg-black/45 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-[color:var(--text-main)]">Chat Query Logs</p>
            <p className="mt-1 text-xs text-muted">
              Token usage is tracked across General, Operator, and Tool Builder modes.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadChatLogs()}
            disabled={chatLogsLoading}
            className="btn-ghost border-amber/35 bg-black/35 px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            {chatLogsLoading ? "Refreshing..." : "Refresh Logs"}
          </button>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <article className="border border-amber/14 bg-black/35 px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Total Tokens</p>
            <p className="mt-1 text-lg font-semibold text-[color:var(--text-main)]">{formatCount(chatUsage?.totalTokens)}</p>
            <p className="mt-1 text-[11px] text-muted">{formatCount(chatUsage?.requestCount)} tracked chat runs</p>
          </article>
          <article className="border border-amber/14 bg-black/35 px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Prompt / Completion</p>
            <p className="mt-1 text-sm font-medium text-[color:var(--text-main)]">
              {formatCount(chatUsage?.promptTokens)} / {formatCount(chatUsage?.completionTokens)}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              Parsed: {formatCount(chatUsage?.parsedCount)} | Mixed: {formatCount(chatUsage?.mixedCount)}
            </p>
          </article>
          <article className="border border-amber/14 bg-black/35 px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Est. Cost (USD)</p>
            <p className="mt-1 text-lg font-semibold text-[color:var(--text-main)]">
              {formatUsd(chatUsage?.costEstimate.usd ?? 0)}
            </p>
            <p className="mt-1 text-[11px] text-muted">Token-based estimate only</p>
          </article>
          <article className="border border-amber/14 bg-black/35 px-3 py-3">
            <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Est. Cost (INR)</p>
            <p className="mt-1 text-lg font-semibold text-[color:var(--text-main)]">
              {formatInr(chatUsage?.costEstimate.inr ?? 0)}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              {chatUsage
                ? `$${chatUsage.costEstimate.usdPerMillionTokens}/1M @ ₹${chatUsage.costEstimate.usdToInrRate.toFixed(2)}/USD`
                : "Awaiting usage data"}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              {chatUsage
                ? chatUsage.costEstimate.note
                : "Live INR conversion will appear once usage is available."}
            </p>
          </article>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {chatUsageModes.length === 0 ? (
            <span className="rounded-full border border-amber/15 bg-black/30 px-3 py-1 text-[11px] text-muted">
              No mode usage yet
            </span>
          ) : (
            chatUsageModes.map((modeSummary) => (
              <span
                key={modeSummary.mode}
                className="rounded-full border border-amber/18 bg-black/30 px-3 py-1 text-[11px] text-muted"
              >
                {modeSummary.mode}: {formatCount(modeSummary.totalTokens)} tokens ({formatCount(modeSummary.requestCount)} runs)
              </span>
            ))
          )}
        </div>

        <div className="mt-4 max-h-[24rem] overflow-auto border border-amber/14 bg-black/30">
          {chatLogs.length === 0 ? (
            <div className="px-4 py-5 text-sm text-muted">
              {chatLogsLoading ? "Loading chat logs..." : "No chat logs captured yet."}
            </div>
          ) : (
            <div className="divide-y divide-amber/12">
              {chatLogs.map((log) => (
                <article key={log.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="rounded-full bg-black/45 px-2 py-1 font-[var(--font-mono)] text-amber">
                        {log.mode}
                      </span>
                      <button
                        type="button"
                        onClick={() => openChatFromLog(log.sessionId)}
                        className="font-[var(--font-mono)] text-skyline underline underline-offset-2"
                      >
                        chat:{log.sessionId.slice(0, 8)}
                      </button>
                      <span className="text-muted">{formatDate(log.createdAt)}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-full px-2 py-1 text-[11px] ${log.success ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
                        {log.success ? "success" : `exit ${log.exitCode}`}
                      </span>
                      <button
                        type="button"
                        onClick={() => setActiveChatLog(log)}
                        className="btn-ghost border-skyline/45 bg-skyline/10 px-3 py-1.5 text-xs text-skyline"
                      >
                        View Logs
                      </button>
                    </div>
                  </div>
                  <p className="mt-2 text-xs text-muted">Q: {log.userContent || "(empty query)"}</p>
                  <p className="mt-1 text-xs text-muted">A: {log.assistantContent || "(empty response)"}</p>
                  <p className="mt-1 text-xs text-muted">
                    Tokens: {formatCount(log.totalTokens)} ({log.tokenSource})
                  </p>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>

      {error && <p className="border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{error}</p>}

      <section className="border border-amber/20 bg-black/45 p-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-[color:var(--text-main)]">Thinking Companion</p>
          <span className="rounded-full bg-black/35 px-3 py-1 text-xs text-muted">
            Current: {THINKING_PANEL_OPTIONS.find((option) => option.value === thinkingPanelMode)?.label ?? "None"}
          </span>
        </div>
        <p className="mt-1 text-xs text-muted">
          Choose what appears in the right companion panel while the model is thinking.
        </p>
        <div className="mt-4 grid gap-2 md:grid-cols-3">
          {THINKING_PANEL_OPTIONS.map((option) => {
            const active = option.value === thinkingPanelMode;
            const isYouTubeShortsOption = option.value === "insta";
            return (
              <div
                key={option.value}
                className={`border px-3 py-3 text-left transition ${
                  active
                    ? "border-amber/45 bg-amber/12"
                    : "border-amber/18 bg-black/30 hover:border-amber/35 hover:bg-black/45"
                }`}
              >
                <button
                  type="button"
                  onClick={() => handleThinkingPanelModeChange(option.value)}
                  className="w-full text-left"
                >
                  <p className="text-sm font-medium text-[color:var(--text-main)]">{option.label}</p>
                  <p className="mt-1 text-xs text-muted">{option.detail}</p>
                </button>
                {isYouTubeShortsOption && (
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <p className="text-[11px] text-muted">
                      {youtubeShortSettingsLoading ? "Loading topics..." : `${youtubeShortQueryCount} topics configured`}
                    </p>
                    <button
                      type="button"
                      onClick={openYouTubeShortSettingsModal}
                      disabled={youtubeShortSettingsLoading}
                      className="btn-ghost px-2.5 py-1.5 text-[11px] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      YT Short Topics
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {youtubeShortSettingsModalOpen && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/70 px-3 py-3 backdrop-blur-sm md:items-center md:px-4 md:py-6">
          <button
            type="button"
            aria-label="Close YouTube Shorts settings"
            onClick={() => setYouTubeShortSettingsModalOpen(false)}
            className="absolute inset-0"
          />
          <div className="relative z-[71] my-auto flex w-full max-w-4xl flex-col overflow-hidden border border-amber/20 bg-[#11100d] max-md:min-h-[calc(100dvh-1.5rem)] max-md:max-h-[calc(100dvh-1.5rem)] md:max-h-[calc(100dvh-3rem)]">
            <div className="shrink-0 border-b border-amber/15 px-4 py-4 md:px-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-amber/70">YouTube Shorts Settings</p>
                  <p className="mt-1 text-sm text-[color:var(--text-main)]">
                    Configure topic queries used by the companion feed.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setYouTubeShortSettingsModalOpen(false)}
                  className="flex h-9 w-9 items-center justify-center border border-white/10 bg-black/35 text-muted transition hover:border-amber/35 hover:text-[color:var(--text-main)]"
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M6 6l12 12" />
                    <path d="M18 6L6 18" />
                  </svg>
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-5 md:py-5">
              <div className="grid gap-3 md:grid-cols-3">
                <label className="text-xs text-muted">
                  Region Code (optional)
                  <input
                    value={youtubeShortRegionDraft}
                    onChange={(event) => setYouTubeShortRegionDraft(event.target.value.toUpperCase())}
                    placeholder="IN"
                    className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                  />
                </label>
                <label className="text-xs text-muted">
                  Category Boost (1-8)
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={youtubeShortBoostDraft}
                    onChange={(event) => setYouTubeShortBoostDraft(Number(event.target.value || 3))}
                    className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                  />
                </label>
                <label className="text-xs text-muted md:col-span-1">
                  Preferred Categories (comma-separated)
                  <input
                    value={youtubeShortPreferredDraft}
                    onChange={(event) => setYouTubeShortPreferredDraft(event.target.value)}
                    placeholder="Tech, Food, Travel"
                    className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                  />
                </label>
              </div>

              <div className="mt-4 flex items-center justify-between gap-3">
                <p className="text-[11px] uppercase tracking-[0.14em] text-muted">
                  Topics ({youtubeShortQueryDraftRows.length}/{MAX_SHORT_QUERY_SEEDS})
                </p>
                <button
                  type="button"
                  onClick={addYouTubeShortTopicRow}
                  disabled={youtubeShortQueryDraftRows.length >= MAX_SHORT_QUERY_SEEDS}
                  className="btn-ghost px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Add Topic
                </button>
              </div>
              <div className="mt-2 space-y-2">
                {youtubeShortQueryDraftRows.map((row) => (
                  <div key={row.id} className="grid gap-2 border border-amber/14 bg-black/25 p-3 md:grid-cols-[1fr_2fr_auto]">
                    <input
                      value={row.category}
                      onChange={(event) => {
                        const value = event.target.value;
                        setYouTubeShortQueryDraftRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, category: value } : item))
                        );
                      }}
                      placeholder="Category"
                      className="w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                    />
                    <input
                      value={row.query}
                      onChange={(event) => {
                        const value = event.target.value;
                        setYouTubeShortQueryDraftRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, query: value } : item))
                        );
                      }}
                      placeholder="Query, e.g. latest tech hacks"
                      className="w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
                    />
                    <button
                      type="button"
                      onClick={() => removeYouTubeShortTopicRow(row.id)}
                      className="btn-ghost border-coral/35 bg-coral/10 px-3 py-2 text-xs text-coral"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="shrink-0 border-t border-amber/15 px-4 py-3 md:px-5">
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setYouTubeShortSettingsModalOpen(false)}
                  className="btn-ghost px-4 py-2 text-sm"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void saveYouTubeShortSettings()}
                  disabled={youtubeShortSettingsSaving}
                  className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {youtubeShortSettingsSaving ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {activeChatLog && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/70 px-3 py-3 backdrop-blur-sm md:items-center md:px-4 md:py-6">
          <button
            type="button"
            aria-label="Close chat log panel"
            onClick={() => setActiveChatLog(null)}
            className="absolute inset-0"
          />
          <div className="relative z-[71] my-auto flex w-full max-w-4xl flex-col overflow-hidden border border-amber/20 bg-[#11100d] max-md:min-h-[calc(100dvh-1.5rem)] max-md:max-h-[calc(100dvh-1.5rem)] md:max-h-[calc(100dvh-3rem)]">
            <div className="shrink-0 border-b border-amber/15 px-4 py-4 md:px-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-[11px] uppercase tracking-[0.18em] text-amber/70">Chat Execution Log</p>
                  <p className="mt-1 text-sm text-[color:var(--text-main)]">
                    chat:{activeChatLog.sessionId} • {activeChatLog.mode} • {formatDate(activeChatLog.createdAt)}
                  </p>
                  <p className="mt-1 text-[11px] text-muted">
                    Tokens: {formatCount(activeChatLog.totalTokens)} ({activeChatLog.tokenSource})
                    {activeChatLog.promptTokens !== null && activeChatLog.completionTokens !== null
                      ? ` • prompt ${formatCount(activeChatLog.promptTokens)} / completion ${formatCount(activeChatLog.completionTokens)}`
                      : ""}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveChatLog(null)}
                  className="flex h-9 w-9 items-center justify-center border border-white/10 bg-black/35 text-muted transition hover:border-amber/35 hover:text-[color:var(--text-main)]"
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M6 6l12 12" />
                    <path d="M18 6L6 18" />
                  </svg>
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 md:px-5 md:py-5">
              <p className="text-[11px] uppercase tracking-[0.14em] text-muted">Query</p>
              <pre className="mt-2 whitespace-pre-wrap border border-amber/14 bg-black/25 p-3 font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
                {activeChatLog.userContent || "(empty query)"}
              </pre>
              <p className="mt-4 text-[11px] uppercase tracking-[0.14em] text-muted">Response</p>
              <pre className="mt-2 whitespace-pre-wrap border border-amber/14 bg-black/25 p-3 font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
                {activeChatLog.assistantContent || "(empty response)"}
              </pre>
              <p className="mt-4 text-[11px] uppercase tracking-[0.14em] text-muted">Raw Model Logs</p>
              <pre className="mt-2 max-h-[24rem] overflow-auto whitespace-pre-wrap border border-amber/14 bg-black/25 p-3 font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
                {activeChatLog.rawLogs || "(no raw logs captured for this quick response path)"}
              </pre>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
