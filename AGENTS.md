# Net Switch

This project builds a Linux desktop app and CLI for per-application network selection.

- Run `python -m unittest discover -s tests -v` after backend/CLI changes.
- Run `python -m compileall -q netswitch install.py` for syntax checks.
- Launch the desktop app with `netswitch gui`; the desktop requires PyQt6.
- The user launcher installed by `python install.py` points to its checkout. Root service uses a separate root-owned copy; update it with `netswitch install-system` after daemon/core changes.
- Machine-readable CLI: `netswitch --json interfaces`, `netswitch --json status`, `netswitch --json run --detach wifi -- COMMAND ARG...`.
- Internet health: `netswitch --json health`. System-wide failover is opt-in: `netswitch failover enable --prefer eth0 --fallback wlan0`; `netswitch failover disable` removes only NetSwitch's default override. Configuration changes require administrator authentication.
- Native app automatic rules: `netswitch --json auto enable APP_PROFILE`, `netswitch --json auto list`, `netswitch --json auto disable APP_PROFILE`. Profiles need an executable command; rules apply to ordinary future launches and persist across reboot. Re-enable after CLI profile edits to update the registered rule.
- Launcher options precede the profile name. A profile can specify an explicit fallback. No fallback means block if unavailable.
- Apps require explicit NetSwitch launches or a saved automatic native-executable rule. Automatic assignment must happen only through verified kernel exec-permission events; retain the direct-child boundary for socket-created sessions. Never try to move already-running game sockets or route all of Steam when targeting one game.
- Use curl or an isolated browser instance for verification. Do not launch a user's games for testing; preserve normal browser data and active ISP connections.
- Do not flush the global firewall, restart NetworkManager, disconnect either ISP for testing, or remove VPN rules.
- Service accepts only the authenticated user's direct child processes; retain that boundary and execute app commands without root or shell interpolation.
- Root installation may trigger the system's administrator authentication dialog. The installed service requires no per-launch sudo.
- Public examples and UI screenshots must use synthetic data. Do not commit user profiles, real network addresses, personal paths, local app inventories, or credentials. Render public screenshots with `python scripts/render_demo.py`.
