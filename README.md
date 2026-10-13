# Finsight

전자금융 법령과 상장사를 묻는 질문에 근거를 붙여 답하는 AI 리서치 도구입니다.

- **법령 질문**: 전자금융거래법·시행령·감독규정·시행세칙에서 조문을 찾아 인용과 함께 답합니다.
- **기업 분석 리포트**: 공시·시세·기사로 실적, 밸류에이션, 주주환원을 정리한 리포트를 만듭니다.
- **채팅**: 위 두 가지와 기업 뉴스 요약을 한 대화창에서 씁니다.

## 버전

| 버전 | 위치 | 설명 |
| --- | --- | --- |
| v1 | 태그 `v1-hackathon` | K intelligence 해커톤 2025 Track1 3위. 팀 작업(Yeyeong99, Kimyongari, hanseo). [리포트 시연](https://www.youtube.com/watch?v=xQm8__sc2js) · [RAG 시연](https://www.youtube.com/watch?v=BaZBrlYc3Uc) |
| v2 | `v2` 브랜치 | v1을 바탕으로 다시 설계한 개인 작업(hanseo). 이 문서는 v2 기준 |

## 실행

필요한 것: Python 3.11~3.13, Node.js 20 이상, Docker.

```bash
cp backends/.env.copy backends/.env                 # 키 입력: Anthropic, OpenRouter, OpenDART, Tavily, 법령 API
bash scripts/setup_local.sh                         # 가상환경·의존성 설치, Weaviate·Neo4j 실행, 테스트, 키 확인
cd backends && source .venv/bin/activate
PYTHONPATH=. python scripts/ingest_law.py           # 법령 적재(처음 한 번)
uvicorn app.main:app --port 8000                    # 백엔드
cd ../frontends/corp-advisor--app && npm install && npm run dev   # 프론트: http://localhost:5173
```

## 동작 방식

**법령**: 조문을 조·항·별표 단위로 저장하고 조문끼리 서로 가리키는 관계를 그래프로 이어 둡니다. 질문이 오면 관련 조문을 찾은 뒤 그 조문이 가리키는 조문까지 따라가 근거를 모읍니다. 예를 들어 "정보보호최고책임자 자격"을 물으면 법 제21조의2 → 시행령 제11조의4 → 시행령 별표 1까지 찾아 답합니다. 위임 관계("대통령령으로 정한다")는 본문 문구와 국가법령정보 API의 위임 정보를 맞춰 보고, API가 위임 정보를 주는 법률·시행령에서는 API에 없는 쌍을 뺍니다. 별표의 괘선 표는 행·열이 살아 있는 표로 바꿔 저장합니다.

**리포트**: 재무 숫자는 모두 코드가 계산합니다. 문장은 Claude가 쓰지만 숫자 자리에는 계산값의 이름표만 넣고 화면에 그릴 때 실제 값으로 바꿉니다. 그 뒤 검증 단계가 숫자 출처와 주장 근거를 확인하고 통과하지 못한 문장은 다시 씁니다.

**채팅**: 질문을 법령·기업·뉴스·복합·안내 중 하나로 분류해 필요한 작업을 돌린 뒤 답을 합칩니다. "삼성"처럼 회사가 여럿이면 어느 회사인지 되묻습니다.

## 구조

```
backends/app/
├── core/      외부 연결: OpenDART, 법령 API, Weaviate, Neo4j, Tavily, Claude
├── domain/    외부 연결 없는 계산: 재무 지표, 조문 참조 해석, 뉴스 점수
├── agents/    처리 흐름: 리포트, 법령, 뉴스, 채팅, 검증
└── api/       HTTP 경로
backends/eval/                  평가셋과 평가 실행 코드
frontends/corp-advisor--app/    리포트 화면, 채팅 화면
```

## API

| 경로 | 설명 |
| --- | --- |
| `GET /report/{corp_code}` | 기업 분석 리포트(HTML). `/stream`은 진행 상황을 실시간으로, `/json`은 데이터로 |
| `POST /chat` | 채팅. 답이 만들어지는 과정을 실시간으로 보냅니다 |
| `POST /files/upload-pdf`, `POST /rag/register` | 내규 PDF 올리기, 법령 검색에 함께 쓰이도록 적재 |
| `GET /rag/show_files_in_collection`, `POST /rag/delete_objects_from_file_name` | 올린 문서 목록, 삭제 |
| `POST /admin/legal/ingest`, `POST /admin/legal/reset` | 법령 다시 적재, 초기화(`X-Admin-Token` 필요) |
| `POST /financial/corp_list` | 기업명 검색 |
| `GET /health/budget` | 유료 API 누적 비용 |

## 평가

평가용 문항은 AI가 초안을 만들고 사람이 검수합니다. 아래 표는 검수 전 초안으로 일부만 돌려 본 예비 결과입니다.

| 항목 | 예비 결과 |
| --- | --- |
| 법령 답변 정확도 | 핵심 사실 포함률 0.91~0.94 |
| 여러 조문을 이어야 하는 질문 | 필요한 조문을 모두 찾은 비율이 그래프 탐색 전 0.67, 후 0.78 |
| 범위 밖 질문 거절 | 4문항 모두 거절 |
| 리포트 숫자 | 공시 원본과 일치율 1.0(16개 기업) |
| 리포트 문장 근거 | 근거가 확인된 문장 비율이 검증 전 0.82, 검증·재작성 뒤 0.87 |
| 복합 질문(기업+법령, 기업+뉴스) | 10개 중 6개가 채점 항목을 모두 채움, 항목 충족률 0.92 |

전체 결과는 [docs/eval_results.md](docs/eval_results.md)에 있습니다.

```bash
cd backends && PYTHONPATH=. python -m eval.run --config L4 --split dev
```
