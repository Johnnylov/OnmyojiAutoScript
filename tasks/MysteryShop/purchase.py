"""神秘商店新旧购买弹窗兼容；不改变其他商店的购买规则。"""

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.GameUi.page import random_click


CONFIRM_QUANTITY = RuleClick(roi_front=(569, 546, 140, 40),
                            roi_back=(569, 546, 140, 40), name='ms_buy_quantity_one')
QUANTITY = RuleOcr(roi=(573, 445, 75, 48), area=(573, 445, 75, 48),
                   mode='Digit', method='Default', keyword='', name='ms_buy_quantity')


def buy_shop_one(task, start_click, check_image):
    # 只扩展弹窗内商品区域，不能匹配后方货架，也不修改共享资产对象。
    check = RuleImage(roi_front=tuple(check_image.roi_front), roi_back=(455, 175, 280, 210),
                      threshold=check_image.threshold, method=check_image.method, file=check_image.file)
    opening = Timer(20).start()
    while not opening.reached():
        task.screenshot()
        if task.appear(check):
            break
        # 其他商品弹窗已经打开时，不能继续点后方货架。
        if task.appear(task.I_BUY_PLUS) and task.appear(task.I_BUY_SUB):
            raise GameStuckError('MysteryShop purchase item could not be verified')
        task.appear_then_click(start_click, interval=1)
    else:
        raise GameStuckError('MysteryShop purchase dialog did not appear')

    confirming = Timer(30).start()
    reward_seen = False
    quantity_layout = False
    while not confirming.reached():
        task.screenshot()
        if task.appear(task.I_BUY_RMB):
            logger.warning('MysteryShop refuses purchases using soul jade/RMB')
            task.click(task.C_BUY_CANCEL, interval=1)
            return False
        if task.appear(task.I_BUY_SUCCESS):
            reward_seen = True
            task.click(random_click(), interval=0.8)
            continue
        if task.ui_reward_appear_click():
            reward_seen = True
            continue
        if reward_seen:
            if not task.appear(task.I_UI_REWARD, threshold=0.6):
                return True
            continue
        # 弹窗消失但奖励尚未确认时只等待，绝不点背后的商品或确认位置。
        if not task.appear(check):
            continue
        plus, sub = task.appear(task.I_BUY_PLUS), task.appear(task.I_BUY_SUB)
        quantity_layout = quantity_layout or plus or sub
        if quantity_layout:
            if not (plus and sub):
                continue
            count = QUANTITY.ocr(task.device.image)
            if type(count) is not int or count < 1:
                continue  # OCR 不确定时绝不提交购买。
            if count > 1:
                task.appear_then_click(task.I_BUY_SUB, interval=0.4)
                continue
            task.click(CONFIRM_QUANTITY, interval=2.8)
        else:
            task.click(task.C_BUY_ONE, interval=2.8)
    raise GameStuckError('MysteryShop purchase result/quantity could not be confirmed')
