# L3b (legal, dev)

L2 + 인용 그래프 2홉, 답변 포함

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| cite_precision | 0.557 | [0.460, 0.670] | 25 |
| cite_recall | 0.960 | [0.900, 1.000] | 25 |
| correctness | 0.940 | [0.887, 0.987] | 25 |
| false_refusal | 0.000 | [0.000, 0.000] | 25 |
| groundedness | 0.837 | [0.730, 0.933] | 25 |
| mrr | 0.940 | [0.880, 1.000] | 25 |
| ndcg@10 | 0.903 | [0.831, 0.964] | 25 |
| path_complete | 0.778 | [0.556, 1.000] | 9 |
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
  "latency_p50_s": 26.52,
  "latency_p95_s": 40.43,
  "cost_total_usd": 0.062,
  "cost_per_item_usd": 0.00214,
  "errors": 0
 },
 "n_items": 29
}
```
