import argparse
import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from . import __version__
from .core import execute, interfaces, load_profiles, request, update_profile, validate_interface

def emit(value, as_json):
    if as_json:
        print(json.dumps(value, indent=2))
    elif isinstance(value, list):
        for row in value:
            print(json.dumps(row, ensure_ascii=False))
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False))

def resolve(name):
    profiles = load_profiles()
    if name in profiles:
        return profiles[name]
    return {'interface': validate_interface(name), 'fallback': None, 'command': []}

def launch(profile, command, *, detach=False):
    if not command:
        raise ValueError('Provide a command after --, or save one in the profile')
    if os.geteuid() == 0:
        raise ValueError('Run netswitch as your regular user, not sudo')
    from .apps import require_browser_closed
    require_browser_closed(command)
    # Start behind a pipe barrier: no application sockets exist before its routing rules.
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(write_fd)
            os.setsid()
            if os.read(read_fd, 1) != b'1':
                os._exit(125)
            os.close(read_fd)
            if detach:
                logs = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'netswitch'
                logs.mkdir(parents=True, exist_ok=True)
                log = os.open(str(logs / f'{os.getpid()}.log'), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                os.dup2(log, 1)
                os.dup2(log, 2)
                os.close(log)
                null = os.open('/dev/null', os.O_RDONLY)
                os.dup2(null, 0)
                os.close(null)
            os.execvp(command[0], command)
        except BaseException as error:
            print(f'netswitch: {error}', file=sys.stderr)
            os._exit(127)
    os.close(read_fd)
    try:
        session = request({'action': 'create', 'pid': pid, 'interface': profile['interface'], 'fallback': profile.get('fallback')})
        os.write(write_fd, b'1')
    except BaseException:
        os.close(write_fd)
        os.waitpid(pid, 0)
        raise
    os.close(write_fd)
    return pid, session

def parser():
    root = argparse.ArgumentParser(prog='netswitch', description='Launch applications through a selected Linux network interface.')
    root.add_argument('--json', action='store_true', help='Machine-readable output (place before the subcommand)')
    root.add_argument('--version', action='version', version=__version__)
    commands = root.add_subparsers(dest='action', required=True)
    commands.add_parser('interfaces', help='List interfaces, addresses and gateways')
    commands.add_parser('status', help='List your active routed application sessions')
    commands.add_parser('health', help='Show internet reachability for each network')
    commands.add_parser('doctor', help='Check prerequisites and routing conflicts')
    commands.add_parser('gui', help='Open the desktop app')
    commands.add_parser('install-system', help='Install the root routing service (administrator authentication)')
    priority = commands.add_parser('priority', help='Set persistent default-network priority through NetworkManager')
    priority.add_argument('--prefer', required=True)
    priority.add_argument('--fallback', required=True)
    priority.add_argument('--apply', action='store_true', help='Apply now; otherwise show the planned changes')
    priority.add_argument('--internet-fallback', action=argparse.BooleanOptionalAction, default=None, help='Also switch the default network when an ISP loses internet')
    failover = commands.add_parser('failover', help='Manage automatic system-wide internet failover').add_subparsers(dest='operation', required=True)
    failover.add_parser('status')
    failover.add_parser('disable')
    enable = failover.add_parser('enable')
    enable.add_argument('--prefer', required=True)
    enable.add_argument('--fallback', required=True)
    automatic = commands.add_parser('auto', help='Route native apps whenever they start normally').add_subparsers(dest='operation', required=True)
    automatic.add_parser('list')
    enable_auto = automatic.add_parser('enable')
    enable_auto.add_argument('profile', help='Saved app profile (must have an app command)')
    disable_auto = automatic.add_parser('disable')
    disable_auto.add_argument('profile')
    profiles = commands.add_parser('profile', help='Manage saved routing and app profiles').add_subparsers(dest='operation', required=True)
    profiles.add_parser('list')
    add = profiles.add_parser('add')
    add.add_argument('name')
    add.add_argument('--interface', required=True)
    add.add_argument('--fallback')
    add.add_argument('--command', nargs=argparse.REMAINDER, default=[])
    delete = profiles.add_parser('delete')
    delete.add_argument('name')
    for action in ('run', 'launch'):
        run = commands.add_parser(action, help='Launch a command or a saved application profile')
        run.add_argument('profile', help='Saved profile name or interface name')
        run.add_argument('--detach', action='store_true', help='Run in the background; logs go to ~/.local/state/netswitch/')
        run.add_argument('--dry-run', action='store_true', help='Show plan without starting the app')
        run.add_argument('command', nargs=argparse.REMAINDER, help='Command after --')
    stop = commands.add_parser('stop', help='Terminate a routed app and all its child processes')
    stop.add_argument('session')
    return root

def priority_plan(preferred, fallback):
    preferred, fallback = validate_interface(preferred), validate_interface(fallback)
    if preferred == fallback:
        raise ValueError('Preferred and fallback must differ')
    commands = []
    for device, metric in ((preferred, 100), (fallback, 600)):
        uuid = execute(['/usr/bin/nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', device]).stdout.strip()
        if not uuid or uuid == '--':
            raise ValueError(f'{device} needs an active NetworkManager connection to configure priority')
        commands.append(['/usr/bin/nmcli', 'connection', 'modify', 'uuid', uuid,
                         'ipv4.route-metric', str(metric), 'ipv6.route-metric', str(metric),
                         'ipv4.never-default', 'no', 'ipv6.never-default', 'no'])
        commands.append(['/usr/bin/nmcli', 'device', 'reapply', device])
    return commands

def configure_failover(preferred=None, fallback=None):
    request({'action': 'health'})  # Require the updated routing service first.
    config = {'enabled': preferred is not None}
    if preferred:
        config.update(preferred=validate_interface(preferred), fallback=validate_interface(fallback))
        if preferred == fallback:
            raise ValueError('Preferred and fallback must differ')
    installer = Path(__file__).resolve().parent.parent / 'install.py'
    process = subprocess.run(['/usr/bin/pkexec', '/usr/bin/python', '-I', str(installer), '--configure-defaults', json.dumps(config)], capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip() or 'Administrator authentication was cancelled')
    # Configuration is picked up in the daemon's next maintenance tick.
    return config

def doctor():
    from shutil import which
    result = {'version': __version__, 'interfaces': interfaces(), 'cgroup_v2': Path('/sys/fs/cgroup/cgroup.controllers').exists(),
              'tools': {name: bool(which(name)) for name in ('ip', 'nft', 'nmcli', 'pkexec')},
              'rules': json.loads(execute(['/usr/bin/ip', '-j', 'rule']).stdout),
              'notes': ['Routes apply to new explicit launches or saved native automatic rules; existing sockets are not migrated.',
                        'Apps delegating networking to an existing process need a separate app instance.',
                        'System resolver DNS requests may follow the system default connection.',
                        'VPN firewall/kill-switch rules can still block direct connections.']}
    try:
        result['sessions'] = request({'action': 'status'})
        result['service'] = 'ready'
    except RuntimeError as error:
        result['service'] = str(error)
    return result

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == 'interfaces':
            emit(interfaces(), args.json)
        elif args.action == 'doctor':
            emit(doctor(), args.json)
        elif args.action == 'status':
            emit(request({'action': 'status'}), args.json)
        elif args.action == 'health':
            emit(request({'action': 'health'}), args.json)
        elif args.action == 'auto':
            if args.operation == 'list':
                emit(request({'action': 'automatic-status'}), args.json)
            elif args.operation == 'disable':
                emit(request({'action': 'automatic-delete', 'name': args.profile}), args.json)
                profiles = load_profiles()
                if args.profile in profiles:
                    update_profile(args.profile, {**profiles[args.profile], 'automatic': False})
            else:
                profile = load_profiles().get(args.profile)
                if not profile or not profile.get('command'):
                    raise ValueError('Save an app profile with a launch command first')
                if profile.get('app', {}).get('steam_id'):
                    raise ValueError('Steam games need their per-game Net Switch launch option')
                executable = shutil.which(profile['command'][0])
                if not executable:
                    raise ValueError('The saved app executable is not installed')
                emit(request({'action': 'automatic-set', 'name': args.profile, 'executable': executable,
                              'interface': profile['interface'], 'fallback': profile.get('fallback')}), args.json)
                update_profile(args.profile, {**profile, 'automatic': True})
        elif args.action == 'failover':
            if args.operation == 'status':
                emit(request({'action': 'health'})['defaults'], args.json)
            elif args.operation == 'enable':
                emit(configure_failover(args.prefer, args.fallback), args.json)
            else:
                emit(configure_failover(), args.json)
        elif args.action == 'profile':
            profiles = load_profiles()
            if args.operation == 'add':
                if not args.name.strip() or len(args.name) > 100:
                    raise ValueError('Profile names must contain 1–100 characters')
                profiles = update_profile(args.name, {'interface': validate_interface(args.interface),
                                       'fallback': validate_interface(args.fallback) if args.fallback else None,
                                       'command': args.command})
            elif args.operation == 'delete':
                if args.name not in profiles:
                    raise ValueError('Unknown profile')
                request({'action': 'automatic-delete', 'name': args.name})
                profiles = update_profile(args.name, None)
            emit(profiles, args.json)
        elif args.action in ('run', 'launch'):
            profile = resolve(args.profile)
            command = args.command
            # REMAINDER keeps flags for the app, so launcher options precede the profile.
            if command and command[0] == '--':
                command = command[1:]
            command = command or profile.get('command', [])
            if profile.get('app', {}).get('steam_id') and not args.command and not args.dry_run:
                raise ValueError('For this Steam game, copy its launch option from the desktop app, then launch it in Steam. The Steam client can otherwise reuse an existing process.')
            if args.dry_run:
                emit({'profile': profile, 'command': command, 'detach': args.detach}, args.json)
                return 0
            pid, session = launch(profile, command, detach=args.detach)
            emit(session, args.json) if args.detach or args.json else print(f"Net Switch: {session['id']} → {session['active']}", file=sys.stderr)
            if not args.detach:
                try:
                    _, status = os.waitpid(pid, 0)
                except KeyboardInterrupt:
                    request({'action': 'stop', 'id': session['id']})
                    _, status = os.waitpid(pid, 0)
                return os.waitstatus_to_exitcode(status)
        elif args.action == 'stop':
            emit(request({'action': 'stop', 'id': args.session}), args.json)
        elif args.action == 'priority':
            commands = priority_plan(args.prefer, args.fallback)
            if args.apply:
                for command in commands:
                    execute(command)
                if args.internet_fallback is not None:
                    configure_failover(args.prefer, args.fallback) if args.internet_fallback else configure_failover()
            emit({'applied': args.apply, 'commands': commands, 'internet_fallback': args.internet_fallback}, args.json)
        elif args.action == 'install-system':
            installer = Path(__file__).resolve().parent.parent / 'install.py'
            process = subprocess.run(['/usr/bin/pkexec', '/usr/bin/python', '-I', str(installer), '--system'])
            if process.returncode:
                raise RuntimeError('System installation failed or administrator authentication was cancelled')
            emit({'service': 'installed'}, args.json)
        elif args.action == 'gui':
            from .gui import main as gui_main
            return gui_main()
        return 0
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        if args.json:
            print(json.dumps({'ok': False, 'error': str(error)}))
        else:
            print(f'netswitch: {error}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
