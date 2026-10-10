from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from app.domain.models import (ConclusionDraft, CorpProfile, Disclosure, FinancialSnapshot, LegalFinding, Metric,
                               NewsItem, PeriodicFiling, VerificationResult)


class CompanyState(TypedDict, total=False):
    corp: CorpProfile
    filing: PeriodicFiling | None
    snapshot: FinancialSnapshot | None
    history: list[FinancialSnapshot]           # 이익률 추이용 과거 사업연도
    industry: str
    metrics: dict[str, Metric]
    disclosures: list[Disclosure]
    news: list[NewsItem]                       # 선택·분석이 끝난 기사
    news_note: str | None
    news_text: dict[str, str]                  # 기사 ID → 본문(검증용)
    price_chart: dict | None
    triggers: list[str]
    regulatory: list[LegalFinding]             # 트리거됐을 때만
    legal_texts: dict[str, str]                # 조문 key → 본문(검증용)
    draft: ConclusionDraft | None
    verification: VerificationResult | None
    judged: dict[str, Any]                     # 주장 텍스트 → LLM 판정 결과(재검증 생략용)
    attempts: int
    html: str
    errors: Annotated[list[str], operator.add]  # 병렬 노드의 오류 누적
