"""사용자가 올린 PDF(내규 등): 업로드, 벡터 적재, 목록, 삭제, 원본 내려받기.

프론트 업로드 화면이 쓰는 v1 경로와 형식을 그대로 받는다(설계서 'API 계약').
- POST /files/upload-pdf          multipart files → {"uploaded": [...], "rejected": [{"file", "reason"}]}
- POST /files/download-pdf/       {file_name} → PDF
- POST /rag/register              {file_name: [..]} → 조문 단위로 나눠 Weaviate에 적재(level=내규, source=upload)
- GET  /rag/show_files_in_collection → {"unique_file_name_and_chunk": [{file_name, chunk_count}]}
- POST /rag/delete_objects_from_file_name  {file_name} → 올린 문서만 지운다(법령 코퍼스는 지울 수 없다)
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.law import uploads
from app.core.law.vector_store import article_chunks
from app.deps import AppState, get_state

log = logging.getLogger(__name__)
files_router = APIRouter()
rag_router = APIRouter()


class FileName(BaseModel):
    file_name: str


class FileNames(BaseModel):
    file_name: list[str]


def _vectors(st: AppState):
    if st.law_tools is None:
        raise HTTPException(503, "법령 검색 DB(Weaviate·Neo4j)가 연결되지 않았습니다")
    return st.law_tools.vectors


@files_router.post("/upload-pdf", summary="PDF 업로드(저장만, 적재는 /rag/register)")
async def upload_pdf(files: list[UploadFile] = File(...)):
    uploaded, rejected = [], []
    for f in files:
        try:
            data = await f.read()
            path = uploads.save_file(f.filename or "", data)
            uploaded.append(path.name)
        except uploads.UploadError as e:
            rejected.append({"file": f.filename, "reason": str(e)})
    if not uploaded and rejected:
        raise HTTPException(400, {"uploaded": [], "rejected": rejected})
    return {"uploaded": uploaded, "rejected": rejected}


@files_router.post("/download-pdf/", summary="올린 PDF 원본")
async def download_pdf(req: FileName):
    try:
        path = uploads.file_path(req.file_name)
    except uploads.UploadError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@rag_router.post("/register", summary="올린 PDF를 조문 단위로 나눠 벡터 DB에 적재")
async def register(req: FileNames, st: AppState = Depends(get_state)):
    vec = _vectors(st)
    done, failed = [], []
    for name in req.file_name:
        try:
            path = uploads.file_path(name)
            doc_name = Path(path.name).stem
            pages = await asyncio.to_thread(uploads.pdf_pages, path)
            arts = uploads.split_articles(doc_name, pages)
            if not arts:
                raise uploads.UploadError(f"{path.name}: 텍스트를 읽지 못했습니다(스캔 PDF는 지원하지 않습니다)")
            await vec.delete_doc(doc_name)              # 같은 문서를 다시 올리면 바꾼다
            chunks = [c for a in arts for c in article_chunks(a)]
            n = await vec.add(chunks)
            uploads.put(path.name, doc_name, arts, n)
            done.append({"file_name": path.name, "articles": len(arts), "chunk_count": n})
        except uploads.UploadError as e:
            failed.append({"file_name": name, "reason": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("적재 실패")
            failed.append({"file_name": name, "reason": f"{type(e).__name__}: {e}"})
    return {"registered": done, "failed": failed}


@rag_router.get("/show_files_in_collection", summary="올린 문서 목록")
async def show_files():
    return {"unique_file_name_and_chunk": [{"file_name": k, "chunk_count": v.get("chunk_count", 0),
                                            "articles": len(v.get("articles", []))}
                                           for k, v in sorted(uploads.all_docs().items())]}


@rag_router.post("/delete_objects_from_file_name", summary="올린 문서 삭제")
async def delete_file(req: FileName, st: AppState = Depends(get_state)):
    try:
        name = uploads.safe_name(req.file_name)
    except uploads.UploadError as e:
        raise HTTPException(400, str(e)) from e
    d = uploads.all_docs().get(name)
    if d is None:
        raise HTTPException(404, f"올린 문서가 아닙니다: {req.file_name}")
    await _vectors(st).delete_doc(d["doc_name"])
    uploads.remove(name)
    return {"deleted": name}
