# R (report, test)

R1(DART 구조화 + 코드 계산, 첫 초안)과 R2(+ 숫자 슬롯·검증 노드, 최종)를 한 실행에서 잰다

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| claims_dropped_R2 | 0.000 | [0.000, 0.000] | 1 |
| flagged_R2 | 0.000 | [0.000, 0.000] | 1 |
| num_coverage | 1.000 | [1.000, 1.000] | 1 |
| num_error | 0.000 | [0.000, 0.000] | 1 |
| num_match | 1.000 | [1.000, 1.000] | 1 |
| support_R1 | 0.870 | [0.870, 0.870] | 1 |
| support_R2 | 0.905 | [0.905, 0.905] | 1 |
| untraceable_rate_R1 | 0.000 | [0.000, 0.000] | 1 |
| verify_fail_rate_R1 | 0.304 | [0.304, 0.304] | 1 |

```json
{
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "note": "R1=첫 초안(검증 전), R2=검증·재작성 뒤. 수치는 2025 사업연도 주요계정과 대조",
 "ops": {
  "latency_p50_s": 125.52,
  "latency_p95_s": 125.52,
  "cost_total_usd": 0.0274,
  "cost_per_item_usd": 0.02739,
  "errors": 0
 },
 "n_items": 1
}
```
