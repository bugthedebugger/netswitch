"""Root-owned service. All launch commands execute in the unprivileged client."""
import fcntl
import json
import os
import select
import signal
import socket
import struct
import time
from pathlib import Path
from .core import SOCKET, execute, interfaces, nft_rules, route_commands, validate_interface
from .health import HealthMonitor
from .defaults import DefaultRouting
from .automatic import AutomaticRules

ROOT = Path('/sys/fs/cgroup/netswitch')
STATE = Path('/run/netswitch/sessions.json')

class Service:
    def __init__(self):
        self.sessions = {}
        self.reverse = {}
        self.health = HealthMonitor()
        self.defaults = DefaultRouting()
        self.health.refresh(interfaces())
        ROOT.mkdir(exist_ok=True)
        if STATE.exists():
            # Recover ownership records after a service restart; keep live apps routed.
            state = json.loads(STATE.read_text())
            self.reverse = state.get('reverse', {})
            for session in state.get('sessions', []):
                self.sessions[session['id']] = session
            for session in list(self.sessions.values()):
                group = ROOT / session['id']
                if not group.exists() or 'populated 0' in (group / 'cgroup.events').read_text():
                    self.remove(session)
                else:
                    self.update(session, force=True)
        self.automatic = AutomaticRules(self.assign_automatic)

    def persist(self):
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps({'sessions': list(self.sessions.values()), 'reverse': self.reverse}))
        temporary.chmod(0o600)
        temporary.replace(STATE)

    def create(self, uid, peer_pid, data):
        preferred = validate_interface(data['interface'])
        fallback = data.get('fallback')
        if fallback:
            fallback = validate_interface(fallback)
        pid = int(data['pid'])
        status = Path(f'/proc/{pid}/status').read_text()
        fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
        if int(fields['PPid']) != peer_pid or any(int(x) != uid for x in fields['Uid'].split()):
            raise ValueError('Only a newly launched child of the requesting process may be assigned')
        if not 0 < uid:
            raise ValueError('Launch applications as your regular user')
        return self.assign(uid, pid, {'interface': preferred, 'fallback': fallback})

    def assign_automatic(self, uid, pid, rule):
        # This private entry is invoked only by a blocked kernel exec event.
        # Socket clients cannot request assignment of arbitrary existing PIDs.
        return self.assign(uid, pid, rule, automatic=True)

    def assign(self, uid, pid, data, automatic=False):
        preferred, fallback = data['interface'], data.get('fallback')
        if sum(s['uid'] == uid for s in self.sessions.values()) >= 64:
            raise ValueError('Session limit reached (64 apps per user)')
        available = {i['name']: i for i in interfaces()}
        if not automatic and not any(available[n]['connected'] for n in (preferred, fallback) if n):
            raise ValueError('Selected interfaces have no usable default route')
        used = {s['table'] for s in self.sessions.values()}
        external = set()
        for family in ('-4', '-6'):
            external.update(int(r['table']) for r in json.loads(execute(['/usr/bin/ip', family, '-j', 'rule']).stdout) if str(r.get('table', '')).isdigit())
            external.update(int(r['table']) for r in json.loads(execute(['/usr/bin/ip', family, '-j', 'route', 'show', 'table', 'all']).stdout) if str(r.get('table', '')).isdigit())
        table = next((n for n in range(42000, 43000) if n not in used | external), None)
        if table is None:
            raise RuntimeError('No free routing tables')
        identifier = f's{table}'
        group = ROOT / identifier
        group.mkdir()
        session = {'id': identifier, 'table': table, 'mark': 0x4E530000 + table - 42000,
                   'uid': uid, 'pid': pid, 'interface': preferred, 'fallback': fallback,
                   'active': None, 'fingerprint': None, 'created': time.time()}
        if automatic:
            session.update(automatic=True, name=data['name'])
        original = next((line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines() if line.startswith('0::')), None)
        self.sessions[identifier] = session
        try:
            (group / 'cgroup.procs').write_text(str(pid))
            for family in ('-4', '-6'):
                execute(['/usr/bin/ip', family, 'rule', 'add', 'priority', '30', 'fwmark', str(session['mark']), 'lookup', str(table)])
            self.update(session, force=True)
            self.persist()
            return session
        except Exception:
            if original:
                try:
                    (Path('/sys/fs/cgroup') / original.lstrip('/') / 'cgroup.procs').write_text(str(pid))
                except OSError:
                    pass
            self.remove(session)
            raise

    def update(self, session, force=False):
        available = {i['name']: i for i in interfaces()}
        if hasattr(self, 'health'):
            selected = self.health.choose(available, session['interface'], session['fallback'], session.get('active'))
        else:
            selected = next((available[n] for n in (session['interface'], session['fallback'])
                             if n and n in available and available[n]['connected']), None)
        fingerprint = json.dumps({'name': selected['name'], 'routes': selected['routes'],
                                  'addresses': [(a['family'], a['local'], a['prefixlen']) for a in selected['addresses']]}, sort_keys=True) if selected else 'null'
        if not force and fingerprint == session.get('fingerprint'):
            return
        table, mark, identifier = session['table'], session['mark'], session['id']
        # Close the session's output before rebuilding routes, so updates cannot leak.
        exists = execute(['/usr/bin/nft', 'list', 'table', 'inet', f'ns_{identifier}'], check=False).returncode == 0
        execute(['/usr/bin/nft', '-f', '-'], data=(f'delete table inet ns_{identifier}\n' if exists else '') + nft_rules(identifier, mark, None))
        for family in ('-4', '-6'):
            execute(['/usr/bin/ip', family, 'route', 'flush', 'table', str(table)], check=False)
        for command in route_commands(table, selected):
            execute(command)
        if selected:
            path = Path(f"/proc/sys/net/ipv4/conf/{selected['name']}/rp_filter")
            if selected['name'] not in self.reverse:
                self.reverse[selected['name']] = path.read_text().strip()
            path.write_text('2')
        rules = f'delete table inet ns_{identifier}\n' + nft_rules(identifier, mark, selected['name'] if selected else None)
        execute(['/usr/bin/nft', '-f', '-'], data=rules)
        session['active'] = selected['name'] if selected else None
        session['fingerprint'] = fingerprint

    def remove(self, session):
        identifier = session['id']
        execute(['/usr/bin/nft', 'delete', 'table', 'inet', f'ns_{identifier}'], check=False)
        for family in ('-4', '-6'):
            execute(['/usr/bin/ip', family, 'rule', 'del', 'priority', '30', 'fwmark', str(session['mark']), 'lookup', str(session['table'])], check=False)
            execute(['/usr/bin/ip', family, 'route', 'flush', 'table', str(session['table'])], check=False)
        group = ROOT / identifier
        try:
            group.rmdir()
        except OSError:
            pass
        self.sessions.pop(identifier, None)
        self.persist()

    def tick(self):
        self.automatic.refresh()
        devices = interfaces()
        self.health.refresh(devices)
        try:
            self.defaults.update({d['name']: d for d in devices}, self.health)
        except Exception as error:
            self.defaults.error = str(error)
            print(f'System failover: {error}', flush=True)
        for session in list(self.sessions.values()):
            group = ROOT / session['id']
            if not group.exists() or 'populated 0' in (group / 'cgroup.events').read_text():
                self.remove(session)
                continue
            try:
                self.update(session)
            except Exception as error:
                print(f"Session {session['id']}: {error}", flush=True)
        active = {s['active'] for s in self.sessions.values()}
        for interface in list(self.reverse):
            if interface not in active:
                path = Path(f'/proc/sys/net/ipv4/conf/{interface}/rp_filter')
                if path.exists() and path.read_text().strip() == '2':
                    path.write_text(self.reverse[interface])
                del self.reverse[interface]
        self.persist()

    def handle(self, uid, peer_pid, data):
        action = data.get('action')
        if action == 'health':
            return {'networks': self.health.snapshot(interfaces()), 'defaults': self.defaults.snapshot()}
        if action == 'automatic-status':
            return self.automatic.snapshot(uid)
        if action == 'automatic-set':
            data['interface'] = validate_interface(data['interface'])
            if data.get('fallback'):
                data['fallback'] = validate_interface(data['fallback'])
            return self.automatic.set(uid, data['name'], data)
        if action == 'automatic-delete':
            return self.automatic.delete(uid, data['name'])
        if action == 'create':
            return self.create(uid, peer_pid, data)
        if action == 'status':
            sessions = []
            for session in self.sessions.values():
                if session['uid'] != uid:
                    continue
                public = {k: v for k, v in session.items() if k != 'fingerprint'}
                counters = {}
                result = execute(['/usr/bin/nft', '-j', 'list', 'table', 'inet', f"ns_{session['id']}"], check=False)
                if result.returncode == 0:
                    for item in json.loads(result.stdout).get('nftables', []):
                        rule = item.get('rule', {})
                        for expression in rule.get('expr', []):
                            if 'counter' in expression:
                                counter = expression['counter']
                                counters[rule['chain']] = counter
                public['traffic'] = {'routed': counters.get('route', {'packets': 0, 'bytes': 0}),
                                     'blocked': counters.get('guard', {'packets': 0, 'bytes': 0})}
                sessions.append(public)
            return sessions
        if action == 'stop':
            session = self.sessions.get(data['id'])
            if not session or session['uid'] != uid:
                raise ValueError('Unknown session')
            # Stop the whole app tree before removing its route.
            (ROOT / session['id'] / 'cgroup.kill').write_text('1')
            return {'stopping': session['id']}
        raise ValueError('Unknown operation')

def main():
    if os.geteuid() != 0:
        raise RuntimeError('The routing service requires root')
    Path(SOCKET).parent.mkdir(mode=0o755, exist_ok=True)
    lock = open('/run/netswitch/daemon.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    Path(SOCKET).unlink(missing_ok=True)
    service = Service()
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(SOCKET)
        os.chmod(SOCKET, 0o666)  # SO_PEERCRED limits operations to each client's own children.
        server.listen(16)
        next_tick = time.monotonic() + 1
        while running:
            watch = service.automatic.watch
            ready, _, _ = select.select([server, *([watch.fd] if watch else [])], [], [], max(0, next_tick - time.monotonic()))
            if watch and watch.fd in ready:
                watch.drain()
            if time.monotonic() >= next_tick:
                service.tick()
                next_tick = time.monotonic() + 1
            if server not in ready:
                continue
            connection, _ = server.accept()
            with connection:
                connection.settimeout(3)
                try:
                    pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    raw = connection.makefile('rb').readline(65537)
                    if len(raw) > 65536:
                        raise ValueError('Request too large')
                    result = service.handle(uid, pid, json.loads(raw))
                    answer = {'ok': True, 'result': result}
                except Exception as error:
                    answer = {'ok': False, 'error': str(error)}
                try:
                    connection.sendall(json.dumps(answer).encode() + b'\n')
                except OSError:
                    pass
    # Keep live session routes on service shutdown. Restart recovers state.
    Path(SOCKET).unlink(missing_ok=True)
    service.health.close()
    if service.automatic.watch:
        service.automatic.watch.close()

if __name__ == '__main__':
    main()
