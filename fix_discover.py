import re
path = "bin/discover.py"
with open(path, "r") as f:
    py = f.read()

# Add path modification for subprocess to ensure it finds rtl_wmbus inside /usr/bin or /usr/local/bin if missing from Loxberry user env
py = py.replace('import subprocess', 'import subprocess\nimport os')

py = py.replace('proc = subprocess.Popen(cmd, stdout=out, stderr=out, text=True)', 'env = os.environ.copy()\n    env["PATH"] = env.get("PATH", "") + ":/usr/bin:/usr/local/bin:/usr/sbin:/sbin:/bin"\n    proc = subprocess.Popen(cmd, stdout=out, stderr=out, text=True, env=env)')

with open(path, "w") as f:
    f.write(py)

