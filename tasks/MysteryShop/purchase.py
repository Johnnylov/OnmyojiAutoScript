"""神秘商店购买：灰色商品不点击，每次提交必须等待明确结果。"""

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import GameStuckError
from module.logger import logger
from tasks.MysteryShop.purchase_view import (
    item_availability, bond_required, purchase_dialog_present, shelf_controls_enabled,
)


CONFIRM_QUANTITY = RuleClick(roi_front=(569, 546, 140, 40),
                            roi_back=(569, 546, 140, 40), name='ms_buy_quantity_one')
QUANTITY = RuleOcr(roi=(573, 445, 75, 48), area=(573, 445, 75, 48),
                   mode='Digit', method='Default', keyword='', name='ms_buy_quantity')


def purchase_item_rule(check_image):
    # The current quantity dialog's icon is approximately 2.5% larger. Keep
    # the original confidence requirement and only search inside the dialog.
    check = RuleImage(roi_front=tuple(check_image.roi_front), roi_back=(455, 175, 280, 210),
                      threshold=check_image.threshold, method=RuleImage.METHOD_MULTI_SCALE_TEMPLATE_MATCH,
                      file=check_image.file)
    check.scale_range, check.scale_step = (.9, 1.1), .025
    return check


def _shelf_visible(task):
    return (not purchase_dialog_present(task.device.image)
            and not task.appear(task.I_BUY_PLUS) and not task.appear(task.I_BUY_SUB)
            and shelf_controls_enabled(task.device.image) and task._shop_page_visible())


def _cancel_purchase(task, check):
    task.click(task.C_BUY_CANCEL, interval=1)
    closing = Timer(5).start()
    while not closing.reached():
        task.screenshot()
        if (not task.appear(check) and not task.appear(task.I_BUY_PLUS)
                and not task.appear(task.I_BUY_SUB) and _shelf_visible(task)):
            return False
    raise GameStuckError('MysteryShop paid purchase dialog did not close')


def buy_shop_one(task, start_click, check_image):
    check = purchase_item_rule(check_image)
    opening = Timer(8).start()
    opened = False
    panel_seen = False
    while not opening.reached():
        task.screenshot()
        dialog = purchase_dialog_present(task.device.image)
        if dialog and task.appear(check):
            break
        # A partially animated or different item dialog must never receive a
        # shelf click. Give the requested icon time to finish appearing.
        if dialog or task.appear(task.I_BUY_PLUS) or task.appear(task.I_BUY_SUB):
            panel_seen = True
            continue
        if bond_required(task.device.image):
            logger.info('MysteryShop item requires a higher friendship level; skip')
            return False
        availability = item_availability(task.device.image, start_click)
        if not opened:
            if availability != 'available':
                logger.info(f'MysteryShop item is {availability}; skip without clicking')
                return False
            opened = bool(task.appear_then_click(start_click, interval=1))
        # After a delivered click, only observe. A locked/unresponsive item is
        # not reopened every second until the device's repeated-click guard.
    else:
        if not panel_seen and _shelf_visible(task):
            logger.warning('MysteryShop item did not open after one click; skip')
            return False
        raise GameStuckError('MysteryShop purchase item could not be verified')

    confirming = Timer(30).start()
    reward_seen = False
    quantity_layout = False
    submitted = False
    previous_count = None
    reducing_from = None
    while not confirming.reached():
        task.screenshot()
        if task.appear(task.I_BUY_SUCCESS):
            reward_seen = reward_seen or submitted
            continue  # The success toast expires; it does not need a blind click.
        if task.ui_reward_appear_click():
            reward_seen = reward_seen or submitted
            continue
        if reward_seen:
            if (not task.appear(task.I_UI_REWARD, threshold=0.6)
                    and not task.appear(check) and _shelf_visible(task)):
                return True
            continue
        # Never re-submit while a request is pending, even if its old dialog
        # remains visible. A missing acknowledgement is an error, not a retry.
        if submitted:
            continue
        if not purchase_dialog_present(task.device.image) or not task.appear(check):
            previous_count = None
            continue
        if task.appear(task.I_BUY_RMB):
            logger.warning('MysteryShop refuses purchases using soul jade/RMB')
            return _cancel_purchase(task, check)
        plus, sub = task.appear(task.I_BUY_PLUS), task.appear(task.I_BUY_SUB)
        quantity_layout = quantity_layout or plus or sub
        if quantity_layout:
            if not (plus and sub):
                previous_count = None
                continue
            count = QUANTITY.ocr(task.device.image)
            if type(count) is not int or not 1 <= count <= 99:
                previous_count = None
                continue
            if reducing_from is not None:
                if count == reducing_from:
                    continue
                if count != reducing_from - 1:
                    raise GameStuckError('MysteryShop quantity changed unexpectedly')
                reducing_from = None
            if count > 1:
                if task.appear_then_click(task.I_BUY_SUB, interval=.4):
                    reducing_from = count
                previous_count = None
                continue
            if previous_count != count:
                previous_count = count
                continue
            # BaseTask.click without an interval returns False even when sent;
            # this interval-based call returns True only for a delivered click.
            submitted = bool(task.click(CONFIRM_QUANTITY, interval=2.8))
        else:
            submitted = bool(task.click(task.C_BUY_ONE, interval=2.8))
    raise GameStuckError('MysteryShop purchase result/quantity could not be confirmed')
