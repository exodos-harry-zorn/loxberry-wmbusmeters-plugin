#!/bin/bash
set -u
PDIR="$3"
SUDOERS_DEST="/etc/sudoers.d/50-${PDIR}"
if [ -f "$SUDOERS_DEST" ]; then
  rm -f "$SUDOERS_DEST"
  echo "<INFO> Removed old sudoers file $SUDOERS_DEST before install/upgrade"
fi
exit 0
