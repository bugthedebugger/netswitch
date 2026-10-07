import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
try:
    from PyQt6.QtWidgets import QApplication
    from netswitch.ui.usage import UsagePage, bytes_text
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

@unittest.skipUnless(HAVE_QT, 'PyQt6 optional dependency is unavailable')
class UsageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_current_route_is_distinct_from_preference_and_updates(self):
        page = UsagePage()
        devices = [{'name': 'wlan0', 'kind': 'Wi-Fi', 'connected': True},
                   {'name': 'eth0', 'kind': 'Ethernet / virtual', 'connected': True}]
        session = {'id': 's42000', 'pid': 123, 'interface': 'wlan0', 'fallback': 'eth0', 'active': 'eth0',
                   'automatic': True, 'traffic': {'routed': {'bytes': 2048}, 'blocked': {'packets': 0}}}
        page.update_routes([session], devices, 'eth0', lambda _: 'Test browser')
        self.assertEqual([page.table.item(0, c).text() for c in range(6)],
                         ['Test browser', 'LAN', 'Wi-Fi', 'LAN', '2.0 KB', 'Automatic'])
        session['active'] = None
        page.update_routes([session], devices, 'eth0', lambda _: 'Test browser')
        self.assertEqual(page.table.item(0, 1).text(), 'Waiting')
        page.update_routes([], devices, 'eth0', lambda _: '')
        self.assertEqual(page.table.rowCount(), 0)
        self.assertFalse(page.empty.isHidden())
        self.assertTrue(page.table.isHidden())
        page.deleteLater()

    def test_outgoing_bytes_format(self):
        self.assertEqual(bytes_text(0), '0 B')
        self.assertEqual(bytes_text(1024 * 1024), '1.0 MB')
