"""Per-account preparation before joining a Bondling team.

Only the random-soul offer may spend bond memory. Summoning uses existing
stones; its out-of-stones shop must always be dismissed, never purchased.
All transitions are bounded, and an unreadable amount is not treated as zero.
"""
import re
from time import monotonic, sleep

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskDeferred
from module.logger import logger
from tasks.Component.Buy.assets import BuyAssets
from tasks.GameUi.page import page_mall, page_bondling_fairyland


def text_rule(name, roi, keyword='', mode='Single'):
    return RuleOcr(roi=roi, area=roi, mode=mode, method='Default', keyword=keyword, name=name)


def compact(text):
    return re.sub(r'\s+', '', str(text))


def number(text):
    text = compact(text)
    return int(text) if re.fullmatch(r'\d{1,5}', text) else None


def valid_counter(counter):
    if not isinstance(counter, (tuple, list)) or len(counter) != 3 or not all(type(v) is int for v in counter):
        return False
    current, remaining, total = counter
    return total > 0 and 0 <= current <= total and current + remaining == total


class BondlingPreparation:
    # Reuse checked-in button artwork, with independent names/ROIs. The shop
    # and summon dialogs have different positions and must not share I_BUY_*.
    I_MEMORY_MAX = BuyAssets.I_BUY_PLUS
    I_MEMORY_SUB = BuyAssets.I_BUY_SUB
    C_MEMORY_PAY = BuyAssets.C_BUY_MORE
    O_MEMORY_TITLE = text_rule('bondling_memory_title', (450, 65, 395, 120), '随机御魂', 'Full')
    O_STONE_SHOP = text_rule('bondling_stone_shop', (470, 95, 345, 95), '鸣契石', 'Full')
    O_MEMORY_QUANTITY = text_rule('bondling_memory_quantity', (568, 416, 92, 101))
    O_MEMORY_PRICE = text_rule('bondling_memory_price', (643, 525, 70, 78))
    O_SUMMON_ENTRY = text_rule('bondling_summon_entry', (287, 392, 120, 118), '召唤', 'Full')
    O_SUMMON_CONFIRM = text_rule('bondling_summon_confirm', (420, 288, 440, 66))
    I_SUMMON_MAX = RuleImage(roi_front=(765, 543, 43, 40), roi_back=(730, 520, 105, 90),
        threshold=.8, method='Template matching', file='./tasks/BondlingFairyland/stone/buy_plus.png')
    C_PREP_DISMISS = RuleClick(roi_front=(940, 280, 35, 60), roi_back=(940, 280, 35, 60),
                             name='bondling_dismiss_purchase')

    def _prep_frames(self, description, seconds=30):
        deadline = monotonic() + seconds
        # A frame cap also bounds a faulty or frozen screenshot source.
        for _ in range(max(10, int(seconds * 5))):
            if monotonic() >= deadline:
                break
            self.screenshot()
            yield
            sleep(.2)
        raise TaskDeferred(f'契灵准备超时：{description}，本轮未完成，稍后重试')

    def read_bond_memory(self):
        previous = None
        for _ in self._prep_frames('读取契忆余额', 12):
            value = self.O_BL_CHECK_MONEY.ocr_digit_counter(self.device.image)
            if valid_counter(value):
                if previous == value:
                    return value[0]
                previous = value
            else:
                previous = None

    def _memory_dialog(self):
        return bool(self.ocr_appear(self.O_MEMORY_TITLE, exact=True))

    def _stone_shop(self):
        return bool(self.ocr_appear(self.O_STONE_SHOP, exact=True))

    def close_bondling_dialogs(self):
        """Dismiss a shortage shop first; never click its currency button."""
        for _ in self._prep_frames('关闭兑换或召唤窗口', 20):
            if self._stone_shop() or self._memory_dialog():
                self.click(self.C_PREP_DISMISS, interval=1)
                continue
            if self.appear(self.I_STONE_CLOSE):
                self.appear_then_click(self.I_STONE_CLOSE, interval=1)
                continue
            if self.appear(self.I_MALL_BONDLINGS_ON) or self.appear(self.I_BALL_HELP) or self.in_search_ui():
                return

    def _open_memory_offer(self):
        missing = 0
        for _ in self._prep_frames('打开随机御魂兑换', 15):
            if self._memory_dialog():
                return True
            if self.appear_then_click(self.I_BL_BUY_SOULS, interval=1):
                missing = 0
            elif self.appear(self.I_MALL_BONDLINGS_ON):
                missing += 1
                if missing >= 5:
                    logger.info('随机御魂兑换不可用或已达每周限购，保留剩余契忆')
                    return False

    def _memory_quantity(self):
        previous = None
        for _ in self._prep_frames('读取兑换数量', 10):
            if not self._memory_dialog():
                raise TaskDeferred('随机御魂兑换窗口已变化，未继续购买')
            quantity = number(self.O_MEMORY_QUANTITY.ocr(self.device.image))
            if quantity is not None and quantity == previous:
                return quantity
            previous = quantity

    def _choose_memory_quantity(self, affordable):
        for _ in self._prep_frames('选择最大兑换数量', 10):
            if not self._memory_dialog():
                raise TaskDeferred('未识别到随机御魂兑换窗口，未继续购买')
            if self.appear_then_click(self.I_MEMORY_MAX, interval=.8):
                sleep(.5)
                break
        quantity = self._memory_quantity()
        if quantity == 0:
            return 0
        if quantity > 200:
            raise TaskDeferred('随机御魂单次数量异常，未继续购买')
        # The game clamps the maximum to its per-purchase and weekly limits.
        # Clamp it further to the actual balance instead of buying stones or
        # retrying an unaffordable purchase indefinitely.
        while quantity > affordable:
            before = quantity
            if not self.appear_then_click(self.I_MEMORY_SUB, interval=.2):
                raise TaskDeferred('无法调整随机御魂兑换数量，未继续购买')
            quantity = self._memory_quantity()
            if quantity != before - 1:
                raise TaskDeferred('随机御魂兑换数量未按预期减少，未继续购买')
        return quantity

    def _pay_memory_offer(self, quantity):
        for _ in self._prep_frames('核验随机御魂兑换价格', 10):
            if not self._memory_dialog():
                raise TaskDeferred('随机御魂兑换窗口已变化，未继续购买')
            price = number(self.O_MEMORY_PRICE.ocr(self.device.image))
            if price == quantity * 20:
                self.click(self.C_MEMORY_PAY)
                break
        # Submit once. A delayed response is never permission to buy twice.
        for _ in self._prep_frames('等待随机御魂兑换结果', 30):
            if self.ui_reward_appear_click():
                continue
            if self._memory_dialog():
                continue
            if self.appear(self.I_MALL_BONDLINGS_ON):
                return

    def prepare_bond_memory(self):
        """Read -> redeem affordable random souls -> read the new baseline."""
        self.goto_page(page_mall, confirm_wait=2.5)
        self.ui_click(self.I_MALL_SCCALES, self.I_MALL_SCCALES_CHECK)
        self.ui_click(self.I_MALL_BONDLINGS_SURE, self.I_MALL_BONDLINGS_ON)
        before = balance = self.read_bond_memory()
        logger.info(f'兑换前契忆：{before}')
        # Each successful exchange must lower the balance. Even one-item
        # batches cannot exceed this budget; inventory/caps come from the UI.
        for _ in range(before // 20):
            if balance < 20 or not self._open_memory_offer():
                break
            quantity = self._choose_memory_quantity(balance // 20)
            if quantity == 0:
                self.close_bondling_dialogs()
                break
            self._pay_memory_offer(quantity)
            after = self.read_bond_memory()
            if after != balance - quantity * 20:
                raise TaskDeferred('兑换后的契忆变化无法核验，未重复兑换；稍后重新检查')
            balance = after
            self.device.click_record_clear()
        # Always re-read, including the <20 and sold-out cases.
        self.initial_bond_memory = self.read_bond_memory()
        logger.info(f'本轮起始契忆：{self.initial_bond_memory}（兑换前 {before}）')
        return self.initial_bond_memory

    def _open_target_summon(self, index):
        target = (self.C_STONE_1, self.C_STONE_2, self.C_STONE_3, self.C_STONE_4)[(index - 1) % 4]
        for _ in self._prep_frames('打开目标契灵的召唤窗口', 30):
            if self._stone_shop():
                self.close_bondling_dialogs()
                return False
            if self.appear(self.I_STONE_SURE):
                return True
            if self.appear(self.I_BALL_HELP):
                self.ocr_appear_click(self.O_SUMMON_ENTRY, interval=1, exact=True)
            elif self.in_search_ui():
                self.click(target, interval=1)

    def _summon_existing_stones(self, species):
        # No pre-check may skip the summon attempt: zero inventory is handled
        # by the game's shortage dialog, including when a target already exists.
        for _ in self._prep_frames('选择召唤数量', 12):
            if self._stone_shop():
                self.close_bondling_dialogs()
                return False
            if self.appear_then_click(self.I_SUMMON_MAX, interval=.8):
                sleep(.5)
                break
        submitted = False
        confirmed = False
        for _ in self._prep_frames('确认召唤现有鸣契石', 30):
            if self._stone_shop():
                logger.info('鸣契石已用完，关闭购买窗口并进入刷取流程')
                self.close_bondling_dialogs()
                return False
            if self.appear(self.I_GI_SURE):
                message = compact(self.O_SUMMON_CONFIRM.ocr(self.device.image)).replace('镇墓善', '镇墓兽')
                if '鸣契石' not in message or '召唤' not in message or species not in message:
                    raise TaskDeferred('召唤确认信息与本轮契灵不一致，未确认消耗')
                if self.appear_then_click(self.I_GI_SURE, interval=1):
                    confirmed = True
                continue
            if confirmed and not self.appear(self.I_STONE_SURE) and self.appear(self.I_BALL_HELP):
                return True
            if not submitted and self.appear_then_click(self.I_STONE_SURE, interval=1):
                submitted = True

    def prepare_bond_summons(self, index, species):
        logger.hr(f'使用现有鸣契石召唤：{species}', 2)
        # The game can limit a batch. Re-open after each success until the
        # zero-stone purchase overlay appears; never purchase that offer.
        for _ in range(40):
            if not self._open_target_summon(index) or not self._summon_existing_stones(species):
                self.goto_page(page_bondling_fairyland)
                return
            self.device.click_record_clear()
        raise TaskDeferred('召唤批次数异常，未进入战斗；稍后检查剩余鸣契石')
