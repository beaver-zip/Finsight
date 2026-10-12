import { useCallback, useRef, useState } from "react";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

/** 드롭다운: 자동(기본) · 법령 · 기업 · 뉴스. auto가 아니면 백엔드가 의도를 그 값으로 고정한다. */
export type QueryMode = "auto" | "legal" | "company" | "news";

export type ChatCitation = {
  kind: "article" | "annex" | "filing" | "news" | "metric";
  id: string;
  label: string;
  url?: string | null;
};

export type FinalAnswer = {
  markdown: string;
  citations: ChatCitation[];
};

export type Interrupt = { question: string; options: string[] };

type Handlers = {
  onProgress?: (detail: string) => void;
  onInterrupt?: (it: Interrupt) => void;
  onFinal?: (answer: FinalAnswer | null) => void;
  onError?: (message: string, partial: boolean) => void;
};

function newThreadId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `t-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** POST /chat(SSE)을 읽는다. 같은 채팅 창은 같은 thread_id를 써서 백엔드가 대화를 이어 간다. */
export function useChatStream() {
  const [threadId, setThreadId] = useState<string>(newThreadId);
  const [isLoading, setIsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const resetThread = useCallback(() => {
    abortRef.current?.abort();
    setThreadId(newThreadId());
    setIsLoading(false);
  }, []);

  const send = useCallback(
    async (body: { message?: string; resume?: string; mode: QueryMode }, h: Handlers) => {
      setIsLoading(true);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const res = await fetch(`${BASE_URL}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ thread_id: threadId, ...body }),
          signal: ctrl.signal,
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let finished = false;
        while (!finished) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const raw = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const ev = /^event: (.*)$/m.exec(raw)?.[1];
            const dataLine = /^data: (.*)$/m.exec(raw)?.[1];
            if (!ev || !dataLine) continue;
            const data = JSON.parse(dataLine);
            if (ev === "progress") h.onProgress?.(data.detail);
            else if (ev === "interrupt") {
              h.onInterrupt?.({ question: data.question, options: data.options || [] });
              finished = true;
            } else if (ev === "final") {
              h.onFinal?.(data.answer);
              finished = true;
            } else if (ev === "error") {
              h.onError?.(data.message, Boolean(data.partial));
              if (!data.partial) finished = true;
            }
          }
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          h.onError?.(e instanceof Error ? e.message : String(e), false);
        }
      } finally {
        setIsLoading(false);
      }
    },
    [threadId],
  );

  return { send, isLoading, resetThread, threadId };
}
