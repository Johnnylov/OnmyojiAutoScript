# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from datetime import datetime

from module.atom.click import RuleClick
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_daily
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.TalismanPass.assets import TalismanPassAssets
from tasks.TalismanPass.config import TalismanConfig, LevelReward

from module.logger import logger
from module.exception import TaskEnd
from module.base.timer import Timer


class ScriptTask(GameUi, TalismanPassAssets):

    C_MONTHLY_SKIN_SKIP = RuleClick(
        roi_front=(1150, 25, 80, 55),
        roi_back=(1150, 25, 80, 55),
        name='talisman_monthly_skin_skip',
    )

    def run(self):
        self.prepare_monthly_skin_intro()
        self.goto_page(page_daily)
        con: TalismanConfig = self.config.talisman_pass.talisman

        # 收取任务全部奖励
        if self.in_task():
            self.get_all()
        # 收取花合战等级奖励
        if con.get_flower:
            self.get_flower(con.level_reward)
        # 收取1500签御魂
        if con.harvest_soul:
            self.goto_page(page_main)
            self.harvest_soul()
        self.goto_page(page_main)
        self.set_next_run(task='TalismanPass', success=True, finish=True)
        raise TaskEnd('TalismanPass')

    def prepare_monthly_skin_intro(self):
        """仅在每月 1 日注册花合战新皮肤展示的关闭动作。"""
        if datetime.now().day != 1:
            return

        logger.info('Enable first-day talisman pass skin intro handler')
        self.navigator.add_unknown_closer(self.close_monthly_skin_intro)

        daily_page = self.navigator.resolve_page(page_daily)
        if daily_page is not None:
            # 从庭院进入花合战后，动画会先导致目标页到达判定失败。
            # 放在默认失败钩子之前点击“跳过”，后续默认空白点击即可关闭皮肤展示。
            daily_page.on_enter_failure.insert(0, self.close_monthly_skin_intro)

    def close_monthly_skin_intro(self) -> bool:
        """跳过月初动画；也可通过点击空白处关闭紧随其后的皮肤展示。"""
        if datetime.now().day != 1 or self.appear(self.I_CHECK_MAIN):
            return False

        logger.info('Close first-day talisman pass skin intro')
        clicked = self.click(self.C_MONTHLY_SKIN_SKIP, interval=1)
        if not clicked:
            return False

        # 部分动画会在点击“跳过”后出现二次确认。
        time.sleep(0.8)
        self.screenshot()
        self.appear_then_click(ActivityShikigamiAssets.I_CONFIRM_SKIP, interval=0.8)
        return True

    def get_all(self):
        """
        一键收取所有的
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_TP_GET_ALL):
            logger.info('No appear get all button')
        self.ui_get_reward(self.I_TP_GET_ALL)
        logger.info('Get all reward')
        time.sleep(0.5)

    def get_flower(self, level: LevelReward = LevelReward.TWO):
        """
        收取花合战等级奖励
        :return:
        """
        match_level = {
            LevelReward.ONE: self.I_TP_LEVEL_1,
            LevelReward.TWO: self.I_TP_LEVEL_2,
            LevelReward.THREE: self.I_TP_LEVEL_3,
        }
        self.screenshot()
        if not self.appear(self.I_RED_POINT_LEVEL):
            logger.info('No any level reward')
            return
        logger.info('Appear level reward')
        self.ui_click(self.I_RED_POINT_LEVEL, self.I_TP_GET_ALL)
        logger.info('Click level reward')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()
            if self.appear_then_click(match_level[level], interval=0.8):
                logger.info(f'Select {level} reward')
                if self.appear_then_click(self.I_OVERFLOW_CONFIRME):
                    pass
                check_timer.reset()
                continue

            if self.ui_reward_appear_click(False):
                logger.info('Get reward')
                check_timer.reset()
                continue
            if check_timer.reached():
                logger.warning('No reward and break')
                break
            if self.appear_then_click(self.I_TP_GET_ALL, interval=2.1):
                logger.info('Get all reward')
                check_timer.reset()
                continue

    def in_task(self) -> bool:
        """
        判断是否在任务的界面
        :return:
        """
        self.screenshot()
        if self.appear(self.I_TP_GOTO) or self.appear(self.I_TP_EXP):
            return True
        if self.appear(self.I_RED_POINT_TASK):
            self.click(self.I_RED_POINT_TASK)
            logger.info('Appear task reward')
            return True
        logger.info('No any task reward')
        return False
    
    def harvest_soul(self):
        """
        获得1500签御魂奖励
        :return: 如果没有发现御魂奖励则退出
        """
        logger.hr('Harvest soul')
        timer_harvest = Timer(5)  # 如果连续5秒没有发现任何奖励，退出
        while 1:
            self.screenshot()
            # 自选御魂
            if self.appear(self.I_TP_SOUL_1):
                logger.info('Select soul 2')
                self.ui_click(self.I_TP_SOUL_1, stop=self.I_TP_SOUL_2)
                self.ui_click(self.I_TP_SOUL_2, stop=self.I_TP_SOUL_3, interval=3)
                self.ui_click_until_disappear(click=self.I_TP_SOUL_3)
                timer_harvest.reset()
            # 五秒内没有发现任何奖励，退出
            if not timer_harvest.started():
                timer_harvest.start()
            else:
                if timer_harvest.reached():
                    logger.info('No more reward')
                    return



if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()

