#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="python3"
PORT="${PORT:-8000}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo "PORT must be an integer from 1024 to 65535" >&2
  exit 2
fi
if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=1)" 2>/dev/null; then
  echo "Port ${PORT} is already serving an application; choose another PORT." >&2
  exit 1
fi

if [[ -n "${ARENA_JUDGE_ROOT:-}" ]]; then
  JUDGE_ROOT="$ARENA_JUDGE_ROOT"
  KEEP_ROOT=1
else
  JUDGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/quant-arena-judge.XXXXXX")"
  KEEP_ROOT="${JUDGE_KEEP_ARTIFACTS:-0}"
fi

export ARENA_DB_PATH="$JUDGE_ROOT/arena.sqlite3"
export ARENA_DEMO_AUTH=1
export ARENA_DEMO_INSTRUCTOR_CODE="${ARENA_DEMO_INSTRUCTOR_CODE:-arena-local-judge}"
export ARENA_SESSION_SECRET="${ARENA_SESSION_SECRET:-arena-local-judge-session-secret}"
export MARKET_FUZZER_ARTIFACT_ROOT="$JUDGE_ROOT/market_fuzzer"
export MARKET_FUZZER_EXPERIMENT_ROOT="$JUDGE_ROOT/experiments"
mkdir -p "$MARKET_FUZZER_ARTIFACT_ROOT" "$MARKET_FUZZER_EXPERIMENT_ROOT"

SERVER_PID=""
CLEANED=0
cleanup() {
  [[ "$CLEANED" == "1" ]] && return
  CLEANED=1
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  [[ "$KEEP_ROOT" == "1" ]] || rm -rf "$JUDGE_ROOT"
  [[ "$KEEP_ROOT" != "1" ]] || echo "Judge state kept at: $JUDGE_ROOT"
}
trap cleanup EXIT INT TERM

"$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$JUDGE_ROOT/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=1)" 2>/dev/null; then
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || { cat "$JUDGE_ROOT/server.log" >&2; exit 1; }
  sleep 0.25
done
"$PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=1)" || { cat "$JUDGE_ROOT/server.log" >&2; exit 1; }

cat <<EOF

Quant Challenge Arena is ready:
  Primary URL:     http://127.0.0.1:${PORT}
  Advanced lab:    http://127.0.0.1:${PORT}/market-fuzzer
  API health:      http://127.0.0.1:${PORT}/api/health
  Instructor code: ${ARENA_DEMO_INSTRUCTOR_CODE}
  Isolated state:  ${JUDGE_ROOT}

Five-minute path:
  Student demo -> Configure -> Practice -> Submit final
  Instructor code -> Instructor demo -> Lock -> Evaluate -> Release
  Refresh rankings -> Inspect replay -> Explain evidence -> Market Fuzzer

This is the complete deterministic no-key path. GPT-5.6 analysis is optional
when OPENAI_API_KEY is supplied; it never controls market outcomes or scores.
The instructor code and session boundary are local demo authentication, not SSO.

Press Ctrl-C to stop.
EOF

wait "$SERVER_PID"
