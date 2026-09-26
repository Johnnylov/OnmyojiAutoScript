"""One maximum batch of plain soul boxes, verified against the snake-skin balance."""
import re
from time import monotonic, sleep

from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.exception import TaskDeferred, RequestHumanTakeover
from module.logger import logger


def text_rule(name, roi):
    return RuleOcr(roi=roi, area=roi, mode='Single', method='Default', keyword='', name=name)


def compact(value):
    return re.sub(r'\s+', '', str(value))


def number(value):
    value = compact(value)
    return int(value) if re.fullmatch(r'\d{1,6}', value) else None


class PlainSoulExchange:
    O_PLAIN_TITLE = text_rule('plain_soul_title', (530, 160, 225, 48))
    O_PLAIN_CARD = text_rule('plain_soul_card', (27, 444, 290, 48))
    O_PLAIN_QUANTITY = text_rule('plain_soul_quantity', (567, 421, 91, 73))
    O_PLAIN_PRICE = text_rule('plain_soul_price', (642, 532, 67, 35))
    # Exclude the currency icon, which OCR otherwise prefixes as a digit 1.
    O_PLAIN_BALANCE = text_rule('plain_soul_balance', (553, 12, 120, 31))
    C_PLAIN_OPEN = RuleClick(roi_front=(182, 492, 57, 26), roi_back=(182, 492, 57, 26),
                             name='plain_soul_exchange')
    C_PLAIN_PAY = RuleClick(roi_front=(563, 534, 139, 34), roi_back=(563, 534, 139, 34),
                            name='plain_soul_confirm')
    C_PLAIN_DISMISS = RuleClick(roi_front=(940, 280, 35, 60), roi_back=(940, 280, 35, 60),
                                name='plain_soul_dismiss')

    def _plain_frames(self, action, seconds=15):
        deadline = monotonic() + seconds
        for _ in range(int(seconds * 5)):
            if monotonic() >= deadline:
                break
            self.screenshot()
            yield
            sleep(.2)
        raise TaskDeferred(f'朴素御魂兑换超时：{action}')

    @staticmethod
    def _plain_name(value):
        return compact(value).strip('，,·') in ('朴素的御魂礼盒', '朴素御魂礼盒')

    def _plain_dialog(self):
        return self._plain_name(self.O_PLAIN_TITLE.ocr(self.device.image))

    def _plain_shop(self):
        return (not self._plain_dialog()
                and self._plain_name(self.O_PLAIN_CARD.ocr(self.device.image)))

    def _plain_balance(self):
        previous = None
        for _ in self._plain_frames('读取蛇皮余额'):
            value = self.O_PLAIN_BALANCE.ocr_digit_counter(self.device.image)
            valid = (isinstance(value, (tuple, list)) and len(value) == 3
                     and all(type(n) is int for n in value)
                     and value[2] > 0 and 0 <= value[0] <= value[2]
                     and value[0] + value[1] == value[2])
            if valid:
                if value == previous:
                    return value[0]
                previous = value
            else:
                previous = None

    def _plain_quantity(self):
        previous = None
        for _ in self._plain_frames('读取兑换数量', 10):
            if not self._plain_dialog():
                raise TaskDeferred('朴素御魂兑换窗口已变化，未确认购买')
            value = number(self.O_PLAIN_QUANTITY.ocr(self.device.image))
            if value is not None and value == previous:
                return value
            previous = value

    def _close_plain_offer(self):
        for _ in self._plain_frames('关闭兑换窗口'):
            if self._plain_shop():
                return
            if self._plain_dialog():
                self.click(self.C_PLAIN_DISMISS, interval=1)

    def exchange_plain_souls(self, enabled):
        if not enabled:
            return 0
        # Navigation is owned by Scales.execute_scales. A missing counter is
        # never zero; weekly availability is clamped by the game dialog.
        balance = self._plain_balance()
        if balance < 50:
            logger.info('紫色蛇皮不足 50，跳过朴素御魂兑换')
            return 0
        for _ in self._plain_frames('打开朴素御魂礼盒'):
            if self._plain_dialog():
                break
            if self._plain_shop():
                self.click(self.C_PLAIN_OPEN, interval=1)
        for _ in self._plain_frames('选择最大兑换数量', 10):
            if not self._plain_dialog():
                raise TaskDeferred('朴素御魂兑换窗口已变化，未确认购买')
            if self.appear_then_click(self.I_BUY_PLUS, interval=.8):
                sleep(.5)
                break
        quantity = self._plain_quantity()
        if quantity > 200:
            raise TaskDeferred('朴素御魂单次兑换数量异常，未确认购买')
        # Some versions cap MAX by weekly quota but not by the current balance.
        while quantity > balance // 50:
            previous = quantity
            if not self.appear_then_click(self.I_BUY_SUB, interval=.2):
                raise TaskDeferred('无法调整朴素御魂兑换数量，未确认购买')
            quantity = self._plain_quantity()
            if quantity != previous - 1:
                raise TaskDeferred('朴素御魂兑换数量未按预期减少，未确认购买')
        if quantity == 0:
            self._close_plain_offer()
            logger.info('朴素御魂礼盒已达兑换上限')
            return 0
        previous = None
        for _ in self._plain_frames('核验兑换价格', 10):
            if not self._plain_dialog():
                raise TaskDeferred('朴素御魂兑换窗口已变化，未确认购买')
            value = number(self.O_PLAIN_PRICE.ocr(self.device.image))
            if value == previous == quantity * 50:
                break
            previous = value
        # This transaction submits once. An uncertain response stops for review
        # instead of retrying the payment or buying another batch.
        if not self.click(self.C_PLAIN_PAY):
            raise TaskDeferred('朴素御魂兑换点击未送达')
        try:
            for _ in self._plain_frames('等待兑换结果', 30):
                if self._plain_dialog():
                    continue
                if self.appear(self.I_SCA_SIX_STAR) or self.appear(self.I_SCA_REWARD):
                    self.click(self.C_SCA_SOULS_GET, interval=1)
                    continue
                if self.ui_reward_appear_click():
                    continue
                if self._plain_shop():
                    break
            after = self._plain_balance()
            if balance - after != quantity * 50:
                raise TaskDeferred('扣除的蛇皮与确认金额不一致')
        except TaskDeferred as exc:
            raise RequestHumanTakeover(f'朴素御魂兑换已提交，但结果未确认：{exc}；请检查游戏') from exc
        logger.info(f'朴素御魂礼盒兑换完成：{quantity} 个，紫色蛇皮 {balance} → {after}')
        return quantity
