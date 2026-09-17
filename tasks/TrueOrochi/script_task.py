# This Python file uses the following encoding: utf-8
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic, sleep
import json

import cv2

from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import TaskEnd
from module.logger import logger
from tasks.GameUi.page import page_main, page_shikigami_records
from tasks.Orochi.config import Layer
from tasks.Orochi.page import page_orochi
from tasks.Orochi.script_task import ScriptTask as OrochiScriptTask
from tasks.TrueOrochi.assets import TrueOrochiAssets
from tasks.TrueOrochi.config import TrueOrochi, TeamRole
from tasks.TrueOrochi.team import LocalTeam, TeamSyncError, week_key
from tasks.TrueOrochi.view import TrueOrochiView


class TrueOrochiError(RuntimeError):
    pass


class ScriptTask(OrochiScriptTask, TrueOrochiAssets):
    _team_sync = None
    _last_heartbeat = 0
    _true_battling = False
    O_TRUE_TEXT = RuleOcr(roi=(0, 0, 1, 1), area=(0, 0, 1, 1), mode='Single',
                          method='Default', keyword='', name='true_orochi_count')

    def _default_detect_categories(self) -> set[str]:
        categories = super()._default_detect_categories()
        categories.add('orochi')
        return categories

    def screenshot(self):
        if self._team_sync and monotonic() - self._last_heartbeat > 2:
            self._last_heartbeat = monotonic()
            try:
                self._team_sync.heartbeat()
            except TeamSyncError:
                # Finish an already running fight even if the partner stops.
                if not self._true_battling:
                    raise
        result = super().screenshot()
        self._true_view = TrueOrochiView(self.device.image)
        return result

    def _read_text(self, image):
        if image.size == 0:
            return ''
        return self.O_TRUE_TEXT.ocr_item(cv2.resize(image, None, fx=2, fy=2))

    def _keep_long_wait(self):
        # Every device click clears detect_record, including Ready and Auto.
        if 'BATTLE_STATUS_S' not in self.device.detect_record:
            self.device.stuck_record_add('BATTLE_STATUS_S')

    def _tap(self, region, name):
        x, y, w, h = region
        height, width = self.device.image.shape[:2]
        if x < 0 or y < 0 or x+w > width or y+h > height:
            raise TrueOrochiError(f'真蛇控件超出截图: {name}')
        return self.click(RuleClick(roi_front=region, roi_back=region, name=name), interval=1)

    def _reset_week(self):
        conf = self.config.true_orochi.true_orochi_config
        current = week_key()
        if conf.success_week and conf.success_week != current:
            conf.current_success = 0
        conf.current_success = max(0, min(2, conf.current_success))
        conf.success_week = current
        self.config.save()

    def _connect_team(self):
        team = self.config.true_orochi.team_config
        own = self.config.config_name
        peer = team.teammate_config.strip()
        if not peer or peer == own or any(c in peer for c in '/\\:') or peer in ('.', '..'):
            raise TrueOrochiError('请填写另一个 OAS 配置名（不带 .json），两个配置互相填写')
        path = Path('config') / f'{peer}.json'
        if not path.is_file():
            raise TrueOrochiError(f'没有找到队友配置: {peer}')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        other = TrueOrochi(**data.get('true_orochi', {}))
        if (not other.team_config.enable or other.team_config.teammate_config.strip() != own or
                other.team_config.user_status == team.user_status):
            raise TrueOrochiError('双方须启用真蛇组队、互填配置名，并分别选择初始队长和队员')
        if (len(self.config.true_orochi.invite_config.friend_list_v) != 1 or
                len(other.invite_config.friend_list_v) != 1):
            raise TrueOrochiError('双方的真蛇邀请名单都须填写对方的一个游戏好友名，以便轮换邀请')
        serial = data.get('script', {}).get('device', {}).get('serial')
        if serial and serial == self.config.script.device.serial:
            raise TrueOrochiError('两个真蛇配置不能操作同一个模拟器')
        leader = own if team.user_status == TeamRole.LEADER else peer
        mode = team.hosting_mode if leader == own else other.team_config.hosting_mode
        return LocalTeam(own, peer, leader, mode)

    def run(self):
        self._reset_week()
        successful = False
        error = ''
        try:
            if self.config.true_orochi.team_config.enable:
                self._team_sync = self._connect_team()
            self.switch_true_orochi_souls()
            successful = self._run_team() if self._team_sync else self._run_solo()
        except (TrueOrochiError, TeamSyncError) as exc:
            error = str(exc)
            logger.warning(error)
        except BaseException as exc:
            if self._team_sync:
                self._team_sync.close(str(exc) or type(exc).__name__)
            raise
        finally:
            if self._team_sync:
                self._team_sync.close(error or ('' if successful else '真蛇任务未完成'))
                self._team_sync = None
        self._leave_true_room()
        self.goto_page(page_main)
        self.check_times(successful)
        raise TaskEnd('TrueOrochi')

    def switch_true_orochi_souls(self):
        soul = self.config.true_orochi.switch_soul
        if soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(soul.switch_group_team)
        if soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(soul.group_name, soul.team_name)

    def _inspect_counts(self):
        self._leave_true_room()
        self.goto_page(page_orochi)
        previous = None
        entries = None
        for _ in range(5):
            self.screenshot()
            entries = self._true_view.entry_count(self._read_text)
            if entries == 0 and self.appear(self.I_FIND_TS):
                entries = None
            if entries is not None and entries == previous:
                break
            previous = entries
            sleep(.4)
        else:
            raise TrueOrochiError('未能稳定识别真蛇入口数量')
        conf = self.config.true_orochi.true_orochi_config
        if entries == 0:
            # An account without its own entry can still join its partner.
            return 0, max(0, 2-conf.current_success)
        timer = Timer(20).start()
        previous = None
        while not timer.reached():
            self.screenshot()
            rewards = self._true_view.rewards(self._read_text)
            if rewards is not None and rewards == previous:
                conf.current_success = 2-rewards
                self.config.save()
                logger.info(f'真蛇入口 {entries}，本周剩余奖励 {rewards}/2')
                self._leave_true_room()
                return entries, rewards
            previous = rewards
            if panel := self._true_view.entry():
                self._tap(panel.roi(23, 21, 47, 40), 'TRUE_OROCHI_ENTRY')
            sleep(.3)
        raise TrueOrochiError('本周剩余奖励次数识别失败，停止本次挑战')

    def _run_solo(self):
        for _ in range(2):
            entries, rewards = self._inspect_counts()
            if rewards == 0:
                return True
            if entries == 0:
                if not self.config.true_orochi.true_orochi_config.find_true_orochi or not self.get_true_orochi():
                    return False
                self.switch_true_orochi_souls()
                entries, rewards = self._inspect_counts()
                if rewards == 0:
                    return True
            self._create_true_room(rewards)
            self._start_true_room()
            if not self.run_true_orochi_battle():
                return False
            self._record_success()
        return True

    def _wait_sync(self, number, key):
        timer = Timer(self.config.true_orochi.invite_config.wait_time_v.total_seconds()).start()
        while not timer.reached():
            row = self._team_sync.round_state(number)
            if key == 'decision' and 'decision' in row:
                return row['decision']
            if key == 'done' and len(row.get('done', {})) == 2:
                return all(row['done'].values())
            self._keep_long_wait()
            self.screenshot()
            sleep(.5)
        raise TeamSyncError('等待另一个 OAS 配置的真蛇任务超时，请让双方同时运行')

    def _run_team(self):
        for number in range(2):
            entries, rewards = self._inspect_counts()
            self._team_sync.ready(number, entries, rewards)
            host = self._wait_sync(number, 'decision')
            if host == 'finished':
                return True
            if host == 'no_entries':
                logger.warning('双方都没有可用真蛇入口，稍后重试')
                return False
            logger.info(f'真蛇第 {number+1} 轮：由 {host} 创建私密队伍')
            if host == self.config.config_name:
                self._create_true_room(rewards)
                self._invite_true_friend()
                self._start_true_room(number)
            else:
                self.goto_page(page_main)
                self._wait_true_invitation(number)
            success = self.run_true_orochi_battle()
            if success:
                self._record_success()
            self._leave_true_room()
            self._team_sync.done(number, success)
            if not self._wait_sync(number, 'done'):
                return False
        return True

    def _create_true_room(self, expected_rewards):
        self.goto_page(page_orochi)
        timer = Timer(45).start()
        private_confirmed = False
        while not timer.reached():
            self.screenshot()
            view = self._true_view
            if view.room():
                if not private_confirmed:
                    raise TrueOrochiError('未核验不公开设置，停止建队')
                self._true_room_created_at = monotonic()
                return
            if panel := view.private():
                if view.private_selected(panel):
                    private_confirmed = True
                    self._tap(panel.roi(207, 322, 132, 36), 'TRUE_OROCHI_CREATE')
                else:
                    self._tap(panel.roi(50, 247, 24, 24), 'TRUE_OROCHI_PRIVATE')
                continue
            if panel := view.confirm():
                if view.rewards(self._read_text) != expected_rewards:
                    raise TrueOrochiError('确认窗奖励次数与开始前不一致，停止本轮')
                self._tap(panel.roi(605, 327, 112, 36), 'TRUE_OROCHI_CONFIRM')
                continue
            if panel := view.detail():
                if view.rewards(self._read_text) != expected_rewards:
                    raise TrueOrochiError('奖励次数发生变化或识别失败，停止本轮')
                self._tap(panel.roi(830, 435, 57, 35), 'TRUE_OROCHI_CHALLENGE')
                continue
            if panel := view.entry():
                self._tap(panel.roi(23, 21, 47, 40), 'TRUE_OROCHI_ENTRY')
        raise TrueOrochiError('进入真蛇私密队伍超时')

    def _invite_true_friend(self):
        timer = Timer(15).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_LOAD_FRIEND) or self.appear(self.I_INVITE_ENSURE):
                if not self.invite_friends(self.config.true_orochi.invite_config, open_invite=False):
                    raise TrueOrochiError('OCR 未找到指定好友，停止挑战')
                return
            if panel := self._true_view.room():
                self._tap(panel.roi(555, 209, 40, 38), 'TRUE_OROCHI_INVITE')
        raise TrueOrochiError('打开好友邀请列表超时')

    def _at_true_battle_start(self):
        return (self.appear(self.I_ST_FIRE_PREPARE) or self.appear(self.I_BUFF) or
                self.is_in_real_battle(False))

    def _start_true_room(self, number=None):
        # The game's room automatically starts at five minutes. Leave a minute
        # for cleanup, including time already spent finding the friend by OCR.
        room_age = monotonic() - getattr(self, '_true_room_created_at', monotonic())
        wait_seconds = min(self.config.true_orochi.invite_config.wait_time_v.total_seconds(),
                           max(0, 240-room_age))
        timer = Timer(wait_seconds).start()
        retry = Timer(20).start()
        fired = False
        while not timer.reached():
            self._keep_long_wait()
            self.screenshot()
            if self._at_true_battle_start():
                if number is not None and not fired:
                    raise TrueOrochiError('队伍在队友核验前已开始，本次不记录成功')
                return
            panel = self._true_view.room()
            if not panel:
                continue
            if number is not None:
                row = self._team_sync.round_state(number)
                if self._team_sync.peer not in row['joined'] or self._true_view.empty_slot(panel):
                    if self._team_sync.peer not in row['joined'] and retry.reached():
                        self._invite_true_friend()
                        retry.reset()
                    continue
            fired = self._tap(panel.roi(1070, 549, 53, 48), 'TRUE_OROCHI_ROOM_FIRE') or fired
        raise TrueOrochiError('等待队友进入或开启挑战超时')

    def _wait_true_invitation(self, number):
        timer = Timer(self.config.true_orochi.invite_config.wait_time_v.total_seconds()).start()
        accepted = False
        while not timer.reached():
            self._keep_long_wait()
            self.screenshot()
            if self._at_true_battle_start():
                return
            if self._true_view.room():
                self._team_sync.joined(number)
                sleep(.3)
                continue
            if self.appear_then_click(self.I_I_ACCEPT, interval=1) or self.appear_then_click(self.I_I_ACCEPT_APPRENTICE, interval=1):
                accepted = True
                continue
            if accepted:
                self.appear_then_click(self.I_GI_SURE, interval=1)
            sleep(.3)
        raise TrueOrochiError('等待真蛇队长邀请或挑战超时')

    def run_true_orochi_battle(self):
        """Keep auto enabled across all ten floors, then dismiss rewards/frame."""
        logger.hr('True Orochi Battle')
        self._true_battling = True
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        timeout = Timer(1200).start()
        guard = Timer(240).start()
        rewarded = False
        failed = False
        settlement = None
        try:
            while not timeout.reached():
                self._keep_long_wait()
                self.screenshot()
                if settlement and settlement.reached():
                    raise TrueOrochiError('真蛇结算退出超时，保留现场等待下次检查')
                if self.appear_then_click(self.I_ST_FRAME, interval=1):
                    continue
                if self.appear(self.I_FALSE):
                    failed = True
                    if settlement is None:
                        settlement = Timer(45).start()
                    self.click(self.C_RANDOM_BOTTOM, interval=1)
                    continue
                if self.appear(self.I_GREED_GHOST):
                    rewarded = True
                    if settlement is None:
                        settlement = Timer(45).start()
                    self.appear_then_click(self.I_GREED_GHOST, interval=1)
                    continue
                if rewarded or failed:
                    if self._true_view.room() or self._true_view.detail() or self._at_orochi_or_home():
                        return rewarded and not failed
                    self._click_reward_exit()
                    sleep(.4)
                    continue
                if self.appear_then_click(self.I_ST_FIRE_PREPARE, interval=1.5):
                    continue
                if self.appear(self.I_BUFF):
                    self.appear_then_click(self.I_ST_AUTO_FALSE, interval=1.8)
                    # Manual first-floor ready can stall auto at floor two.
                if guard.reached():
                    self.device.stuck_record_clear()
                    self.device.stuck_record_add('BATTLE_STATUS_S')
                    guard.reset()
                sleep(.5)
        finally:
            self._true_battling = False
        raise TrueOrochiError('真蛇战斗或结算超时，未记为成功')

    def _click_reward_exit(self):
        # Only called after a reward/defeat marker, never during a battle floor.
        h, w = self.device.image.shape[:2]
        rule = RuleOcr(roi=(w//4, h//2, w//2, h//2), area=(0, 0, 1, 1),
                       mode='Full', method='Default', keyword='退出', name='true_orochi_reward_exit')
        region = rule.ocr(self.device.image, exact=True)
        if region[2] > 0 and region[3] > 0:
            self._tap(tuple(int(v) for v in region), 'TRUE_OROCHI_REWARD_EXIT')

    def _at_orochi_or_home(self):
        return any(self.appear(rule) for rule in (
            self.I_CHECK_MAIN, self.I_CHECK_EXPLORATION, self.I_OROCHI_CHECK_10,
            self.I_OROCHI_CHECK_11, self.I_OROCHI_CHECK_12, self.I_OROCHI_CHECK_13))

    def _leave_true_room(self):
        timer = Timer(15).start()
        leaving = False
        while not timer.reached():
            self.screenshot()
            if self.appear_then_click(self.I_ST_FRAME, interval=1):
                continue
            if leaving and self.appear_then_click(self.I_GI_SURE, interval=1):
                continue
            if panel := self._true_view.room():
                leaving = self._tap(panel.roi(19, 19, 29, 28), 'TRUE_OROCHI_LEAVE') or leaving
                continue
            if panel := self._true_view.confirm():
                self._tap(panel.roi(395, 325, 112, 38), 'TRUE_OROCHI_CANCEL')
                continue
            if panel := self._true_view.detail():
                self._tap(panel.roi(922, 78, 31, 29), 'TRUE_OROCHI_CLOSE')
                continue
            if leaving and self.appear(self.I_GI_SURE):
                continue
            return
        raise TrueOrochiError('退出真蛇队伍超时')

    def _record_success(self):
        conf = self.config.true_orochi.true_orochi_config
        conf.current_success = min(2, conf.current_success + 1)
        conf.success_week = week_key()
        self.config.save()

    def get_true_orochi(self) -> bool:
        """Solo compatibility: search at most ten soul-ten fights."""
        original_layer = self.config.orochi.orochi_config.layer
        try:
            self.config.orochi.orochi_config.layer = Layer.TEN
            self.limit_count = 0
            self.limit_time = timedelta(hours=10)
            if self.config.true_orochi.switch_soul.enable_switch_layer_soul:
                self.switch_orochi_souls()
            self.goto_page(page_orochi)
            for _ in range(10):
                if self.check_true_orochi(True):
                    return True
                self.limit_count += 1
                self.run_alone()
            return self.check_true_orochi(True)
        finally:
            self.config.orochi.orochi_config.layer = original_layer

    def check_true_orochi(self, screenshot=False) -> bool:
        if screenshot:
            self.screenshot()
        return bool(self._true_view.entry()) or self.appear(self.I_FIND_TS)

    def check_times(self, battle: bool):
        # Scheduling never grants a success or resets this week's counter just
        # because the next scheduled time happens to cross Monday.
        now = datetime.now()
        conf = self.config.true_orochi
        interval = conf.scheduler.success_interval if battle else conf.scheduler.failure_interval
        next_run = now + interval
        if conf.true_orochi_config.current_success >= 2:
            next_run = (now + timedelta(days=7-now.weekday())).replace(hour=0, minute=5, second=0, microsecond=0)
        self.config.save()
        self.set_next_run(task='TrueOrochi', target=next_run)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    ScriptTask(c, Device(c)).run()
