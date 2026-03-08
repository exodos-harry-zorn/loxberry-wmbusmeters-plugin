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
log "User post-upgrade started"

mkdir -p "$PCONFIG"
if [ -d "/tmp/${PTEMPDIR}_upgrade/config/$PDIR" ]; then
  log "Restoring existing config files from backup"
  cp -p -v -r /tmp/${PTEMPDIR}_upgrade/config/$PDIR/* "$PCONFIG/" || true
elif [ -d "/tmp/${PTEMPDIR}_upgrade/config" ]; then
  # Fallback just in case the backup structure was slightly different
  log "Restoring existing config files from fallback backup path"
  cp -p -v -r /tmp/${PTEMPDIR}_upgrade/config/* "$PCONFIG/" || true
fi

exit 0
