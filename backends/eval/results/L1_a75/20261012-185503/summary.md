# L1_a75 (legal, dev)

L1, α=0.75(α 탐색)

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| mrr | 0.900 | [0.820, 0.980] | 25 |
| ndcg@10 | 0.868 | [0.784, 0.943] | 25 |
| path_complete | 0.556 | [0.222, 0.778] | 9 |
| recall@10 | 0.940 | [0.860, 1.000] | 25 |
| recall@5 | 0.920 | [0.840, 0.980] | 25 |

```json
{
 "threshold_sweep": {
  "threshold": 0.9,
  "balanced": 0.96,
  "refuse_unanswerable": 1.0,
  "answer_answerable": 0.92
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
