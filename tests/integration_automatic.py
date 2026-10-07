"""Root-only automatic exec test, using a disposable curl copy and real HTTPS.

pkexec /usr/bin/python -I /absolute/path/tests/integration_automatic.py
Neither user browser data nor either ISP connection is changed.
"""
import json
import os
import pwd
import select
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from netswitch import automatic
from netswitch.core import execute, interfaces
from netswitch.daemon import Service
from netswitch.health import Health, HealthMonitor

def main():
    uid = int(os.environ.get('PKEXEC_UID', '0'))
    if os.geteuid() != 0 or not uid:
        raise SystemExit('Run through pkexec from your normal account')
    user = pwd.getpwuid(uid)
    device = next(d for d in interfaces() if d['kind'] == 'Wi-Fi' and d['connected'])
    service = Service.__new__(Service)
    service.sessions, service.reverse = {}, {}
    service.persist = lambda: None
    service.health = HealthMonitor.__new__(HealthMonitor)
    service.health.records = {device['name']: Health(online=True)}
    child = None
    with tempfile.TemporaryDirectory(prefix='netswitch-exec-test-') as directory:
        folder = Path(directory)
        folder.chmod(0o755)
        executable = folder / 'testcurl'
        shutil.copyfile('/usr/bin/curl', executable)
        executable.chmod(0o755)
        automatic.CONFIG = folder / 'rules.json'
        rules = automatic.AutomaticRules(service.assign_automatic)
        try:
            rules.set(uid, 'Automatic test', {'executable': str(executable), 'interface': device['name']})
            # Root executes the same marked file too: it must not get this user's rule.
            read_fd, write_fd = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                os.dup2(write_fd, 1)
                os.close(write_fd)
                os.setgroups([])
                os.setgid(user.pw_gid)
                os.setuid(uid)
                # This is an ordinary shell launch, not a NetSwitch wrapper.
                os.execl('/usr/bin/sh', 'sh', '-c', 'exec "$1" --disable --noproxy "*" --max-time 10 --silent --fail https://api.ipify.org', 'sh', str(executable))
            os.close(write_fd)
            deadline = time.monotonic() + 20
            output = b''
            finished = False
            while time.monotonic() < deadline:
                ready, _, _ = select.select([read_fd, rules.watch.fd], [], [], 0.1)
                if rules.watch.fd in ready:
                    rules.watch.drain()
                if read_fd in ready:
                    chunk = os.read(read_fd, 4096)
                    if not chunk:
                        finished = True
                        break
                    output += chunk
            os.close(read_fd)
            assert finished, 'Automatic assignment did not complete before deadline'
            _, status = os.waitpid(child, 0)
            child = None
            assert os.waitstatus_to_exitcode(status) == 0 and output, 'Test HTTPS request failed'
            assert len(service.sessions) == 1, 'Normal launch did not create exactly one routed session'
            session = next(iter(service.sessions.values()))
            assert session['automatic'] and session['uid'] == uid and session['active'] == device['name']
            public = service.handle(uid, os.getpid(), {'action': 'status'})[0]
            assert public['traffic']['routed']['packets'] > 0, 'No packets matched automatic routing'
            assert public['traffic']['blocked']['packets'] == 0, 'Automatic route rejected test packets'
            rules.delete(uid, 'Automatic test')
            assert not rules.watch.marks and not rules.rules
            print(json.dumps({'ordinary_shell_launch': 'routed', 'matched_packets': public['traffic']['routed']['packets'],
                              'pre_execution_assignment': 'passed', 'rule_cleanup': 'passed', 'game_tested': False}))
        finally:
            rules.watch.close()
            if child:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            for session in list(service.sessions.values()):
                service.remove(session)
            for name, value in service.reverse.items():
                Path(f'/proc/sys/net/ipv4/conf/{name}/rp_filter').write_text(value)

if __name__ == '__main__':
    main()
