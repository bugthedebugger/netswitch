import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from netswitch import automatic
from netswitch.cli import parser

class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.config = Path(self.folder.name) / 'rules.json'
        self.patches = [patch.object(automatic, 'CONFIG', self.config), patch.object(automatic, 'ExecutionWatch')]
        for p in self.patches:
            p.start()
        self.attach = Mock()
        self.rules = automatic.AutomaticRules(self.attach)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.folder.cleanup()

    def add(self, uid=1000, name='App'):
        return self.rules.set(uid, name, {'executable': '/usr/bin/true', 'interface': 'wlan0', 'fallback': 'eth0'})

    def test_rules_persist_and_are_private_to_user(self):
        self.add()
        self.add(uid=1001)
        self.assertEqual(len(self.rules.snapshot(1000)['rules']), 1)
        self.assertNotIn('uid', self.rules.snapshot(1000)['rules'][0])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        recovered = automatic.AutomaticRules(self.attach)
        self.assertEqual(len(recovered.rules), 2)
        self.rules.delete(1000, 'App')
        self.assertEqual([r['uid'] for r in json.loads(self.config.read_text())], [1001])

    def test_same_executable_cannot_have_conflicting_user_rules(self):
        self.add()
        with self.assertRaisesRegex(ValueError, 'already has'):
            self.add(name='Other')
        self.assertEqual(len(self.rules.rules), 1)

    def test_shared_interpreters_and_routing_tools_are_rejected(self):
        for path in ('/usr/bin/python', '/usr/bin/sh', '/usr/bin/ip', '/usr/bin/nft'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'app-specific'):
                automatic.executable_path(path)

    def test_failed_watch_does_not_persist_a_new_rule(self):
        self.rules.watch.sync.side_effect = OSError('watch unavailable')
        with self.assertRaisesRegex(RuntimeError, 'watch unavailable'):
            self.add()
        self.assertFalse(self.rules.rules)
        self.assertFalse(self.config.exists())

    def test_kernel_event_assigns_only_matching_regular_user(self):
        self.add()
        info = os.stat('/usr/bin/true')
        status = Mock()
        group = Mock()
        group.read_text.return_value = '0::/user.slice/app.scope\n'
        with patch.object(automatic, 'Path', side_effect=lambda p: status if p.endswith('/status') else group):
            for uid in (0, 1001):
                status.read_text.return_value = f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nTgid:\t123\n'
                self.assertTrue(self.rules.execution(123, (info.st_dev, info.st_ino)))
                self.attach.assert_not_called()
            status.read_text.return_value = 'Uid:\t1000\t1000\t1000\t1000\nTgid:\t123\n'
            self.assertTrue(self.rules.execution(123, (info.st_dev, info.st_ino)))
            self.attach.assert_called_once()
            self.assertEqual(self.attach.call_args.args[:2], (1000, 123))
            self.attach.reset_mock()
            group.read_text.return_value = '0::/netswitch/s42000\n'
            self.assertTrue(self.rules.execution(123, (info.st_dev, info.st_ino)))
            self.attach.assert_not_called()

    def test_cli_automatic_rule_commands(self):
        enable = parser().parse_args(['--json', 'auto', 'enable', 'Firefox · Wi-Fi'])
        self.assertEqual((enable.operation, enable.profile), ('enable', 'Firefox · Wi-Fi'))
        self.assertTrue(enable.json)
        self.assertEqual(parser().parse_args(['auto', 'disable', 'App']).operation, 'disable')
