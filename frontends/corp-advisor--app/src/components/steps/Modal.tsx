import { useState } from "react";
import { X } from "lucide-react";
import { UploadModal } from "./UploadModal";
import { TriggerModal } from "./TriggerModal";
import { CompleteModal } from "./CompleteModal";
import { StepIndicator } from "./StepIndicator";

export function Modal({ onClose }: { onClose: () => void }) {
  const [currentStep, setCurrentStep] = useState(1);
  const [uploadedFiles, setUploadedFiles] = useState<string[]>([]);
  const renderStep = () => {
    switch (currentStep) {
      case 1:
        return (
          <UploadModal
            onUploadSuccess={(files) => {
              setUploadedFiles(files);
              setCurrentStep(2);
            }}
          />
        );
      case 2:
        // 목록 갱신은 닫을 때 onClose가 한 번만 한다
        return <TriggerModal files={uploadedFiles} onTriggerSuccess={() => setCurrentStep(3)} />;
      case 3:
        return <CompleteModal onClose={onClose} />;
      default:
        return null;
    }
  };
  return (
    // 모달 배경 (Overlay)
    // 화면 전체를 덮는 반투명한 배경
    // 클릭하면 onClose 함수가 호출되어 모달이 닫힙니다.
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-md rounded-2xl border border-ink-200 bg-white p-7 shadow-lift"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          aria-label="닫기"
          className="absolute right-3 top-3 rounded-lg p-1.5 text-ink-400 transition-colors hover:bg-ink-100 hover:text-ink-800"
        >
          <X size={16} />
        </button>
        <StepIndicator currentStep={currentStep} />
        {renderStep()}
      </div>
    </div>
  );
}
