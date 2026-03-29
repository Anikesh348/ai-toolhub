"use client";

import { useEffect, useMemo, useState } from "react";
import { useAuth, useClerk, useUser } from "@clerk/nextjs";

import {
  CodexAuthStatus,
  CodexLoginStreamEvent,
  fetchCodexAuthStatus,
  fetchGitSshPublicKey,
  GitSshPublicKeyStatus,
  GitSshVerification,
  logoutCodexAuth,
  streamCodexLogin,
  verifyGitSshConnection
} from "@/lib/api";

const CONNECTIONS = [
  { id: "telegram", name: "Telegram Bot", detail: "Connect a Telegram bot for notifications and commands." }
] as const;

const GIT_SSH_HOST_STORAGE_KEY = "toolhub.git.ssh.host";
const GIT_SSH_USERNAME_STORAGE_KEY = "toolhub.git.ssh.username";
const GIT_SSH_VERIFICATION_STORAGE_KEY = "toolhub.git.ssh.verification";

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
      const checkedAt = new Date().toISOString();
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

  return (
    <main className="mx-auto flex h-full w-full max-w-5xl flex-col gap-6 overflow-y-auto py-2 lg:py-4">
      <header className="border-b border-amber/15 pb-4">
        <p className="text-[11px] uppercase tracking-[0.2em] text-muted">Profile</p>
        <h1 className="mt-1 text-3xl font-semibold text-[color:var(--text-main)]">Account & Integrations</h1>
        <p className="mt-2 text-sm text-muted">Manage Google workspace sign-in, ChatGPT authorization, and integrations.</p>
      </header>

      <section className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <article className="border border-amber/20 bg-black/45 p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-[color:var(--text-main)]">ChatGPT Access</p>
              <p className="mt-1 text-xs text-muted">Codex authentication used by chat and tool workflows.</p>
            </div>
            <span className={`rounded-full px-3 py-1 text-xs ${auth?.loggedIn ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
              {codexStatusLabel}
            </span>
          </div>

          <p className="mt-3 text-xs text-muted">{auth?.message ?? "No status message yet."}</p>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void handleCodexLogin()}
              disabled={loginInProgress || logoutInProgress}
              className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loginInProgress ? "Signing in..." : auth?.loggedIn ? "Reconnect ChatGPT" : "Sign in with ChatGPT"}
            </button>

            <button
              type="button"
              onClick={() => void handleCodexLogout()}
              disabled={logoutInProgress || loginInProgress || !auth?.loggedIn}
              className="btn-ghost border-coral/35 bg-coral/10 px-4 py-2 text-sm text-coral disabled:cursor-not-allowed disabled:opacity-60"
            >
              {logoutInProgress ? "Logging out..." : "Logout from ChatGPT"}
            </button>
          </div>
        </article>

        <article className="border border-amber/20 bg-black/45 p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-[color:var(--text-main)]">Google Workspace Access</p>
              <p className="mt-1 text-xs text-muted">Primary sign-in layer for AI ToolHub access.</p>
            </div>
            <span className={`rounded-full px-3 py-1 text-xs ${isSignedIn ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"}`}>
              {googleStatusLabel}
            </span>
          </div>

          <div className="mt-4 flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full border border-amber/30 bg-black/50 text-sm font-semibold text-amber">
              {profileInitials}
            </div>
            <div>
              <p className="text-sm font-medium text-[color:var(--text-main)]">{profileName}</p>
              <p className="text-xs text-muted">{profileSubtitle}</p>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void handleGoogleSignOut()}
              disabled={googleSignOutInProgress}
              className="btn-ghost border-coral/35 bg-coral/10 px-4 py-2 text-sm text-coral disabled:cursor-not-allowed disabled:opacity-60"
            >
              {googleSignOutInProgress ? "Signing out..." : "Sign out of Google"}
            </button>
          </div>
        </article>
      </section>

      <section className="border border-amber/20 bg-black/45 p-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-[color:var(--text-main)]">Git SSH Integration</p>
          <span className={`rounded-full px-3 py-1 text-xs ${
            gitVerificationMatchesTarget && gitVerification?.connected ? "bg-mint/20 text-mint" : "bg-coral/15 text-coral"
          }`}
          >
            {gitVerificationMatchesTarget && gitVerification
              ? (gitVerification.connected ? "Connected" : "Not connected")
              : "Not verified"}
          </span>
        </div>

        <p className="mt-2 text-xs text-muted">
          Copy this SSH public key, add it to your Git account, then run verification from here.
        </p>

        <div className="mt-3 border border-amber/14 bg-black/40 p-3">
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

        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void handleVerifyGitSsh()}
            disabled={gitVerifying}
            className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            {gitVerifying ? "Verifying..." : "Verify SSH Connection"}
          </button>
        </div>

        {gitVerification && (
          <div className="mt-3 border border-amber/14 bg-black/40 p-3">
            <p className="text-xs text-[color:var(--text-main)]">{gitVerification.message}</p>
            <p className="mt-1 text-[11px] text-muted">
              Target: {gitVerification.username}@{gitVerification.host} | Exit code: {gitVerification.exitCode}
            </p>
            {gitVerificationCheckedAt && (
              <p className="mt-1 text-[11px] text-muted">Last checked: {new Date(gitVerificationCheckedAt).toLocaleString()}</p>
            )}
            <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap font-[var(--font-mono)] text-[12px] leading-5 text-[color:var(--text-main)]">
              {gitVerification.logs || "No terminal output captured."}
            </pre>
          </div>
        )}
      </section>

      <section className="border border-amber/20 bg-black/45 p-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium text-[color:var(--text-main)]">Other Services</p>
          <span className="text-xs text-muted">Roadmap ready</span>
        </div>

        <div className="mt-4 space-y-2">
          {CONNECTIONS.map((connection) => (
            <div key={connection.id} className="flex flex-wrap items-center justify-between gap-3 border border-amber/14 bg-black/40 px-3 py-3">
              <div>
                <p className="text-sm text-[color:var(--text-main)]">{connection.name}</p>
                <p className="mt-0.5 text-xs text-muted">{connection.detail}</p>
              </div>
              <button
                type="button"
                disabled
                className="rounded-full border border-amber/20 bg-black/35 px-3 py-1.5 text-xs text-muted"
              >
                Coming soon
              </button>
            </div>
          ))}
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
    </main>
  );
}
