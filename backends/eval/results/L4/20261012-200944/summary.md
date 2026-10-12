# L4 (legal, dev)

L3(2홉) + 질의 계획·충분성 루프(서브그래프 전체), 답변 포함

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| cite_precision | 0.660 | [0.543, 0.777] | 25 |
| cite_recall | 0.920 | [0.840, 0.980] | 25 |
| correctness | 0.920 | [0.853, 0.973] | 25 |
| false_refusal | 0.000 | [0.000, 0.000] | 25 |
| groundedness | 0.767 | [0.646, 0.872] | 24 |
| mrr | 0.877 | [0.777, 0.973] | 25 |
| ndcg@10 | 0.868 | [0.790, 0.945] | 25 |
| path_complete | 0.667 | [0.333, 0.889] | 9 |
| recall@10 | 0.960 | [0.900, 1.000] | 25 |
| recall@5 | 0.920 | [0.840, 0.980] | 25 |
| refusal_correct | 0.250 | [0.000, 0.750] | 4 |

```json
{
 "threshold_sweep": {
  "threshold": 0.95,
  "balanced": 0.875,
  "refuse_unanswerable": 0.75,
  "answer_answerable": 1.0
 },
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "ops": {
  "latency_p50_s": 53.95,
  "latency_p95_s": 119.77,
  "cost_total_usd": 0.1036,
  "cost_per_item_usd": 0.00357,
  "errors": 0
 },
 "n_items": 29
}
```
