"""Passive task telemetry. No screenshots, input operations or scheduling changes.

The existing count-loop tasks increment current_count on challenge entry. That
counter is labelled 次挑战; a battle is counted separately only after an existing
game recognizer has observed its settlement. Never derive victories from it.
"""
from __future__ import annotations


# Every executable entry in ConfigMenu has an explicit telemetry contract.
# A zero is meaningful only for tasks whose settlement path is instrumented.
# Non-battle work has task progress but no invented battle counter.
BATTLE_TASKS = frozenset({
    'Orochi', 'Sougenbi', 'FallenSun', 'EternitySea', 'SixRealms',
    'OtherWorldTwilight', 'AreaBoss', 'GoldYoukai', 'ExperienceYoukai',
    'Nian', 'DemonEncounter', 'Pets', 'WantedQuests', 'Tako',
    'BondlingFairyland', 'EvoZone', 'GoryouRealm', 'Exploration',
    'HeroTest', 'FindJade', 'RealmRaid', 'RyouToppa', 'Dokan', 'Hunt',
    'AbyssShadows', 'DemonRetreat', 'TrueOrochi', 'Secret', 'Duel',
    'Moonlight', 'ActivityShikigami', 'MartialTournament', 'MetaDemon',
    'DyeTrials', 'GuguArtStudio',
})
NON_BATTLE_TASKS = frozenset({
    'Restart', 'DailyTrifles', 'TalismanPass', 'SoulsTidy', 'Delegation',
    'AutoCheckinBigGod', 'MemoryScrolls', 'KekkaiUtilize',
    'KekkaiActivation', 'CollectiveMissions', 'GuildBanquet',
    'GuildActivityMonitor', 'RichMan', 'WeeklyTrifles', 'MysteryShop',
    'FrogBoss', 'FloatParade', 'Quiz', 'KittyShop',
})
TASK_METRIC_COMMANDS = BATTLE_TASKS | NON_BATTLE_TASKS


COUNT_TARGETS = {
    'Orochi': ('orochi', 'orochi_config', 'limit_count'),
    'FallenSun': ('fallen_sun', 'fallen_sun_config', 'limit_count'),
    'EvoZone': ('evo_zone', 'evo_zone_config', 'limit_count'),
    'GoryouRealm': ('goryou_realm', 'goryou_config', 'limit_count'),
    'Sougenbi': ('sougenbi', 'sougenbi_config', 'limit_count'),
    'EternitySea': ('eternity_sea', 'eternity_sea_config', 'limit_count'),
    'OtherWorldTwilight': ('other_world_twilight', 'other_world_twilight_config', 'limit_count'),
    'HeroTest': ('hero_test', 'herotest', 'limit_count'),
    'RyouToppa': ('ryou_toppa', 'raid_config', 'limit_count'),
}


def report_task_progress(task, current, target, *, unit='项', phase=None):
    """Publish an existing observed task counter; this never marks success."""
    execution = execution_for(task)
    if execution is not None:
        return execution.report_progress(current, target, unit=unit, phase=phase)
    return False


def battle_unavailable_reason(command):
    return 'not_applicable' if command in NON_BATTLE_TASKS else 'not_observed_yet'


def execution_for(task):
    return getattr(getattr(task, 'config', None), 'solana_execution', None)


def begin_battle(task):
    execution = execution_for(task)
    if execution is not None:
        return execution.begin_battle()
    return None


def finish_battle(task, token, result='settled'):
    execution = execution_for(task)
    if execution is not None and token is not None:
        return execution.finish_battle(token, result)
    return False


def report_count_progress(task):
    """Report only counters whose task loop and target have been inspected."""
    execution = execution_for(task)
    if execution is None:
        return
    command = getattr(getattr(task.config, 'task', None), 'command', None)
    # These counters belong to different loops/phases; do not use the common
    # current_count (which can reset in helper objects) as their numerator.
    if command == 'Moonlight' and hasattr(task, 'moonlight_count'):
        report_task_progress(task, task.moonlight_count,
                             task.config.moonlight.general_config.challenge_limit,
                             unit='次挑战')
        return
    if command == 'MetaDemon' and hasattr(task, 'total_count'):
        report_task_progress(task, task.total_count,
                             task.config.meta_demon.meta_demon_config.limit_count,
                             unit='次挑战')
        return
    if command == 'MartialTournament' and hasattr(task, 'current_mode'):
        mode = task.current_mode
        if mode not in ('ap', 'pass'):
            return
        report_task_progress(task, task.current_count,
                             getattr(task.config.martial_tournament.general_climb, mode + '_limit'),
                             unit='次挑战', phase='体力挑战' if mode == 'ap' else '门票挑战')
        return
    if command == 'ActivityShikigami' and hasattr(task, 'count_map'):
        modes = task.config.activity_shikigami.general_climb.run_sequence_v
        if not modes:
            return
        mode = task.climb_type
        if mode not in modes:
            return
        report_task_progress(task, task.count_map[mode],
                             getattr(task.config.activity_shikigami.general_climb, mode + '_limit'),
                             unit='次挑战', phase={'pass': '门票挑战', 'ap': '体力挑战',
                                 'ap100': '百体挑战', 'boss': '首领挑战'}.get(mode, '活动挑战'))
        return
    path = COUNT_TARGETS.get(command)
    if path is None:
        return  # Do not reinterpret unrelated current_count / limit_count fields.
    target = getattr(task, 'limit_count', None)
    if target is None:
        target = task.config
        for name in path:
            target = getattr(target, name, None)
    current = getattr(task, 'current_count', None)
    if isinstance(current, int) and not isinstance(current, bool) and current >= 0:
        if not isinstance(target, int) or isinstance(target, bool) or target < 0:
            target = None
        execution.report_progress(current, target, unit='次挑战')


def abyss_progress(planned, done, unavailable):
    """Unique configured targets only; someone else's kill is not our work."""
    valid = {f'{area}-{number}' for area in 'ABCD' for number in range(1, 7)}
    planned = set(map(str, planned)) & valid
    done = set(map(str, done)) & planned
    unavailable = (set(map(str, unavailable)) & planned) - done
    return len(done), len(planned), len(unavailable)
