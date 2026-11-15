export async function uploadPdfs(baseUrl: string, files: File[]) {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  const response = await fetch(`${baseUrl}/files/upload-pdf`, {
    method: "POST",
    body: form,
  });
  const data = await response.json();
  const body = response.ok ? data : data.detail ?? {};
  return {
    uploaded: (body.uploaded ?? []) as string[],
    rejected: (body.rejected ?? []) as { file: string; reason: string }[],
  };
}

/** 올린 문서를 지운다. 실패하면 백엔드 detail(없으면 HTTP 상태)로 예외를 던진다. */
export async function deleteDoc(baseUrl: string, fileName: string) {
  const response = await fetch(`${baseUrl}/rag/delete_objects_from_file_name`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_name: fileName }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({})); // 500은 평문일 수 있다
    throw new Error(data.detail || `삭제 실패(HTTP ${response.status})`);
  }
}
