# 문서 교체 실패 회귀 검증

날짜: 2026-11-11. 대상: 감사 3번. 상태: 구현·로컬 검사 완료, 실제 Weaviate 검사 대기.

## 변경

- `documents.register`는 새 청크를 고유 `vector_name`으로 저장하고 성공한 뒤 `uploads.put`으로 활성 세대를 전환한다. 실패하면 새 세대만 정리한다.
- `articles.json`은 같은 디렉터리의 임시 파일을 완성한 뒤 원자적으로 교체한다. 실패 전의 메타데이터를 유지한다.
- Weaviate 스키마 변경 없이 업로드 청크의 `law_name`에 내부 세대 이름을 저장한다. 조문·heading·표시 이름은 유지하고 검색 반환 시 원래 문서명으로 복원한다.
- 검색은 메타데이터가 가리키는 업로드 세대만 포함한다. 구세대나 부분 삽입 데이터는 검색하지 않는다. 공식 법령과 level 필터는 유지한다.
- `vector_name` 없는 기존 메타데이터도 읽는다. 삭제도 활성 세대 이름을 사용한다.

## 실행 결과

- 수정 전 신규 회귀 3개: **3 failed**. 삽입 전 오류, 부분 삽입 오류, 메타데이터 저장 오류 모두 기존 벡터 소실을 확인했다.
- 수정 후 `tests/test_documents.py`: **13 passed, 1 deselected**.
- 전체 `.venv/bin/python -m pytest -q`: **132 passed, 1 deselected, 4 warnings**, 16.66초.
- 프론트 `npm run build`: TypeScript/Vite 성공. 기존 브라우저 데이터 갱신 경고 유지.
- `git diff --check`: 통과.
- `pytest -q tests/test_documents.py -m live`: **1 skipped, 13 deselected**. 테스트용 DB 포트가 없어 실행되지 않았다.

임베딩/부분 삽입/메타데이터 실패, 재시도, 다른 문서 보존, 첫 등록 실패, 기존 형식 교체, 구세대 정리 실패, 파일 교체 실패, 검색의 활성 세대 선택과 표시 이름을 검사한다. 기본 검사에서는 외부 벡터 DB와 임베딩을 대체하며 실제 서버의 필터 동작까지 증명하지 않는다.

## 실제 DB 검사 재개

현재 환경에서 Docker 명령을 찾지 못했다. 별도 테스트 Weaviate(프로젝트 기준 1.32.4, `ENABLE_TOKENIZER_KAGOME_KR=true`)를 준비한 뒤 `backends/`에서 실행한다. 아래 포트 변수에는 테스트 서버의 실제 포트를 지정한다.

```bash
FINSIGHT_TEST_WEAVIATE_HTTP_PORT=<HTTP포트> \
FINSIGHT_TEST_WEAVIATE_GRPC_PORT=<GRPC포트> \
.venv/bin/python -m pytest -q tests/test_documents.py -m live
```

테스트는 localhost에 연결하고 UUID 기반 고유 컬렉션만 생성·삭제한다. 운영 컬렉션을 초기화하지 않는다. 임베딩은 고정 벡터로 대체하고 실제 저장·부분 삽입 후 실패·검색·교체·삭제를 확인한다. 유료 API는 호출하지 않는다.

## 남은 한계

- 프로세스 강제 종료나 정리 실패 시 비활성 벡터가 남을 수 있다. 검색에서는 제외하지만 자동 청소 작업은 추가하지 않았다.
- 업로드 단계에서 원본 PDF를 덮어쓰는 기존 동작은 유지한다. 이번 보존 대상은 기존 검색 벡터와 조문 메타데이터이며 PDF 버전 보관 기능은 아니다.
- 기존 프로세스별 메타데이터 캐시·락을 유지한다. 다중 워커 간 동기화, 교체와 동시에 진행 중인 검색의 스냅샷 일관성, 전원 장애 내구성은 보장하지 않는다.
- 조문 ID 충돌(감사 7번)은 별도 미수정 항목이다.
- 실제 Weaviate 검사 통과 전까지 이 항목을 전체 검증 완료로 표시하지 않는다.
