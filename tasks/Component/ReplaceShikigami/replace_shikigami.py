# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from module.base.timer import Timer
from module.logger import logger

from tasks.base_task import BaseTask
from tasks.Utils.config_enum import ShikigamiClass
from tasks.Component.ReplaceShikigami.assets import ReplaceShikigamiAssets
import time
from module.exception import GameStuckError


class ReplaceShikigami(BaseTask, ReplaceShikigamiAssets):

    def in_shikigami_growth(self, screenshot=False) -> bool:
        # 判定是否在式神育成界面
        # 判定的依据是是否出现了 式神录 这个图片
        if screenshot:
            self.screenshot()
        return self.appear(self.I_RS_RECORDS_SHIKI, interval=0.5)

    def switch_shikigami_class(self, shikigami_class: ShikigamiClass = ShikigamiClass.N):
        """
        要求在式神育成的界面
        切换分类
        :param shikigami_class:
        :param shikigami_order:
        :return:
        """
        match_selected = {ShikigamiClass.MATERIAL: self.I_RS_MATERIAL_SELECTED,
                          ShikigamiClass.N: self.I_RS_N_SELECTED,
                          ShikigamiClass.R: self.I_RS_R_SELECTED,
                          ShikigamiClass.SR: self.I_RS_SR_SELECTED,
                          ShikigamiClass.SSR: self.I_RS_SSR_SELECTED,
                          ShikigamiClass.SP: self.I_RS_SP_SELECTED,
                          ShikigamiClass.UR: self.I_RS_UR_SELECTED}
        match_click = {ShikigamiClass.MATERIAL: self.I_RS_MATERIAL,
                       ShikigamiClass.N: self.I_RS_N,
                       ShikigamiClass.R: self.I_RS_R,
                       ShikigamiClass.SR: self.I_RS_SR,
                       ShikigamiClass.SSR: self.I_RS_SSR,
                       ShikigamiClass.SP: self.I_RS_SP,
                       ShikigamiClass.UR: self.I_RS_UR}
        check_selected = match_selected[shikigami_class]
        check_click = match_click[shikigami_class]
        # 选择式神的种类
        while 1:
            self.screenshot()
            if self.appear(check_selected, interval=1):
                break
            if self.appear(check_click, interval=3):
                if self.wait_until_pos_stable(check_click, stable_time=0.8, timeout=2.5):
                    self.click(check_click)
                continue
            if self.appear_then_click(self.I_RS_ALL_SELECTED, interval=5):
                continue
        logger.info('Select shikigami class: %s' % shikigami_class)

    def unset_shikigami_max_lv(self):
        """
        要求在式神育成的界面
        拉下满级的式神，留空位置
        :return:
        """
        while 1:
            self.screenshot()
            if not self.appear(self.I_RS_LEVEL_MAX):
                break
            else:
                self.appear_then_click(self.I_RS_LEVEL_MAX, interval=0.5)
        logger.info('Unset all shikigami max lv')

    def set_shikigami(self, shikigami_order: int = 7, stop_image: RuleImage = None) -> bool:
        """
        要求在式神育成的界面
        选择式神 1-7
        :param stop_image:  结束的图片，如果不出现就结束
        :param shikigami_order:
        :return: True: 寄养成功(坑位已占用) False: 寄养连续失败(坑位被抢/已寄养其他式神)
        """
        # 选择式神
        _click_match = {1: self.C_SHIKIGAMI_LEFT_1,
                        2: self.C_SHIKIGAMI_LEFT_2,
                        3: self.C_SHIKIGAMI_LEFT_3,
                        4: self.C_SHIKIGAMI_LEFT_4,
                        5: self.C_SHIKIGAMI_LEFT_5,
                        6: self.C_SHIKIGAMI_LEFT_6,
                        7: self.C_SHIKIGAMI_LEFT_7}
        click_match = _click_match[shikigami_order]
        TIMEOUT_SEC = 120          # 超时时长（秒）
        start_time = time.time()   # 记录起始时间
        click_interval_timer = Timer(1.5).start()  # 点击选择式神间隔
        clicked = False
        confirm_count = 0  # 确认按钮点击次数, 成功寄养后坑位消失会跳出循环, 连续点击说明寄养一直失败
        select_count = 0  # 连续选择式神但未出现确认按钮的次数
        while 1:
            # ——1. 先做超时检查——
            if time.time() - start_time > TIMEOUT_SEC:
                logger.error('寄养等待超过 2 分钟，自动退出')
                raise GameStuckError('寄养超时（>120 s）')
            # 恢复点击操作
            if click_interval_timer.reached_and_reset():
                clicked = False
            self.screenshot()
            if self.appear_then_click(self.I_U_CONFIRM_SMALL, interval=0.5):
                clicked = False  # 点击了确认, 恢复选式神的操作
                confirm_count += 1
                select_count = 0  # 确认流程正常, 重置选择计数
                if confirm_count >= 4:
                    # 寄养成功坑位会消失并跳出循环, 连续点确认说明寄养一直失败
                    # (如坑位同时被别人寄养/已寄养其他式神), 退出避免死循环
                    logger.warning('寄养连续失败, 可能是坑位被抢或已寄养其他式神, 退出寄养')
                    return False
                time.sleep(1.5)  # 等待寄养结果, 避免成功动画期间重复点击
                continue
            if not self.appear(stop_image):
                break
            # 与下方点击第7个式神操作互斥, 防止确认按钮还没有出现被下方取消掉
            if not clicked and self.click(click_match, interval=1.5):
                clicked = True
                select_count += 1
                if select_count >= 5:
                    # 连续点了多次式神都没弹出确认框, 该位置的式神无法寄养
                    # (已被育成/寄养到别处), 退出避免重复点击触发 GameTooManyClickError
                    logger.warning('连续选择式神未出现确认按钮, 该位置式神可能已被育成或占用, 退出寄养')
                    return False
                continue
            if not clicked and self.click(_click_match[6], interval=4.5):
                # 有的时候第七个格子被占用到寄养上去了
                # 导致一直无法选上
                clicked = True
                select_count += 1
                if select_count >= 5:
                    logger.warning('连续选择式神未出现确认按钮, 该位置式神可能已被育成或占用, 退出寄养')
                    return False
                continue
            if self.appear_then_click(self.I_U_CIRCLE_ALTERNATE, interval=2.5):
                self.appear_then_click(self.I_U_CONFIRM_ALTERNATE, interval=1.5)
                continue
        logger.info('Set shikigami: %d' % shikigami_order)
        return True

    def detect_no_shikigami(self) -> bool:
        self.screenshot()
        if self.appear(self.I_DETECT_EMPTY_1)\
            or self.appear(self.I_DETECT_EMPTY_2) \
                or self.appear(self.I_DETECT_EMPTY_3) \
                or self.appear(self.I_DETECT_EMPTY_4) \
                or self.appear(self.I_DETECT_EMPTY_5) \
                or self.appear(self.I_DETECT_EMPTY_6):
            return True
        return False


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('日常2')
    d = Device(c)
    t = ReplaceShikigami(c, d)
    t.switch_shikigami_class(ShikigamiClass.N)
