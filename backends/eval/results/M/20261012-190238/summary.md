# M (multiturn, test)

멀티턴 후속 질문 해결률

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| followup_resolved | 0.500 | [0.333, 0.667] | 36 |
| intent_match | 0.500 | [0.360, 0.667] | 36 |

```json
{
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "ops": {
  "latency_p50_s": 3.075,
  "latency_p95_s": 4.79,
  "cost_total_usd": 0.0091,
  "cost_per_item_usd": 0.00025,
  "errors": 0
 },
 "n_items": 36
}
```
