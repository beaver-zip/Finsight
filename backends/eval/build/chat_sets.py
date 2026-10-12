"""routing(100), composite_tasks(10), multiturn(10) 초안. Haiku 5.5가 쓰고 사람이 검수한다(source="llm_draft").

- routing: 의도별 20문항 = 기본 10(그중 모호·혼합 4) + 기본 문항마다 바꿔 쓴 1문항. split은 의도로 층화(dev 30%).
- composite_tasks: 기업+법령 5, 기업+뉴스 5. 과업마다 체크리스트 3~5항목. 기업은 news_companies 8개에서.
- multiturn: 3~5턴 대화 10개. 2턴부터 질문마다 정답 독립 질문(standalone)과 의도. 전부 test.
사용: cd backends && PYTHONPATH=. python -m eval.build.chat_sets   (비용 약 0.03달러)
"""
from __future__ import annotations

import asyncio
from collections import Counter

from pydantic import BaseModel

from app.core import llm
from eval.common import DATASETS, assign_splits, load_jsonl, save_jsonl

INTENTS = {
    "legal": "전자금융 법령(전자금융거래법·시행령·전자금융감독규정·시행세칙)의 의무·요건·절차·제재를 묻는 질문",
    "company": "특정 상장사의 실적·재무·밸류에이션·주가·주주환원을 묻는 질문",
    "news": "특정 기업의 최근 뉴스·이슈·동향을 묻는 질문",
    "composite": "기업 실적·뉴스와 전자금융 법령을 함께 물어 둘 이상의 분석이 필요한 질문",
    "chat": "인사, 챗봇 기능 문의, 위 범위 밖의 잡담·질문",
}


class _Q(BaseModel):
    question: str
    paraphrase: str
    ambiguous: bool


class _Qs(BaseModel):
    items: list[_Q]


class _Task(BaseModel):
    question: str
    checklist: list[str]


class _Turn(BaseModel):
    user: str
    standalone: str
    intent: str


class _Conv(BaseModel):
    turns: list[_Turn]


async def routing(companies: list[str]) -> list[dict]:
    rows = []
    for intent, desc in INTENTS.items():
        qs = await llm.structured(_Qs, [{"role": "user", "content":
            f"금융 리서치 챗봇의 의도 분류 평가셋을 만든다. 의도 '{intent}' = {desc}.\n"
            f"이 의도에 해당하는 한국어 사용자 질문 10개를 써라. 그중 4개는 다른 의도와 헷갈리기 쉬운 모호·혼합 문항으로 하고 "
            f"ambiguous=true로 표시한다. 문항마다 같은 뜻을 다른 말투·표현으로 바꿔 쓴 paraphrase를 붙인다.\n"
            f"기업이 필요하면 다음 중에서 고른다: {', '.join(companies)}, 삼성전자, KB금융, 카카오뱅크."}],
            effort="low", task="eval.routing", validate=lambda q: None if len(q.items) >= 10 else "10개 이상")
        for i, q in enumerate(qs.items[:10], 1):
            for j, text in enumerate((q.question, q.paraphrase)):
                rows.append({"id": f"rt-{intent}-{i:02d}{'p' if j else ''}", "question": text, "intent": intent,
                             "ambiguous": q.ambiguous, "paraphrase_of": f"rt-{intent}-{i:02d}" if j else None,
                             "source": "llm_draft"})
    # 원문과 바꿔 쓴 문항이 같은 분할에 들도록 원문 id로 층화·분할한다
    base = assign_splits([r for r in rows if not r["paraphrase_of"]], strata=lambda r: r["intent"])
    split = {r["id"]: r["split"] for r in base}
    for r in rows:
        r["split"] = split[r["paraphrase_of"] or r["id"]]
    return rows


async def composite(companies: list[str]) -> list[dict]:
    rows = []
    kinds = [("company+legal", "그 기업의 최근 실적과, 그 기업 업종에 적용되는 전자금융 법령상 의무를 함께 묻는"),
             ("company+news", "그 기업의 최근 실적과 최근 뉴스·이슈를 함께 묻는")]
    n = 0
    for kind, desc in kinds:
        for corp in companies[:5]:
            n += 1
            t = await llm.structured(_Task, [{"role": "user", "content":
                f"기업 '{corp}'에 대해 {desc} 한국어 질문 하나와, 좋은 답이 반드시 갖춰야 할 체크리스트 3~5항목을 써라. "
                "체크리스트는 답만 보고 예·아니오로 판정할 수 있게 쓴다(예: '최근 분기 영업이익을 숫자로 제시한다', "
                "'근거 조문을 1개 이상 인용한다'). 특정 수치를 정답으로 적지 않는다."}],
                effort="low", task="eval.composite", validate=lambda x: None if 3 <= len(x.checklist) <= 5 else "3~5")
            rows.append({"id": f"ct-{n:02d}", "kind": kind, "company": corp, "question": t.question,
                         "checklist": t.checklist, "split": "test", "source": "llm_draft"})
    return rows


async def multiturn(companies: list[str]) -> list[dict]:
    rows = []
    themes = ["법령 질문 뒤 '그럼 위반하면?' 같은 후속 질문", "기업 실적 질문 뒤 '거기 뉴스는?' 같은 지시어 질문",
              "법령 → 기업 → 법령으로 주제가 바뀌는 대화", "기업 두 곳을 번갈아 묻는 대화", "인사 뒤 실제 질문으로 이어지는 대화"]
    for i in range(10):
        corp = companies[i % len(companies)]
        c = await llm.structured(_Conv, [{"role": "user", "content":
            f"금융 리서치 챗봇과 사용자의 3~5턴 대화에서 사용자 발화만 쓴다. 주제: {themes[i % len(themes)]}. "
            f"기업이 필요하면 '{corp}'를 쓴다. 턴마다 standalone(앞 대화 없이도 이해되도록 지시어를 풀어 쓴 질문)과 "
            f"intent(legal·company·news·composite·chat 중 하나)를 붙인다. 2턴부터는 앞 대화를 가리키는 지시어·생략을 넣는다."}],
            effort="low", task="eval.multiturn", validate=lambda x: None if 3 <= len(x.turns) <= 5 else "3~5턴")
        rows.append({"id": f"mt-{i + 1:02d}", "turns": [t.model_dump() for t in c.turns], "split": "test",
                     "source": "llm_draft"})
    return rows


async def main() -> None:
    companies = [x["name"] for x in load_jsonl(DATASETS / "news_companies.v1.jsonl")]
    r, c, m = await asyncio.gather(routing(companies), composite(companies), multiturn(companies))
    save_jsonl(DATASETS / "routing.v1.jsonl", r)
    save_jsonl(DATASETS / "composite_tasks.v1.jsonl", c)
    save_jsonl(DATASETS / "multiturn.v1.jsonl", m)
    print("routing", len(r), Counter(x["intent"] for x in r), Counter(x["split"] for x in r))
    print("composite", len(c), "multiturn", len(m), sum(len(x["turns"]) for x in m), "턴")


if __name__ == "__main__":
    asyncio.run(main())
