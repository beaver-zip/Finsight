# 로컬 개발 환경 (macOS)

맥에서 Weaviate·Neo4j는 Docker로, 백엔드·프론트는 직접 실행한다. Claude Code는 레포 루트에서 띄운다.

## 준비물

| 항목 | 설치 | 확인 |
| --- | --- | --- |
| Homebrew | https://brew.sh 의 설치 명령 | `brew --version` |
| Git, Python 3.13, Node.js 20+ | `brew install git python@3.13 node` | `python3.13 --version`, `node --version` |
| Docker Desktop(또는 OrbStack) | https://www.docker.com/products/docker-desktop/ | `docker info` |
| Claude Code | `curl -fsSL https://claude.ai/install.sh \| bash` 또는 `brew install --cask claude-code` | `claude --version` |

- Python은 3.11~3.13이면 된다.
- Docker Desktop은 설정 > Resources에서 메모리를 4GB 이상 준다.
- Claude Code는 Pro·Max·Team·Enterprise 또는 Console 계정이 있어야 한다. 데스크톱 앱에서도 쓸 수 있다.

## 1. 레포 받기

```bash
cd ~/PycharmProjects          # 원하는 위치
git clone -b v2 https://github.com/beaver-zip/finsight.git
cd finsight
```

비공개 레포라 인증을 물으면 `gh auth login`(GitHub CLI)이나 SSH로 로그인한다.

## 2. 레포 밖 파일 넣기

`.env`와 캐시는 커밋하지 않으므로 따로 받은 파일을 넣는다.

```bash
mv ~/Downloads/finsight.env backends/.env            # 키와 로컬 설정 (필수)
mkdir -p backends/data/cache
mv ~/Downloads/cache.sqlite backends/data/cache/     # 비용 장부와 수집 캐시 (선택)
```

`cache.sqlite`를 넣으면 지금까지의 비용 장부(누적 0.16달러)가 이어져 10달러 예산 차단이 정확해지고, 삼성전자 등 이미 수집한 DART·뉴스 응답을 다시 받지 않는다.

## 3. 세팅 스크립트

```bash
bash scripts/setup_local.sh
```

파이썬 가상환경과 의존성 설치 → Weaviate·Neo4j 컨테이너 실행 → 단위 테스트 → 키·DB 연결 확인 순서로 돈다. 처음에는 의존성(torch 등)과 Docker 이미지(약 1.3GB)를 받느라 10분 안팎 걸릴 수 있다. 마지막 표에 8줄이 모두 `OK`(LangSmith는 `-`여도 됨)면 끝이다.

## 4. 실행

```bash
# 터미널 1: 백엔드
cd backends && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# 터미널 2: 프론트
cd frontends/corp-advisor--app
npm install
npm run dev
```

- 프론트: http://localhost:5173 (리포트 화면만 동작한다. 채팅·법령 검색·업로드는 아직 백엔드가 없다)
- 예산 현황: http://localhost:8000/health/budget
- 리포트 직접 보기: http://localhost:8000/report/00126380 (삼성전자. 처음 만들 땐 1~3분, LLM 비용 약 0.01~0.02달러. 같은 회사는 7일 캐시)
- Neo4j 브라우저: http://localhost:7474 (`neo4j` / `finsight-local`)

## 5. Claude Code로 작업

```bash
cd ~/PycharmProjects/finsight
claude
```

- 처음 실행하면 브라우저로 로그인한다.
- 레포 루트의 `CLAUDE.md`를 세션마다 자동으로 읽는다(작업 방식, 보안 규칙, 현재 상태, 알려진 버그, 결정 대기 목록). 설계서는 `docs/design.md`.
- 첫 메시지 예: "CLAUDE.md랑 docs/design.md 읽고 로컬 환경이 제대로 떴는지 확인해줘"

## 주의

- `ANTHROPIC_API_KEY`를 셸 설정(`~/.zshrc` 등)에 export하지 않는다. Claude Code가 그 키를 쓸지 물어보는데, 이 키는 프로젝트 예산(10달러)용이다. 백엔드는 `backends/.env`에서 키를 읽으므로 export할 필요가 없다.
- `backends/.env`, `backends/data/`, `weaviate_data/`, `neo4j_data/`는 커밋되지 않는다.
- DB 끄기 `docker compose stop`, 다시 켜기 `docker compose up -d weaviate neo4j`. 데이터는 `weaviate_data/`, `neo4j_data/`에 남는다.
- 메모리가 8GB인 맥은 DB 두 개, 리랭커 모델, 백엔드, 프론트를 한꺼번에 띄우면 빠듯하다. 안 쓰는 것은 끈다.
