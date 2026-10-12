# route (routing, dev)

라우팅(현재 설정: effort low)

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| accuracy | 0.633 | [0.467, 0.800] | 30 |
| accuracy_ambiguous | 0.438 | [0.188, 0.688] | 16 |

```json
{
 "macro_f1": 0.6248,
 "f1_by_intent": {
  "legal": 0.8,
  "company": 0.6667,
  "news": 0.2857,
  "composite": 0.5714,
  "chat": 0.8
 },
 "confusions": {
  "news→composite": 5,
  "legal→composite": 2,
  "company→composite": 2,
  "chat→company": 2
 },
 "ops": {
  "latency_p50_s": 1.81,
  "latency_p95_s": 2.44,
  "cost_total_usd": 0.0064,
  "cost_per_item_usd": 0.00021,
  "errors": 0
 },
 "n_items": 30
}
```
