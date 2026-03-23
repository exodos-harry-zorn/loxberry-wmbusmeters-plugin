path = "postroot.sh"
with open(path, "r") as f:
    sh = f.read()

sh = sh.replace('mkdir -p "$TMPDIR" "$PCONFIG"', 'mkdir -p "$TMPDIR" "$PCONFIG"\nchmod 777 "$TMPDIR" 2>/dev/null || true')

with open(path, "w") as f:
    f.write(sh)
