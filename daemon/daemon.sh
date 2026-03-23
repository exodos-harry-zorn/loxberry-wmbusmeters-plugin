#!/usr/bin/env bash
set -u
export PATH="$PATH:/usr/bin:/usr/local/bin:/usr/sbin:/sbin:/bin"

PLUGIN_FOLDER="loxberry-wmbusmeters"
CONFIG_DIR="${LB_WMBUS_CONFIG_DIR:-/opt/loxberry/config/plugins/${PLUGIN_FOLDER}}"
BIN_DIR="${LB_WMBUS_BIN_DIR:-/opt/loxberry/bin/plugins/${PLUGIN_FOLDER}}"
WORKDIR="${LB_WMBUS_TMP_DIR:-/tmp/loxberry-wmbusmeters}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
RUNTIME_CONFIG_DIR="${WORKDIR}/generated"
PIDFILE="${WORKDIR}/bridge.pid"
LOGFILE="${WORKDIR}/bridge.log"

mkdir -p "$WORKDIR"

is_running() {
  [[ -f "$PIDFILE" ]] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then
    echo "Already running with PID $(cat "$PIDFILE")"
    return 0
  fi
  if ! command -v wmbusmeters >/dev/null 2>&1; then
    echo "wmbusmeters binary not found in PATH"
    return 1
  fi
  python3 "$BIN_DIR/generate_config.py" --input "$CONFIG_FILE" --output-dir "$RUNTIME_CONFIG_DIR" || return 1
  nohup bash -c "wmbusmeters --silent --useconfig=${RUNTIME_CONFIG_DIR} 2>>${LOGFILE} | python3 ${BIN_DIR}/publisher.py >>${LOGFILE} 2>&1" >/dev/null 2>&1 &
  echo $! > "$PIDFILE"
  echo "Started with PID $(cat "$PIDFILE")"
}

stop() {
  if is_running; then
    local pid
    pid="$(cat "$PIDFILE")"
    kill "$pid" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PIDFILE"
  echo "Stopped"
}

status() {
  if is_running; then
    echo "Running with PID $(cat "$PIDFILE")"
    return 0
  fi
  echo "Not running"
  return 1
}

restart() {
  stop
  start
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  *) echo "Usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
