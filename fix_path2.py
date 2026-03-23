path = "webfrontend/htmlauth/content.py"
with open(path, "r") as f:
    py = f.read()

py = py.replace("""def shell(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)""", """def shell(cmd, timeout=60):
    try:
        import os
        env = os.environ.copy()
        env["PATH"] = env.get("PATH", "") + ":/usr/bin:/usr/local/bin:/usr/sbin:/sbin:/bin"
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)""")

with open(path, "w") as f:
    f.write(py)
