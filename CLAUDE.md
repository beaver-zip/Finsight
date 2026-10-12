# Finsight v2 작업 안내

전자금융 법령 리서치와 상장사 기업 분석 리포트를 만드는 에이전트 시스템이다. 백엔드는 FastAPI + LangGraph(`backends/`), 프론트는 React + Vite(`frontends/corp-advisor--app/`), 작업 브랜치는 `v2`.

## 먼저 읽을 것

- 설계서: `docs/design.md`. 정본은 [Claude Docs](https://claude.ai/code/artifact/57ce8521-8362-43c4-a254-ebfa8ead32c7)이고 절마다 구현/설계 상태가 적혀 있다.
- 로컬 실행 방법: `docs/LOCAL_SETUP.md`.
- 실제 동작이 설계서와 다르면 설계서(`docs/design.md`)를 코드에 맞춰 고치고, 무엇이 바뀌었는지 사용자에게 알린다.

## 사용자와 일하는 방식

- 한국어 존댓말(해요체), 짧게. 사용자 결정이 필요한 질문은 무엇을 고르면 무엇이 달라지는지 구체적으로 쓴다. 번역투·모호한 표현을 쓰지 않는다.
- 로드맵·일정·작업 순서를 마음대로 정하지 않는다. 우선순위를 물으면 필수/덜 중요로만 나눈다(날짜·기간 없이).
- "덜 중요한 것"도 되도록 구현해 완성도를 높이고 싶어 한다. 과하다고 보는 설계는 근거와 함께 제안하고 결정을 기다린다(아래 목록).
- 리포트·화면 문구에서 "AI 티"를 싫어한다: 머리말 분류 라벨(기업분석·잠정실적 리뷰 등), 도표마다 붙는 긴 "주:" 주석, 시스템 자기 설명, 자리표시 배지, 같은 숫자 반복, "~구조/~구간/~셈/~가능성" 같은 맺음말, 바로 뒤 차트 제목을 되풀이하는 소제목.
- 디자인은 레퍼런스를 근거로 정한다. 거절된 것: 흑백, 쨍한 파랑+주황, 남색+금색. 리포트는 승인된 목업 v9(회색 단계 + 초록 `#0B7A5C` 하나, 등락에만 빨강 `#D6343C`·파랑 `#2E6BD8`, BBC·Economist 차트 문법).

## 보안

- 키는 `backends/.env`에만 둔다(커밋 금지). 출력·로그·커밋 메시지에 키 값을 찍지 않는다.
- OpenDART·법령 API URL에는 키(`crtfc_key`, `OC`)가 들어간다. 예외 메시지와 로그에 URL을 넣지 않는다(`core/dart/client.py` 방식). httpx·httpcore 로거는 WARNING으로 둔다.
- 캐시·녹화 저장본에서 키를 지운다: `Authorization` 헤더, `crtfc_key`, `OC`(법령 응답의 상세 링크에도 `OC=`가 들어 있다).
- 셸에 `ANTHROPIC_API_KEY`를 export하지 않는다. 이 키는 프로젝트 예산용이다.

## 비용

- 유료 API(Claude·OpenRouter·Tavily) 합계 10달러 이내, `BUDGET_USD=9`에서 자동 차단. 2026-10-12 누적 약 0.30달러. 장부는 `backends/data/cache/cache.sqlite`의 `ledger` 표, 합계는 `GET /health/budget`.
- 리포트 1건: 뉴스 캐시가 있으면 약 0.012달러(주장 지지 검증이 절반), 뉴스까지 새로 받으면 약 0.02달러 + Tavily 3~5크레딧(무료 플랜 월 1,000).
- 생성 모델은 Haiku 5.5(`claude-haiku-5-5`). Sonnet 5.5는 법령 30문항의 생성 모델 비교에만 쓴다.

## 명령

```bash
bash scripts/setup_local.sh                     # 처음 한 번: 가상환경·의존성·Weaviate·Neo4j·테스트·키 확인
docker compose up -d weaviate neo4j             # DB만 켜기 (끄기: docker compose stop)
cd backends && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000       # 백엔드
PYTHONPATH=. pytest -q                          # 테스트
PYTHONPATH=. python scripts/check_keys.py       # 키별 소량 요청(키 값은 출력하지 않음)
cd frontends/corp-advisor--app && npm install && npm run dev   # 프론트 http://localhost:5173
```

리포트 확인: `curl -s localhost:8000/report/00126380 -o /tmp/samsung.html`(삼성전자, LLM 비용이 든다).

## 현재 상태

- 구현: 공통 기반(설정·캐시·비용 장부·Claude 호출·임베딩), OpenDART 수집, 시세·동종 업종, 뉴스 파이프라인, 검증 노드, 리포트 v2(`/report/{corp_code}`, `/json`, `/stream`)와 프론트 리포트 화면, 법령 리서치(적재 `scripts/ingest_law.py`, 검색·리랭크, Neo4j 인용 그래프, 서브그래프 `agents/legal/`, 리포트 규제 노드에 연결), 채팅 오케스트레이터와 `POST /chat`(SSE, 되묻기), 프론트 채팅 화면(`/chat` 사용). 테스트 85개.
- 없음: 업로드·관리 API(프론트 지식베이스 버튼은 숨김), 평가셋·러너, 요청당 토큰 상한.
- 채팅 측정(M1): 안내 5초, 법령 약 67초·0.004달러, 기업(리포트 캐시) 43초·0.008달러, 뉴스 25초·0.009달러, 새 회사 복합 질문 약 3분·0.014달러.
- v1 잔여 파일(`app/routers/`, `app/services/`, `app/schemas/`, `app/core/VDB/`, `app/core/llm/llm.py`, `app/core/web_search_agent/`, `app/core/Preprocessor/`, `app/charts.py`, `backends/pdfs/`)은 어디에도 연결돼 있지 않다.
- 법령 질문 1건 약 50초(M1, 리랭커 bge-reranker-v2-m3를 MPS로, 후보 20개). 앱 시작 때 Weaviate·Neo4j가 꺼져 있으면 법령 리서치 없이 뜬다. 코퍼스를 바꾸면 `PYTHONPATH=. python scripts/ingest_law.py`(임베딩 0.01달러 미만).

## 알려진 버그 (2026-10-12)

LLM 없이 수집·분석·렌더링만 돌려 에코프로비엠(01160363), KB금융지주(00688996), 현대약품(00164706)에서 찾았다. 별도 재무제표 ROE·PER, 금융지주 피어, 금융업 현금흐름 절, 음수 영업현금흐름/순이익은 고쳤다. 남은 것:

- 부문 표는 삼성전자 형식만 읽힌다(세 회사 모두 부문 차트 없음).
- KB금융은 분기 이력이 3Q23부터만 잡힌다. OpenDART 재무 API(`fnlttSinglAcntAll`·`fnlttSinglAcnt`)가 2023-10-05 기재정정된 22년 사업~23년 반기보고서에 013을 준다. 메우려면 원문 표를 읽어야 한다.

## 과한 설계로 제안한 것 (사용자 결정 대기)

결정이 나기 전에는 만들지 말고 물어본다.

- 결정됨(2026-10-12, 채팅): 프론트를 `/chat`으로 바꾸고 v1 호환 경로(`api/legacy.py`)는 만들지 않음, 사용자 선호(요약 길이·표) 저장 안 함, 긴 대화는 최근 10개 메시지만 씀(요약 없음), 채팅 뉴스는 기존 뉴스 파이프라인을 한 번 호출(에이전트 없음, 기업 없는 뉴스 질문은 못 다룸). 법령 리랭크 후보 20개(하이브리드)·20개(확장). 법령 답변은 시드 점수가 임계값 미만일 때만 거절(재검색 2회 뒤 부족해도 찾은 조문으로 답함). 참조 해석기 표본 100개는 Claude 초안 → 사용자 검수(`backends/eval/datasets/citation_refs.v1.csv`).
- 결정됨(2026-10-12, 설계서 반영): 관심 기업 메모리·리포트 스냅샷 저장 빼기, 업로드 내규는 벡터 검색만(참조 엣지 없음), `report_companies` 16개(8층×2), 중복 임계값 {0.85, 0.88, 0.91}만 탐색, 법령 결과는 리포트에 규제 절로 넣기(`reg_head`·`reg_a`, 블록 `legal_keys`).

- 빼기: networkx vs Neo4j 지연 비교, mitmproxy 녹화·재생(SQLite 캐시 고정으로 대체), CI의 LLM 응답 캐시 스모크 평가, Message Batches, LangSmith 데이터셋 업로드, McNemar, 옛 PDF 페이지 매핑, `domain/metrics.py`·v1 잔여 파일.
- 줄이기: 법령 튜닝 손잡이(α·홉 수·거절 임계값만), 뉴스 가중치 격자 탐색(고정값), 작업별 effort 탐색, 별표 변환 도구 사슬(평가에 쓰는 별표만 수작업), 위임 관계 API 이중화(정규식만), 리포트 수치 정답 수작업 검수(주요계정 API 자동 대조), judge κ 보정 반복.

## 외부 API 메모

- OpenDART: 요청을 몰아서 보내지 않는다(상장사 목록을 만들 때 수천 건을 보냈다가 IP가 하루 막혔다). 새 회사 리포트 1건에 요청 100~200건. `020`(한도 초과)과 3xx·4xx는 재시도하지 않는다.
- 법령 API: `https://www.law.go.kr/DRF/lawSearch.do`·`lawService.do`, OC=`s`(정상 동작). 연결이 가끔 끊기므로 재시도를 넣는다. 전자금융거래법 현행은 2026-10-02 시행본(MST 290247, 조문 80개). `backends/data/law_pdfs/`의 PDF는 2024-12~2025-02판이라 현행 본문과 다를 수 있다.
- Tavily: `search_depth="basic"` 고정, 기업당 최대 5회, `topic="general"` + 국내 1등급 언론사 도메인, 응답 캐시 7일.
- Docker: Weaviate `1.32.4`(`ENABLE_TOKENIZER_KAGOME_KR=true`, 호스트 포트 8090·50051), Neo4j `5.26-community`(7474·7687, 로컬 비밀번호 `finsight-local`). 데이터는 `weaviate_data/`, `neo4j_data/`(커밋 안 함).

## 사용자 작업 대기

- 참조 해석기 표본 검수: `backends/eval/datasets/citation_refs.v1.csv`의 `판정(O/X)`과 틀린 줄의 `정답_대상`을 채운다(100줄).

## 코드 규칙

- `core/`(외부 클라이언트), `domain/`(I/O 없는 순수 로직, 단위 테스트), `agents/`(LangGraph 그래프·노드), `api/`(HTTP).
- 주석·docstring·커밋 메시지는 한국어. 커밋 메시지 머리말은 `[feat]`, `[fix]`, `[chore]`, `[docs]`.
- 노드는 예외를 잡아 `errors`에 "노드명: 메시지"를 더하고 자기 출력 키를 비운 채 다음 노드로 넘긴다.
- LLM은 재무 숫자를 직접 쓰지 않는다. 사실표 id를 `{{id}}` 슬롯으로 쓰고 렌더러가 표시값과 출처 툴팁으로 바꾼다.
