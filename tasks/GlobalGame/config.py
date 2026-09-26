# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from enum import Enum

from pydantic import BaseModel, Field

from tasks.GlobalGame.config_emergency import Emergency
from tasks.Component.Costume.config import CostumeConfig


class Transport(str, Enum):
    TCP = 'TCP'
    SSL_TLS = 'SSL/TLS'


class TeamFlow(BaseModel):
    enable: bool = Field(default=False, description='enable_help')
    broker: str = Field(default='', description='broker_help')
    port: int = Field(default=8883, description='port_help')
    transport: Transport = Field(default=Transport.TCP, description='transport_help')
    ca: str = Field(default='', description='ca_help')
    username: str = Field(default='', description='username_help')
    password: str = Field(default='', description='password_help')


class BattleTaskOverEnum(str, Enum):
    FINISH = 'finish'
    EXIT = 'exit'


class BattleTakeover(BaseModel):
    battle_timeout: int = Field(default=420, description='battle_timeout_global_help', ge=1)
    on_takeover: BattleTaskOverEnum = Field(default=BattleTaskOverEnum.FINISH, description='on_takeover_help')


class LocalTeam(BaseModel):
    enable: bool = Field(default=False, title='启用本机组队联动', description='启用后，两份运行中的配置会相互调动组队任务；不会自动启动已停止的配置')
    partner_config: str = Field(default='', title='队友配置名', description='填写同一本机后端中的配置名，例如 oas2；两边需要相互绑定')
    sync_orochi: bool = Field(default=True, title='八岐大蛇联动', description='需配成队长与队员，并启用双方任务')
    sync_bondling: bool = Field(default=True, title='契灵联动', description='支持队长/队员以及 handoff1/handoff2')
    sync_true_orochi: bool = Field(default=False, title='真八岐大蛇联动',
                                  description='双方启用真蛇任务和真蛇双开组队，真蛇队友须与上方配置名一致，并配成初始队长和队员；沿用真蛇开车模式和好友邀请名单')
    ready_timeout: int = Field(default=600, ge=30, le=1800, title='就绪等待上限（秒）',
                              description='队友结束当前战斗并完成准备的最长等待时间；超时后两分钟重试')


class GlobalGame(BaseModel):
    emergency: Emergency = Field(default_factory=Emergency)
    costume_config: CostumeConfig = Field(default_factory=CostumeConfig)
    battle: BattleTakeover = Field(default_factory=BattleTakeover)
    team_flow: TeamFlow = Field(default_factory=TeamFlow)
    local_team: LocalTeam = Field(default_factory=LocalTeam)
