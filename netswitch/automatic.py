"""Native executable interception; only kernel execution events can assign PIDs."""
import ctypes
import errno
import json
import os
import stat
import struct
from pathlib import Path

CONFIG = Path('/var/lib/netswitch/automatic.json')
MASK = 0x40000  # FAN_OPEN_EXEC_PERM
DENY, ALLOW = 2, 1
METADATA = struct.Struct('=IBBHQii')

def executable_path(value):
    path = Path(value).resolve(strict=True)
    info = path.stat()
    if not path.is_file() or not info.st_mode & 0o111:
        raise ValueError('Choose an executable app file')
    name = path.name.lower()
    if name in ('ip', 'nft', 'nmcli', 'pkexec', 'sudo', 'systemctl', 'steam', 'flatpak', 'snap',
                'bash', 'sh', 'dash', 'zsh', 'fish', 'env', 'wine', 'wine64', 'wineserver', 'proton',
                'node', 'perl', 'ruby') or name.startswith(('python', 'ld-linux', 'ld-musl')):
        raise ValueError('Automatic routing needs an app-specific native executable or launcher. '
                         'Shared runtimes, Steam/Proton and sandbox launchers need a Net Switch launch command.')
    if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise ValueError('Privileged executables cannot have automatic app rules')
    try:
        if os.getxattr(path, 'security.capability'):
            raise ValueError('Privileged executables cannot have automatic app rules')
    except OSError as error:
        if error.errno not in (errno.ENODATA, errno.ENOTSUP):
            raise
    return str(path)

class ExecutionWatch:
    def __init__(self, callback):
        self.callback = callback
        self.marks = {}
        self.error = None
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.libc.fanotify_mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int, ctypes.c_char_p]
        self.fd = self.libc.fanotify_init(1 | 2 | 4, os.O_RDONLY | os.O_CLOEXEC)
        if self.fd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))

    def mark(self, flags, path):
        if self.libc.fanotify_mark(self.fd, flags, MASK, -100, os.fsencode(path)) < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(path))

    def sync(self, paths):
        wanted = {}
        for path in paths:
            info = os.stat(path)
            wanted[(info.st_dev, info.st_ino)] = path
        for identity in list(self.marks):
            if identity not in wanted:
                held = self.marks.pop(identity)
                try:
                    self.mark(2, f'/proc/self/fd/{held}')
                finally:
                    os.close(held)
        for identity, path in wanted.items():
            if identity not in self.marks:
                held = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
                try:
                    info = os.fstat(held)
                    if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                        raise OSError('Executable changed while registering automatic routing')
                    self.mark(1, f'/proc/self/fd/{held}')
                except Exception:
                    os.close(held)
                    raise
                self.marks[identity] = held

    def drain(self):
        # One bounded batch keeps CLI requests and health maintenance responsive.
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        offset = 0
        while offset + METADATA.size <= len(data):
            length, version, _, header, mask, fd, pid = METADATA.unpack_from(data, offset)
            if version != 3 or length < header or length < METADATA.size or offset + length > len(data):
                raise RuntimeError('Invalid fanotify metadata')
            offset += length
            if fd < 0:
                self.error = 'Execution event queue overflow; automatic routing may have missed a launch'
                continue
            response = ALLOW
            try:
                if mask & MASK:
                    info = os.fstat(fd)
                    response = ALLOW if self.callback(pid, (info.st_dev, info.st_ino)) else DENY
            except Exception as error:
                # Selected apps fail closed on assignment errors. Unselected apps
                # are allowed by the callback before any routing work begins.
                response = DENY
                self.error = str(error)
                print(f'Automatic routing: {error}', flush=True)
            finally:
                try:
                    if mask & MASK:
                        os.write(self.fd, struct.pack('=iI', fd, response))
                finally:
                    os.close(fd)

    def close(self):
        os.close(self.fd)  # Closing releases pending permission events.
        for fd in self.marks.values():
            os.close(fd)
        self.marks.clear()

class AutomaticRules:
    def __init__(self, attach):
        self.rules = json.loads(CONFIG.read_text()) if CONFIG.exists() else []
        self.attach = attach
        self.identities = {}
        self.error = None
        try:
            self.watch = ExecutionWatch(self.execution)
        except OSError as error:
            self.watch = None
            self.error = f'Automatic execution interception is unavailable: {error}'
        self.refresh()

    def save(self):
        CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = CONFIG.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.rules))
        temporary.chmod(0o600)
        temporary.replace(CONFIG)

    def refresh(self):
        identities, paths = {}, []
        for rule in self.rules:
            try:
                path = executable_path(rule['executable'])
                info = os.stat(path)
                identities[(rule['uid'], info.st_dev, info.st_ino)] = rule
                paths.append(path)
                rule.pop('error', None)
            except (ValueError, OSError) as error:
                rule['error'] = str(error)
        self.identities = identities
        if self.watch:
            try:
                self.watch.sync(paths)
                self.error = None
            except OSError as error:
                self.error = str(error)

    def set(self, uid, name, data):
        if uid <= 0:
            raise ValueError('Automatic rules belong to regular users')
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError('Rule names must contain 1–100 characters')
        if not self.watch:
            raise RuntimeError(self.error)
        rule = {'uid': uid, 'name': name, 'executable': executable_path(data['executable']),
                'interface': data['interface'], 'fallback': data.get('fallback')}
        identity = os.stat(rule['executable'])
        existing = self.identities.get((uid, identity.st_dev, identity.st_ino))
        if (existing and existing['name'] != name) or any(r['uid'] == uid and r['executable'] == rule['executable'] and r['name'] != name for r in self.rules):
            raise ValueError('This executable already has an automatic rule. Update or remove that rule first.')
        if sum(r['uid'] == uid for r in self.rules) >= 64 and not any(r['uid'] == uid and r['name'] == name for r in self.rules):
            raise ValueError('Automatic rule limit reached (64 apps per user)')
        previous = self.rules[:]
        self.rules = [r for r in self.rules if not (r['uid'] == uid and r['name'] == name)] + [rule]
        try:
            self.refresh()
            if self.error or rule.get('error'):
                raise RuntimeError(self.error or rule['error'])
            self.save()
            self.watch.error = None
        except Exception:
            self.rules = previous
            self.refresh()
            raise
        return self.snapshot(uid)

    def delete(self, uid, name):
        self.rules = [r for r in self.rules if not (r['uid'] == uid and r['name'] == name)]
        self.refresh()
        self.save()
        return self.snapshot(uid)

    def snapshot(self, uid):
        return {'available': self.watch is not None, 'error': self.error or (self.watch.error if self.watch else None),
                'rules': [{k: v for k, v in r.items() if k != 'uid'} for r in self.rules if r['uid'] == uid]}

    def execution(self, pid, identity):
        try:
            fields = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
            uids = [int(v) for v in fields['Uid'].split()]
            uid = uids[0]
            rule = self.identities.get((uid, *identity))
            if not rule or uid <= 0 or any(v != uid for v in uids):
                return True
            # Explicit Net Switch launches and routed descendants keep their route.
            if any(line.split(':', 2)[-1].startswith('/netswitch/') for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines()):
                return True
            self.attach(uid, int(fields['Tgid']), rule)
            return True
        except FileNotFoundError:
            return True  # The requesting process has already exited.
