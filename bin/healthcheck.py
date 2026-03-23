#!/usr/bin/env python3
import subprocess
import json
import logging
from pathlib import Path
import os
from common import TMP_DIR, ensure_dir, DEPS_STATUS_FILE # type: ignore

LOG_FILE = TMP_DIR / "healthcheck.log"

def command_exists(name):
    try:
        subprocess.run(['bash', '-lc', f'command -v {name}'], capture_output=True, text=True, errors="replace", check=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False

def run_rtl_test():
    try:
        # Try to run rtl_test for a very short duration to check dongle presence
        result = subprocess.run(['rtl_test', '-t'], capture_output=True, text=True, errors="replace", check=True, timeout=5)
        if "No supported devices found." in result.stderr:
            return False, "RTL-SDR device not found."
        return True, "RTL-SDR device found and working."
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return False, f"rtl_test failed: {e}"
    except FileNotFoundError:
        return False, "rtl_test command not found."

def perform_health_check():
    ensure_dir(TMP_DIR)
    status = {}

    status["wmbusmeters_installed"] = command_exists("wmbusmeters")
    status["wmbusmeters_status_text"] = "wmbusmeters is installed." if status["wmbusmeters_installed"] else "wmbusmeters is NOT installed."

    rtl_ok, rtl_msg = run_rtl_test()
    status["rtl_sdr_detected"] = rtl_ok
    status["rtl_sdr_status_text"] = rtl_msg
    
    overall_status = "OK" if status["wmbusmeters_installed"] and status["rtl_sdr_detected"] else "ISSUES"
    status["overall_status"] = overall_status

    try:
        with DEPS_STATUS_FILE.open("w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, ensure_ascii=False)
        logging.info("Health check completed and status saved.")
    except Exception as e:
        logging.error(f"Failed to write health check status to file: {e}")
    
    return status

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", filename=LOG_FILE)
    logging.info("Starting health check.")
    perform_health_check()
    logging.info("Health check script finished.")
