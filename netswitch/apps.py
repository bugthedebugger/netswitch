"""Desktop-app discovery and launch preparation, independent of the UI."""
import configparser
import os
import re
import shlex
import shutil
from pathlib import Path

def desktop_command(value, name, icon, path):
    result = []
    for token in shlex.split(value):
        if token in ('%f', '%F', '%u', '%U', '%d', '%D', '%n', '%N', '%v', '%m'):
            continue
        if token == '%i':
            if icon:
                result.extend(['--icon', icon])
            continue
        result.append(re.sub(r'%(.)', lambda m: {'%': '%', 'c': name, 'k': str(path)}[m[1]], token))
    return result

def installed_apps(roots=None):
    if roots is None:
        roots = [Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications']
        roots += [Path(p) / 'applications' for p in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')]
    seen, apps = set(), []
    for root in roots:
        for path in sorted(Path(root).rglob('*.desktop')) if Path(root).exists() else []:
            identifier = str(path.relative_to(root)).replace('/', '-')
            if identifier in seen:
                continue
            seen.add(identifier)
            try:
                config = configparser.ConfigParser(interpolation=None, strict=False)
                config.read(path, encoding='utf-8')
                entry = config['Desktop Entry']
                if entry.get('Type') != 'Application' or any(entry.getboolean(k, fallback=False) for k in ('Hidden', 'NoDisplay', 'Terminal')):
                    continue
                name, icon = entry.get('Name', path.stem), entry.get('Icon', '')
                command = desktop_command(entry.get('Exec', ''), name, icon, path)
                if not command or name in ('NetSwitch', 'Net Switch'):
                    continue
                if not shutil.which(command[0]) and not (Path(command[0]).is_file() and os.access(command[0], os.X_OK)):
                    continue
                steam = re.search(r'steam://(?:rungameid|run)/(\d+)', ' '.join(command))
                apps.append({'name': name, 'icon': icon, 'command': command, 'steam_id': steam[1] if steam else None})
            except (configparser.Error, ValueError, KeyError, OSError):
                continue
    return sorted(apps, key=lambda app: app['name'].casefold())

def browser_kind(executable):
    name = Path(executable).name.lower()
    if name in ('firefox', 'firefox-bin', 'librewolf'):
        return 'librewolf' if name == 'librewolf' else 'firefox'
    for brand in ('brave', 'helium', 'vivaldi', 'microsoft-edge', 'chromium', 'chrome'):
        if brand in name:
            return brand
    return None

def app_command(app, interface, state_home=None):
    """Keep browser data and desktop arguments; never invent a user profile."""
    command = list(app['command'])
    if not command or app.get('steam_id') or app.get('custom'):
        return command, False
    kind = browser_kind(command[0])
    if kind in ('firefox', 'librewolf') and not any(arg in ('--new-instance', '-new-instance', '--no-remote', '-no-remote') for arg in command):
        command.insert(1, '--new-instance')
    return command, bool(kind)

def restore_browser_profile(profile):
    """Replace only the exact browser command older GUI versions generated."""
    app = profile.get('app', {})
    original = app.get('command', [])
    if not original or app.get('custom') or app.get('steam_id'):
        return profile
    executable = Path(original[0]).name.lower()
    kind = browser_kind(executable)
    if not kind:
        return profile
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    directory = state / 'netswitch/browsers' / f"{executable}-{profile['interface']}"
    legacy = ([original[0], '--no-remote', '--profile', str(directory)] if kind in ('firefox', 'librewolf') else
              [original[0], f'--user-data-dir={directory}', '--no-first-run', '--no-default-browser-check', '--new-window'])
    if profile.get('command') == legacy:
        return {**profile, 'command': app_command(app, profile['interface'])[0]}
    return profile

def require_browser_closed(command, proc_root=Path('/proc')):
    """Prevent a default-profile launch from delegating to an unrouted browser."""
    kind = browser_kind(command[0]) if command else None
    if not kind:
        return
    # Explicit profile selection is intentional (including isolated test profiles).
    selectors = ('--profile', '-profile', '-P', '--user-data-dir')
    if any(arg in selectors or arg.startswith(('--profile=', '--user-data-dir=')) for arg in command[1:]):
        return
    for process in proc_root.iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            arguments = (process / 'cmdline').read_bytes().split(b'\0')
            if not arguments[0]:
                continue
            executable = os.readlink(process / 'exe')
            running = browser_kind(executable) or browser_kind(os.fsdecode(arguments[0]))
            # Chromium installations sometimes name their executable "chrome".
            if running == 'chrome' and 'chromium' in Path(executable).parts:
                running = 'chromium'
            if running == kind:
                raise ValueError('Close all windows of this browser, then launch it from Net Switch. '
                                 'Your existing bookmarks, logins and settings will be used. '
                                 'An already-running browser cannot change networks.')
        except (OSError, PermissionError):
            continue

def network_name(device):
    if device['kind'] == 'Wi-Fi':
        return 'Wi-Fi'
    return 'LAN' if device['name'].startswith(('en', 'eth')) else device['name']
