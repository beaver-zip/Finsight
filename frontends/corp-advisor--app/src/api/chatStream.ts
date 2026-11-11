import type { Handlers } from "../hooks/useChatStream";

export async function readChatStream(stream: ReadableStream<Uint8Array>, h: Handlers, signal: AbortSignal) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let finished = false;
  try {
    while (!finished && !signal.aborted) {
      const { value, done } = await reader.read();
      if (signal.aborted) return;
      if (done) throw new Error("완료 이벤트를 받기 전에 연결이 종료되었습니다. 다시 시도해주세요.");
      buf = (buf + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let idx: number;
      while (!finished && (idx = buf.indexOf("\n\n")) >= 0) {
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
          h.onError?.(data.message, Boolean(data.partial), data.code);
          if (!data.partial) finished = true;
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
