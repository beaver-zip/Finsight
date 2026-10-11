#!/usr/bin/env bash
# 맥 로컬 개발 환경을 처음 한 번 세팅한다: 파이썬 가상환경·의존성 → Weaviate·Neo4j → 단위 테스트 → 키·DB 확인.
# 사용: 레포 루트에서 bash scripts/setup_local.sh  (다시 실행해도 된다)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

say() { printf '\n== %s\n' "$1"; }
die() { printf '\n[중단] %s\n' "$1" >&2; exit 1; }

say "1/5 준비물 확인"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 &&
     "$c" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done
[ -n "$PY" ] || die "파이썬 3.11~3.13이 필요하다. brew install python@3.13 후 다시 실행해."
echo "파이썬: $("$PY" --version)"
command -v docker >/dev/null 2>&1 || die "Docker가 없다. Docker Desktop(또는 OrbStack)을 설치해."
docker info >/dev/null 2>&1 || die "Docker가 꺼져 있다. Docker Desktop을 켜고 다시 실행해."
[ -f backends/.env ] || die "backends/.env가 없다. 받은 finsight.env를 옮겨: mv ~/Downloads/finsight.env backends/.env"

say "2/5 파이썬 가상환경과 의존성 (처음엔 몇 분 걸린다)"
cd backends
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data/cache
cd "$ROOT"

say "3/5 Weaviate·Neo4j 실행 (처음엔 이미지 약 1.3GB를 받는다)"
docker compose up -d weaviate neo4j
printf 'Weaviate 기다리는 중'
for i in $(seq 1 90); do
  if curl -fsS http://localhost:8090/v1/.well-known/ready >/dev/null 2>&1; then echo " → 준비됨"; break; fi
  [ "$i" = 90 ] && die "Weaviate가 3분 안에 뜨지 않았다. docker compose logs weaviate 로 확인해."
  printf '.'
  sleep 2
done
printf 'Neo4j 기다리는 중'
for i in $(seq 1 90); do
  if curl -fsS http://localhost:7474 >/dev/null 2>&1; then echo " → 준비됨"; break; fi
  [ "$i" = 90 ] && die "Neo4j가 3분 안에 뜨지 않았다. docker compose logs neo4j 로 확인해."
  printf '.'
  sleep 2
done

say "4/5 단위 테스트"
cd backends
PYTHONPATH=. .venv/bin/python -m pytest -q

say "5/5 키·DB 연결 확인 (Claude 16토큰 1회, 0.0001달러 미만)"
PYTHONPATH=. .venv/bin/python scripts/check_keys.py || echo "실패한 항목이 있다. 위 표를 확인해."

say "끝"
cat <<'EOF'
백엔드      cd backends && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
프론트      cd frontends/corp-advisor--app && npm install && npm run dev   → http://localhost:5173
Claude Code 레포 루트에서 claude
EOF
