#!/usr/bin/env bash
# Deploy the BA Meeting Agent to Cloudflare Containers.
# Syncs Worker secrets from .env (plus MEET_URL), then runs wrangler deploy.
#
# Usage:
#   ./scripts/deploy-cloudflare.sh
#   ./scripts/deploy-cloudflare.sh --meet-url "https://meet.google.com/abc-defg-hij"
#   MEET_URL="https://meet.google.com/abc-defg-hij" ./scripts/deploy-cloudflare.sh
#   ./scripts/deploy-cloudflare.sh --secrets-only
#   ./scripts/deploy-cloudflare.sh --skip-secrets
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-$ROOT/.env}"
SECRETS_ONLY=0
SKIP_SECRETS=0
MEET_URL_ARG=""

SECRET_KEYS=(
  MEET_URL
  BA_LLM__API_KEY
  BA_NOTION__TOKEN
  BA_NOTION__DATABASES__SUMMARY
  BA_NOTION__DATABASES__TASKS
)

usage() {
  awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --secrets-only) SECRETS_ONLY=1; shift ;;
    --skip-secrets) SKIP_SECRETS=1; shift ;;
    --meet-url)
      [[ $# -ge 2 ]] || { echo "error: --meet-url needs a value" >&2; exit 1; }
      MEET_URL_ARG="$2"
      shift 2
      ;;
    --meet-url=*)
      MEET_URL_ARG="${1#*=}"
      shift
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage 1
      ;;
  esac
done

if [[ "$SECRETS_ONLY" -eq 1 && "$SKIP_SECRETS" -eq 1 ]]; then
  echo "error: --secrets-only and --skip-secrets cannot be combined" >&2
  exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "error: npx not found; install Node.js first" >&2
  exit 1
fi

if [[ ! -d "$ROOT/node_modules/wrangler" ]]; then
  echo "→ npm install"
  npm install
fi

if [[ "$SKIP_SECRETS" -eq 0 ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "error: missing $ENV_FILE (copy .env.example and fill values)" >&2
    exit 1
  fi

  # Preserve caller-supplied environment overrides before sourcing .env.
  CALLER_MEET_URL="${MEET_URL:-}"
  CALLER_LLM_KEY="${BA_LLM__API_KEY:-}"
  CALLER_OPENAI_KEY="${OPENAI_API_KEY:-}"
  CALLER_LLM_MODEL="${BA_LLM__MODEL:-}"
  CALLER_NOTION_TOKEN="${BA_NOTION__TOKEN:-}"
  CALLER_NOTION_SUMMARY="${BA_NOTION__DATABASES__SUMMARY:-}"
  CALLER_NOTION_TASKS="${BA_NOTION__DATABASES__TASKS:-}"

  # Load .env into the shell without printing values.
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  [[ -n "$CALLER_MEET_URL" ]] && MEET_URL="$CALLER_MEET_URL"
  [[ -n "$CALLER_LLM_KEY" ]] && BA_LLM__API_KEY="$CALLER_LLM_KEY"
  [[ -n "$CALLER_OPENAI_KEY" ]] && OPENAI_API_KEY="$CALLER_OPENAI_KEY"
  [[ -n "$CALLER_LLM_MODEL" ]] && BA_LLM__MODEL="$CALLER_LLM_MODEL"
  [[ -n "$CALLER_NOTION_TOKEN" ]] && BA_NOTION__TOKEN="$CALLER_NOTION_TOKEN"
  [[ -n "$CALLER_NOTION_SUMMARY" ]] && BA_NOTION__DATABASES__SUMMARY="$CALLER_NOTION_SUMMARY"
  [[ -n "$CALLER_NOTION_TASKS" ]] && BA_NOTION__DATABASES__TASKS="$CALLER_NOTION_TASKS"

  if [[ -n "$MEET_URL_ARG" ]]; then
    MEET_URL="$MEET_URL_ARG"
  fi

  # OPENAI_API_KEY is an allowed local fallback when BA_LLM__API_KEY is empty.
  if [[ -z "${BA_LLM__API_KEY:-}" && -n "${OPENAI_API_KEY:-}" ]]; then
    BA_LLM__API_KEY="$OPENAI_API_KEY"
  fi

  # Fall back to config/notion_schema.yaml for database IDs if not set in .env.
  if [[ ( -z "${BA_NOTION__DATABASES__SUMMARY:-}" || -z "${BA_NOTION__DATABASES__TASKS:-}" ) && -f "$ROOT/config/notion_schema.yaml" ]]; then
    eval "$(python3 - "$ROOT/config/notion_schema.yaml" <<'PY'
import shlex, sys
from pathlib import Path

try:
    import yaml
    data = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
    dbs = data.get("databases") or {}
    summary = str(dbs.get("summary") or "").strip()
    tasks = str(dbs.get("tasks") or "").strip()
    if summary:
        print(f"SCHEMA_SUMMARY={shlex.quote(summary)}")
    if tasks:
        print(f"SCHEMA_TASKS={shlex.quote(tasks)}")
except Exception:
    pass
PY
)"
    [[ -z "${BA_NOTION__DATABASES__SUMMARY:-}" && -n "${SCHEMA_SUMMARY:-}" ]] && BA_NOTION__DATABASES__SUMMARY="$SCHEMA_SUMMARY"
    [[ -z "${BA_NOTION__DATABASES__TASKS:-}" && -n "${SCHEMA_TASKS:-}" ]] && BA_NOTION__DATABASES__TASKS="$SCHEMA_TASKS"
  fi

  missing=()
  for key in "${SECRET_KEYS[@]}"; do
    if [[ -z "${!key:-}" ]]; then
      missing+=("$key")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "error: missing required secret(s): ${missing[*]}" >&2
    echo "Set them in $ENV_FILE, or pass --meet-url for MEET_URL." >&2
    exit 1
  fi

  case "$MEET_URL" in
    https://meet.google.com/*) ;;
    *)
      echo "error: MEET_URL must look like https://meet.google.com/…" >&2
      exit 1
      ;;
  esac

  tmp="$(mktemp)"
  cleanup() { rm -f "$tmp"; }
  trap cleanup EXIT

  # JSON bulk upload handles special characters in API tokens safely.
  export MEET_URL BA_LLM__API_KEY BA_NOTION__TOKEN BA_NOTION__DATABASES__SUMMARY BA_NOTION__DATABASES__TASKS
  export BA_LLM__MODEL="${BA_LLM__MODEL:-}"
  SECRET_KEYS_CSV="$(IFS=,; echo "${SECRET_KEYS[*]}")" \
    python3 - "$tmp" <<'PY'
import json, os, sys

path = sys.argv[1]
keys = os.environ["SECRET_KEYS_CSV"].split(",")
payload = {}
for key in keys:
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f"error: empty secret after load: {key}")
    payload[key] = value
model = (os.environ.get("BA_LLM__MODEL") or "").strip()
if model:
    payload["BA_LLM__MODEL"] = model
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
PY
fi

if [[ "$SECRETS_ONLY" -eq 1 ]]; then
  echo "→ syncing Worker secrets"
  npx wrangler secret bulk "$tmp"
  echo "→ secrets updated"
  echo "Done (secrets only). Run without --secrets-only to deploy."
  exit 0
fi

echo "→ wrangler deploy (first container image build can take several minutes)"
npx wrangler deploy

if [[ "$SKIP_SECRETS" -eq 0 ]]; then
  echo "→ syncing Worker secrets"
  npx wrangler secret bulk "$tmp"
  echo "→ secrets updated"
fi

echo
echo "Deployed. Open /status on the workers.dev URL above, then admit TechSol BA in Meet."
echo "A deploy mid-call stops the container; redeploy before the meeting, not during it."
