import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from netswitch.health import Health, HealthMonitor, probe
from netswitch.defaults import DefaultRouting
from netswitch.daemon import Service

DEVICES = {
    'eth0': {'name': 'eth0', 'connected': True, 'routes': {'-4': {'gateway': '198.51.100.1'}}, 'addresses': []},
    'wlan0': {'name': 'wlan0', 'connected': True, 'routes': {'-4': {'gateway': '192.0.2.1'}}, 'addresses': []},
}

class HealthTests(unittest.TestCase):
    def monitor(self):
        monitor = HealthMonitor.__new__(HealthMonitor)
        monitor.records = {'eth0': Health(online=True), 'wlan0': Health(online=True)}
        return monitor

    def test_two_failures_and_two_recoveries_prevent_flapping(self):
        health = Health(online=True)
        health.record(False, 0)
        self.assertTrue(health.online)
        health.record(False, 5)
        self.assertFalse(health.online)
        health.record(True, 10)
        self.assertFalse(health.online)
        health.record(True, 15)
        self.assertTrue(health.online)

    def test_isp_outage_switches_even_though_adapter_stays_connected(self):
        monitor = self.monitor()
        self.assertEqual(monitor.choose(DEVICES, 'eth0', 'wlan0')['name'], 'eth0')
        monitor.records['eth0'].record(False, 0)
        monitor.records['eth0'].record(False, 5)
        self.assertTrue(DEVICES['eth0']['connected'])
        self.assertEqual(monitor.choose(DEVICES, 'eth0', 'wlan0')['name'], 'wlan0')
        monitor.records['eth0'].record(True, 10)
        self.assertEqual(monitor.choose(DEVICES, 'eth0', 'wlan0')['name'], 'wlan0')
        monitor.records['eth0'].record(True, 15)
        self.assertEqual(monitor.choose(DEVICES, 'eth0', 'wlan0')['name'], 'eth0')

    def test_both_offline_block_a_fallback_profile(self):
        monitor = self.monitor()
        for health in monitor.records.values():
            health.online = False
        self.assertIsNone(monitor.choose(DEVICES, 'eth0', 'wlan0'))

    def test_restart_keeps_backup_until_preferred_has_been_checked(self):
        monitor = self.monitor()
        monitor.records['eth0'] = Health()
        self.assertEqual(monitor.choose(DEVICES, 'eth0', 'wlan0', current='wlan0')['name'], 'wlan0')
        monitor.records['eth0'].record(True, 0)
        self.assertEqual(monitor.choose(DEVICES, 'eth0', 'wlan0', current='wlan0')['name'], 'eth0')

    def test_pinned_app_does_not_change_isp_due_to_probe_failure(self):
        monitor = self.monitor()
        monitor.records['eth0'].online = False
        self.assertEqual(monitor.choose(DEVICES, 'eth0', None)['name'], 'eth0')

    def test_probes_are_bound_and_do_not_use_proxy_or_unverified_tls(self):
        with patch('netswitch.health.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='h=1.1.1.1\nip=203.0.113.1\n')) as run:
            self.assertTrue(probe('wlan0'))
        command = run.call_args.args[0]
        self.assertEqual(command[1], '--disable')
        self.assertEqual(command[command.index('--interface') + 1], 'if!wlan0')
        self.assertEqual(command[command.index('--noproxy') + 1], '*')
        self.assertNotIn('--insecure', command)
        self.assertNotIn('-k', command)

    def test_second_provider_can_succeed_when_first_fails(self):
        responses = [SimpleNamespace(returncode=28, stdout=''), SimpleNamespace(returncode=0, stdout='{"Status":0}')]
        with patch('netswitch.health.subprocess.run', side_effect=responses):
            self.assertTrue(probe('wlan0'))

    def test_captive_portal_html_is_not_internet_success(self):
        with patch('netswitch.health.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='<html>Sign in</html>')):
            self.assertFalse(probe('wlan0'))

    def test_live_session_updates_to_healthy_backup(self):
        service = Service.__new__(Service)
        service.reverse = {'wlan0': '2'}
        service.health = self.monitor()
        service.health.records['eth0'].online = False
        session = {'id': 's42000', 'table': 42000, 'mark': 123, 'interface': 'eth0', 'fallback': 'wlan0', 'fingerprint': None}
        with patch('netswitch.daemon.interfaces', return_value=list(DEVICES.values())), patch('netswitch.daemon.execute', return_value=SimpleNamespace(returncode=0)), patch('netswitch.daemon.Path'):
            service.update(session)
        self.assertEqual(session['active'], 'wlan0')

    def test_system_failover_uses_private_table_and_preserves_main_routes(self):
        controller = DefaultRouting.__new__(DefaultRouting)
        controller.owned = None
        controller.active = None
        controller.fingerprint = None
        monitor = self.monitor()
        monitor.records['eth0'].online = False
        calls = []
        def fake_execute(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='[]')
        config = {'enabled': True, 'preferred': 'eth0', 'fallback': 'wlan0'}
        with patch.object(controller, 'configuration', return_value=config), patch.object(controller, 'save'), patch('netswitch.defaults.execute', side_effect=fake_execute):
            controller.update(DEVICES, monitor)
        self.assertEqual(controller.active, 'wlan0')
        self.assertTrue(any('41999' in command and 'wlan0' in command and 'replace' in command for command in calls))
        self.assertFalse(any('route' in command and 'flush' in command and 'main' in command for command in calls))
        self.assertFalse(any(command[0] == '/usr/bin/nmcli' for command in calls))
        self.assertFalse(any('rule' in command and 'del' in command for command in calls))

    def test_system_failover_refuses_rule_collisions(self):
        controller = DefaultRouting.__new__(DefaultRouting)
        controller.owned = None
        controller.active = None
        controller.fingerprint = None
        config = {'enabled': True, 'preferred': 'eth0', 'fallback': 'wlan0'}
        with patch.object(controller, 'configuration', return_value=config), patch('netswitch.defaults.execute', return_value=SimpleNamespace(stdout='[{"priority":90}]')):
            with self.assertRaisesRegex(RuntimeError, 'conflicts'):
                controller.update(DEVICES, self.monitor())

    def test_all_isps_offline_keeps_last_system_route(self):
        controller = DefaultRouting.__new__(DefaultRouting)
        controller.owned = {'interfaces': ['eth0', 'wlan0']}
        controller.active = 'wlan0'
        controller.fingerprint = None
        monitor = self.monitor()
        for state in monitor.records.values():
            state.online = False
        config = {'enabled': True, 'preferred': 'eth0', 'fallback': 'wlan0'}
        with patch.object(controller, 'configuration', return_value=config), patch.object(controller, 'save'), patch('netswitch.defaults.execute', return_value=SimpleNamespace(stdout='[]')):
            controller.update(DEVICES, monitor)
        self.assertEqual(controller.active, 'wlan0')
