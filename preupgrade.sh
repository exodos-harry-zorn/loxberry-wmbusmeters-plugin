#!/bin/bash
ARGV1=$1
ARGV3=$3
ARGV5=$5
echo "<INFO> Creating temporary folders for upgrading"
mkdir -p /tmp/${ARGV1}_upgrade/config
if [ -d "$ARGV5/config/plugins/$ARGV3" ]; then
  echo "<INFO> Backing up existing config files"
  cp -p -v -r "$ARGV5/config/plugins/$ARGV3/" /tmp/${ARGV1}_upgrade/config || true
fi
exit 0
