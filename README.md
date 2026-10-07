# Net Switch

A Linux desktop app and CLI that routes applications through a selected network interface. Save automatic rules for native apps, or launch apps explicitly through Net Switch. Applications and their children run as your normal user. Unselected applications retain the system default route.

## Install

Requires Python 3.11+, systemd with cgroup v2, iproute2, nftables, curl 8.9+ and NetworkManager. The desktop app additionally requires PyQt6.

```bash
python install.py                    # installs your CLI and desktop entry
netswitch install-system             # one-time administrator password prompt
netswitch gui
```

The system installation copies the service to `/usr/local/lib/netswitch-app`, installs `/usr/local/bin/netswitch`, and enables `netswitch.service`. Launch **Net Switch** from your application menu. Administrator authentication is required only for installing/updating the service; agents can subsequently launch apps using the CLI without sudo. Run `netswitch install-system` again after changing backend code.

## Desktop app

UI examples use synthetic data: [overview](docs/overview.png) and [app routing](docs/app-routing.png).

The desktop opens on **Overview**, with live routes and connection summaries. Select a saved application in the sidebar to edit its network. The editor groups connection and launch behavior separately and keeps its Save/Launch controls fixed at the bottom.

Click **Add application**, search for an installed app, then click **Wi-Fi** or **LAN** and **Launch app**. Launching also saves the app in the sidebar. Turn on **Switch networks if this one disconnects or loses internet** if you want a backup connection. Net Switch switches back when the preferred ISP recovers.

For supported native apps, **Apply whenever I open this app** is enabled when you choose a new app. Click **Save changes**, then open it normally from the application menu, shortcuts or terminal. The service intercepts execution of that app's executable and applies its route before it starts. You can close Net Switch's window; the background service keeps the rule active across reboots. Existing saved entries require checking this option and saving once. Uncheck it and save to return to explicit Net Switch launches.

Automatic rules match the executable for your user, not the shortcut, command arguments or individual browser profile. Every launch of that executable uses the rule, with descendants inheriting it. Apps already running need restarting. Steam/Proton, Flatpak/Snap, shared interpreters and privileged executables are excluded; use explicit launches or per-game Steam launch options for those. Rules refresh when executable files are replaced. Automatic routing requires a running service and Linux fanotify permission-event support; unsupported kernels report the feature unavailable. If the service is stopped, normal launches use ordinary routing. Assignment errors deny the selected execution and are reported by `auto list` and the service journal.

To remove a saved app, select it in the sidebar and click **Remove app** at the bottom of its editor, or right-click it and choose **Remove from Net Switch**. This removes its saved entry; it does not uninstall the app or close running windows. CLI equivalent: `netswitch profile delete "profile name"`.

Removing a saved app also removes its automatic rule. Existing sessions keep their current routing until they exit.

Browsers use your existing profile, including bookmarks, sign-ins, extensions and settings. Close the browser before launching it through Net Switch so the new process receives the selected route. Net Switch shows a message if it detects that browser already running. Older saved entries that used an automatically generated Net Switch browser profile now use the original app command. Explicit custom profile commands are preserved.

Steam games show **Set up in Steam** instead of launching Steam through the routing wrapper. Follow the three-step dialog and copy its per-game launch option. Net Switch does not change your Steam settings automatically. Restart the game when you are ready to apply its selected connection.

**Overview** in the sidebar shows active routed apps, the network each is using now, its preferred and backup connections, and outgoing traffic counters. It refreshes every three seconds, including automatic sessions and apps launched through the CLI or Steam launch option. Unselected apps are not individually monitored. Counters reset when routing rules change and do not include incoming traffic.

**Network settings** controls the default connection for everything else. **Advanced options** exposes network adapter names and custom commands when needed. App discovery, dialogs, reusable widgets, theme tokens and the main window live in separate modules under `netswitch/apps.py` and `netswitch/ui/`; the sidebar/workspace layout and compact dark theme take inspiration from [T3 Code](https://github.com/pingdotgg/t3code).

## CLI

### Agent skill

Install the reusable [Net Switch agent skill](skills/netswitch/SKILL.md) by asking your agent:

> Install the Net Switch skill from https://github.com/bugthedebugger/netswitch, at `skills/netswitch`.

Codex's skill installer accepts the repository and `--path skills/netswitch`. Other agents can install that folder in their own skill directory. From a local checkout, Codex users can also run:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/netswitch "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Invoke `$netswitch` after the skill becomes available. Installing the skill provides instructions; Net Switch and its routing service must also be installed on the Linux machine.

```bash
netswitch interfaces
netswitch profile add wifi --interface wlan0
netswitch profile add lan --interface eth0 --fallback wlan0
netswitch run wifi -- curl https://api.ipify.org
netswitch run --detach wifi -- firefox --no-remote --profile /path/to/profile
netswitch profile add work-browser --interface eth0 --fallback wlan0 --command firefox --no-remote --profile /path/to/profile
netswitch launch --detach work-browser
netswitch status
netswitch stop s42000                 # terminates the app and its children
netswitch doctor
```

Automatic routing for an app opened anywhere normally:

```bash
netswitch profile add firefox-wifi --interface wlan0 --fallback eth0 --command firefox
netswitch --json auto enable firefox-wifi
netswitch --json auto list
firefox                              # normal launch; no Net Switch wrapper required
netswitch --json auto disable firefox-wifi
```

Rules are per user and need no additional administrator prompt once the routing service is installed. `auto enable` registers the executable and routing choices from the saved profile. After editing a profile through the CLI, run `auto enable` again to update its rule; saving in the GUI updates it automatically. `auto disable` removes only the automatic rule and keeps the saved profile.

Put launcher options **before the profile name**; everything after the name (or `--`) belongs to the launched app. Commands are argument arrays, never evaluated as shell strings. Use `sh -c '…'` explicitly if you need shell behavior.

For agents, put `--json` before the subcommand:

```bash
netswitch --json interfaces
netswitch --json run --dry-run wifi -- curl https://api.ipify.org
netswitch --json run --detach wifi -- your-program --its-option
netswitch --json status
```

Detached app stdout/stderr go to `~/.local/state/netswitch/<pid>.log`. Foreground launches return the application's exit code. JSON errors return exit code 1. Use detached launches when consuming JSON so app output cannot mix with CLI output.

### Default connection and fallback

```bash
netswitch priority --prefer eth0 --fallback wlan0        # preview
netswitch priority --prefer eth0 --fallback wlan0 --apply
```

This sets both IPv4 and IPv6 route metrics to 100 for the preferred connection and 600 for the backup, then reapplies active NetworkManager connections. Both interfaces need active NetworkManager profiles. Changes persist in those profiles. NetworkManager may require administrator authentication according to your system policy. Physical connection loss falls back to the other default route.

For an ISP outage while the adapter stays connected, enable automatic internet failover in **Settings → Switch automatically if internet stops working**, or use:

```bash
netswitch failover enable --prefer eth0 --fallback wlan0
netswitch --json health
netswitch --json failover status
netswitch failover disable
```

Enabling/disabling system-wide failover requires administrator authentication and persists across reboots. It installs its own routing table and rules while preserving existing main routes and VPN policy. Disabling removes only those rules. Per-app routes remain independent: an app pinned to Wi-Fi stays on Wi-Fi even when the system default changes. `priority --apply --internet-fallback` also enables automatic failover; `--no-internet-fallback` disables it.

Checks run through each adapter separately using verified HTTPS requests to Cloudflare and Google IP endpoints, without system DNS or proxy settings. One successful endpoint establishes internet reachability. Checks run again about five seconds after a completed round; two failed rounds declare an outage, and two successful rounds restore a failed connection. Depending on timeouts, detection generally takes roughly 10–25 seconds. A single dropped request does not trigger switching. IPv4 is checked when available, with IPv6 checks for IPv6-only interfaces. This detects general internet reachability, not availability of every individual game server or website.

Switching ISP changes the public IP. Existing connections may need to reconnect; applications are not restarted automatically. If both ISPs fail, app profiles with fallback wait for a working connection; system-wide routing retains its last selected route while checking for recovery.

### Steam launch option

After creating the `wifi` profile, set an individual game's launch options to:

```text
/absolute/path/to/netswitch run wifi -- %command%
```

Restart that game for the rule to apply. Do not wrap the entire Steam client unless you want all its traffic routed. Networking delegated to an already-running process, including a previously running Wine server, remains with that process; verify the session and use a fresh per-app runtime where necessary.

## How it works

Explicit CLI launches fork an unprivileged child behind a pipe barrier. A root-owned service authenticates the CLI with Unix socket peer credentials and verifies that the child belongs to that user and is a direct child of the CLI. Automatic launches use a separate trusted path: inode-scoped `FAN_OPEN_EXEC_PERM` events hold execution while the daemon checks kernel-reported process credentials against that user's saved rule. Socket clients cannot use this path to assign arbitrary existing PIDs. In either case, the service moves the process into a dedicated cgroup, sets per-session nftables socket matching, packet marking, routing rules and source NAT, then releases execution. Descendants inherit the cgroup. An explicit Net Switch launch or already-routed descendant retains its chosen route. No executable or desktop shortcut is modified.

Each session uses an isolated routing table with terminal unreachable IPv4/IPv6 routes. If its selected interface disappears, traffic is blocked unless the profile explicitly supplies a fallback. The service checks interface changes once per second while idle and updates routes when gateways or addresses change. Route updates are guarded against falling through to the default ISP. Source NAT corrects the source address selected before the marked packet is rerouted. IPv6 uses the selected interface when it has an IPv6 default route; otherwise IPv6 is blocked for that app rather than leaking through another connection.

Priority 30 policy rules precede the existing PIA rules in an existing VPN configuration. Net Switch neither removes VPN firewall rules nor overrides kill switches; those can still block direct traffic. System DNS queries handled by a shared resolver can use the system default route. Net Switch selects the adapter for application socket traffic, not every supporting system service. Localhost is exempt so desktop/Steam IPC can keep working.

Routing ends after all processes in the app's cgroup exit. Closing the GUI does not stop apps. `netswitch stop` terminates the entire selected app tree. A service restart recovers live sessions. Unexpected daemon failure leaves the last routing rules in place until recovery. Changes to `rp_filter` are temporary and restored when the connection has no active Net Switch sessions.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q netswitch install.py
```

Unit tests cover isolation rules, no-network blocking, IPv6 leak prevention, argument boundaries, launch barriers, profile permissions and cross-user request rejection. End-to-end verification requires the installed root service and two active network connections.

## Remove

First close routed apps or stop their sessions and wait for `netswitch status` to be empty. Then:

```bash
sudo systemctl disable --now netswitch.service
sudo rm /etc/systemd/system/netswitch.service /usr/local/bin/netswitch
sudo rm -r /usr/local/lib/netswitch-app
sudo systemctl daemon-reload
rm ~/.local/bin/netswitch ~/.local/share/applications/netswitch.desktop
```

Saved profiles remain in `~/.config/netswitch/profiles.json` unless you delete them.
