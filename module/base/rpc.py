"""Keep recognition RPCs responsive and recover one transient connection failure."""

from functools import wraps

import gevent
from gevent.event import AsyncResult
import zerorpc

from module.logger import logger


def wait_for_future(future):
    """Wait for a native worker without blocking the ZeroRPC event loop."""
    if future.done():
        return future.result()
    hub = gevent.get_hub()
    completed = AsyncResult()
    # Future callbacks run in native threads. Wake the owning hub through
    # its thread-safe queue instead of touching a gevent event from a worker.
    # The referenced watcher also keeps offline callers' otherwise idle hub
    # alive until the callback arrives; no polling delay is added per match.
    with hub.loop.async_() as keep_alive:
        keep_alive.start(lambda: None)
        future.add_done_callback(lambda done: hub.loop.run_callback_threadsafe(completed.set, done))
        return completed.get().result()


def in_worker(method):
    """Run a CPU-bound image endpoint on its existing native worker pool."""
    @wraps(method)
    def call(self, *args, **kwargs):
        return wait_for_future(self._scheduler.submit(method, self, *args, **kwargs))
    return call


def call_with_reconnect(proxy, method, *args):
    """Retry recognition/cache requests once; never retry remote application errors.

    Only image/OCR operations use this helper. No game inputs are replayed.
    The replacement uses the usual timeout for future calls; this one retry
    gets 20 seconds for a busy service or a newly loading OCR model.
    """
    try:
        return getattr(proxy.client, method)(*args)
    except (zerorpc.TimeoutExpired, zerorpc.LostRemote) as exc:
        logger.warning(f'Recognition RPC {method}: {exc}; reconnect and retry once')
    proxy.client.close()
    proxy.client = zerorpc.Client(timeout=10)
    proxy.client.connect(proxy.address)
    return getattr(proxy.client, method)(*args, timeout=20)
