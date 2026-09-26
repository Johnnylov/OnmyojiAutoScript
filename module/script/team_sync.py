"""Local, two-profile task rendezvous. No peer config or game input is written here."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from filelock import FileLock

from module.config.atomicwrites import atomic_write
from module.logger import logger


TASK_KEYS = {'Orochi': 'orochi', 'BondlingFairyland': 'bondling_fairyland', 'TrueOrochi': 'true_orochi'}
TASK_SWITCHES = {'Orochi': 'sync_orochi', 'BondlingFairyland': 'sync_bondling',
                 'TrueOrochi': 'sync_true_orochi'}
TASK_LABELS = {'Orochi': '八岐大蛇', 'BondlingFairyland': '契灵', 'TrueOrochi': '真八岐大蛇'}


class TeamTaskSwitch(Exception):
    """Yield without changing the interrupted task's schedule."""


class TeamSyncUnavailable(Exception):
    """The requested team cannot currently rendezvous; retry later."""


class TeamPartnerFinished(Exception):
    def __init__(self, next_run: str):
        self.next_run = datetime.fromisoformat(next_run)
        super().__init__('队友已结束本轮组队，跟随队友的下次运行时间')


class TeamState:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + '.lock', timeout=5):
            state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
                'peers': {}, 'sessions': {},
            }
            original = json.dumps(state, ensure_ascii=False, sort_keys=True)
            yield state
            if json.dumps(state, ensure_ascii=False, sort_keys=True) != original:
                with atomic_write(str(self.path), overwrite=True, encoding='utf-8') as stream:
                    json.dump(state, stream, ensure_ascii=False, indent=2)


class LocalTeamCoordinator:
    HEARTBEAT_INTERVAL = 2
    STALE_AFTER = 30
    RETRY_AFTER = 120
    # Recovery and TrueOrochi's two-round protocol finish normally once started.
    NO_INTERRUPT = {'Restart', 'GotoMain', 'TrueOrochi'}

    def __init__(self, config_name: str, root: Path | None = None, *, clock=time.time):
        self.name = config_name
        self.root = Path(root or Path.cwd())
        self.state = TeamState(self.root / 'log' / '.local_team.json')
        self.clock = clock
        self.token = uuid4().hex
        self.current_task = ''
        self.session_id = None
        self.pair_key = None
        self._stop = threading.Event()
        self._thread = None
        self._next_poll = 0
        self._config_cache = {}

    def _read_config(self, name):
        if not isinstance(name, str) or not name or any(c in name for c in '/\\:') or name in {'.', '..'}:
            raise TeamSyncUnavailable('组队配置名称无效')
        path = self.root / 'config' / (name + '.json')
        try:
            stamp = path.stat().st_mtime_ns
            cached = self._config_cache.get(name)
            if cached and cached[0] == stamp:
                return cached[1]
            with FileLock(str(path) + '.lock', timeout=5):
                data = json.loads(path.read_text(encoding='utf-8'))
            self._config_cache[name] = (stamp, data)
            return data
        except (OSError, ValueError) as exc:
            raise TeamSyncUnavailable(f'无法读取组队配置 {name}') from exc

    def _settings(self):
        return self._read_config(self.name).get('global_game', {}).get('local_team', {})

    def _pair(self, task):
        settings = self._settings()
        if task not in TASK_KEYS or not settings.get('enable') or not settings.get(
                TASK_SWITCHES[task], task != 'TrueOrochi'):
            return None
        partner = settings.get('partner_config', '').strip()
        if partner == self.name:
            raise TeamSyncUnavailable('组队配置不能绑定自身')
        own = self._read_config(self.name)
        other = self._read_config(partner)
        peer_settings = other.get('global_game', {}).get('local_team', {})
        if not peer_settings.get('enable') or peer_settings.get('partner_config', '').strip() != self.name:
            raise TeamSyncUnavailable(f'{partner} 未启用相互绑定的本机组队联动')
        label = TASK_LABELS[task]
        if not peer_settings.get(TASK_SWITCHES[task], task != 'TrueOrochi'):
            raise TeamSyncUnavailable(f'{partner} 未启用{label}联动')
        key = TASK_KEYS[task]
        for name, data in ((self.name, own), (partner, other)):
            if not data.get(key, {}).get('scheduler', {}).get('enable'):
                raise TeamSyncUnavailable(f'{name} 的{label}任务已关闭')
        group = {'Orochi': 'orochi_config', 'BondlingFairyland': 'bondling_config',
                 'TrueOrochi': 'team_config'}[task]
        a, b = own[key].get(group, {}), other[key].get(group, {})
        roles = {a.get('user_status'), b.get('user_status')}
        valid_roles = roles == {'leader', 'member'} or (
            task == 'BondlingFairyland' and roles == {'handoff1', 'handoff2'})
        if not valid_roles:
            raise TeamSyncUnavailable(f'{label}需要队长和队员配对；契灵也支持交接一和交接二')
        if task == 'TrueOrochi':
            for name, peer_name, data, team in ((self.name, partner, own, a),
                                               (partner, self.name, other, b)):
                if not team.get('enable') or team.get('teammate_config', '').strip() != peer_name:
                    raise TeamSyncUnavailable(f'{name} 须启用真蛇双开组队，且真蛇队友与本机联动队友一致')
                friends = data[key].get('invite_config', {}).get('friend_list', '')
                if len([line for line in friends.splitlines() if line.strip()]) != 1:
                    raise TeamSyncUnavailable(f'{name} 的真蛇邀请名单须填写对方的一个游戏好友名')
            own_serial = own.get('script', {}).get('device', {}).get('serial')
            peer_serial = other.get('script', {}).get('device', {}).get('serial')
            if own_serial and own_serial == peer_serial:
                raise TeamSyncUnavailable('两个真蛇配置不能操作同一个模拟器，请分别指定设备连接地址')
        if task == 'Orochi' and a.get('layer') != b.get('layer'):
            raise TeamSyncUnavailable('两份八岐大蛇配置的层数不同')
        if task == 'BondlingFairyland':
            if a.get('bondling_mode') in {'mode_1', '只刷探查(仅限单刷)'}:
                raise TeamSyncUnavailable('只刷探查不支持组队联动')
            if any(a.get(field) != b.get(field) for field in ('bondling_mode', 'bondling_stone_class')):
                raise TeamSyncUnavailable('两份契灵配置的模式或契灵种类不同')
        members = sorted([self.name, partner])
        timeout = min(settings.get('ready_timeout', 600), peer_settings.get('ready_timeout', 600))
        return json.dumps(members, ensure_ascii=False), members, max(30, min(1800, int(timeout)))

    def start(self):
        if self._thread is not None or not self._settings().get('enable'):
            return
        self._stop.clear()
        self._heartbeat()
        self._thread = threading.Thread(target=self._keep_alive, name='LocalTeamHeartbeat', daemon=True)
        self._thread.start()

    def _heartbeat(self):
        with self.state.transaction() as state:
            state['peers'][self.name] = {
                'token': self.token, 'updated': self.clock(), 'task': self.current_task,
            }

    def _keep_alive(self):
        while not self._stop.wait(self.HEARTBEAT_INTERVAL):
            try:
                self._heartbeat()
            except Exception as exc:
                logger.warning(f'Local team heartbeat failed: {exc}')

    def close(self):
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=6)
        self._thread = None
        with self.state.transaction() as state:
            if state['peers'].get(self.name, {}).get('token') == self.token:
                del state['peers'][self.name]

    def _expire(self, state, session):
        if session['status'] not in {'waiting', 'running'}:
            return
        reason = None
        if session['status'] == 'waiting' and self.clock() >= session['deadline']:
            reason = '等待队友就绪超时'
        for name, token in session['tokens'].items():
            if name in session['done']:
                continue
            peer = state['peers'].get(name, {})
            if peer.get('token') != token or self.clock() - peer.get('updated', 0) > self.STALE_AFTER:
                reason = f'{name} 已停止或重启'
        if reason:
            session.update(status='cancelled', reason=reason, retry_at=self.clock() + self.RETRY_AFTER)

    def pending_task(self):
        settings = self._settings()
        if not settings.get('enable'):
            if self.session_id:
                self._cancel(self.pair_key, self.session_id, '本配置已关闭组队联动')
            return None
        self.start()
        partner = settings.get('partner_config', '').strip()
        key = json.dumps(sorted([self.name, partner]), ensure_ascii=False)
        with self.state.transaction() as state:
            session = state['sessions'].get(key)
            if not session:
                return None
            self._expire(state, session)
            if session['status'] not in {'waiting', 'running'} or self.name in session['done']:
                return None
            task = session['task']
        try:
            pair = self._pair(task)
            if pair is None or pair[0] != key:
                raise TeamSyncUnavailable('组队联动已关闭或绑定已改变')
        except TeamSyncUnavailable as exc:
            self._cancel(key, session['id'], str(exc))
            return None
        return task

    def request(self, task):
        pair = self._pair(task)
        if pair is None:
            return task
        self.start()
        key, members, timeout = pair
        with self.state.transaction() as state:
            session = state['sessions'].get(key)
            if session:
                self._expire(state, session)
            if session and session['status'] in {'waiting', 'running'}:
                if self.name in session['done']:
                    raise TeamSyncUnavailable('上一轮组队尚未结束，稍后重试')
                # The first request wins, including simultaneous different tasks.
                return session['task']
            if (session and session['status'] == 'skipped' and session['task'] == task and
                    self.clock() < datetime.fromisoformat(session['next_run']).timestamp()):
                raise TeamPartnerFinished(session['next_run'])
            if session and session['status'] == 'cancelled' and self.clock() < session['retry_at']:
                raise TeamSyncUnavailable(session['reason'])
            tokens = {name: state['peers'][name]['token'] for name in members
                      if name in state['peers'] and
                      self.clock() - state['peers'][name]['updated'] <= self.STALE_AFTER}
            session = {
                'id': uuid4().hex, 'task': task, 'source': self.name, 'members': members,
                'tokens': tokens, 'deadline': self.clock() + timeout, 'status': 'waiting',
                'ready': [], 'done': {},
            }
            state['sessions'][key] = session
        logger.info(f'本机组队联动: {self.name} 发起 {task}，等待 {members}')
        return task

    def begin(self, task):
        self.current_task = task
        self.session_id = None
        self.pair_key = None
        if task not in TASK_KEYS:
            return
        pair = self._pair(task)
        if pair is None:
            return
        key, _, _ = pair
        with self.state.transaction() as state:
            session = state['sessions'].get(key)
            if not session:
                return  # Direct, unscheduled runs retain their original behavior.
            self._expire(state, session)
            if session['status'] == 'cancelled':
                raise TeamSyncUnavailable(session['reason'])
            if session['status'] == 'skipped':
                raise TeamPartnerFinished(session['next_run'])
            if session['status'] == 'finished':
                return
            if session['task'] != task:
                raise TeamTaskSwitch(f'优先切换到双方已约定的 {session["task"]}')
            if self.name in session['done']:
                raise TeamSyncUnavailable('本配置已完成当前组队轮次')
            session['tokens'][self.name] = self.token
            self.session_id, self.pair_key = session['id'], key

    def _cancel(self, key, session_id, reason):
        with self.state.transaction() as state:
            session = state['sessions'].get(key)
            if session and session['id'] == session_id:
                session.update(status='cancelled', reason=reason, retry_at=self.clock() + self.RETRY_AFTER)

    def ready(self, on_wait=None):
        if not self.session_id:
            return
        logger.info(f'本机组队联动: {self.name} 已准备好 {self.current_task}，等待队友')
        last_log = self.clock()
        while True:
            if self._stop.is_set():
                self._cancel(self.pair_key, self.session_id, '本配置已停止')
                raise TeamSyncUnavailable('本配置已停止')
            if on_wait is not None:
                on_wait()
            pair = self._pair(self.current_task)
            if pair is None or pair[0] != self.pair_key:
                raise TeamSyncUnavailable('等待期间组队联动已关闭或绑定已改变')
            with self.state.transaction() as state:
                session = state['sessions'].get(self.pair_key)
                if not session or session['id'] != self.session_id:
                    raise TeamSyncUnavailable('组队请求已经失效')
                self._expire(state, session)
                if session['status'] == 'cancelled':
                    error = TeamSyncUnavailable(session['reason'])
                elif session['status'] == 'skipped':
                    error = TeamPartnerFinished(session['next_run'])
                else:
                    error = None
                    if self.name not in session['ready']:
                        session['ready'].append(self.name)
                    if set(session['ready']) == set(session['members']):
                        session['status'] = 'running'
                ready = session['status'] == 'running'
            if error:
                raise error
            if ready:
                logger.info(f'本机组队联动: 双方 {self.current_task} 已就绪，开始组队')
                return
            if self.clock() - last_log >= 30:
                logger.info('本机组队联动: 队友仍在结束当前任务或进行准备')
                last_log = self.clock()
            time.sleep(0.5)

    def interruption(self):
        if not self.current_task or self.current_task in self.NO_INTERRUPT or self.clock() < self._next_poll:
            return None
        self._next_poll = self.clock() + 1
        pending = self.pending_task()
        if pending and pending != self.current_task:
            return TeamTaskSwitch(f'收到队友的 {pending} 请求，当前任务保留在原调度中')
        if self.session_id:
            with self.state.transaction() as state:
                session = state['sessions'].get(self.pair_key)
                if session and session['id'] == self.session_id:
                    if session['status'] == 'cancelled':
                        return TeamSyncUnavailable(session['reason'])
                    if session['status'] == 'skipped':
                        return TeamPartnerFinished(session['next_run'])
                    for name, next_run in session['done'].items():
                        if name != self.name and next_run:
                            return TeamPartnerFinished(next_run)
        return None

    def finish(self, task, completed):
        if self.session_id and task == self.current_task:
            next_run = self._read_config(self.name)[TASK_KEYS[task]]['scheduler']['next_run']
            with self.state.transaction() as state:
                session = state['sessions'].get(self.pair_key)
                if session and session['id'] == self.session_id:
                    if completed and session['status'] == 'running':
                        session['done'][self.name] = next_run
                        if len(session['done']) == len(session['members']):
                            session['status'] = 'finished'
                    elif completed and session['status'] == 'waiting':
                        # A capacity/availability check may intentionally skip the task.
                        # Let the partner follow that schedule instead of retrying forever.
                        session.update(status='skipped', next_run=next_run)
                    elif session['status'] in {'waiting', 'running'}:
                        session.update(status='cancelled', reason=f'{self.name} 提前结束或准备失败',
                                       retry_at=self.clock() + self.RETRY_AFTER)
        self.current_task = ''
        self.session_id = None
        self.pair_key = None
