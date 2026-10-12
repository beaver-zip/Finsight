# L3b (legal, dev)

L2 + 인용 그래프 2홉, 답변 포함

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| cite_precision | 0.593 | [0.493, 0.710] | 25 |
| cite_recall | 0.900 | [0.820, 0.980] | 25 |
| correctness | 0.913 | [0.847, 0.967] | 25 |
| false_refusal | 0.000 | [0.000, 0.000] | 25 |
| groundedness | 0.760 | [0.643, 0.863] | 25 |
| mrr | 0.940 | [0.880, 1.000] | 25 |
| ndcg@10 | 0.903 | [0.831, 0.964] | 25 |
| path_complete | 0.667 | [0.333, 0.889] | 9 |
| recall@10 | 0.940 | [0.880, 1.000] | 25 |
| recall@5 | 0.940 | [0.880, 1.000] | 25 |
| refusal_correct | 1.000 | [1.000, 1.000] | 4 |

```json
{
 "threshold_sweep": {
  "threshold": 0.3,
  "balanced": 1.0,
  "refuse_unanswerable": 1.0,
  "answer_answerable": 1.0
 },
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "ops": {
  "latency_p50_s": 26.07,
  "latency_p95_s": 38.91,
  "cost_total_usd": 0.0612,
  "cost_per_item_usd": 0.00211,
  "errors": 0
 },
 "n_items": 29
}
```
