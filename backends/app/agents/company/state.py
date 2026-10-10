from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from app.domain.models import (CorpProfile, Disclosure, LegalFinding, Metric, NewsItem, PeriodicFiling, ReportDraft,
                               VerificationResult)


class CompanyState(TypedDict, total=False):
    corp: CorpProfile
    today: date                                # 작성일(KST). 비우면 노드가 오늘로 채운다
    filing: PeriodicFiling | None
    collected: Any                             # collect.Collected: 분기 이력·시세·잠정실적·부문·피어
    industry: str
    disclosures: list[Disclosure]
    news: list[NewsItem]                       # 선택·분석이 끝난 기사
    news_note: str | None
    news_text: dict[str, str]                  # 기사 ID → 본문(검증용)
    consensus: Any                             # analysis.Consensus | None
    data: Any                                  # analysis.ReportData: 차트·표·핵심 수치·사실표
    metrics: dict[str, Metric]                 # 사실표(slot_id → Metric)
    triggers: list[str]
    regulatory: list[LegalFinding]             # 트리거됐을 때만
    legal_texts: dict[str, str]                # 조문 key → 본문(검증용)
    draft: ReportDraft | None
    verification: VerificationResult | None
    judged: dict[str, Any]                     # 주장 텍스트 → LLM 판정 결과(재검증 생략용)
    attempts: int
    html: str
    errors: Annotated[list[str], operator.add]  # 병렬 노드의 오류 누적
