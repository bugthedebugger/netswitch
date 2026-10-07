"""Bound-interface HTTPS checks with hysteresis, outside the control-server thread."""
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

INTERVAL = 5
THRESHOLD = 2

def probe(interface, family='-4'):
    targets = [('https://1.1.1.1/cdn-cgi/trace', 'trace'),
               ('https://8.8.8.8/resolve?name=example.com&type=A', 'dns')]
    if family == '-6':
        targets = [('https://[2606:4700:4700::1111]/cdn-cgi/trace', 'trace'),
                   ('https://[2001:4860:4860::8888]/resolve?name=example.com&type=A', 'dns')]
    for url, kind in targets:
        try:
            result = subprocess.run(['/usr/bin/curl', '--disable', family, '--interface', f'if!{interface}',
                                     '--noproxy', '*', '--connect-timeout', '2', '--max-time', '3',
                                     '--max-filesize', '32768', '--silent', '--fail', url],
                                    capture_output=True, text=True, timeout=4)
            if result.returncode == 0:
                if kind == 'trace' and '\nip=' in result.stdout:
                    return True
                if kind == 'dns' and json.loads(result.stdout).get('Status') == 0:
                    return True
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return False

@dataclass
class Health:
    online: bool | None = None
    failures: int = 0
    successes: int = 0
    checked_at: float | None = None
    next_check: float = 0

    def record(self, success, now):
        self.checked_at = time.time()
        self.next_check = now + INTERVAL
        if success:
            self.successes += 1
            self.failures = 0
            if self.online is None or self.successes >= THRESHOLD:
                self.online = True
        else:
            self.failures += 1
            self.successes = 0
            if self.failures >= THRESHOLD:
                self.online = False

class HealthMonitor:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='internet-check')
        self.records, self.pending, self.signatures = {}, {}, {}

    def refresh(self, devices):
        now = time.monotonic()
        for device in devices:
            name = device['name']
            signature = json.dumps({'routes': device['routes'], 'connected': device['connected'],
                                    'addresses': [(a['family'], a['local'], a['prefixlen']) for a in device.get('addresses', [])]}, sort_keys=True)
            if self.signatures.get(name) != signature:
                self.signatures[name] = signature
                self.records[name] = Health()
            record = self.records[name]
            future = self.pending.get(name)
            if future and future[0].done():
                del self.pending[name]
                if future[1] == signature:
                    try:
                        success = future[0].result()
                    except Exception:
                        success = False
                    record.record(success, now)
            if not device['connected']:
                record.online = False
                continue
            if name not in self.pending and now >= record.next_check:
                family = '-4' if '-4' in device['routes'] else '-6'
                self.pending[name] = (self.pool.submit(probe, name, family), signature)

    def choose(self, devices, preferred, fallback, current=None):
        if fallback and current == fallback and self.records.get(preferred, Health()).online is None:
            device = devices.get(current)
            if device and device['connected'] and self.records.get(current, Health()).online is not False:
                return device  # Keep recovered sessions stable while fresh checks run.
        for name in (preferred, fallback):
            device = devices.get(name)
            if device and device['connected']:
                # A strictly pinned app keeps its chosen ISP. Health drives
                # switching only when the user has explicitly chosen a backup.
                if not fallback or self.records.get(name, Health()).online is not False:
                    return device
        return None

    def snapshot(self, devices):
        result = {}
        for device in devices:
            record = self.records.get(device['name'], Health())
            internet = 'disconnected' if not device['connected'] else 'online' if record.online is True else 'offline' if record.online is False else 'checking'
            result[device['name']] = {'internet': internet, 'checked_at': record.checked_at,
                                      'failures': record.failures, 'successes': record.successes}
        return result

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
