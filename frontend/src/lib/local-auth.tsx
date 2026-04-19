import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type AuthSession = {
  username: string;
  signedInAt: string;
};

type AuthCredentials = {
  username: string;
  password: string;
  updatedAt: string;
};

type SignInResult =
  | { ok: true }
  | { ok: false; message: string };

type LocalAuthContextValue = {
  isReady: boolean;
  needsSetup: boolean;
  isAuthenticated: boolean;
  username: string | null;
  savedUsername: string | null;
  defaultSetupUsername: string;
  completeSetup: (username: string, password: string) => SignInResult;
  signIn: (username: string, password: string) => SignInResult;
  signOut: () => void;
};

const AUTH_STORAGE_KEY = "toolhub.auth.session.v1";
const AUTH_CREDENTIALS_STORAGE_KEY = "toolhub.auth.credentials.v1";

const defaultSetupUsername = (
  import.meta.env.NEXT_PUBLIC_AUTH_USERNAME
  ?? import.meta.env.VITE_AUTH_USERNAME
  ?? "admin"
).trim() || "admin";

const LocalAuthContext = createContext<LocalAuthContextValue | null>(null);

function readStoredSession(): AuthSession | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as Partial<AuthSession>;
    const username = typeof parsed.username === "string" ? parsed.username.trim() : "";
    const signedInAt = typeof parsed.signedInAt === "string" ? parsed.signedInAt.trim() : "";

    if (!username || !signedInAt) {
      return null;
    }

    return { username, signedInAt };
  } catch {
    return null;
  }
}

function readStoredCredentials(): AuthCredentials | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(AUTH_CREDENTIALS_STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed = JSON.parse(raw) as Partial<AuthCredentials>;
    const username = typeof parsed.username === "string" ? parsed.username.trim() : "";
    const password = typeof parsed.password === "string" ? parsed.password : "";
    const updatedAt = typeof parsed.updatedAt === "string" ? parsed.updatedAt.trim() : "";

    if (!username || !password || !updatedAt) {
      return null;
    }

    return { username, password, updatedAt };
  } catch {
    return null;
  }
}

function writeStoredSession(session: AuthSession | null): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    if (!session) {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      return;
    }

    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Ignore local storage write errors.
  }
}

function writeStoredCredentials(credentials: AuthCredentials | null): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    if (!credentials) {
      window.localStorage.removeItem(AUTH_CREDENTIALS_STORAGE_KEY);
      return;
    }

    window.localStorage.setItem(AUTH_CREDENTIALS_STORAGE_KEY, JSON.stringify(credentials));
  } catch {
    // Ignore local storage write errors.
  }
}

export function LocalAuthProvider({ children }: { children: ReactNode }) {
  const [credentials, setCredentials] = useState<AuthCredentials | null>(null);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    const storedCredentials = readStoredCredentials();
    const storedSession = readStoredSession();

    setCredentials(storedCredentials);
    if (!storedCredentials) {
      setSession(null);
      writeStoredSession(null);
      setIsReady(true);
      return;
    }

    if (storedSession?.username === storedCredentials.username) {
      setSession(storedSession);
    } else {
      setSession(null);
      writeStoredSession(null);
    }

    setIsReady(true);
  }, []);

  const completeSetup = useCallback((username: string, password: string): SignInResult => {
    const normalizedUsername = username.trim();
    if (normalizedUsername.length < 3) {
      return { ok: false, message: "Username must be at least 3 characters." };
    }
    if (password.length < 4) {
      return { ok: false, message: "Password must be at least 4 characters." };
    }

    const nextCredentials: AuthCredentials = {
      username: normalizedUsername,
      password,
      updatedAt: new Date().toISOString()
    };
    const nextSession: AuthSession = {
      username: normalizedUsername,
      signedInAt: new Date().toISOString()
    };
    setCredentials(nextCredentials);
    writeStoredCredentials(nextCredentials);
    setSession(nextSession);
    writeStoredSession(nextSession);
    return { ok: true };
  }, []);

  const signIn = useCallback((username: string, password: string): SignInResult => {
    if (!credentials) {
      return { ok: false, message: "Create credentials first." };
    }

    const normalizedUsername = username.trim();
    if (!normalizedUsername || !password) {
      return { ok: false, message: "Enter both username and password." };
    }

    if (normalizedUsername !== credentials.username || password !== credentials.password) {
      return { ok: false, message: "Invalid username or password." };
    }

    const nextSession: AuthSession = {
      username: normalizedUsername,
      signedInAt: new Date().toISOString()
    };
    setSession(nextSession);
    writeStoredSession(nextSession);
    return { ok: true };
  }, [credentials]);

  const signOut = useCallback(() => {
    setSession(null);
    writeStoredSession(null);
  }, []);

  const contextValue = useMemo<LocalAuthContextValue>(() => {
    return {
      isReady,
      needsSetup: !credentials,
      isAuthenticated: Boolean(session),
      username: session?.username ?? null,
      savedUsername: credentials?.username ?? null,
      defaultSetupUsername,
      completeSetup,
      signIn,
      signOut
    };
  }, [isReady, credentials, session, completeSetup, signIn, signOut]);

  return <LocalAuthContext.Provider value={contextValue}>{children}</LocalAuthContext.Provider>;
}

export function useLocalAuth(): LocalAuthContextValue {
  const context = useContext(LocalAuthContext);
  if (!context) {
    throw new Error("useLocalAuth must be used within LocalAuthProvider");
  }
  return context;
}
