# N1 (news, dev)

질의 계획 + 임베딩 중복 묶기 + 점수·MMR(커버리지 재검색 없음)

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| age_median_days | 36.500 | [25.000, 48.000] | 2 |
| aspects | 1.500 | [1.000, 2.000] | 2 |
| date_acc | 1.000 | [1.000, 1.000] | 1 |
| dup_rate | 0.333 | [0.000, 0.667] | 2 |
| ndcg@5 | 0.311 | [0.145, 0.477] | 2 |
| p@5 | 0.400 | [0.200, 0.600] | 2 |
| unjudged | 0.000 | [0.000, 0.000] | 2 |

```json
{
 "note": "relevance≥1을 관련으로 본다. 라벨 풀 밖 선택은 관련 0(unjudged 비율 참고)",
 "ops": {
  "latency_p50_s": 11.145,
  "latency_p95_s": 11.36,
  "cost_total_usd": 0.0031,
  "cost_per_item_usd": 0.00157,
  "errors": 0
 },
 "n_items": 2,
 "rescored": true
}
```
