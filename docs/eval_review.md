# 평가셋 검수 안내

평가셋 초안은 Claude(Haiku 5.5)가 만들었습니다. 사람이 아래 항목을 확인하고 고쳐야 결과표 수치를 낼 수 있습니다. 파일은 모두 `/Users/hanseo/PycharmProjects/finsight/backends/eval/datasets/`에 있습니다.

jsonl 파일은 한 줄이 한 문항입니다. 고친 문항은 `"source"`를 `"llm_draft+human"`으로 바꾸고, 쓸 수 없는 문항은 그 줄을 지웁니다(지운 수는 결과표에 적습니다).

## 1. citation_refs.v1.csv (100줄, 엑셀)

참조 해석기가 "법 제28조" 같은 표현을 어느 조문으로 풀었는지 맞는지 봅니다.

- `판정(O/X)`: 맞으면 O, 틀리면 X.
- `정답_대상`: X일 때만 맞는 조문을 `전자금융거래법시행령:15`처럼 씁니다. 참조가 아닌 표현이면 `(없음)`.
- 다 채우면 `PYTHONPATH=. python -m eval.score_citations`로 정밀도·재현율을 냅니다.

## 2. legal_qa.v1.jsonl (100문항)

| 필드 | 확인할 것 |
| --- | --- |
| `question` | 실무자가 물을 법한 질문인지, 조문 번호를 그대로 쓰지 않았는지 |
| `gold_keys` | 답에 꼭 필요한 조문·별표 key가 모두 들어 있는지(빠졌으면 더하고, 필요 없는 것은 뺍니다) |
| `key_facts` | 정답에 반드시 들어가야 할 사실 2~3개가 조문 문구와 맞는지 |
| `type`이 `unanswerable` | 정말 4개 법령으로 답할 수 없는 질문인지. 답할 수 있으면 `answerable`을 true로 바꾸고 `gold_keys`·`key_facts`를 채웁니다 |

조문 key는 `전자금융거래법:21-2`(제21조의2), `전자금융거래법시행령:annex-1`(별표 1) 형식입니다. 조문 원문은 `backends/data/law/corpus.json`에 있습니다.

## 3. routing.v1.jsonl (100문항)

`intent`(legal·company·news·composite·chat)가 질문에 맞는지 봅니다. `ambiguous: true`는 일부러 헷갈리게 만든 문항입니다.

## 4. composite_tasks.v1.jsonl (10과업)

`checklist` 항목이 답만 보고 예·아니오로 판정할 수 있는지, 과업 질문에 맞는지 봅니다. 4개 법령 밖의 의무(자본시장법 등)를 요구하는 항목은 고칩니다.

## 5. multiturn.v1.jsonl (10대화)

턴마다 `standalone`(앞 대화 없이도 이해되는 질문)과 `intent`가 맞는지 봅니다. 4개 법령 밖 주제(예: 자본시장법)로 흐르는 대화는 전자금융 법령으로 바꿉니다.

## 6. news_labels.v1.jsonl (168건)

| 필드 | 값 |
| --- | --- |
| `relevance` | 2 = 그 회사가 기사의 주체, 1 = 다루지만 주제가 아님, 0 = 다른 회사이거나 이름만 나옴 |
| `aspect` | 실적·재무, 투자·생산, 제품·기술, 시장·경쟁, 리스크·규제, 지배구조·자본 중 하나(relevance 0이면 null) |
| `materiality` | 1 = 일상 보도, 2 = 실적·사업에 영향 가능, 3 = 실적·주가에 직접 영향 |
| `dup_group` | 같은 사건을 다룬 기사끼리 같은 값 |
| `published_label` | 기사 발행일(YYYY-MM-DD). URL을 열어 확인합니다 |

## 7. judge 보정용 (법령 답변 채점)

법령 L4 예비 실행이 끝나면 `judge_human_dev.v1.csv`가 생깁니다. 답변이 각 핵심 사실을 담고 있으면 `사람_판정(O/X)`에 O, 아니면 X를 씁니다. 이 표로 judge 모델 3개 중 사람과 가장 잘 맞는 모델을 고릅니다.

## 검수하지 않는 것

- `report_companies.v1.jsonl`, `report_gold.v1.jsonl`: 리포트 수치 정답은 OpenDART 주요계정 API 값을 그대로 씁니다(2026-10-12 결정).
- `news_companies.v1.jsonl`: 뉴스 평가 대상 회사 목록입니다.
