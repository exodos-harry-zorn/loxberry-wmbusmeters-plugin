path = "postroot.sh"
with open(path, "r") as f:
    sh = f.read()

sh = sh.replace('chmod 777 "$TMPDIR" 2>/dev/null || true', 'chmod 777 "$TMPDIR" 2>/dev/null || true\nchmod 666 "$TMPDIR"/*.log "$TMPDIR"/*.txt 2>/dev/null || true')

with open(path, "w") as f:
    f.write(sh)
