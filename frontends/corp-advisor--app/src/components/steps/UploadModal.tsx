import { useState } from "react";
import { uploadPdfs } from "../../api/documents";
import { FileUploader } from "../FileUploader";
import { Button } from "../Button";
import { BASE_URL } from "../../api/baseUrl";

// UploadModal 컴포넌트가 받을 props 타입 정의
type UploadModalProps = {
  onUploadSuccess: (files: string[]) => void;
};

export function UploadModal({ onUploadSuccess }: UploadModalProps) {
  // 파일 업로드
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleUpload = async () => {
    if (uploadedFiles.length === 0) {
      alert("업로드할 파일을 선택해주세요.");
      return;
    }

    setIsUploading(true);

    try {
      const { uploaded: accepted, rejected } = await uploadPdfs(BASE_URL, uploadedFiles);
      setUploadError(rejected.map((r) => r.reason).join("\n") || null);
      if (accepted.length > 0) {
        // 서버가 받아 준 파일만 다음 단계(벡터 적재)로 넘긴다
        onUploadSuccess(accepted);
      }
    } catch (err) {
      console.error("파일 업로드에 실패했습니다:", err);
    } finally {
      setIsUploading(false);
    }
  };
  return (
    <>
      <h2 className="mb-1.5 text-[17px] font-bold text-ink-900">
        분석할 PDF 업로드
      </h2>
      <p className="mb-4 text-[13.5px] leading-relaxed text-ink-500">
        법령·규정 PDF를 올리면 다음 단계에서 벡터 DB에 적재합니다.
      </p>
      <FileUploader
        uploadedFiles={uploadedFiles}
        setUploadedFiles={setUploadedFiles}
      />
      <Button
        ButtonText={isUploading ? "업로드 중…" : "업로드 완료"}
        onClick={handleUpload}
        disabled={isUploading || uploadedFiles.length === 0}
      />
      {uploadError && (
        <p className="mt-3 whitespace-pre-line text-[13px] text-red-600">{uploadError}</p>
      )}
    </>
  );
}
