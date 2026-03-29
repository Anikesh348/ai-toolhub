"use client";

import { useAuth, useSignIn } from "@clerk/nextjs";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { Sidebar } from "@/components/sidebar";
const DEFAULT_SIDEBAR_WIDTH = 268;
const MIN_SIDEBAR_WIDTH = 236;
const MAX_SIDEBAR_WIDTH = 420;

function getErrorMessage(error: unknown): string {
  if (!error) {
    return "Unable to complete Google sign in. Please retry.";
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  if (typeof error === "object" && error !== null && "errors" in error && Array.isArray((error as { errors: unknown[] }).errors)) {
    const first = (error as { errors: Array<{ message?: string }> }).errors[0];
    if (first?.message) {
      return first.message;
    }
  }

  return "Unable to complete Google sign in. Please retry.";
}

function GoogleSignInLayer() {
  const { isLoaded, signIn } = useSignIn();
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleGoogleSignIn(): Promise<void> {
    if (!isLoaded || !signIn || signingIn) {
      return;
    }

    setError(null);
    setSigningIn(true);

    try {
      await signIn.authenticateWithRedirect({
        strategy: "oauth_google",
        redirectUrl: "/sso-callback",
        redirectUrlComplete: "/chat"
      });
    } catch (signInError) {
      setSigningIn(false);
      setError(getErrorMessage(signInError));
    }
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl items-center justify-center px-4 py-8">
      <section className="w-full border border-amber/25 bg-black/50 p-6 backdrop-blur md:p-8">
        <p className="text-[11px] uppercase tracking-[0.2em] text-muted">Authentication Required</p>
        <h1 className="mt-2 text-3xl font-semibold text-[color:var(--text-main)] md:text-4xl">Sign In With Google</h1>
        <p className="mt-2 text-sm text-muted">
          Sign in with your Google account to access AI ToolHub. After login, connect ChatGPT from your Profile page.
        </p>

        <div className="mt-5 border border-amber/20 bg-black/45 p-4">
          <p className="text-[11px] uppercase tracking-[0.16em] text-muted">Step 1</p>
          <p className="mt-1 text-sm text-[color:var(--text-main)]">Continue to Google authentication</p>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void handleGoogleSignIn()}
            disabled={!isLoaded || signingIn}
            className="btn-primary px-5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            {signingIn ? "Redirecting To Google..." : "Continue with Google"}
          </button>
        </div>

        {error && <p className="mt-3 border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{error}</p>}
      </section>
    </main>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth();
  const pathname = usePathname();
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);

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

  const handleSidebarResizeStart = useCallback(
    (event: import("react").MouseEvent<HTMLButtonElement>): void => {
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

  if (!isLoaded) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-3xl items-center justify-center px-4 py-8">
        <section className="w-full border border-amber/25 bg-black/50 p-6 backdrop-blur md:p-8">
          <p className="text-sm text-muted">Loading authentication...</p>
        </section>
      </main>
    );
  }

  if (!isSignedIn) {
    if (pathname.startsWith("/sso-callback")) {
      return <>{children}</>;
    }
    return <GoogleSignInLayer />;
  }

  return (
    <div className="flex min-h-screen w-full flex-col lg:h-screen lg:flex-row lg:overflow-hidden">
      <Sidebar sidebarWidth={sidebarWidth} onResizeStart={handleSidebarResizeStart} />
      <div className="fade-in min-h-0 flex-1 overflow-auto lg:h-full lg:overflow-hidden">
        <div className="h-full px-3 py-3 lg:px-6 lg:py-5">{children}</div>
      </div>
    </div>
  );
}
