#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PATH="$PATH:/usr/sbin:/usr/bin:/sbin:/bin"
PLUGIN_FOLDER="loxberry-wmbusmeters"
TMP_DIR="/tmp/${PLUGIN_FOLDER}"
LOGFILE="${TMP_DIR}/deps_install.log"
PIDFILE="${TMP_DIR}/deps_install.pid"
STATUSFILE="${TMP_DIR}/deps_status.txt"
mkdir -p "$TMP_DIR"

log(){ echo "[$(date -Is)] $*"; }
need_root(){
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "This script must run as root" >&2
    exit 2
  fi
}
is_running(){
  [[ -f "$PIDFILE" ]] || return 1
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  [[ -n "${pid:-}" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}
start_bg(){
  need_root
  if is_running; then
    echo "Dependency installation already running with PID $(cat "$PIDFILE")"
    exit 0
  fi
  nohup "$0" --run >>"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  echo "running" > "$STATUSFILE"
  echo "Started dependency installation in background with PID $(cat "$PIDFILE")"
  exit 0
}
status(){
  if is_running; then
    echo "running (pid $(cat "$PIDFILE"))"
    exit 0
  fi
  if [[ -f "$STATUSFILE" ]]; then
    cat "$STATUSFILE"
  else
    echo "idle"
  fi
}
finish(){
  rc="$1"
  if [[ "$rc" -eq 0 ]]; then
    echo "ok" > "$STATUSFILE"
  else
    echo "failed (rc=$rc)" > "$STATUSFILE"
  fi
  rm -f "$PIDFILE"
  exit "$rc"
}
wait_for_apt(){
  local waited=0
  while fuser /var/lib/dpkg/lock >/dev/null 2>&1 ||         fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 ||         fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
    log "Waiting for apt/dpkg lock... (${waited}s)"
    sleep 5
    waited=$((waited+5))
    if [[ "$waited" -ge 300 ]]; then
      log "Timed out waiting for apt/dpkg lock"
      return 1
    fi
  done
}
apt_install(){
  wait_for_apt
  log "Running apt-get update"
  apt-get update || log "apt-get update returned warnings; continuing with available package indexes"
  log "Installing packages"
  apt-get install -y --no-install-recommends     git build-essential pkg-config autoconf automake libtool make     rtl-sdr librtlsdr-dev libusb-1.0-0-dev libxml2-dev     python3 python3-pip python3-paho-mqtt     mosquitto mosquitto-clients ca-certificates sudo
}
ensure_python_module(){
  if ! python3 -c 'import paho.mqtt.client' >/dev/null 2>&1; then
    log "Installing paho-mqtt with pip"
    python3 -m pip install --break-system-packages paho-mqtt
  fi
}
install_wmbusmeters(){
  if command -v wmbusmeters >/dev/null 2>&1; then
    log "wmbusmeters already present at $(command -v wmbusmeters)"
    return 0
  fi
  if apt-cache show wmbusmeters >/dev/null 2>&1; then
    log "Installing wmbusmeters from apt"
    apt-get install -y --no-install-recommends wmbusmeters && return 0
    log "apt installation of wmbusmeters failed, falling back to source build"
  fi
  WORKDIR=$(mktemp -d)
  trap 'rm -rf "$WORKDIR"' EXIT
  log "Cloning wmbusmeters repository"
  git clone --depth 1 https://github.com/wmbusmeters/wmbusmeters.git "$WORKDIR/wmbusmeters"
  cd "$WORKDIR/wmbusmeters"
  if [[ -x ./configure ]]; then
    log "Configuring wmbusmeters"
    ./configure
  elif command -v autoreconf >/dev/null 2>&1 && [[ -f configure.ac || -f configure.in ]]; then
    log "Generating configure script with autoreconf"
    autoreconf -fi
    log "Configuring wmbusmeters"
    ./configure
  else
    log "No configure script available for wmbusmeters"
    return 1
  fi
  log "Building wmbusmeters"
  make
  log "Installing wmbusmeters"
  make install
  hash -r
}
run_install(){
  need_root
  exec >>"$LOGFILE" 2>&1
  log "Starting dependency installation $*"
  echo "running" > "$STATUSFILE"
  trap 'finish 1' ERR
  apt_install
  ensure_python_module
  install_wmbusmeters
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable mosquitto >/dev/null 2>&1 || true
    systemctl start mosquitto >/dev/null 2>&1 || true
  fi
  log "Dependency installation finished"
  finish 0
}
case "${1:-}" in
  --background) start_bg ;;
  --status) status ;;
  --run|--from-postroot|'') run_install "$@" ;;
  *) echo "Usage: $0 [--background|--status|--run]"; exit 1 ;;
esac
