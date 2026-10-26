#!/usr/bin/env bash
# Set the Google Meet link for local .env and (optionally) Cloudflare.
#
# Usage:
#   ./scripts/set-meet-url.sh "https://meet.google.com/abc-defg-hij"
#   ./scripts/set-meet-url.sh --local "https://meet.google.com/abc-defg-hij"
#   npm run meet-url -- "https://meet.google.com/abc-defg-hij"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-$ROOT/.env}"
LOCAL_ONLY=0
URL=""

usage() {
  awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --local) LOCAL_ONLY=1; shift ;;
    --)
      shift
      [[ $# -ge 1 ]] || { echo "error: missing Meet URL" >&2; usage 1; }
      URL="$1"
      shift
      break
      ;;
    -*)
      echo "error: unknown argument: $1" >&2
      usage 1
      ;;
    *)
      if [[ -n "$URL" ]]; then
        echo "error: unexpected argument: $1" >&2
        usage 1
      fi
      URL="$1"
      shift
      ;;
  esac
done

if [[ -z "$URL" ]]; then
  echo "error: missing Meet URL" >&2
  usage 1
fi

case "$URL" in
  https://meet.google.com/*) ;;
  *)
    echo "error: URL must look like https://meet.google.com/…" >&2
    exit 1
    ;;
esac

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: missing $ENV_FILE (copy .env.example and fill values)" >&2
  exit 1
fi

# Upsert MEET_URL in .env without printing the value twice in logs.
python3 - "$ENV_FILE" "$URL" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
url = sys.argv[2]
text = path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
out: list[str] = []
found = False
for line in lines:
    if line.startswith("MEET_URL="):
        out.append(f"MEET_URL={url}\n")
        found = True
    else:
        out.append(line)
if not found:
    if out and not out[-1].endswith("\n"):
        out[-1] = out[-1] + "\n"
    if out and out[-1].strip():
        out.append("\n")
    out.append(f"MEET_URL={url}\n")
path.write_text("".join(out), encoding="utf-8")
PY

echo "→ wrote MEET_URL to $ENV_FILE"

if [[ "$LOCAL_ONLY" -eq 1 ]]; then
  echo "Done (local only). Docker: MEET_URL from .env via docker compose."
  echo "Cloudflare unchanged; omit --local to sync the Worker secret."
  exit 0
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "error: npx not found; install Node.js, or pass --local" >&2
  exit 1
fi

if [[ ! -d "$ROOT/node_modules/wrangler" ]]; then
  echo "→ npm install"
  npm install
fi

echo "→ syncing MEET_URL Worker secret"
printf '%s' "$URL" | npx wrangler secret put MEET_URL

echo
echo "Done. Next container start uses this Meet link."
echo "If a container is already running, wait for it to sleep or redeploy before the meeting."
echo "  npm run deploy"
