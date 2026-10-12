"""Pydantic 도메인 모델. 외부 I/O 없이 domain·agents·api가 공유한다."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

# ---------- 기업·공시 ----------
ReprtCode = Literal["11013", "11012", "11014", "11011"]  # 1분기, 반기, 3분기, 사업보고서
IndustryGroup = Literal["general", "financial"]


class CorpProfile(BaseModel):
    corp_code: str
    corp_name: str
    stock_code: str | None = None
    corp_cls: str | None = None        # Y 유가, K 코스닥, N 코넥스, E 기타
    induty_code: str | None = None
    acc_mt: int | None = None          # 결산월

    @property
    def listed(self) -> bool:
        return bool(self.stock_code)


class PeriodicFiling(BaseModel):
    rcept_no: str
    report_nm: str
    rcept_dt: date
    bsns_year: int
    reprt_code: ReprtCode


class Disclosure(BaseModel):
    rcept_no: str
    report_nm: str
    rcept_dt: date
    pblntf_ty: str

    @property
    def url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"


# ---------- 재무 ----------
class FinancialLine(BaseModel):
    sj_div: Literal["BS", "IS", "CIS", "CF", "SCE"]
    account_id: str                    # XBRL 표준 ID. 비표준 계정은 "-표준계정코드 미사용-"
    account_nm: str
    ord: int
    thstrm_amount: Decimal | None = None      # 당기 (분·반기 손익은 3개월)
    thstrm_add_amount: Decimal | None = None  # 당기 누적
    frmtrm_amount: Decimal | None = None      # 전기 (재무상태표는 전기말)
    frmtrm_q_amount: Decimal | None = None    # 전기 동분기 3개월
    frmtrm_add_amount: Decimal | None = None  # 전기 누적
    currency: str = "KRW"


class FinancialSnapshot(BaseModel):
    corp_code: str
    rcept_no: str                      # 원문 링크와 모든 숫자의 출처
    bsns_year: int
    reprt_code: ReprtCode
    fs_div: Literal["CFS", "OFS"]
    period_label: str                  # "2025년 반기 누적(1–6월)"
    lines: list[FinancialLine]


class SourceRef(BaseModel):
    rcept_no: str
    sj_div: str
    account_id: str
    account_nm: str
    column: str                        # "thstrm_add_amount" 등
    amount: Decimal


class Metric(BaseModel):
    id: str                            # "op_margin"
    label: str
    period: str                        # "2025H1"(누적), "2024FY"
    value: Decimal | None
    unit: Literal["KRW", "%", "x", "%p", "n", "text"]
    formula: str
    inputs: list[SourceRef] = Field(default_factory=list)
    note: str | None = None            # "흑자 전환", "연환산", "자본잠식"
    text: str | None = None            # 리포트 v2: 표시 문자열을 계산 단계에서 정한 사실(조원·배·%p 등)
    source: str | None = None          # 리포트 v2: 출처 설명(공시 접수번호·시세 등)

    @property
    def slot_id(self) -> str:
        return f"{self.id}.{self.period}"


# ---------- 결론·검증 ----------
class Claim(BaseModel):
    text: str                          # 슬롯·인용을 포함한 문장
    id: str                            # "threats.2"
    metric_ids: list[str] = Field(default_factory=list)
    news_ids: list[str] = Field(default_factory=list)
    legal_keys: list[str] = Field(default_factory=list)


# ---------- 리포트 v2 문장 ----------
class Headline(BaseModel):
    """리포트 제목·절 제목. 숫자를 쓰면 {{슬롯}}, 근거 사실의 id는 metric_ids에."""
    text: str
    metric_ids: list[str] = Field(default_factory=list)
    news_ids: list[str] = Field(default_factory=list)


class Block(BaseModel):
    """소제목(한 줄 주장) + 본문 2–4문장. 숫자는 {{슬롯}}, 기사에서 온 사실은 [n1]."""
    id: str                            # "earnings_a.1"
    head: str
    text: str
    metric_ids: list[str] = Field(default_factory=list)
    news_ids: list[str] = Field(default_factory=list)


class ReportDraft(BaseModel):
    title: Headline
    summary: list[Block]
    earnings_head: Headline
    earnings_a: list[Block]            # 최근 분기 실적·컨센서스 → '분기 영업이익' 차트
    earnings_b: list[Block]            # 이익의 원인·부문 → '부문' 차트, 분기 표
    valuation_head: Headline
    valuation_a: list[Block]           # PBR 위치 → 'PBR 밴드' 차트
    valuation_b: list[Block]           # PER·동종 업종 → '피어' 차트, 표
    cash_head: Headline
    cash_a: list[Block]                # 잉여현금흐름·순현금·운전자본 → '현금' 차트
    cash_b: list[Block]                # 주주환원·주식 수 → 현금흐름 표
    market_head: Headline
    market_a: list[Block]              # 최근 주가 반응 → '1년 주가' 차트
    market_b: list[Block]              # 잠정실적 발표 반응 이력 → '발표 반응' 차트
    market_c: list[Block]              # 시장의 시각(기사)

    GROUPS: ClassVar[tuple[str, ...]] = ("summary", "earnings_a", "earnings_b", "valuation_a", "valuation_b",
                                         "cash_a", "cash_b", "market_a", "market_b", "market_c")
    HEADS: ClassVar[tuple[str, ...]] = ("title", "earnings_head", "valuation_head", "cash_head", "market_head")

    def blocks(self) -> list[Block]:
        return [b for g in self.GROUPS for b in getattr(self, g)]

    def claims(self) -> list[Claim]:
        """검증 단위: 제목·절 제목은 id가 필드 이름, 블록은 블록 id."""
        out = [Claim(id=h, text=getattr(self, h).text, metric_ids=getattr(self, h).metric_ids,
                     news_ids=getattr(self, h).news_ids) for h in self.HEADS]
        out += [Claim(id=b.id, text=f"{b.head}\n{b.text}", metric_ids=b.metric_ids, news_ids=b.news_ids)
                for b in self.blocks()]
        return out


class VerificationIssue(BaseModel):
    claim_id: str
    type: Literal["untraceable_number", "missing_slot", "missing_citation", "evidence",
                  "unsupported", "partial", "investment_advice", "uncited_legal", "style"]
    detail: str


class VerificationResult(BaseModel):
    passed: bool
    issues: list[VerificationIssue] = Field(default_factory=list)


# ---------- 뉴스 ----------
Aspect = Literal["실적·재무", "투자·생산", "제품·기술", "시장·경쟁", "리스크·규제", "지배구조·자본"]
ASPECTS: tuple[str, ...] = ("실적·재무", "투자·생산", "제품·기술", "시장·경쟁", "리스크·규제", "지배구조·자본")
ASPECT_GROUPS: tuple[tuple[str, str], ...] = (
    ("실적·재무", "투자·생산"), ("제품·기술", "시장·경쟁"), ("리스크·규제", "지배구조·자본"))


class QueryPlan(BaseModel):
    aspect: Aspect                     # 묶음의 첫 측면
    query: str
    must_include: list[str]            # 기업명·약칭. 제목·스니펫에 하나도 없는 결과는 버린다


class ArticleJudgment(BaseModel):
    article_id: str
    relevance: Literal[0, 1, 2]        # 0 다른 기업이거나 이름만 나옴, 1 다루지만 주제가 아님, 2 기사의 주체
    aspect: Aspect | None
    materiality: Literal[1, 2, 3]      # 1 일상 보도, 2 실적·사업에 영향 가능, 3 실적·주가에 직접 영향
    event_type: str
    disclosure_rcept_no: str | None


class ArticleAnalysis(BaseModel):
    summary: list[str]                 # 3문장
    impact_direction: Literal["positive", "negative", "neutral"]
    impact_horizon: Literal["short", "long"]
    impact_channel: Literal["매출", "비용", "규제", "평판", "자본"]
    evidence: str                      # 본문에서 그대로 인용한 한 문장


class NewsItem(BaseModel):
    id: str                            # "n1"
    title: str
    url: str
    source: str
    published_at: datetime | None
    aspect: Aspect | None = None
    materiality: int | None = None
    score: float | None = None
    cluster_size: int = 1
    analysis: ArticleAnalysis | None = None


class NewsFindings(BaseModel):
    summary: str
    items: list[NewsItem]


# ---------- 법령 ----------
class PdfLocation(BaseModel):
    file_name: str
    page: int


class Paragraph(BaseModel):
    key: str                           # "전자금융거래법시행령:11-4:p4"
    no: int
    text: str


class Article(BaseModel):
    key: str                           # "전자금융거래법시행령:11-4"
    law_id: str
    law_name: str
    level: Literal["법률", "시행령", "고시", "세칙", "내규"]
    no: int
    branch: int = 0
    title: str
    chapter: str | None = None
    section: str | None = None
    paragraphs: list[Paragraph] = Field(default_factory=list)
    effective_date: date | None = None
    source: Literal["lawgo", "upload"] = "lawgo"
    pdf: PdfLocation | None = None

    @property
    def display_title(self) -> str:
        no = f"제{self.no}조" + (f"의{self.branch}" if self.branch else "")
        return f"{self.law_name} {no}({self.title})" if self.title else f"{self.law_name} {no}"


class Annex(BaseModel):
    key: str                           # "전자금융거래법시행령:annex-1"
    law_name: str = ""
    title: str
    text: str
    related_article: str | None = None


class ArticleHit(BaseModel):
    article: Article | Annex
    score: float
    hops: int = 0
    path: list[str] = Field(default_factory=list)


class CitedSpan(BaseModel):
    source: str                        # 조문 key
    paragraph_index: int | None
    cited_text: str


class LegalPoint(BaseModel):
    text: str
    citations: list[CitedSpan] = Field(default_factory=list)


class LegalAnswer(BaseModel):
    answerable: bool
    points: list[LegalPoint] = Field(default_factory=list)
    paths: list[list[str]] = Field(default_factory=list)
    fallback: list[str] = Field(default_factory=list)   # 거절 시 상위 조문 key
    notes: list[str] = Field(default_factory=list)      # 시행일 기준, 코퍼스 밖 참조 법령 등 답변 끝에 붙일 말


class LegalFinding(BaseModel):
    question: str
    answer: LegalAnswer


# ---------- 오케스트레이터·API ----------
class Citation(BaseModel):
    kind: Literal["article", "annex", "filing", "news", "metric"]
    id: str
    label: str
    url: str | None = None
    pdf: PdfLocation | None = None


class SubResult(BaseModel):
    agent: Literal["legal", "company", "news"]
    content: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    error: str | None = None


class CompanyReport(BaseModel):
    """/report/{corp_code}/json 응답. data는 analysis.ReportData.to_json()(차트·표·핵심 수치)."""
    corp: CorpProfile
    filing: PeriodicFiling | None
    latest: str | None = None                    # "3Q26 잠정" 등 최근 실적 기준
    fs_div: Literal["CFS", "OFS"] | None = None
    metrics: dict[str, Metric] = Field(default_factory=dict)   # 사실표(slot_id → Metric)
    data: dict | None = None
    draft: ReportDraft | None = None
    verification: VerificationResult | None = None
    disclosures: list[Disclosure] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    news_note: str | None = None
    regulatory: list[LegalFinding] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
