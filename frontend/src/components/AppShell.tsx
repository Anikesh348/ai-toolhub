import { Outlet, useLocation } from "react-router-dom";
import type { FormEvent, MouseEvent as ReactMouseEvent } from "react";
import { useCallback, useEffect, useState } from "react";

import { Sidebar } from "@/components/Sidebar";
import { useLocalAuth } from "@/lib/local-auth";

const DEFAULT_SIDEBAR_WIDTH = 268;
const MIN_SIDEBAR_WIDTH = 236;
const MAX_SIDEBAR_WIDTH = 420;
const MOBILE_BREAKPOINT_QUERY = "(max-width: 1023px)";
const DISPLAY_MODE_STANDALONE_QUERY = "(display-mode: standalone)";

function getPhoneViewState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  return window.matchMedia(MOBILE_BREAKPOINT_QUERY).matches;
}

function getStandalonePwaState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  const displayModeStandalone = window.matchMedia(DISPLAY_MODE_STANDALONE_QUERY).matches;
  const iosStandalone = Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone);
  return displayModeStandalone || iosStandalone;
}

function CredentialSetupLayer() {
  const { completeSetup, defaultSetupUsername } = useLocalAuth();
  const [username, setUsername] = useState(defaultSetupUsername);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (submitting) {
      return;
    }

    setError(null);
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    const result = completeSetup(username, password);
    if (!result.ok) {
      setSubmitting(false);
      setError(result.message);
      return;
    }

    setPassword("");
    setConfirmPassword("");
    setSubmitting(false);
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl items-center justify-center px-4 py-8">
      <section className="w-full border border-amber/25 bg-black/50 p-6 backdrop-blur md:p-8">
        <p className="text-[11px] uppercase tracking-[0.2em] text-muted">First-Time Setup</p>
        <h1 className="mt-2 text-3xl font-semibold text-[color:var(--text-main)] md:text-4xl">Create Login Credentials</h1>
        <p className="mt-2 text-sm text-muted">
          Pick a username and password for this browser. These are saved locally and used for future sign-ins.
        </p>

        <form onSubmit={handleSubmit} className="mt-5 space-y-3 border border-amber/20 bg-black/45 p-4">
          <label className="block text-xs text-muted">
            Username
            <input
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
              placeholder="Choose username"
            />
          </label>

          <label className="block text-xs text-muted">
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
              placeholder="Choose password"
            />
          </label>

          <label className="block text-xs text-muted">
            Confirm password
            <input
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
              placeholder="Re-enter password"
            />
          </label>

          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={submitting}
              className="btn-primary px-5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Saving..." : "Save & Continue"}
            </button>
          </div>
        </form>

        {error && <p className="mt-3 border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{error}</p>}
      </section>
    </main>
  );
}

function UsernamePasswordSignInLayer() {
  const { signIn, savedUsername } = useLocalAuth();
  const [username, setUsername] = useState(savedUsername ?? "");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (savedUsername) {
      setUsername(savedUsername);
    }
  }, [savedUsername]);

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (submitting) {
      return;
    }

    setError(null);
    setSubmitting(true);
    const result = signIn(username, password);
    if (!result.ok) {
      setSubmitting(false);
      setError(result.message);
      return;
    }

    setPassword("");
    setSubmitting(false);
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl items-center justify-center px-4 py-8">
      <section className="w-full border border-amber/25 bg-black/50 p-6 backdrop-blur md:p-8">
        <p className="text-[11px] uppercase tracking-[0.2em] text-muted">Authentication Required</p>
        <h1 className="mt-2 text-3xl font-semibold text-[color:var(--text-main)] md:text-4xl">Sign In</h1>
        <p className="mt-2 text-sm text-muted">
          Enter your local username and password to access AI ToolHub.
        </p>

        <form onSubmit={handleSubmit} className="mt-5 space-y-3 border border-amber/20 bg-black/45 p-4">
          <label className="block text-xs text-muted">
            Username
            <input
              type="text"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
              placeholder="Enter username"
            />
          </label>

          <label className="block text-xs text-muted">
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              className="mt-1 w-full border border-amber/20 bg-black/35 px-3 py-2 text-sm text-[color:var(--text-main)] outline-none focus:border-amber/40"
              placeholder="Enter password"
            />
          </label>

          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={submitting}
              className="btn-primary px-5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Signing in..." : "Sign in"}
            </button>
          </div>
        </form>

        {error && <p className="mt-3 border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{error}</p>}
      </section>
    </main>
  );
}

export function AppShell() {
  const { isReady, needsSetup, isAuthenticated } = useLocalAuth();
  const { pathname } = useLocation();
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
  const [isPhoneView, setIsPhoneView] = useState(false);
  const [isStandalonePwa, setIsStandalonePwa] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const clampSidebarWidth = useCallback((width: number): number => {
    return Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, width));
  }, []);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("toolhub.sidebar.width");
      if (!saved) {
        return;
      }
      const numericWidth = Number(saved);
      if (Number.isFinite(numericWidth)) {
        setSidebarWidth(clampSidebarWidth(numericWidth));
      }
    } catch {
      // Ignore storage errors and keep defaults.
    }
  }, [clampSidebarWidth]);

  useEffect(() => {
    try {
      window.localStorage.setItem("toolhub.sidebar.width", String(sidebarWidth));
    } catch {
      // Ignore storage errors.
    }
  }, [sidebarWidth]);

  useEffect(() => {
    if (!("serviceWorker" in navigator)) {
      return;
    }
    void navigator.serviceWorker.register("/sw.js").catch(() => {
      // Ignore service worker registration failures.
    });
  }, []);

  useEffect(() => {
    const updateMode = (): void => {
      setIsPhoneView(getPhoneViewState());
      setIsStandalonePwa(getStandalonePwaState());
    };

    updateMode();
    const displayModeMediaQuery = window.matchMedia(DISPLAY_MODE_STANDALONE_QUERY);
    window.addEventListener("resize", updateMode);
    document.addEventListener("visibilitychange", updateMode);

    if ("addEventListener" in displayModeMediaQuery) {
      displayModeMediaQuery.addEventListener("change", updateMode);
    } else {
      displayModeMediaQuery.addListener(updateMode);
    }

    return () => {
      window.removeEventListener("resize", updateMode);
      document.removeEventListener("visibilitychange", updateMode);

      if ("removeEventListener" in displayModeMediaQuery) {
        displayModeMediaQuery.removeEventListener("change", updateMode);
      } else {
        displayModeMediaQuery.removeListener(updateMode);
      }
    };
  }, []);

  useEffect(() => {
    document.body.classList.toggle("pwa-mobile", isPhoneView);
    document.body.classList.toggle("pwa-standalone", isStandalonePwa);
    return () => {
      document.body.classList.remove("pwa-mobile");
      document.body.classList.remove("pwa-standalone");
    };
  }, [isPhoneView, isStandalonePwa]);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [pathname]);

  const handleSidebarResizeStart = useCallback(
    (event: ReactMouseEvent<HTMLButtonElement>): void => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = sidebarWidth;

      const previousUserSelect = document.body.style.userSelect;
      const previousCursor = document.body.style.cursor;
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";

      const onMouseMove = (moveEvent: MouseEvent): void => {
        const nextWidth = clampSidebarWidth(startWidth + (moveEvent.clientX - startX));
        setSidebarWidth(nextWidth);
      };

      const onMouseUp = (): void => {
        document.body.style.userSelect = previousUserSelect;
        document.body.style.cursor = previousCursor;
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      };

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [sidebarWidth, clampSidebarWidth]
  );

  if (!isReady) {
    return <main className="min-h-screen" aria-hidden="true" />;
  }

  if (needsSetup) {
    return <CredentialSetupLayer />;
  }

  if (!isAuthenticated) {
    return <UsernamePasswordSignInLayer />;
  }

  if (isPhoneView) {
    return (
      <div className="pwa-mobile-shell">
        <div
          className={`pwa-mobile-backdrop ${mobileMenuOpen ? "is-open" : ""}`}
          onClick={() => setMobileMenuOpen(false)}
          aria-hidden="true"
        />

        <div className={`pwa-mobile-drawer ${mobileMenuOpen ? "is-open" : ""}`}>
          <Sidebar sidebarWidth={Math.max(260, Math.min(sidebarWidth, 320))} mobile onNavigate={() => setMobileMenuOpen(false)} />
        </div>

        <header className="pwa-mobile-topbar">
          <button
            type="button"
            onClick={() => setMobileMenuOpen(true)}
            className="flex h-10 w-10 items-center justify-center rounded-full border border-amber/30 bg-black/40 text-[color:var(--text-main)]"
            aria-label="Open navigation menu"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M4 7h16" />
              <path d="M4 12h16" />
              <path d="M4 17h16" />
            </svg>
          </button>
          <div className="flex flex-1 justify-center px-2">
            <div className="pwa-mobile-title-pill">
              <p className="truncate text-sm font-semibold text-[color:var(--text-main)]">AI ToolHub</p>
            </div>
          </div>
          <div aria-hidden="true" className="h-10 w-10" />
        </header>

        <main className="fade-in pwa-mobile-content">
          <div className="h-full px-3 py-3">
            <Outlet />
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen w-full flex-col lg:h-screen lg:flex-row lg:overflow-hidden">
      <Sidebar sidebarWidth={sidebarWidth} onResizeStart={handleSidebarResizeStart} />
      <div className="fade-in min-h-0 flex-1 overflow-auto lg:h-full lg:overflow-hidden">
        <div className="h-full px-3 py-3 lg:px-6 lg:py-5">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
