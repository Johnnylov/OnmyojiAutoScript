"""月华流光独立任务，沿用本地导航和通用战斗。"""
from datetime import datetime

from cached_property import cached_property

from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_shikigami_records
from tasks.Moonlight.activity import MoonlightAct
from tasks.Moonlight.assets import MoonlightAssets


class ScriptTask(MoonlightAct, GeneralBattle, GameUi, SwitchSoul, MoonlightAssets):
    @cached_property
    def conf(self):
        return self.config.model.moonlight

    def _switch_moonlight_soul(self):
        switch = self.conf.switch_soul
        if not (switch.enable or switch.enable_switch_by_name):
            return
        if (self.conf.general_config.challenge_limit == 0
                or self._moon_time_limit_reached()):
            return

        logger.info('月华流光：从庭院进入式神录，切换御魂')
        self._goto_moonlight(page_main)
        self._goto_moonlight(page_shikigami_records)
        if switch.enable:
            self.run_switch_soul(switch.switch_group_team)
        if switch.enable_switch_by_name:
            self.run_switch_soul_by_name(switch.group_name, switch.team_name)
        self._goto_moonlight(page_main)

    def run(self):
        self.start_time = datetime.now()
        self.current_count = 0
        self._switch_moonlight_soul()
        self.run_moonlight()
        self._goto_moonlight(page_main)
        if self.conf.general_config.active_souls_clean:
            self.set_next_run(task='SoulsTidy', success=False, finish=False,
                              target=datetime.now())
        self.set_next_run(task='Moonlight', success=True)
        raise TaskEnd
