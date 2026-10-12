# L1 (legal, dev)

API 코퍼스 + 부모-자식 청킹 + kagome_kr 하이브리드(리랭크 없음), α=0.5

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| mrr | 0.860 | [0.747, 0.953] | 25 |
| ndcg@10 | 0.862 | [0.775, 0.943] | 25 |
| path_complete | 0.667 | [0.333, 0.889] | 9 |
| recall@10 | 0.960 | [0.900, 1.000] | 25 |
| recall@5 | 0.880 | [0.780, 0.980] | 25 |

```json
{
 "threshold_sweep": {
  "threshold": 0.85,
  "balanced": 0.96,
  "refuse_unanswerable": 1.0,
  "answer_answerable": 0.92
 },
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "ops": {
  "latency_p50_s": 0.8,
  "latency_p95_s": 0.94,
  "cost_total_usd": 0.0,
  "cost_per_item_usd": 0.0,
  "errors": 0
 },
 "n_items": 29
}
```
