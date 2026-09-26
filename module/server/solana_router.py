"""Versioned HTTP/WS boundary. CPU/disk queries run outside the event loop."""
import asyncio
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from module.server.solana_runtime import get_runtime, ServiceError


class ControlRequest(BaseModel):
    profile_id: str
    action: Literal['start', 'restart', 'safe_stop', 'pause', 'resume', 'immediate_stop']
    request_id: str
    expected_state_version: int | None = None


class ConfigValueRequest(BaseModel):
    profile_id: str
    task: str
    group: str
    argument: str
    types: str
    value: object = None
    expected_revision: str
    request_id: str


class ResolveRunRequest(BaseModel):
    profile_id: str
    run_id: str
    request_id: str
    resolution: Literal['close_interrupted']
    game_state_reviewed: bool


class ResolveOperationRequest(BaseModel):
    kind: Literal['legacy_requests', 'config_requests', 'requests']
    key: str
    expected_revisions: dict[str, str | None]
    reviewed: bool
    request_id: str


def create_router(service_provider=get_runtime):
    router = APIRouter(prefix='/api/v2', tags=['solana'])

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except ServiceError as exc:
            return JSONResponse(status_code=exc.status, content={'error': {'code': exc.code, 'message': str(exc)}})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'request_rejected', 'message': str(exc.detail)}
            return JSONResponse(status_code=exc.status_code, content={'error': detail})
        except (ValueError, TypeError) as exc:
            return JSONResponse(status_code=422, content={'error': {'code': 'invalid_request', 'message': '参数不符合规则'}})
        except Exception as exc:
            code = getattr(exc, 'code', type(exc).__name__)
            status = 410 if type(exc).__name__ in ('HistoryExpired', 'ReplayExpired') else 503
            return JSONResponse(status_code=status, content={'error': {'code': str(code), 'message': '数据不可用，请刷新状态后重试'}})

    @router.get('/capabilities')
    async def capabilities():
        return {'api_version': 2, 'event_schema_version': 1, 'strategies': ['legacy', 'eevdf_shadow', 'eevdf'],
                'control_actions': ['start', 'restart', 'safe_stop', 'pause', 'resume', 'immediate_stop'],
                'storage_modes': ['standard', 'summary_only', 'off'], 'config_revisions': True,
                'cooperative_tasks': [{'task_id': 'Orochi', 'mode': 'alone', 'boundary': 'settlement_and_courtyard'}],
                'audit_identity': 'client_request', 'max_page_size': 200, 'legacy_available': True}

    @router.get('/overview')
    async def overview():
        return await call(service_provider().overview)

    @router.get('/scheduler')
    async def scheduler(profile_id: str | None = None):
        return await call(service_provider().scheduler_snapshot, profile_id)

    @router.get('/preview')
    async def preview(profile_id: str):
        return await call(service_provider().preview, profile_id)

    @router.post('/control')
    async def control(body: ControlRequest, request: Request):
        service = service_provider()
        source = {'type': 'http_v2', 'client_id': request.headers.get('X-Client-ID', 'unknown')[:128]}
        def begin():
            with service.lock:
                receipt = service.begin_control({**body.model_dump(), 'source': source})
                if not receipt.get('duplicate'):
                    service.active_operations.add(receipt['request_id'])
                return receipt
        receipt = await call(begin)
        if isinstance(receipt, Response) or receipt.get('duplicate'):
            return receipt
        try:
            try:
                status = await service.adapter.control(service, receipt)
            except Exception as exc:
                return await call(service.complete_control, receipt, False, 'failed', type(exc).__name__)
            return await call(service.complete_control, receipt, True, status)
        finally:
            with service.lock:
                service.active_operations.discard(receipt['request_id'])

    @router.put('/scheduler/policy')
    async def scheduler_policy(body: dict):
        return await call(service_provider().set_scheduler_policy, body)

    @router.get('/statistics')
    async def statistics(start_date: str | None = None, end_date: str | None = None,
                         profile_id: str | None = None, task_id: str | None = None):
        return await call(service_provider().store.statistics, start_date=start_date, end_date=end_date,
                          profile_id=profile_id, task_id=task_id)

    async def events(kind, profile_id, task_id, cursor, limit, event_type=None, start_date=None, end_date=None):
        filters = {key: value for key, value in {'profile_id': profile_id, 'task_id': task_id,
                   'type': event_type, 'start_date': start_date, 'end_date': end_date}.items() if value}
        if kind == 'runs':
            filters['types'] = ['run.created', 'run.started', 'run.finished', 'run.yielded', 'run.paused',
                                'run.resumed', 'recovery.requested', 'recovery.resolved']
        else:
            filters['types'] = ['config.changed', 'strategy.changed', 'control.requested', 'control.completed',
                                'scheduler.selected', 'scheduler.override', 'scheduler.skipped', 'recovery.requested', 'recovery.resolved',
                                'storage.degraded', 'storage.recovered']
        return await call(service_provider().store.query, filters=filters, cursor=cursor, limit=limit)

    @router.get('/audit')
    async def audit(profile_id: str | None = None, task_id: str | None = None, cursor: str | None = None,
                    limit: int = Query(50, ge=1, le=200), type: str | None = None,
                    start_date: str | None = None, end_date: str | None = None):
        return await events('audit', profile_id, task_id, cursor, limit, type, start_date, end_date)

    @router.get('/runs')
    async def runs(profile_id: str | None = None, task_id: str | None = None, cursor: str | None = None,
                   limit: int = Query(50, ge=1, le=200), start_date: str | None = None, end_date: str | None = None):
        return await call(service_provider().query_runs, profile_id=profile_id, task_id=task_id,
                          cursor=cursor, limit=limit, start_date=start_date, end_date=end_date)

    @router.get('/storage')
    async def storage():
        return await call(service_provider().storage_status)

    @router.get('/recovery')
    async def recovery(profile_id: str | None = None):
        return await call(service_provider().recovery_status, profile_id)

    @router.post('/recovery/resolve')
    async def resolve_run(body: ResolveRunRequest):
        return await call(service_provider().resolve_run, body.model_dump())

    @router.get('/recovery/operations')
    async def pending_operations():
        return await call(service_provider().pending_operations)

    @router.post('/recovery/operations/resolve')
    async def resolve_operation(body: ResolveOperationRequest):
        return await call(service_provider().resolve_operation, body.model_dump())

    @router.post('/storage/reconcile')
    async def reconcile_storage():
        return await call(service_provider().reconcile_storage)

    @router.put('/storage/policy')
    async def storage_policy(body: dict):
        service = service_provider()
        def update():
            with service.lock:
                service.emit({'type': 'control.requested', 'payload': {'action': 'storage.policy', 'policy': body}})
                result = service.store.set_policy(body)
                service.emit({'type': 'config.changed', 'payload': {'scope': 'storage', 'policy': body}})
                service.publish('storage.changed', service.store.storage_status())
                return result
        return await call(update)

    @router.post('/storage/cleanup')
    async def cleanup():
        return await call(service_provider().store.cleanup, force=True)

    @router.get('/config/{profile_id}/{task}/args')
    async def args(profile_id: str, task: str):
        service = service_provider()
        def get():
            with service.lock:
                return service.adapter.args(service.profile_name(profile_id), task)
        return await call(get)

    @router.put('/config/value')
    async def save_value(body: ConfigValueRequest):
        service = service_provider()
        return await call(service.adapter.save_value, service, body.model_dump())

    @router.websocket('/events')
    async def stream(websocket: WebSocket):
        await websocket.accept()
        service = service_provider()
        stream_id = websocket.query_params.get('stream_id', '')
        try:
            after = int(websocket.query_params.get('after', '-1'))
        except ValueError:
            await websocket.close(code=1008)
            return
        try:
            while not service.closed:
                result = service.stream_after(stream_id, after)
                if result.get('resync_required'):
                    await websocket.send_json({'schema_version': 1, 'type': 'resync_required', **result})
                    await websocket.close(code=1000)
                    return
                for item in result['items']:
                    await websocket.send_json(item)
                    after = item['stream_seq']
                # A receive timeout is a transport keepalive, not an execution timeout.
                try:
                    message = await asyncio.wait_for(websocket.receive_text(), timeout=0.25)
                    if message == 'ping':
                        await websocket.send_json({'type': 'pong', 'stream_id': stream_id, 'stream_seq': after})
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError, OSError):
            return

    return router


solana_app = create_router()
