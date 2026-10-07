"""Optional system-wide failover. Main routes and VPN rules remain untouched."""
import json
from pathlib import Path
from .core import execute, route_commands

CONFIG = Path('/etc/netswitch/default.json')
STATE = Path('/run/netswitch/default-routing.json')
TABLE = 41999

class DefaultRouting:
    def __init__(self):
        self.owned = json.loads(STATE.read_text()) if STATE.exists() else None
        self.fingerprint = None
        self.active = self.owned.get('active') if self.owned else None
        self.error = None

    def configuration(self):
        return json.loads(CONFIG.read_text()) if CONFIG.exists() else None

    def save(self):
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.owned))
        temporary.chmod(0o600)
        temporary.replace(STATE)

    def cleanup(self):
        if not self.owned:
            return
        for family in ('-4', '-6'):
            execute(['/usr/bin/ip', family, 'rule', 'del', 'priority', '90', 'lookup', 'main', 'suppress_prefixlength', '0'], check=False)
            execute(['/usr/bin/ip', family, 'rule', 'del', 'priority', '91', 'lookup', str(TABLE)], check=False)
            for name in self.owned['interfaces']:
                execute(['/usr/bin/ip', family, 'rule', 'del', 'priority', '85', 'oif', name, 'lookup', 'main'], check=False)
            execute(['/usr/bin/ip', family, 'route', 'flush', 'table', str(TABLE)], check=False)
        self.owned, self.active, self.fingerprint = None, None, None
        STATE.unlink(missing_ok=True)

    def update(self, devices, monitor):
        config = self.configuration()
        if not config or not config.get('enabled'):
            self.cleanup()
            return
        names = [config['preferred'], config['fallback']]
        if self.owned and self.owned['interfaces'] != names:
            self.cleanup()
        selected = monitor.choose(devices, *names, current=self.active)
        # If every ISP fails, retain the last/default route rather than making
        # endpoint-specific failures cause a machine-wide black hole.
        if selected is None:
            selected = devices.get(self.active) or next((devices[n] for n in names if n in devices and devices[n]['connected']), None)
        if selected is None:
            return
        fingerprint = json.dumps({'names': names, 'routes': selected['routes'], 'active': selected['name']}, sort_keys=True)
        if fingerprint == self.fingerprint:
            return
        if not self.owned:
            for family in ('-4', '-6'):
                rules = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'rule']).stdout)
                routes = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'route', 'show', 'table', 'all']).stdout)
                if any(r.get('priority') in (85, 90, 91) for r in rules) or any(str(r.get('table')) == str(TABLE) for r in rules + routes):
                    raise RuntimeError('System failover conflicts with existing routing rules (85/90/91) or table 41999')
            self.owned = {'interfaces': names, 'active': None}
            self.save()  # Ownership is recoverable even if the daemon stops mid-install.
        # Replace the new route before removing an old gateway. Metrics distinguish
        # alternating slots, so a usable route exists throughout the update.
        for family in ('-4', '-6'):
            route = selected['routes'].get(family)
            old = json.loads(execute(['/usr/bin/ip', family, '-j', 'route', 'show', 'table', str(TABLE)], check=False).stdout or '[]')
            slot = 100 if not old or old[0].get('metric') != 100 else 101
            if route:
                for command in route_commands(TABLE, selected):
                    if command[1] == family and 'unreachable' not in command:
                        command[command.index('metric') + 1] = str(slot)
                        execute(command)
            else:
                execute(['/usr/bin/ip', family, 'route', 'replace', 'unreachable', 'default', 'metric', str(slot), 'table', str(TABLE)])
            for entry in old:
                if entry.get('dst') == 'default' and entry.get('metric') != slot:
                    execute(['/usr/bin/ip', family, 'route', 'del', 'default', 'metric', str(entry.get('metric', 0)), 'table', str(TABLE)], check=False)
            # Idempotently add only our precise rules, preserving everything else.
            rules = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'rule']).stdout)
            for name in names:
                if not any(r.get('priority') == 85 and r.get('oif') == name and str(r.get('table')) == '254' for r in rules):
                    execute(['/usr/bin/ip', family, 'rule', 'add', 'priority', '85', 'oif', name, 'lookup', 'main'])
            if not any(r.get('priority') == 90 for r in rules):
                execute(['/usr/bin/ip', family, 'rule', 'add', 'priority', '90', 'lookup', 'main', 'suppress_prefixlength', '0'])
            if not any(r.get('priority') == 91 for r in rules):
                execute(['/usr/bin/ip', family, 'rule', 'add', 'priority', '91', 'lookup', str(TABLE)])
        self.active = selected['name']
        self.owned['active'] = self.active
        self.fingerprint, self.error = fingerprint, None
        self.save()

    def snapshot(self):
        return {'configuration': self.configuration(), 'active': self.active, 'error': self.error}
