from module.logger import logger
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.ActivityShikigami.base_act import BaseAct
import tasks.ActivityShikigami.page as pages
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralBattle.general_battle import ExitMatcher
from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.exception import BattleTransitionTimeout
from tasks.ActivityShikigami.dispatch import DailyDispatcher, DispatchError
from tasks.ActivityShikigami.dispatch_view import DispatchView
from tasks.ActivityShikigami.coloring import DailyColorer, ColoringError
from tasks.ActivityShikigami.coloring_view import ColoringView
from tasks.ActivityShikigami.soul_selection import (
    DailySoulSelector, SoulSelectionError, choose_souls, recommendations,
    recorded_today, server_date,
)
from tasks.ActivityShikigami.soul_selection_view import SoulSelectionView


class NormalClimbAct(BaseAct):
    """普通爬塔活动"""

    def _exit_matcher(self) -> ExitMatcher | None:
        return pages.any_of(self.I_ACT_FIRE, self.I_AS_BOSS_FIRE)

    def before_run(self):
        super().before_run()
        page_act = self.navigator.resolve_page(pages.page_act)
        page_act_pass = self.navigator.resolve_page(pages.page_act_pass)
        page_act_ap = self.navigator.resolve_page(pages.page_act_ap)
        page_act_map = self.navigator.resolve_page(pages.page_act_map)
        flag = pages.special_act_Flag

        if flag:
            # 体力爬塔和中转界面关联
            page_act_map.connect(
                page_act_ap,
                ActivityShikigamiAssets.I_MAP_GOTO_BATTLE,
                key="page_act_map->page_act_ap",
            )
        else:
            # 体力爬塔和主界面关联
            page_act.connect(
                page_act_ap,
                ActivityShikigamiAssets.I_TO_BATTLE_MAIN,
                key="page_act->page_act_ap",
            )
        # 体力爬塔进入是门票则切换
        page_act_ap.add_enter_failure_hooks(
            pages.conditional_action(
                condition=ActivityShikigamiAssets.I_CLIMB_MODE_PASS,
                action=ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH,
            )
        )
        if flag:
            # 门票爬塔和中转界面关联
            page_act_map.connect(
                page_act_pass,
                ActivityShikigamiAssets.I_MAP_GOTO_BATTLE,
                key="page_act_map->page_act_pass",
            )
        else:
            # 门票爬塔和主界面关联
            page_act.connect(
                page_act_pass,
                ActivityShikigamiAssets.I_TO_BATTLE_MAIN,
                key="page_act->page_act_pass",
            )
        # 门票爬塔进入是体力则切换
        page_act_pass.add_enter_failure_hooks(
            pages.conditional_action(
                condition=ActivityShikigamiAssets.I_CLIMB_MODE_AP,
                action=ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH,
            )
        )
        # 门票和体力互相切换
        page_act_pass.connect(
            page_act_ap,
            ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH,
            key="page_act_pass->page_act_ap",
        )
        page_act_ap.connect(
            page_act_pass,
            ActivityShikigamiAssets.I_CLIMB_MODE_SWITCH,
            key="page_act_ap->page_act_pass",
        )

        # A previous interrupted run may have left the selection popup open;
        # handle it before page navigation attempts to leave an unknown page.
        if self.conf.general_climb.auto_select_souls and recommendations(server_date()):
            if self._soul_selection_view.find_panel(self.screenshot()) is not None:
                self._select_daily_souls(force=True)
        self._restore_daily_activity_map()
        self._dispatch_once_today()

    @property
    def _dispatch_view(self):
        if not hasattr(self, '_daily_dispatch_view'):
            self._daily_dispatch_view = DispatchView(read_text=self._activity_read_text)
        return self._daily_dispatch_view

    @property
    def _coloring_view(self):
        if not hasattr(self, '_daily_coloring_view'):
            self._daily_coloring_view = ColoringView()
        return self._daily_coloring_view

    def _activity_read_text(self, image, roi, name='activity_text'):
        # Keep raw strings: a blank OCR result must never become zero/100%.
        rule = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                       keyword='', name=name)
        return rule.ocr(image)

    def _activity_click_roi(self, roi, name):
        self.click(RuleClick(roi_front=roi, roi_back=roi, name=name))
        # BaseTask.click returns False for a delivered click without interval;
        # our UI workers use False to mean no action, so normalize that API.
        return True

    def _activity_owner(self):
        return (self.config.config_name + '\n' +
                self.config.model.restart.login_character_config.character)

    def _colorer(self):
        return DailyColorer(self.screenshot, self._activity_click_roi,
                            self._activity_read_text, view=self._coloring_view)

    def _restore_daily_activity_map(self):
        """Close known interrupted panels before generic page navigation."""
        if not pages.special_act_Flag:
            return
        image = self.screenshot()
        try:
            # This recovery does not spend pigment, even with coloring disabled.
            if self._coloring_view.find_page(image) is not None:
                if not self._colorer().leave():
                    raise ColoringError('未确认从百鬼夜行图返回地图')
                image = self.screenshot()
            observation = self._dispatch_view.observe(image)
            if (observation.kind in ('portraits', 'setup')
                    or getattr(observation, 'close_roi', None) is not None):
                dispatcher = DailyDispatcher(self.screenshot, self._activity_click_roi,
                                             view=self._dispatch_view)
                if not dispatcher.restore_map():
                    raise DispatchError('上阵面板未确认收起')
        except (DispatchError, ColoringError) as exc:
            raise BattleTransitionTimeout(f'活动面板恢复失败：{exc}') from exc

    def _dispatch_once_today(self):
        """Check this account's unlocked dispatch slots once per server day."""
        if not pages.special_act_Flag:
            return
        current = self.config.model.activity_shikigami
        if not current.general_climb.run_sequence_v:
            return
        day, owner = server_date(), self._activity_owner()
        record = current.daily_dispatch_record
        if record.date == day.isoformat() and record.owner == owner:
            return
        self.goto_page(pages.page_act_map)
        if self._dispatch_view.observe(self.screenshot()).kind != 'map':
            logger.info('当前活动没有可确认的上阵地图，继续爬塔')
            return
        dispatcher = DailyDispatcher(self.screenshot, self._activity_click_roi,
                                     view=self._dispatch_view)
        try:
            result = dispatcher.run()
        except DispatchError as exc:
            raise BattleTransitionTimeout(f'每日上阵未完成：{exc}') from exc
        if not result.completed:
            logger.warning(f'每日上阵未确认完成：{result.reason}')
            return
        if server_date() != day or self._activity_owner() != owner:
            raise BattleTransitionTimeout('上阵期间日期或角色发生变化，下次重新检查')
        # Screenshot callbacks may reload the model; save into the current one.
        record = self.config.model.activity_shikigami.daily_dispatch_record
        record.date, record.owner = day.isoformat(), owner
        self.config.save()
        logger.info(f'每日上阵已检查：新上阵 {result.dispatched} 个格子；{result.reason}')

    def after_run(self):
        climb = self.config.model.activity_shikigami.general_climb
        if not climb.auto_color_hyakki or not climb.run_sequence_v:
            return
        if not pages.special_act_Flag:
            return
        self.goto_page(pages.page_act_map)
        colorer = self._colorer()
        try:
            result = colorer.run()
            if not colorer.leave():
                raise ColoringError('上色结束后未确认返回地图')
        except ColoringError as exc:
            raise BattleTransitionTimeout(f'百鬼夜行图上色未完成：{exc}') from exc
        logger.info(f'百鬼夜行图：{result.status}，提交 {result.submissions} 次，'
                    f'全服进度 {result.global_progress}')

    @property
    def _soul_selection_view(self):
        if not hasattr(self, '_daily_soul_view'):
            self._daily_soul_view = SoulSelectionView()
        return self._daily_soul_view

    def _select_daily_souls(self, force=False):
        current_conf = self.config.model.activity_shikigami
        if not current_conf.general_climb.auto_select_souls:
            return
        day = server_date()
        if not recommendations(day):
            return
        record = current_conf.soul_selection_record
        owner = (self.config.config_name + '\n' +
                 self.config.model.restart.login_character_config.character)
        already_done = recorded_today(record, day, owner)
        if already_done and not force:
            return
        target = frozenset(record.slots) if already_done else choose_souls(day)
        logger.info(f'按日期自选御魂 {day}: 推荐格 {recommendations(day)}，本次选择 {sorted(target)}')

        def click_roi(roi, name):
            self.click(RuleClick(roi_front=roi, roi_back=roi, name=name))

        selector = DailySoulSelector(self.screenshot, click_roi, self._soul_selection_view)
        try:
            selector.select(day, target)
        except SoulSelectionError as exc:
            # Use the existing bounded-transition recovery: skip this task and
            # retry later instead of spending tickets with unverified choices.
            raise BattleTransitionTimeout(f'当期爬塔自选御魂失败：{exc}') from exc
        # Screenshot handlers may accept a bounty and reload config.model;
        # write to the current model, not the cached conf held before the UI.
        record = self.config.model.activity_shikigami.soul_selection_record
        record.date = day.isoformat()
        record.slots = sorted(target)
        record.owner = owner
        self.config.save()
        logger.info(f'御魂自选已提交并回读确认：{day}，格子 {record.slots}')

    def _run_common(self):
        if self.climb_type in ('pass', 'ap'):
            # This check runs between battles too, so midnight triggers a new
            # selection even within a single long-running climb task.
            self._select_daily_souls()
        return super()._run_common()

    def _before_challenge(self):
        # Recheck after loadout changes, random rests and entry retries, right
        # before a challenge click, so a midnight refresh cannot be missed.
        if self.climb_type in ('pass', 'ap'):
            self._select_daily_souls()

    def lock_team(self, battle_conf: GeneralBattleConfig):
        enable = battle_conf.lock_team_enable
        match self.climb_type:
            case "boss":
                lock_rule = self.I_LOCK
                unlock_rule = self.I_UNLOCK
            case _:
                lock_rule = self.I_AP_LOCK
                unlock_rule = self.I_AP_UNLOCK
        if enable:
            logger.info(f"Lock {self.climb_type} team")
            self.ui_click(unlock_rule, stop=lock_rule, interval=1.5)
            return
        logger.info(f"Unlock {self.climb_type} team")
        self.ui_click(lock_rule, stop=unlock_rule, interval=1.5)
