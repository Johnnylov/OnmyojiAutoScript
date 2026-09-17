"""Local, locked rendezvous for two independently scheduled OAS processes.

Only this task's runtime journal is written. Account configurations are never
changed by the other process. A session token fences stopped/restarted runners.
"""
import hashlib
import json
import os
import random
import time
import uuid
from datetime import datetime
from pathlib import Path

from filelock import FileLock


class TeamSyncError(RuntimeError):
    pass


def week_key(now=None):
    year, week, _ = (now or datetime.now()).isocalendar()
    return f'{year}-{week:02d}'


def choose_plan(leader, member, mode, choice=random.choice):
    plans = {
        'true_orochi_leader_twice': [leader, leader],
        'true_orochi_split': [leader, member],
        'true_orochi_member_twice': [member, member],
    }
    if mode == 'true_orochi_random':
        mode = choice(list(plans))
    if mode not in plans:
        raise TeamSyncError(f'未知真蛇开车模式: {mode}')
    return plans[mode]


class LocalTeam:
    STALE_SECONDS = 180

    def __init__(self, name, peer, leader, mode, directory=None, clock=time.time,
                 week=None):
        if name == peer or leader not in (name, peer):
            raise TeamSyncError('真蛇组队需要两个不同的配置和一个初始队长')
        self.name, self.peer = name, peer
        self.clock = clock
        self.week = week or week_key()
        self.token = uuid.uuid4().hex
        pair = sorted((name, peer))
        key = hashlib.sha256(json.dumps(pair).encode()).hexdigest()[:24]
        directory = Path(directory or 'config/.runtime/true_orochi')
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f'{key}.json'
        self.lock = FileLock(str(self.path) + '.lock', timeout=5)
        with self.lock:
            state = self._read()
            members = state.get('members', {})
            live = any(clock() - p['updated'] < self.STALE_SECONDS and
                       p['stage'] != 'finished' for p in members.values())
            reset = (state.get('week') != self.week or state.get('aborted') or not live)
            if reset:
                # Keep this week's random draw even after a deferred attempt.
                plan = state.get('plan') if (state.get('week') == self.week and
                       state.get('leader') == leader and state.get('mode') == mode) else None
                state = dict(week=self.week, pair=pair, leader=leader, mode=mode,
                             plan=plan or choose_plan(leader, peer if leader == name else name, mode),
                             session=uuid.uuid4().hex, members={}, rounds={}, aborted='')
            elif state['leader'] != leader or state['mode'] != mode:
                raise TeamSyncError('两个配置的队长/开车模式与正在运行的真蛇会话不一致')
            elif name in members:
                raise TeamSyncError('本配置的上一次真蛇会话尚未结束，请等待双方退出后重试')
            self.session = state['session']
            state['members'][name] = dict(token=self.token, updated=clock(), stage='checking')
            self._write(state)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            raise TeamSyncError('真蛇同步记录无法读取，停止本次任务') from exc

    def _write(self, state):
        temporary = self.path.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)

    def _checked(self):
        state = self._read()
        if (state.get('session') != self.session or
                state.get('members', {}).get(self.name, {}).get('token') != self.token):
            raise TeamSyncError('真蛇同步会话已改变，旧任务停止操作')
        if state['aborted']:
            raise TeamSyncError(state['aborted'])
        peer = state['members'].get(self.peer)
        if peer and peer['stage'] != 'finished' and self.clock() - peer['updated'] > self.STALE_SECONDS:
            state['aborted'] = f'队友 {self.peer} 超时未响应'
            self._write(state)
            raise TeamSyncError(state['aborted'])
        state['members'][self.name]['updated'] = self.clock()
        return state

    def heartbeat(self):
        with self.lock:
            state = self._checked()
            self._write(state)

    def ready(self, number, entries, rewards):
        if entries not in (0, 1, 2) or rewards not in (0, 1, 2):
            raise TeamSyncError('真蛇次数尚未可靠识别')
        with self.lock:
            state = self._checked()
            row = state['rounds'].setdefault(str(number), dict(ready={}, joined=[], done={}))
            observation = dict(entries=entries, rewards=rewards)
            if self.name in row['ready'] and row['ready'][self.name] != observation:
                raise TeamSyncError('同一轮的真蛇次数发生变化，请重新检查')
            row['ready'][self.name] = observation
            state['members'][self.name]['stage'] = 'ready'
            if len(row['ready']) == 2 and 'decision' not in row:
                remaining = max(p['rewards'] for p in row['ready'].values())
                if remaining == 0:
                    row['decision'] = 'finished'
                else:
                    preferred = state['plan'][2 - remaining]
                    alternate = next(n for n in state['pair'] if n != preferred)
                    row['decision'] = next((n for n in (preferred, alternate)
                                            if row['ready'][n]['entries'] > 0), 'no_entries')
            self._write(state)

    def round_state(self, number):
        with self.lock:
            state = self._checked()
            self._write(state)
            return state['rounds'].get(str(number), {})

    def joined(self, number):
        with self.lock:
            state = self._checked()
            row = state['rounds'][str(number)]
            if self.name not in row['joined']:
                row['joined'].append(self.name)
            state['members'][self.name]['stage'] = 'joined'
            self._write(state)

    def done(self, number, success):
        with self.lock:
            state = self._checked()
            state['rounds'][str(number)]['done'][self.name] = bool(success)
            state['members'][self.name]['stage'] = 'settled'
            if not success:
                state['aborted'] = f'{self.name} 真蛇战斗未成功完成，本轮停止'
            self._write(state)

    def close(self, error=''):
        with self.lock:
            state = self._read()
            # Never let cleanup by an old process cancel a replacement session.
            if (state.get('session') != self.session or
                    state.get('members', {}).get(self.name, {}).get('token') != self.token):
                return
            if error:
                state['aborted'] = str(error)
            state['members'][self.name].update(stage='finished', updated=self.clock())
            self._write(state)
