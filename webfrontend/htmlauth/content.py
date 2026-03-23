#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LoxBerry Plugin wM-Bus Heat Meter Bridge - Python Content Provider

import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

PLUGIN_FOLDER = "loxberry-wmbusmeters"
PLUGIN_NAME = "wmbusmetersbridge"

CONFIG_DIR = Path(os.environ.get('LBP_CONFIGDIR', f'/opt/loxberry/config/plugins/{PLUGIN_FOLDER}'))
BIN_DIR = Path(os.environ.get('LBP_BINDIR', f'/opt/loxberry/bin/plugins/{PLUGIN_FOLDER}'))
DAEMON_PATH = Path(f'/opt/loxberry/system/daemons/plugins/{PLUGIN_NAME}')
INSTALLER = BIN_DIR / 'install_deps.sh'
TMP_DIR = Path(os.environ.get('LBPTMPDIR', f'/tmp/loxberry-wmbusmeters'))
TMP_DIR.mkdir(parents=True, exist_ok=True)
ERROR_LOG = TMP_DIR / 'ui_error.log'
CONFIG_FILE = CONFIG_DIR / 'config.json'
EXAMPLE_FILE = CONFIG_DIR / 'config.example.json'
LOG_FILE = TMP_DIR / 'bridge.log'
DISCOVERY_LOG = TMP_DIR / 'discovery.log'
DEPS_LOG = TMP_DIR / 'deps_install.log'
PID_FILE = TMP_DIR / 'bridge.pid'

HEX_16 = re.compile(r'^[0-9A-Fa-f]{32}$')
METER_NAME_SAFE = re.compile(r'[^A-Za-z0-9_.-]+')
STATUS_STALE_MINUTES = 15
STATUS_OFFLINE_MINUTES = 60

if 'LBPBINDIR' in os.environ:
    BIN_DIR_FOR_IMPORT = Path(os.environ['LBPBINDIR'])
elif 'LBP_BINDIR' in os.environ:
    BIN_DIR_FOR_IMPORT = Path(os.environ['LBP_BINDIR'])
else:
    BIN_DIR_FOR_IMPORT = BIN_DIR
    if not BIN_DIR_FOR_IMPORT.exists():
        BIN_DIR_FOR_IMPORT = Path(__file__).resolve().parents[2] / 'bin'

sys.path.insert(0, str(BIN_DIR_FOR_IMPORT))
from common import (  # type: ignore
    HEALTHCHECK_FILE,
    METER_STATUS_FILE,
    admin_home_url,
    mqtt_widget_url,
    plugin_overview_url,
    resolve_mqtt_settings,
)


def read_health_check_status():
    try:
        if HEALTHCHECK_FILE.exists():
            with HEALTHCHECK_FILE.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        log_error(f'read_health_check_status failed: {e}')
    return {}


def log_error(msg):
    try:
        with ERROR_LOG.open('a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


def default_config():
    return {
        'mqtt': {'base_topic': 'wmbus/heat', 'retain': True, 'qos': 1, 'client_id': 'loxberry-wmbusmeters'},
        'radio': {
            'device': 'rtlwmbus', 'mode': 't1', 'ppm': 0, 'rtl_index': 0, 'reset_after': '23h',
            'log_level': 'normal', 'log_telegrams': False, 'ignore_duplicates': True, 'discovery_seconds': 30,
            'meter_whitelist': [], 'meter_blacklist': []
        },
        'meters': []
    }


def load_config():
    path = CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_FILE
    if not path.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        default = default_config()
        save_config(default)
        return default

    with path.open('r', encoding='utf-8') as f:
        cfg = json.load(f)

    default = default_config()
    default['mqtt'].update(cfg.get('mqtt', {}))
    default['radio'].update(cfg.get('radio', {}))
    if isinstance(cfg.get('meters'), list):
        default['meters'] = cfg['meters']
    return default


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with CONFIG_FILE.open('w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write('\n')


def read_post_data():
    try:
        length = int(os.environ.get('CONTENT_LENGTH', '0') or '0')
    except ValueError:
        length = 0
    return sys.stdin.read(length) if length > 0 else ''


def get_form():
    method = os.environ.get('REQUEST_METHOD', 'GET').upper()
    raw = read_post_data() if method == 'POST' else os.environ.get('QUERY_STRING', '')
    return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def bool_from_form(form, key):
    return form.get(key) in ('1', 'on', 'true', 'yes')


def shell(cmd, timeout=60):
    try:
        import os
        env = os.environ.copy()
        env["PATH"] = env.get("PATH", "") + ":/usr/bin:/usr/local/bin:/usr/sbin:/sbin:/bin"
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, env=env)
    except Exception as e:
        log_error(f'shell failed {cmd}: {e}')
        class Dummy:
            returncode = 1
            stdout = ''
            stderr = str(e)
        return Dummy()


def sudo_shell(cmd, timeout=60):
    return shell(['sudo', '-n'] + cmd, timeout=timeout)


def command_exists(name):
    return shell(['bash', '-lc', f'command -v {name} >/dev/null 2>&1']).returncode == 0


def deps_status():
    return {
        'python3': command_exists('python3'),
        'mosquitto_pub': command_exists('mosquitto_pub'),
        'rtl_test': command_exists('rtl_test'),
        'rtl_wmbus': command_exists('rtl_wmbus'),
        'wmbusmeters': command_exists('wmbusmeters'),
        'installer': INSTALLER.exists(),
        'sudo_daemon': sudo_shell([str(DAEMON_PATH), 'status']).returncode in (0, 1) if DAEMON_PATH.exists() else False,
        'sudo_installer': sudo_shell([str(INSTALLER), '--status']).returncode == 0 if INSTALLER.exists() else False,
    }


def dependency_job_status():
    if INSTALLER.exists():
        res = sudo_shell([str(INSTALLER), '--status'])
        txt = (res.stdout or res.stderr or '').strip()
        if txt:
            return txt.replace('sudo: a password is required', 'sudoers not active').replace('sudo: a terminal is required to read the password', 'sudoers not active')
    return 'idle'


def service_status():
    if DAEMON_PATH.exists():
        result = sudo_shell([str(DAEMON_PATH), 'status'])
        out = (result.stdout or '').strip()
        err = (result.stderr or '').strip()
        if out:
            return out
        if err:
            return err.replace('sudo: a password is required', 'sudoers not active').replace('sudo: a terminal is required to read the password', 'sudoers not active')
    if PID_FILE.exists():
        return f'Running (pid file: {PID_FILE})'
    return 'Stopped'


def read_tail(path, lines=120):
    try:
        if not path.exists():
            return ''
        content = path.read_text(encoding='utf-8', errors='replace').splitlines()
        return '\n'.join(content[-lines:])
    except Exception as e:
        log_error(f'read_tail failed for {path}: {e}')
        return ''


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def normalize_meter_name(raw_name, fallback):
    cleaned = METER_NAME_SAFE.sub('_', str(raw_name or '').strip()).strip('._-')
    return cleaned or fallback


def normalize_meter_id(raw_id):
    return str(raw_id or '').strip()


def normalize_key(raw_key):
    return ''.join(str(raw_key or '').split()).upper()


def key_is_valid(key):
    return not key or bool(HEX_16.match(key))


def key_masked(key):
    key = normalize_key(key)
    if not key:
        return '-'
    if len(key) <= 8:
        return '•' * len(key)
    return f'{key[:4]}…{key[-4:]}'


def format_minutes_ago(minutes):
    if minutes is None:
        return 'No telegram yet'
    minutes = max(0, int(minutes))
    if minutes < 1:
        return '< 1 min ago'
    if minutes < 60:
        return f'{minutes} min ago'
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f'{hours}h {mins}m ago' if mins else f'{hours}h ago'
    days, hours = divmod(hours, 24)
    return f'{days}d {hours}h ago' if hours else f'{days}d ago'


def meter_status_class(status):
    return {'online': 'good', 'stale': 'warn', 'offline': 'bad'}.get((status or '').lower(), 'warn')


def normalize_discovered_driver(data):
    return str(
        data.get('meter_type')
        or data.get('meter')
        or data.get('driver')
        or data.get('device')
        or ''
    ).strip()


def normalize_discovered_name(data, meter_id, driver):
    base = (
        data.get('name')
        or data.get('meter')
        or data.get('meter_type')
        or data.get('model')
        or ''
    )
    candidate = normalize_meter_name(base, '') if base else ''
    if candidate:
        return candidate.lower()
    if driver:
        return normalize_meter_name(f'{driver}_{meter_id[-6:]}', f'meter_{meter_id[-6:]}').lower()
    return normalize_meter_name(f'meter_{meter_id[-6:]}', f'meter_{meter_id[-6:]}').lower()


def build_existing_meter_maps(cfg):
    by_id = {}
    names = set()
    duplicate_ids = set()
    for idx, meter in enumerate(cfg.get('meters', [])):
        meter_id = normalize_meter_id(meter.get('id'))
        if meter_id:
            if meter_id in by_id:
                duplicate_ids.add(meter_id)
            else:
                by_id[meter_id] = idx
        names.add(str(meter.get('name', '')).strip())
    return by_id, names, sorted(duplicate_ids)


def read_meter_statuses():
    try:
        if METER_STATUS_FILE.exists():
            with METER_STATUS_FILE.open('r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception as e:
        log_error(f'read_meter_statuses failed: {e}')
    return {}


def enrich_meter_statuses(cfg):
    raw_status = read_meter_statuses()
    meters = []
    for idx, meter in enumerate(cfg.get('meters', [])):
        meter_id = normalize_meter_id(meter.get('id'))
        status_info = raw_status.get(meter_id) if meter_id else None
        status = (status_info or {}).get('status', 'offline' if meter.get('enabled', True) else 'disabled')
        minutes = (status_info or {}).get('minutes_since_seen')
        last_seen = (status_info or {}).get('last_seen')
        meters.append({
            'index': idx,
            'config': meter,
            'status': status,
            'minutes_since_seen': minutes,
            'last_seen': last_seen,
        })
    return meters


def save_from_form(form, cfg):
    cfg['mqtt'] = {
        'base_topic': form.get('mqtt_base_topic', 'wmbus/heat').strip().rstrip('/'),
        'retain': bool_from_form(form, 'mqtt_retain'),
        'qos': int(form.get('mqtt_qos', '1') or '1'),
        'client_id': cfg.get('mqtt', {}).get('client_id', 'loxberry-wmbusmeters'),
    }
    cfg['radio'] = {
        'device': form.get('radio_device', 'rtlwmbus'),
        'mode': form.get('radio_mode', 't1'),
        'ppm': int(form.get('radio_ppm', '0') or '0'),
        'rtl_index': int(form.get('radio_rtl_index', '0') or '0'),
        'reset_after': form.get('radio_reset_after', '23h'),
        'log_level': form.get('radio_log_level', 'normal'),
        'log_telegrams': bool_from_form(form, 'radio_log_telegrams'),
        'ignore_duplicates': bool_from_form(form, 'radio_ignore_duplicates'),
        'discovery_seconds': int(form.get('radio_discovery_seconds', '30') or '30'),
        'meter_whitelist': [x.strip() for x in form.get('radio_meter_whitelist', '').split(',') if x.strip()],
        'meter_blacklist': [x.strip() for x in form.get('radio_meter_blacklist', '').split(',') if x.strip()],
    }

    new_meters = []
    meter_indices = []
    for k in form.keys():
        if k.startswith('meter_') and k.endswith('_name'):
            try:
                idx = int(k.split('_')[1])
                if idx not in meter_indices:
                    meter_indices.append(idx)
            except ValueError:
                pass

    meter_indices.sort()
    seen_ids = {}
    warnings = []

    for i in meter_indices:
        name = normalize_meter_name(form.get(f'meter_{i}_name', '').strip(), f'meter_{i+1}')
        meter = {
            'name': name,
            'label': form.get(f'meter_{i}_label', '').strip() or f'Meter {i+1}',
            'id': normalize_meter_id(form.get(f'meter_{i}_id', '')),
            'driver': form.get(f'meter_{i}_driver', '').strip() or 'sharky',
            'key': normalize_key(form.get(f'meter_{i}_key', '')),
            'enabled': bool_from_form(form, f'meter_{i}_enabled')
        }
        if not key_is_valid(meter['key']):
            raise ValueError(f"Meter '{meter['label']}' has an invalid AES key. Expected 32 hex chars.")
        if meter['id']:
            if meter['id'] in seen_ids:
                first_label = seen_ids[meter['id']]
                warnings.append(f"Duplicate meter ID {meter['id']} kept once ({first_label} / {meter['label']}).")
                continue
            seen_ids[meter['id']] = meter['label']
        if meter['name'] or meter['id'] or meter['enabled']:
            new_meters.append(meter)

    cfg['meters'] = new_meters
    save_config(cfg)
    return warnings


def handle_action(form, cfg):
    messages = []
    action = form.get('action', '')
    if action == 'save':
        messages.append('Configuration saved.')
        messages.extend(save_from_form(form, cfg))
    elif action in ('start', 'stop', 'restart', 'status'):
        if action in ('start', 'restart'):
            messages.extend(save_from_form(form, cfg))
        if DAEMON_PATH.exists():
            result = sudo_shell([str(DAEMON_PATH), action])
            msg = f'{action}: rc={result.returncode}'
            if result.stdout:
                msg += ' | ' + result.stdout.strip().replace('\n', ' | ')
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
            messages.append(msg)
        else:
            messages.append(f'Daemon not found at {DAEMON_PATH}')
    elif action == 'install_deps':
        if INSTALLER.exists():
            result = sudo_shell([str(INSTALLER), '--background'])
            msg = f'install_deps: rc={result.returncode}'
            if result.stdout:
                msg += ' | ' + result.stdout.strip().replace('\n', ' | ')
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
            messages.append(msg)
        else:
            messages.append(f'Installer not found at {INSTALLER}')
    elif action == 'discover':
        messages.extend(save_from_form(form, cfg))
        discover = BIN_DIR / 'discover.py'
        if not command_exists('wmbusmeters'):
            messages.append('wmbusmeters is not installed. Click “Repair / update dependencies” first.')
        elif discover.exists():
            radio = cfg.get('radio', {})
            mode = str(radio.get('mode', 't1')).strip().lower()
            ppm = int(radio.get('ppm', 0))
            rtl_index = int(radio.get('rtl_index', 0))
            device = str(radio.get('device', 'rtlwmbus')).strip()
            if device == 'rtlwmbus':
                device_expr = f'rtlwmbus[{rtl_index}]:{mode}(ppm={ppm})'
            else:
                device_expr = device

            result = sudo_shell([
                str(discover), '--device', device_expr,
                '--seconds', str(cfg.get('radio', {}).get('discovery_seconds', 30)), '--logfile', str(DISCOVERY_LOG)
            ], timeout=max(180, cfg.get('radio', {}).get('discovery_seconds', 30) + 20))
            msg = f'discovery: rc={result.returncode}'
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
            messages.append(msg)
        else:
            messages.append('discover.py not found.')
    elif action == 'clear_discovery_log':
        DISCOVERY_LOG.write_text('', encoding='utf-8')
        messages.append('Discovery log cleared.')
    elif action == 'run_healthcheck':
        healthcheck_script = BIN_DIR / 'healthcheck.py'
        if healthcheck_script.exists():
            result = shell([sys.executable, str(healthcheck_script)])
            msg = f'Health check: rc={result.returncode}'
            if result.stdout:
                msg += ' | ' + result.stdout.strip().replace('\n', ' | ')
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
            messages.append(msg)
        else:
            messages.append('healthcheck.py not found.')
    return ' '.join(m for m in messages if m), cfg


def status_badge(text):
    lower = text.lower()
    cls = 'bad'
    if 'running' in lower or lower == 'ok' or lower == 'online':
        cls = 'good'
    elif 'idle' in lower or 'stopped' in lower or 'not running' in lower or lower == 'stale':
        cls = 'warn'
    return f'<span class="badge {cls}">{esc(text)}</span>'


def parse_discovery_log(log_path: Path) -> list[dict]:
    meters = {}
    try:
        if not log_path.exists():
            return []
        content = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
        for line in content:
            line = line.strip()
            if not (line.startswith('{') and line.endswith('}')):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            meter_id = normalize_meter_id(data.get('id'))
            driver = normalize_discovered_driver(data)
            if not meter_id or not driver:
                continue
            key = normalize_key(data.get('key', ''))
            current = meters.get(meter_id, {
                'id': meter_id,
                'driver': driver,
                'key': '',
                'name': normalize_discovered_name(data, meter_id, driver),
                'source_name': str(data.get('name') or '').strip(),
                'last_seen': str(data.get('timestamp') or '').strip(),
                'telegram_count': 0,
            })
            current['driver'] = current.get('driver') or driver
            current['name'] = current.get('name') or normalize_discovered_name(data, meter_id, driver)
            if key and not current.get('key'):
                current['key'] = key
            if data.get('timestamp'):
                current['last_seen'] = str(data.get('timestamp'))
            current['telegram_count'] = int(current.get('telegram_count', 0)) + 1
            meters[meter_id] = current
    except Exception as e:
        log_error(f'parse_discovery_log failed for {log_path}: {e}')
    return sorted(meters.values(), key=lambda item: (item.get('id') or ''))


def render_status_summary(cfg):
    enriched = enrich_meter_statuses(cfg)
    enabled = [m for m in enriched if m['config'].get('enabled', True)]
    counts = {'online': 0, 'stale': 0, 'offline': 0}
    for meter in enabled:
        counts[meter['status']] = counts.get(meter['status'], 0) + 1
    return f'''
    <div class="card" style="margin-top: 20px;">
      <h2>Meter reception health</h2>
      <p>
        <strong>{counts.get('online', 0)}</strong> online &nbsp;·&nbsp;
        <strong>{counts.get('stale', 0)}</strong> stale &nbsp;·&nbsp;
        <strong>{counts.get('offline', 0)}</strong> offline
      </p>
      <p class="footerhint">Pragmatic defaults: stale after {STATUS_STALE_MINUTES} min, offline after {STATUS_OFFLINE_MINUTES} min without telegrams.</p>
    </div>
    '''


def render_overview(cfg):
    meter_count = sum(1 for m in cfg.get('meters', []) if m.get('enabled'))
    configured_count = sum(1 for m in cfg.get('meters', []) if m.get('enabled') and m.get('id'))

    health_status = read_health_check_status()
    health_check_html = ''
    if health_status:
        health_check_html = f'''
        <div class="card">
          <h2>Health Check</h2>
          <p><strong>Overall Status:</strong> {status_badge(health_status.get('overall_status', 'unknown'))}</p>
          <table class="compact">
            <tr><th>wmbusmeters</th><td>{esc(health_status.get('wmbusmeters_status_text', 'N/A'))}</td></tr>
            <tr><th>RTL-SDR</th><td>{esc(health_status.get('rtl_sdr_status_text', 'N/A'))}</td></tr>
          </table>
          <div class="button-row" style="margin-top: 15px;">
            <button data-inline="true" data-mini="true" name="action" value="run_healthcheck" class="secondary">Re-run Health Check</button>
          </div>
        </div>
        '''

    dongles_html = get_connected_dongles_html()

    return f'''
    <style>
      .badge.inline-status {{ margin-left: .4rem; }}
      .meter-status-chip {{ display:inline-block; padding:.15rem .5rem; border-radius:999px; font-size:12px; font-weight:600; }}
      .meter-status-chip.good {{ background:#e9f7e7; color:#127d00; }}
      .meter-status-chip.warn {{ background:#fff5d6; color:#8b6500; }}
      .meter-status-chip.bad {{ background:#fdecea; color:#b42318; }}
      .meter-status-hint {{ color:#666; font-size:12px; margin-top:4px; }}
      .aes-key-row {{ display:flex; gap:8px; align-items:end; }}
      .aes-key-row .aes-key-input-wrap {{ flex:1; }}
      .aes-key-row .toggle-key-visibility {{ white-space:nowrap; }}
      .aes-key-help {{ font-size:12px; color:#666; margin-top:4px; }}
      .discovery-tag {{ display:inline-block; padding:.15rem .45rem; border-radius:999px; background:#eef5fb; color:#245ea8; font-size:12px; font-weight:600; }}
      .discovery-tag.warn {{ background:#fff5d6; color:#8b6500; }}
      .discovery-tag.good {{ background:#e9f7e7; color:#127d00; }}
    </style>

    <div class="card" style="margin-bottom: 20px; background: #eef5fb; border-left: 5px solid var(--blue);">
      <h2><i class="fa fa-info-circle"></i> Welcome to the wM-Bus Heat Meter Bridge!</h2>
      <p>This plugin connects your compatible wireless M-Bus (wM-Bus) meters to LoxBerry using an RTL-SDR USB dongle.
      It reads meter transmissions, translates them, and forwards the data into your MQTT broker for smart home integration.</p>
      <p><strong>Quick Start:</strong> Ensure your SDR dongle is connected, configure your meter IDs in the <em>Meters</em> tab, and hit Start.
      If you don't know your meter IDs, use the <em>Discovery & Logs</em> tab to find them.</p>
    </div>

    <div class="grid two">
      <div class="card">
        <h2>Bridge Control</h2>
        <p><strong>Status:</strong> {status_badge(service_status())}</p>
        <p><strong>Dependency job:</strong> {status_badge(dependency_job_status())}</p>
        <p><strong>Meters enabled:</strong> {meter_count} &nbsp; <strong>Configured IDs:</strong> {configured_count}</p>
        <div class="button-row" style="margin-top: 15px;">
          <button data-inline="true" data-mini="true" name="action" value="start">Start</button>
          <button data-inline="true" data-mini="true" name="action" value="restart">Restart</button>
          <button data-inline="true" data-mini="true" name="action" value="stop" class="secondary">Stop</button>
        </div>
      </div>

      <div class="card">
        <h2>Connected USB SDR Dongles</h2>
        {dongles_html}
      </div>
    </div>

    {render_status_summary(cfg)}
    {health_check_html}
    '''


def render_mqtt(cfg):
    mqtt_live = resolve_mqtt_settings(cfg)
    return f'''
    <div class="card narrow" style="margin-bottom: 20px;">
      <h2>MQTT Topic Settings</h2>
      <p>This plugin follows the LoxBerry MQTT widget. Broker host, port and credentials come from LoxBerry. Edit them in the MQTT widget; set only the topic prefix here.</p>
      <label>MQTT topic prefix
        <input type="text" name="mqtt_base_topic" value="{esc(cfg['mqtt'].get('base_topic', 'wmbus/heat'))}">
      </label>
      <div class="grid two compactgrid">
        <label>QoS
          <input type="number" min="0" max="2" name="mqtt_qos" value="{esc(cfg['mqtt'].get('qos', 1))}">
        </label>
        <label class="checkbox"><input type="checkbox" name="mqtt_retain" {'checked' if cfg['mqtt'].get('retain', True) else ''}> Retain messages</label>
      </div>
      <div class="button-row" style="margin-top: 15px;">
        <button data-inline="true" data-mini="true" name="action" value="save">Save Settings</button>
      </div>
    </div>

    <div class="card narrow">
      <h2>MQTT Widget Integration Details</h2>
      <table class="compact">
        <tr><th>Broker source</th><td>{esc(mqtt_live.get('source', 'unknown'))}</td></tr>
        <tr><th>Broker host</th><td>{esc(mqtt_live.get('host', '127.0.0.1'))}</td></tr>
        <tr><th>Broker port</th><td>{esc(mqtt_live.get('port', 1883))}</td></tr>
        <tr><th>Resolved Topic</th><td>{esc(cfg['mqtt'].get('base_topic', 'wmbus/heat'))}</td></tr>
      </table>
      <div class="button-row linkrow" style="margin-top: 15px;">
        <a class="linkbtn ui-btn ui-btn-inline ui-mini" href="{esc(mqtt_widget_url())}"><i class="fa fa-search"></i> Open MQTT Finder</a>
      </div>
    </div>
    '''


def get_connected_dongles_html():
    try:
        res = subprocess.run(["lsusb"], capture_output=True, text=True, errors="replace", timeout=5)
        dongles = []
        for line in res.stdout.splitlines():
            line_lower = line.lower()
            if 'rtl2838' in line_lower or 'rtl2832' in line_lower or 'dvb-t' in line_lower or 'realtek' in line_lower:
                parts = line.split(":", 2)
                dongles.append(parts[2].strip() if len(parts) == 3 else line.strip())

        if command_exists('rtl_test'):
            res_rtl = subprocess.run(['rtl_test', '-t'], capture_output=True, text=True, errors="replace", timeout=5)
            rtl_output = res_rtl.stderr + res_rtl.stdout
            rtl_dongles = []
            for line in rtl_output.splitlines():
                if "Realtek" in line and "SN:" in line:
                    rtl_dongles.append(line.strip())
            if rtl_dongles:
                dongles = rtl_dongles

        if not dongles:
            return '<div style="padding: 10px; border: 1px solid #ccc; border-radius: 5px; background: #fff5d6; color: #8b6500; font-size: 14px;"><strong>No RTL-SDR compatible DVB-T Dongles detected.</strong></div>'

        html_list = ""
        for i, d in enumerate(dongles):
            html_list += f'<div style="margin-bottom: 4px;"><b>#{i}</b>: <span style="color: #2d6cdf;">{esc(d)}</span></div>'

        return f'<div style="padding: 10px; border: 1px solid #ccc; border-radius: 5px; background: #e9f7e7; color: #127d00; font-size: 14px; font-family: monospace;">{html_list}</div>'
    except Exception as e:
        return f'<div style="color: red; font-size: 13px;">Error detecting dongles: {esc(e)}</div>'


def render_radio(cfg):
    radio = cfg['radio']
    current_device = radio.get('device', 'rtlwmbus')
    device_options = [
        ('rtlwmbus', 'RTL-SDR Dongle (Recommended)'),
        ('auto', 'Auto-detect Device'),
        ('/dev/ttyUSB0', 'Serial USB Dongle (ttyUSB0)'),
        ('/dev/ttyUSB1', 'Serial USB Dongle (ttyUSB1)'),
        ('/dev/ttyAMA0', 'Raspberry Pi GPIO (ttyAMA0)')
    ]
    if current_device not in [d[0] for d in device_options]:
        device_options.insert(0, (current_device, f'Custom ({current_device})'))

    return f'''
    <div class="card narrow">
      <h2>Radio / RTL-SDR Setup</h2>
      <div class="grid two compactgrid">
        <label>Device (Hardware Interface)
          <select name="radio_device">
            {''.join(f'<option value="{d[0]}" {"selected" if current_device == d[0] else ""}>{d[1]}</option>' for d in device_options)}
          </select>
        </label>
        <label>Mode
          <select name="radio_mode">
            {''.join(f'<option value="{m}" {"selected" if radio.get("mode") == m else ""}>{m.upper()}</option>' for m in ['t1','c1','t1,c1'])}
          </select>
        </label>
        <label>PPM correction
          <input type="number" name="radio_ppm" value="{esc(radio.get('ppm', 0))}">
        </label>
        <label>RTL index
          <input type="number" name="radio_rtl_index" value="{esc(radio.get('rtl_index', 0))}">
        </label>
        <label>Discovery duration (seconds)
          <input type="number" min="5" max="300" name="radio_discovery_seconds" value="{esc(radio.get('discovery_seconds', 30))}">
        </label>
        <label>Reset after
          <input type="text" name="radio_reset_after" value="{esc(radio.get('reset_after', '23h'))}">
        </label>
        <label>Log level
          <select name="radio_log_level">
            {''.join(f'<option value="{m}" {"selected" if radio.get("log_level") == m else ""}>{m}</option>' for m in ['normal','verbose','debug'])}
          </select>
        </label>
      </div>
      <div class="grid two compactgrid">
        <label class="checkbox"><input type="checkbox" name="radio_log_telegrams" {'checked' if radio.get('log_telegrams', False) else ''}> Log telegrams</label>
        <label class="checkbox"><input type="checkbox" name="radio_ignore_duplicates" {'checked' if radio.get('ignore_duplicates', True) else ''}> Ignore duplicates</label>
      </div>
      <h3>Meter Filtering (IDs separated by commas)</h3>
      <label>Whitelist (only process these meters)
        <input type="text" name="radio_meter_whitelist" value="{esc(','.join(radio.get('meter_whitelist', [])))}">
      </label>
      <label>Blacklist (do not process these meters)
        <input type="text" name="radio_meter_blacklist" value="{esc(','.join(radio.get('meter_blacklist', [])))}">
      </label>
      <div class="button-row">
        <button name="action" value="save">Save</button>
        <button name="action" value="discover" class="secondary">Run discovery</button>
      </div>
    </div>
    '''


def render_meter_card(meter_data, index, status_info=None):
    meter = meter_data
    try:
        idx_num = int(index)
        display_idx = idx_num + 1
    except ValueError:
        display_idx = "${nextMeterIndex + 1}"

    status = (status_info or {}).get('status', 'offline' if meter.get('enabled', True) else 'disabled')
    minutes = (status_info or {}).get('minutes_since_seen')
    status_label = status if status != 'disabled' else 'disabled'
    last_seen_text = format_minutes_ago(minutes)
    badge_html = '' if status == 'disabled' else f'<span class="meter-status-chip {meter_status_class(status)}">{esc(status_label)}</span>'

    return f'''
    <div class="card meter-card" data-meter-index="{index}" data-meter-id="{esc(meter.get('id', ''))}">
        <h2>
            <span class="meter-label">{esc(meter.get('label', f'Meter {display_idx}'))}</span>
            {badge_html}
            <button type="button" class="ui-btn ui-mini ui-btn-inline ui-btn-icon-notext ui-icon-delete ui-btn-a remove-meter-btn" title="Remove Meter" style="float: right;">Remove</button>
        </h2>
        <div class="meter-status-hint">Last telegram: <span class="meter-last-seen">{esc(last_seen_text)}</span></div>
        <div class="grid two compactgrid">
            <label>Name
                <input type="text" name="meter_{index}_name" value="{esc(meter.get('name', f'meter_{display_idx}'))}" pattern="[A-Za-z0-9_.-]+" title="Allowed: letters, numbers, underscore, dash, dot">
            </label>
            <label>Label
                <input type="text" name="meter_{index}_label" value="{esc(meter.get('label', f'Meter {display_idx}'))}">
            </label>
            <label>Meter ID
                <input type="text" name="meter_{index}_id" value="{esc(meter.get('id', ''))}" placeholder="discover first, then paste ID here">
            </label>
            <label>Driver
                <input type="text" name="meter_{index}_driver" value="{esc(meter.get('driver', 'sharky'))}" placeholder="e.g., sharky">
            </label>
            <div>
              <label>AES key (optional)</label>
              <div class="aes-key-row">
                <div class="aes-key-input-wrap">
                  <input type="password" class="aes-key-input" name="meter_{index}_key" value="{esc(normalize_key(meter.get('key', '')))}" placeholder="32 hex chars if meter is encrypted" inputmode="latin" autocomplete="new-password" spellcheck="false" pattern="[0-9A-Fa-f]{32}" title="Optional, but if set it must be exactly 32 hex characters">
                </div>
                <button type="button" class="ui-btn ui-mini ui-btn-inline toggle-key-visibility">Show</button>
              </div>
              <div class="aes-key-help">Stored masked in the UI; validation accepts exactly 32 hex chars.</div>
            </div>
            <label class="checkbox"><input type="checkbox" name="meter_{index}_enabled" {'checked' if meter.get('enabled', True) else ''}> Enabled</label>
        </div>
    </div>
    '''


def render_meters(cfg):
    enriched = enrich_meter_statuses(cfg)
    cards = []
    for item in enriched:
        cards.append(render_meter_card(item['config'], item['index'], item))

    by_id, _, duplicate_ids = build_existing_meter_maps(cfg)
    duplicate_warning = ''
    if duplicate_ids:
        duplicate_warning = f'<div class="ui-corner-all ui-shadow ui-bar-a" style="margin-bottom:1em; background:#fff5d6; color:#8b6500;"><p>Duplicate configured meter IDs detected: {esc(", ".join(duplicate_ids))}. Saving will keep the first entry per ID.</p></div>'

    template_html = render_meter_card({}, "METER_INDEX_PLACEHOLDER")

    return f'''
    {duplicate_warning}
    <div id="meter-container">
        {''.join(cards)}
    </div>
    <div class="button-row">
        <button type="button" id="add-meter-btn" class="ui-btn ui-btn-inline ui-corner-all ui-shadow ui-icon-plus ui-btn-icon-left">Add New Meter</button>
        <button name="action" value="save">Save All Meters</button>
        <button name="action" value="start" class="secondary">Save & Start Bridge</button>
    </div>

    <script>
        let nextMeterIndex = {len(cfg.get('meters', []))};
        const configuredMeterIds = {json.dumps(by_id)};

        function bindMeterCard($card) {{
            $card.find('.remove-meter-btn').off('click').on('click', function() {{
                $(this).closest('.meter-card').remove();
            }});
            $card.find('.toggle-key-visibility').off('click').on('click', function() {{
                const $input = $(this).closest('.aes-key-row').find('.aes-key-input');
                const visible = $input.attr('type') === 'text';
                $input.attr('type', visible ? 'password' : 'text');
                $(this).text(visible ? 'Show' : 'Hide');
            }});
            $card.find('.aes-key-input').off('input').on('input', function() {{
                const cleaned = ($(this).val() || '').replace(/\s+/g, '').toUpperCase();
                if (cleaned !== $(this).val()) {{
                    $(this).val(cleaned);
                }}
            }});
        }}

        function refreshMeterIds() {{
            Object.keys(configuredMeterIds).forEach(key => delete configuredMeterIds[key]);
            $('#meter-container .meter-card').each(function() {{
                const index = $(this).data('meter-index');
                const id = ($(`input[name="meter_${{index}}_id"]`).val() || '').trim();
                if (id) configuredMeterIds[id] = index;
            }});
        }}

        function defaultLabelForIndex(index) {{
            return `Meter ${{parseInt(index, 10) + 1}}`;
        }}

        function addMeter(meterData = {{}}) {{
            let newMeterHtml = `{template_html}`;
            newMeterHtml = newMeterHtml.replace(/METER_INDEX_PLACEHOLDER/g, nextMeterIndex);
            const $newCard = $(newMeterHtml);
            fillMeterCard($newCard, nextMeterIndex, meterData);
            $('#meter-container').append($newCard);
            $(`#meter-container .meter-card[data-meter-index="${{nextMeterIndex}}"]`).enhanceWithin();
            bindMeterCard($newCard);
            nextMeterIndex++;
            refreshMeterIds();
            return $newCard;
        }}

        function fillMeterCard($card, index, meterData) {{
            const fallbackName = meterData.name || `meter_${{parseInt(index, 10) + 1}}`;
            const fallbackLabel = meterData.label || defaultLabelForIndex(index);
            if (meterData.name) $card.find(`input[name="meter_${{index}}_name"]`).val(meterData.name);
            else $card.find(`input[name="meter_${{index}}_name"]`).val(fallbackName);
            if (meterData.label) $card.find(`input[name="meter_${{index}}_label"]`).val(meterData.label);
            else $card.find(`input[name="meter_${{index}}_label"]`).val(fallbackLabel);
            if (typeof meterData.id !== 'undefined') $card.find(`input[name="meter_${{index}}_id"]`).val(meterData.id);
            if (typeof meterData.driver !== 'undefined') $card.find(`input[name="meter_${{index}}_driver"]`).val(meterData.driver);
            if (typeof meterData.key !== 'undefined') $card.find(`input[name="meter_${{index}}_key"]`).val(meterData.key);
            if (typeof meterData.enabled !== 'undefined') $card.find(`input[name="meter_${{index}}_enabled"]`).prop('checked', !!meterData.enabled);
            $card.attr('data-meter-id', meterData.id || '');
            if (meterData.label) $card.find('.meter-label').text(meterData.label);
        }}

        function findExistingMeterIndexById(id) {{
            refreshMeterIds();
            return Object.prototype.hasOwnProperty.call(configuredMeterIds, id) ? configuredMeterIds[id] : null;
        }}

        function mergeDiscoveredMeter(meterData) {{
            const existingIndex = meterData.id ? findExistingMeterIndexById(meterData.id) : null;
            if (existingIndex !== null) {{
                const $card = $(`#meter-container .meter-card[data-meter-index="${{existingIndex}}"]`);
                fillMeterCard($card, existingIndex, meterData);
                bindMeterCard($card);
                return {{ mode: 'updated', index: existingIndex }};
            }}
            addMeter(meterData);
            return {{ mode: 'added', index: nextMeterIndex - 1 }};
        }}

        $(function() {{
            $('#meter-container .meter-card').each(function() {{ bindMeterCard($(this)); }});
            refreshMeterIds();
            $('#add-meter-btn').on('click', function() {{ addMeter(); }});
            window.wmbusDiscoveryMerge = mergeDiscoveredMeter;
        }});
    </script>
    '''


def render_discovery(cfg):
    bridge_log = read_tail(LOG_FILE, 80)
    discovery_log = read_tail(DISCOVERY_LOG, 120)
    deps_log = read_tail(DEPS_LOG, 80)

    rows = []
    for k, ok in deps_status().items():
        rows.append(f'<tr><td>{esc(k)}</td><td class="status">{"OK" if ok else "missing"}</td></tr>')

    deps_html = f'''
    <div class="card" style="margin-bottom: 20px;">
      <h2>Dependency Status</h2>
      <table class="compact"><tr><th>Component</th><th>Status</th></tr>{''.join(rows)}</table>
      <div class="button-row" style="margin-top: 15px;">
        <button data-inline="true" data-mini="true" name="action" value="install_deps" class="secondary">Repair / Update Dependencies</button>
      </div>
    </div>
    '''

    discovered_meters = parse_discovery_log(DISCOVERY_LOG)
    existing_by_id, existing_names, duplicate_ids = build_existing_meter_maps(cfg)
    discovered_meters_html = ''
    if discovered_meters:
        rows = []
        for meter in discovered_meters:
            existing_idx = existing_by_id.get(meter['id'])
            suggested_label = meter.get('source_name') or f"Meter {meter['id'][-6:]}"
            suggested_name = meter.get('name') or normalize_meter_name(suggested_label, f"meter_{meter['id'][-6:]}")
            while suggested_name in existing_names and existing_idx is None:
                suggested_name = normalize_meter_name(f"{suggested_name}_{len(existing_names)+1}", suggested_name)
            action_label = 'Update existing' if existing_idx is not None else 'Add new meter'
            action_tag = '<span class="discovery-tag warn">matches existing ID</span>' if existing_idx is not None else '<span class="discovery-tag good">new meter</span>'
            rows.append(f'''
            <tr>
                <td><code>{esc(meter['id'])}</code><br>{action_tag}</td>
                <td>{esc(meter['driver'])}</td>
                <td>{esc(key_masked(meter['key']))}</td>
                <td>{esc(str(meter.get('telegram_count', 1)))}</td>
                <td>{esc(meter.get('last_seen') or '-')}</td>
                <td>
                  <button type="button" class="ui-btn ui-mini ui-btn-inline ui-corner-all ui-shadow add-discovered-meter-btn"
                          data-meter-id="{esc(meter['id'])}"
                          data-meter-driver="{esc(meter['driver'])}"
                          data-meter-key="{esc(meter['key'])}"
                          data-meter-name="{esc(suggested_name)}"
                          data-meter-label="{esc(suggested_label)}">{esc(action_label)}</button>
                </td>
            </tr>
            ''')
        duplicate_notice = ''
        if duplicate_ids:
            duplicate_notice = f'<p style="color:#8b6500;"><strong>Configured duplicates detected:</strong> {esc(", ".join(duplicate_ids))}. Discovery updates the first matching ID.</p>'
        discovered_meters_html = f'''
        <h3>Discovered Meters</h3>
        <p>Use the action button to merge data directly into the <em>Meters</em> tab. Existing IDs are updated instead of duplicated.</p>
        {duplicate_notice}
        <table class="compact">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Driver</th>
                    <th>AES key</th>
                    <th>Telegrams</th>
                    <th>Last seen</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        <script>
            $(function() {{
                $('.add-discovered-meter-btn').off('click').on('click', function() {{
                    const meterData = {{
                        id: String($(this).data('meter-id') || '').trim(),
                        driver: String($(this).data('meter-driver') || '').trim(),
                        key: String($(this).data('meter-key') || '').trim(),
                        name: String($(this).data('meter-name') || '').trim(),
                        label: String($(this).data('meter-label') || '').trim(),
                        enabled: true
                    }};
                    if (typeof window.wmbusDiscoveryMerge !== 'function') {{
                        alert('Meters tab helper is not available.');
                        return;
                    }}
                    const result = window.wmbusDiscoveryMerge(meterData);
                    $('input[name="tab"]').val('meters');
                    if (result && result.index !== undefined) {{
                        const $target = $(`#meter-container .meter-card[data-meter-index="${{result.index}}"]`);
                        if ($target.length) $('html, body').animate({{ scrollTop: $target.offset().top - 80 }}, 250);
                    }}
                }});
            }});
        </script>
        '''

    return f'''
    <div class="grid two">
      <div class="card">
        <h2>Discovery</h2>
        <p>Discovery does not need configured meter IDs. Run it with the SDR attached, then merge discovered meters directly into your configuration.</p>
        <div class="button-row">
          <button name="action" value="discover">Run discovery</button>
          <button name="action" value="clear_discovery_log" class="secondary">Clear discovery log</button>
        </div>
        <pre>{esc(discovery_log or 'No discovery log yet.')}</pre>
        {discovered_meters_html}
      </div>
      <div class="card">
        <h2>Bridge log</h2>
        <div class="button-row">
          <button name="action" value="status" class="secondary">Refresh status</button>
          <button name="action" value="restart">Restart bridge</button>
        </div>
        <pre>{esc(bridge_log or 'No bridge log yet.')}</pre>
      </div>
    </div>

    {deps_html}

    <div class="card">
      <h2>Dependency installer log</h2>
      <pre>{esc(deps_log or 'No dependency installer log yet.')}</pre>
    </div>
    '''


def main():
    try:
        form = get_form()
        cfg = load_config()
        active_tab = os.environ.get('LB_ACTIVE_TAB', 'overview')

        message = ''
        if form.get('action'):
            try:
                message, cfg = handle_action(form, cfg)
            except ValueError as e:
                message = str(e)
            active_tab = form.get('tab', active_tab)

        sections = {
            'overview': render_overview(cfg),
            'mqtt': render_mqtt(cfg),
            'radio': render_radio(cfg),
            'meters': render_meters(cfg),
            'discovery': render_discovery(cfg),
        }
        body = ''.join(
            f'<div class="section" id="section-{key}" style="display: {"block" if key == active_tab else "none"};">{section_html}</div>'
            for key, section_html in sections.items()
        )

        display_style = 'block' if message else 'none'
        notice_html = f'<div class="ui-corner-all ui-shadow ui-bar-a" style="display: {display_style}; margin-bottom: 1em;"><p>{esc(message)}</p></div>' if message else ''

        print(f'''
{notice_html}
<form method="post" action="{os.environ.get('SCRIPT_NAME', 'index.cgi')}">
    <input type="hidden" name="tab" value="{esc(active_tab)}">
    <h1>wM-Bus Heat Meter Bridge</h1>
    {body}
    <p class="footerhint">Broker settings come from the LoxBerry MQTT widget. Configure only the topic prefix here. Discovery works without meter IDs; the bridge start requires IDs for enabled meters.</p>
</form>''')

    except Exception as e:
        log_error(f'Fatal UI error: {e}')
        print(f'UI error: {e}')


if __name__ == '__main__':
    main()
