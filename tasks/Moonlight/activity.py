"""月映千灯：月华流光挑战；不依赖其他活动的配置或运行状态。"""
import time
from datetime import datetime

from module.base.protect import random_sleep
from module.exception import GamePageUnknownError, GameStuckError
from module.logger import logger
from module.scheduling.task_metrics import report_count_progress
from tasks.GameUi.page import page_battle_prepare, page_battle, page_battle_result
import tasks.Moonlight.page as pages


class MoonlightAct:
    NAVIGATION_TIMEOUT = 45
    ENTRY_TIMEOUT = 30

    def _moon_confirmation_visible(self) -> bool:
        return self.appear(self.I_UI_CONFIRM) or self.appear(self.I_UI_CONFIRM_SAMLL)

    def _cancel_moon_confirmation(self) -> None:
        if not (self.appear_then_click(self.I_UI_CANCEL, interval=0)
                or self.appear_then_click(self.I_UI_CANCEL_SAMLL, interval=0)):
            raise GameStuckError('月华流光：无法取消确认弹窗')

    def screenshot(self):
        # The shared navigator has a progress timeout that resets on page changes.
        # Bound this task's complete navigation too, including alternating pages.
        deadline = getattr(self, '_moon_navigation_deadline', None)
        cancelled = False
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise GamePageUnknownError('月华流光：页面导航超时')
            image = super().screenshot()
            if deadline is None:
                return image
            if time.monotonic() >= deadline:
                raise GamePageUnknownError('月华流光：页面导航超时')
            if not self._moon_confirmation_visible():
                return image
            # Intercept overlays before the generic navigator can confirm them,
            # even when the underlying page is still recognizable.
            if not cancelled:
                logger.warning('月华流光：取消导航过程中出现的未知确认弹窗')
                self._cancel_moon_confirmation()
                cancelled = True
            time.sleep(.3)

    def _goto_moonlight(self, destination):
        previous_deadline = getattr(self, '_moon_navigation_deadline', None)
        deadline = time.monotonic() + self.NAVIGATION_TIMEOUT
        self._moon_navigation_deadline = (min(previous_deadline, deadline)
                                          if previous_deadline is not None else deadline)
        try:
            if not self.goto_page(destination, skip_first_screenshot=False, timeout=20):
                raise GamePageUnknownError('月华流光：无法到达目标页面')
        finally:
            self._moon_navigation_deadline = previous_deadline

    def _enter_moonlight_battle(self) -> bool:
        """资源不足时复查一次；只点击一次挑战，不确认购买弹窗。"""
        self.screenshot()
        if self._moon_confirmation_visible():
            self._cancel_moon_confirmation()
            return False
        if not self.appear(self.I_MOON_BATTLE):
            raise GameStuckError('月华流光：未识别到挑战页面')
        resource = self.O_MOON_RESOURCE.ocr_digit(self.device.image)
        if resource < 6:
            time.sleep(.3)
            self.screenshot()
            if self._moon_confirmation_visible():
                self._cancel_moon_confirmation()
                return False
            if not self.appear(self.I_MOON_BATTLE):
                raise GameStuckError('月华流光：复查挑战资源时页面发生变化')
            resource = self.O_MOON_RESOURCE.ocr_digit(self.device.image)
            if resource < 6:
                logger.info('月华流光：连续两次识别到挑战资源不足 6 点，停止挑战')
                return False
        if not self.appear_then_click(self.I_MOON_CHALLENGE, interval=0):
            raise GameStuckError('月华流光：未识别到挑战按钮，或点击未执行')
        deadline = time.monotonic() + self.ENTRY_TIMEOUT
        reward_cap_confirmed = False
        while time.monotonic() < deadline:
            self.screenshot()
            if self._moon_confirmation_visible():
                notice = ''.join(
                    result.ocr_text for result in self.O_MOON_REWARD_NOTICE.detect_and_ocr(self.device.image)
                ).replace(' ', '')
                if '奖励已达获取上限' in notice and '继续挑战' in notice:
                    if not reward_cap_confirmed:
                        if not (self.appear_then_click(self.I_UI_CONFIRM, interval=0)
                                or self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=0)):
                            raise GameStuckError('月华流光：无法确认奖励已达上限的提示')
                        reward_cap_confirmed = True
                    time.sleep(.3)
                    continue
                logger.warning('月华流光：出现未知确认弹窗，取消并停止挑战')
                self._cancel_moon_confirmation()
                return False
            # A generic reward overlay is not evidence of a new challenge. The
            # navigator confirms prepare/combat/result markers in two frames.
            entered = self.detect_page_in(page_battle_prepare, page_battle,
                                          page_battle_result, include_global=False)
            if entered and not self._moon_confirmation_visible():
                return True
            time.sleep(.3)
        raise GameStuckError('月华流光：点击挑战后等待进入战斗超时')

    def _moon_time_limit_reached(self) -> bool:
        return datetime.now() - self.start_time >= self.conf.general_config.limit_time_v

    def run_moonlight(self):
        logger.hr('开始活动：月华流光', 1)
        self.moonlight_count = 0
        report_count_progress(self)
        self._battle_shared_state.pop('moonlight', None)
        general = self.conf.general_config
        while (self.moonlight_count < general.challenge_limit
               and not self._moon_time_limit_reached()):
            self._goto_moonlight(pages.page_moon_battle)
            if general.random_sleep:
                random_sleep(probability=0.2)
            if self._moon_time_limit_reached() or not self._enter_moonlight_battle():
                return
            # Count only a confirmed battle entry; a loss still consumes a challenge.
            self.moonlight_count += 1
            report_count_progress(self)
            source = self.conf.moonlight_battle_conf
            battle = source.model_copy(update={
                'preset_enable': source.preset_enable and self.moonlight_count == 1,
                'lock_team_enable': False,
                'continuous_battle': False,
                'max_continuous': 0,
            })
            self.run_general_battle(battle, battle_key='moonlight',
                                    exit_matcher=self.I_MOON_BATTLE)
            # GeneralBattle may finish after its settlement fallback. Confirm the
            # challenge page before another resource read or challenge click.
            self._goto_moonlight(pages.page_moon_battle)
