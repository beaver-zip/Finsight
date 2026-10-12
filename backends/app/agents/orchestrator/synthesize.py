"""답변 합성: 하위 결과를 Claim 목록으로 합치고(구조화 출력), 마크다운과 인용 목록으로 렌더링한다.

- 법령 과업 하나뿐이면 법령 답변의 점(LegalPoint)을 그대로 Claim으로 쓴다(Claude를 다시 부르지 않는다).
- 나머지는 Haiku가 하위 결과만 근거로 주장 목록을 쓴다. 재무 숫자는 사실표 id를 {{id}} 슬롯으로 쓴다.
- 재작성(revise)은 같은 함수에 검증 지적을 함께 넘긴다.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from app.core import llm
from app.domain.binding import render
from app.domain.models import Citation, Claim, Metric, SubResult, VerificationIssue


class _ClaimOut(BaseModel):
    text: str
    metric_ids: list[str]
    news_ids: list[str]
    legal_keys: list[str]


class _Synth(BaseModel):
    claims: list[_ClaimOut]


SYNTH_SYSTEM = """너는 금융 리서치 챗봇이다. 사용자 질문에 함께 준 하위 결과(기업 리포트 사실표·본문, 기사, 법령 답변)만 근거로 한국어로 답한다.

[형식]
- claims는 답변 문단 목록이다. 첫 문단은 질문에 대한 결론 1~2문장, 이어서 근거 문단 2~5개. 문단은 2~4문장.
- 존댓말(~습니다)로 쓴다. 머리말, 소제목, 목록 기호, 맺음말("도움이 되셨길" 등)을 쓰지 않는다.
- 작업 과정을 말하지 않는다: "사실표", "자료", "하위 결과", "제공된", "기사 보도를 기준으로" 같은 말을 쓰지 않는다.
- "~구조", "~구간", "~셈", "~가능성" 같은 해석용 맺음말과 "주목할 필요", "지켜볼 필요"를 쓰지 않는다.

[근거]
- 재무·시세 숫자는 직접 쓰지 않고 사실표 id를 {{id}}로 쓴다(예: "영업이익은 {{op.2Q26}}"). 쓴 id는 metric_ids에 넣는다.
- 기사에서 온 사실은 문장 끝에 [n1]처럼 기사 id를 붙이고 news_ids에 넣는다. 기사 숫자는 기사 표기 그대로 쓴다.
- 법령 답변에서 온 내용은 legal_keys에 그 조문 key(예: "전자금융거래법:21-2")를 넣는다.
- 자료에 없는 숫자·사실·전망을 쓰지 않는다. 매수·매도·목표주가 같은 투자 권유를 쓰지 않는다.
- 하위 결과가 실패했거나 비어 있으면 그 부분은 확인하지 못했다고 한 문장으로 쓴다."""


def _prompt(question: str, results: list[SubResult], issues: list[VerificationIssue] | None,
            prev: list[Claim] | None) -> str:
    parts = [f"질문: {question}", ""]
    for r in results:
        head = {"legal": "법령 답변", "company": "기업 리포트", "news": "기사"}[r.agent]
        if r.error:
            parts.append(f"[{head}] 실패: {r.error}")
        else:
            parts.append(f"[{head}]\n{r.content}")
            if r.agent == "legal" and r.citations:
                parts.append("legal_keys에 쓸 수 있는 조문 key(이 값 그대로): " + ", ".join(
                    f"{c.id}({c.label})" for c in r.citations))
            if r.agent == "legal" and r.claims:
                parts.append("법령 답변 문단별 근거 조문 key: " + "; ".join(
                    f"{c.text[:40]}… → {', '.join(c.legal_keys)}" for c in r.claims))
        parts.append("")
    if issues and prev:
        parts.append("직전 답변과 검증 지적(지적받은 문단을 고치거나 빼고, 나머지는 그대로 둔다):")
        by = {c.id: c for c in prev}
        for i in issues:
            if i.claim_id in by:
                parts.append(f"- 문단 {i.claim_id}: {by[i.claim_id].text}\n  지적({i.type}): {i.detail}")
    return "\n".join(parts)


async def synthesize(question: str, results: list[SubResult], *, issues: list[VerificationIssue] | None = None,
                     prev: list[Claim] | None = None) -> list[Claim]:
    out = await llm.structured(_Synth, [{"role": "user", "content": _prompt(question, results, issues, prev)}],
                               system=SYNTH_SYSTEM, effort="medium", task="chat.synthesize",
                               validate=lambda s: None if 1 <= len(s.claims) <= 8 else "claims는 1~8개")
    return [Claim(id=f"c{i}", text=c.text, metric_ids=c.metric_ids, news_ids=c.news_ids,
                  legal_keys=[norm_key(k) for k in c.legal_keys]) for i, c in enumerate(out.claims, 1)]


def norm_key(k: str) -> str:
    """'전자금융거래법 시행령:11-4' → '전자금융거래법시행령:11-4'(법령명의 공백 제거)."""
    law, _, rest = k.partition(":")
    return re.sub(r"\s+", "", law) + (":" + rest.strip() if rest else "")


def to_markdown(claims: list[Claim], metrics: dict[str, Metric], citations: list[Citation]) -> str:
    """슬롯을 표시값으로 바꾸고, 기사 [n1]·조문은 끝의 출처 목록 번호로 바꾼다."""
    used: list[str] = []
    by_id = {c.id: c for c in citations}

    def ref(cid: str) -> str:
        if cid not in by_id:
            return ""
        if cid not in used:
            used.append(cid)
        return f"[{used.index(cid) + 1}]"

    paras = []
    for c in claims:
        t = render(c.text, metrics)
        t = re.sub(r"\s*\[(n\d+)\]", lambda m: ref(m.group(1)), t)
        marks = "".join(ref(k) for k in c.legal_keys)
        paras.append(t.rstrip() + marks)
    body = "\n\n".join(paras)
    if used:
        body += "\n\n### 출처\n" + "\n".join(
            f"{i}. [{by_id[k].label}]({by_id[k].url})" if by_id[k].url else f"{i}. {by_id[k].label}"
            for i, k in enumerate(used, 1))
    return body
