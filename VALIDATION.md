# Validation

## Automated checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q netswitch install.py
```

The suite covers routing isolation, IPv6 blocking, profile concurrency, launch barriers, ownership checks, internet health and recovery, browser data preservation, automatic-rule persistence, desktop navigation and live usage updates. Test network fixtures use documentation-only IP ranges.

## Integration checks

Run only on an authorized Linux test host with the installed routing service:

- `tests/integration_automatic.py` uses a disposable curl executable to verify ordinary launches enter their routing group before sending HTTPS traffic. It removes its temporary rules afterward.
- `tests/integration_failover.py` discovers connected Ethernet and Wi-Fi adapters, simulates failed health checks, and verifies different ISP routes and stable recovery without disconnecting either adapter.
- `tests/integration_failover.py --default-routing` validates fallback, local-network preservation, interface-bound probes and cleanup in a disposable network namespace.

The integration scripts require administrator authentication. Do not use a user's normal browser data or games for testing. The scripts make outbound requests to public connectivity endpoints.

## Limitations

Physical ISP disconnection, seamless continuation of established TCP sessions across ISPs, a real upstream IPv6 gateway and VPN kill-switch interoperability require separate environment-specific verification. Shared system DNS may follow the system default route. ISP changes may require applications to reconnect.

The screenshots under `docs/` are illustrative renders using synthetic profiles, network details and traffic counters; they contain no local app inventory or user session data.
