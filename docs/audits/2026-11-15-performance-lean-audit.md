# Finsight 성능·군더더기·가독성 감사

- 작성일: 2026-11-15
- 대상: `beaver-zip/Finsight`, `main`, `ab2ba29`
- 방법: `ponytail-audit`. 7개 영역(재무 계산, 보고서 생성, 외부 데이터 클라이언트, 법령 RAG, 채팅·뉴스, 프론트, 저장소 전체)을 영역별로 끝까지 읽고, 발견사항마다 별도 검증자가 코드를 다시 읽고 반박을 시도했다. 마지막에 누락 점검을 한 번 더 했다.
- 가정한 부하: 한 사람이 로컬 uvicorn 서버 하나를 띄워 쓴다. 서버 안에서는 asyncio 작업과 LangGraph 분기가 병렬로 돈다.
- 결과: 88개 발견, 반박으로 빠진 것 0개, 57개는 위치·심각도·수정 방법을 고쳐서 남김. 아래 본문은 그중 20개(관련 항목 묶음)다. 나머지는 [부록](#부록-본문에서-뺀-항목)에 있다.
- 상태: 1·2·3·4·6번 **fixed**(2026-11-15, 아래 각 항목의 상태 줄). 나머지는 **open**. 수정 뒤 백엔드 `189 passed, 1 deselected`.
- 이전 감사: [2026-11-11 감사](2026-11-11-refactoring-audit.md)에서 고친 11개는 다시 올리지 않았다.

## 무엇을 실행했고 무엇을 못 했나

- 실행함: 백엔드 테스트 `174 passed, 1 deselected`(26초). 검증자들이 현재 모듈을 import하는 임시 스크립트로 몇 가지를 직접 돌렸다. TTM 계산, 액면분할, DART 302, `△` 파서, 내규 조문 분리, U+2028 SSE 파싱, 폰트 서브셋 시간, 체크포인터 크기, 리랭커 첫 로딩 시간이 그 대상이다. 임시 스크립트는 저장소 밖 scratchpad에만 있다.
- 못 함: 실제 DART·Claude·Tavily·법령 API 호출, Neo4j·Weaviate 실접속, 브라우저 실행, 부하 시험. `backends/data/*.json`, `eval/results/` 내용은 읽지 않았다.

## What this repo does

Finsight는 공시·주가·뉴스를 모아 상장사 분석 리포트를 만들고, 전자금융 법령과 사용자가 올린 내규에서 조문을 찾아 근거를 붙여 답한다. 채팅은 질문을 법령·기업·뉴스로 나눠 병렬로 돌린 뒤 답을 합치고 검증한다.

## Must fix

1. **빠진 분기·연도를 0이나 다른 해로 계산한다** (`backends/app/agents/company/analysis.py:L336-353, L501-510, L709-717`)
   - **What this is:** 최근 4개 분기 합계(TTM, trailing twelve months)와 전년 대비 증감률, 잠정실적과 컨센서스 비교를 계산한다.
   - **Problem:** TTM은 `sum(f(...) or 0 ...)`로 빈 분기를 0으로 더한다. 한 분기 순이익이 없으면 PER이 71.6배에서 97.7배로 바뀌고(직접 실행), 모두 없으면 사실표에 `0원`, ROE `0.0%`가 들어간다. 같은 파일의 `_valuation`은 4개가 다 있어야 더해서 두 규칙이 다르다. 연간 증감률은 중간 사업보고서가 없으면 2년 전과 비교하면서 라벨은 "2024년 → 2025년"이라고 쓴다. 잠정실적에 매출이 없고 컨센서스에 매출이 있으면 `None / float` TypeError로 최근 분기 표가 통째로 빠진다.
   - **Fix:** 연속된 4개 분기 key(`prev_quarter`)로 찾아 하나라도 없으면 `None`을 주는 작은 함수 하나를 만들어 두 곳이 같이 쓴다. 증감률은 `heads[-2] == str(y - 1)`일 때만 넣는다. 컨센서스 비교는 `rev is not None`일 때만 나눈다. 세 경우를 `test_report_v2.py`에 회귀 테스트로 넣는다(지금 TTM·PER·ROE 숫자를 확인하는 테스트가 없다).
   - **If we skip it:** 계정 이름 하나를 못 찾은 분기만 있어도 PER·ROE·EPS가 틀린 채 리포트에 나간다.
   - **상태:** fixed, `0d5486e`. 밸류에이션 밴드와 같은 `_ttm` 규칙(연속 4개 분기가 모두 있을 때만)으로 통일했다. 테스트: `test_ttm_sums_four_consecutive_quarters`, `test_ttm_with_missing_quarter_is_dropped_not_zero`, `test_annual_growth_needs_the_previous_year`, `test_prelim_without_revenue_keeps_latest_table`(새 테스트 4개는 수정 전 코드에서 실패 확인).

2. **액면분할 뒤 시가총액·연도별 PER이 분할 비율만큼 틀린다** (`backends/app/agents/company/analysis.py:L242-244, L482-494, L952-956`)
   - **What this is:** 주가는 yfinance에서 분할이 반영된 값으로 받고, 주식 수는 공시에서 받는다.
   - **Problem:** `_shares_at`만 `_split_factor`로 주식 수를 맞춘다. 시가총액, 연도별 EPS·DPS, 발행주식 증감 비교는 분할 전 주식 수를 그대로 쓴다. 분기말 뒤 5:1 분할 입력으로 돌리면 시가총액이 9.0조원에서 1.8조원, 연도별 PER이 40.6에서 8.1이 된다. 같은 표의 TTM PER 71.6배는 그대로라 한 표 안에서 숫자가 안 맞는다. 10:1 병합은 "발행주식 90.0% 감소", 즉 소각처럼 보인다.
   - **Fix:** 세 곳에 이미 있는 `_split_factor`를 곱하거나 나눈다. 분할 입력 테스트 하나를 더한다. 피어 표(`core/peers.py:L114-125`)는 피어마다 분할 이력을 불러야 하므로 따로 하거나 `shortcut:` 주석으로 한계를 적는다.
   - **If we skip it:** 분할한 회사는 대표 숫자인 시가총액이 몇 배 틀린다.
   - **상태:** fixed, `36bc298` + `c6ebc8a`. 리뷰에서 분기말 28일 가정, 사업보고서 제출 전 분할의 EPS 이중 보정, 피어 표 자기 행 누락을 찾아 두 번째 커밋에서 고쳤다. 테스트: `test_split_keeps_per_share_values`(분할 시점 5가지). 한계: 다른 피어 행은 분할 이력이 없어 `core/peers.py`에 `shortcut:` 주석으로 남겼다. EPS 기준(배당 공시 접수일)은 K-IFRS 1033 소급 규칙에 기댄 것이라 실제 DART 응답으로는 확인하지 않았다.

3. **OpenDART 점검 응답(302)을 따라가서 예비 캐시가 안 돈다** (`backends/app/main.py:L35`, `backends/app/core/dart/client.py:L41-68, L91-108`)
   - **What this is:** DART 클라이언트는 3xx를 실패로 보고, 실패하면 만료된 캐시라도 쓰게 되어 있다.
   - **Problem:** 공유 `httpx.AsyncClient(follow_redirects=True)`가 302를 점검 HTML 페이지까지 따라가 200을 받는다. 그러면 `r.json()`에서 `JSONDecodeError`가 난다. `DartError`가 아니라서 예비 캐시 분기를 건너뛰고, 리포트 API는 502 대신 500을 준다. MockTransport로 재현했다. 기존 테스트는 `DartError("302")`를 직접 던지는 대역이라 이 경로를 못 잡는다.
   - **Fix:** `DartClient._get`의 `http.get(...)`에 `follow_redirects=False` 하나를 넣는다. 뷰어(`viewer.py`)는 `r.history`가 있으면 실패로 보고 캐시하지 않는다. 302 → `DartError` 테스트 하나를 둔다.
   - **If we skip it:** DART 점검 시간에는 어제 캐시가 있어도 기업 검색과 리포트가 500으로 실패한다. 뷰어 원문 자리에 점검 페이지가 영구 캐시될 수 있다.
   - **상태:** fixed, `84298cb` + `a129abe`. DART API·뷰어 요청만 리다이렉트를 끈다(평소 응답은 200, 리다이렉트 없음을 공개 URL로 확인). 두 번째 커밋은 오류 문구의 Location에서 질의를 빼 인증키 노출을 막는다. 테스트: `test_dart_redirect_is_dart_error_even_on_redirect_following_client`, `test_dart_viewer_redirect_is_not_cached`. 실제 점검 응답은 확인하지 못했다.

4. **원문 재무제표 파서가 `△` 음수를 못 읽는다** (`backends/app/core/dart/original.py:L30-43`)
   - **What this is:** 재무 API가 데이터 없음(013)을 줄 때 공시 원문 표에서 숫자를 읽는 대체 경로다.
   - **Problem:** `_AMT` 정규식이 `△`를 허용하지 않아 `△1,234`가 `None`이 된다. 그 줄은 건너뛰어 영업손실이 빈칸이 된다. 이 결과는 `dart.orig` 캐시에 만료 없이 남는다. `prelim._num`, `segments._amount`는 `△`를 읽어서 규칙이 서로 다르다.
   - **Fix:** 첫 줄을 `t = tok.replace(" ", "").replace("△", "-")`로 바꾸고, 캐시를 다시 읽도록 `VERSION`을 3으로 올린다. `test_original.py`에 `△` 줄 하나를 더한다.
   - **If we skip it:** 적자 분기의 손실이 리포트에서 사라진다.
   - **상태:** fixed, `d7b56b7`. 테스트: `test_triangle_negative_amounts`.

5. **내규 PDF에서 줄 첫머리의 "제N조에 따른"을 새 조문으로 자른다** (`backends/app/core/law/uploads.py:L25, L83-112`)
   - **What this is:** 올린 내규 PDF 본문을 조문 단위로 잘라 검색 인덱스에 넣는다.
   - **Problem:** `(?m)^\s*제\s*(\d+)\s*조`가 줄 첫머리에 온 참조도 조 머리로 본다. PDF는 줄바꿈이 많아 이런 줄이 흔하다. 실행해 보면 제2조 본문 뒷부분이 제1조에 붙고, `\n제5조에 따른다.`는 제목 없는 가짜 `내규:5`를 만든다. 주석은 "앞 조문에 붙인다"인데 코드는 같은 번호 조문에 붙인다.
   - **Fix:** 머리를 고를 때 매치 바로 뒤가 조사·참조 표현이면 뺀다. `heads = [m for m in _HEAD.finditer(text) if not _REF.match(text, m.end())]`. `_HEAD` 끝에 부정 전방탐색을 붙이면 `제2조(정의)에 따른`에서 되돌아가 막지 못하니 쓰지 않는다. 입력 세 개로 회귀 테스트를 더한다.
   - **If we skip it:** 내규로 답할 때 인용 조문 번호와 본문이 엇갈린다.

6. **기업+뉴스 복합 질문에서 기사 ID가 겹치고 뉴스 파이프라인이 두 번 돈다** (`backends/app/agents/orchestrator/subagents.py:L92-128`, `backends/app/agents/news/pipeline.py:L289`, `backends/app/agents/orchestrator/graph.py:L153-183`)
   - **What this is:** "삼성전자 실적이랑 최근 뉴스"처럼 묻으면 라우터가 company와 news 과업을 함께 병렬로 돌린다.
   - **Problem:** 두 쪽 모두 기사 ID를 `n1`~`n5`로 붙인다. 회사 리포트 기사(최대 7일 캐시)와 새 뉴스 기사는 거의 항상 달라서, 서로 다른 기사가 같은 `[n1]`이 된다. 근거 병합은 뒤의 값으로 덮어쓴다. 그래서 출처 링크가 다른 기사로 가고, 검증기는 다른 기사 본문으로 주장을 판정한다. 리포트 캐시가 없으면 같은 회사 뉴스 수집·분류·요약이 동시에 두 번 돌아 Tavily 크레딧과 LLM 비용도 두 배다.
   - **Fix:** news 과업이 같이 있으면 company 쪽은 `rep.news`를 비우고 쓴다. `Send` payload에 `skip_news`를 넣는 정도로 된다. 캐시가 없을 때 리포트 그래프의 `get_news`까지 건너뛰려면 같은 플래그를 `build_report`에 넘긴다. company+news 테스트 하나로 최종 출처에 `n1`이 한 번만 나오는지 본다.
   - **If we skip it:** 복합 질문 답의 출처가 틀리게 붙거나 맞는 주장이 검증에서 떨어지고, 뉴스 비용이 두 번 나간다.
   - **상태:** ID 충돌은 fixed, `7a5d088` + `cdfce90`. 첫 커밋(리포트 기사 빼기)은 리포트 본문의 `[n1]` 표기가 남고 뉴스 실패 때 기사가 사라져서, 두 번째 커밋에서 리포트 기사 id를 목록·본문·news_ids에서 함께 n101~로 옮기는 방식으로 바꿨다. 테스트: `test_company_and_news_do_not_share_article_ids`(뉴스 성공·실패). **뉴스 이중 실행은 open:** 리포트 캐시가 없으면 리포트 생성 안의 뉴스 수집이 채팅 뉴스 과업과 따로 한 번 더 돈다. 리포트에 기사가 필요하고 캐시되는 결과물이라 이번에는 바꾸지 않았다.

## Should fix

7. **수집 일부가 실패한 리포트도 7일 동안 완성본으로 캐시된다** (`backends/app/agents/company/collect.py:L85-130`, `backends/app/core/dart/history.py:L94-101`, `backends/app/api/report.py:L67-69`)
   - **What this is:** 재무 분기, 주식 수, 배당, 부문 자료를 병렬로 모은다.
   - **Problem:** `gather(return_exceptions=True)` 뒤 `isinstance` 필터가 예외를 버리고, `_snap`은 한도 초과(020)·타임아웃을 INFO 로그만 남기고 `None`으로 바꾼다. `errors`에는 아무것도 남지 않는다. `_cacheable`은 `errors`를 보지 않으므로, 한 분기 실패로 빈 리포트(4분기 값도 같이 빔)가 7일 캐시된다. 강제 새로고침 방법도 없다.
   - **Fix:** 버린 예외와 실패한 분기를 `err`에 `collect.` 접두어로 남긴다. `_cacheable`(또는 TTL)은 `collect.` 오류가 있으면 1시간 같은 짧은 TTL로 낮춘다. 아예 캐시하지 않으면 매번 실패하는 회사가 요청마다 LLM 비용을 낸다. `_snap`의 try/except만 지우면 한 분기 실패로 이력 전체가 비니 그렇게 하지 않는다.
   - **If we skip it:** 잠깐의 DART 장애가 일주일짜리 빈 리포트로 남고, 사용자는 이유를 모른다.

8. **일시 오류나 빈 응답을 너무 오래 캐시하고, 서버를 켤 때 예비 캐시를 지운다** (`backends/app/core/dart/shares.py:L35-46`, `backends/app/core/search/extract.py:L22-58`, `backends/app/core/law/client.py:L62-71`, `backends/app/main.py:L34`)
   - **What this is:** 외부 응답을 SQLite 캐시에 둔다. 재무제표는 지난 감사에서 빈 응답 5분 규칙을 넣었다.
   - **Problem:** 같은 규칙이 다른 곳에는 없다. 주식 수·배당 빈 응답은 만료 없이 저장해, 보고서 제출 당일 한 번 부르면 그 분기는 영원히 "없음"이다. 기사 본문 수집은 503·타임아웃 결과도 7일 저장해서, 발행일이 없는 그 기사는 일주일 동안 후보에서 빠진다. 법령 API는 200이면 오류 본문도 저장하고 키에 OC가 없다. 그래서 키를 고친 뒤에도 캐시된 오류를 읽는다. 또 `main.py`의 `purge_expired()`가 시작할 때 만료 행을 지워, "장애 때는 만료 캐시라도 쓴다"는 `cached()` 규칙의 예비값을 없앤다. `corp_list`만 `cached()`를 안 쓰는 것도 같은 문제다.
   - **Fix:** `shares.py`는 `ttl_s=None if rows else 300` 한 줄. `extract.py`는 예외·5xx·429일 때 캐시하지 않는다. 법령 검색은 `<resultCode>00</resultCode>`가 없으면 `LawApiError`를 낸다(`scripts/check_keys.py`와 같은 기준). `purge_expired` 호출과 메서드를 지우고, `corp_list`를 `cached("dart.corplist", ...)`로 감싼다.
   - **If we skip it:** 한 번의 순간 장애가 며칠, 또는 영구적인 데이터 누락으로 남는다.

9. **같은 이름으로 다시 올리면 적재 전에 원본 PDF를 덮어쓴다** (`backends/app/core/law/uploads.py:L44-53`, `backends/app/api/documents.py:L52-106`)
   - **What this is:** 업로드는 파일을 저장하고, 등록 단계가 그 파일을 읽어 벡터를 바꾼다. 지난 감사에서 벡터·메타데이터는 실패해도 보존하게 고쳤다.
   - **Problem:** `save_file`이 `FILES/<이름>`에 바로 덮어쓴다. 스캔 PDF라 적재가 실패하면 검색은 옛 문서인데 내려받기는 새 파일을 준다. 옛 원본은 사라진다.
   - **Fix:** 이미 등록된 이름이면 `<이름>.pending`에 쓰고, `uploads.put` 성공 뒤 `replace`로 바꾼다. 실패하면 pending을 남겨 재시도할 수 있게 한다. 교체 실패 테스트에 "원본 바이트가 그대로다" 한 줄을 넣는다.
   - **If we skip it:** 실패한 교체 한 번으로 원본 내규 PDF를 잃는다.

10. **문체 지적만 남아도 "검증 실패"가 되어 경고가 뜨고 매번 다시 만든다** (`backends/app/agents/company/nodes.py:L204-216, L245-251`, `backends/app/api/report.py:L67-69`)
    - **What this is:** 리포트 문장을 검증하고 실패한 블록을 뺀다. `finalize`는 style 지적을 실패로 보지 않는다.
    - **Problem:** `passed=not issues`가 style 지적까지 세서, 소제목이 차트 제목과 비슷하거나 금지 어미가 하나 남으면 `passed=False`가 된다. 그러면 빠진 문장이 없는데도 "근거 문제가 남은 문장은 제외했습니다" 경고가 뜨고, 캐시하지 않아 다음 요청 때 작성·검증·재작성 2번을 다시 돈다.
    - **Fix:** 경고와 `_cacheable`을 `[i for i in ver.issues if i.type != "style"]` 기준으로 정한다. style만 남은 회귀 테스트 하나를 더한다.
    - **If we skip it:** 틀린 경고가 뜨고, 같은 기업을 열 때마다 유료 파이프라인이 다시 돈다.

11. **검증 재사용 캐시가 문장만 키로 써서 근거 변경을 무시한다** (`backends/app/agents/verifier.py:L173-185`)
    - **What this is:** 재작성 루프에서 이미 판정한 주장은 다시 LLM에 보내지 않는다.
    - **Problem:** 키가 `c.text`뿐이다. 재작성이 같은 문장에 `metric_ids`만 채워 와도 예전 "근거 없음" 판정을 그대로 받아 결국 빈 제목이 된다. 반대로 `news_ids`를 지웠는데 예전 "통과"가 남아 근거 없는 주장이 통과할 수 있다. 채팅 검증도 같은 함수를 쓴다.
    - **Fix:** 키를 `c.model_dump_json(exclude={"id"})`로 바꾼다. 두 곳만 고치면 된다. 근거 ID만 바뀐 주장을 다시 판정하는 테스트 하나를 더한다.
    - **If we skip it:** 재작성이 고친 내용을 검증이 못 보고, 반대로 근거가 빠진 문장이 통과할 수 있다.

12. **되묻기 뒤 합성이 원래 질문 대신 짧은 답을 질문으로 쓴다** (`backends/app/agents/orchestrator/graph.py:L77-81, L104-106, L197, L231`)
    - **What this is:** 라우터가 "어느 회사인가요?"라고 되물으면 사용자 답을 메시지에 붙이고 다시 라우팅한다.
    - **Problem:** 합성과 재작성은 `_question`(마지막 사람 메시지)을 쓴다. "그 회사 영업이익 추이는?" → "카카오뱅크"이면 합성 프롬프트가 `질문: 카카오뱅크`가 되어 일반 요약을 쓴다. 라우터가 지시어를 풀어 쓴 `SubTask.instruction`은 합성에 쓰이지 않는다.
    - **Fix:** 합성·재작성 질문을 `"\n".join(t.instruction for t in state["route"].sub_tasks)`로 바꾸고 `_question`을 지운다. 되묻기 경로 테스트를 하나 더한다(지금 없음).
    - **If we skip it:** 되묻기와 후속 질문의 답이 질문 의도에서 벗어난다.

13. **기사 요약 하나가 API 오류를 내면 뉴스 결과 전체가 사라진다** (`backends/app/agents/news/pipeline.py:L156-176, L281-288`)
    - **What this is:** 고른 기사 5건을 병렬로 요약하고, 실패한 건은 다음 순위 기사로 채운다.
    - **Problem:** `summarize`는 `StructuredOutputError`만 잡는다. SDK 재시도 뒤에도 남은 타임아웃·529가 `gather` 밖으로 나가 이미 성공한 요약 4건을 버린다. 진행 중이던 다른 요약은 취소되지 않아 비용은 그대로 나간다. 백업 요약도 하나씩 차례로 돈다.
    - **Fix:** except를 `(llm.StructuredOutputError, anthropic.APIError)`로 넓혀 그 기사만 `None` 처리한다. 예산 예외는 그대로 올린다. 백업 루프는 부족한 수만큼 `gather`로 돌린다.
    - **If we skip it:** API가 잠깐 불안정하면 뉴스가 통째로 빠지고 비용만 나간다.

14. **이벤트 루프를 수 초 막는 작업과 첫 요청 지연** (`backends/app/agents/company/nodes.py:L261`, `backends/app/agents/company/render.py:L748-784`, `backends/app/core/reranker.py:L17-46`)
    - **What this is:** 리포트 HTML에 쓰인 글자만 잘라 폰트를 넣는다. 법령 검색은 CrossEncoder 리랭커를 쓴다.
    - **Problem:** 폰트 서브셋이 async 노드 안에서 동기로 돌아 리포트마다 약 3.2~4초 동안 서버 전체(채팅 SSE 포함)가 멈춘다. 이 렌더 테스트 4개가 백엔드 테스트 시간의 절반 이상을 쓴다. 리랭커는 첫 법령 질문 때 torch import·모델 로딩으로 약 23초가 걸리고, 그동안 락을 잡아 다른 리랭크도 기다린다(이 Mac에서 측정).
    - **Fix:** `html = await asyncio.to_thread(render_report, ...)` 한 줄. `test_report_v2.py`에는 `font_faces`를 바꾸는 autouse 픽스처를 둔다(`test_report_verification.py`에 같은 방식이 있다). 리랭커는 lifespan에서 `law_tools`가 있을 때 락 안에서 `asyncio.to_thread(_model)`로 미리 올린다.
    - **If we skip it:** 리포트를 만들 때마다 서버가 몇 초씩 멈추고, 서버를 켠 뒤 첫 법령 답이 20초 이상 늦다.

15. **리포트 규제 검토가 쓸데없는 LLM 호출을 하고 줄 서서 돈다** (`backends/app/agents/company/nodes.py:L141-174`, `backends/app/agents/company/graph.py:L27-29`)
    - **What this is:** 리스크 기사나 제재 공시가 있으면 질문을 만들어 법령 서브그래프에 묻는다.
    - **Problem:** 법령 러너가 없을 때(`legal_runner=None`, eval 기본값)도 질문 생성 LLM을 먼저 부르고 결과를 버린다. 러너가 있을 때는 질문 생성과 법령 그래프 실행을 하나씩 await하고, 쓰지 않는 `analyze` 결과를 기다린다.
    - **Fix:** runner `None` 검사를 질문 생성 전으로 올린다. 질문 생성과 `runner(q)`를 `asyncio.gather`로 돌리고, 그래프에서 `regulatory_check`를 `analyze`와 나란히 둔다(`add_edge(["analyze","regulatory_check"], "write")`).
    - **If we skip it:** 버려지는 LLM 비용이 나가고, 규제 트리거가 있는 기업의 리포트가 수십 초 늦다.

16. **채팅 체크포인터가 단계마다 전체 상태를 쌓고 지우지 않는다** (`backends/app/api/chat.py:L58-59`, `backends/app/main.py:L39-43`)
    - **What this is:** LangGraph가 대화 상태를 `state.sqlite`에 저장한다. 되묻기 재개에 필요하다.
    - **Problem:** 기본 durability가 단계마다 저장해서, 한 턴에 상위 체크포인트 약 10개와 하위 그래프(법령·리포트) 체크포인트까지 쌓인다. 측정으로는 2턴에 약 2.4MB였다. 프론트는 새로고침마다 새 thread를 만들고, 옛 thread를 지우는 코드는 없다.
    - **Fix:** `astream(..., durability="exit")` 한 줄. 측정으로는 같은 2턴이 108KB였고, 되묻기 재개도 정상이었다.
    - **If we skip it:** 질문마다 DB가 수백 KB~MB씩 커진다. 정답 문제는 아니고 디스크와 쓰기 시간 문제다.

17. **프론트 채팅·보고서의 오류 처리 버그 3개** (`frontends/corp-advisor--app/src/api/chatStream.ts:L19`, `src/hooks/useChatStream.ts:L37-73`, `src/pages/Report.tsx:L161-178`)
    - **What this is:** 채팅은 SSE 스트림을 읽고, 보고서 화면은 백엔드 HTML을 받아 보여 준다.
    - **Problem:** (a) `/^data: (.*)$/m`의 `.`이 U+2028에서 멈춘다. 백엔드는 `ensure_ascii=False`라 이 글자를 그대로 보내므로, 기사에서 온 이 글자 하나로 답 대신 "답변을 가져오지 못했습니다"가 뜬다(node로 재현). (b) 채팅 화면을 떠나도 fetch를 끊지 않아, 백엔드가 결과를 버릴 LLM·Tavily 호출을 끝까지 한다. (c) 정기보고서가 없는 회사는 백엔드가 200과 빈 본문을 주고, 화면은 아무 안내 없이 검색 화면으로 돌아간다.
    - **Fix:** (a) `/^data: ([^\n]*)/m`과 테스트 하나. (b) `useEffect(() => () => abortRef.current?.abort(), [])`. 연결이 끊기면 Starlette가 생성기를 취소한다. (c) `const data = await response.text();` 다음 줄에 `if (!data.trim()) throw new Error("empty");`.
    - **If we skip it:** 드물게 답이 사라지고, 나간 화면의 비용이 계속 나가고, 빈 화면의 원인을 알 수 없다.

18. **쓰지 않는 backend·frontend 컨테이너 설정이 `.env`를 이미지에 넣고 8000 포트를 LAN에 연다** (`docker-compose.yml:L42-72`, `backends/Dockerfile`, `frontends/corp-advisor--app/Dockerfile`)
    - **What this is:** 문서상 실행은 `docker compose up -d weaviate neo4j`뿐이다. backend·frontend 서비스는 옛 설정이다.
    - **Problem:** 서비스 이름 없이 `docker compose up -d`를 하면 backend 이미지를 만든다. `.dockerignore`가 없어서 `backends/.env`(API 키)와 1.6GB `.venv`가 이미지 층에 들어간다. 컨테이너는 `0.0.0.0:8000`으로 떠서, 같은 와이파이의 누구나 인증 없는 `/report`, `/chat`으로 유료 API 예산을 쓸 수 있다.
    - **Fix:** 두 서비스와 Dockerfile 두 개를 지운다(참조하는 곳 없음). 남겨야 하면 `.dockerignore`를 두고 포트를 `127.0.0.1:8000:8000`으로 바꾼다.
    - **If we skip it:** 명령 하나만 잘못 쳐도 키가 이미지에 남고 유료 API가 네트워크에 열린다.

## Nice to have

19. **프론트의 죽은 코드와 꺼진 검사** (`src/pages/Report.tsx:L18-98, L196, L234-248`, `src/index.css:L126-201`, `src/ChatContext.tsx`, `src/pages/Chatbot.tsx:L50-86`, `tsconfig.app.json`, `tsconfig.node.json`, `.github/workflows/ci.yml`)
    - **What this is:** v1 보고서 시절의 렌더러·저장 로직과 설정이 남아 있다.
    - **Problem:** 보고서 렌더러는 백엔드가 내지 않는 `<script>`를 다시 실행하고 v1 분기·CSS 약 75줄을 들고 있다. `ChatContext`의 localStorage 저장은 새로고침해도 복원하지 않으면서, 폰트가 든 큰 HTML을 매번 쓴다. 가짜 타자 효과는 16ms마다 모든 말풍선의 마크다운을 다시 파싱하고, 다음 답이 오면 앞 답을 처음부터 다시 친다. `tsc -b`는 루트 `tsconfig.json`만 읽어서 app/node 설정의 엄격 옵션이 아무 데서도 돌지 않는다. CI는 lint를 돌리지 않고, 지금 `npm run lint`는 `ChatContext.tsx` 때문에 실패한다. `@tailwindcss/typography`, `tailwind-scrollbar-hide`는 클래스 사용처가 없다.
    - **Fix:** 렌더러를 `dangerouslySetInnerHTML` 한 줄로 바꾸고 링크 새 탭 속성은 `render.py`의 `<a>`로 옮긴다. `ChatContext`를 지우고 Report는 `useState("")` 하나만 둔다. 타자 효과를 지우고 onFinal에서 완성본을 바로 넣는다. app 설정을 루트 tsconfig로 합치고 두 파일을 지운다. CI에 `npm run lint`를 더한다. 두 의존성을 지운다. 쓰지 않는 props와 `BASE_URL` 복사 7곳은 같이 정리한다.
    - **If we skip it:** 약 350줄의 죽은 코드가 남고, 긴 대화에서 화면이 버벅이고, 엄격 검사가 켜져 있다고 착각한다.

20. **백엔드의 중복 경로와 죽은 코드** (`backends/app/api/report.py:L72-86, L129-169`, `backends/app/agents/company/analysis.py`, `backends/app/core/dart/*`, `backends/app/agents/company/render.py:L42-45, L623-626`, `backends/app/core/llm.py:L46-52`)
    - **What this is:** 리포트 API, 계산 모듈, DART 파서 곳곳의 군더더기다.
    - **Problem:** `/report/{code}/stream`은 `build_report`의 캐시·실행·결과 변환을 손으로 다시 만들었고, 프론트는 부르지 않는다. 지난 감사의 캐시 수정도 두 곳에 넣어야 했다. `analysis.py`에는 쓰기만 하고 읽지 않는 ctx 키 15곳이 있고, `Inputs`가 `Collected`의 필드 16개를 다시 적는다. 단위 정규식이 3번, 분기 키·날짜 파서가 2번씩 있고, `viewer.section`, `market.INDEX_SOURCES`, `NEWS_MODE`는 쓰는 곳이 없다. `render.sgn`·`_md`는 `fmt.signed`·`md`를 다시 만든 것이고, 회사명 정리와 기사 발췌 함수도 두 벌씩 있다. `langsmith`는 requirements에 있는데 없을 때의 대체 코드가 남아 있다.
    - **Fix:** 진행 표시가 필요 없으면 `report_stream`과 관련 테스트·README 줄을 지운다. 필요하면 진행 이벤트만 보내고 결과와 캐시는 `build_report`에 맡긴다. 나머지는 하나만 남기고 import해서 쓴다. `Inputs(Collected)` 상속은 `kw_only=True`가 있어야 깨지지 않는다.
    - **If we skip it:** 같은 규칙을 두 곳에서 고쳐야 하고, 1107줄 계산 파일에서 진짜 의존을 찾기 어렵다.

## 판단

**Verdict:** 구조는 건강하다. 먼저 1~4번(재무 숫자)과 6번(복합 질문 출처)을 고친다. 리포트에 틀린 숫자나 틀린 출처가 조용히 나가는 문제라 가장 비싸다. 그다음 7·8번(장애가 오래 남는 캐시)을 고친다.

**Lean:** 약 -560줄, 의존성 -2개(`@tailwindcss/typography`, `tailwind-scrollbar-hide`) 가능. 대부분 프론트와 컨테이너 설정이다. 버그 수정에는 회귀 테스트가 붙으므로 전체 줄 수는 이보다 덜 준다.

**Not checked:** 실제 외부 API·DB·브라우저 동작, 부하. `eval/` 코드는 의존성(`scipy`, `yaml`이 requirements에 없음)만 봤다. `render.py`의 차트 함수 각각의 숫자 정확성은 전수 확인하지 않았다.

## 부록: 본문에서 뺀 항목

검증을 통과했지만 작거나 영향이 좁아 본문에서 뺀 항목이다. 모두 open이다.

| 분류 | 종류 | 제목 | 위치 |
| --- | --- | --- | --- |
| Should | bug | 잠정실적 발표 횟수 라벨의 '최근 제외'가 틀릴 때가 있다 | `backends/app/agents/company/analysis.py:L1008-L1012` |
| Should | test | render의 HTML 이스케이프·URL 필터를 지키는 테스트가 없음 | `backends/app/agents/company/render.py:L38-39` |
| Should | bug | corp_list만 만료 캐시 대체를 안 써서 점검 때 기업 검색이 통째로 실패 | `backends/app/core/dart/corp.py:L15-31` |
| Should | bug | 우선주 후보가 5·7·9만 봐서 신형 우선주(…K)를 놓치고, 실패한 시세는 매번 다시 부름 | `backends/app/core/market.py:L40-55` |
| Should | test | OpenDART 상태 처리·보고서명 해석·주식 수 파서에 테스트가 없음 | `backends/app/core/dart/client.py:L41-88` |
| Should | speed | 재검색을 2번 한 뒤의 세 번째 충분성 판단 LLM 호출은 결과와 상관없이 항상 '답변'으로 간다 | `backends/app/agents/legal/graph.py:L133-L158` |
| Should | risk | 업로드 메타데이터 삭제와 코퍼스 저장은 원자적으로 쓰지 않는다(put만 임시 파일+교체를 쓴다) | `backends/app/core/law/uploads.py:L136-L165` |
| Should | bug | revise 실패 때 넣는 passed=True는 바로 덮어써지고, 유료 재작성을 한 번 더 부른다 | `backends/app/agents/orchestrator/graph.py:L228-236, L204-225, L318` |
| Should | test | 기업 후보 고르기와 되묻기 실패 분기에 테스트가 없다 | `backends/app/agents/orchestrator/graph.py:L100-150` |
| Should | risk | 업로드·삭제가 실패해도 화면에 아무 안내가 없다 | `frontends/corp-advisor--app/src/components/steps/UploadModal.tsx:L33-L34` |
| Should | speed | 기업 목록 약 11만 줄을 조회할 때마다 SQLite에서 JSON으로 다시 읽는다 | `backends/app/core/dart/corp.py:L15-31, L38-55, L66-73` |
| Should | test | 채팅 기업 후보 규칙(listed_candidates)을 지키는 테스트가 없다 | `backends/app/agents/orchestrator/graph.py:L133-150` |
| Nice | bug | 순이익 대표 차트가 연결 순이익과 지배주주 순이익을 섞는다 | `backends/app/agents/company/analysis.py:L446` |
| Nice | lean | Metric 값 기반 표시 경로와 출처 필드는 쓰이지 않는다 | `backends/app/domain/binding.py:L14, L26-L41, L125-L128` |
| Nice | lean | 같은 일을 하는 작은 헬퍼가 여러 곳에 있다 | `backends/app/agents/company/analysis.py:L104-L109, L261-L262, L463, L837-L838, L694-L699` |
| Nice | bug | 핵심 수치 카드: 52주 카드가 지수 실패에 묶여 있고, 시가총액 반올림 규칙이 둘이다 | `backends/app/agents/company/analysis.py:L245, L252-L258, L1034-L1058 (지수 실패 경로: collect.py:L89)` |
| Nice | speed | 검증의 근거 판정과 투자 권유 판정 LLM 호출이 차례로 실행됨 | `backends/app/agents/verifier.py:L180` |
| Nice | bug | 뉴스 노드가 공시 목록을 따로 다시 불러 DART를 중복 호출함 | `backends/app/agents/company/nodes.py:L93` |
| Nice | lean | 작은 죽은 코드와 중복 분기(_cell 튜플, Units.s, 오류 매핑 두 벌) | `backends/app/agents/company/render.py:L560-566` |
| Nice | bug | cited()가 max_tokens 16000인데 스트리밍을 안 써서 120초 타임아웃 위험이 있음 | `backends/app/core/llm.py:L167-196 (비교: llm.py:L131-137), 호출처 backends/app/agents/legal/answer.py:L119-125` |
| Nice | speed | 기업 검색마다 20MB 기업 목록 JSON을 이벤트 루프에서 다시 파싱 | `backends/app/core/dart/corp.py:L15-74` |
| Nice | scale | API의 rcept_no가 목록과 다르면 재무 캐시가 매번 무시됨 | `backends/app/core/dart/financials.py:L60-80` |
| Nice | speed | 원문 재무제표를 한 분기씩 차례로 받고 큰 XML을 루프 스레드에서 파싱 | `backends/app/core/dart/history.py:L112-132` |
| Nice | bug | 적재에 실패했거나 아직 적재하지 않은 업로드 파일은 목록에도 안 나오고 지울 수도 없다 | `backends/app/api/documents.py:L109-L127` |
| Nice | bug | 관리 토큰 헤더에 ASCII가 아닌 글자가 있으면 401이 아니라 500이 난다 | `backends/app/api/admin.py:L20-L25` |
| Nice | scale | 업로드 파일 전체를 메모리로 읽은 뒤에야 20MB 제한을 검사한다 | `backends/app/api/documents.py:L55-L58` |
| Nice | lean | 같은 일을 하는 헬퍼가 여러 곳에 있다: 공백 제거 키와 별표 번호 표기 | `backends/app/core/law/parser.py:L44-L45` |
| Nice | lean | LawTools.get_article과 search_law(levels)는 운영 코드에서 쓰지 않는다 | `backends/app/agents/legal/tools.py:L111-L129` |
| Nice | test | OC 인증키를 가리는 scrub과 법령 API 재시도 분기에 테스트가 없다 | `backends/app/core/law/client.py:L21-L60` |
| Nice | speed | 재검색 루프마다 이미 점수를 낸 확장 후보를 같은 질문으로 다시 리랭크한다 | `backends/app/agents/legal/graph.py:L101-L130` |
| Nice | lean | 쓰이지 않는 인자·설정 조회·결과 필드 | `backends/app/agents/orchestrator/subagents.py:L92, L120-123` |
| Nice | lean | 같은 일을 하는 코드가 두 곳에 있다 | `backends/app/api/chat.py:33-34 vs backends/app/api/report.py:129-130` |
| Nice | test | 채팅 SSE 생성기에 테스트가 없다 | `backends/app/api/chat.py:L45-85` |
| Nice | test | 뉴스 재검색·백업 요약 분기에 테스트가 없다 | `backends/app/agents/news/pipeline.py:L250-297` |
| Nice | risk | 기사 본문 속 지시문을 막는 경계가 없다 | `backends/app/agents/news/pipeline.py:L96-106, L114-118, L139-141, L157` |
| Nice | bug | 기업 검색 응답이 늦게 오면 이전 검색 결과가 새 결과를 덮어쓴다 | `frontends/corp-advisor--app/src/hooks/useCorpData.ts:L39-L72` |
| Nice | lean | 업로드 모달의 단계 state가 Chatbot에 올라가 있고, 파일 이름도 오타(TirggerModal)다 | `frontends/corp-advisor--app/src/pages/Chatbot.tsx:L33-L45, L232-L246` |
| Nice | lean | 기업명 검색 정렬을 백엔드와 프론트가 똑같이 두 번 한다 | `frontends/corp-advisor--app/src/hooks/useCorpData.ts:L12-27, L61` |
| Nice | lean | scipy는 이항검정 하나에만 쓰고, eval이 쓰는 yaml·scipy가 requirements에 없다 | `backends/eval/stats.py:L31-41` |
| Nice | lean | 쓰지 않는 상수와 한 줄짜리 중복 몇 개 | `backends/app/core/market.py:L9` |
| Nice | speed | embed()가 호출마다 새 HTTP 연결을 만들고, 세마포어가 있는데도 배치를 하나씩 보낸다 | `backends/app/core/embeddings.py:L13-L14, L25, L28-L39` |

본문 20개에 묶인 원 발견사항은 47개, 부록은 41개다.
