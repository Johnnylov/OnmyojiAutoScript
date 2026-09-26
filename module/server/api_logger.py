# This Python file uses the following encoding: utf-8
import inspect
import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from fastapi.responses import FileResponse
from starlette.responses import Response, StreamingResponse

from module.logger import file_formatter


API_LOGGER_NAME = "oas.api"
_TEXT_PREVIEW_LIMIT = 240
_LIST_PREVIEW_LIMIT = 20
_DICT_PREVIEW_LIMIT = 200


api_logger = logging.getLogger(API_LOGGER_NAME)
api_logger.setLevel(logging.INFO)
api_logger.propagate = False


def _build_api_file_handler(log_file: str) -> logging.FileHandler:
    handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    handler.setFormatter(file_formatter)
    handler._oas_api_handler = True
    handler._oas_api_log_file = log_file
    return handler


def ensure_api_logger() -> logging.Logger:
    log_dir = Path("./log")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(log_dir / f"{date.today().isoformat()}_api.txt")

    current_handler = None
    for handler in list(api_logger.handlers):
        if getattr(handler, "_oas_api_handler", False):
            if getattr(handler, "_oas_api_log_file", "") == log_file:
                current_handler = handler
                continue
            api_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    if current_handler is None:
        api_logger.addHandler(_build_api_file_handler(log_file))

    api_logger.log_file = log_file
    return api_logger


def _summarize_text(text: str) -> str | dict[str, Any]:
    if len(text) <= _TEXT_PREVIEW_LIMIT:
        return text
    return {
        "type": "text",
        "length": len(text),
        "preview": text[:_TEXT_PREVIEW_LIMIT] + "...",
    }


def _is_large_text_key(key: str) -> bool:
    lowered = str(key).lower()
    return "base64" in lowered or "content" in lowered and "image" in lowered


def summarize_data(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if any(part in str(key).lower() for part in ('password', 'token', 'secret', 'cookie', 'authorization', 'notify_config')):
        return '<modified>'
    if depth > 4:
        return "<max_depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value)}
    if isinstance(value, str):
        if _is_large_text_key(key):
            return {"type": "text", "length": len(value), "preview": value[:64] + ("..." if len(value) > 64 else "")}
        return _summarize_text(value)
    if isinstance(value, dict):
        marker = str(value.get('argument', value.get('name', value.get('key', value.get('path', ''))))).lower()
        if any(part in marker for part in ('password', 'token', 'secret', 'cookie', 'notify_config')):
            value = {k: '<modified>' if k in ('value', 'old', 'new', 'default') else v for k, v in value.items()}
        result: dict[str, Any] = {}
        items = list(value.items())
        for index, (sub_key, sub_value) in enumerate(items):
            if index >= _DICT_PREVIEW_LIMIT:
                result["__truncated_keys__"] = len(items) - _DICT_PREVIEW_LIMIT
                break
            result[str(sub_key)] = summarize_data(sub_value, key=str(sub_key), depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [summarize_data(item, depth=depth + 1) for item in items[:_LIST_PREVIEW_LIMIT]]
        if len(items) > _LIST_PREVIEW_LIMIT:
            result.append({"__truncated_items__": len(items) - _LIST_PREVIEW_LIMIT})
        return result

    try:
        encoded = jsonable_encoder(value)
    except Exception:
        return _summarize_text(str(value))
    if encoded is value:
        return _summarize_text(str(value))
    return summarize_data(encoded, key=key, depth=depth + 1)


async def build_request_summary(request: Request) -> dict[str, Any]:
    # Transport metadata only. Structured config audit owns safe field diffs.
    route = request.scope.get('route')
    return {
        "method": request.method,
        "route": getattr(route, 'path', '<unmatched>'),
        "path_parameter_names": sorted(request.path_params),
        "query_parameter_names": sorted(request.query_params),
        "content_type": request.headers.get('content-type', '').split(';', 1)[0],
        "content_length": request.headers.get('content-length'),
    }


def summarize_response(response: Response) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "response_type": type(response).__name__,
        "status_code": response.status_code,
    }

    media_type = getattr(response, "media_type", None)
    if media_type:
        summary["media_type"] = media_type

    body = getattr(response, "body", None)
    if isinstance(body, (bytes, memoryview)):
        summary['body_bytes'] = len(body)
    return summary


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def log_http_access(payload: dict[str, Any]) -> None:
    logger = ensure_api_logger()
    status_code = int(payload.get("response", {}).get("status_code", 200))
    message = f"HTTP {_serialize_payload(payload)}"
    if status_code >= 500:
        logger.error(message)
    elif status_code >= 400:
        logger.warning(message)
    else:
        logger.info(message)


def log_ws_event(message: str, level: str = "info") -> None:
    logger = ensure_api_logger()
    getattr(logger, level, logger.info)(f"{message}")


async def build_exception_response(request: Request, exc: Exception) -> Response | None:
    handlers = request.app.exception_handlers
    if hasattr(exc, "status_code") and exc.status_code in handlers:
        handler = handlers[exc.status_code]
        result = handler(request, exc)
        return await result if inspect.isawaitable(result) else result

    for cls in type(exc).__mro__:
        handler = handlers.get(cls)
        if handler is None:
            continue
        result = handler(request, exc)
        return await result if inspect.isawaitable(result) else result
    return None


class ApiLoggingRoute(APIRoute):
    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            started_at = time.perf_counter()
            request_summary = await build_request_summary(request)
            exc: Exception | None = None

            try:
                from module.server.solana_legacy_audit import legacy_request_context
                with legacy_request_context(request.headers.get('x-request-id'),
                                            request.headers.get('x-client-id'), 'http', request):
                    response = await original_route_handler(request)
            except Exception as error:
                exc = error
                response = await build_exception_response(request, error)
                if response is None:
                    raise

            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 3)
            payload = {
                "request": request_summary,
                "response": summarize_response(response),
                "elapsed_ms": elapsed_ms,
            }
            if exc is not None:
                payload["exception"] = {
                    "type": type(exc).__name__,
                }
            if getattr(request.state, 'legacy_audit_state', None):
                response.headers['X-OAS-Audit-State'] = request.state.legacy_audit_state
            if getattr(request.state, 'legacy_request_id', None):
                response.headers['X-Request-ID'] = request.state.legacy_request_id
            log_http_access(payload)
            return response

        return custom_route_handler
