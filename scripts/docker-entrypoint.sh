#!/bin/sh
# Join the Meet URL from the command line (docker compose) or from MEET_URL (Render).
# Stay PID 1 and forward SIGTERM and SIGINT to Python. An exec'd xvfb-run would
# keep Cloudflare's stop signal from the process that saves notes and leaves Meet.
set -eu

if [ -n "${PORT:-}" ]; then
  export BA_SERVER__STATUS_HOST="${BA_SERVER__STATUS_HOST:-0.0.0.0}"
  export BA_SERVER__STATUS_PORT="${BA_SERVER__STATUS_PORT:-$PORT}"
fi

is_python_argv() {
  cmd=$1
  first=${cmd%% *}
  name=${first##*/}
  case "$name" in
    python|python3|python3.[0-9]|python3.[0-9][0-9])
      return 0
      ;;
  esac
  return 1
}

# Print the Python pid under a process, or nothing. xvfb-run is the parent.
python_pid_under() {
  root=$1
  for cmdline in /proc/[0-9]*/cmdline; do
    pid=${cmdline#/proc/}
    pid=${pid%/cmdline}
    cmd=$(tr '\0' ' ' < "$cmdline" 2>/dev/null || true)
    is_python_argv "$cmd" || continue
    cursor=$pid
    while [ -n "$cursor" ] && [ "$cursor" != "0" ]; do
      if [ "$cursor" = "$root" ]; then
        printf '%s\n' "$pid"
        return 0
      fi
      parent=$(awk '/^PPid:/ { print $2; exit }' "/proc/$cursor/status" 2>/dev/null || true)
      if [ -z "$parent" ] || [ "$parent" = "$cursor" ]; then
        break
      fi
      cursor=$parent
    done
  done
  return 1
}

forward_signal() {
  sig=$1
  wrapper=$2
  forward_pid=""
  forward_i=0
  while [ "$forward_i" -lt 200 ]; do
    if ! kill -0 "$wrapper" 2>/dev/null; then
      break
    fi
    forward_pid=$(python_pid_under "$wrapper" || true)
    if [ -n "$forward_pid" ]; then
      break
    fi
    forward_i=$((forward_i + 1))
    sleep 0.1
  done
  if [ -n "$forward_pid" ]; then
    kill -s "$sig" "$forward_pid" 2>/dev/null || true
  elif kill -0 "$wrapper" 2>/dev/null; then
    kill -s "$sig" "$wrapper" 2>/dev/null || true
  fi
}

run_bot() {
  xvfb-run -a python -m ba_agent "$@" &
  wrapper=$!
  interrupted=0
  trap 'forward_signal TERM "$wrapper"; interrupted=1' TERM
  trap 'forward_signal INT "$wrapper"; interrupted=1' INT
  status=0
  wait "$wrapper" || status=$?
  # A signal interrupts the first wait before Python has exited.
  if [ "$interrupted" -eq 1 ]; then
    wait "$wrapper" 2>/dev/null || status=$?
  fi
  trap - TERM INT
  exit "$status"
}

if [ "${1:-}" = "join" ] && [ -n "${2:-}" ]; then
  run_bot "$@"
fi

if [ $# -eq 0 ] || [ "${1:-}" = "join" ]; then
  if [ -z "${MEET_URL:-}" ]; then
    echo "Set MEET_URL, or pass: join <meet-url>" >&2
    exit 1
  fi
  run_bot join "$MEET_URL"
fi

run_bot "$@"
