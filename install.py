#!/usr/bin/python
"""Install user launchers, or a root-owned copy and its systemd service."""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
SYSTEM = Path('/usr/local/lib/netswitch-app')

def launcher(destination, source):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('#!/usr/bin/python\nimport sys\nsys.path.insert(0, ' + repr(str(source)) + ')\nfrom netswitch.cli import main\nsys.exit(main())\n')
    destination.chmod(0o755)

def install_user():
    launcher(Path.home() / '.local/bin/netswitch', SOURCE)
    icon_root = Path.home() / '.local/share/icons/hicolor'
    for asset in (SOURCE / 'netswitch/assets').glob('netswitch*'):
        size = 'scalable' if asset.suffix == '.svg' else asset.stem.removeprefix('netswitch-')
        target = icon_root / size / 'apps' / ('netswitch' + asset.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(asset, target)
    desktop = Path.home() / '.local/share/applications/netswitch.desktop'
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Net Switch\nComment=Choose a network for each app\nExec=' + str(Path.home() / '.local/bin/netswitch') + ' gui\nIcon=netswitch\nStartupWMClass=NetSwitch\nTerminal=false\nCategories=Network;Utility;\n')
    if shutil.which('gtk-update-icon-cache'):
        subprocess.run(['gtk-update-icon-cache', '-f', '-t', str(icon_root)], capture_output=True)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(desktop.parent)], capture_output=True)
    print(f'Installed {Path.home() / ".local/bin/netswitch"} and the Net Switch desktop entry.')

def install_system():
    if os.geteuid() != 0:
        raise SystemExit('Run netswitch install-system for administrator authentication.')
    if SOURCE != SYSTEM:
        SYSTEM.mkdir(parents=True, exist_ok=True)
        package = SYSTEM / 'netswitch'
        package.mkdir(exist_ok=True)
        for path in (SOURCE / 'netswitch').rglob('*'):
            if not path.is_file() or path.suffix not in ('.py', '.svg', '.png'):
                continue
            target = package / path.relative_to(SOURCE / 'netswitch')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o755)
            os.chown(target.parent, 0, 0)
            shutil.copyfile(path, target)
            target.chmod(0o644)
            os.chown(target, 0, 0)
        shutil.copyfile(SOURCE / 'install.py', SYSTEM / 'install.py')
        (SYSTEM / 'install.py').chmod(0o644)
        for directory in (SYSTEM, package):
            directory.chmod(0o755)
            os.chown(directory, 0, 0)
    launcher(Path('/usr/local/bin/netswitch'), SYSTEM)
    unit = Path('/etc/systemd/system/netswitch.service')
    unit.write_text('''[Unit]
Description=Net Switch per-application network routing
After=NetworkManager.service

[Service]
Type=simple
WorkingDirectory=/usr/local/lib/netswitch-app
ExecStart=/usr/bin/python -m netswitch.daemon
Restart=on-failure
RestartSec=2
RuntimeDirectory=netswitch
RuntimeDirectoryPreserve=yes
UMask=0077
Environment=PYTHONDONTWRITEBYTECODE=1

[Install]
WantedBy=multi-user.target
''')
    unit.chmod(0o644)
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True)
    subprocess.run(['/usr/bin/systemctl', 'enable', '--now', 'netswitch.service'], check=True)
    subprocess.run(['/usr/bin/systemctl', 'restart', 'netswitch.service'], check=True)
    print('Routing service installed and started.')

def configure_defaults(payload):
    if os.geteuid() != 0:
        raise SystemExit('Changing system-wide failover requires administrator authentication.')
    config = json.loads(payload)
    path = Path('/etc/netswitch/default.json')
    if config.get('enabled') is not True:
        path.unlink(missing_ok=True)
        print('System-wide internet failover disabled.')
        return
    names = [config.get('preferred'), config.get('fallback')]
    if names[0] == names[1] or any(not isinstance(n, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', n) or not Path(f'/sys/class/net/{n}').exists() or n == 'lo' for n in names):
        raise SystemExit('Choose two different, existing network interfaces.')
    if not Path('/run/netswitch/default-routing.json').exists():
        for family in ('-4', '-6'):
            rules = json.loads(subprocess.check_output(['/usr/bin/ip', family, '-N', '-j', 'rule'], text=True))
            routes = json.loads(subprocess.check_output(['/usr/bin/ip', family, '-N', '-j', 'route', 'show', 'table', 'all'], text=True))
            if any(r.get('priority') in (85, 90, 91) for r in rules) or any(str(r.get('table')) == '41999' for r in rules + routes):
                raise SystemExit('Automatic default failover conflicts with existing policy rules or routing table 41999.')
    path.parent.mkdir(mode=0o755, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'enabled': True, 'preferred': names[0], 'fallback': names[1]}))
    temporary.chmod(0o644)
    temporary.replace(path)
    print('System-wide internet failover enabled.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--system', action='store_true')
    parser.add_argument('--configure-defaults')
    args = parser.parse_args()
    if args.configure_defaults is not None:
        configure_defaults(args.configure_defaults)
    else:
        install_system() if args.system else install_user()
