import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI ToolHub",
  description: "Self-hosted AI workspace for chat, tool building, and operator workflows."
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="font-[var(--font-sans)] antialiased">
        <ClerkProvider>
          <AppShell>{children}</AppShell>
        </ClerkProvider>
      </body>
    </html>
  );
}
