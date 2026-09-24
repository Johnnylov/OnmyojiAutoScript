"""独立限时活动：月华流光。"""
from datetime import time, timedelta

from pydantic import Field

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.SwitchSoul.switch_soul_config import SwitchSoulConfig
from tasks.Component.config_base import ConfigBase, Time
from tasks.Component.config_scheduler import Scheduler


class GeneralConfig(ConfigBase):
    challenge_limit: int = Field(default=1, ge=0, title='挑战次数',
                                 description='moonlight_challenge_limit_help')
    limit_time: Time = Field(default=Time(hour=1, minute=30), title='最长运行时间',
                            description='moonlight_limit_time_help')
    random_sleep: bool = Field(default=False, title='行动前随机休息',
                               description='moonlight_random_sleep_help')
    active_souls_clean: bool = Field(default=False, title='结束后整理御魂',
                                     description='moonlight_active_souls_clean_help')

    @property
    def limit_time_v(self) -> timedelta:
        value = self.limit_time
        if isinstance(value, time):
            return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
        return value

class MoonlightSwitchSoulConfig(SwitchSoulConfig):
    enable: bool = Field(default=False, title='按编号切换御魂',
                         description='moonlight_switch_soul_enable_help')


class Moonlight(ConfigBase):
    scheduler: Scheduler = Field(default_factory=Scheduler, title='任务调度')
    general_config: GeneralConfig = Field(default_factory=GeneralConfig, title='通用设置')
    moonlight_battle_conf: GeneralBattleConfig = Field(default_factory=GeneralBattleConfig,
                                                      title='战斗设置')
    switch_soul: MoonlightSwitchSoulConfig = Field(default_factory=MoonlightSwitchSoulConfig,
                                                  title='执行任务前切换御魂')
