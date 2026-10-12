# L3a (legal, dev)

L2 + 인용 그래프 1홉, 답변 포함

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| cite_precision | 0.595 | [0.500, 0.707] | 25 |
| cite_recall | 0.960 | [0.900, 1.000] | 25 |
| correctness | 0.927 | [0.867, 0.973] | 25 |
| false_refusal | 0.000 | [0.000, 0.000] | 25 |
| groundedness | 0.795 | [0.669, 0.911] | 25 |
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
  "latency_p50_s": 24.67,
  "latency_p95_s": 42.82,
  "cost_total_usd": 0.0639,
  "cost_per_item_usd": 0.0022,
  "errors": 0
 },
 "n_items": 29
}
```
