#!/usr/bin/env python3
import argparse
import json
import logging
import signal
import sys
import time
from typing import Any, Dict, Iterable, Optional, Tuple

import paho.mqtt.client as mqtt
from common import load_config, resolve_mqtt_settings

RUNNING = True

FIELD_CANDIDATES = {
    "energy_kwh": ["total_energy_consumption_kwh", "total_kwh", "energy_kwh", "total_energy_kwh"],
    "volume_m3": ["total_volume_m3", "volume_m3", "total_m3"],
    "power_kw": ["current_power_consumption_kw", "power_kw", "current_kw"],
    "flow_m3h": ["flow_temperature_flow_m3h", "flow_m3h", "current_flow_m3h", "max_flow_m3h"],
    "temperature_1_c": ["flow_temperature_c", "temperature_1_c", "t1_temperature_c", "t1_c"],
    "temperature_2_c": ["return_temperature_c", "temperature_2_c", "t2_temperature_c", "t2_c"],
    "status": ["status", "meter_status", "info_codes"],
    "rssi_dbm": ["rssi_dbm", "rssi"],
}


def handle_signal(signum, frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


class Bridge:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        mqtt_cfg = resolve_mqtt_settings(cfg)
        self.base_topic = mqtt_cfg.get("base_topic", "wmbus/heat").rstrip("/")
        self.qos = int(mqtt_cfg.get("qos", 1))
        self.retain = bool(mqtt_cfg.get("retain", True))
        self.client = mqtt.Client(client_id=mqtt_cfg.get("client_id", "loxberry-wmbusmeters"), protocol=mqtt.MQTTv311)
        if mqtt_cfg.get("username"):
            self.client.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password", ""))
        self.client.connect(mqtt_cfg.get("host", "127.0.0.1"), int(mqtt_cfg.get("port", 1883)), keepalive=60)
        self.client.loop_start()
        self.meter_names = {str(m.get("id")): m.get("name") for m in cfg.get("meters", []) if m.get("enabled", True) and m.get("id")}
        self.publish(f"{self.base_topic}/bridge/status", "online")
        self.publish(f"{self.base_topic}/bridge/mqtt_source", mqtt_cfg.get("source", "unknown"))
        radio_cfg = cfg.get("radio", {})
        self.meter_whitelist = [str(x) for x in radio_cfg.get("meter_whitelist", []) if x]
        self.meter_blacklist = [str(x) for x in radio_cfg.get("meter_blacklist", []) if x]

    def stop(self):
        self.publish(f"{self.base_topic}/bridge/status", "offline")
        self.client.loop_stop()
        self.client.disconnect()

    def publish(self, topic: str, payload: Any, retain: Optional[bool] = None):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        elif payload is None:
            payload = ""
        else:
            payload = str(payload)
        self.client.publish(topic, payload, qos=self.qos, retain=self.retain if retain is None else retain)

    def topic(self, meter_name: str, suffix: str) -> str:
        return f"{self.base_topic}/{meter_name}/{suffix}"

    def normalized_fields(self, msg: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
        for out_field, candidates in FIELD_CANDIDATES.items():
            for candidate in candidates:
                if candidate in msg:
                    yield out_field, msg[candidate]
                    break

    def process(self, msg: Dict[str, Any]):
        meter_id = str(msg.get("id"))
        if self.meter_whitelist and meter_id not in self.meter_whitelist:
            logging.debug(f"Meter {meter_id} not in whitelist, skipping.")
            return
        if self.meter_blacklist and meter_id in self.meter_blacklist:
            logging.debug(f"Meter {meter_id} in blacklist, skipping.")
            return
        meter_name = msg.get("name") or self.meter_names.get(meter_id) or meter_id
        timestamp = msg.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self.publish(self.topic(meter_name, "raw_json"), msg)
        self.publish(self.topic(meter_name, "last_seen"), timestamp)
        self.publish(self.topic(meter_name, "availability"), "online")
        self.publish(self.topic(meter_name, "id"), msg.get("id", ""))
        self.publish(self.topic(meter_name, "meter_type"), msg.get("meter", ""))
        self.publish(self.topic(meter_name, "media"), msg.get("media", ""))
        for key, value in self.normalized_fields(msg):
            self.publish(self.topic(meter_name, key), value)


def parse_line(line: str):
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    bridge = Bridge(load_config())
    try:
        while RUNNING:
            line = sys.stdin.readline()
            if line == "":
                time.sleep(0.1)
                continue
            msg = parse_line(line)
            if msg:
                bridge.process(msg)
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
