"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type ChatMarkdownProps = {
  content: string;
  isUser?: boolean;
};

export default function ChatMarkdown({ content, isUser = false }: ChatMarkdownProps) {
  if (isUser) {
    return <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-[color:var(--text-main)]">{content}</p>;
  }

  return (
    <div className="space-y-3 text-[15px] leading-relaxed text-[color:var(--text-main)]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="whitespace-pre-wrap leading-relaxed">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold text-skyline">{children}</strong>,
          em: ({ children }) => <em className="italic text-amber">{children}</em>,
          ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="pl-1">{children}</li>,
          a: ({ href, children }) => (
            <a
              href={href ?? "#"}
              target="_blank"
              rel="noreferrer"
              className="text-skyline underline decoration-skyline/45 underline-offset-4 hover:text-amber"
            >
              {children}
            </a>
          ),
          code: ({ className, children }) => {
            const isBlock = Boolean(className);
            return isBlock ? (
              <code className="block overflow-x-auto rounded-xl border border-amber/25 bg-black/55 p-3 font-[var(--font-mono)] text-[13px] text-[color:var(--text-main)]">
                {children}
              </code>
            ) : (
              <code className="rounded bg-black/50 px-1.5 py-0.5 font-[var(--font-mono)] text-[13px] text-skyline">{children}</code>
            );
          },
          blockquote: ({ children }) => <blockquote className="border-l-2 border-amber/40 pl-3 text-muted">{children}</blockquote>
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
