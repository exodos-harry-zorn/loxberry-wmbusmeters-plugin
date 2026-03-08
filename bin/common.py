#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

PLUGIN_FOLDER = "loxberry-wmbusmeters"
INSTALL_DIR = Path(os.environ.get("LB_WMBUS_INSTALL_DIR", f"/opt/loxberry/data/system/install/{PLUGIN_FOLDER}"))
CONFIG_DIR = Path(os.environ.get("LB_WMBUS_CONFIG_DIR", f"/opt/loxberry/config/plugins/{PLUGIN_FOLDER}"))
BIN_DIR = Path(os.environ.get("LB_WMBUS_BIN_DIR", f"/opt/loxberry/bin/plugins/{PLUGIN_FOLDER}"))
WEB_DIR = Path(os.environ.get("LB_WMBUS_WEB_DIR", f"/opt/loxberry/webfrontend/html/plugins/{PLUGIN_FOLDER}"))
TMP_DIR = Path(os.environ.get("LB_WMBUS_TMP_DIR", "/tmp/loxberry-wmbusmeters"))
CONFIG_FILE = CONFIG_DIR / "config.json"
EXAMPLE_FILE = CONFIG_DIR / "config.example.json"
RUNTIME_CONFIG_DIR = TMP_DIR / "generated"
LOG_FILE = TMP_DIR / "bridge.log"
PID_FILE = TMP_DIR / "bridge.pid"
DISCOVERY_LOG = TMP_DIR / "discovery.log"
DEPS_LOG = TMP_DIR / "deps_install.log"
DEPS_STATUS_FILE = TMP_DIR / "deps_status.txt"
HEALTHCHECK_FILE = TMP_DIR / "healthcheck_status.json"
HEALTHCHECK_FILE = TMP_DIR / "healthcheck_status.json"
MQTT_CANDIDATE_FILES = [
    Path("/opt/loxberry/config/system/mqtt.json"),
    Path("/opt/loxberry/config/system/mqttgateway.json"),
    Path("/opt/loxberry/config/system/general.json"),
    Path("/opt/loxberry/data/system/mqtt.json"),
]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _default_config() -> Dict[str, Any]:
    return {
        "mqtt": {
            "base_topic": "wmbus/heat",
            "retain": True,
            "qos": 1,
            "client_id": "loxberry-wmbusmeters",
        },
        "radio": {
            "device": "rtlwmbus",
            "mode": "t1",
            "ppm": 0,
            "rtl_index": 0,
            "reset_after": "23h",
            "log_level": "normal",
            "log_telegrams": False,
            "ignore_duplicates": True,
            "discovery_seconds": 30,
            "meter_whitelist": [],
            "meter_blacklist": [],
        },
        "paths": {"workdir": str(TMP_DIR), "logfile": str(LOG_FILE)},
        "meters": [
            {"name": "wohnung1", "label": "Wohnung 1", "id": "", "driver": "sharky", "key": "", "enabled": True},
            {"name": "wohnung2", "label": "Wohnung 2", "id": "", "driver": "sharky", "key": "", "enabled": True},
            {"name": "wohnung3", "label": "Wohnung 3", "id": "", "driver": "sharky", "key": "", "enabled": True},
        ],
    }


def save_config(cfg: Dict[str, Any]) -> None:
    ensure_dir(CONFIG_DIR)
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config() -> Dict[str, Any]:
    path = CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_FILE
    if not path.exists():
        ensure_dir(CONFIG_DIR)
        default = _default_config()
        save_config(default)
        return default
    with path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    default = _default_config()
    default["mqtt"].update(cfg.get("mqtt", {}))
    default["radio"].update(cfg.get("radio", {}))
    default["paths"].update(cfg.get("paths", {}))
    if isinstance(cfg.get("meters"), list):
        default["meters"] = cfg["meters"]
    return default


def daemon_pid() -> Optional[int]:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _first_nonempty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        return None
    return None


def _nested_get(data: Dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def resolve_mqtt_settings(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    mqtt_cfg = cfg.get("mqtt", {})
    resolved: Dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 1883,
        "username": "",
        "password": "",
        "client_id": mqtt_cfg.get("client_id", "loxberry-wmbusmeters"),
        "base_topic": mqtt_cfg.get("base_topic", "wmbus/heat").rstrip("/"),
        "retain": bool(mqtt_cfg.get("retain", True)),
        "qos": _coerce_int(mqtt_cfg.get("qos", 1), 1),
        "source": "default local mosquitto",
    }

    env_map = {
        "host": _first_nonempty(os.environ.get("MQTT_BROKER"), os.environ.get("MQTT_C_CLIENT_BROKER"), os.environ.get("LBP_MQTT_BROKER")),
        "port": _first_nonempty(os.environ.get("MQTT_PORT"), os.environ.get("MQTT_C_CLIENT_PORT"), os.environ.get("LBP_MQTT_PORT")),
        "username": _first_nonempty(os.environ.get("MQTT_USERNAME"), os.environ.get("MQTT_C_CLIENT_USERNAME"), os.environ.get("LBP_MQTT_USERNAME")),
        "password": _first_nonempty(os.environ.get("MQTT_PASSWORD"), os.environ.get("MQTT_C_CLIENT_PASSWORD"), os.environ.get("LBP_MQTT_PASSWORD")),
    }
    if env_map["host"]:
        resolved["host"] = env_map["host"]
        resolved["port"] = _coerce_int(env_map["port"], resolved["port"])
        resolved["username"] = env_map["username"] or ""
        resolved["password"] = env_map["password"] or ""
        resolved["source"] = "environment / MQTT widget"
        return resolved

    for candidate in MQTT_CANDIDATE_FILES:
        data = _load_json_file(candidate)
        if not data:
            continue
        candidate_host = _first_nonempty(
            _nested_get(data, "BrokerHost"),
            _nested_get(data, "broker", "host"),
            _nested_get(data, "mqtt", "host"),
            _nested_get(data, "Server"),
            _nested_get(data, "server"),
            _nested_get(data, "mqttserver"),
        )
        if not candidate_host:
            continue
        resolved["host"] = candidate_host
        resolved["port"] = _coerce_int(
            _first_nonempty(
                _nested_get(data, "BrokerPort"),
                _nested_get(data, "broker", "port"),
                _nested_get(data, "mqtt", "port"),
                _nested_get(data, "Port"),
                _nested_get(data, "port"),
            ),
            resolved["port"],
        )
        resolved["username"] = _first_nonempty(
            _nested_get(data, "BrokerUsername"),
            _nested_get(data, "broker", "username"),
            _nested_get(data, "mqtt", "username"),
            _nested_get(data, "username"),
        ) or ""
        resolved["password"] = _first_nonempty(
            _nested_get(data, "BrokerPassword"),
            _nested_get(data, "broker", "password"),
            _nested_get(data, "mqtt", "password"),
            _nested_get(data, "password"),
        ) or ""
        resolved["source"] = f"{candidate}"
        return resolved

    return resolved


def mqtt_widget_url() -> str:
    return "/admin/system/mqtt-finder.cgi"


def admin_home_url() -> str:
    return "/admin/"


def plugin_overview_url() -> str:
    return "/admin/plugins/"
