"""검증 노드: 추적 불가 숫자, 인용 존재, 근거 인용문, 주장 지지, 투자 권유를 검사한다.

리포트(ReportDraft.claims())와 채팅(FinalAnswer.claims)이 같은 함수를 쓴다. 주장은 Claim.id로 가리킨다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.core import llm
from app.domain.binding import SLOT, display, missing_slots, slots_in, untraceable_numbers
from app.domain.models import Claim, LegalFinding, Metric, NewsItem, VerificationIssue, VerificationResult

ADVICE = re.compile(r"매수|매도|목표\s*주가|목표가|비중\s*(?:확대|축소)|투자\s*추천|사야|팔아야")


@dataclass
class Evidence:
    """이번 실행의 도구 출력. 검증은 이 ID들만 근거로 인정한다."""
    metrics: dict[str, Metric] = field(default_factory=dict)        # slot_id → Metric
    news: dict[str, NewsItem] = field(default_factory=dict)          # "n1" → NewsItem
    news_text: dict[str, str] = field(default_factory=dict)          # "n1" → 본문(또는 스니펫)
    legal: dict[str, str] = field(default_factory=dict)              # 조문 key → 본문
    tokens: set[str] = field(default_factory=set)                    # 리포트에 보이는 숫자 토큰(사실표·차트 제목)

    @classmethod
    def build(cls, metrics: dict[str, Metric], news: list[NewsItem], news_text: dict[str, str],
              regulatory: list[LegalFinding], legal_texts: dict[str, str] | None = None) -> "Evidence":
        return cls(metrics={m.slot_id: m for m in metrics.values()}, news={n.id: n for n in news},
                   news_text=news_text, legal=dict(legal_texts or {}))


class _Verdict(BaseModel):
    claim_id: str
    verdict: Literal["support", "partial", "unrelated"]
    missing: str | None


class _Verdicts(BaseModel):
    verdicts: list[_Verdict]


class _Advice(BaseModel):
    claim_id: str
    is_advice: bool


class _AdviceList(BaseModel):
    items: list[_Advice]


SUPPORT_SYSTEM = """너는 기업 분석 리포트의 사실 검증자다. 주장마다 함께 준 근거(지표 값, 기사 요약·인용문·본문 조각, 조문)만 보고 판정한다.
support = 근거가 주장 전체를 뒷받침, partial = 일부만 뒷받침(과장, 근거에 없는 원인·전망 추가 포함),
unrelated = 근거가 주장과 무관하거나 근거가 없음. partial이면 missing에 근거에 없는 부분을 한 문장으로 적는다.
{{…}} 슬롯은 함께 준 지표 값으로 읽는다. 일반적인 해석·모니터링 제안은 근거 지표와 맞으면 support다."""

ADVICE_SYSTEM = """주장이 특정 종목의 매수·매도·보유 권유, 목표주가 제시, 비중 확대·축소 권유이면 is_advice=true다.
기사나 증권사가 낸 목표주가를 사실로 전하는 문장은 권유가 아니다(false)."""


def _sources_for(c: Claim, ev: Evidence) -> list[str]:
    out = []
    for nid in c.news_ids:
        if nid in ev.news_text:
            out.append(ev.news_text[nid])
        n = ev.news.get(nid)
        if n and n.analysis:
            out += n.analysis.summary + [n.analysis.evidence]
    for k in c.legal_keys:
        if k in ev.legal:
            out.append(ev.legal[k])
    return out


def deterministic_checks(claims: list[Claim], ev: Evidence) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    for c in claims:
        for s in missing_slots(c.text, ev.metrics):
            issues.append(VerificationIssue(claim_id=c.id, type="missing_slot", detail=f"없는 슬롯 {{{{{s}}}}}"))
        bad = untraceable_numbers(c.text, _sources_for(c, ev), ev.tokens)
        if bad:
            issues.append(VerificationIssue(claim_id=c.id, type="untraceable_number",
                                            detail=f"슬롯·출처 밖 숫자: {', '.join(bad)}"))
        cited_news = set(c.news_ids) | set(re.findall(r"\[(n\d+)\]", c.text))
        missing = [m for m in c.metric_ids if m not in ev.metrics] + \
                  [n for n in cited_news if n not in ev.news] + [k for k in c.legal_keys if k not in ev.legal]
        if missing:
            issues.append(VerificationIssue(claim_id=c.id, type="missing_citation",
                                            detail=f"이번 실행에 없는 출처 ID: {', '.join(missing)}"))
        for nid in c.news_ids:
            n = ev.news.get(nid)
            if n and n.analysis and nid in ev.news_text:
                from app.agents.news.pipeline import evidence_ok
                if not evidence_ok(n.analysis.evidence, ev.news_text[nid]):
                    issues.append(VerificationIssue(claim_id=c.id, type="evidence",
                                                    detail=f"{nid}의 근거 인용문이 본문에 없다"))
    return issues


def _excerpt_for(claim: str, body: str, width: int = 360, limit: int = 1800) -> str:
    """주장에 나온 숫자·명사 주변의 기사 본문 조각(판정 근거). 못 찾으면 본문 앞부분."""
    if not body:
        return ""
    keys = {t for t in re.findall(r"[0-9][0-9,.]*|[가-힣A-Za-z]{2,}", SLOT.sub(" ", claim)) if len(t) >= 2}
    spans = []
    for k in keys:
        i = body.find(k)
        if i >= 0:
            spans.append((max(0, i - width // 2), min(len(body), i + len(k) + width // 2)))
    if not spans:
        return body[:limit]
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return " … ".join(body[a:b] for a, b in merged)[:limit]


def _evidence_block(c: Claim, ev: Evidence) -> dict:
    metrics = {s: f"{ev.metrics[s].label}({ev.metrics[s].period}) = {display(ev.metrics[s])}"
               for s in set(slots_in(c.text)) | set(c.metric_ids) if s in ev.metrics}
    news = {}
    for nid in set(c.news_ids) | set(re.findall(r"\[(n\d+)\]", c.text)):
        n = ev.news.get(nid)
        if n:
            news[nid] = {"title": n.title, "summary": n.analysis.summary if n.analysis else [],
                         "evidence": n.analysis.evidence if n.analysis else None,
                         "excerpt": _excerpt_for(c.text, ev.news_text.get(nid, ""))}
    legal = {k: ev.legal[k][:1500] for k in c.legal_keys if k in ev.legal}
    return {"claim_id": c.id, "claim": c.text, "metrics": metrics, "news": news, "legal": legal}


async def support_check(claims: list[Claim], ev: Evidence) -> list[VerificationIssue]:
    if not claims:
        return []
    blocks = [_evidence_block(c, ev) for c in claims]
    ids = [c.id for c in claims]

    def check(v: _Verdicts) -> str | None:
        return None if sorted(x.claim_id for x in v.verdicts) == sorted(ids) else "모든 claim_id를 한 번씩 판정"

    out = await llm.structured(_Verdicts, [{"role": "user", "content": "\n".join(
        json.dumps(b, ensure_ascii=False) for b in blocks)}], system=SUPPORT_SYSTEM, effort="medium",
        task="verify.support", validate=check)
    issues = []
    for v in out.verdicts:
        if v.verdict == "unrelated":
            issues.append(VerificationIssue(claim_id=v.claim_id, type="unsupported", detail="근거와 무관"))
        elif v.verdict == "partial":
            issues.append(VerificationIssue(claim_id=v.claim_id, type="partial",
                                            detail=v.missing or "근거가 일부만 지지"))
    return issues


async def advice_check(claims: list[Claim]) -> list[VerificationIssue]:
    flagged = [c for c in claims if ADVICE.search(c.text)]
    if not flagged:
        return []
    out = await llm.structured(_AdviceList, [{"role": "user", "content": "\n".join(
        json.dumps({"claim_id": c.id, "claim": c.text}, ensure_ascii=False) for c in flagged)}],
        system=ADVICE_SYSTEM, effort="medium", task="verify.advice")
    return [VerificationIssue(claim_id=a.claim_id, type="investment_advice", detail="투자 권유 표현")
            for a in out.items if a.is_advice]


async def verify(claims: list[Claim], ev: Evidence, *, judged: dict[str, list[VerificationIssue]] | None = None
                 ) -> VerificationResult:
    """judged: 이미 LLM 판정을 받은 주장 텍스트 → 결과. 바뀌지 않은 주장은 다시 판정하지 않는다."""
    judged = judged if judged is not None else {}
    issues = deterministic_checks(claims, ev)
    todo = [c for c in claims if c.text not in judged]
    if todo:
        found = await support_check(todo, ev) + await advice_check(todo)
        for c in todo:
            judged[c.text] = [i for i in found if i.claim_id == c.id]
    for c in claims:
        issues += [i.model_copy(update={"claim_id": c.id}) for i in judged.get(c.text, [])]
    return VerificationResult(passed=not issues, issues=issues)


def finalize(claims: list[Claim], result: VerificationResult) -> list[Claim]:
    """재작성 2회 뒤에도 실패한 주장: 사유가 '부분'뿐이면 "(근거 불충분)"을 붙여 남기고, 나머지는 지운다."""
    by_claim: dict[str, set[str]] = {}
    for i in result.issues:
        by_claim.setdefault(i.claim_id, set()).add(i.type)
    out = []
    for c in claims:
        types = by_claim.get(c.id)
        if not types:
            out.append(c)
        elif types == {"partial"}:
            out.append(c.model_copy(update={"text": c.text.rstrip() + " (근거 불충분)"}))
    return out
