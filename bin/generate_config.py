#!/usr/bin/env python3
import argparse
import json
import pathlib
import re
import sys
from typing import Dict, Any

from common import ensure_dir, load_config, CONFIG_FILE

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
HEX_16 = re.compile(r"^[0-9A-Fa-f]{32}$")


def validate_meter(meter: Dict[str, Any], require_id: bool = True) -> None:
    name = meter.get("name", "")
    if not SAFE_NAME.match(name):
        raise ValueError(f"Invalid meter name: {name!r}")
    if require_id and not meter.get("id"):
        raise ValueError(f"Meter {name!r} is missing id")
    if require_id and not meter.get("driver"):
        raise ValueError(f"Meter {name!r} is missing driver")
    key = meter.get("key", "")
    if key and not HEX_16.match(key):
        raise ValueError(f"Meter {name!r} has invalid AES key; expected 32 hex chars")


def radio_device_expr(cfg: Dict[str, Any]) -> str:
    radio = cfg.get("radio", {})
    device = str(radio.get("device", "rtlwmbus"))
    if device == "rtlwmbus":
        rtl_index = int(radio.get("rtl_index", 0))
        return f"rtlwmbus[{rtl_index}]"
    if device == "auto":
        return device
    return device


def write_global_config(cfg: Dict[str, Any], outdir: pathlib.Path) -> None:
    radio = cfg.get("radio", {})
    mode = str(radio.get("mode", "t1")).strip().lower()
    conf = [
        f"device={radio_device_expr(cfg)}",
        f"listento={mode}",
        "format=json",
        "meterfiles=/tmp/loxberry-wmbusmeters/generated/wmbusmeters.d",
        f"loglevel={radio.get('log_level', 'normal')}",
        f"logtelegrams={'true' if radio.get('log_telegrams', False) else 'false'}",
        f"ignoreduplicates={'true' if radio.get('ignore_duplicates', True) else 'false'}",
        f"resetafter={radio.get('reset_after', '23h')}",
    ]
    (outdir / "wmbusmeters.conf").write_text("\n".join(conf) + "\n", encoding="utf-8")


def write_meter_files(cfg: Dict[str, Any], outdir: pathlib.Path, allow_missing_ids: bool = False) -> None:
    meters_dir = outdir / "wmbusmeters.d"
    ensure_dir(meters_dir)
    for child in meters_dir.glob("*.conf"):
        child.unlink()
    for meter in cfg.get("meters", []):
        if not meter.get("enabled", True):
            continue
        has_id = bool(str(meter.get("id", "")).strip())
        if not has_id and allow_missing_ids:
            continue
        validate_meter(meter, require_id=True)
        lines = [
            f"id={meter['id']}",
            f"type={meter['driver']}",
        ]
        if meter.get("key"):
            lines.append(f"key={meter['key']}")
        (meters_dir / f"{meter['name']}.conf").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate wmbusmeters config from plugin JSON config")
    parser.add_argument("--input", default=str(CONFIG_FILE))
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--allow-missing-ids", action="store_true")
    args = parser.parse_args()
    cfg = load_config() if args.input == str(CONFIG_FILE) else json.load(open(args.input, encoding='utf-8'))
    outdir = pathlib.Path(args.output_dir)
    ensure_dir(outdir)
    write_global_config(cfg, outdir)
    write_meter_files(cfg, outdir, allow_missing_ids=args.allow_missing_ids)
    print(f"Generated config in {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
