# R (report, dev)

R1(DART 구조화 + 코드 계산, 첫 초안)과 R2(+ 숫자 슬롯·검증 노드, 최종)를 한 실행에서 잰다

LLM 단계는 문항마다 1회 실행.

| 지표 | 값 | 95% CI | n |
| --- | --- | --- | --- |
| claims_dropped_R2 | 0.000 | [0.000, 0.000] | 4 |
| flagged_R2 | 0.013 | [0.000, 0.038] | 4 |
| num_coverage | 1.000 | [1.000, 1.000] | 4 |
| num_error | 0.083 | [0.000, 0.250] | 4 |
| num_match | 0.917 | [0.750, 1.000] | 4 |
| support_R1 | 0.873 | [0.800, 0.955] | 3 |
| support_R2 | 0.907 | [0.825, 0.976] | 4 |
| untraceable_rate_R1 | 0.013 | [0.000, 0.038] | 4 |
| verify_fail_rate_R1 | 0.260 | [0.214, 0.306] | 4 |

```json
{
 "judge": "google/gemini-2.5-flash (임시 judge)",
 "note": "R1=첫 초안(검증 전), R2=검증·재작성 뒤. 수치는 2025 사업연도 주요계정과 대조",
 "ops": {
  "latency_p50_s": 88.26,
  "latency_p95_s": 96.97,
  "cost_total_usd": 0.0512,
  "cost_per_item_usd": 0.01281,
  "errors": 0
 },
 "n_items": 4
}
```
