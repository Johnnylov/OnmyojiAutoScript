"""Recover observed login download/FAQ screens without blind screen clicks."""

import re
import math

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.logger import logger


def _marker(name, roi):
    return RuleImage(roi_front=roi, roi_back=roi, threshold=0.9,
                     method='Template matching',
                     file=f'./tasks/Component/Login/recovery_assets/{name}.png')


DOWNLOAD = _marker('download', (497, 648, 102, 24))
FAQ_TITLE = _marker('faq_title', (31, 17, 100, 27))
WEBVIEW_REFRESH = _marker('webview_refresh', (1224, 232, 42, 38))
WEBVIEW_EXIT = _marker('webview_exit', (1226, 650, 41, 38))
EXIT_FAQ = RuleClick(roi_front=(1233, 654, 25, 28), roi_back=(1233, 654, 25, 28),
                     name='login_exit_faq')
DOWNLOAD_PROGRESS = RuleOcr(roi=(490, 641, 330, 37), area=(490, 641, 330, 37),
                            mode='Single', method='Default', keyword='',
                            name='login_download_progress')


def parse_download_progress(text):
    """Accept only the download label followed by a sane byte counter pair."""
    if not isinstance(text, str):
        return None
    text = re.sub(r'\s+', '', text).upper()
    match = re.fullmatch(
        r'正在下载[:：]?(\d+(?:\.\d+)?)([KMG])B?/(\d+(?:\.\d+)?)([KMG])B?', text)
    if match is None:
        return None
    factors = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}
    done = float(match[1]) * factors[match[2]]
    total = float(match[3]) * factors[match[4]]
    return (done, total) if math.isfinite(total) and 0 <= done <= total and total > 0 else None


class LoginRecovery:
    """One login attempt: downloads get more time only while bytes advance."""

    def __init__(self):
        self.progress_timer = Timer(2)
        self.progress = {}

    def handle(self, task):
        if task.appear(DOWNLOAD):
            if self.progress_timer.reached():
                self.progress_timer.reset()
                current = parse_download_progress(DOWNLOAD_PROGRESS.ocr(task.device.image))
                if current is not None:
                    done, total = current
                    previous = self.progress.get(total)
                    # A changed total starts another baseline; merely oscillating
                    # between OCR readings must not renew the timeout forever.
                    if not self.progress or (previous is not None and done > previous):
                        task.device.stuck_record_clear()
                        task.device.stuck_record_add('LOGIN_CHECK')
                        logger.info('Login resource download is progressing; keep waiting')
                    self.progress[total] = max(done, previous or 0)
            # Even an unreadable/stalled counter must not trigger animation or
            # login clicks. Device stuck detection still bounds this wait.
            return True
        if (task.appear(FAQ_TITLE) and task.appear(WEBVIEW_REFRESH)
                and task.appear(WEBVIEW_EXIT)):
            task.click(EXIT_FAQ, interval=2)
            logger.info('Close login FAQ webview')
            return True
        return False
