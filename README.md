# Finsight

전자금융 법령 리서치와 상장사 기업 분석을 하는 에이전트 시스템입니다. 법령 질문에는 조문을 인용해 답하고, 기업은 공시·시세·기사를 근거로 분석 리포트를 만듭니다.

## 버전

| 버전 | 위치 | 작업 |
| --- | --- | --- |
| v1 | 태그 `v1-hackathon`(커밋 4fe7118) | K intelligence 해커톤 2025 Track1(AI Agent 개발) 3위 수상작. 팀 작업(Yeyeong99, Kimyongari, hanseo) |
| v2 | `v2` 브랜치 | v1을 바탕으로 다시 설계한 개인 작업(hanseo). 아래 내용은 v2 기준 |

v1 시연 영상: [Report Agent](https://www.youtube.com/watch?v=xQm8__sc2js), [RAG Agent](https://www.youtube.com/watch?v=BaZBrlYc3Uc)

## v2에서 바뀐 것

- **법령 리서치**: v1의 PDF 청크 검색 대신 국가법령정보 Open API의 조문 구조(조·항·별표)로 적재하고, 조문 사이의 참조·위임을 Neo4j 인용 그래프로 연결했습니다. 하이브리드 검색(Weaviate, `kagome_kr`) → 리랭크(bge-reranker-v2-m3) → 그래프 1~2홉 확장 → 근거 충분성 판단과 재검색 → Claude 인용 답변 순서입니다. "정보보호최고책임자 자격" 질문이면 법 제21조의2 → 시행령 제11조의4 → 시행령 별표 1로 이어지는 근거를 함께 찾습니다.
- **기업 분석 리포트**: OpenDART 재무제표·공시와 시세로 실적, 밸류에이션(PBR 밴드·동종 업종), 현금흐름·주주환원, 잠정실적 발표 반응을 코드로 계산합니다. 본문은 Claude가 쓰되 재무 숫자는 사실표 id 슬롯으로만 쓰고 렌더러가 값으로 바꿉니다. 검증 노드가 숫자 출처·인용·주장 지지를 확인하고 실패한 문장은 다시 씁니다.
- **뉴스**: 측면별 질의 → 기사 수집 → 임베딩 중복 묶기 → 관련도·중요도 분류 → 점수와 MMR로 5건 선택 → 요약.
- **채팅**: 질문을 법령·기업·뉴스·복합·안내로 나눠 하위 과업을 병렬로 돌리고 답을 합칩니다. 기업명이 애매하면 되묻습니다(예: "삼성" → 삼성전자·삼성전기·…).
- **평가**: 법령 QA 100, 라우팅 100, 리포트 기업 16, 뉴스 라벨 등 평가셋과 러너를 두고, 구성마다 같은 문항으로 짝지어 비교합니다(아래 '평가').

설계와 결정 기록은 [docs/design.md](docs/design.md)에 있습니다.

## 구조

```
backends/
├── app/
│   ├── core/          # 외부 클라이언트: OpenDART, 법령 API, Weaviate, Neo4j, Tavily, Claude, 임베딩, 리랭커, 캐시·비용 장부
│   ├── domain/        # I/O 없는 계산: 계정 매핑, 사실표·슬롯, 참조 해석기, 뉴스 점수
│   ├── agents/        # LangGraph: company(리포트), legal(법령), news(뉴스), orchestrator(채팅), verifier(검증)
│   └── api/           # FastAPI: /report, /chat, /financial
├── eval/              # 평가셋·설정·러너·결과
├── scripts/           # 법령 적재, 상장사 목록, 키 확인
└── tests/             # 단위 테스트
frontends/corp-advisor--app/   # React + Vite(리포트 화면, 채팅 화면)
```

## API

| 경로 | 내용 |
| --- | --- |
| `GET /report/{corp_code}` | 기업 분석 리포트 HTML(7일 캐시). `/stream`은 진행 단계를 SSE로, `/json`은 구조화 결과 |
| `POST /chat` | 채팅(SSE). `{thread_id, message, mode, resume}`, `mode`는 `auto`·`legal`·`company`·`news` |
| `POST /financial/corp_list` | 기업명 검색 |
| `GET /health/budget` | 유료 API 누적 비용 |

## 실행

맥 로컬 환경 준비는 [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md)를 따릅니다.

```bash
docker compose up -d weaviate neo4j                  # 벡터 DB·그래프 DB
cd backends && source .venv/bin/activate
PYTHONPATH=. python scripts/ingest_law.py            # 법령 적재(처음 한 번)
uvicorn app.main:app --port 8000                     # 백엔드
cd ../frontends/corp-advisor--app && npm run dev     # 프론트 http://localhost:5173
```

키는 `backends/.env`에 둡니다(Anthropic, OpenRouter, OpenDART, Tavily, 법령 API OC). 생성 모델은 Claude Haiku 5.5, 임베딩은 OpenRouter의 bge-m3입니다.

## 평가

평가셋 초안은 LLM이 만들고 사람이 검수합니다. 아래는 검수 전 초안 라벨로 dev 분할만 돌린 예비 결과이고, 결과표 수치는 검수 뒤 test 분할에서 다시 냅니다.

```bash
cd backends && PYTHONPATH=. python -m eval.run --config L4 --split dev
PYTHONPATH=. python -m eval.compare eval/results/L3b/<ts> eval/results/L4/<ts>
```

### 예비 결과 (2026-10-12, dev, 초안 라벨)

judge는 κ로 고르기 전의 임시 모델(`google/gemini-2.5-flash`)이고, LLM 단계는 문항마다 1회 실행했습니다. 문항 수가 적어 신뢰구간이 넓습니다(구간은 `eval/results/<설정>/<시각>/summary.md`).

법령 QA(dev 29문항: 답 있음 25, 그중 다중 홉 9, 답 없음 4)

| 구성 | Recall@10 | MRR | 경로 완결률 | 정답성 | 근거 충실도 | 인용 정밀도 / 재현율 | 거절 정확도 | 지연 P50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| L1 하이브리드(α 0.5) | 0.96 | 0.86 | — | — | — | — | — | 1초 |
| L2 + 리랭크 | 0.94 | 0.94 | 0.67 | 0.94 | 0.80 | 0.62 / 0.92 | 1.0 | 21초 |
| L3a + 그래프 1홉 | 0.94 | 0.94 | 0.78 | 0.93 | 0.80 | 0.60 / 0.96 | 1.0 | 25초 |
| L3b + 그래프 2홉 | 0.94 | 0.94 | 0.78 | 0.94 | 0.84 | 0.56 / 0.96 | 1.0 | 27초 |
| L4 + 충분성 루프 | 0.96 | 0.85 | 0.78 | 0.91 | 0.74 | 0.57 / 0.90 | 1.0 | 50초 |

- α는 0.25·0.5·0.75 중 0.5의 Recall@10이 가장 높았습니다(0.94·0.96·0.94).
- 그래프 확장은 다중 홉 9문항 중 경로를 완결한 문항을 6개에서 7개로 늘렸습니다.

그 밖의 스위트

| 스위트 | 결과 |
| --- | --- |
| 라우팅(dev 30) | 정확도 0.63, macro-F1 0.62. effort low·medium·high가 0.63·0.63·0.60이라 low를 유지. 틀린 문항 다수는 "실적과 뉴스 같이"처럼 복합 질문을 단일 의도로 적은 라벨 오류 |
| 멀티턴(test 36턴) | 후속 질문 해결률 0.50, 의도 일치 0.50 |
| 뉴스(dev 2개 기업) | N1 P@5 0.40·nDCG@5 0.31, N2 P@5 0.50·nDCG@5 0.40 |
| 리포트(dev 2개 기업) | 수치 일치율 1.0, 첫 초안 추적 불가 숫자 0, 첫 검증 실패율 0.13, 주장 지지율 R1 0.75 → R2 0.84 |
| 복합 과업(2개) | 체크리스트 전 항목 충족 2/2 |

