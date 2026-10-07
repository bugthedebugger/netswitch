---
name: netswitch
description: Use the installed Net Switch Linux CLI to launch apps through Wi-Fi, LAN, or another adapter, manage saved app profiles, and configure internet-aware fallback. Use when a user asks to choose an app's network or manage Net Switch routing.
---

# Net Switch

Net Switch routes newly launched applications and their child processes through a selected adapter. Native apps can have persistent automatic rules so ordinary launches are routed too. Other apps retain the system default network. Use its CLI as the normal user; the installed root service handles routing. Do not use sudo for app launches.

## Discover before changing anything

```bash
command -v netswitch
netswitch --json doctor
netswitch --json interfaces
netswitch --json health
netswitch --json profile list
netswitch --json status
netswitch --json auto list
```

If absent from PATH, check `~/.local/bin/netswitch` and `/usr/local/bin/netswitch`. If the service is unavailable, report the prerequisite; install or update it with `netswitch install-system` only within the user's requested setup scope. Linux may show an administrator dialog; the user enters their password there, never in chat or command arguments.

Discover adapter names rather than guessing. `interfaces` returns an array with `name`, `kind`, `connected`, addresses, and default `routes`. A connected adapter can still have no internet. `health.networks[NAME].internet` is `online`, `offline`, `checking`, or `disconnected`; `health.defaults` contains the system-wide configuration, active adapter, and error. Adapter names in the examples are placeholders; use the interfaces discovered on the target machine.

Inspect a saved profile's actual `interface`, `fallback`, and `command`, rather than trusting its name. Adding an existing profile name replaces its saved definition. Preserve unrelated profiles and sessions.

## Launch an app on one network

For a persistent rule that works when the user opens a native app normally:

```bash
netswitch --json profile add firefox-wifi --interface wlan0 --fallback eth0 --command firefox
netswitch --json auto enable firefox-wifi
netswitch --json auto list
```

The app can then be opened from the menu, a shortcut or terminal without a wrapper. Restart any existing instance. Rules match the executable for that user, regardless of arguments or browser profile, and descendants inherit the route. `auto list` reports `available`, `error`, and the user's rules (`name`, `executable`, `interface`, `fallback`, and any rule error). Check those results before claiming the rule is active. After changing a CLI profile, run `auto enable` again to refresh its registered route. `auto disable PROFILE_NAME` keeps the saved profile but removes automatic routing; existing sessions keep their routes until they exit. GUI saves update automatic rules directly.

Automatic rules persist across reboot, and need no administrator prompt after service installation. They require the service running and Linux fanotify permission-event support. They do not support Steam/Proton, Flatpak/Snap, shared interpreters or privileged executables; use the explicit launch flow below instead. If the service stops, normal launches use the ordinary network. Do not register automatic rules for general-purpose testing tools unless the user intends all their launches to use that route; prefer a disposable executable copy and remove its rule afterward.

Use an interface directly for a one-off strict launch, or create a saved profile. Replace example adapter names and executables with discovered values.

```bash
netswitch --json run --dry-run wlan0 -- /usr/bin/curl https://api.ipify.org
netswitch --json run --detach wlan0 -- /path/to/program --its-option
netswitch --json profile add app-wifi --interface wlan0 --fallback eth0
netswitch --json run --detach app-wifi -- /path/to/program --its-option
netswitch --json profile add saved-app --interface wlan0 --command /path/to/program --its-option
netswitch --json launch --detach saved-app
```

- Put `--json` before the subcommand, and `--detach` / `--dry-run` before the profile or interface name. Everything after that name, optionally separated by `--`, belongs to the app. For `profile add`, put `--command` last.
- Commands are argument arrays, not shell expressions. Shell pipelines require an explicit shell command; do not introduce one unnecessarily.
- Detached launches return session JSON containing `id`, `pid`, `interface`, `fallback`, and `active`. Save the session ID for verification and cleanup. App output goes to `~/.local/state/netswitch/<pid>.log` (or the corresponding XDG state directory).
- Foreground launches wait and return the app's exit code. Their output may mix with JSON; use detached mode when parsing launch responses.
- No fallback means strict pinning: a missing adapter blocks traffic rather than using another ISP. An explicit fallback switches on disconnection or failed internet checks, and returns after recovery. If both fail, the session waits for a usable network.

An already-running app's sockets cannot be moved. Apps that forward requests to an existing instance need a fresh process. Browsers use the user's existing data by default; close the browser before launching it through Net Switch. Default browser launches refuse when that browser is detected running, rather than silently opening a window on its old network. Explicit custom profile selectors are preserved. For a browser **test**, use a dedicated data directory so testing does not disturb user data:

```bash
test_profile=$(mktemp -d "${TMPDIR:-/tmp}/netswitch-browser.XXXXXX")
netswitch --json run --detach wlan0 -- chromium --user-data-dir="$test_profile" --no-first-run --new-window https://api.ipify.org
```

Firefox requires a dedicated profile directory with `--no-remote --profile DIRECTORY`. Keep test profile paths until the browser session has stopped; avoid the user's normal browser data.

For an individual Steam game, create the routing profile and provide this per-game launch option using the discovered absolute CLI path:

```text
/absolute/path/to/netswitch run app-wifi -- %command%
```

The user applies it in Steam and restarts the game when ready. Wrapping an already-running Steam client can reuse its existing process and fail to route the game. Shared existing Wine/server processes can also retain their previous route. Use isolated browser or curl tests rather than launching or modifying a user’s game for verification.

## Verify and remove

```bash
netswitch --json status
netswitch --json health
netswitch --json stop SESSION_ID
netswitch --json profile delete PROFILE_NAME
```

Confirm the saved session's `active` adapter; `null` means waiting for a connection. A public-IP request launched through the chosen adapter can corroborate routing; compare against a normal request when needed. Stop only your test session and poll until its ID disappears. `stop` terminates the entire app process tree, so use it on user apps only when closure is requested. Deleting a profile removes the saved entry; it neither uninstalls nor closes the app. Closing the GUI also leaves routed apps running.

## Default network for everything else

Per-app fallback and system-wide fallback are independent. Change the system default only when the user requests that scope:

```bash
netswitch --json failover status
netswitch failover enable --prefer eth0 --fallback wlan0
netswitch failover disable
```

Enable/disable requires administrator authentication, persists across reboot, and takes effect on the daemon's next maintenance tick. Verify `health.defaults.configuration`, `active`, and `error`; accepting configuration alone does not prove routing succeeded. Disabling removes Net Switch's default override and returns to the pre-existing default routing; it does not undo NetworkManager metric changes.

To configure persistent NetworkManager priority separately:

```bash
netswitch priority --prefer eth0 --fallback wlan0
netswitch priority --prefer eth0 --fallback wlan0 --apply
```

The first command previews changes. `--apply` sets IPv4/IPv6 route metrics to 100/600 and reapplies both active connections. It provides physical disconnect fallback; add `--internet-fallback` with `--apply` to also enable ISP outage detection. Both adapters need active NetworkManager connections for this command.

## Operational limits

Internet checks use adapter-bound HTTPS. Two failed rounds mark an outage; two successful rounds restore the preferred network. Detection generally takes about 10–25 seconds and checks general reachability, not a particular game's server. Changing ISP changes the public IP; existing connections may need to reconnect. Net Switch does not restart apps automatically.

Shared system DNS may follow the default network. IPv6 is blocked for a pinned app when its selected adapter lacks an IPv6 default route. VPN kill switches may still block direct traffic.

Use Net Switch's commands rather than manually editing cgroups, firewall rules, or policy tables. Do not disconnect live ISPs, restart NetworkManager, flush global firewall rules, remove VPN rules, or disturb unrelated sessions for routing verification. Simulated failures and disposable network namespaces are preferable when developing/testing the backend.

CLI errors return exit code 1, with `{"ok": false, "error": "..."}` under `--json`. Diagnose with `doctor`, `health`, and `journalctl -u netswitch.service`; report blocked administrator authentication rather than repeatedly triggering dialogs.
