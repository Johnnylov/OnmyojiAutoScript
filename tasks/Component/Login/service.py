# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import os
import re
import time

from module.base.timer import Timer
from module.base.utils import save_image
from module.exception import RequestHumanTakeover, GameTooManyClickError, GameStuckError
from module.handler.sensitive_info import handle_sensitive_image
from module.logger import logger
from tasks.GameUi.assets import GameUiAssets
from tasks.Restart.assets import RestartAssets
from tasks.base_task import BaseTask


class LoginService(BaseTask, RestartAssets, GameUiAssets):
    character: str
    LOGIN_RETRY_COUNT = 3

    def __init__(self, *wargs, **kwargs):
        super().__init__(*wargs, **kwargs)
        self.character = self.config.restart.login_character_config.character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = self.character

    def _login_screenshot(self) -> bool:
        """仅在当前帧为游戏横屏时处理邀请和登录识别。"""
        # 保留 Device.screenshot 的卡死检查，竖屏持续不恢复时仍交给登录重试。
        self.device.screenshot()
        if getattr(self.device.image, 'shape', None) != (720, 1280, 3):
            return False
        self._burst()
        # 邀请处理可能再次截图，调用方必须使用处理后的最新有效帧。
        return getattr(self.device.image, 'shape', None) == (720, 1280, 3)

    def _app_handle_login(self) -> bool:
        """
        最终是在庭院界面
        :return:
        """
        logger.hr('App login')
        self.device.stuck_record_add('LOGIN_CHECK')

        confirm_timer = Timer(1.5, count=2).start()
        orientation_timer = Timer(10)
        skip_login_animation = True
        skip_click_mx_cnt = 5
        login_success = False

        while 1:
            if not login_success and orientation_timer.reached():
                self.device.get_orientation()
                orientation_timer.reset()

            if not self._login_screenshot():
                confirm_timer.reset()
                continue
            if self.appear_then_click(self.I_CANCEL_BATTLE, interval=0.8):
                logger.info('Cancel continue battle')
                continue
            if self.appear(self.I_CHECK_MAIN, interval=0.2) and not self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS):
                logger.info('The main had already appeared, but shikigami records had not yet appeared')
                skip_login_animation = False
                if self.click(self.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
                    continue
            if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, interval=0.2):
                if confirm_timer.reached():
                    logger.info('Login to main confirm (shikigami records button appears)')
                    break
            else:
                confirm_timer.reset()
            if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS, interval=0.5):
                logger.info('Login success: shikigami records button appears')
                login_success = True
                skip_login_animation = False
            if self.appear(self.I_HARVEST_ZIDU, interval=1):
                self.I_HARVEST_ZIDU.roi_front[0] -= 200
                self.I_HARVEST_ZIDU.roi_front[1] -= 200
                if self.click(self.I_HARVEST_ZIDU, interval=2):
                    logger.info('Close zidu')
                continue
            if self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=2.5):
                logger.info('Soul overflow confirm')
                continue
            if self.appear_then_click(self.I_LOGIN_LOAD_DOWN, interval=1):
                logger.info('Download inbetweening')
                continue
            if self.appear_then_click(self.I_WATCH_VIDEO_CANCEL, interval=0.6):
                logger.info('Close video')
                continue
            if self.appear_then_click(self.I_LOGIN_RED_CLOSE, interval=0.6):
                logger.info('Close red close')
                continue
            if self.appear_then_click(self.I_LOGIN_YELLOW_CLOSE, interval=0.6):
                logger.info('Close yellow close')
                continue
            if self.appear_then_click(self.I_LOGIN_LOGIN_GOTO_BIND_PHONE):
                while 1:
                    if not self._login_screenshot():
                        continue
                    if self.appear_then_click(self.I_LOGIN_LOGIN_CANCEL_BIND_PHONE):
                        logger.info("Close bind phone")
                        break
                continue
            from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
            if self.appear_then_click(gia.I_I_REJECT, interval=0.8):
                logger.info("reject invites")
                continue
            if self.appear_then_click(self.I_LOGIN_LOGIN_ONMYOJI_GENIE):
                logger.info("click onmyoji genie")
                continue
            if self.appear(self.I_LOGIN_SPECIFIC_SERVE, interval=0.6) \
                    and self.ocr_appear_click(self.O_LOGIN_SPECIFIC_SERVE, interval=0.6):
                while True:
                    if not self._login_screenshot():
                        continue
                    if self.appear(self.I_LOGIN_SPECIFIC_SERVE):
                        self.click(self.C_LOGIN_ENSURE_LOGIN_CHARACTER_IN_SAME_SVR, interval=2)
                        continue
                    break
                logger.info('login specific user')
                continue

            if self.appear(self.I_CREATE_ACCOUNT):
                logger.warning('Appear create account')
                raise GameStuckError('Appear create account')
            if self.appear(self.I_CHARACTARS, interval=1):
                logger.info('误入区服设置')
                self.device.click(x=106, y=535)
                continue
            if self.appear(self.I_EARLY_SERVER) and self.appear_then_click(self.I_EARLY_SERVER_CANCEL):
                logger.info('Cancel switch from early server to normal server')
                continue

            # 进入登录页面后或点击超过一定次数不再处理登录动画逻辑
            if self.appear(self.I_LOGIN_8, interval=0.6) or skip_click_mx_cnt <= 0:
                skip_login_animation = False
            if skip_login_animation:
                if self.ocr_appear_click(self.O_LOGIN_ANIMATION_SKIP, interval=2.5):  # 点击跳过登录动画
                    continue
                if self.click(self.C_LOGIN_ANIMATION_CENTER, interval=5):  # 点击屏幕中央触发跳过显示
                    skip_click_mx_cnt -= 1

            if self.ocr_appear_click(self.O_LOGIN_ENTER_GAME_ORIGIN, interval=3) or self.ocr_appear_click(self.O_LOGIN_ENTER_GAME, interval=3):
                skip_login_animation = False  # 进入登录页面后不再处理登录动画逻辑
                wait_timer = Timer(5).start()
                while not wait_timer.reached():
                    if not self._login_screenshot():
                        continue
                    if self.appear(self.I_LOGIN_SPECIFIC_SERVE):
                        break
                else:
                    logger.warning(f"Wait until appear {self.I_LOGIN_SPECIFIC_SERVE.name} timeout")
                continue

        return login_success

    def app_handle_login(self) -> bool:
        for attempt in range(self.LOGIN_RETRY_COUNT + 1):
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            try:
                self._app_handle_login()
                return True
            except (GameTooManyClickError, GameStuckError) as e:
                logger.warning(e)
                self._save_login_error_screenshot(attempt + 1)
                if attempt >= self.LOGIN_RETRY_COUNT:
                    break
                logger.warning(
                    f'Login failed, restart game and retry current task '
                    f'({attempt + 1}/{self.LOGIN_RETRY_COUNT})'
                )
                self.device.app_stop()
                self.device.app_start()

        logger.critical('Login failed')
        logger.critical('Onmyoji server may be under maintenance, or you may lost network connection')
        raise RequestHumanTakeover

    def _save_login_error_screenshot(self, attempt: int) -> None:
        """保存登录失败时的当前画面，截图失败不影响后续重试。"""
        try:
            os.makedirs('./log/error', exist_ok=True)
            config_name = re.sub(r'[^0-9A-Za-z._-]+', '_', self.config.config_name).strip('._') or 'oas'
            timestamp_ms = int(time.time() * 1000)
            file = f'./log/error/{config_name}_login_{attempt}_{timestamp_ms}.png'
            image = handle_sensitive_image(self.device.image)
            save_image(image, file)
            logger.warning(f'Saving login error screenshot: {file}')
        except Exception as e:
            logger.warning(f'Failed to save login error screenshot: {e}')

    def set_specific_usr(self, character: str):
        self.character = character
        self.O_LOGIN_SPECIFIC_SERVE.keyword = character
