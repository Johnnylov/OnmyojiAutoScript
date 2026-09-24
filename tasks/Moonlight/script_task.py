"""月华流光独立任务，沿用本地导航和通用战斗。"""
from datetime import datetime

from cached_property import cached_property

from module.exception import TaskEnd
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.Moonlight.activity import MoonlightAct
from tasks.Moonlight.assets import MoonlightAssets


class ScriptTask(MoonlightAct, GeneralBattle, GameUi, MoonlightAssets):
    @cached_property
    def conf(self):
        return self.config.model.moonlight

    def run(self):
        self.start_time = datetime.now()
        self.current_count = 0
        self.run_moonlight()
        self._goto_moonlight(page_main)
        if self.conf.general_config.active_souls_clean:
            self.set_next_run(task='SoulsTidy', success=False, finish=False,
                              target=datetime.now())
        self.set_next_run(task='Moonlight', success=True)
        raise TaskEnd
