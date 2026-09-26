# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime
from module.config.utils import convert_to_underscore

from module.logger import logger
from module.server.api_logger import ApiLoggingRoute, log_ws_event
from module.server.solana_legacy_audit import run_config_mutation, run_legacy_control, legacy_request_context
from module.server.config_manager import (
    ConfigAlreadyExistsError,
    ConfigJsonError,
    ConfigManager,
    ConfigNameError,
    ConfigNotFoundError,
    ConfigTaskError,
    ConfigValidationError,
)
from module.server.main_manager import mm
from module.server.script_process import ScriptProcess, ScriptState

from tasks.Component.config_base import TimeDelta


script_app = APIRouter(route_class=ApiLoggingRoute)


@script_app.get('/test')
async def script_test():
    return 'success'

@script_app.get('/script_menu')
async def script_menu():
    return mm.config_cache('template').gui_menu_list
# ----------------------------------   配置文件管理   ----------------------------------
@script_app.get('/config_list')
async def config_list():
    return mm.all_script_files()

@script_app.post('/config_copy')
async def config_copy(file: str, template: str = 'template'):
    def copy():
        mm.copy(file, template)
        return mm.all_script_files()
    return run_config_mutation('config.copy', [file, template], copy,
                               arguments={'file': file, 'template': template})

@script_app.get('/config_new_name')
async def config_new_name():
    return mm.generate_script_name()

@script_app.get('/config_all')
async def config_all():
    return mm.all_json_file()


@script_app.post('/config/import')
async def config_import(name: str = Form(...), file: UploadFile = File(...)):
    """
    导入脚本配置文件，上传文件名不会作为落盘名称。
    """
    try:
        raw = await file.read()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise ConfigJsonError(f'Config file must be UTF-8 JSON: {e}') from e
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ConfigJsonError(f'Config JSON parse failed: {e}') from e

        def import_profile():
            config_name = mm.import_config(name, data)
            mm.add_script_file(config_name)
            return {"name": config_name, "file": f"{config_name}.json"}
        return run_config_mutation('config.import', [name], import_profile, arguments=data)
    except ConfigAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ConfigValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"message": str(e), "fields": e.fields},
        )
    except (ConfigNameError, ConfigJsonError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@script_app.get('/config/export')
async def config_export(name: str):
    """
    导出脱敏后的脚本配置文件。
    """
    try:
        config_name, data = mm.load_config_for_export(name)
    except ConfigNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConfigNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConfigJsonError as e:
        raise HTTPException(status_code=400, detail=str(e))

    redacted = ConfigManager.redact_config(data)
    content = json.dumps(redacted, indent=2, ensure_ascii=False, sort_keys=False, default=str)
    filename = f"{config_name}.json"
    quoted_filename = quote(filename, safe='')
    fallback_filename = "config.json"
    return Response(
        content=content,
        media_type='application/json; charset=utf-8',
        headers={
            'Content-Disposition': f"attachment; filename=\"{fallback_filename}\"; filename*=UTF-8''{quoted_filename}",
            'Cache-Control': 'no-store',
        },
    )


@script_app.post('/config/task/import')
async def config_task_import(
    config_name: str = Form(...),
    task_name: str = Form(...),
    json_text: str | None = Form(None),
    file: UploadFile | None = File(None),
):
    """
    导入单个任务配置 JSON，内容必须是 {"task_key": {...}}。
    """
    try:
        file_content = await file.read() if file is not None else None
        data = mm.parse_task_json_source(json_text=json_text, file_content=file_content)
        def import_task():
            updated_name, task_key = mm.import_task_config(config_name, task_name, data)
            return {"config_name": updated_name, "task_name": task_key,
                    "file": f"{updated_name}.json", "updated": True}
        return run_config_mutation('config.task.import', [config_name], import_task,
                                   arguments={'task_name': task_name, 'data': data})
    except ConfigValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"message": str(e), "fields": e.fields},
        )
    except ConfigNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ConfigNameError, ConfigJsonError, ConfigTaskError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@script_app.get('/config/task/export')
async def config_task_export(config_name: str, task_name: str):
    """
    导出脱敏后的单个任务配置 JSON 文件。
    """
    try:
        config_name, task_key, data = mm.load_task_for_export(config_name, task_name)
    except ConfigNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ConfigNameError, ConfigJsonError, ConfigTaskError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    content = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False, default=str)
    filename = f"{config_name}-{task_key}.json"
    quoted_filename = quote(filename, safe='')
    fallback_filename = "task.json"
    return Response(
        content=content,
        media_type='application/json; charset=utf-8',
        headers={
            'Content-Disposition': f"attachment; filename=\"{fallback_filename}\"; filename*=UTF-8''{quoted_filename}",
            'Cache-Control': 'no-store',
        },
    )


@script_app.get('/config/task/copy-json')
async def config_task_copy_json(config_name: str, task_name: str):
    """
    复制单个任务配置 JSON，返回未脱敏普通 JSON。
    """
    try:
        _, _, data = mm.load_task_for_transfer(config_name, task_name)
        return data
    except ConfigNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ConfigNameError, ConfigJsonError, ConfigTaskError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@script_app.put('/config')
async def config_rename(old_name: str = '', new_name: str = ''):
    """
    update config name
    :param old_name: old config name
    :param new_name: new config name
    :return: True or False
    """
    if old_name == new_name or new_name == '':
        return False
    if old_name in mm.script_process:
        if mm.script_process[old_name].state != ScriptState.INACTIVE:
            await run_legacy_control(old_name, 'immediate_stop', mm.script_process[old_name].stop,
                                     child_operation='rename.stop')
    def rename():
        if not mm.rename(old_name, new_name):
            raise HTTPException(status_code=400, detail='Rename failed')
        return True
    result = run_config_mutation('config.rename', [old_name, new_name], rename,
        arguments={'old_name': old_name, 'new_name': new_name},
        rename={'old_name': old_name, 'new_name': new_name})
    if result:
        mm.script_process.pop(old_name, None)
    return result


@script_app.delete('/config')
async def config_delete(name: str = ''):
    """
    delete config file
    :param name: config name
    :return: True or False
    """
    if name == '' or name == 'template':
        raise HTTPException(status_code=400, detail='Delete failed')
    if name in mm.script_process:
        if mm.script_process[name].state != ScriptState.INACTIVE:
            await run_legacy_control(name, 'immediate_stop', mm.script_process[name].stop,
                                     child_operation='delete.stop')
    def delete():
        if not mm.delete(name):
            raise HTTPException(status_code=400, detail='Delete failed')
        return True
    result = run_config_mutation('config.delete', [name], delete)
    if result:
        mm.script_process.pop(name, None)
    return result


@script_app.put('/config/task/copy')
async def task_copy(task_name: str, dest_config_name: str, source_config_name: str):
    def copy():
        if dest_config_name not in mm.script_process or source_config_name not in mm.script_process:
            return False
        source_task = getattr(mm.config_cache(source_config_name).model, convert_to_underscore(task_name), None)
        if source_task is None:
            return False
        return mm.config_cache(dest_config_name).model.copy_script_task(task_name, source_task)
    return run_config_mutation('config.task.copy', [dest_config_name, source_config_name], copy,
                               arguments={'task_name': task_name})


@script_app.put('/config/task/group/copy')
async def task_group_copy(task_name: str, group_name: str, dest_config_name: str, source_config_name: str):
    def copy():
        if dest_config_name not in mm.script_process or source_config_name not in mm.script_process:
            return False
        source_task = getattr(mm.config_cache(source_config_name).model, convert_to_underscore(task_name), None)
        if source_task is None:
            return False
        return mm.config_cache(dest_config_name).model.copy_task_group(task_name, group_name, source_task)
    return run_config_mutation('config.group.copy', [dest_config_name, source_config_name], copy,
                               arguments={'task_name': task_name, 'group_name': group_name})


# ---------------------------------   脚本实例管理   ----------------------------------
@script_app.get('/{script_name}/start')
async def script_start(script_name: str):
    if script_name not in mm.script_process:
        mm.script_process[script_name] = ScriptProcess(script_name)
    await run_legacy_control(script_name, 'start', mm.script_process[script_name].start)
    return

@script_app.get('/{script_name}/stop')
async def script_stop(script_name: str):
    if script_name not in mm.script_process:
        logger.warning(f'[{script_name}] script process does not exist')
        async def already_stopped():
            return
        await run_legacy_control(script_name, 'immediate_stop', already_stopped)
        return
    await run_legacy_control(script_name, 'immediate_stop', mm.script_process[script_name].stop)
    return

@script_app.get('/{script_name}/{task}/args')
async def script_task(script_name: str, task: str):
    return mm.config_cache(script_name).model.script_task(task)

@script_app.put('/{script_name}/{task}/{group}/{argument}/value')
async def script_task(script_name: str, task: str, group: str, argument: str, types: str, value):
    try:
        match types:
            case 'integer':
                value = int(value)
            case 'number':
                value = float(value)
            case 'boolean':
                if isinstance(value, str):
                    logger.warning(f'[{script_name}] script argument {argument} value is string, try to convert to bool')
                    if value.lower() in ['true', '1']:
                        value = True
                    elif value.lower() in ['false', '0']:
                        value = False
                value = bool(value)
            case 'string':
                pass
            case 'date_time' | 'next_run':
                value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            case 'time_delta':
                # strptime 是个好东西，但是不能解析00的天数
                day = int(value[1])
                date_time = datetime.strptime(value[3:], '%H:%M:%S')
                value = TimeDelta(days=day, hours=date_time.hour, minutes=date_time.minute, seconds=date_time.second)
            case 'time':
                value = datetime.strptime(value, '%H:%M:%S').time()
            case _: pass
    except Exception as e:
        # 类型不正确
        raise HTTPException(status_code=400, detail=f'Argument type error: {e}')
    def save():
        config = mm.config_cache(script_name)
        saved = config.model.script_set_arg(task, group, argument, value)
        if (saved and types == 'next_run'
                and convert_to_underscore(task) == 'mystery_shop'
                and group == 'scheduler' and argument == 'next_run'
                and value <= datetime.now()):
            # An ordinary date edit does not grant a manual shop run.
            from tasks.MysteryShop.schedule import MysteryShopSchedule
            MysteryShopSchedule(script_name).request_manual_run(datetime.now())
            logger.info(f'[{script_name}] MysteryShop manual run requested')
        return saved
    return run_config_mutation('config.value', [script_name], save,
        arguments={'task': task, 'group': group, 'argument': argument, 'types': types, 'value': value},
        external_side_effect=types == 'next_run' and convert_to_underscore(task) == 'mystery_shop')


@script_app.put('/{script_name}/{task}/sync_next_run')
async def sync_next_run(script_name: str, task: str, target_dt: str):
    if script_name not in mm.script_process:
        return False
    config = mm.config_cache(script_name)
    target = datetime.strptime(target_dt, '%Y-%m-%d %H:%M:%S') if target_dt else None
    def synchronize():
        config.task_delay(task=task, success=True, target=target)
        return True
    result = run_config_mutation('config.sync_next_run', [script_name], synchronize,
                                arguments={'task': task, 'target_dt': target_dt})
    script_process = mm.script_process[script_name]
    config.get_next()
    await script_process.broadcast_state({"schedule": config.get_schedule_data()})
    return result


# --------------------------------------  SSE  --------------------------------------
@script_app.get('/{script_name}/state')
async def script_task_state(script_name: str):
    async def state_generate_events():
        while True:
            # 生成 SSE 事件数据
            event_data = "data: Hello, SSE!\n\n"
            yield event_data

            # 模拟异步操作，可以替换为您的实际处理逻辑
            await asyncio.sleep(1)

    response = StreamingResponse(state_generate_events(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    return response

@script_app.get('/{script_name}/log')
async def script_task_log(script_name: str):
    async def log_generate_events():
        while True:
            # 生成 SSE 事件数据
            event_data = "data: log\n"
            yield event_data

            # 模拟异步操作，可以替换为您的实际处理逻辑
            await asyncio.sleep(1)

    response = StreamingResponse(log_generate_events(), media_type="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    return response

# -------------------------------------- websocket --------------------------------------

@script_app.websocket("/ws/{script_name}")
async def websocket_endpoint(websocket: WebSocket, script_name: str):
    if script_name not in mm.script_process:
        mm.script_process[script_name] = ScriptProcess(script_name)
    script_process = mm.script_process[script_name]
    await script_process.connect(websocket)
    log_ws_event(f"ws[{script_name}] connected")

    try:
        await script_process.send_json(websocket, {"state": script_process.state})
        log_ws_event(f"ws[{script_name}] connect state: {script_process.state}")
        config = mm.config_cache(script_name)
        config.get_next()
        schedule_data = config.get_schedule_data()
        await script_process.send_json(websocket, {"schedule": schedule_data})
        log_ws_event(f"ws[{script_name}] connect response: {schedule_data}")
        while True:
            # 初次进入，广播state schedule
            data = await websocket.receive_text()
            command = data
            request_id = None
            if data.startswith('{'):
                try:
                    message = json.loads(data)
                    command, request_id = message.get('type'), message.get('request_id')
                except (ValueError, AttributeError):
                    command = None
            log_ws_event(f"ws command: {command if command in ('get_state', 'get_schedule', 'start', 'stop') else 'unrecognized'}")
            if command == 'get_state':
                await script_process.broadcast_state({"state": script_process.state})
                log_ws_event(f"ws[{script_name}] response: {script_process.state}")
            elif command == 'get_schedule':
                config = mm.config_cache(script_name)
                config.get_next()
                schedule_data = config.get_schedule_data()
                await script_process.broadcast_state({"schedule": schedule_data})
                log_ws_event(f"ws[{script_name}] response: {schedule_data}")
            elif command in ('start', 'stop'):
                with legacy_request_context(request_id, websocket.headers.get('x-client-id'), 'ws'):
                    await run_legacy_control(script_name, 'start' if command == 'start' else 'immediate_stop',
                                             script_process.start if command == 'start' else script_process.stop)

    except WebSocketDisconnect:
        log_ws_event(f"ws[{script_name}] disconnect", level="warning")
        await script_process.disconnect(websocket)
    except Exception as e:
        log_ws_event(f"ws error: {type(e).__name__}", level="error")
        logger.exception(f'[{script_name}] websocket error: {e}')
        await script_process.disconnect(websocket)
