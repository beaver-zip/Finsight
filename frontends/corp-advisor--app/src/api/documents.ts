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
