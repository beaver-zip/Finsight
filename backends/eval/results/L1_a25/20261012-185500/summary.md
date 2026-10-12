# L1_a25 (legal, dev)

L1, α=0.25(α 탐색)

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| mrr | 0.781 | [0.655, 0.900] | 25 |
| ndcg@10 | 0.814 | [0.704, 0.914] | 25 |
| path_complete | 0.667 | [0.333, 0.889] | 9 |
| recall@10 | 0.940 | [0.880, 1.000] | 25 |
| recall@5 | 0.900 | [0.800, 0.980] | 25 |

```json
{
 "threshold_sweep": {
  "threshold": 0.8,
  "balanced": 0.875,
  "refuse_unanswerable": 0.75,
  "answer_answerable": 1.0
 },
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "ops": {
  "latency_p50_s": 0.01,
  "latency_p95_s": 0.01,
  "cost_total_usd": 0,
  "cost_per_item_usd": 0.0,
  "errors": 0
 },
 "n_items": 29
}
```
