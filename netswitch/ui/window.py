import json
import shlex
import re
from pathlib import Path
from PyQt6.QtCore import QProcess, QSettings, QTimer, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                            QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                            QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QMenu,
                            QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
from ..apps import app_command, installed_apps, network_name, require_browser_closed
from ..automatic import executable_path
import shutil
from ..core import interfaces, load_profiles, request, update_profile
from .dialogs import AppPicker
from .widgets import label, panel
from .icons import app_icon
from .usage import UsagePage

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Net Switch')
        self.setWindowIcon(app_icon())
        self.resize(1040, 740)
        self.setMinimumSize(880, 640)
        self.processes, self.devices, self.network_buttons = [], [], {}
        self.app_choice = None
        self.loaded_profile = None
        self.service_ready = False
        self.default_failover = {}
        self.auto_available = False
        self.auto_error = None
        self.settings = QSettings('NetSwitch', 'Desktop')
        self.session_labels = self.settings.value('session_names', {}) or {}
        self.steam_names = {app['steam_id']: app['name'] for app in installed_apps() if app.get('steam_id')}
        from .layout import build_window
        build_window(self)
        self.refresh()
        self.refresh_profiles()
        self.show_usage()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)

    def friendly(self, name):
        return next((network_name(d) for d in self.devices if d['name'] == name), name or 'No connection')

    def choose_app(self):
        picker = AppPicker(self)
        if picker.exec() == QDialog.DialogCode.Accepted:
            self.loaded_profile = None
            self.profiles.clearSelection()
            self.set_app(picker.selected_app)
            self.automatic.setChecked(self.automatic.isEnabled())

    def set_app(self, app):
        self.show_apps()
        self.app_choice = dict(app)
        self.remove_app.setVisible(bool(self.loaded_profile))
        self.app_title.setText(app['name'])
        self.breadcrumb.setText(f"Apps  /  {app['name']}")
        self.app_icon.setPixmap(QIcon.fromTheme(app.get('icon', ''), QIcon.fromTheme('application-x-executable')).pixmap(32, 32))
        self.connection_changed()
        self.update_automatic()

    def show_usage(self):
        self.pages.setCurrentWidget(self.usage)
        self.breadcrumb.setText('Overview')
        self.overview_button.setChecked(True)
        self.refresh()

    def show_apps(self):
        self.pages.setCurrentIndex(0)
        self.overview_button.setChecked(False)
        self.breadcrumb.setText(f"Apps  /  {self.app_choice['name']}" if self.app_choice else 'Apps  /  New app')

    def update_automatic(self):
        reason = ''
        if not self.app_choice:
            reason = 'Choose an app using Browse apps to enable this option.'
        elif self.app_choice.get('steam_id'):
            reason = 'Steam games need the per-game launch option. Click Set up in Steam below.'
        else:
            try:
                command = shlex.split(self.command.text())
                executable_path(shutil.which(command[0]) or command[0])
            except IndexError:
                reason = 'Choose an app or enter its launch command first.'
            except ValueError as error:
                reason = str(error)
            except OSError:
                reason = 'The app executable could not be found. Choose an installed app using Browse apps.'
            if not reason and not self.auto_available:
                reason = self.auto_error or 'Automatic routing is unavailable. Update the routing service, then reopen Net Switch.'
        self.automatic.setEnabled(not reason)
        steam = bool(self.app_choice and self.app_choice.get('steam_id'))
        self.automatic.setVisible(not steam)
        self.behavior_hint.setVisible(not reason)
        self.behavior_hint.setText('Save once, then open the app normally.' if self.automatic.isChecked() else 'Launch through Net Switch to apply this rule.')
        self.automatic_reason.setText(reason)
        self.automatic_reason.setVisible(bool(reason))
        self.automatic.setToolTip(reason or 'Save once, then open normally. Restart an already-open app to apply its rule.')

    def toggle_advanced(self, visible):
        self.advanced.setVisible(visible)
        self.advanced_toggle.setText('Advanced options  ⌄' if visible else 'Advanced options  ›')

    def network_clicked(self, button):
        self.interface.setCurrentIndex(self.interface.findData(button.property('interface')))

    def connection_changed(self):
        name = self.interface.currentData()
        self.network_group.setExclusive(False)
        for device, button in self.network_buttons.items():
            button.setChecked(device == name)
        self.network_group.setExclusive(True)
        if self.app_choice:
            command, browser = app_command(self.app_choice, name or 'default')
            self.command.setText(shlex.join(command))
            self.app_hint.setText('Steam game · Uses a per-game launch option.' if self.app_choice.get('steam_id') else
                                  'Uses your usual bookmarks and logins. Close the browser before launching.' if browser else
                                  'If this app is already open, close it before launching here.')
        self.update_hint()

    def custom_command(self):
        if self.app_choice:
            try:
                command = shlex.split(self.command.text())
            except ValueError:
                return  # Quotes may be incomplete while the user is typing.
            self.app_choice = {**self.app_choice, 'command': command, 'custom': True, 'steam_id': None}
        self.update_hint()
        self.update_automatic()

    def backup_interface(self):
        return next((d['name'] for d in self.devices if d['name'] != self.interface.currentData()
                     and (d['kind'] == 'Wi-Fi' or d['name'].startswith(('en', 'eth')))), None) if self.fallback.isChecked() else None

    def update_hint(self):
        preferred, backup = self.friendly(self.interface.currentData()), self.backup_interface()
        self.route_hint.setText(f'{preferred} first. {self.friendly(backup)} takes over if its internet stops working.' if backup else
                                f'Only this app uses {preferred}. Your other apps stay on their usual network.')
        self.launch_button.setText('Set up in Steam' if self.app_choice and self.app_choice.get('steam_id') else 'Launch app')
        self.launch_button.setEnabled(bool(self.app_choice and self.interface.currentData() and self.service_ready))

    def refresh_profiles(self, selection=None):
        self.profiles.blockSignals(True)
        self.profiles.clear()
        for name, profile in load_profiles().items():
            if not profile.get('command'):
                continue
            app = profile.get('app', {})
            item = QListWidgetItem(QIcon.fromTheme(app.get('icon', 'application-x-executable')), app.get('name', name))
            item.setToolTip(f"Uses {self.friendly(profile['interface'])}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.profiles.addItem(item)
            if selection == name:
                self.profiles.setCurrentItem(item)
        self.profiles.blockSignals(False)
        self.saved_empty.setVisible(self.profiles.count() == 0)

    def select(self, item, _=None):
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        profile = load_profiles().get(name)
        if profile:
            self.loaded_profile = name
            self.interface.setCurrentIndex(self.interface.findData(profile['interface']))
            self.fallback.setChecked(bool(profile.get('fallback')))
            self.set_app(profile.get('app') or {'name': name, 'command': profile['command'], 'icon': '', 'custom': True})
            self.command.setText(shlex.join(profile['command']))
            self.automatic.setChecked(bool(profile.get('automatic')))
            self.update_automatic()

    def profile_data(self):
        if not self.app_choice:
            raise ValueError('Choose an app first using Browse apps.')
        command = shlex.split(self.command.text())
        if not command or not self.interface.currentData():
            raise ValueError('Choose an app and a connection first.')
        name = self.loaded_profile or f"{self.app_choice['name']} · {self.friendly(self.interface.currentData())}"
        return name, {'interface': self.interface.currentData(), 'fallback': self.backup_interface(), 'command': command, 'app': self.app_choice,
                      'automatic': self.automatic.isEnabled() and self.automatic.isChecked()}

    def save(self):
        try:
            name, profile = self.profile_data()
            if profile['automatic']:
                request({'action': 'automatic-set', 'name': name, 'executable': shutil.which(profile['command'][0]) or profile['command'][0],
                         'interface': profile['interface'], 'fallback': profile['fallback']})
            elif self.auto_available:
                request({'action': 'automatic-delete', 'name': name})
            update_profile(name, profile)
            self.loaded_profile = name
            self.remove_app.show()
            self.refresh_profiles(name)
            self.set_message(f"Saved {self.app_choice['name']}. Open it normally and its network applies automatically." if profile['automatic'] else
                                 f"Saved {self.app_choice['name']}. Open it from the sidebar next time.")
            return name
        except Exception as error:
            QMessageBox.information(self, 'Choose an app', str(error))

    def app_menu(self, position):
        item = self.profiles.itemAt(position)
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        remove = menu.addAction('Remove from Net Switch')
        remove.triggered.connect(lambda: self.delete(name))
        menu.exec(self.profiles.viewport().mapToGlobal(position))

    def delete(self, name=None):
        name = name or self.loaded_profile
        if not name:
            return
        try:
            if self.auto_available:
                request({'action': 'automatic-delete', 'name': name})
            update_profile(name, None)
        except (ValueError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, 'Could not remove app', str(error))
            return
        if name == self.loaded_profile:
            self.loaded_profile = None
            self.app_choice = None
            self.command.clear()
            self.app_title.setText('Choose an app')
            self.app_icon.setPixmap(QIcon.fromTheme('application-x-executable').pixmap(32, 32))
            self.app_hint.setText('Installed apps and games. No commands to type.')
            self.breadcrumb.setText('Apps  /  New app')
            self.fallback.setChecked(False)
            self.automatic.setChecked(False)
            self.automatic.setEnabled(False)
            self.update_automatic()
            self.advanced_toggle.setChecked(False)
            self.remove_app.hide()
            self.update_hint()
        self.refresh_profiles(self.loaded_profile)
        self.set_message('App removed from Net Switch.')

    def launch(self):
        try:
            require_browser_closed(shlex.split(self.command.text()))
        except ValueError as error:
            QMessageBox.information(self, 'Close your browser first', str(error))
            return
        name = self.save()
        if not name:
            return
        if self.app_choice.get('steam_id'):
            self.steam_setup(name)
        else:
            title = self.app_choice['name']
            self.invoke(['launch', '--detach', name], f'Opened {title} on {self.friendly(self.interface.currentData())}.', title)

    def steam_setup(self, name):
        dialog = QDialog(self)
        dialog.setWindowTitle('Connect your Steam game')
        dialog.resize(560, 320)
        content = QVBoxLayout(dialog)
        content.setContentsMargins(22, 22, 22, 22)
        content.addWidget(label('One quick setup in Steam', 'heading'))
        content.addWidget(label('1. In Steam, right-click this game → Properties.\n2. Under General, find Launch Options.\n3. Paste the line below, then launch the game in Steam.'))
        content.addWidget(label('If you already have launch options, put this before them.', 'muted'))
        command = shlex.join([str(Path.home() / '.local/bin/netswitch'), 'run', name, '--']) + ' %command%'
        field = QLineEdit(command)
        field.setReadOnly(True)
        content.addWidget(field)
        copy = QPushButton('Copy launch option')
        copy.setObjectName('primary')
        def copied():
            QApplication.clipboard().setText(command)
            copy.setText('Copied ✓')
        copy.clicked.connect(copied)
        content.addWidget(copy)
        content.addWidget(label('An already-running game changes connection after you restart it.', 'muted'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        content.addWidget(buttons)
        dialog.exec()

    def open_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Default connection')
        dialog.resize(450, 330)
        content = QVBoxLayout(dialog)
        content.setContentsMargins(22, 22, 22, 22)
        content.addWidget(label('Everything else', 'heading'))
        content.addWidget(label('Choose the connection for apps you don’t launch through Net Switch.', 'muted'))
        form = QFormLayout()
        preferred, backup = QComboBox(), QComboBox()
        devices = [d for d in self.devices if d['kind'] == 'Wi-Fi' or d['name'].startswith(('en', 'eth'))]
        devices.sort(key=lambda d: d['routes'].get('-4', {}).get('metric', 9999))
        for combo in (preferred, backup):
            for device in devices:
                combo.addItem(network_name(device), device['name'])
        backup.setCurrentIndex(1 if len(devices) > 1 else 0)
        config = self.default_failover.get('configuration') or {}
        if config.get('enabled'):
            preferred.setCurrentIndex(preferred.findData(config['preferred']))
            backup.setCurrentIndex(backup.findData(config['fallback']))
        form.addRow('Use first', preferred)
        form.addRow('Backup network', backup)
        content.addLayout(form)
        automatic = QCheckBox('Switch automatically if internet stops working')
        automatic.setChecked(bool(config.get('enabled')))
        content.addWidget(automatic)
        content.addWidget(label('Switches back once your preferred network is working again.', 'muted'))
        apply = QPushButton('Save default connection')
        apply.setObjectName('primary')
        apply.setEnabled(len(devices) > 1)
        def save_defaults():
            if preferred.currentData() == backup.currentData():
                QMessageBox.information(dialog, 'Choose a different backup', 'Select one connection as your default and the other as your backup.')
                return
            if automatic.isChecked():
                args = ['failover', 'enable', '--prefer', preferred.currentData(), '--fallback', backup.currentData()]
            else:
                args = ['priority', '--prefer', preferred.currentData(), '--fallback', backup.currentData(), '--apply']
                if config.get('enabled'):
                    args.append('--no-internet-fallback')
            self.invoke(args, 'Default connection saved.')
            dialog.accept()
        apply.clicked.connect(save_defaults)
        content.addWidget(apply)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        content.addWidget(buttons)
        dialog.exec()

    def populate_networks(self):
        selected = self.interface.currentData()
        names = [d['name'] for d in self.devices]
        if [self.interface.itemData(i) for i in range(self.interface.count())] != names:
            self.interface.blockSignals(True)
            self.interface.clear()
            for device in self.devices:
                self.interface.addItem(f"{network_name(device)} ({device['name']})", device['name'])
            self.interface.setCurrentIndex(max(0, self.interface.findData(selected)))
            self.interface.blockSignals(False)
        simple = [d for d in self.devices if d['kind'] == 'Wi-Fi' or d['name'].startswith(('en', 'eth'))]
        if set(self.network_buttons) != {d['name'] for d in simple}:
            for button in self.network_buttons.values():
                self.network_group.removeButton(button)
                self.network_row.removeWidget(button)
                button.deleteLater()
            self.network_buttons = {}
            for device in sorted(simple, key=lambda d: d['kind'] != 'Wi-Fi'):
                button = QPushButton()
                button.setObjectName('network')
                button.setCheckable(True)
                button.setMinimumHeight(72)
                button.setProperty('interface', device['name'])
                self.network_group.addButton(button)
                self.network_row.addWidget(button, 1)
                self.network_buttons[device['name']] = button
        for device in simple:
            button = self.network_buttons[device['name']]
            state = {'online': '● Internet available', 'offline': '○ No internet', 'checking': 'Checking internet…', 'disconnected': '○ Disconnected'}.get(device.get('internet'), '● Connected' if device['connected'] else '○ Disconnected')
            button.setText(f"{network_name(device)}\n{state}")
            button.setChecked(device['name'] == self.interface.currentData())
        self.network_summary.setText('\n'.join(f"{'○' if not d['connected'] or d.get('internet') == 'offline' else '●'}  {network_name(d)}{' · No internet' if d.get('internet') == 'offline' else ''}" for d in simple))
        connected = sorted((d for d in self.devices if d['connected']), key=lambda d: d['routes'].get('-4', {}).get('metric', 9999))
        active = self.default_failover.get('active')
        self.default_summary.setText(f'Default: {self.friendly(active)}' if active else f"Default: {network_name(connected[0])}" if connected else 'No connection')

    def render_sessions(self, sessions):
        self.live_sessions = sessions

    def set_message(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def session_title(self, session):
        saved = self.session_labels.get(session['id'])
        title = saved.get('title') if isinstance(saved, dict) and saved.get('pid') == session['pid'] else session.get('name')
        if not title:
            try:
                arguments = Path(f"/proc/{session['pid']}/cmdline").read_bytes().decode(errors='replace')
                match = re.search(r'AppId=(\d+)', arguments)
                if match:
                    title = self.steam_names.get(match[1])
            except OSError:
                pass
        if not title:
            try:
                title = Path(f"/proc/{session['pid']}/comm").read_text().strip()
            except OSError:
                title = 'App'
        return title

    def confirm_close(self, identifier):
        if QMessageBox.question(self, 'Close this app?', 'This closes the app and its windows. Save any work first.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.invoke(['stop', identifier], 'App closed.')

    def refresh(self):
        try:
            self.devices = interfaces()
            try:
                health = request({'action': 'health'})
                if isinstance(health, dict):
                    for device in self.devices:
                        device.update(health.get('networks', {}).get(device['name'], {}))
                    self.default_failover = health.get('defaults', {})
            except RuntimeError:
                pass
            try:
                automatic = request({'action': 'automatic-status'})
                if isinstance(automatic, dict):
                    self.auto_available = automatic.get('available', False)
                    self.auto_error = automatic.get('error') if not self.auto_available else None
            except RuntimeError as error:
                self.auto_available = False
                self.auto_error = ('Update the routing service to enable automatic routing.' if 'Unknown operation' in str(error) else
                                   'The routing service is unavailable. Start it to enable automatic routing.')
            self.update_automatic()
            self.populate_networks()
            try:
                sessions = request({'action': 'status'})
                self.service_ready = True
            except RuntimeError:
                sessions = []
                self.service_ready = False
            self.install.setVisible(not self.service_ready)
            self.render_sessions(sessions)
            connected = sorted((d for d in self.devices if d['connected']), key=lambda d: d['routes'].get('-4', {}).get('metric', 9999))
            default = self.default_failover.get('active') or (connected[0]['name'] if connected else None)
            self.usage.update_routes(sessions, self.devices, default, self.session_title, self.service_ready)
            self.update_hint()
        except Exception as error:
            self.set_message(str(error))

    def invoke(self, args, success='Done.', app_name=None):
        process = QProcess(self)
        self.processes.append(process)
        process.setProgram('/usr/bin/python')
        process.setArguments(['-m', 'netswitch.cli', '--json', *args])
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        self.set_message('Opening app…' if app_name else 'Saving…')
        def finished(code, _):
            output, errors = bytes(process.readAllStandardOutput()).decode(), bytes(process.readAllStandardError()).decode()
            if code:
                try:
                    error = json.loads(output).get('error', output)
                except ValueError:
                    error = errors or output or 'Please try again.'
                QMessageBox.warning(self, 'Could not complete that', error)
                self.set_message('Could not complete that. Please try again.')
            else:
                if app_name:
                    try:
                        session = json.loads(output)
                        self.session_labels[session['id']] = {'title': app_name, 'pid': session['pid']}
                        self.settings.setValue('session_names', self.session_labels)
                    except (ValueError, KeyError):
                        pass
                self.set_message(success)
            self.processes.remove(process)
            process.deleteLater()
            self.refresh()
        process.finished.connect(finished)
        process.start()
