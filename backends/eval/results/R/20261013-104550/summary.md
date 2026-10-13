# R (report, all)

R1(DART 구조화 + 코드 계산, 첫 초안)과 R2(+ 숫자 슬롯·검증 노드, 최종)를 한 실행에서 잰다

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| claims_dropped_R2 | 0.000 | [0.000, 0.000] | 2 |
| flagged_R2 | 0.000 | [0.000, 0.000] | 2 |
| num_coverage | 1.000 | [1.000, 1.000] | 2 |
| num_error | 0.000 | [0.000, 0.000] | 2 |
| num_match | 1.000 | [1.000, 1.000] | 2 |
| support_R1 | 0.746 | [0.682, 0.810] | 2 |
| support_R2 | 0.809 | [0.800, 0.818] | 2 |
| untraceable_rate_R1 | 0.023 | [0.000, 0.045] | 2 |
| verify_fail_rate_R1 | 0.326 | [0.318, 0.333] | 2 |

```json
{
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "note": "R1=첫 초안(검증 전), R2=검증·재작성 뒤. 수치는 2025 사업연도 주요계정과 대조",
 "ops": {
  "latency_p50_s": 88.35499999999999,
  "latency_p95_s": 98.57,
  "cost_total_usd": 0.026,
  "cost_per_item_usd": 0.01298,
  "errors": 0
 },
 "n_items": 2
}
```
