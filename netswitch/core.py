import ipaddress
import json
import os
import re
import socket
import subprocess
import tempfile
import fcntl
from pathlib import Path

SOCKET = '/run/netswitch/control.sock'
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'netswitch/profiles.json'

def execute(args, *, data=None, check=True):
    result = subprocess.run(args, input=data, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f'{args[0]} failed')
    return result

def interfaces():
    addresses = json.loads(execute(['/usr/bin/ip', '-j', 'address']).stdout)
    routes = {}
    for family in ('-4', '-6'):
        routes[family] = json.loads(execute(['/usr/bin/ip', family, '-j', 'route', 'show', 'table', 'main']).stdout)
    result = []
    for item in addresses:
        name = item['ifname']
        if name == 'lo':
            continue
        info = {'name': name, 'kind': 'Wi-Fi' if Path(f'/sys/class/net/{name}/wireless').exists() else 'Ethernet / virtual',
                'up': 'UP' in item['flags'], 'addresses': item['addr_info'], 'routes': {}}
        for family, entries in routes.items():
            defaults = [r for r in entries if r.get('dst') == 'default' and r.get('dev') == name and 'linkdown' not in r.get('flags', [])]
            if defaults:
                info['routes'][family] = min(defaults, key=lambda r: r.get('metric', 0))
        info['connected'] = info['up'] and bool(info['routes'])
        result.append(info)
    return result

def validate_interface(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', name):
        raise ValueError('Invalid interface name')
    if name not in {i['name'] for i in interfaces()}:
        raise ValueError(f'Interface {name} does not exist')
    return name

def load_profiles():
    if not CONFIG.exists():
        return {}
    from .apps import restore_browser_profile
    return {name: restore_browser_profile(profile) for name, profile in json.loads(CONFIG.read_text()).items()}

def save_profiles(profiles):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=CONFIG.parent, prefix='.profiles-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(profiles, indent=2) + '\n')
    try:
        temporary.chmod(0o600)
        temporary.replace(CONFIG)
    finally:
        temporary.unlink(missing_ok=True)

def update_profile(name, profile):
    """Serialize profile changes from the GUI and multiple agent processes."""
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG.parent / 'profiles.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profiles = load_profiles()
        if profile is None:
            if name not in profiles:
                raise ValueError('Unknown profile')
            del profiles[name]
        else:
            profiles[name] = profile
        save_profiles(profiles)
        return profiles

def request(payload):
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(20)
            connection.connect(SOCKET)
            connection.sendall(json.dumps(payload).encode() + b'\n')
            response = connection.makefile('rb').readline(1024 * 1024)
    except (FileNotFoundError, ConnectionRefusedError):
        raise RuntimeError('Routing service is not installed/running. Run: netswitch install-system') from None
    answer = json.loads(response)
    if not answer.get('ok'):
        raise RuntimeError(answer.get('error', 'Service request failed'))
    return answer['result']

def nft_rules(identifier, mark, active):
    # Interface names and identifiers are validated/generated, never shell input.
    output = f'''table inet ns_{identifier} {{
 chain route {{
  type route hook output priority 300; policy accept;
  ip daddr 127.0.0.0/8 return
  ip6 daddr ::1 return
  socket cgroupv2 level 2 "netswitch/{identifier}" counter meta mark set {mark}
 }}
 chain guard {{
  type filter hook output priority 301; policy accept;
  meta mark {mark} oifname "lo" return
'''
    if active:
        # The OUTPUT hook's interface metadata may still reflect the initial
        # route; the marked routing table itself enforces the selected interface.
        output += f'  meta mark {mark} return\n'
    output += f'''  meta mark {mark} counter reject
 }}
 chain nat {{
  type nat hook postrouting priority srcnat; policy accept;
'''
    if active:
        output += f'  meta mark {mark} oifname "{active}" masquerade\n'
    return output + ' }\n}\n'

def route_commands(table, interface):
    commands = []
    for family in ('-4', '-6'):
        # A terminal route prevents accidental fallback to another ISP.
        commands.append(['/usr/bin/ip', family, 'route', 'replace', 'unreachable', 'default', 'metric', '32767', 'table', str(table)])
        route = interface['routes'].get(family) if interface else None
        if route:
            args = ['/usr/bin/ip', family, 'route', 'replace', 'default']
            if route.get('gateway'):
                args += ['via', str(ipaddress.ip_address(route['gateway']))]
            args += ['dev', interface['name'], 'onlink', 'metric', '100', 'table', str(table)]
            commands.append(args)
    return commands
