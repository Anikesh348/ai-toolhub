/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly NEXT_PUBLIC_API_BASE_URL?: string;
  readonly NEXT_PUBLIC_TOOL_FRONTEND_BASE_URL?: string;
  readonly NEXT_PUBLIC_TOOL_BACKEND_BASE_URL?: string;
  readonly NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY?: string;
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_TOOL_FRONTEND_BASE_URL?: string;
  readonly VITE_TOOL_BACKEND_BASE_URL?: string;
  readonly VITE_CLERK_PUBLISHABLE_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
