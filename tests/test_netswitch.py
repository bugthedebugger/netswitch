import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from netswitch import core
from netswitch.cli import parser, priority_plan
from netswitch.daemon import Service

class RoutingTests(unittest.TestCase):
    def test_marking_is_scoped_and_loopback_is_exempt(self):
        rules = core.nft_rules('s42000', 1314062336, 'wlan0')
        self.assertIn('socket cgroupv2 level 2 "netswitch/s42000"', rules)
        self.assertLess(rules.index('ip daddr 127.0.0.0/8 return'), rules.index('socket cgroupv2'))
        self.assertIn('meta mark 1314062336 oifname "wlan0" masquerade', rules)
        self.assertIn('meta mark 1314062336 counter reject', rules)
        self.assertNotIn('flush ruleset', rules)

    def test_disconnected_profile_blocks_all_external_traffic(self):
        rules = core.nft_rules('s42000', 123, None)
        self.assertIn('meta mark 123 counter reject', rules)
        self.assertNotIn('masquerade', rules)
        commands = core.route_commands(42000, None)
        self.assertEqual(len(commands), 2)
        self.assertTrue(all('unreachable' in c for c in commands))

    def test_ipv4_only_connection_has_terminal_ipv6_route(self):
        device = {'name': 'wlan0', 'routes': {'-4': {'gateway': '192.0.2.1'}}}
        commands = core.route_commands(42000, device)
        self.assertEqual(len(commands), 3)
        self.assertIn('onlink', commands[1])
        self.assertIn('-6', commands[2])
        self.assertIn('unreachable', commands[2])

    def test_no_shell_interpolation_or_invalid_interface(self):
        for name in ('wlan0"; flush ruleset', '../eth0', '', 'a' * 16):
            with self.assertRaises(ValueError):
                core.validate_interface(name)

    def test_profiles_roundtrip_and_permissions(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'CONFIG', Path(folder) / 'profiles.json'):
            profile = {'wifi': {'interface': 'wlan0', 'command': ['browser', 'a b']}}
            core.save_profiles(profile)
            self.assertEqual(core.load_profiles(), profile)
            self.assertEqual(core.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_concurrent_agent_profile_updates_are_not_lost(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'CONFIG', Path(folder) / 'profiles.json'):
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(core.update_profile, f'app-{i}', {'interface': 'wlan0'}) for i in range(8)]
                for future in futures:
                    future.result()
            self.assertEqual(set(core.load_profiles()), {f'app-{i}' for i in range(8)})

    def test_agent_command_preserves_argument_boundaries(self):
        args = parser().parse_args(['--json', 'run', '--detach', 'wifi', '--', 'browser', '--profile', 'a b'])
        self.assertTrue(args.json)
        self.assertTrue(args.detach)
        self.assertEqual(args.command, ['browser', '--profile', 'a b'])

    def test_empty_command_saved_profile(self):
        args = parser().parse_args(['launch', '--detach', 'browser'])
        self.assertEqual(args.command, [])

    def test_priority_rejects_same_connection(self):
        with patch('netswitch.cli.validate_interface', side_effect=lambda n: n):
            with self.assertRaises(ValueError):
                priority_plan('wlan0', 'wlan0')

    def test_service_rejects_control_of_an_unrelated_process(self):
        service = Service.__new__(Service)
        with patch('netswitch.daemon.validate_interface', side_effect=lambda n: n):
            with self.assertRaisesRegex(ValueError, 'newly launched child'):
                service.create(os.getuid(), os.getpid(), {'interface': 'wlan0', 'pid': os.getpid()})

    def test_service_cannot_stop_someone_elses_app(self):
        service = Service.__new__(Service)
        service.sessions = {'s42000': {'uid': 999}}
        with self.assertRaisesRegex(ValueError, 'Unknown session'):
            service.handle(1000, 1, {'action': 'stop', 'id': 's42000'})

    def test_launch_failure_never_executes_the_app(self):
        from netswitch.cli import launch
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'ran'
            with patch('netswitch.cli.request', side_effect=RuntimeError('blocked')):
                with self.assertRaisesRegex(RuntimeError, 'blocked'):
                    launch({'interface': 'wlan0'}, ['/usr/bin/touch', str(marker)])
            self.assertFalse(marker.exists())

    def test_route_updates_are_atomic_and_fallback_is_explicit(self):
        from types import SimpleNamespace
        preferred = {'name': 'eth0', 'connected': False, 'routes': {}, 'addresses': []}
        fallback = {'name': 'wlan0', 'connected': True, 'routes': {'-4': {'gateway': '192.0.2.1'}}, 'addresses': []}
        service = Service.__new__(Service)
        service.reverse = {'wlan0': '1'}
        session = {'id': 's42000', 'table': 42000, 'mark': 123, 'interface': 'eth0', 'fallback': 'wlan0', 'fingerprint': None}
        calls = []
        def fake_execute(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout='')
        with patch('netswitch.daemon.interfaces', return_value=[preferred, fallback]), patch('netswitch.daemon.execute', side_effect=fake_execute), patch('netswitch.daemon.Path'):
            service.update(session)
        self.assertEqual(session['active'], 'wlan0')
        nft_batches = [kwargs['data'] for args, kwargs in calls if args[:2] == ['/usr/bin/nft', '-f']]
        self.assertEqual(len(nft_batches), 2)
        self.assertTrue(all(b.startswith('delete table inet ns_s42000\ntable inet ns_s42000') for b in nft_batches))
        self.assertNotIn('masquerade', nft_batches[0])
        self.assertIn('masquerade', nft_batches[1])
        self.assertTrue(any('unreachable' in args for args, _ in calls))
        calls.clear()
        session['fallback'] = None
        with patch('netswitch.daemon.interfaces', return_value=[preferred, fallback]), patch('netswitch.daemon.execute', side_effect=fake_execute):
            service.update(session)
        self.assertIsNone(session['active'])

    def test_lease_lifetime_changes_do_not_rebuild_firewall(self):
        from types import SimpleNamespace
        interface = {'name': 'wlan0', 'connected': True, 'routes': {'-4': {'gateway': '192.0.2.1'}},
                     'addresses': [{'family': 'inet', 'local': '192.0.2.2', 'prefixlen': 24, 'valid_life_time': 20}]}
        service = Service.__new__(Service)
        service.reverse = {'wlan0': '1'}
        session = {'id': 's42000', 'table': 42000, 'mark': 123, 'interface': 'wlan0', 'fallback': None, 'fingerprint': None}
        with patch('netswitch.daemon.interfaces', return_value=[interface]), patch('netswitch.daemon.execute', return_value=SimpleNamespace(returncode=0)), patch('netswitch.daemon.Path'):
            service.update(session)
        interface['addresses'][0]['valid_life_time'] = 19
        with patch('netswitch.daemon.interfaces', return_value=[interface]), patch('netswitch.daemon.execute') as execute:
            service.update(session)
        execute.assert_not_called()

if __name__ == '__main__':
    unittest.main()
