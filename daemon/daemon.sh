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
PIPELINE_PIDFILE="${WORKDIR}/bridge.pipeline.pid"

mkdir -p "$WORKDIR"

process_matches() {
  pgrep -f "wmbusmeters .*${RUNTIME_CONFIG_DIR}" >/dev/null 2>&1 || \
  pgrep -f "python3 ${BIN_DIR}/publisher.py" >/dev/null 2>&1 || \
  pgrep -f "rtl_sdr .*868.625M" >/dev/null 2>&1 || \
  pgrep -f "rtl_wmbus -s -f" >/dev/null 2>&1
}

is_running() {
  [[ -f "$PIDFILE" ]] || { process_matches; return $?; }
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)"
  [[ -n "$pid" ]] || { process_matches; return $?; }
  kill -0 "$pid" 2>/dev/null || { process_matches; return $?; }
}

kill_descendants() {
  local parent="$1"
  local child
  for child in $(pgrep -P "$parent" 2>/dev/null); do
    kill_descendants "$child"
    kill "$child" 2>/dev/null || true
    sleep 0.2
    kill -9 "$child" 2>/dev/null || true
  done
}

start() {
  if process_matches; then
    stop >/dev/null 2>&1 || true
    sleep 1
  fi
  if is_running; then
    echo "Already running with PID $(cat "$PIDFILE" 2>/dev/null || echo unknown)"
    return 0
  fi
  if ! command -v wmbusmeters >/dev/null 2>&1; then
    echo "wmbusmeters binary not found in PATH"
    return 1
  fi
  python3 "$BIN_DIR/generate_config.py" --input "$CONFIG_FILE" --output-dir "$RUNTIME_CONFIG_DIR" || return 1
  local runtime_conf meter_args meter_file
  runtime_conf="${RUNTIME_CONFIG_DIR}/wmbusmeters.conf"
  meter_args=""
  shopt -s nullglob
  for meter_file in "${RUNTIME_CONFIG_DIR}"/wmbusmeters.d/*.conf; do
    meter_args+=" ${meter_file}"
  done
  shopt -u nullglob
  if [[ -z "$meter_args" ]]; then
    echo "No meter files generated in ${RUNTIME_CONFIG_DIR}/wmbusmeters.d"
    return 1
  fi
  nohup bash -c "wmbusmeters --useconfig=${runtime_conf} ${meter_args} 2>>${LOGFILE} | python3 ${BIN_DIR}/publisher.py >>${LOGFILE} 2>&1" >/dev/null 2>&1 &
  echo $! > "$PIDFILE"
  echo $! > "$PIPELINE_PIDFILE"
  echo "Started with PID $(cat "$PIDFILE")"
}

stop() {
  if [[ -f "$PIDFILE" ]]; then
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]]; then
      kill_descendants "$pid"
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  pkill -f "python3 ${BIN_DIR}/publisher.py" 2>/dev/null || true
  pkill -f "wmbusmeters .*${RUNTIME_CONFIG_DIR}" 2>/dev/null || true
  pkill -f "rtl_sdr .*868.625M" 2>/dev/null || true
  pkill -f "rtl_wmbus -s -f" 2>/dev/null || true
  pkill -f "tail -f /tmp/wmbusmeters_rtlsdr" 2>/dev/null || true
  rm -f "$PIDFILE" "$PIPELINE_PIDFILE"
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
