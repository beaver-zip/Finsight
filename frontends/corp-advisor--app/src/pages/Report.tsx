import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCorpData } from "../hooks/useCorpData.ts";
import { Table } from "../components/Table.tsx";
import { SendHorizonal, ChevronLeft, Printer, RotateCcw } from "lucide-react";
import { LoadingSpinner } from "../components/LoadingSpinner.tsx";
import { BASE_URL } from "../api/baseUrl";

const REPORT_STEPS = [
  "DART 기업개황",
  "재무제표 파싱",
  "뉴스 수집·분석",
  "차트 생성",
  "종합 결론",
];

/** 보고서 HTML에서 제목(h1)을 뽑아 상단 바에 표시한다. */
function extractReportTitle(html: string): string {
  const match = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i);
  if (!match) return "기업 분석 보고서";
  return match[1].replace(/<[^>]+>/g, "").trim() || "기업 분석 보고서";
}

function Report() {
  const navigate = useNavigate();

  const [searchTerm, setSearchTerm] = useState(""); // 입력 폼 값
  const [submittedTerm, setSubmittedTerm] = useState(""); // fetch에 사용
  const [reportHtml, setReportHtml] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const {
    data: corpData,
    loading: corpLoading,
    error: corpError,
  } = useCorpData(submittedTerm);

  const handleCorpSearch = () => {
    if (!searchTerm.trim()) return;
    setSubmittedTerm(searchTerm.trim());
  };

  const handleNewReport = () => {
    setReportHtml("");
    setErrorMessage(null);
  };

  const generateReport = async (corpCode?: string) => {
    const codeToUse = corpCode;
    if (!codeToUse?.trim()) return;

    setReportHtml("");
    setIsGenerating(true);
    setErrorMessage(null);
    setSearchTerm("");

    try {
      const response = await fetch(`${BASE_URL}/report/${codeToUse}`, {
        method: "GET",
      });

      if (!response.ok) throw new Error("네트워크 응답이 실패했습니다.");
      const data = await response.text();
      // 분석 자료가 없으면 백엔드가 200과 빈 본문을 준다
      if (!data.trim()) throw new Error("빈 보고서");
      setReportHtml(data);
    } catch (err) {
      console.error("답변을 가져오는 데 실패했습니다:", err);
      setErrorMessage(
        "보고서를 생성하지 못했습니다. 선택한 기업의 공시 데이터가 없거나 서버에 문제가 있을 수 있습니다."
      );
    } finally {
      setIsGenerating(false);
    }
  };

  const reportTitle = reportHtml ? extractReportTitle(reportHtml) : "";

  // ---------- 보고서 결과 화면 ----------
  if (reportHtml) {
    return (
      <div className="flex min-h-screen w-full flex-col bg-ink-50 font-sans">
        <header className="sticky top-0 z-20 border-b border-ink-200 bg-white">
          <div className="mx-auto flex w-full max-w-4xl items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
            <button
              onClick={() => navigate("/")}
              className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13.5px] font-medium text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800"
            >
              <ChevronLeft size={18} />
              <span className="hidden sm:inline">홈으로</span>
            </button>

            <p className="min-w-0 flex-1 truncate text-center text-[14px] font-bold tracking-tight text-ink-900">
              {reportTitle}
            </p>

            <div className="flex items-center gap-1">
              <button
                onClick={() => window.print()}
                className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[13.5px] font-medium text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800"
              >
                <Printer size={15} />
                <span className="hidden sm:inline">인쇄</span>
              </button>
              <button
                onClick={handleNewReport}
                className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-[13.5px] font-semibold text-white transition-colors hover:bg-brand-700"
              >
                <RotateCcw size={14} />
                <span className="hidden sm:inline">다른 기업</span>
              </button>
            </div>
          </div>
        </header>

        <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8 sm:px-6">
          {/* 백엔드 보고서(.fs-report)는 자체 스타일·여백·면책 문구를 갖고 있다 */}
          <article className="overflow-hidden rounded-2xl border border-ink-200 bg-white shadow-card">
            <div className="w-full" dangerouslySetInnerHTML={{ __html: reportHtml }} />
          </article>
        </main>
      </div>
    );
  }

  // ---------- 기업 검색 화면 ----------
  return (
    <div className="flex min-h-screen w-full flex-col bg-ink-50 font-sans">
      <header className="border-b border-ink-200 bg-white/90 backdrop-blur">
        <div className="mx-auto flex w-full max-w-3xl items-center justify-between gap-3 px-4 py-2.5 sm:px-6">
          <button
            onClick={() => navigate("/")}
            className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[13.5px] font-medium text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800"
          >
            <ChevronLeft size={18} />
            <span className="hidden sm:inline">홈으로</span>
          </button>
          <span className="text-[14px] font-bold tracking-tight text-ink-900">
            기업 분석 보고서
          </span>
          <span className="hidden rounded-full border border-ink-200 bg-ink-50 px-2 py-0.5 text-[11.5px] font-medium text-ink-500 sm:inline">
            DART · 뉴스 · 차트
          </span>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-4 py-10 sm:px-6">
        {isGenerating ? (
          <LoadingSpinner
            loadingText="보고서를 생성하고 있습니다"
            steps={REPORT_STEPS}
            block
          />
        ) : (
          <>
            <div className="mb-7 text-center">
              <h1 className="mb-2 text-[30px] font-extrabold tracking-tight text-ink-900">
                어떤 기업을 분석할까요?
              </h1>
              <p className="text-[14.5px] text-ink-500">
                DART 공시 기준 기업명을 입력해주세요. (예: 삼성전자, 카카오,
                셀트리온)
              </p>
            </div>

            <div className="mb-4 flex gap-2">
              <input
                type="text"
                className="flex-1 rounded-xl border border-ink-200 bg-white px-4 py-3 text-[15px] shadow-card transition focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100"
                placeholder="기업명을 입력하세요"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCorpSearch();
                }}
              />
              <button
                type="button"
                onClick={handleCorpSearch}
                aria-label="기업 검색"
                className="flex items-center justify-center rounded-xl bg-brand-600 px-5 text-white transition-all hover:bg-brand-700 active:scale-95"
              >
                <SendHorizonal size={18} />
              </button>
            </div>

            {errorMessage && (
              <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13.5px] text-red-700">
                {errorMessage}
              </div>
            )}

            <Table
              loading={corpLoading}
              error={corpError}
              isIdle={!submittedTerm}
              data={corpData}
              onClick={generateReport}
            />
          </>
        )}
      </main>
    </div>
  );
}

export default Report;
