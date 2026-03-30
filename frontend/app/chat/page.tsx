"use client";

import { ChangeEvent, FormEvent, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  API_BASE_URL,
  ChatMessage,
  ChatMode,
  ChatSession,
  ChatStreamEvent,
  createChatSession,
  fetchChatModels,
  fetchChatMessages,
  fetchChatSessions,
  formatDate,
  streamChatMessage,
  uploadChatAttachment,
  updateChatSession
} from "@/lib/api";
import ChatMarkdown from "@/components/chat-markdown";

const MODE_LABELS: Record<ChatMode, string> = {
  general: "General",
  tool_builder: "Tool Builder",
  operator: "Operator",
  pi_operator: "Operator"
};

type SelectableMode = Exclude<ChatMode, "pi_operator">;
type StreamActivity = "ready" | "thinking" | "web_search";
type MessageAttachment = {
  id: string;
  fileName: string;
  contentType: string;
  size: number;
  url: string;
};

const MODE_OPTIONS: Array<{ value: SelectableMode; label: string }> = [
  { value: "general", label: "General" },
  { value: "tool_builder", label: "Tool Builder" },
  { value: "operator", label: "Operator" }
];

function parseMessageAttachments(metadata: Record<string, unknown>): MessageAttachment[] {
  const raw = metadata.attachments;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      id: String(item.id ?? ""),
      fileName: String(item.fileName ?? "image"),
      contentType: String(item.contentType ?? ""),
      size: Number(item.size ?? 0),
      url: String(item.url ?? "")
    }))
    .filter((item) => Boolean(item.id) && Boolean(item.url));
}

function toAttachmentUrl(url: string): string {
  if (url.startsWith("http://") || url.startsWith("https://")) {
    return url;
  }
  const base = API_BASE_URL.endsWith("/") ? API_BASE_URL.slice(0, -1) : API_BASE_URL;
  const path = url.startsWith("/") ? url : `/${url}`;
  return `${base}${path}`;
}

function toSelectableMode(mode: ChatMode): SelectableMode {
  if (mode === "tool_builder") {
    return "tool_builder";
  }
  if (mode === "operator" || mode === "pi_operator") {
    return "operator";
  }
  return "general";
}

function isPinnedNewChat(chat: ChatSession): boolean {
  return chat.title.trim().toLowerCase() === "new chat";
}

function sortChatsForSidebar(list: ChatSession[]): ChatSession[] {
  return [...list].sort((left, right) => {
    const leftPinned = isPinnedNewChat(left);
    const rightPinned = isPinnedNewChat(right);

    if (leftPinned !== rightPinned) {
      return leftPinned ? -1 : 1;
    }

    return new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime();
  });
}

function ChatPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [composerMode, setComposerMode] = useState<SelectableMode>("general");
  const [composerModel, setComposerModel] = useState<string | null>(null);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [creatingChat, setCreatingChat] = useState(false);
  const [updatingMode, setUpdatingMode] = useState(false);
  const [updatingModel, setUpdatingModel] = useState(false);
  const [streamActivity, setStreamActivity] = useState<StreamActivity>("ready");
  const [streamingAssistant, setStreamingAssistant] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pendingImage, setPendingImage] = useState<{ file: File; previewUrl: string } | null>(null);

  const initializedRef = useRef(false);
  const handledNewTokenRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const activeChatIdRef = useRef<string | null>(null);
  const streamTokenRef = useRef(0);
  const imageInputRef = useRef<HTMLInputElement | null>(null);

  const activeChat = useMemo(() => chats.find((chat) => chat.id === activeChatId) ?? null, [chats, activeChatId]);

  const requestedChatId = searchParams.get("chatId") ?? searchParams.get("sessionId");
  const newChatToken = searchParams.get("new");

  function resolveModelForRequest(): string | null {
    return composerModel ?? activeChat?.model ?? defaultModel ?? availableModels[0] ?? null;
  }

  async function loadChats(): Promise<ChatSession[]> {
    const data = sortChatsForSidebar(await fetchChatSessions());
    setChats(data);
    setActiveChatId((current) => {
      if (current && data.some((chat) => chat.id === current)) {
        return current;
      }
      return data[0]?.id ?? null;
    });
    return data;
  }

  async function findReusableNewChat(list: ChatSession[]): Promise<ChatSession | null> {
    const candidates = list.filter((chat) => chat.title.trim().toLowerCase() === "new chat").slice(0, 5);

    for (const candidate of candidates) {
      try {
        const candidateMessages = await fetchChatMessages(candidate.id);
        if (candidateMessages.length === 0) {
          return candidate;
        }
      } catch {
        continue;
      }
    }

    return null;
  }

  async function bootstrapChat(initialChatId: string | null, preferFreshChat: boolean): Promise<void> {
    const list = sortChatsForSidebar(await fetchChatSessions());
    let selectedChatId: string | null = null;

    if (initialChatId) {
      setChats(list);
      const target = list.find((chat) => chat.id === initialChatId);
      if (target) {
        setActiveChatId(target.id);
        selectedChatId = target.id;
      }
      if (!requestedChatId && selectedChatId) {
        router.replace(`/chat?chatId=${selectedChatId}`, { scroll: false });
      }
      if (selectedChatId) {
        return;
      }
    }

    if (preferFreshChat) {
      const reusableNewChat = await findReusableNewChat(list);
      if (reusableNewChat) {
        setChats(list);
        setActiveChatId(reusableNewChat.id);
        selectedChatId = reusableNewChat.id;
        router.replace(`/chat?chatId=${reusableNewChat.id}`, { scroll: false });
        return;
      }

      const created = await createChatSession("New Chat", "general", resolveModelForRequest());
      setChats(sortChatsForSidebar([created, ...list]));
      setActiveChatId(created.id);
      selectedChatId = created.id;
      router.replace(`/chat?chatId=${created.id}`, { scroll: false });
      return;
    }

    setChats(list);
    selectedChatId = list[0]?.id ?? null;
    setActiveChatId(selectedChatId);
    if (selectedChatId && !requestedChatId) {
      router.replace(`/chat?chatId=${selectedChatId}`, { scroll: false });
    }
  }

  useEffect(() => {
    if (initializedRef.current) {
      return;
    }

    initializedRef.current = true;
    handledNewTokenRef.current = newChatToken;
    setError(null);

    void bootstrapChat(requestedChatId, !requestedChatId).catch((loadError: unknown) => {
      setError(loadError instanceof Error ? loadError.message : "Unable to initialize chat");
    });
  }, [requestedChatId, newChatToken]);

  useEffect(() => {
    void fetchChatModels()
      .then((response) => {
        setAvailableModels(response.models);
        setDefaultModel(response.defaultModel);
        setComposerModel((current) => {
          if (current && response.models.includes(current)) {
            return current;
          }
          return response.defaultModel ?? response.models[0] ?? null;
        });
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Unable to load models");
      });
  }, []);

  useEffect(() => {
    return () => {
      if (pendingImage) {
        URL.revokeObjectURL(pendingImage.previewUrl);
      }
    };
  }, [pendingImage]);

  useEffect(() => {
    if (!initializedRef.current || requestedChatId || !newChatToken) {
      return;
    }
    if (handledNewTokenRef.current === newChatToken) {
      return;
    }

    handledNewTokenRef.current = newChatToken;
    void handleCreateChat(composerMode);
  }, [newChatToken, requestedChatId, composerMode]);

  useEffect(() => {
    activeChatIdRef.current = activeChatId;
  }, [activeChatId]);

  useEffect(() => {
    if (!activeChat) {
      return;
    }
    setComposerMode(toSelectableMode(activeChat.mode));
    const sessionModel = activeChat.model;
    if (sessionModel && availableModels.includes(sessionModel)) {
      setComposerModel(sessionModel);
      return;
    }
    setComposerModel(defaultModel ?? availableModels[0] ?? null);
  }, [activeChat, defaultModel, availableModels]);

  useEffect(() => {
    clearPendingImage();
  }, [activeChatId]);

  useEffect(() => {
    streamTokenRef.current += 1;
    setStreamActivity("ready");
    setStreamingAssistant("");

    if (!activeChatId) {
      setMessages([]);
      return;
    }

    setLoadingMessages(true);
    setError(null);
    void fetchChatMessages(activeChatId)
      .then((data) => {
        setMessages(data);
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Unable to load messages");
      })
      .finally(() => {
        setLoadingMessages(false);
      });
  }, [activeChatId]);

  useEffect(() => {
    if (!requestedChatId) {
      return;
    }
    if (!chats.some((chat) => chat.id === requestedChatId)) {
      void loadChats().catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Unable to load chats");
      });
      return;
    }
    setActiveChatId(requestedChatId);
  }, [requestedChatId, chats]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamingAssistant, streamActivity]);

  function upsertChat(chat: ChatSession): void {
    setChats((current) => {
      const index = current.findIndex((item) => item.id === chat.id);
      if (index === -1) {
        return sortChatsForSidebar([chat, ...current]);
      }
      const copy = [...current];
      copy[index] = chat;
      return sortChatsForSidebar(copy);
    });
  }

  function handleStreamEvent(event: ChatStreamEvent, streamToken: number, streamChatId: string): void {
    if (streamToken !== streamTokenRef.current) {
      return;
    }
    if (activeChatIdRef.current !== streamChatId) {
      return;
    }

    if (event.type === "error") {
      setError(event.error);
      setStreamActivity("ready");
      setStreamingAssistant("");
      return;
    }
    if (event.type === "user_message") {
      upsertChat(event.session);
      setMessages((current) => [...current, event.message]);
      return;
    }
    if (event.type === "status") {
      if (event.status === "web_search") {
        setStreamActivity("web_search");
      } else if (event.status === "ready") {
        setStreamActivity("ready");
      } else {
        setStreamActivity("thinking");
      }
      return;
    }
    if (event.type === "assistant_delta") {
      setStreamActivity("thinking");
      setStreamingAssistant((current) => `${current}${event.delta}`);
      return;
    }
    if (event.type === "assistant_message") {
      upsertChat(event.session);
      setStreamActivity("ready");
      setStreamingAssistant("");
      setMessages((current) => [...current, event.message]);
      return;
    }
    if (event.type === "done") {
      setStreamActivity("ready");
      setStreamingAssistant("");
    }
  }

  async function handleCreateChat(mode: SelectableMode = composerMode): Promise<void> {
    if (creatingChat) {
      return;
    }

    setCreatingChat(true);
    setError(null);
    try {
      const title = mode === "general" ? "New Chat" : `${MODE_LABELS[mode]} Chat`;
      const selectedModel = resolveModelForRequest();
      const created = await createChatSession(title, mode, selectedModel);
      setChats((current) => sortChatsForSidebar([created, ...current]));
      setActiveChatId(created.id);
      setComposerMode(mode);
      setComposerModel(created.model ?? selectedModel);
      setMessages([]);
      setPrompt("");
      router.push(`/chat?chatId=${created.id}`, { scroll: false });
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Unable to create chat");
    } finally {
      setCreatingChat(false);
    }
  }

  async function handleModeChange(nextMode: SelectableMode): Promise<void> {
    setComposerMode(nextMode);
    if (!activeChat || updatingMode) {
      return;
    }

    if (toSelectableMode(activeChat.mode) === nextMode) {
      return;
    }

    setError(null);
    setUpdatingMode(true);
    try {
      const updated = await updateChatSession(activeChat.id, { mode: nextMode });
      upsertChat(updated);
    } catch (modeError) {
      setError(modeError instanceof Error ? modeError.message : "Unable to switch chat mode");
    } finally {
      setUpdatingMode(false);
    }
  }

  async function handleModelChange(nextModel: string): Promise<void> {
    setComposerModel(nextModel);
    if (!activeChat || updatingModel) {
      return;
    }

    const currentModel = activeChat.model ?? defaultModel ?? availableModels[0] ?? null;
    if (!nextModel || currentModel === nextModel) {
      return;
    }

    setError(null);
    setUpdatingModel(true);
    try {
      const updated = await updateChatSession(activeChat.id, { model: nextModel });
      upsertChat(updated);
    } catch (modelError) {
      setError(modelError instanceof Error ? modelError.message : "Unable to switch model");
    } finally {
      setUpdatingModel(false);
    }
  }

  function clearPendingImage(): void {
    setPendingImage((current) => {
      if (current) {
        URL.revokeObjectURL(current.previewUrl);
      }
      return null;
    });
    if (imageInputRef.current) {
      imageInputRef.current.value = "";
    }
  }

  function handleImageSelection(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    if (!file.type.startsWith("image/")) {
      setError("Please select an image file.");
      if (imageInputRef.current) {
        imageInputRef.current.value = "";
      }
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError("Image is too large. Maximum size is 10MB.");
      if (imageInputRef.current) {
        imageInputRef.current.value = "";
      }
      return;
    }

    setError(null);
    const previewUrl = URL.createObjectURL(file);
    setPendingImage((current) => {
      if (current) {
        URL.revokeObjectURL(current.previewUrl);
      }
      return { file, previewUrl };
    });
  }

  async function submitPrompt(): Promise<void> {
    if (!activeChatId || sending || (!prompt.trim() && !pendingImage)) {
      return;
    }

    const streamChatId = activeChatId;
    setError(null);
    setSending(true);
    const content = prompt.trim() || "Please analyze the attached image.";
    const streamToken = streamTokenRef.current + 1;
    streamTokenRef.current = streamToken;
    setPrompt("");

    try {
      setStreamActivity("thinking");
      setStreamingAssistant("");
      const selectedModel = resolveModelForRequest();
      const attachmentIds: string[] = [];
      if (pendingImage) {
        const uploaded = await uploadChatAttachment(streamChatId, pendingImage.file);
        attachmentIds.push(uploaded.id);
      }
      await streamChatMessage(
        streamChatId,
        content,
        selectedModel,
        attachmentIds,
        (event) => handleStreamEvent(event, streamToken, streamChatId)
      );
      await loadChats();
      if (pendingImage) {
        clearPendingImage();
      }
    } catch (sendError) {
      setPrompt(content);
      streamTokenRef.current += 1;
      setStreamActivity("ready");
      setStreamingAssistant("");
      setError(sendError instanceof Error ? sendError.message : "Unable to send message");
    } finally {
      setSending(false);
    }
  }

  async function handleSend(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await submitPrompt();
  }

  const showCenteredWelcome = !loadingMessages && messages.length === 0 && !streamingAssistant && streamActivity === "ready";

  return (
    <main className="chat-page-root flex h-full min-h-0 flex-col">
      <header className="chat-page-header shrink-0 flex items-center justify-between gap-3 border-b border-amber/15 px-2 pb-3 lg:px-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.18em] text-muted">Chat</p>
          <h1 className="mt-1 text-xl font-medium text-[color:var(--text-main)]">{activeChat?.title ?? "New chat"}</h1>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-black/45 px-3 py-1 text-xs text-muted">
            {streamActivity === "web_search" ? "Searching web" : streamActivity === "thinking" ? "Thinking" : "Ready"}
          </span>
          <button
            type="button"
            onClick={() => void handleCreateChat(composerMode)}
            disabled={creatingChat}
            className="btn-ghost px-3 py-1.5 text-xs"
          >
            {creatingChat ? "Creating..." : "New chat"}
          </button>
        </div>
      </header>

      <section className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-2 pb-4 pt-5 lg:px-4">
          {loadingMessages && <p className="text-sm text-muted">Loading messages...</p>}

          {showCenteredWelcome && (
            <div className="flex h-full min-h-[46vh] flex-col items-center justify-center text-center">
              <h2 className="text-4xl font-medium tracking-tight text-[color:var(--text-main)] md:text-5xl">What can I help with?</h2>
              <p className="mt-3 max-w-lg text-sm text-muted">Start a new chat and pick the mode below to guide how ToolHub should respond.</p>
            </div>
          )}

          <div className="mx-auto w-full max-w-4xl space-y-4">
            {messages.map((message) => {
              const messageAttachments = parseMessageAttachments(message.metadata ?? {});
              return (
                <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[88%] px-3 py-2.5 ${
                      message.role === "user"
                        ? "rounded-3xl bg-amber/15 text-[color:var(--text-main)]"
                        : "text-[color:var(--text-main)]"
                    }`}
                  >
                    <ChatMarkdown content={message.content} isUser={message.role === "user"} />
                    {messageAttachments.length > 0 && (
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        {messageAttachments.map((attachment) => (
                          <a
                            key={attachment.id}
                            href={toAttachmentUrl(attachment.url)}
                            target="_blank"
                            rel="noreferrer"
                            className="block overflow-hidden rounded-xl border border-amber/25 bg-black/25"
                          >
                            <img
                              src={toAttachmentUrl(attachment.url)}
                              alt={attachment.fileName}
                              className="max-h-60 w-full object-cover"
                            />
                          </a>
                        ))}
                      </div>
                    )}
                    <p className="mt-2 text-[11px] text-muted">{formatDate(message.createdAt)}</p>
                  </div>
                </div>
              );
            })}

            {(streamActivity !== "ready" || streamingAssistant) && (
              <div className="flex justify-start">
                <div className="max-w-[88%] px-3 py-2.5 text-[color:var(--text-main)]">
                  {streamingAssistant ? (
                    <ChatMarkdown content={streamingAssistant} />
                  ) : (
                    <p className="whitespace-pre-wrap text-[15px] leading-relaxed">
                      {streamActivity === "web_search" ? "Searching web..." : "Thinking..."}
                    </p>
                  )}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        <form className="chat-page-composer shrink-0 border-t border-amber/15 bg-[#0e0d0b]/88 px-2 pb-3 pt-3 backdrop-blur lg:px-4 lg:pb-4" onSubmit={handleSend}>
          <div className="mx-auto w-full max-w-4xl">
            <div className="chat-page-modes mb-2 flex flex-wrap items-center gap-2">
              {MODE_OPTIONS.map((option) => {
                const active = composerMode === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => void handleModeChange(option.value)}
                    disabled={!activeChat || updatingMode}
                    className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                      active
                        ? "bg-amber/20 text-[color:var(--text-main)]"
                        : "bg-black/40 text-muted hover:bg-black/60 hover:text-[color:var(--text-main)]"
                    } disabled:cursor-not-allowed disabled:opacity-60`}
                  >
                    {option.label}
                  </button>
                );
              })}
              {updatingMode && <span className="text-[11px] text-muted">Switching mode...</span>}
              <div className="ml-auto flex items-center gap-2">
                <label htmlFor="chat-model-select" className="text-[11px] uppercase tracking-[0.14em] text-muted">
                  Model
                </label>
                <select
                  id="chat-model-select"
                  value={composerModel ?? ""}
                  onChange={(event) => void handleModelChange(event.target.value)}
                  disabled={!activeChat || sending || availableModels.length === 0 || updatingModel}
                  className="rounded-full border border-amber/30 bg-black/40 px-3 py-1 text-xs text-[color:var(--text-main)] outline-none disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {availableModels.length === 0 ? (
                    <option value="">Default</option>
                  ) : (
                    availableModels.map((modelOption) => (
                      <option key={modelOption} value={modelOption}>
                        {modelOption}
                      </option>
                    ))
                  )}
                </select>
                {updatingModel && <span className="text-[11px] text-muted">Saving model...</span>}
              </div>
            </div>

            {pendingImage && (
              <div className="mb-2 flex items-center gap-3 rounded-2xl border border-amber/25 bg-black/35 px-3 py-2">
                <img src={pendingImage.previewUrl} alt={pendingImage.file.name} className="h-16 w-16 rounded-lg object-cover" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs text-[color:var(--text-main)]">{pendingImage.file.name}</p>
                  <p className="text-[11px] text-muted">{Math.max(1, Math.round(pendingImage.file.size / 1024))} KB</p>
                </div>
                <button type="button" onClick={clearPendingImage} className="btn-ghost px-2 py-1 text-xs">
                  Remove
                </button>
              </div>
            )}

            <input ref={imageInputRef} type="file" accept="image/*" className="hidden" onChange={handleImageSelection} />

            <div className="flex items-end gap-2 rounded-3xl border border-amber/22 bg-[#1a1813]/90 px-3 py-2">
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitPrompt();
                  }
                }}
                rows={2}
                className="w-full resize-none bg-transparent py-2 text-sm text-[color:var(--text-main)] outline-none placeholder:text-muted/90"
                placeholder="Ask anything"
                disabled={!activeChat || sending}
              />
              <button
                type="button"
                onClick={() => imageInputRef.current?.click()}
                disabled={!activeChat || sending}
                className="btn-ghost rounded-full px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-60"
              >
                Image
              </button>
              <button
                type="submit"
                disabled={!activeChat || sending || (!prompt.trim() && !pendingImage)}
                className="btn-primary rounded-full px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              >
                {sending ? "..." : "Send"}
              </button>
            </div>

            <p className="mt-2 text-xs text-muted">Enter to send, Shift + Enter for a new line.</p>
          </div>
          {error && <div className="mx-auto mt-2 w-full max-w-4xl border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral">{error}</div>}
        </form>
      </section>
    </main>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <main className="flex items-center justify-center lg:h-full lg:min-h-0">
          <p className="text-sm text-muted">Loading chat...</p>
        </main>
      }
    >
      <ChatPageContent />
    </Suspense>
  );
}
