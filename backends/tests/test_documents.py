"""업로드 내규: 파일 이름·형식 검사, 조문 분할, 엔드포인트 왕복(가짜 벡터 저장소), 관리 토큰."""
from __future__ import annotations

import asyncio
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


@pytest.mark.parametrize("ref", ["제1조에 따른 목적에 맞게 해석한다.", "제5조에 따른다.", "제1조(목적)에 따른 목적에 맞게 해석한다.",
                                 "제2조제3호에 따른 임원으로 한다.", "제1조ㆍ제2조에 따른 사항을 지킨다.",
                                 "제1조, 제2조를 지킨다.", "제2조 각 호의 사람이다.", "제1조는 적용하지 아니한다.",
                                 "제1조로 정한다."])
def test_split_articles_keeps_line_start_reference_in_current_article(ref):
    pages = [f"제1조(목적) 이 규정은 내부통제를 정한다.\n제2조(정의) 용어의 뜻은\n{ref}\n제3조(책임) 의장은 점검한다."]
    arts = {a.key: a for a in uploads.split_articles("내규", pages)}
    assert list(arts) == ["내규:1", "내규:2", "내규:3"]
    assert [p.text for p in arts["내규:1"].paragraphs] == ["이 규정은 내부통제를 정한다."]
    assert ref in arts["내규:2"].paragraphs[0].text


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
    assert uploads.get_article(uploads.document_key("내규.pdf") + ":1").paragraphs[0].text.startswith("Article 1")
    files = c.get("/rag/show_files_in_collection").json()["unique_file_name_and_chunk"]
    assert files == [{"file_name": "내규.pdf", "chunk_count": 2, "articles": 2}]
    assert c.post("/files/download-pdf/", json={"file_name": "내규.pdf"}).content[:4] == b"%PDF"
    assert c.post("/rag/delete_objects_from_file_name", json={"file_name": "없는.pdf"}).status_code == 404
    uploads.save_file("내규.pdf", pdf)  # 적재 전 교체본(pending)도 함께 지워야 한다
    assert c.post("/rag/delete_objects_from_file_name", json={"file_name": "내규.pdf"}).json() == {"deleted": "내규.pdf"}
    assert vec.chunks == [] and uploads.all_docs() == {} and list((store / "files").iterdir()) == []


def test_upload_rejects_oversized_file_without_truncating(store, monkeypatch):
    app = FastAPI()
    app.include_router(documents.files_router, prefix="/files")
    monkeypatch.setattr(uploads, "MAX_BYTES", 8)
    r = TestClient(app).post("/files/upload-pdf", files=[("files", ("큰.pdf", b"%PDF-1.4 0123456789", "application/pdf"))])
    assert r.status_code == 400 and "20MB" in r.json()["detail"]["rejected"][0]["reason"]
    assert not (store / "files" / "큰.pdf").exists()


def test_unregistered_upload_is_listed_and_deletable(store):
    vec = FakeVectors()
    app = FastAPI()
    app.include_router(documents.rag_router, prefix="/rag")
    app.dependency_overrides[get_state] = lambda: NS(law_tools=NS(vectors=vec))
    c = TestClient(app)
    uploads.save_file("비밀.pdf", _pdf("Scanned."))
    files = c.get("/rag/show_files_in_collection").json()["unique_file_name_and_chunk"]
    assert files == [{"file_name": "비밀.pdf", "chunk_count": 0, "articles": 0}]
    assert c.post("/rag/delete_objects_from_file_name", json={"file_name": "비밀.pdf"}).json() == {"deleted": "비밀.pdf"}
    assert vec.deleted == [] and list((store / "files").iterdir()) == []
    assert c.post("/rag/delete_objects_from_file_name", json={"file_name": "비밀.pdf"}).status_code == 404


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
    assert c.post("/admin/legal/reset", headers=[(b"X-Admin-Token", "é".encode())]).status_code == 401

    async def fake_reset():
        return {"ok": True}
    monkeypatch.setattr(admin.law_ingest, "reset", fake_reset)
    assert c.post("/admin/legal/reset", headers={"X-Admin-Token": "t0ken"}).json() == {"ok": True}


@pytest.mark.parametrize("failure", ["embedding", "partial", "metadata"])
@pytest.mark.asyncio
async def test_failed_replacement_preserves_old_document_and_retry(store, monkeypatch, failure):
    from copy import deepcopy

    vec = FakeVectors()
    st = NS(law_tools=NS(vectors=vec))
    old_pdf, new_pdf = _pdf("Old policy."), _pdf("New policy.\fAnother new page.")
    uploads.save_file("내규.pdf", old_pdf)
    uploads.save_file("다른.pdf", _pdf("Other policy."))
    await documents.register(documents.FileNames(file_name=["내규.pdf", "다른.pdf"]), st)
    before = deepcopy(uploads.all_docs())
    old_chunks = list(vec.chunks)
    uploads.save_file("내규.pdf", new_pdf)

    async def fail_add(chunks):
        if failure == "partial":
            vec.chunks.append(chunks[0])
        raise RuntimeError("injected insert failure")

    def fail_put(*args, **kwargs):
        raise OSError("injected metadata failure")

    with monkeypatch.context() as patch:
        if failure == "metadata":
            patch.setattr(uploads, "put", fail_put)
        else:
            patch.setattr(vec, "add", fail_add)
        result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
    assert result["failed"] and not result["registered"]
    assert uploads.all_docs() == before
    assert vec.chunks == old_chunks
    assert uploads.get_article(uploads.document_key("내규.pdf") + ":1").paragraphs[0].text == "Old policy."
    assert (store / "files" / "내규.pdf").read_bytes() == old_pdf

    result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
    assert not result["failed"] and result["registered"][0]["chunk_count"] == 2
    assert sorted(p.name for p in (store / "files").iterdir()) == sorted(["내규.pdf", "다른.pdf"])
    assert (store / "files" / "내규.pdf").read_bytes() == new_pdf
    assert sorted(c.text for c in vec.chunks) == ["Another new page.", "New policy.", "Other policy."]
    assert uploads.all_docs()["다른.pdf"] == before["다른.pdf"]
    assert uploads.get_article(uploads.document_key("내규.pdf") + ":1").paragraphs[0].text == "New policy."


def test_metadata_replace_failure_keeps_disk_and_cache(store, monkeypatch):
    from pathlib import Path

    old = uploads.split_articles("내규", ["Old policy."])
    uploads.put("내규.pdf", "내규", old, 1)
    before = uploads.STORE.read_bytes()
    before_files = set(store.iterdir())

    def fail_replace(self, target):
        raise OSError("injected rename failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="rename failure"):
        uploads.put("내규.pdf", "내규", uploads.split_articles("내규", ["New policy."]), 1)
    assert uploads.STORE.read_bytes() == before
    uploads._load.cache_clear()
    assert uploads.get_article(uploads.document_key("내규.pdf") + ":1").paragraphs[0].text == "Old policy."
    assert set(store.iterdir()) == before_files


def test_metadata_writes_are_atomic(store, monkeypatch, tmp_path):
    from pathlib import Path

    from app.core.law import corpus

    uploads.put("내규.pdf", "내규", uploads.split_articles("내규", ["Old policy."]), 1)
    monkeypatch.setattr(corpus, "CORPUS_FILE", tmp_path / "law" / "corpus.json")
    corpus.save([])
    before = uploads.STORE.read_bytes(), corpus.CORPUS_FILE.read_bytes()
    before_files = set(tmp_path.rglob("*"))

    def fail_replace(self, target):
        raise OSError("injected rename failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError):
        uploads.remove("내규.pdf")
    with pytest.raises(OSError):
        corpus.save([NS(name="법", key_name="법", law_id="1", level="법률", serial="1", effective_date=None,
                        promulgation_no="", articles=[], annexes=[], delegations=[])])
    assert (uploads.STORE.read_bytes(), corpus.CORPUS_FILE.read_bytes()) == before
    assert set(tmp_path.rglob("*")) == before_files
    corpus.load_corpus.cache_clear()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_revert_successful_replacement(store, monkeypatch):
    vec = FakeVectors()
    st = NS(law_tools=NS(vectors=vec))
    # 기존 vector_name 없는 메타데이터도 교체할 수 있어야 한다.
    old = uploads.split_articles("내규", ["Old policy."])
    uploads.put("내규.pdf", "내규", old, 1)
    await vec.add([c for a in old for c in documents.article_chunks(a)])
    uploads.save_file("내규.pdf", _pdf("New policy."))

    async def fail_delete(name):
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(vec, "delete_doc", fail_delete)
    result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
    assert result["registered"] and not result["failed"]
    active = uploads.all_docs()["내규.pdf"]["vector_name"]
    assert [c.text for c in vec.chunks if c.law_name == active] == ["New policy."]
    assert uploads.get_article(uploads.document_key("내규.pdf") + ":1").paragraphs[0].text == "New policy."


@pytest.mark.asyncio
async def test_failed_first_registration_can_be_retried(store, monkeypatch):
    vec = FakeVectors()
    st = NS(law_tools=NS(vectors=vec))
    uploads.save_file("내규.pdf", _pdf("New policy."))

    async def fail_add(chunks):
        vec.chunks.append(chunks[0])
        raise RuntimeError("injected partial failure")

    with monkeypatch.context() as patch:
        patch.setattr(vec, "add", fail_add)
        result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
    assert result["failed"] and uploads.all_docs() == {} and vec.chunks == []
    result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
    assert result["registered"] and len(vec.chunks) == 1


@pytest.mark.parametrize("levels", [None, ["내규"]])
@pytest.mark.asyncio
async def test_search_only_uses_published_vectors_and_restores_display_name(store, monkeypatch, levels):
    from dataclasses import asdict
    from unittest.mock import AsyncMock
    import numpy as np
    from app.core.law import vector_store

    uploads.put("기존.pdf", "기존", [], 1)
    uploads.put("내규.pdf", "내규", [], 1, vector_name="active-generation")
    rows = []
    for name, source, level in [("기존", "upload", "내규"), ("active-generation", "upload", "내규"),
                                ("incomplete-generation", "upload", "내규"), ("내규", "upload", "내규"),
                                ("법령", "lawgo", "법률")]:
        chunk = vector_store.Chunk("legacy:1:p0", "legacy:1", name, level, source, "heading", name)
        rows.append(NS(properties=asdict(chunk), metadata=NS(score=1.0)))

    # SDK 필터를 평가하는 외부 저장소 대역. 실제 LawVectors.hybrid 경로를 호출한다.
    def matches(flt, props):
        if hasattr(flt, "filters"):
            children = [matches(f, props) for f in flt.filters]
            return all(children) if type(flt).__name__ == "_FilterAnd" else any(children)
        value = props[flt.target]
        if flt.operator.value == "Equal":
            return value == flt.value
        if flt.operator.value == "NotEqual":
            return value != flt.value
        assert flt.operator.value == "ContainsAny"
        return value in flt.value

    async def hybrid(**kwargs):
        return NS(objects=[row for row in rows if matches(kwargs["filters"], row.properties)])

    col = NS(query=NS(hybrid=hybrid))
    client = NS(collections=NS(get=lambda _: col))
    monkeypatch.setattr(vector_store, "embed", AsyncMock(return_value=np.array([[1.0, 0.0]])))
    vec = vector_store.LawVectors(client)
    hits = await vec.hybrid("policy", levels=levels)
    assert [h.chunk.law_name for h in hits] == (["기존", "내규"] if levels else ["기존", "내규", "법령"])
    assert [h.chunk.text for h in hits][:2] == ["기존", "active-generation"]
    assert hits[0].chunk.article_key == uploads.document_key("기존.pdf") + ":1"
    assert hits[1].chunk.chunk_key == uploads.document_key("내규.pdf") + ":1:p0"
    uploads.put("중복.pdf", "중복", [], 1, vector_name="active-generation")
    hits = await vec.hybrid("policy")
    assert [h.chunk.law_name for h in hits] == ["기존", "법령"]
    monkeypatch.setattr(uploads, "all_docs", lambda: {})
    hits = await vec.hybrid("policy")
    assert [h.chunk.law_name for h in hits] == ["법령"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_replacement_with_isolated_weaviate(store, monkeypatch):
    """명시한 로컬 테스트 DB에 고유 컬렉션을 만들고 마지막에 그 컬렉션만 삭제한다."""
    import os
    from uuid import uuid4
    from unittest.mock import AsyncMock
    import numpy as np
    import weaviate
    from app.core.law import vector_store

    http_port = os.environ.get("FINSIGHT_TEST_WEAVIATE_HTTP_PORT")
    grpc_port = os.environ.get("FINSIGHT_TEST_WEAVIATE_GRPC_PORT")
    if not http_port or not grpc_port:
        pytest.skip("격리 Weaviate의 HTTP/GRPC 포트를 명시해야 합니다")
    collection = f"UploadTest{uuid4().hex}"
    monkeypatch.setattr(vector_store, "COLLECTION", collection)
    monkeypatch.setattr(vector_store, "embed", AsyncMock(side_effect=lambda texts: np.array([[1., 0., 0.] for _ in texts])))
    client = weaviate.use_async_with_local(port=int(http_port), grpc_port=int(grpc_port))
    async with vector_store.LawVectors(client) as vec:
        try:
            await vec.reset()
            st = NS(law_tools=NS(vectors=vec))
            uploads.save_file("내규.pdf", _pdf("Old policy."))
            uploads.save_file("다른.pdf", _pdf("Other policy."))
            result = await documents.register(documents.FileNames(file_name=["내규.pdf", "다른.pdf"]), st)
            assert not result["failed"]
            uploads.save_file("내규.pdf", _pdf("New policy.\fAnother new page."))
            add = vec.add

            async def partial_failure(chunks):
                await add(chunks[:1])
                hits = await vec.hybrid("policy", alpha=1)
                assert sorted(h.chunk.text for h in hits) == ["Old policy.", "Other policy."]
                raise RuntimeError("injected failure after real insertion")

            with monkeypatch.context() as patch:
                patch.setattr(vec, "add", partial_failure)
                result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
            assert result["failed"]
            hits = await vec.hybrid("policy", alpha=1)
            assert sorted(h.chunk.text for h in hits) == ["Old policy.", "Other policy."]
            result = await documents.register(documents.FileNames(file_name=["내규.pdf"]), st)
            assert not result["failed"]
            hits = await vec.hybrid("policy", alpha=1)
            assert sorted(h.chunk.text for h in hits) == ["Another new page.", "New policy.", "Other policy."]
            assert {h.chunk.law_name for h in hits} == {"내규", "다른"}
            from contextlib import asynccontextmanager
            from app.core.law import ingest

            @asynccontextmanager
            async def borrowed_vectors():
                yield vec

            law = uploads.split_articles("테스트법", ["Law text."])[0].model_copy(update={"source": "lawgo", "level": "법률"})
            with monkeypatch.context() as patch:
                patch.setattr(ingest, "LawVectors", borrowed_vectors)
                patch.setattr(ingest, "load_corpus", lambda: NS(articles={law.key: law}, annexes={}))
                await ingest.ingest(fetch=False, graph=False)
            hits = await vec.hybrid("policy", alpha=1, levels=["내규"])
            assert sorted(h.chunk.text for h in hits) == ["Another new page.", "New policy.", "Other policy."]
            await documents.delete_file(documents.FileName(file_name="내규.pdf"), st)
            assert [h.chunk.text for h in await vec.hybrid("policy", alpha=1, levels=["내규"])] == ["Other policy."]
            uploads.save_file("다 른.pdf", _pdf("Spaced policy."))
            uploads.save_file("테스트법.pdf", _pdf("Uploaded law policy."))
            result = await documents.register(documents.FileNames(file_name=["다 른.pdf", "테스트법.pdf"]), st)
            assert not result["failed"]
            hits = await vec.hybrid("policy", alpha=1, levels=["내규"])
            assert len({h.chunk.article_key for h in hits}) == 3
            for h in hits:
                assert uploads.get_article(h.chunk.article_key).paragraphs[0].text == h.chunk.text
            from app.core.law.corpus import Corpus
            corpus = Corpus(docs=[], delegations=[], articles={law.key: law}, annexes={})
            assert corpus.get(law.key).source == "lawgo"
            assert all(corpus.get(h.chunk.article_key).source == "upload" for h in hits)
        finally:
            if await client.collections.exists(collection):
                await client.collections.delete(collection)


@pytest.mark.parametrize("vector_name", [None, "upload:active-generation"])
@pytest.mark.asyncio
async def test_reingest_preserves_uploaded_vector_identity(store, monkeypatch, vector_name):
    from app.core.law import ingest

    arts = uploads.split_articles("내규", ["Policy text."])
    uploads.put("내규.pdf", "내규", arts, 1, vector_name=vector_name)
    vec = FakeVectors()

    class RebuildVectors:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def reset(self):
            vec.chunks.clear()
        async def add(self, chunks):
            return await vec.add(chunks)

    monkeypatch.setattr(ingest, "LawVectors", RebuildVectors)
    monkeypatch.setattr(ingest, "load_corpus", lambda: NS(articles={}, annexes={}))
    result = await ingest.ingest(fetch=False, graph=False)
    assert result["vectors"]["upload_chunks"] == 1
    assert vec.chunks[0].law_name == (vector_name or "내규")
    assert vec.chunks[0].article_key == uploads.article_key("내규.pdf", arts[0].key)
    assert vec.chunks[0].text == "Policy text."


@pytest.mark.asyncio
async def test_registered_article_ids_do_not_collide_with_other_uploads_or_laws(store):
    from app.core.law.corpus import Corpus
    from app.agents.legal.tools import short_name

    vec = FakeVectors()
    st = NS(law_tools=NS(vectors=vec))
    names = ["내 규.pdf", "내규.pdf", "전자금융거래법.pdf", "내규 .pdf"]
    for i, name in enumerate(names):
        uploads.save_file(name, _pdf(f"Policy {i}."))
    result = await documents.register(documents.FileNames(file_name=names), st)
    assert not result["failed"]
    docs = uploads.all_docs()
    keys = [docs[n]["articles"][0]["key"] for n in names]
    assert len(set(keys)) == len(names)
    law = uploads.split_articles("전자금융거래법", ["Official law."])[0]
    corpus = Corpus(docs=[], delegations=[], articles={law.key: law}, annexes={})
    assert corpus.get(law.key).paragraphs[0].text == "Official law."
    for i, (name, key) in enumerate(zip(names, keys)):
        assert key != law.key and corpus.get(key).paragraphs[0].text == f"Policy {i}."
        assert short_name(key).endswith("제1조") and docs[name]["doc_name"] in short_name(key)
    await documents.delete_file(documents.FileName(file_name=names[0]), st)
    assert corpus.get(keys[0]) is None
    assert all(corpus.get(k) is not None for k in keys[1:])
    assert corpus.get(law.key).paragraphs[0].text == "Official law."


def test_legacy_article_keys_are_adapted_without_rewriting_store(store):
    import json

    article = uploads.split_articles("내 규", ["Legacy policy."])[0]
    old = json.dumps({"내 규.pdf": {"doc_name": "내 규", "articles": [article.model_dump(mode="json")]}}, ensure_ascii=False)
    uploads.STORE.write_text(old)
    canonical = uploads.document_key("내 규.pdf") + ":1"
    assert uploads.get_article(canonical).paragraphs[0].text == "Legacy policy."
    assert uploads.get_article(canonical).paragraphs[0].key == canonical + ":p0"
    assert uploads.STORE.read_text() == old


class GatedVectors(FakeVectors):
    """add가 gate를 기다리게 해 적재 도중에 다른 요청이 끼어드는 상황을 만든다."""

    def __init__(self):
        super().__init__()
        self.entered, self.gate = asyncio.Event(), asyncio.Event()
        self.gate.set()

    async def add(self, chunks):
        self.entered.set()
        await self.gate.wait()
        return await super().add(chunks)


async def _register_replacement_paused(name: str, old: bytes, new: bytes):
    """old를 등록하고 new를 올린 뒤, new 적재가 벡터 추가에서 멈춘 상태를 돌려준다."""
    vec = GatedVectors()
    st = NS(law_tools=NS(vectors=vec))
    uploads.save_file(name, old)
    await documents.register(documents.FileNames(file_name=[name]), st)
    uploads.save_file(name, new)
    vec.entered.clear()
    vec.gate.clear()
    task =asyncio.create_task(documents.register(documents.FileNames(file_name=[name]), st))
    await vec.entered.wait()
    return vec, st, task


@pytest.mark.asyncio
async def test_delete_during_replacement_register_leaves_nothing(store):
    vec, st, reg = await _register_replacement_paused("삭제.pdf", _pdf("Old."), _pdf("New."))
    delete = asyncio.create_task(documents.delete_file(documents.FileName(file_name="삭제.pdf"), st))
    await asyncio.sleep(0)
    vec.gate.set()
    result, deleted = await reg, await delete
    assert result["registered"] and not result["failed"] and deleted == {"deleted": "삭제.pdf"}
    assert uploads.all_docs() == {} and vec.chunks == [] and list((store / "files").iterdir()) == []


@pytest.mark.asyncio
async def test_concurrent_registers_of_replacement_keep_one_generation(store):
    vec, st, first = await _register_replacement_paused("동시.pdf", _pdf("Old."), _pdf("New."))
    second = asyncio.create_task(documents.register(documents.FileNames(file_name=["동시.pdf"]), st))
    await asyncio.sleep(0)
    vec.gate.set()
    for result in (await first, await second):
        assert result["registered"] and not result["failed"]
    active = uploads.all_docs()["동시.pdf"]["vector_name"]
    assert [(c.law_name, c.text) for c in vec.chunks] == [(active, "New.")]
    assert sorted(p.name for p in (store / "files").iterdir()) == ["동시.pdf"]


@pytest.mark.asyncio
async def test_upload_during_register_stays_pending(store):
    from tempfile import SpooledTemporaryFile

    from fastapi import UploadFile

    v2, v3 = _pdf("V2."), _pdf("V3.")
    vec, st, reg = await _register_replacement_paused("교체.pdf", _pdf("V1."), v2)
    body = SpooledTemporaryFile()  # 메모리 파일이라 read가 스레드로 넘어가지 않고 바로 끝난다
    body.write(v3)
    body.seek(0)
    upload = asyncio.create_task(documents.upload_pdf([UploadFile(body, filename="교체.pdf")]))
    await asyncio.sleep(0)
    vec.gate.set()
    assert (await reg)["registered"] and (await upload)["uploaded"] == ["교체.pdf"]
    path = store / "files" / "교체.pdf"
    assert [c.text for c in vec.chunks] == ["V2."] and path.read_bytes() == v2
    assert uploads.pending_path(path).read_bytes() == v3


@pytest.mark.asyncio
async def test_unregistered_upper_case_pdf_is_listed(store):
    uploads.save_file("Policy.PDF", _pdf("Scanned."))
    files = (await documents.show_files())["unique_file_name_and_chunk"]
    assert files == [{"file_name": "Policy.PDF", "chunk_count": 0, "articles": 0}]


def test_split_articles_splits_untitled_heading_before_particle_like_body():
    pages = ["제1조(목적) 이 규정은 내부통제를 정한다.\n제2조 의장은 매년 점검한다.\n제3조 가맹점은 보고한다.\n"
             "제4조 (정의) 용어를 정한다."]
    arts = {a.key: a for a in uploads.split_articles("내규", pages)}
    assert list(arts) == ["내규:1", "내규:2", "내규:3", "내규:4"]
    assert arts["내규:2"].paragraphs[0].text == "의장은 매년 점검한다." and arts["내규:4"].title == "정의"
