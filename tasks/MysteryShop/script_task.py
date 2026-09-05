# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import random
from time import sleep
from datetime import timedelta, datetime
from cached_property import cached_property

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.swipe import RuleSwipe
from module.exception import GameStuckError, RequestHumanTakeover, TaskEnd
from module.image.rpc import get_image_client
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.page import page_main, page_mall
from tasks.GameUi.game_ui import GameUi
from tasks.RichMan.mall.friendship_points import FriendshipPoints
from tasks.MysteryShop.config import MysteryShop, ShopConfig
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.MysteryShop.schedule import MysteryShopSchedule
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.GeneralInvite.config_invite import InviteConfig

class ScriptTask(FriendshipPoints, MysteryShopAssets, GeneralInvite):
    OPEN_WEEKDAYS = (2, 5)
    MAX_SHOP_SWIPES = 8
    SHOP_STABLE_TIMEOUT = 5
    SHOP_BOUNDARY_CONFIRMATIONS = 2
    SHOP_IMAGE_PADDING = 4
    SHOP_SWIPE_DURATION = 2
    SHOP_SWIPE_WAIT = 2

    # 任务可单独重载，不依赖被缓存的旧 Assets 类新增成员。
    C_MS_GOODS = RuleClick(roi_front=(165, 70, 850, 490), roi_back=(165, 70, 850, 490), name='ms_goods')
    S_MS_DOWN = RuleSwipe(roi_front=(590, 455, 30, 20), roi_back=(590, 255, 30, 20),
                         mode='default', name='ms_down')
    S_MS_TO_TOP = RuleSwipe(roi_front=(590, 255, 30, 20), roi_back=(590, 455, 30, 20),
                           mode='default', name='ms_to_top')

    def run(self):
        logger.info('MysteryShop scroll mode: ADB 2s drag (KekkaiUtilize)')
        self._ensure_shop_due()
        self.goto_page(page_mall)
        self.ui_click(self.I_ME_ENTER, self.I_MS_SHARE)
        logger.info('Enter MysteryShop')
        con = self.config.mystery_shop
        self.share(con.invite_config)
        while 1:
            self.run_shop(con.shop_config)
            if not self.next_one():
                break
        self.shop_reward()
        logger.info('Exit MysteryShop')
        self.back_mall()


        self.next_time(True)

    def next_one(self):
        """
        切换下一个好友的商店
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_MS_NEXT):
            sleep(0.5)
            self.screenshot()
            if self.appear(self.I_MS_NEXT):
                pass
            else:
                logger.info('No next friend')
                return False

        own_page = self.appear(self.I_MS_SHARE)
        if own_page:
            while 1:
                self.screenshot()
                if not self.appear(self.I_MS_SHARE):
                    break
                if self.appear_then_click(self.I_MS_NEXT, interval=1):
                    continue
            logger.info('Switch to next friend')
            return True

        present_friend = self.O_MS_FRIEND.ocr(self.device.image)
        while 1:
            self.screenshot()
            next_friend = self.O_MS_FRIEND.ocr(self.device.image)
            if present_friend != next_friend:
                break
            if self.appear_then_click(self.I_MS_NEXT, interval=2.5):
                continue

        logger.info('Switch to next friend')
        return True



    def run_shop(self, shop_config: ShopConfig = None):
        """逐屏购买当前商店，确认货架到底后才切换好友。"""
        self._ensure_shop_open()
        if shop_config is None:
            shop_config = self.config.mystery_shop.shop_config
        if not any(getattr(shop_config, key) for key, _, _, _ in self._shop_items):
            logger.info('All MysteryShop purchases are disabled')
            return
        self._rewind_shop()
        unchanged = 0
        for page_index in range(self.MAX_SHOP_SWIPES + 1):
            self._ensure_shop_open()
            self._wait_shop_stable()
            logger.info(f'MysteryShop scan shelf {page_index + 1}')
            self._buy_visible_items(shop_config)
            if page_index == self.MAX_SHOP_SWIPES:
                raise GameStuckError('MysteryShop shelf scan limit reached before confirming bottom')
            moved = self._swipe_shop(self.S_MS_DOWN)
            unchanged = 0 if moved else unchanged + 1
            if unchanged >= self.SHOP_BOUNDARY_CONFIRMATIONS:
                logger.info('MysteryShop shelf bottom confirmed, current shop scan complete')
                return

    @cached_property
    def _shop_items(self):
        """上边缘留出库存 OCR 高度，避免识别半截库存文字。"""
        items = (
            ('mystery_amulet', self.I_MS_BLUE, self.I_MS_CHECK_BLUE, 85),
            ('black_daruma_scrap', self.I_MS_BLACK, self.I_MS_CHECK_BLACK, 60),
            ('shop_kaiko_3', self.I_MS_TAIKO_3, self.I_MS_CHECK_TAIKO_3, 45),
            ('shop_kaiko_4', self.I_MS_TAIKO_4, self.I_MS_CHECK_TAIKO_4, 80),
        )
        result = []
        safe_top = self.C_MS_GOODS.roi_back[1] + self.O_SP_RES_NUMBER.roi[3]
        for key, button, check, money in items:
            x, y, width, height = button.roi_back
            top = max(y, safe_top)
            target = RuleImage(roi_front=tuple(button.roi_front), roi_back=(x, top, width, y + height - top),
                               method=button.method, threshold=button.threshold, file=button.file)
            result.append((key, target, check, money))
        return result

    def _buy_visible_items(self, shop_config: ShopConfig):
        """当前商品未出现/售罄/余额不足不结束整家店的扫描。"""
        for key, button, check, money in self._shop_items:
            if not getattr(shop_config, key):
                continue
            while True:
                self._ensure_shop_open()
                if not self.buy_mall_one(buy_button=button, buy_check=check,
                                         money_ocr=self.O_MALL_RESOURCE_5, buy_money=money):
                    break

    def _wait_shop_stable(self):
        """只比较货架，排除右侧人物、倒计时和底部奖励动画。"""
        previous = None
        timeout = Timer(self.SHOP_STABLE_TIMEOUT).start()
        stable_count = 0
        reason = 'waiting for shop'
        while not timeout.reached():
            self._ensure_shop_open()
            self.screenshot()
            if getattr(self.device.image, 'shape', None) != (720, 1280, 3):
                reason = 'invalid screenshot size'
            else:
                # 正常货架也有魂玉价格，不能用 I_BUY_RMB 判断是否弹窗。
                # 真正购买仍由 buy_one 保留魂玉/人民币拦截。
                popup = next((marker for marker in (
                    self.I_BUY_PLUS, self.I_BUY_SUCCESS, self.I_UI_REWARD, self.I_INVITE_ENSURE
                ) if self.appear(marker)), None)
                if popup is not None:
                    reason = f'popup {popup.name}'
                elif not (self.appear(self.I_MS_SHARE) or self.appear(self.I_MS_BEFORE)
                          or self.appear(self.I_MS_NEXT)):
                    reason = 'shop anchor not visible'
                else:
                    reason = 'shelf still moving'
                    stable = previous is not None and self._shop_frame_matches(previous, threshold=0.98)
                    stable_count = stable_count + 1 if stable else 0
                    previous = self._shop_template()
                    if stable_count >= 2:
                        return
                    sleep(0.2)
                    continue
            previous = None
            stable_count = 0
            sleep(0.2)
        raise GameStuckError(f'MysteryShop shelf wait timeout: {reason}')

    def _shop_template(self):
        """内缩模板留出 4px 搜索余量，容忍货架轻微摆动。"""
        x, y, width, height = self.C_MS_GOODS.roi_back
        pad = self.SHOP_IMAGE_PADDING
        return self.device.image[y + pad:y + height - pad, x + pad:x + width - pad].copy()

    def _shop_frame_matches(self, template, threshold: float) -> bool:
        result = get_image_client().match_dynamic_template(
            template=template, image=self.device.image, frame_id=self.device.image_frame_id,
            roi_back=self.C_MS_GOODS.roi_back, threshold=threshold, name='ms_shelf',
        )
        return bool(result.get('matched'))

    def _swipe_shop(self, swipe) -> bool:
        """复用结界寄养的 ADB 慢拖动、清理点击记录及等待流程。"""
        self._ensure_shop_open()
        self._wait_shop_stable()
        before = self._shop_template()
        x, y, width, height = swipe.roi_front
        p1 = (random.randint(x, x + width - 1), random.randint(y, y + height - 1))
        # 保持竖直拖动；商店每次 200px 留重叠，避免照搬寄养 416px 后漏掉商品。
        p2 = (p1[0], p1[1] + swipe.roi_back[1] - y)
        logger.info(f'MysteryShop swipe {swipe.name}: ADB {p1} -> {p2}, {self.SHOP_SWIPE_DURATION}s')
        # swipe_adb 成功返回 None，失败抛异常，不能按布尔值判断成功。
        self.device.swipe_adb(p1, p2, duration=self.SHOP_SWIPE_DURATION)
        self.device.click_record_clear()
        sleep(self.SHOP_SWIPE_WAIT)
        self._wait_shop_stable()
        moved = not self._shop_frame_matches(before, threshold=0.985)
        logger.info(f'MysteryShop {swipe.name}: shelf moved={moved}')
        return moved

    def _rewind_shop(self):
        unchanged = 0
        for _ in range(self.MAX_SHOP_SWIPES):
            moved = self._swipe_shop(self.S_MS_TO_TOP)
            unchanged = 0 if moved else unchanged + 1
            if unchanged >= self.SHOP_BOUNDARY_CONFIRMATIONS:
                logger.info('MysteryShop shelf top confirmed')
                return
        raise GameStuckError('MysteryShop could not confirm shelf top within swipe limit')

    def _ensure_shop_open(self):
        if datetime.now().weekday() not in self.OPEN_WEEKDAYS:
            logger.info('MysteryShop only runs on Wednesday and Saturday, reschedule without shopping')
            self.next_time(False)

    @cached_property
    def _independent_schedule(self):
        return MysteryShopSchedule(self.config.config_name)

    def _ensure_shop_due(self):
        """OASX 重写通用 next_run 也不能提前触发已完成的商店。"""
        now = datetime.now()
        try:
            next_run = self._independent_schedule.resolve_next_run(now)
        except (OSError, ValueError) as exc:
            raise RequestHumanTakeover(f'Cannot read MysteryShop independent schedule: {exc}') from exc
        if next_run is not None and now < next_run:
            logger.info(f'MysteryShop independent next run: {next_run}; skip external early trigger')
            self.set_next_run(task='MysteryShop', target=next_run, server=False, finish=True)
            raise TaskEnd('MysteryShop')
        self._ensure_shop_open()

    def share(self, invite_config: InviteConfig = None):
        logger.hr('Share', 3)
        if len(invite_config.friend_list_v) == 0:
            logger.info('Share is disabled')
            return
        self.ui_click(self.I_MS_SHARE, self.I_INVITE_ENSURE)
        self.invite_friends(invite_config, False, self.I_INVITE_ENSURE)

    def shop_reward(self):
        logger.info('Shop reward')
        self.screenshot()
        number = self.O_MS_RECORDS.ocr(self.device.image)
        if not isinstance(number, int):
            logger.warning('No shop reward')
            return
        logger.info(f'Shop reward {number}')
        if number >= 3:
            self.ui_get_reward(self.I_MS_REWARD_3)
            sleep(0.5)
        if number >= 5:
            self.ui_get_reward(self.I_MS_REWARD_5)
            sleep(0.5)
        if number >= 10:
            self.ui_get_reward(self.I_MS_REWARD_10)
            sleep(0.5)
        logger.info('Shop reward done')

    def next_time(self, success: bool = True):
        """下一次只安排在周三/周六，成功完成当天后不再重复购买。"""
        target_time = self.config.mystery_shop.shop_config.time_of_mystery
        now = datetime.now()
        for offset in range(8):
            candidate = datetime.combine(now.date() + timedelta(days=offset), target_time)
            if candidate.weekday() not in self.OPEN_WEEKDAYS or candidate <= now:
                continue
            if success and offset == 0:
                continue
            if success:
                try:
                    self._independent_schedule.write_next_run(candidate)
                except (OSError, ValueError) as exc:
                    raise RequestHumanTakeover(f'Cannot save MysteryShop independent schedule: {exc}') from exc
                logger.info(f'MysteryShop saved independent next run: {candidate}')
            self.set_next_run(task='MysteryShop', target=candidate, server=False, finish=True)
            raise TaskEnd('MysteryShop')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    # t.run_shop(t.config.mystery_shop.shop_config)
    t.run()

