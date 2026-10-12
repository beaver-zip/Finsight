"""업로드 내규: 파일 이름·형식 검사, 조문 분할, 엔드포인트 왕복(가짜 벡터 저장소), 관리 토큰."""
from __future__ import annotations

from types import SimpleNamespace as NS

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import admin, documents
from app.core.law import uploads
from app.deps import get_state


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(uploads, "FILES", tmp_path / "files")
    monkeypatch.setattr(uploads, "STORE", tmp_path / "articles.json")
    uploads._load.cache_clear()
    yield tmp_path
    uploads._load.cache_clear()


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    for page in text.split("\f"):
        doc.new_page().insert_text((40, 60), page, fontname="korea", fontsize=10)
    return doc.tobytes()


def test_safe_name_blocks_paths_and_non_pdf():
    assert uploads.safe_name("../../etc/내규.pdf") == "내규.pdf"
    assert uploads.safe_name("a:b.PDF") == "a_b.PDF"
    for bad in ("x.txt", ".pdf", ""):
        with pytest.raises(uploads.UploadError):
            uploads.safe_name(bad)


def test_save_file_checks_magic(store):
    with pytest.raises(uploads.UploadError):
        uploads.save_file("가짜.pdf", b"hello")
    assert uploads.save_file("진짜.pdf", b"%PDF-1.4 ...").name == "진짜.pdf"


def test_split_articles_by_heading_and_by_page():
    pages = ["제1조(목적) 이 규정은 전자금융 내부통제를 정한다.\n제2조(정의) ① 이 규정에서 쓰는 말은 다음과 같다.\n② 그 밖의 말은 법을 따른다.",
             "제3조(책임자) 정보보호최고책임자는 매년 점검한다."]
    arts = uploads.split_articles("전자금융 내부통제 규정", pages)
    assert [a.key for a in arts] == ["전자금융내부통제규정:1", "전자금융내부통제규정:2", "전자금융내부통제규정:3"]
    assert arts[1].title == "정의" and [p.no for p in arts[1].paragraphs] == [1, 2]
    assert all(a.level == "내규" and a.source == "upload" for a in arts)
    loose = uploads.split_articles("메모", ["첫 쪽 내용", "", "셋째 쪽 내용"])
    assert [a.key for a in loose] == ["메모:1", "메모:3"] and loose[0].title == "1쪽"


class FakeVectors:
    def __init__(self):
        self.chunks, self.deleted = [], []

    async def add(self, chunks):
        self.chunks += chunks
        return len(chunks)

    async def delete_doc(self, name):
        self.deleted.append(name)
        self.chunks = [c for c in self.chunks if c.law_name != name]


def test_upload_register_list_delete_roundtrip(store):
    vec = FakeVectors()
    app = FastAPI()
    app.include_router(documents.files_router, prefix="/files")
    app.include_router(documents.rag_router, prefix="/rag")
    app.dependency_overrides[get_state] = lambda: NS(law_tools=NS(vectors=vec))
    c = TestClient(app)
    pdf = _pdf("Article 1 internal control rule.\fArticle 2 security officer reviews yearly.")
    r = c.post("/files/upload-pdf", files=[("files", ("내규.pdf", pdf, "application/pdf")),
                                           ("files", ("메모.txt", b"x", "text/plain"))])
    assert r.status_code == 200 and r.json()["uploaded"] == ["내규.pdf"] and len(r.json()["rejected"]) == 1
    r = c.post("/rag/register", json={"file_name": ["내규.pdf"]})
    reg = r.json()["registered"][0]
    assert reg["articles"] == 2 and reg["chunk_count"] == 2 and all(x.source == "upload" for x in vec.chunks)
    assert uploads.get_article("내규:1").paragraphs[0].text.startswith("Article 1")
    files = c.get("/rag/show_files_in_collection").json()["unique_file_name_and_chunk"]
    assert files == [{"file_name": "내규.pdf", "chunk_count": 2, "articles": 2}]
    assert c.post("/files/download-pdf/", json={"file_name": "내규.pdf"}).content[:4] == b"%PDF"
    assert c.post("/rag/delete_objects_from_file_name", json={"file_name": "없는.pdf"}).status_code == 404
    assert c.post("/rag/delete_objects_from_file_name", json={"file_name": "내규.pdf"}).json() == {"deleted": "내규.pdf"}
    assert vec.chunks == [] and uploads.all_docs() == {} and not (store / "files" / "내규.pdf").exists()


def test_admin_token(monkeypatch):
    from pydantic import SecretStr

    from app.settings import get_settings
    app = FastAPI()
    app.include_router(admin.router, prefix="/admin")
    c = TestClient(app)
    s = get_settings()
    monkeypatch.setattr(s, "admin_token", None)
    assert c.post("/admin/legal/reset").status_code == 403
    monkeypatch.setattr(s, "admin_token", SecretStr("t0ken"))
    assert c.post("/admin/legal/reset", headers={"X-Admin-Token": "nope"}).status_code == 401

    async def fake_reset():
        return {"ok": True}
    monkeypatch.setattr(admin.law_ingest, "reset", fake_reset)
    assert c.post("/admin/legal/reset", headers={"X-Admin-Token": "t0ken"}).json() == {"ok": True}
