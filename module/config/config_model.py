# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from tasks.GuguArtStudio.config import GuguArtStudio
from tasks.GuildActivityMonitor.config import GuildActivityMonitor
from tasks.OtherWorldTwilight.config import OtherWorldTwilight
from typing import Dict, Any

import re
import inflection
import copy

from pathlib import Path
from pydantic import BaseModel, ValidationError, Field, field_validator

from module.config.utils import *
from module.logger import logger

# 导入配置的Python文件
from tasks.Component.config_base import ConfigBase, TimeDelta
from tasks.Exploration.config import Exploration
from tasks.RyouToppa.config import RyouToppa
from tasks.Dokan.config import Dokan
from tasks.Script.config import Script
from tasks.Restart.config import Restart
from tasks.GlobalGame.config import GlobalGame
# 每日任务-----------------------------------------------------------------------------------------------------
from tasks.AreaBoss.config import AreaBoss
from tasks.ExperienceYoukai.config import ExperienceYoukai
from tasks.GoldYoukai.config import GoldYoukai
from tasks.Nian.config import Nian
from tasks.KekkaiUtilize.config import KekkaiUtilize
from tasks.KekkaiActivation.config import KekkaiActivation
from tasks.DemonEncounter.config import DemonEncounter
from tasks.DailyTrifles.config import DailyTrifles
from tasks.TalismanPass.config import TalismanPass
from tasks.Pets.config import Pets
from tasks.SoulsTidy.config import SoulsTidy
from tasks.Delegation.config import Delegation
from tasks.WantedQuests.config import WantedQuests
from tasks.Tako.config import Tako
from tasks.AutoCheckinBigGod.config import AutoCheckinBigGod
# ----------------------------------------------------------------------------------------------------------------------
from tasks.Orochi.config import Orochi
from tasks.OrochiMoans.config import OrochiMoans
from tasks.Sougenbi.config import Sougenbi
from tasks.FallenSun.config import FallenSun
from tasks.EternitySea.config import EternitySea
from tasks.SixRealms.config import SixRealms
from tasks.RealmRaid.config import RealmRaid
from tasks.CollectiveMissions.config import CollectiveMissions
from tasks.Hunt.config import Hunt
from tasks.AbyssShadows.config import AbyssShadows
from tasks.GuildBanquet.config import GuildBanquet
from tasks.DemonRetreat.config import DemonRetreat
from tasks.GuildActivityMonitor.config import GuildActivityMonitor

# 这一部分是活动的配置-----------------------------------------------------------------------------------------------------
from tasks.ActivityShikigami.config import ActivityShikigami
from tasks.Moonlight.config import Moonlight
from tasks.MartialTournament.config import MartialTournament
from tasks.MetaDemon.config import MetaDemon
from tasks.FrogBoss.config import FrogBoss
from tasks.FloatParade.config import FloatParade
from tasks.Quiz.config import Quiz
from tasks.KittyShop.config import KittyShop
from tasks.DyeTrials.config import DyeTrials
# ----------------------------------------------------------------------------------------------------------------------

# 肝帝专属---------------------------------------------------------------------------------------------------------------
from tasks.BondlingFairyland.config import BondlingFairyland
from tasks.EvoZone.config import EvoZone
from tasks.GoryouRealm.config import GoryouRealm
from tasks.HeroTest.config import HeroTest
from tasks.FindJade.config import FindJade
from tasks.MemoryScrolls.config import MemoryScrolls
# ----------------------------------------------------------------------------------------------------------------------

# 每周任务---------------------------------------------------------------------------------------------------------------
from tasks.TrueOrochi.config import TrueOrochi
from tasks.RichMan.config import RichMan
from tasks.Secret.config import Secret
from tasks.WeeklyTrifles.config import WeeklyTrifles
from tasks.MysteryShop.config import MysteryShop
from tasks.Duel.config import Duel
# ----------------------------------------------------------------------------------------------------------------------

class ConfigModel(ConfigBase):
    config_name: str = "oas"
    running_task: str = ''
    script: Script = Field(default_factory=Script)
    restart: Restart = Field(default_factory=Restart)
    global_game: GlobalGame = Field(default_factory=GlobalGame)

    # 这些是每日任务的
    area_boss: AreaBoss = Field(default_factory=AreaBoss)
    experience_youkai: ExperienceYoukai = Field(default_factory=ExperienceYoukai)
    gold_youkai: GoldYoukai = Field(default_factory=GoldYoukai)
    nian: Nian = Field(default_factory=Nian)
    realm_raid: RealmRaid = Field(default_factory=RealmRaid)
    ryou_toppa: RyouToppa = Field(default_factory=RyouToppa)
    kekkai_utilize: KekkaiUtilize = Field(default_factory=KekkaiUtilize)
    kekkai_activation: KekkaiActivation = Field(default_factory=KekkaiActivation)
    demon_encounter: DemonEncounter = Field(default_factory=DemonEncounter)
    daily_trifles: DailyTrifles = Field(default_factory=DailyTrifles)
    talisman_pass: TalismanPass = Field(default_factory=TalismanPass)
    pets: Pets = Field(default_factory=Pets)
    souls_tidy: SoulsTidy = Field(default_factory=SoulsTidy)
    delegation: Delegation = Field(default_factory=Delegation)
    exploration: Exploration = Field(default_factory=Exploration)
    wanted_quests: WantedQuests = Field(default_factory=WantedQuests)
    tako: Tako = Field(default_factory=Tako)
    auto_checkin_big_god: AutoCheckinBigGod = Field(default_factory=AutoCheckinBigGod)

    # 这些是刷御魂的
    orochi: Orochi = Field(default_factory=Orochi)
    orochi_moans: OrochiMoans = Field(default_factory=OrochiMoans)
    sougenbi: Sougenbi = Field(default_factory=Sougenbi)
    fallen_sun: FallenSun = Field(default_factory=FallenSun)
    eternity_sea: EternitySea = Field(default_factory=EternitySea)
    six_realms: SixRealms = Field(default_factory=SixRealms)
    other_world_twilight : OtherWorldTwilight = Field(default_factory=OtherWorldTwilight)

    # 这些是活动的
    activity_shikigami: ActivityShikigami = Field(default_factory=ActivityShikigami)
    moonlight: Moonlight = Field(default_factory=Moonlight)
    martial_tournament: MartialTournament = Field(default_factory=MartialTournament)
    meta_demon: MetaDemon = Field(default_factory=MetaDemon)
    frog_boss: FrogBoss = Field(default_factory=FrogBoss)
    float_parade: FloatParade = Field(default_factory=FloatParade)
    quiz: Quiz = Field(default_factory=Quiz)
    kitty_shop: KittyShop = Field(default_factory=KittyShop)
    dye_trials: DyeTrials = Field(default_factory=DyeTrials)
    gugu_art_studio: GuguArtStudio = Field(default_factory=GuguArtStudio)

    # 这些是肝帝专属
    bondling_fairyland: BondlingFairyland = Field(default_factory=BondlingFairyland)
    evo_zone: EvoZone = Field(default_factory=EvoZone)
    goryou_realm: GoryouRealm = Field(default_factory=GoryouRealm)
    hero_test: HeroTest = Field(default_factory=HeroTest)
    find_jade: FindJade = Field(default_factory=FindJade)
    memory_scrolls: MemoryScrolls = Field(default_factory=MemoryScrolls)

    # 这些是每周任务
    true_orochi: TrueOrochi = Field(default_factory=TrueOrochi)
    rich_man: RichMan = Field(default_factory=RichMan)
    secret: Secret = Field(default_factory=Secret)
    weekly_trifles: WeeklyTrifles = Field(default_factory=WeeklyTrifles)
    mystery_shop: MysteryShop = Field(default_factory=MysteryShop)
    duel: Duel = Field(default_factory=Duel)

    # 阴阳寮
    collective_missions: CollectiveMissions = Field(default_factory=CollectiveMissions)
    hunt: Hunt = Field(default_factory=Hunt)
    dokan: Dokan = Field(default_factory=Dokan)
    abyss_shadows: AbyssShadows = Field(default_factory=AbyssShadows)
    guild_banquet: GuildBanquet = Field(default_factory=GuildBanquet)
    demon_retreat: DemonRetreat = Field(default_factory=DemonRetreat)
    guild_activity_monitor: GuildActivityMonitor = Field(default_factory=GuildActivityMonitor)

    @field_validator('running_task', mode='before')
    @classmethod
    def clear_removed_task(cls, value):
        # Old user configs may still contain the removed task's resume marker.
        return '' if value in ('Chess', 'Hyakkiyakou', 'hyakkiyakou') else value

    def __init__(self, config_name: str=None, **data) -> None:
        """

        :param config_name:
        """
        if data:
            if config_name:
                data["config_name"] = config_name
            super().__init__(**data)
            self._initialize_merge_state(None, None)
            return
        if not config_name:
            super().__init__()
            self._initialize_merge_state(None, None)
            return
        from module.config.edit_lock import config_path, config_edit_lock
        path = config_path(config_name)
        with config_edit_lock(path):
            existed = path.exists()
            data = self.read_json(config_name)
            raw = copy.deepcopy(data)
            data["config_name"] = config_name
            super().__init__(**data)
            self._initialize_merge_state(raw, path, existed)

    @staticmethod
    def _json_snapshot(data):
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))

    def _initialize_merge_state(self, disk, path, existed=None):
        object.__setattr__(self, '_local_baseline', self._json_snapshot(self.model_dump()))
        object.__setattr__(self, '_disk_baseline', copy.deepcopy(disk or {}))
        object.__setattr__(self, '_baseline_path', str(path) if path else None)
        object.__setattr__(self, '_baseline_existed', bool(existed if existed is not None else path and Path(path).exists()))

    def __setattr__(self, key, value):
        """
        只要修改属性就会触发这个函数 自动保存
        :param key:
        :param value:
        :return:
        """
        if key.startswith('_'):
            super().__setattr__(key, value)
            return
        previous = getattr(self, key, None)
        super().__setattr__(key, value)
        logger.info("auto save config")
        try:
            self.save()
        except Exception:
            # Failed top-level assignment must not poison the local model.
            super().__setattr__(key, previous)
            raise

    @staticmethod
    def read_json(config_name: str) -> dict:
        """
        读文件 没有额外操作
        :param config_name:  不带后缀
        :return:
        """
        filepath = Path.cwd() / "config" / f"{config_name}.json"
        return read_file(filepath)

    def write_json(self, config_name: str, data) -> None:
        """

        :param config_name: 不带后缀
        :param data:  字典而不是字符串
        :return:
        """
        from module.config.edit_lock import config_edit_lock, config_path, merge_config_fields, ConfigConflict
        filepath = config_path(config_name)
        desired = self._json_snapshot(data)
        desired['config_name'] = config_name
        with config_edit_lock(filepath):
            current = read_file(filepath)
            if self._baseline_path != str(filepath):
                # Detached models may create a new profile. The development
                # template generator deliberately replaces the template only.
                if current and config_name != 'template':
                    raise ConfigConflict(['<unloaded configuration>'])
                merged, expected = desired, desired
            else:
                if not filepath.exists() and self._baseline_existed:
                    raise ConfigConflict(['<deleted configuration>'])
                merged, expected = merge_config_fields(self._local_baseline, self._disk_baseline, desired, current)
            merged['config_name'] = config_name
            expected['config_name'] = config_name
            if merged != current or not filepath.exists():
                write_file(filepath, merged)
            object.__setattr__(self, '_local_baseline', copy.deepcopy(desired))
            object.__setattr__(self, '_disk_baseline', copy.deepcopy(expected))
            object.__setattr__(self, '_baseline_path', str(filepath))
            object.__setattr__(self, '_baseline_existed', True)

    def gui_args(self, task: str) -> str:
        """
        返回提供给gui显示的参数
        :param task: 输入的是任务的名称英文 如'Script' 或者是'script'都是可以的
        :return: 返回的是pydantic给我们结构化的输出的信息, 如果不能获取就返回空的str
        """
        task = convert_to_underscore(task)
        task_gui = getattr(self, task, None)
        if task_gui is None:
            logger.warning(f'{task} is no inexistence')
            return ''

        schema2 = task_gui.schema()
        # https://github.com/pydantic/pydantic/discussions/5687
        if 'definitions' in schema2:
            if 'Scheduler' in schema2['definitions']:
                if 'properties' in schema2['definitions']['Scheduler']:
                    properties = schema2['definitions']['Scheduler']['properties']
                    if 'success_interval' in properties:
                        properties['success_interval']['type'] = 'string'
                    if 'failure_interval' in properties:
                        properties['failure_interval']['type'] = 'string'
        return json.dumps(schema2)

    def gui_task(self, task: str) -> str:
        """
        返回提供给gui显示的参数
        :param task:
        :return:
        """
        task_name = convert_to_underscore(task)
        task = getattr(self, task_name, None)
        if task is None:
            logger.warning(f'{task_name} is no inexistence')
            return ''
        return task.json()

    def save(self) -> None:
        """

        :return:
        """
        self.write_json(self.config_name, self.model_dump())

    @staticmethod
    def type(key: str) -> str:
        """
        输入模型的键值，获取这个字段对象的类型 比如输入是orochi输出是Orochi
        :param key:
        :return:
        """
        field_type: str = str(ConfigModel.__annotations__[key])
        # return field_type
        if '.' in field_type:
            classname = field_type.split('.')[-1][:-2]
            return classname
        else:
            classname = re.findall(r"'([^']*)'", field_type)[0]
            return classname

    @staticmethod
    def deep_get(obj, keys: str, default=None):
        """
        递归获取模型的值
        :param obj:
        :param keys:
        :param default:
        :return:
        """
        if not isinstance(keys, list):
            keys = keys.split('.')
        value = obj
        try:
            for key in keys:
                value = getattr(value, key)
        except AttributeError:
            return default
        return value

    @staticmethod
    def deep_set(obj, keys: str, value) -> bool:
        if not isinstance(keys, list):
            keys = keys.split('.')
        current_obj = obj
        try:
            for key in keys[:-1]:
                current_obj = getattr(current_obj, key)
            setattr(current_obj, keys[-1], value)
            return True
        except (AttributeError, KeyError):
            return False

    # ----------------------------------- fastapi -----------------------------------
    def script_task(self, task: str) -> dict:
        """

        :param task: 同gui_args函数
        :return:
        """
        from module.config.config_menu import ConfigMenu
        task_key = convert_to_underscore(task)
        is_limited_activity = task_key in {
            convert_to_underscore(name)
            for name in ConfigMenu().menu['Activity Task']
        }
        task = getattr(self, task_key, None)
        if task is None:
            logger.warning(f'{task} is no inexistence')
            return {}

        def extract_groups(sch):
            # 从schema 中提取未解析的group的数据
            # properties = properties_groups(sch)
            results = {}
            properties = {}
            for key, value in sch["properties"].items():
                if 'items' in value:
                    properties[key] = re.search(r"/([^/]+)$", value['items']['$ref']).group(1)
                else:
                    properties[key] = re.search(r"/([^/]+)$", value['$ref']).group(1)

            for key, value in properties.items():
                results[key] = sch["$defs"][value]
            return results

        def merge_value(groups, jsons, definitions) -> list[dict]:
            # 将 groups的参数，同导出的json一起合并, 用于前端显示
            result = []
            for key, value in groups["properties"].items():
                # deal with exclude 
                if key in jsons and jsons[key] == 0xABCDEF:
                    continue

                item = {}
                item["name"] = key
                item["title"] = value["title"] if "title" in value else inflection.underscore(key)
                if "description" in value:
                    item["description"] = value["description"]
                item["default"] = value["default"]
                item["value"] = jsons[key] if key in jsons else value["default"]
                item["type"] = value["type"] if "type" in value else "enum"
                if '$ref' in value:  # list
                    enum_key = re.search(r"/([^/]+)$", value['$ref']).group(1)
                    item["enumEnum"] = definitions[enum_key]["enum"]
                # if 'allOf' in value:
                #     enum_key = re.search(r"/([^/]+)$", value['allOf'][0]['$ref']).group(1)
                #     item["enumEnum"] = definitions[enum_key]["enum"]
                result.append(item)
            return result

        schema = task.model_json_schema()
        groups = extract_groups(schema)
        groups_value = groups.copy()

        result: dict[str, list] = {}
        for key, value in task.model_dump(context={'hide': True}).items():
            if value == 0xABCDEF:
                continue
            if key not in groups:
                for group_name in groups.keys():
                    if group_name in key:
                        groups_value[key] = groups[group_name]
            result[key] = merge_value(groups_value[key], value, schema["$defs"])

        # Scheduler remains backward-compatible on disk; expose the activity
        # cutoff only for tasks in the actual limited-activity menu category.
        if not is_limited_activity and 'scheduler' in result:
            result['scheduler'] = [item for item in result['scheduler']
                                   if item['name'] != 'real_deadline']

        return result

    def script_set_arg(self, task: str, group: str, argument: str, value, *, raise_conflicts=False) -> bool:
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)

        def find_group():
            task_object = getattr(self, task, None)
            if not isinstance(task_object, BaseModel):
                return None
            group_object = getattr(task_object, group, None)
            if group_object is not None:
                return group_object
            # OASX numbers repeated configuration groups from one.
            for name, items in dict(task_object).items():
                match = re.fullmatch(re.escape(name) + r'_?(\d+)', group)
                if match and isinstance(items, list):
                    index = int(match.group(1)) - 1
                    return items[index] if 0 <= index < len(items) else None
            return None

        group_object = find_group()
        if not isinstance(group_object, BaseModel) or argument not in type(group_object).model_fields:
            logger.error(f'Set arg {task}.{group}.{argument} failed')
            return False

        try:
            # The legacy TimeDelta validator substitutes one day for malformed
            # strings. Reject invalid UI edits instead of silently changing them.
            if type(group_object).model_fields[argument].annotation is timedelta and isinstance(value, str):
                match = re.fullmatch(r'(\d+)\s+(\d{1,2}):(\d{1,2}):(\d{1,2})', value)
                if not match:
                    raise ValueError('Invalid interval; expected days HH:MM:SS')
                days, hours, minutes, seconds = map(int, match.groups())
                if hours >= 24 or minutes >= 60 or seconds >= 60:
                    raise ValueError('Invalid interval clock value')
                value = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

            # Validate an isolated copy before touching live state or saving. Calling
            # model_validate would invoke ConfigBase.__init__, which replaces some
            # out-of-range values with defaults instead of rejecting the edit.
            candidate = group_object.model_copy(deep=True)
            type(candidate).__pydantic_validator__.validate_assignment(candidate, argument, value)
            value = getattr(candidate, argument)

            reset_datetime = None
            if (task == 'restart' and group in ('task_config', 'tasks_config_reset')
                    and argument == 'reset_task_datetime_enable' and value is True):
                reset_datetime = candidate.reset_task_datetime
                if isinstance(reset_datetime, str):
                    reset_datetime = datetime.fromisoformat(reset_datetime)
        except (ValidationError, ValueError, TypeError, OverflowError):
            logger.error(f'Invalid config field {task}.{group}.{argument}')
            return False

        from module.config.edit_lock import ConfigConflict
        previous = getattr(group_object, argument)
        try:
            setattr(group_object, argument, value)
            logger.info(f'Set arg {self.config_name}.{task}.{group}.{argument}')
            if reset_datetime is not None:
                # Apply the switch and its schedule side effect in ONE commit.
                self.reset_datetime_for_all_enabled_tasks(reset_datetime)
            else:
                self.save()
            return True
        except ConfigConflict:
            setattr(group_object, argument, previous)
            if raise_conflicts:
                raise
            logger.warning(f'Concurrent config change rejected: {task}.{group}.{argument}')
            return False
        except Exception:
            setattr(group_object, argument, previous)
            raise

    def copy_script_task(self, task_name: str, source_task: BaseModel) -> bool:
        model_task_name = convert_to_underscore(task_name)
        try:
            setattr(self, model_task_name, source_task)
            self.save()
            logger.info(f'Copy task {model_task_name} success')
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    def copy_task_group(self, task_name: str, group_name: str, source_task: BaseModel) -> bool:
        model_task_name = convert_to_underscore(task_name)
        model_group_name = convert_to_underscore(group_name)
        task_object = getattr(self, model_task_name, None)
        if not task_object:
            return False
        source_group_obj = getattr(source_task, model_group_name, None)
        if not source_group_obj:
            return False
        try:
            setattr(task_object, model_group_name, source_group_obj)
            self.save()
            logger.info(f'Copy task group {model_task_name}.{model_group_name} success')
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    def replace_next_run(self, d, dt: datetime):
        for k, v in d.items():
            if isinstance(v, dict):
                self.replace_next_run(v, dt=dt)
            elif k == "next_run":
                d[k] = dt
                # convert value to datetime if it's a str
                if isinstance(v, str):
                    current_time = datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
                    if current_time != dt:
                        d[k] = dt.strftime("%Y-%m-%d %H:%M:%S")
                # already a datetime value
                elif isinstance(v, datetime) and v != dt:
                    d[k] = dt.strftime("%Y-%m-%d %H:%M:%S")

    def reset_datetime_for_all_enabled_tasks(self, task_datetime: datetime):
        logger.warning(f"trying to reset datetime of all tasks to: {task_datetime}")
        # logger.info(f"current config: {self.dict()}")
        data = self.model_dump()
        self.replace_next_run(data, task_datetime)
        # logger.info(f"new config: {data}")

        # write to json config  file
        from module.config.edit_lock import config_path, config_edit_lock
        path = config_path(self.config_name)
        with config_edit_lock(path):
            self.write_json(self.config_name, data)
            # Reload this explicit reset operation under the same transaction.
            data = self.read_json(self.config_name)
            super().__init__(**data)
            self._initialize_merge_state(data, path, True)


if __name__ == "__main__":
    try:
        c = ConfigModel("oas1")
    except ValidationError as e:
        print(e)
        c = ConfigModel()

    print(c.script_task('GuildBanquet'))
