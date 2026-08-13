from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.common_response import ApiError, ApiResponse


def _build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ApiResponse(
        success=False,
        data=None,
        error=ApiError(
            code=code,
            message=message,
            details=details,
        ),
    )

    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body),
        headers=headers,
    )


def _default_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "요청 처리 중 오류가 발생했습니다."


def _http_exception_content(
    status_code: int,
    detail: Any,
) -> tuple[str, Any | None]:
    if isinstance(detail, str):
        return detail, None

    if isinstance(detail, dict):
        raw_message = detail.get("message")
        if isinstance(raw_message, str) and raw_message.strip():
            details = {
                key: value
                for key, value in detail.items()
                if key != "message"
            }
            return raw_message, details or None

    return _default_message(status_code), detail


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        del request

        message, details = _http_exception_content(
            exc.status_code,
            exc.detail,
        )

        return _build_error_response(
            status_code=exc.status_code,
            code=f"HTTP_{exc.status_code}",
            message=message,
            details=details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del request

        return _build_error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="요청 값이 올바르지 않습니다.",
            details=exc.errors(),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request,
        exc: ValueError,
    ) -> JSONResponse:
        del request

        return _build_error_response(
            status_code=422,
            code="VALUE_ERROR",
            message=str(exc),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        del request
        del exc

        return _build_error_response(
            status_code=500,
            code="INTERNAL_SERVER_ERROR",
            message="서버 내부 오류가 발생했습니다.",
        )
