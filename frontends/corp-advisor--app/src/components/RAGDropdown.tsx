import type { ReactNode } from "react";
import { Sparkles, Scale, Building2, Newspaper } from "lucide-react";
import type { QueryMode } from "../hooks/useChatStream";

const options: {
  label: string;
  value: QueryMode;
  icon: ReactNode;
  hint: string;
}[] = [
  {
    label: "자동",
    value: "auto",
    icon: <Sparkles size={14} />,
    hint: "질문을 보고 법령·기업·뉴스 중 필요한 쪽을 골라 답합니다.",
  },
  {
    label: "법령",
    value: "legal",
    icon: <Scale size={14} />,
    hint: "전자금융거래법·시행령·감독규정·시행세칙 조문을 인용해 답합니다.",
  },
  {
    label: "기업",
    value: "company",
    icon: <Building2 size={14} />,
    hint: "상장사의 실적·밸류에이션·주주환원을 공시와 시세로 답합니다.",
  },
  {
    label: "뉴스",
    value: "news",
    icon: <Newspaper size={14} />,
    hint: "기업의 최근 기사를 골라 요약합니다.",
  },
];

type RAGDropdownProps = {
  value: QueryMode;
  onSelect: (value: QueryMode) => void;
  disabled?: boolean;
};

/** 분석 모드 선택용 세그먼티드 컨트롤. */
export function RAGDropdown({ value, onSelect, disabled }: RAGDropdownProps) {
  const active = options.find((o) => o.value === value) ?? options[0];

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <div
        role="tablist"
        aria-label="분석 모드"
        className="inline-flex rounded-xl border border-ink-200 bg-ink-100/70 p-1"
      >
        {options.map((option) => {
          const isActive = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              role="tab"
              aria-selected={isActive}
              disabled={disabled}
              onClick={() => onSelect(option.value)}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-semibold transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-50 ${
                isActive
                  ? "bg-white text-brand-700 shadow-sm"
                  : "text-ink-500 hover:text-ink-700"
              }`}
            >
              {option.icon}
              {option.label}
            </button>
          );
        })}
      </div>
      <p className="text-[12.5px] text-ink-400">{active.hint}</p>
    </div>
  );
}
