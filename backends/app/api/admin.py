"""관리 API: 법령 코퍼스 재적재와 초기화. X-Admin-Token 헤더가 .env의 ADMIN_TOKEN과 같아야 한다.

ADMIN_TOKEN이 비어 있으면 관리 API를 모두 막는다(403).
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.core.law import ingest as law_ingest
from app.settings import get_settings

log = logging.getLogger(__name__)
router = APIRouter()


def check_token(token: str | None) -> None:
    want = get_settings().admin_token
    if want is None or not want.get_secret_value():
        raise HTTPException(403, "ADMIN_TOKEN이 설정되지 않아 관리 API를 쓸 수 없습니다")
    if not token or not hmac.compare_digest(token, want.get_secret_value()):
        raise HTTPException(401, "관리 토큰이 맞지 않습니다")


class IngestRequest(BaseModel):
    fetch: bool = True          # false면 법령 API를 부르지 않고 저장본(data/law/corpus.json)으로 다시 넣는다
    graph: bool = True
    vectors: bool = True


@router.post("/legal/ingest", summary="법령 코퍼스 재적재(법령 API → 그래프 + 벡터)")
async def ingest(req: IngestRequest, x_admin_token: str | None = Header(default=None)):
    check_token(x_admin_token)
    try:
        return await law_ingest.ingest(fetch=req.fetch, graph=req.graph, vectors=req.vectors)
    except Exception as e:  # noqa: BLE001
        log.exception("법령 재적재 실패")
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e


@router.post("/legal/reset", summary="그래프·벡터 초기화(저장본과 올린 파일은 남긴다)")
async def reset(x_admin_token: str | None = Header(default=None)):
    check_token(x_admin_token)
    return await law_ingest.reset()
