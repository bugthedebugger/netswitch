import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    from PyQt6.QtWidgets import QApplication
    from netswitch.ui.window import Window
    from netswitch.ui.dialogs import AppPicker
    HAVE_QT = True
except ImportError:
    HAVE_QT = False
from netswitch import core

DEVICES = [
    {'name': 'eth0', 'kind': 'Ethernet / virtual', 'connected': True, 'routes': {'-4': {'metric': 100}}},
    {'name': 'wlan0', 'kind': 'Wi-Fi', 'connected': True, 'routes': {'-4': {'metric': 600}}},
]

@unittest.skipUnless(HAVE_QT, 'PyQt6 optional dependency is unavailable')
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [patch.object(core, 'CONFIG', Path(self.directory.name) / 'profiles.json'),
                        patch('netswitch.ui.window.interfaces', return_value=DEVICES),
                        patch('netswitch.ui.window.request', return_value=[])]
        for item in self.patches:
            item.start()
        self.window = Window()

    def tearDown(self):
        self.window.timer.stop()
        self.window.close()
        self.window.deleteLater()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_choose_network_and_save_without_typing_command(self):
        self.window.set_app({'name': 'Test App', 'icon': '', 'command': ['/usr/bin/true']})
        self.window.network_buttons['wlan0'].click()
        self.window.fallback.setChecked(True)
        name = self.window.save()
        profile = core.load_profiles()[name]
        self.assertEqual(profile['command'], ['/usr/bin/true'])
        self.assertEqual(profile['interface'], 'wlan0')
        self.assertEqual(profile['fallback'], 'eth0')
        self.assertFalse(self.window.advanced.isVisible())

    def test_overview_is_home_and_editor_actions_stay_visible(self):
        self.assertIs(self.window.pages.currentWidget(), self.window.usage)
        self.window.set_app({'name': 'Test Game', 'command': ['/usr/bin/true'], 'steam_id': '123'})
        self.window.resize(880, 640)
        self.window.show()
        self.qt.processEvents()
        self.assertFalse(self.window.launch_button.visibleRegion().isEmpty())
        self.assertTrue(self.window.automatic.isHidden())
        self.assertFalse(self.window.automatic_reason.isHidden())
        self.window.show_usage()
        self.assertIs(self.window.pages.currentWidget(), self.window.usage)
        self.assertTrue(self.window.overview_button.isChecked())

    def test_launch_uses_saved_app_and_cli(self):
        self.window.set_app({'name': 'Test App', 'icon': '', 'command': ['/usr/bin/true']})
        with patch.object(self.window, 'invoke') as invoke:
            self.window.launch()
        self.assertEqual(invoke.call_args.args[0][:2], ['launch', '--detach'])

    def test_browser_launch_explains_existing_data_and_open_browser(self):
        self.window.set_app({'name': 'Firefox', 'command': ['/usr/bin/firefox']})
        self.assertIn('usual bookmarks', self.window.app_hint.text())
        self.assertNotIn('--profile', self.window.command.text())
        with patch('netswitch.ui.window.require_browser_closed', side_effect=ValueError('Close all windows')), \
             patch('netswitch.ui.window.QMessageBox.information') as dialog, patch.object(self.window, 'invoke') as invoke:
            self.window.launch()
        dialog.assert_called_once()
        invoke.assert_not_called()

    def test_automatic_save_and_removal_update_background_rule(self):
        self.window.auto_available = True
        self.window.set_app({'name': 'Test App', 'command': ['/usr/bin/true']})
        self.window.automatic.setChecked(True)
        with patch('netswitch.ui.window.request') as request:
            name = self.window.save()
            self.assertEqual(request.call_args.args[0]['action'], 'automatic-set')
            self.assertEqual(request.call_args.args[0]['name'], name)
            self.assertTrue(core.load_profiles()[name]['automatic'])
            self.window.delete(name)
            self.assertEqual(request.call_args.args[0], {'action': 'automatic-delete', 'name': name})

    def test_disabled_automatic_control_explains_why(self):
        self.assertIn('Choose an app', self.window.automatic_reason.text())
        self.window.auto_available = True
        self.window.set_app({'name': 'Firefox', 'command': ['/usr/bin/true']})
        self.assertTrue(self.window.automatic.isEnabled())
        self.assertTrue(self.window.automatic_reason.isHidden())
        self.window.set_app({'name': 'Steam Game', 'command': ['/usr/bin/true'], 'steam_id': '123'})
        self.assertFalse(self.window.automatic.isEnabled())
        self.assertIn('Set up in Steam', self.window.automatic_reason.text())
        self.window.auto_available = False
        self.window.set_app({'name': 'Native App', 'command': ['/usr/bin/true']})
        self.assertIn('Update the routing service', self.window.automatic_reason.text())

    def test_steam_session_uses_game_name_instead_of_launcher(self):
        self.window.steam_names = {'123': 'Test Game'}
        with patch('netswitch.ui.window.Path') as path:
            path.return_value.read_bytes.return_value = b'reaper\0SteamLaunch\0AppId=123\0'
            self.assertEqual(self.window.session_title({'id': 'test', 'pid': 987654}), 'Test Game')

    def test_remove_saved_app_clears_editor_and_preserves_other_apps(self):
        core.update_profile('Other app', {'interface': 'wlan0', 'command': ['/usr/bin/true']})
        self.window.set_app({'name': 'Test App', 'command': ['/usr/bin/true']})
        name = self.window.save()
        self.assertFalse(self.window.remove_app.isHidden())
        with patch.object(self.window, 'invoke') as invoke:
            self.window.remove_app.click()
        self.assertNotIn(name, core.load_profiles())
        self.assertIn('Other app', core.load_profiles())
        self.assertIsNone(self.window.app_choice)
        self.assertIsNone(self.window.loaded_profile)
        self.assertEqual(self.window.command.text(), '')
        self.assertFalse(self.window.launch_button.isEnabled())
        self.assertTrue(self.window.remove_app.isHidden())
        invoke.assert_not_called()

    def test_removing_other_app_keeps_current_selection(self):
        core.update_profile('Other app', {'interface': 'wlan0', 'command': ['/usr/bin/true']})
        self.window.set_app({'name': 'Test App', 'command': ['/usr/bin/true']})
        name = self.window.save()
        self.window.delete('Other app')
        self.assertEqual(self.window.loaded_profile, name)
        self.assertEqual(self.window.app_choice['name'], 'Test App')
        self.assertNotIn('Other app', core.load_profiles())

    def test_disconnected_backup_can_be_saved_for_later(self):
        self.window.set_app({'name': 'Test App', 'command': ['/usr/bin/true']})
        self.window.network_buttons['wlan0'].click()
        self.window.devices = [{**DEVICES[0], 'connected': False}, DEVICES[1]]
        self.window.fallback.setChecked(True)
        self.assertEqual(self.window.profile_data()[1]['fallback'], 'eth0')

    def test_unfinished_advanced_quote_does_not_crash(self):
        self.window.set_app({'name': 'Test App', 'icon': '', 'command': ['/usr/bin/true']})
        self.window.command.setText('/usr/bin/true "unfinished')
        self.window.custom_command()
        self.assertEqual(self.window.app_choice['command'], ['/usr/bin/true'])

    def test_game_opens_setup_instead_of_launching(self):
        self.window.set_app({'name': 'Test Game', 'command': ['/usr/bin/true'], 'steam_id': '123'})
        with patch.object(self.window, 'steam_setup') as setup, patch.object(self.window, 'invoke') as invoke:
            self.window.launch()
        setup.assert_called_once()
        invoke.assert_not_called()

    def test_picker_search_filters_installed_apps(self):
        with patch('netswitch.ui.dialogs.installed_apps', return_value=[{'name': 'Test Browser', 'icon': '', 'command': ['/usr/bin/true']} ]):
            picker = AppPicker()
            picker.search.setText('no match')
            self.assertTrue(picker.list.item(0).isHidden())
            picker.search.setText('browser')
            self.assertFalse(picker.list.item(0).isHidden())
            picker.list.setCurrentRow(0)
            picker.choose()
            self.assertEqual(picker.selected_app['name'], 'Test Browser')
            picker.close()
