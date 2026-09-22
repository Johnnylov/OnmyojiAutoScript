"""Offline lifecycle, account isolation and option tests for daily climb extras."""

from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_image_template_guard import methods
from tasks.ActivityShikigami.config import ActivityShikigami

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 9, 14)


class DispatchError(RuntimeError):
    pass


class ColoringError(RuntimeError):
    pass


class TransitionError(RuntimeError):
    pass


class PreparationError(TransitionError):
    pass


class DailyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.clock = Mock(return_value=DAY)
        self.dispatcher = Mock()
        self.dispatcher.run.return_value = SimpleNamespace(completed=True, dispatched=4, reason='done')
        self.factory = Mock(return_value=self.dispatcher)
        self.pages = SimpleNamespace(special_act_Flag=True, page_act_map='map')
        cls = methods('tasks/ActivityShikigami/activities/normal.py', 'NormalClimbAct',
                      ['_activity_owner', '_activity_read_text', '_activity_click_roi',
                       '_dispatch_once_today', '_restore_daily_activity_map', 'after_run'],
                      dict(server_date=self.clock, DailyDispatcher=self.factory, pages=self.pages,
                           DispatchError=DispatchError, ColoringError=ColoringError,
                           ActivityPreparationTimeout=PreparationError,
                           BattleTransitionTimeout=TransitionError, logger=Mock(),
                           RuleClick=lambda **kwargs: SimpleNamespace(**kwargs),
                           RuleOcr=lambda **kwargs: SimpleNamespace(ocr=lambda image: image)))
        self.task = cls()
        self.task.conf = ActivityShikigami()
        self.task.config = SimpleNamespace(config_name='a', model=SimpleNamespace(
            activity_shikigami=self.task.conf, restart=SimpleNamespace(
                login_character_config=SimpleNamespace(character='role1'))), save=Mock())
        self.task.screenshot = Mock(return_value='frame')
        self.task.click = Mock()
        self.task.goto_page = Mock(side_effect=lambda p: self.events.append(p))
        self.task._dispatch_view = Mock()
        self.task._dispatch_view.observe.return_value = SimpleNamespace(kind='map')
        self.task._coloring_view = Mock(find_page=Mock(return_value=None),
                                        find_intro=Mock(return_value=None),
                                        find_reward=Mock(return_value=None))
        self.colorer = Mock()
        self.colorer.run.return_value = SimpleNamespace(status='no_currency', submissions=2,
                                                        global_progress=54.0)
        self.task._colorer = Mock(return_value=self.colorer)

    def test_first_run_records_account_and_day_then_second_run_skips_dispatch(self):
        self.task._dispatch_once_today()
        record = self.task.config.model.activity_shikigami.daily_dispatch_record
        self.assertEqual((record.date, record.owner), (DAY.isoformat(), 'a\nrole1'))
        self.task._dispatch_once_today()
        self.dispatcher.run.assert_called_once()
        self.task.config.save.assert_called_once()
        self.assertEqual(self.events, ['map', 'map'])

    def test_all_running_slots_complete_today_without_redeploying(self):
        self.dispatcher.run.return_value = SimpleNamespace(completed=True, dispatched=0, reason='running')
        self.task._dispatch_once_today()
        self.task._dispatch_once_today()
        self.dispatcher.run.assert_called_once()
        self.task.click.assert_not_called()

    def test_record_survives_reload_and_new_day_or_other_character_runs_again(self):
        self.task._dispatch_once_today()
        conf = self.task.config.model.activity_shikigami
        self.task.config.model.activity_shikigami = ActivityShikigami.model_validate_json(conf.model_dump_json())
        self.task._dispatch_once_today()
        self.assertEqual(self.dispatcher.run.call_count, 1)
        self.clock.return_value = DAY + timedelta(days=1)
        self.task._dispatch_once_today()
        self.task.config.model.restart.login_character_config.character = 'role2'
        self.task._dispatch_once_today()
        self.task.config.config_name = 'b'
        self.task._dispatch_once_today()
        self.assertEqual(self.dispatcher.run.call_count, 4)

    def test_partial_dispatch_never_records_success_and_can_be_checked_again(self):
        self.dispatcher.run.return_value = SimpleNamespace(completed=False, dispatched=1, reason='unconfirmed')
        self.task._dispatch_once_today()
        self.task._dispatch_once_today()
        self.assertEqual(self.dispatcher.run.call_count, 2)
        self.task.config.save.assert_not_called()
        self.assertEqual(self.task.conf.daily_dispatch_record.date, '')

    def test_failed_dispatch_already_on_map_continues_without_daily_record(self):
        self.dispatcher.run.side_effect = DispatchError('unverified')
        self.task._dispatch_once_today()
        self.assertEqual(self.events, ['map', 'map'])
        self.assertEqual(self.task._dispatch_view.observe.call_count, 3)
        self.dispatcher.restore_map.assert_called_once()
        self.task.config.save.assert_not_called()
        self.assertEqual(self.task.conf.daily_dispatch_record.date, '')

    def test_failed_dispatch_without_verified_map_requests_preparation_retry(self):
        self.task._dispatch_view.observe.side_effect = [SimpleNamespace(kind='map'),
                                                       SimpleNamespace(kind='map'),
                                                       SimpleNamespace(kind='unknown')]
        self.dispatcher.run.side_effect = DispatchError('unverified')
        with self.assertRaises(PreparationError):
            self.task._dispatch_once_today()
        self.assertEqual(self.events, ['map'])
        self.assertEqual(self.task._dispatch_view.observe.call_count, 3)
        self.dispatcher.restore_map.assert_called_once()
        self.task.config.save.assert_not_called()
        self.assertEqual(self.task.conf.daily_dispatch_record.date, '')

    def test_other_event_skips_dispatch_but_unknown_current_map_requires_recovery(self):
        self.pages.special_act_Flag = False
        self.task._dispatch_once_today()
        self.task.goto_page.assert_not_called()
        self.pages.special_act_Flag = True
        self.task._dispatch_view.observe.return_value.kind = 'unknown'
        with self.assertRaises(PreparationError):
            self.task._dispatch_once_today()
        self.dispatcher.run.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_no_configured_battles_does_not_spend_dispatch_resources(self):
        climb = self.task.conf.general_climb
        climb.pass_limit = climb.ap_limit = climb.ap100_limit = climb.boss_limit = 0
        self.task._dispatch_once_today()
        self.task.goto_page.assert_not_called()
        self.dispatcher.run.assert_not_called()
        climb.auto_color_hyakki = True
        self.task.after_run()
        self.task._colorer.assert_not_called()

    def test_delayed_drawer_gets_one_more_recovery_before_daily_dispatch(self):
        self.task._dispatch_view.observe.side_effect = [
            SimpleNamespace(kind='map'), SimpleNamespace(kind='setup'),
            SimpleNamespace(kind='setup'), SimpleNamespace(kind='map')]
        self.task._dispatch_once_today()
        self.assertEqual(self.dispatcher.restore_map.call_count, 2)
        self.dispatcher.run.assert_called_once()
        self.task.config.save.assert_called_once()

    def test_delayed_drawer_recovery_is_bounded_and_never_records_failure_as_done(self):
        self.task._dispatch_view.observe.return_value = SimpleNamespace(kind='setup')
        with self.assertRaises(PreparationError):
            self.task._dispatch_once_today()
        self.assertEqual(self.dispatcher.restore_map.call_count, 2)
        self.dispatcher.run.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_config_reload_during_dispatch_saves_current_model(self):
        original = self.task.conf
        replacement = ActivityShikigami()
        def reload():
            self.task.config.model.activity_shikigami = replacement
            return SimpleNamespace(completed=True, dispatched=1, reason='done')
        self.dispatcher.run.side_effect = reload
        self.task._dispatch_once_today()
        self.assertEqual(original.daily_dispatch_record.date, '')
        self.assertEqual(replacement.daily_dispatch_record.date, DAY.isoformat())

    def test_navigation_crossing_midnight_rechecks_new_day_after_recovery(self):
        record = self.task.conf.daily_dispatch_record
        record.date, record.owner = DAY.isoformat(), 'a\nrole1'
        self.task.goto_page.side_effect = lambda page: setattr(self.clock, 'return_value', DAY + timedelta(days=1))
        self.task._dispatch_once_today()
        self.dispatcher.run.assert_called_once()
        self.assertEqual(record.date, (DAY + timedelta(days=1)).isoformat())

    def test_navigation_reload_checks_the_current_character_record(self):
        original = self.task.conf.daily_dispatch_record
        original.date, original.owner = DAY.isoformat(), 'a\nrole1'
        replacement = ActivityShikigami()

        def reload(page):
            self.task.config.model.activity_shikigami = replacement
            self.task.config.model.restart.login_character_config.character = 'role2'

        self.task.goto_page.side_effect = reload
        self.task._dispatch_once_today()
        self.dispatcher.run.assert_called_once()
        self.assertEqual(replacement.daily_dispatch_record.owner, 'a\nrole2')
        self.assertEqual((original.date, original.owner), (DAY.isoformat(), 'a\nrole1'))

    def test_midnight_or_role_change_during_dispatch_does_not_write_stale_record(self):
        for changed in ('day', 'role'):
            with self.subTest(changed=changed):
                self.setUp()
                def change():
                    if changed == 'day':
                        self.clock.return_value = DAY + timedelta(days=1)
                    else:
                        self.task.config.model.restart.login_character_config.character = 'role2'
                    return SimpleNamespace(completed=True, dispatched=1, reason='done')
                self.dispatcher.run.side_effect = change
                with self.assertRaises(PreparationError):
                    self.task._dispatch_once_today()
                self.task.config.save.assert_not_called()

    def test_coloring_off_performs_no_navigation_or_coloring(self):
        self.task.after_run()
        self.task.goto_page.assert_not_called()
        self.task._colorer.assert_not_called()

    def test_coloring_uses_current_option_and_returns_to_map(self):
        replacement = ActivityShikigami()
        replacement.general_climb.auto_color_hyakki = True
        self.task.config.model.activity_shikigami = replacement
        self.task.after_run()
        self.assertEqual(self.events, ['map'])
        self.colorer.run.assert_called_once()
        self.colorer.leave.assert_called_once()
        self.task.config.save.assert_not_called()

    def test_failed_coloring_or_return_propagates_before_task_success(self):
        self.task.conf.general_climb.auto_color_hyakki = True
        self.colorer.run.side_effect = ColoringError('unexpected panel')
        with self.assertRaises(TransitionError) as raised:
            self.task.after_run()
        self.assertNotIsInstance(raised.exception, PreparationError)
        self.task.config.save.assert_not_called()
        self.colorer.run.side_effect = None
        self.colorer.leave.return_value = False
        with self.assertRaises(TransitionError):
            self.task.after_run()

    def test_interrupted_painting_closes_without_spending_even_if_option_off(self):
        self.task._coloring_view.find_page.return_value = object()
        self.task._restore_daily_activity_map()
        self.colorer.leave.assert_called_once()
        self.colorer.run.assert_not_called()

    def test_interrupted_painting_intro_closes_without_spending_with_option_off(self):
        self.task._coloring_view.find_intro.return_value = object()
        self.task._restore_daily_activity_map()
        self.colorer.leave.assert_called_once()
        self.colorer.run.assert_not_called()

    def test_return_popup_opened_by_navigation_is_closed_before_dispatch(self):
        self.task._dispatch_view.observe.side_effect = [
            SimpleNamespace(kind='returned', close_roi=None, dismiss_roi=(1, 2, 3, 4)),
            SimpleNamespace(kind='map')]
        self.task._dispatch_once_today()
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_called_once()
        self.task.config.save.assert_called_once()

    def test_recorded_day_still_closes_return_popup_without_redeploying(self):
        self.task.conf.daily_dispatch_record.date = DAY.isoformat()
        self.task.conf.daily_dispatch_record.owner = 'a\nrole1'
        self.task._dispatch_view.observe.side_effect = [
            SimpleNamespace(kind='returned', close_roi=None, dismiss_roi=(1, 2, 3, 4)),
            SimpleNamespace(kind='map')]
        self.task._dispatch_once_today()
        self.assertEqual(self.events, ['map'])
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_recorded_day_does_not_hide_an_unclosed_return_popup(self):
        self.task.conf.daily_dispatch_record.date = DAY.isoformat()
        self.task.conf.daily_dispatch_record.owner = 'a\nrole1'
        self.task._dispatch_view.observe.return_value = SimpleNamespace(
            kind='returned', close_roi=None, dismiss_roi=(1, 2, 3, 4))
        self.dispatcher.restore_map.side_effect = DispatchError('return popup stuck')
        with self.assertRaises(PreparationError):
            self.task._dispatch_once_today()
        self.dispatcher.run.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_post_climb_return_popup_is_closed_before_opening_coloring(self):
        self.task.conf.general_climb.auto_color_hyakki = True
        self.task._dispatch_view.observe.return_value = SimpleNamespace(
            kind='returned', close_roi=None, dismiss_roi=(1, 2, 3, 4))
        self.dispatcher.restore_map.side_effect = lambda: self.events.append('close_return') or True
        self.colorer.run.side_effect = lambda: self.events.append('color') or SimpleNamespace(
            status='no_currency', submissions=0, global_progress=54.0)
        self.task.after_run()
        self.assertEqual(self.events, ['map', 'close_return', 'color'])
        self.dispatcher.run.assert_not_called()

    def test_failed_post_climb_return_recovery_does_not_retry_completed_battles(self):
        self.task.conf.general_climb.auto_color_hyakki = True
        self.task._dispatch_view.observe.return_value = SimpleNamespace(
            kind='returned', close_roi=None, dismiss_roi=(1, 2, 3, 4))
        self.dispatcher.restore_map.side_effect = DispatchError('return popup stuck')
        with self.assertRaises(TransitionError) as raised:
            self.task.after_run()
        self.assertNotIsInstance(raised.exception, PreparationError)
        self.colorer.run.assert_not_called()

    def test_interrupted_dispatch_closes_without_redeploying(self):
        self.task._dispatch_view.observe.return_value.kind = 'setup'
        self.task._restore_daily_activity_map()
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_recalled_dispatch_popup_recovers_before_navigation_without_spending(self):
        self.task._dispatch_view.observe.return_value = SimpleNamespace(
            kind='interrupted', dismiss_roi=(1, 2, 3, 4), close_roi=None)
        self.task._restore_daily_activity_map()
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_not_called()
        self.task.goto_page.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_interrupted_coloring_reward_is_closed_even_when_coloring_is_disabled(self):
        self.task.conf.general_climb.auto_color_hyakki = False
        self.task._coloring_view.find_reward.return_value = object()
        self.task._restore_daily_activity_map()
        self.colorer.leave.assert_called_once()
        self.colorer.run.assert_not_called()
        self.task.click.assert_not_called()
        self.task.config.save.assert_not_called()

    def test_interrupted_success_popup_recovers_without_redeploying_or_completing_task(self):
        self.task._dispatch_view.observe.return_value = SimpleNamespace(
            kind='success', dismiss_roi=(1, 2, 3, 4), close_roi=None)
        self.task._restore_daily_activity_map()
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_not_called()
        self.task.goto_page.assert_not_called()
        self.task.config.save.assert_not_called()
        self.assertEqual(self.task.conf.daily_dispatch_record.date, '')

    def test_level_up_is_closed_before_navigation_without_redeploying_or_completing_task(self):
        self.task._dispatch_view.observe.return_value = SimpleNamespace(
            kind='level_up', dismiss_roi=(1, 2, 3, 4), close_roi=None)
        self.task._restore_daily_activity_map()
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_not_called()
        self.task.goto_page.assert_not_called()
        self.task.config.save.assert_not_called()
        self.assertEqual(self.task.conf.daily_dispatch_record.date, '')

    def test_unclosed_success_popup_requests_preparation_retry_without_daily_record(self):
        self.task._dispatch_view.observe.return_value = SimpleNamespace(kind='success', close_roi=None)
        for failure in (False, DispatchError('popup did not close')):
            with self.subTest(failure=failure):
                self.dispatcher.restore_map.side_effect = failure if isinstance(failure, Exception) else None
                self.dispatcher.restore_map.return_value = False
                with self.assertRaises(PreparationError):
                    self.task._restore_daily_activity_map()
        self.task.config.save.assert_not_called()
        self.dispatcher.run.assert_not_called()

    def test_partially_recognized_drawer_uses_verified_collapse_without_deployment(self):
        self.task._dispatch_view.observe.return_value = SimpleNamespace(kind='unknown', close_roi=(1, 2, 3, 4))
        self.task._restore_daily_activity_map()
        self.dispatcher.restore_map.assert_called_once()
        self.dispatcher.run.assert_not_called()

    def test_real_base_click_adapter_reports_delivery_and_propagates_device_failure(self):
        from tasks.ActivityShikigami.activities.normal import NormalClimbAct
        task = object.__new__(NormalClimbAct)
        task.device = SimpleNamespace(click=Mock())
        self.assertTrue(task._activity_click_roi((100, 100, 20, 20), 'dispatch_slot'))
        self.assertEqual(task.device.click.call_args.kwargs['control_name'], 'dispatch_slot')
        task.device.click.side_effect = OSError('device disconnected')
        with self.assertRaises(OSError):
            task._activity_click_roi((100, 100, 20, 20), 'dispatch_slot')

    def test_ocr_keeps_blank_raw_text_and_click_adapter_keeps_verified_region(self):
        self.assertEqual(self.task._activity_read_text('', (1, 2, 3, 4)), '')
        self.assertEqual(self.task._activity_read_text('54.0%', (1, 2, 3, 4)), '54.0%')
        self.task._activity_click_roi((1, 2, 3, 4), 'dispatch_slot1')
        clicked = self.task.click.call_args.args[0]
        self.assertEqual((clicked.roi_front, clicked.roi_back, clicked.name),
                         ((1, 2, 3, 4), (1, 2, 3, 4), 'dispatch_slot1'))


class ConfigurationTests(unittest.TestCase):
    def test_new_option_is_off_and_visible_with_chinese_label(self):
        conf = ActivityShikigami()
        self.assertFalse(conf.general_climb.auto_color_hyakki)
        schema = conf.general_climb.model_json_schema()['properties']['auto_color_hyakki']
        labels = json.loads((ROOT / 'assets/i18n/zh-CN.json').read_text(encoding='utf-8'))
        self.assertEqual(schema['type'], 'boolean')
        self.assertIn('上色', labels['auto_color_hyakki'])
        self.assertIn('100%', labels[schema['description']])

    def test_template_and_legacy_config_upgrade_and_hidden_record(self):
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))['activity_shikigami']
        conf = ActivityShikigami.model_validate(template)
        self.assertFalse(conf.general_climb.auto_color_hyakki)
        self.assertEqual(conf.daily_dispatch_record.date, '')
        hidden = conf.model_dump(context={'hide': True})
        self.assertEqual(hidden['daily_dispatch_record'], 0xABCDEF)
        self.assertIn('auto_color_hyakki', hidden['general_climb'])
        old = dict(template)
        old.pop('daily_dispatch_record')
        old['general_climb'] = dict(old['general_climb'])
        old['general_climb'].pop('auto_color_hyakki')
        self.assertEqual(ActivityShikigami.model_validate(old).daily_dispatch_record.date, '')
        self.assertIsNot(ActivityShikigami().daily_dispatch_record, ActivityShikigami().daily_dispatch_record)


class LifecycleTests(unittest.TestCase):
    def test_post_actions_follow_all_climb_types_and_precede_main_and_scheduler(self):
        events = []
        class LimitCountOut(Exception):
            pass
        class TaskEnd(Exception):
            pass
        namespace = dict(logger=Mock(), Optional=__import__('typing').Optional,
                         pages=SimpleNamespace(Page=object, page_act_pass='pass', page_act_ap='ap', page_main='main'),
                         LimitCountOut=LimitCountOut, LimitTimeOut=RuntimeError,
                         TicketsNotEnough=ValueError, TaskEnd=TaskEnd)
        cls = methods('tasks/ActivityShikigami/base_act.py', 'BaseAct', ['run'], namespace)
        task = cls()
        task.conf = SimpleNamespace(general_climb=SimpleNamespace(
            run_sequence_v=['pass', 'ap'], active_souls_clean=False), pass_battle_conf=object(), ap_battle_conf=object())
        task.climb_type = 'pass'
        task.before_run = lambda: events.append('before')
        task.after_run = lambda: events.append('after')
        task.goto_page = lambda page: events.append(page)
        task.lock_team = Mock()
        task.screenshot = Mock()
        task.update_status = Mock(side_effect=LimitCountOut)
        task.switch_next = lambda: setattr(task, 'climb_type', 'ap')
        task.set_next_run = lambda **kwargs: events.append('schedule')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(events, ['before', 'pass', 'ap', 'after', 'main', 'schedule'])
        events.clear()
        task.update_status.side_effect = OSError('battle failed')
        with self.assertRaises(OSError):
            task.run()
        self.assertNotIn('after', events)
        self.assertNotIn('schedule', events)
        events.clear()
        task.before_run = Mock(side_effect=PreparationError('dispatch popup did not close'))
        with self.assertRaises(PreparationError):
            task.run()
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
