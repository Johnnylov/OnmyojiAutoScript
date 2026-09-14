"""Offline real activity lifecycle with obscured, incomplete dispatch maps.

No Device or user Config is created. Navigation and battles are simulated;
the activity lifecycle, daily dispatch worker and completion path are real.
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import ActivityPreparationTimeout, TaskEnd
from tasks.ActivityShikigami import base_act
from tasks.ActivityShikigami.activities import normal
from tasks.ActivityShikigami.config import ActivityShikigami
from tasks.ActivityShikigami.dispatch import DailyDispatcher, DispatchError
from tasks.ActivityShikigami.dispatch_view import DispatchObservation


DAY = date(2026, 9, 14)


class Page:
    def __init__(self, key):
        self.key = key
        self.recognizer = object()
        self.edges = []

    def connect(self, destination, action, **kwargs):
        self.edges.append(destination)

    def add_enter_failure_hooks(self, *hooks):
        pass


class OfflineClimb(normal.NormalClimbAct):
    @property
    def act_page_handle_dict(self):
        return self.offline_handlers


class PartialDispatchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.workers = []
        self.observation = DispatchObservation(kind='map', locked=3, uncertain=1)
        self.pages = SimpleNamespace(
            special_act_Flag=True, Page=Page,
            any_of=lambda *args: args,
            conditional_action=lambda **kwargs: kwargs,
            **{name: Page(name) for name in ('page_act', 'page_act_map', 'page_act_pass',
                                           'page_act_ap', 'page_battle_result', 'page_main')})
        self.task = object.__new__(OfflineClimb)
        self.task.run_idx = 0
        self.task.conf = ActivityShikigami()
        climb = self.task.conf.general_climb
        climb.run_sequence = 'pass,ap'
        climb.pass_limit = climb.ap_limit = 1
        climb.active_souls_clean = climb.auto_select_souls = climb.auto_color_hyakki = False
        self.task.config = SimpleNamespace(config_name='offline', model=SimpleNamespace(
            activity_shikigami=self.task.conf,
            restart=SimpleNamespace(login_character_config=SimpleNamespace(character='role1'))), save=Mock())
        self.task.navigator = SimpleNamespace(resolve_page=lambda page: page)
        self.task._daily_dispatch_view = SimpleNamespace(observe=lambda image: image)
        self.task._daily_coloring_view = SimpleNamespace(find_page=lambda image: None)
        self.task.screenshot = Mock(side_effect=self.capture)
        self.task.click = Mock()
        self.task.goto_page = Mock(side_effect=self.goto_page)
        self.task.lock_team = Mock()
        self.task.update_status = Mock(side_effect=self.update_status)
        self.task.get_current_page = lambda: self.current_page
        self.completed_battles = set()
        self.task.offline_handlers = {
            self.pages.page_act_pass: lambda: self.battle('pass'),
            self.pages.page_act_ap: lambda: self.battle('ap'),
        }
        self.task.set_next_run = Mock(side_effect=lambda **kwargs: self.events.append('schedule'))
        self.current_page = self.pages.page_act_map
        self.factory = Mock(side_effect=self.dispatcher)
        for target, value in ((normal, self.pages), (base_act, self.pages)):
            patcher = patch.object(target, 'pages', value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in (normal, base_act):
            patcher = patch.object(target, 'logger', Mock())
            patcher.start()
            self.addCleanup(patcher.stop)
        for attribute, value in (('server_date', lambda: DAY), ('DailyDispatcher', self.factory)):
            patcher = patch.object(normal, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def capture(self):
        if self.task.screenshot.call_count > 100:
            raise AssertionError('Activity preparation exceeded its bounded screenshot budget')
        return self.observation

    def dispatcher(self, capture, click, view):
        worker = DailyDispatcher(capture, click, view=view, sleep=lambda seconds: None)
        self.workers.append(worker)
        return worker

    def goto_page(self, page):
        self.events.append(page.key)
        self.current_page = page

    def update_status(self):
        if self.task.climb_type in self.completed_battles:
            raise base_act.LimitCountOut()

    def battle(self, climb_type):
        self.completed_battles.add(climb_type)
        self.events.append(f'battle_{climb_type}')

    def assert_climbed_and_scheduled(self, map_visits=1):
        with self.assertRaises(TaskEnd):
            self.task.run()
        self.assertEqual(self.events, ['page_act_map'] * map_visits +
                         ['page_act_pass', 'battle_pass', 'page_act_ap', 'battle_ap', 'page_main', 'schedule'])
        self.task.set_next_run.assert_called_once_with(task='ActivityShikigami', success=True)
        self.task.config.save.assert_not_called()
        record = self.task.conf.daily_dispatch_record
        self.assertEqual((record.date, record.owner), ('', ''))

    def test_partial_map_does_not_block_battles_or_successful_completion(self):
        self.assertFalse(self.observation.all_slots_known)
        self.assert_climbed_and_scheduled()
        self.task.click.assert_not_called()
        self.assertEqual(len(self.workers), 1)

    def test_real_obscured_map_snapshot_completes_climbing_without_dispatch_clicks(self):
        import cv2
        from tasks.ActivityShikigami.dispatch_view import DispatchView

        snapshot = (Path(__file__).resolve().parents[1] / 'log/error/oas2_1789363479556'
                    / '2026-09-14_13-24-36-529033.png')
        if not snapshot.is_file():
            self.skipTest('Private error snapshot is not present in this checkout')
        image = cv2.imread(str(snapshot))
        self.assertIsNotNone(image)
        self.observation = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        self.task._daily_dispatch_view = DispatchView(read_text=Mock(return_value=''))
        self.assert_climbed_and_scheduled()
        self.task.click.assert_not_called()
        self.assertEqual(len(self.workers), 1)

    def test_partial_map_with_visible_empty_slot_does_not_guess_or_deploy(self):
        self.observation = DispatchObservation(kind='map', empty=((100, 100, 20, 20),), locked=2,
                                               uncertain=1)
        self.assert_climbed_and_scheduled()
        self.task.click.assert_not_called()

    def test_incomplete_check_can_be_retried_later_the_same_day(self):
        self.task._dispatch_once_today()
        self.task._dispatch_once_today()
        self.assertEqual(len(self.workers), 2)
        self.task.config.save.assert_not_called()
        self.task.click.assert_not_called()
        self.assertEqual(self.task.conf.daily_dispatch_record.date, '')

    def test_submit_followed_by_obscured_map_continues_without_resubmitting_or_daily_record(self):
        self.observation = DispatchObservation(kind='map', empty=((100, 100, 20, 20),), locked=3)
        portrait = (200, 500, 20, 20)

        def click(rule):
            if rule.name == 'dispatch_slot':
                self.observation = DispatchObservation(kind='portraits', available=((0, portrait),),
                                                       close_roi=(500, 440, 20, 20))
            elif rule.name == 'dispatch_portrait':
                self.observation = DispatchObservation(kind='setup', available=((0, portrait),),
                                                       selected=0, current=9, maximum=9,
                                                       submit_roi=(800, 400, 50, 25),
                                                       close_roi=(500, 440, 20, 20))
            elif rule.name == 'dispatch_submit':
                self.observation = DispatchObservation(kind='map', locked=3, uncertain=1)
            else:
                self.fail(f'Unexpected extra dispatch action: {rule.name}')

        self.task.click.side_effect = click
        self.assert_climbed_and_scheduled()
        self.assertEqual([call.args[0].name for call in self.task.click.call_args_list],
                         ['dispatch_slot', 'dispatch_portrait', 'dispatch_submit'])

    def test_dispatch_error_already_recovered_to_map_does_not_block_climbing(self):
        worker = Mock(run=Mock(side_effect=DispatchError('Unconfirmed dispatch; drawer closed')))
        self.factory.side_effect = None
        self.factory.return_value = worker
        self.assert_climbed_and_scheduled(map_visits=2)
        worker.restore_map.assert_not_called()
        self.task.click.assert_not_called()

    def test_unknown_popup_after_dispatch_error_still_requires_preparation_retry(self):
        def fail():
            self.observation = DispatchObservation()
            raise DispatchError('Map could not be recovered')

        self.factory.side_effect = None
        self.factory.return_value = Mock(run=Mock(side_effect=fail), restore_map=Mock(return_value=False))
        with self.assertRaises(ActivityPreparationTimeout):
            self.task.run()
        self.assertEqual(self.events, ['page_act_map'])
        self.assertEqual(self.completed_battles, set())
        self.task.set_next_run.assert_not_called()
        self.task.config.save.assert_not_called()
        self.task.click.assert_not_called()

    def test_unknown_initial_map_does_not_start_dispatch_or_climbing(self):
        self.observation = DispatchObservation()
        with self.assertRaises(ActivityPreparationTimeout):
            self.task.run()
        self.assertEqual(self.events, ['page_act_map'])
        self.factory.assert_not_called()
        self.assertEqual(self.completed_battles, set())
        self.task.set_next_run.assert_not_called()
        self.task.config.save.assert_not_called()
        self.task.click.assert_not_called()

    def test_interrupted_popup_that_cannot_close_blocks_battle_success(self):
        self.observation = DispatchObservation(kind='success', dismiss_roi=(520, 180, 25, 22))
        with self.assertRaises(ActivityPreparationTimeout):
            self.task.run()
        self.assertEqual(self.events, [])
        self.assertEqual(self.completed_battles, set())
        self.task.set_next_run.assert_not_called()
        self.task.config.save.assert_not_called()
        self.assertEqual(self.task.click.call_count, 1)
        self.assertEqual(self.task.click.call_args.args[0].name, 'dispatch_success_close')


if __name__ == '__main__':
    unittest.main()
