#!/usr/bin/env python3
import argparse
import subprocess
import os
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a timed wmbusmeters discovery session")
    parser.add_argument("--device", default="auto:t1", help="Device string, e.g. rtlwmbus:t1")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--binary", default="wmbusmeters")
    parser.add_argument("--logfile", required=False)
    args = parser.parse_args()

    if args.logfile:
        Path(args.logfile).parent.mkdir(parents=True, exist_ok=True)
        out = open(args.logfile, "a", encoding="utf-8")
    else:
        out = sys.stdout

    cmd = [args.binary, "--format=json", args.device]
    print(f"Starting discovery: {' '.join(cmd)} for {args.seconds}s", file=out)
    out.flush()
    env = os.environ.copy()
    env["PATH"] = env.get("PATH", "") + ":/usr/bin:/usr/local/bin:/usr/sbin:/sbin:/bin"
    proc = subprocess.Popen(cmd, stdout=out, stderr=out, text=True, env=env)
    try:
        time.sleep(max(1, args.seconds))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        print(f"Discovery finished with rc={proc.returncode}", file=out)
        out.flush()
    if args.logfile:
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
