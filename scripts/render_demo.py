"""Render public UI screenshots using synthetic data only (requires PyQt6)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication
from netswitch import core
from netswitch.ui.theme import apply_theme
from netswitch.ui.window import Window

DEVICES = [
    {'name': 'eth0', 'kind': 'Ethernet / virtual', 'connected': True, 'internet': 'online',
     'routes': {'-4': {'metric': 100}}, 'addresses': []},
    {'name': 'wlan0', 'kind': 'Wi-Fi', 'connected': True, 'internet': 'online',
     'routes': {'-4': {'metric': 600}}, 'addresses': []},
]
BROWSER = {'name': 'Firefox', 'icon': 'firefox', 'command': ['/usr/bin/firefox']}
PROFILE = {'interface': 'wlan0', 'fallback': 'eth0', 'automatic': True,
           'command': ['/usr/bin/firefox', '--new-instance'], 'app': BROWSER}
SESSION = {'id': 'demo-browser', 'pid': 60000, 'name': 'Firefox', 'interface': 'wlan0',
           'fallback': 'eth0', 'active': 'wlan0', 'automatic': True,
           'traffic': {'routed': {'bytes': 2097152, 'packets': 1000}, 'blocked': {'packets': 0}}}

def response(data):
    if data['action'] == 'status':
        return [SESSION]
    if data['action'] == 'health':
        return {'networks': {d['name']: {'internet': 'online'} for d in DEVICES}, 'defaults': {}}
    if data['action'] == 'automatic-status':
        return {'available': True, 'error': None, 'rules': []}
    raise RuntimeError('Demo rendering cannot mutate routing or launch apps')

def main():
    app = QApplication([])
    apply_theme(app)
    with tempfile.TemporaryDirectory() as directory, \
         patch.object(core, 'CONFIG', Path(directory) / 'profiles.json'), \
         patch('netswitch.ui.window.interfaces', return_value=DEVICES), \
         patch('netswitch.ui.window.installed_apps', return_value=[BROWSER]), \
         patch('netswitch.ui.window.request', side_effect=response), \
         patch('netswitch.ui.window.executable_path', side_effect=lambda path: path):
        core.update_profile('Firefox · Wi-Fi', PROFILE)
        window = Window()
        window.show()
        output = ROOT / 'docs'
        output.mkdir(exist_ok=True)
        def capture(name):
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            window.grab().save(str(output / name))
        capture('overview.png')
        window.set_app(BROWSER)
        window.network_buttons['wlan0'].click()
        window.fallback.setChecked(True)
        window.automatic.setChecked(True)
        capture('app-routing.png')
        window.timer.stop()
        window.close()

if __name__ == '__main__':
    main()
