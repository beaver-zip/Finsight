# N2 (news, dev)

N1 + 커버리지 재검색

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| age_median_days | 153.000 | [153.000, 153.000] | 1 |
| aspects | 1.500 | [0.000, 3.000] | 2 |
| date_acc | 1.000 | [1.000, 1.000] | 1 |
| dup_rate | 0.000 | [0.000, 0.000] | 1 |
| ndcg@5 | 0.395 | [0.000, 0.790] | 2 |
| p@5 | 0.500 | [0.000, 1.000] | 2 |
| unjudged | 0.000 | [0.000, 0.000] | 1 |

```json
{
 "note": "relevance≥1을 관련으로 본다. 라벨 풀 밖 선택은 관련 0(unjudged 비율 참고)",
 "ops": {
  "latency_p50_s": 25.565,
  "latency_p95_s": 27.21,
  "cost_total_usd": 0.0104,
  "cost_per_item_usd": 0.00521,
  "errors": 0
 },
 "n_items": 2,
 "rescored": true
}
```
