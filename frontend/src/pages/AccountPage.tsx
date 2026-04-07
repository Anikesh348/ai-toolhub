import { useEffect, useMemo, useState } from "react";
import { useAuth, useClerk, useUser } from "@clerk/clerk-react";

import {
  currentIstTimestamp,
  CodexAuthStatus,
  CodexLoginStreamEvent,
  fetchCodexAuthStatus,
  fetchGitSshPublicKey,
  formatDate,
  GitSshPublicKeyStatus,
  GitSshVerification,
  logoutCodexAuth,
  streamCodexLogin,
  verifyGitSshConnection
} from "@/lib/api";
import {
  loadThinkingPanelMode,
  saveThinkingPanelMode,
  ThinkingPanelMode
} from "@/lib/chat-thinking-panel";

const GIT_SSH_HOST_STORAGE_KEY = "toolhub.git.ssh.host";
const GIT_SSH_USERNAME_STORAGE_KEY = "toolhub.git.ssh.username";
const GIT_SSH_VERIFICATION_STORAGE_KEY = "toolhub.git.ssh.verification";
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

export default function AccountPage() {
  const { isLoaded: googleLoaded, isSignedIn } = useAuth();
  const { user } = useUser();
  const { signOut } = useClerk();

  const [auth, setAuth] = useState<CodexAuthStatus | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [loginInProgress, setLoginInProgress] = useState(false);
  const [logoutInProgress, setLogoutInProgress] = useState(false);
  const [googleSignOutInProgress, setGoogleSignOutInProgress] = useState(false);
  const [authLogs, setAuthLogs] = useState("");
  const [gitSshKey, setGitSshKey] = useState<GitSshPublicKeyStatus | null>(null);
  const [loadingGitSshKey, setLoadingGitSshKey] = useState(true);
  const [copyKeyMessage, setCopyKeyMessage] = useState<string | null>(null);
  const [gitHost, setGitHost] = useState("github.com");
  const [gitUsername, setGitUsername] = useState("git");
  const [gitVerifying, setGitVerifying] = useState(false);
  const [gitVerification, setGitVerification] = useState<GitSshVerification | null>(null);
  const [gitVerificationCheckedAt, setGitVerificationCheckedAt] = useState<string | null>(null);
  const [thinkingPanelMode, setThinkingPanelMode] = useState<ThinkingPanelMode>("none");
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

  const googleStatusLabel = useMemo(() => {
    if (!googleLoaded) {
      return "Checking";
    }
    if (isSignedIn) {
      return "Connected";
    }
    return "Not connected";
  }, [googleLoaded, isSignedIn]);

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
    return user?.fullName?.trim() || user?.firstName?.trim() || user?.primaryEmailAddress?.emailAddress || "ToolHub User";
  }, [user]);

  const profileSubtitle = useMemo(() => {
    return user?.primaryEmailAddress?.emailAddress || "Workspace user";
  }, [user]);

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
    void loadGitSshKey();
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
    } catch (logoutError) {
      setError(logoutError instanceof Error ? logoutError.message : "Unable to log out of Codex");
    } finally {
      setLogoutInProgress(false);
    }
  }

  async function handleGoogleSignOut(): Promise<void> {
    if (googleSignOutInProgress) {
      return;
    }

    setError(null);
    setGoogleSignOutInProgress(true);
    try {
      await signOut({ redirectUrl: "/" });
    } catch (signOutError) {
      setError(signOutError instanceof Error ? signOutError.message : "Unable to sign out of Google");
      setGoogleSignOutInProgress(false);
    }
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
                <p className="text-sm font-medium text-[color:var(--text-main)]">Google</p>
                <p className="mt-1 text-xs text-muted">Primary account sign-in for AI ToolHub.</p>
              </div>
              <div className="flex flex-wrap items-center gap-2 md:justify-end">
                <span className={`rounded-full px-3 py-1 text-xs ${isSignedIn ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
                  {googleStatusLabel}
                </span>
                <button
                  type="button"
                  onClick={() => void handleGoogleSignOut()}
                  disabled={googleSignOutInProgress || !isSignedIn}
                  className="btn-ghost border-coral/35 bg-coral/10 px-4 py-2 text-sm text-coral disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {googleSignOutInProgress ? "Signing out..." : "Sign out"}
                </button>
              </div>
            </div>
          </article>

          <article className="border border-amber/14 bg-black/40 px-4 py-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm font-medium text-[color:var(--text-main)]">ChatGPT</p>
                <p className="mt-1 text-xs text-muted">Codex authentication used by chat and tool workflows.</p>
                <p className="mt-2 text-xs text-muted">{auth?.message ?? "No status message yet."}</p>
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
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => handleThinkingPanelModeChange(option.value)}
                className={`border px-3 py-3 text-left transition ${
                  active
                    ? "border-amber/45 bg-amber/12"
                    : "border-amber/18 bg-black/30 hover:border-amber/35 hover:bg-black/45"
                }`}
              >
                <p className="text-sm font-medium text-[color:var(--text-main)]">{option.label}</p>
                <p className="mt-1 text-xs text-muted">{option.detail}</p>
              </button>
            );
          })}
        </div>
      </section>
    </main>
  );
}
