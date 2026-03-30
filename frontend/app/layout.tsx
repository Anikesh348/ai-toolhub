import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI ToolHub",
  description: "Self-hosted AI workspace for chat, tool building, and operator workflows.",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "AI ToolHub",
    statusBarStyle: "black-translucent"
  },
  icons: {
    icon: [
      { url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" }
    ],
    shortcut: [{ url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" }],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }]
  }
};

export const viewport: Viewport = {
  themeColor: "#0a0907",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover"
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
