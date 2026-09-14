"""Execute the real NormalClimbAct setup against an isolated page graph."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def setup_class(namespace):
    path = ROOT / 'tasks/ActivityShikigami/activities/normal.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                    and node.name == 'NormalClimbAct')
    before = next(node for node in original.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'before_run')
    subject = ast.ClassDef(name='Subject', bases=[ast.Name(id='BaseAct', ctx=ast.Load())],
                           keywords=[], body=[before], decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[subject], type_ignores=[])),
                 str(path), 'exec'), namespace)
    return namespace['Subject']


class Page:
    def __init__(self, key):
        self.key = key
        self.edges = []
        self.hooks = []

    def connect(self, destination, action, **kwargs):
        self.edges.append((destination.key, action, kwargs['key']))

    def add_enter_failure_hooks(self, *hooks):
        self.hooks.extend(hooks)


class SetupWorld:
    def __init__(self, soul_panel=False, auto_souls=True, special=True, event_active=True):
        self.events = []
        self.soul_panel = soul_panel
        self.pages = SimpleNamespace(
            special_act_Flag=special,
            conditional_action=lambda **kwargs: kwargs,
            **{name: Page(name) for name in
               ('page_act', 'page_act_map', 'page_act_ap', 'page_act_pass')})
        self.local = {page.key: Page(page.key) for page in vars(self.pages).values()
                      if isinstance(page, Page)}
        world = self

        class BaseAct:
            def before_run(self):
                world.events.append('base_setup')

        assets = SimpleNamespace(**{name: name for name in (
            'I_MAP_GOTO_BATTLE', 'I_TO_BATTLE_MAIN', 'I_CLIMB_MODE_PASS',
            'I_CLIMB_MODE_AP', 'I_CLIMB_MODE_SWITCH')})
        namespace = dict(BaseAct=BaseAct, pages=self.pages, ActivityShikigamiAssets=assets,
                         server_date=lambda: 'today',
                         recommendations=lambda day: (1, 4, 10, 13) if event_active else ())
        self.task = setup_class(namespace)()
        self.task.navigator = SimpleNamespace(resolve_page=lambda page: self.local[page.key])
        self.task.conf = SimpleNamespace(general_climb=SimpleNamespace(auto_select_souls=auto_souls))
        self.task.screenshot = Mock(side_effect=self.screenshot)
        self.task._soul_selection_view = SimpleNamespace(
            find_panel=Mock(side_effect=lambda image: object() if image == 'soul_panel' else None))
        self.task._select_daily_souls = Mock(side_effect=self.select_souls)
        self.task._restore_daily_activity_map = Mock(side_effect=self.restore)
        self.task._dispatch_once_today = Mock(side_effect=self.dispatch)

    def screenshot(self):
        self.events.append('capture')
        return 'soul_panel' if self.soul_panel else 'map'

    def select_souls(self, force):
        if not force:
            raise AssertionError('Interrupted selection must be recovered with force=True')
        self.events.append('souls_verified')
        self.soul_panel = False

    def restore(self):
        self.events.append('restore')

    def dispatch(self):
        if self.soul_panel:
            raise AssertionError('Attempted dispatch while a soul-selection panel is open')
        origin = 'page_act_map' if self.pages.special_act_Flag else 'page_act'
        destinations = {edge[0] for edge in self.local[origin].edges}
        if not {'page_act_ap', 'page_act_pass'} <= destinations:
            raise AssertionError('Dispatch started before climb routes were installed')
        self.events.append('dispatch')


class DailySetupTests(unittest.TestCase):
    def test_interrupted_soul_panel_is_verified_before_restore_and_dispatch(self):
        world = SetupWorld(soul_panel=True)
        world.task.before_run()
        self.assertEqual(world.events,
                         ['base_setup', 'capture', 'souls_verified', 'restore', 'dispatch'])
        world.task._select_daily_souls.assert_called_once_with(force=True)
        self.assertFalse(world.soul_panel)

    def test_selection_failure_prevents_dispatch_and_unknown_page_recovery(self):
        world = SetupWorld(soul_panel=True)
        world.task._select_daily_souls.side_effect = RuntimeError('selection not confirmed')
        with self.assertRaisesRegex(RuntimeError, 'selection not confirmed'):
            world.task.before_run()
        world.task._restore_daily_activity_map.assert_not_called()
        world.task._dispatch_once_today.assert_not_called()
        self.assertTrue(world.soul_panel)

    def test_normal_map_does_not_force_an_unneeded_soul_selection(self):
        world = SetupWorld()
        world.task.before_run()
        world.task._select_daily_souls.assert_not_called()
        self.assertEqual(world.events, ['base_setup', 'capture', 'restore', 'dispatch'])

    def test_soul_option_off_or_event_inactive_keeps_setup_and_dispatch_hooks(self):
        for options in ({'auto_souls': False}, {'event_active': False}):
            with self.subTest(options=options):
                world = SetupWorld(**options)
                world.task.before_run()
                world.task._select_daily_souls.assert_not_called()
                world.task.screenshot.assert_not_called()
                self.assertEqual(world.events, ['base_setup', 'restore', 'dispatch'])

    def test_graph_changes_are_local_and_include_mode_recovery_before_dispatch(self):
        world = SetupWorld()
        world.task.before_run()
        for name in ('page_act', 'page_act_map', 'page_act_ap', 'page_act_pass'):
            self.assertEqual(getattr(world.pages, name).edges, [])
            self.assertEqual(getattr(world.pages, name).hooks, [])
        self.assertEqual(len(world.local['page_act_ap'].hooks), 1)
        self.assertEqual(len(world.local['page_act_pass'].hooks), 1)
        self.assertEqual(world.local['page_act_ap'].edges[0][0], 'page_act_pass')
        self.assertEqual(world.local['page_act_pass'].edges[0][0], 'page_act_ap')

    def test_non_map_event_keeps_direct_routes_without_touching_map_routes(self):
        world = SetupWorld(special=False)
        world.task.before_run()
        self.assertEqual(world.local['page_act_map'].edges, [])
        self.assertEqual({edge[0] for edge in world.local['page_act'].edges},
                         {'page_act_pass', 'page_act_ap'})


if __name__ == '__main__':
    unittest.main()
