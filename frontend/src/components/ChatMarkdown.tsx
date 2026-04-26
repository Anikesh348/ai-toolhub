import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { API_BASE_URL } from "@/lib/api";

type ChatMarkdownProps = {
  content: string;
  isUser?: boolean;
};

function resolveMarkdownMediaUrl(value: string): string {
  const cleaned = (value || "").trim();
  if (!cleaned) {
    return "";
  }
  if (/^(https?:|data:|blob:)/i.test(cleaned)) {
    return cleaned;
  }

  const base = API_BASE_URL.endsWith("/") ? API_BASE_URL.slice(0, -1) : API_BASE_URL;
  const path = cleaned.startsWith("/") ? cleaned : `/${cleaned}`;
  return `${base}${path}`;
}

function resolveMarkdownHref(value: string): string {
  return resolveMarkdownMediaUrl(value);
}

function markdownChildrenToText(children: unknown): string {
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) {
    return children.map((child) => markdownChildrenToText(child)).join("");
  }
  if (children && typeof children === "object" && "props" in children) {
    const childProps = (children as { props?: { children?: unknown } }).props;
    return markdownChildrenToText(childProps?.children);
  }
  return "";
}

function isLikelyVideoLink(rawHref: string, label: string): boolean {
  const value = `${rawHref} ${label}`.toLowerCase();
  return /\.(webm|mp4|mov|m4v)(?:$|[?#\s])/i.test(value) || /browser-recording-[^\s)]*/i.test(value);
}

export default function ChatMarkdown({ content, isUser = false }: ChatMarkdownProps) {
  if (isUser) {
    return (
      <p className="min-w-0 whitespace-pre-wrap break-words text-[15px] leading-relaxed text-[color:var(--text-main)] [overflow-wrap:anywhere]">
        {content}
      </p>
    );
  }

  return (
    <div className="min-w-0 max-w-full space-y-3 break-words text-[15px] leading-relaxed text-[color:var(--text-main)] [overflow-wrap:anywhere]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="min-w-0 whitespace-pre-wrap break-words leading-relaxed [overflow-wrap:anywhere]">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-skyline">{children}</strong>,
          em: ({ children }) => <em className="italic text-amber">{children}</em>,
          ul: ({ children }) => <ul className="min-w-0 list-disc space-y-1 pl-5 [overflow-wrap:anywhere]">{children}</ul>,
          ol: ({ children }) => <ol className="min-w-0 list-decimal space-y-1 pl-5 [overflow-wrap:anywhere]">{children}</ol>,
          li: ({ children }) => <li className="min-w-0 break-words pl-1 [overflow-wrap:anywhere]">{children}</li>,
          a: ({ href, children }) => {
            const rawHref = href ?? "";
            const resolvedHref = resolveMarkdownHref(rawHref) || "#";
            const label = markdownChildrenToText(children).trim();
            if (isLikelyVideoLink(rawHref, label)) {
              return (
                <span className="mt-2 block w-full max-w-2xl overflow-hidden rounded-xl border border-amber/25 bg-black/35">
                  <video
                    src={resolvedHref}
                    controls
                    preload="metadata"
                    className="block aspect-video w-full bg-black object-contain"
                  >
                    <a href={resolvedHref} target="_blank" rel="noreferrer">
                      {label || "Open recording"}
                    </a>
                  </video>
                  <a
                    href={resolvedHref}
                    target="_blank"
                    rel="noreferrer"
                    className="block break-words px-3 py-2 text-xs text-skyline underline decoration-skyline/45 underline-offset-4 hover:text-amber [overflow-wrap:anywhere]"
                  >
                    {label || "Open recording"}
                  </a>
                </span>
              );
            }
            return (
              <a
                href={resolvedHref}
                target="_blank"
                rel="noreferrer"
                className="break-words text-skyline underline decoration-skyline/45 underline-offset-4 hover:text-amber [overflow-wrap:anywhere]"
              >
                {children}
              </a>
            );
          },
          pre: ({ children }) => (
            <pre className="min-w-0 max-w-full whitespace-pre-wrap break-words rounded-xl border border-amber/25 bg-black/55 p-3 font-[var(--font-mono)] text-[13px] leading-relaxed text-[color:var(--text-main)] [overflow-wrap:anywhere]">
              {children}
            </pre>
          ),
          code: ({ className, children }) => {
            const isBlock = Boolean(className);
            return isBlock ? (
              <code className="block min-w-0 max-w-full whitespace-pre-wrap break-words font-[var(--font-mono)] text-[13px] text-[color:var(--text-main)] [overflow-wrap:anywhere]">
                {children}
              </code>
            ) : (
              <code className="rounded bg-black/50 px-1.5 py-0.5 font-[var(--font-mono)] text-[13px] text-skyline [overflow-wrap:anywhere]">{children}</code>
            );
          },
          table: ({ children }) => (
            <table className="w-full table-fixed border-collapse text-left text-sm [overflow-wrap:anywhere]">{children}</table>
          ),
          th: ({ children }) => (
            <th className="border border-amber/20 bg-black/35 px-2 py-1.5 align-top font-semibold text-skyline [overflow-wrap:anywhere]">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-amber/14 px-2 py-1.5 align-top [overflow-wrap:anywhere]">{children}</td>
          ),
          img: ({ src, alt }) => (
            <img
              src={resolveMarkdownMediaUrl(src ?? "")}
              alt={alt ?? "image"}
              className="max-h-96 w-full max-w-2xl rounded-xl border border-amber/25 object-contain"
            />
          ),
          blockquote: ({ children }) => <blockquote className="border-l-2 border-amber/40 pl-3 text-muted">{children}</blockquote>
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
