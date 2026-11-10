"""Единый формат ошибок без содержимого запросов и внутренних исключений."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException


def error_response(status: int, code: str, message: str, details: list | None = None):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or []}},
    )


def register_errors(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        messages = {
            "session_not_found": "Диалог не найден",
            "session_busy": "В диалоге уже обрабатывается сообщение",
            "consultant_unavailable": "Консультант временно недоступен",
            "consultant_timeout": "Консультант не ответил вовремя",
        }
        if isinstance(exc.detail, str) and exc.detail in messages:
            return error_response(exc.status_code, exc.detail, messages[exc.detail])
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(part) for part in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return error_response(422, "validation_error", "Проверьте параметры запроса", details)

    async def storage_error(request: Request, exc: Exception):
        return error_response(503, "storage_unavailable", "Хранилище временно недоступно")

    app.add_exception_handler(OperationalError, storage_error)
    app.add_exception_handler(PoolTimeout, storage_error)
