import re
path = "bin/install_deps.sh"
with open(path, "r") as f:
    sh = f.read()

# Make sure we don't accidentally trap RETURN and break other things
# It's safer to just do a direct cleanup
sh = sh.replace("""  trap 'rm -rf "$WORKDIR"' RETURN
  git clone https://github.com/xaelsouth/rtl-wmbus.git "$WORKDIR/rtl-wmbus"
  cd "$WORKDIR/rtl-wmbus"
  log "Building rtl_wmbus"
  make
  log "Installing rtl_wmbus"
  cp build/rtl_wmbus /usr/bin/rtl_wmbus
  chmod +x /usr/bin/rtl_wmbus
  hash -r""", """  git clone https://github.com/xaelsouth/rtl-wmbus.git "$WORKDIR/rtl-wmbus"
  cd "$WORKDIR/rtl-wmbus"
  log "Building rtl_wmbus"
  make
  log "Installing rtl_wmbus"
  cp build/rtl_wmbus /usr/bin/rtl_wmbus
  chmod +x /usr/bin/rtl_wmbus
  hash -r
  rm -rf "$WORKDIR"
""")

with open(path, "w") as f:
    f.write(sh)
