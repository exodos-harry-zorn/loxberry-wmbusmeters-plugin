#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LoxBerry Plugin wM-Bus Heat Meter Bridge - Python Content Provider
#
# This script is responsible for rendering the plugin's main content (forms, tables, logs).
# It is called by a Perl wrapper script which handles the LoxBerry UI scaffolding
# (header, navigation, footer, CSS, JS).

import html
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs

PLUGIN_FOLDER = "loxberry-wmbusmeters"
PLUGIN_NAME = "wmbusmetersbridge" # This must match the NAME in plugin.cfg

# LoxBerry environment variables are generally available
CONFIG_DIR = Path(os.environ.get('LBP_CONFIGDIR', f'/opt/loxberry/config/plugins/{PLUGIN_FOLDER}'))
BIN_DIR = Path(os.environ.get('LBP_BINDIR', f'/opt/loxberry/bin/plugins/{PLUGIN_FOLDER}'))
DAEMON_PATH = Path(f'/opt/loxberry/system/daemons/plugins/{PLUGIN_NAME}')
INSTALLER = BIN_DIR / 'install_deps.sh'
TMP_DIR = Path(os.environ.get('LBPTMPDIR', f'/tmp/loxberry-wmbusmeters')) # Use LBPTMPDIR if available
TMP_DIR.mkdir(parents=True, exist_ok=True)
ERROR_LOG = TMP_DIR / 'ui_error.log'
CONFIG_FILE = CONFIG_DIR / 'config.json'
EXAMPLE_FILE = CONFIG_DIR / 'config.example.json'
LOG_FILE = TMP_DIR / 'bridge.log'
DISCOVERY_LOG = TMP_DIR / 'discovery.log'
DEPS_LOG = TMP_DIR / 'deps_install.log'
PID_FILE = TMP_DIR / 'bridge.pid'

# Ensure common.py is importable. Try standard LBPBINDIR, fallback to relative.
if 'LBPBINDIR' in os.environ:
    BIN_DIR_FOR_IMPORT = Path(os.environ['LBPBINDIR'])
elif 'LBP_BINDIR' in os.environ:
    BIN_DIR_FOR_IMPORT = Path(os.environ['LBP_BINDIR'])
else:
    BIN_DIR_FOR_IMPORT = BIN_DIR

sys.path.insert(0, str(BIN_DIR_FOR_IMPORT))
from common import resolve_mqtt_settings, mqtt_widget_url, admin_home_url, plugin_overview_url, HEALTHCHECK_FILE # type: ignore

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
        'meters': [] # Start with an empty list for dynamic meters
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
        'meter_whitelist': [x.strip() for x in form.get('radio_meter_whitelist', '').split(',') if x.strip()],
        'meter_blacklist': [x.strip() for x in form.get('radio_meter_blacklist', '').split(',') if x.strip()],
    }

    # Dynamically read meters from form data
    new_meters = []
    
    # Collect all submitted meter indices
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
    
    for i in meter_indices:
        meter_name_key = f'meter_{i}_name'
        meter = {
            'name': form.get(meter_name_key, '').strip() or f'meter_{i+1}',
            'label': form.get(f'meter_{i}_label', '').strip() or f'Meter {i+1}',
            'id': form.get(f'meter_{i}_id', '').strip(),
            'driver': form.get(f'meter_{i}_driver', '').strip() or 'sharky',
            'key': form.get(f'meter_{i}_key', '').strip(),
            'enabled': bool_from_form(form, f'meter_{i}_enabled')
        }
        # Add if at least name or ID is present, or if it's explicitly enabled
        if meter['name'] or meter['id'] or meter['enabled']:
            new_meters.append(meter)
    
    cfg['meters'] = new_meters
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
    elif action == 'run_healthcheck':
        healthcheck_script = BIN_DIR / 'healthcheck.py'
        if healthcheck_script.exists():
            result = shell([sys.executable, str(healthcheck_script)]) # Run in foreground for immediate feedback
            msg = f'Health check: rc={result.returncode}'
            if result.stdout:
                msg += ' | ' + result.stdout.strip().replace('\n', ' | ')
            if result.stderr:
                msg += ' | ' + result.stderr.strip().replace('\n', ' | ')
        else:
            msg = 'healthcheck.py not found.'
    return msg, cfg

def status_badge(text):
    lower = text.lower()
    cls = 'bad'
    if 'running' in lower or lower == 'ok' or lower == 'online':
        cls = 'good'
    elif 'idle' in lower or 'stopped' in lower or 'not running' in lower:
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
            if line.startswith('{') and line.endswith('}'):
                try:
                    data = json.loads(line)
                    meter_id = str(data.get('id'))
                    if meter_id and data.get('meter_type'):
                        meters[meter_id] = {
                            'id': meter_id,
                            'driver': data.get('meter_type'),
                            'key': data.get('key', '') # wmbusmeters might provide this
                        }
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        log_error(f'parse_discovery_log failed for {log_path}: {e}')
    return list(meters.values())

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
        # First try lsusb to find generic Realtek RTL2838/RTL2832 DVB-T dongles
        res = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        dongles = []
        for line in res.stdout.splitlines():
            line_lower = line.lower()
            if 'rtl2838' in line_lower or 'rtl2832' in line_lower or 'dvb-t' in line_lower or 'realtek' in line_lower:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    dongles.append(parts[2].strip())
                else:
                    dongles.append(line.strip())
        
        # Try to enrich with rtl_test to get serial numbers if available
        if command_exists('rtl_test'):
            res_rtl = subprocess.run(['rtl_test', '-t'], capture_output=True, text=True, timeout=5)
            # rtl_test typically outputs to stderr
            rtl_output = res_rtl.stderr + res_rtl.stdout
            rtl_dongles = []
            for line in rtl_output.splitlines():
                if "Realtek" in line and "SN:" in line:
                    rtl_dongles.append(line.strip())
            if rtl_dongles:
                # If rtl_test gave good detailed output, prefer that
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
    return f'''
    <div class="card narrow">
      <h2>Radio / RTL-SDR Setup</h2>
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

def render_meter_card(meter_data, index):
    meter = meter_data
    # Use index to generate unique names for dynamically added fields.
    # Handle the case where index is a placeholder string "METER_INDEX_PLACEHOLDER"
    try:
        idx_num = int(index)
        display_idx = idx_num + 1
    except ValueError:
        display_idx = "${nextMeterIndex + 1}"
        
    return f'''
    <div class="card meter-card" data-meter-index="{index}">
        <h2>
            <span class="meter-label">{esc(meter.get('label', f'Meter {display_idx}'))}</span>
            <button type="button" class="ui-btn ui-mini ui-btn-inline ui-btn-icon-notext ui-icon-delete ui-btn-a remove-meter-btn" title="Remove Meter" style="float: right;">Remove</button>
        </h2>
        <div class="grid two compactgrid">
            <label>Name
                <input type="text" name="meter_{index}_name" value="{esc(meter.get('name', f'meter_{display_idx}'))}">
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
            <label>AES key (optional)
                <input type="text" name="meter_{index}_key" value="{esc(meter.get('key', ''))}" placeholder="32 hex chars if meter is encrypted">
            </label>
            <label class="checkbox"><input type="checkbox" name="meter_{index}_enabled" {'checked' if meter.get('enabled', True) else ''}> Enabled</label>
        </div>
    </div>
    '''

def render_meters(cfg):
    cards = []
    for i, meter in enumerate(cfg.get('meters', [])):
        cards.append(render_meter_card(meter, i))

    # Generate a template for new meters in Python, allowing JS to replace the placeholder
    template_html = render_meter_card({}, "METER_INDEX_PLACEHOLDER")

    return f'''
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

        function addMeter(meterData = {{}}) {{
            let newMeterHtml = `{template_html}`;
            // Simple replacement for index placeholder
            newMeterHtml = newMeterHtml.replace(/METER_INDEX_PLACEHOLDER/g, nextMeterIndex);
            
            // If data is provided (from discovery), we can pre-fill it via jQuery after insertion
            const $newCard = $(newMeterHtml);
            
            if (meterData.id) {{
                $newCard.find('input[name="meter_' + nextMeterIndex + '_id"]').val(meterData.id);
            }}
            if (meterData.driver) {{
                $newCard.find('input[name="meter_' + nextMeterIndex + '_driver"]').val(meterData.driver);
            }}
            if (meterData.key) {{
                $newCard.find('input[name="meter_' + nextMeterIndex + '_key"]').val(meterData.key);
            }}

            $('#meter-container').append($newCard);
            // Re-enhance new jQuery Mobile elements
            $(`#meter-container .meter-card[data-meter-index="${{nextMeterIndex}}"]`).enhanceWithin();
            
            nextMeterIndex++;
            // Re-bind remove event for new button
            $('.remove-meter-btn').off('click').on('click', function() {{
                $(this).closest('.meter-card').remove();
            }});
        }}

        $(function() {{
            // Initial binding for remove buttons (for already rendered meters)
            $('.remove-meter-btn').on('click', function() {{
                $(this).closest('.meter-card').remove();
            }});

            // Bind add button
            $('#add-meter-btn').on('click', function() {{
                addMeter();
            }});
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
    discovered_meters_html = ''
    if discovered_meters:
        rows = []
        for i, meter in enumerate(discovered_meters):
            rows.append(f'''
            <tr>
                <td>{esc(meter['id'])}</td>
                <td>{esc(meter['driver'])}</td>
                <td>{esc(meter['key'] or '-')}</td>
                <td><button type="button" class="ui-btn ui-mini ui-btn-inline ui-corner-all ui-shadow add-discovered-meter-btn"
                            data-meter-id="{esc(meter['id'])}"
                            data-meter-driver="{esc(meter['driver'])}"
                            data-meter-key="{esc(meter['key'])}">Add</button></td>
            </tr>
            ''')
        discovered_meters_html = f'''
        <h3>Discovered Meters</h3>
        <table class="compact">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Driver</th>
                    <th>Key</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        <script>
            $(function() {{
                $('.add-discovered-meter-btn').on('click', function(event) {{
                    const meterId = $(this).data('meter-id');
                    const meterDriver = $(this).data('meter-driver');
                    const meterKey = $(this).data('meter-key');
                    
                    // Create a dummy form object to pass to addMeter
                    const newMeterData = {{
                        'id': meterId,
                        'driver': meterDriver,
                        'key': meterKey,
                        'name': `meter_${{nextMeterIndex + 1}}`, // Generate a default name
                        'label': `Meter ${{nextMeterIndex + 1}}`, // Generate a default label
                        'enabled': true
                    }};
                    // Call the addMeter function from the meters tab logic
                    addMeter(newMeterData);
                    
                    // Switch to the meters tab after adding
                    $('input[name="tab"]').val('meters');
                    $('form').submit();
                }});
            }});
        </script>
        '''

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
            message, cfg = handle_action(form, cfg)
            active_tab = form.get('tab', active_tab) # Re-read active tab in case action changed it

        sections = {
            'overview': render_overview(cfg),
            'mqtt': render_mqtt(cfg),
            'radio': render_radio(cfg),
            'meters': render_meters(cfg),
            'discovery': render_discovery(cfg),
        }
        body = ''.join(
            f'<div class="section" id="section-{key}" style="display: {"block" if key == active_tab else "none"};">{html}</div>'
            for key, html in sections.items()
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
