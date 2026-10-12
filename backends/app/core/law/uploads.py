"""사용자가 올린 PDF(내규 등): 저장, 조문 단위 분할, 목록·삭제.

- 파일은 data/uploads/files/에, 나눈 조문은 data/uploads/articles.json에 둔다(조문 조회용). 벡터는 Weaviate에
  level="내규", source="upload"로 넣는다. 내규는 형식이 제각각이라 참조 해석기·인용 그래프에는 넣지 않는다(설계 결정).
- 조문 key는 "<문서명(공백 제거)>:<조 번호>"다. 조 머리("제N조(제목)")가 없는 문서는 쪽 단위로 나누고 쪽 번호를 조 번호로 쓴다.
"""
from __future__ import annotations

import json
import re
import threading
from functools import lru_cache
from pathlib import Path

from app.core.law.parser import CIRCLED, clean, key_name, split_paragraphs
from app.domain.models import Article, Paragraph
from app.settings import BACKEND_DIR

UPLOAD_DIR = BACKEND_DIR / "data" / "uploads"
FILES = UPLOAD_DIR / "files"
STORE = UPLOAD_DIR / "articles.json"
MAX_BYTES = 20 * 1024 * 1024
_HEAD = re.compile(r"(?m)^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^)\n]{1,60})\))?")
_lock = threading.Lock()


class UploadError(ValueError):
    pass


def safe_name(name: str) -> str:
    """경로 성분을 떼고, 파일 이름에 쓸 수 없는 글자를 바꾼다. PDF만 받는다."""
    base = Path(name or "").name.strip()
    base = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", base)
    if not base.lower().endswith(".pdf") or base in (".pdf", ""):
        raise UploadError(f"PDF 파일만 올릴 수 있습니다: {name}")
    if len(base) > 150:
        raise UploadError(f"파일 이름이 너무 깁니다: {name}")
    return base


def save_file(name: str, data: bytes) -> Path:
    fname = safe_name(name)
    if len(data) > MAX_BYTES:
        raise UploadError(f"{fname}: 20MB를 넘습니다")
    if not data.startswith(b"%PDF"):
        raise UploadError(f"{fname}: PDF 형식이 아닙니다")
    FILES.mkdir(parents=True, exist_ok=True)
    path = FILES / fname
    path.write_bytes(data)
    return path


def file_path(name: str) -> Path:
    path = FILES / safe_name(name)
    if not path.is_file():
        raise UploadError(f"올린 파일이 없습니다: {name}")
    return path


def pdf_pages(path: Path) -> list[str]:
    import fitz  # PyMuPDF

    with fitz.open(path) as doc:
        return [p.get_text("text") for p in doc]


def split_articles(doc_name: str, pages: list[str]) -> list[Article]:
    """조 머리("제N조(제목)")로 나누고, 없으면 쪽 단위로 나눈다. 같은 조 번호가 다시 나오면 앞 조문에 붙인다."""
    law = key_name(doc_name)
    text = "\n".join(pages)
    heads = list(_HEAD.finditer(text))
    out: dict[str, Article] = {}
    if len(heads) >= 2:
        for i, m in enumerate(heads):
            body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
            no, branch = int(m.group(1)), int(m.group(2) or 0)
            akey = f"{law}:{no}" + (f"-{branch}" if branch else "")
            paras = split_paragraphs(akey, body) if any(c in body for c in CIRCLED) else \
                [Paragraph(key=f"{akey}:p0", no=0, text=clean(body))]
            paras = [p for p in paras if p.text]
            if not paras:
                continue
            if akey in out:            # 본문에서 다른 조를 인용한 줄이 머리로 잡힌 경우: 앞 조문에 붙인다
                out[akey].paragraphs += paras
                continue
            out[akey] = Article(key=akey, law_id="upload", law_name=doc_name, level="내규", no=no, branch=branch,
                                title=(m.group(3) or "").strip(), paragraphs=paras, source="upload")
    if not out:
        for i, page in enumerate(pages, 1):
            t = clean(page)
            if t:
                akey = f"{law}:{i}"
                out[akey] = Article(key=akey, law_id="upload", law_name=doc_name, level="내규", no=i,
                                    title=f"{i}쪽", paragraphs=[Paragraph(key=f"{akey}:p0", no=0, text=t[:4000])],
                                    source="upload")
    return list(out.values())


@lru_cache(maxsize=1)
def _load() -> dict[str, dict]:
    """{파일 이름: {"doc_name", "articles": [...]}}"""
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def all_docs() -> dict[str, dict]:
    return _load()


def get_article(key: str) -> Article | None:
    for d in _load().values():
        for a in d["articles"]:
            if a["key"] == key:
                return Article.model_validate(a)
    return None


def put(file_name: str, doc_name: str, articles: list[Article], chunk_count: int) -> None:
    with _lock:
        data = dict(_load())
        data[file_name] = {"doc_name": doc_name, "chunk_count": chunk_count,
                           "articles": [a.model_dump(mode="json") for a in articles]}
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False))
        _load.cache_clear()


def remove(file_name: str) -> dict | None:
    with _lock:
        data = dict(_load())
        d = data.pop(file_name, None)
        STORE.write_text(json.dumps(data, ensure_ascii=False))
        _load.cache_clear()
    p = FILES / file_name
    if p.is_file():
        p.unlink()
    return d
