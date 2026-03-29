"use client";

import type { MouseEvent } from "react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ChatSession, deleteChatSession, fetchChatSessions } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/chat", label: "Chat", subtitle: "Conversation" },
  { href: "/requests", label: "Tools generated", subtitle: "Manage runtime" }
];

type SidebarProps = {
  sidebarWidth: number;
  onResizeStart?: (event: MouseEvent<HTMLButtonElement>) => void;
  mobile?: boolean;
  onNavigate?: () => void;
  chatOnly?: boolean;
};

function sortChatsForSidebar(list: ChatSession[]): ChatSession[] {
  return [...list].sort((left, right) => {
    const leftPinned = left.title.trim().toLowerCase() === "new chat";
    const rightPinned = right.title.trim().toLowerCase() === "new chat";

    if (leftPinned !== rightPinned) {
      return leftPinned ? -1 : 1;
    }

    return new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime();
  });
}

export function Sidebar({ sidebarWidth, onResizeStart, mobile = false, onNavigate, chatOnly = false }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeChatId = searchParams.get("chatId") ?? searchParams.get("sessionId");

  const [chats, setChats] = useState<ChatSession[]>([]);
  const [loadingChats, setLoadingChats] = useState(false);
  const [deletingChatId, setDeletingChatId] = useState<string | null>(null);
  const [pendingDeleteChat, setPendingDeleteChat] = useState<ChatSession | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    async function loadChats(showSpinner = false): Promise<void> {
      if (showSpinner) {
        setLoadingChats(true);
      }
      try {
        const data = sortChatsForSidebar(await fetchChatSessions());
        if (!mounted) {
          return;
        }
        setChats(data.slice(0, 35));
      } finally {
        if (mounted) {
          setLoadingChats(false);
        }
      }
    }

    void loadChats(true);
    const intervalId = setInterval(() => {
      void loadChats();
    }, 5000);

    return () => {
      mounted = false;
      clearInterval(intervalId);
    };
  }, []);

  function openNewChat(): void {
    onNavigate?.();
    router.push(`/chat?new=${Date.now()}`);
  }

  function openChat(chatId: string): void {
    onNavigate?.();
    router.push(`/chat?chatId=${chatId}`);
  }

  function handleDeleteClick(event: MouseEvent<HTMLButtonElement>, chat: ChatSession): void {
    event.preventDefault();
    event.stopPropagation();
    setDeleteError(null);
    setPendingDeleteChat(chat);
  }

  async function confirmDeleteChat(): Promise<void> {
    if (deletingChatId) {
      return;
    }
    if (!pendingDeleteChat) {
      return;
    }

    setDeletingChatId(pendingDeleteChat.id);

    try {
      await deleteChatSession(pendingDeleteChat.id);
      const remaining = chats.filter((item) => item.id !== pendingDeleteChat.id);
      setChats(remaining);

      if (pathname === "/chat" && activeChatId === pendingDeleteChat.id) {
        if (remaining.length > 0) {
          router.push(`/chat?chatId=${remaining[0].id}`);
        } else {
          router.push(`/chat?new=${Date.now()}`);
        }
      }
      setPendingDeleteChat(null);
    } catch (deleteChatError) {
      setDeleteError(deleteChatError instanceof Error ? deleteChatError.message : "Unable to delete chat");
    } finally {
      setDeletingChatId(null);
    }
  }

  return (
    <aside
      className={`relative flex h-full w-full flex-col border-r border-amber/20 bg-black/45 backdrop-blur ${
        mobile
          ? "min-h-0 px-3 py-4"
          : "px-3 py-4 lg:min-h-0 lg:w-[var(--sidebar-width)] lg:min-w-[var(--sidebar-width)] lg:px-4"
      }`}
      style={{ ["--sidebar-width" as string]: `${sidebarWidth}px` }}
    >
      <div className="px-2 pb-4">
        <p className="text-[10px] uppercase tracking-[0.24em] text-muted">AI ToolHub</p>
        <h1 className="mt-1 text-xl font-semibold text-[color:var(--text-main)]">{chatOnly ? "Chats" : "Workspace"}</h1>
      </div>

      <button
        type="button"
        onClick={openNewChat}
        className="mb-4 flex w-full items-center justify-center gap-2 rounded-xl border border-amber/35 bg-amber/15 px-3 py-2 text-sm font-semibold text-[color:var(--text-main)] transition hover:border-amber/55 hover:bg-amber/20"
      >
        <span aria-hidden="true">+</span>
        New chat
      </button>

      {!chatOnly && (
        <nav className="space-y-1.5">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => onNavigate?.()}
                className={`block rounded-xl px-3 py-2.5 transition ${
                  active
                    ? "bg-amber/14 text-[color:var(--text-main)]"
                    : "text-muted hover:bg-black/55 hover:text-[color:var(--text-main)]"
                }`}
              >
                <p className="text-sm font-medium">{item.label}</p>
                <p className="mt-0.5 text-xs text-muted">{item.subtitle}</p>
              </Link>
            );
          })}
        </nav>
      )}

      <section className={`${chatOnly ? "mt-1" : "mt-4"} flex min-h-0 flex-1 flex-col`}>
        <div className="flex items-center justify-between px-1">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted">Recent chats</p>
          <span className="text-[11px] text-muted">{chats.length}</span>
        </div>

        <div className="mt-2 min-h-0 space-y-1 overflow-y-auto pr-1">
          {loadingChats && chats.length === 0 && <p className="px-2 py-1 text-xs text-muted">Loading chats...</p>}
          {!loadingChats && chats.length === 0 && <p className="px-2 py-1 text-xs text-muted">No chats yet.</p>}

          {chats.map((chat) => {
            const isActive = pathname === "/chat" && activeChatId === chat.id;
            return (
              <div key={chat.id} className="group relative">
                <button
                  type="button"
                  onClick={() => openChat(chat.id)}
                  className={`w-full rounded-lg px-2 py-2 pr-10 text-left transition ${
                    isActive ? "bg-amber/14 text-[color:var(--text-main)]" : "text-muted hover:bg-black/55 hover:text-[color:var(--text-main)]"
                  }`}
                >
                  <p className="truncate text-sm">{chat.title}</p>
                  <p className="mt-0.5 text-[11px] text-muted">{new Date(chat.updatedAt).toLocaleDateString()}</p>
                </button>

                <button
                  type="button"
                  onClick={(event) => handleDeleteClick(event, chat)}
                  disabled={deletingChatId === chat.id}
                  className={`absolute right-1 top-1/2 -translate-y-1/2 rounded-md border border-coral/35 bg-coral/10 p-1 text-coral transition ${
                    deletingChatId === chat.id ? "opacity-100" : mobile ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                  } disabled:cursor-not-allowed disabled:opacity-70`}
                  title={`Delete ${chat.title}`}
                  aria-label={`Delete ${chat.title}`}
                >
                  {deletingChatId === chat.id ? (
                    <span className="block h-3.5 w-3.5 text-[10px] leading-[14px]">...</span>
                  ) : (
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                      <path d="M3 6h18" />
                      <path d="M8 6V4h8v2" />
                      <path d="M19 6l-1 14H6L5 6" />
                      <path d="M10 11v6" />
                      <path d="M14 11v6" />
                    </svg>
                  )}
                </button>
              </div>
            );
          })}
        </div>

        {deleteError && <p className="mt-2 px-2 text-xs text-coral">{deleteError}</p>}
      </section>

      {!chatOnly && (
        <div className="mt-3 border-t border-amber/20 pt-3">
          <Link
            href="/account"
            onClick={() => onNavigate?.()}
            className={`flex items-center gap-3 rounded-xl px-2.5 py-2 transition ${
              pathname === "/account"
                ? "bg-amber/14 text-[color:var(--text-main)]"
                : "text-muted hover:bg-black/55 hover:text-[color:var(--text-main)]"
            }`}
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-full border border-amber/35 bg-black/50 text-xs font-semibold text-amber">
              AT
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[color:var(--text-main)]">Your profile</p>
              <p className="truncate text-xs text-muted">Open settings</p>
            </div>
          </Link>
        </div>
      )}

      <button
        type="button"
        onMouseDown={onResizeStart}
        className={`absolute -right-1 top-0 h-full w-2 cursor-col-resize bg-transparent ${mobile ? "hidden" : "hidden lg:block"}`}
        aria-label="Resize sidebar"
      />

      {pendingDeleteChat && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm border border-amber/30 bg-[#11100d] p-4 shadow-2xl backdrop-blur">
            <p className="text-sm font-semibold text-[color:var(--text-main)]">Delete chat?</p>
            <p className="mt-2 text-sm text-muted">
              This will permanently delete <span className="text-[color:var(--text-main)]">"{pendingDeleteChat.title}"</span>.
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingDeleteChat(null)}
                disabled={deletingChatId === pendingDeleteChat.id}
                className="btn-ghost px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDeleteChat()}
                disabled={deletingChatId === pendingDeleteChat.id}
                className="rounded-md border border-coral/40 bg-coral/15 px-3 py-1.5 text-xs font-semibold text-coral transition hover:bg-coral/20 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {deletingChatId === pendingDeleteChat.id ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
