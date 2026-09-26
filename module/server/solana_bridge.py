"""Bounded synchronous JSON RPC over a private inherited process pipe.

Only the parent owns storage and device coordination. A timed-out mutation is
never retried automatically: the caller must reconcile its stable event ID.
"""
import json
import threading
import time
import uuid

MAX_MESSAGE_BYTES = 1024 * 1024


class RuntimeBridgeError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class ProcessBridge:
    def __init__(self, connection, profile_id, owner_id, device_id=None, timeout=15):
        self.connection = connection
        self.profile_id, self.owner_id, self.device_id = profile_id, owner_id, device_id
        self.timeout = timeout
        self._lock = threading.Lock()
        self._broken = False

    def __getstate__(self):
        return {key: value for key, value in self.__dict__.items() if key != '_lock'}

    def __setstate__(self, state):
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def call(self, operation, payload):
        deadline = time.monotonic() + self.timeout
        if not self._lock.acquire(timeout=self.timeout):
            raise RuntimeBridgeError('bridge_busy', 'Another runtime operation is still pending')
        try:
            if self._broken:
                raise RuntimeBridgeError('bridge_unavailable', 'Parent connection requires reconciliation')
            request_id = str(uuid.uuid4())
            raw = json.dumps({'id': request_id, 'operation': operation, 'payload': payload},
                             ensure_ascii=False, allow_nan=False).encode('utf-8')
            if len(raw) > MAX_MESSAGE_BYTES:
                raise RuntimeBridgeError('message_too_large', 'Runtime message exceeds the limit')
            completed = threading.Event()
            outcome = {}

            def exchange():
                try:
                    self.connection.send_bytes(raw)
                    if not self.connection.poll(max(0, deadline - time.monotonic())):
                        raise TimeoutError('No acknowledgement before deadline')
                    outcome['response'] = json.loads(self.connection.recv_bytes(MAX_MESSAGE_BYTES))
                except Exception as error:
                    outcome['error'] = error
                finally:
                    completed.set()

            # A pipe write, and a recv after a partial frame, can themselves
            # block. One disposable daemon handles I/O; timeout permanently
            # fences this bridge, so failures never accumulate retry threads.
            worker = threading.Thread(target=exchange, daemon=True, name='solana-rpc-io')
            worker.start()
            if not completed.wait(max(0, deadline - time.monotonic())):
                self._broken = True
                self.connection.close()
                raise RuntimeBridgeError('ack_timeout', 'Operation outcome is unknown; do not replay blindly')
            if 'error' in outcome:
                self._broken = True
                error = outcome['error']
                code = 'ack_timeout' if isinstance(error, TimeoutError) else 'bridge_unavailable'
                raise RuntimeBridgeError(code, 'Parent connection requires reconciliation') from error
            response = outcome['response']
            if response.get('id') != request_id:
                self._broken = True
                raise RuntimeBridgeError('protocol_error', 'Response identity did not match')
            if 'error' in response:
                error = response['error']
                raise RuntimeBridgeError(error['code'], error['message'])
            return response['result']
        finally:
            self._lock.release()


def serve_bridge(connection, service, owner_id, process):
    """One bounded outstanding request per child; no unbounded event queue."""
    try:
        while process.is_alive():
            if not connection.poll(0.2):
                continue
            message = json.loads(connection.recv_bytes(MAX_MESSAGE_BYTES))
            response = {'id': message['id']}
            try:
                response['result'] = service.dispatch(owner_id, message['operation'], message['payload'])
            except Exception as exc:
                # Do not send arbitrary exception strings (they can contain config secrets).
                response['error'] = {'code': getattr(exc, 'code', type(exc).__name__),
                                     'message': 'Runtime operation failed; state must be checked'}
            raw = json.dumps(response, ensure_ascii=False, default=str, allow_nan=False).encode('utf-8')
            if len(raw) > MAX_MESSAGE_BYTES:
                raw = json.dumps({'id': message['id'], 'error': {
                    'code': 'response_too_large', 'message': 'Runtime response exceeds the limit'}}).encode()
            connection.send_bytes(raw)
    except (EOFError, OSError, ValueError, KeyError):
        # A broken pipe does not revoke a device lease while its process lives.
        pass
    finally:
        connection.close()
        process.join()
        service.process_exited(owner_id, process.exitcode)
