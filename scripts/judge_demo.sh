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
  echo "Port ${PORT} is already serving a Market Fuzzer process; choose another PORT." >&2
  exit 1
fi
if [[ -n "${MARKET_FUZZER_JUDGE_ARTIFACT_ROOT:-}" ]]; then
  JUDGE_ROOT="$MARKET_FUZZER_JUDGE_ARTIFACT_ROOT"
  KEEP_ROOT=1
else
  JUDGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/market-fuzzer-judge.XXXXXX")"
  KEEP_ROOT="${JUDGE_KEEP_ARTIFACTS:-0}"
fi
export MARKET_FUZZER_ARTIFACT_ROOT="$JUDGE_ROOT/market_fuzzer"
export MARKET_FUZZER_EXPERIMENT_ROOT="$JUDGE_ROOT/experiments"
mkdir -p "$MARKET_FUZZER_ARTIFACT_ROOT" "$MARKET_FUZZER_EXPERIMENT_ROOT"
SERVER_PID=""
CLEANED=0
cleanup() {
  [[ "$CLEANED" == "1" ]] && return
  CLEANED=1
  [[ -z "$SERVER_PID" ]] || { kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; }
  [[ "$KEEP_ROOT" == "1" ]] || rm -rf "$JUDGE_ROOT"
  [[ "$KEEP_ROOT" != "1" ]] || echo "Judge artifacts kept at: $JUDGE_ROOT"
}
trap cleanup EXIT INT TERM
echo "Generating the real no-key tutorial artifacts..."
"$PYTHON" -m app.cli run-example
echo "Starting Market Fuzzer on http://127.0.0.1:${PORT}"
"$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" >"$JUDGE_ROOT/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 60); do
  if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=1)" 2>/dev/null; then break; fi
  kill -0 "$SERVER_PID" 2>/dev/null || { cat "$JUDGE_ROOT/server.log" >&2; exit 1; }
  sleep 0.25
done
"$PYTHON" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=1)" || { cat "$JUDGE_ROOT/server.log" >&2; exit 1; }
cat <<EOF

Market Fuzzer is ready for the judge:
  URL:        http://127.0.0.1:${PORT}
  API health: http://127.0.0.1:${PORT}/api/health
  Artifacts:  $MARKET_FUZZER_ARTIFACT_ROOT

First-run path:
  Start with POV example -> Baseline -> Break My Strategy -> Replay -> Compare -> Export

This is the deterministic no-key path. GPT-5.6 analysis is enabled only when
OPENAI_API_KEY is supplied to the server process; it never controls PASS/FAIL.
Press Ctrl-C to stop.
EOF
wait "$SERVER_PID"
