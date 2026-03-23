path = "bin/install_deps.sh"
with open(path, "r") as f:
    sh = f.read()

sh = sh.replace('mkdir -p "$TMP_DIR"', 'mkdir -p "$TMP_DIR"\nchmod 777 "$TMP_DIR" 2>/dev/null || true\ntouch "$LOGFILE" "$STATUSFILE" 2>/dev/null || true\nchmod 666 "$LOGFILE" "$STATUSFILE" 2>/dev/null || true')

with open(path, "w") as f:
    f.write(sh)
