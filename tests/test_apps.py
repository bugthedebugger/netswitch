import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from netswitch.apps import app_command, desktop_command, installed_apps, network_name, restore_browser_profile, require_browser_closed

class AppTests(unittest.TestCase):
    def test_desktop_arguments_and_field_codes(self):
        self.assertEqual(desktop_command('/usr/bin/true "a b" %U %i %c %k %%', 'Test App', 'test-icon', '/x/test.desktop'),
                         ['/usr/bin/true', 'a b', '--icon', 'test-icon', 'Test App', '/x/test.desktop', '%'])

    def test_hidden_user_entry_overrides_system_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            user, system = Path(directory) / 'user', Path(directory) / 'system'
            user.mkdir(); system.mkdir()
            (user / 'test.desktop').write_text('[Desktop Entry]\nType=Application\nName=Hidden\nHidden=true\nExec=/usr/bin/true\n')
            (system / 'test.desktop').write_text('[Desktop Entry]\nType=Application\nName=System\nExec=/usr/bin/true\n')
            self.assertEqual(installed_apps([user, system]), [])

    def test_steam_game_is_recognized_without_launching(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'game.desktop').write_text('[Desktop Entry]\nType=Application\nName=Test Game\nExec=/usr/bin/true steam://rungameid/123\n')
            apps = installed_apps([Path(directory)])
            self.assertEqual(apps[0]['steam_id'], '123')

    def test_browsers_keep_user_data_and_desktop_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = {'command': ['/usr/bin/chromium']}
            wifi, recognized = app_command(browser, 'wlan0', directory)
            lan, _ = app_command(browser, 'eth0', directory)
            self.assertTrue(recognized)
            self.assertEqual(wifi, lan)
            self.assertEqual(wifi, browser['command'])
            firefox, recognized = app_command({'command': ['/usr/bin/firefox', '--private-window', 'https://example.com']}, 'wlan0', directory)
            self.assertTrue(recognized)
            self.assertEqual(firefox, ['/usr/bin/firefox', '--new-instance', '--private-window', 'https://example.com'])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_generated_browser_profile_is_restored(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'XDG_STATE_HOME': directory}):
            for executable in ('firefox', 'chromium'):
                original = [f'/usr/bin/{executable}', 'https://example.com']
                path = str(Path(directory) / f'netswitch/browsers/{executable}-wlan0')
                legacy = ([original[0], '--no-remote', '--profile', path] if executable == 'firefox' else
                          [original[0], f'--user-data-dir={path}', '--no-first-run', '--no-default-browser-check', '--new-window'])
                profile = {'interface': 'wlan0', 'fallback': 'eth0', 'command': legacy, 'app': {'command': original}}
                restored = restore_browser_profile(profile)
                self.assertEqual(restored['command'], app_command(profile['app'], 'wlan0')[0])
                self.assertEqual(restored['fallback'], 'eth0')
                custom = {**profile, 'command': [original[0], '--profile', '/my/profile']}
                self.assertEqual(restore_browser_profile(custom), custom)

    def test_running_default_browser_is_blocked_without_closing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / '123'
            process.mkdir()
            (process / 'cmdline').write_bytes(b'/usr/lib/firefox/firefox\0')
            (process / 'exe').symlink_to('/usr/lib/firefox/firefox')
            with self.assertRaisesRegex(ValueError, 'Close all windows'):
                require_browser_closed(['/usr/bin/firefox', '--new-instance'], Path(directory))
            require_browser_closed(['/usr/bin/firefox', '--profile', '/isolated/test'], Path(directory))
            self.assertTrue(process.exists())

    def test_custom_command_is_preserved(self):
        app = {'custom': True, 'command': ['chromium', '--my-argument', 'value with spaces']}
        self.assertEqual(app_command(app, 'wlan0'), (app['command'], False))

    def test_friendly_network_names(self):
        self.assertEqual(network_name({'name': 'wlan0', 'kind': 'Wi-Fi'}), 'Wi-Fi')
        self.assertEqual(network_name({'name': 'eth0', 'kind': 'Ethernet / virtual'}), 'LAN')
