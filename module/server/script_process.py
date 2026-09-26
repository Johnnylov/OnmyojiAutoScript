# This Python file uses the following encoding: utf-8
# @author runhey
# 脚本进程
# github https://github.com/runhey
import multiprocessing
import threading
import uuid
from asyncio import QueueEmpty, CancelledError, sleep
from enum import Enum

from module.logger import logger
from module.server.config_manager import ConfigManager
from module.server.script_websocket import ScriptWSManager

# 脚本进程必须使用全新解释器，避免继承主服务已连接的 ZeroRPC/ZeroMQ context。
# Pipe、Queue 和 Process 必须来自同一个 multiprocessing context。
_SCRIPT_PROCESS_CONTEXT = multiprocessing.get_context("spawn")


class ScriptState(int, Enum):
    INACTIVE = 0
    RUNNING = 1
    WARNING = 2
    UPDATING = 3


class ScriptProcess(ScriptWSManager):

    def __init__(self, config_name: str) -> None:
        super().__init__()
        if config_name not in ConfigManager.all_script_files():
            raise FileNotFoundError(f'{config_name}.json not found')
        self.config_name = config_name  # config_name
        self.log_pipe_out, self.log_pipe_in = _SCRIPT_PROCESS_CONTEXT.Pipe(False)
        self.state_queue = _SCRIPT_PROCESS_CONTEXT.Queue()
        self.state: ScriptState = ScriptState.INACTIVE
        self._process = None
        self._owner_id = None
        self._bridge_thread = None
        self._preview_thread = None
        self._preview_queue = None

    @staticmethod
    def _extract_log_dedup_key(log: str) -> str | None:
        text = str(log).strip()
        if not text:
            return None
        parts = str(log).split('|', 2)
        if len(parts) != 3:
            return None
        level, timestamp, message = [part.strip() for part in parts]
        if not level or not timestamp or not message:
            return None
        return message

    async def start(self):
        if self._process and self._process.is_alive():
            return
        from module.server.solana_runtime import get_runtime
        from module.server.solana_bridge import ProcessBridge, serve_bridge
        runtime = get_runtime()
        owner_id = str(uuid.uuid4())
        identity = runtime.register_process(self.config_name, owner_id)
        parent_pipe, child_pipe = _SCRIPT_PROCESS_CONTEXT.Pipe(duplex=True)
        bridge = ProcessBridge(child_pipe, **identity)
        preview_queue = _SCRIPT_PROCESS_CONTEXT.Queue(maxsize=1)
        self.state = ScriptState.RUNNING
        if self._process:
            logger.warning(f'Script {self.config_name} is initialized')
        # Do not yield after registering an owner until its process exists.
        # Otherwise a concurrent stop can observe _process=None, return stopped,
        # and then allow this suspended start to create an unexpected executor.
        self._process = _SCRIPT_PROCESS_CONTEXT.Process(
            target=func,
            args=(self.config_name, self.state_queue, self.log_pipe_in, bridge, preview_queue),
            name=self.config_name,
            daemon=True,
        )
        try:
            self._process.start()
        except Exception:
            parent_pipe.close()
            child_pipe.close()
            runtime.process_exited(owner_id, -1)
            raise
        child_pipe.close()
        self._owner_id = owner_id
        self._bridge_thread = threading.Thread(target=serve_bridge,
            args=(parent_pipe, runtime, owner_id, self._process), daemon=True,
            name=f'solana-{self.config_name}')
        self._bridge_thread.start()
        from module.scheduling.preview import consume_previews
        self._preview_queue = preview_queue
        self._preview_thread = threading.Thread(target=consume_previews,
            args=(preview_queue, runtime, owner_id, self._process), daemon=True,
            name=f'preview-{self.config_name}')
        self._preview_thread.start()
        await self.broadcast_state({"state": self.state})


    async def stop(self):
        self.state = ScriptState.INACTIVE
        await self.broadcast_state({"state": self.state})
        if self._process is None:
            logger.warning(f'Script {self.config_name} process is removed')
            return
        if not self._process.is_alive():
            logger.warning(f'Script {self.config_name} is not running')
            # The bridge cleanup may lag process death; reconcile only after
            # joining the process, so an explicit restart cannot stay fenced.
            self._process.join()
            if self._owner_id:
                from module.server.solana_runtime import get_runtime
                get_runtime().process_exited(self._owner_id, self._process.exitcode)
            self._process = None
            return
        self._process.terminate()
        from asyncio import to_thread
        process = self._process
        await to_thread(process.join, 10)
        if process.is_alive():
            # Do not release the lease while an executor can still issue actions.
            raise RuntimeError('Executor did not exit; device ownership remains fenced')
        from module.server.solana_runtime import get_runtime
        get_runtime().process_exited(self._owner_id, process.exitcode)
        self._process = None

    async def coroutine_broadcast_state(self):
        try:
            while 1:
                if self.state == ScriptState.INACTIVE:
                    await sleep(1)
                    continue
                await sleep(0.1)
                try:
                    if self.state_queue.empty():
                        await sleep(1)
                        continue
                    data = self.state_queue.get_nowait()
                    if not data:
                        await sleep(0.5)
                        continue
                    if 'state' in data and data['state'] in [item.value for item in ScriptState]:
                        self.state = ScriptState(data['state'])
                    await self.broadcast_state(data)
                except QueueEmpty as e:
                    logger.warning(f'QueueEmpty: {e}')
                    await sleep(0.5)
                    continue
                except Exception as e:
                    logger.error(f'Error: {e}')
                    continue
        except CancelledError as e:
            logger.warning(f'{self.config_name} state coroutine is cancelled')
            return

    async def coroutine_broadcast_log(self):
        try:
            previous_log_key = None  # 缓存上一条正文日志，用于相邻重复去重
            while 1:
                if self.state == ScriptState.INACTIVE:
                    await sleep(1)
                    continue
                await sleep(0.05)
                try:
                    if not self.log_pipe_out.poll():
                        await sleep(0.3)
                        continue
                    log = self.log_pipe_out.recv()
                    if not str(log).strip():
                        continue
                    current_log_key = self._extract_log_dedup_key(log)
                    if current_log_key is not None:
                        if current_log_key == previous_log_key:
                            continue
                        previous_log_key = current_log_key
                    else:
                        previous_log_key = None
                    await self.broadcast_log(log)
                except EOFError as e:
                    await sleep(0.5)
                    logger.warning(f'EOFError: {e}')
                    continue
                except Exception as e:
                    logger.error(f'Log Error: {e}')
                    continue
        except CancelledError as e:
            logger.warning(f'{self.config_name} log coroutine is cancelled')
            return


def func(config: str, state_queue: multiprocessing.Queue, log_pipe_in, solana_bridge=None, preview_queue=None) -> None:

    def start_log() -> None:
        try:
            from module.logger import set_file_logger, set_func_logger
            set_file_logger(name=config)
            set_func_logger(log_pipe_in.send)
        except Exception as e:
            logger.exception(f'Start log error')
            logger.error(f'Error: {e}')
            raise
    start_log()
    import time
    try:
        # while 1:
        #     time.sleep(1)
        #     logger.info(f'Script {config} is running')
        #     state_queue.put({"state": ScriptState.RUNNING})
        from script import Script
        script = Script(config_name=config, solana_bridge=solana_bridge)
        if script.solana_execution is not None and preview_queue is not None:
            from module.scheduling.preview import PreviewPublisher
            script.solana_execution.preview_publisher = PreviewPublisher(preview_queue,
                metadata=lambda: script.solana_execution.progress_snapshot())
        script.state_queue = state_queue
        script.loop()
    except SystemExit as e:
        logger.info(f'Script {config} process exit')
        if e.code not in (None, 0):
            logger.error(f'Error: {e}')
        state_queue.put({"state": ScriptState.INACTIVE if e.code in (None, 0) else ScriptState.WARNING})
        time.sleep(0.1)
        raise SystemExit(0 if e.code in (None, 0) else -1)
    except Exception as e:
        logger.exception(f'Run script {config} error')
        logger.error(f'Error: {e}')
        raise


if __name__ == '__main__':
    p = ScriptProcess('oas1')
    p.start()
    from time import sleep
    sleep(10)
    logger.info(p._process.exitcode)
