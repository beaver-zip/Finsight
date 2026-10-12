"""/financial/corp_list: 기업명 키워드 검색(corpCode.xml 1일 캐시)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.dart import corp as dart_corp
from app.core.dart.client import DartClient
from app.deps import get_dart

router = APIRouter()


class KeywordRequest(BaseModel):
    keyword: str


class CorplistResponse(BaseModel):
    data: list[dict]
    success: bool
    err_msg: str


@router.get("/health")
async def health():
    return {"message": "Financial router is active"}


@router.post("/corp_list", response_model=CorplistResponse)
async def corp_list(req: KeywordRequest, dart: DartClient = Depends(get_dart)):
    try:
        rows = await dart_corp.search_keyword(dart, req.keyword)
    except Exception as e:  # noqa: BLE001
        return CorplistResponse(data=[{}], success=False, err_msg=f"기업 목록을 가져오지 못했습니다: {e}")
    if not rows:
        return CorplistResponse(data=[{}], success=False, err_msg="keyword와 일치하는 기업이 없습니다.")
    return CorplistResponse(data=rows, success=True, err_msg="")
