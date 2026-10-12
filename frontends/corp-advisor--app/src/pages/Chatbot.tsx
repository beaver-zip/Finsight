// Chatbot.tsx: POST /chat(SSE) 채팅 화면. 같은 창은 같은 thread_id로 대화를 잇고, "새 대화"는 새 thread_id를 받는다.
import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronLeft, Sparkles, RotateCcw } from "lucide-react";

import { ChatForm } from "../components/ChatForm";
import { Bubble } from "../components/Bubble";
import { LoadingSpinner } from "../components/LoadingSpinner";
import type { Message } from "../ChatContext";
import { useChatStream } from "../hooks/useChatStream";
import type { QueryMode } from "../hooks/useChatStream";

// 빈 화면에서 제안하는 예시 질문
const SAMPLE_QUESTIONS = [
  "정보보호최고책임자를 임원으로 지정해야 하는 회사는?",
  "전자금융업 등록에 필요한 자본금 기준은?",
  "삼성전자 최근 분기 실적은 어땠어?",
  "카카오뱅크 요즘 뉴스 뭐 있어?",
];

function Chatbot() {
  const navigate = useNavigate();

  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [typingTextMap, setTypingTextMap] = useState<{
    [id: number]: { answer: string; cites?: any[] };
  }>({});
  const [queryMode, setQueryMode] = useState<QueryMode>("auto");
  const [awaitingResume, setAwaitingResume] = useState(false);
  const { send, isLoading, resetThread } = useChatStream();

  const chatEndRef = useRef<null | HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 타자 치기 효과(긴 답변은 한 번에 여러 글자씩 흘려 체감 속도를 유지)
  useEffect(() => {
    const entries = Object.entries(typingTextMap);
    if (entries.length === 0) return;
    const intervals: ReturnType<typeof setInterval>[] = [];
    entries.forEach(([idStr, data]) => {
      const id = Number(idStr);
      const fullText = data.answer ?? "";
      const step = Math.max(2, Math.ceil(fullText.length / 220));
      let charIndex = 0;
      const intervalId = setInterval(() => {
        if (charIndex < fullText.length) {
          charIndex = Math.min(charIndex + step, fullText.length);
          const currentText = fullText.slice(0, charIndex);
          setMessages((prev) =>
            prev.map((msg) => (msg.id === id ? { ...msg, type: "answer", text: currentText } : msg)),
          );
        } else {
          clearInterval(intervalId);
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === id ? { ...msg, isStreaming: false, type: "answer", cites: data.cites } : msg,
            ),
          );
          setTypingTextMap((prev) => {
            const { [id]: _, ...rest } = prev;
            return rest;
          });
        }
      }, 16);
      intervals.push(intervalId);
    });
    return () => intervals.forEach(clearInterval);
  }, [typingTextMap]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [inputValue]);

  const setLoadingText = (id: number, detail: string) =>
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text: <LoadingSpinner loadingText={detail} /> } : m)),
    );

  /** 질문(message) 또는 되묻기 답(resume)을 보내고, 진행·되묻기·최종 답을 말풍선에 반영한다. */
  const run = (shown: string, body: { message?: string; resume?: string }) => {
    const qid = Date.now();
    const aid = qid + 1;
    setMessages((prev) => [
      ...prev.map((m) => (m.options ? { ...m, options: undefined } : m)),
      { id: qid, type: "question", text: shown },
      {
        id: aid,
        type: "loading",
        text: <LoadingSpinner loadingText="질문을 분류하고 있습니다" />,
        isStreaming: true,
      },
    ]);
    setInputValue("");
    setAwaitingResume(false);

    send(
      { ...body, mode: queryMode },
      {
        onProgress: (detail) => setLoadingText(aid, detail),
        onInterrupt: (it) => {
          setAwaitingResume(true);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === aid
                ? { ...m, type: "answer", text: it.question, isStreaming: false, options: it.options }
                : m,
            ),
          );
        },
        onFinal: (answer) => {
          // 출처는 답변 마크다운 끝의 번호 목록(본문 [1]과 같은 번호)에 있으므로 따로 패널을 띄우지 않는다
          setTypingTextMap((prev) => ({
            ...prev,
            [aid]: { answer: answer?.markdown ?? "답변을 만들지 못했습니다.", cites: [] },
          }));
        },
        onError: (message, partial) => {
          if (partial) return; // 일부 단계 실패는 최종 답에 반영돼 있다
          setMessages((prev) =>
            prev.map((m) =>
              m.id === aid
                ? {
                    ...m,
                    type: "answer",
                    isStreaming: false,
                    text: `답변을 가져오지 못했습니다. 백엔드 서버 상태를 확인해 주세요. (${message})`,
                  }
                : m,
            ),
          );
        },
      },
    );
  };

  const submitQuestion = (rawText: string) => {
    const text = rawText.trim();
    if (!text || isLoading) return;
    // 되묻기 중이면 입력을 그 답으로 보낸다
    run(text, awaitingResume ? { resume: text } : { message: text });
  };

  const handleSubmit = () => submitQuestion(inputValue);

  const handleReset = () => {
    setMessages([]);
    setTypingTextMap({});
    setAwaitingResume(false);
    resetThread();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const columnClass = "mx-auto w-full max-w-3xl px-4 sm:px-6";
  const hasMessages = messages.length > 0;

  const form = (
    <ChatForm
      inputContainerClass="w-full"
      textareaRef={textareaRef}
      inputValue={inputValue}
      onChange={(e) => setInputValue(e.target.value)}
      onKeyDown={handleKeyDown}
      onClick={handleSubmit}
      handleOpenModal={() => {}}
      collectionFiles={[]}
      handleFileDelete={() => {}}
      hasMessages={hasMessages}
      isLoading={isLoading}
      queryMode={queryMode}
      setQueryMode={setQueryMode}
      afterSubmitPlaceholder={awaitingResume ? "답을 입력하거나 위에서 고르세요." : undefined}
    />
  );

  return (
    <div className="flex h-screen w-full flex-col bg-ink-50 font-sans">
      <header className="z-20 shrink-0 border-b border-ink-200 bg-white/90 backdrop-blur">
        <div className="flex items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
          <button
            onClick={() => navigate("/")}
            className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13.5px] font-medium text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800"
          >
            <ChevronLeft size={18} />
            <span className="hidden sm:inline">홈으로</span>
          </button>

          <span className="text-[14px] font-bold tracking-tight text-ink-900">금융 자문 챗봇</span>

          <button
            onClick={handleReset}
            disabled={!hasMessages}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[13.5px] font-medium text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800 disabled:pointer-events-none disabled:opacity-40"
          >
            <RotateCcw size={15} />
            <span className="hidden sm:inline">새 대화</span>
          </button>
        </div>
      </header>

      {hasMessages ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <main className="min-h-0 flex-1 overflow-y-auto py-6">
            <div className={`${columnClass} flex flex-col gap-6`}>
              {messages.map((msg) => (
                <div key={msg.id}>
                  <Bubble
                    isLoading={isLoading}
                    onCiteClick={() => {}}
                    isQuestion={msg.type === "question"}
                    cites={msg.cites || []}
                    msg={msg}
                  />
                  {msg.options && msg.options.length > 0 && (
                    <div className="ml-10 mt-2 flex flex-wrap gap-2">
                      {msg.options.map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          disabled={isLoading}
                          onClick={() => run(opt, { resume: opt })}
                          className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-[13px] font-medium text-ink-700 shadow-card transition-colors hover:border-brand-300 hover:text-brand-700 disabled:opacity-50"
                        >
                          {opt}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>
          </main>

          <footer className="shrink-0 border-t border-ink-200 bg-white/90 py-3 backdrop-blur">
            <div className={columnClass}>{form}</div>
          </footer>
        </div>
      ) : (
        <main className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto py-8">
          <div className={`${columnClass} w-full`}>
            <div className="mb-8 text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-ink-900 text-white">
                <Sparkles size={22} />
              </div>
              <h1 className="mb-2 text-[28px] font-extrabold tracking-tight text-ink-900">
                무엇을 확인해 드릴까요?
              </h1>
              <p className="text-[14.5px] text-ink-500">
                전자금융 법령은 조문을 인용해, 상장사는 공시·시세·기사를 근거로 답합니다.
              </p>
            </div>

            {form}

            <div className="mt-6">
              <p className="mb-2.5 text-[11.5px] font-semibold uppercase tracking-wider text-ink-400">
                예시 질문
              </p>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {SAMPLE_QUESTIONS.map((question) => (
                  <button
                    key={question}
                    type="button"
                    onClick={() => submitQuestion(question)}
                    className="rounded-xl border border-ink-200 bg-white px-3.5 py-3 text-left text-[13.5px] leading-snug text-ink-600 shadow-card transition-all hover:-translate-y-0.5 hover:border-brand-300 hover:text-ink-900"
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </main>
      )}
    </div>
  );
}

export default Chatbot;
