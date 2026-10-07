"""Root-only isolated routing verification; never disconnects an adapter.

Run with pkexec /usr/bin/python -I /absolute/path/tests/integration_failover.py.
Only this test's cgroup/table/firewall rules are changed. ISP outage results
are simulated; HTTPS requests and kernel routing changes are real.
"""
import json
import os
import pwd
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from netswitch.core import execute, interfaces
from netswitch.daemon import ROOT, Service
from netswitch.health import Health, HealthMonitor

def default_routing_namespace():
    """Verify system-wide rules in a disposable network namespace."""
    import netswitch.defaults as defaults
    for name, address, gateway, metric in [('testlan0', '192.0.2.2/24', '192.0.2.1', '100'),
                                            ('testwifi0', '198.51.100.2/24', '198.51.100.1', '600')]:
        execute(['/usr/bin/ip', 'link', 'add', name, 'type', 'dummy'])
        execute(['/usr/bin/ip', 'address', 'add', address, 'dev', name])
        execute(['/usr/bin/ip', 'link', 'set', name, 'up'])
        execute(['/usr/bin/ip', 'route', 'add', 'default', 'via', gateway, 'dev', name, 'metric', metric])
    devices = {d['name']: d for d in interfaces()}
    monitor = HealthMonitor.__new__(HealthMonitor)
    monitor.records = {'testlan0': Health(online=True), 'testwifi0': Health(online=True)}
    def route(destination='203.0.113.10', extra=None):
        return json.loads(execute(['/usr/bin/ip', '-j', 'route', 'get', destination, *(extra or [])]).stdout)[0]['dev']
    with tempfile.TemporaryDirectory() as folder:
        defaults.CONFIG = Path(folder) / 'default.json'
        defaults.STATE = Path(folder) / 'state.json'
        defaults.CONFIG.write_text(json.dumps({'enabled': True, 'preferred': 'testlan0', 'fallback': 'testwifi0'}))
        controller = defaults.DefaultRouting()
        try:
            controller.update(devices, monitor)
            assert route() == 'testlan0'
            monitor.records['testlan0'].online = False
            controller.update(devices, monitor)
            assert route() == 'testwifi0'
            assert route('192.0.2.10') == 'testlan0', 'Local LAN route was lost'
            assert route(extra=['oif', 'testlan0']) == 'testlan0', 'Internet checks would use the wrong ISP'
            monitor.records['testlan0'].record(True, 10)
            controller.update(devices, monitor)
            assert route() == 'testwifi0'
            monitor.records['testlan0'].record(True, 15)
            controller.update(devices, monitor)
            assert route() == 'testlan0'
            for family in ('-4', '-6'):
                rules = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'rule']).stdout)
                assert len([r for r in rules if r.get('priority') == 85]) == 2, 'Duplicate interface rules'
        finally:
            controller.cleanup()
        assert route() == 'testlan0'
        for family in ('-4', '-6'):
            rules = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'rule']).stdout)
            assert not any(r.get('priority') in (85, 90, 91) for r in rules), 'Failover rules were not cleaned up'
        print(json.dumps({'system_failover': 'passed', 'local_LAN_access': 'preserved', 'bound_probes': 'isolated', 'host_network_changed': False}))

def main():
    if os.geteuid() != 0:
        raise SystemExit('This isolated kernel-routing test requires root.')
    if '--default-routing' in sys.argv:
        result = execute(['/usr/bin/unshare', '--net', '/usr/bin/python', '-I', str(Path(__file__).resolve()), '--default-routing-child'])
        print(result.stdout.strip())
        return
    if '--default-routing-child' in sys.argv:
        default_routing_namespace()
        return
    uid = int(os.environ.get('PKEXEC_UID', '0'))
    if not uid:
        raise SystemExit('Run through pkexec from your normal user account.')
    user = pwd.getpwuid(uid)
    devices = {d['name']: d for d in interfaces()}
    preferred = next((d['name'] for d in devices.values() if d['connected'] and d['name'].startswith(('en', 'eth'))), None)
    fallback = next((d['name'] for d in devices.values() if d['connected'] and d['kind'] == 'Wi-Fi'), None)
    assert preferred and fallback, 'Two connected Ethernet and Wi-Fi adapters are required'
    identifier = f'verify{os.getpid()}'
    table = 45000
    for family in ('-4', '-6'):
        routes = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'route', 'show', 'table', 'all']).stdout)
        rules = json.loads(execute(['/usr/bin/ip', family, '-N', '-j', 'rule']).stdout)
        assert not any(str(item.get('table')) == str(table) for item in routes + rules), 'Test table is already in use'
    group = ROOT / identifier
    group.mkdir()
    session = {'id': identifier, 'table': table, 'mark': 0x4e550001, 'interface': preferred, 'fallback': fallback, 'active': None, 'fingerprint': None}
    service = Service.__new__(Service)
    service.sessions = {identifier: session}
    service.reverse = {}
    service.persist = lambda: None
    service.health = HealthMonitor.__new__(HealthMonitor)
    service.health.records = {preferred: Health(online=True), fallback: Health(online=True)}
    def fetch():
        read_fd, write_fd = os.pipe()
        gate_read, gate_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd); os.close(gate_write)
            if os.read(gate_read, 1) != b'1':
                os._exit(125)
            os.close(gate_read)
            os.dup2(write_fd, 1); os.close(write_fd)
            os.setgroups([]); os.setgid(user.pw_gid); os.setuid(uid)
            os.execv('/usr/bin/curl', ['curl', '--disable', '--noproxy', '*', '--max-time', '10', '--silent', '--show-error', 'https://api.ipify.org'])
        os.close(write_fd); os.close(gate_read)
        try:
            (group / 'cgroup.procs').write_text(str(pid))
            os.write(gate_write, b'1')
        finally:
            os.close(gate_write)
        with os.fdopen(read_fd) as stream:
            address = stream.read().strip()
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0 and address
        return address
    try:
        for family in ('-4', '-6'):
            execute(['/usr/bin/ip', family, 'rule', 'add', 'priority', '30', 'fwmark', str(session['mark']), 'lookup', str(table)])
        service.update(session)
        lan = fetch()
        record = service.health.records[preferred]
        record.record(False, 0); record.record(False, 5)
        service.update(session)
        assert session['active'] == fallback
        wifi = fetch()
        assert lan != wifi
        record.record(True, 10)
        service.update(session)
        assert session['active'] == fallback, 'Recovered too early'
        record.record(True, 15)
        service.update(session)
        assert session['active'] == preferred
        assert fetch() == lan
        print(json.dumps({'outage_failover': 'passed', 'stable_recovery': 'passed', 'different_public_ips': True, 'interfaces_disconnected': False}))
    finally:
        service.remove(session)
        for interface, original in service.reverse.items():
            Path(f'/proc/sys/net/ipv4/conf/{interface}/rp_filter').write_text(original)

if __name__ == '__main__':
    main()
