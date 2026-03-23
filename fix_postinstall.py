path = "postinstall.sh"
with open(path, "r") as f:
    sh = f.read()

sh = sh.replace('mkdir -p /tmp/loxberry-wmbusmeters', 'mkdir -p /tmp/loxberry-wmbusmeters\nchmod 777 /tmp/loxberry-wmbusmeters 2>/dev/null || true')

with open(path, "w") as f:
    f.write(sh)
