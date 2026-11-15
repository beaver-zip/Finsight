import type { ComponentProps, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Sparkles } from "lucide-react";

export type Message = {
  id: number;
  type: "question" | "answer" | "loading";
  text: string | ReactNode;
  options?: string[];       // 되묻기 선택지(/chat interrupt)
};

type BubbleProps = {
  isQuestion: boolean;
  msg: Message;
};

const markdownComponents = {
  a: ({ node: _node, ...props }: ComponentProps<"a"> & { node?: unknown }) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
  // 넓은 표가 레이아웃을 밀어내지 않도록 감싼다
  table: ({ node: _node, ...props }: ComponentProps<"table"> & { node?: unknown }) => (
    <div className="md-scroll">
      <table {...props} />
    </div>
  ),
};

export function Bubble({ isQuestion, msg }: BubbleProps) {
  // ---------- 사용자 질문 ----------
  if (isQuestion) {
    return (
      <div className="flex w-full animate-fade-up justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-600 px-4 py-2.5 text-[15px] leading-relaxed text-white shadow-sm">
          {msg.text}
        </div>
      </div>
    );
  }

  // ---------- 답변 ----------
  const renderBody = () => {
    if (typeof msg.text !== "string") {
      // 로딩 스피너 등 JSX는 그대로 렌더링
      return msg.text;
    }

    return (
      <div className="md-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={markdownComponents}
          >
            {msg.text}
          </ReactMarkdown>
      </div>
    );
  };

  return (
    <div className="flex w-full animate-fade-up gap-3">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-ink-900 text-white">
        <Sparkles size={14} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="mb-1.5 text-[12px] font-semibold text-ink-400">
          FinSight
        </p>
        <div className="rounded-2xl rounded-tl-md border border-ink-200 bg-white px-5 py-4 shadow-card">
          {renderBody()}
        </div>
      </div>
    </div>
  );
}
