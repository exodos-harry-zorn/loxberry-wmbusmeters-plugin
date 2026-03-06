#!/bin/bash
set -u
COMMAND=$0
PTEMPDIR=$1
PSHNAME=$2
PDIR=$3
PVERSION=$4
PTEMPPATH=$6

PBIN="$LBPBIN/$PDIR"
PCONFIG="$LBPCONFIG/$PDIR"
PHTMLAUTH="$LBPHTMLAUTH/$PDIR"
TMPDIR="/tmp/loxberry-wmbusmeters"
DAEMON_TARGET="/opt/loxberry/system/daemons/plugins/wmbusmetersbridge"
SUDOERS_TEMPLATE="$PTEMPPATH/sudoers/wmbusmetersbridge"
SUDOERS_DEST="/etc/sudoers.d/50-${PDIR}"

log() { echo "<INFO> $*"; }
warn() { echo "<WARNING> $*"; }
fail() { echo "<ERROR> $*"; exit 1; }

log "Root post-install started"
mkdir -p "$TMPDIR" "$PCONFIG"
chmod 755 "$PBIN"/*.sh 2>/dev/null || true
chmod 755 "$PHTMLAUTH"/*.cgi 2>/dev/null || true
chmod 755 "$DAEMON_TARGET" 2>/dev/null || true

if [ ! -f "$PCONFIG/config.json" ] && [ -f "$PCONFIG/config.example.json" ]; then
  cp -p "$PCONFIG/config.example.json" "$PCONFIG/config.json"
  log "Created config.json as root"
fi

mkdir -p /etc/sudoers.d
if [ -f "$SUDOERS_TEMPLATE" ]; then
  TMP_SUDOERS="$(mktemp)"
  sed \
    -e "s|REPLACEDAEMONPATH|$DAEMON_TARGET|g" \
    -e "s|REPLACEINSTALLERPATH|$PBIN/install_deps.sh|g" \
    "$SUDOERS_TEMPLATE" > "$TMP_SUDOERS"
  chmod 440 "$TMP_SUDOERS"
  if command -v visudo >/dev/null 2>&1; then
    if visudo -c -f "$TMP_SUDOERS" >/dev/null 2>&1; then
      install -m 440 "$TMP_SUDOERS" "$SUDOERS_DEST"
      log "Installed sudoers rules to $SUDOERS_DEST"
    else
      rm -f "$TMP_SUDOERS"
      fail "Generated sudoers file is invalid"
    fi
  else
    install -m 440 "$TMP_SUDOERS" "$SUDOERS_DEST"
    warn "visudo not found; installed sudoers rules without validation"
  fi
  rm -f "$TMP_SUDOERS"
else
  warn "sudoers template not found at $SUDOERS_TEMPLATE"
fi

if [ -x "$PBIN/install_deps.sh" ]; then
  log "Attempting automatic dependency installation"
  if "$PBIN/install_deps.sh" --run; then
    log "Automatic dependency installation finished successfully"
  else
    warn "Automatic dependency installation failed; user can retry from the web UI"
  fi
else
  warn "Dependency installer not found at $PBIN/install_deps.sh"
fi

log "Root post-install finished"
exit 0
