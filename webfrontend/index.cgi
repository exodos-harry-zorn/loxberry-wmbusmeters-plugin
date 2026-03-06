#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import html
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs

PLUGIN_FOLDER = "loxberry-wmbusmeters"
PLUGIN_NAME = "wmbusmetersbridge"
CONFIG_DIR = Path(os.environ.get('LBP_CONFIGDIR', f'/opt/loxberry/config/plugins/{PLUGIN_FOLDER}'))
BIN_DIR = Path(os.environ.get('LBP_BINDIR', f'/opt/loxberry/bin/plugins/{PLUGIN_FOLDER}'))
DAEMON_PATH = Path(f'/opt/loxberry/system/daemons/plugins/{PLUGIN_NAME}')
INSTALLER = BIN_DIR / 'install_deps.sh'
TMP_DIR = Path('/tmp/loxberry-wmbusmeters')
TMP_DIR.mkdir(parents=True, exist_ok=True)
ERROR_LOG = TMP_DIR / 'ui_error.log'
CONFIG_FILE = CONFIG_DIR / 'config.json'
EXAMPLE_FILE = CONFIG_DIR / 'config.example.json'
LOG_FILE = TMP_DIR / 'bridge.log'
DISCOVERY_LOG = TMP_DIR / 'discovery.log'
DEPS_LOG = TMP_DIR / 'deps_install.log'
PID_FILE = TMP_DIR / 'bridge.pid'

sys.path.insert(0, str(BIN_DIR))
from common import resolve_mqtt_settings, mqtt_widget_url, admin_home_url, plugin_overview_url  # type: ignore

TABS = [
    ("overview", "Overview"),
    ("mqtt", "MQTT"),
    ("radio", "Radio"),
    ("meters", "Meters"),
    ("discovery", "Discovery & Logs"),
]


def log_error(msg):
    try:
        with ERROR_LOG.open('a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except Exception:
        pass


def default_config():
    return {
        'mqtt': {'base_topic': 'wmbus/heat', 'retain': True, 'qos': 1, 'client_id': 'loxberry-wmbusmeters'},
        'radio': {'device': 'rtlwmbus', 'mode': 't1', 'ppm': 0, 'rtl_index': 0, 'reset_after': '23h', 'log_level': 'normal', 'log_telegrams': False, 'ignore_duplicates': True, 'discovery_seconds': 30},
        'meters': [
            {'name': 'wohnung1', 'label': 'Wohnung 1', 'id': '', 'driver': 'sharky', 'key': '', 'enabled': True},
            {'name': 'wohnung2', 'label': 'Wohnung 2', 'id': '', 'driver': 'sharky', 'key': '', 'enabled': True},
            {'name': 'wohnung3', 'label': 'Wohnung 3', 'id': '', 'driver': 'sharky', 'key': '', 'enabled': True},
        ]
    }


def load_config():
    for path in [CONFIG_FILE, EXAMPLE_FILE]:
        try:
            if path.exists():
                with path.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        cfg = default_config()
                        cfg['mqtt'].update(data.get('mqtt', {}))
                        cfg['radio'].update(data.get('radio', {}))
                        if isinstance(data.get('meters'), list):
                            cfg['meters'] = data['meters']
                        return cfg
        except Exception as e:
            log_error(f'load_config failed for {path}: {e}')
    return default_config()


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
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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
    }
    meters = []
    for i in range(3):
        meters.append({
            'name': form.get(f'meter_{i}_name', f'wohnung{i+1}').strip() or f'wohnung{i+1}',
            'label': form.get(f'meter_{i}_label', f'Wohnung {i+1}').strip() or f'Wohnung {i+1}',
            'id': form.get(f'meter_{i}_id', '').strip(),
            'driver': form.get(f'meter_{i}_driver', 'sharky').strip() or 'sharky',
            'key': form.get(f'meter_{i}_key', '').strip(),
            'enabled': bool_from_form(form, f'meter_{i}_enabled')
        })
    cfg['meters'] = meters
    save_config(cfg)


def handle_action(form, cfg):
    msg = ''
    action = form.get('action', '')
    if action == 'save':
        save_from_form(form, cfg)
        msg = 'Configuration saved.'
    elif action in ('start', 'stop', 'restart', 'status'):
        if action in ('start', 'restart'):
            save_from_form(form, cfg)
        if DAEMON_PATH.exists():
            result = sudo_shell([str(DAEMON_PATH), action])
            msg = f'{action}: rc={result.returncode}'
            if result.stdout:
                msg += ' | ' + result.stdout.strip().replace('\n', ' | ')
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
        else:
            msg = f'Daemon not found at {DAEMON_PATH}'
    elif action == 'install_deps':
        if INSTALLER.exists():
            result = sudo_shell([str(INSTALLER), '--background'])
            msg = f'install_deps: rc={result.returncode}'
            if result.stdout:
                msg += ' | ' + result.stdout.strip().replace('\n', ' | ')
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
        else:
            msg = f'Installer not found at {INSTALLER}'
    elif action == 'discover':
        save_from_form(form, cfg)
        discover = BIN_DIR / 'discover.py'
        if not command_exists('wmbusmeters'):
            msg = 'wmbusmeters is not installed. Click “Repair / update dependencies” first.'
        elif discover.exists():
            result = shell([
                sys.executable, str(discover), '--mode', cfg.get('radio', {}).get('mode', 't1'),
                '--seconds', str(cfg.get('radio', {}).get('discovery_seconds', 30)), '--logfile', str(DISCOVERY_LOG)
            ], timeout=max(180, cfg.get('radio', {}).get('discovery_seconds', 30) + 20))
            msg = f'discovery: rc={result.returncode}'
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
        else:
            msg = 'discover.py not found.'
    elif action == 'clear_discovery_log':
        DISCOVERY_LOG.write_text('', encoding='utf-8')
        msg = 'Discovery log cleared.'
    return msg, cfg


def tab_link(tab, active):
    cls = 'tab active' if active else 'tab'
    return f'<a class="{cls}" href="?tab={esc(tab)}">{esc(dict(TABS)[tab])}</a>'


def status_badge(text):
    lower = text.lower()
    cls = 'bad'
    if 'running' in lower or lower == 'ok' or lower == 'online':
        cls = 'good'
    elif 'idle' in lower or 'stopped' in lower or 'not running' in lower:
        cls = 'warn'
    return f'<span class="badge {cls}">{esc(text)}</span>'


def render_overview(cfg):
    mqtt_live = resolve_mqtt_settings(cfg)
    rows = []
    for k, ok in deps_status().items():
        rows.append(f'<tr><td>{esc(k)}</td><td class="status">{"OK" if ok else "missing"}</td></tr>')
    meter_count = sum(1 for m in cfg.get('meters', []) if m.get('enabled'))
    configured_count = sum(1 for m in cfg.get('meters', []) if m.get('enabled') and m.get('id'))
    return f'''
    <div class="grid two">
      <div class="card">
        <h2>Bridge</h2>
        <p><strong>Status:</strong> {status_badge(service_status())}</p>
        <p><strong>Dependency job:</strong> {status_badge(dependency_job_status())}</p>
        <p><strong>Meters enabled:</strong> {meter_count} &nbsp; <strong>Configured IDs:</strong> {configured_count}</p>
        <div class="button-row">
          <button name="action" value="start">Start</button>
          <button name="action" value="restart">Restart</button>
          <button name="action" value="stop" class="secondary">Stop</button>
          <button name="action" value="install_deps" class="secondary">Repair / update dependencies</button>
        </div>
      </div>
      <div class="card">
        <h2>MQTT widget integration</h2>
        <p>This plugin uses the LoxBerry MQTT widget for broker and credential settings. Only the topic prefix is stored in this plugin.</p>
        <table class="compact">
          <tr><th>Broker source</th><td>{esc(mqtt_live.get('source', 'unknown'))}</td></tr>
          <tr><th>Broker host</th><td>{esc(mqtt_live.get('host', '127.0.0.1'))}</td></tr>
          <tr><th>Broker port</th><td>{esc(mqtt_live.get('port', 1883))}</td></tr>
          <tr><th>Topic prefix</th><td>{esc(cfg.get('mqtt', {}).get('base_topic', 'wmbus/heat'))}</td></tr>
        </table>
        <div class="button-row linkrow">
          <a class="linkbtn" target="_blank" href="{esc(mqtt_widget_url())}">Open MQTT Widget</a>
          <a class="linkbtn" target="_blank" href="{esc(plugin_overview_url())}">Installed Plugins</a>
          <a class="linkbtn" target="_blank" href="{esc(admin_home_url())}">LoxBerry Home</a>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>Dependency status</h2>
      <table class="compact"><tr><th>Component</th><th>Status</th></tr>{''.join(rows)}</table>
    </div>
    '''


def render_mqtt(cfg):
    return f'''
    <div class="card narrow">
      <h2>MQTT</h2>
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
      <div class="button-row">
        <button name="action" value="save">Save</button>
        <a class="linkbtn" target="_blank" href="{esc(mqtt_widget_url())}">Open MQTT Widget</a>
      </div>
    </div>
    '''


def render_radio(cfg):
    radio = cfg['radio']
    return f'''
    <div class="card narrow">
      <h2>Radio / RTL-SDR</h2>
      <div class="grid two compactgrid">
        <label>Device
          <input type="text" name="radio_device" value="{esc(radio.get('device', 'rtlwmbus'))}">
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
      <div class="button-row">
        <button name="action" value="save">Save</button>
        <button name="action" value="discover" class="secondary">Run discovery</button>
      </div>
    </div>
    '''


def render_meters(cfg):
    cards = []
    for i, meter in enumerate(cfg.get('meters', [])):
        cards.append(f'''
        <div class="card">
          <h2>{esc(meter.get('label', f'Wohnung {i+1}'))}</h2>
          <div class="grid two compactgrid">
            <label>Name
              <input type="text" name="meter_{i}_name" value="{esc(meter.get('name', f'wohnung{i+1}'))}">
            </label>
            <label>Label
              <input type="text" name="meter_{i}_label" value="{esc(meter.get('label', f'Wohnung {i+1}'))}">
            </label>
            <label>Meter ID
              <input type="text" name="meter_{i}_id" value="{esc(meter.get('id', ''))}" placeholder="discover first, then paste ID here">
            </label>
            <label>Driver
              <input type="text" name="meter_{i}_driver" value="{esc(meter.get('driver', 'sharky'))}">
            </label>
            <label>AES key (optional)
              <input type="text" name="meter_{i}_key" value="{esc(meter.get('key', ''))}" placeholder="32 hex chars if meter is encrypted">
            </label>
            <label class="checkbox"><input type="checkbox" name="meter_{i}_enabled" {'checked' if meter.get('enabled', True) else ''}> Enabled</label>
          </div>
        </div>
        ''')
    return f'''<div class="hint">Run discovery first. Start requires IDs for every enabled meter.</div>{''.join(cards)}<div class="button-row"><button name="action" value="save">Save</button><button name="action" value="start">Save & Start</button></div>'''


def render_discovery(cfg):
    bridge_log = read_tail(LOG_FILE, 80)
    discovery_log = read_tail(DISCOVERY_LOG, 120)
    deps_log = read_tail(DEPS_LOG, 80)
    return f'''
    <div class="grid two">
      <div class="card">
        <h2>Discovery</h2>
        <p>Discovery does not need configured meter IDs. Use it with the SDR attached, then copy the discovered IDs into the meter cards.</p>
        <div class="button-row">
          <button name="action" value="discover">Run discovery</button>
          <button name="action" value="clear_discovery_log" class="secondary">Clear discovery log</button>
        </div>
        <pre>{esc(discovery_log or 'No discovery log yet.')}</pre>
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
    <div class="card">
      <h2>Dependency installer log</h2>
      <pre>{esc(deps_log or 'No dependency installer log yet.')}</pre>
    </div>
    '''


def main():
    try:
        form = get_form()
        cfg = load_config()
        active_tab = form.get('tab', 'overview') if form.get('tab', 'overview') in dict(TABS) else 'overview'
        message = ''
        if form.get('action'):
            message, cfg = handle_action(form, cfg)
            if form.get('tab') in dict(TABS):
                active_tab = form['tab']
        mqtt_live = resolve_mqtt_settings(cfg)

        sections = {
            'overview': render_overview(cfg),
            'mqtt': render_mqtt(cfg),
            'radio': render_radio(cfg),
            'meters': render_meters(cfg),
            'discovery': render_discovery(cfg),
        }
        body = ''.join(
            f'<div class="section {"active" if key == active_tab else "hidden"}" id="section-{key}">{html}</div>'
            for key, html in sections.items()
        )

        print('Content-Type: text/html; charset=utf-8\n')
        print(f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wM-Bus Heat Meter Bridge</title>
<style>
:root {{ --green:#73b11b; --dark:#303030; --light:#f5f5f5; --border:#d7d7d7; --blue:#2d6cdf; }}
body {{ font-family: Arial, Helvetica, sans-serif; margin:0; background:#efefef; color:#222; }}
.topbar {{ background:var(--green); color:#fff; padding:10px 16px; display:flex; justify-content:space-between; align-items:center; }}
.topbar a {{ color:#fff; text-decoration:none; margin-right:14px; font-weight:bold; }}
.subtitle {{ font-size:13px; opacity:0.95; }}
.tabbar {{ background:#353535; padding:0 16px; display:flex; gap:0; overflow:auto; }}
.tab {{ color:#fff; text-decoration:none; padding:14px 18px; display:inline-block; border-bottom:3px solid transparent; font-weight:bold; white-space:nowrap; }}
.tab.active {{ background:#444; border-bottom-color:var(--green); }}
.container {{ max-width:1400px; margin:0 auto; padding:18px; }}
.card {{ background:#fff; border:1px solid var(--border); border-radius:8px; padding:18px; margin-bottom:18px; box-shadow:0 1px 2px rgba(0,0,0,0.04); }}
.card.narrow {{ max-width:980px; }}
.grid.two {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.grid.two.compactgrid {{ gap:14px 18px; }}
label {{ display:block; font-weight:bold; margin-bottom:10px; }}
input[type=text], input[type=number], select {{ width:100%; padding:10px 12px; border:1px solid #bfc6ce; border-radius:6px; box-sizing:border-box; margin-top:6px; font-size:15px; }}
.checkbox {{ font-weight:normal; display:flex; align-items:center; gap:8px; margin-top:18px; }}
.checkbox input {{ margin:0; }}
.button-row {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
button, .linkbtn {{ background:var(--green); color:#fff; border:none; border-radius:6px; padding:10px 14px; font-weight:bold; cursor:pointer; text-decoration:none; display:inline-block; }}
button.secondary, .linkbtn.secondary {{ background:#666; }}
.linkrow .linkbtn {{ background:#2d6cdf; }}
pre {{ background:#10151a; color:#dbe7f3; padding:14px; border-radius:8px; overflow:auto; white-space:pre-wrap; word-break:break-word; max-height:420px; }}
table.compact {{ border-collapse:collapse; width:100%; }}
table.compact th, table.compact td {{ border:1px solid #d9d9d9; padding:9px 10px; text-align:left; }}
table.compact th {{ background:#fafafa; }}
.status {{ color:#127d00; font-weight:bold; }}
.badge {{ display:inline-block; padding:4px 10px; border-radius:999px; font-weight:bold; font-size:13px; }}
.badge.good {{ background:#e9f7e7; color:#127d00; }}
.badge.warn {{ background:#fff5d6; color:#8b6500; }}
.badge.bad {{ background:#fde9e9; color:#b00020; }}
.notice {{ background:#edf7ed; border:1px solid #b8d8b8; color:#234523; padding:12px 14px; border-radius:8px; margin-bottom:18px; }}
.hint {{ background:#f7f7f7; border:1px dashed #bdbdbd; padding:12px 14px; margin-bottom:18px; border-radius:8px; }}
.footerhint {{ color:#444; font-size:13px; margin-top:8px; }}
@media (max-width: 980px) {{ .grid.two {{ grid-template-columns:1fr; }} .container {{ padding:12px; }} }}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <a href="{esc(admin_home_url())}" target="_blank">LoxBerry Home</a>
    <a href="{esc(plugin_overview_url())}" target="_blank">Plugins</a>
    <a href="{esc(mqtt_widget_url())}" target="_blank">MQTT Widget</a>
  </div>
  <div class="subtitle">wM-Bus Heat Meter Bridge • MQTT source: {esc(mqtt_live.get('source', 'unknown'))}</div>
</div>
<div class="tabbar">{''.join(tab_link(tab, tab == active_tab) for tab, _ in TABS)}</div>
<div class="container">
  <form method="post" action="index.cgi">
    <input type="hidden" name="tab" value="{esc(active_tab)}">
    <h1>wM-Bus Heat Meter Bridge</h1>
    {f'<div class="notice">{esc(message)}</div>' if message else ''}
    {body}
    <p class="footerhint">Broker settings come from the LoxBerry MQTT widget. Configure only the topic prefix here. Discovery works without meter IDs; the bridge start requires IDs for enabled meters.</p>
  </form>
</div>
</body>
</html>''')
    except Exception as e:
        log_error(f'Fatal UI error: {e}')
        print('Content-Type: text/plain; charset=utf-8\n')
        print(f'UI error: {e}')


if __name__ == '__main__':
    main()
