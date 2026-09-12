# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import copy
import time

import random
import unicodedata
from dataclasses import dataclass

from tasks.Component.Summon.assets import SummonAssets
from tasks.base_task import BaseTask
from module.logger import logger
import re


@dataclass(frozen=True)
class FreeSummonResult:
    completed: int = 0
    exhausted: bool = False


class Summon(BaseTask, SummonAssets):


    def summon(self):
        """
        召唤, 就是随机画一个， 划线
        :return:
        """
        self.screenshot()
        random_swipe = random.randint(0, 3)
        target_swipe = None
        match random_swipe:
            case 0: target_swipe = self.S_RANDOM_SWIPE_1
            case 1: target_swipe = self.S_RANDOM_SWIPE_2
            case 2: target_swipe = self.S_RANDOM_SWIPE_3
            case 3: target_swipe = self.S_RANDOM_SWIPE_4
            case _: target_swipe = self.S_RANDOM_SWIPE_1
        self.swipe(target_swipe, interval=0.5)

    def summon_mystery_pattern(self):
        """
        每月神秘图案
        :return:
        """
        # 目前只测试了7月的，其他月份的我是根据截图描的，可能需要调整
        # 一月
        jan = [(400, 119), (406, 525), (862, 123), (864, 521), (402, 121)]
        # 二月、八月
        febAndAug = [(450, 105), (453, 525)]
        # 三月、九月
        marAndSep = [(390, 304), (886, 302)]
        # 四月、十月
        aprAndOct = [(402, 526), (862, 125)]
        # 五月、十一月
        mayAndNov = [(414, 207), (648, 550), (870, 209)]
        # 六月、十二月
        junAndDec = [(413, 138), (850, 133), (856, 226), (415, 239), (416, 140), (531, 136), (535, 590), (791, 586),
                     (760, 131)]
        # 七月
        jul = [(418, 124), (421, 504), (853, 511), (855, 128)]
        # 月份字典
        month_dict = {
            1: jan,
            2: febAndAug,
            3: marAndSep,
            4: aprAndOct,
            5: mayAndNov,
            6: junAndDec,
            7: jul,
            8: febAndAug,
            9: marAndSep,
            10: aprAndOct,
            11: mayAndNov,
            12: junAndDec
        }
        # 获取当前月份
        current_month = time.localtime().tm_mon
        current_pattern = month_dict.get(current_month, None)
        if current_pattern is None:
            logger.warning(f'不支持的月份: {current_month}')
            return
        self.screenshot()
        self.device.draw_adb(current_pattern)



    @staticmethod
    def _parse_free_summon_count(text) -> int | None:
        """Read remaining/total, never infer free attempts from owned tickets.

        The existing narrow counter ROI may return only ``1/2`` instead of
        the full caption ``剩余免费次数 1/2``. The numerator is remaining.
        A bare positive number is ambiguous and must not authorize a draw.
        """
        if not isinstance(text, str):
            return None
        text = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))
        prefix = r'(?:(?:今日|每日)?(?:剩余)?免费(?:召唤)?(?:次数|机会)?(?:剩余)?[:：]?)'
        fraction = re.fullmatch(prefix + r'?(\d{1,2})/(\d{1,2})(?:次)?', text)
        if fraction:
            remaining, total = map(int, fraction.groups())
            if 0 <= remaining <= total <= 10 and total > 0:
                return remaining
            return None
        number = re.fullmatch(prefix + r'(\d{1,2})(?:次)?', text)
        if number and 0 <= int(number.group(1)) <= 10:
            return int(number.group(1))
        if re.fullmatch(prefix + r'(?:已用完|已耗尽|用尽)', text):
            return 0
        return None

    def _read_free_summon_count(self, counter, main_marker, previous=None) -> int | None:
        """Require two matching fresh quota frames; tolerate update lag."""
        # Image matching moves the normal ticket's roi_front on event menus.
        # Keep the calibrated caption offset from that matched ticket instead
        # of reading its old absolute screen position. Do not mutate shared
        # OCR assets: other accounts and the recall menu have their own ROI.
        counter = copy.copy(counter)
        last_count = None
        for _ in range(8):
            self.screenshot()
            count = None
            if self.appear(main_marker):
                if main_marker is self.I_BLUE_TICKET:
                    x, y, _, _ = main_marker.roi_front
                    counter.roi = [x - 21, y + 95, 100, 32]
                raw = counter.ocr(self.device.image)
                logger.info(f'Free summon quota: {raw!r}, ROI: {counter.roi}')
                count = self._parse_free_summon_count(raw)
            # After a draw, an unchanged caption is not permission for another.
            if count is not None and (previous is None or count < previous):
                if count == last_count:
                    return count
                last_count = count
            else:
                last_count = None
            time.sleep(0.25)
        logger.warning('Free summon quota is unknown or has not decreased; stop drawing')
        return None

    def _event_summon_canvas_appear(self) -> bool:
        """Recognize the event skin without replacing the legacy ticket UI."""
        if not self.appear(self.I_EVENT_ONE_TICKET):
            return False
        text = self.O_EVENT_DRAW_PROMPT.ocr(self.device.image)
        return isinstance(text, str) and re.sub(r'\s+', '', text) == '画出轨迹召唤式神'

    def _perform_free_summon(self, main_marker, single_marker, confirmations,
                             draw_mystery_pattern=False) -> bool:
        """Perform one authorized draw and confirm its result with bounded waits."""
        deadline = time.monotonic() + 30
        entered = False
        event_canvas = False
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(single_marker):
                break
            if main_marker is self.I_BLUE_TICKET and self._event_summon_canvas_appear():
                event_canvas = True
                break
            if not entered and self.appear(main_marker):
                self.click(main_marker)
                entered = True
        else:
            logger.warning('Free summon entry timed out')
            return False

        if event_canvas:
            # Both single and ten-draw buttons are visible on this skin. Select
            # single explicitly, then verify its own free caption before drawing.
            logger.info('Event summon canvas: select single and verify free quota')
            self.click(self.I_EVENT_ONE_TICKET)
            time.sleep(0.5)
            remaining = self._read_free_summon_count(
                self.O_EVENT_FREE_QUOTA, self.I_EVENT_ONE_TICKET)
            if remaining is None or remaining <= 0:
                logger.warning('Event single summon has no verified free attempt; stop drawing')
                return False

        # Keep the canvas-settling delay used by the original single draw.
        time.sleep(0.5)
        deadline = time.monotonic() + 90
        drawn = False
        confirmed = False
        while time.monotonic() < deadline:
            self.screenshot()
            result = next((marker for marker in confirmations if self.appear(marker)), None)
            if confirmed:
                if result is None:
                    return True
                continue
            if drawn and result is not None:
                self.click(result)
                confirmed = True
                continue
            canvas_ready = not drawn and (
                self._event_summon_canvas_appear() if event_canvas else self.appear(single_marker))
            if canvas_ready:
                if self.appear_then_click(self.I_UI_CANCEL, interval=0.8):
                    continue
                if draw_mystery_pattern:
                    self.summon_mystery_pattern()
                else:
                    self.summon()
                drawn = True
        logger.warning('Free summon result was not confirmed; stop drawing')
        return False

    def _summon_free_until_empty(self, counter, main_marker, single_marker,
                                 confirmations, draw_mystery_pattern=False) -> FreeSummonResult:
        completed = 0
        previous = None
        # Quotas are capped at ten and must decrease after each confirmed draw.
        for _ in range(11):
            if not self.back_summon_main(main_marker=main_marker):
                return FreeSummonResult(completed)
            remaining = self._read_free_summon_count(counter, main_marker, previous)
            if remaining is None:
                return FreeSummonResult(completed)
            if remaining == 0:
                logger.info(f'Free summons exhausted, completed {completed} this run')
                return FreeSummonResult(completed, exhausted=True)
            if not self._perform_free_summon(
                    main_marker, single_marker, confirmations,
                    draw_mystery_pattern=draw_mystery_pattern and completed == 0):
                return FreeSummonResult(completed)
            completed += 1
            previous = remaining
            if remaining == 1:
                # The last free draw restores the ordinary menu caption on
                # some skins, rather than showing 0/2. A confirmed result has
                # consumed that verified last attempt; only require returning.
                returned = self.back_summon_main(main_marker=main_marker)
                logger.info(f'Last verified free summon confirmed; completed {completed} this run')
                return FreeSummonResult(completed, exhausted=returned)
            logger.info(f'Free summon {completed} confirmed; return to menu and recheck quota')
        return FreeSummonResult(completed)

    def summon_one(self, draw_mystery_pattern=False) -> FreeSummonResult:
        """Consume verified free summons, including multiple event attempts."""
        for swipe in (self.S_RANDOM_SWIPE_1, self.S_RANDOM_SWIPE_2,
                      self.S_RANDOM_SWIPE_3, self.S_RANDOM_SWIPE_4):
            swipe.name = 'S_RANDOM_SWIPE'
        return self._summon_free_until_empty(
            self.O_ONE_TICKET, self.I_BLUE_TICKET, self.I_ONE_TICKET,
            (self.I_SM_CONFIRM, self.I_SM_CONFIRM_2), draw_mystery_pattern)

    def back_summon_main(self, main_marker=None) -> bool:
        """Return to the selected summon menu before reading its free quota."""
        main_marker = self.I_BLUE_TICKET if main_marker is None else main_marker
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.screenshot()
            if self.appear(main_marker):
                return True
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue
        logger.warning('Could not return to summon menu; quota remains unverified')
        return False
