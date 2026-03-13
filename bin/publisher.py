#!/usr/bin/env python3
import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import paho.mqtt.client as mqtt
from common import METER_STATUS_FILE, METER_RATES_FILE, load_config, resolve_mqtt_settings

RUNNING = True
STATUS_STALE_SECONDS = 15 * 60
STATUS_OFFLINE_SECONDS = 60 * 60
STATUS_PUBLISH_INTERVAL_SECONDS = 30

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
        self.meter_configs = {str(m.get("id")): m for m in cfg.get("meters", []) if m.get("enabled", True) and m.get("id")}
        self.meter_whitelist = [str(x) for x in cfg.get("radio", {}).get("meter_whitelist", []) if x]
        self.meter_blacklist = [str(x) for x in cfg.get("radio", {}).get("meter_blacklist", []) if x]
        self.status_cache: Dict[str, Dict[str, Any]] = self._initial_status_cache()
        self.rates_cache: Dict[str, Dict[str, Any]] = self._load_rates_cache()
        self.last_status_publish = 0.0

        self.publish(f"{self.base_topic}/bridge/status", "online")
        self.publish(f"{self.base_topic}/bridge/mqtt_source", mqtt_cfg.get("source", "unknown"))
        self.publish_statuses(force=True)

    def _load_rates_cache(self) -> Dict[str, Dict[str, Any]]:
        try:
            if Path(METER_RATES_FILE).exists():
                with Path(METER_RATES_FILE).open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            logging.exception("Failed to load meter rates file")
        return {}

    def write_rates_file(self):
        try:
            Path(METER_RATES_FILE).parent.mkdir(parents=True, exist_ok=True)
            with Path(METER_RATES_FILE).open("w", encoding="utf-8") as f:
                json.dump(self.rates_cache, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception:
            logging.exception("Failed to write meter rates file")

    def _initial_status_cache(self) -> Dict[str, Dict[str, Any]]:
        cache: Dict[str, Dict[str, Any]] = {}
        now = time.time()
        for meter_id in self.meter_configs.keys():
            cache[meter_id] = {
                "status": "offline",
                "last_seen": None,
                "last_seen_epoch": None,
                "minutes_since_seen": None,
                "updated_at_epoch": int(now),
            }
        return cache

    def stop(self):
        for meter_id in list(self.status_cache.keys()):
            self.set_meter_status(meter_id, "offline", publish=True)
        self.publish(f"{self.base_topic}/bridge/status", "offline")
        self.client.loop_stop()
        self.client.disconnect()
        self.write_status_file()
        self.write_rates_file()

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

    def parse_timestamp(self, raw: Any) -> Tuple[str, int]:
        if raw:
            text = str(raw).strip()
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                epoch = int(dt.timestamp())
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), epoch
            except Exception:
                pass
        now = int(time.time())
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), now

    def derive_status(self, seconds_since_seen: Optional[int]) -> str:
        if seconds_since_seen is None:
            return "offline"
        if seconds_since_seen >= STATUS_OFFLINE_SECONDS:
            return "offline"
        if seconds_since_seen >= STATUS_STALE_SECONDS:
            return "stale"
        return "online"

    def write_status_file(self):
        try:
            Path(METER_STATUS_FILE).parent.mkdir(parents=True, exist_ok=True)
            with Path(METER_STATUS_FILE).open("w", encoding="utf-8") as f:
                json.dump(self.status_cache, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception:
            logging.exception("Failed to write meter status file")

    def set_meter_status(self, meter_id: str, status: str, publish: bool = False):
        meter_name = self.meter_names.get(meter_id) or meter_id
        entry = self.status_cache.setdefault(meter_id, {
            "status": "offline",
            "last_seen": None,
            "last_seen_epoch": None,
            "minutes_since_seen": None,
            "updated_at_epoch": int(time.time()),
        })
        changed = entry.get("status") != status
        entry["status"] = status
        entry["updated_at_epoch"] = int(time.time())
        if publish or changed:
            self.publish(self.topic(meter_name, "availability"), status)
            self.publish(self.topic(meter_name, "status_json"), entry)

    def publish_statuses(self, force: bool = False):
        now = int(time.time())
        any_changed = False
        for meter_id in self.meter_configs.keys():
            entry = self.status_cache.setdefault(meter_id, {
                "status": "offline",
                "last_seen": None,
                "last_seen_epoch": None,
                "minutes_since_seen": None,
                "updated_at_epoch": now,
            })
            last_seen_epoch = entry.get("last_seen_epoch")
            seconds_since_seen = None if last_seen_epoch is None else max(0, now - int(last_seen_epoch))
            minutes_since_seen = None if seconds_since_seen is None else seconds_since_seen // 60
            new_status = self.derive_status(seconds_since_seen)
            if entry.get("minutes_since_seen") != minutes_since_seen:
                entry["minutes_since_seen"] = minutes_since_seen
                any_changed = True
            meter_name = self.meter_names.get(meter_id) or meter_id
            self.publish(self.topic(meter_name, "minutes_since_seen"), "" if minutes_since_seen is None else minutes_since_seen)
            self.publish(self.topic(meter_name, "last_seen_epoch"), "" if last_seen_epoch is None else last_seen_epoch)
            if force or entry.get("status") != new_status:
                self.set_meter_status(meter_id, new_status, publish=True)
                any_changed = True
        if force or any_changed:
            self.write_status_file()
        self.last_status_publish = time.time()

    def process(self, msg: Dict[str, Any]):
        meter_id = str(msg.get("id"))
        if self.meter_whitelist and meter_id not in self.meter_whitelist:
            logging.debug("Meter %s not in whitelist, skipping.", meter_id)
            return
        if self.meter_blacklist and meter_id in self.meter_blacklist:
            logging.debug("Meter %s in blacklist, skipping.", meter_id)
            return
        meter_name = msg.get("name") or self.meter_names.get(meter_id) or meter_id
        timestamp, timestamp_epoch = self.parse_timestamp(msg.get("timestamp"))
        self.publish(self.topic(meter_name, "raw_json"), msg)
        self.publish(self.topic(meter_name, "last_seen"), timestamp)
        self.publish(self.topic(meter_name, "last_seen_epoch"), timestamp_epoch)
        self.publish(self.topic(meter_name, "minutes_since_seen"), 0)
        self.publish(self.topic(meter_name, "id"), msg.get("id", ""))
        self.publish(self.topic(meter_name, "meter_type"), msg.get("meter", ""))
        self.publish(self.topic(meter_name, "media"), msg.get("media", ""))
        
        parsed_fields = dict(self.normalized_fields(msg))
        for key, value in parsed_fields.items():
            self.publish(self.topic(meter_name, key), value)
            
        self.process_smart_deltas(meter_id, meter_name, timestamp_epoch, parsed_fields)

        entry = self.status_cache.setdefault(meter_id, {
            "status": "offline",
            "last_seen": None,
            "last_seen_epoch": None,
            "minutes_since_seen": None,
            "updated_at_epoch": int(time.time()),
        })
        entry["last_seen"] = timestamp
        entry["last_seen_epoch"] = timestamp_epoch
        entry["minutes_since_seen"] = 0
        self.set_meter_status(meter_id, "online", publish=True)
        self.write_status_file()

    def process_smart_deltas(self, meter_id: str, meter_name: str, timestamp_epoch: int, parsed_fields: Dict[str, Any]):
        entry = self.rates_cache.setdefault(meter_id, {})
        dt_now = datetime.fromtimestamp(timestamp_epoch)
        today_date = dt_now.strftime("%Y-%m-%d")

        if entry.get("today_date") != today_date:
            entry["today_date"] = today_date
            entry["daily_start_volume_m3"] = parsed_fields.get("volume_m3")
            entry["daily_start_energy_kwh"] = parsed_fields.get("energy_kwh")
            self.publish(self.topic(meter_name, "today_volume_m3"), 0.0)
            self.publish(self.topic(meter_name, "today_energy_kwh"), 0.0)

        prev_epoch = entry.get("last_reading_epoch")
        time_diff = (timestamp_epoch - prev_epoch) if prev_epoch else 0
        entry["last_reading_epoch"] = timestamp_epoch

        current_vol = parsed_fields.get("volume_m3")
        if current_vol is not None:
            try:
                current_vol = float(current_vol)
                prev_vol = entry.get("last_volume_m3")
                start_vol = entry.get("daily_start_volume_m3")

                if prev_vol is not None and prev_vol <= current_vol:
                    delta_vol = current_vol - prev_vol
                    self.publish(self.topic(meter_name, "delta_volume_m3"), round(delta_vol, 4))
                    if time_diff > 0:
                        flow_m3h = (delta_vol / time_diff) * 3600.0
                        self.publish(self.topic(meter_name, "calculated_flow_m3h"), round(flow_m3h, 4))
                
                if start_vol is not None and start_vol <= current_vol:
                    today_vol = current_vol - float(start_vol)
                    self.publish(self.topic(meter_name, "today_volume_m3"), round(today_vol, 4))
                    
                entry["last_volume_m3"] = current_vol
                if start_vol is None:
                    entry["daily_start_volume_m3"] = current_vol
            except ValueError:
                pass

        current_energy = parsed_fields.get("energy_kwh")
        if current_energy is not None:
            try:
                current_energy = float(current_energy)
                prev_energy = entry.get("last_energy_kwh")
                start_energy = entry.get("daily_start_energy_kwh")

                if prev_energy is not None and prev_energy <= current_energy:
                    delta_energy = current_energy - prev_energy
                    self.publish(self.topic(meter_name, "delta_energy_kwh"), round(delta_energy, 4))
                    if time_diff > 0:
                        power_kw = (delta_energy / time_diff) * 3600.0
                        self.publish(self.topic(meter_name, "calculated_power_kw"), round(power_kw, 4))
                
                if start_energy is not None and start_energy <= current_energy:
                    today_energy = current_energy - float(start_energy)
                    self.publish(self.topic(meter_name, "today_energy_kwh"), round(today_energy, 4))

                entry["last_energy_kwh"] = current_energy
                if start_energy is None:
                    entry["daily_start_energy_kwh"] = current_energy
            except ValueError:
                pass

        self.write_rates_file()


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
                if time.time() - bridge.last_status_publish >= STATUS_PUBLISH_INTERVAL_SECONDS:
                    bridge.publish_statuses()
                time.sleep(0.1)
                continue
            msg = parse_line(line)
            if msg:
                bridge.process(msg)
                if time.time() - bridge.last_status_publish >= STATUS_PUBLISH_INTERVAL_SECONDS:
                    bridge.publish_statuses()
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
