# C (agent, test)

복합 과업(기업+법령 5, 기업+뉴스 5) 체크리스트

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| calls | 17.000 | [14.000, 20.000] | 2 |
| checklist_rate | 0.500 | [0.200, 0.800] | 2 |
| success | 0.000 | [0.000, 0.000] | 2 |
| tool_valid | 1.000 | [1.000, 1.000] | 2 |

```json
{
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "human_recheck": "무작위 10과업은 사람이 재판정한다(eval/datasets/agent_recheck.v1.jsonl)",
 "ops": {
  "latency_p50_s": 199.815,
  "latency_p95_s": 204.98,
  "cost_total_usd": 0.0442,
  "cost_per_item_usd": 0.0221,
  "errors": 0
 },
 "n_items": 2
}
```
