# route_high (routing, dev)

라우팅 effort 탐색: high

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| accuracy | 0.600 | [0.433, 0.767] | 30 |
| accuracy_ambiguous | 0.438 | [0.188, 0.688] | 16 |

```json
{
 "macro_f1": 0.5934,
 "f1_by_intent": {
  "legal": 0.8,
  "company": 0.5455,
  "news": 0.25,
  "composite": 0.5714,
  "chat": 0.8
 },
 "confusions": {
  "news→composite": 5,
  "legal→composite": 2,
  "company→composite": 2,
  "chat→company": 2,
  "company→news": 1
 },
 "ops": {
  "latency_p50_s": 2.71,
  "latency_p95_s": 4.68,
  "cost_total_usd": 0.0101,
  "cost_per_item_usd": 0.00034,
  "errors": 0
 },
 "n_items": 30
}
```
