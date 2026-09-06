"""Offline window regressions: no real show/hide/foreground/game operations."""

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from module.device import window_selector as selector
from test_mystery_shop_scroll import function_from_file


def device(**overrides):
    fields = dict(hwnd=1181904, title='MuMu模拟器', pid=50204,
                  process='MuMuNxDevice.exe', visible=True, iconic=False,
                  children=('MuMuNxDevice', 'nemudisplay'), ports=(5555, 7555, 16384))
    fields.update(overrides)
    return selector.EmulatorWindow(**fields)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.first = device()
        self.second = device(hwnd=3214686, title='MuMu模拟器-xiao', pid=47396,
                             ports=(5557, 7555, 16416))
        self.manager = device(hwnd=133174, pid=19664, process='MuMuNxMain.exe',
                              children=('MuMu模拟器',), ports=())
        self.windows = [self.manager, self.first, self.second]

    def choose(self, name='MuMu模拟器', serial='127.0.0.1:16384', windows=None):
        return selector.select_emulator_window(
            self.windows if windows is None else windows, name, serial)

    def test_real_manager_collision_and_both_accounts(self):
        self.assertEqual(self.choose(), self.first)
        self.assertEqual(self.choose(serial='127.0.0.1:16416'), self.second)

    def test_enumeration_order_does_not_choose_account(self):
        from itertools import permutations
        for windows in permutations(self.windows):
            self.assertEqual(self.choose(windows=windows), self.first)
            self.assertEqual(self.choose(windows=windows, serial='127.0.0.1:16416'), self.second)

    def test_manager_only_is_not_a_fallback(self):
        self.assertIsNone(self.choose(windows=[self.manager]))

    def test_hidden_manager_rejected_even_with_render_child(self):
        window = replace(self.manager, visible=False, children=('MuMuNxDevice',), ports=(16384,))
        self.assertIsNone(self.choose(windows=[window]))

    def test_helpers_owned_and_tool_windows_are_rejected(self):
        for window in [device(owner=12), device(tool=True), device(children=()),
                       device(process='MuMuNxService.exe')]:
            with self.subTest(window=window):
                self.assertIsNone(self.choose(windows=[window]))

    def test_empty_name_never_matches_everything(self):
        for name in ['', None, '   ']:
            self.assertIsNone(self.choose(name=name))

    def test_unknown_remote_or_mismatched_serial_does_not_fall_back(self):
        for serial in ['127.0.0.1:16448', '192.168.0.9:16384', 'bad', '127.0.0.1:0']:
            self.assertIsNone(self.choose(serial=serial))

    def test_auto_name_uses_serial_and_excludes_manager(self):
        self.assertEqual(self.choose(name='auto', serial='127.0.0.1:16416'), self.second)

    def test_auto_serial_with_multiple_instances_is_ambiguous(self):
        for serial in ['', 'auto']:
            self.assertIsNone(self.choose(serial=serial))

    def test_numeric_handle_is_validated_against_serial_and_process(self):
        self.assertEqual(self.choose(name=str(self.first.hwnd)), self.first)
        self.assertIsNone(self.choose(name=str(self.second.hwnd)))
        self.assertIsNone(self.choose(name=str(self.manager.hwnd)))
        self.assertIsNone(self.choose(name='9999999'))

    def test_verified_hidden_game_can_be_restored(self):
        hidden = replace(self.first, visible=False)
        self.assertEqual(self.choose(windows=[hidden]), hidden)
        self.assertIsNone(self.choose(windows=[hidden], serial='auto'))

    def test_generic_hidden_helper_is_not_selected(self):
        self.assertIsNone(self.choose(windows=[device(process='other.exe', visible=False)]))

    def test_duplicate_instances_with_same_port_fail_closed(self):
        duplicate = replace(self.first, hwnd=333, pid=444)
        self.assertIsNone(self.choose(windows=[self.first, duplicate]))

    def test_shared_compatibility_port_does_not_prefer_exact_title(self):
        self.assertIsNone(self.choose(serial='127.0.0.1:7555'))

    def test_missing_port_metadata_is_not_guessed_from_title(self):
        self.assertIsNone(self.choose(windows=[replace(self.first, ports=())]))

    def test_minimized_game_remains_selectable(self):
        minimized = replace(self.first, iconic=True)
        self.assertEqual(self.choose(windows=[minimized]), minimized)

    def test_non_mumu_unique_title_still_works(self):
        window = device(process='dnplayer.exe', title='雷电模拟器', children=('TheRender',), ports=())
        self.assertEqual(self.choose(name='雷电模拟器', windows=[window]), window)

    def test_supported_local_adb_aliases(self):
        self.assertEqual(self.choose(serial='localhost:16384'), self.first)
        self.assertEqual(self.choose(serial='emulator-5554'), self.first)


class InstanceMetadataTests(unittest.TestCase):
    marker = 'ShellWindowReveiceMessageWndMuMuPlayer-12.0-1'
    exe = str(ROOT / 'fake-mumu/nx_device/12.0/shell/MuMuNxDevice.exe')

    def test_reads_namespaced_actual_ports_not_formula(self):
        xml = '<root xmlns="urn:mumu"><Forwarding hostport="19001" guestport="5555" proto="1"/>' \
              '<Forwarding hostport="5557" guestport="5555"/>' \
              '<Forwarding hostport="123" guestport="9999"/>' \
              '<Forwarding hostport="124" guestport="5555" proto="2"/></root>'
        with patch.object(Path, 'is_file', return_value=True), \
                patch.object(selector.ET, 'parse', return_value=ET.ElementTree(ET.fromstring(xml))) as read:
            self.assertEqual(selector.instance_ports(self.exe, [self.marker]), (5557, 19001))
            self.assertEqual(read.call_args.args[0].name, 'MuMuPlayer-12.0-1.nemu')

    def test_missing_or_conflicting_pid_markers_fail_closed(self):
        for markers in [[], ['unrelated'], [self.marker, self.marker.replace('-1', '-0')]]:
            with patch.object(selector.ET, 'parse') as read:
                self.assertEqual(selector.instance_ports(self.exe, markers), ())
                read.assert_not_called()

    def test_missing_instance_file_never_infers_default_port(self):
        with patch.object(Path, 'is_file', return_value=False):
            self.assertEqual(selector.instance_ports(self.exe, [self.marker]), ())

    def test_unreadable_and_corrupt_instance_file_fail_closed(self):
        for exc in [OSError('denied'), ET.ParseError('broken')]:
            with patch.object(Path, 'is_file', return_value=True), \
                    patch.object(selector.ET, 'parse', side_effect=exc):
                self.assertEqual(selector.instance_ports(self.exe, [self.marker]), ())


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.window = device()
        self.gui = SimpleNamespace(
            error=RuntimeError, IsWindow=Mock(return_value=True),
            GetWindowText=Mock(return_value=self.window.title),
            IsIconic=Mock(return_value=False), IsWindowVisible=Mock(return_value=True),
            ShowWindow=Mock(), SetForegroundWindow=Mock())
        self.process = SimpleNamespace(GetWindowThreadProcessId=Mock(return_value=(1, self.window.pid)))
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {'win32gui': self.gui, 'win32process': self.process}).start()
        self.resolve = patch.object(selector, 'resolve_emulator_window', return_value=self.window).start()

    def action(self, action='show', **kwargs):
        return selector.change_emulator_window('MuMu模拟器', '127.0.0.1:16384', action, **kwargs)

    def test_show_only_selected_window(self):
        self.assertTrue(self.action())
        self.gui.ShowWindow.assert_called_once_with(self.window.hwnd, 5)
        self.gui.SetForegroundWindow.assert_called_once_with(self.window.hwnd)

    def test_minimized_window_is_restored(self):
        self.gui.IsIconic.return_value = True
        self.assertTrue(self.action())
        self.gui.ShowWindow.assert_called_once_with(self.window.hwnd, 9)

    def test_hidden_minimize_is_nonactivating(self):
        self.gui.IsWindowVisible.return_value = False
        self.assertTrue(self.action('minimize'))
        self.gui.ShowWindow.assert_called_once_with(self.window.hwnd, 7)
        self.gui.SetForegroundWindow.assert_not_called()

    def test_visible_minimize_and_hide(self):
        for action, command in [('minimize', 6), ('hide', 0)]:
            self.gui.ShowWindow.reset_mock()
            self.assertTrue(self.action(action))
            self.gui.ShowWindow.assert_called_once_with(self.window.hwnd, command)
        self.gui.SetForegroundWindow.assert_not_called()

    def test_hidden_conversion_can_be_disabled(self):
        self.gui.IsWindowVisible.return_value = False
        self.assertFalse(self.action('minimize', convert_hidden=False))
        self.gui.ShowWindow.assert_not_called()

    def test_unresolved_window_has_no_side_effects(self):
        self.resolve.return_value = None
        self.assertFalse(self.action())
        self.gui.ShowWindow.assert_not_called()
        self.gui.SetForegroundWindow.assert_not_called()

    def test_disappeared_or_reused_hwnd_has_no_side_effects(self):
        self.gui.IsWindow.return_value = False
        self.assertFalse(self.action())
        self.gui.IsWindow.return_value = True
        self.process.GetWindowThreadProcessId.return_value = (1, 999)
        self.assertFalse(self.action())
        self.process.GetWindowThreadProcessId.return_value = (1, self.window.pid)
        self.gui.GetWindowText.return_value = 'different window'
        self.assertFalse(self.action())
        self.gui.ShowWindow.assert_not_called()

    def test_foreground_denial_does_not_break_scheduler(self):
        self.gui.SetForegroundWindow.side_effect = RuntimeError('not permitted')
        self.assertTrue(self.action())

    def test_window_closing_during_action_fails_safely(self):
        self.gui.ShowWindow.side_effect = RuntimeError('gone')
        self.assertFalse(self.action())


class IntegrationTests(unittest.TestCase):
    def test_scheduler_passes_account_serial_and_respects_background_flags(self):
        for windows, background, minimized, action in [
                (True, False, False, 'show'), (True, False, True, 'minimize'),
                (True, True, False, None), (True, True, True, None),
                (False, False, False, None)]:
            api = SimpleNamespace(show_window_by_name=Mock(), minimize_by_name=Mock())
            date = SimpleNamespace(today=Mock(side_effect=[0, RuntimeError('stop before tasks')]))
            namespace = dict(_log_switch_lock=MagicMock(), logger=Mock(), date=date, IS_WINDOWS=windows)
            loop = function_from_file(ROOT / 'script.py', 'loop', namespace, 'Script')
            obj = SimpleNamespace(config_name='offline', anti_ban_guard=Mock(),
                                  config=SimpleNamespace(model=SimpleNamespace(), script=SimpleNamespace(
                                      device=SimpleNamespace(handle='MuMu模拟器', serial='127.0.0.1:16416',
                                                             run_background_only=background,
                                                             emulator_window_minimize=minimized))))
            with patch.dict(sys.modules, {'module.device.platform2.platform_windows': api}), \
                    self.assertRaisesRegex(RuntimeError, 'stop before tasks'):
                loop(obj)
            if action == 'show':
                api.show_window_by_name.assert_called_once_with('MuMu模拟器', serial='127.0.0.1:16416')
                api.minimize_by_name.assert_not_called()
            elif action == 'minimize':
                api.minimize_by_name.assert_called_once_with('MuMu模拟器', serial='127.0.0.1:16416')
                api.show_window_by_name.assert_not_called()
            else:
                api.show_window_by_name.assert_not_called()
                api.minimize_by_name.assert_not_called()

    def test_start_watch_hides_only_confirmed_instance_and_only_once(self):
        action = Mock(return_value=False)
        namespace = dict(change_emulator_window=action)
        hide = function_from_file(ROOT / 'module/device/platform2/platform_windows.py',
                                  '_hide_emulator_window_if_needed', namespace, 'PlatformWindows')
        obj = SimpleNamespace(config=SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(
            handle='MuMu模拟器', run_background_only=True))), _log_emulator_watch_once=Mock())
        state = SimpleNamespace(serial='127.0.0.1:16416', window_hidden=False)
        hide(obj, state)
        self.assertFalse(state.window_hidden)
        action.return_value = True
        hide(obj, state)
        self.assertTrue(state.window_hidden)
        action.assert_called_with('MuMu模拟器', '127.0.0.1:16416', 'hide')
        action.reset_mock()
        hide(obj, state)
        action.assert_not_called()
        state.window_hidden = False
        obj.config.script.device.run_background_only = False
        hide(obj, state)
        action.assert_not_called()

    def test_finalize_respects_background_and_passes_instance_serial(self):
        minimize = Mock()
        namespace = dict(logger=Mock(), Timer=Mock(), minimize_by_name=minimize)
        finalize = function_from_file(ROOT / 'module/device/platform2/platform_windows.py',
                                      '_finalize_emulator_window', namespace, 'PlatformWindows')
        cfg = SimpleNamespace(handle='MuMu模拟器', emulator_window_minimize=True, run_background_only=False)
        obj = SimpleNamespace(config=SimpleNamespace(script=SimpleNamespace(device=cfg)),
                              _log_emulator_watch_once=Mock())
        state = SimpleNamespace(serial='127.0.0.1:16416', window_hidden=True)
        finalize(obj, state)
        minimize.assert_called_once_with('MuMu模拟器', serial='127.0.0.1:16416')
        minimize.reset_mock()
        cfg.run_background_only = True
        finalize(obj, state)
        minimize.assert_not_called()

    def test_platform_wrappers_forward_serial(self):
        source = ROOT / 'module/device/platform2/platform_windows.py'
        change = Mock(return_value=True)
        namespace = dict(change_emulator_window=change)
        show = function_from_file(source, 'show_window_by_name', namespace)
        minimize = function_from_file(source, 'minimize_by_name', namespace)
        show('MuMu模拟器', serial='127.0.0.1:16416')
        change.assert_called_with('MuMu模拟器', '127.0.0.1:16416', 'show')
        minimize('MuMu模拟器', serial='127.0.0.1:16384')
        change.assert_called_with('MuMu模拟器', '127.0.0.1:16384', 'minimize', True)

    def test_handle_initialization_uses_shared_resolver(self):
        from anytree import NodeMixin, RenderTree
        class Node(NodeMixin):
            def __init__(self, name, num):
                self.name, self.num = name, num
        config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(
            handle='MuMu模拟器', serial='127.0.0.1:16416',
            screenshot_method='ADB_nc', control_method='minitouch')))
        chosen = device(hwnd=3214686, title='MuMu模拟器-xiao', ports=(16416,))
        resolver = Mock(return_value=chosen)
        namespace = dict(logger=Mock(), Config=type('Config', (), {}),
                         resolve_emulator_window=resolver, WindowNode=Node,
                         RenderTree=RenderTree, window_scale_rate=lambda: 1.25,
                         RequestHumanTakeover=RuntimeError)
        init = function_from_file(ROOT / 'module/device/handle.py', '__init__', namespace, 'Handle')
        handle = SimpleNamespace(config=config, emulator_family='MuMu',
                                 screenshot_handle_num=7, screenshot_size=(1280, 720))
        namespace['Handle'] = SimpleNamespace(handle_tree=Mock())
        init(handle, config)
        resolver.assert_called_once_with('MuMu模拟器', '127.0.0.1:16416')
        self.assertEqual(handle.root_handle_num, 3214686)
        self.assertEqual(handle.root_handle_title, 'MuMu模拟器-xiao')
        namespace['Handle'].handle_tree.assert_called_once()

    def test_handle_failure_never_enumerates_desktop(self):
        namespace = dict(logger=Mock(), resolve_emulator_window=Mock(return_value=None),
                         Handle=SimpleNamespace(handle_tree=Mock()), RequestHumanTakeover=RuntimeError)
        init = function_from_file(ROOT / 'module/device/handle.py', '__init__', namespace, 'Handle')
        cfg = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(
            handle='MuMu模拟器', serial='127.0.0.1:16384',
            screenshot_method='ADB_nc', control_method='minitouch')))
        handle = SimpleNamespace(config=cfg)
        init(handle, cfg)
        self.assertEqual(handle.root_handle_num, 0)
        namespace['Handle'].handle_tree.assert_not_called()
        for field, value in [('screenshot_method', 'window_background'), ('control_method', 'window_message')]:
            setattr(cfg.script.device, field, value)
            with self.assertRaises(RuntimeError):
                init(handle, cfg)
            cfg.script.device.screenshot_method = 'ADB_nc'


if __name__ == '__main__':
    unittest.main()
