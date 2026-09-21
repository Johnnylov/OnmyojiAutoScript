# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import time, datetime, timedelta

from module.logger import logger
from module.exception import TaskEnd, TaskDeferred
from module.atom.click import RuleClick
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_delegation
from tasks.Delegation.config import DelegationConfig
from tasks.Delegation.assets import DelegationAssets


class ScriptTask(GameUi, DelegationAssets):

    def run(self):
        self.goto_page(page_delegation)
        self.check_reward()
        con: DelegationConfig = self.config.delegation.delegation_config
        if con.miyoshino_painting:
            self.delegate_one('画')
        if con.bird_feather:
            self.delegate_one('鸟羽')
        if con.find_earring:
            self.delegate_one('寻找耳环')
        if con.cat_boss:
            self.delegate_one('猫老大')
        if con.miyoshino:
            self.delegate_one('接送')
        if con.strange_trace:
            self.delegate_one('痕迹')


        self.set_next_run(task='Delegation', success=True, finish=True)
        raise TaskEnd

    def delegate_one(self, name: str) -> bool:
        """
        委派一个任务
        :param name:
        :return:
        """
        def ui_click(click, stop):
            while 1:
                self.screenshot()
                if self.appear(stop):
                    break
                if self.click(click, interval=1.5):
                    continue
        logger.hr('Delegation one', 2)
        self.O_D_NAME.keyword = name
        self.screenshot()
        if not self.ocr_appear(self.O_D_NAME):
            logger.warning(f'Delegation: {name} not found')
            return False
        entry_timer = Timer(45).start()
        while not entry_timer.reached():
            self.screenshot()
            if self.appear(self.I_D_START):
                break
            if self.painting_dialogue_visible():
                # A completed story mission also appears in the mission list.
                # Claim its dialogue/reward before looking for a new assignment.
                self.check_reward()
                self.screenshot()
                if not self.ocr_appear(self.O_D_NAME):
                    logger.info(f'Delegation: {name} story reward collected')
                    return False
                continue
            # 如果出现’召回‘ ’返回‘ 说明这个是现在委派中
            # 需要退出
            if self.appear(self.I_D_BACK):
                logger.warning(f'Delegation: {name} is in delegation')
                self.ui_click_until_disappear(self.I_D_BACK)
                self.wait_until_appear(self.I_REWARDS_MIN)
                return False
            if self.appear_then_click(self.I_D_SKIP, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_CONFIRM, interval=0.8):
                continue
            if self.ocr_appear_click(self.O_D_NAME, interval=1):
                continue
        else:
            raise TaskDeferred(f'委派 {name} 未能进入出发页面，稍后重试')
        # 进入委派  fefe e  fe
        logger.info(f'Enter Delegation: {name}')
        ui_click(self.C_D_1, self.I_D_SELECT_1)
        ui_click(self.C_D_2, self.I_D_SELECT_2)
        ui_click(self.C_D_3, self.I_D_SELECT_3)
        ui_click(self.C_D_4, self.I_D_SELECT_4)
        # 委派开始
        logger.info(f'Delegation: {name} start')
        while 1:
            self.screenshot()
            if not self.appear(self.I_D_START):
                break
            if self.click(self.C_D_5, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_START, interval=1.8):
                continue
        # ui_click(self.C_D_5, self.I_D_SELECT_5)
        # self.ui_click_until_disappear(self.I_D_START)

    def click_completed_delegation(self):
        """Open one completed mission on the map, excluding the sidebar label."""
        rule = self.O_D_DONE
        results = rule.detect_and_ocr(self.device.image)
        for result in results:
            if result.ocr_text != rule.keyword:
                continue
            # Multiple completed missions must be opened one at a time. Full OCR
            # merges matching boxes, which can otherwise click between markers.
            x, y = result.box[0]
            right, bottom = result.box[2]
            area = (int(rule.roi[0] + x), int(rule.roi[1] + y),
                    int(right - x), int(bottom - y))
            action = RuleClick(roi_front=area, roi_back=area, name=rule.name)
            return self.click(action, interval=1.5)
        return False

    def painting_dialogue_visible(self):
        """Require both the known delegation map and the dialogue overlay."""
        return self.appear(self.I_STORY_MAP) and self.appear(self.I_STORY_PANEL)

    def check_reward(self):
        check_timer = Timer(3)
        check_timer.start()
        recovery_timer = Timer(45).start()
        dialogue_clicks = 0
        while not recovery_timer.reached():
            self.screenshot()
            if self.painting_dialogue_visible():
                if dialogue_clicks >= 8:
                    raise TaskDeferred('委派剧情未能结束，保留任务稍后重试')
                if self.click(self.C_STORY_CONTINUE, interval=1):
                    dialogue_clicks += 1
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_GET, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_CHAT, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_1, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_2, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_DONE, interval=1):
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_FALSE, interval=1):
                check_timer.reset()
                continue


            if not self.appear(self.I_REWARDS_MIN):
                continue
            if self.click_completed_delegation():
                check_timer.reset()
                continue
            if check_timer.reached():
                return
        raise TaskDeferred('委派奖励页面未能恢复，保留任务稍后重试')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    from memory_profiler import profile
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    # t.delegate_one('弥助的画')
    t.run()

