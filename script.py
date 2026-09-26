# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import zerorpc
import zmq
import re
import cv2
import time
import os
import inflection
import json
import copy

from datetime import date
import threading
from module.device.device import Device
from typing import Any, Callable
from datetime import datetime, timedelta
from pathlib import Path
from cached_property import cached_property
from pydantic import BaseModel, ValidationError
from threading import Thread
from multiprocessing.queues import Queue
from module.config.utils import convert_to_underscore
from module.config.config import Config
from module.config.anti_ban import AntiBanGuard
from module.device.device import Device
from module.device.env import IS_WINDOWS
from module.base.utils import load_module
from module.base.decorator import del_cached_property
from module.logger import logger
from module.exception import *
from module.server.i18n import I18n
from module.image.rpc import ensure_image_server_ready
from module.ocr.rpc import ensure_ocr_server_ready
from module.script import ScriptRuntimeController, ScriptRuntimeDecision
from module.script.team_sync import LocalTeamCoordinator
from tasks.Restart.server_update import delay_pending_tasks_for_server_update, is_server_update_window
from module.server.log_service import build_error_log_dir_name
from module.scheduling.runtime import (ExecutionRuntime, SafeBoundaryExit, ReconciliationRequired,
                                      DispatchStopped, DeadlineExpired, classify_outcome, cooperative_task)
from module.scheduling.coordinator import LeaseLost
from module.scheduling.deadline import is_expired

_log_switch_lock = threading.Lock()#线程锁


class Script:
    def __init__(self, config_name: str ='oas', solana_bridge=None) -> None:
        logger.hr('Start', level=0)
        self.server = None
        self.state_queue: Queue = None
        self._emulator_down = False
        self.runtime = ScriptRuntimeController(self)
        self.gui_update_task: Callable = None  # 回调函数, gui进程注册当每次config更新任务的时候更新gui的信息
        self.config_name = config_name
        self.solana_bridge = solana_bridge
        self.solana_execution = ExecutionRuntime(solana_bridge, config_name) if solana_bridge else None
        self.team_sync = LocalTeamCoordinator(config_name)
        # Skip first restart
        self.is_first_task = True
        # Failure count of tasks
        # Key: str, task name, value: int, failure count
        self.failure_record = {}
        self.last_task_runtime_outcome: dict[str, Any] | None = None
        self.task_hoarding_until: datetime | None = None
        self.task_hoarding_released = False
        # 运行loop的线程
        self.loop_thread: Thread = None
        self.anti_ban_guard: AntiBanGuard = AntiBanGuard()

    @cached_property
    def config(self) -> "Config":
        try:
            from module.config.config import Config
            config = Config(config_name=self.config_name)
            config.team_sync = self.team_sync
            if getattr(self, 'solana_execution', None) is not None:
                config.solana_execution = getattr(self, 'solana_execution', None)
            return config
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def device(self) -> Device | None:
        try:
            from module.device.device import Device
            device = Device(config=self.config)
            return device
        except RequestHumanTakeover:
            logger.critical('Request human takeover')
            exit(1)
        except Exception as e:
            logger.exception(e)
            exit(1)

    @cached_property
    def checker(self):
        """
        占位函数，在alas中是检查服务器是否正常的
        :return:
        """
        return None

    def save_error_log(self):
        """
        保存错误现场到 ./log/error/<script_name>_<timestamp_ms>。

        保存内容包括:
        - 最近一段截图, 文件名为时间戳 PNG。
        - 当前脚本日志的截取内容, 文件名为 log.txt。

        说明:
        - 新错误目录名会带上脚本名, 便于前端区分不同脚本产生的错误。
        - 目录名和脚本名都会经过统一净化, 避免路径注入。
        """
        from module.base.utils import save_image
        from module.handler.sensitive_info import (handle_sensitive_image,
                                                   handle_sensitive_logs)
        if self.config.script.error.save_error:
            if not os.path.exists('./log/error'):
                os.mkdir('./log/error')
            # 用统一规则生成错误目录名, 目录格式为 <script_name>_<timestamp_ms>。
            folder_name = build_error_log_dir_name(self.config_name, int(time.time() * 1000))
            folder = f'./log/error/{folder_name}'
            logger.warning(f'Saving error: {folder}')
            os.mkdir(folder)
            for data in self.device.screenshot_deque:
                image_time = datetime.strftime(data['time'], '%Y-%m-%d_%H-%M-%S-%f')
                image = handle_sensitive_image(data['image'])
                save_image(image, f'{folder}/{image_time}.png')
            with open(logger.log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                start = 0
                for index, line in enumerate(lines):
                    line = line.strip(' \r\t\n')
                    if re.match('^═{15,}$', line):
                        start = index
                lines = lines[start - 2:]
                lines = handle_sensitive_logs(lines)
            with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
                f.writelines(lines)

    def init_server(self, port: int) -> int:
        """
        初始化zerorpc服务，返回端口号
        :return:
        """
        self.server = zerorpc.Server(self)
        try:
            self.server.bind(f'tcp://127.0.0.1:{port}')
            return port
        except zmq.error.ZMQError:
            logger.error(f"Ocr server cannot bind on port {port}")
            return None

    def run_server(self) -> None:
        """
        启动zerorpc服务
        :return:
        """
        self.server.run()

    def gui_args(self, task: str) -> str:
        """
        获取给gui显示的参数
        :return:
        """
        return self.config.gui_args(task=task)

    def gui_menu(self) -> str:
        """
        获取给gui显示的菜单
        :return:
        """
        return self.config.gui_menu

    def gui_task(self, task: str) -> str:
        """
        获取给gui显示的任务 的参数的具体值
        :return:
        """
        return self.config.model.gui_task(task=task)

    def gui_set_task(self, task: str, group: str, argument: str, value) -> bool:
        """
        设置给gui显示的任务 的参数的具体值
        :return:
        """
        # 验证参数
        task = convert_to_underscore(task)
        group = convert_to_underscore(group)
        argument = convert_to_underscore(argument)
        # pandtic验证
        if isinstance(value, str):
            if len(value) == 8:
                try:
                    value = datetime.strptime(value, '%H:%M:%S').time()
                except ValueError:
                    pass


        path = f'{task}.{group}.{argument}'
        task_object = getattr(self.config.model, task, None)
        group_object = getattr(task_object, group, None)
        argument_object = getattr(group_object, argument, None)

        if argument_object is None:
            logger.error(f'Set arg {task}.{group}.{argument}.{value} failed')
            return False

        try:
            setattr(group_object, argument, value)
            argument_object = getattr(group_object, argument, None)
            logger.info(f'Set arg {task}.{group}.{argument}.{argument_object}')
            self.config.save()  # 我是没有想到什么方法可以使得属性改变自动保存的
            return True
        except ValidationError as e:
            logger.error(e)
            return False

    @zerorpc.stream
    def gui_mirror_image(self):
        """
        获取给gui显示的镜像
        :return: cv2的对象将 numpy 数组转换为字节串。接下来MsgPack 进行序列化发送方将图像数据转换为字节串
        """
        # return msgpack.packb(cv2.imencode('.jpg', self.device.screenshot())[1].tobytes())
        img = cv2.cvtColor(self.device.screenshot(), cv2.COLOR_RGB2BGR)
        self.device.stuck_record_clear()
        ret, buffer = cv2.imencode('.jpg', img)
        yield buffer.tobytes()

    def _gui_update_tasks(self) -> None:
        """
        获取更新任务后 pending waiting 的任务 和 当前的任务的数据。打包给gui显示
        :return:
        """
        data = {}
        pending = []
        waiting = []
        task = {}
        if self.config.task is not None and self.config.task.next_run < datetime.now():
            task["name"] = self.config.task.command
            task["next_run"] = str(self.config.task.next_run)
        data["task"] = task

        for p in self.config.pending_task[1:]:
            item = {"name": p.command, "next_run": str(p.next_run)}
            pending.append(item)

        for w in self.config.waiting_task:
            item = {"name": w.command, "next_run": str(w.next_run)}
            waiting.append(item)


        data["pending"] = pending
        data["waiting"] = waiting

        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def _gui_set_status(self, status: str) -> None:
        """
        设置给gui显示的状态
        :param status: 可以在gui中显示的状态 有 "Init", "Empty"(不显示), "Run"(运行中), "Error", "Free"(空闲)
        :return:
        """
        data = {"status": status}
        if self.gui_update_task is not None:
            self.gui_update_task(data)

    def gui_task_list(self) -> str:
        """
        获取给gui显示的任务列表
        :return:
        """
        result = {}
        for key, value in self.config.model.dict().items():
            if isinstance(value, str):
                continue
            if key == "restart":
                continue
            if "scheduler" not in value:
                continue

            scheduler = value["scheduler"]
            item = {"enable": scheduler["enable"],
                    "next_run": str(scheduler["next_run"])}
            key = self.config.model.type(key)
            result[key] = item
        return json.dumps(result)

    def wait_until(self, future):
        """
        Wait until a specific time.

        Args:
            future (datetime):

        Returns:
            bool: True if wait finished, False if config changed.
        """
        execution = getattr(self, 'solana_execution', None)
        if execution is not None and execution.lease and execution.active is None:
            execution.release()
        future = future + timedelta(seconds=1)
        self.config.start_watching()
        while 1:
            if execution is not None:
                control = execution.control_status().get('control')
                if control in ('stop', 'safe_stop'):
                    execution.stop_at_boundary()
                if control in ('pause', 'paused'):
                    time.sleep(0.5)
                    continue
            if datetime.now() > future:
                return True
            # if self.stop_event is not None:
            #     if self.stop_event.is_set():
            #         logger.info("Update event detected")
            #         logger.info(f"[{self.config_name}] exited. Reason: Update")
            #         exit(0)

            team_sync = getattr(self, 'team_sync', None)
            if team_sync:
                requested = team_sync.pending_task()
                if requested and requested != getattr(self, '_waiting_team_request', None):
                    return False
            time.sleep(1 if team_sync else 5)

            if self.config.should_reload():
                return False

    def _hoard_next_task(self, task, now: datetime):
        """在空闲时延迟首个到期任务，窗口内到达的任务一并等待。"""
        duration = self.config.script.optimization.task_hoarding_duration
        if duration <= 0 or not self.config.pending_task:
            self.task_hoarding_until = None
            self.task_hoarding_released = False
            return task

        if (self.config.model.running_task or self.task_hoarding_released
                or (self.is_first_task and task.command == 'Restart')
                or (task.next_run > now and self.task_hoarding_until is None)):
            return task

        if self.task_hoarding_until is None:
            self.task_hoarding_until = now + timedelta(minutes=duration)
            logger.info(
                f"Task hoarding started for {duration:g} minutes, "
                f"release at {self.task_hoarding_until.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        if now < self.task_hoarding_until:
            task = copy.deepcopy(task)
            task.next_run = max(self.task_hoarding_until, task.next_run)
            logger.info(
                f"Task hoarding active, defer pending tasks until "
                f"{self.task_hoarding_until.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            return task

        self.task_hoarding_until = None
        self.task_hoarding_released = True
        logger.info("Task hoarding window ended, resume scheduled task execution")
        return task

    def get_next_task(self) -> str:
        """
        获取下一个任务的名字, 大驼峰。
        :return:
        """
        from module.scheduling.coordinator import LeaseLost
        from module.scheduling.deadline import is_expired
        while True:
            task = self.config.get_next()
            team_sync = getattr(self, 'team_sync', None)
            requested = team_sync.pending_task() if team_sync else None
            self._waiting_team_request = requested
            if requested and (task.command != 'Restart' or task.next_run > datetime.now()):
                from module.config.config import Function
                key = convert_to_underscore(requested)
                requested_task = Function(key, getattr(self.config.model, key).model_dump())
                if is_expired(requested_task.scheduling.get('deadline'), time.time()):
                    requested = None
                    self._waiting_team_request = None
                else:
                    task = requested_task
                    task.next_run = datetime.now().replace(microsecond=0)
                    self.config.pending_task = [task] + [
                        item for item in self.config.pending_task if item.command != requested]
                    self.config.waiting_task = [item for item in self.config.waiting_task if item.command != requested]
            now = datetime.now()
            antiban_wake = self.anti_ban_guard.wake_time(now, self.config.script.anti_ban)
            if antiban_wake is not None:
                task.next_run = max(task.next_run, antiban_wake)
            if not requested:
                task = self._hoard_next_task(task, now)
            self.config.task = task
            if self.state_queue:
                self.state_queue.put({"schedule": self.config.get_schedule_data()})
            # 任务时间到了返回任务名称
            if task.next_run <= now:
                if getattr(self, 'solana_execution', None) is not None:
                    result = self.solana_execution.acquire(self.config, preferred_task=requested)
                    if result['status'] != 'acquired':
                        if result.get('control') in ('stop', 'safe_stop'):
                            self.solana_execution.stop_at_boundary()
                        self._wait_for_dispatch(result)
                        del_cached_property(self, 'config')
                        continue
                    self._dispatch_wait_key = None
                    self._dispatch_wait_attempts = 0
                    command = result['lease']['task']
                    selected = next(item for item in self.config.pending_task + self.config.waiting_task
                                    if item.command == command)
                    self.config.task = selected
                    try:
                        if team_sync and team_sync.request(command) != command:
                            self.solana_execution.release()
                            continue
                    except Exception:
                        # No gameplay started, but the admission lease already
                        # exists. A peer rejection must never leave it held.
                        self.solana_execution.release()
                        raise
                    return command
                if team_sync and team_sync.request(task.command) != task.command:
                    continue
                return task.command
            # 根据策略执行等待逻辑
            wait_until = task.next_run
            if self.task_hoarding_until and self.config.waiting_task:
                wait_until = min(wait_until, self.config.waiting_task[0].next_run)
            if getattr(self, 'solana_execution', None) is not None:
                # Publish future candidates, then obtain short maintenance leases
                # for existing close-game/preheat behaviour. Sleeping releases them.
                idle_result = self.solana_execution.acquire(self.config, release_floor=wait_until.timestamp())
                if idle_result.get('control') in ('stop', 'safe_stop'):
                    self.solana_execution.stop_at_boundary()
                self.solana_execution.maintenance_mode = True
            try:
                decision = self.runtime.handle_wait_during_idle(wait_until)
            except LeaseLost:
                time.sleep(0.5)
                decision = ScriptRuntimeDecision.RESCHEDULE
            finally:
                if getattr(self, 'solana_execution', None) is not None:
                    self.solana_execution.maintenance_mode = False
                    if self.solana_execution.lease and self.solana_execution.active is None:
                        self.solana_execution.release()
            if decision == ScriptRuntimeDecision.RESCHEDULE:
                logger.info('Idle wait requested scheduler refresh, reload config and reschedule')
                del_cached_property(self, "config")
            elif decision == ScriptRuntimeDecision.FAILED:
                logger.warning('Idle wait preparation failed, reload config and retry scheduling')
                del_cached_property(self, "config")

    def _wait_for_dispatch(self, result):
        """Back off unchanged admission waits, while still honoring stop promptly."""
        decision = result.get('decision') or {}
        blocked = decision.get('blocked') or {}
        local_prefix = self.solana_execution.profile_id + ':'
        local_reasons = [reason for key, reason in blocked.items() if key.startswith(local_prefix)]
        if local_reasons and all(reason == 'recovery_budget_disabled' for reason in local_reasons):
            raise ReconciliationRequired('恢复预算为零，已停止调度，请调整恢复预算后重新运行')
        if local_reasons and all(reason == 'recovery_budget_exhausted' for reason in local_reasons):
            raise ReconciliationRequired('任务连续恢复失败，已停止调度，请检查游戏状态后重新运行')
        reason = result.get('reason') or decision.get('reason') or result['status']
        key = (result['status'], result.get('control'), reason, tuple(sorted(blocked.items())))
        changed = key != getattr(self, '_dispatch_wait_key', None)
        attempts = 1 if changed else getattr(self, '_dispatch_wait_attempts', 0) + 1
        self._dispatch_wait_key, self._dispatch_wait_attempts = key, attempts
        now = time.monotonic()
        if changed or now - getattr(self, '_dispatch_wait_logged_at', 0) >= 60:
            logger.info(f'Scheduler waiting: {reason}; blocked={blocked}')
            self._dispatch_wait_logged_at = now
        remaining = min(5.0, 0.5 * 2 ** min(attempts - 1, 4))
        while remaining > 0:
            control = self.solana_execution.control_status().get('control')
            if control in ('stop', 'safe_stop'):
                self.solana_execution.stop_at_boundary()
            interval = min(0.5, remaining)
            time.sleep(interval)
            remaining -= interval

    def exception_handler(self, e: Exception, command: str) -> None:
        # 处理御魂溢出
        from tasks.Utils.post_diagnotor import PostDiagnotor, AnalyzeType
        image = getattr(self.device, 'image', None)
        # image为None则不做处理
        if image is None:
            return
        analyse_type = PostDiagnotor().handle(e=e, command=command, image=image)
        if analyse_type == AnalyzeType.SoulOverflow:
            self.config.task_call('SoulsTidy')
            time.sleep(1)

    def _reset_task_runtime_outcome(self) -> None:
        self.last_task_runtime_outcome = None
        self._solana_error_category = None
        if 'config' in self.__dict__:
            self.config.task_runtime_outcome = None

    def _set_task_runtime_outcome(self, task: str, status: str, wait_until: datetime | None = None) -> None:
        outcome = {
            'task': task,
            'status': status,
        }
        if wait_until is not None:
            outcome['wait_until'] = wait_until
        self.last_task_runtime_outcome = outcome
        if 'config' in self.__dict__:
            self.config.task_runtime_outcome = outcome

    def _capture_task_runtime_outcome(self, command: str) -> None:
        outcome = getattr(self.config, 'task_runtime_outcome', None)
        self.last_task_runtime_outcome = outcome if isinstance(outcome, dict) else None
        if self.last_task_runtime_outcome is None:
            return
        status = self.last_task_runtime_outcome.get('status')
        if status == 'server_update_delayed':
            wait_until = self.last_task_runtime_outcome.get('wait_until')
            logger.info(f'{command} runtime outcome: server_update_delayed (wait_until={wait_until})')
            if isinstance(wait_until, datetime):
                self.runtime.server_update_wait_until = wait_until
                self.runtime.server_update_wait_log_until = None
            return
        if command != 'Restart':
            return
        if status == 'recovered':
            logger.info('Restart runtime outcome: recovered')
            return
        logger.info(f'Restart runtime outcome: {status}')

    def _delay_tasks_for_server_update(self, task: str, reason: str) -> bool:
        if not is_server_update_window():
            return False

        delay_target = delay_pending_tasks_for_server_update(self.config, reason=reason)
        self._set_task_runtime_outcome(task=task, status='server_update_delayed', wait_until=delay_target)
        return True

    def run(self, command: str) -> bool:
        """
        :param command:  大写驼峰命名的任务名字
        :return:
        """
        if command == 'start' or command == 'goto_main':
            logger.error(f'Invalid command `{command}`')

        self._reset_task_runtime_outcome()
        team_sync = getattr(self, 'team_sync', None)
        completed = False
        try:
            if team_sync:
                team_sync.begin(command)
            self.device.screenshot()
            module_name = 'script_task'
            module_path = str(Path.cwd() / 'tasks' / command / (module_name + '.py'))
            logger.info(f'module_path: {module_path}, module_name: {module_name}')
            task_module = load_module(module_name, module_path)
            returned = task_module.ScriptTask(config=self.config, device=self.device).run()
            if returned is True:
                completed = True
                return True
        except Exception as e:
            result = self._handle_task_exception(e, command)
            completed = isinstance(e, TaskEnd) or (
                isinstance(self.last_task_runtime_outcome, dict) and
                self.last_task_runtime_outcome.get('status') == 'team_partner_finished')
            return result
        finally:
            if team_sync:
                team_sync.finish(command, completed)
        return False

    def _defer_business_precondition(self, command):
        """Known clock-only prerequisites need no run or game preparation."""
        from module.scheduling.preflight import business_preflight
        from module.scheduling.runtime import ReconciliationRequired
        execution = getattr(self, 'solana_execution', None)
        if execution is None:
            return False
        decision = business_preflight(command, datetime.now())
        if decision is None:
            return False
        try:
            self.config.task_delay(task=command, target=decision.next_run, server=False)
            execution.skip_before_begin(command, decision)
        except Exception as exc:
            # An unconfirmed schedule/decision must not become new gameplay.
            raise ReconciliationRequired('Business prerequisite defer could not be saved') from exc
        return True

    def _finish_preparation_reschedule(self):
        execution = getattr(self, 'solana_execution', None)
        if execution is None or not execution.active:
            return
        if getattr(self.runtime, 'preparation_recovered', False):
            # Restart has verified that the game is running. The business task
            # has not run yet; return to ordinary selection, without marking
            # the whole device as failed or consuming the recovery budget.
            execution.recovery_tasks.clear()
            execution.finish('yielded', 'runtime_prepared')
        else:
            execution.finish('recovery_requested', 'runtime_preparation')

    def loop(self):
        """
        Main loop of scheduler.
        :return:
        """
        from time import monotonic
        from module.scheduling.runtime import DispatchStopped, ReconciliationRequired, DeadlineExpired
        from module.scheduling.coordinator import LeaseLost
        team_sync = getattr(self, 'team_sync', None)
        try:
            if team_sync:
                team_sync.start()
            with _log_switch_lock:
                logger.set_file_logger(self.config_name, do_cleanup=True)
            start_day = date.today()
            logger.info(f'Start scheduler loop: {self.config_name}')
            self.config.model.running_task = ''
            self.anti_ban_guard.reset()

            # Update GUI 防呆, 读取设置并立刻显示后台模拟器到前台
            if not self.config.script.device.run_background_only and IS_WINDOWS:
                from module.device.platform2.platform_windows import minimize_by_name, show_window_by_name
                target_window_name = self.config.script.device.handle  # 在这里输入你的具体窗口名称
                if self.config.script.device.emulator_window_minimize:
                    minimize_by_name(target_window_name, serial=self.config.script.device.serial)
                else:
                    show_window_by_name(target_window_name, serial=self.config.script.device.serial)

            while 1:
                if date.today() > start_day:
                    with _log_switch_lock:
                        logger.set_file_logger(self.config_name, do_cleanup=True)
                    start_day = date.today()

                task = ""
                try:
                    # Get task
                    task = self.get_next_task()
                    # Skip first restart
                    if self.is_first_task and task == 'Restart':
                        logger.info('Skip task `Restart` at scheduler start')
                        self.config.task_delay(task='Restart', success=True, server=True)
                        if getattr(self, 'solana_execution', None) is not None:
                            self.solana_execution.release('succeeded')
                        del_cached_property(self, 'config')
                        continue
                    if getattr(self, 'solana_execution', None) is not None and self._defer_business_precondition(task):
                        del_cached_property(self, 'config')
                        continue
                    if getattr(self, 'solana_execution', None) is not None:
                        task_config = getattr(self.config.model, convert_to_underscore(task))
                        self.solana_execution.begin(task, task_config, cooperative_task(task, self.config),
                            device_config=self.config.script.device.model_dump(mode='json'))
                    decision = self.runtime.prepare_task_execution(task)
                except DeadlineExpired:
                    logger.info(f'活动截止时间已到，跳过任务 `{task}`')
                    del_cached_property(self, 'config')
                    continue
                except Exception as e:
                    if isinstance(e, (LeaseLost, ReconciliationRequired)):
                        raise DispatchStopped(str(e)) from e
                    self._handle_task_exception(e, task)
                    if getattr(self, 'solana_execution', None) is not None and self.solana_execution.active:
                        self.solana_execution.finish('recovery_requested', type(e).__name__)
                    # 本轮 prepare 失败,重新调度
                    del_cached_property(self, 'config')
                    continue

                if decision == ScriptRuntimeDecision.RESCHEDULE:
                    self._finish_preparation_reschedule()
                    logger.info(f'Runtime preparation for `{task}` requested reschedule, reload config and retry scheduling')
                    del_cached_property(self, 'config')
                    continue
                if decision == ScriptRuntimeDecision.FAILED:
                    # A failed environment preparation still needs bounded
                    # recovery. Marking it terminal here would clear the
                    # recovery fence and retry the same due task forever.
                    self._finish_preparation_reschedule()
                    logger.warning(f'Runtime preparation for `{task}` failed, reload config and retry scheduling')
                    del_cached_property(self, 'config')
                    continue

                execution = getattr(self, 'solana_execution', None)
                if execution is not None and execution.deadline_expired():
                    # Login/environment preparation can cross the cutoff. No
                    # activity should start afterward, in any scheduler mode.
                    execution.finish('cancelled', 'deadline_expired')
                    del_cached_property(self, 'config')
                    continue

                # Run
                logger.info(f'Scheduler: Start task `{task}`')
                self.device.stuck_record_clear()
                self.device.click_record_clear()
                logger.hr(task, level=0)
                self.config.model.running_task = task
                _task_start = monotonic()
                success = self.run(inflection.camelize(task))
                self.config.model.running_task = ''
                logger.info(f'Scheduler: End task `{task}`')
                self.is_first_task = False
                self.anti_ban_guard.record_active(monotonic() - _task_start)

                if getattr(self, 'solana_execution', None) is not None:
                    execution = getattr(self, 'solana_execution', None)
                    final_outcome = classify_outcome(success, self.last_task_runtime_outcome, execution.business_success)
                    control = execution.validate().get('control')
                    if control in ('stop', 'safe_stop'):
                        final_outcome = 'cancelled'
                    if not success and self.failure_record.get(task, 0) >= 2 and self.config.script.error.error_repeated:
                        # Preserve configured error shutdown while still owning
                        # this device; never stop another profile's later lease.
                        self.device.emulator_stop()
                    reason = ((self.last_task_runtime_outcome or {}).get('status') or
                              getattr(self, '_solana_error_category', None))
                    if reason == 'business_skipped':
                        execution.event('scheduler.skipped', {
                            'reason': (self.last_task_runtime_outcome or {}).get('reason'),
                            'phase': 'business_precondition_after_prepare',
                            'next_run': str((self.last_task_runtime_outcome or {}).get('wait_until'))})
                        reason = 'business_precondition:' + str((self.last_task_runtime_outcome or {}).get('reason'))
                    execution.finish(final_outcome, reason)
                    if reason == 'deadline_expired' and control not in ('stop', 'safe_stop'):
                        del_cached_property(self, 'config')
                        continue
                    if final_outcome == 'cancelled' and (control in ('stop', 'safe_stop') or
                            not str(reason or '').startswith('business_precondition:')):
                        raise DispatchStopped(0)
                    if final_outcome in ('paused', 'yielded', 'recovery_requested'):
                        del_cached_property(self, 'config')
                        continue

                outcome = getattr(self, 'last_task_runtime_outcome', None)
                if isinstance(outcome, dict) and outcome.get('status') == 'team_preempted':
                    del_cached_property(self, 'config')
                    continue

                # Check failures
                # failed = deep_get(self.failure_record, keys=task, default=0)
                failed = self.failure_record[task] if task in self.failure_record else 0
                failed = 0 if success else failed + 1
                # deep_set(self.failure_record, keys=task, value=failed)
                self.failure_record[task] = failed
                if failed >= 3:
                    logger.critical(f"Task `{task}` failed 3 or more times.")
                    logger.critical("Possible reason #1: You haven't used it correctly. "
                                    "Please read the help text of the options.")
                    logger.critical("Possible reason #2: There is a problem with this task. "
                                    "Please contact developers or try to fix it yourself.")
                    logger.critical('Request human takeover')
                    # 添加失败三次的推送通知
                    self.config.notifier.push(
                        title=f'{I18n.trans_zh_cn(task)}{task}',
                        content=f"<{self.config_name}> 任务连续失败三次，请上线查看"
                    )
                    # 关闭模拟器
                    if self.config.script.error.error_repeated and getattr(self, 'solana_execution', None) is None:
                        self.device.emulator_stop()
                    exit(1)

                if success:
                    del_cached_property(self, 'config')
                    continue
                elif self.config.script.error.handle_error:
                    # self.config.task_delay(success=False)
                    del_cached_property(self, 'config')
                    # self.checker.check_now()
                    continue
                else:
                    break
        finally:
            execution = getattr(self, 'solana_execution', None)
            if execution is not None:
                try:
                    if execution.active and execution.active.get('state') == 'running':
                        execution.finish('interrupted', 'worker_exit')
                    elif execution.lease:
                        execution.release('interrupted')
                except Exception:
                    logger.error('Solana runtime exit could not persist; parent must reconcile this run')
            if team_sync:
                team_sync.close()

    def _handle_task_exception(self, e: Exception, command: str) -> bool:
        """
        统一处理任务执行 / 准备阶段抛出的异常。
        Returns:
            True  -> 视为正常结束或已自动恢复 (例如已 task_call('Restart')),
                     调度器继续推进
            False -> 视为失败,脚本继续运行
        对致命异常 (ScriptError / RequestHumanTakeover / 未识别 Exception)
        在内部直接 exit(1)。
        """
        from module.scheduling.runtime import SafeBoundaryExit, ReconciliationRequired, DispatchStopped
        from module.scheduling.coordinator import LeaseLost
        if not isinstance(e, TaskEnd):
            self._solana_error_category = type(e).__name__
        if isinstance(e, SafeBoundaryExit):
            self._set_task_runtime_outcome(command, e.outcome)
            return True
        if isinstance(e, (LeaseLost, ReconciliationRequired)):
            raise DispatchStopped(str(e)) from e
        from module.script.team_sync import TeamTaskSwitch, TeamSyncUnavailable, TeamPartnerFinished
        if isinstance(e, TeamTaskSwitch):
            logger.info(f'{command}: {e}')
            self._set_task_runtime_outcome(task=command, status='team_preempted')
            return True
        if isinstance(e, (TeamSyncUnavailable, TeamPartnerFinished)):
            command = command or self.config.task.command
            retry_at = datetime.now().replace(microsecond=0) + timedelta(minutes=2)
            status = 'team_wait_failed'
            if isinstance(e, TeamPartnerFinished):
                retry_at = max(datetime.now().replace(microsecond=0) + timedelta(seconds=1), e.next_run)
                status = 'team_partner_finished'
            logger.warning(f'{command}: {e}; next run {retry_at}')
            self.config.task_delay(task=command, target=retry_at, server=False)
            self._set_task_runtime_outcome(task=command, status=status, wait_until=retry_at)
            return True

        from module.exception import TaskDeferred
        if isinstance(e, TaskDeferred):
            retry_at = datetime.now().replace(microsecond=0) + timedelta(seconds=max(60, e.retry_after))
            logger.warning(f'{command}: 未完成，等待重试；{e}; next run {retry_at}')
            self.config.task_delay(task=command, target=retry_at, server=False)
            self._set_task_runtime_outcome(task=command, status='retry_scheduled', wait_until=retry_at)
            return True

        if isinstance(e, TaskEnd):
            self._capture_task_runtime_outcome(command)
            return True

        if isinstance(e, ActivityPreparationTimeout):
            logger.warning(f'{command}: {e}; restart and retry unfinished activity preparation in one minute')
            self.save_error_log()
            retry_at = datetime.now().replace(microsecond=0) + timedelta(minutes=1)
            # Climbing has not started, so do not consume its daily failure interval.
            self.config.task_delay(task=command, target=retry_at, server=False)
            self.config.task_call('Restart')
            self._set_task_runtime_outcome(task=command, status='retry_scheduled', wait_until=retry_at)
            # Count genuine failures so the existing three-failure limit still applies.
            return False

        if isinstance(e, BattleTransitionTimeout):
            logger.warning(f'{command}: {e}; skip current task and continue scheduling')
            self.save_error_log()
            self.config.task_delay(task=command, success=False)
            task_config = getattr(self.config.model, convert_to_underscore(command))
            next_run = task_config.scheduler.next_run
            # A zero/expired failure interval must not immediately select this task again.
            earliest_retry = datetime.now().replace(microsecond=0) + timedelta(minutes=1)
            if next_run < earliest_retry:
                self.config.task_delay(task=command, target=earliest_retry, server=False)
                next_run = earliest_retry
            # Clear a possibly stuck battle/popup before the next gameplay task.
            self.config.task_call('Restart')
            self._set_task_runtime_outcome(task=command, status='skipped', wait_until=next_run)
            # A handled skip must not accumulate toward the scheduler's three-failure stop.
            return True

        if isinstance(e, GameNotRunningError):
            logger.warning(e)
            self.exception_handler(e=e, command=command)
            self.config.task_call('Restart')
            self._set_task_runtime_outcome(command, 'recovery_requested')
            return True

        if isinstance(e, (GameStuckError, GameTooManyClickError)):
            logger.error(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning(f'Game stuck, {self.device.package} will be restarted in 10 seconds')
            logger.warning('If you are playing by hand, please stop Alas')
            self.config.notifier.push(title=f'{I18n.trans_zh_cn(command)}{command}',
                                      content=f"<{self.config_name}> GameStuckError or GameTooManyClickError")
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False

        if isinstance(e, GameBugError):
            logger.warning(e)
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            logger.warning('An error has occurred in Azur Lane game client, Alas is unable to handle')
            logger.warning(f'Restarting {self.device.package} to fix it')
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False

        if isinstance(e, GamePageUnknownError):
            logger.info('Game server may be under maintenance or network may be broken, check server status now')
            if command == 'GotoMain' and self._delay_tasks_for_server_update(
                    task=command,
                    reason='failed to goto main during morning server update window',
            ):
                logger.info('GotoMain failed during server update window, delayed pending tasks and reschedule')
                return False
            logger.critical('Game page unknown')
            self.save_error_log()
            self.exception_handler(e=e, command=command)
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> GamePageUnknownError",
            )
            self.config.task_call('Restart')
            self.device.sleep(10)
            return False

        if isinstance(e, ScriptError):
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('This is likely to be a mistake of developers, but sometimes just random issues')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> ScriptError",
            )
            exit(1)

        if isinstance(e, RequestHumanTakeover):
            logger.critical(e)
            self.exception_handler(e=e, command=command)
            logger.critical('Request human takeover')
            self.config.notifier.push(
                title=f'{I18n.trans_zh_cn(command)}{command}',
                content=f"<{self.config_name}> RequestHumanTakeover",
            )
            exit(1)

        # generic
        logger.exception(e)
        self.exception_handler(e=e, command=command)
        self.save_error_log()
        self.config.notifier.push(
            title=f'{I18n.trans_zh_cn(command)}{command}',
            content=f"<{self.config_name}> Exception occured",
        )
        exit(1)
        return False

    def start_loop(self) -> None:
        """
        创建一个线程，运行loop
        :return:
        """
        if self.loop_thread is None:
            self.loop_thread = Thread(target=self.loop, name='Script_loop')
            self.loop_thread.start()


if __name__ == "__main__":
    ensure_image_server_ready()
    ensure_ocr_server_ready()
    script = Script("oas1")
    script.loop()
