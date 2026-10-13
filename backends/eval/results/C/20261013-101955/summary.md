# C (agent, test)

복합 과업(기업+법령 5, 기업+뉴스 5) 체크리스트

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| calls | 13.833 | [10.167, 17.833] | 6 |
| checklist_rate | 0.800 | [0.467, 1.000] | 6 |
| success | 0.667 | [0.333, 1.000] | 6 |
| tool_valid | 1.000 | [1.000, 1.000] | 6 |

```json
{
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "human_recheck": "무작위 10과업은 사람이 재판정한다(eval/datasets/agent_recheck.v1.jsonl)",
 "ops": {
  "latency_p50_s": 182.79,
  "latency_p95_s": 280.21,
  "cost_total_usd": 0.1215,
  "cost_per_item_usd": 0.02025,
  "errors": 0
 },
 "n_items": 6
}
```
