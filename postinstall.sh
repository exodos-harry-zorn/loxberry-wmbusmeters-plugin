#!/bin/bash
set -u
PDIR="$3"
PCONFIG="$LBPCONFIG/$PDIR"
mkdir -p "$PCONFIG"
if [ ! -f "$PCONFIG/config.json" ] && [ -f "$PCONFIG/config.example.json" ]; then
  cp -p "$PCONFIG/config.example.json" "$PCONFIG/config.json"
  echo "<INFO> Created default config.json from config.example.json"
fi
mkdir -p /tmp/loxberry-wmbusmeters
exit 0
